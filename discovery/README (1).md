# AWS Redis/Valkey Observability — Plano único de conclusão

**Data-base: 21/10/2026.** Arquitetura existente: Rocky Linux 192.168.3.55 → Kind `redis-monitoring` → CronJob `redis-discovery-aws` a cada 5 min. Virgínia (`redis-observability-lab`) mantém coleta local; São Paulo (`redis-observability-sa-test`) já tem ECS Service `redis-observability-sa-test` e envio de `redis_up=1` para AMP `ws-a32bf55d-d10a-4d22-9873-a937f133db67` em us-east-1. **Não recriar esses recursos.**

> Este kit é um complemento ao repositório existente, não um repositório substituto. O `ecs_controller.py` opera em **DRY_RUN** por padrão. `--apply` só age sobre Redis e VPCs com tag `RedisObservability=enabled` e, em novos Redis, `RedisObservabilityAuth=noauth` ou `secret`. O piloto existente é reconhecido como legado, sem mudanças. O código foi compilado e testado localmente com testes unitários limitados, **não** executado contra a AWS nesta sessão. Homologue com um recurso de teste antes de habilitar em CronJob.

## 0. Escopo e decisões irreversíveis

- Não transformar a VM Rocky em túnel VPN universal: Fargate fica na VPC do Redis e manda HTTPS assinado ao AMP central.
- Uma tarefa por Redis opt-in na primeira versão. Dimensionar custo de Fargate, saída de rede e ingestão AMP antes de ampliar. Não há remoção automática.
- Nunca assumir que Serverless sem User Group significa ausência de senha. A tag `RedisObservabilityAuth=noauth` é declaração operacional explícita e deve ser aplicada só depois de validar o acesso.
- Redis com senha utiliza Secrets Manager **na mesma conta e região**; `REDIS_PASSWORD` vem da opção `secrets` do ECS, não de JSON ou repositório.
- Para controle inicial: `--max-changes 1`. A execução periódica poderá provisionar um novo coletor por ciclo, com limites de tag/autorização.
- Regiões fixas são suportadas imediatamente. `regions: auto` é opcional, exige permissões `ec2:DescribeRegions` e varredura de todas as regiões habilitadas.
- Em contas adicionais, configurar IAM/AssumeRole/AMP resource policy **uma vez por conta**, pois não é possível conceder acesso cross-account sem confiança explícita.

## 1. Copiar arquivos e versionar de forma recuperável

Baixar `aws_redis_observability_completion.zip` e copiar para o Rocky Linux (ex.: `scp` ou cliente SFTP), como `/root/aws_redis_observability_completion.zip`.

```bash
cd /root/projetos/aws_redis_observability
source .venv-aws/bin/activate
export AWS_PROFILE=redis-observability-admin
export AWS_DEFAULT_REGION=sa-east-1

git status -sb
git switch -c feature/ecs-autoprovision-aws
mkdir -p /tmp/redis-completion
unzip -oq /root/aws_redis_observability_completion.zip -d /tmp/redis-completion

# Backups locais, antes da troca do AWS client existente
mkdir -p /root/redis-observability-backup
cp -a discovery/aws_discovery/client.py /root/redis-observability-backup/client.py
cp -a kubernetes/aws-discovery/cronjob.yaml /root/redis-observability-backup/cronjob.yaml

cp -v /tmp/redis-completion/discovery/aws_discovery/{client.py,ecs_controller.py,filter_local_targets.py} discovery/aws_discovery/
cp -v /tmp/redis-completion/discovery/tests/test_{ecs_controller,filter_local_targets}.py discovery/tests/
mkdir -p scripts
cp -v /tmp/redis-completion/scripts/*.py scripts/
python -m compileall -q discovery/aws_discovery
python -m unittest discover -s discovery/tests -p 'test_*.py' -v
```

**Esperado:** testes anteriores continuam passando e 7 testes adicionais passam. O nome do branch evita alterar `main` antes da homologação. Se o checkout falhar por diferenças locais, use `git diff`/`git stash` com cuidado; não descarte código.

## 2. Obter Task Definition validada como template

Se o arquivo da etapa anterior ainda existir:

```bash
cp /tmp/redis-observability-ecs/task-definition.json discovery/config/ecs-task-template.json
```

Se não existir, recupere **somente a configuração registrável**, sem ARN da revisão e metadados da AWS:

```bash
aws ecs describe-task-definition --region sa-east-1 \
  --task-definition redis-observability-sa-pilot:1 \
  --query taskDefinition --output json > /tmp/task-sao-paulo.json
python - <<'PY'
import json
from pathlib import Path
raw=json.load(open('/tmp/task-sao-paulo.json'))
fields=('family','taskRoleArn','executionRoleArn','networkMode','containerDefinitions',
        'volumes','placementConstraints','requiresCompatibilities','cpu','memory',
        'ephemeralStorage','runtimePlatform','pidMode','ipcMode','proxyConfiguration')
clean={k:raw[k] for k in fields if k in raw}
Path('discovery/config/ecs-task-template.json').write_text(json.dumps(clean,indent=2))
PY
```

Confira que a template contém duas imagens validadas, `AOT_CONFIG_CONTENT`, log group, roles e AMP central. **Nunca inserir senha em claro.**

## 3. Configurar políticas de coleta e inventário

Manter `discovery/config/collection-routing.yaml` atual:

```yaml
version: 1
allowed_accounts:
  - "762012032320"
default_mode: distributed
local_targets:
  - "762012032320:us-east-1:replication_group:redis-observability-lab"
blocked_targets: []
```

Manter inicialmente `discovery/config/aws-accounts.yaml` com a conta e duas regiões já validadas:

```yaml
accounts:
  - name: estudos
    account_id: "762012032320"
    profile: redis-discovery
    regions:
      - us-east-1
      - sa-east-1
    # preencher antes de habilitar --apply no CronJob:
    # provision_role_arn: arn:aws:iam::762012032320:role/RedisObservabilityProvisionRole

tag_labels:
  Project: project
  Environment: environment
  CostCenter: cost_center
  project: project
  environment: environment
  cost_center: cost_center
  business_unit: business_unit
  owner: owner
```

Se já existirem mais campos/tags no arquivo real, **não os remover**: o exemplo é apenas a estrutura mínima.

Para monitorar novas regiões automaticamente depois da homologação:

```bash
python scripts/patch_auto_regions.py --file discovery/aws_discovery/__main__.py
# Em aws-accounts.yaml trocar somente a lista de regions pela string: regions: auto
```

A descoberta automática examina todas as regiões habilitadas e com endpoint ElastiCache reconhecido pelo SDK. Consulte cotas e privilégios; pode ser preferível deixar a lista explícita.

## 4. Simulação end-to-end ainda SEM escrita

```bash
export REDIS_AWS_PROFILE=redis-observability-admin
python -m discovery.aws_discovery > /tmp/redis-inventory-final.json
python -m discovery.aws_discovery.targets \
  --input /tmp/redis-inventory-final.json > /tmp/redis-targets-final.json
python -m discovery.aws_discovery.ecs_controller \
  --inventory /tmp/redis-inventory-final.json \
  --profile redis-observability-admin \
  --output /tmp/redis-ecs-controller-dry-run.json
python -m discovery.aws_discovery.filter_local_targets \
  --input /tmp/redis-targets-final.json \
  --output /tmp/redis-local-targets.json
python -m discovery.aws_discovery.collection_policy \
  --input /tmp/redis-distributed-plan-v3.json \
  --output /tmp/redis-collection-plan-final.json
```

Esperado: `KEEP_LOCAL` para Virgínia, `EXISTING_LEGACY_SERVICE` para São Paulo e `LOCAL_TARGET_COUNT: 1`. Nenhum `CREATE` para os recursos existentes. Também confira que `filter_local_targets` não retornou um target novo por engano.

> Se `--profile redis-observability-admin` não funcionar (SSO expirado), `aws sso login --profile redis-observability-admin --use-device-code`.

## 5. Bootstrap de IAM: uma vez por conta

**Apenas administradores** executam esta fase. A role do Discovery via Roles Anywhere já existe na conta principal; **não recriá-la**.

### 5.1 Gerar políticas para análise

```bash
python scripts/bootstrap_policies.py \
  --account 762012032320 \
  --source-role-arn arn:aws:iam::762012032320:role/RedisObservabilityDiscoveryRole \
  --output /tmp/redis-iam-bootstrap
ls -l /tmp/redis-iam-bootstrap
```

Os documentos gerados são um **baseline de permissões**, não uma política final de produção. Revisar com IAM Access Analyzer e restringir regiões/VPCs/recursos. A lógica do controller também exige opt-in duplo de VPC e Redis, mas **tags não substituem limites IAM**.

### 5.2 Criar a role de provisionamento separada

Na conta principal:

```bash
aws iam get-role --role-name RedisObservabilityProvisionRole >/dev/null 2>&1 || \
aws iam create-role \
  --role-name RedisObservabilityProvisionRole \
  --assume-role-policy-document file:///tmp/redis-iam-bootstrap/provision-trust.json
aws iam put-role-policy \
  --role-name RedisObservabilityProvisionRole \
  --policy-name RedisObservabilityScopedProvision \
  --policy-document file:///tmp/redis-iam-bootstrap/provision-policy.json
```

O controller executa consultas ECS/EC2/Secrets Manager **antes** de assumir a role de provisionamento. Conceder permissões de leitura controladas à role `RedisObservabilityDiscoveryRole`:

```bash
aws iam put-role-policy \
  --role-name RedisObservabilityDiscoveryRole \
  --policy-name RedisObservabilityControllerRead \
  --policy-document file:///tmp/redis-iam-bootstrap/controller-read-policy.json
```

Conceder também permissão de assumir somente a role de escrita:

```bash
aws iam put-role-policy \
  --role-name RedisObservabilityDiscoveryRole \
  --policy-name RedisObservabilityAssumeProvision \
  --policy-document file:///tmp/redis-iam-bootstrap/source-assume-provision-policy.json
```

Adicionar no `aws-accounts.yaml` da conta:

```yaml
provision_role_arn: arn:aws:iam::762012032320:role/RedisObservabilityProvisionRole
```

Antes de rodar pelo CronJob, testar de dentro do pod/Job se a role do Roles Anywhere consegue executar `sts:AssumeRole`. Confirmar a session policy da Roles Anywhere e eventuais permission boundaries/SCP.

### 5.3 Execução e segredo

As roles `redis-observability-ecs-execution` e `redis-observability-ecs-task` já existem na conta principal. Na conta nova, criá-las com `ecs-task-trust.json`. Associar à execution role a AWS-managed `AmazonECSTaskExecutionRolePolicy`; à task role, `task-amp-write-policy.json`.

```bash
# Somente numa conta nova ou se estiver ausente:
aws iam create-role --role-name redis-observability-ecs-execution \
  --assume-role-policy-document file:///tmp/redis-iam-bootstrap/ecs-task-trust.json
aws iam attach-role-policy --role-name redis-observability-ecs-execution \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam create-role --role-name redis-observability-ecs-task \
  --assume-role-policy-document file:///tmp/redis-iam-bootstrap/ecs-task-trust.json
aws iam put-role-policy --role-name redis-observability-ecs-task \
  --policy-name RedisObservabilityAMPWrite \
  --policy-document file:///tmp/redis-iam-bootstrap/task-amp-write-policy.json
```

**Quando houver Redis com autenticação por senha**, adicionar à **execution role** a política `execution-role-secrets-policy.json` (secret prefix `redis-observability/`):

```bash
aws iam put-role-policy \
  --role-name redis-observability-ecs-execution \
  --policy-name RedisObservabilityReadSecrets \
  --policy-document file:///tmp/redis-iam-bootstrap/execution-role-secrets-policy.json
```

Se o Secret usar chave KMS personalizada, adicionar `kms:Decrypt` restrito àquela chave, se necessário. A Secrets Manager `GetSecretValue` é usada pela execution role, não pela task role. Verificar política do segredo também. <https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html>

### 5.4 Outras contas AWS

Cada conta nova requer uma bootstrap explícita, não totalmente delegável sem confiança prévia:

1. Administrador da **conta destino** cria a read role `RedisObservabilityTargetReadRole` confiando na **role discovery central**, anexa `controller-read-policy.json` e permite `sts:AssumeRole` apenas para `RedisObservabilityProvisionRole` local.
2. A role read assume a `RedisObservabilityProvisionRole` local; gerar `provision-trust.json` usando **source-role-arn = ARN da read role da conta destino**.
3. No IAM da conta central, conceder à `RedisObservabilityDiscoveryRole` `sts:AssumeRole` para a ARN da read role destino; conferir session policy do Roles Anywhere.
4. Na conta destino criar execution role e task role conforme acima, com `aps:RemoteWrite` no **workspace central**.
5. No AMP central, incorporar a task role da conta destino à **resource policy existente** do workspace com `aps:RemoteWrite` (não sobrescrever a política sem ler sua revisão). Exemplo CLI: `aws amp get-resource-policy --workspace-id ... --region us-east-1` e `aws amp put-resource-policy --workspace-id ... --policy-document file://... --revision-id ... --region us-east-1`. Configurar KMS policy caso workspace use CMK.
6. Adicionar a conta à allowlist `collection-routing.yaml` e configurar em `aws-accounts.yaml` o `role_arn` da read role, `provision_role_arn` da write role, e as regiões.

## 6. Habilitar UM Redis novo como piloto de provisionamento

**Não usar o Redis São Paulo atual**; ele já está operando e não será migrado por este kit. Criar um Redis **de teste**, em VPC autorizada, preferencialmente usando região já testada. Confirmar endpoint, TLS, capacidade, custo e conectividade antes de aplicar tags.

A autorização usa duas tags explícitas:

- VPC: `RedisObservability=enabled`
- Redis: `RedisObservability=enabled` e `RedisObservabilityAuth=noauth` **ou** `RedisObservabilityAuth=secret`.

Se tiver senha/RBAC:

- Criar Secret Manager **na região e conta do Redis**, com nome que comece por `redis-observability/` e valor **plaintext da senha** (não JSON; a variável ECS `REDIS_PASSWORD` espera a senha diretamente).
- Adicionar ao Redis `RedisObservabilitySecretArn=<ARN real do secret>` e, se necessário, `RedisObservabilityUser=<ACL user>`.
- Validar `elasticache:UserGroupId`, `AuthTokenEnabled` e a política do Secret.

Exemplo CLI com placeholders, após conferir a conta/região:

```bash
# Substituir apenas valores realmente obtidos por aws ec2 describe-vpcs e
# aws elasticache describe-serverless-caches --serverless-cache-name ...
export REGION=sa-east-1
export VPC_ID='vpc-SEU-LAB'
export REDIS_ARN='arn:aws:elasticache:sa-east-1:762012032320:serverlesscache:SEU-REDIS'

aws ec2 create-tags --region "$REGION" --resources "$VPC_ID" \
  --tags Key=RedisObservability,Value=enabled
aws elasticache add-tags-to-resource --region "$REGION" \
  --resource-name "$REDIS_ARN" \
  --tags Key=RedisObservability,Value=enabled Key=RedisObservabilityAuth,Value=noauth
```

**Noauth acima é somente exemplo.** Se Redis exigir autenticação, usar `secret` e configurar Secret. Evitar tag opt-in em VPC de produção sem autorização formal; a role deve ter IAM restrito.

## 7. Simular e executar a primeira criação

```bash
python -m discovery.aws_discovery > /tmp/inventory-before-create.json
python -m discovery.aws_discovery.ecs_controller \
  --inventory /tmp/inventory-before-create.json \
  --profile redis-observability-admin \
  --output /tmp/ecs-create-preview.json
```

Esperado: Virgínia `KEEP_LOCAL`, São Paulo `EXISTING_LEGACY_SERVICE`, novo Redis `CREATE_CANDIDATE`. O dry-run **não** cria SG, CloudWatch nem ECS.

Para criação controlada no laboratório, usando seu perfil SSO admin, faça apenas após revisar o JSON:

```bash
python -m discovery.aws_discovery.ecs_controller \
  --inventory /tmp/inventory-before-create.json \
  --profile redis-observability-admin \
  --apply --confirm-account 762012032320 --max-changes 1 \
  --allow-direct-admin \
  --output /tmp/ecs-create-result.json
```

**Nunca usar `--allow-direct-admin` no CronJob.** Na operação automática, o controller deve assumir `RedisObservabilityProvisionRole`, e não usar Admin SSO.

O código criará ou reutilizará cluster, log group, SG, regra de ingresso TCP na SG do Redis, Task Definition e ECS Service. A operação é idempotente por nomes determinísticos. Se houver falha parcial, revisar AWS antes de repetir. Não há remoção automática.

### Validação de ECS

```bash
aws ecs list-services --region sa-east-1 --cluster redis-observability-collectors
aws ecs list-tasks --region sa-east-1 --cluster redis-observability-collectors
aws logs tail /ecs/redis-observability-collectors --region sa-east-1 --since 20m
```

Extraia `family` e `region` de `/tmp/ecs-create-result.json`; consulte o serviço específico e rode `aws ecs wait services-stable --cluster ... --services <family> --region ...`. Conferir `desiredCount=1`, `runningCount=1`, `pendingCount=0`.

### Validação do AMP

```bash
AMP_URL='https://aps-workspaces.us-east-1.amazonaws.com/workspaces/ws-a32bf55d-d10a-4d22-9873-a937f133db67/api/v1/query'
# Substitua NOVO_REDIS exatamente como aparece em redis_name
awscurl --profile redis-observability-admin --region us-east-1 --service aps \
  -X POST -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'query=redis_up{redis_name="NOVO_REDIS"}' "$AMP_URL"
```

Exigir `redis_up=1` do Redis recém-criado e `up=1` do seu exporter. HTTP `403` sugere policy SigV4/AMP, `redis_up=0` sugere ACL/TLS/SG, ausência de séries sugere falha ADOT/scrape/ingestão.

**Reexecutar o controller em modo dry-run e exigir `UNCHANGED` no novo Redis**. Isso comprova idempotência.

## 8. Publicar a imagem v5 e migrar o CronJob existente

O patch preserva ServiceAccount, ConfigMap e secret de Roles Anywhere no manifesto original, e apenas substitui a imagem e a sequência de comandos. Ele também altera o reconciliador Kubernetes para receber só targets locais.

```bash
# Inicialmente apenas dry-run para ECS
python scripts/patch_cronjob.py --file kubernetes/aws-discovery/cronjob.yaml --mode dry-run
kubectl apply --dry-run=client -f kubernetes/aws-discovery/cronjob.yaml

# Localizar o Dockerfile original para usar o contexto correto:
find . -maxdepth 4 -name Dockerfile -print
```

**Não presumir Dockerfile**: definir `DOCKERFILE` e `BUILD_CONTEXT` conforme repositório existente. Verifique que a imagem copia também `discovery/config/ecs-task-template.json`, `collection-routing.yaml` e o novo módulo Python.

```bash
# EXEMPLO: ajustar o caminho para o Dockerfile do projeto
export DOCKERFILE='./Dockerfile'
export BUILD_CONTEXT='.'
docker build -f "$DOCKERFILE" -t redis-discovery-aws:local-v5 "$BUILD_CONTEXT"

docker run --rm redis-discovery-aws:local-v5 \
  python -m discovery.aws_discovery.ecs_controller --help
kind load docker-image redis-discovery-aws:local-v5 --name redis-monitoring
```

**Atenção:** o script de patch reserializa YAML sem os comentários originais, mas mantém os objetos. Existe backup `.pre-v5.bak`. Verificar o diff antes do apply.

Suspender agendamento apenas durante a troca, garantindo que um Job não esteja em andamento:

```bash
kubectl patch cronjob redis-discovery-aws -n redis-observability \
  --type merge -p '{"spec":{"suspend":true}}'
kubectl apply -f kubernetes/aws-discovery/cronjob.yaml
kubectl create job --from=cronjob/redis-discovery-aws \
  redis-discovery-v5-check-$(date +%s) -n redis-observability
kubectl get jobs -n redis-observability --sort-by=.metadata.creationTimestamp | tail -7
# Ler logs do Job de teste identificado acima:
kubectl logs -n redis-observability job/NOME_REAL_DO_JOB
```

Esperado: sem erro de Roles Anywhere, `KEEP_LOCAL`/`EXISTING_LEGACY_SERVICE`, `LOCAL_TARGET_COUNT: 1`, ScrapeConfig Virgínia `UNCHANGED`. **O antigo ScrapeConfig São Paulo permanecerá STALE**, pois o reconciliador Kubernetes não remove recursos automaticamente.

Quando o dry-run em Kubernetes passar e a role `RedisObservabilityProvisionRole` tiver sido validada, alterar somente o pipeline:

```bash
python scripts/patch_cronjob.py --file kubernetes/aws-discovery/cronjob.yaml --mode apply
kubectl apply -f kubernetes/aws-discovery/cronjob.yaml
# Pode iniciar 1 Job manual de homologacao; depois:
kubectl patch cronjob redis-discovery-aws -n redis-observability \
  --type merge -p '{"spec":{"suspend":false}}'
```

Se aplicou arquivo que contém `suspend: false`, a suspensão anterior pode ter sido anulada. **Conferir `kubectl get cronjob ...` e evitar Jobs sobrepostos**. O CronJob continua **um só**, com `concurrencyPolicy: Forbid` e `--max-changes 1`.

## 9. Limpar ScrapeConfig obsoleto de São Paulo, com segurança

**Somente** após reconfirmar `redis_up=1` recente no AMP para a tarefa ECS gerenciada e que o novo CronJob contém o filtro de targets locais:

```bash
kubectl get scrapeconfig auto-aws-f6372ce604ed -n redis-observability --show-labels
kubectl delete scrapeconfig auto-aws-f6372ce604ed -n redis-observability
```

Não remover `auto-aws-0fea88c6b524` (Virgínia). Confirmar após duas execuções do CronJob que o ScrapeConfig de São Paulo não reapareceu. **Se a coleta distribuída falhar, não excluir alvos locais até resolver**.

## 10. Alertas e monitoramento sem Grafana

O **AMP** mantém as séries dos Redis distribuídos; o **Prometheus do Kind** guarda as séries locais. `redis_up=1` no AMP não aparece automaticamente no Prometheus Kind. Escolher explicitamente:

- manter consultas separadas de AMP (awscurl ou API SigV4) e Prometheus local; ou
- adicionar um remote write autenticado com SigV4 do Prometheus local para AMP, com controle de rótulos para evitar duplicidade, e então consultar tudo pelo AMP.

Sem Grafana, usar PromQL/AMP HTTP API e regras gerenciadas/Alertmanager com SNS (avaliar permissões e regras de alerta). Alertas mínimos: `redis_up == 0`, `absent(redis_up{redis_name="..."})`, ECS desired/running divergente, falha de Job de discovery, ADOT Remote Write com falha; acrescentar severidade e roteamento. **`up` sozinho não confirma Redis**, só o exporter.

## 11. Multiaccount e descomissionamento

- Para cada conta: criar roles de leitura/escrita/execução/task, configurar trust, secrets e resource policy AMP; registrar account e habilitar regiões (`auto` opcional).
- Testar nova VPC com Redis opt-in, sem IP/rede codificada, validando `redis_up=1` em AMP.
- Novo Redis sem tag ou VPC sem tag é `SKIP_NO_OPT_IN`/`BLOCKED`; isso é proteção intencional.
- Não há `DELETE` automático. Quando Redis desaparecer, listar ECS Services gerenciados e comparar com inventory; exigir duas descobertas consistentes, ticket e autorização antes de `update-service --desired-count 0` e `delete-service`. Revogar regra SG e secret só com verificação de não-compartilhamento.
- `redis-exporter` por Redis consome Fargate. Revisar custos, quotas, IPs públicos, capacidade e retenção AMP antes de ampliar a todas as contas.

## 12. Testes, Git e evidências de aceite

```bash
python -m unittest discover -s discovery/tests -p 'test_*.py' -v
python -m compileall -q discovery/aws_discovery
python - <<'PY'
import yaml
with open('kubernetes/aws-discovery/cronjob.yaml') as f:
    docs=list(yaml.safe_load_all(f))
assert any(x and x.get('kind')=='CronJob' for x in docs)
print('CronJob YAML OK')
PY
kubectl get cronjob redis-discovery-aws -n redis-observability
kubectl get scrapeconfigs -n redis-observability -l discovery-source=aws
kubectl get jobs -n redis-observability --sort-by=.metadata.creationTimestamp | tail -8

git diff --check
git status -sb
git add discovery kubernetes/aws-discovery/cronjob.yaml scripts
git commit -m "feat: reconcile AWS Redis collectors on ECS Fargate"
git push -u origin feature/ecs-autoprovision-aws
```

Evitar incluir `.bak`, `/tmp`, tokens SSO, chaves/certificados Roles Anywhere ou Secrets no commit. Configurações devem conter apenas ARNs/nomes, nunca material secreto. Abrir PR com revisão antes de integrar em `main`.

### Critérios mínimos para declarar operacional

1. Serviços **existentes** permanecem: Virgínia LOCAL; São Paulo ECS `ACTIVE 1/1`; AMP São Paulo `redis_up=1`.
2. Novo Redis opt-in em nova VPC é detectado no prazo do CronJob, Service é criado sem operação manual; `redis_up=1` no AMP.
3. Novo ciclo de discovery não gera Task Definition adicional nem SG/regra/Service duplicados.
4. Redis com autenticação secret recupera credencial pela execution role e apresenta `redis_up=1`.
5. Uma conta adicional passa pelas trust policies e envia métricas ao AMP central (se multiaccount é requisito da entrega).
6. CronJob `local-v5` executa a cada 5 min; permissionamento sem AWS SSO humano; região auto se habilitada; sem targets AWS obsoletos DOWN.
7. Alertas e consulta são acessíveis no(s) backend(s) de métricas escolhidos; custo/cotas estão documentados.
8. Testes novos de controller, falhas parciais, backoff, rede, auth/Secrets, cross-account e lifecycle passam; runbook e PR aprovados.

> **Limitação transparente:** este kit implementa o fluxo seguro de criação e atualização básica para Redis Serverless e replication group *cluster mode disabled*, em regiões/contas autorizadas. Ele não implementa automaticamente deleção, verificação ponta a ponta de NACL/DNS/IAM, autorização cross-account inicial, clonagem de policies KMS ou agrupamento multi-target por VPC. Essas são etapas operacionais e de endurecimento descritas acima; não declarar multiaccount sem validá-las.
