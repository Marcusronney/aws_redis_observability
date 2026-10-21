import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
import reconcile


class FakeTable:
    def __init__(self):
        self.counts = {}
        self.deleted = []
    def put_item(self, Item):
        self.counts[Item["service"]] = 0
    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ReturnValues):
        name = Key["service"]
        self.counts[name] = self.counts.get(name, 0) + 1
        return {"Attributes": {"missing_count": self.counts[name]}}
    def delete_item(self, Key):
        self.deleted.append(Key["service"])


class FakeDdb:
    def __init__(self):
        self.table = FakeTable()
    def Table(self, name):
        return self.table


class FakePager:
    def paginate(self, cluster):
        return [{"serviceArns": ["arn:redis-collector-old", "arn:other"]}]


class FakeEcs:
    def __init__(self):
        self.events = []
    def get_paginator(self, name):
        return FakePager()
    def describe_services(self, cluster, services, include):
        return {"services": [
            {"serviceName": "redis-collector-old", "status": "ACTIVE", "tags": [{"key": "ManagedBy", "value": "redis-observability-controller"}]},
            {"serviceName": "other", "status": "ACTIVE", "tags": []},
        ]}
    def update_service(self, **kwargs):
        self.events.append(("update", kwargs["service"]))
    def delete_service(self, **kwargs):
        self.events.append(("delete", kwargs["service"]))


class TestPrune(unittest.TestCase):
    def test_requires_configured_state_table(self):
        with self.assertRaises(ValueError):
            reconcile.reconcile_stale_services(FakeEcs(), FakeDdb(), {"cluster": "test"}, set(), True, True)

    def test_never_deletes_on_first_missing_cycle(self):
        ecs, ddb = FakeEcs(), FakeDdb()
        cfg = {"cluster": "test", "state_table": "state"}
        result = reconcile.reconcile_stale_services(ecs, ddb, cfg, set(), True, True)
        self.assertIn(("redis-collector-old", "STALE_FIRST_CONFIRMATION"), result)
        self.assertEqual(ecs.events, [])

    def test_two_missing_cycles_delete_only_owned_service(self):
        ecs, ddb = FakeEcs(), FakeDdb()
        cfg = {"cluster": "test", "state_table": "state"}
        reconcile.reconcile_stale_services(ecs, ddb, cfg, set(), True, True)
        reconcile.reconcile_stale_services(ecs, ddb, cfg, set(), True, True)
        self.assertEqual(ecs.events, [("update", "redis-collector-old"), ("delete", "redis-collector-old")])
        self.assertEqual(ddb.table.deleted, ["redis-collector-old"])

    def test_return_resets_missing_counter(self):
        ecs, ddb = FakeEcs(), FakeDdb()
        cfg = {"cluster": "test", "state_table": "state"}
        reconcile.reconcile_stale_services(ecs, ddb, cfg, set(), True, True)
        reconcile.reconcile_stale_services(ecs, ddb, cfg, {"redis-collector-old"}, True, True)
        self.assertEqual(ddb.table.counts["redis-collector-old"], 0)
        reconcile.reconcile_stale_services(ecs, ddb, cfg, set(), True, True)
        self.assertEqual(ecs.events, [])

    def test_no_prune_without_opt_in(self):
        ecs, ddb = FakeEcs(), FakeDdb()
        cfg = {"cluster": "test", "state_table": "state"}
        reconcile.reconcile_stale_services(ecs, ddb, cfg, set(), True, False)
        self.assertEqual(ecs.events, [])
        self.assertEqual(ddb.table.counts, {})


if __name__ == "__main__":
    unittest.main()
