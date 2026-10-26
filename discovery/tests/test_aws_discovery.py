
import unittest
from unittest.mock import MagicMock, patch
from discovery.aws_discovery.__main__ import discover_region

from discovery.aws_discovery.elasticache import (
    list_paginated,
)
from discovery.aws_discovery.normalize import (
    normalize_cluster,
    normalize_replication_group,
    normalize_serverless,
    allowed_labels,
)


class TestAWSDiscovery(unittest.TestCase):

    @patch('discovery.aws_discovery.__main__.get_resource_tags', return_value={})
    @patch('discovery.aws_discovery.__main__.discover_resources')
    @patch('discovery.aws_discovery.__main__.create_elasticache_client')
    def test_replication_group_resolves_vpc_for_dynamic_routing(self, make_client, discover, tags):
        make_client.return_value.describe_cache_subnet_groups.return_value = {
            'CacheSubnetGroups': [{'VpcId': 'vpc-test'}]}
        discover.return_value = {'clusters': [{'CacheClusterId': 'node', 'CacheSubnetGroupName': 'private'}],
            'replication_groups': [{'ReplicationGroupId': 'group', 'MemberClusters': ['node']}], 'serverless': []}
        resources = discover_region(MagicMock(), 'us-east-1', {})
        self.assertEqual([r['vpc_id'] for r in resources], ['vpc-test', 'vpc-test'])
        make_client.return_value.describe_cache_subnet_groups.assert_called_once_with(CacheSubnetGroupName='private')

    def test_tag_whitelist(self):
        tags = {
            "Project": "observability",
            "Environment": "lab",
            "CostCenter": "4050",
            "Secret": "nao-expor",
        }

        mapping = {
            "Project": "project",
            "Environment": "environment",
            "CostCenter": "cost_center",
        }

        labels = allowed_labels(tags, mapping)

        self.assertEqual(
            labels["cost_center"],
            "4050",
        )

        self.assertNotIn("Secret", labels)

    def test_cluster_endpoint(self):
        resource = {
            "CacheClusterId": "redis-lab-001",
            "Engine": "redis",
            "CacheClusterStatus": "available",
            "CacheNodes": [
                {
                    "Endpoint": {
                        "Address": "redis.internal",
                        "Port": 6379,
                    }
                }
            ],
        }

        result = normalize_cluster(
            resource,
            {"environment": "lab"},
        )

        self.assertEqual(
            result["id"],
            "redis-lab-001",
        )

        self.assertEqual(
            result["endpoints"][0]["address"],
            "redis.internal",
        )

        self.assertEqual(
            result["endpoints"][0]["port"],
            6379,
        )

    def test_replication_group(self):
        resource = {
            "ReplicationGroupId": "redis-ha",
            "Status": "available",
            "ClusterEnabled": False,
            "NodeGroups": [
                {
                    "NodeGroupId": "0001",
                    "PrimaryEndpoint": {
                        "Address": "primary.internal",
                        "Port": 6379,
                    },
                    "ReaderEndpoint": {
                        "Address": "reader.internal",
                        "Port": 6379,
                    },
                }
            ],
        }

        result = normalize_replication_group(
            resource,
            {},
        )

        self.assertEqual(len(result["endpoints"]), 2)

        roles = {
            item["role"]
            for item in result["endpoints"]
        }

        self.assertIn("primary:0001", roles)
        self.assertIn("reader:0001", roles)

    def test_serverless(self):
        resource = {
            "ServerlessCacheName": "valkey-lab",
            "Engine": "valkey",
            "Status": "available",
            "Endpoint": {
                "Address": "valkey.internal",
                "Port": 6379,
            },
        }

        result = normalize_serverless(
            resource,
            {},
        )

        self.assertEqual(
            result["engine"],
            "valkey",
        )

        self.assertEqual(
            result["endpoints"][0]["role"],
            "primary",
        )

    def test_pagination(self):
        client = MagicMock()
        paginator = client.get_paginator.return_value

        paginator.paginate.return_value = [
            {"CacheClusters": [{"CacheClusterId": "a"}]},
            {"CacheClusters": [{"CacheClusterId": "b"}]},
        ]

        results = list_paginated(
            client,
            "describe_cache_clusters",
            "CacheClusters",
        )

        self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
