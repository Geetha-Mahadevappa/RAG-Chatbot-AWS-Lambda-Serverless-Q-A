"""
Lightweight input/output guardrails for the RAG chatbot.

These are heuristics, not a production content-safety system: pattern-matching + a
retrieval-relevance threshold for input, a lexical-groundedness check for output. A
production system would likely layer an LLM-based classifier or a managed service (e.g.
Bedrock Guardrails) on top of this, not replace it -- cheap heuristics still catch the common
cases before you pay for a model call.
"""

import re

import config
import prompts

MAX_QUERY_LENGTH = config.MAX_QUERY_LENGTH


class GuardrailRejection(Exception):
    """Raised when a query fails an input guardrail check, before any paid SageMaker call."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


# Deliberately simple phrase screen for prompt-injection attempts. Not foolproof -- a
# determined attacker can phrase around it -- but it catches common patterns for free,
# before the billed SageMaker call.
_INJECTION_PATTERNS = [
    r"ignore (all|any|the)?\s*(previous|prior|above)\s*instructions",
    r"disregard (all|any|the)?\s*(previous|prior|above)\s*instructions",
    r"reveal (your|the)\s*(system\s*)?prompt",
    r"you are now\b",
    r"forget (all|everything|your instructions)",
    r"act as (if|though) you",
    r"new instructions\s*:",
    r"^\s*system\s*:",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

# Two-tier relevance gate based on the dense cosine-similarity score of the single best
# retrieved chunk (see rag_core.dense_relevance_score -- NOT the fused hybrid RRF score,
# which doesn't separate relevant from irrelevant queries cleanly on a small corpus).
#
# Empirically, on-topic queries against this project's corpus scored ~0.6-0.8 cosine
# similarity; off-topic queries (e.g. "cookie recipe") scored ~0.03-0.08:
#   score < REJECT_THRESHOLD        -> "reject": no relevant content exists at all. Reject
#                                       before calling the endpoint -- a guardrail AND a cost
#                                       control, no point paying for a generation the
#                                       retrieved context can't support.
#   REJECT_THRESHOLD <= score       -> "weak": some marginally-relevant content was found.
#     < CONFIDENT_THRESHOLD            Still call the LLM, but with a fallback prompt
#                                       (prompts.WEAK_CONTEXT_TEMPLATE) that instructs it to
#                                       admit uncertainty rather than confidently guess.
#   score >= CONFIDENT_THRESHOLD    -> "confident": normal grounded prompt.
# Tune both thresholds per-corpus if you swap in different documents (config.yaml).
REJECT_THRESHOLD = config.REJECT_THRESHOLD
CONFIDENT_THRESHOLD = config.CONFIDENT_THRESHOLD

# Below this lexical-overlap ratio between the answer and retrieved context, flag the answer
# as low-confidence rather than blocking it outright (models legitimately paraphrase).
MIN_GROUNDEDNESS_RATIO = config.MIN_GROUNDEDNESS_RATIO

_WORD_RE = re.compile(r"[a-z0-9]+")


def screen_query(query: str) -> None:
    """Basic input hygiene + prompt-injection screen. Raises GuardrailRejection on failure."""
    if not query or not query.strip():
        raise GuardrailRejection("empty_query", "Query is empty.")
    if len(query) > MAX_QUERY_LENGTH:
        raise GuardrailRejection(
            "query_too_long",
            f"Query is {len(query)} characters; max allowed is {MAX_QUERY_LENGTH}.",
        )
    if _INJECTION_RE.search(query):
        raise GuardrailRejection(
            "possible_prompt_injection",
            "Query matches a known prompt-injection pattern and was rejected.",
        )


def check_relevance(top_score: float, query: str) -> str:
    """Returns the relevance tier ("weak" or "confident") for a query whose single best
    retrieved match scored top_score. Raises GuardrailRejection if top_score is below
    REJECT_THRESHOLD entirely -- i.e. the knowledge base has nothing relevant at all. The
    rejection message is a friendly, templated "graceful degradation" response (prompts.py),
    not a bare error code -- the reject path stays free (no SageMaker call) either way, but
    there's no reason the user-facing copy has to read like one."""
    if top_score < REJECT_THRESHOLD:
        raise GuardrailRejection("out_of_scope", prompts.graceful_degradation_message(query))
    if top_score < CONFIDENT_THRESHOLD:
        return "weak"
    return "confident"


def check_groundedness(answer: str, context_text: str) -> tuple[float, str | None]:
    """Lexical-overlap heuristic, doubling as this project's RAGAS-style "faithfulness" proxy.
    Returns (overlap_ratio, warning) -- warning is a string if the answer looks poorly
    grounded in the retrieved context, else None. This FLAGS, it does not block -- hard-
    blocking here would false-positive too often since models paraphrase legitimately.
    overlap_ratio is returned unconditionally so callers can log/emit it as a metric even
    when it's well above the warning threshold."""
    answer_words = set(_WORD_RE.findall(answer.lower()))
    if not answer_words:
        return 0.0, "Model returned an empty or non-textual answer."

    context_words = set(_WORD_RE.findall(context_text.lower()))
    overlap_ratio = len(answer_words & context_words) / len(answer_words)
    if overlap_ratio < MIN_GROUNDEDNESS_RATIO:
        return overlap_ratio, (
            "This answer has low lexical overlap with the retrieved context -- it may not "
            "be well-grounded in the knowledge base. Verify independently."
        )
    return overlap_ratio, None
