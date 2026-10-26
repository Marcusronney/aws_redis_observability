terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}
variable "member_profile" { type = string }
variable "member_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.member_account_id))
    error_message = "Use the real twelve-digit member account ID."
  }
}
variable "trusted_principal_arns" { type = list(string) }
variable "organization_id" {
  type    = string
  default = null
}
provider "aws" {
  region              = "us-east-1"
  profile             = var.member_profile
  allowed_account_ids = [var.member_account_id]
}
module "discovery" {
  source                 = "../../modules/member-discovery-role"
  trusted_principal_arns = var.trusted_principal_arns
  organization_id        = var.organization_id
}
output "role_arn" { value = module.discovery.role_arn }
