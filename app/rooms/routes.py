"""Room routes: create/join/lobby/leaderboard + the host start/end/close
lifecycle actions, matching the RAMCO reference app's assessment flow.

A room covers one or more levels (Room.level_set) — the create-room form
lets the host filter the level list by difficulty and then tick as many of
those levels as the assessment should cover; a participant plays through
them in order, one attempt per level, same as self-learning."""
import os
import tempfile
from datetime import date, datetime

from flask import Blueprint, render_template, send_file, abort, redirect, url_for, request, flash, jsonify
from flask_login import login_required, current_user

from app.extensions import db
from app.game.level_manager import LevelManager
from app.rooms.models import Room, RoomMember, MAX_ASSESSMENT_DAYS
from app.evaluator.report_generator import build_room_report, export_room_report_pdf

bp = Blueprint("rooms", __name__, url_prefix="/room")


def _parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _levels_by_id():
    return {l["level"]: l for l in LevelManager().get_available_levels(max_level=5)}


def _level_summary(level_ids):
    """'Level 01 — First Response, Level 02 — Resource Under Pressure'"""
    by_id = _levels_by_id()
    parts = []
    for lid in sorted(level_ids):
        info = by_id.get(lid)
        title = info["title"] if info else f"Level {lid}"
        parts.append(f"Level {lid:02d} — {title}")
    return parts


@bp.route("/create", methods=["GET", "POST"])
@login_required
def create_room():
    """Any signed-in account can create an assessment room and become its Host —
    every registered account is the same, matching the RAMCO flow."""
    manager = LevelManager()
    levels = manager.get_available_levels(max_level=5)
    difficulties = sorted({l["difficulty"] for l in levels if l.get("difficulty")}) or ["standard"]
    today = date.today().isoformat()
    error = None

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        try:
            participant_limit = int(request.form.get("participant_limit"))
        except (TypeError, ValueError):
            participant_limit = None
        difficulty = request.form.get("difficulty") or ""
        try:
            selected_levels = sorted({int(v) for v in request.form.getlist("levels")})
        except (TypeError, ValueError):
            selected_levels = []
        from_date = _parse_date(request.form.get("from_date"))
        to_date = _parse_date(request.form.get("to_date"))
        valid_level_ids = {l["level"] for l in levels}

        if not name:
            error = "Enter a room name."
        elif not participant_limit or not (1 <= participant_limit <= 200):
            error = "Participant limit must be between 1 and 200."
        elif difficulty not in difficulties:
            error = "Choose a valid difficulty."
        elif not selected_levels or not set(selected_levels).issubset(valid_level_ids):
            error = "Choose at least one level."
        elif not from_date or not to_date:
            error = "Select an assessment start and end date."
        elif from_date < date.today():
            error = "The assessment cannot start in the past."
        elif to_date < from_date:
            error = "The end date must be on or after the start date."
        elif (to_date - from_date).days + 1 > MAX_ASSESSMENT_DAYS:
            error = f"Assessment period cannot exceed {MAX_ASSESSMENT_DAYS} days."

        if not error:
            room = Room(
                name=name,
                instructor_id=current_user.id,
                difficulty=difficulty,
                participant_limit=participant_limit,
                from_date=from_date,
                to_date=to_date,
            )
            room.level_set = selected_levels
            db.session.add(room)
            db.session.commit()
            return redirect(url_for("rooms.room_detail", code=room.code))

    return render_template(
        "room_create.html",
        levels=levels,
        difficulties=difficulties,
        today=today,
        max_days=MAX_ASSESSMENT_DAYS,
        error=error,
        form=request.form,
    )


@bp.route("/join", methods=["GET", "POST"])
@login_required
def join_room():
    """Learner enters a join code."""
    if request.method == "POST":
        code = (request.form.get("code") or "").strip().upper()
        room = Room.query.filter_by(code=code).first()
        if not room:
            flash("No room found with that code.", "error")
            return render_template("room_join.html"), 404

        if room.refresh_expiry():
            db.session.commit()

        if room.instructor_id == current_user.id:
            return redirect(url_for("rooms.room_detail", code=room.code))

        member = RoomMember.query.filter_by(room_id=room.id, user_id=current_user.id).first()
        if not member:
            if room.status not in ("waiting", "active"):
                flash("This assessment is not available to join.", "error")
                return render_template("room_join.html"), 409
            if room.participant_count() >= room.participant_limit:
                flash("This room is full.", "error")
                return render_template("room_join.html"), 409
            member = RoomMember(room_id=room.id, user_id=current_user.id)
            db.session.add(member)
            db.session.commit()
        return redirect(url_for("rooms.room_detail", code=room.code))

    return render_template("room_join.html")


@bp.route("/<code>")
@login_required
def room_detail(code):
    """Host sees the waiting-room lobby (or final leaderboard once the
    assessment has started/ended); a participant sees the assessment-room
    detail view with a join/enter/wait state depending on room.status."""
    room = Room.query.filter_by(code=code).first_or_404()
    if room.refresh_expiry():
        db.session.commit()

    level_summary = _level_summary(room.level_set)

    if current_user.id == room.instructor_id:
        members = list(room.members)
        if room.status in ("waiting", "active"):
            return render_template(
                "room_waiting.html",
                room=room,
                members=members,
                participant_count=len(members),
                level_summary=level_summary,
            )
        leaderboard = sorted(members, key=lambda m: (m.finished_at is None, -(len(m.levels_completed()))))
        return render_template("leaderboard.html", room=room, members=leaderboard)

    member = RoomMember.query.filter_by(room_id=room.id, user_id=current_user.id).first()
    attempt = member.latest_attempt() if member else None
    next_level = None
    if member:
        completed = member.levels_completed()
        next_level = next((lv for lv in sorted(room.level_set) if lv not in completed), None)
    return render_template(
        "room_assessment.html",
        room=room,
        member=member,
        attempt=attempt,
        participant_count=room.participant_count(),
        level_summary=level_summary,
        next_level=next_level,
    )


@bp.route("/<code>/leaderboard")
@login_required
def room_leaderboard(code):
    """Standalone leaderboard view, reachable from the waiting room / assessment room at any time."""
    room = Room.query.filter_by(code=code).first_or_404()
    if room.refresh_expiry():
        db.session.commit()
    members = list(room.members)
    leaderboard = sorted(members, key=lambda m: (m.finished_at is None, -(len(m.levels_completed()))))
    return render_template("leaderboard.html", room=room, members=leaderboard)


@bp.route("/<code>/status")
@login_required
def room_status(code):
    """Lightweight JSON the waiting-room page polls for a live participant count."""
    room = Room.query.filter_by(code=code).first_or_404()
    if room.refresh_expiry():
        db.session.commit()
    return jsonify({
        "status": room.status,
        "participant_count": room.participant_count(),
        "participant_limit": room.participant_limit,
    })


def _host_room_or_404(code):
    room = Room.query.filter_by(code=code).first_or_404()
    if room.instructor_id != current_user.id:
        abort(403)
    return room


@bp.route("/<code>/start", methods=["POST"])
@login_required
def start_room(code):
    room = _host_room_or_404(code)
    if room.status == "waiting" and room.participant_count() > 0 and room.window_open:
        room.status = "active"
        room.started_at = datetime.utcnow()
        db.session.commit()
    return redirect(url_for("rooms.room_detail", code=code))


@bp.route("/<code>/end", methods=["POST"])
@login_required
def end_room(code):
    room = _host_room_or_404(code)
    if room.status == "active":
        room.status = "completed"
        room.completed_at = datetime.utcnow()
        db.session.commit()
    return redirect(url_for("rooms.room_detail", code=code))


@bp.route("/<code>/close", methods=["POST"])
@login_required
def close_room(code):
    room = _host_room_or_404(code)
    if room.status == "waiting":
        room.status = "closed"
        room.closed_at = datetime.utcnow()
        db.session.commit()
    return redirect(url_for("dashboard.dashboard"))


@bp.route("/<code>/enter")
@login_required
def enter_assessment(code):
    """Participant clicks 'Enter/Resume Assessment' — stamps the room onto
    the session so game.routes ties the resulting Attempt back to this room,
    then sends them to the first level in the room's level_set they haven't
    completed yet (levels are played in order, one attempt each)."""
    room = Room.query.filter_by(code=code).first_or_404()
    member = RoomMember.query.filter_by(room_id=room.id, user_id=current_user.id).first()
    if not member or room.status != "active":
        return redirect(url_for("rooms.room_detail", code=code))
    completed = member.levels_completed()
    next_level = next((lv for lv in sorted(room.level_set) if lv not in completed), None)
    if next_level is None:
        return redirect(url_for("rooms.room_detail", code=code))
    from flask import session
    session["active_room_id"] = room.id
    return redirect(url_for("game.play_level", level=next_level))


def _maybe_mark_finished(member: RoomMember) -> None:
    """Call this after every attempt completes inside a room (from
    game/routes.py's finish_level, alongside save_attempt_outcome). Once the
    learner has completed every level in the room's level_set, this closes
    out their participation so report #3 is ready to show/download."""
    if member.finished_at is None and member.is_finished():
        member.finished_at = datetime.utcnow()
        db.session.commit()
        room = member.room
        if room.status == "active":
            total = room.participant_count()
            finished = sum(1 for m in room.members if m.finished_at is not None)
            if total and finished >= total:
                room.status = "completed"
                room.completed_at = datetime.utcnow()
                db.session.commit()


@bp.route("/<code>/report")
@login_required
def room_report(code):
    """
    Report #3 — one learner's performance across every level they played in
    this room, shown once the assessment ends (irrespective of how many of
    the room's levels that turned out to be).
    """
    room = Room.query.filter_by(code=code).first_or_404()
    member = RoomMember.query.filter_by(room_id=room.id, user_id=current_user.id).first_or_404()

    report = build_room_report(room, current_user)
    download_url = url_for("rooms.download_room_report_pdf", code=room.code)
    return render_template("room_report.html", report=report, room=room, member=member, download_url=download_url)


def _export_room_report_pdf_for(room, learner):
    report = build_room_report(room, learner)
    path = os.path.join(tempfile.gettempdir(), f"room_{room.code}_report_{learner.id}.pdf")
    export_room_report_pdf(report, path)
    return path


@bp.route("/<code>/report/pdf")
@login_required
def download_room_report_pdf(code):
    """Learner downloads their own report for this room."""
    room = Room.query.filter_by(code=code).first_or_404()
    RoomMember.query.filter_by(room_id=room.id, user_id=current_user.id).first_or_404()

    path = _export_room_report_pdf_for(room, current_user)
    return send_file(path, as_attachment=True, download_name=f"assessment_report_{room.code}.pdf")


@bp.route("/<code>/report/<int:user_id>")
@login_required
def room_report_for_learner(code, user_id):
    """Host view of a specific learner's assessment report."""
    room = Room.query.filter_by(code=code).first_or_404()
    if room.instructor_id != current_user.id:
        abort(403)
    member = RoomMember.query.filter_by(room_id=room.id, user_id=user_id).first_or_404()
    report = build_room_report(room, member.user)
    download_url = url_for("rooms.download_room_report_pdf_for_learner", code=room.code, user_id=user_id)
    return render_template("room_report.html", report=report, room=room, member=member, download_url=download_url)


@bp.route("/<code>/report/<int:user_id>/pdf")
@login_required
def download_room_report_pdf_for_learner(code, user_id):
    """Host downloads a specific learner's report for this room — the button
    on room_report.html points here (not the self-download route above) when
    the host is viewing someone else's report, since the host usually has no
    RoomMember row of their own in the room and would otherwise 404."""
    room = Room.query.filter_by(code=code).first_or_404()
    if room.instructor_id != current_user.id:
        abort(403)
    member = RoomMember.query.filter_by(room_id=room.id, user_id=user_id).first_or_404()

    path = _export_room_report_pdf_for(room, member.user)
    return send_file(path, as_attachment=True, download_name=f"assessment_report_{room.code}_{member.user.name}.pdf")
