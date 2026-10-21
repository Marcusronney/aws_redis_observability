apiVersion: batch/v1
kind: CronJob
metadata:
  name: redis-distributed-controller
  namespace: redis-observability
  labels:
    app.kubernetes.io/part-of: redis-observability
spec:
  schedule: "*/5 * * * *"
  concurrencyPolicy: Forbid
  suspend: true # IMPORTANT: activate only after terraform bootstrap, config validation and dry-run
  successfulJobsHistoryLimit: 2
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 0
      activeDeadlineSeconds: 240
      ttlSecondsAfterFinished: 7200
      template:
        spec:
          serviceAccountName: redis-discovery-aws
          restartPolicy: Never
          automountServiceAccountToken: true
          securityContext:
            runAsUser: 10001
            runAsGroup: 10001
            fsGroup: 10001
            runAsNonRoot: true
          containers:
            - name: controller
              image: redis-discovery-aws:distributed-v1
              imagePullPolicy: IfNotPresent
              command: ["sh", "-ec"]
              args:
                - |
                  python -m discovery.aws_discovery > /tmp/inventory.json
                  python /app/distributed_collector/reconcile.py \
                    --config /etc/distributed/controller-config.yaml \
                    --inventory /tmp/inventory.json \
                    --apply
                  # DO NOT add --prune until DynamoDB and rollback tests are verified.
              env:
                - name: REDIS_AWS_PROFILE
                  value: redis-discovery-rolesanywhere
                - name: AWS_CONFIG_FILE
                  value: /etc/aws/config
                - name: AWS_EC2_METADATA_DISABLED
                  value: "true"
              resources:
                requests:
                  cpu: 50m
                  memory: 96Mi
                limits:
                  cpu: 500m
                  memory: 384Mi
              securityContext:
                allowPrivilegeEscalation: false
                readOnlyRootFilesystem: true
                capabilities: {drop: ["ALL"]}
              volumeMounts:
                - {name: aws-config, mountPath: /etc/aws, readOnly: true}
                - {name: certificate, mountPath: /etc/rolesanywhere, readOnly: true}
                - {name: distributed-config, mountPath: /etc/distributed, readOnly: true}
                - {name: tmp, mountPath: /tmp}
          volumes:
            - name: aws-config
              configMap: {name: redis-discovery-aws-config}
            - name: certificate
              secret: {secretName: redis-discovery-aws-cert, defaultMode: 288}
            - name: distributed-config
              configMap: {name: redis-distributed-controller-config}
            - name: tmp
              emptyDir: {}
