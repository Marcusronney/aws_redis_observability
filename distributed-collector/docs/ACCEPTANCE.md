# Acceptance matrix — runtime evidence

| Test | Acceptance | State in this package |
|--|--|--|
| A1 | AWS discovery sees sa-east-1 and us-east-1 | Already demonstrated from previous 2-ScrapeConfig run |
| A2 | Existing Kind Prometheus continues UP for us-east-1 | Must verify after applying, no changes made |
| A3 | Terraform AMP backend created | Files provided, APPLY NOT EXECUTED |
| A4 | Terraform ECS regional bootstrap created | Files provided, APPLY NOT EXECUTED |
| A5 | Terraform pilot task starts in VPC Sao Paulo | Files provided, APPLY NOT EXECUTED |
| A6 | `query_amp.py`: `redis_up{job="redis-aws-distributed",aws_region="sa-east-1"} == 1` | PENDING live AWS test |
| A7 | New Serverless created in onboarded region/VPC -> ECS service without manual Terraform | Controller provided, PENDING live AWS test |
| A8 | Business labels project, environment, cost_center, business_unit | Included in ADOT config; PENDING live AMP test |
| A9 | Existing Fargate collector unchanged on 2 consecutive runs | Idempotent task family and service logic; PENDING live AWS test |
| A10 | Removed cache -> collector deleted after 2 complete cycles | DynamoDB state logic unit tested; PENDING live deletion test |
| A11 | Other AWS account -> task role sends cross-account AMP remote-write | Terraform AMP policy + IAM role logic; PENDING second AWS account |
| A12 | AUTH credentials from Vault rotated without exposure | PENDING Vault system/identity integration |
| A13 | No NAT or egress available in target VPC | **NOT AUTOMATIC**: provision network or use suitable endpoints |
| A14 | Same browser `192.168.3.55:19090` shows AMP samples | **NOT IMPLEMENTED**: AMP is separate Prometheus-compatible backend |

Document all live acceptance evidence with timestamps/screenshots/CloudWatch logs.
