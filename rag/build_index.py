"""
One-time (rerun whenever protocol docs change) indexing script.

What it does:
  1. Loads every JSON file in data/protocols/
  2. Chunks each doc into small text passages (so retrieval returns focused context,
     not an entire document)
  3. Embeds each chunk with Ollama (nomic-embed-text)
  4. Upserts the chunks + embeddings into a persistent Chroma collection

Run with: `python rag/build_index.py`
"""
import json
import glob
import os

import chromadb

from app.config import CHROMA_PATH
from app.evaluator.ollama_client import get_embedding

COLLECTION_NAME = "clinical_protocols"


def load_protocol_docs(protocols_dir: str = "data/protocols") -> list[dict]:
    """Read every *.json file in data/protocols and return them as dicts."""
    docs = []
    for path in glob.glob(os.path.join(protocols_dir, "*.json")):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            data["_source_file"] = os.path.basename(path)
            docs.append(data)
    return docs


def chunk_protocol(doc: dict) -> list[str]:
    """
    Turn one protocol JSON doc into a list of short, self-contained text chunks.

    Strategy: one chunk per meaningful sub-item (a step, a level, a criterion)
    plus one chunk for the overall purpose. Keeping chunks small and focused
    means the retriever can surface exactly the relevant step instead of
    dumping the whole protocol into the LLM's context.
    """
    chunks = []
    title = doc.get("protocol", doc.get("_source_file", "protocol"))
    purpose = doc.get("purpose")
    if purpose:
        chunks.append(f"{title}: {purpose}")

    for key in ("steps", "sequence", "criteria", "levels"):
        for item in doc.get(key, []):
            if isinstance(item, str):
                chunks.append(f"{title} - {item}")
            elif isinstance(item, dict):
                # Flatten a dict item like {"step": "A", "name": "Airway", "action": "..."}
                text = " | ".join(f"{k}: {v}" for k, v in item.items())
                chunks.append(f"{title} - {text}")

    categories = doc.get("categories")
    if isinstance(categories, dict):
        for tag, meaning in categories.items():
            chunks.append(f"{title} - category '{tag}': {meaning}")

    return chunks


def build_index():
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_or_create_collection(COLLECTION_NAME)

    docs = load_protocol_docs()
    if not docs:
        print("No protocol docs found in data/protocols/. Add some JSON files first.")
        return

    ids, texts, embeddings, metadatas = [], [], [], []
    chunk_counter = 0

    for doc in docs:
        for chunk_text in chunk_protocol(doc):
            chunk_counter += 1
            ids.append(f"chunk-{chunk_counter}")
            texts.append(chunk_text)
            embeddings.append(get_embedding(chunk_text))
            metadatas.append({"source": doc["_source_file"]})

    collection.upsert(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)
    print(f"Indexed {chunk_counter} chunks from {len(docs)} protocol files into '{COLLECTION_NAME}'.")


if __name__ == "__main__":
    build_index()
