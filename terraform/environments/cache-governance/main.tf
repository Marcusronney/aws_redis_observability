terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.70.0" }
  }
}
variable "admin_profile" { default = "redis-observability-admin" }
variable "management_account_id" { default = "762012032320" }
variable "ou_ids" { default = ["ou-m28r-4zfxlwec"] }
provider "aws" {
  profile             = var.admin_profile
  region              = "us-east-1"
  allowed_account_ids = [var.management_account_id]
}
data "aws_caller_identity" "operator" {}
data "aws_iam_session_context" "operator" { arn = data.aws_caller_identity.operator.arn }
locals {
  required_tags  = ["Project", "Environment", "CostCenter", "RedisObservability", "ManagedBy"]
  create_actions = ["elasticache:CreateReplicationGroup", "elasticache:CreateCacheCluster", "elasticache:CreateServerlessCache"]
}
resource "aws_cloudformation_stack_set" "provisioner" {
  name             = "redis-terraform-provisioner"
  permission_model = "SERVICE_MANAGED"
  capabilities     = ["CAPABILITY_NAMED_IAM"]
  auto_deployment {
    enabled                          = true
    retain_stacks_on_account_removal = true
  }
  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Resources = { Provisioner = {
      Type = "AWS::IAM::Role"
      Properties = {
        RoleName = "RedisTerraformProvisioner"
        AssumeRolePolicyDocument = { Version = "2012-10-17", Statement = [{
          Effect    = "Allow", Action = ["sts:AssumeRole", "sts:SetSourceIdentity", "sts:TagSession"],
          Principal = { AWS = data.aws_iam_session_context.operator.issuer_arn }
        }] }
        Policies = [{ PolicyName = "TerraformCacheLifecycle", PolicyDocument = {
          Version = "2012-10-17", Statement = [{ Effect = "Allow", Resource = "*", Action = [
            "elasticache:Describe*", "elasticache:ListTagsForResource", "elasticache:CreateReplicationGroup",
            "elasticache:ModifyReplicationGroup", "elasticache:DeleteReplicationGroup",
            "elasticache:CreateCacheSubnetGroup", "elasticache:ModifyCacheSubnetGroup", "elasticache:DeleteCacheSubnetGroup",
            "elasticache:AddTagsToResource", "elasticache:RemoveTagsFromResource",
            "ec2:Describe*", "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup", "ec2:CreateTags", "ec2:DeleteTags",
            "ec2:RevokeSecurityGroupEgress"
            ] }, { Effect = "Allow", Action = "iam:CreateServiceLinkedRole",
            Resource      = "arn:aws:iam::*:role/aws-service-role/elasticache.amazonaws.com/*",
            Condition     = { StringEquals = { "iam:AWSServiceName" = "elasticache.amazonaws.com" } }
          }]
        } }]
      }
    } }
  })
}
resource "aws_cloudformation_stack_set_instance" "provisioner" {
  stack_set_name            = aws_cloudformation_stack_set.provisioner.name
  stack_set_instance_region = "us-east-1"
  deployment_targets { organizational_unit_ids = var.ou_ids }
  retain_stack = true
}
resource "aws_organizations_policy" "terraform_cache" {
  name = "RedisTerraformAndRequiredTags"
  type = "SERVICE_CONTROL_POLICY"
  content = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Sid = "CreateOnlyViaProvisioner", Effect = "Deny", Action = local.create_actions, Resource = "*",
    Condition = { ArnNotLike = { "aws:PrincipalArn" = "arn:aws:iam::*:role/RedisTerraformProvisioner" } } },
    { Sid = "KeepRequiredTags", Effect = "Deny", Action = "elasticache:RemoveTagsFromResource", Resource = "*",
    Condition = { "ForAnyValue:StringEquals" = { "aws:TagKeys" = local.required_tags } } }
    ], [for tag in local.required_tags : {
      Effect    = "Deny", Action = local.create_actions, Resource = "*",
      Condition = { Null = { "aws:RequestTag/${tag}" = "true" } }
      }], [for tag in local.required_tags : {
      Effect                             = "Deny", Action = local.create_actions, Resource = "*",
      Condition                          = { StringEquals = { "aws:RequestTag/${tag}" = "" } }
      }], [for tag, value in { ManagedBy = "terraform", RedisObservability = "vault-lab" } : {
      Effect                             = "Deny", Action = concat(local.create_actions, ["elasticache:AddTagsToResource"]), Resource = "*",
      Condition                          = { Null = { "aws:RequestTag/${tag}" = "false" }, StringNotEquals = { "aws:RequestTag/${tag}" = value } }
  }]) })
}
resource "aws_organizations_policy_attachment" "ou" {
  for_each   = toset(var.ou_ids)
  policy_id  = aws_organizations_policy.terraform_cache.id
  target_id  = each.value
  depends_on = [aws_cloudformation_stack_set_instance.provisioner]
}
