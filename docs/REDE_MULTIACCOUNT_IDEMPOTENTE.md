# Rede multiaccount: reconciliação simples

## Funcionamento

Atualização de 26/10/2026: planos IAM/StackSet aplicados com aprovação explícita;
AssumeRole da role de rede confirmado nas contas `417140778338` e `848349338183`.
A opção `cross_account_network` está habilitada. O laboratório e o gateway
permanecem ON para o teste manual. Não há cache criado por esta etapa.

A VM Rocky consulta as contas ACTIVE da OU e as regiões habilitadas. Todo
Redis/Valkey encontrado entra no inventário. Somente caches com
`RedisObservability=vault-lab` autorizam alterações de rede.

Para cada conta/região/VPC, o controlador usa sempre o mesmo diretório de
estado Terraform em `/root/redis-network-automation/links/`. O provider central
solicita o peering; o provider destino assume `RedisObservabilityOUNetworkRole`
para aceitar e configurar o retorno. A role de discovery permanece somente leitura.

```text
OU → conta → regiões → caches → VPC autorizada
 → estado Terraform existente → peering/rotas/SG
 → EC2 WireGuard com SNAT TCP 6379 → exporter local → Prometheus
```

O StackSet distribui a role de rede às contas atuais e futuras. Somente a
identidade `RedisObservabilityNetworkAutomationRole` pode assumi-la, dentro
da Organization. As permissões abrangem leitura EC2, aceitação de peering,
rotas, regras de entrada e tags técnicas na conta membro. A restrição de adesão
por tag e a limitação TCP 6379 são aplicadas pelo controlador/Terraform; não
são uma condição IAM que restringe todas as operações de rede.

## Idempotência e limites

- Estado estável por conta/região/VPC; reaplicar reutiliza os recursos gerenciados.
- Lock do controlador e lock Terraform evitam execuções concorrentes.
- Regras SNAT usam verificação antes de adicionar; rotas locais existentes são preservadas.
- Rotas de terceiros não são adotadas nem substituídas.
- CIDRs sobrepostos são rejeitados antes do apply. VPCs default repetidas podem conflitar.
- Falhas de acesso preservam o inventário anterior como desatualizado.
- Remover um cache não destrói automaticamente o peering ou a VPC.
- A senha nunca é descoberta na AWS: deve estar no Vault para caches autenticados.
- Sem cache elegível, nenhuma conexão nova é criada.

## Ativação

Primeiro revisar/aplicar os planos de `organization-onboarding` e
`account-access-source`: eles distribuem a role de rede e permitem seu AssumeRole
somente pela identidade de automação. O nome da role é uma convenção única;
não há lista manual de IDs de contas.

Depois habilitar em `discovery/config/automatic-regions.yaml`:

```yaml
cross_account_network: true
member_network_role_name: RedisObservabilityOUNetworkRole
```

A ativação foi feita após a implantação IAM e a revisão. Para suspender somente
a extensão, configure `cross_account_network: false`; a descoberta continua
consultando as contas, mas não provisiona novos links cross-account.

Na VM, com o laboratório ON:

```bash
cd /root/redis-vault-test-source
source /root/projetos/aws_redis_observability/.venv-aws/bin/activate
python -m discovery.aws_discovery.accounts --purpose network --check-access
python -m scripts.automatic_regions
```

O último comando consulta e valida metadados, sem aplicar Terraform ou configurar
gateway/rotas. Para executar o ciclo com alterações e manter os próximos ciclos:

```bash
sudo systemctl start redis-regions.timer
sudo systemctl start redis-regions.service
journalctl -u redis-regions.service -n 40 --no-pager
```

## Teste de repetição

1. Criar o cache em uma conta da OU com a tag de adesão, TLS e credencial no Vault.
2. Aguardar o primeiro ciclo configurar a rede e publicar a política de coleta.
3. Confirmar `redis_up=1` e anotar o peering registrado em `links.json`.
4. Executar outro ciclo e confirmar que o ID do peering permanece o mesmo.
5. No diretório de estado do link, executar `terraform plan -detailed-exitcode`:
   saída `0` significa ausência de alterações; `2` indica diferenças a investigar.

Não executar `terraform destroy` como forma de pausa. O OFF do laboratório
interrompe a automação e para o gateway configurado, preservando os estados.

## Validação desta implementação

Implantação aplicada nas duas contas. Dois ciclos consecutivos consultaram 34
combinações conta/região sem erros e sem criar redes, pois não havia caches.
Os planos de conferência IAM/StackSet retornaram código 0 (sem alterações).
109 testes passaram localmente e na VM. O cluster ficou Ready e o timer ativo.
A comparação final do ConfigMap ficou incompleta por uma falha transitória da
API Kubernetes; não foi usada como prova de ausência de alterações.
Veja a [evidência](evidence/cross-account-network-2026-10-26.json).

Os testes de regressão cobrem separação da role de rede, recusa quando desabilitada,
preservação do caminho da conta central, CIDRs e rotas de terceiros. O teste real
de dois ciclos com cache e tráfego deve ser registrado após existir um cache;
testes unitários não comprovam conectividade real ou ausência de drift na AWS.

Referência: [peering entre VPCs e contas](https://docs.aws.amazon.com/vpc/latest/peering/vpc-peering-basics.html).
