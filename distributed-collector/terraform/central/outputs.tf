output "workspace_id" { value = aws_prometheus_workspace.redis.id }
output "workspace_arn" { value = aws_prometheus_workspace.redis.arn }
output "workspace_region" { value = var.region }
output "query_endpoint" { value = aws_prometheus_workspace.redis.prometheus_endpoint }
output "remote_write_endpoint" {
  value = "${trimsuffix(aws_prometheus_workspace.redis.prometheus_endpoint, "/")}/api/v1/remote_write"
}
