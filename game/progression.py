"""
game.progression — which vehicles a profile has unlocked.

Vehicles unlock as the campaign is cleared: a unit at stage N becomes available
once the player has cleared N levels. Stage-0 units (and base turrets) are
available from the start. Turrets are not part of the unlock ladder.
"""

from __future__ import annotations

import json
from . import config

# Ordered roster shown in the catalog: (vehicle_key, unlock_stage, label).
# NOTE: the unlock_stage values below are DEFAULTS — the editable config file
# `unlocks.json` (config.UNLOCKS_FILE) overrides them, so designers can retune
# the unlock ladder without touching code. The label/order here still drive how
# the catalog is displayed.
ROSTER = [
    ("patrol",           0, "Patrol Boat"),
    ("frigate",          0, "Frigate"),
    ("submarine",        0, "Submarine"),
    ("helicopter",       0, "Helicopter"),
    ("aa_gun_ship",      1, "AA-Machinegun Boat"),
    ("hovercraft",       2, "Hovercraft"),
    ("torpedo_bomber",   2, "Torpedo Bomber"),
    ("anti_sub_heli",    3, "Anti-Submarine Helicopter"),
    ("fighter_jet",      4, "Fighter"),
    ("destroyer",        5, "Destroyer"),
    ("battleship",       6, "Battleship"),
    ("atomic_submarine", 7, "Atomic Submarine"),
    ("bomber",           8, "Bomber"),
    ("carrier",          9, "Aircraft Carrier"),
    ("cruiser",          1, "Cruiser"),
    ("mlrs",             6, "MLRS Boat"),
    ("coastal_artillery", 4, "Coastal Artillery"),
    ("minelayer",        2, "Minelayer"),
    ("oilrig",           5, "Bastion"),
]

# Base turrets and their unlock stage (coastal_artillery is gated via ROSTER above).
TURRET_STAGE = {"torpedo_battery": 0, "aa_turret": 2, "missile_battery": 4,
                "oil_rig": 5}


def _load_unlock_config() -> dict:
    """Read unlocks.json. Returns {'units':{}, 'turrets':{}} with whatever the
    file provides; missing/invalid → empty (code defaults stand)."""
    try:
        with open(config.UNLOCKS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    out = {}
    for section in ("units", "turrets"):
        sec = data.get(section, {})
        if isinstance(sec, dict):
            out[section] = {k: int(v) for k, v in sec.items()
                            if isinstance(v, (int, float))}
    return out


_UNLOCK_CFG = _load_unlock_config()

# Code defaults first, then let unlocks.json override per key.
UNLOCK = {key: stage for key, stage, _ in ROSTER}
UNLOCK.update(TURRET_STAGE)
UNLOCK.update(_UNLOCK_CFG.get("units", {}))
UNLOCK.update(_UNLOCK_CFG.get("turrets", {}))
# Keep TURRET_STAGE in sync with any override so turret gating matches.
for _k in TURRET_STAGE:
    TURRET_STAGE[_k] = UNLOCK.get(_k, TURRET_STAGE[_k])


# Short "how to use it" tips shown in the ship catalog.
TIPS = {
    "patrol":           "Cheap and fast — spam early to pressure the line and screen your capital ships.",
    "frigate":          "All-rounder. Its depth charges make it your first real answer to submarines.",
    "submarine":        "Submerged: only depth-charge units can touch it. Slip past and torpedo surface ships.",
    "helicopter":       "Flexible 360° gun hits ships AND aircraft — good glue when you're unsure what's coming.",
    "aa_gun_ship":      "Pure anti-air. Park it behind the line to swat enemy planes; useless against ships.",
    "hovercraft":       "Very fast raider that drops homing torpedoes straight down — they hunt both surface ships and submarines, so it's flexible glue against mixed threats.",
    "torpedo_bomber":   "Circle enemy ships and drop torpedoes near them — they only home once they hit the water.",
    "anti_sub_heli":    "Dedicated sub-hunter; scatters a salvo of depth charges over detected submarines.",
    "fighter_jet":      "Air-superiority interceptor. Mass it when the enemy floods the sky with aircraft.",
    "destroyer":        "Twin guns for ships plus flak that air-bursts on planes — a solid mid-game backbone.",
    "battleship":       "A tanky mid-range artillery platform: huge HP and three triple-gun turrets that lob a heavy shell salvo. Wade it into the front line and soak fire.",
    "atomic_submarine": "Heavy sub: torpedoes surface ships and fires AA missiles. Still needs depth charges to kill.",
    "bomber":           "Flies the whole map laying a carpet of bombs — devastating against clustered ships and bases.",
    "carrier":          "A mobile airbase that launches wave after wave of aircraft. Protect it and let the air wing work.",
    "cruiser":          "Homing missiles that hit ships AND aircraft, and it locks the newest threat first — a superb escort.",
    "mlrs":             "Lobs one wild rocket salvo over a wide area, then pulls back to base to rearm. Time its bursts.",
    "coastal_artillery":"A base battery you aim by hand: ↑/↓ set the elevation, and it auto-lobs heavy shells at that range.",
    "minelayer":        "Runs out ahead of the line dropping a trail of naval mines, then turns about and steams off the map. Mines blow up ships and submarines that wander into them.",
    "oilrig":           "Deploy it on your base pier, seaward of the main fort: a very tanky, self-repairing bulwark that soaks enemy fire to shield your base. It makes no income, but its deck mounts TWO surface turrets. Defend it.",
    "oil_rig":          "Place it on any empty base turret node to boost your income while it stands. It has no weapons and the enemy will target it, so screen it with real turrets.",
}


def tip(key: str) -> str:
    return TIPS.get(key, "")

def stage(key: str) -> int:
    return UNLOCK.get(key, 0)

def is_unlocked(key: str, cleared: int) -> bool:
    return UNLOCK.get(key, 0) <= cleared

def unlocked_keys(cleared: int) -> set[str]:
    return {k for k, s in UNLOCK.items() if s <= cleared}
