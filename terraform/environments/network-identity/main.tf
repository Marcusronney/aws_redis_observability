terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}
variable "admin_profile" { default = "redis-observability-admin" }
variable "account_id" { default = "762012032320" }
variable "region" { default = "us-east-1" }
variable "gateway_instance_id" { default = "i-0f18f326bfde065db" }
provider "aws" {
  region              = var.region
  profile             = var.admin_profile
  allowed_account_ids = [var.account_id]
}
data "aws_iam_role" "discovery" { name = "RedisObservabilityDiscoveryRole" }
resource "aws_iam_role" "network" {
  name               = "RedisObservabilityNetworkAutomationRole"
  assume_role_policy = data.aws_iam_role.discovery.assume_role_policy
  tags               = { ManagedBy = "redis-network-automation" }
}
resource "aws_iam_role_policy" "network" {
  role = aws_iam_role.network.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = [
      "ec2:Describe*", "elasticache:Describe*", "elasticache:ListTagsForResource"
    ], Resource = "*" },
    { Effect = "Allow", Action = [
      "ec2:CreateVpcPeeringConnection", "ec2:AcceptVpcPeeringConnection",
      "ec2:DeleteVpcPeeringConnection", "ec2:CreateRoute", "ec2:DeleteRoute",
      "ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress",
      "ec2:ModifySecurityGroupRules", "ec2:CreateTags", "ec2:DeleteTags"
    ], Resource = "arn:aws:ec2:*:${var.account_id}:*" },
    { Effect = "Allow", Action = ["ec2:ModifyInstanceAttribute"],
    Resource = "arn:aws:ec2:${var.region}:${var.account_id}:instance/${var.gateway_instance_id}" },
    { Effect = "Allow", Action = ["ssm:SendCommand"], Resource = [
      "arn:aws:ec2:${var.region}:${var.account_id}:instance/${var.gateway_instance_id}",
      "arn:aws:ssm:${var.region}::document/AWS-RunShellScript"
    ] },
    { Effect = "Allow", Action = ["ssm:GetCommandInvocation"], Resource = "*" }
  ] })
}
resource "aws_rolesanywhere_profile" "network" {
  name             = "redis-network-automation"
  role_arns        = [aws_iam_role.network.arn]
  enabled          = true
  duration_seconds = 900
}
output "role_arn" { value = aws_iam_role.network.arn }
output "profile_arn" { value = aws_rolesanywhere_profile.network.arn }
