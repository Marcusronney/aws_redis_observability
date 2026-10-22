import unittest
from discovery.aws_discovery.ecs_controller import (
    digest_for, route_key, describe_auth, make_task, task_matches,
)


class TestECSController(unittest.TestCase):
    def test_deterministic_family(self):
        self.assertEqual(digest_for('123456789012', 'sa-east-1', 'serverless', 'demo'),
                         digest_for('123456789012', 'sa-east-1', 'serverless', 'demo'))
        self.assertEqual(len(digest_for('123456789012', 'sa-east-1', 'serverless', 'demo')), 12)

    def test_route_key(self):
        self.assertEqual(route_key('1','r','serverless','x'), '1:r:serverless:x')

    def test_noauth_conflict(self):
        with self.assertRaises(ValueError):
            describe_auth({'RedisObservabilityAuth': 'noauth'},
                          {'user_group_id': 'group', 'auth_token_enabled': False},
                          'sa-east-1', '123456789012')

    def test_auth_secret_account_scoped(self):
        with self.assertRaises(ValueError):
            describe_auth({'RedisObservabilityAuth': 'secret',
                           'RedisObservabilitySecretArn': 'arn:aws:secretsmanager:us-east-1:111111111111:secret:x'},
                          {}, 'sa-east-1', '123456789012')

    def test_noauth_tag(self):
        self.assertEqual(describe_auth({'RedisObservabilityAuth': 'noauth'},
                                       {'user_group_id': None, 'auth_token_enabled': False},
                                       'sa-east-1', '123456789012'), {'mode': 'noauth'})

    def test_task_matches_simple(self):
        c = [{'name':'redis-exporter','image':'test','essential':True,
              'environment':[{'name':'REDIS_ADDR','value':'redis://demo:6379'}]},
             {'name':'adot-collector','image':'adot','essential':True,'environment':[]}]
        task = {'family':'x','networkMode':'awsvpc','requiresCompatibilities':['FARGATE'],
                'cpu':'1024','memory':'2048','executionRoleArn':'a','taskRoleArn':'b',
                'containerDefinitions':c}
        self.assertTrue(task_matches(task, dict(task)))
        changed = dict(task, cpu='512')
        self.assertFalse(task_matches(task, changed))


if __name__ == '__main__':
    unittest.main()
