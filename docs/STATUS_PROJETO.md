# Ponto de retomada — 23/10/2026

## Etapa OU aplicada em 26/10/2026

A conta `848349338183` integra `Contas-Monitoradas` (`ou-m28r-4zfxlwec`).
O modo atual do catalogo e `organizations`, sem lista individual de IDs.
Trusted Access e StackSet SERVICE_MANAGED foram ativados. A stack instance
ficou CURRENT/SUCCEEDED, com autodeploy para futuras contas da OU.
As roles centrais propagam a identidade e as tags da sessao Roles Anywhere.
O preflight real retornou `inventory_ready` com 17 regioes para a conta membro.
105 testes locais passaram. A role manual anterior foi preservada.

O laboratorio permanece OFF. Rede cross-account continua pendente e bloqueada
para escrita automatica; este teste valida ingresso e inventario, sem afirmar
coleta Redis entre contas. Consulte o [guia da etapa OU](ETAPA_OU_MONITORAMENTO.md)
e sua evidencia para os resultados do Pod e as instrucoes de teste.

## Último encerramento solicitado

Veja o [novo guia resumido](GUIA_RESUMIDO_PROJETO.md), incluindo arquitetura,
funcionamento dos Pods/exporters e os comandos para retomar e testar.
Os caches de Canadá/Mumbai foram excluídos, ECS foi zerado e as VPNs foram
paradas. A instância externa `aws_lifecicle` e seu disco de 16 GiB foram
excluídos com autorização específica.
O usuário escolheu preservar os dois discos de 8 GiB das VPNs, Elastic IP e
histórico AMP; portanto, não há garantia de custo zero.
Vault/PVC, credenciais e estados foram preservados. O backup WireGuard foi
verificado e salvo apenas no diretório privado da VM.
Detalhes: [evidência do encerramento](evidence/shutdown-2026-10-23.json).
As seções seguintes são histórico de implementação/testes anteriores.

## Evolução: regiões automáticas

Atualização de inventário: todos os Redis/Valkey aparecem na lista `caches` do
status, com ou sem tags. A autorização de rede continua exigindo adesão.
86 testes passaram localmente e na VM. O ciclo real consultou 17 regiões e
encontrou `rediscanada` em `ca-central-1` ainda em criação. A leitura de tags
retornou `InvalidReplicationGroupStateFault`; o cache permaneceu visível como
`tags_unavailable`, sem autorizar alterações de rede. Consulte a
[evidência](evidence/inventory-all-caches-2026-10-23.json).

Implementados controlador host `scripts/automatic_regions.py`, timer systemd,
configuração `discovery/config/automatic-regions.yaml`, identidade Roles Anywhere
própria e módulos Terraform `network-identity` / `network-link`.
O foco desta etapa é uma conta com descoberta de regiões habilitadas; a segunda
conta foi adiada pelo usuário. A versão local passou em 83 testes; a bateria
também é executada na VM após a implantação.
Varredura inicial real: 17 regiões, sem erros e sem caches candidatos pela tag.
Isso não comprova coleta Redis inter-regional; falta cache remoto para homologar.
Consulte o [procedimento e limites](DESCOBERTA_REGIOES_AUTOMATICA.md).

Os resultados e o estado OFF abaixo pertencem ao checkpoint anterior; não
representam o estado atual dos serviços durante os novos testes manuais.

O fluxo principal foi validado no piloto em 23/10/2026. A expansão real para
outras contas/regiões e múltiplas AZs ainda depende de rede e homologação próprias.
Não executar novos testes pagos automaticamente ao retomar.

## Resultado final desta execução

- **73 testes passaram no Windows e na VM Linux**; Terraform validou na VM.
- O roteiro real completou `FULL_ACCEPTANCE_OK`: criação automática, tags,
  rotação com senha antiga rejeitada, senha inválida preservando Secret/estado/
  targets, recuperação, OFF/ON, exclusão e recriação do cache temporário.
- Coleta confirmada no Prometheus em **116,75 s** após observar disponibilidade
  inicial e **184,21 s** na recriação, sem apply de monitoramento por Redis.
- A configuração final usa nós dos replication groups: coletou o membro em
  `us-east-1b` com labels de nó, shard, função e negócio. **Duas réplicas** do
  exporter autenticado confirmaram a geração do Secret; `redis_up=1` recente.
- O Terraform iniciou a EC2 WireGuard existente a partir de `stopped`, fez
  bootstrap/unseal e, no OFF, encerrou o pipeline e parou a EC2 novamente.
- O verificador ganhou kubeconfig explícito para execução por systemd, porta
  local livre e repetição do port-forward. A primeira checagem desse novo
  verificador falhou; sua limpeza automática funcionou. A repetição passou.
- Senhas correntes do exporter não apareceram nos states/backups gerenciados
  dos módulos local e cache de teste. Credenciais continuam fora do state.

**OFF final confirmado em 23/10/2026 às 10:39 BRT (13:39 UTC):** três
Deployments em zero, CronJob suspenso, nenhum Job/target do pipeline, PVC Bound,
cache temporário ausente na AWS e state dedicado vazio, EC2
`i-0f18f326bfde065db` parada. Os dois CronJobs legados permanecem suspensos.
Os **quatro caches anteriores continuam available**; OFF não elimina seus custos,
nem os de outros recursos anteriores ou discos/endereços preservados.

Evidência consolidada sem credenciais:
[acceptance-2026-10-23.json](evidence/acceptance-2026-10-23.json).
[Matriz e limites da aceitação](ACEITACAO_PILOTO.md).

Para retomar na VM, use `/root/redis-vault-test-source`, seu state existente,
o virtualenv e o `terraform.tfvars` já configurado com a EC2. Renove o SSO
administrativo se necessário e aplique `active=true`; para parar, `active=false`.
O diretório `/root/redis-vault-session` e o PVC precisam ser preservados.
[Guia completo](../terraform/environments/vault-lab/README.md).

Contas/regiões, redes/tags, namespace, contexto Kubernetes e réplicas são
configuráveis. Não é necessário manter dois ambientes para esse piloto.
Antes de produção: [operação e ajustes](OPERACAO_E_PRODUCAO.md). A validação
real desta rodada foi em uma conta/região e com grupos de um nó; seleção de
outras topologias tem testes unitários, sem promessa de homologação integral.

As seções abaixo preservam o histórico dos checkpoints anteriores e não
substituem o resultado final acima.

## Atualização: operação local por Terraform

O módulo [vault-lab](../terraform/environments/vault-lab/README.md) agora executa
bootstrap/unseal no `apply -var='active=true'` e encerra Jobs/targets no
`apply -var='active=false'`, preservando PVC e credenciais. Não cria recursos AWS.
Contas/regiões, VPC e tags substituem a lista fixa de endpoints; o pool de
exporters aceita `exporter_replicas` e o controller exige reload de todos os Pods.

Validação desta implementação: **67 testes passaram** no virtualenv local;
Terraform validou na VM, ON/OFF executaram e dois Pods autenticados ficaram
Ready. A descoberta acessou APIs AWS e autenticou no Vault. Porém, a conexão
TCP da própria VM para o Redis `10.80.20.220:6379` expirou, e o WireGuard estava
sem handshake recente. O controller abortou sem publicar targets inválidos.
**Não houve nova confirmação de `redis_up=1` nesta rodada.** Restaurar a rede
privada é o próximo requisito para a aceitação de ponta a ponta; rotação e
remoção/recriação reais continuam pendentes. Nenhum recurso AWS novo foi criado.

As duas réplicas confirmaram a geração do Secret. Um segundo ON após OFF
reutilizou o Vault persistente; o OFF final confirmou os três Deployments em
zero, CronJob suspenso, nenhum Job/target e PVC Bound.
[Evidência desta execução](evidence/terraform-vault-local-2026-10-23.json).

O código atual está em `/root/redis-vault-test-source` na VM; use esse diretório
e seu state existente, com o virtualenv indicado no guia. O pipeline foi
deixado em OFF ao concluir esta validação; os recursos AWS anteriores não são
administrados por esse módulo e continuam exigindo o plano separado de custos.

## Confirmações desta revisão

- Branch `feature/kind-client-vpn`; último commit existente: `6891243`.
- **65 testes locais passaram** em 23/10/2026. Isso não equivale à aceitação
  completa em AWS/Kind. A última execução registrada na VM tinha 63 testes.
- Mudanças de Vault, controller, Terraform e operadores continuam **sem commit**.
  Há alterações/deleções anteriores em `distributed-collector/` e outros arquivos:
  revisar separadamente e evitar `git add -A` indiscriminado.
- VM `192.168.3.55` acessível; Kind disponível.
- Na inspeção inicial, Vault/exporters tinham uma réplica e os CronJobs estavam
  habilitados. O desligamento solicitado anteriormente não tinha sido concluído.
- Os perfis AWS administrativo e discovery do host falharam com
  `TokenRetrievalError`. A identidade Roles Anywhere dentro de um Pod existente
  conseguiu consultar os caches, sem criar recursos.
- A consulta confirmou `redis-observability-lab` em Virgínia e três Serverless de
  São Paulo como `available`. **Os quatro caches continuam ativos na AWS.**
- O cache temporário `redis-vault-lifecycle-test` não apareceu no inventário
  atual; seu state Terraform dedicado tem zero recursos. Ele foi removido.

## Onde o teste parou

| Etapa | Resultado |
|---|---|
| Client VPN temporária | Validada e removida em 22/10; evidências versionadas |
| Vault TLS, KV-v2 e Kubernetes auth | Bootstrap e integração com Redis existente funcionaram |
| Cache temporário | Descoberto automaticamente, com `redis_up=1` no Prometheus |
| Prazo de descoberta | 139,7 s da primeira observação de disponibilidade até a coleta confirmada |
| Tags de negócio | Mudança de `CostCenter` refletida nas labels |
| Senha em state | Verificação em memória dos states passou nas operações realizadas |
| Rotação `ROTATE` | Nova credencial publicada no Vault |
| Reload confirmado e `SET` | Job manual excedeu 240 s; revogação da senha antiga não comprovada |
| Senha inválida, remoção e recriação | Cobertura unitária; aceitação real completa pendente |
| OFF/ON integral | Espera após unseal ajustada; bootstrap voltou a funcionar, mas roteiro integral não passou |

O log `full-test.log` contém `ROTATION_PUBLISHED_OK` e `OFF_OK`, mas não contém
`ROTATION_FINALIZED_OK`, `WRONG_PASSWORD_FAIL_CLOSED_OK` ou `FULL_ACCEPTANCE_OK`.
Depois da limpeza automática, o laboratório foi ligado para diagnóstico;
essa foi a última execução anterior à revisão de hoje.

O código agora cria Jobs de aceitação independentes do CronJob e possui
marcadores de fase sem dados secretos. Ainda não foi demonstrado que essas
alterações resolvem o `DeadlineExceeded`.

## Preservação e pausa

Preservar o PVC `redis-vault-lab/vault-data`, o cluster Kind e o diretório privado
`/root/redis-vault-session`, que contém a recuperação do Vault. Não colocar seu
conteúdo no Git ou no chat. Preservar também os states Terraform da cópia isolada
da VM; não substituí-los por um checkout novo.

A pausa local foi confirmada em **23/10/2026 às 08:52 BRT (11:52 UTC)**:

- `redis-observability/redis-discovery`: suspenso, nenhum Job ativo associado.
- `redis-observability/redis-discovery-aws`: suspenso, nenhum Job ativo associado.
- `redis-vault-lab/discovery`: suspenso, nenhum Job ativo associado.
- Vault e os dois exporters do laboratório: zero réplicas.
- PVC `vault-data`: preservado e `Bound`.
- State do cache temporário: zero recursos.
- VM, Kind e os demais componentes locais não foram desligados.

O operador de pausa terminou com exit code 0. Os estados anteriores dos CronJobs
e o log da pausa foram salvos no diretório privado da sessão na VM.
Evidência: [pausa confirmada](evidence/pause-confirmed-2026-10-23.json).

Pausar o Kind não desliga ElastiCache, ECS, AMP, EC2, EBS ou IPv4 na AWS.
O inventário e a sequência de desativação estão no
[plano separado](PLANO_DESLIGAMENTO_ANTERIORES.md). Os estados de ECS/AMP/EC2 nesse
plano são de 22/10 e não devem ser apresentados como confirmação atual.

## Ordem para continuar

1. Conferir recursos AWS ainda ativos e verificar/renovar a autenticação administrativa.
   Resolver o consumo pendente antes de assumir custo zero.
2. Preservar states, PVC e recuperação do Vault fora do Git.
3. Revisar e salvar o diff, separando alterações anteriores do usuário.
   Sincronizar fonte/imagem no Kind: há mudanças locais posteriores à última imagem.
4. Validar um Job independente com o Redis existente antes de criar cache pago.
   Investigar agendamento, início do Pod e fases; não apenas aumentar o deadline.
5. Quando autorizado retomar, usar `scripts/vault_lab.py on` na raiz da cópia
   isolada da VM. O comando usa Terraform e bootstrap/unseal; um apply isolado
   não substitui todos os pré-requisitos operacionais.
6. Repetir a aceitação: reload, `SET` com rejeição da senha antiga, senha inválida
   sem perda de targets, dois scans de ausência, retirada da credencial do Secret,
   OFF/ON e recriação AWS. O roteiro pago é `scripts/vault_full_test.py`;
   conferir preço/autorização antes de executar. Não reutilizar evidências antigas
   para aprovar uma execução nova.
7. Homologar conectividade privada permanente das outras regiões/contas e
   consolidar os pipelines. São Paulo teve apenas conectividade temporária.
8. Encerrar cada teste verificando state, Kubernetes e inventário AWS; registrar
   recursos preservados e custos residuais.

Arquitetura: [architecture.md](../architecture.md).
Operação: [VAULT_CICLO_SEGURO.md](VAULT_CICLO_SEGURO.md).
Evidências históricas recuperadas da VM: [criação](evidence/vault-creation-observed-2026-10-22.json)
e [labels](evidence/vault-labels-observed-2026-10-22.json).
