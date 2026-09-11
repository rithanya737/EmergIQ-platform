# Deterministic keyword-based fallback scorer used when the LLM/RAG path is unavailable

KEYWORD_RULES = {
    "airway": ("correct", 7, "Prioritizing airway aligns with the ABCDE sequence."),
    "bleeding": ("correct", 7, "Controlling major bleeding is a correct circulation-step priority."),
    "consent": ("correct", 9, "Seeking consent respects patient autonomy where time permits."),
    "walk": ("incorrect", 4, "Moving/ignoring an unstable patient contradicts triage protocol."),
    "ignore": ("harmful", 1, "Ignoring a patient in distress is not an acceptable action."),
    "favorite": ("harmful", 1, "Allocating care by favoritism violates triage fairness/equity."),
}

DEFAULT = ("acceptable", 5, "No strong keyword match; manual review recommended.")


# Scores a decision by keyword match, falling back to a neutral default
def score_decision(decision_text: str) -> dict:
    text = decision_text.lower()
    for keyword, (verdict, ethical_score, reasoning) in KEYWORD_RULES.items():
        if keyword in text:
            return {
                "clinical_verdict": verdict,
                "ethical_score": ethical_score,
                "reasoning": reasoning,
                "missed_protocol_points": [],
                "_source": "rubric_fallback",
            }

    verdict, ethical_score, reasoning = DEFAULT
    return {
        "clinical_verdict": verdict,
        "ethical_score": ethical_score,
        "reasoning": reasoning,
        "missed_protocol_points": [],
        "_source": "rubric_fallback",
    }
