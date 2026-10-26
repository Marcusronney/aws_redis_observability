
import argparse
import json
import sys
from pathlib import Path


def select_targets(inventory, collect_nodes=False):
    if inventory.get("errors"):
        raise ValueError(
            "Inventario AWS incompleto. "
            "Nao gerar targets com erros de discovery."
        )

    targets = []
    seen = set()

    for account in inventory.get("accounts", []):
        account_id = str(account["account_id"])

        for region in account.get("regions", []):
            if region.get("status") != "success":
                raise ValueError(
                    f"Regiao incompleta: {account_id}/"
                    f"{region.get('region')}"
                )

            region_name = region["region"]
            resources = region.get("resources", [])

            clusters = {
                item["id"]: item
                for item in resources
                if item.get("type") == "cluster"
            }



            for resource in resources:

                if resource.get('type') == 'cluster' and not resource.get('replication_group_id'):
                    if resource.get('status') != 'available' or resource.get('engine') not in ('redis', 'valkey'):
                        continue
                    endpoint = next((e for e in resource.get('endpoints', []) if e.get('role') == 'node'), None)
                    if not endpoint:
                        continue
                    identifier = f"{account_id}:{region_name}:cluster:{resource['id']}"
                    if identifier in seen:
                        raise ValueError('Duplicate standalone Redis identity')
                    seen.add(identifier)
                    targets.append({
                        'id': identifier, 'account_id': account_id, 'region': region_name,
                        'replication_group_id': resource['id'], 'engine': resource['engine'],
                        'endpoint': f"{endpoint['address']}:{endpoint['port']}", 'endpoint_role': 'node',
                        'aws_resource_type': 'cluster', 'vpc_id': resource.get('vpc_id'),
                        'tags': resource.get('tags', {}), 'tls': resource.get('transit_encryption'),
                        'auth_required': resource.get('auth_token_enabled'), 'labels': resource.get('labels', {})})
                    continue

                # ElastiCache Serverless (Redis / Valkey)
                if resource.get("type") == "serverless":

                    if resource.get("status") != "available":
                        continue

                    engine = resource.get("engine")

                    if engine not in ("redis", "valkey"):
                        continue

                    primary = next(
                        (
                            ep
                            for ep in resource.get("endpoints", [])
                            if ep.get("role") == "primary"
                        ),
                        None,
                    )

                    if not primary:
                        continue

                    identifier = (
                        f"{account_id}:{region_name}:"
                        f"serverless:{resource['id']}"
                    )

                    if identifier in seen:
                        continue

                    seen.add(identifier)

                    targets.append({
                        "id": identifier,
                        "account_id": account_id,
                        "region": region_name,
                        "replication_group_id": resource["id"],
                        "engine": engine,
                        "endpoint": (
                            f"{primary['address']}:"
                            f"{primary['port']}"
                        ),
                        "endpoint_role": "primary",
                        "tls": True,
                        # Absence of a UserGroup alone does not authorize noauth.
                        "auth_required": serverless_auth(resource),
                        "vpc_id": resource.get("vpc_id"),
                        "tags": resource.get("tags", {}),
                        "aws_resource_type": "serverless",
                        "labels": {
                            **resource.get("labels", {}),
                            "aws_resource_type": "serverless",
                        },
                    })

                    continue

                # ElastiCache baseado em nos (logica existente)
                if resource.get("type") != "replication_group":
                    continue



                if resource.get("status") != "available":
                    continue

                # Cluster mode habilitado exige outra
                # estrategia de coleta.
                if resource.get("cluster_enabled") is not False and not (collect_nodes and resource.get('cluster_enabled') is True):
                    continue

                primary = next(
                    (
                        ep for ep in resource.get(
                            "endpoints", []
                        )
                        if ep.get("role", "").startswith(
                            "primary:"
                        )
                    ),
                    None,
                )

                if not primary and not (collect_nodes and resource.get('nodes')):
                    continue

                member_ids = resource.get(
                    "member_clusters", []
                )

                member_engines = {
                    clusters[cluster_id].get("engine")
                    for cluster_id in member_ids
                    if cluster_id in clusters
                }

                member_engines.discard(None)

                # Para esta primeira versao, so
                # aceitamos engine confirmada.
                if len(member_engines) != 1:
                    continue

                engine = member_engines.pop()

                if engine not in ("redis", "valkey"):
                    continue

                if collect_nodes and resource.get('nodes'):
                    for node in resource['nodes']:
                        if not node.get('endpoint'):
                            raise ValueError('Incomplete Redis node endpoint inventory')
                        identifier = (f"{account_id}:{region_name}:{resource['id']}:"
                                      f"node/{node['cluster_id']}/{node['node_id']}")
                        if identifier in seen:
                            raise ValueError('Duplicate Redis node identity')
                        seen.add(identifier)
                        targets.append({
                            'id': identifier, 'account_id': account_id, 'region': region_name,
                            'replication_group_id': resource['id'], 'engine': engine,
                            'endpoint': f"{node['endpoint']['address']}:{node['endpoint']['port']}",
                            'endpoint_role': node['role'], 'endpoint_kind': 'node',
                            'aws_resource_type': 'replication_group',
                            'vpc_id': resource.get('vpc_id'), 'tags': resource.get('tags', {}),
                            'tls': resource.get('transit_encryption'), 'auth_required': replication_auth(resource),
                            'labels': {**resource.get('labels', {}), 'availability_zone': node['availability_zone'],
                                       'redis_node': node['cluster_id'] + '/' + node['node_id'],
                                       'redis_shard': node['shard_id'], 'redis_role': node['role']}})
                    continue

                identifier = (
                    f"{account_id}:"
                    f"{region_name}:"
                    f"{resource['id']}"
                )

                if identifier in seen:
                    continue

                seen.add(identifier)

                endpoint = (
                    f"{primary['address']}:"
                    f"{primary['port']}"
                )

                targets.append({
                    "id": identifier,
                    "account_id": account_id,
                    "region": region_name,
                    "replication_group_id": resource["id"],
                    "engine": engine,
                    "endpoint": endpoint,
                    "endpoint_role": "primary",
                    "aws_resource_type": "replication_group",
                    "vpc_id": resource.get("vpc_id"),
                    "tags": resource.get("tags", {}),
                    "tls": resource.get(
                        "transit_encryption"
                    ),
                    "auth_required": replication_auth(resource),
                    "labels": resource.get(
                        "labels", {}
                    ),
                })

    return sorted(targets, key=lambda t: t["id"])


def replication_auth(resource):
    # AuthTokenEnabled=False does not mean noauth when RBAC user groups exist.
    if resource.get('user_group_ids'):
        return True if resource.get('tags', {}).get('RedisObservabilityAuth') == 'secret' else None
    return resource.get('auth_token_enabled')


def serverless_auth(resource):
    mode = resource.get("tags", {}).get("RedisObservabilityAuth")
    if mode == "noauth" and not resource.get("user_group_id"):
        return False
    if mode == "secret":
        return True
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Preview de targets ElastiCache"
    )

    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Arquivo JSON de discovery AWS",
    )

    args = parser.parse_args()

    try:
        with args.input.open(
            encoding="utf-8"
        ) as file:
            inventory = json.load(file)

        targets = select_targets(inventory)

        print(json.dumps(
            {
                "mode": "targets-dry-run",
                "target_count": len(targets),
                "targets": targets,
            },
            indent=2,
            ensure_ascii=False,
        ))

        return 0

    except (OSError, ValueError, KeyError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
