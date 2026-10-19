
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .client import (
    create_session,
    create_elasticache_client,
)
from .elasticache import (
    discover_resources,
    get_resource_tags,
)
from .normalize import (
    normalize_cluster,
    normalize_replication_group,
    normalize_serverless,
    allowed_labels,
)


DEFAULT_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "aws-accounts.yaml"
)


def discover_region(session, region, tag_mapping):
    client = create_elasticache_client(session, region)

    inventory = discover_resources(client)
    resources = []

    normalizers = {
        "clusters": normalize_cluster,
        "replication_groups": normalize_replication_group,
        "serverless": normalize_serverless,
    }

    for kind, normalizer in normalizers.items():
        for item in inventory[kind]:
            arn = item.get("ARN")

            tags = get_resource_tags(client, arn)

            labels = allowed_labels(
                tags,
                tag_mapping,
            )

            resource = normalizer(item, labels)
            resource["region"] = region

            resources.append(resource)

    return resources


def main():
    parser = argparse.ArgumentParser(
        description="AWS Redis/Valkey Discovery"
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
    )

    args = parser.parse_args()

    with args.config.open(
        encoding="utf-8"
    ) as handle:
        configuration = yaml.safe_load(handle)

    result = {
        "mode": "aws-dry-run",
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "accounts": [],
        "errors": [],
    }

    tag_mapping = configuration.get(
        "tag_labels", {}
    )

    for account in configuration["accounts"]:
        account_id = str(account["account_id"])

        account_output = {
            "name": account["name"],
            "account_id": account_id,
            "regions": [],
        }

        result["accounts"].append(account_output)

        try:
            session = create_session(account)

        except Exception as exc:
            result["errors"].append({
                "account_id": account_id,
                "region": None,
                "error": str(exc),
            })
            continue

        for region in account["regions"]:
            try:
                resources = discover_region(
                    session,
                    region,
                    tag_mapping,
                )

                for resource in resources:
                    resource["account_id"] = account_id

                account_output["regions"].append({
                    "region": region,
                    "status": "success",
                    "resource_count": len(resources),
                    "resources": resources,
                })

            except Exception as exc:
                result["errors"].append({
                    "account_id": account_id,
                    "region": region,
                    "error": str(exc),
                })

                account_output["regions"].append({
                    "region": region,
                    "status": "error",
                    "resources": [],
                })

    print(json.dumps(
        result,
        indent=2,
        ensure_ascii=False,
    ))

    if result["errors"]:
        print(
            "Discovery incompleto. Consulte errors.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
