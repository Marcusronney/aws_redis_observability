"""Politica de roteamento de coleta Redis/Valkey."""

import argparse
import json
from pathlib import Path

import yaml


DEFAULT_POLICY = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "collection-routing.yaml"
)


def load_policy(path):
    with Path(path).open(encoding="utf-8") as handle:
        policy = yaml.safe_load(handle)

    if not isinstance(policy, dict):
        raise ValueError("Politica invalida")

    if policy.get("version") != 1:
        raise ValueError("Versao de politica nao suportada")

    if policy.get("default_mode") != "distributed":
        raise ValueError("Modo padrao deve ser distributed")

    for field in (
        "allowed_accounts",
        "local_targets",
        "blocked_targets",
    ):
        if not isinstance(policy.get(field), list):
            raise ValueError(f"Campo invalido: {field}")

    local = set(policy["local_targets"])
    blocked = set(policy["blocked_targets"])

    if local.intersection(blocked):
        raise ValueError("Targets presentes em duas politicas")

    return policy


def classify(item, policy):
    account = str(item["account_id"])
    region = item["region"]
    resource_type = item["type"]
    redis_name = item["redis"]

    resource_id = (
        f"{account}:{region}:{resource_type}:{redis_name}"
    )

    allowed = set(map(str, policy["allowed_accounts"]))
    local = set(policy["local_targets"])
    blocked = set(policy["blocked_targets"])

    if account not in allowed:
        return "BLOCKED", "Conta nao autorizada"

    if resource_id in blocked:
        return "BLOCKED", "Recurso bloqueado pela politica"

    if resource_id in local:
        return "LOCAL", "Coleta direta existente"

    return "DISTRIBUTED", "Coletor ECS elegivel"


def generate_plan(source, policy):
    if source.get("mode") != "READ_ONLY":
        raise ValueError("Entrada nao e diagnostico read-only")

    result = []

    for item in source.get("resources", []):
        mode, reason = classify(item, policy)

        entry = dict(item)
        entry["collection_mode"] = mode
        entry["policy_reason"] = reason

        if mode == "LOCAL":
            entry["planned_action"] = "KEEP_LOCAL"
        elif mode == "BLOCKED":
            entry["planned_action"] = "SKIP"
        elif item.get("action") == "UNCHANGED":
            entry["planned_action"] = "UNCHANGED"
        elif item.get("ecs_service"):
            entry["planned_action"] = "REVIEW_UPDATE"
        else:
            entry["planned_action"] = "REVIEW_CREATE"

        result.append(entry)

    return {
        "mode": "COLLECTION_POLICY_DRY_RUN",
        "resources": result,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--policy", type=Path, default=DEFAULT_POLICY
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    policy = load_policy(args.policy)

    with args.input.open(encoding="utf-8") as handle:
        source = json.load(handle)

    plan = generate_plan(source, policy)

    args.output.write_text(
        json.dumps(plan, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("Modo: DRY_RUN")
    for item in plan["resources"]:
        print(
            item["region"],
            item["redis"],
            item["collection_mode"],
            item["planned_action"],
        )


if __name__ == "__main__":
    main()
