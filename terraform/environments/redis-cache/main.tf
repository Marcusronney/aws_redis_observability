terraform {
  required_version = ">= 1.11.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.70.0" }
  }
}
variable "admin_profile" { default = "redis-observability-admin" }
variable "account_id" { type = string }
variable "provisioner_role_name" { default = "RedisTerraformProvisioner" }
variable "region" { type = string }
variable "name" { type = string }
variable "vpc_id" { type = string }
variable "subnet_ids" { type = set(string) }
variable "project" { default = "redis-observability" }
variable "environment" { default = "lab" }
variable "cost_center" { type = string }
variable "replicas" { default = 0 }
variable "auth_enabled" { default = false }
variable "auth_token" {
  type      = string
  sensitive = true
  ephemeral = true
  default   = null
}
variable "auth_version" { default = 1 }
provider "aws" {
  profile             = var.admin_profile
  region              = var.region
  allowed_account_ids = [var.account_id]
  assume_role { role_arn = "arn:aws:iam::${var.account_id}:role/${var.provisioner_role_name}" }
}
module "cache" {
  source       = "../../modules/redis-cache"
  name         = var.name
  vpc_id       = var.vpc_id
  subnet_ids   = var.subnet_ids
  project      = var.project
  environment  = var.environment
  cost_center  = var.cost_center
  replicas     = var.replicas
  auth_token   = var.auth_token
  auth_enabled = var.auth_enabled
  auth_version = var.auth_version
}
output "cache" {
  value = { name = module.cache.name, arn = module.cache.arn, tags = module.cache.tags }
}
