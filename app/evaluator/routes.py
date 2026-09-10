"""
/evaluate/decision — internal endpoint called by app/game/engine.py whenever
a learner submits a decision at a decision point.

Expected JSON payload from the game engine:
{
  "scenario_text": "...",
  "decision_text": "...",
  "time_taken_seconds": 12.4,       # how long the learner took to decide
  "expected_seconds": 20,           # from the scenario's decision_point config
  "resources_used": 2,              # count of scarce resources the learner consumed
  "resources_optimal": 2            # the scenario's answer-key optimal count
}

Returns the LLM/rubric judgment PLUS the 4 per-decision metric scores from
app/evaluator/metrics.py (metric #5, patient_outcome, is aggregated across
a whole attempt by app/evaluator/report_generator.py, not per decision).
"""
from flask import Blueprint, jsonify, request

from app.evaluator.rag_pipeline import evaluate_decision
from app.evaluator.rubric import score_decision
from app.evaluator.ollama_client import is_reachable
from app.evaluator.metrics import decision_scorecard

bp = Blueprint("evaluator", __name__, url_prefix="/evaluate")


def _judgment_for(scenario_text: str, decision_text: str) -> dict:
    """Try the RAG+LLM path; fall back to the deterministic rubric on any failure."""
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
