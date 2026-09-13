# Both tables use PAY_PER_REQUEST (on-demand) billing -- negligible cost at this project's
# volume (a few cents at most for a demo session) and no capacity planning needed, unlike
# provisioned mode where you'd guess a WCU/RCU baseline for near-zero traffic.

# Per-file ingestion job state: hash + status + processed/total chunk counts. See
# backend/ingestion_state.py -- lets a crashed/retried ingestion run show real progress
# instead of being a black box, and skips reprocessing a file that hasn't changed.
resource "aws_dynamodb_table" "ingestion_state" {
  name         = "${var.project_name}-ingestion-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "source_file"

  attribute {
    name = "source_file"
    type = "S"
  }
}

# Inference idempotency cache: request_id -> cached response, so a client retry (timeout,
# double-click) replays the cached result instead of triggering a second billed SageMaker
# call. See backend/idempotency.py. TTL keeps the table from growing unbounded.
resource "aws_dynamodb_table" "inference_idempotency" {
  name         = "${var.project_name}-inference-idempotency"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "request_id"

  attribute {
    name = "request_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
