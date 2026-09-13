# One dashboard combining SageMaker/Lambda RESOURCE metrics (invocations, latency, errors --
# "is the infrastructure healthy") with this project's own RagChatbot/Quality metrics
# ("is the RAG pipeline actually answering well") -- a single view for both concerns.

resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = "${var.project_name}-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6,
        properties = {
          title  = "SageMaker Endpoint - Invocations & Latency"
          region = var.aws_region
          metrics = [
            ["AWS/SageMaker", "Invocations", "EndpointName", local.sagemaker_endpoint_name, "VariantName", "AllTraffic", { stat = "Sum" }],
            ["AWS/SageMaker", "ModelLatency", "EndpointName", local.sagemaker_endpoint_name, "VariantName", "AllTraffic", { stat = "Average", yAxis = "right" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6,
        properties = {
          title  = "SageMaker Endpoint - Errors"
          region = var.aws_region
          metrics = [
            ["AWS/SageMaker", "Invocation4XXErrors", "EndpointName", local.sagemaker_endpoint_name, "VariantName", "AllTraffic", { stat = "Sum" }],
            ["AWS/SageMaker", "Invocation5XXErrors", "EndpointName", local.sagemaker_endpoint_name, "VariantName", "AllTraffic", { stat = "Sum" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6,
        properties = {
          title  = "Lambda - Invocations & Errors (both functions)"
          region = var.aws_region
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.qa.function_name, { stat = "Sum", label = "qa invocations" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.qa.function_name, { stat = "Sum", label = "qa errors" }],
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.ingest.function_name, { stat = "Sum", label = "ingest invocations" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.ingest.function_name, { stat = "Sum", label = "ingest errors" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6,
        properties = {
          title  = "RAGAS-style Quality Metrics (live heuristics)"
          region = var.aws_region
          metrics = [
            ["RagChatbot/Quality", "ContextRelevanceScore", { stat = "Average" }],
            ["RagChatbot/Quality", "FaithfulnessScore", { stat = "Average" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 12, height = 6,
        properties = {
          title  = "Guardrail Rejections by reason"
          region = var.aws_region
          metrics = [
            ["RagChatbot/Quality", "GuardrailRejection", "code", "out_of_scope", { stat = "Sum" }],
            ["RagChatbot/Quality", "GuardrailRejection", "code", "possible_prompt_injection", { stat = "Sum" }],
            ["RagChatbot/Quality", "GuardrailRejection", "code", "empty_query", { stat = "Sum" }],
            ["RagChatbot/Quality", "GuardrailRejection", "code", "query_too_long", { stat = "Sum" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 12, width = 12, height = 6,
        properties = {
          title  = "Ingestion pipeline (S3 event -> Qdrant)"
          region = var.aws_region
          metrics = [
            ["RagChatbot/Quality", "IngestionSuccess", "action", "upsert", { stat = "Sum" }],
            ["RagChatbot/Quality", "IngestionSuccess", "action", "delete", { stat = "Sum" }],
            # IngestionFailure is dimensioned by source_file, which isn't known ahead of time
            # -- a search expression aggregates across whatever files have actually failed,
            # instead of a static per-dimension-value list like the two metrics above.
            [{ expression = "SEARCH('{RagChatbot/Quality,source_file} MetricName=\"IngestionFailure\"', 'Sum', 300)", label = "IngestionFailure (any file)" }],
          ]
        }
      },
    ]
  })
}
