# One shared image for both Lambda functions -- Q&A and ingestion need the same heavy deps
# (sentence-transformers, fastembed, qdrant-client), so they run the same image with
# different image_config.command overrides (see lambda.tf) instead of building two images.

resource "aws_ecr_repository" "backend" {
  name         = "${var.project_name}-backend"
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

locals {
  backend_dir   = "${path.module}/../backend"
  backend_files = fileset(local.backend_dir, "**")
  # Content hash of every file in backend/ (Dockerfile, requirements.txt, all .py handlers).
  # Tagging the image with this hash (instead of :latest) is what makes Terraform notice code
  # changes -- an aws_lambda_function's image_uri argument only triggers an update when the
  # STRING changes; ":latest" never changes even when the underlying image content does.
  backend_hash = sha256(join("", [for f in local.backend_files : filesha256("${local.backend_dir}/${f}")]))
  image_tag    = substr(local.backend_hash, 0, 12)
  image_uri    = "${aws_ecr_repository.backend.repository_url}:${local.image_tag}"
}

resource "null_resource" "docker_build_push" {
  triggers = {
    image_tag = local.image_tag
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail
      aws ecr get-login-password --region ${var.aws_region} \
        | docker login --username AWS --password-stdin ${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com
      docker build -t ${local.image_uri} ${local.backend_dir}
      docker push ${local.image_uri}
    EOT
  }

  depends_on = [aws_ecr_repository.backend]
}
