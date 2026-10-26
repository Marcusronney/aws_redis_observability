import base64
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from discovery.aws_discovery.controller import Controller, b64, lease
from discovery.aws_discovery.lifecycle import deletion_plan
from discovery.aws_discovery.scrapeconfig import build_scrapeconfig
from discovery.aws_discovery.vault import credential_path, CredentialError


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc).timestamp()
        self.config = {'accounts': [{'account_id': '123456789012', 'regions': ['us-east-1']}]}
        self.policy = {'allowed_accounts': ['123456789012']}
        self.target = dict(id='123456789012:us-east-1:test', account_id='123456789012',
            region='us-east-1', replication_group_id='test', engine='valkey',
            endpoint='private.example:6379', endpoint_role='primary', tls=True, auth_required=True)
        self.manifest = build_scrapeconfig(self.target, namespace='lab', scope='test')
        self.name = self.manifest['metadata']['name']
        self.existing = {self.name: self.manifest}

    def inventory(self, offset=0, resources=None):
        return {'mode': 'aws-dry-run', 'errors': [],
            'generated_at': datetime.fromtimestamp(self.now + offset, timezone.utc).isoformat(),
            'accounts': [{'account_id': '123456789012', 'regions': [
                {'region': 'us-east-1', 'status': 'success', 'resources': resources or []}]}]}

    def plan(self, inventory, state=None, **kwargs):
        return deletion_plan(self.existing, {}, inventory, self.config, self.policy,
                             state or {}, now=self.now + 120, **kwargs)

    def test_delete_requires_two_fresh_observations_and_grace(self):
        deletes, state = self.plan(self.inventory())
        self.assertEqual(deletes, [])
        deletes, state = self.plan(self.inventory(30), state)
        self.assertEqual(deletes, [])
        deletes, _ = self.plan(self.inventory(61), state)
        self.assertEqual(deletes, [self.name])

    def test_replayed_inventory_cannot_advance_deletion(self):
        _, state = self.plan(self.inventory())
        with self.assertRaises(ValueError):
            self.plan(self.inventory(), state)

    def test_failed_partial_stale_and_duplicate_scans_abort(self):
        mutations = []
        failed = self.inventory(); failed['errors'] = [{'region': 'us-east-1'}]; mutations.append(failed)
        partial = self.inventory(); partial['accounts'] = []; mutations.append(partial)
        stale = self.inventory(-400); mutations.append(stale)
        duplicate = self.inventory(); duplicate['accounts'] *= 2; mutations.append(duplicate)
        for inventory in mutations:
            with self.subTest(inventory=inventory), self.assertRaises(ValueError):
                self.plan(inventory)

    def test_existing_but_unavailable_resource_never_deleted(self):
        _, state = self.plan(self.inventory())
        deletes, state = self.plan(self.inventory(90, [
            {'type': 'replication_group', 'id': 'test', 'status': 'modifying'}]), state)
        self.assertEqual(deletes, [])
        self.assertEqual(state['missing'], {})

    def test_policy_change_resets_absence_confirmation(self):
        _, state = self.plan(self.inventory())
        self.policy['blocked_targets'] = ['some-other-target']
        deletes, _ = self.plan(self.inventory(90), state)
        self.assertEqual(deletes, [])

    def test_unknown_scope_is_preserved(self):
        self.manifest['metadata']['annotations']['redis-observability/target-id'] = 'other:us-east-1:test'
        _, state = self.plan(self.inventory())
        deletes, _ = self.plan(self.inventory(90), state)
        self.assertEqual(deletes, [])

    def test_second_account_missing_from_scan_aborts(self):
        self.config['accounts'].append({'account_id': '999999999999', 'regions': ['sa-east-1']})
        with self.assertRaises(ValueError):
            self.plan(self.inventory())

    def test_inconsistent_resource_count_aborts(self):
        inventory = self.inventory()
        inventory['accounts'][0]['regions'][0]['resource_count'] = 1
        with self.assertRaises(ValueError):
            self.plan(inventory)

    def test_unknown_resource_schema_cannot_authorize_deletion(self):
        with self.assertRaises(ValueError):
            self.plan(self.inventory(resources=[{'type': 'unknown', 'id': 'test'}]))

    def test_vault_path_is_identity_based_and_rejects_traversal(self):
        self.assertEqual(credential_path(self.target),
            'kv/data/redis-observability/123456789012/us-east-1/replication_group/test')
        self.target['replication_group_id'] = '../root'
        with self.assertRaises(CredentialError):
            credential_path(self.target)

    def controller(self):
        core, objects, vault = Mock(), Mock(), Mock()
        core.read_namespaced_config_map.return_value = NS(data={'state.json': '{}'}, metadata=NS(name='state'))
        core.read_namespaced_secret.return_value = NS(metadata=NS(name='credentials', labels={
            'redis-observability/scope': 'test'}), data={'passwords.json': b64('{}'), 'generation': b64('g')})
        objects.list_namespaced_custom_object.return_value = {'items': []}
        vault.read.return_value = ('default', 'test-only-password')
        controller = Controller(core, objects, vault, dict(namespace='lab', scope='test',
            auth_exporter='auth:9121', noauth_exporter='noauth:9121', state_name='state', secret_name='credentials'))
        return controller

    def cycle(self, controller):
        with patch('discovery.aws_discovery.controller.select_targets', return_value=[self.target]), \
             patch('discovery.aws_discovery.controller.filter_targets', side_effect=lambda document, policy: document):
            return controller.reconcile(self.inventory(), self.config, self.policy)

    def test_vault_failure_does_not_publish_or_mutate_targets(self):
        controller = self.controller()
        old_target = dict(self.target, id='123456789012:us-east-1:removed', replication_group_id='removed')
        old_manifest = build_scrapeconfig(old_target, namespace='lab', scope='test')
        old_name = old_manifest['metadata']['name']
        controller.objects.list_namespaced_custom_object.return_value = {'items': [self.manifest, old_manifest]}
        _, state = deletion_plan({old_name: old_manifest}, {}, self.inventory(-90), self.config,
                                 self.policy, {}, now=self.now)
        controller.core.read_namespaced_config_map.return_value.data = {'state.json': json.dumps(state)}
        controller.vault.read.side_effect = CredentialError('unavailable')
        with self.assertRaises(CredentialError):
            self.cycle(controller)
        controller.core.replace_namespaced_secret.assert_not_called()
        controller.objects.create_namespaced_custom_object.assert_not_called()
        controller.objects.delete_namespaced_custom_object.assert_not_called()
        controller.core.replace_namespaced_config_map.assert_not_called()

    def test_pending_credential_keeps_existing_target_and_password(self):
        controller = self.controller()
        controller.settings['isolate_pending_targets'] = True
        controller.objects.list_namespaced_custom_object.return_value = {'items': [self.manifest]}
        uri = self.manifest['spec']['staticConfigs'][0]['targets'][0]
        controller.core.read_namespaced_secret.return_value.data['passwords.json'] = b64(json.dumps({uri: 'previous'}))
        controller.core.read_namespaced_config_map.return_value.data = {'state.json': json.dumps({
            'credential_uris': {self.target['id']: uri}})}
        controller.vault.read.side_effect = CredentialError('unavailable')
        controller.publish = Mock()
        inventory = self.inventory(resources=[{'type': 'replication_group', 'id': 'test', 'status': 'available'}])
        with patch('discovery.aws_discovery.controller.select_targets', return_value=[self.target]), \
             patch('discovery.aws_discovery.controller.filter_targets', side_effect=lambda document, policy: document):
            controller.reconcile(inventory, self.config, self.policy)
        controller.objects.delete_namespaced_custom_object.assert_not_called()
        controller.objects.patch_namespaced_custom_object.assert_not_called()
        self.assertEqual(controller.publish.call_args.args[0][uri], 'previous')

    def test_concurrent_owner_blocks_lease_without_mutation(self):
        api = Mock()
        api.read_namespaced_lease.return_value = NS(spec=NS(holder_identity='other',
            renew_time=datetime.now(timezone.utc), lease_duration_seconds=300))
        with self.assertRaises(RuntimeError), lease(api, 'lab', 'discovery'):
            self.fail('An active lease must not be entered')
        api.replace_namespaced_lease.assert_not_called()

    @patch('discovery.aws_discovery.controller.verify_redis', side_effect=RuntimeError('invalid auth'))
    def test_wrong_password_does_not_replace_working_credentials(self, verify):
        controller = self.controller()
        with self.assertRaises(RuntimeError):
            self.cycle(controller)
        controller.core.replace_namespaced_secret.assert_not_called()
        controller.objects.create_namespaced_custom_object.assert_not_called()

    @patch('discovery.aws_discovery.controller.verify_redis')
    def test_failed_exporter_reload_restores_previous_map(self, verify):
        controller = self.controller()
        controller.publish = Mock(side_effect=[RuntimeError('reload timeout'), None])
        with self.assertRaises(RuntimeError):
            self.cycle(controller)
        self.assertEqual(controller.publish.call_args_list[-1].args, ({},))
        controller.objects.create_namespaced_custom_object.assert_not_called()

    @patch('discovery.aws_discovery.controller.verify_redis')
    def test_create_contains_username_and_no_password(self, verify):
        controller = self.controller()
        controller.publish = Mock()
        controller.healthy = Mock()
        self.cycle(controller)
        body = controller.objects.create_namespaced_custom_object.call_args.args[-1]
        self.assertIn('rediss://default@private.example:6379', json.dumps(body))
        self.assertNotIn('test-only-password', json.dumps(body))
        controller.core.replace_namespaced_config_map.assert_called_once()

    def test_confirmed_deletion_uses_uid_and_resource_version(self):
        controller = self.controller()
        self.manifest['metadata'].update(uid='original-uid', resourceVersion='42')
        controller.objects.list_namespaced_custom_object.return_value = {'items': [self.manifest]}
        first = self.inventory(-90)
        _, state = deletion_plan(self.existing, {}, first, self.config, self.policy, {}, now=self.now)
        controller.core.read_namespaced_config_map.return_value.data = {'state.json': json.dumps(state)}
        controller.publish = Mock()
        controller.reconcile(self.inventory(), self.config, self.policy)
        body = controller.objects.delete_namespaced_custom_object.call_args.kwargs['body']
        self.assertEqual(body, {'preconditions': {'uid': 'original-uid', 'resourceVersion': '42'}})

    def test_scaled_exporter_checks_each_pod_and_waits_for_pending_pod(self):
        controller = self.controller()
        controller.settings['reload_pod_selector'] = 'app=exporter-auth'
        def pod(ip, phase='Running', deleting=None):
            return NS(metadata=NS(deletion_timestamp=deleting), status=NS(phase=phase, pod_ip=ip))
        controller.core.list_namespaced_pod.return_value = NS(items=[pod('10.244.1.2'), pod('10.244.1.3')])
        self.assertEqual(controller.reload_urls(), ['http://10.244.1.2:9122/', 'http://10.244.1.3:9122/'])
        controller.core.list_namespaced_pod.return_value.items.append(pod(None, 'Pending'))
        self.assertEqual(controller.reload_urls(), [])

    @patch('discovery.aws_discovery.controller.time.sleep')
    @patch('discovery.aws_discovery.controller.urllib.request.urlopen')
    def test_publish_waits_for_all_replicas_not_just_service_endpoint(self, urlopen, sleep):
        controller = self.controller()
        controller.reload_urls = Mock(return_value=['http://pod1:9122/', 'http://pod2:9122/'])
        def response(value):
            result = Mock()
            result.__enter__ = Mock(return_value=NS(read=lambda: value.encode()))
            result.__exit__ = Mock(return_value=False)
            return result
        urlopen.side_effect = [response('g'), response('old-generation'), response('g'), response('g')]
        controller.publish({})
        self.assertEqual(urlopen.call_count, 4)
        sleep.assert_called_once_with(2)


if __name__ == '__main__':
    unittest.main()
