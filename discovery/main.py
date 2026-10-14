
import argparse
import copy
import os
import re
from pathlib import Path

import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException


NAMESPACE = "redis-observability"
GROUP = "monitoring.coreos.com"
VERSION = "v1alpha1"
PLURAL = "scrapeconfigs"

INVENTORY = Path(
    os.getenv(
        "REDIS_INVENTORY",
        "discovery/config/inventory-local.yaml"
    )
)

MANAGED_LABELS = {
    "app.kubernetes.io/part-of": "redis-observability",
    "app.kubernetes.io/managed-by": "redis-discovery",
    "discovery-source": "local"
}

TAG_MAPPING = {
    "Environment": "environment",
    "Project": "project",
    "CostCenter": "cost_center",
    "BusinessUnit": "business_unit"
}

EXPORTERS = {
    False: (
        "redis-exporter-noauth."
        "redis-observability.svc.cluster.local:9121"
    ),
    True: (
        "redis-exporter-auth."
        "redis-observability.svc.cluster.local:9121"
    )
}


def load_inventory():
    with INVENTORY.open(encoding="utf-8") as file:
        inventory = yaml.safe_load(file)

    if not isinstance(inventory, dict):
        raise ValueError("Inventario invalido")

    resources = inventory.get("redis")

    if not isinstance(resources, list):
        raise ValueError("A chave redis deve ser uma lista")

    ids = set()

    for resource in resources:
        if not isinstance(resource, dict):
            raise ValueError("Recurso Redis invalido")

        name = resource.get("id")
        address = resource.get("address")
        auth = resource.get("authentication")
        tags = resource.get("tags", {})

        if not isinstance(name, str) or not re.fullmatch(
            r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", name
        ) or len(name) > 50:
            raise ValueError("ID Redis invalido")

        if name in ids:
            raise ValueError(f"Redis duplicado: {name}")

        if not isinstance(address, str) or not address:
            raise ValueError(f"Endereco invalido: {name}")

        if type(auth) is not bool:
            raise ValueError(f"Autenticacao invalida: {name}")

        if not isinstance(tags, dict):
            raise ValueError(f"Tags invalidas: {name}")

        ids.add(name)

    return resources


def build_scrapeconfig(resource):
    name = "auto-" + resource["id"]
    auth = resource["authentication"]
    address = resource["address"]
    tags = resource.get("tags", {})

    labels = {
        "authentication": str(auth).lower(),
        "discovery_type": "local"
    }

    for aws_tag, prometheus_label in TAG_MAPPING.items():
        if aws_tag in tags:
            labels[prometheus_label] = str(tags[aws_tag])

    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "ScrapeConfig",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": copy.deepcopy(MANAGED_LABELS)
        },
        "spec": {
            "jobName": "redis-auto",
            "scrapeInterval": "30s",
            "scrapeTimeout": "10s",
            "metricsPath": "/scrape",
            "staticConfigs": [
                {
                    "targets": [address],
                    "labels": labels
                }
            ],
            "relabelings": [
                {
                    "sourceLabels": ["__address__"],
                    "targetLabel": "__param_target"
                },
                {
                    "sourceLabels": ["__param_target"],
                    "targetLabel": "instance"
                },
                {
                    "targetLabel": "__address__",
                    "replacement": EXPORTERS[auth]
                }
            ]
        }
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplicar alteracoes no Kubernetes"
    )
    args = parser.parse_args()

    # Validar todo o inventario antes de alterar recursos.
    resources = load_inventory()

    config.load_kube_config()
    api = client.CustomObjectsApi()

    desired = {
        obj["metadata"]["name"]: obj
        for obj in map(build_scrapeconfig, resources)
    }

    result = api.list_namespaced_custom_object(
        GROUP,
        VERSION,
        NAMESPACE,
        PLURAL,
        label_selector=(
            "app.kubernetes.io/managed-by=redis-discovery,"
            "discovery-source=local"
        )
    )

    existing = {
        obj["metadata"]["name"]: obj
        for obj in result["items"]
    }

    actions = []

    for name, obj in desired.items():
        if name not in existing:
            actions.append(("CREATE", name, obj))
        elif existing[name]["spec"] != obj["spec"]:
            actions.append(("UPDATE", name, obj))
        else:
            actions.append(("UNCHANGED", name, obj))

    for name in sorted(existing.keys() - desired.keys()):
        actions.append(("DELETE", name, None))

    for action, name, obj in actions:
        print(f"{action}: {name}")

        if not args.apply or action == "UNCHANGED":
            continue

        if action == "CREATE":
            api.create_namespaced_custom_object(
                GROUP, VERSION, NAMESPACE, PLURAL, obj
            )

        elif action == "UPDATE":
            api.patch_namespaced_custom_object(
                GROUP,
                VERSION,
                NAMESPACE,
                PLURAL,
                name,
                {"spec": obj["spec"]},
                _content_type="application/merge-patch+json"
            )

        elif action == "DELETE":
            api.delete_namespaced_custom_object(
                GROUP, VERSION, NAMESPACE, PLURAL, name
            )

    if not args.apply:
        print("DRY RUN: nenhuma alteracao realizada")


if __name__ == "__main__":
    main()
