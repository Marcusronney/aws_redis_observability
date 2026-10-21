# Existing São Paulo Valkey, not created or destroyed by this module.
region                  = "sa-east-1"
aws_profile             = "redis-observability-admin"
vpc_id                  = "vpc-0eb95c9e597551a70"
subnet_ids              = ["subnet-096f17764a3bceb2d", "subnet-00b359872fbcc41ac", "subnet-0dac5f3e3cef3d224"]
redis_security_group_id = "sg-0ec65f2b300695795"
redis_host              = "redis-observability-sa-test-ob1yxa.serverless.sae1.cache.amazonaws.com"
redis_port              = 6379
redis_auth              = false

# Fill these outputs from terraform/region-bootstrap for sa-east-1:
cluster_name       = "REPLACE_BOOTSTRAP_CLUSTER"
execution_role_arn = "arn:aws:iam::762012032320:role/REPLACE_BOOTSTRAP_EXECUTION_ROLE"
task_role_arn      = "arn:aws:iam::762012032320:role/REPLACE_BOOTSTRAP_TASK_ROLE"
log_group_name     = "/ecs/redis-observability-distributed"

# Fill from terraform/central output:
amp_remote_write_url = "https://aps-workspaces.us-east-1.amazonaws.com/workspaces/ws-REPLACE/api/v1/remote_write"
amp_region           = "us-east-1"

# Lab only: network interface receives public IP for HTTPS AMP/pulling images.
# Requires public subnet with effective route to IGW.
assign_public_ip = true
