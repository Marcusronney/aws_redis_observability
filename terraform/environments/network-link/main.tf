terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}
variable "source_region" { type = string }
variable "peer_region" { type = string }
variable "profile" { type = string }
variable "account_id" { type = string }
variable "peer_account_id" {
  type    = string
  default = null
}
variable "peer_role_arn" {
  type    = string
  default = null
}
variable "source_vpc" { type = string }
variable "peer_vpc" { type = string }
variable "gateway_ip" { type = string }
variable "source_route_table" { type = string }
variable "peer_route_tables" { type = set(string) }
variable "peer_cidrs" { type = set(string) }
variable "security_groups" { type = set(string) }
provider "aws" {
  region              = var.source_region
  profile             = var.profile
  allowed_account_ids = [var.account_id]
}
provider "aws" {
  alias               = "peer"
  region              = var.peer_region
  profile             = var.profile
  allowed_account_ids = [coalesce(var.peer_account_id, var.account_id)]
  dynamic "assume_role" {
    for_each = var.peer_role_arn == null ? [] : [var.peer_role_arn]
    content { role_arn = assume_role.value }
  }
}
resource "aws_vpc_peering_connection" "link" {
  vpc_id        = var.source_vpc
  peer_vpc_id   = var.peer_vpc
  peer_region   = var.peer_region
  peer_owner_id = coalesce(var.peer_account_id, var.account_id)
  tags          = { ManagedBy = "redis-network-automation" }
}
resource "aws_vpc_peering_connection_accepter" "link" {
  provider                  = aws.peer
  vpc_peering_connection_id = aws_vpc_peering_connection.link.id
  auto_accept               = true
  tags                      = { ManagedBy = "redis-network-automation" }
}
resource "aws_route" "outbound" {
  for_each                  = var.peer_cidrs
  route_table_id            = var.source_route_table
  destination_cidr_block    = each.value
  vpc_peering_connection_id = aws_vpc_peering_connection_accepter.link.id
}
resource "aws_route" "return" {
  provider                  = aws.peer
  for_each                  = var.peer_route_tables
  route_table_id            = each.value
  destination_cidr_block    = "${var.gateway_ip}/32"
  vpc_peering_connection_id = aws_vpc_peering_connection_accepter.link.id
}
resource "aws_vpc_security_group_ingress_rule" "redis" {
  provider          = aws.peer
  for_each          = var.security_groups
  security_group_id = each.value
  cidr_ipv4         = "${var.gateway_ip}/32"
  ip_protocol       = "tcp"
  from_port         = 6379
  to_port           = 6379
  description       = "Redis monitoring through central WireGuard SNAT"
  tags              = { ManagedBy = "redis-network-automation" }
}
output "peering_id" { value = aws_vpc_peering_connection_accepter.link.id }
