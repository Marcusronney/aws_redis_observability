terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}
provider "aws" {
  region  = var.region
  profile = var.aws_profile
}
variable "region" {
  type = string
  default = "us-east-1"
}
variable "aws_profile" {
  type = string
  default = "redis-observability-admin"
}
variable "existing_discovery_role_name" {
  description = "Existing Roles Anywhere role name. This configuration ATTACHES an IAM policy; it does not recreate the role."
  type        = string
  default     = "RedisObservabilityDiscoveryRole"
}
variable "regional_controller_role_arns" {
  type = set(string)
}

resource "aws_iam_role_policy" "assume_regional_controller" {
  name = "RedisObservabilityAssumeRegionalCollectors"
  role = var.existing_discovery_role_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sts:AssumeRole"]
      Resource = sort(tolist(var.regional_controller_role_arns))
    }]
  })
}
