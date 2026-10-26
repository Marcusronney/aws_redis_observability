"""Authoritative inventory and two-observation deletion planning (no API mutations)."""
from datetime import datetime, timezone
import hashlib
import json


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Inventory timestamp must include timezone")
    return result.timestamp()


def authoritative_inventory(inventory, configuration, now=None):
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    if inventory.get("mode") != "aws-dry-run" or inventory.get("errors") != []:
        raise ValueError("Incomplete AWS inventory")
    scan_time = timestamp(inventory["generated_at"])
    if not -30 <= now - scan_time <= 300:
        raise ValueError("Stale or future AWS inventory")
    expected = {(str(a["account_id"]), r) for a in configuration["accounts"] for r in a["regions"]}
    if not expected or any(not isinstance(a["regions"], list) or not a["regions"] for a in configuration["accounts"]):
        raise ValueError("Explicit nonempty account/region scope required")
    actual, observed = set(), set()
    for account in inventory["accounts"]:
        account_id = str(account["account_id"])
        for region in account["regions"]:
            pair = (account_id, region["region"])
            if pair in actual or region.get("status") != "success" or not isinstance(region.get("resources"), list):
                raise ValueError("Incomplete or duplicate inventory region")
            actual.add(pair)
            if region.get('resource_count', len(region['resources'])) != len(region['resources']):
                raise ValueError('Inventory resource count mismatch')
            identities = set()
            for resource in region["resources"]:
                kind = resource["type"]
                if kind not in ('cluster', 'replication_group', 'serverless'):
                    raise ValueError('Unknown inventory schema cannot authorize deletion')
                identity = (kind, resource['id'])
                if identity in identities:
                    raise ValueError('Duplicate AWS resource')
                identities.add(identity)
                if kind == "replication_group":
                    observed.add(f"{account_id}:{pair[1]}:{resource['id']}")
                    for node in resource.get('nodes', []):
                        observed.add(f"{account_id}:{pair[1]}:{resource['id']}:node/{node['cluster_id']}/{node['node_id']}")
                elif kind == "serverless":
                    observed.add(f"{account_id}:{pair[1]}:serverless:{resource['id']}")
                elif kind == 'cluster' and not resource.get('replication_group_id'):
                    observed.add(f"{account_id}:{pair[1]}:cluster:{resource['id']}")
    if actual != expected:
        raise ValueError("Inventory does not cover the configured scope exactly")
    return scan_time, observed, expected


def deletion_plan(existing, desired, inventory, configuration, policy, state, now=None,
                  grace_seconds=60, max_deletions=1):
    scan_time, observed, scope = authoritative_inventory(inventory, configuration, now)
    if scan_time <= state.get("last_scan", 0):
        raise ValueError("A replayed inventory cannot advance reconciliation")
    fingerprint = hashlib.sha256(json.dumps([configuration, policy], sort_keys=True).encode()).hexdigest()
    prior_missing = state.get("missing", {}) if state.get("scope_hash") == fingerprint else {}
    missing, delete = {}, []
    for name, item in sorted(existing.items()):
        identity = item.get("metadata", {}).get("annotations", {}).get("redis-observability/target-id")
        # A filtered, unavailable, unknown-type or unscanned resource is not proof of deletion.
        if name in desired or not identity or identity in observed:
            continue
        # An unavailable group is not authoritative about disappearance of a member.
        if ':node/' in identity:
            parent = identity.split(':node/', 1)[0]
            unavailable = {f"{a['account_id']}:{r['region']}:{item['id']}"
                for a in inventory['accounts'] for r in a['regions'] for item in r['resources']
                if item['type'] == 'replication_group' and item.get('status') != 'available'}
            if parent in unavailable:
                continue
        parts = identity.split(":")
        if len(parts) not in (3, 4) or tuple(parts[:2]) not in scope:
            continue
        old = prior_missing.get(identity, {})
        entry = {"count": old.get("count", 0) + 1, "first_seen": old.get("first_seen", scan_time)}
        missing[identity] = entry
        if entry["count"] >= 2 and scan_time - entry["first_seen"] >= grace_seconds and len(delete) < max_deletions:
            delete.append(name)
    return delete, {**state, "last_scan": scan_time, "scope_hash": fingerprint, "missing": missing}
