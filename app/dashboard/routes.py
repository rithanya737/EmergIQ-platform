"""/ (landing), /home (choose your path), /self-learning (level map),
/dashboard (report #2), and PDF downloads for reports #1 and #2."""
import os
import tempfile

from flask import Blueprint, redirect, render_template, send_file, abort, url_for, session
from flask_login import login_required, current_user

from app.extensions import db
from app.game.level_manager import LevelManager, PASS_THRESHOLD
from app.game.models import Attempt
from app.rooms.models import Room, RoomMember
from app.evaluator.report_generator import (
    build_attempt_report,
    export_attempt_report_pdf,
    build_dashboard_report,
    export_dashboard_report_pdf,
    build_achievements,
)

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def index():
    """Landing route — send signed-in users to their home screen, everyone else to login."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))
    return redirect(url_for("auth.login"))


@bp.route("/home")
@login_required
def home():
    """Choose-your-path screen shown right after login: Self Learning / Create Room / Join Assessment."""
    session.pop("active_room_id", None)
    return render_template("home.html")


def _self_learning_progress(user):
    """A level only counts as passed (and unlocks the next one) once its
    best score reaches PASS_THRESHOLD — scoring below that leaves the level
    "unlocked" so the learner keeps retrying it instead of advancing."""
    manager = LevelManager()
    available = manager.get_available_levels(max_level=5)

    rows = (
        Attempt.query.filter_by(user_id=user.id)
        .filter(Attempt.room_id.is_(None))
        .filter(Attempt.completed_at.isnot(None))
        .all()
    )
    best_by_level = {}
    attempts_by_level = {}
    for a in rows:
        score = float(a.average_total or 0)
        if a.level not in best_by_level or score > best_by_level[a.level]:
            best_by_level[a.level] = score
        attempts_by_level[a.level] = attempts_by_level.get(a.level, 0) + 1

    passed_levels = {lid for lid, score in best_by_level.items() if score >= PASS_THRESHOLD}

    levels, prev_passed, current_set, current_level = [], True, False, None
    for info in available:
        lid = info["level"]
        best = best_by_level.get(lid)
        passed = lid in passed_levels
        unlocked = prev_passed
        needs_retry = unlocked and best is not None and not passed
        is_current = unlocked and not passed and not current_set
        if is_current:
            current_set = True
            current_level = lid
        levels.append({
            "level_id": lid,
            "title": info["title"],
            "difficulty": info["difficulty"],
            "status": "completed" if passed else ("unlocked" if unlocked else "locked"),
            "best_score": round(best) if best is not None else None,
            "attempts": attempts_by_level.get(lid, 0),
            "needs_retry": needs_retry,
            "is_current": is_current,
        })
        prev_passed = passed

    all_scores = list(best_by_level.values())
    latest_row = max(rows, key=lambda a: a.completed_at) if rows else None
    all_completed = len(passed_levels) >= len(available) and len(available) > 0

    return levels, {
        "current_level": current_level,
        "all_completed": all_completed,
        "completed_count": len(passed_levels),
        "total_levels": len(available),
        "latest_score": round(float(latest_row.average_total or 0)) if latest_row else None,
        "best_score": round(max(all_scores)) if all_scores else None,
        "recent": latest_row,
        "pass_threshold": PASS_THRESHOLD,
    }


@bp.route("/self-learning")
@login_required
def self_learning():
    """RAMCO-style level map: locked / unlocked / current / completed, backed by our GameEngine's
    5 levels (data/scenarios/level_1..5.json) and the learner's own Attempt history."""
    session.pop("active_room_id", None)
    levels, progress = _self_learning_progress(current_user)
    return render_template(
        "self-learning.html",
        levels=levels,
        completed_count=progress["completed_count"],
        best_score=progress["best_score"],
        latest_score=progress["latest_score"],
        pass_threshold=progress["pass_threshold"],
    )


@bp.route("/dashboard")
@login_required
def dashboard():
    """Report #2 — overall performance across every attempt stored for this learner,
    plus the RAMCO-style Self Learning Progress / Recent Score / Achievements cards."""
    report = build_dashboard_report(current_user)
    achievements = build_achievements(current_user)
    _, self_learning_progress = _self_learning_progress(current_user)

    hosted_rooms = Room.query.filter_by(instructor_id=current_user.id).order_by(Room.created_at.desc()).all()
    joined_members = (
        RoomMember.query.filter_by(user_id=current_user.id)
        .join(Room, RoomMember.room_id == Room.id)
        .order_by(Room.created_at.desc())
        .all()
    )

    changed = False
    for room in hosted_rooms:
        if room.refresh_expiry():
            changed = True
    for member in joined_members:
        if member.room.refresh_expiry():
            changed = True
    if changed:
        db.session.commit()

    return render_template(
        "dashboard.html",
        report=report,
        achievements=achievements,
        self_learning=self_learning_progress,
        hosted_rooms=hosted_rooms,
        joined_rooms=joined_members,
    )


@bp.route("/dashboard/report/<int:attempt_id>/pdf")
@login_required
def download_attempt_report_pdf(attempt_id):
    """Download button on report #1 (the per-level report)."""
    attempt = Attempt.query.get_or_404(attempt_id)
    if attempt.user_id != current_user.id and not current_user.is_instructor:
        abort(403)

    report = build_attempt_report(attempt)
    path = os.path.join(tempfile.gettempdir(), f"level_report_{attempt_id}.pdf")
    export_attempt_report_pdf(report, path)
    return send_file(path, as_attachment=True, download_name=f"level_{attempt.level}_report.pdf")


@bp.route("/dashboard/report/pdf")
@login_required
def download_dashboard_report_pdf():
    """Download button on report #2 (the dashboard's overall report)."""
    report = build_dashboard_report(current_user)
    path = os.path.join(tempfile.gettempdir(), f"dashboard_report_{current_user.id}.pdf")
    export_dashboard_report_pdf(report, path)
    return send_file(path, as_attachment=True, download_name="overall_performance_report.pdf")
