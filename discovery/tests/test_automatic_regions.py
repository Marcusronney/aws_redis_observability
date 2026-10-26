import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from scripts.automatic_regions import no_overlap, validate_routes, regions, gateway_script, host_routes, scan, preserve_failed_inventory, network_session


class AutomaticRegionsTests(unittest.TestCase):
    def scan_fixture(self, tags=None):
        cache, ec2 = Mock(), Mock()
        members = [{'CacheClusterId': name + '-node', 'ReplicationGroupId': name, 'Engine': 'valkey',
                    'CacheSubnetGroupName': 'private', 'SecurityGroups': [{'SecurityGroupId': 'sg-' + name}]}
                   for name in ('tagged', 'untagged')]
        members.append({'CacheClusterId': 'memcached', 'Engine': 'memcached'})
        groups = [{'ReplicationGroupId': name, 'ARN': 'arn:' + name, 'Status': 'available',
                   'MemberClusters': [name + '-node']} for name in ('tagged', 'untagged')]
        pages = {'describe_cache_clusters': {'CacheClusters': members},
                 'describe_replication_groups': {'ReplicationGroups': groups},
                 'describe_serverless_caches': {'ServerlessCaches': []}}
        cache.get_paginator.side_effect = lambda operation: Mock(paginate=Mock(return_value=[pages[operation]]))
        cache.list_tags_for_resource.side_effect = tags or (lambda **kw: {'TagList':
            [{'Key': 'RedisObservability', 'Value': 'vault-lab'}] if kw['ResourceName'] == 'arn:tagged' else []})
        cache.describe_cache_subnet_groups.return_value = {'CacheSubnetGroups': [{'Subnets': [{'SubnetIdentifier': 'subnet-a'}]}]}
        ec2.describe_subnets.return_value = {'Subnets': [{'VpcId': 'vpc-a'}]}
        with patch('scripts.automatic_regions.boto3.Session'), \
             patch('scripts.automatic_regions.client', side_effect=lambda s, service, r: cache if service == 'elasticache' else ec2):
            return scan('profile', 'ca-central-1', {'RedisObservability': 'vault-lab'})

    def test_all_caches_visible_but_only_tagged_cache_authorizes_network(self):
        result = self.scan_fixture()
        self.assertEqual({c['name'] for c in result['caches']}, {'tagged', 'untagged'})
        self.assertEqual(result['networks'][0]['caches'], ['tagged'])
        self.assertEqual(result['networks'][0]['security_groups'], ['sg-tagged'])
        untagged = next(c for c in result['caches'] if c['name'] == 'untagged')
        self.assertFalse(untagged['network_authorized'])
        self.assertEqual(untagged['network_authorization'], 'missing_adoption_tag')
        self.assertEqual(untagged['vpc_id'], 'vpc-a')
        self.assertEqual(untagged['engine'], 'valkey')

    def test_tag_api_failure_does_not_hide_discovered_cache_or_authorize_writes(self):
        result = self.scan_fixture(tags=RuntimeError('unavailable'))
        self.assertEqual(len(result['caches']), 2)
        self.assertEqual(result['networks'], [])
        self.assertEqual(len(result['errors']), 2)
        self.assertTrue(all(c['network_authorization'] == 'tags_unavailable' for c in result['caches']))

    def test_failed_region_retains_last_inventory_as_stale(self):
        previous = {'caches': [{'account_id': '123', 'region': 'ca-central-1', 'name': 'existing', 'inventory_fresh': True}]}
        report = {'caches': [], 'errors': [{'account_id': '123', 'region': 'ca-central-1', 'status': 'inventory_failed'}]}
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'status.json'
            path.write_text(json.dumps(previous))
            preserve_failed_inventory(report, path)
        self.assertEqual(report['caches'][0]['name'], 'existing')
        self.assertFalse(report['caches'][0]['inventory_fresh'])

    def test_repeated_default_vpc_cidr_is_rejected(self):
        with self.assertRaises(ValueError):
            no_overlap(['172.31.0.0/16'], ['172.31.0.0/16'])
        with self.assertRaises(ValueError):
            no_overlap(['172.18.0.0/16'], ['172.18.0.0/16'])
        no_overlap(['10.81.0.0/16'], ['10.80.0.0/16', '172.18.0.0/16'])

    def test_existing_foreign_route_is_not_adopted(self):
        table = {'Routes': [{'DestinationCidrBlock': '10.81.0.0/16',
                             'VpcPeeringConnectionId': 'pcx-foreign'}]}
        with self.assertRaises(ValueError):
            validate_routes(table, ['10.81.0.0/16'])
        with self.assertRaises(ValueError):
            validate_routes(table, ['10.81.0.0/16'], 'pcx-owned')
        validate_routes(table, ['10.81.0.0/16'], 'pcx-foreign')

    def test_enabled_regions_are_resolved_without_hardcoded_region_list(self):
        ec2 = Mock()
        ec2.describe_regions.return_value = {'Regions': [{'RegionName': 'sa-east-1'}, {'RegionName': 'us-east-1'}]}
        with patch('scripts.automatic_regions.client', return_value=ec2):
            self.assertEqual(regions(Mock(), {'regions': 'auto'}), ['sa-east-1', 'us-east-1'])
        ec2.describe_regions.assert_called_once_with(AllRegions=False)

    def test_snat_only_handles_redis_and_never_flushes_firewall(self):
        script = gateway_script('10.80.30.230', ['10.81.0.0/16'])
        self.assertIn('--dport 6379', script)
        self.assertIn('--to-source 10.80.30.230', script)
        self.assertNotIn(' -F', script)
        with self.assertRaises(ValueError):
            gateway_script('10.80.30.230', ['10.81.0.0/16; reboot'])

    def test_host_routes_preserve_existing_wireguard_allowed_ips(self):
        results = ['peer-public\n', 'peer-public\t10.80.0.0/16\n', '', '[]', '']
        with patch('scripts.automatic_regions.run', side_effect=results) as command:
            host_routes('wg0', ['10.81.0.0/16'])
        update = command.call_args_list[2].args[0]
        self.assertEqual(update[-1], '10.80.0.0/16,10.81.0.0/16')

    def test_host_routes_do_not_replace_an_existing_foreign_route(self):
        results = ['peer-public\n', 'peer-public\t10.80.0.0/16\n', '', '[{"dev":"eth0"}]']
        with patch('scripts.automatic_regions.run', side_effect=results) as command:
            with self.assertRaises(ValueError):
                host_routes('wg0', ['10.81.0.0/16'])
        self.assertFalse(any('replace' in c.args[0] for c in command.call_args_list))


class CrossAccountNetworkTests(unittest.TestCase):
    def setUp(self):
        self.gateway = {'account_id': '111122223333', 'profile': 'machine-network'}
        self.account = {'account_id': '444455556666', 'profile': 'readonly',
                        'role_arn': 'arn:aws:iam::444455556666:role/DiscoveryOnly'}

    def test_disabled_cross_account_never_assumes_write_role(self):
        with patch('scripts.automatic_regions.create_session') as create:
            with self.assertRaises(ValueError):
                network_session({}, self.gateway, self.account, Mock())
            create.assert_not_called()

    def test_member_writes_use_network_identity_not_discovery_role(self):
        with patch('scripts.automatic_regions.create_session') as create:
            session, arn = network_session({'cross_account_network': True}, self.gateway, self.account, Mock())
            self.assertEqual(arn, 'arn:aws:iam::444455556666:role/RedisObservabilityOUNetworkRole')
            self.assertEqual(create.call_args.args[0]['profile'], 'machine-network')
            self.assertIs(session, create.return_value)
            self.assertEqual(self.account['role_arn'], 'arn:aws:iam::444455556666:role/DiscoveryOnly')

    def test_central_vpc_keeps_existing_session_without_role_chaining(self):
        original = Mock()
        with patch('scripts.automatic_regions.create_session') as create:
            session, role = network_session({}, self.gateway, {'account_id': '111122223333'}, original)
            self.assertIs(session, original)
            self.assertIsNone(role)
            create.assert_not_called()

    def test_invalid_write_role_is_rejected_before_aws_call(self):
        with self.assertRaises(ValueError):
            network_session({'cross_account_network': True, 'member_network_role_name': '*'}, self.gateway, self.account, Mock())
