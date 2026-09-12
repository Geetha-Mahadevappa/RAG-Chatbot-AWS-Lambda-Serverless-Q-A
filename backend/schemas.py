"""
Pydantic models for the Q&A Lambda's request/response shape. This validates STRUCTURE and
TYPES (is `query` a non-empty string under the length limit; does the response have the
fields the frontend expects) -- it is deliberately separate from guardrails.py, which
validates CONTENT (prompt injection, relevance, groundedness). Pydantic has no notion of
"is this text trying to manipulate the model" or "is this answer actually grounded" -- that
stays regex/embedding-based. The two layers catch different failure modes:
  - malformed/wrong-shaped request body -> caught here, before guardrails ever run
  - well-formed but adversarial/out-of-scope/ungrounded content -> caught by guardrails.py

One request schema handles two actions (discriminated by `action`) rather than two separate
endpoints, since there's only one Lambda/Function URL: "ask" a question (optionally a
self-correction "regenerate" of a previous answer) or submit "feedback" (satisfied yes/no) on
an answer already given.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

import config


class QueryRequest(BaseModel):
    action: Literal["ask", "feedback"] = "ask"
    query: str = Field(..., min_length=1, max_length=config.MAX_QUERY_LENGTH)
    # Client-generated (crypto.randomUUID() in the frontend). Required for every request:
    # ties a "feedback" action back to the "ask" it's rating, and is the idempotency key for
    # "ask" actions (see idempotency.py).
    request_id: str = Field(..., min_length=1, max_length=100)
    regenerate: bool = False
    previous_answer: str | None = None
    # Client-tracked count of regenerations already performed for this original question --
    # a trust-the-client guard against runaway billed SageMaker calls, same spirit as the
    # other heuristic guardrails (not a hard security boundary, just a sane default limit).
    regenerate_count: int = 0
    satisfied: Literal["yes", "no"] | None = None

    @model_validator(mode="after")
    def _check_action_specific_fields(self):
        if self.action == "feedback" and self.satisfied is None:
            raise ValueError("'satisfied' is required when action is 'feedback'")
        if self.regenerate and not self.previous_answer:
            raise ValueError("'previous_answer' is required when regenerate is true")
        return self


class SourceModel(BaseModel):
    source_file: str
    source_url: str
    score: float
    excerpt: str


class SuccessResponse(BaseModel):
    status: Literal["ok"] = "ok"
    request_id: str
    answer: str
    tier: Literal["weak", "confident"]
    prompt_version: str
    warnings: list[str] = Field(default_factory=list)
    sources: list[SourceModel] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    status: Literal["error"] = "error"
    code: str
    message: str


class FeedbackAck(BaseModel):
    status: Literal["ok"] = "ok"
    request_id: str
    recorded: bool = True
