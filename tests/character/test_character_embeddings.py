"""WP-11 tests for app/character/embeddings.py: batched embedding calls.

Frozen test list (playbook §4 WP-11, items 5-6). `embed(texts)` POSTs to
`{base_url}/embeddings` in the OpenAI shape (verified on gx10, plan §11
`recall.embeddings.base_url` ends in /v1), in batches of 32, and returns one
vector per text in input order. D-20: embeddings are computed at fragment
creation and per beat in the live driver, never at ingest. The HTTP layer is
an httpx.MockTransport; no network.
"""
import pytest

from fakes_generator import RecordingTransport, embeddings_reply
from pending import require

embeddings = require("character.embeddings", "app/character/embeddings.py", wp="WP-11")
from character import config  # noqa: E402  (promoted in WP-03, before WP-11)

BASE_URL = "http://ollama.test:11434/v1"
MODEL = "nomic-embed-text"


def _vector_for(text, dim=4):
    """A deterministic vector that encodes the text's number, to check order."""
    number = float(text.split("-")[1])
    return [number] + [0.5] * (dim - 1)


def _echo(body):
    """Reply with one vector per input, in REVERSED data order but correct indexes."""
    data = embeddings_reply([_vector_for(t) for t in body["input"]], model=body["model"])
    data["data"].reverse()
    return data


# T11.5
def test_embed_batches_70_texts_as_3_calls_keeping_order():
    texts = [f"beat-{i}" for i in range(70)]
    transport = RecordingTransport([_echo])
    with transport.client() as client:
        vectors = embeddings.embed(texts, base_url=BASE_URL, model=MODEL, client=client)
    assert [len(r["json"]["input"]) for r in transport.requests] == [32, 32, 6]
    assert all(r["url"] == f"{BASE_URL}/embeddings" for r in transport.requests)
    assert all(r["method"] == "POST" and r["json"]["model"] == MODEL for r in transport.requests)
    assert [v[0] for v in vectors] == [float(i) for i in range(70)]
    assert embeddings.BATCH_SIZE == 32


# T11.5 (nothing to embed -> no call)
def test_embed_empty_list_makes_no_call():
    transport = RecordingTransport([_echo])
    with transport.client() as client:
        assert embeddings.embed([], base_url=BASE_URL, model=MODEL, client=client) == []
    assert transport.requests == []


# T11.6
def test_embed_rejects_a_response_with_the_wrong_count():
    short = lambda body: embeddings_reply([_vector_for(t) for t in body["input"][:-1]])  # noqa: E731
    transport = RecordingTransport([short])
    with transport.client() as client, pytest.raises(embeddings.EmbeddingError) as err:
        embeddings.embed(["beat-1", "beat-2", "beat-3"], base_url=BASE_URL, model=MODEL,
                         client=client)
    assert "3" in str(err.value) and "2" in str(err.value)


# T11.6 (a vector of the wrong dimension, and an HTTP error, are also rejected)
def test_embed_rejects_wrong_dimension_and_http_errors():
    transport = RecordingTransport([_echo])
    with transport.client() as client, pytest.raises(embeddings.EmbeddingError):
        embeddings.embed(["beat-1"], base_url=BASE_URL, model=MODEL, client=client, dim=768)
    transport = RecordingTransport([(500, "boom")])
    with transport.client() as client, pytest.raises(embeddings.EmbeddingError):
        embeddings.embed(["beat-1"], base_url=BASE_URL, model=MODEL, client=client)


# T11.5 (the config-driven wrapper: base_url, model and dim from recall.embeddings)
def test_embedder_uses_recall_embeddings_config():
    cfg = config.load(env={}).recall.embeddings
    reply = lambda body: embeddings_reply([[0.1] * cfg.dim for _ in body["input"]])  # noqa: E731
    transport = RecordingTransport([reply])
    with transport.client() as client:
        vectors = embeddings.Embedder(cfg, client=client).embed(["one", "two"])
    assert len(vectors) == 2 and all(len(v) == cfg.dim for v in vectors)
    assert transport.requests[0]["url"] == cfg.base_url.rstrip("/") + "/embeddings"
    assert transport.requests[0]["json"]["model"] == cfg.model
