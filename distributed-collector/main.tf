# Copy a vetted version of controller/config.yaml here as controller-config.yaml
# BEFORE running kubectl apply -k. Never commit external IDs, tokens or passwords.
amp:
  region: us-east-1
  remote_write_url: https://aps-workspaces.us-east-1.amazonaws.com/workspaces/ws-REPLACE/api/v1/remote_write
collectors:
  exporter_image: ghcr.io/oliver006/redis_exporter:v1.93.0
  adot_image: public.ecr.aws/aws-observability/aws-otel-collector:latest
  assign_public_ip: true
accounts: []
