"""
Full-collection rebuild: wipes and re-creates the Qdrant collection, then re-chunks and
re-embeds every file in data/ from scratch.

The event-driven pipeline (S3 -> backend/ingest_handler.py) only reacts to ONE changed file
at a time and never deletes the whole collection -- use this script instead when you need
everything reprocessed at once: after changing the embedding model, the chunking strategy,
or for disaster recovery. Uses the exact same chunk/embed/upsert logic
(backend/ingest_core.py) as the Lambda-based pipeline, just looped over every local file
instead of triggered by a single S3 event.

Run: python scripts/backfill_reindex_qdrant.py
Cost: $0 -- local compute + the Qdrant free tier. Requires QDRANT_URL / QDRANT_API_KEY set
as real environment variables (no .env file -- see README).
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from fastembed import SparseTextEmbedding  # noqa: E402
from qdrant_client import QdrantClient  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

import ingest_core  # noqa: E402

DATA_DIR = ROOT / "data"
QDRANT_URL = os.environ.get("QDRANT_URL")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY")
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION_NAME", "aws_lambda_docs")


def main():
    if not QDRANT_URL or not QDRANT_API_KEY:
        sys.exit(
            "QDRANT_URL / QDRANT_API_KEY are not set. Export them as real environment "
            "variables first (see README) -- this project does not use a .env file."
        )

    files = sorted(DATA_DIR.glob("*.txt"))
    print(f"Rebuilding collection '{COLLECTION_NAME}' from {len(files)} file(s) in {DATA_DIR} ...")

    print(f"Loading dense embedding model ({ingest_core.DENSE_MODEL_NAME}) ...")
    dense_model = SentenceTransformer(ingest_core.DENSE_MODEL_NAME)
    print(f"Loading sparse embedding model ({ingest_core.SPARSE_MODEL_NAME}) ...")
    sparse_model = SparseTextEmbedding(model_name=ingest_core.SPARSE_MODEL_NAME)

    print(f"Connecting to Qdrant at {QDRANT_URL} ...")
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

    if client.collection_exists(COLLECTION_NAME):
        print(f"Collection '{COLLECTION_NAME}' already exists -- deleting for a clean rebuild.")
        client.delete_collection(COLLECTION_NAME)
    ingest_core.ensure_collection(client, COLLECTION_NAME, dense_model.get_embedding_dimension())

    total = 0
    for path in files:
        raw_text = path.read_text(encoding="utf-8")
        n = ingest_core.embed_and_upsert_file(
            client, dense_model, sparse_model, COLLECTION_NAME, path.name, raw_text
        )
        print(f"  {path.name}: {n} chunks")
        total += n

    count = client.count(COLLECTION_NAME).count
    print(f"\nDone. Wrote {total} chunks; collection now has {count} points total.")


if __name__ == "__main__":
    main()
