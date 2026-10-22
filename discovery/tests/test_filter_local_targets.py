import unittest
from discovery.aws_discovery.filter_local_targets import filter_targets

class TestFilterTargets(unittest.TestCase):
    def test_filter(self):
        policy = {'allowed_accounts': ['123456789012'],
                  'local_targets': ['123456789012:us-east-1:replication_group:a'],
                  'blocked_targets': []}
        items = [
            {'account_id':'123456789012','region':'us-east-1','replication_group_id':'a', 'labels':{}},
            {'account_id':'123456789012','region':'sa-east-1','replication_group_id':'b', 'labels':{'aws_resource_type':'serverless'}},
        ]
        out=filter_targets({'targets':items},policy)
        self.assertEqual(out['target_count'], 1)
        self.assertEqual(out['targets'][0]['replication_group_id'], 'a')
