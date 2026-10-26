"""Bootstrap the isolated lab without passing secrets through Terraform or stdout.

Run on the Kind host. The session directory must survive OFF/ON and is root-only.
Single-key manual unseal is for this disposable lab, not production HA.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import sys

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
import yaml
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from discovery.aws_discovery.accounts import load_config

NS = os.environ.get('RUNTIME_NAMESPACE', 'redis-vault-lab')


def put(api, kind, name, body):
    read = getattr(api, 'read_namespaced_' + kind)
    create = getattr(api, 'create_namespaced_' + kind)
    replace = getattr(api, 'replace_namespaced_' + kind)
    try:
        old = read(name, NS)
    except ApiException as exc:
        if exc.status != 404:
            raise RuntimeError('Kubernetes bootstrap read failed') from None
        create(NS, body)
    else:
        body['metadata']['resourceVersion'] = old.metadata.resource_version
        replace(name, NS, body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--source-namespace', default='redis-observability')
    parser.add_argument('--accounts-file', type=Path, default=Path('discovery/config/vault-local-accounts.yaml'))
    parser.add_argument('--routing-file', type=Path, default=Path('discovery/config/vault-local-routing.yaml'))
    args = parser.parse_args()
    os.umask(0o077)
    session = args.session.resolve()
    checkout = Path(__file__).resolve().parents[1]
    if session == checkout or checkout in session.parents:
        raise ValueError('Recovery material must be stored outside the checkout')
    session.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(session, 0o700)
    kube_context = os.environ.get('RUNTIME_KUBE_CONTEXT', 'kind-redis-monitoring')
    config.load_kube_config(context=kube_context)
    core = client.CoreV1Api()

    def metadata(name):
        return {'name': name, 'namespace': NS, 'labels': {'redis-observability/scope': 'vault-lab'}}

    # CA and server keys stay outside source control and outside Terraform state.
    if not (session / 'tls.crt').exists():
        san = ','.join('DNS:' + host for host in ('vault', 'vault.' + NS,
            'vault.' + NS + '.svc', 'vault.' + NS + '.svc.cluster.local')) + ',IP:127.0.0.1'
        (session / 'server.ext').write_text('subjectAltName=' + san + '\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n')
        commands = [
            ['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-days', '365',
             '-subj', '/CN=redis-vault-lab-ca', '-keyout', 'ca.key', '-out', 'ca.crt'],
            ['openssl', 'req', '-newkey', 'rsa:3072', '-nodes', '-subj', '/CN=vault.' + NS + '.svc.cluster.local',
             '-keyout', 'tls.key', '-out', 'server.csr'],
            ['openssl', 'x509', '-req', '-in', 'server.csr', '-CA', 'ca.crt', '-CAkey', 'ca.key',
             '-CAcreateserial', '-days', '365', '-extfile', 'server.ext', '-out', 'tls.crt']]
        for command in commands:
            subprocess.run(command, cwd=session, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    put(core, 'secret', 'vault-tls', {'apiVersion': 'v1', 'kind': 'Secret',
        'metadata': metadata('vault-tls'), 'type': 'Opaque', 'data': {
            name: base64.b64encode((session / name).read_bytes()).decode() for name in ('ca.crt', 'tls.crt', 'tls.key')}})

    # Copy existing Roles Anywhere credentials in memory; do not introduce AWS keys.
    source = core.read_namespaced_secret('redis-discovery-aws-cert', args.source_namespace)
    put(core, 'secret', 'aws-certificate', {'apiVersion': 'v1', 'kind': 'Secret',
        'metadata': metadata('aws-certificate'), 'type': 'Opaque', 'data': source.data})
    source = core.read_namespaced_config_map('redis-discovery-aws-config', args.source_namespace)
    put(core, 'config_map', 'aws-config', {'apiVersion': 'v1', 'kind': 'ConfigMap',
        'metadata': metadata('aws-config'), 'data': source.data})
    accounts = load_config(args.accounts_file)
    policy = yaml.safe_load(args.routing_file.read_text())
    settings = dict(namespace=NS, scope='vault-lab', secret_name='exporter-credentials',
        state_name='discovery-state', lease_name='discovery',
        auth_exporter='exporter-auth.' + NS + '.svc.cluster.local:9121',
        noauth_exporter='exporter-noauth.' + NS + '.svc.cluster.local:9121',
        reload_ready_url='http://exporter-auth.' + NS + '.svc.cluster.local:9122/',
        reload_pod_selector='app=exporter-auth',
        collect_nodes=accounts.get('collect_nodes', True),
        isolate_failed_scopes=accounts.get('isolate_failed_scopes', False),
        delete_grace_seconds=60, max_deletions_per_cycle=1, credential_retention_seconds=90)
    put(core, 'config_map', 'discovery-config', {'apiVersion': 'v1', 'kind': 'ConfigMap',
        'metadata': metadata('discovery-config'), 'data': {
            'accounts.yaml': yaml.safe_dump(accounts), 'policy.yaml': yaml.safe_dump(policy),
            'settings.yaml': yaml.safe_dump(settings)}})
    # These objects are deliberately NOT Terraform resources: no Secret refresh into state.
    for kind, name, body in [
        ('secret', 'exporter-credentials', {'apiVersion': 'v1', 'kind': 'Secret',
            'data': {'passwords.json': base64.b64encode(b'{}').decode(),
                     'generation': base64.b64encode(str(uuid.uuid4()).encode()).decode()}}),
        ('config_map', 'discovery-state', {'apiVersion': 'v1', 'kind': 'ConfigMap', 'data': {'state.json': '{}'}})]:
        try:
            getattr(core, 'read_namespaced_' + kind)(name, NS)
        except ApiException as exc:
            if exc.status != 404:
                raise RuntimeError('Bootstrap object read failed') from None
            body['metadata'] = metadata(name)
            getattr(core, 'create_namespaced_' + kind)(NS, body)
    coordination = client.CoordinationV1Api()
    try:
        coordination.read_namespaced_lease('discovery', NS)
    except ApiException as exc:
        if exc.status != 404:
            raise RuntimeError('Lease bootstrap failed') from None
        coordination.create_namespaced_lease(NS, {'apiVersion': 'coordination.k8s.io/v1', 'kind': 'Lease',
            'metadata': metadata('discovery'), 'spec': {'leaseDurationSeconds': 300}})

    subprocess.run(['kubectl', '--context', kube_context, '-n', NS, 'rollout', 'status', 'deployment/vault', '--timeout=180s'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    forward = subprocess.Popen(['kubectl', '--context', kube_context, '-n', NS, 'port-forward', '--pod-running-timeout=180s', 'svc/vault', '18200:8200',
        '--address=127.0.0.1'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    context = ssl.create_default_context(cafile=str(session / 'ca.crt'))
    token = None
    def request(method, path, data=None, timeout=30):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Vault-Token'] = token
        req = urllib.request.Request('https://127.0.0.1:18200/v1/' + path, headers=headers,
            method=method, data=json.dumps(data).encode() if data is not None else None)
        try:
            with urllib.request.urlopen(req, context=context, timeout=timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code != 404 and path != 'sys/health':
                print('BOOTSTRAP_HTTP_ERROR: ' + method + ' ' + path + ' HTTP ' + str(exc.code), flush=True)
            raise RuntimeError('Vault bootstrap HTTP ' + str(exc.code)) from None
    try:
        for attempt in range(150):
            if forward.poll() is not None:
                raise RuntimeError('Vault port-forward exited before initialization')
            try:
                initialized = request('GET', 'sys/init')['initialized']
                break
            except (OSError, RuntimeError):
                time.sleep(2)
        else:
            raise RuntimeError('Vault did not become reachable')
        init_file = session / 'vault-init.json'
        if not initialized:
            if init_file.exists():
                raise RuntimeError('Vault storage lost; refusing to overwrite recovery material')
            material = request('PUT', 'sys/init', {'secret_shares': 1, 'secret_threshold': 1}, timeout=180)
            with init_file.open('x') as recovery:
                recovery.write(json.dumps(material))
                recovery.flush()
                os.fsync(recovery.fileno())
        if not init_file.exists():
            raise RuntimeError('Existing Vault requires its original recovery file')
        material = json.loads(init_file.read_text())
        request('PUT', 'sys/unseal', {'key': material['keys_base64'][0]})
        print('VAULT_UNSEALED: recovery material saved outside Terraform', flush=True)
        token = material['root_token']
        # Raft leadership/post-unseal setup can lag the unseal response.
        for _ in range(60):
            try:
                health = request('GET', 'sys/health')
                if health.get('initialized') and health.get('sealed') is False and not health.get('standby'):
                    break
            except (OSError, RuntimeError):
                pass
            time.sleep(2)
        else:
            raise RuntimeError('Vault did not become active after unseal')
        if 'kv/' not in request('GET', 'sys/mounts'):
            request('POST', 'sys/mounts/kv', {'type': 'kv', 'options': {'version': '2'}})
        if 'kubernetes/' not in request('GET', 'sys/auth'):
            request('POST', 'sys/auth/kubernetes', {'type': 'kubernetes'})
        request('POST', 'auth/kubernetes/config', {'kubernetes_host': 'https://kubernetes.default.svc:443'})
        import re
        if 'catalog' in accounts:
            if accounts['catalog'].get('vault_scope') != 'project':
                raise ValueError('Dynamic accounts require explicit project Vault scope')
            # Dedicated project prefix only; no write/list permission or access to other secrets.
            discovery_policy = 'path "kv/data/redis-observability/*" { capabilities = ["read"] }'
        else:
            identifiers = {str(a['account_id']) for a in accounts['accounts']}
            if not identifiers or any(not re.fullmatch(r'[0-9]{12}', a) for a in identifiers):
                raise ValueError('Explicit valid account IDs required for Vault policy')
            discovery_policy = '\n'.join('path "kv/data/redis-observability/' + a + '/*" { capabilities = ["read"] }'
                                          for a in sorted(identifiers))
        request('PUT', 'sys/policies/acl/discovery', {'policy': discovery_policy})
        request('POST', 'auth/kubernetes/role/discovery', {
            'bound_service_account_names': ['discovery'], 'bound_service_account_namespaces': [NS],
            'audience': 'vault', 'token_policies': ['discovery'], 'token_ttl': '300s', 'token_max_ttl': '900s'})
        path = 'kv/data/redis-observability/762012032320/us-east-1/replication_group/redis-observability-lab'
        # Only seed a missing KV entry. Never overwrite a rotated credential on ON.
        try:
            request('GET', path)
        except RuntimeError as exc:
            if str(exc) != 'Vault bootstrap HTTP 404':
                raise
            old = core.read_namespaced_secret('redis-aws-auth', args.source_namespace)
            password = base64.b64decode(old.data['password']).decode()
            request('POST', path, {'options': {'cas': 0}, 'data': {'username': 'default', 'password': password}})
        print('BOOTSTRAP_OK: TLS, persistent Vault, scoped Kubernetes auth, KV credential; no secret values logged')
    finally:
        token = None
        forward.terminate()
        forward.wait(timeout=10)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # SDK exceptions may embed Secret objects. Never emit their representation.
        print('BOOTSTRAP_FAILED: ' + type(exc).__name__)
        raise SystemExit(1)
