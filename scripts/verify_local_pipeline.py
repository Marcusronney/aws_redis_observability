"""Read-only acceptance: fresh Prometheus metrics and reload acknowledgement in every Pod."""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.parse
import urllib.request

from kubernetes import client, config


class AcceptanceError(RuntimeError):
    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--context', default='kind-redis-monitoring')
    parser.add_argument('--namespace', default='redis-vault-lab')
    parser.add_argument('--redis-name', required=True)
    parser.add_argument('--nodes', action='store_true')
    parser.add_argument('--timeout', type=int, default=360)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config.load_kube_config(context=args.context)
    core, custom = client.CoreV1Api(), client.CustomObjectsApi()
    # systemd services may omit HOME; keep kubectl on the same config as Python.
    command_env = dict(os.environ, KUBECONFIG=os.environ.get('KUBECONFIG', str(Path.home() / '.kube/config')))
    kubectl = ['kubectl', '--context', args.context]
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    def start_forward():
        return subprocess.Popen([*kubectl, '-n', 'monitoring', 'port-forward',
            'svc/monitoring-kube-prometheus-prometheus', str(port) + ':9090', '--address=127.0.0.1'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=command_env)
    forward = start_forward()
    selector = 'redis_name=' + json.dumps(args.redis_name) + (',redis_node!=""' if args.nodes else '')
    metric = 'redis_up{' + selector + '}'
    query = metric + ' and (time() - timestamp(' + metric + ') < 90)'
    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline:
            if forward.poll() is not None:
                forward = start_forward()
                time.sleep(2)
                continue
            try:
                manifests = custom.list_namespaced_custom_object('monitoring.coreos.com', 'v1alpha1',
                    args.namespace, 'scrapeconfigs', label_selector='redis-observability/scope=vault-lab',
                    _request_timeout=10)['items']
                expected = [m for m in manifests if m['spec']['staticConfigs'][0]['labels'].get('redis_name') == args.redis_name]
                url = 'http://127.0.0.1:' + str(port) + '/api/v1/query?' + urllib.parse.urlencode({'query': query})
                with urllib.request.urlopen(url, timeout=10) as response:
                    metrics = json.load(response)['data']['result']
                healthy = [m for m in metrics if float(m['value'][1]) == 1]
                if not expected or len(healthy) != len(expected):
                    time.sleep(5)
                    continue
                secret = core.read_namespaced_secret('exporter-credentials', args.namespace, _request_timeout=10)
                generation = base64.b64decode(secret.data['generation']).decode()
                pods = core.list_namespaced_pod(args.namespace, label_selector='app=exporter-auth',
                    _request_timeout=10).items
                acknowledgements = []
                for pod in pods:
                    if pod.metadata.deletion_timestamp:
                        continue
                    ready = bool(pod.status.container_statuses) and all(c.ready for c in pod.status.container_statuses)
                    result = subprocess.run([*kubectl, '-n', args.namespace, 'exec', pod.metadata.name,
                        '-c', 'password-reload', '--', 'python', '-c',
                        "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:9122/',timeout=5).read().decode())"],
                        capture_output=True, text=True, timeout=20, env=command_env)
                    acknowledgements.append({'pod': pod.metadata.name,
                        'ready': ready, 'generation_ack': result.returncode == 0 and result.stdout.strip() == generation})
                if not acknowledgements or not all(p['ready'] and p['generation_ack'] for p in acknowledgements):
                    time.sleep(5)
                    continue
                evidence = {'passed': True, 'verified_at': datetime.now(timezone.utc).isoformat(),
                    'namespace': args.namespace, 'redis_name': args.redis_name,
                    'scrapeconfigs': len(expected), 'nodes_required': args.nodes,
                    'metrics': healthy, 'authenticated_exporters': acknowledgements}
                args.output.write_text(json.dumps(evidence, indent=2))
                print('LOCAL_PIPELINE_OK: fresh redis_up=1 and all exporters acknowledged credentials')
                return
            except (OSError, ValueError, subprocess.TimeoutExpired):
                time.sleep(5)
        raise AcceptanceError('metrics/reload deadline exceeded; check discovery Jobs and Prometheus access')
    finally:
        forward.terminate()
        forward.wait(timeout=10)


if __name__ == '__main__':
    try:
        main()
    except AcceptanceError as exc:
        print('LOCAL_PIPELINE_FAILED: ' + str(exc))
        raise SystemExit(1)
    except Exception as exc:
        print('LOCAL_PIPELINE_FAILED: ' + type(exc).__name__)
        raise SystemExit(1)
