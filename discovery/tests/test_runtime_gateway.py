import os
import unittest
from unittest.mock import Mock, patch

from scripts.vault_runtime import gateway, main


class GatewayTests(unittest.TestCase):
    def test_on_starts_installed_automation_timer(self):
        env = {'RUNTIME_ACTIVE': 'true', 'RUNTIME_SESSION': '/private',
               'RUNTIME_ACCOUNTS': 'accounts.yaml', 'RUNTIME_ROUTING': 'routing.yaml'}
        with patch.dict(os.environ, env), patch('scripts.vault_runtime.gateway'), \
             patch('scripts.vault_runtime.os.path.exists', return_value=True), \
             patch('scripts.vault_runtime.run') as run:
            main()
        run.assert_any_call('systemctl', 'start', 'redis-regions.timer')

    def test_off_stops_automation_before_gateway(self):
        order = []
        with patch.dict(os.environ, {'RUNTIME_ACTIVE': 'false'}), \
             patch('scripts.vault_runtime.os.path.exists', return_value=True), \
             patch('scripts.vault_runtime.run', side_effect=lambda *a: order.append(a)), \
             patch('scripts.vault_runtime.gateway', side_effect=lambda a: order.append(('gateway', a))):
            main()
        self.assertEqual(order[0], ('systemctl', 'stop', 'redis-regions.timer', 'redis-regions.service'))
        self.assertEqual(order[-1], ('gateway', False))

    def test_unconfigured_gateway_does_not_access_aws(self):
        with patch.dict(os.environ, {'RUNTIME_GATEWAY_ID': ''}), patch('boto3.Session') as session:
            gateway(True)
            session.assert_not_called()

    def test_account_guard_prevents_mutations_and_off_stops_only_configured_instance(self):
        env = {'RUNTIME_GATEWAY_ID': 'i-123', 'RUNTIME_GATEWAY_ACCOUNT': '123456789012',
               'RUNTIME_GATEWAY_REGION': 'us-east-1', 'RUNTIME_AWS_PROFILE': 'test'}
        session = Mock()
        sts, ec2 = Mock(), Mock()
        session.client.side_effect = lambda service, **kw: sts if service == 'sts' else ec2
        sts.get_caller_identity.return_value = {'Account': 'wrong'}
        with patch.dict(os.environ, env), patch('boto3.Session', return_value=session):
            with self.assertRaises(RuntimeError):
                gateway(False)
            ec2.stop_instances.assert_not_called()
            sts.get_caller_identity.return_value = {'Account': '123456789012'}
            ec2.describe_instances.return_value = {'Reservations': [{'Instances': [{'State': {'Name': 'running'}}]}]}
            gateway(False)
            ec2.stop_instances.assert_called_once_with(InstanceIds=['i-123'])
