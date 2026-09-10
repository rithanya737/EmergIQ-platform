"""
The 5 performance-evaluation metrics for a decision/attempt.

    1. Clinical accuracy   - was the decision medically correct? (LLM/rubric verdict)
    2. Response time       - how fast did the learner decide, relative to the
                             scenario's expected/allowed time?
    3. Response efficiency - did the learner use scarce resources (IV lines,
                             O2, staff, transport slots, ...) economically,
                             not more than the situation actually needed?
    4. Ethical reasoning   - did the decision navigate the ethical dilemma
                             well (fairness, consent, stewardship)?
    5. Patient outcome     - the aggregate/final score for the attempt,
                             combining the four metrics above.

Each of the first four is normalized to 0-100 so `patient_outcome` can
combine them with simple weights. Tune WEIGHTS below as your game design
settles.
"""

# --- 1. Clinical accuracy -------------------------------------------------
# Matches the scale you specified: correct / acceptable / incorrect / harmful.
CLINICAL_ACCURACY_SCORES = {
    "correct": 100,
    "acceptable": 70,
    "incorrect": 30,
    "harmful": 0,
}


def clinical_accuracy_score(clinical_verdict: str) -> int:
    """clinical_verdict comes from rag_pipeline.evaluate_decision() or rubric.score_decision()."""
    return CLINICAL_ACCURACY_SCORES.get(clinical_verdict, 30)  # default to "incorrect" if unrecognized


# --- 2. Response time ------------------------------------------------------
def response_time_score(time_taken_seconds: float, expected_seconds: float) -> int:
    """
    Full marks for deciding at or under the expected time.
    Score decays linearly to 0 at 3x the expected time (an unresponsive
    learner in an emergency scenario should score poorly), floored at 0.

    Tune `expected_seconds` per decision point in your scenario JSON
    (e.g. `data/scenarios/level_1.json` decision_points) rather than
    hardcoding one value for every scenario.
    """
    if expected_seconds <= 0:
        return 100
    if time_taken_seconds <= expected_seconds:
        return 100
    overrun_ratio = (time_taken_seconds - expected_seconds) / (expected_seconds * 2)
    score = 100 * (1 - overrun_ratio)
    return max(0, min(100, round(score)))


# --- 3. Response efficiency (scarce-resource use) --------------------------
def response_efficiency_score(resources_used: int, resources_optimal: int) -> int:
    """
    Penalizes both over-use (wasting scarce resources - see
    `crisis_standards_of_care_ethics.json` -> "stewardship of resources")
    and under-use (skipping a resource the situation actually required).

    resources_used / resources_optimal are counts you decide how to define
    per decision point, e.g. number of IV lines, O2 masks, ambulance slots,
    or "resource units" consumed relative to what the scenario's answer key
    says was actually needed.
    """
    if resources_optimal <= 0:
        return 100 if resources_used == 0 else 60
    deviation = abs(resources_used - resources_optimal) / resources_optimal
    score = 100 * (1 - deviation)
    return max(0, min(100, round(score)))


# --- 4. Ethical reasoning ---------------------------------------------------
def ethical_reasoning_score(ethical_score_0_to_10: float) -> int:
    """
    `ethical_score_0_to_10` is produced by the LLM in rag_pipeline.evaluate_decision()
    (grounded in biomedical_ethics_principles.json / crisis_standards_of_care_ethics.json),
    or by rubric.score_decision() as a fallback. This just rescales 0-10 -> 0-100.
    """
    return max(0, min(100, round(ethical_score_0_to_10 * 10)))


# --- 5. Patient outcome (aggregate) ----------------------------------------
def outcome_label(total: float) -> str:
    """Shared label thresholds — used both for a single attempt's aggregate
    (patient_outcome_score, below) and for the dashboard/room reports which
    aggregate ACROSS multiple already-averaged attempts."""
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


def decision_scorecard(
    clinical_verdict: str,
    time_taken_seconds: float,
    expected_seconds: float,
    resources_used: int,
    resources_optimal: int,
    ethical_score_0_to_10: float,
) -> dict:
    """Compute all 4 per-decision metrics + a weighted total for ONE decision point."""
    scores = {
        "clinical_accuracy": clinical_accuracy_score(clinical_verdict),
        "response_time": response_time_score(time_taken_seconds, expected_seconds),
        "response_efficiency": response_efficiency_score(resources_used, resources_optimal),
        "ethical_reasoning": ethical_reasoning_score(ethical_score_0_to_10),
    }
    scores["decision_total"] = round(sum(scores[k] * WEIGHTS[k] for k in WEIGHTS))
    return scores


def patient_outcome_score(decision_scorecards: list[dict]) -> dict:
    """
    Aggregate metric #5 across every decision point in an attempt.
    Returns the average of each metric plus an overall total, and a coarse
    outcome label for the report/UI (e.g. "Patient Stabilized").
    """
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


def aggregate_outcomes(outcomes: list[dict]) -> dict:
    """
    Aggregate a list of already-computed outcome dicts (e.g. one per Attempt,
    each shaped like patient_outcome_score()'s return) into one combined
    outcome. Used by the dashboard report (across ALL of a learner's
    attempts) and the room/assessment report (across all attempts within
    one room) — same shape, same label thresholds, different `outcomes` input.
    """
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
