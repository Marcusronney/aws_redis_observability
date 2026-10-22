# aws_redis_observability
Procedimento atual para centralizar a coleta no Prometheus do Kind:
[piloto Client VPN São Paulo](docs/CLIENT_VPN_PILOTO.md).
O piloto foi validado e removido; consulte o [resultado](docs/CLIENT_VPN_RESULTADO.md).

Descoberta e monitoramento automatizados de instâncias AWS Redis em múltiplas contas no Kubernetes, utilizando Terraform, Python, Vault, redis_exporter e Prometheus Operator.

# AWS Redis Observability

Automated multi-account AWS Redis discovery and monitoring on Kubernetes using Terraform, Python, Vault, redis_exporter and Prometheus Operator.

## Objetivo

Desenvolver uma solução de observabilidade capaz de descobrir automaticamente instâncias Redis distribuídas em múltiplas contas e regiões AWS, configurando seu monitoramento no Prometheus sem intervenções manuais.

## Tecnologias

- Kubernetes / Kind
- Docker
- Terraform
- Python / boto3
- Prometheus Operator
- redis_exporter
- HashiCorp Vault
- AWS IAM / STS / ElastiCache
- Helm

## Arquitetura

O Terraform provisiona a infraestrutura de monitoramento. Um controller Python executado periodicamente no Kubernetes descobre recursos Redis nas contas AWS configuradas e reconcilia os targets do Prometheus Operator.

As tags AWS autorizadas são convertidas em labels de métricas, permitindo segmentação por projeto, ambiente e centro de custo.

As credenciais são gerenciadas pelo Vault.

## Ambientes

| Ambiente | Plataforma | Finalidade |
|---|---|---|
| Local | Rocky Linux + Kind | Desenvolvimento e validação |
| AWS | EKS | Integração e testes em nuvem |

## Critérios principais

- Descoberta dinâmica sem cadastro manual de Redis.
- Suporte a múltiplas contas e regiões AWS.
- Exporters separados para Redis autenticados e não autenticados.
- Credenciais protegidas e associadas corretamente a cada recurso.
- Reconciliação idempotente.
- Remoção automática de targets inexistentes.
- Monitoramento de novos Redis em até 5 minutos, validado por testes.

## Status

Piloto temporário Client VPN validado: VM e Pod Kind acessaram os Valkey de São
Paulo; discovery idempotente e `redis_up=1` no Prometheus local foram confirmados.
Os recursos da VPN foram removidos e a configuração anterior foi restaurada.
Redis, ECS/AMP e WireGuard existentes permanecem operacionais.
Consulte [estado, implantação e pendências](docs/CLIENT_VPN_PILOTO.md).
