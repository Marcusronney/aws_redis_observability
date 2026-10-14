
resource "kubernetes_service_account_v1" "discovery" {
  metadata {
    name      = "redis-discovery"
    namespace = var.namespace

    labels = {
      "app.kubernetes.io/part-of" = "redis-observability"
    }
  }

  automount_service_account_token = false
}

resource "kubernetes_role_v1" "discovery" {
  metadata {
    name      = "redis-discovery"
    namespace = var.namespace
  }

  rule {
    api_groups = ["monitoring.coreos.com"]
    resources  = ["scrapeconfigs"]

    verbs = [
      "get",
      "list",
      "watch",
      "create",
      "update",
      "patch",
      "delete"
    ]
  }
}

resource "kubernetes_role_binding_v1" "discovery" {
  metadata {
    name      = "redis-discovery"
    namespace = var.namespace
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.discovery.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.discovery.metadata[0].name
    namespace = var.namespace
  }
}
