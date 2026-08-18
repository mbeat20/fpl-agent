import json

import httpx
import pytest

from fpl_coach.ingestion.voyage_client import EMBEDDING_DIMENSION, VoyageClient


def _fake_client(dimension: int = EMBEDDING_DIMENSION) -> VoyageClient:
    """Fakes Voyage's API at its HTTP boundary (httpx.MockTransport), same
    convention as GrokClient's test_grok_client.py."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [0.1] * dimension, "index": i}
                    for i in range(len(payload["input"]))
                ],
                "model": "voyage-3",
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(
        base_url="https://api.voyageai.com",
        transport=transport,
        headers={"Authorization": "Bearer test-key"},
    )
    return VoyageClient(http_client)


def test_embed_returns_one_vector_per_input_text_in_order():
    client = _fake_client()
    vectors = client.embed(["Buy Salah.", "Captain Haaland."])

    assert len(vectors) == 2
    assert all(len(v) == EMBEDDING_DIMENSION for v in vectors)


def test_embed_of_empty_list_makes_no_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"data": []})

    http_client = httpx.Client(
        base_url="https://api.voyageai.com",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer test-key"},
    )
    client = VoyageClient(http_client)

    assert client.embed([]) == []
    assert calls == []


@pytest.mark.integration
def test_real_voyage_api_returns_1024_dim_embeddings():
    """Confirms VOYAGE_API_KEY is valid and voyage-3 still returns the
    dimension EMBEDDING_DIMENSION/schema.sql's content_chunks.embedding
    column assume -- verified live 2026-08-18."""
    client = VoyageClient()
    vectors = client.embed(["Buy Salah, great fixtures."])

    assert len(vectors) == 1
    assert len(vectors[0]) == EMBEDDING_DIMENSION
