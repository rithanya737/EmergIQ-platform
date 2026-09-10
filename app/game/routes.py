"""Game routes: /game/level/<n>, /game/level/<n>/decide, /game/level/<n>/finish,
/game/level/<n>/start-timer, /game/level/<n>/expire — the last two implement
a real server-side deadline that only starts counting once the pre-level
briefing (the scenario's intro_sequence) has finished."""
import os
from datetime import datetime, timedelta

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.evaluator.metrics import decision_scorecard
from app.evaluator.ollama_client import is_reachable
from app.evaluator.rag_pipeline import evaluate_decision
from app.evaluator.report_generator import build_attempt_report, save_attempt_outcome
from app.evaluator.rubric import score_decision
from app.game.engine import GameEngine
from app.game.level_manager import PASS_THRESHOLD
from app.game.models import Attempt

bp = Blueprint("game", __name__, url_prefix="/game")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# In-memory table of in-progress attempts, keyed by (user_id, level).
# Fine for a single-process dev server; swap for a real session/cache store
# before running this behind multiple workers.
_ACTIVE_GAMES = {}

# Every level gets this many seconds per decision point in its flow, doubled
# from the "expected" pace so a careful learner isn't rushed — this is the
# real, server-enforced deadline (the client's countdown is just a display
# of it, not the authority). It only starts once the briefing has finished
# (see start_timer() below), so reading the intro never eats into it.
SECONDS_PER_STEP = 180


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


def _scenario_path(level: int) -> str:
    return os.path.join(BASE_DIR, "data", "scenarios", f"level_{level}.json")


def _time_budget_seconds(engine: GameEngine) -> int:
    steps = len((engine.scenario.get("flow_control") or {}).get("sequence") or []) or 1
    return steps * SECONDS_PER_STEP


def _intro_phases(scenario: dict) -> list:
    """Reshape the scenario's intro_sequence (a dict of ordered phases, each
    either the hospital voiceover or a character's dialogue) into a flat
    list of {"speaker", "lines"} the play screen shows before the first
    real decision — the "system greeting + situation" briefing."""
    phases = []
    for phase in (scenario.get("intro_sequence") or {}).values():
        lines = phase.get("dialogue") or phase.get("voiceover") or []
        if not lines:
            continue
        speaker = phase.get("character") or "EMERGIQ"
        phases.append({"speaker": speaker.upper(), "lines": lines})
    return phases


def _finalize_attempt(key, game, level, status):
    """Persist whatever this attempt has (finished normally or cut off by
    the server-side deadline) and tear down the in-memory game state."""
    engine = game["engine"]
    room_id = session.get("active_room_id")

    attempt = Attempt(
        user_id=current_user.id,
        room_id=room_id,
        level=level,
        scenario_title=engine.scenario.get("level_name"),
        started_at=game["started_at"],
        status=status,
    )
    db.session.add(attempt)
    db.session.flush()  # get attempt.id before save_attempt_outcome() inserts DecisionScore rows

    if game["evaluated_decisions"]:
        save_attempt_outcome(attempt, game["evaluated_decisions"])
    else:
        # Time ran out before a single decision was scored.
        attempt.completed_at = datetime.utcnow()
        attempt.outcome_label = "No decisions recorded"
    db.session.commit()

    if room_id:
        from app.rooms.models import RoomMember
        from app.rooms.routes import _maybe_mark_finished
        member = RoomMember.query.filter_by(room_id=room_id, user_id=current_user.id).first()
        if member:
            _maybe_mark_finished(member)
    session.pop("active_room_id", None)

    del _ACTIVE_GAMES[key]
    return attempt


@bp.route("/level/<int:level>")
@login_required
def play_level(level):
    """Start (or resume) a level and render the play screen. A brand-new
    game shows the scenario's briefing (intro_sequence) before any decision
    or countdown — the deadline itself is only armed once the client calls
    /start-timer after the briefing finishes."""
    room_id = session.get("active_room_id")

    # Solo (non-room) play is gated: a learner can only open a level once
    # they've scored PASS_THRESHOLD+ on every level before it — otherwise
    # they're bounced back to the level map to retry whichever one is
    # blocking them. Room-based assessments are host-controlled/time-windowed
    # separately (rooms.enter_assessment already sequences those levels), so
    # this gate only applies when there is no active room on the session.
    if not room_id and level > 1:
        best_by_level = {}
        rows = (
            Attempt.query.filter_by(user_id=current_user.id)
            .filter(Attempt.room_id.is_(None))
            .filter(Attempt.completed_at.isnot(None))
            .all()
        )
        for a in rows:
            score = float(a.average_total or 0)
            if a.level not in best_by_level or score > best_by_level[a.level]:
                best_by_level[a.level] = score
        for prior in range(1, level):
            if best_by_level.get(prior, 0) < PASS_THRESHOLD:
                flash(f"Score {PASS_THRESHOLD}+ on Level {prior:02d} before moving on to this level.", "error")
                return redirect(url_for("dashboard.self_learning"))

    key = (current_user.id, level, room_id)
    game = _ACTIVE_GAMES.get(key)
    is_new = game is None

    if is_new:
        engine = GameEngine(scenario_path=_scenario_path(level))
        engine.start_game()
        game = {
            "engine": engine,
            "evaluated_decisions": [],
            "started_at": datetime.utcnow(),
            "expires_at": None,
            "timer_started": False,
        }
        _ACTIVE_GAMES[key] = game

    decision = game["engine"].get_current_decision()
    level_title = game["engine"].scenario.get("level_name") or f"Level {level}"
    intro_phases = _intro_phases(game["engine"].scenario) if is_new else []

    if game["expires_at"] is not None:
        remaining_seconds = max(int((game["expires_at"] - datetime.utcnow()).total_seconds()), 0)
    else:
        remaining_seconds = None

    context_label = "Self Learning"
    is_assessment = bool(room_id)
    if room_id:
        from app.rooms.models import Room
        room = Room.query.get(room_id)
        if room:
            context_label = f"Assessment · {room.name}"

    # Hospital orientation video: shown once before Level 1 (any context)
    # and once before a learner's first-ever assessment (any level) — never
    # on a resumed/reloaded attempt, so it's gated on is_new just like the
    # text briefing above. Which of those two "once" buckets actually apply
    # is decided client-side (game.js) against two separate localStorage
    # flags, since this is a per-browser "seen it" check, not a DB one.
    show_intro_video = is_new and (level == 1 or is_assessment)

    return render_template(
        "game_level.html",
        level=level,
        level_title=level_title,
        decision=decision,
        remaining_seconds=remaining_seconds,
        intro_phases=intro_phases,
        context_label=context_label,
        is_new_attempt=is_new,
        is_assessment=is_assessment,
        show_intro_video=show_intro_video,
        intro_video_url=url_for("static", filename="media/Hospital_Intro_video.mp4"),
    )


@bp.route("/level/<int:level>/exit", methods=["POST"])
@login_required
def exit_level(level):
    """"Save & exit" — there is no partial-progress save (a level is
    scored as a whole once finished), so this simply abandons the
    in-progress attempt. Without this, the in-memory game for this
    (user, level) would stay alive and the next visit would silently
    resume it — skipping the briefing and picking up mid-decision,
    which looks like a bug rather than a fresh start."""
    room_id = session.get("active_room_id")
    key = (current_user.id, level, room_id)
    _ACTIVE_GAMES.pop(key, None)
    session.pop("active_room_id", None)
    if room_id:
        from app.rooms.models import Room
        room = Room.query.get(room_id)
        if room:
            return redirect(url_for("rooms.room_detail", code=room.code))
    return redirect(url_for("dashboard.dashboard"))


@bp.route("/level/<int:level>/start-timer", methods=["POST"])
@login_required
def start_timer(level):
    """Called once the client has walked through the briefing (or
    immediately, if there was none) — arms the real deadline exactly once
    per attempt so reading the intro never costs the learner time."""
    key = (current_user.id, level, session.get("active_room_id"))
    game = _ACTIVE_GAMES.get(key)
    if game is None:
        return jsonify({"error": "No game in progress for this level. Reload the page."}), 400
    if not game["timer_started"]:
        game["expires_at"] = datetime.utcnow() + timedelta(seconds=_time_budget_seconds(game["engine"]))
        game["timer_started"] = True
    remaining = max(int((game["expires_at"] - datetime.utcnow()).total_seconds()), 0)
    return jsonify({"remaining_seconds": remaining})


@bp.route("/level/<int:level>/decide", methods=["POST"])
@login_required
def decide(level):
    """AJAX endpoint the play screen calls for every A/B/C/D choice."""
    key = (current_user.id, level, session.get("active_room_id"))
    game = _ACTIVE_GAMES.get(key)
    if game is None:
        return jsonify({"error": "No game in progress for this level. Reload the page."}), 400

    if game["expires_at"] is not None and datetime.utcnow() >= game["expires_at"]:
        attempt = _finalize_attempt(key, game, level, "time_expired")
        return jsonify({
            "expired": True,
            "game_finished": True,
            "redirect": url_for("game.level_report", attempt_id=attempt.id),
        })

    engine = game["engine"]
    payload = request.get_json(force=True) or {}
    option_id = payload.get("option")
    time_taken_seconds = float(payload.get("time_taken_seconds") or 0)

    decision_before = engine.get_current_decision()
    evaluator_cfg = (decision_before or {}).get("evaluator", {})
    selected_option = next((o for o in decision_before["options"] if o["id"] == option_id), None)
    # The engine's make_decision() wants the actual list of resource names
    # (it extends its own resources_used log with this), not a count.
    resources_used_list = (selected_option or {}).get("resources_used") or []
    resources_used_count = len(resources_used_list)

    result = engine.make_decision(
        option=option_id,
        time_taken_seconds=time_taken_seconds,
        resources_used=resources_used_list,
    )

    # resources_optimal in this scenario schema is a list of resource names
    # (the answer key), not a number — normalize either shape to a count.
    optimal_raw = evaluator_cfg.get("resources_optimal", resources_used_count)
    resources_optimal_count = len(optimal_raw) if isinstance(optimal_raw, (list, tuple)) else int(optimal_raw or 0)
    expected_seconds = evaluator_cfg.get("expected_seconds", evaluator_cfg.get("expected_seconds_per_patient", 0))

    # This scenario schema has no free-text scenario/question field on a
    # node — fall back to the nurse dialogue as grounding context for the
    # RAG/LLM judgment (and the deterministic rubric doesn't need it at all).
    scenario_text = " ".join(decision_before.get("nurse_dialogue") or [])

    # Score this decision for the reporting/evaluator pipeline too (separate
    # from GameEngine's own scoring, which already drove `result`).
    judgment = _judgment_for(scenario_text, result.get("action_text", ""))
    scorecard = decision_scorecard(
        clinical_verdict=judgment.get("clinical_verdict", "acceptable"),
        time_taken_seconds=time_taken_seconds,
        expected_seconds=float(expected_seconds or 0),
        resources_used=resources_used_count,
        resources_optimal=resources_optimal_count,
        ethical_score_0_to_10=float(judgment.get("ethical_score", 5)),
    )
    game["evaluated_decisions"].append({
        "decision_point_id": decision_before.get("decision_id"),
        "decision_text": result.get("action_text"),
        "time_taken_seconds": time_taken_seconds,
        "expected_seconds": expected_seconds,
        "resources_used": resources_used_count,
        "resources_optimal": resources_optimal_count,
        "judgment": judgment,
        "scorecard": scorecard,
    })

    if engine.game_finished:
        # Capture in-time vs. expired at the moment the LAST decision
        # actually lands — finish_level() is called ~900ms later (after the
        # UI's fade transition), and re-checking the clock there instead
        # would wrongly mark a genuinely on-time finish as expired if the
        # deadline ticked over during that idle gap.
        game["finished_in_time"] = game["expires_at"] is None or datetime.utcnow() < game["expires_at"]

    response = dict(result)
    if not engine.game_finished:
        response["next"] = engine.get_current_decision()
    return jsonify(response)


@bp.route("/level/<int:level>/expire", methods=["POST"])
@login_required
def expire_level(level):
    """Client calls this when its countdown hits zero — the server re-checks
    the real deadline before honouring it, so a tampered clock can't cheat."""
    key = (current_user.id, level, session.get("active_room_id"))
    game = _ACTIVE_GAMES.get(key)
    if game is None:
        return jsonify({"error": "No game in progress for this level."}), 400
    if game["expires_at"] is None or datetime.utcnow() < game["expires_at"]:
        return jsonify({"error": "Not expired yet."}), 400
    attempt = _finalize_attempt(key, game, level, "time_expired")
    return jsonify({"redirect": url_for("game.level_report", attempt_id=attempt.id)})


@bp.route("/level/<int:level>/finish", methods=["POST"])
@login_required
def finish_level(level):
    """Persist the just-finished attempt (GameEngine.game_finished == True)."""
    key = (current_user.id, level, session.get("active_room_id"))
    game = _ACTIVE_GAMES.get(key)
    if game is None or not game["engine"].game_finished:
        return jsonify({"error": "Level is not finished yet."}), 400

    status = "completed" if game.get("finished_in_time", True) else "time_expired"
    attempt = _finalize_attempt(key, game, level, status)
    return jsonify({"redirect": url_for("game.level_report", attempt_id=attempt.id)})


@bp.route("/level/report/<int:attempt_id>")
@login_required
def level_report(attempt_id):
    """Report #1 — shown right after a level finishes, with a download button."""
    attempt = Attempt.query.get_or_404(attempt_id)
    if attempt.user_id != current_user.id and not current_user.is_instructor:
        return redirect(url_for("dashboard.dashboard"))

    report = build_attempt_report(attempt)
    return render_template("report.html", report=report, attempt_id=attempt.id, attempt=attempt)
