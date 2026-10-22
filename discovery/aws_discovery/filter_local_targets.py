"""Filtra apenas Redis cuja coleta deve permanecer no Prometheus local."""
import argparse
import json
from pathlib import Path

import yaml


def filter_targets(document, policy):
    if not isinstance(document.get("targets"), list):
        raise ValueError("Documento de targets invalido")
    allowed = {str(a) for a in policy["allowed_accounts"]}
    local = set(policy["local_targets"])
    blocked = set(policy["blocked_targets"])
    selected = []
    for target in document["targets"]:
        account, region = str(target["account_id"]), target["region"]
        kind = target.get("labels", {}).get("aws_resource_type", "replication_group")
        # No targets.py, replication groups nao possuem sufixo :replication_group no id.
        kind = kind if kind == "serverless" else "replication_group"
        name = target["replication_group_id"]
        identifier = f"{account}:{region}:{kind}:{name}"
        if account in allowed and identifier in local and identifier not in blocked:
            selected.append(target)
    return {"mode": "targets-dry-run", "target_count": len(selected), "targets": selected}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--policy", type=Path, default=Path("discovery/config/collection-routing.yaml"))
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    policy = yaml.safe_load(args.policy.read_text(encoding="utf-8"))
    result = filter_targets(source, policy)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"LOCAL_TARGET_COUNT: {result['target_count']}")


if __name__ == "__main__":
    main()
