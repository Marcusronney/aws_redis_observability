variable "region" {
  type = string
}
variable "aws_profile" {
  type = string
  default = "redis-observability-admin"
}
variable "environment_name" {
  type = string
  default = "redis-observability"
}
variable "workspace_arn" {
  type = string
}
variable "controller_principal_arn" {
  description = "ARN of the existing central IAM role that runs the discovery (Roles Anywhere). NOT an IAM user."
  type        = string
}
variable "controller_external_id" {
  description = "Secret external ID is NOT stored here; leave empty for same-account or configure securely for cross-account."
  type = string
  default = ""
}
variable "credential_secret_arns" {
  description = "Pre-existing Secrets Manager ARNs for authenticated Redis targets (credentials are not stored in Terraform)."
  type        = set(string)
  default     = []
}

variable "approved_secret_prefix" {
  description = "All dynamically Vault-synced Redis credentials must be stored under this restricted Secrets Manager prefix (no Terraform per new secret)."
  type        = string
  default     = "redis-observability/"
}
