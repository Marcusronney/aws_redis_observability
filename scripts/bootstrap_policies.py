"""Gera exemplos de politicas IAM para revisao e bootstrap POR CONTA.

Nao usa APIs AWS nem altera IAM. Revisar VPCs e privilegiar permissoes com condicoes.
"""
import argparse
import json
from pathlib import Path


def dump(folder, name, doc):
    (folder / name).write_text(json.dumps(doc, indent=2), encoding='utf-8')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--account', required=True)
    p.add_argument('--source-role-arn', required=True, help='role que pode assumir a role de provisionamento')
    p.add_argument('--central-account', default='762012032320')
    p.add_argument('--workspace', default='ws-a32bf55d-d10a-4d22-9873-a937f133db67')
    p.add_argument('--output', type=Path, default=Path('/tmp/redis-iam-bootstrap'))
    a = p.parse_args()
    folder = a.output
    folder.mkdir(parents=True, exist_ok=True)
    arn = f'arn:aws:iam::{a.account}:role/'
    amp = f'arn:aws:aps:us-east-1:{a.central_account}:workspace/{a.workspace}'
    trust = {'Version':'2012-10-17','Statement':[{'Effect':'Allow','Principal':{'AWS':a.source_role_arn},'Action':'sts:AssumeRole'}]}
    dump(folder, 'provision-trust.json', trust)
    actions = [
        'ecs:DescribeClusters', 'ecs:CreateCluster', 'ecs:DescribeServices',
        'ecs:CreateService', 'ecs:UpdateService', 'ecs:DescribeTaskDefinition',
        'ecs:RegisterTaskDefinition', 'ecs:TagResource',
        'ec2:DescribeSubnets', 'ec2:DescribeVpcs', 'ec2:DescribeRouteTables',
        'ec2:DescribeSecurityGroups', 'ec2:CreateSecurityGroup',
        'ec2:AuthorizeSecurityGroupIngress', 'ec2:CreateTags',
        'logs:CreateLogGroup', 'logs:DescribeLogGroups', 'logs:PutRetentionPolicy',
        'elasticache:DescribeReplicationGroups', 'elasticache:DescribeCacheClusters',
        'elasticache:DescribeCacheSubnetGroups', 'elasticache:DescribeServerlessCaches',
        'elasticache:ListTagsForResource', 'secretsmanager:DescribeSecret', 'iam:GetRole',
    ]
    permissions = {'Version':'2012-10-17','Statement':[
        {'Sid':'ManageRedisObservabilityResources','Effect':'Allow','Action':actions,'Resource':'*'},
        {'Sid':'OnlyPassCollectorRolesToECS','Effect':'Allow','Action':'iam:PassRole',
         'Resource':[arn+'redis-observability-ecs-execution',arn+'redis-observability-ecs-task'],
         'Condition':{'StringEquals':{'iam:PassedToService':'ecs-tasks.amazonaws.com'}}},
    ]}
    dump(folder, 'provision-policy.json', permissions)
    read_actions = [
        'ec2:DescribeRegions', 'ec2:DescribeSubnets', 'ec2:DescribeVpcs',
        'ec2:DescribeRouteTables', 'ec2:DescribeSecurityGroups',
        'ecs:DescribeClusters', 'ecs:DescribeServices', 'ecs:DescribeTaskDefinition',
        'elasticache:DescribeReplicationGroups', 'elasticache:DescribeCacheClusters',
        'elasticache:DescribeCacheSubnetGroups', 'elasticache:DescribeServerlessCaches',
        'elasticache:ListTagsForResource', 'secretsmanager:DescribeSecret',
    ]
    read_policy = {'Version':'2012-10-17','Statement':[{'Effect':'Allow',
        'Action':read_actions,'Resource':'*'}]}
    dump(folder, 'controller-read-policy.json', read_policy)
    assume = {'Version':'2012-10-17','Statement':[{
        'Effect':'Allow','Action':'sts:AssumeRole',
        'Resource':arn+'RedisObservabilityProvisionRole'}]}
    dump(folder, 'source-assume-provision-policy.json', assume)
    ecs_trust = {'Version':'2012-10-17','Statement':[{'Effect':'Allow',
        'Principal':{'Service':'ecs-tasks.amazonaws.com'},'Action':'sts:AssumeRole'}]}
    dump(folder, 'ecs-task-trust.json', ecs_trust)
    amp_write = {'Version':'2012-10-17','Statement':[{'Effect':'Allow',
        'Action':'aps:RemoteWrite','Resource':amp}]}
    dump(folder, 'task-amp-write-policy.json', amp_write)
    # Segredos sao separados por conta e nome, preferindo arn restrita de prefixo.
    secret_read = {'Version':'2012-10-17','Statement':[{'Effect':'Allow',
        'Action':'secretsmanager:GetSecretValue',
        'Resource':f'arn:aws:secretsmanager:*:{a.account}:secret:redis-observability/*'}]}
    dump(folder, 'execution-role-secrets-policy.json', secret_read)
    print('Arquivos criados em:', folder)
    print('AVISO: provision-policy.json e um baseline; restringir por regioes/VPCs/recursos para producao.')
    if a.account != a.central_account:
        print('Conta externa: configurar tambem trust de leitura e resource policy AMP central.')


if __name__ == '__main__':
    main()
