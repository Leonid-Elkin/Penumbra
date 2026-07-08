"""
game.save — persistent campaign profiles, scoring and unlock logic.

Three save slots live as saves/slot{1,2,3}.json. A slot fixes its difficulty at
creation and records, per level, whether it's been completed plus the best stars,
best time and best score.
"""

from __future__ import annotations
import os, json
from . import config

# Difficulty → enemy-volume multiplier fed to EnemyDirector. Locked per save.
DIFFICULTY = {"easy": 0.7, "normal": 1.3, "hard": 2.3}
DIFFICULTY_ORDER = ["easy", "normal", "hard"]


def compute_result(clear_time: float, hp_frac: float) -> dict:
    """Score a win from clear time + remaining base-HP fraction → stars (1-3)."""
    t3, par = config.TIME_3STAR, config.TIME_PAR
    time_score = max(0.0, min(1.0, (par - clear_time) / (par - t3))) if par > t3 else 0.0
    hp_frac = max(0.0, min(1.0, hp_frac))
    score = round(600 * time_score + 400 * hp_frac)
    stars = 3 if score >= 700 else 2 if score >= 400 else 1
    return {"time": round(clear_time, 1), "score": score, "stars": stars}


class SaveSlot:
    def __init__(self, slot: int, difficulty: str, levels: dict | None = None,
                 flags: list | None = None):
        self.slot = slot
        self.difficulty = difficulty if difficulty in DIFFICULTY else "normal"
        # key -> {"completed": bool, "stars": int, "best_time": float, "best_score": int}
        self.levels = levels or {}
        # One-off "seen" markers for in-battle guidance that must fire only the first
        # time per profile (e.g. the oil-platform capture tutorial). See seen/mark_seen.
        self.flags = set(flags or ())

    # ── Results ─────────────────────────────────────────────────────────────
    def record(self, key: str, time: float, score: int, stars: int):
        cur = self.levels.get(key)
        if cur is None:
            self.levels[key] = {"completed": True, "stars": stars,
                                "best_time": round(time, 1), "best_score": score}
            return
        cur["completed"] = True
        cur["stars"] = max(cur.get("stars", 0), stars)
        cur["best_score"] = max(cur.get("best_score", 0), score)
        bt = cur.get("best_time")
        if bt is None or time < bt:
            cur["best_time"] = round(time, 1)

    def info(self, key: str) -> dict | None:
        return self.levels.get(key)

    def stars(self, key: str) -> int:
        d = self.levels.get(key); return d["stars"] if d else 0

    def completed(self, key: str) -> bool:
        d = self.levels.get(key); return bool(d and d.get("completed"))

    # ── One-off guidance flags ──────────────────────────────────────────────
    def seen(self, flag: str) -> bool:
        return flag in self.flags

    def mark_seen(self, flag: str):
        self.flags.add(flag)

    # ── Unlock logic ────────────────────────────────────────────────────────
    def is_unlocked(self, key: str, ordered: list[str]) -> bool:
        if key not in ordered:
            return False
        i = ordered.index(key)
        if i == 0:
            return True
        if key == ordered[-1]:                       # the secret final boss
            return self.unknown_unlocked(ordered)
        return self.completed(ordered[i - 1])

    def unknown_unlocked(self, ordered: list[str]) -> bool:
        """The final level opens only when every prior level is 3-starred."""
        return all(self.stars(k) >= 3 for k in ordered[:-1])

    def total_stars(self) -> int:
        return sum(d.get("stars", 0) for d in self.levels.values())

    def cleared_count(self) -> int:
        return sum(1 for d in self.levels.values() if d.get("completed"))

    # ── Serialisation ───────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {"slot": self.slot, "difficulty": self.difficulty,
                "levels": self.levels, "flags": sorted(self.flags)}

    @classmethod
    def from_dict(cls, d: dict) -> "SaveSlot":
        return cls(int(d.get("slot", 0)), d.get("difficulty", "normal"),
                   d.get("levels", {}), d.get("flags", []))


class SaveManager:
    SLOTS = (1, 2, 3)

    def __init__(self, directory: str | None = None):
        self.dir = directory or config.SAVE_DIR
        os.makedirs(self.dir, exist_ok=True)

    def _path(self, slot: int) -> str:
        return os.path.join(self.dir, f"slot{slot}.json")

    def exists(self, slot: int) -> bool:
        return os.path.exists(self._path(slot))

    def load(self, slot: int) -> SaveSlot | None:
        if not self.exists(slot):
            return None
        try:
            with open(self._path(slot), "r", encoding="utf-8") as fh:
                return SaveSlot.from_dict(json.load(fh))
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def create(self, slot: int, difficulty: str) -> SaveSlot:
        s = SaveSlot(slot, difficulty, {})
        self.save(s)
        return s

    def save(self, slot_obj: SaveSlot):
        with open(self._path(slot_obj.slot), "w", encoding="utf-8") as fh:
            json.dump(slot_obj.to_dict(), fh, indent=2)

    def erase(self, slot: int):
        try:
            os.remove(self._path(slot))
        except OSError:
            pass
