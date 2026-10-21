output "ecs_service_name" { value = aws_ecs_service.collector.name }
output "task_definition_arn" { value = aws_ecs_task_definition.collector.arn }
output "collector_security_group_id" { value = aws_security_group.collector.id }
output "redis_endpoint" { value = "rediss://${var.redis_host}:${var.redis_port}" }
