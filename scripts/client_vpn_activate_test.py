"""Activate the temporary Kind discovery after host and Pod connectivity pass.

Run from the isolated source directory using the project's Python venv.
Snapshots must already exist in /root/redis-vpn-session. Teardown restores them.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import yaml

from discovery.aws_discovery.scrapeconfig import generate_manifests

SESSION = Path('/root/redis-vpn-session')
NAMESPACE = 'redis-observability'


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def main():
    for name in ('cronjob.json', 'configmap.json', 'scrapeconfigs.json', 'pod-connectivity-passed'):
        if not (SESSION / name).exists():
            raise ValueError('Missing prerequisite: ' + name)
    if (SESSION / 'teardown-complete').exists():
        raise ValueError('This session was already removed; use a new snapshot directory')
    # Snapshot proved these two operational tags were absent before this pilot.
    arn = 'arn:aws:elasticache:sa-east-1:762012032320:serverlesscache:redis-observability-sa-test'
    tags_response = run(['aws', 'elasticache', 'list-tags-for-resource', '--region', 'sa-east-1',
                         '--profile', 'redis-observability-admin', '--resource-name', arn], capture_output=True)
    tags = {t['Key']: t['Value'] for t in json.loads(tags_response.stdout)['TagList']}
    keys = ('RedisObservability', 'RedisObservabilityAuth')
    if any(key in tags for key in keys):
        raise ValueError('Unexpected preexisting opt-in tags; do not overwrite them')
    (SESSION / 'sa-test-tags-before.json').write_text(json.dumps(tags))
    # Record intent before mutation, so a lost connection still permits rollback.
    (SESSION / 'sa-test-tags-added').touch()
    run(['aws', 'elasticache', 'add-tags-to-resource', '--region', 'sa-east-1',
         '--profile', 'redis-observability-admin', '--resource-name', arn, '--tags',
         'Key=RedisObservability,Value=enabled', 'Key=RedisObservabilityAuth,Value=noauth'], stdout=subprocess.DEVNULL)
    env = dict(os.environ, REDIS_AWS_PROFILE='redis-discovery')
    inventory = run([sys.executable, '-m', 'discovery.aws_discovery'], env=env, capture_output=True)
    (SESSION / 'inventory-test.json').write_text(inventory.stdout)
    targets = run([sys.executable, '-m', 'discovery.aws_discovery.targets', '--input',
                   str(SESSION / 'inventory-test.json')], capture_output=True)
    (SESSION / 'targets-test.json').write_text(targets.stdout)
    policy = yaml.safe_load(Path('discovery/config/collection-routing.yaml').read_text())
    policy['local_networks'][0]['enabled'] = True
    Path('discovery/config/collection-routing.yaml').write_text(yaml.safe_dump(policy, sort_keys=False))
    run([sys.executable, '-m', 'discovery.aws_discovery.filter_local_targets', '--input',
         str(SESSION / 'targets-test.json'), '--output', str(SESSION / 'local-targets-test.json')])
    desired = generate_manifests(json.loads((SESSION / 'local-targets-test.json').read_text()))
    (SESSION / 'pilot-scrapeconfig-names.json').write_text(json.dumps([m['metadata']['name'] for m in desired]))
    configmap = json.loads((SESSION / 'configmap.json').read_text())
    # Patch exactly two public config keys; retain the actual ECS task template.
    patch = {'data': {
        'aws-accounts.yaml': Path('discovery/config/aws-accounts.yaml').read_text(),
        'collection-routing.yaml': yaml.safe_dump(policy, sort_keys=False),
    }}
    (SESSION / 'cluster-changed').touch()
    run(['kubectl', 'patch', 'cronjob', 'redis-discovery-aws', '-n', NAMESPACE,
         '--type=merge', '-p', '{"spec":{"suspend":true}}'])
    current = json.loads(run(['kubectl', 'get', 'cronjob', 'redis-discovery-aws', '-n', NAMESPACE,
                              '-o', 'json'], capture_output=True).stdout)
    if current.get('status', {}).get('active'):
        raise ValueError('A discovery Job is active; wait for completion before migration')
    run(['kubectl', 'patch', 'configmap', configmap['metadata']['name'], '-n', NAMESPACE,
         '--type=merge', '-p', json.dumps(patch)])
    docs = list(yaml.safe_load_all(Path('kubernetes/aws-discovery/cronjob.yaml').read_text()))
    cronjob = next(d for d in docs if d['kind'] == 'CronJob')
    cronjob['spec']['suspend'] = True
    # Patch only the actual CronJob spec, retaining its identity and credential ConfigMap.
    run(['kubectl', 'patch', 'cronjob', 'redis-discovery-aws', '-n', NAMESPACE,
         '--type=merge', '-p', json.dumps({'spec': cronjob['spec']})])
    for index in (1, 2):
        name = f'redis-vpn-acceptance-{int(time.time())}-{index}'
        job_file = SESSION / 'pilot-job-names.json'
        job_names = json.loads(job_file.read_text()) if job_file.exists() else []
        job_names.append(name)
        job_file.write_text(json.dumps(job_names))
        run(['kubectl', 'create', 'job', '--from=cronjob/redis-discovery-aws', name, '-n', NAMESPACE])
        wait = subprocess.run(['kubectl', 'wait', '--for=condition=complete', 'job/' + name,
                               '-n', NAMESPACE, '--timeout=300s'])
        logs = run(['kubectl', 'logs', 'job/' + name, '-n', NAMESPACE], capture_output=True).stdout
        (SESSION / f'discovery-job-{index}.log').write_text(logs)
        print(logs, flush=True)
        wait.check_returncode()
        if index == 2 and any(line.startswith(('CREATE:', 'UPDATE:')) for line in logs.splitlines()):
            raise ValueError('Second execution was not idempotent')


if __name__ == '__main__':
    main()
