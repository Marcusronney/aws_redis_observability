# Kubernetes Deployment / Git workflow

Use `redis-discovery-aws:distributed-v1` in addition to existing `redis-discovery-aws:local-v4`.
Existing CronJob `redis-discovery-aws` and local Prometheus remain unchanged.

1. Copy `distributed-collector/` into Git repository root.
2. Edit `controller/config.example.yaml` with Terraform outputs; do not include secrets.
3. `docker build -f distributed-collector/kubernetes/Dockerfile.distributed -t redis-discovery-aws:distributed-v1 .`
4. `kind load docker-image redis-discovery-aws:distributed-v1 --name redis-monitoring`
5. `cp distributed-collector/controller/config.yaml distributed-collector/kubernetes/distributed-controller/controller-config.yaml` (keep untracked).
6. `kubectl apply -k distributed-collector/kubernetes/distributed-controller/` (creates ConfigMap and **suspended** CronJob).
7. Keep CronJob suspended; test standalone Python dry-run from Rocky first:
   `python distributed-collector/controller/reconcile.py --config distributed-collector/controller/config.yaml --inventory /tmp/aws-inventory-test.json`
8. To invoke a one-off dry-run in cluster: `kubectl create job --from=cronjob/redis-distributed-controller redis-distributed-preview -n redis-observability` BUT CRONJOB args have --apply. **DO NOT do this before Terraform bootstrap.** For safe preview use local Python CLI only.
9. When ready: `kubectl patch cronjob redis-distributed-controller -n redis-observability -p '{"spec":{"suspend":false}}'`.

**Important:** A new controller-managed ECS Service for `redis-observability-sa-test` would
coexist with the Terraform pilot service and duplicate metrics. Destroy the pilot
Terraform stack after end-to-end validation and BEFORE enabling this controller.

`--prune` deletes collector ECS Services only after two separate successful region-wide
inventories; it never deletes Redis, SGs or Terraform resources. Leave prune OFF until
its DynamoDB state and operational rollback have been tested.
