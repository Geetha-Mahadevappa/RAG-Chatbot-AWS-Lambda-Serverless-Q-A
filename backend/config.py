"""
Loads config.yaml -- the single source of truth for RAG pipeline tuning parameters (chunk
size, hybrid search limits, reranker model, relevance thresholds, generation params). Every
other module (rag_core, guardrails, ingest_core, and scripts/resolve_jumpstart_metadata.py)
reads its tunable constants from here instead of hardcoding its own.
"""

from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

with open(_CONFIG_PATH) as _f:
    _config = yaml.safe_load(_f)

DENSE_MODEL_NAME = _config["embeddings"]["dense_model"]
SPARSE_MODEL_NAME = _config["embeddings"]["sparse_model"]

RERANKER_ENABLED = _config["reranker"]["enabled"]
RERANKER_MODEL_NAME = _config["reranker"]["model"]

CHUNK_SIZE = _config["chunking"]["chunk_size"]
CHUNK_OVERLAP = _config["chunking"]["chunk_overlap"]

HYBRID_PREFETCH_LIMIT = _config["retrieval"]["hybrid_prefetch_limit"]
RERANK_CANDIDATE_LIMIT = _config["retrieval"]["rerank_candidate_limit"]
FINAL_TOP_K = _config["retrieval"]["final_top_k"]

REJECT_THRESHOLD = _config["relevance_gate"]["reject_threshold"]
CONFIDENT_THRESHOLD = _config["relevance_gate"]["confident_threshold"]

MAX_QUERY_LENGTH = _config["guardrails"]["max_query_length"]
MIN_GROUNDEDNESS_RATIO = _config["guardrails"]["min_groundedness_ratio"]

SAGEMAKER_MODEL_ID = _config["generation"]["sagemaker_model_id"]
GENERATION_MAX_LENGTH = _config["generation"]["max_length"]
GENERATION_NUM_BEAMS = _config["generation"]["num_beams"]

DEFAULT_COLLECTION_NAME = _config["qdrant"]["default_collection_name"]

MAX_REGENERATIONS = _config["self_correction"]["max_regenerations"]
IDEMPOTENCY_TTL_SECONDS = _config["idempotency"]["ttl_seconds"]
INGESTION_UPSERT_BATCH_SIZE = _config["ingestion"]["upsert_batch_size"]
