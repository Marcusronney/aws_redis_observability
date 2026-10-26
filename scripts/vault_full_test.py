"""Real paid AWS acceptance with cleanup in finally; requires prior cost approval.

Provision only redis-vault-lifecycle-test. Existing Redis/ECS/AMP/VPN are preserved.
Run after Docker image load and terraform init in both lab/cache directories.
"""
import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', type=Path, default=Path('/root/redis-vault-session'))
    parser.add_argument('--resume-after-labels', action='store_true')
    args = parser.parse_args()
    session = args.session
    def run(script, *arguments):
        subprocess.run([sys.executable, 'scripts/' + script + '.py', *arguments,
            *(['--session', str(session)] if script != 'vault_observe' else [])], check=True)
    def observe(expected, name, cost_center=None):
        extra = ['--cost-center', cost_center] if cost_center else []
        run('vault_observe', expected, '--output', str(session / (name + '.json')), *extra)
    try:
        if args.resume_after_labels:
            import json
            for filename in ('creation-observed.json', 'labels-observed.json'):
                evidence = json.loads((session / filename).read_text())
                if evidence.get('passed') is not True or evidence.get('resource') != 'redis-vault-lifecycle-test':
                    raise RuntimeError('Cannot resume without successful initial observations')
        else:
            run('vault_lab', 'on')
            run('vault_cache_test', 'on')
            observe('present', 'creation-observed', 'vault-test-before')
            run('vault_cache_test', 'tags')
            observe('present', 'labels-observed', 'vault-test-after')
        run('vault_cache_test', 'rotate')
        run('vault_cache_test', 'finalize-rotation')
        observe('present', 'rotation-observed', 'vault-test-after')
        run('vault_cache_test', 'wrong-password')
        # Prove local OFF/ON retains Vault and automatically rediscovers both caches.
        run('vault_lab', 'off')
        run('vault_lab', 'on')
        observe('present', 'reactivation-observed', 'vault-test-after')
        run('vault_cache_test', 'off')
        observe('absent', 'deletion-observed')
        # Recreate the AWS resource using the retained Vault credential and TF state.
        run('vault_cache_test', 'on')
        observe('present', 'aws-reactivation-observed', 'vault-test-before')
        run('vault_cache_test', 'off')
        observe('absent', 'final-deletion-observed')
        print('FULL_ACCEPTANCE_OK', flush=True)
    finally:
        # Attempt both cleanups, even if AWS deletion fails. Failure remains visible.
        errors = []
        for script, action in [('vault_cache_test', 'off'), ('vault_lab', 'off')]:
            try:
                run(script, action)
            except Exception as exc:
                errors.append(type(exc).__name__)
        if errors:
            raise RuntimeError('Cleanup incomplete; inspect temporary cache and lab state')


if __name__ == '__main__':
    main()
