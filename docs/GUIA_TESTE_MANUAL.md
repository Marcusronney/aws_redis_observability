# Guia de teste manual — Redis/Valkey AWS no Prometheus local

## 1. O projeto foi finalizado?

**O piloto funcional foi concluído e validado. A homologação de produção ainda
não está concluída.** O código faz descoberta dinâmica; o teste real cobriu uma
conta, uma região e replication groups de um nó. A seleção de múltiplos nós,
AZs, shards, outras contas e regiões está implementada, mas essas combinações
precisam de homologação nas redes correspondentes.

| O que foi feito | Resultado registrado em 23/10/2026 |
|---|---|
| Terraform liga/desliga pipeline e EC2 WireGuard existente | Validado, incluindo iniciar EC2 parada |
| Descoberta sem novo apply de monitoramento por Redis | Validada: 116,75 s após observar disponibilidade |
| Tags de negócio como labels | Atualização de `CostCenter` confirmada |
| Vault → Secret → exporter autenticado | Validado; duas réplicas confirmaram o reload |
| Rotação e senha inválida | Nova senha aceita, antiga rejeitada; senha inválida preservou targets/Secret/estado |
| Remover e recriar cache real | Validado; recriação monitorada em 184,21 s após observar disponibilidade |
| Seleção por nó | Coleta real de um nó em `us-east-1b`, com labels de nó/shard/função/AZ |
| Testes de código | 73 passaram no Windows e no Linux |
| State dos módulos gerenciados | Verificações de ausência das senhas passaram |

O último estado **verificado**, às **10:39 BRT de 23/10/2026**, foi: EC2 parada,
pipeline em OFF, nenhum Job/target do pipeline, PVC preservado e cache temporário
removido. Os quatro caches anteriores continuavam ativos. Este documento não
representa uma nova consulta ao ambiente depois desse checkpoint.

Fontes: [evidências](evidence/acceptance-2026-10-23.json),
[matriz de aceitação](ACEITACAO_PILOTO.md) e [status](STATUS_PROJETO.md).
[Diagrama de ponta a ponta](FLUXO_PONTA_A_PONTA.md).

## 2. Como funciona

O Terraform cria os objetos estáticos no Kind. Um CronJob consulta as APIs AWS
**a cada minuto** — a frequência deixa margem para alcançar a meta de cinco
minutos, incluindo atualização do Secret, reload e scrape. A conta/região, VPC
e tags autorizadas selecionam os recursos; não há lista fixa de endpoints.

Para um Redis autenticado, o controller obtém a senha no Vault usando Kubernetes
auth, valida TLS/AUTH/PING e publica um mapa no Secret `exporter-credentials`.
O volume do Secret chega ao exporter. O sidecar confirma o reload em todos os
Pods autenticados. Somente após validar `redis_up=1` o controller publica o
ScrapeConfig para o Prometheus Operator.

O Prometheus consulta `/scrape` nos exporters. **São os exporters que conectam
ao Redis**, por TLS, através do WireGuard. A senha não fica na URL do target.
Um Redis novo exige rede acessível e credencial já disponível no Vault: a API
ElastiCache não retorna sua senha. Para recursos sem autenticação confirmada,
o fluxo usa o outro Deployment de exporter.

## 3. Onde executar e o que será criado

Execute os blocos Bash **na VM Rocky Linux**, não no PowerShell do Windows.
Use a cópia com o state existente; não recrie outro state para os mesmos objetos.

```bash
cd /root/redis-vault-test-source
source /root/projetos/aws_redis_observability/.venv-aws/bin/activate
export AWS_PAGER=''
export REDIS_TESTE=redis-vault-lifecycle-test
```

| Recurso | Identificação do piloto |
|---|---|
| VM local | `192.168.3.55` |
| Kind/contexto | `redis-monitoring` / `kind-redis-monitoring` |
| Namespace do pipeline | `redis-vault-lab` |
| Prometheus Operator/Prometheus | namespace `monitoring` |
| Conta e região de teste | `762012032320` / `us-east-1` |
| VPC / CIDR | `vpc-086678957273bf941` / `10.80.0.0/16` |
| Subnet group | `redis-observability-subnets` |
| SG do cache | `sg-0a290bca0ecb019e4` |
| EC2 WireGuard existente | `i-0f18f326bfde065db` |
| Cache criado pelo roteiro principal | `redis-vault-lifecycle-test`, Valkey, `cache.t4g.micro`, um nó |

Valkey usa o protocolo Redis e é monitorado pelo mesmo exporter. Este cache de
um nó **não é um teste de alta disponibilidade Multi-AZ**. O helper usa nome
fixo apenas para delimitar o recurso temporário que pode criar/remover; o
discovery não depende desse nome.

Criar o cache e ligar a EC2 gera cobrança durante a execução. Você executará
cada etapa manualmente; não há limpeza automática se simplesmente abandonar
este roteiro. Faça a seção 10 ao terminar. O OFF local sozinho não remove o cache.

## 4. Verificar a VM, o Kind e o login AWS

```bash
kubectl config get-contexts
kubectl config use-context kind-redis-monitoring
kind get clusters
kubectl get nodes -o wide
kubectl -n monitoring get pods
kubectl -n redis-vault-lab get deployments,cronjobs,pvc
terraform version

aws sso login --profile redis-observability-admin --use-device-code
aws sts get-caller-identity --profile redis-observability-admin
```

Espere nós `Ready` e conta `762012032320`. Em OFF, zero réplicas e CronJob
suspenso são esperados; o PVC `vault-data` deve continuar `Bound`.
O SSO administrativo é usado pelo operador para EC2/Terraform. O Pod discovery
usa a identidade de máquina Roles Anywhere, não o login SSO do terminal.

Confira a configuração pública:

```bash
cat terraform/environments/vault-lab/terraform.tfvars
cat discovery/config/vault-local-accounts.yaml
cat discovery/config/vault-local-routing.yaml
```

O `terraform.tfvars` da VM já foi configurado com `gateway_instance_id` e o
Python do virtualenv. Não deve conter senhas. Preserve a mesma configuração
no ON e no OFF. Não exiba os Secrets ou `/root/redis-vault-session/vault-init.json`.

## 5. Ligar e verificar os Pods

```bash
terraform -chdir=terraform/environments/vault-lab init
terraform -chdir=terraform/environments/vault-lab apply -var='active=true'

kubectl -n redis-vault-lab get deployments,pods,services,pvc
kubectl -n redis-vault-lab get cronjobs,jobs
kubectl -n redis-vault-lab rollout status deployment/vault --timeout=180s
kubectl -n redis-vault-lab rollout status deployment/exporter-auth --timeout=180s
kubectl -n redis-vault-lab rollout status deployment/exporter-noauth --timeout=180s
```

Espere `RUNTIME_ON_OK`, Vault `1/1`, Pod autenticado `2/2` (exporter + sidecar)
e Pod sem autenticação `1/1`. As quantidades de Pods dependem de
`exporter_replicas`; o padrão é um por Deployment. Os Jobs discovery aparecem
periodicamente e terminam `Complete` quando o ciclo passa.

No início, algum Job pode falhar enquanto EC2/Vault iniciam. Verifique os
ciclos seguintes; falhas persistentes precisam de diagnóstico.

Para acompanhar, opcionalmente, use outro terminal:

```bash
kubectl -n redis-vault-lab get pods,jobs --watch
```

`Ctrl+C` encerra a observação; não para o cluster.

## 6. Verificar rede e AWS antes de criar o cache

```bash
aws ec2 describe-instances --profile redis-observability-admin \
  --region us-east-1 --instance-ids i-0f18f326bfde065db \
  --query 'Reservations[].Instances[].{Id:InstanceId,State:State.Name,IP:PublicIpAddress}'

wg show wg0 latest-handshakes
ip route get 10.80.20.220

aws elasticache describe-cache-subnet-groups \
  --profile redis-observability-admin --region us-east-1 \
  --cache-subnet-group-name redis-observability-subnets \
  --query 'CacheSubnetGroups[].{Name:CacheSubnetGroupName,Vpc:VpcId,Subnets:Subnets[].SubnetIdentifier}'

aws ec2 describe-security-groups --profile redis-observability-admin \
  --region us-east-1 --group-ids sg-0a290bca0ecb019e4 \
  --query 'SecurityGroups[].{Name:GroupName,Ingress:IpPermissions}'
```

Espere EC2 `running`, handshake recente e rota privada via `wg0`.
`10.80.20.220` foi o IP observado do cache existente; não é um endpoint fixo
para cadastrar nem uma garantia de que o IP não mudará. Os endpoints são
resolvidos pelo discovery. Security groups e retorno de rede precisam permitir
a origem efetivamente vista na VPC, considerando o encaminhamento/NAT existente.
Não abra a porta Redis para a Internet para resolver um problema de rota.

Valide primeiro o Redis existente:

```bash
python scripts/verify_local_pipeline.py \
  --redis-name redis-observability-lab --nodes \
  --output /tmp/redis-existente-verificado.json
```

Espere `LOCAL_PIPELINE_OK`. Esse verificador só lê o ambiente; não cria Redis.

## 7. Criar manualmente o cache de teste pelo caminho validado

```bash
terraform -chdir=terraform/environments/vault-test-cache init
python scripts/vault_cache_test.py on
```

O helper lê ou cria a credencial no Vault e chama o Terraform do **cache
temporário**, com entrada efêmera/write-only. Ele não cadastra target, endpoint
ou ScrapeConfig. Não copie a senha para `tfvars`, argumentos ou histórico.
O provisionamento AWS pode levar vários minutos; o comando aguarda o provider.

Confira o recurso e suas tags:

```bash
aws elasticache describe-replication-groups \
  --profile redis-observability-admin --region us-east-1 \
  --replication-group-id "$REDIS_TESTE" \
  --query 'ReplicationGroups[].{Name:ReplicationGroupId,Status:Status,TLS:TransitEncryptionEnabled,Auth:AuthTokenEnabled,Members:MemberClusters,Nodes:NodeGroups[].NodeGroupMembers}'

CACHE_ARN=$(aws elasticache describe-replication-groups \
  --profile redis-observability-admin --region us-east-1 \
  --replication-group-id "$REDIS_TESTE" \
  --query 'ReplicationGroups[0].ARN' --output text)

aws elasticache list-tags-for-resource \
  --profile redis-observability-admin --region us-east-1 \
  --resource-name "$CACHE_ARN"
```

Espere `available`, TLS/AUTH habilitados e tag
`RedisObservability=vault-lab`. Essa tag e a VPC autorizam o teste na política
atual. O código não toma o nome do fixture como uma lista de Redis a monitorar.

**Não rode novamente o Terraform de monitoramento após criar o cache.**
Observe o discovery normal:

```bash
kubectl -n redis-vault-lab get jobs
kubectl -n redis-vault-lab get scrapeconfigs
kubectl -n redis-vault-lab get scrapeconfigs -o yaml
```

Escolha um Job recente na primeira saída e consulte seus logs, substituindo
`NOME_DO_JOB` pelo nome real:

```bash
kubectl -n redis-vault-lab logs job/NOME_DO_JOB --tail=80
```

Espere fases de discovery, leitura do Vault, validação Redis, reload e criação
de `auto-aws-...`. Não espere o nome do cache no nome do ScrapeConfig: ele é
derivado de um hash da identidade. O YAML mostra a identidade na annotation,
as labels e o target TLS sem senha. Para inspecionar apenas metadados do Secret:

```bash
kubectl -n redis-vault-lab describe secret exporter-credentials
```

Não use `get secret -o yaml` para compartilhar evidências.

## 8. Confirmar no Prometheus, no terminal e no navegador

```bash
python scripts/verify_local_pipeline.py \
  --redis-name "$REDIS_TESTE" --nodes \
  --output /tmp/redis-manual-verificado.json
```

Espere `LOCAL_PIPELINE_OK`. O resultado exige métricas recentes com
`redis_up=1` e confirmação do Secret em todos os Pods autenticados. O intervalo
de cinco minutos pressupõe cache disponível, rede e credencial corretos; não
inclui o tempo de criação da AWS nem garante prazo durante uma indisponibilidade.

Para abrir a interface, deixe este comando rodando **num terminal da VM**:

```bash
kubectl -n monitoring port-forward \
  svc/monitoring-kube-prometheus-prometheus 19090:9090 --address=127.0.0.1
```

No seu **computador**, em outro terminal, crie o túnel até a VM:

```bash
ssh -N -L 19090:127.0.0.1:19090 root@192.168.3.55
```

Abra `http://127.0.0.1:19090` no navegador do computador. Se a porta já estiver
ocupada, escolha outra porta local no primeiro número de `-L` e na URL.
O SSH acima é computador → VM, porta 22 usada no piloto. O SSH VM → EC2 na
porta 2222, mencionado na operação anterior, é administrativo e não transporta
as métricas Redis.

Na página de consultas, execute:

```promql
redis_up{redis_name="redis-vault-lifecycle-test"}
```

Resultado esperado: `1`, com `aws_account_id`, `aws_region`, `redis_node`,
`redis_shard`, `redis_role`, `availability_zone`, `project`, `environment` e
`cost_center`. Para conferir atualidade:

```promql
time() - timestamp(redis_up{redis_name="redis-vault-lifecycle-test"})
```

Espere amostras recentes, normalmente abaixo de 90 segundos. Na página
**Status → Targets**, busque pelo nome do cache nos labels/URL; a saúde deve
estar `up`. O `up=1` do scrape HTTP sozinho não comprova autenticação Redis:
confirme também `redis_up=1`.

## 9. Testar atualização e, opcionalmente, rotação

Para alterar a tag de negócio do fixture:

```bash
python scripts/vault_cache_test.py tags
```

Após o próximo ciclo completo, consulte:

```promql
redis_up{redis_name="redis-vault-lifecycle-test",cost_center="vault-test-after"}
```

O helper muda a tag do **recurso AWS**; não aplica Terraform de monitoramento.
Os comandos opcionais abaixo alteram somente a senha do cache temporário:

```bash
python scripts/vault_cache_test.py rotate
python scripts/vault_cache_test.py finalize-rotation
```

Espere `ROTATION_PUBLISHED_OK` e `ROTATION_FINALIZED_OK`. A etapa final só
revoga a antiga depois de sincronizar e verificar a nova credencial. O teste
opcional `python scripts/vault_cache_test.py wrong-password` injeta uma senha
inválida no Vault, verifica a preservação do estado e restaura a válida.
Não use esses helpers para alterar os caches anteriores.

## 10. Remover o cache, observar a retirada e desligar

Primeiro remova o recurso AWS **com o pipeline ainda ligado**:

```bash
python scripts/vault_cache_test.py off

aws elasticache describe-replication-groups \
  --profile redis-observability-admin --region us-east-1 \
  --query "ReplicationGroups[?ReplicationGroupId=='redis-vault-lifecycle-test'].{Name:ReplicationGroupId,Status:Status}"
```

Espere `[]`. Aguarde os ciclos normais: a remoção precisa de dois inventários
completos separados por pelo menos 60 segundos; a entrada do Secret tem uma
retenção adicional de 90 segundos após a retirada do target. Durante estados
AWS transitórios, o controller pode preservar os targets até obter inventário
completo. Não confunda isso com erro de exclusão.

```bash
kubectl -n redis-vault-lab get scrapeconfigs -o yaml
```

O ScrapeConfig desse cache deve desaparecer e ele deve sair de Targets.
Dados históricos continuam no Prometheus até sua retenção expirar; apagar um
Redis não apaga o histórico. A query instantânea deixa de retornar a série
depois da propagação/staleness. O verificador de presença da seção 8 não é
um verificador de exclusão: após remover, sua falha é esperada.

Encerre os port-forwards com `Ctrl+C` nos respectivos terminais. Depois:

```bash
terraform -chdir=terraform/environments/vault-lab apply -var='active=false'

kubectl -n redis-vault-lab get deployments,cronjobs,jobs,scrapeconfigs,pvc

aws ec2 describe-instances --profile redis-observability-admin \
  --region us-east-1 --instance-ids i-0f18f326bfde065db \
  --query 'Reservations[].Instances[].State.Name'

terraform -chdir=terraform/environments/vault-test-cache state list
terraform -chdir=terraform/environments/vault-lab plan -var='active=false'
```

Espere Deployments `0/0`, CronJob suspenso, nenhum Job/target do pipeline,
PVC `Bound`, EC2 `stopped`, state do cache sem recursos e plano local sem
alterações. Não use `terraform destroy` no módulo local para pausar; preserve
Kind, PVC, states e `/root/redis-vault-session`.

**Os quatro caches anteriores e outros recursos AWS preservados não são
removidos por estes comandos.** Seus custos exigem o
[plano separado](PLANO_DESLIGAMENTO_ANTERIORES.md). EBS/IPs preservados também
não são excluídos ao parar a EC2.

## 11. Se quiser criar diretamente pelo Console AWS

Este caminho é alternativo ao fixture e não foi o procedimento de criação usado
na aceitação. Use outro nome, por exemplo `redis-manual-console`; não crie pelo
Console o mesmo nome que o Terraform do teste administra.

No ElastiCache em `us-east-1`, crie um cache **com nós**, Valkey ou Redis OSS,
com cluster mode desativado, um nó para o primeiro teste, na VPC/subnet group/SG
da seção 3. Habilite criptografia em trânsito e em repouso e autenticação por
AUTH token. Use uma senha própria sem a colocar em comandos/arquivos do projeto.
Adicione a tag `RedisObservability=vault-lab` e as tags de negócio que desejar.
Não selecione autenticação IAM para este roteiro.

O uso de AUTH exige TLS no ElastiCache.
[Documentação AWS sobre AUTH](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/auth.html).

Com o pipeline ligado, registre **a mesma senha escolhida no Console** no Vault.
O bloco abaixo solicita a senha sem eco e cria apenas uma entrada ainda ausente;
não imprime a credencial e não modifica a AWS:

```bash
python - <<'PY'
from getpass import getpass
from pathlib import Path
from scripts.vault_cache_test import admin
from discovery.aws_discovery.vault import credential_path

target = dict(account_id='762012032320', region='us-east-1',
              aws_resource_type='replication_group',
              replication_group_id='redis-manual-console')
path = credential_path(target)
with admin(Path('/root/redis-vault-session')) as request:
    try:
        request('GET', path)
    except RuntimeError as exc:
        if str(exc) != 'Vault HTTP 404':
            raise
    else:
        raise SystemExit('Entrada já existe; use um procedimento de rotação.')
    password = getpass('Senha AUTH escolhida no Console: ')
    if not password:
        raise SystemExit('Senha vazia não permitida.')
    request('POST', path, {'options': {'cas': 0},
                         'data': {'username': 'default', 'password': password}})
print('Credencial registrada no Vault, sem exibir o valor.')
PY
```

Se mudar o nome no Console, ajuste `replication_group_id` no bloco. Em seguida,
aguarde `available` e execute a seção 8 com `--redis-name redis-manual-console`.
Não é necessário cadastrar endpoint ou aplicar Terraform de monitoramento.

**Limpeza desse caminho:** exclua no Console somente `redis-manual-console`,
aguarde a retirada automática do target e execute o OFF local da seção 10.
`vault_cache_test.py off` não remove um cache criado no Console com outro nome.
A entrada no Vault é preservada para permitir recriação e não representa um
cache AWS ativo. Este caminho fica fora do state Terraform do fixture.

## 12. Diagnóstico rápido

| Sintoma | Verificar |
|---|---|
| SSO expirou | Renovar `aws sso login`; confirmar a conta antes de criar/excluir |
| APIs AWS funcionam, Redis expira | EC2 `running`, handshake WireGuard, rotas, DNS e SG; são caminhos diferentes |
| Pod pendente/erro de mount | `kubectl -n redis-vault-lab describe pod NOME`; eventos e existência dos Secrets/PVC |
| Vault selado após reinício inesperado | Reexecutar apply ON com `-replace=terraform_data.runtime`, como no guia Terraform |
| Redis disponível, nenhum target | Conta/região, VPC, tag permitida, Vault path, TLS e logs do Job recente |
| `CredentialError` | Vault disponível, Kubernetes auth/role e credencial no caminho correto; não divulgue o Secret |
| Senha inválida | Corrigir a origem no Vault; não editar o Secret gerado manualmente |
| `redis_up=0` ou HTTP up sem dados Redis | TLS, AUTH e conectividade a partir dos Pods, não apenas do host |
| Label antiga | Esperar ciclo completo; verificar tags AWS e novos timestamps, não só gráficos históricos |
| Teste não deve usar outra região ainda | A rota permanente para São Paulo não faz parte do piloto entregue |

Para produção, consulte [OPERACAO_E_PRODUCAO.md](OPERACAO_E_PRODUCAO.md):
Vault/armazenamento/rede resilientes, identidade de máquina, segurança dos Secrets
e homologação dos escopos adicionais. Nenhum desses passos exige dois ambientes
separados para executar este teste local.
