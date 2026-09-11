# Flask endpoint: judges a learner's submitted decision and returns its scorecard
from flask import Blueprint, jsonify, request

from app.evaluator.rag_pipeline import evaluate_decision
from app.evaluator.rubric import score_decision
from app.evaluator.ollama_client import is_reachable
from app.evaluator.metrics import decision_scorecard

bp = Blueprint("evaluator", __name__, url_prefix="/evaluate")


# Tries the RAG+LLM path first, falling back to the deterministic rubric on any failure
def _judgment_for(scenario_text: str, decision_text: str) -> dict:
    if is_reachable():
        try:
            result = evaluate_decision(scenario_text, decision_text)
            result["_source"] = "rag_llm"
            return result
        except Exception as e:
            fallback = score_decision(decision_text)
            fallback["_fallback_reason"] = str(e)
            return fallback
    return score_decision(decision_text)


@bp.route("/decision", methods=["POST"])
def evaluate():
    payload = request.get_json(force=True)
    scenario_text = payload.get("scenario_text", "")
    decision_text = payload.get("decision_text", "")

    if not decision_text:
        return jsonify({"error": "decision_text is required"}), 400

    judgment = _judgment_for(scenario_text, decision_text)

    scorecard = decision_scorecard(
        clinical_verdict=judgment.get("clinical_verdict", "acceptable"),
        time_taken_seconds=float(payload.get("time_taken_seconds", 0)),
        expected_seconds=float(payload.get("expected_seconds", 0)),
        resources_used=int(payload.get("resources_used", 0)),
        resources_optimal=int(payload.get("resources_optimal", 0)),
        ethical_score_0_to_10=float(judgment.get("ethical_score", 5)),
    )

    return jsonify({"judgment": judgment, "scorecard": scorecard})
