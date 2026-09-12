"""
Shared ingestion logic: chunk a single document's text, embed it (dense + sparse), and
incrementally update Qdrant -- used by both backend/ingest_handler.py (S3-event-triggered,
one file at a time) and scripts/backfill_reindex_qdrant.py (manual full-collection rebuild).

Incremental update strategy: on any change to a source file, delete every existing point
whose payload.source_file matches that file, then re-chunk/embed/upsert it fresh. This is
simpler and safer than trying to diff old vs new chunks, and correctly handles a file that
shrinks (fewer chunks than before -- a diff-free upsert would leave orphaned old chunks with
higher indices behind). Editing one file never touches another file's vectors.

Point IDs are deterministic (uuid5 of "source_file:chunk_index"), not sequential integers --
sequential IDs only work when you re-embed the *entire* collection at once starting from 0,
which breaks the moment two different files are ingested by two different Lambda invocations
that don't know about each other's counters. uuid5 makes re-ingesting the same file idempotent
(same content + position -> same ID -> a clean overwrite) and collision-safe across files.
"""

import uuid

from qdrant_client import QdrantClient, models

import config

DENSE_MODEL_NAME = config.DENSE_MODEL_NAME
SPARSE_MODEL_NAME = config.SPARSE_MODEL_NAME
CHUNK_SIZE = config.CHUNK_SIZE  # characters per chunk
CHUNK_OVERLAP = config.CHUNK_OVERLAP  # characters of overlap between consecutive chunks

_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "rag-chatbot.qdrant-point-ids")


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Simple fixed-size sliding-window chunker with overlap, splitting on paragraph
    boundaries where possible so we don't cut mid-sentence more than necessary."""
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 1 <= chunk_size:
            current = f"{current}\n{para}" if current else para
        else:
            if current:
                chunks.append(current)
            overlap_text = current[-overlap:] if current else ""
            current = f"{overlap_text}\n{para}" if overlap_text else para
    if current:
        chunks.append(current)
    return chunks


def parse_document(raw_text: str, fallback_title: str) -> tuple[str, str, str]:
    """Our doc files start with 'Source: <url>\\nTitle: <title>\\n\\n<body>'. Returns
    (source_url, title, body)."""
    lines = raw_text.split("\n", 3)
    source_url = lines[0].removeprefix("Source: ") if lines[0].startswith("Source: ") else ""
    title = lines[1].removeprefix("Title: ") if len(lines) > 1 and lines[1].startswith("Title: ") else fallback_title
    body = lines[3] if len(lines) > 3 else raw_text
    return source_url, title, body


def ensure_collection(client: QdrantClient, collection_name: str, dense_dim: int) -> None:
    """Idempotent: creates the collection only if it doesn't already exist. Deliberately does
    NOT delete/recreate an existing collection -- that would wipe every other file's chunks
    every time a single file changes.

    Also creates a payload index on source_file -- Qdrant requires an explicit index to
    filter-delete by a payload field (delete_file below does exactly that); without it,
    every incremental re-ingest would fail with a 400 the first time it tried to clear out a
    file's old chunks.
    """
    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config={"dense": models.VectorParams(size=dense_dim, distance=models.Distance.COSINE)},
            sparse_vectors_config={"sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        client.create_payload_index(
            collection_name=collection_name,
            field_name="source_file",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )


def delete_file(client: QdrantClient, collection_name: str, source_key: str) -> None:
    """Deletes every point belonging to source_key (e.g. on S3 ObjectRemoved)."""
    client.delete(
        collection_name=collection_name,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(key="source_file", match=models.MatchValue(value=source_key))]
            )
        ),
    )


def embed_and_upsert_file(
    client: QdrantClient,
    dense_model,
    sparse_model,
    collection_name: str,
    source_key: str,
    raw_text: str,
    s3_last_modified: str | None = None,
    on_chunked=None,
    on_progress=None,
) -> int:
    """Re-embeds a single file: deletes its existing chunks, chunks + embeds the new content,
    upserts fresh points in batches. Returns the number of chunks written (0 if empty).

    on_chunked(total_chunks) fires once, right after chunking, before any embedding/upsert
    work -- lets a caller (ingest_handler.py) record "this file has N chunks" up front.
    on_progress(processed_chunks) fires after each upsert batch -- lets a caller record
    incremental progress. Both optional and unused by scripts/backfill_reindex_qdrant.py's
    plain rebuild, which doesn't need per-file job tracking.

    Point IDs are deterministic (uuid5), so re-running this after a crash mid-file just
    re-upserts everything from batch 0 -- safe and idempotent, not something that needs
    exact resume-from-chunk-N logic. Batching exists so a caller CAN observe progress and so
    a single file's upsert isn't one giant all-or-nothing network call.
    """
    source_url, title, body = parse_document(raw_text, fallback_title=source_key)
    chunks = chunk_text(body)

    delete_file(client, collection_name, source_key)
    if on_chunked:
        on_chunked(len(chunks))
    if not chunks:
        return 0

    dense_vectors = dense_model.encode(chunks, normalize_embeddings=True)
    sparse_vectors = list(sparse_model.embed(chunks))

    batch_size = config.INGESTION_UPSERT_BATCH_SIZE
    processed = 0
    for batch_start in range(0, len(chunks), batch_size):
        batch = zip(
            range(batch_start, min(batch_start + batch_size, len(chunks))),
            chunks[batch_start : batch_start + batch_size],
            dense_vectors[batch_start : batch_start + batch_size],
            sparse_vectors[batch_start : batch_start + batch_size],
        )
        points = [
            models.PointStruct(
                id=str(uuid.uuid5(_ID_NAMESPACE, f"{source_key}:{i}")),
                vector={
                    "dense": dense_vec.tolist(),
                    "sparse": models.SparseVector(indices=sparse_vec.indices.tolist(), values=sparse_vec.values.tolist()),
                },
                payload={
                    "text": chunk,
                    "source_file": source_key,
                    "source_url": source_url,
                    "title": title,
                    "chunk_index": i,
                    "s3_last_modified": s3_last_modified,
                },
            )
            for i, chunk, dense_vec, sparse_vec in batch
        ]
        client.upsert(collection_name=collection_name, points=points)
        processed += len(points)
        if on_progress:
            on_progress(processed)

    return len(chunks)
