"""Gera Task Definitions ECS sem alterar a AWS."""

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

import yaml


def env_dict(container):
    return {
        item["name"]: item["value"]
        for item in container.get("environment", [])
    }


def set_env(container, name, value):
    environment = container.setdefault("environment", [])

    for item in environment:
        if item["name"] == name:
            item["value"] = value
            return

    environment.append({"name": name, "value": value})


def generate_task(item, template):
    if item["collection_mode"] != "DISTRIBUTED":
        raise ValueError("Recurso nao utiliza coleta distribuida")

    if item["planned_action"] not in (
        "UNCHANGED",
        "REVIEW_CREATE",
        "REVIEW_UPDATE",
    ):
        raise ValueError("Acao nao suportada")

    if not item.get("endpoint") or not item.get("port"):
        raise ValueError("Endpoint Redis incompleto")

    # O piloto existente pode ser renderizado para comparacao.
    # Um novo Redis exige autenticacao previamente validada.
    if (
        item["planned_action"] != "UNCHANGED"
        and item.get("authentication") != "VERIFIED_NOAUTH"
    ):
        raise ValueError(
            "Novo recurso bloqueado: autenticacao nao validada"
        )

    account_id = str(item["account_id"])
    region = item["region"]
    redis_name = item["redis"]

    if not re.fullmatch(r"\d{12}", account_id):
        raise ValueError("Conta AWS invalida")

    task = copy.deepcopy(template)

    # A template validada pertence a conta principal.
    # Outras contas exigirao roles especificas.
    for role in ("taskRoleArn", "executionRoleArn"):
        if not task[role].startswith(
            f"arn:aws:iam::{account_id}:"
        ):
            raise ValueError(
                "Template IAM pertence a outra conta"
            )

    identity = (
        f"{account_id}:{region}:"
        f"{item['type']}:{redis_name}"
    )

    digest = hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()[:12]

    task["family"] = f"redis-collector-{digest}"

    containers = {
        container["name"]: container
        for container in task["containerDefinitions"]
    }

    exporter = containers["redis-exporter"]
    adot = containers["adot-collector"]

    tls = (
        True
        if item["type"] == "serverless"
        else item.get("tls")
    )

    if tls is None:
        raise ValueError("TLS nao determinado para o recurso")

    scheme = "rediss" if tls else "redis"

    redis_address = (
        f"{scheme}://{item['endpoint']}:{item['port']}"
    )

    set_env(exporter, "REDIS_ADDR", redis_address)

    adot_env = env_dict(adot)

    config = yaml.safe_load(
        adot_env["AOT_CONFIG_CONTENT"]
    )

    scrape = config["receivers"]["prometheus"][
        "config"
    ]["scrape_configs"][0]

    labels = {
        "aws_account_id": account_id,
        "aws_region": region,
        "redis_name": redis_name,
        "aws_resource_type": item["type"],
        "project": "aws-redis-observability",
    }

    # Somente metadados da origem, sem reutilizar as
    # etiquetas de negocio de outro Redis.
    for key, value in item.get("labels", {}).items():
        if key not in labels:
            labels[key] = str(value)

    scrape["static_configs"][0]["labels"] = labels

    set_env(
        adot,
        "AOT_CONFIG_CONTENT",
        yaml.safe_dump(
            config,
            sort_keys=False,
        ),
    )

    for container in task["containerDefinitions"]:
        logs = container.get(
            "logConfiguration", {}
        ).get("options", {})

        if "awslogs-region" in logs:
            logs["awslogs-region"] = region

    return task


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--plan", type=Path, required=True
    )
    parser.add_argument(
        "--template", type=Path, required=True
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True
    )

    args = parser.parse_args()

    plan = json.loads(
        args.plan.read_text(encoding="utf-8")
    )

    template = json.loads(
        args.template.read_text(encoding="utf-8")
    )

    if plan.get("mode") != "COLLECTION_POLICY_DRY_RUN":
        raise ValueError("Plano invalido")

    args.output_dir.mkdir(
        parents=True, exist_ok=True
    )

    for item in plan["resources"]:
        if item["collection_mode"] != "DISTRIBUTED":
            print(
                "SKIP_LOCAL:",
                item["region"],
                item["redis"],
            )
            continue

        try:
            task = generate_task(item, template)

        except ValueError as exc:
            print(
                "BLOCKED:",
                item["region"],
                item["redis"],
                str(exc),
            )
            continue

        path = (
            args.output_dir /
            f"{task['family']}.json"
        )

        path.write_text(
            json.dumps(
                task,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        print(
            "GENERATED:",
            item["region"],
            item["redis"],
            path,
        )

    print("READ_ONLY: nenhuma alteracao na AWS")


if __name__ == "__main__":
    main()
