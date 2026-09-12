"""
Thin wrapper around CloudWatch custom metrics. Everything in this project's own quality
signals (relevance, faithfulness, rejection counts) lands in one namespace so a single
CloudWatch dashboard can show them next to SageMaker/Lambda's own resource metrics.
"""

import logging
import os

import boto3

logger = logging.getLogger()

NAMESPACE = "RagChatbot/Quality"
_cloudwatch = None


def _client():
    global _cloudwatch
    if _cloudwatch is None:
        _cloudwatch = boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION", "eu-central-1"))
    return _cloudwatch


def put_metric(name: str, value: float, unit: str = "None", dimensions: dict | None = None) -> None:
    """Emit a single CloudWatch custom metric. Deliberately never raises -- a metrics-pipeline
    failure must never break the request it's trying to observe."""
    try:
        _client().put_metric_data(
            Namespace=NAMESPACE,
            MetricData=[
                {
                    "MetricName": name,
                    "Value": value,
                    "Unit": unit,
                    "Dimensions": [{"Name": k, "Value": str(v)} for k, v in (dimensions or {}).items()],
                }
            ],
        )
    except Exception:
        logger.exception("Failed to emit CloudWatch metric %s", name)
