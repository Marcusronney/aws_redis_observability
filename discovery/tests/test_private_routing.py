import copy
import socket
import unittest
from unittest.mock import Mock, patch

from discovery.aws_discovery.filter_local_targets import filter_targets, check_private_route
from discovery.aws_discovery.targets import select_targets, serverless_auth
from discovery.aws_discovery.scrapeconfig import build_scrapeconfig
from scripts.client_vpn_configure import render


class PrivateRoutingTests(unittest.TestCase):
    def setUp(self):
        self.target = {
            "id": "123:sa-east-1:serverless:dynamic", "account_id": "123",
            "region": "sa-east-1", "replication_group_id": "dynamic",
            "vpc_id": "vpc-pilot", "endpoint": "cache.example:6379",
            "endpoint_role": "primary", "tls": True, "auth_required": False,
            "engine": "valkey", "aws_resource_type": "serverless",
            "labels": {"aws_resource_type": "serverless", "environment": "lab"},
            "tags": {"RedisObservability": "enabled"},
        }
        self.route = {"enabled": True, "account_id": "123", "region": "sa-east-1",
                      "vpc_id": "vpc-pilot", "destination_cidrs": ["172.31.0.0/16"],
                      "required_tags": {"RedisObservability": "enabled"}}
        self.policy = {"allowed_accounts": ["123"], "local_networks": [self.route]}

    def select(self, probe=None):
        return filter_targets({"targets": [self.target]}, self.policy, probe or Mock())

    def test_new_resource_requires_no_manual_target(self):
        probe = Mock()
        self.assertEqual(self.select(probe)["target_count"], 1)
        probe.assert_called_once_with(self.target, self.route)

    def test_account_region_vpc_tag_and_enabled_are_all_required(self):
        for field, value in [("account_id", "other"), ("region", "us-east-1"),
                             ("vpc_id", "other"), ("tags", {})]:
            original = copy.deepcopy(self.target)
            self.target[field] = value
            self.assertEqual(self.select()["target_count"], 0)
            self.target = original
        self.route["enabled"] = False
        self.assertEqual(self.select()["target_count"], 0)

    def test_blocklist_overrides_route(self):
        self.policy["blocked_targets"] = ["123:sa-east-1:serverless:dynamic"]
        self.assertEqual(self.select()["target_count"], 0)

    def test_unreachable_route_aborts_cycle(self):
        with self.assertRaises(OSError):
            self.select(Mock(side_effect=OSError("no route")))

    def test_unknown_auth_fails_closed(self):
        self.target["auth_required"] = None
        with self.assertRaises(ValueError):
            self.select()

    def test_authenticated_pilot_is_not_enabled(self):
        self.target["auth_required"] = True
        self.assertEqual(self.select()["target_count"], 0)

    def test_dns_outside_cidr_is_rejected_before_connect(self):
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.80.1.2", 6379))]), patch("socket.create_connection") as connect:
            with self.assertRaises(ValueError):
                check_private_route(self.target, self.route)
            connect.assert_not_called()

    def test_absent_usergroup_is_not_auth_evidence(self):
        self.assertIsNone(serverless_auth({}))
        self.assertFalse(serverless_auth({"tags": {"RedisObservabilityAuth": "noauth"}}))
        self.assertIsNone(serverless_auth({"user_group_id": "acl", "tags": {"RedisObservabilityAuth": "noauth"}}))

    def test_serverless_inventory_to_exporter_and_labels(self):
        resource = {"type": "serverless", "id": "dynamic", "status": "available",
                    "engine": "valkey", "vpc_id": "vpc-pilot",
                    "tags": {"RedisObservabilityAuth": "noauth", "RedisObservability": "enabled"},
                    "labels": {"environment": "lab", "project": "observability"},
                    "endpoints": [{"role": "primary", "address": "cache.example", "port": 6379}]}
        inventory = {"errors": [], "accounts": [{"account_id": "123", "regions": [
            {"region": "sa-east-1", "status": "success", "resources": [resource]}]}]}
        targets = select_targets(inventory)
        manifest = build_scrapeconfig(targets[0])
        self.assertIn("redis-exporter-noauth", manifest["spec"]["relabelings"][2]["replacement"])
        labels = manifest["spec"]["staticConfigs"][0]["labels"]
        self.assertEqual(labels["redis_name"], "dynamic")
        self.assertEqual(labels["aws_resource_type"], "serverless")

    def test_client_preserves_default_route_and_separate_key(self):
        config = render("client\nremote example 443\n")
        self.assertIn("route-nopull", config)
        self.assertIn("route 172.31.0.0 255.255.0.0", config)
        self.assertNotIn("<key>", config)
        with self.assertRaises(ValueError):
            render("redirect-gateway def1\n")


if __name__ == "__main__":
    unittest.main()
