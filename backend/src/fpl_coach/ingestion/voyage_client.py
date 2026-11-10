import os

import httpx

DEFAULT_BASE_URL = "https://api.voyageai.com"
_MODEL = "voyage-3"
EMBEDDING_DIMENSION = 1024


class VoyageClient:
    """Wraps Voyage AI's embeddings endpoint (docs.voyageai.com, voyage-3,
    1024-dim, confirmed against a real call 2026-08-18).

    Takes an httpx.Client so tests can fake it at its HTTP boundary
    (httpx.MockTransport with canned JSON), same convention as
    GrokClient/FPLClient/UnderstatClient.
    """

    def __init__(self, http_client: httpx.Client | None = None, api_key: str | None = None) -> None:
        self._client = http_client or httpx.Client(
            base_url=DEFAULT_BASE_URL,
            timeout=30.0,
            headers={"Authorization": f"Bearer {api_key or os.environ['VOYAGE_API_KEY']}"},
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Returns one EMBEDDING_DIMENSION-length vector per input text, in
        the same order. Voyage batches multiple inputs in one call."""
        if not texts:
            return []
        response = self._client.post(
            "/v1/embeddings", json={"input": texts, "model": _MODEL}
        )
        response.raise_for_status()
        data = response.json()["data"]
        return [item["embedding"] for item in data]


def vector_literal(embedding: list[float]) -> str:
    """psycopg has no built-in adapter for pgvector's `vector` type -- pass
    this string and cast with `%s::vector` at the call site instead of
    adding the `pgvector` package for one conversion (creator_pipeline.py,
    tools/search_creator_opinions.py)."""
    return "[" + ",".join(str(x) for x in embedding) + "]"
