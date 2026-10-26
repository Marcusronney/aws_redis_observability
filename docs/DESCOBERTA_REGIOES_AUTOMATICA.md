# Descoberta automática de regiões e preparação de conectividade

Implementação: `scripts/automatic_regions.py`, executado no Rocky por systemd.
O CronJob Kubernetes continua coletando inventário e reconciliando os targets.
Não há exporter ou Prometheus novo na AWS.

## Escopo

Esta versão usa uma conta autorizada. `regions: auto` consulta
`DescribeRegions(AllRegions=False)`: inclui regiões habilitadas e não habilita
regiões opt-in. Novas regiões habilitadas entram no ciclo seguinte.
Não depende de uma lista fixa de regiões nem de VPC default.

`discovery/config/automatic-regions.yaml` define gateway, conta, exclusões,
limite de redes e tags de adesão. Uma segunda conta pode ser declarada, mas
o provisionamento cross-account permanece desabilitado nesta versão e gera
pendência explícita. Não confundir estrutura YAML com validação multiaccount.

Todos os Redis/Valkey, com ou sem tags, aparecem em `status.json`, na lista
`caches`. Somente caches com todas as `required_tags` entram na preparação de rede.
O padrão é `RedisObservability=vault-lab`. Não é feita alteração indiscriminada
em todas as VPCs da conta.

## Fluxo

1. Terraform ON inicia o timer `redis-regions.timer`.
2. O timer executa um ciclo, espera cinco minutos após seu término e repete.
3. O controlador verifica se o CronJob está ligado. Se suspenso, não provisiona.
4. Obtém credenciais temporárias via Roles Anywhere e verifica a conta AWS.
5. Consulta as regiões habilitadas e seus caches com até quatro workers.
6. Resolve os caches selecionados para VPCs, sub-redes e security groups reais.
7. Para VPC nova, rejeita sobreposição com a central, outras redes e redes locais.
8. Terraform cria peering direto, aceita a conexão, adiciona rotas e libera TCP
   6379 no SG a partir do IP privado /32 do gateway.
9. SSM configura encaminhamento e SNAT na EC2. A VM acrescenta as rotas e
   AllowedIPs do WireGuard, preservando os existentes.
10. Publica regiões e redes no ConfigMap `discovery-config`, com resourceVersion.
11. O CronJob consulta essas regiões, valida DNS/TLS/AUTH, lê o Vault e publica
    o ScrapeConfig somente depois da validação do exporter.

O peering é por VPC, não por Redis/AZ. Um segundo cache na mesma VPC reutiliza a
conexão. O SNAT deve ser homologado na conexão real: não se pressupõe que o
peering forneça trânsito para o endereço original da rede local.

## Identidade e permissões

`terraform/environments/network-identity` cria três recursos: role, policy inline
e profile Roles Anywhere. O trust reutiliza o trust da identidade X.509 já
existente, inclusive suas condições. Não altera a role de discovery.

A role de rede tem leitura EC2/ElastiCache, operações de peering/rotas/regras SG
nos recursos EC2 da conta configurada, alteração de atributos somente da EC2
gateway configurada e execução do documento SSM AWS-RunShellScript nessa EC2.
Não tem ações IAM, criação de instâncias ou alteração/criação de caches.
As permissões de rede alcançam várias VPCs dessa conta; a seleção por tag é uma
restrição do controlador, não uma restrição IAM equivalente. Produção exige
revisar boundaries/SCPs, contas dedicadas e políticas por rede aprovada.

O instalador copia certificado e chave existentes para um diretório root-only
fora do checkout e configura um credential_process no perfil
`redis-network-automation`. Não grava credenciais AWS estáticas nem depende de
SSO para os ciclos. O certificado ainda precisa de renovação antes de expirar.
O controlador periódico não acessa o root token do Vault.

## Instalação na VM

```bash
cd /root/redis-vault-test-source
source /root/projetos/aws_redis_observability/.venv-aws/bin/activate
terraform -chdir=terraform/environments/network-identity init
terraform -chdir=terraform/environments/network-identity plan
terraform -chdir=terraform/environments/network-identity apply
python scripts/install_automatic_regions.py
```

O SSO administrativo é necessário para o bootstrap IAM, não para o timer.
Reaplicar ON após atualizar o runtime instala/inicia o fluxo operacional:

```bash
terraform -chdir=terraform/environments/vault-lab apply -var='active=true'
```

Se a configuração Terraform já estiver inteiramente aplicada e o timer estiver
parado, pode iniciar somente o timer com `systemctl start redis-regions.timer`.
Após reiniciar a VM, reative pelo Terraform com
`-replace=terraform_data.runtime` se o estado não indicar mudanças; primeiro
confirme que Kind, PVC e WireGuard estão disponíveis.

## Operação e teste

```bash
systemctl list-timers redis-regions.timer --no-pager
systemctl status redis-regions.service --no-pager
journalctl -u redis-regions.service -n 100 --no-pager
cat /root/redis-network-automation/status.json
```

O status lista regiões consultadas, redes configuradas/planejadas e pendências.
Os logs não imprimem senhas, respostas de Secrets ou valores do Vault.
Os logs privados do Terraform de cada conexão ficam no diretório daquele link.

Para listar todos os caches descobertos, incluindo os sem tags:

```bash
python - <<'PY'
import json
from pathlib import Path
r = json.loads(Path('/root/redis-network-automation/status.json').read_text())
for c in sorted(r.get('caches', []), key=lambda c: (c['region'], c['name'])):
    print(c['account_id'], c['region'], c['name'], c['status'],
          c['network_authorization'], 'fresh=' + str(c['inventory_fresh']))
print('Erros de inventário:', len(r.get('errors', [])))
PY
```

`missing_adoption_tag` significa que o recurso foi descoberto, mas sua tag não
autoriza novas mudanças de rede. Não significa necessariamente que uma coleta
preexistente parou. `tags_unavailable` indica erro na consulta de tags, sem
autorizar alterações. `inventory_fresh=false` preserva o último resultado de
uma região cuja consulta falhou. O campo `collection_status=not_checked` deixa
explícito que o inventário não confirmou a coleta: a prova continua sendo a
métrica recente `redis_up` no Prometheus. Não há nova métrica Prometheus de
inventário nesta alteração.

Grupos de replicação não dependem do campo Engine estar presente na resposta:
a engine é obtida dos membros quando disponível. Membros do mesmo grupo não
são listados como caches independentes; Memcached é excluído do inventário.

Para um ciclo só de planejamento, sem alterações AWS ou publicação Kubernetes:

```bash
python -m scripts.automatic_regions
```

Para antecipar um ciclo automático, sem concorrer com o timer:

```bash
systemctl start redis-regions.service
```

Teste real: crie um cache em uma região habilitada, com TLS, na VPC desejada,
adicione a tag de adesão e publique sua senha no caminho Vault dessa conta,
região e nome. A descoberta não recupera senhas da AWS. Serverless com senha
requer `RedisObservabilityAuth=secret`; sem senha requer declaração explícita
`RedisObservabilityAuth=noauth` e ausência de user group. Não selecione IAM auth.

Aguarde a criação do cache, um ciclo regional e os Jobs Kubernetes. Rede nova
pode exigir vários minutos adicionais para peering e validação. Consulte:

```promql
redis_up{discovery_type="aws"}
```

Falhas de credencial/conexão ficam como `TARGET_PENDING`; não removem um alvo
existente nem substituem sua senha válida. Inventário incompleto não autoriza
exclusões. Métricas já publicadas continuam sendo coletadas pelos exporters.

## Estados Terraform e desligamento

Cada conexão usa o módulo `terraform/environments/network-link` com dois
providers regionais fixos por execução e estado independente:

```text
/root/redis-network-automation/
  links.json
  status.json
  controller.lock
  discovery.crt / discovery.key      # privados, nunca versionar
  links/CONTA-REGIAO-VPC/
    main.tf
    terraform.tfvars.json
    terraform.tfstate
    terraform.log
```

Faça backup dos estados. Não apague esse diretório para reiniciar o teste.
Não há senhas Redis em suas variáveis/estados.

```bash
terraform -chdir=terraform/environments/vault-lab apply -var='active=false'
```

OFF para timer/serviço, suspende Jobs, zera deployments e para a EC2 configurada.
Peerings, rotas, regras gerenciadas e identidade IAM são preservados para ON;
não têm cobrança horária própria de peering, mas EC2/EBS/IP e transferência têm
seus custos aplicáveis. Caches criados manualmente NÃO são removidos por OFF.

Retirar o último cache não destrói uma conexão compartilhada. Para remover uma
conexão definitivamente, primeiro exclua a VPC em `exclude_vpcs`, pare o timer,
revise e execute `terraform destroy` exclusivamente no estado daquele link.
Não destrua VPCs, caches ou SGs existentes: o módulo só gerencia as conexões,
as rotas específicas e as regras adicionadas. Faça limpeza das rotas locais e
regras SNAT correspondentes antes de reutilizar a mesma faixa para outra rede.

## Limites da versão

- Rede IPv4 privada, gateway central e conta única; CIDRs sobrepostos viram
  pendência. VPCs default frequentemente usam a mesma faixa 172.31.0.0/16.
- Não adota rotas existentes que não pertençam ao estado do link.
- Gateway deve estar Running, WireGuard ativo e SSM Online. Não cria gateways
  nem redes automaticamente em regiões vazias.
- A regra criada libera 6379. NACLs, SG de saída do gateway, políticas de
  organização e resolução DNS precisam permitir o fluxo.
- Uma EC2 é ponto único de falha. Este não é um desenho de rede HA de produção.
- O teste de uma varredura sem VPC remota candidata não comprova tráfego Redis
  inter-regional: essa homologação precisa de cache remoto real.

Referências: [regiões habilitadas](https://docs.aws.amazon.com/accounts/latest/reference/manage-acct-regions.html),
[limitações de peering](https://docs.aws.amazon.com/vpc/latest/peering/vpc-peering-basics.html),
[subnets ElastiCache](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/SubnetGroups.html).
