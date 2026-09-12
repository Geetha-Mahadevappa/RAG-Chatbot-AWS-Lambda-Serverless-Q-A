"""
Inference-side idempotency cache: a client-generated request_id maps to a cached response, so
a client retry (e.g. after a network timeout, or a Lambda cold start blowing past a tight
client-side timeout) replays the cached result instead of triggering a second billed SageMaker
call. This is what "check the state of the agent if the pipeline crashed mid-way" resolves to
for a single synchronous Q&A request -- there's no multi-step workflow to resume, just a
guarantee that retrying a request you already answered doesn't cost twice.

Table schema: hash key request_id (S). TTL attribute expires_at auto-deletes old records
(config.IDEMPOTENCY_TTL_SECONDS) so the table doesn't grow unbounded.
"""

import json
import os
import time

import boto3

import config

TABLE_NAME = os.environ.get("IDEMPOTENCY_TABLE")
_table = None


def _get_table():
    global _table
    if _table is None:
        if not TABLE_NAME:
            raise RuntimeError("IDEMPOTENCY_TABLE environment variable is not set.")
        _table = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _table


def get_cached_response(request_id: str) -> dict | None:
    """Returns {"status_code": int, "body": dict} if this request_id already completed, else
    None (not seen before, or still in_progress/failed -- both of those should just proceed
    with normal processing rather than replay something incomplete)."""
    item = _get_table().get_item(Key={"request_id": request_id}).get("Item")
    if not item or item.get("status") != "completed":
        return None
    return {"status_code": int(item["status_code"]), "body": json.loads(item["body"])}


def mark_in_progress(request_id: str) -> None:
    _get_table().put_item(
        Item={
            "request_id": request_id,
            "status": "in_progress",
            "created_at": int(time.time()),
            "expires_at": int(time.time()) + config.IDEMPOTENCY_TTL_SECONDS,
        }
    )


def mark_completed(request_id: str, status_code: int, body: dict) -> None:
    _get_table().update_item(
        Key={"request_id": request_id},
        UpdateExpression="SET #s = :s, status_code = :c, body = :b, expires_at = :e",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "completed",
            ":c": status_code,
            ":b": json.dumps(body),
            ":e": int(time.time()) + config.IDEMPOTENCY_TTL_SECONDS,
        },
    )


def mark_failed(request_id: str) -> None:
    _get_table().update_item(
        Key={"request_id": request_id},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "failed"},
    )
