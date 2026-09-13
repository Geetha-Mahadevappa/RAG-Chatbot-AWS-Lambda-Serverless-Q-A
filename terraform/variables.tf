variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "eu-central-1"
}

variable "project_name" {
  description = "Prefix used when naming every resource."
  type        = string
  default     = "rag-chatbot"
}

# --- Qdrant. Deliberately NOT read from a file -- set these via TF_VAR_qdrant_url /
# TF_VAR_qdrant_api_key environment variables before running terraform. Terraform then
# passes them through as native Lambda environment variables (same names, no dotenv/.env
# involved anywhere in this project). ---
variable "qdrant_url" {
  description = "Qdrant Cloud cluster URL. Set via the TF_VAR_qdrant_url environment variable."
  type        = string
  sensitive   = true
}

variable "qdrant_api_key" {
  description = "Qdrant Cloud API key. Set via the TF_VAR_qdrant_api_key environment variable."
  type        = string
  sensitive   = true
}

variable "qdrant_collection_name" {
  description = "Qdrant collection name."
  type        = string
  default     = "aws_lambda_docs"
}

# --- SageMaker JumpStart metadata. NOT secret -- public model registry metadata, resolved by
# scripts/resolve_jumpstart_metadata.py into terraform/generated.auto.tfvars.json, which
# Terraform loads automatically (any *.auto.tfvars.json file is picked up with no -var-file
# flag needed). Terraform has no native JumpStart awareness, so this one resolve step has to
# run before the first `terraform apply`. ---
variable "sagemaker_image_uri" {
  description = "JumpStart container image URI."
  type        = string
}

variable "sagemaker_model_data_s3_uri" {
  description = "S3 URI of the JumpStart model artifacts."
  type        = string
}

variable "sagemaker_model_data_s3_type" {
  description = "S3DataType for the model data source (e.g. S3Prefix)."
  type        = string
  default     = "S3Prefix"
}

variable "sagemaker_model_data_compression_type" {
  description = "CompressionType for the model data source (e.g. None)."
  type        = string
  default     = "None"
}

variable "sagemaker_model_env" {
  description = "Container environment variables required by the JumpStart model."
  type        = map(string)
  default     = {}
}

variable "sagemaker_instance_type" {
  description = "Instance type for the real-time inference endpoint. No CPU option exists for this model in JumpStart."
  type        = string
  default     = "ml.g5.2xlarge"
}
