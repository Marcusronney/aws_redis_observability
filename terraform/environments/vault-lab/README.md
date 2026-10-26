# Ambiente único local: Vault → Secret → redis_exporter

Este módulo reutiliza o Kind `redis-monitoring` na VM Rocky Linux. O nome
histórico `redis-vault-lab` é o namespace do único pipeline desta implementação;
não são criados ambientes de produção/homologação. O pipeline legado permanece
suspenso para evitar duplicação.

Não há provider AWS neste módulo: ele não cria Redis, EC2, VPN, TGW, Fargate,
AMP, NAT Gateway ou discos AWS. Os componentes usam a capacidade já existente
da VM e armazenamento local do Kind. **Isso não torna gratuitos os recursos AWS
existentes, a rede e uma eventual transferência cobrada.**

Opcionalmente, `gateway_instance_id` autoriza o hook Python a iniciar/parar
**uma EC2 WireGuard existente**, com validação da conta AWS. Não a cria nem a
remove. Deixe vazio para administrar a conectividade por outro mecanismo.
Para o piloto atual, configure em `terraform.tfvars`:

```hcl
gateway_instance_id = "i-0f18f326bfde065db"
```

O perfil administrativo deve estar autenticado na VM. ON aguarda a EC2 ficar
saudável; OFF encerra primeiro os Jobs/targets locais e depois para a EC2.
Mantenha esse arquivo nos dois comandos. Não retire a variável antes de OFF.
EC2 ligada é cobrada; EBS/EIP podem continuar cobrados mesmo com EC2 parada.

## Preparação, uma vez

Executar na raiz do projeto **na VM Linux que acessa o Kind**. Pré-requisitos:
Terraform >=1.5, Docker/Kind, kubectl, OpenSSL, Python com
`discovery/requirements-aws.txt`, Prometheus Operator existente e StorageClass
`standard`. Ative o virtualenv antes dos comandos ou configure
`python_executable` com o caminho absoluto dele.

O bootstrap reutiliza a identidade Roles Anywhere já configurada no namespace
`redis-observability` (ConfigMap `redis-discovery-aws-config` e Secret
`redis-discovery-aws-cert`). Ele não cria usuários ou chaves AWS estáticas.
A primeira migração da senha do Redis existente usa seu Secret legado, em
memória; as próximas inicializações preservam a credencial do Vault.

```bash
cd /root/redis-vault-test-source  # cópia que possui o state atual na VM
source /root/projetos/aws_redis_observability/.venv-aws/bin/activate
docker build -f discovery/Dockerfile.aws -t redis-discovery-aws:vault-test .
kind load docker-image redis-discovery-aws:vault-test --name redis-monitoring
terraform -chdir=terraform/environments/vault-lab init
```

Preserve o state do módulo existente na VM. Não inicialize outro state para
administrar o mesmo namespace.

## Ligar e parar por Terraform

```bash
# Ligar: deployments, bootstrap/unseal e descoberta
terraform -chdir=terraform/environments/vault-lab apply -var='active=true'

# Parar: suspender discovery, zero réplicas e retirar Jobs/targets do pipeline
terraform -chdir=terraform/environments/vault-lab apply -var='active=false'
```

O recurso embutido `terraform_data.runtime` executa o operador Python local
após aplicar a infraestrutura. Não é necessário executar um segundo script
manualmente para bootstrap ou cleanup. Se falhar, o apply falha; confira a
mensagem antes de considerar o ambiente pronto.

Após ligar, valide métricas recentes e o reload de todos os exporters:

```bash
python scripts/verify_local_pipeline.py --redis-name redis-observability-lab \
  --nodes --output /tmp/redis-local-acceptance.json
```

Esse comando somente verifica; não altera AWS, credenciais ou targets. Para
outro cluster/namespace, passe `--context` e `--namespace`. Retire `--nodes`
quando estiver validando um endpoint Serverless ou o modo primário.

OFF preserva PVC, dados/versões do Vault, Secrets e recuperação local para ON
posterior. Namespace e recursos estáticos têm `prevent_destroy`. Não usar
`terraform destroy` como botão de pausa nem excluir o Kind: o armazenamento
`local-path` pode ser perdido com a exclusão do cluster.

Se o Vault reiniciar inesperadamente enquanto Terraform já considera o ambiente
ativo, force somente o operador de bootstrap:

```bash
terraform -chdir=terraform/environments/vault-lab apply \
  -var='active=true' -replace=terraform_data.runtime
```

Para outro cluster, configure `kube_context` e `kube_config_path` em um state
próprio. O provider, o bootstrap e os comandos kubectl usam o contexto explícito;
não dependa de trocar globalmente o contexto corrente do operador.

## Contas, regiões, redes e escala

- Contas/regiões e labels: `discovery/config/vault-local-accounts.yaml`.
- `collect_nodes: true` seleciona os membros dos replication groups por nó,
  incluindo cluster mode e múltiplas AZs, com labels `availability_zone`,
  `redis_node`, `redis_shard` e `redis_role`. Todos usam a credencial do grupo
  no Vault. A ausência de um nó exige duas observações completas antes da remoção;
  um grupo indisponível não autoriza remover seus membros. Sem metadados de nós,
  um grupo sem cluster mode mantém o fallback para seu endpoint primário.
- Redes e tags autorizadas: `discovery/config/vault-local-routing.yaml`.
- Outras contas usam `role_arn` autorizado para AssumeRole; a conta é validada
  por STS. Cada VPC precisa de rota privada, retorno, DNS e regras de acesso.
- Não há lista de endpoints Redis na configuração. O padrão seleciona a VPC de
  Virgínia já validada e tags do projeto. Para outro Redis autenticado, o processo
  de provisionamento deve gravar a credencial no caminho determinístico do Vault.
- Alterações nos arquivos de contas/rotas são aplicadas por novo `terraform apply`.
  Criar um Redis elegível dentro do escopo já configurado não requer novo apply.

Ao migrar um pipeline existente entre endpoint primário e coleta por nós,
execute OFF antes da mudança e depois ON: isso retira os targets do modo
anterior sem tratar uma mudança de política como exclusão de um recurso AWS.

Para aumentar o pool, mantenha a variável em um arquivo `terraform.tfvars` público
quanto ao conteúdo, porém ignorado pelo Git, ou passe-a em todos os comandos:

```bash
terraform -chdir=terraform/environments/vault-lab apply \
  -var='active=true' -var='exporter_replicas=2'
```

São dois Deployments (com/sem autenticação), cada um com a quantidade escolhida.
O controller só avança depois que **todos os Pods autenticados** confirmam a
geração do arquivo projetado. O sidecar possui readiness de reload. O controller
continua único, protegido por Lease; não precisa ser replicado para cada Redis.
Inventários grandes exigem dimensionar duração dos Jobs, chamadas AWS e o limite
de tamanho do Secret. Este módulo não promete escala ilimitada. Serverless
expõe endpoint de serviço, sem métricas por nó/AZ administradas pelo cliente.

## Credenciais e persistência

```text
Controller --JWT Kubernetes/TLS--> Vault KV-v2
Controller --mapa de senhas--> Secret Kubernetes
Secret --volume somente leitura--> exporter autenticado
Sidecar --reload + confirmação por Pod--> Controller
Prometheus --/scrape--> exporter --Redis TLS--> Redis AWS
```

Senhas, chave TLS e material de unseal são gerenciados pelo bootstrap fora dos
recursos/data sources Terraform. Nunca entram em tfvars, plano, URL de target ou
saída dos scripts. O diretório padrão `/root/redis-vault-session` é privado e fica
fora do checkout. Preserve-o e faça backup protegido juntamente com os dados do
Vault e states; não publique seu conteúdo.

O laboratório usa Vault de um nó e unseal automatizado com material local
protegido. Não configura HA/auto-unseal externo nem criptografia de Secrets no
etcd do Kind. É uma simplificação para o teste local.
