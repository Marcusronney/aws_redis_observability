
resource "kubernetes_cron_job_v1" "redis_discovery" {
  metadata {
    name      = "redis-discovery"
    namespace = var.namespace

    labels = {
      "app.kubernetes.io/part-of" = "redis-observability"
    }
  }

  spec {
    schedule                      = "* * * * *"
    concurrency_policy            = "Forbid"
    starting_deadline_seconds     = 45
    successful_jobs_history_limit = 3
    failed_jobs_history_limit     = 5
    suspend = false

    job_template {
      metadata {
        labels = {
          app = "redis-discovery"
        }
      }

      spec {
        backoff_limit            = 1
        active_deadline_seconds  = 120
        ttl_seconds_after_finished = 86400

        template {
          metadata {
            labels = {
              app = "redis-discovery"
            }
          }

          spec {
            service_account_name            = kubernetes_service_account_v1.discovery.metadata[0].name
            automount_service_account_token = true

            restart_policy = "Never"
            

            security_context {
              run_as_non_root = true
              run_as_user     = 10001
              run_as_group    = 10001
            }

            container {
              name              = "discovery"
              image             = "redis-discovery:local-v2"
              image_pull_policy = "IfNotPresent"

              command = [
                "python",
                "/app/discovery/main.py"
              ]

              args = ["--apply"]

              env {
                name  = "REDIS_NAMESPACE"
                value = var.namespace
              }

              env {
                name  = "REDIS_INVENTORY"
                value = "/app/discovery/config/inventory-local.yaml"
              }

              resources {
                requests = {
                  cpu    = "25m"
                  memory = "64Mi"
                }

                limits = {
                  cpu    = "250m"
                  memory = "256Mi"
                }
              }

              security_context {
                allow_privilege_escalation = false
                read_only_root_filesystem  = true

                capabilities {
                  drop = ["ALL"]
                }
              }

              volume_mount {
                name       = "inventory"
                mount_path = "/app/discovery/config"
                read_only  = true
              }
            }

            volume {
              name = "inventory"

              config_map {
                name = kubernetes_config_map_v1.redis_inventory.metadata[0].name
              }
            }
          }
        }
      }
    }
  }
}
