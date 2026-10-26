
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from .diagnostics import phase
from .accounts import load_config, resolve_accounts, enabled_regions
from .scopes import complete_scopes
from concurrent.futures import ThreadPoolExecutor

from .client import (
    create_session,
    create_elasticache_client,
)
from .elasticache import (
    discover_resources,
    get_resource_tags,
)
from .normalize import (
    normalize_cluster,
    normalize_replication_group,
    normalize_serverless,
    allowed_labels,
)


DEFAULT_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "vault-local-accounts.yaml"
)


def report_error(account_id, region, exc):
    # Log machine-readable error type/code, never credential_process output/bodies.
    code = getattr(exc, 'response', {}).get('Error', {}).get('Code', '')
    print(f'DISCOVERY_ERROR account={account_id} region={region} type={type(exc).__name__} code={code}',
          file=sys.stderr)


def discover_region(session, region, tag_mapping):
    client = create_elasticache_client(session, region)

    phase('aws-describe-start')
    inventory = discover_resources(client)
    phase('aws-describe-complete')
    resources = []
    subnet_vpcs = {}
    group_vpcs = {}

    normalizers = {
        "clusters": normalize_cluster,
        "replication_groups": normalize_replication_group,
        "serverless": normalize_serverless,
    }

    for kind, normalizer in normalizers.items():
        for item in inventory[kind]:
            arn = item.get("ARN")

            phase('aws-tags-read-start')
            tags = get_resource_tags(client, arn)
            phase('aws-tags-read-complete')

            labels = allowed_labels(
                tags,
                tag_mapping,
            )

            resource = normalizer(item, labels)
            resource["region"] = region
            # Keep operational tags out of metric labels. They authorize routing
            # and authentication, but never contain passwords.
            resource["tags"] = tags
            if kind == "clusters" and resource.get("cache_subnet_group"):
                group = resource['cache_subnet_group']
                if group not in group_vpcs:
                    response = client.describe_cache_subnet_groups(CacheSubnetGroupName=group)
                    group_vpcs[group] = response['CacheSubnetGroups'][0]['VpcId']
                resource['vpc_id'] = group_vpcs[group]
            if kind == "serverless":
                subnet_ids = resource["subnet_ids"]
                missing = sorted(set(subnet_ids) - subnet_vpcs.keys())
                if missing:
                    ec2 = session.client("ec2", region_name=region)
                    for subnet in ec2.describe_subnets(SubnetIds=missing)["Subnets"]:
                        subnet_vpcs[subnet["SubnetId"]] = subnet["VpcId"]
                vpcs = {subnet_vpcs[s] for s in subnet_ids}
                if len(vpcs) != 1:
                    raise ValueError("Serverless subnets must resolve to one VPC")
                resource["vpc_id"] = vpcs.pop()

            resources.append(resource)

    clusters = {r['id']: r for r in resources if r['type'] == 'cluster'}
    for resource in resources:
        if resource['type'] == 'replication_group':
            # ReadEndpoint is absent for cluster mode enabled. Resolve the actual
            # member endpoint from DescribeCacheClusters(ShowCacheNodeInfo=True).
            for node in resource.get('nodes', []):
                if not node['endpoint']:
                    cluster = clusters.get(node['cluster_id'], {})
                    node['endpoint'] = next((n['endpoint'] for n in cluster.get('nodes', [])
                        if n['node_id'] == node['node_id']), None)
            vpcs = {clusters[c].get('vpc_id') for c in resource.get('member_clusters', []) if c in clusters}
            if len(vpcs) == 1 and None not in vpcs:
                resource['vpc_id'] = vpcs.pop()
    return resources


def discover_accounts(configuration):
    entries = resolve_accounts(configuration)
    workers = int(configuration.get('account_workers', 4))
    if not 1 <= workers <= 16:
        raise ValueError('account_workers must be between 1 and 16')
    result = {'mode': 'aws-dry-run', 'generated_at': datetime.now(timezone.utc).isoformat(),
              'accounts': [], 'errors': []}

    def discover(account):
        identifier = str(account['account_id'])
        output = dict(name=account['name'], account_id=identifier, regions=[], status='pending_access')
        resolved = dict(account, regions=[])
        errors = []
        try:
            phase('aws-session-start')
            session = create_session(account)
            phase('aws-session-complete')
            resolved['regions'] = enabled_regions(session, account['regions'])
        except Exception as exc:
            report_error(identifier, None, exc)
            errors.append(dict(account_id=identifier, region=None, error_type=type(exc).__name__))
            return output, resolved, errors
        output['status'] = 'inventory_ready'
        for region in resolved['regions']:
            try:
                resources = discover_region(session, region, configuration.get('tag_labels', {}))
                for resource in resources:
                    resource['account_id'] = identifier
                output['regions'].append(dict(region=region, status='success',
                                               resource_count=len(resources), resources=resources))
            except Exception as exc:
                report_error(identifier, region, exc)
                errors.append(dict(account_id=identifier, region=region, error_type=type(exc).__name__))
                output['regions'].append(dict(region=region, status='error', resources=[]))
        if errors:
            output['status'] = 'partial_inventory'
        return output, resolved, errors

    resolved = dict(configuration, accounts=[])
    resolved.pop('catalog', None)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for account, explicit, errors in pool.map(discover, entries):
            result['accounts'].append(account)
            result['errors'].extend(errors)
            resolved['accounts'].append(explicit)
    return result, resolved


def main():
    parser = argparse.ArgumentParser(description='AWS Redis/Valkey Discovery')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--resolved-config', type=Path)
    args = parser.parse_args()
    try:
        configuration = load_config(args.config)
        result, resolved = discover_accounts(configuration)
        if result['errors']:
            if not configuration.get('isolate_failed_scopes'):
                print(json.dumps(result, indent=2, ensure_ascii=False))
                return 1
            # Validate that at least one scope is complete before allowing reconcile.
            complete_scopes(result, resolved)
        if args.resolved_config:
            args.resolved_config.write_text(yaml.safe_dump(resolved), encoding='utf-8')
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print('DISCOVERY_FAILED type=' + type(exc).__name__ + '; previous targets preserved', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
