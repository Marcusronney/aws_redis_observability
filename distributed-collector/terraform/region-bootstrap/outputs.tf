output "region" { value = var.region }
output "account_id" { value = data.aws_caller_identity.current.account_id }
output "cluster_name" { value = aws_ecs_cluster.collectors.name }
output "execution_role_arn" { value = aws_iam_role.execution.arn }
output "task_role_arn" { value = aws_iam_role.task.arn }
output "controller_role_arn" { value = aws_iam_role.controller.arn }
output "log_group" { value = aws_cloudwatch_log_group.collector.name }
output "state_table" { value = aws_dynamodb_table.collector_state.name }
