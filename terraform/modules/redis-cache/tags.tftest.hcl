mock_provider "aws" {}
override_data {
  target = data.aws_subnet.selected["subnet-a"]
  values = { vpc_id = "vpc-test", availability_zone = "us-east-2a" }
}
override_data {
  target = data.aws_subnet.selected["subnet-b"]
  values = { vpc_id = "vpc-test", availability_zone = "us-east-2b" }
}
variables {
  name        = "redis-test"
  vpc_id      = "vpc-test"
  subnet_ids  = ["subnet-a", "subnet-b"]
  project     = "observability"
  environment = "lab"
  cost_center = "1050"
}
run "required_tags_cannot_be_overridden" {
  command = plan
  variables { extra_tags = { ManagedBy = "console", RedisObservability = "wrong", Project = "wrong" } }
  assert {
    condition     = output.tags.ManagedBy == "terraform" && output.tags.RedisObservability == "vault-lab" && output.tags.Project == "observability"
    error_message = "Required tags must win over extra tags."
  }
  assert {
    condition     = aws_elasticache_replication_group.this.transit_encryption_enabled && aws_elasticache_replication_group.this.num_cache_clusters == 1
    error_message = "Lab must use TLS and one node by default."
  }
}
run "empty_business_tag_rejected" {
  command = plan
  variables { cost_center = " " }
  expect_failures = [aws_elasticache_replication_group.this]
}
