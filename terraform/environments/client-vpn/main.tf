terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region              = "sa-east-1"
  profile             = var.aws_profile
  allowed_account_ids = ["762012032320"]
  default_tags {
    tags = {
      Project   = "aws-redis-observability"
      ManagedBy = "terraform-client-vpn-lab"
    }
  }
}

variable "aws_profile" {
  type    = string
  default = "redis-observability-admin"
}
variable "server_certificate_arn" {
  type        = string
  description = "ACM server certificate imported outside Terraform; private keys never enter state."
}
variable "client_root_certificate_chain_arn" {
  type        = string
  description = "ACM certificate ARN for the CA chain authenticating the exclusive Rocky client."
}
variable "client_cidr" {
  type        = string
  description = "Validate against host, Docker, Pods, Services, WireGuard and AWS networks before apply."
  default     = "172.28.240.0/22"
}
variable "client_public_cidr" {
  type        = string
  description = "Public IPv4 /32 of the Rocky network, permitted only to the VPN transport port."
  validation {
    condition     = can(cidrhost(var.client_public_cidr, 0)) && endswith(var.client_public_cidr, "/32")
    error_message = "Use only the observed public IPv4 /32, never a public catch-all rule."
  }
}
variable "transport_protocol" {
  type        = string
  default     = "tcp"
  description = "TCP 443 is preferable when the client network filters UDP. Changing it replaces the pilot endpoint."
  validation {
    condition     = contains(["tcp", "udp"], var.transport_protocol)
    error_message = "Use tcp or udp."
  }
}

data "aws_vpc" "existing" {
  id = "vpc-0eb95c9e597551a70"
}
data "aws_subnet" "association" {
  id = "subnet-00b359872fbcc41ac"
}

resource "aws_security_group" "vpn" {
  name_prefix = "redis-client-vpn-lab-"
  description = "Exclusive Client VPN lab security group"
  vpc_id      = data.aws_vpc.existing.id
}

resource "aws_vpc_security_group_ingress_rule" "vpn_transport" {
  security_group_id = aws_security_group.vpn.id
  cidr_ipv4         = var.client_public_cidr
  ip_protocol       = var.transport_protocol
  from_port         = 443
  to_port           = 443
  description       = "VPN transport from the exclusive Rocky network"
}

resource "aws_vpc_security_group_egress_rule" "redis" {
  security_group_id            = aws_security_group.vpn.id
  referenced_security_group_id = "sg-0ec65f2b300695795"
  ip_protocol                  = "tcp"
  from_port                    = 6379
  to_port                      = 6379
}

resource "aws_vpc_security_group_ingress_rule" "redis" {
  security_group_id            = "sg-0ec65f2b300695795"
  referenced_security_group_id = aws_security_group.vpn.id
  ip_protocol                  = "tcp"
  from_port                    = 6379
  to_port                      = 6379
  description                  = "Redis observability Client VPN lab only"
}

resource "aws_cloudwatch_log_group" "vpn" {
  name              = "/redis-observability/client-vpn-lab"
  retention_in_days = 7
}
resource "aws_cloudwatch_log_stream" "vpn" {
  name           = "connections"
  log_group_name = aws_cloudwatch_log_group.vpn.name
}

resource "aws_ec2_client_vpn_endpoint" "lab" {
  description            = "Redis observability Sao Paulo lab"
  server_certificate_arn = var.server_certificate_arn
  client_cidr_block      = var.client_cidr
  split_tunnel           = true
  transport_protocol     = var.transport_protocol
  vpn_port               = 443
  vpc_id                 = data.aws_vpc.existing.id
  security_group_ids     = [aws_security_group.vpn.id]
  session_timeout_hours  = 8
  authentication_options {
    type                       = "certificate-authentication"
    root_certificate_chain_arn = var.client_root_certificate_chain_arn
  }
  connection_log_options {
    enabled               = true
    cloudwatch_log_group  = aws_cloudwatch_log_group.vpn.name
    cloudwatch_log_stream = aws_cloudwatch_log_stream.vpn.name
  }
  lifecycle {
    precondition {
      condition     = data.aws_subnet.association.vpc_id == data.aws_vpc.existing.id
      error_message = "Association subnet must belong to the existing VPC."
    }
  }
}

# The VPC route is created automatically by the single subnet association.
# Do not add a duplicate route, default route, NAT, EC2 or Transit Gateway.
resource "aws_ec2_client_vpn_network_association" "lab" {
  client_vpn_endpoint_id = aws_ec2_client_vpn_endpoint.lab.id
  subnet_id              = data.aws_subnet.association.id
}
resource "aws_ec2_client_vpn_authorization_rule" "vpc" {
  client_vpn_endpoint_id = aws_ec2_client_vpn_endpoint.lab.id
  target_network_cidr    = data.aws_vpc.existing.cidr_block
  authorize_all_groups   = true
  description            = "Only the pilot VPC; Redis access further restricted by SG"
}
output "endpoint_id" {
  value = aws_ec2_client_vpn_endpoint.lab.id
}
output "vpn_security_group_id" {
  value = aws_security_group.vpn.id
}
