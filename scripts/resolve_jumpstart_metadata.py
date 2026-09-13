"""
Resolves SageMaker JumpStart model metadata (container image URI, model data S3 location,
required container environment variables, recommended instance type) WITHOUT creating any
AWS resources or deploying anything -- this is purely reading JumpStart's public model
registry via the SageMaker Python SDK. Terraform has no native JumpStart awareness, so this
is the one manual step that has to happen before the first `terraform apply` (and again only
if you change generation.sagemaker_model_id in backend/config.yaml).

Run: python scripts/resolve_jumpstart_metadata.py
Cost: $0 -- read-only metadata lookup, nothing is created.
"""

import json
import os
import sys
from pathlib import Path

from sagemaker.jumpstart.model import JumpStartModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import config  # noqa: E402

MODEL_ID = config.SAGEMAKER_MODEL_ID
AWS_REGION = os.environ.get("AWS_REGION", "eu-central-1")

OUTPUT_PATH = ROOT / "terraform" / "generated.auto.tfvars.json"


def main():
    print(f"Resolving JumpStart model '{MODEL_ID}' in {AWS_REGION} ...")
    model = JumpStartModel(model_id=MODEL_ID, region=AWS_REGION)

    s3_source = model.model_data["S3DataSource"]

    tfvars = {
        "sagemaker_image_uri": model.image_uri,
        "sagemaker_model_data_s3_uri": s3_source["S3Uri"],
        "sagemaker_model_data_s3_type": s3_source["S3DataType"],
        "sagemaker_model_data_compression_type": s3_source["CompressionType"],
        "sagemaker_model_env": model.env,
        "sagemaker_instance_type": model.instance_type,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(tfvars, indent=2) + "\n")

    print(f"\nWrote {OUTPUT_PATH}")
    for k, v in tfvars.items():
        print(f"  {k}: {v}")
    print(
        "\nThis file is auto-loaded by Terraform (*.auto.tfvars.json). It contains no "
        "secrets -- just public JumpStart model metadata -- but is gitignored since it's a "
        "regenerable build artifact, not a source of truth."
    )


if __name__ == "__main__":
    main()
