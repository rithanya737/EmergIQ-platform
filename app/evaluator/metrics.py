# Computes the 5 per-decision/attempt performance metrics and their weighted aggregate

CLINICAL_ACCURACY_SCORES = {
    "correct": 100,
    "acceptable": 70,
    "incorrect": 30,
    "harmful": 0,
}


# Maps an LLM/rubric clinical verdict to a 0-100 score
def clinical_accuracy_score(clinical_verdict: str) -> int:
    return CLINICAL_ACCURACY_SCORES.get(clinical_verdict, 30)


# Scores decision speed against the scenario's expected time, decaying to 0 by 3x overrun
def response_time_score(time_taken_seconds: float, expected_seconds: float) -> int:
    if expected_seconds <= 0:
        return 100
    if time_taken_seconds <= expected_seconds:
        return 100
    overrun_ratio = (time_taken_seconds - expected_seconds) / (expected_seconds * 2)
    score = 100 * (1 - overrun_ratio)
    return max(0, min(100, round(score)))


# Scores resource use against the scenario's optimal count, penalizing over- and under-use
def response_efficiency_score(resources_used: int, resources_optimal: int) -> int:
    if resources_optimal <= 0:
        return 100 if resources_used == 0 else 60
    deviation = abs(resources_used - resources_optimal) / resources_optimal
    score = 100 * (1 - deviation)
    return max(0, min(100, round(score)))


# Rescales the LLM/rubric's 0-10 ethical score to 0-100
def ethical_reasoning_score(ethical_score_0_to_10: float) -> int:
    return max(0, min(100, round(ethical_score_0_to_10 * 10)))


# Maps an aggregate total score to a coarse outcome label for reports/UI
def outcome_label(total: float) -> str:
    if total >= 85:
        return "Patient Stabilized - Excellent Care"
    elif total >= 70:
        return "Patient Stabilized - Acceptable Care"
    elif total >= 50:
        return "Patient Outcome Uncertain - Care Gaps Identified"
    else:
        return "Patient Deteriorated - Critical Care Gaps"


WEIGHTS = {
    "clinical_accuracy": 0.40,
    "response_time": 0.15,
    "response_efficiency": 0.15,
    "ethical_reasoning": 0.30,
}


# Computes all 4 per-decision metrics plus the weighted total for one decision point
def decision_scorecard(
    clinical_verdict: str,
    time_taken_seconds: float,
    expected_seconds: float,
    resources_used: int,
    resources_optimal: int,
    ethical_score_0_to_10: float,
) -> dict:
    scores = {
        "clinical_accuracy": clinical_accuracy_score(clinical_verdict),
        "response_time": response_time_score(time_taken_seconds, expected_seconds),
        "response_efficiency": response_efficiency_score(resources_used, resources_optimal),
        "ethical_reasoning": ethical_reasoning_score(ethical_score_0_to_10),
    }
    scores["decision_total"] = round(sum(scores[k] * WEIGHTS[k] for k in WEIGHTS))
    return scores


# Aggregates metric #5 (patient outcome) across every decision point in one attempt
def patient_outcome_score(decision_scorecards: list[dict]) -> dict:
    if not decision_scorecards:
        return {"average_total": 0, "outcome_label": "No decisions recorded"}

    n = len(decision_scorecards)
    averages = {
        key: round(sum(d[key] for d in decision_scorecards) / n)
        for key in ("clinical_accuracy", "response_time", "response_efficiency", "ethical_reasoning")
    }
    averages["average_total"] = round(sum(d["decision_total"] for d in decision_scorecards) / n)
    averages["outcome_label"] = outcome_label(averages["average_total"])
    return averages


# Aggregates multiple already-computed outcomes (across attempts) into one combined outcome
def aggregate_outcomes(outcomes: list[dict]) -> dict:
    if not outcomes:
        return {
            "clinical_accuracy": 0, "response_time": 0, "response_efficiency": 0,
            "ethical_reasoning": 0, "average_total": 0,
            "outcome_label": "No attempts recorded",
        }
    n = len(outcomes)
    keys = ("clinical_accuracy", "response_time", "response_efficiency", "ethical_reasoning")
    averages = {key: round(sum(o.get(key, 0) for o in outcomes) / n, 1) for key in keys}
    averages["average_total"] = round(sum(o.get("average_total", 0) for o in outcomes) / n, 1)
    averages["outcome_label"] = outcome_label(averages["average_total"])
    return averages
