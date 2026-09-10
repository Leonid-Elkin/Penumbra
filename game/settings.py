"""
game.settings – player-facing options, shared by every screen and persisted.

One global SETTINGS object the whole game reads each frame, so a toggle applies
everywhere at once (menus and a live battle alike). Options are written to
saves/settings.json the moment they change and reloaded on the next launch.
"""

from __future__ import annotations
import json, os

from PyQt6.QtCore import Qt
from PyQt6.QtGui  import QKeySequence

from .config import SAVE_DIR

_FILE = os.path.join(SAVE_DIR, "settings.json")


# ── Rebindable controls ───────────────────────────────────────────────────────
# Each entry is (action id, human label, [default Qt key codes]). Every action
# can carry MORE THAN ONE key – the whole game reads its controls through
# SETTINGS.keys_for(action), so a key rebound in the SETTINGS panel takes effect
# everywhere at once and is remembered across launches. Beyond these fixed
# controls, every deployable ship gets its own "deploy:<unit>" action (defaults
# live in the HUD's deploy order, see DEPLOY_HOTKEYS). Escape stays a fixed,
# non-rebindable cancel/back key so a player can never lock themselves out.
KEYBINDS = [
    ("deploy",     "Deploy Ready Unit",  [int(Qt.Key.Key_Space)]),
    ("pan_left",   "Pan Camera Left",    [int(Qt.Key.Key_Left),  int(Qt.Key.Key_A)]),
    ("pan_right",  "Pan Camera Right",   [int(Qt.Key.Key_Right), int(Qt.Key.Key_D)]),
    ("aim_up",     "Raise Coastal Aim",  [int(Qt.Key.Key_Up)]),
    ("aim_down",   "Lower Coastal Aim",  [int(Qt.Key.Key_Down)]),
    ("restart",    "Restart Battle",     [int(Qt.Key.Key_R)]),
    ("main_menu",  "Quit To Main Menu",  [int(Qt.Key.Key_Q)]),
    ("fullscreen", "Toggle Full Screen", [int(Qt.Key.Key_F11)]),
]
_KEY_DEFAULTS = {a: list(keys) for a, _lbl, keys in KEYBINDS}

# The deploy hotkeys handed out in roster order – a ship with no custom binding
# gets the one at its deploy-order index (mirrors the classic 1..0, Q,E,T… run).
DEPLOY_HOTKEYS = list("1234567890") + list("QETYUIOPFGHJKL")

# Escape is reserved as the universal cancel/back key and can never be bound.
_RESERVED = int(Qt.Key.Key_Escape)
# QKeySequence maps unparseable text to Key_unknown – never store that.
_UNKNOWN = int(Qt.Key.Key_unknown)


def _valid_code(code: int) -> bool:
    return bool(code) and code != _RESERVED and code != _UNKNOWN


def key_display(keycode: int) -> str:
    """A short, human-readable name for a Qt key code ("Space", "Left", "F11")."""
    return QKeySequence(int(keycode)).toString() or "?"


def keys_display(codes, empty: str = "–") -> str:
    """Render a list of key codes as a comma-separated name string."""
    return ", ".join(key_display(c) for c in codes) if codes else empty


def parse_keys(text: str) -> list[int]:
    """Parse a comma-separated key string ("Left, A, Space") into de-duplicated Qt
    key codes. Unknown tokens and the reserved Escape are dropped; modifiers are
    stripped (so "Ctrl+A" binds A)."""
    out: list[int] = []
    for tok in str(text).split(","):
        tok = tok.strip()
        if not tok:
            continue
        seq = QKeySequence(tok)
        if seq.count() == 0:
            continue
        combo = seq[0]
        key = combo.key() if hasattr(combo, "key") else combo   # Qt6 → QKeyCombination
        code = int(key)
        if _valid_code(code) and code not in out:
            out.append(code)
    return out


def default_deploy_key(index: int) -> str:
    """The default deploy hotkey char for the unit at `index` in the roster."""
    return DEPLOY_HOTKEYS[index] if 0 <= index < len(DEPLOY_HOTKEYS) else ""


def _coerce_codes(v) -> list[int]:
    """Accept both the old single-int format and the new list format from disk."""
    if isinstance(v, int):
        v = [v]
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        try:
            c = int(x)
        except (TypeError, ValueError):
            continue
        if _valid_code(c) and c not in out:
            out.append(c)
    return out


class Settings:
    def __init__(self):
        # True = skip all particle effects: explosions, gun smoke, missile
        # exhaust and the menu's flagship gunfire.
        self.no_effects = False
        # True = at the dead of night, ring every hull with a faint moonlit rim
        # so black ships separate from the dark sea. Off leaves hulls unlit.
        self.night_silhouette = True
        # The commander name shown on your health bar in head-to-head battles.
        # Remembered so it need only be typed once.
        self.player_name = ""
        # action id -> list of Qt key codes. Starts as the fixed-control defaults;
        # per-ship "deploy:<unit>" actions are added on demand as they're rebound.
        self.keybinds = {a: list(v) for a, v in _KEY_DEFAULTS.items()}
        self.load()

    # ── Keybind helpers ────────────────────────────────────────────────────────
    def keys_for(self, action: str) -> list[int]:
        """Every Qt key code bound to `action` (the fixed-control default if a known
        action has never been touched; [] for an untouched per-ship action)."""
        if action in self.keybinds:
            return list(self.keybinds[action])
        return list(_KEY_DEFAULTS.get(action, []))

    def key_for(self, action: str) -> int:
        """The first key code bound to `action`, or 0 if nothing is bound."""
        ks = self.keys_for(action)
        return ks[0] if ks else 0

    def has_binding(self, action: str) -> bool:
        """True once `action` has been explicitly set (even to no keys)."""
        return action in self.keybinds

    def keys_display_for(self, action: str, empty: str = "–") -> str:
        """Comma-separated names of every key bound to `action`."""
        return keys_display(self.keys_for(action), empty)

    def set_keys(self, action: str, codes) -> None:
        """Bind `action` to `codes` (a list) and persist. Any of those keys already
        bound to another action is removed from it, so one physical key drives a
        single control."""
        codes = _coerce_codes(list(codes))
        for a, ks in list(self.keybinds.items()):
            if a != action:
                trimmed = [k for k in ks if k not in codes]
                if trimmed != ks:
                    self.keybinds[a] = trimmed
        self.keybinds[action] = codes
        self.save()

    def reset_keys(self) -> None:
        """Back to defaults – fixed controls reset and every per-ship override
        cleared (ships fall back to their deploy-order hotkey)."""
        self.keybinds = {a: list(v) for a, v in _KEY_DEFAULTS.items()}
        self.save()

    def load(self):
        try:
            with open(_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        self.no_effects = bool(d.get("no_effects", False))
        self.night_silhouette = bool(d.get("night_silhouette", True))
        self.player_name = str(d.get("player_name", ""))[:16]
        saved = d.get("keybinds", {})
        if isinstance(saved, dict):
            for a, v in saved.items():
                # accept the fixed controls and any per-ship "deploy:<unit>" action
                if a in _KEY_DEFAULTS or a.startswith("deploy:"):
                    self.keybinds[a] = _coerce_codes(v)

    def save(self):
        try:
            os.makedirs(SAVE_DIR, exist_ok=True)
            with open(_FILE, "w", encoding="utf-8") as f:
                json.dump({"no_effects": self.no_effects,
                           "night_silhouette": self.night_silhouette,
                           "player_name": self.player_name,
                           "keybinds": self.keybinds}, f, indent=2)
        except OSError:
            pass                        # a failed write only loses persistence


SETTINGS = Settings()
