"""Vault-backed reconciler. State is non-secret; only its dedicated Secret holds passwords."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import ipaddress
import logging
import os
from pathlib import Path
import re
import socket
import ssl
import time
import urllib.parse
import urllib.request
import uuid

from kubernetes import client, config
import yaml

from .filter_local_targets import filter_targets
from .lifecycle import deletion_plan
from .reconciler import GROUP, VERSION, PLURAL, is_current
from .scrapeconfig import build_scrapeconfig
from .targets import select_targets
from .vault import Vault
from .diagnostics import phase
from .scopes import complete_scopes

LOG = logging.getLogger('redis-vault-controller')


def verify_redis(target, username=None, password=None):
    """Validate TLS and AUTH before publishing credentials to the shared exporter."""
    host, port = target['endpoint'].rsplit(':', 1)
    def command(stream, *parts):
        encoded = [str(part).encode() for part in parts]
        stream.write(b'*' + str(len(encoded)).encode() + b'\r\n' +
                     b''.join(b'$' + str(len(p)).encode() + b'\r\n' + p + b'\r\n' for p in encoded))
        stream.flush()
        return stream.readline(4096)
    try:
        with socket.create_connection((host, int(port)), timeout=5) as connection:
            with ssl.create_default_context().wrap_socket(connection, server_hostname=host) as secure:
                with secure.makefile('rwb') as stream:
                    if target['auth_required'] and command(stream, 'AUTH', username, password) != b'+OK\r\n':
                        raise ValueError()
                    if command(stream, 'PING') != b'+PONG\r\n':
                        raise ValueError()
    except Exception:
        raise RuntimeError('Redis TLS or authentication validation failed') from None


def b64(value):
    return base64.b64encode(value.encode()).decode()


@contextmanager
def lease(api, namespace, name):
    holder = str(uuid.uuid4())
    current = api.read_namespaced_lease(name, namespace)
    now = datetime.now(timezone.utc)
    spec = current.spec
    if spec.holder_identity and spec.renew_time and (now - spec.renew_time).total_seconds() < spec.lease_duration_seconds:
        raise RuntimeError('Another discovery owns the lease')
    current.spec = client.V1LeaseSpec(holder_identity=holder, renew_time=now, lease_duration_seconds=300)
    api.replace_namespaced_lease(name, namespace, current)
    try:
        yield
    finally:
        try:
            current = api.read_namespaced_lease(name, namespace)
            if current.spec.holder_identity == holder:
                current.spec.holder_identity = ''
                api.replace_namespaced_lease(name, namespace, current)
        except Exception:
            LOG.error('Lease release failed; expiry will release it')


class Controller:
    def __init__(self, core, objects, vault, settings):
        self.core, self.objects, self.vault, self.settings = core, objects, vault, settings
        self.namespace = settings['namespace']
        self.scope = settings['scope']
        self.exporters = {True: settings['auth_exporter'], False: settings['noauth_exporter']}
        self.selector = ('app.kubernetes.io/managed-by=redis-discovery-aws,discovery-source=aws,'
                         'redis-observability/scope=' + self.scope)

    def owned(self):
        items, continuation = {}, None
        while True:
            args = {'_continue': continuation} if continuation else {}
            response = self.objects.list_namespaced_custom_object(GROUP, VERSION, self.namespace,
                         PLURAL, label_selector=self.selector, **args)
            for item in response.get('items', []):
                name = item['metadata']['name']
                if name in items:
                    raise ValueError('Duplicate Kubernetes inventory object')
                items[name] = item
            continuation = response.get('metadata', {}).get('continue')
            if not continuation:
                return items

    def secret(self):
        secret = self.core.read_namespaced_secret(self.settings['secret_name'], self.namespace)
        if (secret.metadata.labels or {}).get('redis-observability/scope') != self.scope:
            raise ValueError('Credential Secret is outside this controller scope')
        try:
            passwords = json.loads(base64.b64decode(secret.data['passwords.json']).decode())
            generation = base64.b64decode(secret.data['generation']).decode()
            if not isinstance(passwords, dict) or any(not isinstance(v, str) for v in passwords.values()):
                raise ValueError()
        except Exception:
            raise ValueError('Invalid credential Secret') from None
        return secret, passwords, generation

    def publish(self, passwords):
        secret, old, generation = self.secret()
        if passwords != old:
            generation = str(uuid.uuid4())
            secret.data = {'passwords.json': b64(json.dumps(passwords, sort_keys=True)), 'generation': b64(generation)}
            self.core.replace_namespaced_secret(secret.metadata.name, self.namespace, secret)
        deadline = time.monotonic() + self.settings.get('reload_timeout_seconds', 100)
        while True:
            try:
                urls = self.reload_urls()
                acknowledged = []
                for url in urls:
                    with urllib.request.urlopen(url, timeout=3) as response:
                        acknowledged.append(response.read().decode() == generation)
                if acknowledged and all(acknowledged):
                    return
            except Exception:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('Exporter did not acknowledge the projected credential generation')
            time.sleep(2)

    def reload_urls(self):
        selector = self.settings.get('reload_pod_selector')
        if not selector:
            return [self.settings['reload_ready_url']]
        pods = self.core.list_namespaced_pod(self.namespace, label_selector=selector,
                                             _request_timeout=5).items
        pods = [p for p in pods if not p.metadata.deletion_timestamp]
        if not pods or any(p.status.phase != 'Running' or not p.status.pod_ip for p in pods):
            return []
        urls = []
        for pod in pods:
            ip = ipaddress.ip_address(pod.status.pod_ip)
            host = '[' + str(ip) + ']' if ip.version == 6 else str(ip)
            urls.append('http://' + host + ':9122/')
        return urls

    def healthy(self, target, manifest):
        uri = manifest['spec']['staticConfigs'][0]['targets'][0]
        address = self.exporters[target['auth_required']]
        url = 'http://' + address + '/scrape?' + urllib.parse.urlencode({'target': uri})
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                metrics = response.read().decode()
            if not re.search(r'^redis_up(?:\{[^\n]*\})?\s+1(?:\.0+)?\s*$', metrics, re.M):
                raise ValueError()
        except Exception:
            raise RuntimeError('Exporter could not authenticate and scrape a selected Redis') from None

    def reconcile(self, inventory, accounts, policy):
        phase('reconcile-start')
        if self.settings.get('isolate_failed_scopes'):
            inventory, accounts = complete_scopes(inventory, accounts)
        state_obj = self.core.read_namespaced_config_map(self.settings['state_name'], self.namespace)
        state = json.loads(state_obj.data.get('state.json', '{}'))
        existing = self.owned()
        selected = filter_targets({'targets': select_targets(inventory,
            collect_nodes=self.settings.get('collect_nodes', False))}, policy)['targets']
        # Validate authority before writing a Secret or touching a ScrapeConfig.
        deletion_plan(existing, {}, inventory, accounts, policy, state)
        desired, current_passwords, credential_uris = {}, {}, dict(state.get('credential_uris', {}))
        pairs = []
        for source in selected:
            target = dict(source)
            try:
                if target['auth_required']:
                    phase('vault-credential-read')
                    target['redis_user'], password = self.vault.read(target)
                    phase('redis-auth-validation')
                    verify_redis(target, target['redis_user'], password)
                else:
                    verify_redis(target)
            except RuntimeError:
                if not self.settings.get('isolate_pending_targets'):
                    raise
                print('TARGET_PENDING: credential or Redis validation failed id=' + target['id'], flush=True)
                continue
            manifest = build_scrapeconfig(target, namespace=self.namespace, exporters=self.exporters, scope=self.scope)
            name = manifest['metadata']['name']
            if name in desired:
                raise ValueError('Duplicate desired target')
            desired[name] = manifest
            pairs.append((target, manifest))
            if target['auth_required']:
                uri = manifest['spec']['staticConfigs'][0]['targets'][0]
                if uri in current_passwords:
                    raise ValueError('Credential endpoint collision')
                current_passwords[uri] = password
                credential_uris[target['id']] = uri
        delete, candidate = deletion_plan(existing, desired, inventory, accounts, policy, state,
                    grace_seconds=self.settings.get('delete_grace_seconds', 60),
                    max_deletions=self.settings.get('max_deletions_per_cycle', 1))
        _, passwords, _ = self.secret()
        previous_passwords = dict(passwords)
        passwords.update(current_passwords)
        try:
            phase('exporter-secret-publish')
            self.publish(passwords)
            phase('exporter-health-validation')
            for target, manifest in pairs:
                self.healthy(target, manifest)
        except Exception:
            # Restore the last map if projection/reload or exporter validation failed.
            self.publish(previous_passwords)
            raise
        actions = []
        phase('scrapeconfig-reconcile')
        for name, manifest in sorted(desired.items()):
            if name not in existing:
                self.objects.create_namespaced_custom_object(GROUP, VERSION, self.namespace, PLURAL, manifest)
                actions.append(('CREATE', name))
            elif not is_current(existing[name], manifest):
                self.objects.patch_namespaced_custom_object(GROUP, VERSION, self.namespace, PLURAL, name,
                    {'spec': manifest['spec'], 'metadata': {'labels': manifest['metadata']['labels'],
                     'annotations': manifest['metadata']['annotations'],
                     'resourceVersion': existing[name]['metadata']['resourceVersion'],
                     'uid': existing[name]['metadata']['uid']}})
                actions.append(('UPDATE', name))
            else:
                actions.append(('UNCHANGED', name))
        for name in delete:
            metadata = existing[name]['metadata']
            self.objects.delete_namespaced_custom_object(GROUP, VERSION, self.namespace, PLURAL, name,
                body={'preconditions': {'uid': metadata['uid'], 'resourceVersion': metadata['resourceVersion']}})
            actions.append(('DELETE', name))
        active_ids = {m['metadata']['annotations']['redis-observability/target-id']
                      for n, m in {**existing, **desired}.items() if n not in delete}
        keep = {uri for identity, uri in credential_uris.items() if identity in active_ids}
        retired, scan_time = {}, candidate['last_scan']
        for uri in list(passwords):
            if uri not in keep:
                first = state.get('retired', {}).get(uri, scan_time)
                if scan_time - first >= self.settings.get('credential_retention_seconds', 90):
                    del passwords[uri]
                else:
                    retired[uri] = first
        self.publish(passwords)
        candidate.update({'credential_uris': {i: u for i, u in credential_uris.items() if i in active_ids},
                          'retired': retired})
        state_obj.data = {'state.json': json.dumps(candidate, sort_keys=True)}
        self.core.replace_namespaced_config_map(state_obj.metadata.name, self.namespace, state_obj)
        for action, name in actions:
            print(action + ': ' + name, flush=True)
        return actions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--accounts', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--settings', type=Path, required=True)
    args = parser.parse_args()
    settings = yaml.safe_load(args.settings.read_text())
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    core, objects = client.CoreV1Api(), client.CustomObjectsApi()
    vault = None
    with lease(client.CoordinationV1Api(), settings['namespace'], settings['lease_name']):
        try:
            phase('vault-login')
            vault = Vault(os.environ['VAULT_ADDR'], os.environ['VAULT_CACERT'],
                          os.environ['VAULT_ROLE'], os.environ['VAULT_JWT_FILE'])
            Controller(core, objects, vault, settings).reconcile(
                json.loads(args.inventory.read_text()), yaml.safe_load(args.accounts.read_text()),
                yaml.safe_load(args.policy.read_text()))
        finally:
            if vault:
                try:
                    vault.close()
                except Exception:
                    LOG.error('Vault token revocation failed; short token TTL applies')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Kubernetes/Vault error objects may contain serialized Secret bodies.
        LOG.error('Cycle aborted safely (%s); no exception body was logged', type(exc).__name__)
        raise SystemExit(1)
