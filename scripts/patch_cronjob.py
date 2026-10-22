"""Aplica novo pipeline ao CronJob YAML existente, com backup.

Usa a estrutura real do YAML (nao substituicoes cegas de strings).
"""
import argparse
from pathlib import Path
import yaml


def patch(path, mode):
    parts = list(yaml.safe_load_all(path.read_text(encoding='utf-8')))
    jobs = [p for p in parts if isinstance(p,dict) and p.get('kind') == 'CronJob'
            and p.get('metadata', {}).get('name') == 'redis-discovery-aws']
    if len(jobs) != 1:
        raise ValueError('CronJob redis-discovery-aws ausente/duplicado')
    job = jobs[0]
    containers = job['spec']['jobTemplate']['spec']['template']['spec']['containers']
    candidates = [c for c in containers if c.get('name') == 'aws-discovery']
    if len(candidates) != 1:
        raise ValueError('Container aws-discovery ausente/duplicado')
    container = candidates[0]
    container['image'] = 'redis-discovery-aws:local-v5'
    # Leitura do inventario; controller sempre executa ANTES da filtragem local.
    # Durante rollout, so dry-run. Em apply, exige provision_role_arn na config e confirm-account.
    apply_suffix = (' --apply --confirm-account 762012032320 --max-changes 1' if mode == 'apply'
                    else '')
    pipeline = '''set -eu
 echo "Iniciando Discovery AWS..."
 python -m discovery.aws_discovery > /tmp/inventory.json
 python -m discovery.aws_discovery.targets --input /tmp/inventory.json > /tmp/targets.json
 echo "Planejamento ECS distribuido..."
 python -m discovery.aws_discovery.ecs_controller --inventory /tmp/inventory.json --output /tmp/ecs-plan.json%s
 echo "Filtrando somente targets locais..."
 python -m discovery.aws_discovery.filter_local_targets --input /tmp/targets.json --output /tmp/local-targets.json
 python -m discovery.aws_discovery.scrapeconfig --input /tmp/local-targets.json --output /tmp/scrapeconfig.yaml
 echo "Reconciliando ScrapeConfigs locais..."
 python -m discovery.aws_discovery.reconciler --input /tmp/local-targets.json --apply
 echo "Ciclo concluido."
''' % apply_suffix
    container['command'] = ['sh', '-ec']
    container['args'] = [pipeline]
    backup = path.with_name(path.name + '.pre-v5.bak')
    if not backup.exists():
        backup.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
    path.write_text(yaml.safe_dump_all(parts, sort_keys=False, allow_unicode=True), encoding='utf-8')
    print(f'CronJob alterado: {path}; backup: {backup}; modo: {mode}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--file', required=True, type=Path)
    parser.add_argument('--mode', choices=['dry-run', 'apply'], default='dry-run')
    a = parser.parse_args()
    patch(a.file, a.mode)
