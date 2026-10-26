# Entrada automática de contas pela OU — teste simples

## Status verificado em 26/10/2026

- Conta `848349338183` movida para a OU autorizada.
- Trusted Access ativado; Terraform aplicado: quatro recursos de onboarding e
  duas policies centrais atualizadas.
- Stack instance da conta membro: `CURRENT` / `SUCCEEDED`; autodeploy habilitado.
- Ajustes `sts:SetSourceIdentity` e `sts:TagSession` aprovados e aplicados
  com sucesso via Terraform, preservando as restrições de Organization.
- Preflight real da identidade de máquina: conta `848349338183` encontrada
  pela OU, AssumeRole aceito e `inventory_ready` em 17 regiões habilitadas.
- O Pod no Kind consultou a mesma conta e as 17 regiões sem erros, retornando
  zero recursos (não existem caches na conta membro neste teste). O Job e o
  ConfigMap temporários foram removidos após a consulta somente de leitura.
- 105 testes passaram localmente e na VM. Acesso ao inventário não significa
  coleta Redis ativa. Veja a [evidência](evidence/ou-onboarding-2026-10-26.json).
- A role manual anterior foi preservada. Nenhum cache ou EC2 foi criado nesta etapa.

O Roles Anywhere define uma identidade de origem. O encadeamento de roles exige
`sts:SetSourceIdentity` e `sts:TagSession` tanto na policy da identidade central
quanto na trust policy do destino. A correção mantém os mesmos principals,
roles e condições de Organization; não concede escrita em ElastiCache ou rede,
nem alteração de tags de recursos AWS.
[Referência IAM](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_temp_control-access_monitor.html).

## Objetivo

Extensão de rede entre contas: veja [reconciliação idempotente](REDE_MULTIACCOUNT_IDEMPOTENTE.md).
O código inclui uma role de rede separada e providers por conta. Os planos
IAM/StackSet foram aplicados e a extensão foi habilitada em 26/10/2026,
com AssumeRole validado nas duas contas membro. O laboratório está ON.
O teste de tráfego requer um cache real e continua separado do preflight de contas.

Uma conta ACTIVE dentro de `Contas-Monitoradas` entra no catálogo de discovery
sem cadastrar seu ID em YAML. O StackSets cria o papel de leitura automaticamente;
o discovery assume esse papel e identifica as regiões habilitadas.

Isso prepara a conta para o monitoramento. A coleta Redis ainda depende de
conectividade privada, política de adesão e credenciais no Vault. O teste inicial
do catálogo foi feito com o laboratório OFF; ele foi retomado para testar a rede.

## Configuração do laboratório

| Item | Valor |
|---|---|
| Organização | `o-nipxlggd11` |
| Conta de gerenciamento / identidade central | `762012032320` |
| OU autorizada | `ou-m28r-4zfxlwec` — Contas-Monitoradas |
| Conta membro usada no teste | `848349338183` — redis-observability-lab-02 |
| Papel leitor do catálogo | `RedisObservabilityOrganizationReader` |
| Papel distribuído pelo StackSet | `RedisObservabilityOUDiscoveryRole` |
| StackSet | `redis-observability-account-onboarding` |

A conta de gerenciamento permanece no Root, fora do escopo da OU. Não confunda
a conta que fornece a identidade com as contas cujos Redis serão inventariados.

## Fluxo

```text
Conta criada ou movida para Contas-Monitoradas
  → StackSets distribui RedisObservabilityOUDiscoveryRole
  → catálogo consulta a OU e suas descendentes
  → AssumeRole + validação do ID retornado pelo STS
  → regiões habilitadas e inventário Redis/Valkey
  → rede e credenciais prontas: Secret → ScrapeConfig → Prometheus
```

A criação da role é assíncrona. Enquanto ela não estiver pronta, a conta pode
aparecer como `pending_access`; o próximo ciclo tenta novamente. A falha de uma
conta não transforma seus caches em recursos excluídos.

## Por que existe um segundo nome de role

Você criou `RedisObservabilityMemberDiscoveryRole` pelo Terraform individual.
Esse recurso foi preservado. Para evitar conflito de propriedade, o StackSet
usa `RedisObservabilityOUDiscoveryRole`. Não há importação nem exclusão da role
manual. As policies centrais permitem os dois nomes durante a transição.

Para as próximas contas, use somente o StackSet: não repita o bootstrap individual.

## Onde fica a automação

- `discovery/config/account-catalog.yaml`: Organization, OU e nome da role,
  sem lista individual de contas.
- `terraform/environments/organization-onboarding`: role leitora e StackSet
  SERVICE_MANAGED, com autodeploy para contas adicionadas à OU.
- `terraform/environments/account-access-source`: permissões de AssumeRole nas
  duas identidades centrais, limitadas à organização e às roles configuradas.
- Trusted Access do CloudFormation StackSets: ativação administrativa inicial.
- Posicionamento da conta de teste na OU: operação administrativa inicial.

O catálogo usa `source: organizations`, filtra contas ACTIVE e resolve regiões
com `regions: auto`. Não é necessário reaplicar Terraform por novo ID de conta.

## Testar pelo terminal da VM

```bash
cd /root/redis-vault-test-source
source /root/projetos/aws_redis_observability/.venv-aws/bin/activate
export AWS_PAGER=""
```

**1. Ver quais contas estão na OU:**

```bash
aws organizations list-accounts-for-parent \
  --profile redis-observability-admin \
  --parent-id ou-m28r-4zfxlwec \
  --query 'Accounts[].{Conta:Id,Nome:Name,Estado:State}' \
  --output table
```

O profile administrativo precisa estar autenticado para esse comando. O
discovery periódico usa a identidade X.509 de máquina, não o SSO administrativo.

**2. Conferir o StackSet e o papel criado:**

```bash
aws cloudformation list-stack-instances \
  --profile redis-observability-admin --region us-east-1 \
  --stack-set-name redis-observability-account-onboarding \
  --query 'Summaries[].{Conta:Account,Regiao:Region,Estado:Status}' \
  --output table
```

**3. Resolver o catálogo automaticamente:**

```bash
python -m discovery.aws_discovery.accounts
```

Este comando agora consulta a AWS Organizations; não é mais apenas leitura de
uma lista local. A conta encontrada deve ter estado `admitted`.

**4. Validar o acesso pela identidade de máquina:**

```bash
python -m discovery.aws_discovery.accounts --purpose network --check-access
```

O esperado para a conta membro é `inventory_ready`, com suas regiões habilitadas.
Esse teste funciona com EC2 e caches desligados. Não cria infraestrutura.
`inventory_ready` não é prova de `redis_up=1`.

## Testar uma conta futura

1. Crie a conta na mesma Organization e coloque-a na OU autorizada, ou em uma
   OU descendente. A criação de contas ainda é uma decisão administrativa.
2. Aguarde o StackSet instalar a role; confira o estado das stack instances.
3. Repita a resolução/preflight acima. O novo ID deve aparecer sem editar YAML.
4. Com o laboratório ON, o Pod também resolve a OU em cada execução. O timer
   regional publica o catálogo e a política de contas ao atualizar sua configuração.
5. Antes de esperar métricas Redis, valide rede e credenciais.

Não foi criada uma terceira conta apenas para testar esse evento. O autodeploy
de contas futuras deve ser conferido quando uma nova conta real for adicionada.

## Custos, segurança e pausa

Esta etapa prepara IAM, StackSets e chamadas de leitura. Não cria EC2, Redis,
NAT Gateway, Transit Gateway ou VPN gerenciada. Os discos/IP e histórico AMP
preservados anteriormente continuam sujeitos às suas próprias cobranças.

O papel membro permite somente metadados EC2/ElastiCache. Não lê senhas, cria
caches ou altera redes. Trusted Access permite à administração usar StackSets
na organização; a ativação foi tratada como alteração administrativa explícita.

O `active=false` do laboratório mantém timers/Jobs/exporters parados e preserva
a integração IAM/StackSets. Assim, o bootstrap de novas contas pode acontecer
sem deixar a EC2 VPN ligada; a coleta só ocorre quando o laboratório está ON.

Para remover esta etapa, revise separadamente os estados de
`organization-onboarding` e `account-access-source`. Não destrua o projeto todo
como forma de pausar. Stacks/roles são retidos na saída da OU para evitar
exclusões acidentais; offboarding completo de alvos/credenciais ainda exige
remoção controlada. A entrada automática não implementa a revogação automática.

**Limite atual:** a extensão de rede cross-account está habilitada, mas o
teste real de peering e coleta Redis entre contas depende da criação de um cache
e da validação de tráfego. Nenhum SLA real de cinco minutos foi homologado.

Referências: [autodeploy StackSets](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/stacksets-orgs-manage-auto-deployment.html),
[Trusted Access](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/stacksets-orgs-activate-trusted-access.html).
