"""Restore the objects saved before the pilot; delete only recorded new targets."""
import argparse
import json
from pathlib import Path
import subprocess


def clean(obj):
    obj.pop('status', None)
    for key in ('resourceVersion', 'uid', 'creationTimestamp', 'managedFields', 'generation'):
        obj.get('metadata', {}).pop(key, None)
    obj.get('metadata', {}).get('annotations', {}).pop('kubectl.kubernetes.io/last-applied-configuration', None)
    return obj


def restore(session):
    objects = [json.loads((session / name).read_text()) for name in ('cronjob.json', 'configmap.json')]
    old_targets = json.loads((session / 'scrapeconfigs.json').read_text())['items']
    # Freeze the pilot schedule while restoring the shared ConfigMap.
    subprocess.run(['kubectl', 'patch', 'cronjob', 'redis-discovery-aws', '-n', 'redis-observability',
                    '--type=merge', '-p', '{"spec":{"suspend":true}}'], check=True)
    recorded_jobs = session / 'pilot-job-names.json'
    for name in json.loads(recorded_jobs.read_text()) if recorded_jobs.exists() else []:
        if not name.startswith('redis-vpn-acceptance-'):
            raise ValueError('Unexpected pilot Job name')
        subprocess.run(['kubectl', 'delete', 'job', name, '-n', 'redis-observability',
                        '--ignore-not-found=true', '--wait=true'], check=True)
    original_names = {obj['metadata']['name'] for obj in old_targets}
    recorded = session / 'pilot-scrapeconfig-names.json'
    names = json.loads(recorded.read_text()) if recorded.exists() else []
    for name in set(names) - original_names:
        if not name.startswith('auto-aws-'):
            raise ValueError('Unexpected pilot target name')
        subprocess.run(['kubectl', 'delete', 'scrapeconfig', name, '-n', 'redis-observability',
                        '--ignore-not-found=true'], check=True)
    # ConfigMap and targets first; restore the original CronJob last.
    for obj in [objects[1], *old_targets, objects[0]]:
        subprocess.run(['kubectl', 'apply', '-f', '-'], input=json.dumps(clean(obj)), text=True, check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', type=Path, required=True)
    restore(parser.parse_args().session)
