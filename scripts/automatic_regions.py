"""Host controller: discover enabled regions, provision owned peerings, publish scopes.

No passwords enter this process or Terraform. Never deletes a link just because
a cache disappeared. Each VPC has an independent Terraform state. Linux only.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import time

import boto3
from botocore.config import Config
import yaml
from discovery.aws_discovery.accounts import load_config, resolve_accounts, enabled_regions
from discovery.aws_discovery.client import create_session

ROOT = Path(__file__).resolve().parents[1]
SDK = Config(connect_timeout=5, read_timeout=20, retries={'max_attempts': 2})


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout


def client(session, service, region):
    return session.client(service, region_name=region, config=SDK)


def regions(session, account):
    if account.get('regions') != 'auto':
        return sorted(set(account['regions']))
    return sorted(r['RegionName'] for r in client(session, 'ec2', 'us-east-1')
                  .describe_regions(AllRegions=False)['Regions'])


def scan(profile, region, required):
    """Inventory every Redis/Valkey; only opted-in caches authorize network writes."""
    session = create_session(profile) if isinstance(profile, dict) else boto3.Session(profile_name=profile, region_name=region)
    cache = client(session, 'elasticache', region)
    ec2 = client(session, 'ec2', region)
    clusters = []
    for page in cache.get_paginator('describe_cache_clusters').paginate(ShowCacheNodeInfo=True):
        clusters.extend(page['CacheClusters'])
    by_id = {c['CacheClusterId']: c for c in clusters}
    resources = []
    for operation, key in [('describe_replication_groups', 'ReplicationGroups'),
                           ('describe_serverless_caches', 'ServerlessCaches')]:
        for page in cache.get_paginator(operation).paginate():
            resources.extend((key, c) for c in page[key])
    resources.extend(('CacheClusters', c) for c in clusters if not c.get('ReplicationGroupId'))
    networks, inventory, errors = {}, [], []
    for kind, resource in resources:
        engine = resource.get('Engine')
        if kind == 'ReplicationGroups' and not engine:
            engines = {by_id[m].get('Engine') for m in resource.get('MemberClusters', []) if m in by_id}
            engine = engines.pop() if len(engines) == 1 else 'unknown'
        if engine not in ('redis', 'valkey') and kind != 'ReplicationGroups':
            continue
        name = resource.get('ServerlessCacheName', resource.get('ReplicationGroupId', resource.get('CacheClusterId')))
        row = {'name': name, 'arn': resource['ARN'], 'region': region,
               'resource_type': {'ServerlessCaches': 'serverless', 'ReplicationGroups': 'replication_group',
                                 'CacheClusters': 'cluster'}[kind],
               'engine': engine or 'unknown', 'status': resource.get('Status', resource.get('CacheClusterStatus')),
               'inventory_fresh': True, 'network_authorized': False,
               'network_authorization': 'tags_unavailable', 'collection_status': 'not_checked'}
        inventory.append(row)
        try:
            tags = {t['Key']: t['Value'] for t in cache.list_tags_for_resource(ResourceName=resource['ARN'])['TagList']}
        except Exception as exc:
            errors.append({'cache': name, 'status': 'tags_read_failed', 'type': type(exc).__name__})
            continue
        authorized = bool(required) and all(tags.get(k) == v for k, v in required.items())
        row.update(tags_present=bool(tags), network_authorized=authorized,
                   missing_adoption_tags=sorted(k for k, v in required.items() if tags.get(k) != v),
                   network_authorization='authorized_by_tag' if authorized else 'missing_adoption_tag')
        try:
            network = resolve_cache_network(cache, ec2, kind, resource, by_id)
            row['vpc_id'] = network['vpc_id']
        except Exception as exc:
            row['network_status'] = 'metadata_pending'
            errors.append({'cache': name, 'status': 'network_metadata_pending', 'type': type(exc).__name__})
            continue
        if not authorized:
            continue
        vpc = network['vpc_id']
        item = networks.setdefault(vpc, {'vpc_id': vpc, 'region': region, 'subnets': [],
                                          'security_groups': [], 'caches': []})
        item['subnets'] = sorted(set(item['subnets']) | set(network['subnets']))
        item['security_groups'] = sorted(set(item['security_groups']) | set(network['security_groups']))
        item['caches'].append(name)
    return {'networks': list(networks.values()), 'caches': inventory, 'errors': errors}


def resolve_cache_network(cache, ec2, kind, resource, by_id):
    """Resolve metadata even for untagged caches, without provisioning anything."""
    if kind == 'ServerlessCaches':
        subnet_ids = resource['SubnetIds']
        groups = resource.get('SecurityGroupIds', [])
    else:
        members = [by_id[c] for c in resource['MemberClusters']] if kind == 'ReplicationGroups' else [resource]
        if not members:
            raise ValueError('Cache members are not available yet')
        subnet_ids, groups = [], []
        for member in members:
            subnet_group = cache.describe_cache_subnet_groups(
                CacheSubnetGroupName=member['CacheSubnetGroupName'])['CacheSubnetGroups'][0]
            subnet_ids.extend(s['SubnetIdentifier'] for s in subnet_group['Subnets'])
            groups.extend(g['SecurityGroupId'] for g in member.get('SecurityGroups', []))
    subnets = ec2.describe_subnets(SubnetIds=sorted(set(subnet_ids)))['Subnets']
    vpcs = {s['VpcId'] for s in subnets}
    if len(vpcs) != 1:
        raise ValueError('Cache must resolve to exactly one VPC')
    vpc = vpcs.pop()
    return {'vpc_id': vpc, 'subnets': sorted(set(subnet_ids)), 'security_groups': sorted(set(groups))}


def cidrs(ec2, vpc):
    item = ec2.describe_vpcs(VpcIds=[vpc])['Vpcs'][0]
    return sorted(x['CidrBlock'] for x in item['CidrBlockAssociationSet']
                  if x['CidrBlockState']['State'] == 'associated')


def no_overlap(candidates, existing):
    for value in candidates:
        network = ipaddress.ip_network(value)
        if network.version != 4 or not network.is_private or network.prefixlen == 0:
            raise ValueError('Only restricted private IPv4 networks are supported')
        if any(network.overlaps(ipaddress.ip_network(other)) for other in existing):
            raise ValueError('CIDR overlap requires a different connectivity design')


def route_table(ec2, vpc, subnet):
    items = ec2.describe_route_tables(Filters=[{'Name': 'association.subnet-id', 'Values': [subnet]}])['RouteTables']
    if not items:
        items = ec2.describe_route_tables(Filters=[{'Name': 'vpc-id', 'Values': [vpc]},
                                                 {'Name': 'association.main', 'Values': ['true']}])['RouteTables']
    if len(items) != 1:
        raise ValueError('Ambiguous route table')
    return items[0]


def validate_routes(table, destinations, peering=None):
    for route in table['Routes']:
        if route.get('DestinationCidrBlock') in destinations:
            if not peering or route.get('VpcPeeringConnectionId') != peering:
                raise ValueError('Existing route is not owned by this link')


def terraform_link(directory, variables):
    """Materialize a fixed two-provider module, with durable per-VPC state."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'main.tf').write_text((ROOT / 'terraform/environments/network-link/main.tf').read_text())
    atomic(directory / 'terraform.tfvars.json', variables)
    identity = ROOT / 'terraform/environments/network-identity'
    lock = identity / '.terraform.lock.hcl'
    if lock.exists():
        (directory / '.terraform.lock.hcl').write_bytes(lock.read_bytes())
    log = directory / 'terraform.log'
    with log.open('w') as output:
        log.chmod(0o600)
        for command in ('init', 'apply'):
            args = ['terraform', '-chdir=' + str(directory), command, '-input=false', '-no-color']
            mirror = identity / '.terraform/providers'
            if command == 'init' and mirror.exists():
                args.append('-plugin-dir=' + str(mirror))
            if command == 'apply':
                args.append('-auto-approve')
            subprocess.run(args, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=360)
    return json.loads(run(['terraform', '-chdir=' + str(directory), 'output', '-json']))['peering_id']['value']


def gateway_script(ip, destinations):
    """Only a dedicated chain; no flush/replacement of existing firewall rules."""
    ipaddress.ip_address(ip)
    for value in destinations:
        ipaddress.ip_network(value)
    lines = ['set -eu', 'sysctl -w net.ipv4.ip_forward=1',
             'iptables -t nat -N REDIS_AUTO_SNAT 2>/dev/null || true',
             'iptables -t nat -C POSTROUTING -j REDIS_AUTO_SNAT 2>/dev/null || iptables -t nat -I POSTROUTING 1 -j REDIS_AUTO_SNAT']
    for destination in sorted(set(destinations)):
        rule = '-d ' + destination + ' -p tcp --dport 6379 -j SNAT --to-source ' + ip
        lines.append('iptables -t nat -C REDIS_AUTO_SNAT ' + rule + ' 2>/dev/null || iptables -t nat -A REDIS_AUTO_SNAT ' + rule)
    return '\n'.join(lines)


def configure_gateway(session, gateway, ip, destinations):
    ec2 = client(session, 'ec2', gateway['region'])
    ec2.modify_instance_attribute(InstanceId=gateway['instance_id'], SourceDestCheck={'Value': False})
    ssm = client(session, 'ssm', gateway['region'])
    result = ssm.send_command(InstanceIds=[gateway['instance_id']], DocumentName='AWS-RunShellScript',
        Parameters={'commands': [gateway_script(ip, destinations)]}, TimeoutSeconds=60)
    command = result['Command']['CommandId']
    for _ in range(45):
        time.sleep(2)
        try:
            invocation = ssm.get_command_invocation(CommandId=command, InstanceId=gateway['instance_id'])
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if invocation['Status'] == 'Success':
            return
        if invocation['Status'] in ('Failed', 'Cancelled', 'TimedOut'):
            break
    raise RuntimeError('Gateway SSM configuration not confirmed')


def host_routes(interface, destinations):
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', interface):
        raise ValueError('Invalid WireGuard interface')
    peers = run(['wg', 'show', interface, 'peers']).split()
    if len(peers) != 1:
        raise ValueError('Exactly one central WireGuard peer required')
    allowed = run(['wg', 'show', interface, 'allowed-ips']).split()
    prefixes = set(allowed[1:]) - {'(none)'}
    prefixes.update(destinations)
    run(['wg', 'set', interface, 'peer', peers[0], 'allowed-ips', ','.join(sorted(prefixes))])
    for destination in destinations:
        ipaddress.ip_network(destination)
        # Never replace an existing route owned by another interface.
        rows = json.loads(run(['ip', '-j', '-4', 'route', 'show', 'exact', destination]))
        if rows and any(r.get('dev') != interface for r in rows):
            raise ValueError('Host route conflict')
        if not rows:
            run(['ip', '-4', 'route', 'add', destination, 'dev', interface])


def publish(configuration, accounts, policy):
    """One atomic ConfigMap replacement; settings and credentials preserved."""
    kubectl = ['kubectl', '--context', configuration['kube_context'], '-n', configuration['namespace']]
    current = json.loads(run(kubectl + ['get', 'configmap', 'discovery-config', '-o', 'json']))
    data = current['data']
    data['accounts.yaml'] = yaml.safe_dump(accounts)
    data['policy.yaml'] = yaml.safe_dump(policy)
    settings = yaml.safe_load(data['settings.yaml'])
    settings['isolate_pending_targets'] = True
    settings['isolate_failed_scopes'] = accounts.get('isolate_failed_scopes', False)
    data['settings.yaml'] = yaml.safe_dump(settings)
    # CAS prevents overwriting an operator/bootstrap update during this cycle.
    body = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {
        'name': 'discovery-config', 'namespace': configuration['namespace'],
        'resourceVersion': current['metadata']['resourceVersion']}, 'data': data}
    if data != json.loads(run(kubectl + ['get', 'configmap', 'discovery-config', '-o', 'json']))['data']:
        run(kubectl + ['replace', '-f', '-'], input=json.dumps(body))


def network_session(configuration, gateway, account, central_session):
    """Use a separate member write role; inventory roles remain read-only."""
    if str(account['account_id']) == gateway['account_id']:
        return central_session, None
    if not configuration.get('cross_account_network', False):
        raise ValueError('Cross-account networking disabled')
    role_name = configuration.get('member_network_role_name', 'RedisObservabilityOUNetworkRole')
    if not re.fullmatch(r'[A-Za-z0-9+=,.@_-]{1,64}', role_name):
        raise ValueError('Invalid member network role name')
    role = 'arn:aws:iam::' + str(account['account_id']) + ':role/' + role_name
    return create_session(dict(account_id=account['account_id'], profile=gateway['profile'], role_arn=role)), role


def cycle(configuration, apply=False):
    if not isinstance(configuration.get('required_tags'), dict) or not configuration['required_tags']:
        raise ValueError('Explicit nonempty adoption tags are required')
    store = Path(configuration['state_directory'])
    store.mkdir(parents=True, exist_ok=True)
    store.chmod(0o700)
    gateway = configuration['gateway']
    session = boto3.Session(profile_name=gateway['profile'], region_name=gateway['region'])
    if session.client('sts', config=SDK).get_caller_identity()['Account'] != gateway['account_id']:
        raise ValueError('Gateway account mismatch')
    source = client(session, 'ec2', gateway['region'])
    instance = source.describe_instances(InstanceIds=[gateway['instance_id']])['Reservations'][0]['Instances'][0]
    if instance['VpcId'] != gateway['vpc_id'] or instance['State']['Name'] != 'running':
        raise ValueError('Configured gateway must be running in the expected VPC')
    source_networks = cidrs(source, gateway['vpc_id'])
    source_table = route_table(source, gateway['vpc_id'], instance['SubnetId'])
    previous_file = store / 'links.json'
    links = json.loads(previous_file.read_text()) if previous_file.exists() else {}
    report = {'started_at': time.time(), 'regions': [], 'caches': [], 'networks': [], 'errors': [], 'applied': apply}
    found = []
    network_accounts = {}
    catalog_config = load_config(ROOT / configuration['baseline_accounts'])
    admitted = resolve_accounts(catalog_config, purpose='network')
    report['accounts'] = []
    for account in admitted:
        identifier = str(account['account_id'])
        network_accounts[identifier] = account
        try:
            regional_session = create_session(account)
            enabled = [r for r in enabled_regions(regional_session, account['regions'])
                       if r not in configuration.get('exclude_regions', [])]
        except Exception as exc:
            report['accounts'].append({'account_id': identifier, 'status': 'pending_access'})
            report['errors'].append({'account_id': identifier, 'region': None,
                                     'status': 'account_access_failed', 'type': type(exc).__name__})
            continue
        report['accounts'].append({'account_id': identifier, 'status': 'inventory_ready'})
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(scan, account, r, configuration['required_tags']): r for r in enabled}
            for future in as_completed(futures):
                region = futures[future]
                try:
                    result = future.result()
                    networks = result['networks']
                    report['regions'].append({'account_id': identifier, 'region': region, 'status': 'scanned', 'networks': len(networks),
                                              'caches_discovered': len(result['caches'])})
                    report['caches'].extend(dict(c, account_id=str(account['account_id'])) for c in result['caches'])
                    report['errors'].extend(dict(e, region=region, account_id=str(account['account_id'])) for e in result['errors'])
                    found.extend(dict(n, account_id=str(account['account_id']), profile=account['profile'],
                                      account_name=account['name']) for n in networks)
                except Exception as exc:
                    report['errors'].append({'account_id': str(account['account_id']), 'region': region,
                                             'status': 'inventory_failed', 'type': type(exc).__name__})
    preserve_failed_inventory(report, store / 'status.json')
    # Keep managed links/scopes on partial scans or cache deletion; never auto-destroy network.
    for network in sorted(found, key=lambda n: (n['region'], n['vpc_id'])):
        if network['vpc_id'] in configuration.get('exclude_vpcs', []):
            continue
        key = network['account_id'] + '-' + network['region'] + '-' + network['vpc_id']
        try:
            cross_account = network['account_id'] != gateway['account_id']
            if cross_account and not configuration.get('cross_account_network', False):
                report['networks'].append({'key': key, 'status': 'pending_network',
                                          'reason': 'cross_account_network_writes_not_enabled'})
                continue
            if network['vpc_id'] == gateway['vpc_id'] and network['region'] == gateway['region']:
                network['cidrs'] = source_networks
                links[key] = network
                report['networks'].append({'key': key, 'status': 'gateway_vpc'})
                continue
            if len(links) >= configuration.get('max_networks', 8) and key not in links:
                raise ValueError('Network limit reached')
            remote_session, peer_role = network_session(configuration, gateway, network_accounts[network['account_id']], session)
            remote = client(remote_session, 'ec2', network['region'])
            network['cidrs'] = cidrs(remote, network['vpc_id'])
            occupied = source_networks + [c for k, n in links.items() if k != key for c in n['cidrs']]
            local = json.loads(run(['ip', '-j', '-4', 'route', 'show']))
            occupied += [r['dst'] for r in local if r.get('dst') != 'default'
                         and r.get('dev') != gateway['wireguard_interface']]
            no_overlap(network['cidrs'], occupied)
            tables = {route_table(remote, network['vpc_id'], s)['RouteTableId']:
                      route_table(remote, network['vpc_id'], s) for s in network['subnets']}
            old = links.get(key, {})
            if not old.get('peering_id'):
                directory = store / 'links' / key
                if (directory / 'terraform.tfstate').exists():
                    old = {'peering_id': json.loads(run(['terraform', '-chdir=' + str(directory),
                        'output', '-json'])).get('peering_id', {}).get('value')}
            validate_routes(source_table, network['cidrs'], old.get('peering_id'))
            for table in tables.values():
                validate_routes(table, [instance['PrivateIpAddress'] + '/32'], old.get('peering_id'))
            variables = dict(source_region=gateway['region'], peer_region=network['region'],
                profile=gateway['profile'], account_id=gateway['account_id'], source_vpc=gateway['vpc_id'],
                peer_vpc=network['vpc_id'], gateway_ip=instance['PrivateIpAddress'],
                source_route_table=source_table['RouteTableId'], peer_route_tables=sorted(tables),
                peer_cidrs=network['cidrs'], security_groups=network['security_groups'],
                peer_account_id=network['account_id'], peer_role_arn=peer_role)
            if apply:
                network['peering_id'] = terraform_link(store / 'links' / key, variables)
                configure_gateway(session, gateway, instance['PrivateIpAddress'], network['cidrs'])
                host_routes(gateway['wireguard_interface'], network['cidrs'])
                links[key] = network
                atomic(previous_file, links)
            report['networks'].append({'key': key, 'status': 'configured' if apply else 'planned'})
        except Exception as exc:
            # Never print SDK/request bodies: they may contain private data.
            report['errors'].append({'network': key, 'status': 'pending', 'type': type(exc).__name__})
    if apply:
        atomic(previous_file, links)
        accounts = catalog_config
        policy = yaml.safe_load((ROOT / configuration['baseline_routing']).read_text())
        policy['allowed_accounts'] = sorted({a['account_id'] for a in admitted})
        policy['isolate_pending_targets'] = True
        for key, network in links.items():
            if 'catalog' not in accounts:
                account = next((a for a in accounts['accounts'] if str(a['account_id']) == network['account_id']), None)
                if account is None:
                    continue
                if network['region'] not in account['regions']:
                    account['regions'].append(network['region'])
            policy['local_networks'].append(dict(name='automatic-' + key, enabled=True,
                account_id=network['account_id'], region=network['region'], vpc_id=network['vpc_id'],
                destination_cidrs=network['cidrs'], required_tags=configuration['required_tags'],
                allow_authenticated=True))
        publish(configuration, accounts, policy)
    report['completed_at'] = time.time()
    atomic(store / 'status.json', report)
    print(json.dumps(report, indent=2))
    return report


def preserve_failed_inventory(report, previous_file):
    """An API failure is not proof that previously discovered caches vanished."""
    if not previous_file.exists():
        return
    failed = {(e['account_id'], e['region']) for e in report['errors']
              if e['status'] == 'inventory_failed'}
    failed_accounts = {e['account_id'] for e in report['errors'] if e['status'] == 'account_access_failed'}
    old = json.loads(previous_file.read_text())
    for cache in old.get('caches', []):
        if cache['account_id'] in failed_accounts or (cache['account_id'], cache['region']) in failed:
            report['caches'].append(dict(cache, inventory_fresh=False, inventory_reason='region_scan_failed'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='discovery/config/automatic-regions.yaml')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    configuration = yaml.safe_load((ROOT / args.config).read_text())
    if not configuration.get('enabled'):
        print('AUTOMATION_DISABLED')
        return
    # Same OS lock also used by OFF, preventing provisioning after shutdown.
    import fcntl
    lock = Path(configuration['state_directory']) / 'controller.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        cron = json.loads(run(['kubectl', '--context', configuration['kube_context'], '-n',
            configuration['namespace'], 'get', 'cronjob', 'discovery', '-o', 'json']))
        if cron['spec'].get('suspend'):
            print('AUTOMATION_IDLE: lab is OFF')
            return
        cycle(configuration, args.apply)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('AUTOMATION_FAILED: ' + type(exc).__name__, flush=True)
        raise SystemExit(1)
