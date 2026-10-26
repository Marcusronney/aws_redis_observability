# Criar e testar Redis pelo Terraform

## Novo padrão

Status da implementação: módulo e ambientes preparados; governança ainda não
aplicada. A sessão SSO administrativa expirou ao gerar os planos reais. Renove
o login e revise os planos antes de aplicar IAM/SCP ou criar o cache. Os recursos
manuais existentes foram preservados. Não interprete o código da SCP como um
bloqueio já ativo na conta.

Use `terraform/environments/redis-cache`, que chama `terraform/modules/redis-cache`.
Não crie novos caches pelo console ou por comandos boto3. Os scripts antigos
de aceitação `vault_cache_test.py` continuam usando Terraform e já possuem tags.

O módulo fixa `RedisObservability=vault-lab` e `ManagedBy=terraform`, exige
Project, Environment e CostCenter não vazios e impede sobrescrita por tags extras.
Cria Valkey com TLS, criptografia em repouso, subnet group e Security Group dedicado.
O controlador configura a entrada TCP 6379 depois da descoberta. Não há acesso público.

O exemplo usa um nó `cache.t4g.micro`, sem backups e sem AUTH, para repetir o
teste atual de baixo custo. TLS permanece obrigatório. Para Multi-AZ use
`replicas=1` ou mais: serão criados nós adicionais com custo adicional.
Um subnet group em duas AZs, sozinho, não significa que há dois nós ou Multi-AZ.

O Terraform não cria VPC ou EC2 neste ambiente. As duas subnets devem pertencer
à VPC escolhida e estar em AZs distintas. CIDRs de VPCs precisam ser distintos
dos outros links do gateway.

## Bloqueio na OU

`terraform/environments/cache-governance` prepara:

- StackSet com `RedisTerraformProvisioner` nas contas atuais e futuras da OU.
- Trust para o papel IAM do operador administrativo central que aplica esse ambiente.
- SCP anexada à OU, bloqueando criação de caches por outras identidades,
  criação sem as cinco tags e remoção dessas tags.
- Ordem: distribuir a role antes de anexar a SCP, para evitar bloquear o fluxo novo.

Não é uma atestação de que o programa chamador é Terraform. Quem puder assumir
a role dedicada ainda poderá chamar APIs diretamente. Para exclusividade técnica
de uma pipeline, restrinja a trust a uma identidade CI e retire o acesso humano.
A SCP atua nas contas membro da OU, não na conta de gerenciamento, e não corrige
retroativamente caches existentes. A implantação dessa governança deve ser
aprovada separadamente por afetar administradores da OU.

## 1. Preparar a VM

```bash
cd /root/redis-vault-test-source
source /root/projetos/aws_redis_observability/.venv-aws/bin/activate
export AWS_PAGER=""
aws sso login --profile redis-observability-admin --use-device-code
```

## 2. Instalar a governança uma única vez

Somente depois de revisar/aprovar o plano IAM/SCP:

```bash
terraform -chdir=terraform/environments/cache-governance init
terraform -chdir=terraform/environments/cache-governance plan
terraform -chdir=terraform/environments/cache-governance apply
```

Confirme no StackSet `redis-terraform-provisioner` que a conta alvo está
CURRENT/SUCCEEDED. Verifique se o tipo SERVICE_CONTROL_POLICY está habilitado
na Organization antes de aplicar; sua habilitação é uma alteração administrativa.
Para outra identidade central operar, revise a trust em vez de ampliar a role de discovery.

## 3. Configurar o novo cache

```bash
cp -n terraform/environments/redis-cache/terraform.tfvars.example \
  terraform/environments/redis-cache/terraform.tfvars
cat terraform/environments/redis-cache/terraform.tfvars
```

O exemplo está configurado para a conta `417140778338`, Ohio (`us-east-2`),
nome `redis-tf-conta03` e as subnets default consultadas na conta.
Confira esses IDs antes do apply. Para outra conta/região, ajuste o arquivo.
Use um diretório de ambiente/estado separado para cada implantação; não troque
o ID da conta dentro de um estado que já administra outro cache.

O cache manual `testeredisconta03` não foi importado, modificado ou excluído.
Este roteiro cria outro recurso. Não use o mesmo nome sem planejar sua importação.

## 4. Revisar e criar

```bash
terraform -chdir=terraform/environments/redis-cache init
terraform -chdir=terraform/environments/redis-cache plan -out=cache.tfplan
terraform -chdir=terraform/environments/redis-cache apply cache.tfplan
terraform -chdir=terraform/environments/redis-cache output cache
```

Revise a conta, região, três recursos e tags no plano. O apply gera cobrança
do cache até sua exclusão. Não é necessário adicionar tags manualmente.
Não é necessário executar discovery Python nem cadastrar endpoint.

## 5. Verificar descoberta, rede e Prometheus

```bash
kubectl -n redis-vault-lab get deployments,cronjobs,jobs
kubectl -n redis-vault-lab get scrapeconfigs
journalctl -u redis-regions.service -n 40 --no-pager
```

Aguarde Available e os ciclos de rede/discovery. Problemas de CIDR, IAM ou
metadados aparecem em `/root/redis-network-automation/status.json`.
O primeiro peering real cross-account ainda precisa ser confirmado pelo teste.

Na VM, mantenha aberto:

```bash
kubectl -n monitoring port-forward \
  svc/monitoring-kube-prometheus-prometheus 19090:9090 --address=127.0.0.1
```

No Windows, em outro terminal:

```bash
ssh -N -L 19090:127.0.0.1:19090 root@192.168.3.55
```

Abra `http://127.0.0.1:19090` e consulte:

```promql
redis_up{aws_account_id="417140778338",redis_name="redis-tf-conta03"}
```

Resultado esperado: `1`. No terminal da VM:

```bash
python scripts/verify_local_pipeline.py \
  --redis-name redis-tf-conta03 --nodes --timeout 600 \
  --output /tmp/redis-tf-verificado.json
```

## 6. Comprovar idempotência

```bash
terraform -chdir=terraform/environments/redis-cache plan -detailed-exitcode
echo $?
```

Esperado: sem mudanças, código `0`. Código `2` significa diferenças; revise o
plano antes de reaplicar. Após outro ciclo de rede, confira que o peering mantém
o mesmo ID e que `redis_up` permanece em `1`.

## 7. Excluir o cache de teste

```bash
terraform -chdir=terraform/environments/redis-cache plan -destroy
terraform -chdir=terraform/environments/redis-cache destroy
```

Esse estado remove somente o novo cache, subnet group e SG que criou. O cache
manual e o gateway continuam existindo. O OFF do laboratório não exclui caches;
eles continuam sendo cobrados até sua remoção. Peerings/rotas são preservados
pela automação e têm estados separados; não destrua esses estados junto sem revisão.

## AUTH/Vault

Para teste autenticado, use `auth_enabled=true` e forneça `TF_VAR_auth_token`
em memória (nunca em tfvars ou argumentos). Registre o mesmo segredo no caminho
Vault da conta/região/nome antes de esperar coleta. Consulte
[VAULT_CICLO_SEGURO.md](VAULT_CICLO_SEGURO.md). O módulo usa campo write-only e
variável efêmera para evitar persistir a senha no state/plano. Rotação exige
atualizar `auth_version` e seguir o fluxo de rotação existente.

Referências: [recurso Terraform ElastiCache](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/elasticache_replication_group),
[controles Organizations](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_authorization_policies.html).
