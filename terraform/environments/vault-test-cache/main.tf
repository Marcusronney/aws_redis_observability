terraform {
  required_version = ">= 1.11.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.70.0" }
  }
}
variable "enabled" {
  type    = bool
  default = false
}
variable "auth_token" {
  type      = string
  sensitive = true
  ephemeral = true
}
variable "auth_version" {
  type    = number
  default = 1
}
variable "auth_strategy" {
  type    = string
  default = "ROTATE"
  validation {
    condition     = contains(["ROTATE", "SET"], var.auth_strategy)
    error_message = "Only ROTATE and SET are permitted for the temporary authenticated cache."
  }
}
variable "cost_center" {
  type    = string
  default = "vault-test-before"
}
provider "aws" {
  profile             = "redis-observability-admin"
  region              = "us-east-1"
  allowed_account_ids = ["762012032320"]
}
resource "aws_elasticache_replication_group" "test" {
  count                      = var.enabled ? 1 : 0
  replication_group_id       = "redis-vault-lifecycle-test"
  description                = "Temporary Vault lifecycle acceptance; destroy after test"
  engine                     = "valkey"
  engine_version             = "9.1"
  node_type                  = "cache.t4g.micro"
  num_cache_clusters         = 1
  automatic_failover_enabled = false
  subnet_group_name          = "redis-observability-subnets"
  security_group_ids         = ["sg-0a290bca0ecb019e4"]
  transit_encryption_enabled = true
  at_rest_encryption_enabled = true
  auth_token_wo              = var.auth_token
  auth_token_wo_version      = var.auth_version
  auth_token_update_strategy = var.auth_strategy
  snapshot_retention_limit   = 0
  apply_immediately          = true
  tags = {
    Project            = "redis-observability"
    Environment        = "temporary-test"
    CostCenter         = var.cost_center
    RedisObservability = "vault-lab"
    ManagedBy          = "terraform-vault-test-cache"
  }
}
