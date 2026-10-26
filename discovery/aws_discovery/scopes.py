"""Partition partial inventory without treating an inaccessible scope as empty."""
from copy import deepcopy


def complete_scopes(inventory, configuration):
    expected, identifiers = set(), set()
    for account in configuration['accounts']:
        identifier = str(account['account_id'])
        if identifier in identifiers or not isinstance(account['regions'], list):
            raise ValueError('Invalid resolved account configuration')
        identifiers.add(identifier)
        expected.update((identifier, r) for r in account['regions'])
    errors = inventory.get('errors', [])
    failed_accounts = {str(e['account_id']) for e in errors if e.get('region') is None}
    failed_regions = {(str(e['account_id']), e['region']) for e in errors if e.get('region') is not None}
    actual, ready, seen_accounts = set(), {}, set()
    for account in inventory['accounts']:
        identifier = str(account['account_id'])
        if identifier not in identifiers or identifier in seen_accounts:
            raise ValueError('Unexpected or duplicate inventory account')
        seen_accounts.add(identifier)
        for region in account['regions']:
            pair = (identifier, region['region'])
            if pair not in expected or pair in actual:
                raise ValueError('Unexpected or duplicate inventory scope')
            actual.add(pair)
            if region.get('status') == 'success' and identifier not in failed_accounts and pair not in failed_regions:
                ready.setdefault(identifier, []).append(deepcopy(region))
            elif region.get('status') != 'error' or (identifier not in failed_accounts and pair not in failed_regions):
                raise ValueError('Unexplained inventory failure')
    missing = expected - actual
    if any(a not in failed_accounts and (a, r) not in failed_regions for a, r in missing):
        raise ValueError('Unexplained missing scope')
    if not ready:
        raise ValueError('No complete scopes available; preserve all targets')
    result = deepcopy(inventory)
    result['accounts'] = [dict(account_id=a, regions=rows) for a, rows in sorted(ready.items())]
    result['errors'] = []
    config = deepcopy(configuration)
    config['accounts'] = [dict(a, regions=[r['region'] for r in ready[str(a['account_id'])]])
                          for a in configuration['accounts'] if str(a['account_id']) in ready]
    return result, config
