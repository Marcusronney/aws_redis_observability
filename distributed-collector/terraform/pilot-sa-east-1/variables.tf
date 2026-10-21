variable "region" {
  type = string
  default = "sa-east-1"
}
variable "aws_profile" {
  type = string
  default = "redis-observability-admin"
}
variable "vpc_id" {
  type = string
}
variable "subnet_ids" {
  type = list(string)
}
variable "redis_security_group_id" {
  type = string
}
variable "redis_host" {
  type = string
}
variable "redis_port" {
  type = number
  default = 6379
}
variable "redis_auth" {
  type = bool
  default = false
}
variable "redis_secret_arn" {
  description = "Optional Secrets Manager secret ARN with raw Redis AUTH password, kept out of Terraform state. Provisioned by a Vault sync process when auth=true."
  type        = string
  default     = null
  sensitive   = true
}
variable "cluster_name" {
  type = string
}
variable "execution_role_arn" {
  type = string
}
variable "task_role_arn" {
  type = string
}
variable "log_group_name" {
  type = string
}
variable "amp_remote_write_url" {
  type = string
}
variable "amp_region" {
  type = string
  default = "us-east-1"
}
variable "assign_public_ip" {
  description = "Lab: true ONLY on public subnets with IGW. Production: false on private subnets with NAT or VPC endpoints."
  type        = bool
  default     = true
}
variable "tags_business" {
  type = map(string)
  default = {
    environment   = "homologation"
    cost_center   = "4050"
    business_unit = "technology"
    project       = "aws-redis-observability"
    owner         = "redis-discovery-test"
  }
}
variable "exporter_image" {
  type = string
  default = "ghcr.io/oliver006/redis_exporter:v1.93.0"
}
variable "adot_image" {
  type = string
  default = "public.ecr.aws/aws-observability/aws-otel-collector:latest"
}
