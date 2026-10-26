# Plano separado para reduzir/remover custos anteriores

**Atualização do encerramento de 23/10/2026:** os serviços ECS foram zerados,
os caches de Canadá/Mumbai foram excluídos e as VPNs estão paradas. O usuário
escolheu preservar os dois discos das VPNs, Elastic IP e histórico AMP.
A EC2 externa `aws_lifecicle` e seu disco foram excluídos com autorização
específica. Consulte o [relatório atual](evidence/shutdown-2026-10-23.json)
e o [guia resumido](GUIA_RESUMIDO_PROJETO.md). As seções abaixo são históricas.

Atualização de 23/10/2026 às 10:39 BRT: o login administrativo foi renovado e
o piloto do ciclo seguro passou. A EC2 WireGuard `i-0f18f326bfde065db` foi
parada pelo Terraform ao encerrar o teste. O cache temporário foi removido;
os quatro caches anteriores continuam `available`. ECS/AMP/EBS/endereços não
foram removidos. O restante deste documento preserva o inventário e plano
anteriores, sem autorização automática para destruir esses recursos.

Plano original: preparar a desativação dos recursos anteriores. Em 23/10/2026,
foi concluída a suspensão dos CronJobs locais e a pausa do laboratório Vault,
atendendo à solicitação posterior de pausa. **A desativação dos recursos AWS
abaixo não foi executada.** O OFF local preserva os recursos AWS anteriores.

A revisão de 23/10 confirmou os quatro caches ainda `available`; os demais
estados da tabela abaixo são do inventário de 22/10. A autenticação administrativa
falhou com `TokenRetrievalError`, impedindo a confirmação completa atual. Consultar o
[status de retomada](STATUS_PROJETO.md).

Inventário somente de leitura realizado em 22/10/2026, conta `762012032320`, regiões
`us-east-1` e `sa-east-1`, sem erros nas consultas. Não representa auditoria de toda
a conta ou garantia de custo zero. O script é `scripts/inventory_existing_costs.py`.

| Região | Recurso confirmado | Estado observado | Ação proposta |
|---|---|---|---|
| us-east-1 | ElastiCache RG `redis-observability-lab` | available, um nó, retenção automática 0 | Snapshot final se houver dados a preservar; excluir somente após definir restauração |
| sa-east-1 | Serverless `redis-observability-sa-test` | available, retenção 0 | Snapshot/exportação se necessários; excluir |
| sa-east-1 | Serverless `redis-observability-v5-demo-02` | available, retenção 0 | Snapshot/exportação se necessários; excluir |
| sa-east-1 | Serverless `redis-observability-v5-e2e` | available, retenção 0 | Snapshot/exportação se necessários; excluir |
| sa-east-1 | ECS `redis-observability-sa-test` | desired/running 1/1 | desiredCount 0, impedir recriação automática |
| sa-east-1 | ECS `redis-collector-4b8dc0662a7f` | desired/running 1/1 | desiredCount 0, impedir recriação automática |
| sa-east-1 | ECS `redis-collector-de7d29a594f1` | desired/running 1/1 | desiredCount 0, impedir recriação automática |
| us-east-1 | EC2 `i-0f18f326bfde065db` (`redis-observability-vpn-v2`) | running, t3.micro | Parar após encerrar os testes; remover posteriormente se desnecessária |
| us-east-1 | EC2 `i-0ed5512ad074e3937` (`redis-observability-vpn`) | stopped, t3.micro | Verificar disco antes de terminar; parada ainda pode deixar armazenamento cobrado |
| us-east-1 | EIP `eipalloc-09de310d334f22c08` | associado à VPN v2 | Liberar somente se aceitar mudar endpoint WireGuard ao recriar |
| us-east-1 | AMP `ws-a32bf55d-d10a-4d22-9873-a937f133db67` | ACTIVE | Encerrar envio primeiro; decidir retenção ou exclusão do histórico |

Não foram encontrados Client VPN nem NAT Gateways ativos nessas duas regiões.
Foram correlacionados dois discos do projeto: `vol-011350c7d7abbc8e5` (8 GiB,
VPN antiga parada) e `vol-0d01db5567b60b497` (8 GiB, VPN v2 em execução).
Conferir a política DeleteOnTermination antes de encerrar as instâncias.
O volume `vol-006cc828758ddb42f` (16 GiB) pertence à instância parada
`i-0de3af21f7c47078e`, nome `aws_lifecicle`, em outra VPC: ambos
**ficam fora do escopo** deste projeto.

Sequência proposta:

1. Salvar configurações públicas, snapshots necessários, state Terraform protegido
   e dependências de rede. Credenciais permanecem no Vault, fora de state/plan.
2. Suspender o CronJob AWS original e qualquer controlador externo que possa
   recriar tasks. O pipeline atual chama `ecs_controller --apply`: apenas reduzir
   desiredCount não garante que as tasks permaneçam desligadas.
3. Zerar os três serviços ECS do cluster `redis-observability-collectors` e verificar
   tasks encerradas. Conferir autoscaling e tasks avulsas antes de considerar a
   computação desligada.
4. Encerrar os Redis escolhidos, com a política de dados aprovada. O inventário
   seguinte deve confirmar a ausência antes de remover os targets correspondentes.
5. Encerrar envio ao AMP; excluir o workspace apenas se o histórico puder ser
   perdido. Conferir separadamente scrapers gerenciados, logs e retenção.
6. Parar a VPN v2 por último. Para eliminar custos residuais, avaliar término das
   instâncias do projeto, discos, EIP, snapshots e logs. Preservar recursos alheios.
7. Reexecutar inventário, conferir Cost Explorer/Billing após a atualização dos
   dados e registrar os identificadores efetivamente encerrados.

Para gerenciar o legado por Terraform, primeiro localizar os states existentes e
importar somente recursos ainda não gerenciados. Capturar configurações reais,
obter um plano sem alterações involuntárias e separar a adoção da desativação.
Não executar `terraform destroy` no diretório AWS atual como atalho: ele também
contém IAM/Roles Anywhere necessários para reativar o discovery. O módulo
`vault-lab` administra somente o ambiente local, e não adota o legado AWS.

Reativação do legado exige recriar Redis/rede quando excluídos, restaurar dados se
necessários, atualizar WireGuard caso o endereço público mude, validar TLS e só
então liberar controladores/ECS. DNS dos Redis é redescoberto; as credenciais
devem existir no caminho determinístico do Vault antes da entrada do target.

As estimativas monetárias devem ser calculadas com preços regionais atuais e
configurações completas antes da execução; não há estimativa de economia total
validada neste documento.

Referências oficiais: [cobrança e exclusão ElastiCache](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/Clusters.Delete.html),
[alteração de tasks ECS](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/update-service-console-v2.html),
[parada/início EC2](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/Stop_Start.html),
[exclusão AMP e perda dos dados](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-delete-workspace.html).
