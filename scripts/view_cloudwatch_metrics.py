"""
Pull both SageMaker's own resource metrics AND this project's custom RAGAS-style quality
metrics from CloudWatch -- run after sending a few test queries (via
scripts/test_rag_local.py or the deployed frontend) to see them show up. The same two
categories are combined in the Terraform-managed CloudWatch dashboard (see terraform outputs).

SageMaker automatically emits per-endpoint metrics to "AWS/SageMaker": Invocations,
ModelLatency, OverheadLatency, 4XX/5XX error counts -- no extra setup required.

This project additionally emits custom metrics (see backend/metrics.py) to a
"RagChatbot/Quality" namespace on every request: ContextRelevanceScore and FaithfulnessScore
(the live RAGAS-style heuristics), ResponseCount by tier (confident/weak),
GuardrailRejection by code (empty_query/query_too_long/possible_prompt_injection/
out_of_scope), and IngestionSuccess/IngestionFailure from the S3-triggered ingestion Lambda.
Every rejected or flagged case is counted here, not just successful ones.

Usage:
    python scripts/view_cloudwatch_metrics.py
    python scripts/view_cloudwatch_metrics.py --minutes 30
"""

import argparse
import os
from datetime import datetime, timedelta, timezone

import boto3

AWS_REGION = os.environ.get("AWS_REGION", "eu-central-1")
ENDPOINT_NAME = os.environ.get("SAGEMAKER_ENDPOINT_NAME", "rag-chatbot-flan-t5-base")

QUALITY_NAMESPACE = "RagChatbot/Quality"
SAGEMAKER_METRICS = [
    ("Invocations", "Sum"),
    ("ModelLatency", "Average"),
    ("OverheadLatency", "Average"),
    ("Invocation4XXErrors", "Sum"),
    ("Invocation5XXErrors", "Sum"),
]
UNDIMENSIONED_QUALITY_METRICS = [("ContextRelevanceScore", "Average"), ("FaithfulnessScore", "Average")]
DIMENSIONED_QUALITY_METRICS = [
    ("ResponseCount", "tier"),
    ("GuardrailRejection", "code"),
    ("IngestionSuccess", "action"),
    ("IngestionFailure", "source_file"),
]


def print_sagemaker_metrics(cw, sm, start, end):
    try:
        desc = sm.describe_endpoint(EndpointName=ENDPOINT_NAME)
    except sm.exceptions.ClientError:
        print(f"SageMaker endpoint '{ENDPOINT_NAME}' not found (not deployed, or already deleted).")
        return

    variant_name = desc["ProductionVariants"][0]["VariantName"]
    print(f"SageMaker endpoint: {ENDPOINT_NAME}  (status={desc['EndpointStatus']}, variant={variant_name})")
    dims = [{"Name": "EndpointName", "Value": ENDPOINT_NAME}, {"Name": "VariantName", "Value": variant_name}]

    for metric_name, stat in SAGEMAKER_METRICS:
        resp = cw.get_metric_statistics(
            Namespace="AWS/SageMaker", MetricName=metric_name, Dimensions=dims,
            StartTime=start, EndTime=end, Period=300, Statistics=[stat],
        )
        points = sorted(resp["Datapoints"], key=lambda p: p["Timestamp"])
        total = sum(p[stat] for p in points) if stat == "Sum" else None
        label = f"total={total}" if total is not None else f"{len(points)} datapoints"
        print(f"  {metric_name:24s} {label}")
        for p in points:
            print(f"      {p['Timestamp'].strftime('%H:%M:%S')}  {stat}={p[stat]:.3f}")


def print_quality_metrics(cw, start, end, minutes):
    print(f"\n{QUALITY_NAMESPACE} (this project's live RAGAS-style + guardrail metrics):")

    for name, stat in UNDIMENSIONED_QUALITY_METRICS:
        resp = cw.get_metric_statistics(
            Namespace=QUALITY_NAMESPACE, MetricName=name,
            StartTime=start, EndTime=end, Period=300, Statistics=[stat],
        )
        points = sorted(resp["Datapoints"], key=lambda p: p["Timestamp"])
        if points:
            mean = sum(p[stat] for p in points) / len(points)
            print(f"  {name}: {len(points)} datapoint(s), mean={mean:.3f}")
        else:
            print(f"  {name}: no data yet")

    for name, dim_key in DIMENSIONED_QUALITY_METRICS:
        listed = cw.list_metrics(Namespace=QUALITY_NAMESPACE, MetricName=name).get("Metrics", [])
        if not listed:
            print(f"  {name}: no data yet")
            continue
        print(f"  {name} by {dim_key}:")
        seen = set()
        for m in listed:
            dim_map = {d["Name"]: d["Value"] for d in m["Dimensions"]}
            value = dim_map.get(dim_key)
            if value in seen:
                continue
            seen.add(value)
            resp = cw.get_metric_statistics(
                Namespace=QUALITY_NAMESPACE, MetricName=name,
                Dimensions=[{"Name": dim_key, "Value": value}],
                StartTime=start, EndTime=end, Period=minutes * 60, Statistics=["Sum"],
            )
            total = sum(p["Sum"] for p in resp["Datapoints"])
            print(f"      {value}: {total:.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=60, help="Look-back window in minutes.")
    args = parser.parse_args()

    sm = boto3.client("sagemaker", region_name=AWS_REGION)
    cw = boto3.client("cloudwatch", region_name=AWS_REGION)
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=args.minutes)

    print(f"Window: last {args.minutes} minutes\n")
    print_sagemaker_metrics(cw, sm, start, end)
    print_quality_metrics(cw, start, end, args.minutes)

    print(
        "\nConsole equivalents: SageMaker console -> Inference -> Endpoints -> "
        f"{ENDPOINT_NAME} -> 'Monitor' tab, or CloudWatch console -> Dashboards -> the "
        "Terraform-created dashboard (see `terraform output dashboard_url`), which combines "
        "both categories in one view."
    )
    print(
        "Lambda logs: aws logs tail /aws/lambda/rag-chatbot-qa --follow --region " + AWS_REGION
        + "\n             aws logs tail /aws/lambda/rag-chatbot-ingest --follow --region " + AWS_REGION
    )


if __name__ == "__main__":
    main()
