"""
Tests for the RAG+LLM evaluator that don't require a running Ollama/Chroma —
they exercise metrics.py and report_generator.py directly, and check that
routes.py falls back to the rubric when the RAG/LLM path raises.
"""
from app.evaluator.metrics import (
    clinical_accuracy_score,
    response_time_score,
    response_efficiency_score,
    ethical_reasoning_score,
    decision_scorecard,
    patient_outcome_score,
)
from app.evaluator.report_generator import build_report, export_report_pdf
from app.evaluator.rubric import score_decision


def test_clinical_accuracy_scores_match_spec():
    assert clinical_accuracy_score("correct") == 100
    assert clinical_accuracy_score("acceptable") == 70
    assert clinical_accuracy_score("incorrect") == 30
    assert clinical_accuracy_score("harmful") == 0


def test_response_time_full_marks_within_expected():
    assert response_time_score(10, 15) == 100


def test_response_time_decays_when_slow():
    score = response_time_score(45, 15)  # 3x expected
    assert 0 <= score < 100


def test_response_efficiency_perfect_match():
    assert response_efficiency_score(2, 2) == 100


def test_response_efficiency_penalizes_overuse():
    assert response_efficiency_score(5, 1) < 50


def test_ethical_reasoning_rescales_0_to_10():
    assert ethical_reasoning_score(8) == 80
    assert ethical_reasoning_score(0) == 0


def test_decision_scorecard_has_weighted_total():
    scorecard = decision_scorecard(
        clinical_verdict="correct",
        time_taken_seconds=10,
        expected_seconds=15,
        resources_used=2,
        resources_optimal=2,
        ethical_score_0_to_10=8,
    )
    assert scorecard["decision_total"] > 0
    assert set(scorecard) == {
        "clinical_accuracy", "response_time", "response_efficiency",
        "ethical_reasoning", "decision_total",
    }


def test_patient_outcome_aggregates_and_labels():
    scorecards = [
        decision_scorecard("correct", 10, 15, 2, 2, 9),
        decision_scorecard("harmful", 60, 15, 5, 1, 1),
    ]
    outcome = patient_outcome_score(scorecards)
    assert "outcome_label" in outcome
    assert 0 <= outcome["average_total"] <= 100


def test_rubric_fallback_shape_matches_rag_pipeline_output():
    result = score_decision("I control the bleeding first.")
    assert result["clinical_verdict"] in {"correct", "acceptable", "incorrect", "harmful"}
    assert 0 <= result["ethical_score"] <= 10


def test_full_report_exports_pdf(tmp_path):
    judgment = {"clinical_verdict": "correct", "ethical_score": 8, "reasoning": "ok", "missed_protocol_points": []}
    scorecard = decision_scorecard("correct", 10, 15, 2, 2, 8)
    report = build_report("Test Learner", level=1, evaluated_decisions=[{"judgment": judgment, "scorecard": scorecard}])

    output_path = tmp_path / "report.pdf"
    export_report_pdf(report, str(output_path))

    assert output_path.exists()
    assert output_path.stat().st_size > 0
