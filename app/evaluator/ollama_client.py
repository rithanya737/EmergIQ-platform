"""
Thin wrapper around the local Ollama HTTP API (default: http://localhost:11434).

Ollama exposes two endpoints we care about:
  - POST /api/embeddings  -> turn text into a vector (for Chroma)
  - POST /api/generate    -> run a chat/completion model (for the evaluator's judgment)

Before using this, pull the models on the machine running Ollama:
    ollama pull nomic-embed-text     # embedding model
    ollama pull llama3.1             # or mistral / phi3 / any chat model you prefer
"""
import time

import requests
from app.config import OLLAMA_HOST

EMBED_MODEL = "nomic-embed-text"
CHAT_MODEL = "llama3.2:1b"

TIMEOUT = 60  # seconds; local LLM calls can be slow on CPU

# is_reachable() is called on every single decision submission — without
# caching, a down/slow Ollama means every decision (and, on the single-
# threaded dev server, every other concurrent request too) eats a real
# network timeout. Cache the result briefly so at most one check per
# CHECK_INTERVAL actually hits the network.
CHECK_INTERVAL = 15  # seconds
_last_check = {"at": 0.0, "reachable": False}


def get_embedding(text: str, model: str = EMBED_MODEL) -> list[float]:
    """Return the embedding vector for `text` using Ollama's embeddings endpoint."""
    resp = requests.post(
        f"{OLLAMA_HOST}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def generate(prompt: str, model: str = CHAT_MODEL, system: str | None = None) -> str:
    """
    Run a single-shot generation and return the raw text response.
    Uses stream=False so we get one JSON object back instead of a stream of chunks.
    """
    payload = {"model": model, "prompt": prompt, "stream": False}
    if system:
        payload["system"] = system

    resp = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()["response"]


def is_reachable() -> bool:
    """Quick health check used by requirements_check.py and before every
    decision judgment. Result is cached for CHECK_INTERVAL seconds so a
    down Ollama doesn't add a network round-trip (and its timeout) to
    every request while it's unreachable."""
    now = time.monotonic()
    if now - _last_check["at"] < CHECK_INTERVAL:
        return _last_check["reachable"]
    try:
        requests.get(f"{OLLAMA_HOST}/api/tags", timeout=2)
        reachable = True
    except requests.RequestException:
        reachable = False
    _last_check["at"] = now
    _last_check["reachable"] = reachable
    return reachable
