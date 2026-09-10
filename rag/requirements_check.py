"""
Run this first: `python rag/requirements_check.py`
Verifies Ollama is running + models are pulled, and that chromadb is importable.
"""
import sys

def check_ollama():
    from app.evaluator.ollama_client import is_reachable, EMBED_MODEL, CHAT_MODEL
    import requests
    from app.config import OLLAMA_HOST

    if not is_reachable():
        print(f"[FAIL] Ollama not reachable at {OLLAMA_HOST}. Start it with `ollama serve`.")
        return False

    tags = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5).json().get("models", [])
    names = [m["name"] for m in tags] + [m["name"].split(":")[0] for m in tags]

    ok = True
    for required in (EMBED_MODEL, CHAT_MODEL):
        if required not in names:
            print(f"[FAIL] Model '{required}' not pulled. Run: ollama pull {required}")
            ok = False
        else:
            print(f"[OK] Model '{required}' available.")
    return ok


def check_chroma():
    try:
        import chromadb  # noqa: F401
        print("[OK] chromadb importable.")
        return True
    except ImportError:
        print("[FAIL] chromadb not installed. Run: pip install chromadb")
        return False


if __name__ == "__main__":
    results = [check_ollama(), check_chroma()]
    if all(results):
        print("\nAll checks passed. You're ready to run build_index.py")
        sys.exit(0)
    else:
        print("\nFix the issues above before continuing.")
        sys.exit(1)
