"""Operate ONLY the explicitly approved temporary cache using ephemeral TF input.

No secrets in arguments, output, plans or state. Root-only recovery file is used
by this host-side test operator; the discovery Pod always uses Kubernetes auth.
"""
import argparse
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

from kubernetes import client, config

NS = 'redis-vault-lab'
NAME = 'redis-vault-lifecycle-test'
TF = 'terraform/environments/vault-test-cache'
KV = 'kv/data/redis-observability/762012032320/us-east-1/replication_group/' + NAME


@contextmanager
def admin(session):
    token = json.loads((session / 'vault-init.json').read_text())['root_token']
    context = ssl.create_default_context(cafile=str(session / 'ca.crt'))
    forward = subprocess.Popen(['kubectl', '-n', NS, 'port-forward', '--pod-running-timeout=180s',
        'svc/vault', '18201:8200', '--address=127.0.0.1'], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def request(method, path, body=None):
        req = urllib.request.Request('https://127.0.0.1:18201/v1/' + path, method=method,
            headers={'X-Vault-Token': token, 'Content-Type': 'application/json'},
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, context=context, timeout=60) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise RuntimeError('Vault HTTP ' + str(exc.code)) from None
    try:
        for _ in range(90):
            if forward.poll() is not None:
                raise RuntimeError('Vault operator port-forward exited')
            try:
                request('GET', 'sys/init')
                break
            except OSError:
                time.sleep(2)
        else:
            raise RuntimeError('Vault operator connection failed')
        yield request
    finally:
        token = None
        forward.terminate()
        forward.wait(timeout=10)


def apply(values, enabled, cost_center='vault-test-before'):
    env = dict(os.environ, TF_VAR_auth_token=values['password'])
    # Provider trace logs can contain API payloads even for write-only inputs.
    for name in ('TF_LOG', 'TF_LOG_CORE', 'TF_LOG_PROVIDER', 'TF_LOG_PATH'):
        env.pop(name, None)
    subprocess.run(['terraform', '-chdir=' + TF, 'apply', '-input=false', '-auto-approve',
        '-var=enabled=' + str(enabled).lower(), '-var=auth_version=' + str(values.get('auth_version', 1)),
        '-var=auth_strategy=' + values.get('auth_strategy', 'ROTATE'),
        '-var=cost_center=' + cost_center], env=env, check=True)
    # Verify all current/back-up states, never print the searched value.
    for path in Path(TF).glob('*.tfstate*'):
        if values['password'].encode() in path.read_bytes():
            raise RuntimeError('Secret value unexpectedly present in Terraform state')
    print('STATE_SECRET_CHECK_OK', flush=True)


def job(expect_success=True):
    name = 'vault-security-' + str(int(time.time()))
    batch = client.BatchV1Api()
    cron = batch.read_namespaced_cron_job('discovery', NS)
    # Independent ownership: CronJob history/concurrency management must not
    # delete or interfere with a manually orchestrated acceptance Job.
    batch.create_namespaced_job(NS, client.V1Job(metadata=client.V1ObjectMeta(name=name,
        labels={'app.kubernetes.io/component': 'vault-acceptance'}), spec=cron.spec.job_template.spec))
    print('ACCEPTANCE_JOB_CREATED: ' + name, flush=True)
    deadline = time.monotonic() + 260
    while time.monotonic() < deadline:
        status = batch.read_namespaced_job_status(name, NS).status
        if status.succeeded or status.failed:
            if bool(status.succeeded) != expect_success:
                raise RuntimeError('Unexpected acceptance Job result')
            return name
        time.sleep(5)
    raise RuntimeError('Acceptance Job timed out')


@contextmanager
def discovery_paused():
    batch = client.BatchV1Api()
    batch.patch_namespaced_cron_job('discovery', NS, {'spec': {'suspend': True}})
    try:
        deadline = time.monotonic() + 270
        while True:
            jobs = batch.list_namespaced_job(NS).items
            pending = [j for j in jobs if not j.status.succeeded and not j.status.failed and
                not any(c.type in ('Failed', 'Complete') and c.status == 'True' for c in (j.status.conditions or []))]
            if not pending:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Existing discovery work did not finish before test')
            time.sleep(5)
        yield
    finally:
        batch.patch_namespaced_cron_job('discovery', NS, {'spec': {'suspend': False}})


def verify_sync(values):
    """Require a fresh successful Job and actual exporter acknowledgement of its map."""
    with discovery_paused():
        name = job(expect_success=True)
    secret = client.CoreV1Api().read_namespaced_secret('exporter-credentials', NS)
    passwords = json.loads(base64.b64decode(secret.data['passwords.json']))
    own = [password for uri, password in passwords.items() if NAME in uri]
    if own != [values['password']]:
        raise RuntimeError('Exporter Secret does not contain the current Vault credential')
    generation = base64.b64decode(secret.data['generation']).decode()
    ack = subprocess.run(['kubectl', '-n', NS, 'exec', 'deploy/exporter-auth', '-c', 'password-reload', '--',
        'python', '-c', 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:9122/",timeout=5).read().decode())'],
        check=True, text=True, capture_output=True).stdout.strip()
    if ack != generation:
        raise RuntimeError('Exporter has not acknowledged the current credential map')
    return {'job': name, 'secret_resource_version': secret.metadata.resource_version,
            'generation_acknowledged': True, 'vault_equals_projected_credential': True}


def wrong_password_test(request):
    core = client.CoreV1Api()
    batch = client.BatchV1Api()
    batch.patch_namespaced_cron_job('discovery', NS, {'spec': {'suspend': True}})
    try:
        deadline = time.monotonic() + 260
        while batch.read_namespaced_cron_job('discovery', NS).status.active:
            if time.monotonic() >= deadline:
                raise RuntimeError('Active scheduled discovery did not finish')
            time.sleep(5)
        original = request('GET', KV)['data']
        good = original['data']
        old_secret = core.read_namespaced_secret('exporter-credentials', NS).data
        old_state = core.read_namespaced_config_map('discovery-state', NS).data
        objects = client.CustomObjectsApi()
        def targets():
            return objects.list_namespaced_custom_object('monitoring.coreos.com', 'v1alpha1', NS,
                'scrapeconfigs', label_selector='redis-observability/scope=vault-lab')['items']
        old_targets = targets()
        bad = dict(good, password=secrets.token_urlsafe(36))
        request('POST', KV, {'options': {'cas': original['metadata']['version']}, 'data': bad})
        try:
            failed = job(expect_success=False)
            if core.read_namespaced_secret('exporter-credentials', NS).data != old_secret:
                raise RuntimeError('Invalid credential changed the working Secret')
            if core.read_namespaced_config_map('discovery-state', NS).data != old_state:
                raise RuntimeError('Invalid credential advanced reconciliation state')
            if targets() != old_targets:
                raise RuntimeError('Invalid credential changed working targets')
            print('WRONG_PASSWORD_FAIL_CLOSED_OK: ' + failed, flush=True)
        finally:
            latest = request('GET', KV)['data']['metadata']['version']
            request('POST', KV, {'options': {'cas': latest}, 'data': good})
        recovered = job(expect_success=True)
        print('CREDENTIAL_RECOVERY_OK: ' + recovered, flush=True)
    finally:
        batch.patch_namespaced_cron_job('discovery', NS, {'spec': {'suspend': False}})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['on', 'off', 'rotate', 'finalize-rotation', 'tags', 'wrong-password'])
    parser.add_argument('--session', type=Path, default=Path('/root/redis-vault-session'))
    args = parser.parse_args()
    if args.action == 'off':
        # Deletion must remain possible even if Vault is sealed/unreachable.
        # count=0 does not use this required ephemeral input in any resource.
        apply({'password': 'unused-write-only-input-during-destruction'}, False)
        return
    config.load_kube_config(context='kind-redis-monitoring')
    with admin(args.session) as request:
        try:
            entry = request('GET', KV)['data']
        except RuntimeError as exc:
            if str(exc) != 'Vault HTTP 404' or args.action != 'on':
                raise
            request('POST', KV, {'options': {'cas': 0}, 'data': {
                'username': 'default', 'password': secrets.token_urlsafe(36), 'auth_version': 1}})
            entry = request('GET', KV)['data']
        values, version = entry['data'], entry['metadata']['version']
        if args.action in ('on', 'off', 'tags'):
            apply(values, args.action != 'off', 'vault-test-after' if args.action == 'tags' else 'vault-test-before')
        elif args.action == 'rotate':
            # AWS ROTATE accepts old/new during Vault propagation. Original caches are untouched.
            pending = dict(values, password=values.get('pending_password') or secrets.token_urlsafe(36),
                           auth_version=values.get('auth_version', 1) + 1, auth_strategy='ROTATE',
                           previous_password=values['password'])
            staged = dict(values, pending_password=pending['password'])
            request('POST', KV, {'options': {'cas': version}, 'data': staged})
            apply(pending, True, 'vault-test-after')
            latest = request('GET', KV)['data']['metadata']['version']
            request('POST', KV, {'options': {'cas': latest}, 'data': pending})
            print('ROTATION_PUBLISHED_OK: AWS dual-token ROTATE, Vault updated', flush=True)
        elif args.action == 'finalize-rotation':
            evidence = verify_sync(values)
            final = dict(values, auth_strategy='SET', auth_version=values.get('auth_version', 1) + 1)
            apply(final, True, 'vault-test-after')
            # Verify both directions so a network failure cannot masquerade as rejection.
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from discovery.aws_discovery.controller import verify_redis
            import boto3
            cache = boto3.Session(profile_name='redis-observability-admin').client('elasticache', region_name='us-east-1')
            endpoint = cache.describe_replication_groups(ReplicationGroupId=NAME)['ReplicationGroups'][0]['NodeGroups'][0]['PrimaryEndpoint']
            target = {'endpoint': endpoint['Address'] + ':' + str(endpoint['Port']), 'auth_required': True}
            verify_redis(target, values['username'], values['password'])
            if not values.get('previous_password'):
                raise RuntimeError('Original credential missing for rejection test')
            try:
                verify_redis(target, values['username'], values['previous_password'])
            except RuntimeError:
                pass
            else:
                raise RuntimeError('Previous credential still accepted after SET')
            verify_redis(target, values['username'], values['password'])
            final.pop('previous_password', None)
            latest = request('GET', KV)['data']['metadata']['version']
            request('POST', KV, {'options': {'cas': latest}, 'data': final})
            evidence.update(old_password_rejected=True, new_password_accepted=True, auth_strategy='SET')
            (args.session / 'rotation-secure.json').write_text(json.dumps(evidence, indent=2))
            print('ROTATION_FINALIZED_OK: Secret reloaded, new credential accepted, previous credential rejected', flush=True)
        else:
            wrong_password_test(request)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('CACHE_TEST_FAILED: ' + type(exc).__name__, flush=True)
        raise SystemExit(1)
