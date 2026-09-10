"""
MEDSIM Game Engine v2

Rewritten to match the richer scenario schema now in data/scenarios/
(level_id / patients[] / decision_tree / flow_control.sequence / endings),
which replaces the older level/scenario/patient single-object schema.

Design notes (why this engine is schema-driven rather than hard-coded
to a fixed D1->D2->D3->D4 shape, because the new scenario files vary a lot):

- Traversal is driven entirely by flow_control.sequence. Each step starts
  at a node (step['chain_start'] or step['node']). After the player picks
  an option, if that option has a truthy 'next_decision', the engine moves
  to that node and STAYS on the same flow_control step (this is how a
  single patient's multi-node chain -- e.g. D1->D2->D3->D4, or
  D1_FARHAN->D2_FARHAN->D3_FARHAN -- plays out). If the chosen option has
  no next_decision, the step is complete and the engine advances to the
  next entry in flow_control.sequence and starts its node.
- A "node path" can be a single decision_tree key ("D1_INITIAL_RESPONSE")
  or a dotted path into a nested node ("TRIAGE_SWEEP.P1_TRIAGE" ->
  decision_tree['TRIAGE_SWEEP']['patients']['P1_TRIAGE']).
- Per-patient effects on an option can appear as: a direct 'vitals_delta'
  / 'new_status' pair (applies to the node's own patient_id), as
  'effect_on_P1' / 'effect_on_P2' dicts, or as flat 'vitals_delta_P1' /
  'new_status_P1' suffixed keys. All three shapes are supported.
- Terminal options resolve an outcome either via a flat 'outcome' key, or
  via 'outcome_if_prior_state_recoverable' / '..._unrecoverable', chosen
  by checking whether the patient's status *before* that decision is
  listed in the node's 'recoverability_rule.unrecoverable_prior_states'
  (this is the "true arrest is unrecoverable" rule the content describes).
- vitals_delta values in this schema are free-text strings ("118 -> 124
  (up)", "-> 108 (stabilizing)", or just "up"/"declining" with no
  numbers). _parse_vitals_delta extracts a trailing numeric/BP-style
  value when present and otherwise leaves the vital's numeric value
  unchanged (still recording the qualitative note for display).
- Levels 1-2 (single patient) and levels 3-5 (2 or 6 patients, including
  meta decisions like TRIAGE_SWEEP / TREATMENT_ORDER / *_ALLOCATION that
  aren't tied to one patient) all run through the same traversal loop.
"""
import json
import os
import re
import time
from copy import deepcopy


QUALITY_SCORES = {
    "optimal": 1.0,
    "delay": 0.60,
    "resource_waste": 0.50,
    "suboptimal_ethics": 0.50,
    "critical_error": 0.0,
}

_PATIENT_SUFFIX_RE = re.compile(r"^(vitals_delta|new_status|effect_on)_?(P\d+)$")


def _coerce_number(text):
    text = text.strip()
    if re.fullmatch(r"-?\d+/\d+", text):  # blood-pressure style, keep as string
        return text
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    try:
        return float(text)
    except ValueError:
        return text


def _parse_vitals_delta(raw):
    """Returns {vital_name: {'raw': original text, 'new': parsed value or None}}"""
    if not raw or not isinstance(raw, dict):
        return {}
    parsed = {}
    for vital, value in raw.items():
        if isinstance(value, (int, float)):
            parsed[vital] = {"raw": str(value), "new": value}
            continue
        if not isinstance(value, str):
            parsed[vital] = {"raw": str(value), "new": None}
            continue
        match = re.search(r"->\s*([\d./]+)", value)
        if match:
            parsed[vital] = {"raw": value, "new": _coerce_number(match.group(1))}
        else:
            parsed[vital] = {"raw": value, "new": None}
    return parsed


class GameEngine:
    """MEDSIM Game Engine v2 -- schema-driven, supports single- and
    multi-patient levels defined with a flow_control.sequence."""

    def __init__(self, scenario_path=None):
        if scenario_path is None:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            scenario_path = os.path.join(base_dir, "data", "scenarios", "level1.json")

        self.scenario_path = scenario_path
        self.scenario = self._load_scenario()

        self.patients = {}
        self.decision_history = []
        self.resources_used = []
        self.sequence = self.scenario.get("flow_control", {}).get("sequence", [])

        self.current_step_index = 0
        self.current_node_path = None

        self.game_started = False
        self.game_finished = False
        self.final_outcome = None
        self.total_score = 0.0
        self.critical_safety_error = False
        self.decision_start_time = None

    # ---------------------------------------------------------
    def _load_scenario(self):
        with open(self.scenario_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ---------------------------------------------------------
    # START
    # ---------------------------------------------------------
    def start_game(self):
        self.patients = {}
        for p in self.scenario.get("patients", []):
            pid = p["patient_id"]
            self.patients[pid] = {
                "patient_id": pid,
                "name": p.get("name"),
                "age": p.get("age"),
                "sex": p.get("sex"),
                "chief_complaint": p.get("chief_complaint"),
                "status": p.get("initial_status", "UNASSESSED"),
                "vitals": deepcopy(p.get("initial_vitals", {})),
                "final_outcome_key": None,
            }

        self.decision_history = []
        self.resources_used = []
        self.total_score = 0.0
        self.critical_safety_error = False
        self.game_started = True
        self.game_finished = False
        self.final_outcome = None

        self.current_step_index = 0
        self._enter_step(0)

        self._start_decision_timer()
        return self.get_game_state()

    # ---------------------------------------------------------
    # NODE / STEP RESOLUTION
    # ---------------------------------------------------------
    def _current_step(self):
        if self.current_step_index >= len(self.sequence):
            return None
        return self.sequence[self.current_step_index]

    def _step_start_node(self, step):
        if step is None:
            return None
        return step.get("chain_start") or step.get("node")

    def _node_path_resolvable(self, node_path):
        if not node_path:
            return False
        try:
            self._resolve_node(node_path)
            return True
        except (ValueError, KeyError, TypeError):
            return False

    def _enter_step(self, step_index):
        """Set current_step_index to the first index >= step_index whose
        node actually resolves in decision_tree, skipping placeholder /
        descriptive-only steps (some scenario files use the 'node' field
        for prose like "implicit in X outcome, no further node" when a
        patient has nothing further to decide at that point). If nothing
        resolvable remains, the game is finished."""
        idx = step_index
        while idx < len(self.sequence):
            node_path = self._step_start_node(self.sequence[idx])
            if self._node_path_resolvable(node_path):
                self.current_step_index = idx
                self.current_node_path = node_path
                return
            idx += 1
        self.current_step_index = len(self.sequence)
        self.current_node_path = None

    def _resolve_node(self, node_path):
        parts = node_path.split(".")
        tree = self.scenario["decision_tree"]
        if parts[0] not in tree:
            raise ValueError(f"Decision node '{parts[0]}' not found.")
        node = tree[parts[0]]
        for key in parts[1:]:
            if isinstance(node, dict) and "patients" in node and key in node["patients"]:
                node = node["patients"][key]
            elif isinstance(node, dict) and key in node:
                node = node[key]
            else:
                raise ValueError(f"Cannot resolve node path '{node_path}' at segment '{key}'.")
        return node

    def _get_option(self, node, option_id):
        option_id = str(option_id).strip().upper()
        for option in node.get("options", []):
            if str(option.get("option_id", option.get("id", ""))).upper() == option_id:
                return option
        raise ValueError(f"Invalid option '{option_id}'.")

    # ---------------------------------------------------------
    # TIMER
    # ---------------------------------------------------------
    def _start_decision_timer(self):
        self.decision_start_time = time.time()

    def _get_time_taken(self):
        if self.decision_start_time is None:
            return 0.0
        return round(time.time() - self.decision_start_time, 2)

    # ---------------------------------------------------------
    # CURRENT DECISION (what the frontend/tests read)
    # ---------------------------------------------------------
    def get_current_decision(self):
        if not self.game_started:
            raise ValueError("Game has not started.")
        if self.game_finished or self.current_node_path is None:
            return None

        node = self._resolve_node(self.current_node_path)
        step = self._current_step()

        primary_patient_id = node.get("patient_id") or (step.get("patient_id") if step else None)
        current_status = self.patients[primary_patient_id]["status"] if primary_patient_id in self.patients else None

        if "nurse_dialogue_by_state" in node and current_status:
            nurse_dialogue = node["nurse_dialogue_by_state"].get(current_status, node.get("nurse_dialogue", []))
        else:
            nurse_dialogue = node.get("nurse_dialogue", [])

        self._start_decision_timer()

        return {
            "decision_id": self.current_node_path,
            "stage": step.get("step") if step else None,
            "patient_id": primary_patient_id,
            "patients_involved": node.get("patients_involved"),
            "nurse_dialogue": nurse_dialogue,
            "options": [
                {
                    "id": o.get("option_id", o.get("id")),
                    "text": o.get("text"),
                    "quality": o.get("quality"),
                    "protocol_alignment": o.get("protocol_alignment"),
                    "resources_used": o.get("resource_use", []),
                }
                for o in node.get("options", [])
            ],
            "patients": {pid: self._patient_hud(pid) for pid in self.patients},
            "evaluator": node.get("evaluator", {}),
        }

    def _patient_hud(self, pid):
        p = self.patients[pid]
        return {
            "id": pid,
            "name": p["name"],
            "age": p["age"],
            "status": p["status"],
            "vitals": deepcopy(p["vitals"]),
        }

    # ---------------------------------------------------------
    # APPLY EFFECTS
    # ---------------------------------------------------------
    def _apply_patient_effect(self, pid, status=None, vitals_delta=None):
        if pid not in self.patients:
            return
        if status:
            self.patients[pid]["status"] = status
        parsed = _parse_vitals_delta(vitals_delta)
        for vital, info in parsed.items():
            if info["new"] is not None:
                self.patients[pid]["vitals"][vital] = info["new"]

    def _resolve_terminal_outcome(self, node, option, primary_patient_id, status_before):
        """Returns the outcome key this option resolves to, if any."""
        if "outcome" in option:
            return option["outcome"]

        if "outcome_if_prior_state_recoverable" in option or "outcome_if_prior_state_unrecoverable" in option:
            rule = node.get("recoverability_rule", {})
            unrecoverable_states = rule.get("unrecoverable_prior_states", [])
            if status_before in unrecoverable_states:
                return option.get("outcome_if_prior_state_unrecoverable")
            return option.get("outcome_if_prior_state_recoverable")

        return None

    # ---------------------------------------------------------
    # MAKE DECISION
    # ---------------------------------------------------------
    def make_decision(self, option=None, time_taken_seconds=None, resources_used=None):
        if not self.game_started:
            raise ValueError("Game has not started.")
        if self.game_finished:
            raise ValueError("Game has already finished.")
        if option is None:
            raise ValueError("A decision option is required.")

        node = self._resolve_node(self.current_node_path)
        step = self._current_step()
        selected = self._get_option(node, option)

        if time_taken_seconds is None:
            time_taken = self._get_time_taken()
        else:
            try:
                time_taken = float(time_taken_seconds)
            except (TypeError, ValueError):
                time_taken = self._get_time_taken()

        runtime_resources = resources_used if resources_used is not None else selected.get("resource_use", [])
        self.resources_used.extend(runtime_resources)

        primary_patient_id = node.get("patient_id") or (step.get("patient_id") if step else None)
        status_before = self.patients[primary_patient_id]["status"] if primary_patient_id in self.patients else None

        # --- direct effect on the node's own patient ---
        if primary_patient_id and ("vitals_delta" in selected or "new_status" in selected):
            self._apply_patient_effect(primary_patient_id, selected.get("new_status"), selected.get("vitals_delta"))

        # --- effect_on_P*, vitals_delta_P*, new_status_P* shaped keys ---
        per_patient_status = {}
        per_patient_vitals = {}
        for key, value in selected.items():
            m = _PATIENT_SUFFIX_RE.match(key)
            if not m:
                continue
            kind, pid = m.groups()
            if kind == "effect_on" and isinstance(value, dict):
                per_patient_status[pid] = value.get("status")
                per_patient_vitals[pid] = value.get("vitals_delta")
            elif kind == "new_status":
                per_patient_status[pid] = value
            elif kind == "vitals_delta":
                per_patient_vitals[pid] = value

        affected_patients = set(per_patient_status) | set(per_patient_vitals)
        for pid in affected_patients:
            self._apply_patient_effect(pid, per_patient_status.get(pid), per_patient_vitals.get(pid))

        # --- terminal outcome resolution ---
        outcome_key = self._resolve_terminal_outcome(node, selected, primary_patient_id, status_before)
        if outcome_key and primary_patient_id:
            self.patients[primary_patient_id]["final_outcome_key"] = outcome_key

        quality = str(selected.get("quality", "")).lower()
        if quality == "critical_error":
            self.critical_safety_error = True

        score = self._score_decision(selected, node, time_taken)
        self.total_score += score["total"]

        status_after = self.patients[primary_patient_id]["status"] if primary_patient_id in self.patients else None

        history_entry = {
            "decision_id": self.current_node_path,
            "patient_id": primary_patient_id or list(affected_patients) or None,
            "option_id": selected.get("option_id", selected.get("id")),
            "action_text": selected.get("text"),
            "quality": selected.get("quality"),
            "patient_status_before": status_before,
            "patient_status_after": status_after,
            "time_taken_seconds": round(time_taken, 2),
            "expected_seconds": node.get("evaluator", {}).get("expected_seconds", node.get("evaluator", {}).get("expected_seconds_per_patient", 0)),
            "resources_used": runtime_resources,
            "resources_optimal": node.get("evaluator", {}).get("resources_optimal", []),
            "score": score,
            "nurse_response": selected.get("nurse_response"),
        }
        self.decision_history.append(history_entry)

        # --- advance traversal ---
        next_decision = selected.get("next_decision")
        if next_decision:
            self.current_node_path = next_decision
        else:
            self._enter_step(self.current_step_index + 1)
            if self.current_node_path is None:
                self.game_finished = True
                self.final_outcome = self._resolve_level_outcome()

        self.decision_start_time = None

        return {
            "decision_id": history_entry["decision_id"],
            "selected_option": history_entry["option_id"],
            "action_text": history_entry["action_text"],
            "quality": history_entry["quality"],
            "patient_status_before": status_before,
            "patient_status_after": status_after,
            "nurse_response": selected.get("nurse_response"),
            "next_decision": self.current_node_path,
            "score": score,
            "total_score": round(self.total_score, 2),
            "game_finished": self.game_finished,
            "final_outcome": self.final_outcome,
        }

    # ---------------------------------------------------------
    # SCORING
    # ---------------------------------------------------------
    def _score_decision(self, option, node, time_taken):
        quality = str(option.get("quality", "")).lower()
        quality_factor = QUALITY_SCORES.get(quality, 0.5)

        evaluator = node.get("evaluator", {})
        expected_seconds = evaluator.get("expected_seconds", evaluator.get("expected_seconds_per_patient", 0)) or 0
        if expected_seconds:
            time_factor = 1.0 if time_taken <= expected_seconds else max(0.0, 1.0 - (time_taken - expected_seconds) / max(expected_seconds, 1))
        else:
            time_factor = 1.0

        resources_optimal = set(evaluator.get("resources_optimal", []))
        resources_used = set(option.get("resource_use", []))
        if resources_optimal:
            resource_factor = 1.0 if resources_used == resources_optimal else (0.5 if resources_used & resources_optimal else 0.0)
        else:
            resource_factor = 1.0

        decision_quality = round(quality_factor * 60, 2)
        response_time = round(time_factor * 20, 2)
        resource_efficiency = round(resource_factor * 10, 2)
        protocol_adherence = round(quality_factor * 10, 2)
        total = round(decision_quality + response_time + resource_efficiency + protocol_adherence, 2)

        return {
            "decision_quality": decision_quality,
            "response_time": response_time,
            "resource_efficiency": resource_efficiency,
            "protocol_adherence_abcde": protocol_adherence,
            "total": total,
        }

    def get_score(self):
        decision_count = len(self.decision_history)
        if decision_count == 0:
            final_score = 0.0
        else:
            final_score = (self.total_score / (decision_count * 100)) * 100
        return {
            "decision_quality": round(sum(d["score"]["decision_quality"] for d in self.decision_history), 2),
            "response_time": round(sum(d["score"]["response_time"] for d in self.decision_history), 2),
            "resource_efficiency": round(sum(d["score"]["resource_efficiency"] for d in self.decision_history), 2),
            "protocol_adherence_abcde": round(sum(d["score"]["protocol_adherence_abcde"] for d in self.decision_history), 2),
            "raw_total": round(self.total_score, 2),
            "total": round(final_score, 2),
        }

    # ---------------------------------------------------------
    # LEVEL OUTCOME (fires once the sequence is exhausted)
    # ---------------------------------------------------------
    def _resolve_level_outcome(self):
        endings = self.scenario.get("endings", {})

        final_statuses = {}
        for pid, p in self.patients.items():
            final_statuses[pid] = p.get("final_outcome_key") or p.get("status")

        any_critical = any("CRITICAL_ENDPOINT" in str(v) for v in final_statuses.values())
        any_partial = any(("PARTIAL" in str(v)) or ("INEFFICIENT" in str(v)) or ("UNMANAGED" in str(v)) for v in final_statuses.values())
        all_optimal = all(d["quality"] == "optimal" for d in self.decision_history) if self.decision_history else False

        # Shape A: flat STABILIZED / PARTIAL_STABILIZATION / CRITICAL_ENDPOINT endings (levels 1-2)
        if "STABILIZED" in endings and len(self.patients) == 1:
            (pid,) = self.patients.keys()
            outcome_key = final_statuses[pid] or "CRITICAL_ENDPOINT"
            if outcome_key not in endings:
                outcome_key = "CRITICAL_ENDPOINT" if any_critical else "PARTIAL_STABILIZATION"
            ending = endings.get(outcome_key, {})
            passed = ending.get("status_code") in ("SUCCESS", "SUCCESS_WITH_PENALTY") and not self.critical_safety_error
            return {
                "outcome_key": outcome_key,
                "status_code": ending.get("status_code", "FAILURE"),
                "banner": ending.get("banner", ""),
                "per_patient": final_statuses,
                "passed": passed,
                "score": self.get_score(),
            }

        # Shape B: per-patient prefixed keys + combined_outcome_logic (level 3 style)
        if "combined_outcome_logic" in endings:
            passed = (not any_critical) and all(
                (v in endings) and (endings[v].get("status_code") in ("SUCCESS", "SUCCESS_WITH_PENALTY"))
                for v in final_statuses.values()
            )
            status_code = "FAILURE" if any_critical else ("SUCCESS_WITH_PENALTY" if any_partial or not all_optimal else "SUCCESS")
            return {
                "outcome_key": "COMBINED",
                "status_code": status_code,
                "banner": "LEVEL COMPLETE" if not any_critical else "PREVENTABLE LOSS OF LIFE",
                "per_patient": final_statuses,
                "passed": passed,
                "score": self.get_score(),
            }

        # Shape C: level_outcome_logic + SUCCESS/SUCCESS_WITH_PENALTY/FAILURE (levels 4-5)
        if "level_outcome_logic" in endings:
            if any_critical:
                status_code = "FAILURE"
            elif any_partial or not all_optimal:
                status_code = "SUCCESS_WITH_PENALTY"
            else:
                status_code = "SUCCESS"
            ending = endings.get(status_code, {})
            passed = status_code in ("SUCCESS", "SUCCESS_WITH_PENALTY")
            return {
                "outcome_key": status_code,
                "status_code": status_code,
                "banner": ending.get("banner", ""),
                "per_patient": final_statuses,
                "passed": passed,
                "score": self.get_score(),
            }

        # Fallback
        status_code = "FAILURE" if any_critical else "SUCCESS"
        return {
            "outcome_key": status_code,
            "status_code": status_code,
            "banner": "",
            "per_patient": final_statuses,
            "passed": status_code == "SUCCESS",
            "score": self.get_score(),
        }

    def get_final_result(self):
        if self.final_outcome is None:
            self.final_outcome = self._resolve_level_outcome()
        return {
            "level": self.scenario.get("level_id"),
            "title": self.scenario.get("level_name"),
            **self.final_outcome,
            "critical_safety_error": self.critical_safety_error,
        }

    # ---------------------------------------------------------
    def get_game_state(self):
        return {
            "level": self.scenario.get("level_id"),
            "title": self.scenario.get("level_name"),
            "patients": {pid: self._patient_hud(pid) for pid in self.patients},
            "current_decision_id": self.current_node_path,
            "game_started": self.game_started,
            "game_finished": self.game_finished,
        }

    def get_patients(self):
        return {pid: self._patient_hud(pid) for pid in self.patients}

    def get_decision_history(self):
        return deepcopy(self.decision_history)

    def reset_game(self):
        return self.start_game()


if __name__ == "__main__":
    engine = GameEngine()
    print("MEDSIM Engine v2 manual test")
    print(engine.start_game())
