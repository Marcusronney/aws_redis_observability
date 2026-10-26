"""Single account catalog, explicit or Organizations. No AWS writes or secrets.

Organizations failure is fail-closed: never substitute an empty successful list.
OU membership controls admission; IAM bootstrap remains a separate concern.
"""
from copy import deepcopy
from pathlib import Path
import re

import yaml

from .client import AWS_CONFIG, create_session


class CatalogError(RuntimeError):
    pass


def load_config(path):
    path = Path(path)
    result = yaml.safe_load(path.read_text(encoding='utf-8'))
    if result.get('catalog_file'):
        catalog = (path.parent / result.pop('catalog_file')).resolve()
        result['catalog'] = yaml.safe_load(catalog.read_text(encoding='utf-8'))
    return result


def account_id(value):
    value = str(value)
    if not re.fullmatch(r'[0-9]{12}', value):
        raise CatalogError('Invalid account ID in catalog')
    return value


def enabled_regions(session, specification):
    if specification == 'auto':
        return sorted(r['RegionName'] for r in session.client('ec2', region_name='us-east-1', config=AWS_CONFIG)
                      .describe_regions(AllRegions=False)['Regions'])
    if not isinstance(specification, list) or not specification:
        raise CatalogError('Use auto or a nonempty region list')
    if any(not isinstance(r, str) or not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-[0-9]+', r) for r in specification):
        raise CatalogError('Invalid region list')
    return sorted(set(specification))


def resolve_accounts(configuration, purpose='discovery'):
    """Return entries suitable for create_session; legacy explicit configs still work."""
    if 'catalog' not in configuration:
        entries = deepcopy(configuration.get('accounts', []))
        for entry in entries:
            entry['account_id'] = account_id(entry['account_id'])
        _unique(entries)
        return entries
    catalog = configuration['catalog']
    defaults = catalog.get('defaults', {})
    central = account_id(defaults['central_account_id'])
    profile = defaults.get('network_profile') if purpose == 'network' else defaults.get('discovery_profile')
    role_name = defaults.get('member_role_name', 'RedisObservabilityMemberDiscoveryRole')
    if not re.fullmatch(r'[A-Za-z0-9+=,.@_-]{1,64}', role_name):
        raise CatalogError('Invalid member role name')
    source = catalog.get('source', 'explicit')
    if source == 'explicit':
        raw = [a for a in catalog.get('accounts', []) if a.get('enabled', True)]
    elif source == 'organizations':
        raw = organization_accounts(catalog, profile)
    else:
        raise CatalogError('Unknown catalog source')
    exclusions = {account_id(a) for a in catalog.get('exclude_account_ids', [])}
    entries = []
    for item in raw:
        identifier = account_id(item['account_id'])
        if identifier in exclusions:
            continue
        entry = dict(name=item.get('name', identifier), account_id=identifier,
                     regions=item.get('regions', defaults.get('regions', 'auto')))
        if profile:
            entry['profile'] = profile
        # Only the central account can use the unassumed base identity.
        if identifier != central or item.get('assume_member_role', False):
            entry['role_arn'] = item.get('role_arn', f'arn:aws:iam::{identifier}:role/{role_name}')
            if not re.fullmatch(r'arn:aws:iam::' + identifier + r':role/[A-Za-z0-9+=,.@_/-]+', entry['role_arn']):
                raise CatalogError('Role ARN does not match catalog account')
        entries.append(entry)
    _unique(entries)
    return sorted(entries, key=lambda a: a['account_id'])


def _unique(entries):
    identifiers = [a['account_id'] for a in entries]
    if not identifiers or len(identifiers) != len(set(identifiers)):
        raise CatalogError('Catalog must be nonempty and contain unique accounts')


def organization_accounts(catalog, profile):
    org = catalog.get('organizations', {})
    expected = org.get('expected_organization_id', '')
    parents = org.get('ou_ids', [])
    if not re.fullmatch(r'o-[a-z0-9]{10,32}', expected) or not parents:
        raise CatalogError('An expected Organization and explicit OUs are required')
    if any(not re.fullmatch(r'ou-[a-z0-9]{4,32}-[a-z0-9]{8,32}', p) for p in parents):
        raise CatalogError('Invalid OU identifier')
    reader = dict(account_id=account_id(org['reader_account_id']), profile=profile)
    if org.get('reader_role_arn'):
        reader['role_arn'] = org['reader_role_arn']
    try:
        session = create_session(reader)
        api = session.client('organizations', region_name='us-east-1', config=AWS_CONFIG)
        if api.describe_organization()['Organization']['Id'] != expected:
            raise CatalogError('Organization mismatch')
        found, visited, pending = {}, set(), list(parents)
        while pending:
            parent = pending.pop()
            if parent in visited:
                continue
            visited.add(parent)
            for page in api.get_paginator('list_accounts_for_parent').paginate(ParentId=parent):
                for item in page['Accounts']:
                    # State is current; Status retained solely for older API fixtures.
                    if item.get('State', item.get('Status')) == 'ACTIVE':
                        found[item['Id']] = dict(account_id=item['Id'], name=item.get('Name', item['Id']))
            if org.get('recursive', True):
                for page in api.get_paginator('list_organizational_units_for_parent').paginate(ParentId=parent):
                    pending.extend(o['Id'] for o in page['OrganizationalUnits'])
        return list(found.values())
    except CatalogError:
        raise
    except Exception:
        raise CatalogError('Organizations catalog unavailable; preserve previous inventory') from None


def main():
    """Read-only admission check; no network provisioning or cache creation."""
    import argparse
    from concurrent.futures import ThreadPoolExecutor
    import json
    parser = argparse.ArgumentParser(description='Resolve the shared AWS account catalog')
    parser.add_argument('--config', type=Path, default=Path('discovery/config/vault-local-accounts.yaml'))
    parser.add_argument('--purpose', choices=['discovery', 'network'], default='network')
    parser.add_argument('--check-access', action='store_true')
    args = parser.parse_args()
    try:
        entries = resolve_accounts(load_config(args.config), args.purpose)
        def inspect(entry):
            result = dict(account_id=entry['account_id'], name=entry['name'], status='admitted')
            if entry.get('role_arn'):
                result['role_arn'] = entry['role_arn']
            if args.check_access:
                try:
                    result['regions'] = enabled_regions(create_session(entry), entry['regions'])
                    result['status'] = 'inventory_ready'
                except Exception as exc:
                    result.update(status='pending_access', error_type=type(exc).__name__)
            return result
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(inspect, entries))
        print(json.dumps({'accounts': rows}, indent=2))
        return int(any(r['status'] == 'pending_access' for r in rows))
    except Exception as exc:
        print(json.dumps({'status': 'catalog_unavailable', 'error_type': type(exc).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
