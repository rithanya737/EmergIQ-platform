"""Attempt, DecisionScore models — one row per level playthrough / per decision."""
import json
from datetime import datetime

from app.extensions import db


class Attempt(db.Model):
    """One playthrough of one level by one learner.

    `room_id` is NULL for solo/self-learning play, and set when the level
    was played as part of an instructor's room (report #3 groups by this).
    """
    __tablename__ = "attempts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey("rooms.id"), nullable=True)
    level = db.Column(db.Integer, nullable=False)
    scenario_title = db.Column(db.String(200), nullable=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    # How this attempt ended. The server enforces a real per-level deadline
    # (see app/game/routes.py's _finalize_attempt) — 'time_expired' when
    # that deadline passed before the learner finished, 'completed' when
    # they finished every decision within it. Nullable so old rows (from
    # before this column existed) don't break anything.
    status = db.Column(db.Enum("completed", "time_expired", name="attempt_status"), nullable=True)

    # Denormalized metric #5 outcome for this attempt — written once by
    # report_generator.build_attempt_report() so the dashboard/room reports
    # never need to recompute from decision_scores on every read.
    average_total = db.Column(db.Numeric(5, 2), nullable=True)
    clinical_accuracy_avg = db.Column(db.Numeric(5, 2), nullable=True)
    response_time_avg = db.Column(db.Numeric(5, 2), nullable=True)
    response_efficiency_avg = db.Column(db.Numeric(5, 2), nullable=True)
    ethical_reasoning_avg = db.Column(db.Numeric(5, 2), nullable=True)
    outcome_label = db.Column(db.String(60), nullable=True)

    user = db.relationship("User", back_populates="attempts")
    room = db.relationship("Room", back_populates="attempts")
    decisions = db.relationship(
        "DecisionScore", back_populates="attempt", order_by="DecisionScore.sequence", lazy="joined"
    )

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None

    @property
    def display_status(self) -> str:
        return self.status or ("completed" if self.completed_at else "in_progress")

    def as_outcome_dict(self) -> dict:
        """Shape matches metrics.patient_outcome_score()'s return, from the stored averages."""
        return {
            "clinical_accuracy": float(self.clinical_accuracy_avg or 0),
            "response_time": float(self.response_time_avg or 0),
            "response_efficiency": float(self.response_efficiency_avg or 0),
            "ethical_reasoning": float(self.ethical_reasoning_avg or 0),
            "average_total": float(self.average_total or 0),
            "outcome_label": self.outcome_label or "No decisions recorded",
        }

    def __repr__(self):
        return f"<Attempt {self.id} user={self.user_id} level={self.level}>"


class DecisionScore(db.Model):
    """One evaluated decision point inside one attempt."""
    __tablename__ = "decision_scores"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("attempts.id"), nullable=False)
    decision_point_id = db.Column(db.String(40), nullable=True)
    sequence = db.Column(db.Integer, nullable=False)
    decision_text = db.Column(db.Text, nullable=True)

    clinical_verdict = db.Column(db.String(20), nullable=False)
    reasoning = db.Column(db.Text, nullable=True)
    missed_protocol_points_json = db.Column("missed_protocol_points", db.Text, nullable=True)

    time_taken_seconds = db.Column(db.Numeric(6, 2), nullable=True)
    expected_seconds = db.Column(db.Numeric(6, 2), nullable=True)
    resources_used = db.Column(db.Integer, nullable=True)
    resources_optimal = db.Column(db.Integer, nullable=True)
    ethical_score_0_to_10 = db.Column(db.Numeric(4, 2), nullable=True)

    clinical_accuracy_score = db.Column(db.Integer, nullable=False)
    response_time_score = db.Column(db.Integer, nullable=False)
    response_efficiency_score = db.Column(db.Integer, nullable=False)
    ethical_reasoning_score = db.Column(db.Integer, nullable=False)
    decision_total = db.Column(db.Integer, nullable=False)

    judgment_source = db.Column(db.String(20), nullable=True)  # rag_llm / rubric_fallback
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    attempt = db.relationship("Attempt", back_populates="decisions")

    @property
    def missed_protocol_points(self) -> list:
        return json.loads(self.missed_protocol_points_json) if self.missed_protocol_points_json else []

    @missed_protocol_points.setter
    def missed_protocol_points(self, points: list) -> None:
        self.missed_protocol_points_json = json.dumps(points or [])

    def as_evaluated_decision(self) -> dict:
        """Reshape back into the {"judgment": ..., "scorecard": ...} dict that
        report_generator.build_attempt_report() / render_*() already expect,
        so persisted rows and live in-memory decisions use the same code path."""
        return {
            "judgment": {
                "clinical_verdict": self.clinical_verdict,
                "reasoning": self.reasoning,
                "missed_protocol_points": self.missed_protocol_points,
                "_source": self.judgment_source,
            },
            "scorecard": {
                "clinical_accuracy": self.clinical_accuracy_score,
                "response_time": self.response_time_score,
                "response_efficiency": self.response_efficiency_score,
                "ethical_reasoning": self.ethical_reasoning_score,
                "decision_total": self.decision_total,
            },
        }
