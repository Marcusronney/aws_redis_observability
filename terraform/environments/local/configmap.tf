
resource "kubernetes_config_map_v1" "redis_inventory" {
  metadata {
    name      = "redis-discovery-inventory"
    namespace = var.namespace

    labels = {
      "app.kubernetes.io/part-of" = "redis-observability"
    }
  }

  data = {
    "inventory-local.yaml" = file(
      "${path.module}/../../../discovery/config/inventory-local.yaml"
    )
  }
}
