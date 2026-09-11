# One-time script: chunks, embeds, and indexes protocol docs into Chroma (rerun when protocols change)
import json
import glob
import os

import chromadb

from app.config import CHROMA_PATH
from app.evaluator.ollama_client import get_embedding

COLLECTION_NAME = "clinical_protocols"


# Loads every protocol JSON file from data/protocols
def load_protocol_docs(protocols_dir: str = "data/protocols") -> list[dict]:
    docs = []
    for path in glob.glob(os.path.join(protocols_dir, "*.json")):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            data["_source_file"] = os.path.basename(path)
            docs.append(data)
    return docs


# Splits one protocol doc into small self-contained text chunks for focused retrieval
def chunk_protocol(doc: dict) -> list[str]:
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
                text = " | ".join(f"{k}: {v}" for k, v in item.items())
                chunks.append(f"{title} - {text}")

    categories = doc.get("categories")
    if isinstance(categories, dict):
        for tag, meaning in categories.items():
            chunks.append(f"{title} - category '{tag}': {meaning}")

    return chunks


# Embeds and upserts every protocol chunk into the persistent Chroma collection
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
