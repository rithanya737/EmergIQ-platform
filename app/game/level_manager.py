"""
LevelManager v2 -- matches the new data/scenarios layout:
  data/scenarios/index.json         (game-wide index, lists each level's file)
  data/scenarios/level1.json ... level5.json   (no underscore, level_id schema)

Falls back to level_manager.load_level(n) reading index.json when present
(so a filename change in index.json doesn't break lookups), and otherwise
tries level{n}.json / level_{n}.json directly.
"""
import json
import os

# A learner must score at least this on a level (metric #5's average_total,
# 0-100) before the next level unlocks in Self Learning. Scoring below this
# leaves the level "unlocked" (retry) rather than "completed" — the learner
# keeps retrying the SAME level until they clear it.
PASS_THRESHOLD = 75


class LevelManager:
    def __init__(self, scenarios_path=None):
        if scenarios_path is None:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            scenarios_path = os.path.join(base_dir, "data", "scenarios")
        self.scenarios_path = scenarios_path
        self._index = self._load_index()

    # ---------------------------------------------------------
    def _load_index(self):
        index_path = os.path.join(self.scenarios_path, "index.json")
        if not os.path.exists(index_path):
            return None
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def _candidate_paths(self, level_number):
        candidates = []
        if self._index:
            for entry in self._index.get("levels", []):
                if entry.get("level_id") == level_number:
                    named = entry.get("file")
                    if named:
                        candidates.append(os.path.join(self.scenarios_path, named))
                    break
        candidates.append(os.path.join(self.scenarios_path, f"level{level_number}.json"))
        candidates.append(os.path.join(self.scenarios_path, f"level_{level_number}.json"))
        return candidates

    def _resolve_path(self, level_number):
        for path in self._candidate_paths(level_number):
            if os.path.exists(path):
                return path
        return None

    # ---------------------------------------------------------
    # LOAD LEVEL
    # ---------------------------------------------------------
    def load_level(self, level_number):
        path = self._resolve_path(level_number)
        if path is None:
            raise FileNotFoundError(
                f"Level {level_number} scenario not found in {self.scenarios_path} "
                f"(tried: {', '.join(self._candidate_paths(level_number))})"
            )
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def level_exists(self, level_number):
        return self._resolve_path(level_number) is not None

    # ---------------------------------------------------------
    # GET LEVEL INFORMATION
    # ---------------------------------------------------------
    def get_level_info(self, level_number):
        scenario = self.load_level(level_number)
        return {
            "level": scenario.get("level_id"),
            "title": scenario.get("level_name"),
            "difficulty": scenario.get("difficulty"),
            "patient_count": scenario.get("patient_count"),
            "main_skill": scenario.get("main_skill"),
        }

    # ---------------------------------------------------------
    # LEVEL UNLOCK LOGIC
    # ---------------------------------------------------------
    def is_level_unlocked(self, level_number, completed_levels=None):
        if level_number <= 1:
            return True
        if completed_levels is None:
            completed_levels = []
        return (level_number - 1) in completed_levels

    def get_unlocked_levels(self, max_level=5, completed_levels=None):
        if completed_levels is None:
            completed_levels = []
        return [n for n in range(1, max_level + 1) if self.is_level_unlocked(n, completed_levels)]

    def get_available_levels(self, max_level=5):
        levels = []
        for level_number in range(1, max_level + 1):
            if self.level_exists(level_number):
                levels.append(self.get_level_info(level_number))
        return levels

    # ---------------------------------------------------------
    # CHECK PASS -- delegates to the engine's own final result,
    # since v2 outcome logic (per-patient / combined / level-wide)
    # is level-shape-dependent and already resolved by GameEngine.
    # ---------------------------------------------------------
    def check_pass(self, level_number, final_result):
        return {
            "passed": bool(final_result.get("passed")),
            "status_code": final_result.get("status_code"),
            "critical_safety_error": bool(final_result.get("critical_safety_error")),
        }


if __name__ == "__main__":
    manager = LevelManager()
    print("MEDSIM Level Manager v2 Test")
    for n in range(1, 6):
        print(n, manager.level_exists(n), manager.get_level_info(n) if manager.level_exists(n) else None)
