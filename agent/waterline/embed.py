"""Load-bearing Vertex embedding adapter for owner-scoped destination recall."""
from __future__ import annotations

import hashlib
import math
import os

from google import genai
from google.genai import types

from .config import vertex_model_client_kwargs


EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768


def _fixture_embedding(text: str) -> list[float]:
    """Deterministic local-only vector; live deployment never selects this path."""
    seed = hashlib.sha256(text.strip().casefold().encode()).digest()
    values = [((seed[index % len(seed)] / 255.0) * 2.0) - 1.0
              for index in range(EMBEDDING_DIMENSIONS)]
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def embed_destination(destination: str, *, task_type: str) -> list[float]:
    """Embed a destination at the schema-pinned dimensionality."""
    if os.environ.get("WATERLINE_EMBEDDING_MODE") == "fixture":
        return _fixture_embedding(destination)
    client_kwargs = vertex_model_client_kwargs()
    if client_kwargs is None:
        raise RuntimeError("destination recall requires Vertex AI embeddings")
    client = genai.Client(
        **client_kwargs,
        http_options=types.HttpOptions(
            api_version="v1",
            timeout=int(os.environ.get("WATERLINE_EMBEDDING_TIMEOUT_MS", "15000")),
        ),
    )
    try:
        response = client.models.embed_content(
            model=os.environ.get("WATERLINE_EMBEDDING_MODEL", EMBEDDING_MODEL),
            contents=destination,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=EMBEDDING_DIMENSIONS,
                auto_truncate=False,
            ),
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    embeddings = response.embeddings or []
    values = embeddings[0].values if embeddings else None
    if values is None or len(values) != EMBEDDING_DIMENSIONS:
        raise RuntimeError("Vertex returned an invalid destination embedding")
    return list(values)
