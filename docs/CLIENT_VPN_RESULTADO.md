# Resultado do piloto temporário — 22/10/2026

O usuário autorizou ligar a Client VPN apenas para testar e, após a validação,
remover somente o piloto VPN. O teste foi executado na VM Rocky Linux
`192.168.3.55`, em uma cópia isolada `/root/redis-vpn-test-source`.
Os backups e logs ficaram em `/root/redis-vpn-session`, com acesso restrito.

## Validação real

| Verificação | Resultado |
|---|---|
| VM → `redis-observability-sa-test` | PONG, TLS 1.3 |
| VM → `redis-observability-v5-e2e` | PONG, TLS 1.3 |
| Pod Kind → ambos os endpoints | DNS e PING/TLS aprovados; Pod `Succeeded`, exit 0 |
| Prometheus local → Redis Virgínia | `redis_up=1` |
| Prometheus local → dois Valkey São Paulo | `redis_up=1`, labels AWS e de negócio presentes |
| Discovery primeiro ciclo | Uma atualização e três criações de ScrapeConfig |
| Discovery segundo ciclo | Quatro `UNCHANGED` |
| AMP existente | Três recursos São Paulo com `redis_up=1` e amostras de origem recentes |
| ECS existente | Três Services mantidos em desired=1/running=1 |

Também foi descoberto e coletado o `redis-observability-v5-demo-02`, sem
cadastro manual de endpoint na política. Ele estava saudável, mas não tinha
tag `project`; essa informação de negócio não foi inventada. Os três recursos
principais de aceite passaram na verificação de labels e amostras <120 s.

As amostras registradas tinham aproximadamente 19–31 segundos de idade.
A troca de labels de Virgínia gerou uma série histórica adicional transitória;
a API de targets confirmou um único target ativo para o recurso.

Evidência: [validação](evidence/client-vpn-acceptance-2026-10-22.json).

## Ajustes encontrados durante o teste

- UDP 443 apresentou saída de pacotes sem resposta. O endpoint temporário foi
  substituído por TCP 443, que conectou. Isso não identifica sozinho em qual
  ponto do caminho UDP ocorreu o bloqueio.
- O gerador de certificados precisava declarar `keyUsage` além de EKU.
  Foi corrigido; OpenVPN confirmou `VERIFY KU OK`, `VERIFY EKU OK` e o nome
  do servidor. A verificação TLS permaneceu habilitada.
- Uma regra de transporte foi limitada ao IPv4 público `/32` da rede Rocky.
  O acesso Redis ficou restrito ao SG da VPN e à porta 6379.
- O NAT Docker existente foi suficiente; não houve mudança nas regras de
  firewall do host, no SELinux ou na rota WireGuard.
- O recurso legado `sa-test` recebeu somente durante o piloto duas tags de
  opt-in, após PONG/TLS provar noauth. As tags originais foram restauradas
  exatamente, sem mudar suas tags de negócio.

## Limitação de métricas de memória

`redis_up=1` e a conectividade foram comprovados. Porém, neste teste,
`INFO memory` no Serverless retornou uma string vazia (`$0`). A consulta
`redis_memory_used_bytes` retornou apenas Virgínia. É necessário tratar a
telemetria de memória do Serverless separadamente, avaliando suas métricas
CloudWatch, como [`BytesUsedForCache`](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/Scaling-serverless.html), antes de afirmar cobertura completa.
Não houve geração de valores artificiais ou substituição da ausência por zero.

## Remoção concluída

- Terraform: **0 criados, 0 alterados, 9 destruídos** na remoção final.
- State dedicado vazio; nenhum Client VPN restante em São Paulo.
- SG exclusivo e suas regras removidos, inclusive a regra temporária no SG Redis.
- Log group e stream exclusivos removidos.
- Os dois certificados ACM importados pelo teste foram removidos.
- OpenVPN parado, interface/rota VPN retiradas e timer de contingência parado.
- Pod e Jobs temporários removidos; novos ScrapeConfigs removidos.
- Spec do CronJob v5 e dados do ConfigMap idênticos aos backups.
- Somente Virgínia voltou a ser target AWS ativo no Prometheus local, saudável.
- Redis Serverless, Services ECS/AMP e WireGuard existentes preservados.

Evidência: [estado final](evidence/client-vpn-teardown-2026-10-22.json).
Arquivos protegidos de certificados locais e auditoria foram mantidos na VM;
não há endpoint ativo que os utilize. O pacote OpenVPN e a imagem de teste
também permanecem disponíveis, sem serviço/túnel ativo.

## Como desligar o piloto de forma reproduzível

O script abaixo foi executado no teste e usa o state exclusivo da VPN:

```bash
bash /root/redis-vpn-test-source/scripts/client_vpn_test_teardown.sh
```

Ele restaura os backups do Kubernetes, cancela somente Jobs registrados pelo
piloto, remove seus targets e tags temporárias, para OpenVPN/port-forward,
confere que o plano contém somente tipos de recursos permitidos e executa
`terraform plan -destroy` seguido de `terraform apply` do plano salvo.
Depois remove os certificados ACM exclusivos. Um marcador evita repetir uma
sessão já encerrada. `client_vpn_restore.py` tem testes que impedem a remoção
de um target preexistente ou de nomes fora do piloto.

O núcleo Terraform, para uso no diretório que contém o state do piloto, é:

```bash
terraform -chdir=terraform/environments/client-vpn plan -destroy -out=teardown.tfplan
terraform -chdir=terraform/environments/client-vpn apply teardown.tfplan
```

Não executar esses comandos nos módulos de Redis/ECS anteriores. Client VPN
não foi deixada apenas desconectada: a associação e o endpoint foram removidos.
Isso encerra a cobrança contínua desses recursos; o uso efetivo do teste pode
aparecer posteriormente na fatura. O valor faturado não foi consultado.

Uma nova sessão exige autorização de custos, novos backups e novo diretório de
sessão; não sobrescrever os backups desta validação. O helper
`python -m scripts.client_vpn_activate_test` pressupõe a estrutura isolada deste
runbook e a aprovação prévia de conectividade do Pod.

O piloto validou conectividade e centralização temporária. Vault, remoção
automática de Redis excluídos, cluster mode/Sentinel, segunda conta e SLO de
cinco minutos continuam pendentes do exercício completo.
