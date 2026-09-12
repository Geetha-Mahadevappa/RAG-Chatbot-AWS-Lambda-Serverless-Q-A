"""
Ingestion job state: one item per source file, tracking its content hash, status, and
chunk-level progress -- so a crashed or retried ingestion run has visible state instead of
being a black box, and an unchanged file doesn't get pointlessly reprocessed.

True chunk-level resume (skip exactly the chunks already written) isn't necessary: Qdrant
point IDs are deterministic (uuid5 of source_file+chunk_index, see ingest_core.py), so
re-running embed_and_upsert_file for a file that partially completed just re-upserts from
batch 0 -- idempotent and cheap (local CPU embedding, no billed calls). What this table adds
is (a) visibility into exactly how far a crashed run got (processed_chunks vs total_chunks),
and (b) skipping a file entirely when its hash hasn't changed since the last *completed* run
(also absorbs S3's occasional duplicate event delivery for the same object version).

Table schema: hash key source_file (S).
"""

import os
import time

import boto3

TABLE_NAME = os.environ.get("INGESTION_STATE_TABLE")
_table = None


def _get_table():
    global _table
    if _table is None:
        if not TABLE_NAME:
            raise RuntimeError("INGESTION_STATE_TABLE environment variable is not set.")
        _table = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _table


def get_state(source_file: str) -> dict | None:
    return _get_table().get_item(Key={"source_file": source_file}).get("Item")


def mark_in_progress(source_file: str, file_hash: str, total_chunks: int) -> None:
    _get_table().put_item(
        Item={
            "source_file": source_file,
            "file_hash": file_hash,
            "status": "in_progress",
            "total_chunks": total_chunks,
            "processed_chunks": 0,
            "updated_at": int(time.time()),
        }
    )


def update_progress(source_file: str, processed_chunks: int) -> None:
    _get_table().update_item(
        Key={"source_file": source_file},
        UpdateExpression="SET processed_chunks = :p, updated_at = :t",
        ExpressionAttributeValues={":p": processed_chunks, ":t": int(time.time())},
    )


def mark_completed(source_file: str) -> None:
    _get_table().update_item(
        Key={"source_file": source_file},
        UpdateExpression="SET #s = :s, updated_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "completed", ":t": int(time.time())},
    )


def mark_failed(source_file: str, error_message: str) -> None:
    _get_table().update_item(
        Key={"source_file": source_file},
        UpdateExpression="SET #s = :s, error_message = :e, updated_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "failed", ":e": str(error_message)[:500], ":t": int(time.time())},
    )


def mark_deleted(source_file: str) -> None:
    _get_table().delete_item(Key={"source_file": source_file})
