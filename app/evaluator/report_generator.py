"""
Builds all 3 performance-report surfaces and exports each as a downloadable
PDF using reportlab:

  1. Attempt (per-level) report   — build_attempt_report()   / export_attempt_report_pdf()
     Shown right after a learner finishes one level, with a download button.

  2. Dashboard report              — build_dashboard_report() / export_dashboard_report_pdf()
     A learner's overall performance across EVERY attempt stored for them
     (solo play + every room they've played in), shown on their dashboard.

  3. Assessment (room) report      — build_room_report()      / export_room_report_pdf()
     One report per learner covering every level they played inside ONE
     room, shown once the room/assessment ends — regardless of how many
     of the room's levels they completed.

All three share the same underlying metrics (app/evaluator/metrics.py) so a
score means the same thing everywhere a report shows it.
"""
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.evaluator.metrics import patient_outcome_score, aggregate_outcomes

# ---------------------------------------------------------------------------
# Shared PDF look & feel
# ---------------------------------------------------------------------------
BRAND_PRIMARY = colors.HexColor("#2b3a55")     # header bars / titles — swap for team brand color
BRAND_ACCENT = colors.HexColor("#1f8a70")      # good-outcome accent — swap for team brand color
ROW_ALT = colors.HexColor("#f0f2f6")


def _styles():
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, leading=10)
    return styles, small


def _summary_table(o: dict) -> Table:
    data = [
        ["Metric", "Average Score (0-100)"],
        ["1. Clinical accuracy", o["clinical_accuracy"]],
        ["2. Response time", o["response_time"]],
        ["3. Response efficiency (resource use)", o["response_efficiency"]],
        ["4. Ethical reasoning", o["ethical_reasoning"]],
        ["5. Patient outcome (overall)", o["average_total"]],
    ]
    table = Table(data, colWidths=[3.5 * inch, 2 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return table


# ===========================================================================
# 1. ATTEMPT (PER-LEVEL) REPORT
# ===========================================================================
def build_report(learner_name: str, level: int, evaluated_decisions: list[dict],
                  room: dict | None = None, scenario_title: str | None = None) -> dict:
    """
    evaluated_decisions: list of {"judgment": {...}, "scorecard": {...}} dicts,
    one per decision point, as returned by the /evaluate/decision endpoint
    (or DecisionScore.as_evaluated_decision() when reloading from the DB).
    room: optional {"name": ..., "code": ...} if this attempt was played inside a room.
    """
    scorecards = [d["scorecard"] for d in evaluated_decisions]
    outcome = patient_outcome_score(scorecards)  # metric #5: aggregate for this attempt

    return {
        "report_type": "attempt",
        "learner_name": learner_name,
        "level": level,
        "scenario_title": scenario_title,
        "room": room,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_decisions": len(evaluated_decisions),
        "outcome": outcome,  # averages for metrics 1-4 + average_total + outcome_label
        "details": evaluated_decisions,
    }


def build_attempt_report(attempt) -> dict:
    """Build report #1 straight from a persisted app.game.models.Attempt row."""
    evaluated_decisions = [d.as_evaluated_decision() for d in attempt.decisions]
    room = {"name": attempt.room.name, "code": attempt.room.code} if attempt.room_id else None
    return build_report(
        learner_name=attempt.user.name,
        level=attempt.level,
        evaluated_decisions=evaluated_decisions,
        room=room,
        scenario_title=attempt.scenario_title,
    )


def render_report_html(report: dict) -> str:
    """Quick HTML preview without Jinja — prefer templates/report.html + render_template() in the real app."""
    o = report["outcome"]
    rows = "".join(
        f"<tr><td>{i + 1}</td><td>{d['judgment'].get('clinical_verdict')}</td>"
        f"<td>{d['scorecard']['clinical_accuracy']}</td><td>{d['scorecard']['response_time']}</td>"
        f"<td>{d['scorecard']['response_efficiency']}</td><td>{d['scorecard']['ethical_reasoning']}</td>"
        f"<td>{d['scorecard']['decision_total']}</td><td>{d['judgment'].get('reasoning', '')}</td></tr>"
        for i, d in enumerate(report["details"])
    )
    return f"""
    <h1>Report for {report['learner_name']} - Level {report['level']}</h1>
    <p>{o['outcome_label']} — overall score {o['average_total']}/100</p>
    <p>Clinical accuracy avg: {o['clinical_accuracy']} | Response time avg: {o['response_time']} |
       Response efficiency avg: {o['response_efficiency']} | Ethical reasoning avg: {o['ethical_reasoning']}</p>
    <table border="1">
      <tr><th>#</th><th>Verdict</th><th>Accuracy</th><th>Speed</th><th>Efficiency</th><th>Ethics</th><th>Total</th><th>Reasoning</th></tr>
      {rows}
    </table>
    """


def export_attempt_report_pdf(report: dict, output_path: str) -> str:
    """
    Render `report` (from build_report / build_attempt_report) to a PDF.
    Wire to GET /dashboard/report/<attempt_id>/pdf.
    """
    doc = SimpleDocTemplate(output_path, pagesize=letter, topMargin=0.7 * inch, bottomMargin=0.7 * inch)
    styles, small_style = _styles()
    o = report["outcome"]
    story = []

    story.append(Paragraph("Emergency Response Performance Report", styles["Title"]))
    story.append(Spacer(1, 6))
    subtitle = f"Learner: {report['learner_name']}  |  Level: {report['level']}"
    if report.get("scenario_title"):
        subtitle += f"  |  Scenario: {report['scenario_title']}"
    if report.get("room"):
        subtitle += f"  |  Room: {report['room']['name']} ({report['room']['code']})"
    subtitle += f"  |  Generated: {report['generated_at']}"
    story.append(Paragraph(subtitle, styles["Normal"]))
    story.append(Spacer(1, 14))

    story.append(Paragraph(f"Overall outcome: {o['outcome_label']}", styles["Heading2"]))
    story.append(Paragraph(f"Overall score: {o['average_total']} / 100", styles["Normal"]))
    story.append(Spacer(1, 10))
    story.append(_summary_table(o))
    story.append(Spacer(1, 18))

    story.append(Paragraph("Decision-by-decision breakdown", styles["Heading2"]))
    story.append(Spacer(1, 6))

    detail_header = ["#", "Verdict", "Acc.", "Speed", "Eff.", "Ethics", "Total", "Reasoning"]
    detail_rows = [detail_header]
    for i, d in enumerate(report["details"]):
        j, s = d["judgment"], d["scorecard"]
        detail_rows.append([
            str(i + 1),
            j.get("clinical_verdict", ""),
            str(s["clinical_accuracy"]),
            str(s["response_time"]),
            str(s["response_efficiency"]),
            str(s["ethical_reasoning"]),
            str(s["decision_total"]),
            Paragraph(j.get("reasoning", ""), small_style),
        ])

    detail_table = Table(detail_rows, colWidths=[0.3 * inch, 0.7 * inch, 0.5 * inch, 0.5 * inch, 0.5 * inch, 0.5 * inch, 0.5 * inch, 2.5 * inch])
    detail_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(detail_table)

    missed = sorted({p for d in report["details"] for p in d["judgment"].get("missed_protocol_points", [])})
    if missed:
        story.append(Spacer(1, 18))
        story.append(Paragraph("Protocol points to review", styles["Heading2"]))
        for point in missed:
            story.append(Paragraph(f"- {point}", styles["Normal"]))

    doc.build(story)
    return output_path


# Back-compat alias (older code/tests may import this name)
export_report_pdf = export_attempt_report_pdf


# ===========================================================================
# 2. DASHBOARD REPORT — a learner's overall performance across ALL attempts
# ===========================================================================
def build_dashboard_report(user, attempts: list | None = None) -> dict:
    """
    user: app.auth.models.User
    attempts: optional pre-fetched list of completed app.game.models.Attempt
              rows for this user (pass this if the caller already queried
              them — e.g. ordered/filtered a particular way). Defaults to
              every completed attempt this user has, across solo play and
              every room.
    """
    if attempts is None:
        attempts = _completed_attempts_for_user(user)

    outcomes = [a.as_outcome_dict() for a in attempts]
    overall = aggregate_outcomes(outcomes)

    per_level: dict[int, list] = {}
    for a in attempts:
        per_level.setdefault(a.level, []).append(a)

    level_summary = []
    for level in sorted(per_level):
        level_attempts = per_level[level]
        level_outcomes = [a.as_outcome_dict() for a in level_attempts]
        best = max(level_outcomes, key=lambda o: o["average_total"])
        latest = level_attempts[-1].as_outcome_dict()
        level_summary.append({
            "level": level,
            "attempts": len(level_attempts),
            "best_score": best["average_total"],
            "latest_score": latest["average_total"],
        })

    attempt_rows = [
        {
            "date": a.completed_at.strftime("%d %b %Y, %H:%M") if a.completed_at else "-",
            "level": a.level,
            "room": a.room.name if a.room_id else "Solo practice",
            "score": float(a.average_total or 0),
            "outcome_label": a.outcome_label or "-",
            "status": a.status or "completed",
        }
        for a in sorted(attempts, key=lambda a: a.completed_at or datetime.min, reverse=True)
    ]

    metric_labels = {
        "clinical_accuracy": "Clinical accuracy",
        "response_time": "Response time",
        "response_efficiency": "Response efficiency",
        "ethical_reasoning": "Ethical reasoning",
    }
    ranked = sorted(metric_labels, key=lambda k: overall[k])
    weakest_metric = metric_labels[ranked[0]] if attempts else None
    strongest_metric = metric_labels[ranked[-1]] if attempts else None

    return {
        "report_type": "dashboard",
        "learner_name": user.name,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_attempts": len(attempts),
        "levels_played": sorted(per_level.keys()),
        "overall": overall,
        "level_summary": level_summary,
        "attempts": attempt_rows,
        "strongest_metric": strongest_metric,
        "weakest_metric": weakest_metric,
    }


def _completed_attempts_for_user(user) -> list:
    from app.game.models import Attempt
    return Attempt.query.filter_by(user_id=user.id).filter(Attempt.completed_at.isnot(None)).all()


def export_dashboard_report_pdf(report: dict, output_path: str) -> str:
    doc = SimpleDocTemplate(output_path, pagesize=letter, topMargin=0.7 * inch, bottomMargin=0.7 * inch)
    styles, small_style = _styles()
    o = report["overall"]
    story = []

    story.append(Paragraph("Overall Performance Report", styles["Title"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Learner: {report['learner_name']}  |  Total attempts: {report['total_attempts']}  |  "
        f"Levels played: {', '.join(str(l) for l in report['levels_played']) or '—'}  |  "
        f"Generated: {report['generated_at']}",
        styles["Normal"],
    ))
    story.append(Spacer(1, 14))

    story.append(Paragraph(f"Overall outcome: {o['outcome_label']}", styles["Heading2"]))
    story.append(Paragraph(f"Overall score: {o['average_total']} / 100", styles["Normal"]))
    if report.get("strongest_metric"):
        story.append(Paragraph(
            f"Strongest area: {report['strongest_metric']}  |  Focus area: {report['weakest_metric']}",
            styles["Normal"],
        ))
    story.append(Spacer(1, 10))
    story.append(_summary_table(o))
    story.append(Spacer(1, 18))

    if report["level_summary"]:
        story.append(Paragraph("Performance by level", styles["Heading2"]))
        story.append(Spacer(1, 6))
        rows = [["Level", "Attempts", "Best score", "Latest score"]]
        for ls in report["level_summary"]:
            rows.append([str(ls["level"]), str(ls["attempts"]), str(ls["best_score"]), str(ls["latest_score"])])
        level_table = Table(rows, colWidths=[1 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch])
        level_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_PRIMARY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
        ]))
        story.append(level_table)
        story.append(Spacer(1, 18))

    if report["attempts"]:
        story.append(Paragraph("Attempt history", styles["Heading2"]))
        story.append(Spacer(1, 6))
        rows = [["Date", "Level", "Room", "Score", "Outcome"]]
        for a in report["attempts"]:
            rows.append([a["date"], str(a["level"]), a["room"], str(a["score"]), a["outcome_label"]])
        history_table = Table(rows, colWidths=[1.4 * inch, 0.6 * inch, 1.5 * inch, 0.7 * inch, 2.3 * inch])
        history_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_PRIMARY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
        ]))
        story.append(history_table)

    doc.build(story)
    return output_path


# ===========================================================================
# 3. ASSESSMENT (ROOM) REPORT — one learner's performance across every level
#    they played inside ONE room, shown once the assessment ends.
# ===========================================================================
def build_room_report(room, user, attempts: list | None = None) -> dict:
    """
    room: app.rooms.models.Room
    user: app.auth.models.User (the learner whose report this is)
    attempts: optional pre-fetched list of this user's completed attempts in
              this room; defaults to querying them.
    """
    if attempts is None:
        from app.game.models import Attempt
        attempts = (
            Attempt.query.filter_by(room_id=room.id, user_id=user.id)
            .filter(Attempt.completed_at.isnot(None))
            .order_by(Attempt.level.asc())
            .all()
        )

    outcomes = [a.as_outcome_dict() for a in attempts]
    overall = aggregate_outcomes(outcomes)

    levels_in_room = room.level_set
    levels_played = sorted(a.level for a in attempts)
    levels_skipped = sorted(set(levels_in_room) - set(levels_played))

    level_rows = [
        {
            "level": a.level,
            "scenario_title": a.scenario_title,
            "score": float(a.average_total or 0),
            "outcome_label": a.outcome_label or "-",
            "clinical_accuracy": float(a.clinical_accuracy_avg or 0),
            "response_time": float(a.response_time_avg or 0),
            "response_efficiency": float(a.response_efficiency_avg or 0),
            "ethical_reasoning": float(a.ethical_reasoning_avg or 0),
        }
        for a in attempts
    ]

    return {
        "report_type": "room_assessment",
        "learner_name": user.name,
        "room_name": room.name,
        "room_code": room.code,
        "instructor_name": room.instructor.name if room.instructor else None,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "levels_in_assessment": levels_in_room,
        "levels_played": levels_played,
        "levels_skipped": levels_skipped,
        "overall": overall,
        "levels": level_rows,
    }


def export_room_report_pdf(report: dict, output_path: str) -> str:
    doc = SimpleDocTemplate(output_path, pagesize=letter, topMargin=0.7 * inch, bottomMargin=0.7 * inch)
    styles, small_style = _styles()
    o = report["overall"]
    story = []

    story.append(Paragraph("Assessment Performance Report", styles["Title"]))
    story.append(Spacer(1, 6))
    subtitle = f"Learner: {report['learner_name']}  |  Room: {report['room_name']} ({report['room_code']})"
    if report.get("instructor_name"):
        subtitle += f"  |  Instructor: {report['instructor_name']}"
    subtitle += f"  |  Generated: {report['generated_at']}"
    story.append(Paragraph(subtitle, styles["Normal"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Levels completed: {', '.join(str(l) for l in report['levels_played']) or 'none'}"
        + (f"  |  Not attempted: {', '.join(str(l) for l in report['levels_skipped'])}" if report["levels_skipped"] else ""),
        styles["Normal"],
    ))
    story.append(Spacer(1, 14))

    story.append(Paragraph(f"Overall outcome: {o['outcome_label']}", styles["Heading2"]))
    story.append(Paragraph(f"Overall score: {o['average_total']} / 100", styles["Normal"]))
    story.append(Spacer(1, 10))
    story.append(_summary_table(o))
    story.append(Spacer(1, 18))

    if report["levels"]:
        story.append(Paragraph("Performance by level", styles["Heading2"]))
        story.append(Spacer(1, 6))
        rows = [["Level", "Scenario", "Acc.", "Speed", "Eff.", "Ethics", "Score", "Outcome"]]
        for lv in report["levels"]:
            rows.append([
                str(lv["level"]), lv["scenario_title"] or "-",
                str(lv["clinical_accuracy"]), str(lv["response_time"]),
                str(lv["response_efficiency"]), str(lv["ethical_reasoning"]),
                str(lv["score"]), lv["outcome_label"],
            ])
        level_table = Table(rows, colWidths=[0.5 * inch, 1.6 * inch, 0.5 * inch, 0.5 * inch, 0.5 * inch, 0.5 * inch, 0.5 * inch, 1.9 * inch])
        level_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_PRIMARY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(level_table)

    doc.build(story)
    return output_path


# ===========================================================================
# Persistence helper — call once when an attempt finishes (game/engine.py)
# ===========================================================================
def save_attempt_outcome(attempt, evaluated_decisions: list[dict]) -> dict:
    """
    Persist the per-decision rows (DecisionScore) and the denormalized
    per-attempt averages onto `attempt` (app.game.models.Attempt), so the
    dashboard/room reports can aggregate without recomputing from scratch.
    Call this once, right when a level ends, then commit the session.
    Returns the outcome dict (same shape patient_outcome_score() returns).
    """
    from app.extensions import db
    from app.game.models import DecisionScore

    scorecards = [d["scorecard"] for d in evaluated_decisions]
    outcome = patient_outcome_score(scorecards)

    for i, d in enumerate(evaluated_decisions, start=1):
        j, s = d["judgment"], d["scorecard"]
        row = DecisionScore(
            attempt_id=attempt.id,
            decision_point_id=d.get("decision_point_id"),
            sequence=i,
            decision_text=d.get("decision_text"),
            clinical_verdict=j.get("clinical_verdict", "acceptable"),
            reasoning=j.get("reasoning"),
            time_taken_seconds=d.get("time_taken_seconds"),
            expected_seconds=d.get("expected_seconds"),
            resources_used=d.get("resources_used"),
            resources_optimal=d.get("resources_optimal"),
            ethical_score_0_to_10=j.get("ethical_score"),
            clinical_accuracy_score=s["clinical_accuracy"],
            response_time_score=s["response_time"],
            response_efficiency_score=s["response_efficiency"],
            ethical_reasoning_score=s["ethical_reasoning"],
            decision_total=s["decision_total"],
            judgment_source=j.get("_source"),
        )
        row.missed_protocol_points = j.get("missed_protocol_points", [])
        db.session.add(row)

    attempt.average_total = outcome["average_total"]
    attempt.clinical_accuracy_avg = outcome["clinical_accuracy"]
    attempt.response_time_avg = outcome["response_time"]
    attempt.response_efficiency_avg = outcome["response_efficiency"]
    attempt.ethical_reasoning_avg = outcome["ethical_reasoning"]
    attempt.outcome_label = outcome["outcome_label"]
    attempt.completed_at = datetime.utcnow()

    return outcome


# ===========================================================================
# ACHIEVEMENTS — computed on the fly from Attempt/Room rows (no separate
# table), mirroring RAMCO's achievements() function.
# ===========================================================================
def build_achievements(user) -> list:
    """Returns a list of {"label", "tone", "detail"} dicts for the badges
    this learner has unlocked. Recomputed on every dashboard load — cheap
    at this data size and always in sync with the underlying attempts."""
    from app.game.models import Attempt
    from app.rooms.models import Room
    from app.game.level_manager import LevelManager

    solo_completed = (
        Attempt.query.filter_by(user_id=user.id)
        .filter(Attempt.room_id.is_(None))
        .filter(Attempt.completed_at.isnot(None))
        .all()
    )
    completed_levels = {a.level for a in solo_completed}
    total_levels = len(LevelManager().get_available_levels(max_level=5))

    best = None
    all_completed_attempts = Attempt.query.filter_by(user_id=user.id).filter(Attempt.completed_at.isnot(None)).all()
    if all_completed_attempts:
        best = max(float(a.average_total or 0) for a in all_completed_attempts)

    rows = []
    if len(completed_levels) >= 1:
        rows.append({"label": "First shift", "tone": "ok", "detail": "Completed your first level"})
    if len(completed_levels) >= 3:
        rows.append({"label": "Three levels cleared", "tone": "info", "detail": "Completed three levels"})
    if total_levels and len(completed_levels) >= total_levels:
        rows.append({"label": "Full command", "tone": "warn", "detail": "Completed every level"})
    if best is not None and best >= 80:
        rows.append({"label": "Clear thinker", "tone": "info", "detail": "Scored 80% or higher"})
    if any(float(a.clinical_accuracy_avg or 0) >= 100 for a in all_completed_attempts):
        rows.append({"label": "Flawless call", "tone": "ok", "detail": "Every decision correct in a run"})
    if Room.query.filter_by(instructor_id=user.id).first():
        rows.append({"label": "Host", "tone": "info", "detail": "Created an assessment room"})
    if any(a.room_id is not None for a in all_completed_attempts):
        rows.append({"label": "Assessed", "tone": "ok", "detail": "Completed an assessment"})
    return rows
