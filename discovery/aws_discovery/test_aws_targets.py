
import unittest

from discovery.aws_discovery.targets import (
    select_targets,
)


class TestAWSTargets(unittest.TestCase):

    def make_inventory(self):
        return {
            "errors": [],
            "accounts": [{
                "account_id": "762012032320",
                "regions": [{
                    "region": "us-east-1",
                    "status": "success",
                    "resources": [
                        {
                            "type": "cluster",
                            "id": "redis-lab-001",
                            "engine": "valkey",
                        },
                        {
                            "type": "replication_group",
                            "id": "redis-lab",
                            "status": "available",
                            "cluster_enabled": False,
                            "member_clusters": [
                                "redis-lab-001"
                            ],
                            "transit_encryption": True,
                            "auth_token_enabled": True,
                            "labels": {
                                "cost_center": "1050",
                            },
                            "endpoints": [
                                {
                                    "address": "master.internal",
                                    "port": 6379,
                                    "role": "primary:0001",
                                },
                                {
                                    "address": "reader.internal",
                                    "port": 6379,
                                    "role": "reader:0001",
                                },
                                {
                                    "address": "node.internal",
                                    "port": 6379,
                                    "role": "node_read",
                                },
                            ],
                        },
                    ],
                }],
            }],
        }

    def test_one_target_for_replication_group(self):
        targets = select_targets(
            self.make_inventory()
        )

        self.assertEqual(len(targets), 1)
        self.assertEqual(
            targets[0]["endpoint"],
            "master.internal:6379",
        )

    def test_tls_and_auth(self):
        targets = select_targets(
            self.make_inventory()
        )

        self.assertTrue(targets[0]["tls"])
        self.assertTrue(
            targets[0]["auth_required"]
        )
        self.assertEqual(
            targets[0]["engine"],
            "valkey",
        )

    def test_incomplete_discovery_fails(self):
        inventory = self.make_inventory()
        inventory["errors"] = [
            {"region": "us-east-1"}
        ]

        with self.assertRaises(ValueError):
            select_targets(inventory)

    def test_cluster_mode_enabled_skipped(self):
        inventory = self.make_inventory()

        inventory["accounts"][0]["regions"][0][
            "resources"
        ][1]["cluster_enabled"] = True

        self.assertEqual(
            select_targets(inventory),
            [],
        )

    def test_unknown_engine_skipped(self):
        inventory = self.make_inventory()

        inventory["accounts"][0]["regions"][0][
            "resources"
        ][0]["engine"] = None

        self.assertEqual(
            select_targets(inventory),
            [],
        )


if __name__ == "__main__":
    unittest.main()
