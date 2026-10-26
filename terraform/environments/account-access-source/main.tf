terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}
variable "admin_profile" { default = "redis-observability-admin" }
variable "central_account_id" { default = "762012032320" }
variable "source_role_names" {
  type    = set(string)
  default = ["RedisObservabilityDiscoveryRole", "RedisObservabilityNetworkAutomationRole"]
}
variable "member_role_name" { default = "RedisObservabilityMemberDiscoveryRole" }
variable "additional_member_role_names" {
  type    = set(string)
  default = []
  validation {
    condition     = alltrue([for name in var.additional_member_role_names : can(regex("^[A-Za-z0-9+=,.@_-]{1,64}$", name))])
    error_message = "Use valid IAM role names."
  }
}
variable "organization_id" {
  type    = string
  default = null
}
variable "organization_reader_role_arn" {
  type    = string
  default = null
}
provider "aws" {
  region              = "us-east-1"
  profile             = var.admin_profile
  allowed_account_ids = [var.central_account_id]
}
resource "aws_iam_role_policy" "assume_members" {
  for_each = var.source_role_names
  name     = "RedisAccountAdmission"
  role     = each.value
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([merge({
    Effect   = "Allow", Action = ["sts:AssumeRole", "sts:SetSourceIdentity", "sts:TagSession"],
    Resource = [for name in setunion(toset([var.member_role_name]), var.additional_member_role_names) : "arn:aws:iam::*:role/${name}"]
    }, var.organization_id == null ? {} : { Condition = { StringEquals = { "aws:ResourceOrgID" = var.organization_id } } })],
    var.organization_reader_role_arn == null ? [] : [{
      Effect = "Allow", Action = ["sts:AssumeRole", "sts:SetSourceIdentity", "sts:TagSession"], Resource = var.organization_reader_role_arn
      }], each.value == "RedisObservabilityNetworkAutomationRole" && var.organization_id != null ? [{
      Effect    = "Allow", Action = ["sts:AssumeRole", "sts:SetSourceIdentity", "sts:TagSession"],
      Resource  = "arn:aws:iam::*:role/RedisObservabilityOUNetworkRole",
      Condition = { StringEquals = { "aws:ResourceOrgID" = var.organization_id } }
  }] : []) })
}
