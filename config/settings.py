"""Central settings, loaded from .env (never committed), plus the model-call counter used to measure cost per route."""
import contextlib
import contextvars
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    openai_api_key: str = ""
    # Project-provided OpenAI-compatible gateway
    openai_base_url: str = ""   # set OPENAI_BASE_URL in .env (the gateway address is deliberately not committed)
    # The gateway is a bare IP, so its TLS cert probably won't validate. Prefer a CA bundle
    # path in openai_ca_bundle; set verify_tls=false only if you accept the risk.
    openai_verify_tls: bool = True
    openai_ca_bundle: str = ""

    # The gateway only serves Qwen/Qwen3-8B (chat); /embeddings returns 404 in this pilot,
    # so embeddings run locally with sentence-transformers.
    chat_model: str = "Qwen/Qwen3-8B"
    embed_model: str = "BAAI/bge-base-en-v1.5"


settings = Settings()


# ---------------------------------------------------------------------------------------------- model-call counting
_COUNTER: contextvars.ContextVar = contextvars.ContextVar("model_call_counter", default=None)


class _Counter:
    """Counts SUCCESSFUL model requests (HTTP 2xx on chat/completions or embeddings). Nested counters all see every call, so a message-level
    counter and an answer-level counter agree. Retries after a 429 are not counted: they are the same logical call."""

    def __init__(self, parent=None):
        self.count, self.parent = 0, parent

    def inc(self):
        self.count += 1
        if self.parent is not None:
            self.parent.inc()


@contextlib.contextmanager
def count_model_calls():
    c = _Counter(parent=_COUNTER.get())
    token = _COUNTER.set(c)
    try:
        yield c
    finally:
        _COUNTER.reset(token)


def _on_response(response) -> None:
    c = _COUNTER.get()
    if c is not None and 200 <= response.status_code < 300 and response.request.url.path.endswith(("/chat/completions", "/embeddings")):
        c.inc()


EVENT_HOOKS = {"response": [_on_response]}


def openai_client():
    """OpenAI client pointed at the project gateway."""
    import httpx
    from openai import OpenAI

    verify = settings.openai_ca_bundle or settings.openai_verify_tls
    return OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url,
                  http_client=httpx.Client(verify=verify, timeout=120, event_hooks=EVENT_HOOKS),
                  max_retries=10)   # gateway allows 1 concurrent request per key; SDK backs off on 429
