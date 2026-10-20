
import argparse
import json
import sys
from pathlib import Path


def select_targets(inventory):
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
                if resource.get("type") != "replication_group":
                    continue

                if resource.get("status") != "available":
                    continue

                # Cluster mode habilitado exige outra
                # estrategia de coleta.
                if resource.get("cluster_enabled") is not False:
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

                if not primary:
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
                    "tls": resource.get(
                        "transit_encryption"
                    ),
                    "auth_required": resource.get(
                        "auth_token_enabled"
                    ),
                    "labels": resource.get(
                        "labels", {}
                    ),
                })

    return sorted(targets, key=lambda t: t["id"])


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
