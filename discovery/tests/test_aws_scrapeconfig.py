
import unittest

from discovery.aws_discovery.scrapeconfig import (
    build_scrapeconfig,
    generate_manifests,
)


class TestAWSScrapeConfig(unittest.TestCase):

    def make_target(self):
        return {
            "id": (
                "762012032320:us-east-1:"
                "redis-observability-lab"
            ),
            "account_id": "762012032320",
            "region": "us-east-1",
            "replication_group_id": "redis-observability-lab",
            "engine": "valkey",
            "endpoint": "master.redis.internal:6379",
            "endpoint_role": "primary",
            "tls": True,
            "auth_required": True,
            "labels": {
                "project": "aws-redis-observability",
                "environment": "lab",
                "cost_center": "1050",
            },
        }

    def test_scrapeconfig_aws_labels(self):
        result = build_scrapeconfig(self.make_target())

        labels = result["metadata"]["labels"]

        self.assertEqual(
            labels["discovery-source"], "aws"
        )

        self.assertEqual(
            labels["app.kubernetes.io/part-of"],
            "redis-observability",
        )

    def test_redis_tls(self):
        result = build_scrapeconfig(self.make_target())

        uri = result["spec"]["staticConfigs"][0][
            "targets"
        ][0]

        self.assertEqual(
            uri, "rediss://master.redis.internal:6379"
        )

    def test_exporter_service(self):
        result = build_scrapeconfig(self.make_target())

        replacement = result["spec"][
            "relabelings"
        ][2]["replacement"]

        self.assertIn(
            "redis-exporter-aws", replacement
        )

    def test_missing_tls_rejected(self):
        target = self.make_target()
        target["tls"] = False

        with self.assertRaises(ValueError):
            build_scrapeconfig(target)

    def test_reserved_label_rejected(self):
        target = self.make_target()
        target["labels"]["job"] = "override"

        with self.assertRaises(ValueError):
            build_scrapeconfig(target)

    def test_target_count(self):
        target = self.make_target()

        manifests = generate_manifests({
            "mode": "targets-dry-run",
            "target_count": 1,
            "targets": [target],
        })

        self.assertEqual(len(manifests), 1)

    def test_duplicate_target_rejected(self):
        target = self.make_target()

        with self.assertRaises(ValueError):
            generate_manifests({
                "mode": "targets-dry-run",
                "target_count": 2,
                "targets": [target, target],
            })


if __name__ == "__main__":
    unittest.main()
