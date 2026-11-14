"""The shared embedding endpoint backend, tested offline with a fake HTTP client (no network, no key)."""
import math

import pytest

from lbs_rag.index import embed as E


class Resp:
    def __init__(self, status=200, vectors=None, text=""):
        self.status_code, self._v, self.text = status, vectors, text

    def json(self):
        return {"data": [{"index": i, "embedding": v} for i, v in enumerate(self._v)]}


class FakeHttp:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def post(self, path, json):
        self.calls.append((path, json))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def vec(seed):
    return [float(seed + 1 + i % 5) for i in range(E.ENDPOINT_DIM)]


@pytest.fixture()
def fake(monkeypatch):
    def install(responses):
        f = FakeHttp(responses)
        monkeypatch.setattr(E, "_http", lambda: f)
        monkeypatch.setattr(E.settings, "embed_backend", "endpoint")
        return f
    return install


def test_vectors_come_back_in_input_order_and_normalised(fake):
    f = fake([Resp(vectors=[vec(0), vec(1)])])
    out = E._post_batch(["a", "b"], sleep=lambda s: None)
    assert len(out) == 2 and all(abs(math.sqrt(sum(x * x for x in v)) - 1) < 1e-9 for v in out)
    assert f.calls[0][0] == "/embeddings" and f.calls[0][1]["input"] == ["a", "b"] and f.calls[0][1]["model"] == "bge-small-en-v1.5"


def test_results_are_reordered_by_index_even_if_the_server_shuffles_them(fake, monkeypatch):
    class Shuffled(Resp):
        def json(self):
            return {"data": [{"index": 1, "embedding": self._v[1]}, {"index": 0, "embedding": self._v[0]}]}
    fake([Shuffled(vectors=[[1.0] + [0.0] * 383, [0.0, 1.0] + [0.0] * 382])])
    a, b = E._post_batch(["first", "second"], sleep=lambda s: None)
    assert a[0] == 1.0 and b[1] == 1.0


def test_capacity_errors_are_retried_then_succeed(fake):
    sleeps = []
    f = fake([Resp(429), Resp(503), Resp(vectors=[vec(0)])])
    out = E._post_batch(["x"], sleep=sleeps.append)
    assert len(out) == 1 and len(f.calls) == 3 and len(sleeps) == 2 and sleeps[1] > sleeps[0]          # back-off grows


def test_dropped_connections_are_retried(fake):
    import httpx
    f = fake([httpx.ConnectError("boom"), Resp(vectors=[vec(0)])])
    assert len(E._post_batch(["x"], sleep=lambda s: None)) == 1 and len(f.calls) == 2


def test_gives_up_with_a_clear_error_after_the_retries(fake):
    fake([Resp(503)] * 3)
    with pytest.raises(E.EmbeddingError, match="unavailable after 3 attempts"):
        E._post_batch(["x"], retries=2, sleep=lambda s: None)


def test_client_errors_are_not_retried(fake):
    f = fake([Resp(401, text="bad key")])
    with pytest.raises(E.EmbeddingError, match="HTTP 401"):
        E._post_batch(["x"], sleep=lambda s: None)
    assert len(f.calls) == 1


def test_wrong_dimension_or_count_is_refused(fake):
    fake([Resp(vectors=[[1.0] * 10])])
    with pytest.raises(E.EmbeddingError, match="384"):
        E._post_batch(["x"], sleep=lambda s: None)
    fake([Resp(vectors=[vec(0)])])
    with pytest.raises(E.EmbeddingError, match="got 1"):
        E._post_batch(["x", "y"], sleep=lambda s: None)


def test_documents_are_sent_in_batches_of_at_most_16(fake):
    f = fake([Resp(vectors=[vec(i) for i in range(16)]), Resp(vectors=[vec(i) for i in range(16)]), Resp(vectors=[vec(0)] * 3)])
    out = E.embed_docs([f"t{i}" for i in range(35)])
    assert len(out) == 35 and [len(c[1]["input"]) for c in f.calls] == [16, 16, 3]


def test_queries_get_the_bge_prefix_and_documents_do_not(fake):
    f = fake([Resp(vectors=[vec(0)]), Resp(vectors=[vec(0)])])
    E.embed_query("how much is it")
    E.embed_docs(["a document"])
    assert f.calls[0][1]["input"][0].startswith(E.QUERY_PREFIX) and f.calls[1][1]["input"] == ["a document"]


def test_each_backend_has_its_own_collection(monkeypatch):
    from lbs_rag.index import vector_store as V
    monkeypatch.setattr(E.settings, "embed_backend", "local")
    assert V.collection_name() == "lbs_chunks"
    monkeypatch.setattr(E.settings, "embed_backend", "endpoint")
    assert V.collection_name() == "lbs_chunks_endpoint"
    assert E.backend_info()["dim"] == 384


def test_missing_configuration_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(E.settings, "embed_base_url", "")
    E._http.cache_clear()
    with pytest.raises(E.EmbeddingError, match="EMBED_BASE_URL"):
        E._http()
