variable "region" {
  type = string
  default = "us-east-1"
}
variable "aws_profile" {
  type = string
  default = "redis-observability-admin"
}
variable "workspace_alias" {
  type = string
  default = "redis-observability-central"
}
variable "writer_account_ids" {
  description = "Allowed AWS accounts containing ECS collector task roles; IAM identity permissions are still required."
  type        = set(string)
  default     = ["762012032320"]
  validation {
    condition = alltrue([for id in var.writer_account_ids : can(regex("^[0-9]{12}$", id))])
    error_message = "Each account ID must contain exactly 12 digits."
  }
}
