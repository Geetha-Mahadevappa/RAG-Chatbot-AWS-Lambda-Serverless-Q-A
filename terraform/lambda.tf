# NOTE: AWS_REGION is a Lambda *reserved* environment variable name -- Lambda sets it
# automatically and rejects any attempt to set it yourself (`terraform apply` would fail with
# an InvalidParameterValueException). It's deliberately omitted from both `environment`
# blocks below; backend code reads it via os.environ with a fallback default, so this is
# transparent at runtime.

resource "aws_cloudwatch_log_group" "qa" {
  name              = "/aws/lambda/${var.project_name}-qa"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${var.project_name}-ingest"
  retention_in_days = 7
}

resource "aws_lambda_function" "qa" {
  function_name = "${var.project_name}-qa"
  role          = aws_iam_role.qa_lambda_execution.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  timeout       = 60
  memory_size   = 3008

  image_config {
    command = ["lambda_handler.lambda_handler"]
  }

  environment {
    variables = {
      SAGEMAKER_ENDPOINT_NAME = local.sagemaker_endpoint_name
      QDRANT_URL              = var.qdrant_url
      QDRANT_API_KEY          = var.qdrant_api_key
      QDRANT_COLLECTION_NAME  = var.qdrant_collection_name
      IDEMPOTENCY_TABLE       = aws_dynamodb_table.inference_idempotency.name
    }
  }

  depends_on = [null_resource.docker_build_push, aws_cloudwatch_log_group.qa]
}

# Private: AuthType=AWS_IAM means only SigV4-signed requests from a principal with
# lambda:InvokeFunctionUrl + lambda:InvokeFunction on this function's ARN are accepted (see
# terraform/cognito.tf for the only principal actually granted that -- the unauthenticated
# Cognito role the static frontend assumes). No aws_lambda_permission resource is needed here:
# same-account AWS_IAM access is governed entirely by the caller's own identity-based policy.
resource "aws_lambda_function_url" "qa" {
  function_name      = aws_lambda_function.qa.function_name
  authorization_type = "AWS_IAM"

  cors {
    allow_origins = ["*"]
    allow_methods = ["POST"]
    # content-type for the JSON body; the rest are added by SigV4 signing (aws4fetch, see
    # frontend/index.html) -- CORS preflight rejects the request if these aren't allowed.
    allow_headers = ["content-type", "authorization", "x-amz-date", "x-amz-security-token", "x-amz-content-sha256"]
  }
}

resource "aws_lambda_function" "ingest" {
  function_name = "${var.project_name}-ingest"
  role          = aws_iam_role.ingest_lambda_execution.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  timeout       = 60
  memory_size   = 3008

  image_config {
    command = ["ingest_handler.lambda_handler"]
  }

  environment {
    variables = {
      QDRANT_URL             = var.qdrant_url
      QDRANT_API_KEY         = var.qdrant_api_key
      QDRANT_COLLECTION_NAME = var.qdrant_collection_name
      INGESTION_STATE_TABLE  = aws_dynamodb_table.ingestion_state.name
    }
  }

  depends_on = [null_resource.docker_build_push, aws_cloudwatch_log_group.ingest]
}
