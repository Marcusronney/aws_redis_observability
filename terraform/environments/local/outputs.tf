
output "discovery_service_account" {
  description = "ServiceAccount utilizada pelo discovery Python"
  value       = kubernetes_service_account_v1.discovery.metadata[0].name
}

output "namespace" {
  description = "Namespace do projeto"
  value       = var.namespace
}
