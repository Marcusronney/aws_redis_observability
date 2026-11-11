A ideia é monitorar automaticamente todos os Redis de várias contas AWS usando redis_exporter, em um cluster Kubernetes que já tem Prometheus Operator.

A infraestrutura precisa ser criada via Terraform, mas o Terraform também deve subir um Pod/Job em Python que faça o discovery a cada 5 minutos.

Esse discovery precisa:

- consultar as contas e regiões AWS configuradas;
- descobrir todos os Redis;
- listar e descrever cada recurso;
- buscar as tags;
- pegar apenas um conjunto configurável dessas tags, principalmente tags de negócio;
- transformar essas tags em labels das métricas;
- descobrir se o Redis usa senha, se é cluster, se usa Sentinel e outras características necessárias;
- direcionar cada Redis para a configuração correta do redis_exporter;
- criar ou atualizar automaticamente o monitoramento no Prometheus Operator.

Também quero dois Deployments do redis_exporter:

- um para Redis com autenticação;
- outro para Redis sem autenticação.

As senhas precisam ficar no Vault e ser disponibilizadas via Secrets para o exporter. Nada de senha exposta.

O ponto principal é:

Depois do terraform apply, se alguém criar um Redis novo em qualquer uma das contas configuradas, em até 5 minutos ele precisa ser descoberto automaticamente e aparecer no Prometheus, com as tags de negócio virando labels, sem precisar rodar Terraform novamente.

Se um Redis for removido, ele também precisa deixar de ser monitorado.

A solução precisa considerar:

- idempotência;
- múltiplas contas e regiões AWS;
- IAM cross-account;
- Kubernetes;
- Vault;
- Secrets;
- discovery;
- redis_exporter;
- Prometheus Operator.

Não vale cadastrar os Redis manualmente.

Quero que você desenhe e implemente essa solução de forma realmente dinâmica, e não apenas um script que funcione para uma lista fixa de Redis.


