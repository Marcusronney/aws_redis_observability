

output "aws_account_id" {
  description = "Conta AWS utilizada pelo Terraform"
  value       = var.aws_account_id
}

output "discovery_role_name" {
  description = "Nome da IAM Role de Discovery"
  value       = aws_iam_role.redis_discovery.name
}

output "discovery_role_arn" {
  description = "ARN da IAM Role de Discovery"
  value       = aws_iam_role.redis_discovery.arn
}

output "discovery_policy_arn" {
  description = "ARN da policy de leitura"
  value       = aws_iam_policy.redis_discovery.arn
}
