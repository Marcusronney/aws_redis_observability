import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.client_vpn_restore import restore


class RestoreTests(unittest.TestCase):
    def snapshot(self, directory, names):
        for filename, kind in [('cronjob.json', 'CronJob'), ('configmap.json', 'ConfigMap')]:
            (directory / filename).write_text(json.dumps({
                'kind': kind, 'metadata': {'name': 'original', 'resourceVersion': 'obsolete'},
                'status': {'ignored': True}}))
        (directory / 'scrapeconfigs.json').write_text(json.dumps({'items': [
            {'kind': 'ScrapeConfig', 'metadata': {'name': 'auto-aws-existing'}}]}))
        (directory / 'pilot-scrapeconfig-names.json').write_text(json.dumps(names))

    def test_deletes_only_recorded_new_targets_and_restores_cronjob_last(self):
        with tempfile.TemporaryDirectory() as temp, patch('subprocess.run') as run:
            directory = Path(temp)
            self.snapshot(directory, ['auto-aws-existing', 'auto-aws-pilot'])
            restore(directory)
            deleted = [call.args[0][3] for call in run.call_args_list if call.args[0][1] == 'delete']
            self.assertEqual(deleted, ['auto-aws-pilot'])
            last = json.loads(run.call_args_list[-1].kwargs['input'])
            self.assertEqual(last['kind'], 'CronJob')
            self.assertNotIn('resourceVersion', last['metadata'])
            self.assertNotIn('status', last)

    def test_rejects_unexpected_recorded_object_name(self):
        with tempfile.TemporaryDirectory() as temp, patch('subprocess.run') as run:
            directory = Path(temp)
            self.snapshot(directory, ['unrelated-monitoring'])
            with self.assertRaises(ValueError):
                restore(directory)
            self.assertFalse(any(call.args[0][1] == 'delete' for call in run.call_args_list))


if __name__ == '__main__':
    unittest.main()
