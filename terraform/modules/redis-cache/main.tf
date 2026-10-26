terraform {
  required_version = ">= 1.11.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.70.0" }
  }
}
variable "name" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,38}[a-z0-9]$", var.name)) && !strcontains(var.name, "--")
    error_message = "Use 2 to 40 lowercase characters, start with a letter and avoid consecutive hyphens."
  }
}
variable "vpc_id" { type = string }
variable "subnet_ids" {
  type = set(string)
  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "Provide at least two subnets in distinct AZs."
  }
}
variable "project" { type = string }
variable "environment" { type = string }
variable "cost_center" { type = string }
variable "replicas" {
  type    = number
  default = 0
  validation {
    condition     = var.replicas >= 0 && var.replicas <= 5 && floor(var.replicas) == var.replicas
    error_message = "Use 0 to 5 replicas."
  }
}
variable "auth_enabled" { default = false }
variable "auth_token" {
  type      = string
  sensitive = true
  ephemeral = true
  default   = null
  validation {
    condition     = !var.auth_enabled || can(regex("^[A-Za-z0-9]{20,128}$", var.auth_token))
    error_message = "When AUTH is enabled, provide 20 to 128 alphanumeric characters through TF_VAR_auth_token."
  }
}
variable "auth_version" {
  type    = number
  default = 1
}
variable "extra_tags" {
  type    = map(string)
  default = {}
}
locals {
  tags = merge(var.extra_tags, {
    Project            = var.project
    Environment        = var.environment
    CostCenter         = var.cost_center
    RedisObservability = "vault-lab"
    ManagedBy          = "terraform"
  })
}
data "aws_subnet" "selected" {
  for_each = var.subnet_ids
  id       = each.value
}
resource "aws_elasticache_subnet_group" "this" {
  name       = var.name
  subnet_ids = var.subnet_ids
  tags       = local.tags
}
resource "aws_security_group" "this" {
  name_prefix = "${var.name}-"
  description = "Redis: monitoring ingress managed by network reconciliation"
  vpc_id      = var.vpc_id
  tags        = local.tags
  lifecycle { create_before_destroy = true }
}
resource "aws_elasticache_replication_group" "this" {
  replication_group_id       = var.name
  description                = "Redis observability - managed exclusively through Terraform"
  engine                     = "valkey"
  engine_version             = "9.1"
  node_type                  = "cache.t4g.micro"
  num_cache_clusters         = 1 + var.replicas
  automatic_failover_enabled = var.replicas > 0
  multi_az_enabled           = var.replicas > 0
  subnet_group_name          = aws_elasticache_subnet_group.this.name
  security_group_ids         = [aws_security_group.this.id]
  port                       = 6379
  transit_encryption_enabled = true
  at_rest_encryption_enabled = true
  auth_token_wo              = var.auth_enabled ? var.auth_token : null
  auth_token_wo_version      = var.auth_enabled ? var.auth_version : null
  auth_token_update_strategy = var.auth_enabled ? "SET" : null
  snapshot_retention_limit   = 0
  apply_immediately          = true
  tags                       = local.tags
  lifecycle {
    precondition {
      condition     = alltrue([for s in data.aws_subnet.selected : s.vpc_id == var.vpc_id]) && length(toset([for s in data.aws_subnet.selected : s.availability_zone])) >= 2
      error_message = "All subnets must belong to the selected VPC and cover at least two AZs."
    }
    precondition {
      condition     = alltrue([for v in [var.project, var.environment, var.cost_center] : length(trimspace(v)) > 0])
      error_message = "Project, Environment and CostCenter must be nonempty."
    }
  }
}
output "arn" { value = aws_elasticache_replication_group.this.arn }
output "name" { value = aws_elasticache_replication_group.this.id }
output "tags" { value = local.tags }
