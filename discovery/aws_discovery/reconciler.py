
#!/usr/bin/env python3
"""Reconciliador AWS de ScrapeConfigs Kubernetes."""

import argparse
import json
import logging
import sys
from pathlib import Path

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from discovery.aws_discovery.scrapeconfig import (
    generate_manifests,
)

LOG = logging.getLogger("redis-discovery-aws")

GROUP = "monitoring.coreos.com"
VERSION = "v1alpha1"
PLURAL = "scrapeconfigs"
NAMESPACE = "redis-observability"

OWNER_SELECTOR = (
    "app.kubernetes.io/managed-by=redis-discovery-aws,"
    "discovery-source=aws"
)


def get_kubernetes_client():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

    return client.CustomObjectsApi()


def normalize_subset(actual, desired):
    """Ignora campos adicionais inseridos pela API Kubernetes."""
    if isinstance(desired, dict):
        if not isinstance(actual, dict):
            return actual

        return {
            key: normalize_subset(actual.get(key), value)
            for key, value in desired.items()
        }

    if isinstance(desired, list):
        if not isinstance(actual, list):
            return actual

        if len(actual) != len(desired):
            return actual

        return [
            normalize_subset(a, d)
            for a, d in zip(actual, desired)
        ]

    return actual


def is_current(existing, desired):
    return (
        normalize_subset(
            existing.get("spec"), desired["spec"]
        ) == desired["spec"]
        and all(
            existing.get("metadata", {})
            .get("labels", {})
            .get(key) == value
            for key, value in desired["metadata"]["labels"].items()
        )
        and all(
            existing.get("metadata", {})
            .get("annotations", {})
            .get(key) == value
            for key, value in desired["metadata"]
            .get("annotations", {}).items()
        )
    )


def plan_actions(api, desired_manifests):
    existing_response = api.list_namespaced_custom_object(
        group=GROUP,
        version=VERSION,
        namespace=NAMESPACE,
        plural=PLURAL,
        label_selector=OWNER_SELECTOR,
    )

    existing = {
        item["metadata"]["name"]: item
        for item in existing_response.get("items", [])
    }

    desired = {
        item["metadata"]["name"]: item
        for item in desired_manifests
    }

    if len(desired) != len(desired_manifests):
        raise ValueError("Nomes de ScrapeConfig duplicados")

    actions = []

    for name, manifest in sorted(desired.items()):
        if name not in existing:
            actions.append(("CREATE", name, manifest))
        elif is_current(existing[name], manifest):
            actions.append(("UNCHANGED", name, manifest))
        else:
            actions.append(("UPDATE", name, manifest))

    # Primeira versao: nao remove recursos automaticamente.
    for name in sorted(existing.keys() - desired.keys()):
        actions.append(("STALE", name, None))

    return actions


def apply_actions(api, actions):
    for action, name, manifest in actions:
        print(f"{action}: {name}", flush=True)

        if action == "CREATE":
            api.create_namespaced_custom_object(
                group=GROUP,
                version=VERSION,
                namespace=NAMESPACE,
                plural=PLURAL,
                body=manifest,
            )

        elif action == "UPDATE":
            api.patch_namespaced_custom_object(
                group=GROUP,
                version=VERSION,
                namespace=NAMESPACE,
                plural=PLURAL,
                name=name,
                body={
                    "spec": manifest["spec"],
                    "metadata": {
                        "labels": manifest["metadata"]["labels"],
                        "annotations": manifest["metadata"]
                        .get("annotations", {}),
                    },
                },
            )

        # UNCHANGED e STALE nao modificam o Kubernetes.


def main():
    parser = argparse.ArgumentParser(
        description="Reconciliar ScrapeConfigs AWS"
    )

    parser.add_argument(
        "--input",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplicar CREATE e UPDATE no Kubernetes",
    )

    args = parser.parse_args()

    with args.input.open(encoding="utf-8") as file:
        data = json.load(file)

    desired_manifests = generate_manifests(data)

    api = get_kubernetes_client()
    actions = plan_actions(api, desired_manifests)

    if args.apply:
        apply_actions(api, actions)
    else:
        for action, name, _ in actions:
            print(f"{action}: {name}")

        print("DRY RUN: nenhuma alteracao realizada")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        ApiException,
        config.ConfigException,
    ) as exc:
        LOG.error("Reconciliacao AWS interrompida: %s", exc)
        sys.exit(1)
