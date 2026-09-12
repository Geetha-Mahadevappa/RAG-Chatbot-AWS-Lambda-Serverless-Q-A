"""
Lambda entrypoint (ingestion path). Triggered by S3 ObjectCreated/ObjectRemoved events on the
docs bucket (see terraform/s3.tf) -- reacts to one changed file at a time and incrementally
updates just that file's chunks in Qdrant, rather than rebuilding the whole collection. This
is the event-driven half of the pipeline: S3 fires the event the instant an object changes,
no polling or manual timestamp comparison needed.

Every file's progress is tracked in DynamoDB (ingestion_state.py): before processing, an
unchanged file (same S3 ETag as the last *completed* run) is skipped entirely -- this also
absorbs S3's occasional duplicate event delivery for the same object version. During
processing, chunk count and progress are recorded so a crashed/timed-out run leaves visible
state instead of silence; a retry (whether a redelivered S3 event or a manual reprocess) just
re-runs from scratch, which is safe because Qdrant point IDs are deterministic (ingest_core.py).
"""

import logging
import os
import urllib.parse

import boto3
from fastembed import SparseTextEmbedding
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

import ingest_core
import ingestion_state
import metrics

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AWS_REGION = os.environ.get("AWS_REGION", "eu-central-1")
QDRANT_URL = os.environ.get("QDRANT_URL")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY")
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION_NAME", "aws_lambda_docs")

_s3 = boto3.client("s3", region_name=AWS_REGION)
_dense_model: SentenceTransformer | None = None
_sparse_model: SparseTextEmbedding | None = None
_qdrant_client: QdrantClient | None = None


def _get_dense_model() -> SentenceTransformer:
    global _dense_model
    if _dense_model is None:
        _dense_model = SentenceTransformer(ingest_core.DENSE_MODEL_NAME)
    return _dense_model


def _get_sparse_model() -> SparseTextEmbedding:
    global _sparse_model
    if _sparse_model is None:
        _sparse_model = SparseTextEmbedding(model_name=ingest_core.SPARSE_MODEL_NAME)
    return _sparse_model


def _get_qdrant_client() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        if not QDRANT_URL or not QDRANT_API_KEY:
            raise RuntimeError("QDRANT_URL / QDRANT_API_KEY environment variables are not set.")
        _qdrant_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return _qdrant_client


def _process_created(client: QdrantClient, bucket: str, key: str) -> dict:
    obj = _s3.get_object(Bucket=bucket, Key=key)
    raw_text = obj["Body"].read().decode("utf-8")
    last_modified = obj["LastModified"].isoformat()
    file_hash = obj["ETag"].strip('"')

    existing = ingestion_state.get_state(key)
    if existing and existing.get("file_hash") == file_hash and existing.get("status") == "completed":
        logger.info("Skipping %s -- unchanged since last completed run (hash=%s)", key, file_hash)
        metrics.put_metric("IngestionSkipped", 1, unit="Count", dimensions={"source_file": key})
        return {"key": key, "action": "skipped_unchanged"}

    dense_model = _get_dense_model()
    ingest_core.ensure_collection(client, COLLECTION_NAME, dense_model.get_embedding_dimension())

    try:
        n_chunks = ingest_core.embed_and_upsert_file(
            client,
            dense_model,
            _get_sparse_model(),
            COLLECTION_NAME,
            key,
            raw_text,
            s3_last_modified=last_modified,
            on_chunked=lambda total: ingestion_state.mark_in_progress(key, file_hash, total),
            on_progress=lambda processed: ingestion_state.update_progress(key, processed),
        )
    except Exception as exc:
        ingestion_state.mark_failed(key, str(exc))
        raise

    ingestion_state.mark_completed(key)
    metrics.put_metric("IngestionSuccess", 1, unit="Count", dimensions={"source_file": key, "action": "upsert"})
    metrics.put_metric("IngestedChunkCount", n_chunks, unit="Count", dimensions={"source_file": key})
    return {"key": key, "action": "upserted", "chunks": n_chunks}


def _process_removed(client: QdrantClient, key: str) -> dict:
    ingest_core.delete_file(client, COLLECTION_NAME, key)
    ingestion_state.mark_deleted(key)
    metrics.put_metric("IngestionSuccess", 1, unit="Count", dimensions={"source_file": key, "action": "delete"})
    return {"key": key, "action": "deleted"}


def lambda_handler(event, context):
    client = _get_qdrant_client()
    results = []

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        event_name = record["eventName"]
        logger.info("S3 event %s for s3://%s/%s", event_name, bucket, key)

        try:
            if event_name.startswith("ObjectRemoved"):
                results.append(_process_removed(client, key))
            else:
                results.append(_process_created(client, bucket, key))
        except Exception:
            logger.exception("Failed to process S3 event for key %s", key)
            metrics.put_metric("IngestionFailure", 1, unit="Count", dimensions={"source_file": key})
            results.append({"key": key, "action": "failed"})

    return {"statusCode": 200, "processed": results}
