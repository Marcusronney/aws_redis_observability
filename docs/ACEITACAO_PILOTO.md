# Aceitação do piloto local

Execução real em 23/10/2026, Kind na VM `192.168.3.55`, com um Redis existente
e um Valkey temporário em `us-east-1`. O cache temporário foi autorizado somente
para esta validação. Este resultado não substitui homologação em outras redes.

| Critério | Evidência observada |
|---|---|
| Descoberta sem novo apply de monitoramento | Cache novo apareceu com `redis_up=1` em 116,75 s após a primeira observação de disponibilidade |
| Tags de negócio | Mudança de `CostCenter` chegou às labels do Prometheus |
| Vault → Secret → exporter | Kubernetes auth, projeção e reload confirmados |
| Rotação | Nova senha aceita; senha anterior rejeitada após `SET` |
| Senha inválida | Job falhou preservando Secret, estado e ScrapeConfigs; recuperação passou |
| OFF/ON local | Vault persistido e coleta restaurada sem reinserir credenciais |
| Remoção real | Cache ausente, target retirado e entrada removida do Secret |
| Recriação real | Coleta retornou com credencial preservada no Vault, em 184,21 s após observar disponibilidade |
| State sem senha | Verificação em memória dos states atuais e backups passou nas operações do cache temporário |
| Seleção por nó, AZ e shard | Implementada e coberta por testes; teste real de múltiplos membros/AZs ainda necessário |
| Standalone/Serverless e contas adicionais | Seleção/configuração implementadas; a matriz real acima se refere a replication groups autenticados em Virgínia |

Os prazos acima começam na **primeira observação de disponibilidade**; não são
medidas do instante exato em que a AWS concluiu o provisionamento. Rede privada
e credencial no Vault são pré-requisitos para a meta de cinco minutos.

A validação final passou: Terraform iniciou a EC2 existente a partir de parada;
o pipeline coletou um nó em `us-east-1b`, e as duas réplicas autenticadas
confirmaram o reload. O OFF final parou a EC2, retirou Jobs/targets, aplicou
zero réplicas e preservou o PVC. Cache temporário ausente e state dedicado vazio.
**73 testes passaram no Windows e no Linux.**
[Evidências consolidadas](evidence/acceptance-2026-10-23.json).

O novo verificador falhou inicialmente ao acessar o Prometheus sob systemd;
foi ajustado para explicitar kubeconfig, usar porta livre e repetir o forward.
A limpeza automática dessa primeira tentativa passou e a repetição confirmou
métricas recentes e reload em todos os Pods.

## Limites para a expansão

A estrutura suporta contas/regiões configuráveis e replicas de exporters, mas
nesta execução não foi criada conectividade permanente para São Paulo ou
outras contas. Cada VPC precisa de rota, retorno, DNS e permissão de acesso.
Os caches Serverless anteriores de São Paulo foram apenas consultados.

Para poucas VPCs, peering é uma possibilidade a avaliar: não há cobrança para
criar a conexão, mas transferência entre regiões/AZs pode ser cobrada.
Nenhum peering foi criado neste teste.
[Referência AWS](https://docs.aws.amazon.com/vpc/latest/peering/what-is-vpc-peering.html).

Os ajustes operacionais antes de produção estão em
[OPERACAO_E_PRODUCAO.md](OPERACAO_E_PRODUCAO.md). O piloto usa um Vault local
de um nó e uma EC2 WireGuard existente; não entrega HA de conectividade/Vault.
