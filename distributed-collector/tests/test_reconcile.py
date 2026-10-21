import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
import reconcile


class FakeCache:
    def describe_serverless_caches(self, ServerlessCacheName):
        return {"ServerlessCaches": [{
            "ServerlessCacheName": ServerlessCacheName, "Status": "available", "Engine": "valkey",
            "SecurityGroupIds": ["sg-1"], "SubnetIds": ["subnet-1"],
            "UserGroupId": None, "ARN": "arn:aws:elasticache:sa-east-1:123:serverlesscache:test",
            "Endpoint": {"Address": "test.cache.amazonaws.com", "Port": 6379},
        }]}


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.inventory = {"errors": [], "accounts": [{"account_id": "123", "regions": [{
            "region": "sa-east-1", "status": "success", "resources": [
                {"type": "serverless", "id": "test", "region": "sa-east-1",
                 "account_id": "123", "status": "available", "labels": {"cost_center": "4050"}},
            ]
        }]}]}
        self.cfg = {"subnet_ids": ["subnet-1"], "credential_secret_arns": {}}

    def test_inventory_selection(self):
        targets = reconcile.targets_from_inventory(self.inventory, "123", "sa-east-1", self.cfg)
        self.assertEqual(len(targets), 1)

    def test_incomplete_inventory_cannot_reconcile(self):
        self.inventory["errors"].append({"error": "AccessDenied"})
        with self.assertRaises(ValueError):
            reconcile.targets_from_inventory(self.inventory, "123", "sa-east-1", self.cfg)

    def test_missing_region_cannot_reconcile(self):
        with self.assertRaises(ValueError):
            reconcile.targets_from_inventory(self.inventory, "123", "us-east-1", self.cfg)

    def test_auth_no_password_is_not_embedded(self):
        resource = self.inventory["accounts"][0]["regions"][0]["resources"][0]
        target, err = reconcile.enrich_resource(FakeCache(), resource, self.cfg)
        self.assertIsNone(err)
        self.assertFalse(target["auth_required"])
        data = reconcile.build_adot_config(target, {"region": "us-east-1", "remote_write_url": "https://example"})
        self.assertIn("aws_region: sa-east-1", data)
        self.assertIn("cost_center: '4050'", data)
        self.assertNotIn("REDIS_PASSWORD", data)

    def test_bad_labels_rejected(self):
        with self.assertRaises(ValueError):
            reconcile.safe_labels({"__param_target": "secret"})

    def test_stable_hash_is_idempotent(self):
        self.assertEqual(reconcile.stable_hash({"b": 2, "a": 1}), reconcile.stable_hash({"a": 1, "b": 2}))

    def test_replication_group_can_be_enriched(self):
        class FakeReplication:
            def describe_replication_groups(self, ReplicationGroupId):
                return {"ReplicationGroups": [{"ARN": "arn:rg", "Status": "available", "ClusterEnabled": False,
                    "NodeGroups": [{"PrimaryEndpoint": {"Address": "primary.cache", "Port": 6379}}],
                    "MemberClusters": ["cluster-001"], "AuthTokenEnabled": True,
                    "TransitEncryptionEnabled": True}]}
            def describe_cache_clusters(self, CacheClusterId):
                return {"CacheClusters": [{"Engine": "valkey", "SecurityGroups": [{"SecurityGroupId": "sg-1"}],
                    "CacheSubnetGroupName": "test-subnets"}]}
            def describe_cache_subnet_groups(self, CacheSubnetGroupName):
                return {"CacheSubnetGroups": [{"Subnets": [{"SubnetIdentifier": "subnet-1"}]}]}
        rg = {"type": "replication_group", "id": "rg", "region": "us-east-1", "account_id": "123", "labels": {}}
        target, reason = reconcile.enrich_resource(FakeReplication(), rg, {"credential_secret_arns": {}})
        self.assertIsNone(target)
        self.assertIn("credential", reason)
        target, reason = reconcile.enrich_resource(FakeReplication(), rg, {"credential_secret_arns": {"arn:rg": "arn:secret"}})
        self.assertIsNone(reason)
        self.assertTrue(target["auth_required"])
        self.assertEqual(target["host"], "primary.cache")


if __name__ == "__main__":
    unittest.main()
