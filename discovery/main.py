#!/usr/bin/env python3
"""Reconciliador local de ScrapeConfigs para o laboratório Redis Observability.

Executar na raiz do repositório:
  python discovery/main.py          # Simular, sem modificar o Kubernetes
  python discovery/main.py --apply  # Reconciliar de fato

O modo local consome um inventário YAML simulado. Não faz discovery AWS.
"""

import argparse
import copy
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

LOG = logging.getLogger("redis-discovery")

NAMESPACE = os.getenv("REDIS_NAMESPACE", "redis-observability")
GROUP = "monitoring.coreos.com"
VERSION = "v1alpha1"
PLURAL = "scrapeconfigs"
INVENTORY = Path(
    os.getenv("REDIS_INVENTORY", "discovery/config/inventory-local.yaml")
)

MANAGED_LABELS = {
    "app.kubernetes.io/part-of": "redis-observability",
    "app.kubernetes.io/managed-by": "redis-discovery",
    "discovery-source": "local",
}
OWNER_SELECTOR = (
    "app.kubernetes.io/managed-by=redis-discovery,discovery-source=local"
)

TAG_MAPPING = {
    "Environment": "environment",
    "Project": "project",
    "CostCenter": "cost_center",
    "BusinessUnit": "business_unit",
}

EXPORTERS = {
    False: "redis-exporter-noauth.redis-observability.svc.cluster.local:9121",
    True: "redis-exporter-auth.redis-observability.svc.cluster.local:9121",
}

DNS_NAME = re.compile(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?\Z")
RESERVED_METRIC_LABELS = {"__name__", "job", "instance"}


def load_inventory() -> List[Dict[str, Any]]:
    """Carrega e valida todo o inventário antes de alterar o cluster."""
    try:
        with INVENTORY.open("r", encoding="utf-8") as stream:
            inventory = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Nao foi possivel ler o inventario {INVENTORY}: {exc}") from exc

    if not isinstance(inventory, dict) or not isinstance(inventory.get("redis"), list):
        raise ValueError("Inventario invalido: a chave 'redis' deve ser uma lista")

    resources = inventory["redis"]
    seen_ids = set()
    seen_addresses = set()
    for index, resource in enumerate(resources):
        if not isinstance(resource, dict):
            raise ValueError(f"Recurso na posicao {index} nao e um objeto")

        name = resource.get("id")
        address = resource.get("address")
        auth = resource.get("authentication")
        tags = resource.get("tags", {})

        if (
            not isinstance(name, str)
            or len(name) > 50
            or not DNS_NAME.fullmatch(name)
        ):
            raise ValueError(f"ID Redis invalido na posicao {index}: {name!r}")
        if name in seen_ids:
            raise ValueError(f"ID Redis duplicado: {name}")
        if not isinstance(address, str) or not address.strip() or address != address.strip():
            raise ValueError(f"Endereco Redis invalido: {name}")
        if address in seen_addresses:
            raise ValueError(f"Endereco Redis duplicado: {address}")
        if type(auth) is not bool:
            raise ValueError(f"Campo authentication deve ser booleano: {name}")
        if not isinstance(tags, dict):
            raise ValueError(f"Tags invalidas: {name}")
        if auth and not address.startswith("redis://"):
            raise ValueError(f"Redis autenticado deve usar a URI redis:// do arquivo de senhas: {name}")
        if not auth and ("@" in address or "//" in address):
            raise ValueError(f"Endereco de Redis sem autenticacao invalido: {name}")
        for key, label in TAG_MAPPING.items():
            if label in RESERVED_METRIC_LABELS:
                raise ValueError(f"Label reservada na configuracao: {label}")
            if key in tags and not isinstance(tags[key], (str, int, float, bool)):
                raise ValueError(f"Tag {key} invalida no Redis {name}")
        seen_ids.add(name)
        seen_addresses.add(address)

    return resources


def build_scrapeconfig(resource: Dict[str, Any]) -> Dict[str, Any]:
    """Monta a configuração desejada sem credenciais."""
    auth = resource["authentication"]
    tags = resource.get("tags", {})
    metric_labels = {
        "authentication": str(auth).lower(),
        "discovery_type": "local",
    }
    for tag_name, prometheus_name in TAG_MAPPING.items():
        if tag_name in tags:
            metric_labels[prometheus_name] = str(tags[tag_name]).lower() if isinstance(tags[tag_name], bool) else str(tags[tag_name])

    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "ScrapeConfig",
        "metadata": {
            "name": f"auto-{resource['id']}",
            "namespace": NAMESPACE,
            "labels": copy.deepcopy(MANAGED_LABELS),
        },
        "spec": {
            "jobName": "redis-auto",
            "scrapeInterval": "30s",
            "scrapeTimeout": "10s",
            "metricsPath": "/scrape",
            "staticConfigs": [{"targets": [resource["address"]], "labels": metric_labels}],
            "relabelings": [
                {"sourceLabels": ["__address__"], "targetLabel": "__param_target"},
                {"sourceLabels": ["__param_target"], "targetLabel": "instance"},
                {"targetLabel": "__address__", "replacement": EXPORTERS[auth]},
            ],
        },
    }


def normalize_subset(actual: Any, desired: Any) -> Any:
    """Compara os campos declarados, ignorando apenas defaults extras da API.

    Preserva todas as listas e valores declarados. Campos extra que o
    Kubernetes acrescenta ao objeto armazenado nao causam atualizacoes.
    """
    if isinstance(desired, dict):
        if not isinstance(actual, dict):
            return actual
        return {key: normalize_subset(actual.get(key), value) for key, value in desired.items()}
    if isinstance(desired, list):
        if not isinstance(actual, list) or len(actual) != len(desired):
            return actual
        return [normalize_subset(item, template) for item, template in zip(actual, desired)]
    return actual


def is_current(existing: Dict[str, Any], desired: Dict[str, Any]) -> bool:
    return (
        normalize_subset(existing.get("spec"), desired["spec"]) == desired["spec"]
        and all(
            existing.get("metadata", {}).get("labels", {}).get(k) == v
            for k, v in MANAGED_LABELS.items()
        )
    )


def kubernetes_client() -> client.CustomObjectsApi:
    """Funciona no Linux com kubeconfig e, no futuro, dentro do Pod."""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CustomObjectsApi()


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconciliar ScrapeConfigs Redis locais")
    parser.add_argument("--apply", action="store_true", help="Modificar o Kubernetes")
    parser.add_argument("--verbose", action="store_true", help="Habilitar logs de diagnostico")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s")

    resources = load_inventory()
    desired = {obj["metadata"]["name"]: obj for obj in map(build_scrapeconfig, resources)}
    api = kubernetes_client()

    # Evita delecoes em caso de falha da API: primeiro lista TODO o escopo.
    fetched = api.list_namespaced_custom_object(
        GROUP, VERSION, NAMESPACE, PLURAL, label_selector=OWNER_SELECTOR
    )
    existing = {obj["metadata"]["name"]: obj for obj in fetched.get("items", [])}

    actions = []
    for name, obj in sorted(desired.items()):
        if name not in existing:
            actions.append(("CREATE", name, obj))
        elif is_current(existing[name], obj):
            actions.append(("UNCHANGED", name, obj))
        else:
            actions.append(("UPDATE", name, obj))

    for name in sorted(existing.keys() - desired.keys()):
        actions.append(("DELETE", name, None))

    for action, name, obj in actions:
        print(f"{action}: {name}", flush=True)
        if not args.apply or action == "UNCHANGED":
            continue
        if action == "CREATE":
            api.create_namespaced_custom_object(
                group=GROUP, version=VERSION, namespace=NAMESPACE,
                plural=PLURAL, body=obj
            )
        elif action == "UPDATE":
            # A biblioteca kubernetes instalada nao aceita _content_type aqui.
            api.patch_namespaced_custom_object(
                group=GROUP, version=VERSION, namespace=NAMESPACE,
                plural=PLURAL, name=name,
                body={"spec": obj["spec"], "metadata": {"labels": MANAGED_LABELS}},
            )
        elif action == "DELETE":
            api.delete_namespaced_custom_object(
                group=GROUP, version=VERSION, namespace=NAMESPACE,
                plural=PLURAL, name=name
            )

    if not args.apply:
        print("DRY RUN: nenhuma alteracao realizada")
    elif not actions:
        print("Nenhum recurso para reconciliar")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, ApiException, config.ConfigException) as exc:
        LOG.error("Discovery interrompido: %s", exc)
        sys.exit(1)
