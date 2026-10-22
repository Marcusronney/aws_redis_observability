#!/usr/bin/env python3
"""Gera ScrapeConfigs AWS em modo preview, sem alterar o Kubernetes.

Compatibilidade com o pipeline atual:
  python -m discovery.aws_discovery.scrapeconfig --input targets.json --output scrapeconfig.yaml

O inventario deve confirmar explicitamente auth_required como True ou False.
A escolha do exporter nao disponibiliza credenciais: os Services/Deployments
com e sem autenticacao devem existir no namespace redis-observability.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import yaml


NAMESPACE = "redis-observability"

# Exporter AWS atual: configurado com Secret para o Valkey com AUTH.
EXPORTER = (
    "redis-exporter-aws."
    "redis-observability.svc.cluster.local:9121"
)

# Service a criar no Kubernetes: exporter sem credenciais.
EXPORTER_NOAUTH = (
    "redis-exporter-noauth."
    "redis-observability.svc.cluster.local:9121"
)

MANAGED_LABELS = {
    "app.kubernetes.io/part-of": "redis-observability",
    "app.kubernetes.io/managed-by": "redis-discovery-aws",
    "discovery-source": "aws",
}

RESERVED_LABELS = {
    "job",
    "instance",
    "__name__",
    "aws_account_id",
    "aws_region",
    "discovery_type",
    "engine",
    "authentication",
    "redis_name",
    "aws_resource_type",
}

LABEL_PATTERN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*\Z")


def scrapeconfig_name(target):
    identity = target["id"]
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"auto-aws-{suffix}"


def confirmed_auth(target):
    """Rejeita None, strings e ausencia do campo (fail-closed)."""
    auth_required = target.get("auth_required")
    if auth_required is not True and auth_required is not False:
        raise ValueError(
            f"Estado de AUTH desconhecido para {target['id']}"
        )
    return auth_required


def redis_uri(target):
    if target.get("tls") is not True:
        raise ValueError(
            f"TLS nao confirmado para {target['id']}"
        )

    confirmed_auth(target)

    endpoint = target["endpoint"]
    if not isinstance(endpoint, str):
        raise ValueError("Endpoint deve ser uma string")

    parsed = urlsplit(f"//{endpoint}")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Porta invalida: {endpoint}") from exc

    if (
        not parsed.hostname
        or not port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"[a-zA-Z0-9.-]+", parsed.hostname)
    ):
        raise ValueError(f"Endpoint Redis invalido: {endpoint}")

    return f"rediss://{parsed.hostname}:{port}"


def metric_labels(target):
    auth_required = confirmed_auth(target)
    labels = {
        "discovery_type": "aws",
        "aws_account_id": str(target["account_id"]),
        "aws_region": str(target["region"]),
        "engine": str(target["engine"]),
        "authentication": "true" if auth_required else "false",
        "redis_name": str(target["replication_group_id"]),
        "aws_resource_type": target.get("aws_resource_type", target.get("labels", {}).get("aws_resource_type", "replication_group")),
    }

    for name, value in target.get("labels", {}).items():
        # Older target documents include this generated label in labels.
        if name == "aws_resource_type" and value == labels[name]:
            continue
        if (
            not LABEL_PATTERN.fullmatch(name)
            or name.startswith("__")
            or name in RESERVED_LABELS
        ):
            raise ValueError(f"Label nao permitida: {name}")

        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"Valor de label invalido: {name}")

        labels[name] = (
            str(value).lower() if isinstance(value, bool) else str(value)
        )

    return labels


def build_scrapeconfig(target):
    uri = redis_uri(target)
    auth_required = confirmed_auth(target)

    if target.get("endpoint_role") != "primary":
        raise ValueError("Somente endpoints primary")

    if target.get("engine") not in ("redis", "valkey"):
        raise ValueError("Engine nao suportada")

    exporter = EXPORTER if auth_required else EXPORTER_NOAUTH

    return {
        "apiVersion": "monitoring.coreos.com/v1alpha1",
        "kind": "ScrapeConfig",
        "metadata": {
            "name": scrapeconfig_name(target),
            "namespace": NAMESPACE,
            "labels": dict(MANAGED_LABELS),
            "annotations": {
                "redis-observability/target-id": target["id"],
            },
        },
        "spec": {
            "jobName": "redis-aws",
            "scrapeInterval": "30s",
            "scrapeTimeout": "10s",
            "metricsPath": "/scrape",
            "staticConfigs": [{
                "targets": [uri],
                "labels": metric_labels(target),
            }],
            "relabelings": [
                {
                    "sourceLabels": ["__address__"],
                    "targetLabel": "__param_target",
                },
                {
                    "sourceLabels": ["__param_target"],
                    "targetLabel": "instance",
                },
                {
                    "targetLabel": "__address__",
                    "replacement": exporter,
                },
            ],
        },
    }


def generate_manifests(document):
    if document.get("mode") != "targets-dry-run":
        raise ValueError("Documento de targets invalido")

    targets = document.get("targets")
    if not isinstance(targets, list):
        raise ValueError("Campo targets deve ser uma lista")

    if document.get("target_count") != len(targets):
        raise ValueError("Contagem de targets inconsistente")

    manifests = []
    seen_names = set()

    for target in targets:
        manifest = build_scrapeconfig(target)
        name = manifest["metadata"]["name"]
        if name in seen_names:
            raise ValueError(f"ScrapeConfig duplicado: {name}")
        seen_names.add(name)
        manifests.append(manifest)

    return manifests


def main():
    parser = argparse.ArgumentParser(description="Preview de ScrapeConfigs AWS")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        with args.input.open(encoding="utf-8") as handle:
            document = json.load(handle)

        manifests = generate_manifests(document)
        with args.output.open("w", encoding="utf-8") as handle:
            yaml.safe_dump_all(
                manifests,
                handle,
                explicit_start=True,
                sort_keys=False,
                allow_unicode=True,
            )

        print(f"Preview gerado: {args.output}")
        print(f"ScrapeConfigs: {len(manifests)}")
        print("Nenhuma alteracao no Kubernetes.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
