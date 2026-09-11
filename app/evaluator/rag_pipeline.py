# RAG pipeline: retrieves grounded protocol/ethics context and asks the LLM to judge a decision
import json

import chromadb

from app.config import CHROMA_PATH
from app.evaluator.ollama_client import get_embedding, generate

COLLECTION_NAME = "clinical_protocols"
TOP_K = 4

SYSTEM_PROMPT = (
    "You are a strict but fair clinical triage evaluator. "
    "Judge the learner's decision ONLY against the provided protocol/ethics excerpts. "
    "Respond with a single JSON object and nothing else, using this exact shape: "
    '{"clinical_verdict": "correct|acceptable|incorrect|harmful", '
    '"ethical_score": <0-10 number, how well the decision navigated any ethical dilemma>, '
    '"reasoning": "<1-3 sentences citing the relevant protocol or ethical principle>", '
    '"missed_protocol_points": ["..."]}. '
    "clinical_verdict meanings: correct = matches protocol exactly; "
    "acceptable = reasonable and not harmful but not the ideal protocol step; "
    "incorrect = deviates from protocol without causing direct harm; "
    "harmful = actively endangers the patient or violates a life-threatening priority."
)


# Opens (or creates) the persistent Chroma collection holding indexed protocol chunks
def _get_collection():
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_or_create_collection(COLLECTION_NAME)


# Embeds the query and returns the top_k most relevant protocol/ethics chunks
def retrieve_context(query_text: str, top_k: int = TOP_K) -> list[str]:
    collection = _get_collection()
    query_embedding = get_embedding(query_text)
    results = collection.query(query_embeddings=[query_embedding], n_results=top_k)
    return results["documents"][0] if results["documents"] else []


# Builds the grounding prompt from the scenario, the learner's decision, and retrieved context
def build_prompt(scenario_text: str, decision_text: str, context_chunks: list[str]) -> str:
    context_block = "\n".join(f"- {c}" for c in context_chunks) or "(no relevant protocol found)"
    return (
        f"SCENARIO:\n{scenario_text}\n\n"
        f"LEARNER'S DECISION:\n{decision_text}\n\n"
        f"RELEVANT PROTOCOL/ETHICS EXCERPTS:\n{context_block}\n\n"
        "Evaluate the learner's decision against the excerpts above, covering both "
        "clinical correctness and ethical reasoning."
    )


# Main entry point: retrieves context, prompts the LLM, and returns a structured judgment
def evaluate_decision(scenario_text: str, decision_text: str) -> dict:
    context_chunks = retrieve_context(f"{scenario_text} {decision_text}")
    prompt = build_prompt(scenario_text, decision_text, context_chunks)
    raw_response = generate(prompt, system=SYSTEM_PROMPT)

    try:
        result = json.loads(raw_response)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM did not return valid JSON: {raw_response!r}") from e

    result.setdefault("clinical_verdict", "acceptable")
    result.setdefault("ethical_score", 5)
    result["_retrieved_context"] = context_chunks
    return result
