locals {
  # Built from a plain string, not a resource attribute, so IAM/Lambda resources that need
  # this ARN (see iam.tf) can be created independently of whether this endpoint exists yet.
  sagemaker_endpoint_name = "${var.project_name}-endpoint"
  sagemaker_endpoint_arn  = "arn:aws:sagemaker:${var.aws_region}:${data.aws_caller_identity.current.account_id}:endpoint/${local.sagemaker_endpoint_name}"
}

resource "aws_sagemaker_model" "this" {
  name               = "${var.project_name}-model"
  execution_role_arn = aws_iam_role.sagemaker_execution.arn

  primary_container {
    image = var.sagemaker_image_uri

    model_data_source {
      s3_data_source {
        s3_uri           = var.sagemaker_model_data_s3_uri
        s3_data_type     = var.sagemaker_model_data_s3_type
        compression_type = var.sagemaker_model_data_compression_type
      }
    }

    environment = var.sagemaker_model_env
  }

  depends_on = [aws_iam_role_policy_attachment.sagemaker_execution]
}

resource "aws_sagemaker_endpoint_configuration" "this" {
  name = "${var.project_name}-endpoint-config"

  production_variants {
    variant_name           = "AllTraffic"
    model_name             = aws_sagemaker_model.this.name
    instance_type          = var.sagemaker_instance_type
    initial_instance_count = 1
  }
}

resource "aws_sagemaker_endpoint" "this" {
  name                 = local.sagemaker_endpoint_name
  endpoint_config_name = aws_sagemaker_endpoint_configuration.this.name
}
