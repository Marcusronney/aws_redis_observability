

# --------------------------------------------------
# 1. TRUST POLICY
# Quem pode assumir a Role de Discovery
# --------------------------------------------------

data "aws_iam_policy_document" "discovery_trust" {
  statement {
    sid    = "AllowSSOAdminForBootstrap"
    effect = "Allow"

    actions = [
      "sts:AssumeRole"
    ]

    principals {
      type = "AWS"

      identifiers = [
        var.admin_sso_role_arn
      ]
    }
  }

  
  statement {
    sid    = "AllowRolesAnywhereDiscovery"
    effect = "Allow"

    actions = [
      "sts:AssumeRole",
      "sts:TagSession",
      "sts:SetSourceIdentity"
    ]

    principals {
      type = "Service"

      identifiers = [
        "rolesanywhere.amazonaws.com"
      ]
    }

    # Aceita somente o Trust Anchor criado
    # por este projeto.

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"

      values = [
        aws_rolesanywhere_trust_anchor.redis_discovery.arn
      ]
    }

    # Limita a conta AWS autorizada.

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"

      values = [
        var.aws_account_id
      ]
    }

    # Somente certificados com este CN
    # poderao assumir a role.

    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalTag/x509Subject/CN"

      values = [
        "redis-observability-discovery"
      ]
    }
  }

}

# --------------------------------------------------
# 2. IAM ROLE
# Identidade utilizada para descoberta
# --------------------------------------------------

resource "aws_iam_role" "redis_discovery" {
  name = "RedisObservabilityDiscoveryRole"

  description = "Permissoes de discovery para Amazon ElastiCache Redis e Valkey"

  assume_role_policy = data.aws_iam_policy_document.discovery_trust.json

  max_session_duration = 3600

  tags = {
    Component = "discovery"
  }
}

# --------------------------------------------------
# 3. IAM POLICY
# Permissoes somente de leitura do ElastiCache
# --------------------------------------------------

data "aws_iam_policy_document" "discovery_permissions" {
  statement {
    sid       = "ReadSubnetVpcForLocalRouting"
    effect    = "Allow"
    actions   = ["ec2:DescribeSubnets"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = ["us-east-1", "sa-east-1"]
    }
  }
  statement {
    sid    = "ReadElastiCacheInventory"
    effect = "Allow"

    actions = [
      "elasticache:DescribeCacheClusters",
      "elasticache:DescribeCacheSubnetGroups",
      "elasticache:DescribeReplicationGroups",
      "elasticache:DescribeServerlessCaches",
      "elasticache:ListTagsForResource"
    ]

    resources = ["*"]
  }
}

resource "aws_iam_policy" "redis_discovery" {
  name = "RedisObservabilityDiscoveryPolicy"

  description = "Permissoes read-only para discovery de Redis e Valkey"

  policy = data.aws_iam_policy_document.discovery_permissions.json

  tags = {
    Component = "discovery"
  }
}

# --------------------------------------------------
# 4. ATTACHMENT
# Associa Policy a Role
# --------------------------------------------------

resource "aws_iam_role_policy_attachment" "redis_discovery" {
  role       = aws_iam_role.redis_discovery.name
  policy_arn = aws_iam_policy.redis_discovery.arn
}
