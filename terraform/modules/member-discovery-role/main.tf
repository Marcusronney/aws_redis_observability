terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}
variable "trusted_principal_arns" {
  type = list(string)
  validation {
    condition = length(var.trusted_principal_arns) > 0 && alltrue([
      for arn in var.trusted_principal_arns : can(regex("^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+$", arn))
    ])
    error_message = "Trust must list explicit central IAM role ARNs, without wildcards."
  }
}
variable "role_name" {
  type    = string
  default = "RedisObservabilityMemberDiscoveryRole"
}
variable "organization_id" {
  type    = string
  default = null
}
resource "aws_iam_role" "member" {
  name = var.role_name
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [merge({
    Effect = "Allow", Action = "sts:AssumeRole", Principal = { AWS = var.trusted_principal_arns }
  }, var.organization_id == null ? {} : { Condition = { StringEquals = { "aws:PrincipalOrgID" = var.organization_id } } })] })
  tags = { ManagedBy = "redis-account-onboarding" }
}
resource "aws_iam_role_policy" "read" {
  name = "RedisDiscoveryReadOnly"
  role = aws_iam_role.member.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Resource = "*",
    Action = jsondecode(file("${path.module}/../../policies/member-discovery-actions.json"))
  }] })
}
output "role_arn" { value = aws_iam_role.member.arn }
