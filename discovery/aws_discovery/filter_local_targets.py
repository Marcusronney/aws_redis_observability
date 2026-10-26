"""Filtra apenas Redis cuja coleta deve permanecer no Prometheus local."""
import argparse
import json
import ipaddress
import socket
import ssl
from pathlib import Path

import yaml


def check_private_route(target, route):
    """Validate DNS, CIDR and TCP/TLS from the calling Pod before reconcile.

    This is not an authentication test; redis_up is the final acceptance gate.
    Failure aborts the cycle instead of withdrawing previously working targets.
    """
    host, port = target["endpoint"].rsplit(":", 1)
    networks = [ipaddress.ip_network(c) for c in route["destination_cidrs"]]
    if not networks or any(n.prefixlen == 0 for n in networks):
        raise ValueError("Private route requires restricted destination CIDRs")
    addresses = sorted({a[4][0] for a in socket.getaddrinfo(host, int(port), type=socket.SOCK_STREAM)})
    if not addresses:
        raise ValueError("Redis DNS returned no addresses")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_private or not any(ip in n for n in networks):
            raise ValueError(f"Redis address outside authorized route: {address}")
        with socket.create_connection((address, int(port)), timeout=3) as conn:
            if target.get("tls") is True:
                with ssl.create_default_context().wrap_socket(conn, server_hostname=host):
                    pass


def filter_targets(document, policy, probe=check_private_route):
    if not isinstance(document.get("targets"), list):
        raise ValueError("Documento de targets invalido")
    allowed = {str(a) for a in policy["allowed_accounts"]}
    # Explicit list remains solely for the already homologated Virginia target.
    local = set(policy.get("local_targets", []))
    blocked = set(policy.get("blocked_targets", []))
    routes = policy.get("local_networks", [])
    selected = []
    seen = set()
    for target in document["targets"]:
        account, region = str(target["account_id"]), target["region"]
        kind = target.get("aws_resource_type", target.get("labels", {}).get("aws_resource_type", "replication_group"))
        # No targets.py, replication groups nao possuem sufixo :replication_group no id.
        kind = kind if kind in ("serverless", "cluster") else "replication_group"
        name = target["replication_group_id"]
        identifier = f"{account}:{region}:{kind}:{name}"
        if account not in allowed or identifier in blocked:
            continue
        matched = identifier in local
        if not matched:
            for route in routes:
                if (route.get("enabled") is not True
                    or str(route["account_id"]) != account
                    or route["region"] != region
                    or route["vpc_id"] != target.get("vpc_id")):
                    continue
                required = route.get("required_tags", {})
                if not required or not all(target.get("tags", {}).get(k) == v for k, v in required.items()):
                    continue
                if type(target.get("auth_required")) is not bool and policy.get('isolate_pending_targets'):
                    print('TARGET_PENDING: authentication mode not confirmed id=' + target['id'], flush=True)
                    continue
                if type(target.get("auth_required")) is not bool:
                    raise ValueError(f"Authentication not confirmed: {identifier}")
                # Pilot only enables noauth. Authenticated routing requires a
                # separately homologated Vault -> Secret -> exporter mapping.
                if target["auth_required"] and route.get("allow_authenticated") is not True:
                    continue
                try:
                    probe(target, route)
                except Exception:
                    if not policy.get('isolate_pending_targets'):
                        raise
                    print('TARGET_PENDING: private connectivity not confirmed id=' + target['id'], flush=True)
                    continue
                matched = True
                break
        node_identity = target.get('id', identifier)
        if matched and node_identity not in seen:
            selected.append(target)
            seen.add(node_identity)
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
