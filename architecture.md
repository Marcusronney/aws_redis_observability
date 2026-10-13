# Architecture contract

Terraform owns static cluster resources; the Python controller will own dynamically generated ScrapeConfig objects.

Discovery inventory is scoped by configured account IDs and regions; Redis targets are never manually enumerated in Terraform.

Planned guardrails: full-scan success before deleting stale targets, per-target Vault credential mapping, label allowlist, no secrets in metrics, and eventual consistency measurement against the 5-minute SLO.
