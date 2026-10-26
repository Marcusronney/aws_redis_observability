from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from discovery.aws_discovery.normalize import normalize_replication_group
from discovery.aws_discovery.targets import select_targets
from discovery.aws_discovery.scrapeconfig import build_scrapeconfig
from discovery.aws_discovery.vault import credential_path
from discovery.aws_discovery.__main__ import discover_region
from discovery.aws_discovery.filter_local_targets import filter_targets
from discovery.tests import test_aws_targets, test_vault_lifecycle


class MultiAZTests(unittest.TestCase):
    def test_node_scrapeconfigs_accept_replica_and_unknown_role(self):
        inventory = test_aws_targets.TestAWSTargets().make_inventory()
        group = self.group()
        group['nodes'][0]['role'] = 'unknown'
        inventory['accounts'][0]['regions'][0]['resources'][1] = group
        targets = select_targets(inventory, collect_nodes=True)
        for target in targets:
            manifest = build_scrapeconfig(target)
            self.assertTrue(manifest['metadata']['name'].startswith('auto-aws-'))
        invalid = deepcopy(targets[0])
        invalid.pop('endpoint_kind')
        with self.assertRaises(ValueError):
            build_scrapeconfig(invalid)

    def test_standalone_is_collected_without_duplicating_group_members(self):
        inventory = test_aws_targets.TestAWSTargets().make_inventory()
        resources = inventory['accounts'][0]['regions'][0]['resources']
        resources[0].update(status='available', replication_group_id='redis-lab',
                            endpoints=[{'role': 'node', 'address': 'member.internal', 'port': 6379}])
        standalone = deepcopy(resources[0])
        standalone.update(id='standalone', replication_group_id=None, auth_token_enabled=False, transit_encryption=True)
        resources.append(standalone)
        targets = select_targets(inventory)
        self.assertEqual(len(targets), 2)
        own = next(t for t in targets if t['aws_resource_type'] == 'cluster')
        self.assertFalse(own['auth_required'])
        self.assertIn('/cluster/standalone', credential_path(own))
        policy = {'allowed_accounts': [own['account_id']],
                  'local_targets': [own['id']], 'blocked_targets': []}
        self.assertEqual(filter_targets({'targets': targets}, policy)['targets'], [own])
        policy['blocked_targets'] = [own['id']]
        self.assertEqual(filter_targets({'targets': targets}, policy)['targets'], [])

    @patch('discovery.aws_discovery.__main__.get_resource_tags', return_value={})
    @patch('discovery.aws_discovery.__main__.discover_resources')
    @patch('discovery.aws_discovery.__main__.create_elasticache_client')
    def test_cluster_mode_resolves_endpoint_from_cache_nodes_without_read_endpoint(self, make_client, discover, tags):
        discover.return_value = {'clusters': [{'CacheClusterId': 'node', 'Engine': 'valkey',
            'CacheNodes': [{'CacheNodeId': '0001', 'Endpoint': {'Address': 'node.internal', 'Port': 6379}}]}],
            'replication_groups': [{'ReplicationGroupId': 'group', 'ClusterEnabled': True,
                'NodeGroups': [{'NodeGroupId': '0001', 'NodeGroupMembers': [
                    {'CacheClusterId': 'node', 'CacheNodeId': '0001', 'PreferredAvailabilityZone': 'us-east-1a'}]}]}],
            'serverless': []}
        group = discover_region(Mock(), 'us-east-1', {})[1]
        self.assertEqual(group['nodes'][0]['endpoint'], {'address': 'node.internal', 'port': 6379})
        self.assertEqual(group['nodes'][0]['role'], 'unknown')

    def group(self):
        return normalize_replication_group({
            'ReplicationGroupId': 'redis-lab', 'Status': 'available', 'ClusterEnabled': True,
            'MemberClusters': ['redis-lab-001'], 'AuthTokenEnabled': True,
            'TransitEncryptionEnabled': True, 'NodeGroups': [{'NodeGroupId': '0001',
                'NodeGroupMembers': [{'CacheClusterId': 'redis-lab-001', 'CacheNodeId': '0001',
                    'ReadEndpoint': {'Address': 'one.internal', 'Port': 6379},
                    'CurrentRole': 'primary', 'PreferredAvailabilityZone': 'us-east-1a'},
                    {'CacheClusterId': 'redis-lab-002', 'CacheNodeId': '0001',
                    'ReadEndpoint': {'Address': 'two.internal', 'Port': 6379},
                    'CurrentRole': 'replica', 'PreferredAvailabilityZone': 'us-east-1b'}]}]}, {'project': 'lab'})

    def test_cluster_mode_and_multi_az_have_distinct_targets_same_vault_path(self):
        inventory = test_aws_targets.TestAWSTargets().make_inventory()
        inventory['accounts'][0]['regions'][0]['resources'][1] = self.group()
        self.assertEqual(select_targets(inventory), [])
        targets = select_targets(inventory, collect_nodes=True)
        self.assertEqual(len(targets), 2)
        self.assertEqual(len({t['id'] for t in targets}), 2)
        self.assertEqual({t['labels']['availability_zone'] for t in targets}, {'us-east-1a', 'us-east-1b'})
        self.assertEqual(credential_path(targets[0]), credential_path(targets[1]))
        self.assertTrue(all(t['auth_required'] for t in targets))
        policy = {'allowed_accounts': ['123456789012'], 'local_networks': [{
            'enabled': True, 'account_id': '123456789012', 'region': 'us-east-1',
            'vpc_id': None, 'required_tags': {'Project': 'test'}, 'allow_authenticated': True}]}
        for target in targets:
            target['tags'] = {'Project': 'test'}
        policy['allowed_accounts'] = [targets[0]['account_id']]
        policy['local_networks'][0]['account_id'] = targets[0]['account_id']
        policy['local_networks'][0]['region'] = targets[0]['region']
        selected = filter_targets({'targets': targets}, policy, probe=lambda *_: None)['targets']
        self.assertEqual(len(selected), 2, 'Routing must not collapse nodes of the same group')

    def test_member_absence_requires_two_scans_and_unavailable_parent_is_preserved(self):
        fixture = test_vault_lifecycle.LifecycleTests(); fixture.setUp()
        item = deepcopy(fixture.manifest)
        item['metadata']['annotations']['redis-observability/target-id'] += ':node/old/0001'
        fixture.existing = {fixture.name: item}
        resource = {'type': 'replication_group', 'id': 'test', 'status': 'available', 'nodes': []}
        deletes, state = fixture.plan(fixture.inventory(resources=[resource]))
        self.assertEqual(deletes, [])
        deletes, _ = fixture.plan(fixture.inventory(90, [dict(resource, status='modifying')]), state)
        self.assertEqual(deletes, [])
        deletes, _ = fixture.plan(fixture.inventory(90, [resource]), state)
        self.assertEqual(deletes, [fixture.name])
