"""
game.config – tunable constants, upgrade tables and asset paths.

Everything here is data the rest of the engine reads but never owns. Edit values
here (or the per-vehicle JSON under vehicles/) rather than hunting through code.
"""

from __future__ import annotations
import os, sys, json

# ─── Debug ──────────────────────────────────────────────────────────────────
DEBUG_INFINITE_MONEY = False      # True = player resources never run out (dev only)

# ─── World ──────────────────────────────────────────────────────────────────
WORLD_W      = 4000              # a tighter battle line (was 5000)
MAX_LVL      = 5

# Fortress sprite geometry (source atlas + on-screen draw size)
FORT_HALF    = 329; FORT_SRC_H   = 364
# FORT_D_W is the base "depth" (how far the fort reaches out from the shore edge):
# it sets the base hit-box width, the assault setback and the turret mounts. Wide
# bases with a stepped staircase and an open strip of water behind them.
FORT_D_W     = 430; FORT_D_H_MAX = 310

# All aircraft cruise with their centre this many px above the waterline, so
# every air unit flies on the same level regardless of sprite size.
PLANE_ALT    = 220.0

# Rear-guard reach: a turret seated further back from its own base's waterline
# front line shoots proportionally further – every pixel of setback behind that
# line adds this many pixels of combat range. Placing a battery high up the
# staircase (far behind) trades forward coverage for extra reach.
TURRET_REAR_RANGE_PER_PX = 1.0

# Boss trigger: when a base falls to this fraction of max HP its boss spawns and
# the base is immune until the boss is defeated.
BOSS_HP_TRIGGER = 0.25

# ─── Central oil-platform score → reward-boss mechanic ──────────────────────
# A single capturable oil platform stands dead-centre between the two forts.
# While a side HOLDS it, that side accrues SCORE_PER_SEC of score. Every
# SCORE_BOSS_INTERVAL seconds the side LEADING on score is AWARDED a flagship
# boss (escalating weakest→strongest) that fights on its OWN team and marches on
# the OPPONENT'S base, and both scores reset to zero. So seizing the platform
# earns you score and calls a flagship down on the enemy. (Active in multiplayer
# + sandbox, not the single-player campaign.)
SCORE_BOSS_INTERVAL = 90.0     # seconds between reward-boss spawns (1.5 min)
SCORE_BOSS_FIRST_PVP = 180.0   # seconds before the FIRST flagship in PvP (3 min)
SCORE_PER_SEC       = 1.0      # score/sec earned by the platform-holder
# Aggression bounty: in multiplayer/sandbox the side that HOLDS the central
# platform is ALSO paid this much gold/sec directly (on top of the score→boss
# loop). Seizing the midfield funds a capital ship, so fighting for the platform
# pays off immediately – the incentive to push rather than turtle.
SCORE_HOLD_INCOME   = 20.0     # gold/sec paid to the platform-holder

# ─── Scoring (stars) ────────────────────────────────────────────────────────
# A level's score rewards a fast clear and a healthy base. Tune freely.
TIME_3STAR = 120.0     # clear at or under this (s) → full speed bonus
TIME_PAR   = 360.0     # clear at or over this → no speed bonus

def stair_frac(lv: int) -> float:
    """Fraction of full fortress height shown at armour level `lv`."""
    return (5 + lv) / 10.0

# ─── Upgrade tables (index = upgrade level) ─────────────────────────────────
# Money still has to be earned, but it flows enough to actually use a unit when
# its cooldown is up (per-unit cooldowns, not poverty, are the anti-spam lever).
# Both sides ramp from cheap hulls to capital ships by investing in the economy.
INCOME_T  = [7,   18,  34,  58,  92,  140]     # resources/sec by Income level
                                               # (each Income upgrade is a bigger jump)
# Fleet: the max number of live mobile units the player may field, by Fleet level.
# It ramps from a small opening flotilla up to the hard cap (see MAX_UNITS below);
# the top level equals that cap, so a fully-upgraded fleet matches the old ceiling.
FLEET_T   = [40,  55,  70,  82,  92,  100]
STORAGE_T = [400, 800, 1500,2400,3600,5000]    # bank cap (top tier banks a Carrier)
# Bases are big, durable objectives (10× the old values) so a battle is a
# sustained assault rather than a quick spam-out.
BASEHP_T  = [5000, 7000, 10000, 14000, 19000, 25000]
# Salvage: fraction of a lost mobile unit's cost refunded to its owner when it is
# destroyed (index = Salvage level). Level 0 = no refund; the top tier reclaims
# nearly half of every wreck, softening attritional losses without touching the
# hulls themselves. Read in battle._process_deaths.
SALVAGE_T = [0.0, 0.10, 0.18, 0.26, 0.34, 0.45]

# Factory (the "warehouse" upgrade) also scales every unit's production cooldown:
# at MAX level the cooldown is the unit's listed value (×1.0); lower levels cool
# proportionally slower. Index = Factory level.
FACTORY_CD_MULT = [2.6, 2.1, 1.7, 1.4, 1.2, 1.0]

# Upgrades are not instant – once bought they take this many seconds to come
# online (index = the level being researched, 0→1 … 4→5). One per side at a time.
UPGRADE_TIME = [6.0, 9.0, 12.0, 16.0, 21.0]

# Minimum gap (s) between units actually entering the field, per side. Spawns are
# buffered and released one at a time, so a big wave (or clicking every unit at
# once) staggers out over time instead of stacking on the same spot.
SPAWN_STAGGER = 0.16

# ─── Surge Rush (Ctrl-to-surge player ability) ──────────────────────────────
# There is no button: holding CTRL engages "surge". While Ctrl is down every
# unit's price in the deploy bar visibly climbs to SURGE_COST_MULT×, and any hull
# you deploy while surging then rebuilds in 1/SURGE_CD_MULT of its normal
# production time (a 3× shorter cooldown) – a pay-more, rebuild-sooner rush.
# Unlocks at SURGE_UNLOCK_LEVEL (1-based campaign level). All three values are
# overlaid from surge.json when present (see load_surge_config), so designers can
# retune the ability without touching code – same idea as capture_points.json.
#   cost_mult    – price multiplier while Ctrl is held (1.5 = +50%)
#   cd_mult      – cooldown DIVISOR on a surged hull (3 = one-third the normal wait)
#   unlock_level – 1-based campaign level from which the ability becomes available
SURGE_DEFAULTS = {
    "cost_mult":    1.5,
    "cd_mult":      3.0,
    "unlock_level": 5,
}
# (SURGE_FILE path + the SURGE_COST_MULT / SURGE_CD_MULT / SURGE_UNLOCK_LEVEL
#  constants are resolved after _HERE is defined – see load_surge_config below.)

# Hard cap on live mobile units per side (bases/rigs/turrets excluded). This is
# the ceiling the Fleet upgrade climbs toward; FLEET_T is clamped to it per side,
# so the enemy's smaller cap still bounds a maxed-out mirror of the player.
MAX_UNITS = {"player": 100, "enemy": 80}

UPGRADE_COSTS = {
    "resource":  [50,  120, 260, 500, 1000],
    "fleet":     [75,  150, 320, 650, 1300],
    "storage":   [60,  130, 280, 560, 1100],
    "warehouse": [100, 220, 450, 900, 1800],
    "health":    [150, 320, 650,1300, 2600],
    "salvage":   [130, 280, 560,1120, 2200],
}

# ─── Asset paths ────────────────────────────────────────────────────────────
# When packaged by PyInstaller the code + bundled data live in a read-only
# extraction dir (sys._MEIPASS); __file__ resolves inside it, so _HERE finds the
# bundled JSON / textures exactly as it does from source – no path change needed.
_HERE        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FROZEN      = getattr(sys, "frozen", False)
TEXTURE_DIR  = os.path.join(_HERE, "Textures")
BOSS_DIR     = os.path.join(TEXTURE_DIR, "Bosses")
VEHICLE_DIR  = os.path.join(_HERE, "vehicles")
BOSS_CFG_DIR = os.path.join(VEHICLE_DIR, "bosses")
LEVEL_DIR    = os.path.join(_HERE, "levels")
# Saves + settings must go somewhere writable AND persistent. In a frozen one-file
# build the bundle dir is a temp folder wiped on exit, so redirect writes to the
# per-user profile; from source they stay next to the project as before.
if _FROZEN:
    _APPROOT = os.environ.get("APPDATA") or os.path.expanduser("~")
    SAVE_DIR = os.path.join(_APPROOT, "Penumbra", "saves")
else:
    SAVE_DIR = os.path.join(_HERE, "saves")
# Editable config controlling at which cleared-level count each unit/powerup
# unlocks (see game/progression.py). Missing entries fall back to code defaults.
UNLOCKS_FILE = os.path.join(_HERE, "unlocks.json")
# Editable config for counterattack / boss spawn-rate pulses (game/ai.py).
WAVES_FILE   = os.path.join(_HERE, "waves.json")
# Editable config for the midfield capturable oil platforms (game/entities/capturepoint.py).
CAPTURE_POINTS_FILE = os.path.join(_HERE, "capture_points.json")
# Editable tuning for the six base-upgrade tracks (values + costs per level).
BASE_UPGRADES_FILE = os.path.join(_HERE, "base_upgrades.json")
# Editable tuning for the Ctrl-to-surge rush (game/battle.py) – see SURGE_DEFAULTS.
SURGE_FILE = os.path.join(_HERE, "surge.json")
FORTRESS_FILE = "364_FortressGraphicsAll.png"

# ─── Capturable oil platforms (capture_points.json) ─────────────────────────
# Defaults for the midfield capture mechanic; capture_points.json (if present
# and well-formed) overlays them so designers can retune income, capture times
# and positions without touching code – same idea as waves.json.
CAPTURE_DEFAULTS = {
    "enabled_from_level":        4,        # 1-based campaign level the rigs appear on
    "count":                     3,
    "positions":                 [0.28, 0.5, 0.72],
    "income_per_rig":            20.0,     # gold/sec for a side's FIRST held rig
    "capture_seconds":           9.0,      # seconds for one ship to seize a neutral rig
    "capture_radius":            320.0,    # world px (along the battle line)
    "ship_speedup":              0.35,     # + this per extra ship near the rig
    "max_ship_speedup":          3.0,      # cap on the ship-count multiplier
    "decay_seconds":             0.0,      # >0: an unattended rig bleeds back to neutral
    # ── Home-field advantage ─────────────────────────────────────────────────
    # A rig on a side's OWN half of the map is captured/held this many times faster
    # by that side; the invader crossing over gets the reciprocal (so 2.0 => the
    # home team seizes at 2x and the invader at 0.5x). This lets you hold your own
    # rig even when out-numbered, and makes the CENTRE rig the real battleground.
    # 1.0 disables home-field entirely.
    "home_advantage":            2.0,
    # ── Anti-snowball levers ─────────────────────────────────────────────────
    # Each ADDITIONAL rig a side holds is worth this fraction of the previous one
    # for INCOME. < 1 makes hoarding all three give little marginal value, so the
    # trailing side's first recapture is the most valuable rig on the map. 1.0
    # restores flat/linear income.
    "holdings_falloff":          0.55,
    # Comeback: you capture faster while holding FEWER rigs than the opponent –
    # + this per rig you're behind, capped by `max_comeback`. Lets a team that has
    # lost the midfield claw a rig back instead of being locked out.
    "comeback_per_rig":          0.6,
    "max_comeback":              2.5,
}


def load_capture_config() -> dict:
    """Merge capture_points.json over CAPTURE_DEFAULTS. Missing file, bad JSON,
    unknown keys and wrong-typed values are ignored so a typo can never crash a
    battle – the offending field just keeps its default."""
    cfg = dict(CAPTURE_DEFAULTS)
    try:
        with open(CAPTURE_POINTS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return cfg
    if not isinstance(data, dict):
        return cfg
    for key, default in CAPTURE_DEFAULTS.items():
        if key not in data or data[key] is None:
            continue
        val = data[key]
        if key == "positions":
            if (isinstance(val, list) and val
                    and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in val)):
                cfg[key] = [float(v) for v in val]
        elif key in ("enabled_from_level", "count"):
            if isinstance(val, int) and not isinstance(val, bool):
                cfg[key] = val
        else:
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                cfg[key] = float(val)
    return cfg


def load_surge_config() -> dict:
    """Merge surge.json over SURGE_DEFAULTS. Missing file, bad JSON, unknown keys
    and wrong-typed values are ignored so a typo can never crash a battle – the
    offending field just keeps its default."""
    cfg = dict(SURGE_DEFAULTS)
    try:
        with open(SURGE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return cfg
    if not isinstance(data, dict):
        return cfg
    for key in SURGE_DEFAULTS:
        val = data.get(key)
        if val is None:
            continue
        if key == "unlock_level":
            if isinstance(val, int) and not isinstance(val, bool):
                cfg[key] = val
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            cfg[key] = float(val)
    return cfg


_SURGE_CFG = load_surge_config()
SURGE_COST_MULT    = _SURGE_CFG["cost_mult"]     # price ×  while Ctrl is held
SURGE_CD_MULT      = _SURGE_CFG["cd_mult"]       # cooldown ÷ on a surged deploy
SURGE_UNLOCK_LEVEL = _SURGE_CFG["unlock_level"]  # 1-based campaign unlock level

# ─── Bulk deploy (Alt-to-bulk player ability) ───────────────────────────────
# There is no button: holding ALT engages "bulk". While Alt is down every unit's
# price in the deploy bar drops to BULK_COST_MULT× (a volume discount), and one
# deploy press then buys a batch of BULK_SIZE hulls at that discounted price –
# the first fills the ready slot, the rest stack into the queue. A buy-more,
# pay-less counterpart to Surge Rush; always available (no campaign unlock).
BULK_SIZE      = 5      # hulls bought per press while Alt is held
BULK_COST_MULT = 0.8    # per-hull price multiplier while Alt is held (0.8 = −20%)

# ─── Surge + Bulk together (Ctrl AND Alt held) ──────────────────────────────
# Holding BOTH engages a surged batch: the surge +50% still stands, but the
# volume discount shrinks from the solo 20% to just 10% off – the deal is worse
# when you also want the rush. The net per-hull multiplier is therefore
# SURGE_COST_MULT × SURGE_BULK_DISCOUNT (1.5 × 0.9 = 1.35), and the batch keeps
# both the surge's shortened cooldown and bulk's BULK_SIZE count.
SURGE_BULK_DISCOUNT = 0.9   # 10% off, applied ON TOP of the surge multiplier


def deploy_unit_cost(base_cost: int, surge: bool, bulk: bool) -> int:
    """The per-hull price for a deploy under the live surge/bulk modifiers.

    This is the SINGLE source of truth shared by the battle sim (what it charges)
    and the HUD (what it shows), so the displayed price is always exactly what the
    player pays – no more mismatch between an elevated label and a base-price buy.

        surge only  → SURGE_COST_MULT×               (Ctrl:  +50%)
        bulk  only  → BULK_COST_MULT×                (Alt:   −20%, ×BULK_SIZE)
        both        → SURGE_COST_MULT×SURGE_BULK_DISCOUNT  (+50% then −10% = 1.35×, ×BULK_SIZE)
        neither     → base
    """
    if surge and bulk:
        mult = SURGE_COST_MULT * SURGE_BULK_DISCOUNT
    elif surge:
        mult = SURGE_COST_MULT
    elif bulk:
        mult = BULK_COST_MULT
    else:
        mult = 1.0
    return int(round(base_cost * mult))


def find_texture(name: str) -> str:
    """Resolve a texture filename to a path, searching Textures/ then Bosses/."""
    for base in (TEXTURE_DIR, BOSS_DIR, _HERE,
                 "/mnt/user-data/uploads"):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return os.path.join(TEXTURE_DIR, name)


# ─── Editable upgrade tuning (base_upgrades.json) ───────────────────────────
# The upgrade tables above are DEFAULTS. base_upgrades.json (if present and
# well-formed) overlays them so designers can retune values/costs without
# touching code – same idea as unlocks.json / waves.json. Tables are mutated in
# PLACE so modules that did `from .config import INCOME_T` see the overrides.

# Maps a base_upgrades.json track key → the value table it drives. The key is
# also the upgrade's id in UPGRADE_COSTS / Faction.upgrades.
_UPG_VALUE_TABLES = {
    "resource":  INCOME_T,
    "fleet":     FLEET_T,
    "storage":   STORAGE_T,
    "warehouse": FACTORY_CD_MULT,
    "health":    BASEHP_T,
    "salvage":   SALVAGE_T,
}


def _apply_base_upgrade_config() -> None:
    """Overlay base_upgrades.json onto the upgrade tables (in place). Missing
    files, bad JSON, unknown tracks and wrong-length lists are ignored so a
    typo can never shorten a table and cause an index error mid-battle."""
    try:
        with open(BASE_UPGRADES_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return
    if not isinstance(data, dict):
        return

    def _nums(seq, n):
        """Return a length-n list of numbers, or None if seq isn't exactly that."""
        if not isinstance(seq, list) or len(seq) != n:
            return None
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in seq):
            return None
        return list(seq)

    n_levels = MAX_LVL + 1                                # values span levels 0..MAX_LVL
    for key, table in _UPG_VALUE_TABLES.items():
        track = data.get(key)
        if not isinstance(track, dict):
            continue
        values = _nums(track.get("values"), n_levels)
        if values is not None:
            table[:] = values                            # in-place: keep shared reference
        costs = _nums(track.get("costs"), MAX_LVL)       # 5 purchases: level 0->1 .. 4->5
        if costs is not None and key in UPGRADE_COSTS:
            UPGRADE_COSTS[key][:] = costs

    rtime = _nums(data.get("research_time"), MAX_LVL)
    if rtime is not None:
        UPGRADE_TIME[:] = rtime


_apply_base_upgrade_config()
