"""Terraform local runtime hook: no AWS mutations and no recursive TF calls."""
import os
import subprocess
import sys


def run(*args):
    if args and args[0] == 'kubectl':
        args = (args[0], '--context', os.environ.get('RUNTIME_KUBE_CONTEXT', 'kind-redis-monitoring'), *args[1:])
    subprocess.run(args, check=True)


def gateway(active):
    """Operate one explicitly configured existing instance; never create/delete AWS resources."""
    identifier = os.environ.get('RUNTIME_GATEWAY_ID', '')
    if not identifier:
        return
    import boto3
    session = boto3.Session(profile_name=os.environ['RUNTIME_AWS_PROFILE'])
    if session.client('sts').get_caller_identity()['Account'] != os.environ['RUNTIME_GATEWAY_ACCOUNT']:
        raise RuntimeError('Gateway AWS account mismatch')
    ec2 = session.client('ec2', region_name=os.environ['RUNTIME_GATEWAY_REGION'])
    response = ec2.describe_instances(InstanceIds=[identifier])
    instance = response['Reservations'][0]['Instances'][0]
    state = instance['State']['Name']
    if active:
        if state == 'stopping':
            ec2.get_waiter('instance_stopped').wait(InstanceIds=[identifier])
            state = 'stopped'
        if state == 'stopped':
            ec2.start_instances(InstanceIds=[identifier])
        elif state not in ('running', 'pending'):
            raise RuntimeError('Gateway cannot be started in its current state')
        ec2.get_waiter('instance_status_ok').wait(InstanceIds=[identifier],
            WaiterConfig={'Delay': 10, 'MaxAttempts': 60})
    else:
        if state in ('running', 'pending'):
            ec2.stop_instances(InstanceIds=[identifier])
        elif state not in ('stopped', 'stopping'):
            raise RuntimeError('Gateway cannot be stopped in its current state')
        ec2.get_waiter('instance_stopped').wait(InstanceIds=[identifier],
            WaiterConfig={'Delay': 10, 'MaxAttempts': 60})
    print('GATEWAY_' + ('ON' if active else 'OFF') + '_OK', flush=True)


def main():
    namespace = os.environ.get('RUNTIME_NAMESPACE', 'redis-vault-lab')
    active = os.environ['RUNTIME_ACTIVE'] == 'true'
    if active:
        gateway(True)
        run(sys.executable, 'scripts/vault_lab_bootstrap.py', '--session', os.environ['RUNTIME_SESSION'],
            '--accounts-file', os.environ['RUNTIME_ACCOUNTS'], '--routing-file', os.environ['RUNTIME_ROUTING'])
        for name in ('vault', 'exporter-auth', 'exporter-noauth'):
            run('kubectl', '-n', namespace, 'rollout', 'status', 'deployment/' + name, '--timeout=180s')
        if os.path.exists('/etc/systemd/system/redis-regions.timer'):
            run('systemctl', 'start', 'redis-regions.timer')
        print('RUNTIME_ON_OK: Vault and exporters ready; discovery enabled', flush=True)
    else:
        if os.path.exists('/etc/systemd/system/redis-regions.timer'):
            run('systemctl', 'stop', 'redis-regions.timer', 'redis-regions.service')
        # TF has already suspended the CronJob and scaled deployments down.
        # Finish active jobs before withdrawing targets to prevent recreation.
        run('kubectl', '-n', namespace, 'delete', 'jobs', '--all', '--cascade=foreground',
            '--wait=true', '--timeout=270s')
        run('kubectl', '-n', namespace, 'delete', 'scrapeconfig', '-l',
            'redis-observability/scope=vault-lab,app.kubernetes.io/managed-by=redis-discovery-aws', '--wait=true')
        gateway(False)
        print('RUNTIME_OFF_OK: jobs and lab targets removed; PVC and credentials retained', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('RUNTIME_FAILED: ' + type(exc).__name__, flush=True)
        raise SystemExit(1)
