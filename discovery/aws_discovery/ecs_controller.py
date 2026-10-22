"""Controlador de coletores ECS. Somente leitura por padrao.

Executa CREATE/UPDATE apenas em Redis opt-in e contas autorizadas.
Reutiliza o inventario gerado pelo Discovery original.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import boto3
import yaml
from botocore.exceptions import ClientError

from discovery.aws_discovery.ecs_preflight import inspect_vpc_network

CLUSTER = "redis-observability-collectors"
LOG_GROUP = "/ecs/redis-observability-collectors"
AMP_REGION = "us-east-1"
AMP_WORKSPACE = "ws-a32bf55d-d10a-4d22-9873-a937f133db67"
EXECUTION_ROLE = "redis-observability-ecs-execution"
TASK_ROLE = "redis-observability-ecs-task"
TAG_PROJECT = "aws-redis-observability"


def read_yaml(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def digest_for(account, region, kind, name):
    return hashlib.sha256(f"{account}:{region}:{kind}:{name}".encode()).hexdigest()[:12]


def route_key(account, region, kind, name):
    return f"{account}:{region}:{kind}:{name}"


def temp_session(credentials, region=None):
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region,
    )


def sessions_for(account, profile=None, apply=False, allow_direct=False):
    """Discovery usa a role de leitura; escrita usa role separada."""
    chosen_profile = profile or os.getenv("REDIS_AWS_PROFILE") or account.get("profile")
    session = boto3.Session(profile_name=chosen_profile) if chosen_profile else boto3.Session()
    read_role = account.get("role_arn")
    if read_role:
        credentials = session.client("sts").assume_role(
            RoleArn=read_role,
            RoleSessionName="RedisObservabilityDiscover",
        )["Credentials"]
        session = temp_session(credentials)
    identity = session.client("sts").get_caller_identity()["Account"]
    if identity != str(account["account_id"]):
        raise ValueError(f"Identidade incorreta: {identity} != {account['account_id']}")
    if apply:
        write_role = account.get("provision_role_arn")
        # Uso interativo de SSO admin e explicitamente opt-in, nunca no CronJob.
        if allow_direct and profile == "redis-observability-admin":
            write_role = None
        if write_role:
            credentials = session.client("sts").assume_role(
                RoleArn=write_role,
                RoleSessionName="RedisObservabilityProvision",
            )["Credentials"]
            session = temp_session(credentials)
        elif not (allow_direct and profile == "redis-observability-admin"):
            raise ValueError("Sem provision_role_arn; --apply bloqueado")
        if session.client("sts").get_caller_identity()["Account"] != identity:
            raise ValueError("Provisionador assumiu uma conta inesperada")
    return session


def resource_metadata(cache, kind, name):
    if kind == "serverless":
        r = cache.describe_serverless_caches(ServerlessCacheName=name)["ServerlessCaches"][0]
        return {
            "arn": r["ARN"], "kind": kind,
            "subnets": r.get("SubnetIds", []),
            "sgs": r.get("SecurityGroupIds", []),
            "endpoint": r.get("Endpoint", {}),
            "tls": True,
            "user_group_id": r.get("UserGroupId"),
            "auth_token_enabled": False,
        }
    rg = cache.describe_replication_groups(ReplicationGroupId=name)["ReplicationGroups"][0]
    if rg.get("ClusterEnabled") is not False:
        raise ValueError("cluster-mode habilitado: onboarding nao implementado")
    members = rg.get("MemberClusters") or []
    if not members:
        raise ValueError("Replication group sem CacheClusters")
    node = cache.describe_cache_clusters(CacheClusterId=members[0])["CacheClusters"][0]
    subnet_group = cache.describe_cache_subnet_groups(
        CacheSubnetGroupName=node["CacheSubnetGroupName"]
    )["CacheSubnetGroups"][0]
    primary = next((g.get("PrimaryEndpoint") for g in rg.get("NodeGroups", [])
                    if g.get("PrimaryEndpoint")), None)
    return {
        "arn": rg["ARN"], "kind": kind,
        "subnets": [s["SubnetIdentifier"] for s in subnet_group["Subnets"]],
        "sgs": [s["SecurityGroupId"] for s in node.get("SecurityGroups", [])],
        "endpoint": primary or {},
        "tls": bool(rg.get("TransitEncryptionEnabled")),
        "user_group_id": (rg.get("UserGroupIds") or [None])[0],
        "auth_token_enabled": bool(rg.get("AuthTokenEnabled")),
    }


def tag_dictionary(cache, arn):
    return {t["Key"]: t["Value"] for t in
            cache.list_tags_for_resource(ResourceName=arn).get("TagList", [])}


def select_network(ec2, metadata):
    if not metadata["subnets"] or not metadata["sgs"]:
        raise ValueError("Cache sem subnet ou Security Group")
    subnets = ec2.describe_subnets(SubnetIds=metadata["subnets"])["Subnets"]
    vpcs = {s["VpcId"] for s in subnets}
    if len(vpcs) != 1:
        raise ValueError("Subnets do cache em VPCs diferentes")
    vpc = vpcs.pop()
    options = inspect_vpc_network(ec2, vpc)
    options = [o for o in options if o["subnet_id"] in metadata["subnets"]]
    if not options:
        raise ValueError("Sem rota NAT/IGW adequada nas subnets do Redis")
    # A preferencia por NAT ja e aplicada no modulo ecs_preflight.
    return vpc, options[0]


def list_existing_service(ecs, region, account, kind, name):
    cluster = ecs.describe_clusters(clusters=[CLUSTER])["clusters"]
    if not any(c.get("status") == "ACTIVE" for c in cluster):
        return None
    family = "redis-collector-" + digest_for(account, region, kind, name)
    response = ecs.describe_services(cluster=CLUSTER, services=[name, family])
    active = [s for s in response.get("services", []) if s.get("status") == "ACTIVE"]
    if len(active) > 1:
        raise ValueError("Dois ECS Services candidatos encontrados; resolva duplicidade")
    return active[0] if active else None


def describe_auth(tags, metadata, region, account):
    mode = tags.get("RedisObservabilityAuth", "").lower()
    if mode not in ("noauth", "secret"):
        raise ValueError("Tag RedisObservabilityAuth deve ser noauth ou secret")
    if mode == "noauth":
        if metadata.get("user_group_id") or metadata.get("auth_token_enabled"):
            raise ValueError("Noauth contradiz autenticacao do ElastiCache")
        return {"mode": mode}
    secret = tags.get("RedisObservabilitySecretArn")
    if not secret or not secret.startswith(f"arn:aws:secretsmanager:{region}:{account}:secret:redis-observability/"):
        raise ValueError("Secret ARN deve pertencer ao prefixo redis-observability/ da conta/regiao")
    username = tags.get("RedisObservabilityUser", "")
    return {"mode": mode, "secret": secret, "username": username}


def make_task(template, *, account, region, kind, name, meta, auth, labels):
    task = copy.deepcopy(template)
    family = "redis-collector-" + digest_for(account, region, kind, name)
    task["family"] = family
    task["executionRoleArn"] = f"arn:aws:iam::{account}:role/{EXECUTION_ROLE}"
    task["taskRoleArn"] = f"arn:aws:iam::{account}:role/{TASK_ROLE}"
    containers = {c["name"]: c for c in task["containerDefinitions"]}
    if set(containers) != {"redis-exporter", "adot-collector"}:
        raise ValueError("Template deve conter exatamente redis-exporter e adot-collector")
    exporter, adot = containers["redis-exporter"], containers["adot-collector"]
    address, port = meta["endpoint"].get("Address"), meta["endpoint"].get("Port")
    if not address or not isinstance(port, int):
        raise ValueError("Endpoint de Redis ausente")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", address):
        raise ValueError("Hostname Redis inesperado")
    scheme = "rediss" if meta["tls"] else "redis"
    env = {e["name"]: e["value"] for e in exporter.get("environment", [])}
    env["REDIS_ADDR"] = f"{scheme}://{address}:{port}"
    env.pop("REDIS_PASSWORD", None)
    env.pop("REDIS_USER", None)
    if auth["mode"] == "secret" and auth.get("username"):
        env["REDIS_USER"] = auth["username"]
    exporter["environment"] = [{"name": k, "value": v} for k, v in sorted(env.items())]
    exporter.pop("secrets", None)
    if auth["mode"] == "secret":
        exporter["secrets"] = [{"name": "REDIS_PASSWORD", "valueFrom": auth["secret"]}]
    adot_env = {e["name"]: e["value"] for e in adot.get("environment", [])}
    config = yaml.safe_load(adot_env["AOT_CONFIG_CONTENT"])
    scrape = config["receivers"]["prometheus"]["config"]["scrape_configs"][0]
    new_labels = {
        "aws_account_id": account, "aws_region": region, "redis_name": name,
        "aws_resource_type": kind, "project": TAG_PROJECT,
    }
    # Labels permitidas pelo Discovery, sem herdar dados de outro Redis.
    for k in ("environment", "cost_center", "business_unit", "owner"):
        if k in labels:
            new_labels[k] = str(labels[k])
    scrape["static_configs"][0]["labels"] = new_labels
    exporters = config["exporters"]
    remote = exporters.get("prometheus_remote_write") or exporters.get("prometheusremotewrite")
    if not remote:
        raise ValueError("Template ADOT sem remote write")
    remote["endpoint"] = (f"https://aps-workspaces.{AMP_REGION}.amazonaws.com/"
                          f"workspaces/{AMP_WORKSPACE}/api/v1/remote_write")
    remote["auth"] = {"authenticator": "sigv4auth"}
    config["extensions"]["sigv4auth"] = {"region": AMP_REGION, "service": "aps"}
    # Preserva o alias antigo no template de piloto para evitar mudancas nao testadas.
    adot_env["AOT_CONFIG_CONTENT"] = yaml.safe_dump(config, sort_keys=False)
    adot["environment"] = [{"name": k, "value": v} for k, v in sorted(adot_env.items())]
    for c in task["containerDefinitions"]:
        c["logConfiguration"]["options"]["awslogs-region"] = region
        c["logConfiguration"]["options"]["awslogs-group"] = LOG_GROUP
    task["tags"] = [{"key": "Project", "value": TAG_PROJECT},
                    {"key": "RedisName", "value": name},
                    {"key": "ManagedBy", "value": "redis-observability-controller"}]
    return task


def task_matches(expected, actual):
    """Compara apenas campos que controlamos; evita revisoes infinitas."""
    keys = ("family", "networkMode", "requiresCompatibilities", "cpu", "memory",
            "executionRoleArn", "taskRoleArn", "containerDefinitions")
    for key in keys:
        left, right = expected.get(key), actual.get(key)
        if key == "requiresCompatibilities":
            left, right = sorted(left or []), sorted(right or [])
        if key == "containerDefinitions":
            def minimum(containers):
                return {c["name"]: {
                    "image": c.get("image"), "essential": c.get("essential"),
                    "environment": sorted(c.get("environment", []), key=lambda e: e["name"]),
                    "secrets": sorted(c.get("secrets", []), key=lambda e: e["name"]),
                    "logConfiguration": c.get("logConfiguration"),
                } for c in containers or []}
            left, right = minimum(left), minimum(right)
        if left != right:
            return False
    return True


def sg_allows_redis(ec2, redis_sg_ids, collector_sg, port):
    for sg in ec2.describe_security_groups(GroupIds=redis_sg_ids)["SecurityGroups"]:
        for rule in sg.get("IpPermissions", []):
            proto = rule.get("IpProtocol")
            covers = proto == "-1" or (proto == "tcp" and
                      rule.get("FromPort", 65536) <= port <= rule.get("ToPort", -1))
            if covers and any(u.get("GroupId") == collector_sg
                              for u in rule.get("UserIdGroupPairs", [])):
                return True
    return False


def ensure_infra(session, region, vpc, metadata, port, family, auth):
    ec2 = session.client("ec2", region_name=region)
    ecs = session.client("ecs", region_name=region)
    logs = session.client("logs", region_name=region)
    iam = session.client("iam")
    for role in (EXECUTION_ROLE, TASK_ROLE):
        iam.get_role(RoleName=role)
    if auth["mode"] == "secret":
        session.client("secretsmanager", region_name=region).describe_secret(SecretId=auth["secret"])
    clusters = ecs.describe_clusters(clusters=[CLUSTER])["clusters"]
    if not any(c.get("status") == "ACTIVE" for c in clusters):
        ecs.create_cluster(clusterName=CLUSTER,
                           tags=[{"key": "Project", "value": TAG_PROJECT}])
    lg = logs.describe_log_groups(logGroupNamePrefix=LOG_GROUP)["logGroups"]
    if not any(g["logGroupName"] == LOG_GROUP for g in lg):
        logs.create_log_group(logGroupName=LOG_GROUP)
    logs.put_retention_policy(logGroupName=LOG_GROUP, retentionInDays=7)
    groups = ec2.describe_security_groups(Filters=[
        {"Name": "vpc-id", "Values": [vpc]},
        {"Name": "group-name", "Values": [family]},
    ])["SecurityGroups"]
    if len(groups) > 1:
        raise ValueError("SGs duplicados para o coletor")
    if groups:
        sg_id = groups[0]["GroupId"]
    else:
        sg_id = ec2.create_security_group(
            GroupName=family, Description="Managed Redis observability exporter",
            VpcId=vpc,
            TagSpecifications=[{"ResourceType": "security-group", "Tags": [
                {"Key": "Project", "Value": TAG_PROJECT},
                {"Key": "ManagedBy", "Value": "redis-observability-controller"},
            ]}],
        )["GroupId"]
    if not sg_allows_redis(ec2, metadata["sgs"], sg_id, port):
        # Habilita acesso somente do SG do coletor para a porta do cache.
        ec2.authorize_security_group_ingress(
            GroupId=metadata["sgs"][0],
            IpPermissions=[{"IpProtocol": "tcp", "FromPort": port, "ToPort": port,
                            "UserIdGroupPairs": [{"GroupId": sg_id}]}],
        )
    return sg_id


def ensure_service(session, region, task, family, sg_id, network, allow_updates):
    ecs = session.client("ecs", region_name=region)
    existing = ecs.describe_services(cluster=CLUSTER, services=[family]).get("services", [])
    service = next((s for s in existing if s.get("status") == "ACTIVE"), None)
    network_config = {"awsvpcConfiguration": {
        "subnets": [network["subnet_id"]],
        "securityGroups": [sg_id],
        "assignPublicIp": network["assign_public_ip"],
    }}
    if service:
        current = ecs.describe_task_definition(taskDefinition=service["taskDefinition"])["taskDefinition"]
        same_network = service.get("networkConfiguration") == network_config
        if task_matches(task, current) and same_network:
            return "UNCHANGED", service["taskDefinition"]
        if not allow_updates:
            raise ValueError("Drift em Service gerenciado; use --allow-updates apos revisar")
    response = ecs.register_task_definition(**task)
    task_arn = response["taskDefinition"]["taskDefinitionArn"]
    if service:
        ecs.update_service(cluster=CLUSTER, service=family,
                           taskDefinition=task_arn, networkConfiguration=network_config)
        return "UPDATED", task_arn
    ecs.create_service(
        cluster=CLUSTER, serviceName=family, taskDefinition=task_arn,
        launchType="FARGATE", desiredCount=1,
        networkConfiguration=network_config,
        deploymentConfiguration={"deploymentCircuitBreaker": {
            "enable": True, "rollback": True,
        }},
        tags=[{"key": "Project", "value": TAG_PROJECT},
              {"key": "ManagedBy", "value": "redis-observability-controller"}],
    )
    return "CREATED", task_arn


def run(args):
    inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
    if inventory.get("errors"):
        raise ValueError("Inventario incompleto; nenhuma alteracao permitida")
    accounts_doc = read_yaml(args.accounts)
    policy = read_yaml(args.policy)
    template = json.loads(Path(args.template).read_text(encoding="utf-8"))
    allowed_accounts = {str(a) for a in policy["allowed_accounts"]}
    local_ids = set(policy["local_targets"])
    blocked_ids = set(policy["blocked_targets"])
    account_config = {str(a["account_id"]): a for a in accounts_doc["accounts"]}
    changes = 0
    results = []
    for account in inventory.get("accounts", []):
        account_id = str(account["account_id"])
        if account_id not in allowed_accounts:
            results.append({"account": account_id, "action": "ACCOUNT_BLOCKED"})
            continue
        cfg = account_config.get(account_id)
        if not cfg:
            raise ValueError(f"Conta nao configurada: {account_id}")
        permitted_regions = cfg.get("regions", [])
        read_session = sessions_for(cfg, args.profile)
        if permitted_regions == "auto":
            # Regioes do inventario ja passaram pelo teste de habilitacao do Discovery.
            permitted_regions = [r["region"] for r in account["regions"]]
        for r in account["regions"]:
            region = r["region"]
            if r.get("status") != "success":
                raise ValueError(f"Discovery incompleto: {account_id}/{region}")
            if region not in permitted_regions:
                raise ValueError(f"Regiao nao autorizada: {region}")
            cache = read_session.client("elasticache", region_name=region)
            ecs = read_session.client("ecs", region_name=region)
            ec2 = read_session.client("ec2", region_name=region)
            for resource in r.get("resources", []):
                kind, name = resource.get("type"), resource.get("id")
                if kind not in ("serverless", "replication_group") or resource.get("status") != "available":
                    continue
                key = route_key(account_id, region, kind, name)
                entry = {"account": account_id, "region": region, "redis": name, "id": key}
                if key in blocked_ids:
                    entry["action"] = "BLOCKED_BY_POLICY"
                    results.append(entry)
                    continue
                if key in local_ids:
                    entry["action"] = "KEEP_LOCAL"
                    results.append(entry)
                    continue
                # O ECS Service do piloto e identificado antes de verificar tags opt-in.
                existing = list_existing_service(ecs, region, account_id, kind, name)
                if (existing and existing["serviceName"] == name
                    and account_id == "762012032320"
                    and region == "sa-east-1"
                    and name == "redis-observability-sa-test"):
                    entry["action"] = "EXISTING_LEGACY_SERVICE"
                    entry["status"] = existing["status"]
                    entry["running"] = existing["runningCount"]
                    results.append(entry)
                    continue
                meta = resource_metadata(cache, kind, name)
                tags = tag_dictionary(cache, meta["arn"])
                if tags.get("RedisObservability", "").lower() != "enabled":
                    entry["action"] = "SKIP_NO_OPT_IN"
                    results.append(entry)
                    continue
                try:
                    auth = describe_auth(tags, meta, region, account_id)
                    vpc, network = select_network(ec2, meta)
                    vpc_info = ec2.describe_vpcs(VpcIds=[vpc])["Vpcs"][0]
                    vpc_tags = {t["Key"]: t["Value"] for t in vpc_info.get("Tags", [])}
                    if vpc_tags.get("RedisObservability", "").lower() != "enabled":
                        raise ValueError("VPC sem tag RedisObservability=enabled")
                    family = "redis-collector-" + digest_for(account_id, region, kind, name)
                    task = make_task(template, account=account_id, region=region,
                                     kind=kind, name=name, meta=meta, auth=auth,
                                     labels=resource.get("labels", {}))
                    if auth["mode"] == "secret":
                        # Apenas DescribeSecret na simulacao. A task execution role precisa GetSecretValue.
                        read_session.client("secretsmanager", region_name=region).describe_secret(
                            SecretId=auth["secret"])
                    entry.update({"family": family, "vpc": vpc,
                                  "subnet": network["subnet_id"],
                                  "network_type": network["network_type"],
                                  "auth_mode": auth["mode"]})
                    if existing:
                        current = ecs.describe_task_definition(
                            taskDefinition=existing["taskDefinition"])["taskDefinition"]
                        net = existing.get("networkConfiguration", {}).get("awsvpcConfiguration", {})
                        subnet_matches = (len(net.get("subnets", [])) == 1 and
                                          net["subnets"][0] in meta["subnets"])
                        ingress_matches = bool(net.get("securityGroups")) and any(
                            sg_allows_redis(ec2, meta["sgs"], sg, meta["endpoint"]["Port"])
                            for sg in net["securityGroups"])
                        if task_matches(task, current) and subnet_matches and ingress_matches:
                            entry["action"] = "UNCHANGED"
                        else:
                            entry["action"] = "UPDATE_REQUIRED"
                    else:
                        entry["action"] = "CREATE_CANDIDATE"
                    if args.apply and entry["action"] in ("CREATE_CANDIDATE", "UPDATE_REQUIRED"):
                        if args.confirm_account != account_id:
                            raise ValueError("--confirm-account obrigatorio para --apply")
                        if entry["action"] == "UPDATE_REQUIRED" and not args.allow_updates:
                            raise ValueError("Update requer --allow-updates")
                        if changes >= args.max_changes:
                            raise ValueError("Limite de alteracoes por ciclo atingido")
                        write_session = sessions_for(cfg, args.profile, apply=True,
                                                     allow_direct=args.allow_direct_admin)
                        sg_id = ensure_infra(write_session, region, vpc, meta,
                                             meta["endpoint"]["Port"], family, auth)
                        outcome, arn = ensure_service(write_session, region, task, family, sg_id,
                                                      network, args.allow_updates)
                        entry["action"], entry["task_definition"] = outcome, arn
                        changes += 1
                except (ValueError, ClientError) as exc:
                    entry["action"] = "BLOCKED"
                    entry["reason"] = str(exc)
                    if args.apply:
                        # Falhar explicitamente, sem esconder provisionamento parcial.
                        print(json.dumps(entry, ensure_ascii=False), file=sys.stderr)
                        raise
                results.append(entry)
    output = {"mode": "APPLY" if args.apply else "DRY_RUN", "changes": changes,
              "resources": results}
    if args.output:
        Path(args.output).write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inventory", required=True)
    p.add_argument("--accounts", default="discovery/config/aws-accounts.yaml")
    p.add_argument("--policy", default="discovery/config/collection-routing.yaml")
    p.add_argument("--template", default="discovery/config/ecs-task-template.json")
    p.add_argument("--profile", default=None)
    p.add_argument("--output")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--confirm-account", default="")
    p.add_argument("--max-changes", type=int, default=1)
    p.add_argument("--allow-updates", action="store_true")
    p.add_argument("--allow-direct-admin", action="store_true",
                   help="Somente laboratorio administrativo; nunca usar no CronJob")
    a = p.parse_args()
    if a.max_changes < 1:
        p.error("--max-changes deve ser positivo")
    run(a)


if __name__ == "__main__":
    main()
