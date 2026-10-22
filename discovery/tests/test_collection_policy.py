import unittest

from discovery.aws_discovery.collection_policy import (
    classify,
    generate_plan,
)


POLICY = {
    "version": 1,
    "allowed_accounts": ["762012032320"],
    "default_mode": "distributed",
    "local_targets": [
        "762012032320:us-east-1:replication_group:redis-observability-lab"
    ],
    "blocked_targets": [],
}


def resource(account, region, name, resource_type="serverless"):
    return {
        "account_id": account,
        "region": region,
        "redis": name,
        "type": resource_type,
        "action": "COLLECTOR_NOT_REGISTERED",
        "ecs_service": None,
    }


class TestCollectionPolicy(unittest.TestCase):

    def test_local_virginia(self):
        item = resource(
            "762012032320",
            "us-east-1",
            "redis-observability-lab",
            "replication_group",
        )
        self.assertEqual(classify(item, POLICY)[0], "LOCAL")

    def test_distributed_saopaulo(self):
        item = resource(
            "762012032320",
            "sa-east-1",
            "redis-observability-sa-test",
        )
        self.assertEqual(
            classify(item, POLICY)[0],
            "DISTRIBUTED",
        )

    def test_unknown_account_blocked(self):
        item = resource(
            "111111111111",
            "sa-east-1",
            "unknown-redis",
        )
        self.assertEqual(classify(item, POLICY)[0], "BLOCKED")

    def test_existing_collector_unchanged(self):
        item = resource(
            "762012032320",
            "sa-east-1",
            "redis-observability-sa-test",
        )
        item["action"] = "UNCHANGED"

        plan = generate_plan(
            {"mode": "READ_ONLY", "resources": [item]},
            POLICY,
        )
        self.assertEqual(
            plan["resources"][0]["planned_action"],
            "UNCHANGED",
        )


if __name__ == "__main__":
    unittest.main()
