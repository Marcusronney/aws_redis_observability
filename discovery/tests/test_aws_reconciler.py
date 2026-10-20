
import unittest
from unittest.mock import Mock

from discovery.aws_discovery.reconciler import (
    OWNER_SELECTOR,
    plan_actions,
    apply_actions,
)


class TestAWSReconciler(unittest.TestCase):

    def make_manifest(self, name="auto-aws-test"):
        return {
            "metadata": {
                "name": name,
                "labels": {
                    "app.kubernetes.io/part-of":
                        "redis-observability",
                    "app.kubernetes.io/managed-by":
                        "redis-discovery-aws",
                    "discovery-source": "aws",
                },
                "annotations": {
                    "redis-observability/target-id":
                        "account:region:cache",
                },
            },
            "spec": {
                "jobName": "redis-aws",
                "metricsPath": "/scrape",
            },
        }

    def test_create_new_resource(self):
        api = Mock()
        api.list_namespaced_custom_object.return_value = {
            "items": []
        }

        actions = plan_actions(
            api, [self.make_manifest()]
        )

        self.assertEqual(actions[0][0], "CREATE")

    def test_unchanged_resource(self):
        manifest = self.make_manifest()
        api = Mock()
        api.list_namespaced_custom_object.return_value = {
            "items": [manifest]
        }

        actions = plan_actions(api, [manifest])

        self.assertEqual(actions[0][0], "UNCHANGED")

    def test_update_changed_resource(self):
        manifest = self.make_manifest()
        old = self.make_manifest()
        old["spec"]["jobName"] = "old-job"

        api = Mock()
        api.list_namespaced_custom_object.return_value = {
            "items": [old]
        }

        actions = plan_actions(api, [manifest])

        self.assertEqual(actions[0][0], "UPDATE")

    def test_stale_without_delete(self):
        api = Mock()
        api.list_namespaced_custom_object.return_value = {
            "items": [self.make_manifest()]
        }

        actions = plan_actions(api, [])

        self.assertEqual(actions[0][0], "STALE")

    def test_selector_is_aws_only(self):
        api = Mock()
        api.list_namespaced_custom_object.return_value = {
            "items": []
        }

        plan_actions(api, [])

        kwargs = api.list_namespaced_custom_object.call_args.kwargs

        self.assertEqual(
            kwargs["label_selector"], OWNER_SELECTOR
        )

        self.assertIn(
            "discovery-source=aws",
            kwargs["label_selector"],
        )

    def test_apply_create(self):
        api = Mock()
        manifest = self.make_manifest()

        apply_actions(
            api,
            [("CREATE", "auto-aws-test", manifest)],
        )

        api.create_namespaced_custom_object.assert_called_once()

    def test_stale_is_never_deleted(self):
        api = Mock()

        apply_actions(
            api,
            [("STALE", "auto-aws-old", None)],
        )

        api.delete_namespaced_custom_object.assert_not_called()


if __name__ == "__main__":
    unittest.main()
