terraform {
  required_version = ">= 1.5.0"
  required_providers {
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.38" }
  }
}

variable "active" {
  type    = bool
  default = false
}
variable "kube_config_path" {
  type    = string
  default = "~/.kube/config"
}
variable "kube_context" {
  type    = string
  default = "kind-redis-monitoring"
}
variable "namespace" {
  type    = string
  default = "redis-vault-lab"
  validation {
    condition     = length(var.namespace) <= 63 && can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", var.namespace))
    error_message = "Use a Kubernetes namespace DNS label of at most 63 characters."
  }
}
variable "controller_image" {
  type    = string
  default = "redis-discovery-aws:vault-test"
}
variable "exporter_replicas" {
  type    = number
  default = 1
  validation {
    condition     = var.exporter_replicas >= 1 && floor(var.exporter_replicas) == var.exporter_replicas
    error_message = "Use at least one exporter replica when active."
  }
}
variable "python_executable" {
  type    = string
  default = "python3"
}
variable "session_directory" {
  type    = string
  default = "/root/redis-vault-session"
}
variable "accounts_file" {
  type    = string
  default = "discovery/config/vault-local-accounts.yaml"
}
variable "routing_file" {
  type    = string
  default = "discovery/config/vault-local-routing.yaml"
}
variable "gateway_instance_id" {
  description = "Optional existing WireGuard EC2 to start on ON and stop on OFF. Empty leaves networking unmanaged."
  type        = string
  default     = ""
  validation {
    condition     = var.gateway_instance_id == "" || can(regex("^i-[0-9a-f]+$", var.gateway_instance_id))
    error_message = "Use an EC2 instance ID or an empty string."
  }
}
variable "gateway_region" {
  type    = string
  default = "us-east-1"
}
variable "gateway_account_id" {
  type    = string
  default = "762012032320"
}
variable "gateway_aws_profile" {
  type    = string
  default = "redis-observability-admin"
}
provider "kubernetes" {
  config_path    = pathexpand(var.kube_config_path)
  config_context = var.kube_context
}

resource "kubernetes_namespace_v1" "lab" {
  metadata {
    name   = var.namespace
    labels = { monitoring = "redis-observability" }
  }
  lifecycle {
    prevent_destroy = true
  }
}

locals {
  namespace = kubernetes_namespace_v1.lab.metadata[0].name
  resources = yamldecode(templatefile("${path.module}/resources.yaml.tftpl", {
    namespace         = var.namespace
    replicas          = var.active ? 1 : 0
    exporter_replicas = var.active ? var.exporter_replicas : 0
    suspend           = !var.active
    controller_image  = var.controller_image
  })).items
}

# Only paths/flags are stored here. Vault material and credential Secret data
# never enter a Terraform resource or data source.
resource "terraform_data" "runtime" {
  triggers_replace = [
    var.active, var.python_executable, var.session_directory, var.kube_context, var.namespace,
    var.gateway_instance_id, var.gateway_region, var.gateway_account_id, var.gateway_aws_profile,
    filesha256("${path.module}/../../../${var.accounts_file}"),
    filesha256("${path.module}/../../../discovery/config/account-catalog.yaml"),
    filesha256("${path.module}/../../../${var.routing_file}"),
    filesha256("${path.module}/../../../scripts/vault_runtime.py"),
    filesha256("${path.module}/../../../scripts/vault_lab_bootstrap.py")
  ]
  depends_on = [kubernetes_manifest.lab]
  provisioner "local-exec" {
    working_dir = "${path.module}/../../.."
    command     = "\"$RUNTIME_PYTHON\" scripts/vault_runtime.py"
    environment = {
      RUNTIME_PYTHON          = var.python_executable
      RUNTIME_ACTIVE          = tostring(var.active)
      RUNTIME_SESSION         = var.session_directory
      RUNTIME_ACCOUNTS        = var.accounts_file
      RUNTIME_ROUTING         = var.routing_file
      RUNTIME_GATEWAY_ID      = var.gateway_instance_id
      RUNTIME_GATEWAY_REGION  = var.gateway_region
      RUNTIME_GATEWAY_ACCOUNT = var.gateway_account_id
      RUNTIME_AWS_PROFILE     = var.gateway_aws_profile
      KUBECONFIG              = pathexpand(var.kube_config_path)
      RUNTIME_KUBE_CONTEXT    = var.kube_context
      RUNTIME_NAMESPACE       = var.namespace
    }
  }
}
resource "kubernetes_manifest" "lab" {
  for_each   = { for item in local.resources : "${item.kind}/${item.metadata.name}" => item }
  manifest   = each.value
  depends_on = [kubernetes_namespace_v1.lab]
  field_manager {
    name            = "redis-vault-lab-terraform"
    force_conflicts = true
  }
  lifecycle {
    prevent_destroy = true
  }
}

output "mode" {
  value = var.active ? "ON: Vault/exporters and discovery enabled" : "OFF: discovery suspended, deployments at zero; PVC retained"
}
