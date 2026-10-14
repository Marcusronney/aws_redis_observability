
variable "kubeconfig_path" {
  description = "Caminho do kubeconfig no servidor Linux"
  type        = string
  default     = "~/.kube/config"
}

variable "kubernetes_context" {
  description = "Contexto Kubernetes utilizado pelo Terraform"
  type        = string
  default     = "kind-redis-monitoring"
}

variable "namespace" {
  description = "Namespace do projeto Redis Observability"
  type        = string
  default     = "redis-observability"
}
