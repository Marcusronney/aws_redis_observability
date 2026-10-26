"""Save non-secret, real AWS/Kind/Prometheus acceptance observations."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request

import boto3
from kubernetes import client, config

NS = 'redis-vault-lab'
NAME = 'redis-vault-lifecycle-test'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('expected', choices=['present', 'absent'])
    parser.add_argument('--cost-center')
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config.load_kube_config(context='kind-redis-monitoring')
    custom, core = client.CustomObjectsApi(), client.CoreV1Api()
    aws = boto3.Session(profile_name='redis-observability-admin').client('elasticache', region_name='us-east-1')
    forward = subprocess.Popen(['kubectl', '-n', 'monitoring', 'port-forward',
        'svc/monitoring-kube-prometheus-prometheus', '19092:9090', '--address=127.0.0.1'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def get(path):
        with urllib.request.urlopen('http://127.0.0.1:19092' + path, timeout=15) as response:
            return json.load(response)['data']
    result = {'expected': args.expected, 'resource': NAME, 'namespace': NS,
              'started_at': datetime.now(timezone.utc).isoformat()}
    available_at = None
    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline:
            try:
                groups = aws.describe_replication_groups(ReplicationGroupId=NAME)['ReplicationGroups']
                available = groups[0]['Status'] == 'available'
                aws_absent = False
            except aws.exceptions.ReplicationGroupNotFoundFault:
                available, aws_absent = False, True
            if available and available_at is None:
                available_at = time.monotonic()
                result['first_observed_available_at'] = datetime.now(timezone.utc).isoformat()
                print('AWS_AVAILABLE_OBSERVED', flush=True)
            manifests = custom.list_namespaced_custom_object('monitoring.coreos.com', 'v1alpha1', NS,
                'scrapeconfigs', label_selector='redis-observability/scope=vault-lab')['items']
            own = [m for m in manifests if m['metadata'].get('annotations', {}).get(
                'redis-observability/target-id') == '762012032320:us-east-1:' + NAME]
            try:
                active = [t for t in get('/api/v1/targets?state=active')['activeTargets']
                          if t['labels'].get('redis_name') == NAME]
                metrics = get('/api/v1/query?' + urllib.parse.urlencode({
                    'query': 'redis_up{redis_name="' + NAME + '",discovery_type="aws"} and '
                             '(time() - timestamp(redis_up{redis_name="' + NAME + '",discovery_type="aws"}) < 90)'}))['result']
            except OSError:
                time.sleep(5)
                continue
            if args.expected == 'present':
                healthy = [m for m in metrics if float(m['value'][1]) == 1 and
                           (not args.cost_center or m['metric'].get('cost_center') == args.cost_center)]
                passed = available and len(own) == 1 and any(t['health'] == 'up' for t in active) and bool(healthy)
                if passed:
                    result.update(metrics=healthy, active_targets=len(active), scrapeconfigs=len(own),
                        seconds_from_first_observed_available=round(time.monotonic() - available_at, 2))
            else:
                import base64
                secret = core.read_namespaced_secret('exporter-credentials', NS)
                passwords = json.loads(base64.b64decode(secret.data['passwords.json']))
                credential_removed = all(NAME not in uri for uri in passwords)
                passed = aws_absent and not own and not active and credential_removed
                if passed:
                    result.update(aws_absent=True, scrapeconfigs=0, active_targets=0, credential_entry_removed=True)
            if passed:
                result.update(passed=True, completed_at=datetime.now(timezone.utc).isoformat())
                args.output.write_text(json.dumps(result, indent=2))
                print('OBSERVATION_OK: ' + args.expected, flush=True)
                return
            time.sleep(5)
        result.update(passed=False, completed_at=datetime.now(timezone.utc).isoformat())
        args.output.write_text(json.dumps(result, indent=2))
        raise RuntimeError('Observation deadline reached')
    finally:
        forward.terminate()
        forward.wait(timeout=10)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('OBSERVATION_FAILED: ' + type(exc).__name__, flush=True)
        raise SystemExit(1)
