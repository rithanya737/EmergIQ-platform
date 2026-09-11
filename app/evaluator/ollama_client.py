# Thin wrapper around the local Ollama HTTP API for embeddings, generation, and health checks
import time

import requests
from app.config import OLLAMA_HOST

EMBED_MODEL = "nomic-embed-text"
CHAT_MODEL = "llama3.2:1b"

TIMEOUT = 60

CHECK_INTERVAL = 15
_last_check = {"at": 0.0, "reachable": False}


# Embeds text into a vector via Ollama's embeddings endpoint
def get_embedding(text: str, model: str = EMBED_MODEL) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_HOST}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


# Runs a single-shot chat/completion generation and returns the raw text response
def generate(prompt: str, model: str = CHAT_MODEL, system: str | None = None) -> str:
    payload = {"model": model, "prompt": prompt, "stream": False}
    if system:
        payload["system"] = system

    resp = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()["response"]


# Cached health check so a down Ollama doesn't add a network timeout to every request
def is_reachable() -> bool:
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
