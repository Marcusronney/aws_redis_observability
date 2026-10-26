# Vault e reconciliação segura no Kind

Operação atual por `terraform apply -var='active=true|false'`:
[guia do ambiente único local](../terraform/environments/vault-lab/README.md).
O Terraform passou a chamar o bootstrap/cleanup pelo operador local; os comandos
Python abaixo continuam como compatibilidade e documentação dos testes anteriores.

O ambiente de teste é isolado em `redis-vault-lab`. O pipeline anterior continua
em `redis-observability`. Terraform administra a estrutura local; o controller
administra os ScrapeConfigs dinâmicos. O módulo não cria recursos AWS.

## Fluxo

1. AWS Roles Anywhere fornece credenciais temporárias de leitura; STS confirma
   a conta antes da consulta às regiões configuradas.
2. Discovery produz inventário completo e datado. O controller exige cobertura
   exata das contas/regiões, nenhum erro, idade máxima de cinco minutos e um scan
   posterior ao último concluído. Inventários parciais não autorizam remoções.
3. A política de rede exige conta/região/VPC, CIDR privado e tag opt-in. A conexão
   TCP/TLS é validada do Pod. O alvo de Virgínia já homologado é uma exceção
   explícita de migração, não o mecanismo de descoberta de novos Redis.
4. O Pod usa JWT projetado, audiência `vault`, para autenticar no Vault por TLS,
   com ServiceAccount/namespace vinculados e token curto. A policy permite apenas
   ler o prefixo da conta de teste. O token é revogado ao terminar o ciclo.
5. Credenciais ficam no KV-v2 em
   `kv/redis-observability/<conta>/<região>/<tipo>/<nome>`.
   Campos: `username` e `password`. O caminho é derivado da identidade AWS e não
   pode ser sobrescrito por tags. Cadastrar credencial não cadastra endpoint.
6. Antes de modificar o Secret, o controller valida TLS, AUTH e PING com a
   credencial recebida. Atualiza um mapa `passwords.json` em Secret dedicado,
   aguarda projeção atômica/reload do exporter e exige `redis_up 1` no scrape.
   Falha nessa etapa restaura o mapa anterior e não altera os targets.
7. Cria/atualiza ScrapeConfigs com labels de negócio permitidas. URL pode conter
   usuário, nunca senha. Exporters com e sem autenticação são Deployments distintos.
8. Exclusão exige ausência do recurso no inventário bruto em dois scans completos,
   separados por pelo menos 60 segundos. Recurso `modifying`/indisponível ou apenas
   filtrado continua preservado. Limite: uma exclusão por ciclo. UID e
   resourceVersion protegem contra remover um objeto substituído concorrencialmente.
9. A credencial de um target retirado permanece por 90 segundos antes da coleta
   de lixo, para acomodar a atualização do Prometheus. A entrada no Vault não é
   apagada pelo controller, permitindo recriar o Redis com a mesma identidade.

Um Lease evita duas reconciliações simultâneas. Seu prazo de 300 segundos é maior
que o `activeDeadlineSeconds=240` do Job. O CronJob executa a cada minuto e usa
`concurrencyPolicy: Forbid`. RBAC limita escrita do Secret/state/Lease a nomes
fixos e ScrapeConfigs ao namespace do laboratório.

## Operação na VM

Pré-requisitos: Kind `redis-monitoring`, Prometheus Operator com seleção do
namespace, WireGuard funcional para a VPC de Virgínia, Roles Anywhere já
configurado, Terraform, Docker, kubectl, Python com `requirements-aws.txt` e
OpenSSL. Executar na raiz de uma cópia do projeto na VM, com kubeconfig local.

```bash
source /root/projetos/aws_redis_observability/.venv-aws/bin/activate
docker build -f discovery/Dockerfile.aws -t redis-discovery-aws:vault-test .
kind load docker-image redis-discovery-aws:vault-test --name redis-monitoring
terraform -chdir=terraform/environments/vault-lab init
python scripts/vault_lab.py test --session /root/redis-vault-session
```

O teste completo com **um cache AWS temporário** usa outro módulo isolado:

```bash
terraform -chdir=terraform/environments/vault-test-cache init
python scripts/vault_full_test.py --session /root/redis-vault-session
```

Esse comando cria `redis-vault-lifecycle-test` em Virgínia e gera cobrança enquanto
ele existir. Preço consultado em 22/10/2026 para Valkey `cache.t4g.micro`:
US$ 0,0128/h, além de eventual transferência. A execução desta sessão foi
explicitamente aprovada. Antes de repetir, conferir preços e autorização.

O roteiro altera uma tag de negócio, faz rotação com `ROTATE`, espera a projeção
da nova senha e confirma reload/Job saudável antes de usar `SET` para revogar a
senha anterior. Injeta temporariamente uma senha incorreta no KV do cache de
teste, comprova preservação do Secret/state e restaura o valor válido. Testa
OFF/ON local, exclui o cache, aguarda remoção do target/credencial do exporter,
recria o cache com a credencial preservada no Vault e o exclui novamente.
Um `finally` tenta remover o cache de teste e desligar o laboratório mesmo
quando alguma etapa falha; erro de cleanup deve ser investigado, não ignorado.

A senha entra no Terraform por variável `ephemeral`, no ambiente do processo,
e é enviada por `auth_token_wo`. Nenhum valor é passado na linha de comando ou
em tfvars. Logs TRACE do Terraform são desabilitados nesse operador de teste;
os states atual e de backup são conferidos em memória contra a senha usada.
O OFF do cache funciona mesmo com Vault indisponível: `enabled=false` não usa
a credencial para excluir o único recurso desse módulo.

O comando local `test` liga, faz bootstrap/unseal, executa dois Jobs reais e executa OFF em
`finally`, inclusive se a aceitação falhar. `on` mantém o laboratório ligado para
depuração; `off` suspende o CronJob, remove Jobs e targets **somente do laboratório**
e aplica `active=false`, levando os três Deployments a zero réplicas.

O OFF preserva PVC, Secrets, configurações e o material de recuperação para
reativar. O bootstrap não sobrescreve a senha já existente no Vault. Não usar
`terraform destroy` para pausa: o PVC usa armazenamento local do Kind e sua
exclusão pode apagar o Vault. Destruir o cluster também pode perder esse volume.
`prevent_destroy` protege os recursos estáticos do módulo e seu namespace.
O diretório `/root/redis-vault-session` é privado (0700), com arquivo de recuperação
0600, fora do Git. Ele precisa de cópia protegida para recuperação.

O bootstrap lê o Secret legado uma única vez para semear a credencial de Virgínia,
sem imprimir seu valor. Secrets, chave TLS e material de unseal são gerenciados
fora do Terraform, evitando que refresh grave seus valores em state/plan.
Configurações públicas e PVC são gerenciados pelo módulo Terraform.

## Limites desta etapa

Vault de um único nó, unseal manual automatizado com uma chave e material root
local são escolhas de laboratório. Produção exige armazenamento/backup adequados,
política de recuperação, auditoria e gestão do token administrativo. Não há
criptografia de Secrets em etcd configurada por este módulo; restringir acesso
ao Kind/VM continua necessário. O exporter autentica Redis por senha estática/ACL;
autenticação IAM Redis exige outra estratégia de renovação.

O controlador é multirregião/multiaccount, mas este bootstrap habilita apenas
Virgínia para aceitação inicial. Outras rotas e políticas Vault devem ser
configuradas e validadas antes de ampliar o escopo. Cluster mode habilitado,
clusters legados sem replication group e Sentinel continuam fora dos targets
suportados pela implementação atual.

O OFF local não interrompe a cobrança dos Redis, ECS, AMP, EC2, EBS ou IPv4
anteriores. Ver [plano separado](PLANO_DESLIGAMENTO_ANTERIORES.md).

Referência: [Vault Kubernetes auth](https://developer.hashicorp.com/vault/docs/auth/kubernetes).
