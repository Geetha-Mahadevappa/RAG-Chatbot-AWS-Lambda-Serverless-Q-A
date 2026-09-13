output "s3_bucket_name" {
  value = aws_s3_bucket.docs.bucket
}

output "sagemaker_endpoint_name" {
  value = aws_sagemaker_endpoint.this.name
}

output "qa_function_url" {
  value = aws_lambda_function_url.qa.function_url
}

output "ecr_repository_url" {
  value = aws_ecr_repository.backend.repository_url
}

output "dashboard_url" {
  value = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.this.dashboard_name}"
}

output "cognito_identity_pool_id" {
  description = "Paste into the frontend's 'Cognito Identity Pool ID' field."
  value       = aws_cognito_identity_pool.this.id
}

output "cognito_user_pool_id" {
  description = "Paste into the frontend's 'Cognito User Pool ID' field."
  value       = aws_cognito_user_pool.this.id
}

output "cognito_user_pool_client_id" {
  description = "Paste into the frontend's 'Cognito App Client ID' field."
  value       = aws_cognito_user_pool_client.frontend.id
}

output "ingestion_state_table" {
  value = aws_dynamodb_table.ingestion_state.name
}

output "inference_idempotency_table" {
  value = aws_dynamodb_table.inference_idempotency.name
}
