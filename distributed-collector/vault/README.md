# Vault — integration boundary (not enabled)

Vault has not been deployed or connected in this package, because the real Vault
URL, auth method/mount, identity trust, certificate and secret naming convention
were not provided. Do not invent secrets or put them in tfvars/ConfigMaps.

Target security architecture:

1. Vault holds per-Redis AUTH/ACL credentials, keyed by resource ARN or stable business ID.
2. A trusted secret sync pipeline authenticates to Vault using Kubernetes auth
   or ECS AWS IAM auth; never uses long-lived static Vault tokens in Git.
3. The sync process writes the value encrypted to approved AWS Secrets Manager
   secret versions (regional/account-local), using a policy limiting target paths.
4. `controller/config.yaml` contains only the Secrets Manager **ARN** keyed by Redis ARN, OR the controller can auto-lookup the
   deterministic name `redis-observability/<sha256(resource_arn)[:20]>` via
   Secrets Manager DescribeSecret. This is NOT an automatic password discovery:
   the Vault sync must populate the secret before a cache requiring auth can work.
5. ECS execution role is scoped by Terraform `credential_secret_arns` to those ARNs;
   Fargate injects secret into the exporter process environment at task startup.
   This meets 'no plaintext in YAML/Git', but environment injection is visible
   to privileged ECS/container administrators. For stricter control, use Vault
   Agent templates + encrypted Fargate EFS shared volumes and REDIS_PASSWORD_FILE.
6. Rotation triggers a forced new ECS deployment; credentials must never appear
   in `redis://user:pass@hostname`, Prometheus labels, task args, or logs.

**Missing for production:** implement and verify the Vault integration,
 credential rotation, IAM-auth secret retrieval and audit policies before
 enabling Redis AUTH tasks. Serverless laboratory resource with no UserGroup
 is the initial NOAUTH pilot.
