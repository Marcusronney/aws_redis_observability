terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}
variable "management_profile" { type = string }
variable "management_account_id" { type = string }
variable "member_role_name" {
  type    = string
  default = "RedisObservabilityOUDiscoveryRole"
  validation {
    condition     = can(regex("^[A-Za-z0-9+=,.@_-]{1,64}$", var.member_role_name))
    error_message = "Use a valid IAM role name."
  }
}
variable "organization_id" {
  type = string
  validation {
    condition     = can(regex("^o-[a-z0-9]{10,32}$", var.organization_id))
    error_message = "Use the real Organization ID."
  }
}
variable "ou_ids" {
  type = set(string)
  validation {
    condition     = length(var.ou_ids) > 0 && alltrue([for ou in var.ou_ids : can(regex("^ou-[a-z0-9]{4,32}-[a-z0-9]{8,32}$", ou))])
    error_message = "Select explicit authorized OUs."
  }
}
variable "trusted_principal_arns" {
  type = list(string)
  validation {
    condition = length(var.trusted_principal_arns) > 0 && alltrue([
      for arn in var.trusted_principal_arns : can(regex("^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+$", arn))
    ])
    error_message = "Use explicit central IAM role ARNs, without wildcards."
  }
}
variable "call_as" {
  type    = string
  default = "SELF"
  validation {
    condition     = contains(["SELF", "DELEGATED_ADMIN"], var.call_as)
    error_message = "Use SELF or DELEGATED_ADMIN."
  }
}
provider "aws" {
  region              = "us-east-1"
  profile             = var.management_profile
  allowed_account_ids = [var.management_account_id]
}
resource "aws_iam_role" "reader" {
  name = "RedisObservabilityOrganizationReader"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = ["sts:AssumeRole", "sts:SetSourceIdentity", "sts:TagSession"], Principal = { AWS = var.trusted_principal_arns },
    Condition = { StringEquals = { "aws:PrincipalOrgID" = var.organization_id } }
  }] })
}
resource "aws_iam_role_policy" "reader" {
  role = aws_iam_role.reader.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Resource = "*", Action = [
    "organizations:DescribeOrganization", "organizations:ListAccountsForParent", "organizations:ListOrganizationalUnitsForParent"
  ] }] })
}
resource "aws_cloudformation_stack_set" "members" {
  name             = "redis-observability-account-onboarding"
  permission_model = "SERVICE_MANAGED"
  call_as          = var.call_as
  capabilities     = ["CAPABILITY_NAMED_IAM"]
  auto_deployment {
    enabled                          = true
    retain_stacks_on_account_removal = true
  }
  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Resources = { DiscoveryRole = {
      Type = "AWS::IAM::Role"
      Properties = {
        RoleName = var.member_role_name
        AssumeRolePolicyDocument = { Version = "2012-10-17", Statement = [{
          Effect    = "Allow", Action = ["sts:AssumeRole", "sts:SetSourceIdentity", "sts:TagSession"], Principal = { AWS = var.trusted_principal_arns },
          Condition = { StringEquals = { "aws:PrincipalOrgID" = var.organization_id } }
        }] }
        Policies = [{ PolicyName = "RedisDiscoveryReadOnly", PolicyDocument = {
          Version = "2012-10-17", Statement = [{ Effect = "Allow", Resource = "*",
          Action = jsondecode(file("${path.module}/../../policies/member-discovery-actions.json")) }]
        } }]
        Tags = [{ Key = "ManagedBy", Value = "redis-account-onboarding" }]
      }
      }, NetworkRole = {
      Type = "AWS::IAM::Role"
      Properties = {
        RoleName = "RedisObservabilityOUNetworkRole"
        AssumeRolePolicyDocument = { Version = "2012-10-17", Statement = [{
          Effect    = "Allow", Action = ["sts:AssumeRole", "sts:SetSourceIdentity", "sts:TagSession"],
          Principal = { AWS = "arn:aws:iam::${var.management_account_id}:role/RedisObservabilityNetworkAutomationRole" },
          Condition = { StringEquals = { "aws:PrincipalOrgID" = var.organization_id } }
        }] }
        Policies = [{ PolicyName = "RedisNetworkReconcile", PolicyDocument = {
          Version = "2012-10-17", Statement = [
            { Effect = "Allow", Action = ["ec2:Describe*"], Resource = "*" },
            { Effect = "Allow", Action = ["ec2:AcceptVpcPeeringConnection", "ec2:CreateRoute", "ec2:DeleteRoute",
              "ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress", "ec2:ModifySecurityGroupRules",
              "ec2:CreateTags", "ec2:DeleteTags"],
            Resource = { "Fn::Sub" = "arn:aws:ec2:*:$${AWS::AccountId}:*" } }
          ]
        } }]
        Tags = [{ Key = "ManagedBy", Value = "redis-account-onboarding" }]
      }
    } }
  })
}
resource "aws_cloudformation_stack_set_instance" "members" {
  stack_set_name = aws_cloudformation_stack_set.members.name
  region         = "us-east-1" # IAM is global; one deployment per member is sufficient.
  call_as        = var.call_as
  retain_stack   = true
  deployment_targets { organizational_unit_ids = var.ou_ids }
}
output "reader_role_arn" { value = aws_iam_role.reader.arn }
