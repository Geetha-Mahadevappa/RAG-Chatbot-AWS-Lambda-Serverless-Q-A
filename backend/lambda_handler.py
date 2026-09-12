"""
Lambda entrypoint (Q&A path). Parses the HTTP request coming through the (private,
AWS_IAM-authenticated) Function URL, validates its shape with Pydantic (schemas.py), and
routes to one of two actions:
  - "ask": input guardrail -> idempotency check -> hybrid retrieval -> rerank -> generation
    (grounded / weak-context / self-correction prompt, depending on tier and regenerate) ->
    output guardrail -> cache the result.
  - "feedback": log + meter a yes/no satisfaction signal against a previous request_id. Never
    calls SageMaker -- feedback alone is free.

Two separate validation layers, on purpose:
  - schemas.py (Pydantic): is the request/response the right SHAPE?
  - guardrails.py (regex + embeddings): is the CONTENT safe/relevant/grounded? Pydantic has
    no opinion on prompt injection or groundedness -- that's a different kind of check.

Idempotency (idempotency.py, DynamoDB): a client retry of the same request_id (e.g. after a
timeout) replays the cached response instead of triggering a second billed SageMaker call.

Self-correction is capped server-side at config.MAX_REGENERATIONS -- a client that keeps
sending regenerate=true past the cap gets a 429, not another billed generation.

Every rejection and every flagged (weak-tier or low-groundedness) response is logged AND
emitted as a CloudWatch metric -- nothing failed is silently dropped.

CORS is handled by the Function URL's own Cors config (see terraform/lambda.tf); SigV4 auth
(AuthType=AWS_IAM) is handled by the Function URL service itself before this code ever runs --
an unsigned or badly-signed request never reaches this handler at all.
"""

import json
import logging

from pydantic import ValidationError

import config
import idempotency
import metrics
from guardrails import GuardrailRejection
from rag_core import answer_query
from schemas import ErrorResponse, FeedbackAck, QueryRequest, SourceModel, SuccessResponse

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_HEADERS = {"Content-Type": "application/json"}


def _response(status_code: int, body) -> dict:
    payload = body.model_dump() if hasattr(body, "model_dump") else body
    return {"statusCode": status_code, "headers": _HEADERS, "body": json.dumps(payload)}


def _handle_feedback(request: QueryRequest) -> dict:
    logger.info(
        "Feedback received: request_id=%s satisfied=%s query=%r",
        request.request_id, request.satisfied, request.query,
    )
    metrics.put_metric("AnswerFeedback", 1, unit="Count", dimensions={"satisfied": request.satisfied})
    return _response(200, FeedbackAck(request_id=request.request_id))


def _handle_ask(request: QueryRequest) -> dict:
    if request.regenerate and request.regenerate_count > config.MAX_REGENERATIONS:
        logger.warning("Regeneration cap exceeded for request_id=%s", request.request_id)
        return _response(
            429,
            ErrorResponse(
                code="regeneration_limit_exceeded",
                message="This answer has already been regenerated as many times as allowed.",
            ),
        )

    cached = idempotency.get_cached_response(request.request_id)
    if cached is not None:
        logger.info("Idempotent replay for request_id=%s", request.request_id)
        return {"statusCode": cached["status_code"], "headers": _HEADERS, "body": json.dumps(cached["body"])}

    idempotency.mark_in_progress(request.request_id)

    try:
        result = answer_query(request.query, regenerate=request.regenerate, previous_answer=request.previous_answer)
    except GuardrailRejection as exc:
        # Expected, user-facing rejection -- not a server error. 422 = request was
        # well-formed (passed schema validation) but semantically rejected.
        logger.warning("Guardrail rejected query: %s (%s)", exc.code, exc.message)
        metrics.put_metric("GuardrailRejection", 1, unit="Count", dimensions={"code": exc.code})
        response = ErrorResponse(code=exc.code, message=exc.message)
        idempotency.mark_completed(request.request_id, 422, response.model_dump())
        return _response(422, response)
    except Exception:
        logger.exception("Unhandled error answering query")
        metrics.put_metric("InternalError", 1, unit="Count")
        idempotency.mark_failed(request.request_id)
        return _response(
            500,
            ErrorResponse(code="internal_error", message="Something went wrong generating the answer."),
        )

    if result.warnings:
        logger.warning(
            "Flagged response: tier=%s warnings=%s query=%r", result.tier, result.warnings, request.query
        )

    response = SuccessResponse(
        request_id=request.request_id,
        answer=result.answer,
        tier=result.tier,
        prompt_version=result.prompt_version,
        warnings=result.warnings,
        sources=[
            SourceModel(source_file=c.source_file, source_url=c.source_url, score=c.score, excerpt=c.text[:200])
            for c in result.sources
        ],
    )
    idempotency.mark_completed(request.request_id, 200, response.model_dump())
    return _response(200, response)


def lambda_handler(event, context):
    try:
        raw_body = event.get("body") or "{}"
        request = QueryRequest.model_validate_json(raw_body)
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("Request failed schema validation: %s", exc)
        return _response(
            400,
            ErrorResponse(
                code="bad_request",
                message="Request body must be valid JSON matching the expected schema (see schemas.QueryRequest).",
            ),
        )

    logger.info("Received action=%s request_id=%s (%d char query)", request.action, request.request_id, len(request.query))

    if request.action == "feedback":
        return _handle_feedback(request)
    return _handle_ask(request)
