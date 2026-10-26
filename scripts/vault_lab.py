"""Reproducible local lab ON/OFF. No AWS resources are created by this module."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

NS = 'redis-vault-lab'
TF = 'terraform/environments/vault-lab'


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def apply(active):
    run('terraform', '-chdir=' + TF, 'apply', '-auto-approve', '-var=active=' + str(active).lower())


def off():
    # Stop new work, then active work, before withdrawing only lab-owned targets.
    run('kubectl', '-n', NS, 'patch', 'cronjob', 'discovery', '--type=merge',
        '-p', '{"spec":{"suspend":true}}', stdout=subprocess.DEVNULL)
    run('kubectl', '-n', NS, 'delete', 'jobs', '--all', '--cascade=foreground',
        '--wait=true', '--timeout=270s', stdout=subprocess.DEVNULL)
    run('kubectl', '-n', NS, 'delete', 'scrapeconfig',
        '-l', 'redis-observability/scope=vault-lab,app.kubernetes.io/managed-by=redis-discovery-aws',
        '--wait=true', stdout=subprocess.DEVNULL)
    apply(False)
    print('OFF_OK: local jobs stopped, lab targets withdrawn, deployments zero; Vault PVC/recovery preserved')


def up(session):
    # The Terraform runtime hook now performs bootstrap/unseal and rollout.
    # Preserve this legacy CLI as a compatibility wrapper.
    os.environ['TF_VAR_session_directory'] = str(session)
    os.environ['TF_VAR_python_executable'] = sys.executable
    apply(True)


def acceptance():
    from kubernetes import client, config
    config.load_kube_config(context='kind-redis-monitoring')
    batch = client.BatchV1Api()
    batch.patch_namespaced_cron_job('discovery', NS, {'spec': {'suspend': True}})
    try:
        deadline = time.monotonic() + 270
        while batch.read_namespaced_cron_job('discovery', NS).status.active:
            if time.monotonic() >= deadline:
                raise RuntimeError('Scheduled discovery did not finish')
            time.sleep(5)
        for index in (1, 2):
            name = 'acceptance-' + str(int(time.time())) + '-' + str(index)
            cron = batch.read_namespaced_cron_job('discovery', NS)
            batch.create_namespaced_job(NS, client.V1Job(metadata=client.V1ObjectMeta(name=name),
                                                       spec=cron.spec.job_template.spec))
            try:
                run('kubectl', '-n', NS, 'wait', '--for=condition=complete', 'job/' + name, '--timeout=250s')
            finally:
                run('kubectl', '-n', NS, 'logs', 'job/' + name)
    finally:
        batch.patch_namespaced_cron_job('discovery', NS, {'spec': {'suspend': False}})
    print('ACCEPTANCE_OK: two real AWS discovery/Vault/exporter reconciliation jobs completed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['on', 'off', 'test'])
    parser.add_argument('--session', type=Path, default=Path('/root/redis-vault-session'))
    args = parser.parse_args()
    if args.action == 'off':
        off()
    elif args.action == 'on':
        up(args.session)
    else:
        try:
            up(args.session)
            acceptance()
        finally:
            off()


if __name__ == '__main__':
    main()
