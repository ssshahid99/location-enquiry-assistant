"""Central settings, loaded from .env (never committed)."""
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


def openai_client():
    """OpenAI client pointed at the project gateway."""
    import httpx
    from openai import OpenAI

    verify = settings.openai_ca_bundle or settings.openai_verify_tls
    return OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url,
                  http_client=httpx.Client(verify=verify, timeout=120),
                  max_retries=10)   # gateway allows 1 concurrent request per key; SDK backs off on 429
