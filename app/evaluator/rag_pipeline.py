"""
Retrieval-Augmented Generation pipeline for scoring a learner's decision.

Flow:
  1. Embed the learner's decision/situation text.
  2. Query Chroma for the top-k most relevant chunks (clinical protocols
     AND ethics documents live in the same collection, so a dilemma-heavy
     decision naturally retrieves ethics context too).
  3. Build a prompt that gives the LLM: the scenario, the learner's decision,
     and the retrieved excerpts as grounding context.
  4. Call Ollama to generate a structured judgment covering BOTH clinical
     accuracy and ethical reasoning, so app/evaluator/metrics.py can score
     both dimensions from one LLM call.
"""
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


def _get_collection():
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_or_create_collection(COLLECTION_NAME)


def retrieve_context(query_text: str, top_k: int = TOP_K) -> list[str]:
    """Embed the query and return the top_k most relevant chunks."""
    collection = _get_collection()
    query_embedding = get_embedding(query_text)
    results = collection.query(query_embeddings=[query_embedding], n_results=top_k)
    return results["documents"][0] if results["documents"] else []


def build_prompt(scenario_text: str, decision_text: str, context_chunks: list[str]) -> str:
    context_block = "\n".join(f"- {c}" for c in context_chunks) or "(no relevant protocol found)"
    return (
        f"SCENARIO:\n{scenario_text}\n\n"
        f"LEARNER'S DECISION:\n{decision_text}\n\n"
        f"RELEVANT PROTOCOL/ETHICS EXCERPTS:\n{context_block}\n\n"
        "Evaluate the learner's decision against the excerpts above, covering both "
        "clinical correctness and ethical reasoning."
    )


def evaluate_decision(scenario_text: str, decision_text: str) -> dict:
    """
    Main entry point called by app/evaluator/routes.py.
    Returns a dict: {clinical_verdict, ethical_score, reasoning, missed_protocol_points}.
    Raises ValueError if the LLM response isn't valid JSON — callers should
    catch this and fall back to app/evaluator/rubric.py.
    """
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
