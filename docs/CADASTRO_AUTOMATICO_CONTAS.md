# Contas AWS: começar com duas e reduzir toil

## O que mudou

O fluxo atual usa um catálogo único:
`discovery/config/account-catalog.yaml`. O Pod de discovery e o controlador
regional da VM consomem esse catálogo por meio de
`vault-local-accounts.yaml`. Não há duas listas independentes de contas.

Há dois modos:

| Modo | Entrada de uma conta |
|---|---|
| `explicit` | Adicionar o ID uma vez no catálogo; indicado para o teste inicial |
| `organizations` | Uma conta ACTIVE entra na OU autorizada; o catálogo a encontra sem editar a lista |

Sem Organizations real não existe descoberta mágica de contas: o modo explícito
reduz o cadastro a um arquivo, mas ainda tem essa intervenção. O modo Organizations
é o que elimina o cadastro individual futuro.

**Status 26/10/2026:** a conta `848349338183` existe e integra a OU.
O modo atual usa `organizations`. O StackSet instalou a role com sucesso;
o preflight real retornou `inventory_ready` em 17 regioes.
Veja o [guia e status da etapa OU](ETAPA_OU_MONITORAMENTO.md).
Os exemplos de cadastro individual abaixo representam o modo anterior.

Verificação desta entrega: 104 testes passaram no Windows e na VM. Os quatro
novos módulos e o ambiente vault-lab validaram; a imagem foi carregada no Kind.
O preflight real da conta atual retornou `inventory_ready` e 17 regiões,
usando apenas APIs de leitura, com o laboratório OFF.
[Evidência](evidence/account-admission-2026-10-26.json).

## Fluxo de entrada

```text
ID no catálogo explícito OU conta ACTIVE na OU autorizada
                         ↓
Resolver catálogo → montar ARN padronizado → testar AssumeRole
                         ↓
Regiões habilitadas → inventário Redis/Valkey com e sem tags
                         ↓
Rede e credenciais disponíveis → Secret → ScrapeConfig → Prometheus
```

O papel padrão nas contas membro é:

```text
arn:aws:iam::ID_DA_CONTA:role/RedisObservabilityMemberDiscoveryRole
```

A conta central pode usar a identidade base existente; contas membro usam
AssumeRole e verificação STS do ID retornado. Não são criados exporters,
Prometheus ou clusters por conta.

## 1. Preparar duas contas reais

Crie a segunda conta e obtenha seu ID. Não use os IDs fictícios dos testes.
O exemplo completo está em
`discovery/config/account-catalog.two-accounts.example.yaml`.

Adicione a segunda entrada ao catálogo real, preservando as configurações:

```yaml
accounts:
  - name: estudos
    account_id: '762012032320'
  - name: segunda-conta
    account_id: 'SUBSTITUA_PELO_ID_REAL'
```

`defaults.regions: auto` descobre as regiões habilitadas. `enabled: false` e
`exclude_account_ids` impedem novas consultas, mas não devem ser tratados como
revogação completa de uma conta previamente monitorada; veja offboarding abaixo.

### Bootstrap IAM, uma vez

São necessárias permissões de ambos os lados: a identidade central pode assumir
o papel e a conta membro confia nela. Os módulos são separados para permitir
revisão do plano e acesso administrativo autorizado em cada conta.

**Na conta central**, configure o profile administrativo e execute:

```bash
terraform -chdir=terraform/environments/account-access-source init
terraform -chdir=terraform/environments/account-access-source plan
terraform -chdir=terraform/environments/account-access-source apply
```

Esse módulo adiciona `sts:AssumeRole` às duas roles centrais existentes, limitado
ao nome padronizado de role membro. No modo Organizations, configure
`organization_id` para limitar os destinos por `aws:ResourceOrgID` e informe
`organization_reader_role_arn`. O acesso não funciona sem o trust na conta destino.

**Na segunda conta**, prepare um `terraform.tfvars` usando o exemplo em
`terraform/environments/account-bootstrap/terraform.tfvars.example`. Informe
profile administrativo, ID real e as duas roles centrais confiáveis. Depois:

```bash
terraform -chdir=terraform/environments/account-bootstrap init
terraform -chdir=terraform/environments/account-bootstrap plan
terraform -chdir=terraform/environments/account-bootstrap apply
```

O provider verifica o ID da conta para evitar criar a role no destino errado.
Não configure um Organization ID fictício: essa condição só serve quando as
contas realmente pertencem à organização indicada.

A role membro só lê metadados ElastiCache/EC2. Não cria caches, altera redes,
acessa senhas ou administra IAM. Os comandos `apply` acima **não foram executados**
por esta implementação: exigem a conta real e a revisão administrativa do plano.

## 2. Conferir o cadastro sem ligar os caches

No diretório do projeto, este comando só lê os arquivos e mostra a adesão:

```bash
python -m discovery.aws_discovery.accounts
```

Para testar acesso AWS sem criar infraestrutura, use na VM já configurada:

```bash
python -m discovery.aws_discovery.accounts --purpose network --check-access
```

Esse segundo comando faz chamadas AWS de leitura. A conta atual usa o profile
de máquina `redis-network-automation`; a segunda usa esse profile como origem
do AssumeRole. Não é necessário compartilhar chaves administrativas entre contas.

Estados iniciais:

- `admitted`: a configuração foi aceita; acesso ainda não testado.
- `pending_access`: role ausente, trust/permissão incorretos ou identidade indisponível.
- `inventory_ready`: acesso e resolução das regiões funcionaram; não significa coleta validada.
- `partial_inventory`: algumas regiões falharam no ciclo do Pod.
- `pending_network`: cache encontrado, mas provisionamento de rede cross-account não habilitado.

## 3. Ativação no Kubernetes

O novo código e imagem devem estar instalados na VM antes de aplicar o CronJob
atualizado: ele gera `/tmp/accounts.resolved.yaml` com o escopo efetivamente
resolvido e o fornece ao reconciliador. Não use um container antigo com os novos
argumentos. O teste `--help` e a bateria de testes não ligam recursos AWS.

Com o gateway/rede existentes e bootstrap concluído, o ON continua sendo:

```bash
terraform -chdir=terraform/environments/vault-lab apply -var='active=true'
```

Esse comando liga a EC2 configurada e pode gerar cobrança. Nesta etapa o
laboratório permanece OFF. Alterações posteriores no catálogo são publicadas
pelo ciclo regional; no modo Organizations o Pod também resolve a OU a cada
execução, sem editar os IDs nem reaplicar Terraform por nova conta.

## 4. Migrar para inclusão automática por OU

Quando houver Organizations real:

1. Autorize trusted access do StackSets com Organizations. Essa autorização
   organizacional é um pré-requisito administrativo, não é ativada pelo script.
2. Configure o ambiente `terraform/environments/organization-onboarding` com o
   profile da administração, Organization ID, OUs e roles centrais confiáveis.
3. Revise/aplique o módulo: ele prepara uma role de leitura do catálogo e um
   StackSet SERVICE_MANAGED para distribuir a role membro nas OUs selecionadas.
4. Configure `account-access-source` com Organization ID e ARN da role leitora.
5. Troque o conteúdo do catálogo real pelo exemplo
   `account-catalog.organizations.example.yaml`, preenchendo os dados reais.

Depois disso:

```text
Nova conta entra na OU
  → StackSets cria a role membro
  → catálogo encontra a conta ACTIVE
  → AssumeRole funciona
  → discovery consulta suas regiões e caches
```

Se o catálogo encontrar a conta antes de a role estar pronta, ela fica em
`pending_access` e é tentada novamente. O bootstrap não é instantâneo.
O IAM é global: o StackSet instala o papel em uma região, sem duplicá-lo em todas.

**Não gerencie a mesma role simultaneamente pelo bootstrap Terraform individual
e pelo StackSet.** Se ela já foi criada no teste de duas contas, faça uma migração
de propriedade/importação planejada antes de adotar StackSets. Sem isso, a criação
do papel com o mesmo nome falhará. É uma migração inicial, não uma operação por Redis.

## Segurança e isolamento de falhas

- O catálogo valida IDs, roles, Organization ID e OUs explícitas. Paginação e
  OUs descendentes são tratadas; contas suspensas não são admitidas.
- Falha ao consultar Organizations aborta o ciclo, preservando os alvos. Não
  transforma uma falha de autorização em uma organização vazia.
- Falha de conta/região não bloqueia os escopos saudáveis. Somente escopos
  completos podem confirmar exclusões. Alvos de contas indisponíveis são preservados.
- Erros contêm tipo/código, sem copiar corpos de exceção ou credenciais.
- Para evitar reconfigurar a ACL do Vault por conta, `vault_scope: project`
  autoriza somente leitura no prefixo `kv/data/redis-observability/*` à ServiceAccount
  vinculada ao namespace. Não autoriza escrita, listagem, outros prefixos ou root token.
  Isso permite ler credenciais de todas as contas desse projeto; é uma decisão de
  isolamento por projeto, não isolamento Vault por conta. O próximo bootstrap ON
  aplicará essa política; nenhuma ACL do Vault foi alterada com o ambiente OFF.
- Credenciais Redis continuam vindo do Vault. Descobrir uma conta não descobre sua senha.

## Limites importantes

O ingresso e o inventário cross-account estão implementados; a **provisão de
peering cross-account continua bloqueada**. A role membro não tem permissões
de escrita de rede. Para coletar nessa conta, precisa haver conectividade
aprovada e regra de coleta compatível. Não confundir cadastro automático com
monitoramento garantido em qualquer VPC.

O controle de rede mantém o limite atual de oito redes e exige CIDRs compatíveis.
Tags de adesão continuam sendo necessárias para novas alterações de rede.
Uma EC2 central continua sendo ponto único de falha.

O pool do Pod é limitado por `account_workers` (padrão 4). Não foi realizado
teste real de 20 contas, de carga ou do SLA de cinco minutos. Os novos testes
validam a lógica de entrada/isolamento com APIs simuladas, não a capacidade real.

Saída da OU, exclusão de uma entrada ou `enabled: false` não revogam de imediato
ScrapeConfigs/credenciais já publicados: a proteção contra inventário incompleto
preserva alvos. O offboarding requer remoção controlada dos alvos/credenciais e
revogação do papel. O StackSet retém stacks na saída da OU para evitar exclusões
acidentais; a automação completa de offboarding ainda não está implementada.

Arquivos `aws-accounts.yaml` e CronJobs do namespace antigo são legados suspensos;
não são o catálogo do pipeline Vault atual.

Referências oficiais:
[autodeploy StackSets](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/stacksets-orgs-manage-auto-deployment.html),
[trusted access](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/stacksets-orgs-activate-trusted-access.html),
[ListAccountsForParent](https://docs.aws.amazon.com/organizations/latest/APIReference/API_ListAccountsForParent.html).
