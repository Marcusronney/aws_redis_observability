resource "aws_prometheus_workspace" "redis" {
  alias = var.workspace_alias
  tags = {
    Project   = "aws-redis-observability"
    ManagedBy = "Terraform"
  }
}

data "aws_iam_policy_document" "writers" {
  statement {
    sid       = "AllowApprovedAccountsToWriteMetrics"
    actions   = ["aps:RemoteWrite"]
    resources = [aws_prometheus_workspace.redis.arn]
    principals {
      type        = "AWS"
      identifiers = [for id in sort(tolist(var.writer_account_ids)) : "arn:aws:iam::${id}:root"]
    }
  }
}

resource "aws_prometheus_resource_policy" "writers" {
  workspace_id    = aws_prometheus_workspace.redis.id
  policy_document = data.aws_iam_policy_document.writers.json
}
