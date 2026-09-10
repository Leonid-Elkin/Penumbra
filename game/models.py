"""
game.models – data definitions for vehicles, weapons and levels.

These dataclasses are the in-memory shape of the JSON files under vehicles/ and
levels/. Each type has a `from_dict()` that builds it from parsed JSON, applying
defaults for omitted keys so config files only need to specify what differs.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal

AttackType = Literal[
    "bullet", "shell", "missile", "torpedo",
    "depth_charge", "aa_missile", "bomb",
    "flak", "shrapnel", "mine", "icbm", "rocket",
]


class ConfigError(ValueError):
    """Raised when a JSON config is missing required fields or malformed."""


# ─── Fire point ─────────────────────────────────────────────────────────────
@dataclass
class FirePoint:
    """Where a shot leaves the hull, in sprite-relative coords (0..1) + a launch
    direction (dx, dy). One fire point == one 'cannon' muzzle."""
    rel_x: float; rel_y: float; dir_x: float; dir_y: float

    @classmethod
    def from_dict(cls, d: dict) -> "FirePoint":
        return cls(float(d["x"]), float(d["y"]),
                   float(d.get("dx", 0.0)), float(d.get("dy", 0.0)))


# ─── Attack ─────────────────────────────────────────────────────────────────
@dataclass
class Attack:
    name:               str
    attack_type:        AttackType
    fire_point_indices: list[int]
    damage:             int
    speed:              float
    fire_interval:      float
    combat_range:       float

    proj_w:          int   = 4
    proj_h:          int   = 2
    homing:          bool  = False
    homing_delay:    float = 0.0
    max_lifetime:    float = 2.5
    can_hit_surface: bool  = True
    can_hit_sub:     bool  = False
    can_hit_plane:   bool  = False
    omnidirectional: bool  = False
    track_target:    bool  = False
    # Launch inaccuracy: fan each shot off its aimed heading by a random angle in
    # [-spread_deg, +spread_deg] degrees. 0 = pin-point. Used to give rapid-fire
    # tracking guns (AA machine guns) a slight, natural scatter around the target.
    spread_deg:      float = 0.0
    # Scatter: spawn this many projectiles per shot, spread across the target area
    scatter_count:   int   = 1
    scatter_spread:  float = 0.0
    # Ballistic: solve a gravity arc to lob the projectile onto the target
    ballistic:       bool  = False
    # Flak: air-burst fuse (s) + shrapnel released on burst
    fuse:            float = 0.0
    frag_count:      int   = 0
    frag_damage:     int   = 0
    # Targeting + special flight
    target_priority: str   = "near"   # near | last (newest / highest id)
    piercing:        bool  = False    # passes through targets, damaging each
    gun_angle:       float = 0.0      # fixed elevation (rad); muzzle SPEED varies to range
    gravity:         float = 0.0      # px/s² for arcing rounds; 0 = per-type default
    target_tags:     list[str] = field(default_factory=list)
    # VLS "Tor"-style launch: cold-ejected straight up, coasts, then tips over
    # ~90° toward the target and boosts to full `speed`. vls_pop_time is the
    # eject/coast phase (s); vls_pop_speed the muzzle eject speed (px/s).
    vls:             bool  = False
    vls_pop_time:    float = 0.35
    vls_pop_speed:   float = 130.0
    # Impact effect: spawn an explosion flash + spark burst where the round lands.
    hit_effect:      bool  = False
    # Exhaust plume: trail the grey motor-smoke puff (the VLS "aegis" plume) from
    # the tail every frame of powered flight – for a plain rocket-motor missile
    # that has no boost phase, unlike the VLS round which only smokes while boosting.
    exhaust_smoke:   bool  = False

    @classmethod
    def from_dict(cls, d: dict) -> "Attack":
        try:
            return cls(
                name=d["name"], attack_type=d["type"],
                fire_point_indices=list(d.get("fire_points", [-1])),
                damage=int(d.get("damage", 0)),
                speed=float(d.get("speed", 0)),
                fire_interval=float(d.get("interval", 1.0)),
                combat_range=float(d.get("range", 0)),
                proj_w=int(d.get("proj_w", 4)), proj_h=int(d.get("proj_h", 2)),
                homing=bool(d.get("homing", False)),
                homing_delay=float(d.get("homing_delay", 0.0)),
                max_lifetime=float(d.get("max_lifetime", 2.5)),
                can_hit_surface=bool(d.get("can_hit_surface", True)),
                can_hit_sub=bool(d.get("can_hit_sub", False)),
                can_hit_plane=bool(d.get("can_hit_plane", False)),
                omnidirectional=bool(d.get("omnidirectional", False)),
                track_target=bool(d.get("track_target", False)),
                spread_deg=float(d.get("spread_deg", 0.0)),
                scatter_count=int(d.get("scatter_count", 1)),
                scatter_spread=float(d.get("scatter_spread", 0.0)),
                ballistic=bool(d.get("ballistic", False)),
                fuse=float(d.get("fuse", 0.0)),
                frag_count=int(d.get("frag_count", 0)),
                frag_damage=int(d.get("frag_damage", 0)),
                target_priority=d.get("target_priority", "near"),
                piercing=bool(d.get("piercing", False)),
                gun_angle=float(d.get("gun_angle", 0.0)),
                gravity=float(d.get("gravity", 0.0)),
                target_tags=list(d.get("target_tags", [])),
                vls=bool(d.get("vls", False)),
                vls_pop_time=float(d.get("vls_pop_time", 0.35)),
                vls_pop_speed=float(d.get("vls_pop_speed", 130.0)),
                hit_effect=bool(d.get("hit_effect", False)),
                exhaust_smoke=bool(d.get("exhaust_smoke", False)),
            )
        except KeyError as e:
            raise ConfigError(f"attack missing required field {e}") from None


# ─── Ship / vehicle definition ──────────────────────────────────────────────
@dataclass
class ShipDef:
    key:         str;  name:        str
    cost:        int;  hp:          int;   speed: float
    description: str

    sprite_file:  str
    sprite_crop:  Optional[tuple[int, int, int, int]] = None
    flip_player:  bool = True;  flip_enemy:  bool = False
    display_w:    int  = 120;   display_h:   int  = 0
    player_tint:  str  = "#0a0a0a"; enemy_tint: str = "#cc1111"
    remove_white: bool = False

    # Optional elevating barrel drawn as a separate sprite pivoting on the base
    # (AA gun, coastal artillery). Authored at the SAME pixel scale as the base
    # sprite so the engine seats it with a single display_w/base_width factor.
    #   barrel_pivot  – pivot point on the BASE sprite (0..1 fractions)
    #   barrel_anchor – the point on the BARREL sprite that sits on that pivot
    #   barrel_rest   – resting elevation (rad) when the gun has no target
    barrel_file:   Optional[str]   = None
    barrel_pivot:  tuple           = (0.5, 0.5)
    barrel_anchor: tuple           = (0.2, 0.5)
    barrel_rest:   float           = 0.6
    barrel_disp:   object = field(default=None, compare=False, repr=False)  # (w,h), set at load

    unit_type:     str = "ship"        # ship | submarine | plane | turret
    turret_slot:   str = "surface"     # surface | underwater (turret placement)
    aircraft_type: str = "loop"        # loop | hover | carpet (plane movement)
    tags:          list[str] = field(default_factory=list)
    spawn_unit_keys: list[str] = field(default_factory=list)
    spawn_interval:  float     = 0.0

    # Oil-platform capture pull: how many "ships" this hull counts as when it sits
    # in a rig's capture lane (see game.entities.capturepoint). 1.0 is a normal
    # hull; raise it to let a specialist seize faster (the Hovercraft's raid role).
    capture_weight:  float     = 1.0
    # Passive economy: gold/sec added to the owner's income while this unit stands
    # (0 for everything but the Oil Rig income turret).
    income:          int       = 0

    # Construction time (seconds) for structures raised on deploy – the Bastion's
    # platform rises over this long, fragile while it goes up.
    build_time:      float     = 6.0

    # Production cooldown: minimum seconds between successive orders of this unit
    # type (per team). Discourages spamming one unit – heavier hulls cool longer.
    cooldown:   float = 0.0

    # Special behaviours
    rearms:     bool  = False           # fire one salvo, then retreat to rearm
    rearm_time: float = 6.0
    manual_aim: bool  = False           # player aims elevation with ↑/↓
    advance_while_firing: bool = False  # keep creeping forward even while in combat
    burst_count:    int   = 1           # fire this many rounds one-by-one per salvo
    burst_interval: float = 0.12        # seconds between rounds in the series
    retreat_turn:   bool  = False       # when rearming/retreating, turn about and
                                        # drive forward (sprite flips) instead of
                                        # backing up in reverse
    minelayer:      bool  = False       # advance dropping mines, then run off-screen

    # Boss-only extras (harmless defaults for normal vehicles)
    is_boss:   bool = False
    boss_type: str  = ""               # air | carrier | sub | surface
    # Standoff: a boss halts its advance once a ground/surface unit is within this
    # distance, holding back to bombard instead of driving onto it (0 = disabled).
    standoff_range: float = 0.0
    # Surface boss only: fraction of the sprite HEIGHT that sits BELOW the waterline
    # (0 = floats on top, 1 = fully submerged). The default keeps just the bottom of
    # the hull dipping under; raise it to seat a boss lower in the water.
    submerged_frac: float = 0.05
    # Per-difficulty HP overrides (bosses). When a bucket is > 0 it is used
    # verbatim for that difficulty instead of scaling `hp` by the difficulty
    # multiplier – so a boss reads its exact wiki health on Easy/Normal/Hard.
    hp_easy:   int = 0
    hp_normal: int = 0
    hp_hard:   int = 0

    # Collision hitbox. `hitbox` is an optional JSON override [x0, y0, x1, y1] in
    # sprite fractions (0..1) forcing a plain rectangle; when omitted the engine
    # derives a hull/superstructure silhouette from the sprite at load time.
    # `hitmask` is that built silhouette (a game.hitmask.HitMask), attached by the
    # sprite loader – never read from JSON.
    hitbox:  Optional[list] = None
    hitmask: object = field(default=None, compare=False, repr=False)

    fire_points: list[FirePoint] = field(default_factory=list)
    attacks:     list[Attack]    = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict, *, source: str = "<dict>") -> "ShipDef":
        req = ("key", "name", "cost", "hp", "speed", "sprite")
        miss = [k for k in req if k not in d]
        if miss:
            raise ConfigError(f"{source}: missing required field(s) {miss}")
        crop = d.get("sprite_crop")
        try:
            return cls(
                key=d["key"], name=d["name"],
                cost=int(d["cost"]), hp=int(d["hp"]), speed=float(d["speed"]),
                description=d.get("description", ""),
                sprite_file=d["sprite"],
                sprite_crop=tuple(crop) if crop else None,
                flip_player=bool(d.get("flip_player", True)),
                flip_enemy=bool(d.get("flip_enemy", False)),
                display_w=int(d.get("display_w", 120)),
                display_h=int(d.get("display_h", 0)),
                player_tint=d.get("player_tint", "#0a0a0a"),
                enemy_tint=d.get("enemy_tint", "#cc1111"),
                remove_white=bool(d.get("remove_white", False)),
                barrel_file=d.get("barrel"),
                barrel_pivot=tuple(d.get("barrel_pivot", (0.5, 0.5))),
                barrel_anchor=tuple(d.get("barrel_anchor", (0.2, 0.5))),
                barrel_rest=float(d.get("barrel_rest", 0.6)),
                unit_type=d.get("unit_type", "ship"),
                turret_slot=d.get("turret_slot", "surface"),
                aircraft_type=d.get("aircraft_type", "loop"),
                tags=list(d.get("tags", [])),
                spawn_unit_keys=list(d.get("spawn_units", [])),
                spawn_interval=float(d.get("spawn_interval", 0.0)),
                capture_weight=float(d.get("capture_weight", 1.0)),
                income=int(d.get("income", 0)),
                build_time=float(d.get("build_time", 6.0)),
                cooldown=float(d.get("cooldown", 0.0)),
                rearms=bool(d.get("rearms", False)),
                rearm_time=float(d.get("rearm_time", 6.0)),
                manual_aim=bool(d.get("manual_aim", False)),
                advance_while_firing=bool(d.get("advance_while_firing", False)),
                burst_count=int(d.get("burst_count", 1)),
                burst_interval=float(d.get("burst_interval", 0.12)),
                retreat_turn=bool(d.get("retreat_turn", False)),
                minelayer=bool(d.get("minelayer", False)),
                is_boss=bool(d.get("boss", False)),
                boss_type=d.get("boss_type", ""),
                standoff_range=float(d.get("standoff_range", 0.0)),
                submerged_frac=float(d.get("submerged_frac", 0.05)),
                hp_easy=int(d.get("hp_easy", 0)),
                hp_normal=int(d.get("hp_normal", 0)),
                hp_hard=int(d.get("hp_hard", 0)),
                hitbox=list(d["hitbox"]) if d.get("hitbox") else None,
                fire_points=[FirePoint.from_dict(f) for f in d.get("fire_points", [])],
                attacks=[Attack.from_dict(a) for a in d.get("attacks", [])],
            )
        except (TypeError, ValueError) as e:
            raise ConfigError(f"{source}: {e}") from None


# ─── Level definition ───────────────────────────────────────────────────────
@dataclass
class LevelDef:
    key:         str
    name:        str
    boss:        str                       # boss key that defends the enemy base
    environment: str  = "sea"              # air | sea | deep (theme label)
    description: str  = ""
    sky_tint:    Optional[str] = None      # overrides for the backdrop gradient
    water_tint:  Optional[str] = None
    # Named backdrop skin override. Normally the backdrop is picked from the
    # campaign `order` as a time-of-day band (see game.ui.backdrop); a level may
    # instead name a special skin here – e.g. "abyss" for the ominous finale.
    backdrop:    Optional[str] = None
    order:       int  = 100                # sort order in the level-select grid
    # Per-level encounter config (overridable in the level JSON):
    boss_trigger: float = 0.25             # base-HP fraction at which the boss spawns
    # Caps how high the base-upgrade tracks may be researched in THIS level (both
    # sides). None = no cap, the engine max (config.MAX_LVL) stands. Early levels
    # can set this below the max to lock off the top upgrade tier. See battle.
    max_upgrade_level: Optional[int] = None
    counter_thresholds: list = field(default_factory=lambda: [0.85, 0.65, 0.45])
    # Per-level spawn-rate pulse overrides for counterattacks/boss; any field not
    # given falls back to the global defaults in waves.json. Shape:
    #   {"counterattack": {peak, rise_seconds, fall_seconds}, "boss": {...}}
    waves: dict = field(default_factory=dict)
    # Per-level base enemy spawn cadence (units/sec the director sustains). Shape:
    #   {"base": 0.14, "ramp": 0.26, "ramp_seconds": 180}
    #   base        – opening spawn rate
    #   ramp        – extra rate added as the battle drags on
    #   ramp_seconds– seconds over which `ramp` is fully reached
    # Any field omitted falls back to the engine defaults (see ai.EnemyDirector).
    spawn_rate: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict, *, source: str = "<dict>") -> "LevelDef":
        for k in ("key", "name", "boss"):
            if k not in d:
                raise ConfigError(f"{source}: level missing required field '{k}'")
        cthr = d.get("counter_thresholds", [0.85, 0.65, 0.45])
        return cls(
            key=d["key"], name=d["name"], boss=d["boss"],
            environment=d.get("environment", "sea"),
            description=d.get("description", ""),
            sky_tint=d.get("sky_tint"), water_tint=d.get("water_tint"),
            backdrop=d.get("backdrop"),
            order=int(d.get("order", 100)),
            boss_trigger=float(d.get("boss_trigger", 0.25)),
            max_upgrade_level=(int(d["max_upgrade_level"])
                               if d.get("max_upgrade_level") is not None else None),
            counter_thresholds=[float(x) for x in cthr],
            waves=dict(d.get("waves", {})),
            spawn_rate=dict(d.get("spawn_rate", {})),
        )
