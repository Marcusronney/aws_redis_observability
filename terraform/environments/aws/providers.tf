

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      Project     = "aws-redis-observability"
      Environment = "lab"
      ManagedBy   = "terraform"
      CostCenter  = "1050"
    }
  }
}
