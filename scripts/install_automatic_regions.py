"""Install host timer and machine identity after applying network-identity TF.

Private X.509 files stay in root-only storage outside checkout/Terraform.
The timer never needs an SSO login or the Vault root token.
"""
import base64
import configparser
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.check_output(args, text=True)


def main():
    import yaml
    config = yaml.safe_load((ROOT / 'discovery/config/automatic-regions.yaml').read_text())
    directory = Path(config['state_directory'])
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    k = ['kubectl', '--context', config['kube_context'], '-n', config['namespace']]
    secret = json.loads(run(*k, 'get', 'secret', 'aws-certificate', '-o', 'json'))
    aws_config = json.loads(run(*k, 'get', 'configmap', 'aws-config', '-o', 'json'))['data']['config']
    parser = configparser.RawConfigParser()
    parser.read_string(aws_config)
    source = next(s for s in parser.sections() if parser.has_option(s, 'credential_process'))
    command = shlex.split(parser.get(source, 'credential_process'))
    for flag in ('--certificate', '--private-key'):
        index = command.index(flag) + 1
        name = Path(command[index]).name
        value = secret['data'].get(name)
        if not value:
            raise ValueError('Expected X.509 file not found in dedicated Secret')
        path = directory / name
        path.write_bytes(base64.b64decode(value))
        path.chmod(0o600)
        command[index] = str(path)
    outputs = json.loads(run('terraform', '-chdir=' + str(ROOT / 'terraform/environments/network-identity'), 'output', '-json'))
    for flag, output in [('--profile-arn', 'profile_arn'), ('--role-arn', 'role_arn')]:
        command[command.index(flag) + 1] = outputs[output]['value']
    profile = config['gateway']['profile']
    home_config = Path.home() / '.aws/config'
    existing = configparser.RawConfigParser()
    if home_config.exists():
        existing.read(home_config)
    section = 'profile ' + profile
    if not existing.has_section(section):
        existing.add_section(section)
    existing.set(section, 'region', config['gateway']['region'])
    existing.set(section, 'credential_process', shlex.join(command))
    temporary = home_config.with_suffix('.automation.tmp')
    with temporary.open('w') as handle:
        existing.write(handle)
    temporary.chmod(0o600)
    temporary.replace(home_config)
    service = ('[Unit]\nDescription=Redis automatic region and network discovery\nAfter=network-online.target\n'
        '[Service]\nType=oneshot\nUser=root\nWorkingDirectory=' + str(ROOT) + '\n'
        'Environment=HOME=/root\nEnvironment=KUBECONFIG=/root/.kube/config\n'
        'ExecStart=' + sys.executable + ' -m scripts.automatic_regions --apply\n'
        'TimeoutStartSec=1200\nTimeoutStopSec=600\nUMask=0077\n')
    timer = ('[Unit]\nDescription=Periodic discovery of AWS regions and Redis networks\n'
        '[Timer]\nOnActiveSec=30\nOnUnitInactiveSec=300\nRandomizedDelaySec=15\n'
        'Unit=redis-regions.service\n[Install]\nWantedBy=timers.target\n')
    Path('/etc/systemd/system/redis-regions.service').write_text(service)
    Path('/etc/systemd/system/redis-regions.timer').write_text(timer)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    print('AUTOMATION_INSTALLED: machine identity configured; timer ready for Terraform ON')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('AUTOMATION_INSTALL_FAILED: ' + type(exc).__name__)
        raise SystemExit(1)
