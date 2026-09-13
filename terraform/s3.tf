# Knowledge base storage + the event-driven half of the ingestion pipeline. S3 fires an
# event the instant an object is created/overwritten/deleted -- no polling, no manual
# "did the timestamp change" comparison needed.

resource "aws_s3_bucket" "docs" {
  bucket        = "${var.project_name}-docs-${data.aws_caller_identity.current.account_id}"
  force_destroy = true # lets `terraform destroy` remove the bucket even if objects remain

  tags = {
    Project = var.project_name
  }
}

resource "aws_s3_bucket_versioning" "docs" {
  bucket = aws_s3_bucket.docs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "docs" {
  bucket                  = aws_s3_bucket.docs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Uploads every data/*.txt file. The etag changes whenever a local file's content changes, so
# `terraform apply` after editing a doc re-uploads just that file -- which fires the S3 event
# notification below and triggers re-ingestion of just that file. The very FIRST apply
# uploads all files, which also fires the notification for each of them -- no separate manual
# bulk-load step is needed to populate the knowledge base initially.
resource "aws_s3_object" "docs" {
  for_each = fileset("${path.module}/../data", "*.txt")

  bucket = aws_s3_bucket.docs.id
  key    = each.value
  source = "${path.module}/../data/${each.value}"
  etag   = filemd5("${path.module}/../data/${each.value}")
}

resource "aws_lambda_permission" "allow_s3_invoke_ingest" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.docs.arn
}

resource "aws_s3_bucket_notification" "docs" {
  bucket = aws_s3_bucket.docs.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.ingest.arn
    events              = ["s3:ObjectCreated:*", "s3:ObjectRemoved:*"]
    filter_suffix       = ".txt"
  }

  depends_on = [aws_lambda_permission.allow_s3_invoke_ingest]
}
