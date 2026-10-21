data "aws_caller_identity" "current" {}

locals {
  redis_uri = "rediss://${var.redis_host}:${var.redis_port}"
  labels = merge(var.tags_business, {
    aws_account_id    = data.aws_caller_identity.current.account_id
    aws_region        = var.region
    engine            = "valkey"
    authentication    = tostring(var.redis_auth)
    aws_resource_type = "serverless"
    discovery_type    = "aws-distributed"
  })
  # The Redis exporter and ADOT collector share the Fargate task network namespace.
  collector_yaml = yamlencode({
    receivers = {
      prometheus = {
        config = {
          global = { scrape_interval = "30s", scrape_timeout = "10s" }
          scrape_configs = [{
            job_name     = "redis-aws-distributed"
            metrics_path = "/scrape"
            static_configs = [{ targets = [local.redis_uri], labels = local.labels }]
            relabel_configs = [
              { source_labels = ["__address__"], target_label = "__param_target" },
              { source_labels = ["__param_target"], target_label = "instance" },
              { target_label = "__address__", replacement = "127.0.0.1:9121" }
            ]
          }]
        }
      }
    }
    extensions = { sigv4auth = { service = "aps", region = var.amp_region } }
    exporters = {
      prometheusremotewrite = {
        endpoint = var.amp_remote_write_url
        auth     = { authenticator = "sigv4auth" }
      }
    }
    service = {
      extensions = ["sigv4auth"]
      pipelines  = { metrics = { receivers = ["prometheus"], exporters = ["prometheusremotewrite"] } }
    }
  })
}

resource "aws_security_group" "collector" {
  name_prefix = "redis-collector-${var.region}-"
  description = "ECS Redis Collector: no inbound, outbound only"
  vpc_id      = var.vpc_id
  # Deliberately no ingress. Egress 443 to AMP, 6379 to Redis and image/log dependencies.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Lab egress; restrict with endpoints and proxy in production"
  }
  tags = { Project = "aws-redis-observability", ManagedBy = "Terraform", Component = "distributed-collector" }
}

# This references the *existing* ElastiCache SG without taking ownership of it.
resource "aws_vpc_security_group_ingress_rule" "redis_from_collector" {
  security_group_id            = var.redis_security_group_id
  referenced_security_group_id = aws_security_group.collector.id
  from_port                    = var.redis_port
  to_port                      = var.redis_port
  ip_protocol                  = "tcp"
  description                  = "Collector Fargate in same VPC, TLS Redis scrape"
}

resource "aws_ecs_task_definition" "collector" {
  family                   = "redis-observability-sa-pilot"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn
  container_definitions = jsonencode([
    {
      name      = "redis-exporter"
      image     = var.exporter_image
      essential = true
      command   = ["--redis.addr="]
      portMappings = [{ containerPort = 9121, protocol = "tcp" }]
      environment = [{ name = "REDIS_EXPORTER_SKIP_TLS_VERIFICATION", value = "false" }]
      secrets = var.redis_auth && var.redis_secret_arn != null ? [{
        name = "REDIS_PASSWORD", valueFrom = var.redis_secret_arn
      }] : []
      logConfiguration = { logDriver = "awslogs", options = {
        awslogs-group = var.log_group_name, awslogs-region = var.region, awslogs-stream-prefix = "redis-exporter"
      } }
    },
    {
      name      = "adot"
      image     = var.adot_image
      essential = true
      environment = [{ name = "AOT_CONFIG_CONTENT", value = local.collector_yaml }]
      logConfiguration = { logDriver = "awslogs", options = {
        awslogs-group = var.log_group_name, awslogs-region = var.region, awslogs-stream-prefix = "adot"
      } }
    }
  ])
  tags = { Project = "aws-redis-observability" }
  lifecycle {
    precondition {
      condition     = !var.redis_auth || (var.redis_secret_arn != null && var.redis_secret_arn != "")
      error_message = "Authenticated Redis requires redis_secret_arn (from Vault->Secrets Manager sync)."
    }
  }
}

resource "aws_ecs_service" "collector" {
  name            = "redis-observability-sa-pilot"
  cluster         = var.cluster_name
  task_definition = aws_ecs_task_definition.collector.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.collector.id]
    assign_public_ip = var.assign_public_ip
  }
  tags = { Project = "aws-redis-observability", ManagedBy = "Terraform" }
}
