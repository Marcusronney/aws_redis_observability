"""AWS ElastiCache -> ECS Fargate per-resource collector reconciler.

Bootstraps (ECS cluster, IAM roles, log group, AMP policy, DynamoDB) are Terraform-owned.
Runtime ECS services and per-VPC SGs are controller-owned, like Kubernetes ScrapeConfigs.
No secrets in metadata, logs, task environment, or terraform state.

Run --dry-run first; --apply is explicitly opt-in. --prune needs two consecutive
complete, successful discoveries in the *same region*, tracked in DynamoDB.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

import boto3
import yaml
from botocore.exceptions import ClientError

MANAGED = "redis-collector-"
LABEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z_0-9]*$")


def stable_hash(value, size=12):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:size]


def aws_session(account, region_config):
    session = boto3.Session(profile_name=account.get("profile")) if account.get("profile") else boto3.Session()
    role = region_config.get("assume_role_arn")
    if role:
        creds = session.client("sts").assume_role(
            RoleArn=role,
            RoleSessionName="RedisObservabilityCollector",
            **({"ExternalId": region_config["external_id"]} if region_config.get("external_id") else {}),
        )["Credentials"]
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
    return session


def safe_labels(mapping):
    result = {}
    for name, value in mapping.items():
        if not LABEL_RE.fullmatch(name) or name.startswith("__") or name in ("job", "instance", "__name__"):
            raise ValueError(f"Invalid business label: {name}")
        result[name] = str(value).lower() if isinstance(value, bool) else str(value)
    return result


def targets_from_inventory(inventory, account_id, region, region_cfg):
    """Normalized existing discovery inventory, enriched with VPC data from AWS below.

    This pure selector does not assume a public Redis endpoint and does not return secrets.
    Unknown security/authentication is rejected rather than guessed.
    """
    if inventory.get("errors"):
        raise ValueError("Incomplete AWS inventory: refusing to reconcile")
    for acc in inventory.get("accounts", []):
        if str(acc.get("account_id")) != str(account_id):
            continue
        matches = [r for r in acc.get("regions", []) if r.get("region") == region]
        if len(matches) != 1 or matches[0].get("status") != "success":
            raise ValueError("Region missing/incomplete: refusing to reconcile")
        return [r for r in matches[0].get("resources", [])
                if r.get("type") in ("serverless", "replication_group") and r.get("status") == "available"]
    raise ValueError("Account not present in inventory: refusing to reconcile")


def enrich_resource(elasticache, resource, region_cfg, secretsmanager=None):
    """Retrieve actual Redis VPC, SG, endpoint, auth from AWS; fail closed if unknown.

    Uses the same selection restrictions as original targets.py for non-cluster RG:
    one primary endpoint, unique node engine, confirmed TLS/auth.
    """
    typ = resource["type"]
    if typ == "serverless":
        response = elasticache.describe_serverless_caches(ServerlessCacheName=resource["id"])
        matches = response.get("ServerlessCaches", [])
        if len(matches) != 1 or matches[0].get("Status") != "available":
            return None, "Serverless not available"
        raw = matches[0]
        if raw.get("Engine") not in ("redis", "valkey"):
            return None, "Unsupported engine"
        sg_ids = raw.get("SecurityGroupIds") or []
        subnet_ids = raw.get("SubnetIds") or []
        auth = bool(raw.get("UserGroupId"))
        ep = raw.get("Endpoint") or {}
        tls = True  # TLS is mandatory for AWS ElastiCache Serverless
    elif typ == "replication_group":
        response = elasticache.describe_replication_groups(ReplicationGroupId=resource["id"])
        matches = response.get("ReplicationGroups", [])
        if len(matches) != 1 or matches[0].get("Status") != "available":
            return None, "Replication group not available"
        raw = matches[0]
        if raw.get("ClusterEnabled") is not False:
            return None, "Cluster-mode replication groups need shard-aware collector support"
        nodes = raw.get("NodeGroups") or []
        if len(nodes) != 1:
            return None, "Expected one node group in non-cluster replication group"
        ep = nodes[0].get("PrimaryEndpoint") or {}
        member_ids = raw.get("MemberClusters") or []
        if not member_ids:
            return None, "Missing replication group member clusters"
        cluster_response = elasticache.describe_cache_clusters(CacheClusterId=member_ids[0])
        clusters = cluster_response.get("CacheClusters", [])
        if len(clusters) != 1 or clusters[0].get("Engine") not in ("redis", "valkey"):
            return None, "Missing or unsupported cluster engine"
        sg_ids = [x["SecurityGroupId"] for x in clusters[0].get("SecurityGroups", [])]
        subnet_name = clusters[0].get("CacheSubnetGroupName")
        if not subnet_name:
            return None, "CacheSubnetGroup missing"
        subnet_response = elasticache.describe_cache_subnet_groups(CacheSubnetGroupName=subnet_name)
        subnet_groups = subnet_response.get("CacheSubnetGroups", [])
        if len(subnet_groups) != 1:
            return None, "Cache subnet group missing"
        subnet_ids = [x["SubnetIdentifier"] for x in subnet_groups[0].get("Subnets", [])]
        if not isinstance(raw.get("AuthTokenEnabled"), bool):
            return None, "AuthTokenEnabled unknown"
        auth = raw["AuthTokenEnabled"] or bool(raw.get("UserGroupIds"))
        if not isinstance(raw.get("TransitEncryptionEnabled"), bool):
            return None, "TransitEncryptionEnabled unknown"
        tls = raw["TransitEncryptionEnabled"]
    else:
        return None, "Unsupported AWS resource type"
    if not sg_ids or not subnet_ids:
        return None, "Missing SG/subnets: configuration required"
    secrets_map = region_cfg.get("credential_secret_arns") or {}
    arn = raw.get("ARN")
    secret_arn = secrets_map.get(arn)
    if auth and not secret_arn and secretsmanager is not None:
        # Deterministic name assigned by separate Vault->SecretsManager sync service.
        # No password is returned or printed; DescribeSecret returns metadata only.
        secret_name = "redis-observability/" + stable_hash(arn, 20)
        try:
            metadata = secretsmanager.describe_secret(SecretId=secret_name)
            if not metadata.get("DeletedDate"):
                secret_arn = metadata.get("ARN")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
                raise
    if auth and not secret_arn:
        return None, "Authentication required, but no approved credential secret ARN available"
    if not ep.get("Address") or not ep.get("Port"):
        return None, "Endpoint missing"
    labels = safe_labels(resource.get("labels") or {})
    result = {
        "id": resource["id"], "arn": arn, "type": typ,
        "region": resource["region"], "account_id": str(resource["account_id"]),
        "vpc_subnets": subnet_ids, "sg_ids": sg_ids,
        "host": ep["Address"], "port": ep["Port"], "tls": tls,
        "engine": raw.get("Engine") if typ == "serverless" else clusters[0]["Engine"],
        "auth_required": auth, "secret_arn": secret_arn,
        "labels": labels,
    }
    return result, None


def build_adot_config(target, amp):
    labels = dict(target["labels"])
    labels.update({
        "aws_account_id": target["account_id"],
        "aws_region": target["region"],
        "aws_resource_type": target["type"],
        "discovery_type": "aws-distributed",
        "authentication": "true" if target["auth_required"] else "false",
        "engine": target.get("engine", "valkey"),
    })
    scheme = 'rediss' if target.get('tls', True) else 'redis'
    uri = f'{scheme}://{target["host"]}:{target["port"]}'
    return yaml.safe_dump({
        "receivers": {"prometheus": {"config": {
            "global": {"scrape_interval": "30s", "scrape_timeout": "10s"},
            "scrape_configs": [{
                "job_name": "redis-aws-distributed", "metrics_path": "/scrape",
                "static_configs": [{"targets": [uri], "labels": labels}],
                "relabel_configs": [
                    {"source_labels": ["__address__"], "target_label": "__param_target"},
                    {"source_labels": ["__param_target"], "target_label": "instance"},
                    {"target_label": "__address__", "replacement": "127.0.0.1:9121"},
                ],
            }],
        }}},
        "extensions": {"sigv4auth": {"region": amp["region"], "service": "aps"}},
        "exporters": {"prometheusremotewrite": {
            "endpoint": amp["remote_write_url"],
            "auth": {"authenticator": "sigv4auth"},
        }},
        "service": {"extensions": ["sigv4auth"], "pipelines": {"metrics": {
            "receivers": ["prometheus"], "exporters": ["prometheusremotewrite"]
        }}},
    }, sort_keys=True)


def ecs_containers(target, region_cfg, config):
    region = target["region"]
    log_group = region_cfg["log_group"]
    exporter = {
        "name": "redis-exporter", "image": config["collectors"]["exporter_image"],
        "essential": True, "command": ["--redis.addr="],
        "portMappings": [{"containerPort": 9121, "protocol": "tcp"}],
        "environment": [{"name": "REDIS_EXPORTER_SKIP_TLS_VERIFICATION", "value": "false"}],
        "logConfiguration": {"logDriver": "awslogs", "options": {
            "awslogs-group": log_group, "awslogs-region": region,
            "awslogs-stream-prefix": "redis-exporter"}},
    }
    if target["auth_required"]:
        if not target["secret_arn"]:
            raise ValueError("Missing pre-approved secret for authenticated resource")
        exporter["secrets"] = [{"name": "REDIS_PASSWORD", "valueFrom": target["secret_arn"]}]
    return [exporter, {
        "name": "adot", "image": config["collectors"]["adot_image"],
        "essential": True,
        "environment": [{"name": "AOT_CONFIG_CONTENT", "value": build_adot_config(target, config["amp"])}],
        "logConfiguration": {"logDriver": "awslogs", "options": {
            "awslogs-group": log_group, "awslogs-region": region,
            "awslogs-stream-prefix": "adot"}},
    }]


def vpc_id_of_subnet(ec2, subnet_id):
    subs = ec2.describe_subnets(SubnetIds=[subnet_id]).get("Subnets", [])
    if len(subs) != 1:
        raise ValueError(f"Subnet not found: {subnet_id}")
    return subs[0]["VpcId"]


def find_collector_sg(ec2, vpc_id, apply):
    sg_name = "redis-obs-collector-" + stable_hash(vpc_id, 10)
    found = ec2.describe_security_groups(Filters=[
        {"Name": "vpc-id", "Values": [vpc_id]},
        {"Name": "group-name", "Values": [sg_name]},
    ]).get("SecurityGroups", [])
    if found:
        return found[0]["GroupId"]
    if not apply:
        return "<create-SG-on-apply>"
    created = ec2.create_security_group(GroupName=sg_name, Description="Redis distributed collector", VpcId=vpc_id)
    sg_id = created["GroupId"]
    ec2.create_tags(Resources=[sg_id], Tags=[
        {"Key": "Project", "Value": "aws-redis-observability"},
        {"Key": "ManagedBy", "Value": "redis-observability-controller"},
    ])
    return sg_id


def allow_collector_to_cache(ec2, cache_sg, collector_sg, port, apply):
    current = ec2.describe_security_groups(GroupIds=[cache_sg])["SecurityGroups"][0]
    for permission in current.get("IpPermissions", []):
        if (permission.get("IpProtocol") in ("tcp", "-1")
            and permission.get("FromPort", 0) <= port <= permission.get("ToPort", 65535)):
            for pair in permission.get("UserIdGroupPairs", []):
                if pair.get("GroupId") == collector_sg:
                    return "unchanged"
    if apply:
        ec2.authorize_security_group_ingress(GroupId=cache_sg, IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": port, "ToPort": port,
            "UserIdGroupPairs": [{"GroupId": collector_sg, "Description": "Redis collector same VPC"}],
        }])
        return "created"
    return "would-create"


def ensure_service(ecs, target, region_cfg, config, sg_id, apply):
    containers = ecs_containers(target, region_cfg, config)
    family = MANAGED + stable_hash({"id": target["arn"], "containers": containers}, 16)
    name = MANAGED + stable_hash(target["arn"], 12)
    desired_network = {"awsvpcConfiguration": {
        "subnets": target["vpc_subnets"], "securityGroups": [sg_id],
        "assignPublicIp": "ENABLED" if config["collectors"].get("assign_public_ip") else "DISABLED",
    }}
    existing = ecs.describe_services(cluster=region_cfg["cluster"], services=[name]).get("services", [])
    existing = next((s for s in existing if s.get("status") != "INACTIVE"), None)
    if not apply:
        return name, "WOULD_UPDATE" if existing else "WOULD_CREATE"
    try:
        task = ecs.describe_task_definition(taskDefinition=family)["taskDefinition"]["taskDefinitionArn"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in ("ClientException", "InvalidParameterException"):
            raise
        task = ecs.register_task_definition(
            family=family, requiresCompatibilities=["FARGATE"], networkMode="awsvpc",
            cpu="512", memory="1024", executionRoleArn=region_cfg["execution_role_arn"],
            taskRoleArn=region_cfg["task_role_arn"], containerDefinitions=containers,
            tags=[{"key": "ManagedBy", "value": "redis-observability-controller"}],
        )["taskDefinition"]["taskDefinitionArn"]
    if existing:
        current_network = existing.get("networkConfiguration") or {}
        wanted = desired_network["awsvpcConfiguration"]
        actual = current_network.get("awsvpcConfiguration", {})
        network_unchanged = (
            set(actual.get("subnets", [])) == set(wanted["subnets"])
            and set(actual.get("securityGroups", [])) == set(wanted["securityGroups"])
            and actual.get("assignPublicIp") == wanted["assignPublicIp"]
        )
        if existing.get("taskDefinition") == task and existing.get("desiredCount") == 1 and network_unchanged:
            return name, "UNCHANGED"
        ecs.update_service(cluster=region_cfg["cluster"], service=name,
                           taskDefinition=task, desiredCount=1, networkConfiguration=desired_network)
        return name, "UPDATED"
    ecs.create_service(
        cluster=region_cfg["cluster"], serviceName=name, taskDefinition=task,
        desiredCount=1, launchType="FARGATE", networkConfiguration=desired_network,
        enableECSManagedTags=True, propagateTags="TASK_DEFINITION",
        tags=[{"key": "ManagedBy", "value": "redis-observability-controller"},
              {"key": "Project", "value": "aws-redis-observability"},
              {"key": "RedisARNHash", "value": stable_hash(target["arn"], 20)}],
    )
    return name, "CREATED"


def reconcile_stale_services(ecs, ddb, region_cfg, desired_names, apply, prune):
    """Two consecutive confirmed misses before delete; never touch non-owned ECS services."""
    state_table = region_cfg.get("state_table")
    if prune and not state_table:
        raise ValueError("--prune requires Terraform-created regional DynamoDB state_table")
    if not state_table:
        return []
    table = ddb.Table(state_table)
    found_arns = []
    paginator = ecs.get_paginator("list_services")
    for page in paginator.paginate(cluster=region_cfg["cluster"]):
        found_arns.extend(page.get("serviceArns", []))
    actions = []
    for start in range(0, len(found_arns), 10):
        result = ecs.describe_services(
            cluster=region_cfg["cluster"], services=found_arns[start:start+10], include=["TAGS"])
        for svc in result.get("services", []):
            name = svc["serviceName"]
            tags = {x["key"]: x["value"] for x in svc.get("tags", [])}
            if (not name.startswith(MANAGED)
                    or tags.get("ManagedBy") != "redis-observability-controller"
                    or svc.get("status") == "INACTIVE"):
                continue
            if name in desired_names:
                if apply:
                    table.put_item(Item={"service": name, "missing_count": 0})
                continue
            if not prune:
                actions.append((name, "STALE_NO_DELETE"))
                continue
            if not apply:
                actions.append((name, "WOULD_INCREMENT_STALE_COUNTER"))
                continue
            response = table.update_item(
                Key={"service": name},
                UpdateExpression="ADD missing_count :one",
                ExpressionAttributeValues={":one": 1},
                ReturnValues="ALL_NEW",
            )
            misses = int(response["Attributes"]["missing_count"])
            if misses < 2:
                actions.append((name, "STALE_FIRST_CONFIRMATION"))
                continue
            ecs.update_service(cluster=region_cfg["cluster"], service=name, desiredCount=0)
            ecs.delete_service(cluster=region_cfg["cluster"], service=name, force=True)
            table.delete_item(Key={"service": name})
            actions.append((name, "DELETED_AFTER_TWO_COMPLETE_DISCOVERIES"))
    return actions


def reconcile_region(inventory, account, region_name, region_cfg, config, apply, prune=False):
    session = aws_session(account, region_cfg)
    ec = session.client("elasticache", region_name=region_name)
    ec2 = session.client("ec2", region_name=region_name)
    sm = session.client("secretsmanager", region_name=region_name)
    ecs = session.client("ecs", region_name=region_name)
    selected = targets_from_inventory(inventory, account["account_id"], region_name, region_cfg)
    output = []
    desired_names = set()
    skipped = False
    for resource in selected:
        # Avoid auto-deploying unsupported cluster/replication shapes.
        target, reason = enrich_resource(ec, resource, region_cfg, sm)
        if not target:
            skipped = True
            output.append((resource["id"], "SKIPPED", reason))
            continue
        vpcs = {vpc_id_of_subnet(ec2, s) for s in target["vpc_subnets"]}
        if len(vpcs) != 1:
            skipped = True
            output.append((resource["id"], "SKIPPED", "Subnets span multiple VPCs"))
            continue
        sg_id = find_collector_sg(ec2, vpcs.pop(), apply)
        for cache_sg in target["sg_ids"]:
            result = allow_collector_to_cache(ec2, cache_sg, sg_id, target["port"], apply)
            output.append((resource["id"], "SG", result))
        service_name, action = ensure_service(ecs, target, region_cfg, config, sg_id, apply)
        desired_names.add(service_name)
        output.append((service_name, action, target["arn"]))
    # Never prune when selection includes unsupported or misconfigured resources.
    if skipped and prune:
        raise ValueError("Incomplete target selection. Pruning blocked to protect existing ECS collectors")
    if region_cfg.get("state_table"):
        ddb = session.resource("dynamodb", region_name=region_name)
        output.extend(reconcile_stale_services(ecs, ddb, region_cfg, desired_names, apply, prune))
    elif prune:
        raise ValueError("Pruning requested without a DynamoDB state_table")
    return output


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reconcile Redis Fargate collectors; dry-run by default")
    ap.add_argument("--config", required=True)
    ap.add_argument("--inventory", required=True, help="Successful output of existing aws_discovery command")
    ap.add_argument("--apply", action="store_true", help="Create/update managed ECS resources")
    ap.add_argument("--prune", action="store_true", help="Delete after two successful region-scoped discoveries; requires --apply and DynamoDB")
    args = ap.parse_args(argv)
    if args.prune and not args.apply:
        raise ValueError("--prune requires --apply")
    with open(args.config, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    with open(args.inventory, encoding="utf-8") as f:
        inventory = json.load(f)
    if inventory.get("errors"):
        raise ValueError("Inventory incomplete; no changes applied")
    for account in config["accounts"]:
        for region, cfg in account["regions"].items():
            for line in reconcile_region(inventory, account, region, cfg, config, args.apply, args.prune):
                print(" | ".join(str(x) for x in line))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, ClientError, OSError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
