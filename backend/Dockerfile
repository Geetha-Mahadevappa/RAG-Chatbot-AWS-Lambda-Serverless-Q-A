# Lambda container image (not a zip deployment) -- torch + sentence-transformers +
# langchain-aws + qdrant-client + fastembed comfortably exceed the 250MB zip package limit,
# so we package as a container instead. Lambda's container support has no extra cost over
# zip packages; you only pay for image storage in ECR (first 500MB/month free for 12 months,
# then ~$0.10/GB-month -- a few cents at most for an image this size).
FROM public.ecr.aws/lambda/python:3.10

# CPU-only torch wheel: the default PyPI wheel bundles ~2GB of CUDA libraries we don't need
# for CPU-only sentence-transformers inference inside Lambda. Installing from PyTorch's CPU
# wheel index shrinks the final image from multi-GB down to roughly 1.5-2GB.
COPY requirements.txt .
RUN pip install --no-cache-dir \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -r requirements.txt

# config.yaml has to be present before this step -- model names (dense/sparse/reranker) are
# read from it, not hardcoded, so the pre-baked models below must match what config.py
# resolves at runtime.
COPY config.yaml config.py .

# Pre-download the embedding + reranker models at BUILD time, not first-request time.
# Lambda's filesystem is read-only at runtime except /tmp, and cold starts are already
# dominated by model-load time -- baking the model weights into the image avoids adding a
# network fetch (and a dependency on Hugging Face Hub being reachable) on top of that.
RUN python -c "import config; from sentence_transformers import SentenceTransformer; SentenceTransformer(config.DENSE_MODEL_NAME)"
RUN python -c "import config; from fastembed import SparseTextEmbedding; SparseTextEmbedding(model_name=config.SPARSE_MODEL_NAME)"
RUN python -c "import config; from sentence_transformers import CrossEncoder; CrossEncoder(config.RERANKER_MODEL_NAME)"

# Both Lambda functions (Q&A + ingestion) share this one image -- Terraform selects which
# handler each function runs via image_config.command, so only one COPY/CMD set is needed.
COPY rag_core.py guardrails.py lambda_handler.py prompts.py metrics.py ingest_core.py ingest_handler.py schemas.py idempotency.py ingestion_state.py ${LAMBDA_TASK_ROOT}/

# Default handler if the image is run without a command override; Terraform overrides this
# to ingest_handler.lambda_handler for the ingestion function.
CMD ["lambda_handler.lambda_handler"]
