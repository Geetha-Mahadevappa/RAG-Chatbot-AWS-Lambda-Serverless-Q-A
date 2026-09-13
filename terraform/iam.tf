# Three roles, each scoped to only what that resource needs: SageMaker itself, the Q&A
# Lambda (invoke one endpoint, nothing else), and the ingestion Lambda (read one bucket,
# nothing else). Both Lambda roles use a manually-constructed endpoint ARN / log-group ARN
# (see locals in sagemaker.tf) rather than a direct resource reference, so these roles can be
# created independently of whether the SageMaker endpoint exists yet -- required for the
# two-phase apply.

data "aws_iam_policy_document" "sagemaker_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sagemaker_execution" {
  name               = "${var.project_name}-sagemaker-execution-role"
  assume_role_policy = data.aws_iam_policy_document.sagemaker_assume.json
}

# AmazonSageMakerFullAccess for simplicity -- a tighter policy would scope this to just the
# JumpStart artifacts bucket and this specific model's ECR image.
resource "aws_iam_role_policy_attachment" "sagemaker_execution" {
  role       = aws_iam_role.sagemaker_execution.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSageMakerFullAccess"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# --- Q&A Lambda ---
resource "aws_iam_role" "qa_lambda_execution" {
  name               = "${var.project_name}-qa-lambda-execution-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "qa_lambda_permissions" {
  statement {
    sid       = "InvokeThisEndpointOnly"
    effect    = "Allow"
    actions   = ["sagemaker:InvokeEndpoint"]
    resources = [local.sagemaker_endpoint_arn]
  }

  statement {
    sid       = "WriteOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project_name}-qa:*"]
  }

  statement {
    sid       = "PutQualityMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"] # PutMetricData has no resource-level ARNs; scoped by namespace condition instead
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["RagChatbot/Quality"]
    }
  }

  statement {
    sid       = "InferenceIdempotencyTable"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.inference_idempotency.arn]
  }
}

resource "aws_iam_role_policy" "qa_lambda" {
  name   = "${var.project_name}-qa-lambda-policy"
  role   = aws_iam_role.qa_lambda_execution.id
  policy = data.aws_iam_policy_document.qa_lambda_permissions.json
}

# --- Ingestion Lambda ---
resource "aws_iam_role" "ingest_lambda_execution" {
  name               = "${var.project_name}-ingest-lambda-execution-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "ingest_lambda_permissions" {
  statement {
    sid       = "ReadDocsBucket"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.docs.arn}/*"]
  }

  statement {
    sid       = "WriteOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project_name}-ingest:*"]
  }

  statement {
    sid       = "PutQualityMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["RagChatbot/Quality"]
    }
  }

  statement {
    sid       = "IngestionStateTable"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.ingestion_state.arn]
  }
}

resource "aws_iam_role_policy" "ingest_lambda" {
  name   = "${var.project_name}-ingest-lambda-policy"
  role   = aws_iam_role.ingest_lambda_execution.id
  policy = data.aws_iam_policy_document.ingest_lambda_permissions.json
}
