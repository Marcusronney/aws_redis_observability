# No passwords or Vault tokens in this file.
# Copy to config.yaml outside Git or use protected Kubernetes ConfigMap.
amp:
  region: us-east-1
  remote_write_url: https://aps-workspaces.us-east-1.amazonaws.com/workspaces/ws-REPLACE/api/v1/remote_write
collectors:
  exporter_image: ghcr.io/oliver006/redis_exporter:v1.93.0
  adot_image: public.ecr.aws/aws-observability/aws-otel-collector:latest
  # For lab public subnets only. Private subnets require NAT/VPC endpoints.
  assign_public_ip: true
  scrape_interval: 30s
accounts:
  - account_id: "762012032320"
    name: estudos
    profile: redis-discovery-rolesanywhere
    regions:
      sa-east-1:
        assume_role_arn: arn:aws:iam::762012032320:role/redis-observability-controller-sa-east-1
        cluster: redis-observability-distributed
        task_role_arn: arn:aws:iam::762012032320:role/redis-observability-ecs-task-sa-east-1
        execution_role_arn: arn:aws:iam::762012032320:role/redis-observability-ecs-execution-sa-east-1
        log_group: /ecs/redis-observability-distributed
        # Subnets for Fargate task. Must be in SAME VPC as cache.
        # Explicit selection is required if Redis lives in private subnets without NAT.
        # Uses each cache's actual SubnetIds; ensure outbound HTTPS via IGW/NAT/VPC endpoints.
        state_table: redis-observability-collector-state-sa-east-1
        credential_secret_arns: {}
        # Map resource ARN -> ARN of existing Secrets Manager secret populated by Vault sync.
      # us-east-1:
      #   ... bootstrap outputs for that region
