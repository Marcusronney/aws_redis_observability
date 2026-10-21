data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "ecs_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "controller_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type = "AWS"
      identifiers = [var.controller_principal_arn]
    }
    dynamic "condition" {
      for_each = var.controller_external_id == "" ? [] : [var.controller_external_id]
      content {
        test     = "StringEquals"
        variable = "sts:ExternalId"
        values   = [condition.value]
      }
    }
  }
}

resource "aws_ecs_cluster" "collectors" {
  name = "${var.environment_name}-distributed"
  setting {
    name = "containerInsights"
    value = "enabled"
  }
  tags = { Project = var.environment_name, ManagedBy = "Terraform" }
}

resource "aws_cloudwatch_log_group" "collector" {
  name              = "/ecs/${var.environment_name}-distributed"
  retention_in_days = 7
  tags              = { Project = var.environment_name }
}

resource "aws_iam_role" "execution" {
  name               = "${var.environment_name}-ecs-execution-${var.region}"
  assume_role_policy = data.aws_iam_policy_document.ecs_trust.json
}
resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "exec_secret" {
  role = aws_iam_role.execution.id
  name = "ReadApprovedRedisCredentials"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = concat(sort(tolist(var.credential_secret_arns)), [
        "arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:${var.approved_secret_prefix}*"
      ])
    }]
  })
}

resource "aws_iam_role" "task" {
  name               = "${var.environment_name}-ecs-task-${var.region}"
  assume_role_policy = data.aws_iam_policy_document.ecs_trust.json
}
resource "aws_iam_role_policy" "write_metrics" {
  role = aws_iam_role.task.id
  name = "SendMetricsToCentralAMP"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["aps:RemoteWrite"], Resource = var.workspace_arn }]
  })
}

resource "aws_iam_role" "controller" {
  name               = "${var.environment_name}-controller-${var.region}"
  assume_role_policy = data.aws_iam_policy_document.controller_trust.json
}

# The controller creates per-Redis runtime ECS services and per-VPC collector SGs.
# Unlike Terraform bootstrap infrastructure, these are continuously reconciled operational resources.
# This policy must be reviewed/limited with IAM boundaries/SCP in production.
resource "aws_iam_role_policy" "controller" {
  role = aws_iam_role.controller.id
  name = "DistributedRedisCollectorReconciliation"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["elasticache:DescribeServerlessCaches", "elasticache:DescribeReplicationGroups", "elasticache:DescribeCacheClusters", "elasticache:DescribeCacheSubnetGroups", "elasticache:ListTagsForResource", "ec2:DescribeVpcs", "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups", "ec2:DescribeRouteTables", "ec2:DescribeNetworkInterfaces", "ecs:DescribeServices", "ecs:ListServices", "ecs:DescribeTaskDefinition", "ecs:ListTaskDefinitions", "ecs:DescribeClusters", "logs:DescribeLogGroups"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:RegisterTaskDefinition", "ecs:DeregisterTaskDefinition", "ecs:CreateService", "ecs:UpdateService", "ecs:DeleteService", "ecs:TagResource", "ec2:CreateSecurityGroup", "ec2:CreateTags", "ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress", "ec2:DeleteSecurityGroup"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = ["secretsmanager:DescribeSecret"]
        Resource = "arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:${var.approved_secret_prefix}*"
      },
      {
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [aws_iam_role.task.arn, aws_iam_role.execution.arn]
        Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
      }
    ]
  })
}

# Tracks successive missing discovery cycles. Prune is opt-in; it needs 2 complete runs.
resource "aws_dynamodb_table" "collector_state" {
  name         = "${var.environment_name}-collector-state-${var.region}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "service"
  attribute {
    name = "service"
    type = "S"
  }
  point_in_time_recovery { enabled = true }
  tags = { Project = var.environment_name, ManagedBy = "Terraform" }
}
resource "aws_iam_role_policy" "controller_state" {
  role = aws_iam_role.controller.id
  name = "ReadWriteCollectorState"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"], Resource = aws_dynamodb_table.collector_state.arn }]
  })
}
