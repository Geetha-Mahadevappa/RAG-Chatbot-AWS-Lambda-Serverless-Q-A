"""
Shared RAG pipeline: embed query -> Qdrant hybrid retrieval -> cross-encoder rerank ->
prompt construction -> SageMaker endpoint call via LangChain. Used identically by
scripts/test_rag_local.py (local iteration) and backend/lambda_handler.py (deployed Lambda)
-- same code path in both places, so "it worked locally" means "it'll work in Lambda."

SageMaker concept notes:
  - "Endpoint" = a persistent, auto-scaling HTTPS-accessible deployment of a model. You are
    billed for the underlying instance for as long as it exists, regardless of request volume
    -- this is "real-time inference," as opposed to serverless/batch/async inference.
  - We invoke it here via LangChain's SagemakerEndpoint LLM wrapper, which itself calls
    boto3's sagemaker-runtime `invoke_endpoint` under the hood -- LangChain just standardizes
    the LLM interface and handles the request/response transform via a "content handler."

Tunable pipeline parameters (chunk size, hybrid/rerank limits, model names, thresholds) live
in config.yaml / config.py, not here. Deployment-specific values (secrets, endpoint name)
come from real process environment variables (os.environ) -- no .env file, no python-dotenv.
Set AWS_REGION / SAGEMAKER_ENDPOINT_NAME / QDRANT_URL / QDRANT_API_KEY /
QDRANT_COLLECTION_NAME in your shell for local runs; Terraform sets the same names as native
Lambda environment variables for the deployed function.
"""

import json
import os
from dataclasses import dataclass, field

from fastembed import SparseTextEmbedding
from langchain_aws.llms.sagemaker_endpoint import LLMContentHandler, SagemakerEndpoint
from qdrant_client import QdrantClient, models
from sentence_transformers import CrossEncoder, SentenceTransformer

import config
import metrics
import prompts
from guardrails import GuardrailRejection, check_groundedness, check_relevance, screen_query

AWS_REGION = os.environ.get("AWS_REGION", "eu-central-1")
SAGEMAKER_ENDPOINT_NAME = os.environ.get("SAGEMAKER_ENDPOINT_NAME")
QDRANT_URL = os.environ.get("QDRANT_URL")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY")
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION_NAME", config.DEFAULT_COLLECTION_NAME)

DENSE_MODEL_NAME = config.DENSE_MODEL_NAME
SPARSE_MODEL_NAME = config.SPARSE_MODEL_NAME

# Module-scope singletons: in Lambda, these get initialized once per *cold start* and reused
# across warm invocations of the same container -- this is the standard trick to keep Lambda
# fast after the first request (loading the embedding models is the expensive part, not the
# per-request inference).
_dense_model: SentenceTransformer | None = None
_sparse_model: SparseTextEmbedding | None = None
_reranker: CrossEncoder | None = None
_qdrant_client: QdrantClient | None = None
_llm: SagemakerEndpoint | None = None


def _get_dense_model() -> SentenceTransformer:
    global _dense_model
    if _dense_model is None:
        _dense_model = SentenceTransformer(DENSE_MODEL_NAME)
    return _dense_model


def _get_sparse_model() -> SparseTextEmbedding:
    global _sparse_model
    if _sparse_model is None:
        _sparse_model = SparseTextEmbedding(model_name=SPARSE_MODEL_NAME)
    return _sparse_model


def _get_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoder(config.RERANKER_MODEL_NAME)
    return _reranker


def _get_qdrant_client() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        if not QDRANT_URL or not QDRANT_API_KEY:
            raise RuntimeError("QDRANT_URL / QDRANT_API_KEY environment variables are not set.")
        _qdrant_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return _qdrant_client


class _FlanT5ContentHandler(LLMContentHandler):
    """Translates between LangChain's plain-string LLM interface and the JSON payload shape
    the JumpStart FLAN-T5 endpoint actually expects/returns.
    Request:  {"text_inputs": "<prompt>", "max_length": ..., "num_beams": ...}
    Response: {"generated_texts": ["<answer>"]}
    """

    content_type = "application/json"
    accepts = "application/json"

    def transform_input(self, prompt: str, model_kwargs: dict) -> bytes:
        payload = {"text_inputs": prompt, **model_kwargs}
        return json.dumps(payload).encode("utf-8")

    def transform_output(self, output) -> str:
        response = json.loads(output.read())
        return response["generated_texts"][0]


def _get_llm() -> SagemakerEndpoint:
    global _llm
    if _llm is None:
        if not SAGEMAKER_ENDPOINT_NAME:
            raise RuntimeError("SAGEMAKER_ENDPOINT_NAME environment variable is not set.")
        _llm = SagemakerEndpoint(
            endpoint_name=SAGEMAKER_ENDPOINT_NAME,
            region_name=AWS_REGION,
            content_handler=_FlanT5ContentHandler(),
            model_kwargs={
                "max_length": config.GENERATION_MAX_LENGTH,
                "num_beams": config.GENERATION_NUM_BEAMS,
            },
        )
    return _llm


@dataclass
class RetrievedChunk:
    text: str
    score: float  # fused hybrid (RRF) score from Qdrant
    source_file: str
    source_url: str
    rerank_score: float | None = None  # cross-encoder score, set by rerank() if enabled


@dataclass
class RagResult:
    answer: str
    tier: str  # "weak" or "confident"
    prompt_version: str
    sources: list[RetrievedChunk] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def dense_relevance_score(query: str, dense_vec: list[float] | None = None) -> float:
    """Raw dense cosine similarity of the single best match -- used only as the input-side
    relevance gate (see guardrails.check_relevance), separately from the fused hybrid search
    used for context selection below.

    Why not just gate on the fused RRF score? RRF rewards a chunk for ranking highly in
    *either* the dense or sparse list, weighted by 1/(k + rank) -- it has no absolute notion
    of "similar enough." With a small corpus, an off-topic query (e.g. a cookie recipe) still
    ranks *some* chunk highly in the sparse/BM25 list purely on common words, so its fused
    score lands in the same range as genuinely relevant queries -- empirically verified
    against this project's own data before picking this approach. Cosine similarity, by
    contrast, has a real semantic scale (-1..1), so it cleanly separates in-scope from
    out-of-scope questions.
    """
    if dense_vec is None:
        dense_vec = _get_dense_model().encode(query, normalize_embeddings=True).tolist()
    hits = _get_qdrant_client().query_points(
        collection_name=COLLECTION_NAME,
        query=dense_vec,
        using="dense",
        limit=1,
        with_payload=False,
    ).points
    return hits[0].score if hits else 0.0


def hybrid_search(
    query: str,
    candidate_limit: int = config.RERANK_CANDIDATE_LIMIT,
    dense_vec: list[float] | None = None,
) -> list[RetrievedChunk]:
    """Dense + sparse retrieval fused with Reciprocal Rank Fusion (RRF) -- returns a candidate
    pool (config.RERANK_CANDIDATE_LIMIT, wider than the final prompt needs) for rerank() below
    to narrow down, rather than the final context directly (see dense_relevance_score above
    for the separate in-scope/out-of-scope gate, which is unaffected by any of this).

    RRF combines two ranked lists (dense semantic matches, sparse BM25 keyword matches)
    without needing to normalize their very different score scales -- it just rewards chunks
    that rank highly in *either* list, weighted by 1/(k + rank). This is why hybrid search
    tends to beat either method alone: dense catches paraphrases, sparse catches exact terms
    (error codes, resource names, acronyms) that embeddings tend to blur together.
    """
    if dense_vec is None:
        dense_vec = _get_dense_model().encode(query, normalize_embeddings=True).tolist()
    sparse_vec = next(_get_sparse_model().embed([query]))

    hits = _get_qdrant_client().query_points(
        collection_name=COLLECTION_NAME,
        prefetch=[
            models.Prefetch(query=dense_vec, using="dense", limit=config.HYBRID_PREFETCH_LIMIT),
            models.Prefetch(
                query=models.SparseVector(
                    indices=sparse_vec.indices.tolist(), values=sparse_vec.values.tolist()
                ),
                using="sparse",
                limit=config.HYBRID_PREFETCH_LIMIT,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=candidate_limit,
        with_payload=True,
    ).points

    return [
        RetrievedChunk(
            text=hit.payload["text"],
            score=hit.score,
            source_file=hit.payload["source_file"],
            source_url=hit.payload["source_url"],
        )
        for hit in hits
    ]


def rerank(query: str, chunks: list[RetrievedChunk], top_n: int = config.FINAL_TOP_K) -> list[RetrievedChunk]:
    """Cross-encoder reranking. Dense/sparse retrieval score the query and each chunk
    independently (which is what makes them indexable/fast at collection scale); a
    cross-encoder instead scores the (query, chunk) pair jointly in one forward pass --
    slower per-pair and not something you could run over an entire collection, but typically
    more precise. That's why it runs only on hybrid search's already-narrowed candidate pool,
    not the whole collection: precision on a small set, not recall over everything.
    """
    if not chunks:
        return chunks
    reranker = _get_reranker()
    scores = reranker.predict([(query, c.text) for c in chunks])
    for chunk, score in zip(chunks, scores):
        chunk.rerank_score = float(score)
    return sorted(chunks, key=lambda c: c.rerank_score, reverse=True)[:top_n]


def answer_query(query: str, regenerate: bool = False, previous_answer: str | None = None) -> RagResult:
    """Full pipeline: input guardrail -> hybrid retrieval -> cross-encoder rerank ->
    relevance guardrail (2-tier) -> versioned prompt -> SageMaker call -> output guardrail.
    Raises GuardrailRejection if the query or the retrieval relevance fails the input-side
    checks (caller should turn that into a UI-facing error, not a 500).

    regenerate/previous_answer implement self-correction: when the caller (lambda_handler.py,
    after a "not satisfied" signal from the frontend) passes these, the self_correction prompt
    template is used instead of grounded/weak_context -- giving the model its own prior answer
    and an explicit "do better" instruction. Retrieval and the relevance gate still run
    normally; regeneration only ever makes sense following an answer that already passed them.
    Capping how many times this can happen is enforced by the caller (lambda_handler.py,
    against config.MAX_REGENERATIONS), not here -- this function just generates one attempt.

    RAGAS-style quality signals (context relevance, faithfulness) are emitted as CloudWatch
    metrics on every call, including rejected ones, so threshold tuning has real data behind
    it and nothing failed is silently dropped.
    """
    screen_query(query)

    dense_vec = _get_dense_model().encode(query, normalize_embeddings=True).tolist()
    relevance_score = dense_relevance_score(query, dense_vec=dense_vec)
    metrics.put_metric("ContextRelevanceScore", relevance_score)

    tier = check_relevance(relevance_score, query)  # raises GuardrailRejection if out of scope

    candidates = hybrid_search(query, dense_vec=dense_vec)
    chunks = rerank(query, candidates) if config.RERANKER_ENABLED else candidates[: config.FINAL_TOP_K]

    context = "\n\n".join(f"[{i + 1}] {c.text}" for i, c in enumerate(chunks))
    if regenerate and previous_answer:
        prompt = prompts.build_prompt("self_correction", query, context, previous_answer=previous_answer)
    else:
        template_name = "weak_context" if tier == "weak" else "grounded"
        prompt = prompts.build_prompt(template_name, query, context)

    answer = _get_llm().invoke(prompt)

    context_text = "\n".join(c.text for c in chunks)
    warnings = []
    faithfulness_ratio, groundedness_warning = check_groundedness(answer, context_text)
    metrics.put_metric("FaithfulnessScore", faithfulness_ratio)
    if groundedness_warning:
        warnings.append(groundedness_warning)
    if tier == "weak":
        warnings.append("Retrieved context was only weakly relevant to this question.")

    metrics.put_metric("ResponseCount", 1, unit="Count", dimensions={"tier": tier})
    if regenerate:
        metrics.put_metric("SelfCorrectionCount", 1, unit="Count")

    return RagResult(
        answer=answer,
        tier=tier,
        prompt_version=prompts.PROMPT_VERSION,
        sources=chunks,
        warnings=warnings,
    )
