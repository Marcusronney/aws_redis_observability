

variable "aws_region" {
  description = "Regiao AWS do laboratorio"
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "Perfil SSO usado pelo Terraform"
  type        = string
  default     = "redis-observability-admin"
}

variable "aws_account_id" {
  description = "ID da conta AWS de laboratorio"
  type        = string
  default     = "762012032320"
}

variable "admin_sso_role_arn" {
  description = "ARN IAM da role administrativa criada pelo Identity Center"
  type        = string

  validation {
    condition = startswith(
      var.admin_sso_role_arn,
      "arn:aws:iam::762012032320:role/"
    )

    error_message = "Informe o ARN IAM da role SSO da conta Estudos."
  }
}
