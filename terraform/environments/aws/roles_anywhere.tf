
# Trust Anchor para o Discovery executado fora da AWS.

resource "aws_rolesanywhere_trust_anchor" "redis_discovery" {
  name    = "RedisObservabilityDiscoveryCA"
  enabled = true

  source {
    source_type = "CERTIFICATE_BUNDLE"

    source_data {
      x509_certificate_data = file(
        var.roles_anywhere_ca_cert_path
      )
    }
  }
}

# Profile autorizado a assumir apenas a role
# existente de leitura do ElastiCache.

resource "aws_rolesanywhere_profile" "redis_discovery" {
  name             = "RedisObservabilityDiscoveryProfile"
  enabled          = true
  duration_seconds = 3600

  role_arns = [
    aws_iam_role.redis_discovery.arn
  ]
}
