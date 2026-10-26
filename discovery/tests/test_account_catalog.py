from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from discovery.aws_discovery.accounts import CatalogError, load_config, resolve_accounts, enabled_regions
from discovery.aws_discovery.__main__ import discover_accounts
from discovery.aws_discovery.scopes import complete_scopes
from discovery.aws_discovery.lifecycle import deletion_plan
from discovery.aws_discovery.scrapeconfig import build_scrapeconfig

CENTRAL = '111122223333'
MEMBER = '444455556666'


def catalog():
    return {'source': 'explicit', 'defaults': {'central_account_id': CENTRAL,
        'discovery_profile': 'pod-profile', 'network_profile': 'host-profile', 'regions': ['us-east-1']},
        'accounts': [{'account_id': CENTRAL, 'name': 'central'}, {'account_id': MEMBER, 'name': 'member'}]}


class CatalogTests(unittest.TestCase):
    def test_two_accounts_generate_member_role_and_keep_central_base_identity(self):
        rows = resolve_accounts({'catalog': catalog()})
        self.assertNotIn('role_arn', rows[0])
        self.assertEqual(rows[1]['role_arn'], f'arn:aws:iam::{MEMBER}:role/RedisObservabilityMemberDiscoveryRole')
        self.assertEqual(rows[1]['profile'], 'pod-profile')
        self.assertEqual(resolve_accounts({'catalog': catalog()}, 'network')[1]['profile'], 'host-profile')

    def test_disabled_and_excluded_accounts_not_admitted(self):
        c = catalog(); c['accounts'][1]['enabled'] = False
        self.assertEqual(len(resolve_accounts({'catalog': c})), 1)
        c['accounts'][1]['enabled'] = True; c['exclude_account_ids'] = [MEMBER]
        self.assertEqual(len(resolve_accounts({'catalog': c})), 1)

    def test_empty_duplicate_invalid_and_wrong_role_account_rejected(self):
        cases = []
        c = catalog(); c['accounts'] = []; cases.append(c)
        c = catalog(); c['accounts'].append(c['accounts'][0]); cases.append(c)
        c = catalog(); c['accounts'][1]['account_id'] = 'REPLACE'; cases.append(c)
        c = catalog(); c['accounts'][1]['role_arn'] = f'arn:aws:iam::{CENTRAL}:role/Other'; cases.append(c)
        for c in cases:
            with self.subTest(c=c), self.assertRaises(CatalogError):
                resolve_accounts({'catalog': c})

    def test_relative_catalog_reference_is_embedded_for_configmap(self):
        import yaml
        with TemporaryDirectory() as directory:
            p = Path(directory)
            (p/'catalog.yaml').write_text(yaml.safe_dump(catalog()))
            (p/'config.yaml').write_text('catalog_file: catalog.yaml\ncollect_nodes: true\n')
            loaded = load_config(p/'config.yaml')
            self.assertIn('catalog', loaded)
            self.assertNotIn('catalog_file', loaded)
            self.assertEqual(len(resolve_accounts(loaded)), 2)

    def organization_fixture(self):
        c = catalog(); c['source'] = 'organizations'; c.pop('accounts')
        c['organizations'] = dict(reader_account_id=CENTRAL,
            expected_organization_id='o-abcdefghij', ou_ids=['ou-abcd-abcdefgh'], recursive=True)
        api = Mock()
        api.describe_organization.return_value = {'Organization': {'Id': 'o-abcdefghij'}}
        def paginator(name):
            def pages(**kw):
                if name == 'list_accounts_for_parent':
                    return [{'Accounts': []}, {'Accounts': [
                        {'Id': MEMBER, 'Name': 'member', 'State': 'ACTIVE'},
                        {'Id': '777788889999', 'State': 'SUSPENDED'}]}]
                return [{'OrganizationalUnits': []}]
            return Mock(paginate=Mock(side_effect=pages))
        api.get_paginator.side_effect = paginator
        session = Mock(); session.client.return_value = api
        return c, api, session

    def test_organizations_pagination_and_active_filter(self):
        c, api, session = self.organization_fixture()
        with patch('discovery.aws_discovery.accounts.create_session', return_value=session):
            rows = resolve_accounts({'catalog': c})
        self.assertEqual([r['account_id'] for r in rows], [MEMBER])
        self.assertIn('role_arn', rows[0])

    def test_ou_role_does_not_reuse_individually_bootstrapped_role(self):
        c, api, session = self.organization_fixture()
        c['defaults']['member_role_name'] = 'RedisObservabilityOUDiscoveryRole'
        with patch('discovery.aws_discovery.accounts.create_session', return_value=session):
            rows = resolve_accounts({'catalog': c})
        self.assertEqual(rows[0]['role_arn'], f'arn:aws:iam::{MEMBER}:role/RedisObservabilityOUDiscoveryRole')

    def test_new_org_account_appears_next_resolution_without_catalog_edit(self):
        c, api, session = self.organization_fixture()
        with patch('discovery.aws_discovery.accounts.create_session', return_value=session):
            self.assertEqual(len(resolve_accounts({'catalog': c})), 1)
            api.get_paginator.side_effect = lambda name: Mock(paginate=Mock(return_value=[
                {'Accounts': [{'Id': CENTRAL, 'State': 'ACTIVE'}, {'Id': MEMBER, 'State': 'ACTIVE'}]}
                if name == 'list_accounts_for_parent' else {'OrganizationalUnits': []}]))
            self.assertEqual(len(resolve_accounts({'catalog': c})), 2)

    def test_nested_ou_accounts_are_discovered_and_overlapping_ous_deduplicated(self):
        c, api, session = self.organization_fixture()
        child = 'ou-abcd-ijklmnop'
        c['organizations']['ou_ids'].append(child)
        def paginate(name):
            def pages(ParentId):
                if name == 'list_accounts_for_parent':
                    return [{'Accounts': [{'Id': MEMBER, 'State': 'ACTIVE'}]}]
                return [{'OrganizationalUnits': [{'Id': child}] if ParentId != child else []}]
            return Mock(paginate=Mock(side_effect=pages))
        api.get_paginator.side_effect = paginate
        with patch('discovery.aws_discovery.accounts.create_session', return_value=session):
            self.assertEqual(len(resolve_accounts({'catalog': c})), 1)

    def test_twenty_org_accounts_use_same_role_convention_without_catalog_entries(self):
        c, api, session = self.organization_fixture()
        members = [{'Id': f'{i:012d}', 'State': 'ACTIVE'} for i in range(1, 21)]
        api.get_paginator.side_effect = lambda name: Mock(paginate=Mock(return_value=[
            {'Accounts': members} if name == 'list_accounts_for_parent' else {'OrganizationalUnits': []}]))
        with patch('discovery.aws_discovery.accounts.create_session', return_value=session):
            rows = resolve_accounts({'catalog': c})
        self.assertEqual(len(rows), 20)
        self.assertNotIn('accounts', c)
        self.assertTrue(all(r['role_arn'].endswith('/RedisObservabilityMemberDiscoveryRole') for r in rows))

    def test_organization_mismatch_and_denied_catalog_fail_closed(self):
        c, api, session = self.organization_fixture()
        with patch('discovery.aws_discovery.accounts.create_session', return_value=session):
            api.describe_organization.return_value = {'Organization': {'Id': 'o-wrongorg123'}}
            with self.assertRaises(CatalogError): resolve_accounts({'catalog': c})
            api.describe_organization.side_effect = RuntimeError('private credential_process output')
            with self.assertRaises(CatalogError) as error: resolve_accounts({'catalog': c})
            self.assertNotIn('private', str(error.exception))

    def test_regions_auto_uses_enabled_regions(self):
        session = Mock()
        session.client.return_value.describe_regions.return_value = {'Regions': [
            {'RegionName': 'ca-central-1'}, {'RegionName': 'us-east-1'}]}
        self.assertEqual(enabled_regions(session, 'auto'), ['ca-central-1', 'us-east-1'])
        session.client.return_value.describe_regions.assert_called_once_with(AllRegions=False)

    def test_account_access_failure_does_not_stop_other_account(self):
        sessions = {CENTRAL: Mock(), MEMBER: Mock()}
        def session(account):
            if account['account_id'] == MEMBER: raise RuntimeError('sensitive body')
            return sessions[CENTRAL]
        with patch('discovery.aws_discovery.__main__.create_session', side_effect=session), \
             patch('discovery.aws_discovery.__main__.discover_region', return_value=[]):
            inventory, config = discover_accounts({'catalog': catalog(), 'account_workers': 2})
        self.assertEqual(inventory['accounts'][0]['status'], 'inventory_ready')
        self.assertEqual(inventory['accounts'][1]['status'], 'pending_access')
        self.assertNotIn('sensitive', json.dumps(inventory))
        ready, resolved = complete_scopes(inventory, config)
        self.assertEqual([a['account_id'] for a in resolved['accounts']], [CENTRAL])
        self.assertEqual(ready['errors'], [])

    def test_cli_writes_resolved_scope_for_controller_and_allows_safe_partial_result(self):
        import yaml
        from discovery.aws_discovery.__main__ import main
        def session(account):
            if account['account_id'] == MEMBER: raise RuntimeError('PRIVATE_CREDENTIAL_BODY')
            return Mock()
        with TemporaryDirectory() as directory:
            path = Path(directory)
            source = path/'accounts.yaml'; destination = path/'resolved.yaml'
            source.write_text(yaml.safe_dump({'catalog': catalog(), 'isolate_failed_scopes': True}))
            output = StringIO()
            with patch('sys.argv', ['discovery', '--config', str(source), '--resolved-config', str(destination)]), \
                 patch('discovery.aws_discovery.__main__.create_session', side_effect=session), \
                 patch('discovery.aws_discovery.__main__.discover_region', return_value=[]), redirect_stdout(output):
                self.assertEqual(main(), 0)
            resolved = yaml.safe_load(destination.read_text())
            self.assertNotIn('catalog', resolved)
            self.assertEqual(resolved['accounts'][0]['regions'], ['us-east-1'])
            self.assertEqual(resolved['accounts'][1]['regions'], [])
            self.assertNotIn('PRIVATE_CREDENTIAL_BODY', output.getvalue())

    def test_cli_does_not_publish_resolved_scope_when_catalog_is_invalid(self):
        import yaml
        from discovery.aws_discovery.__main__ import main
        with TemporaryDirectory() as directory:
            path = Path(directory); destination = path/'resolved.yaml'
            c = catalog(); c['accounts'] = []
            (path/'accounts.yaml').write_text(yaml.safe_dump({'catalog': c}))
            with patch('sys.argv', ['discovery', '--config', str(path/'accounts.yaml'), '--resolved-config', str(destination)]):
                self.assertEqual(main(), 1)
            self.assertFalse(destination.exists())

    def test_assume_role_client_verifies_returned_account_identity(self):
        from discovery.aws_discovery.client import create_session
        base, member = Mock(), Mock()
        base.client.return_value.assume_role.return_value = {'Credentials': {
            'AccessKeyId': 'TEST_ONLY', 'SecretAccessKey': 'TEST_ONLY', 'SessionToken': 'TEST_ONLY'}}
        member.client.return_value.get_caller_identity.return_value = {'Account': MEMBER}
        entry = resolve_accounts({'catalog': catalog()})[1]
        with patch('discovery.aws_discovery.client.boto3.Session', side_effect=[base, member]):
            self.assertIs(create_session(entry), member)
        base.client.return_value.assume_role.assert_called_once_with(
            RoleArn=entry['role_arn'], RoleSessionName='RedisObservabilityDiscovery')
        member.client.return_value.get_caller_identity.return_value = {'Account': CENTRAL}
        with patch('discovery.aws_discovery.client.boto3.Session', side_effect=[base, member]):
            with self.assertRaises(RuntimeError): create_session(entry)

    def test_failed_region_targets_cannot_be_deleted_while_good_scope_reconciles(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        inventory = {'mode': 'aws-dry-run', 'generated_at': now.isoformat(),
            'errors': [dict(account_id=MEMBER, region='us-east-1', error_type='AccessDenied')],
            'accounts': [dict(account_id=CENTRAL, regions=[dict(region='us-east-1', status='success', resources=[])]),
                         dict(account_id=MEMBER, regions=[dict(region='us-east-1', status='error', resources=[])])]}
        config = {'accounts': resolve_accounts({'catalog': catalog()})}
        ready, resolved = complete_scopes(inventory, config)
        target = dict(id=MEMBER+':us-east-1:old', account_id=MEMBER, region='us-east-1',
            replication_group_id='old', endpoint='old.internal:6379', endpoint_role='primary',
            auth_required=True, tls=True, engine='valkey')
        manifest = build_scrapeconfig(target)
        deleted, _ = deletion_plan({'old': manifest}, {}, ready, resolved, {}, {}, now=now.timestamp())
        self.assertEqual(deleted, [])

    def test_reconcile_creates_healthy_target_and_preserves_failed_account_secret(self):
        from discovery.tests.test_vault_lifecycle import LifecycleTests
        from discovery.aws_discovery.controller import b64
        fixture = LifecycleTests(); fixture.setUp()
        controller = fixture.controller()
        controller.settings['isolate_failed_scopes'] = True
        old = dict(fixture.target, id=MEMBER+':us-east-1:old', account_id=MEMBER,
                   replication_group_id='old', endpoint='other.internal:6379')
        old_manifest = build_scrapeconfig(old, namespace='lab', scope='test')
        old_uri = old_manifest['spec']['staticConfigs'][0]['targets'][0]
        controller.objects.list_namespaced_custom_object.return_value = {'items': [old_manifest]}
        controller.core.read_namespaced_secret.return_value.data['passwords.json'] = b64(json.dumps({old_uri: 'retained'}))
        controller.core.read_namespaced_config_map.return_value.data = {'state.json': json.dumps({
            'credential_uris': {old['id']: old_uri}})}
        inventory = fixture.inventory(resources=[{'type': 'replication_group', 'id': 'test', 'status': 'available'}])
        inventory['accounts'].append(dict(account_id=MEMBER, regions=[]))
        inventory['errors'] = [dict(account_id=MEMBER, region=None, error_type='AccessDenied')]
        config = deepcopy(fixture.config)
        config['accounts'].append(dict(account_id=MEMBER, regions=['us-east-1']))
        controller.publish = Mock(); controller.healthy = Mock()
        with patch('discovery.aws_discovery.controller.select_targets', return_value=[fixture.target]), \
             patch('discovery.aws_discovery.controller.filter_targets', side_effect=lambda d, p: d), \
             patch('discovery.aws_discovery.controller.verify_redis'):
            controller.reconcile(inventory, config, fixture.policy)
        controller.objects.create_namespaced_custom_object.assert_called_once()
        controller.objects.delete_namespaced_custom_object.assert_not_called()
        self.assertEqual(controller.publish.call_args.args[0][old_uri], 'retained')

    def test_partial_scope_filter_rejects_unexplained_missing_and_unexpected_scopes(self):
        config = {'accounts': resolve_accounts({'catalog': catalog()})}
        inventory = {'errors': [], 'accounts': [dict(account_id=CENTRAL,
            regions=[dict(region='us-east-1', status='success', resources=[])])]}
        with self.assertRaises(ValueError): complete_scopes(inventory, config)
        inventory['accounts'][0]['regions'][0]['region'] = 'sa-east-1'
        with self.assertRaises(ValueError): complete_scopes(inventory, config)

    def test_no_healthy_scope_aborts(self):
        config = {'accounts': resolve_accounts({'catalog': catalog()})}
        inventory = {'errors': [dict(account_id=a, region=None) for a in (CENTRAL, MEMBER)],
                     'accounts': [dict(account_id=a, regions=[]) for a in (CENTRAL, MEMBER)]}
        with self.assertRaises(ValueError): complete_scopes(inventory, config)
