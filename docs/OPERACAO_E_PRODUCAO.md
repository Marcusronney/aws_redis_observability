# Operação e caminho para produção

O fluxo ativo é único: Kubernetes externo → descoberta AWS → Vault → Secret →
exporters → Prometheus local. O piloto não exige ECS, Fargate, AMP, TGW ou
Client VPN. Recursos anteriores desses serviços não são removidos pelo módulo.

## Operação do piloto

Use `/root/redis-vault-test-source` na VM, preservando seu state e o diretório
privado `/root/redis-vault-session`. Ative o virtualenv indicado no
[guia Terraform](../terraform/environments/vault-lab/README.md).

1. Autentique o perfil administrativo caso o módulo gerencie a EC2 WireGuard.
2. Use o mesmo `terraform.tfvars` para ON e OFF. Configure a EC2 existente
   somente se quiser que ela seja iniciada/parada junto com o pipeline.
3. Aplique `active=true`; confira bootstrap, rollout e Jobs de descoberta.
4. Consulte `redis_up{discovery_type="aws"}` no Prometheus e verifique as labels
   de conta, região, negócio e, para replication groups, nó/shard/AZ.
5. Aplique `active=false` e confirme zero réplicas, discovery suspenso, ausência
   dos targets do pipeline e, se configurada, EC2 parada. O PVC deve ficar Bound.

Não é necessário executar Terraform quando um Redis elegível é criado. A rede
privada precisa alcançá-lo e sua credencial deve existir no Vault. O provisionador
do Redis publica `username/password` em
`kv/redis-observability/<conta>/<região>/replication_group/<grupo>`; para
Serverless, substitua o tipo por `serverless`. A API AWS não retorna senhas.

Discovery roda a cada minuto para acomodar a projeção do Secret, reload e scrape
dentro da meta de cinco minutos em inventários pequenos. Esse prazo é contado
após o recurso ficar disponível, com rede e credencial prontas. Indisponibilidade
da AWS/Vault/rede não permite prometer esse prazo: o ciclo falha preservando os
targets anteriores. A remoção exige dois inventários completos e frescos.

## Configuração para crescimento

| Necessidade | Ajuste |
|---|---|
| Mais contas/regiões | Arquivo de contas, roles de leitura cross-account e política Vault por conta |
| Mais VPCs | Arquivo de rotas/tags, caminho privado de ida/volta, DNS e security groups |
| Mais exporters | `exporter_replicas`; reload confirmado em todos os Pods autenticados |
| Múltiplas AZs/cluster mode | `collect_nodes: true`; credencial única do grupo e um target por membro |
| Outro Kubernetes | `kube_config_path`, `kube_context`, `namespace`, imagem publicada em registry e StorageClass adequada |
| Inventários grandes | Dividir escopos por conta/região com Lease, Secret e estado próprios; dimensionar Jobs e API quotas |

O modelo atual compartilha um Secret por pool: o tamanho máximo do objeto e a
duração do ciclo limitam seu crescimento. Réplicas extras dividem as requisições
de scrape; não tornam infinitos o inventário ou o mapa de credenciais. Evite
labels de alta cardinalidade e tags não autorizadas.
Cada partição deve ter state, namespace e diretório de recuperação próprios,
além de arquivos de contas/rotas exclusivos. Não altere o namespace de um state
existente para migrar dados; crie uma implantação com backend distinto.

ElastiCache gerenciado não expõe um serviço Sentinel para configurar no exporter.
Serverless não expõe seus nós/AZs físicos. Redis autogerenciado em EC2/Sentinel
exige outro mecanismo de descoberta e não é coberto pelo inventário ElastiCache.
Caches Redis legados sem replication group também possuem seleção automática
por endpoint de nó; a credencial usa o tipo `cluster` no caminho do Vault.
TLS e estado de autenticação confirmado continuam obrigatórios.
Autenticação IAM Redis não é inferida como ausência de senha; fica fora deste
fluxo de credenciais estáticas no Vault.

Para cluster mode, os endpoints individuais são resolvidos em `CacheNodes`:
o `ReadEndpoint` e o `CurrentRole` do membro não são garantidos nesse modo.
Quando a AWS não informa o papel, a label `redis_role` fica `unknown`; o papel
efetivo deve ser consultado nas métricas de INFO do próprio Redis.
Referência: [AWS NodeGroupMember](https://docs.aws.amazon.com/AmazonElastiCache/latest/APIReference/API_NodeGroupMember.html).

## Antes de usar em produção

O código de descoberta/reconciliação pode ser reutilizado. A infraestrutura do
piloto tem simplificações que precisam ser substituídas deliberadamente:

- Usar Kubernetes com armazenamento durável, backup e capacidade adequada,
  em vez de Kind; fixar imagens por digest em registry acessível ao cluster.
- Usar Vault operacional com HA, backup, audit e processo de unseal/auto-unseal
  adequado. A chave única e o root token locais são conveniência do piloto.
  O bootstrap administrativo atual deve ser substituído por provisionamento
  controlado; o controller continua usando Kubernetes auth com acesso de leitura.
- Habilitar criptografia de Secrets em repouso, limitar acesso ao namespace e
  adicionar NetworkPolicies compatíveis com o CNI. Nunca expor `/scrape` ou o
  endpoint de reload à Internet.
- Configurar identidade AWS de máquina, rotação do certificado Roles Anywhere,
  roles de privilégio mínimo e state Terraform protegido com locking/backup.
  SSO administrativo interativo não é identidade de automação de produção.
- Homologar rotas de todas as contas/regiões e conectividade resiliente. Uma EC2
  WireGuard única é simples para laboratório, mas é um ponto único de falha.
- Alertar para Jobs falhos, inventário atrasado, Vault selado, expiração de
  certificados e `redis_up=0`; medir o prazo de descoberta com o inventário real.

Esses ajustes não exigem ambientes separados de produção e homologação para
executar o piloto. Também não tornam a AWS gratuita: caches, conectividade,
discos e endereços podem continuar cobrados mesmo com o pipeline local em OFF.
