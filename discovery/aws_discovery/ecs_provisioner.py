"""Planejador read-only de provisionamento ECS Redis/Valkey."""

import argparse
import json
from pathlib import Path

import boto3

CLUSTER = "redis-observability-collectors"
EXECUTION_ROLE = "redis-observability-ecs-execution"
TASK_ROLE = "redis-observability-ecs-task"
LOG_GROUP = "/ecs/redis-observability-collectors"


def inspect_prerequisites(item, profile):
    session = boto3.Session(profile_name=profile)

    account = session.client("sts").get_caller_identity()["Account"]

    if account != str(item["account_id"]):
        raise ValueError(
            f"Conta incorreta: {account} != {item['account_id']}"
        )

    region = item["region"]

    ecs = session.client("ecs", region_name=region)
    logs = session.client("logs", region_name=region)
    iam = session.client("iam")

    checks = {}

    response = ecs.describe_clusters(clusters=[CLUSTER])

    checks["ecs_cluster"] = any(
        c.get("status") == "ACTIVE"
        for c in response.get("clusters", [])
    )

    for name, role in (
        ("execution_role", EXECUTION_ROLE),
        ("task_role", TASK_ROLE),
    ):
        try:
            iam.get_role(RoleName=role)
            checks[name] = True
        except iam.exceptions.NoSuchEntityException:
            checks[name] = False

    response = logs.describe_log_groups(
        logGroupNamePrefix=LOG_GROUP
    )

    checks["cloudwatch_logs"] = any(
        g["logGroupName"] == LOG_GROUP
        for g in response.get("logGroups", [])
    )

    return checks


def build_plan(source, profile):
    if source.get("mode") != "COLLECTION_POLICY_DRY_RUN":
        raise ValueError("Plano de entrada invalido")

    results = []

    for item in source["resources"]:
        original = item["planned_action"]

        result = {
            "account_id": item["account_id"],
            "region": item["region"],
            "redis": item["redis"],
            "collection_mode": item["collection_mode"],
            "action": None,
            "checks": {},
            "blockers": [],
        }

        if original == "KEEP_LOCAL":
            result["action"] = "KEEP_LOCAL"

        elif original == "SKIP":
            result["action"] = "SKIP"

        elif original == "UNCHANGED":
            result["action"] = "UNCHANGED"

        elif original == "REVIEW_UPDATE":
            result["action"] = "BLOCKED"
            result["blockers"].append(
                "Atualizacao automatica ainda nao autorizada"
            )

        elif original == "REVIEW_CREATE":
            result["checks"] = inspect_prerequisites(
                item, profile
            )

            missing = [
                name
                for name, passed in result["checks"].items()
                if not passed
            ]

            result["blockers"].extend(
                f"Pre-requisito ausente: {name}"
                for name in missing
            )

            # Nunca assumir que ausencia de User Group
            # significa Redis sem autenticacao.
            if item.get("authentication") != "VERIFIED_NOAUTH":
                result["blockers"].append(
                    "Autenticacao nao validada para novo coletor"
                )

            result["action"] = (
                "READY_FOR_FURTHER_VALIDATION"
                if not result["blockers"]
                else "BLOCKED"
            )

        else:
            raise ValueError(
                f"Acao desconhecida: {original}"
            )

        results.append(result)

    return {
        "mode": "ECS_PROVISIONING_DRY_RUN",
        "resources": results,
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--profile", default="redis-observability-admin"
    )

    args = parser.parse_args()

    source = json.loads(
        args.input.read_text(encoding="utf-8")
    )

    result = build_plan(source, args.profile)

    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    for resource in result["resources"]:
        print(
            resource["region"],
            resource["redis"],
            resource["action"],
        )
        for blocker in resource["blockers"]:
            print("  BLOQUEIO:", blocker)

    print("DRY_RUN: nenhuma alteracao na AWS")


if __name__ == "__main__":
    main()
