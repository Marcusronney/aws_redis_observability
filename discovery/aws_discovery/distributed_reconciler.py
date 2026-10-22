"""Diagnostico read-only da coleta distribuida AWS Redis/Valkey."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from discovery.aws_discovery.client import create_session
from discovery.aws_discovery.ecs_compare import compare_collector


CLUSTER_NAME = "redis-observability-collectors"


def inspect_resource(session, account_id, region, resource):
    ec = session.client("elasticache", region_name=region)
    ec2 = session.client("ec2", region_name=region)
    ecs = session.client("ecs", region_name=region)

    typ = resource["type"]
    name = resource["id"]

    if typ == "serverless":
        data = ec.describe_serverless_caches(
            ServerlessCacheName=name
        )["ServerlessCaches"][0]

        subnets = data.get("SubnetIds") or []
        security_groups = data.get("SecurityGroupIds") or []
        endpoint = data.get("Endpoint") or {}
        auth = (
            "USER_GROUP_CONFIGURED"
            if data.get("UserGroupId")
            else "REQUIRES_VALIDATION"
        )

    elif typ == "replication_group":
        data = ec.describe_replication_groups(
            ReplicationGroupId=name
        )["ReplicationGroups"][0]

        members = data.get("MemberClusters") or []
        if not members:
            raise ValueError("Replication group sem membros")

        cluster = ec.describe_cache_clusters(
            CacheClusterId=members[0]
        )["CacheClusters"][0]

        subnet_group = ec.describe_cache_subnet_groups(
            CacheSubnetGroupName=cluster["CacheSubnetGroupName"]
        )["CacheSubnetGroups"][0]

        subnets = [
            item["SubnetIdentifier"]
            for item in subnet_group["Subnets"]
        ]

        security_groups = [
            item["SecurityGroupId"]
            for item in cluster.get("SecurityGroups", [])
        ]

        groups = data.get("NodeGroups") or []
        endpoint = (
            groups[0].get("PrimaryEndpoint") or {}
            if groups else {}
        )

        auth = (
            "ENABLED"
            if data.get("AuthTokenEnabled")
            or data.get("UserGroupIds")
            else "REQUIRES_VALIDATION"
        )

    else:
        raise ValueError("Tipo nao suportado")

    if not subnets or not security_groups:
        raise ValueError("Subnets ou Security Groups ausentes")

    network = ec2.describe_subnets(
        SubnetIds=subnets
    )["Subnets"]

    vpcs = sorted({item["VpcId"] for item in network})

    if len(vpcs) != 1:
        raise ValueError("Subnets pertencem a VPCs diferentes")

    existing_clusters = ecs.describe_clusters(
        clusters=[CLUSTER_NAME]
    ).get("clusters", [])

    cluster_exists = any(
        item.get("status") == "ACTIVE"
        for item in existing_clusters
    )

    digest = hashlib.sha256(
        str(resource.get("arn") or name).encode()
    ).hexdigest()[:12]

    candidates = [
        name,
        f"redis-collector-{digest}",
    ]

    existing_service = None

    if cluster_exists:
        response = ecs.describe_services(
            cluster=CLUSTER_NAME,
            services=candidates,
        )

        existing_service = next(
            (
                {
                    "name": item["serviceName"],
                    "status": item["status"],
                    "running": item["runningCount"],
                    "desired": item["desiredCount"],
                    "task_definition": item["taskDefinition"],
                    "network": item.get("networkConfiguration", {}),
                }
                for item in response.get("services", [])
                if item.get("status") != "INACTIVE"
            ),
            None,
        )

    comparison = None

    if existing_service:
        address = endpoint.get("Address")
        port = endpoint.get("Port")

        if not address or port is None:
            raise ValueError("Endpoint Redis incompleto")

        comparison = compare_collector(
            ecs=ecs,
            ec2=ec2,
            service=existing_service,
            account_id=account_id,
            region=region,
            redis_name=name,
            endpoint=f"{address}:{port}",
            redis_vpc=vpcs[0],
            redis_security_groups=security_groups,
            tls=(
                True if typ == "serverless"
                else bool(data.get("TransitEncryptionEnabled"))
            ),
        )

    return {
        "labels": resource.get("labels", {}),
        "account_id": str(account_id),
        "region": region,
        "redis": name,
        "type": typ,
        "endpoint": endpoint.get("Address"),
        "port": endpoint.get("Port"),
        "vpc": vpcs[0],
        "subnets": subnets,
        "security_groups": security_groups,
        "authentication": auth,
        "ecs_cluster_exists": cluster_exists,
        "ecs_service": existing_service,
        "comparison": comparison,
        "action": (
            "UNCHANGED"
            if comparison and comparison["matches"]
            else "UPDATE_REQUIRED"
            if existing_service
            else "COLLECTOR_NOT_REGISTERED"
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    args = parser.parse_args()

    inventory = json.loads(
        args.inventory.read_text(encoding="utf-8")
    )

    if inventory.get("errors"):
        raise ValueError("Discovery incompleto: operacao cancelada")

    results = []

    for account in inventory["accounts"]:
        session = create_session({
            "account_id": account["account_id"],
            "profile": "redis-observability-admin",
        })

        for region in account["regions"]:
            if region.get("status") != "success":
                raise ValueError("Regiao com discovery incompleto")

            for resource in region.get("resources", []):
                if resource.get("type") not in (
                    "serverless",
                    "replication_group",
                ):
                    continue

                if resource.get("status") != "available":
                    continue

                results.append(
                    inspect_resource(
                        session,
                        account["account_id"],
                        region["region"],
                        resource,
                    )
                )

    print(json.dumps(
        {
            "mode": "READ_ONLY",
            "resources": results,
        },
        indent=2,
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        sys.exit(1)
