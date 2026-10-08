import os
from dataclasses import dataclass
from functools import lru_cache

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from dotenv import load_dotenv

from mandate import crypto

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./mandate.db")


@dataclass(frozen=True)
class Settings:
    paypal_base_url: str
    paypal_client_id: str
    paypal_client_secret: str
    payment_provider: str
    private_key_b64: str
    public_key_b64: str
    council_enabled: bool
    ollama_url: str
    ollama_model: str
    intent_models: tuple[str, ...]
    llm_timeout: float
    risk_enabled: bool
    embedder: str
    embedding_model: str


@lru_cache
def get_settings() -> Settings:
    ollama_model = os.environ.get("OLLAMA_MODEL", "llama3.1")
    intent_models = tuple(
        m.strip() for m in os.environ.get("INTENT_MODELS", ollama_model).split(",") if m.strip()
    )
    return Settings(
        paypal_base_url=os.environ.get("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com"),
        paypal_client_id=os.environ.get("PAYPAL_CLIENT_ID", ""),
        paypal_client_secret=os.environ.get("PAYPAL_CLIENT_SECRET", ""),
        payment_provider=os.environ.get("PAYMENT_PROVIDER", "paypal"),
        private_key_b64=os.environ.get("MANDATE_PRIVATE_KEY", ""),
        public_key_b64=os.environ.get("MANDATE_PUBLIC_KEY", ""),
        council_enabled=os.environ.get("COUNCIL_ENABLED", "true").lower() in ("1", "true", "yes"),
        ollama_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        ollama_model=ollama_model,
        intent_models=intent_models,
        llm_timeout=float(os.environ.get("LLM_TIMEOUT", "90")),
        risk_enabled=os.environ.get("RISK_ENABLED", "true").lower() in ("1", "true", "yes"),
        embedder=os.environ.get("EMBEDDER", "auto"),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
    )


def get_private_key() -> Ed25519PrivateKey:
    value = get_settings().private_key_b64
    if not value:
        raise RuntimeError("MANDATE_PRIVATE_KEY is not set")
    return crypto.private_key_from_b64(value)


def get_public_key() -> Ed25519PublicKey:
    value = get_settings().public_key_b64
    if not value:
        raise RuntimeError("MANDATE_PUBLIC_KEY is not set")
    return crypto.public_key_from_b64(value)
