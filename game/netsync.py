"""
game.netsync – world-state serialization for the LAN battle.

The HOST runs the authoritative simulation and, a few dozen times a second,
packs the on-screen world into a compact JSON snapshot (serialize). The CLIENT
does not simulate at all: it applies each snapshot (apply) by reconstructing
lightweight render entities into the very lists BattleCanvas.paintGL already
draws from (ships / projectiles / effects / capture points) plus the two
factions' economy. So the client renders through the unchanged paint path.

Only what the client must SEE or COMMAND is sent:
  · every ship/boss/bastion – position, hp, aim, a few animation flags
  · projectiles – position, velocity, type + calibre + age/fade, so the client
    rebuilds a real Projectile and draws it through the SAME per-type ordnance
    art the host uses (identical shape, team colour and fade on both screens)
  · fresh effects this frame (explosions, muzzle smoke, launch/exhaust plumes) –
    spawned as real, self-animating effects so both players see the same bursts
  · both factions' economy, the enemy fort's free slots (for P2's placement),
    enemy production cooldowns / research, capture-point ownership, game state.
"""

from __future__ import annotations

from .entities import (Ship, Boss, OilRig, Explosion, Projectile,
                       MuzzleSmoke, LaunchSmoke, Splash)
from .models import Attack
from .config import WORLD_W


# ── The client renders the shared world from its OWN point of view ────────────
# The host simulates with itself as the "player" faction (left, black/amber) and
# the joiner as the "enemy" (right, red). If the client drew those snapshots
# verbatim it would see ITSELF on the right in red. So every snapshot is applied
# through a horizontal mirror – world x → WORLD_W - x, horizontal motion negated –
# together with a player↔enemy team swap, so the client's own side lands on the
# LEFT in player colours and the opponent on the RIGHT in enemy red. The rest of
# the paint / HUD path is then identical to the host's (see BattleCanvas.my_team).
_SWAP = {"player": "enemy", "enemy": "player"}


def _swap(team: str) -> str:
    return _SWAP.get(team, team)


# ── Host: assign a stable network id to an entity the first time it's sent ────
def _nid(canvas, ent) -> int:
    n = getattr(ent, "_nid", None)
    if n is None:
        canvas._nid_seq += 1
        n = canvas._nid_seq
        ent._nid = n
    return n


def _fac_dict(fac) -> dict:
    return {
        "hp":  round(fac.base_hp, 1),
        "mhp": round(fac.max_base_hp, 1),
        "res": round(fac.resources, 1),
        "upg": dict(fac.upgrades),
        "bi":  fac.bonus_income,
    }


def _ship_kind(s) -> str:
    if isinstance(s, Boss) or getattr(getattr(s, "sdef", None), "is_boss", False):
        return "boss"
    if getattr(s, "is_rig", False):
        return "rig"
    return "ship"


def serialize(canvas) -> dict:
    c = canvas
    ships = []
    for s in c.ships:
        if getattr(s, "is_base", False):
            continue                                   # bases drawn from faction hp
        sdef = getattr(s, "sdef", None)
        if sdef is None:
            continue
        kind = _ship_kind(s)
        d = {
            "n":  _nid(c, s), "k": sdef.key, "t": s.team, "ki": kind,
            "x":  round(s.x, 1), "y": round(getattr(s, "top_y", 0.0), 1),
            "hp": round(getattr(s, "hp", 1.0), 1),
            "mhp": round(getattr(s, "max_hp", 1.0), 1),
        }
        if kind == "rig":
            d["ct"] = round(getattr(s, "construction_t", 0.0), 2)
            d["nt"] = [nt is not None for nt in getattr(s, "node_turrets", [])]
        else:
            am = getattr(s, "aim_mirror", None)
            d["a"]  = round(getattr(s, "aim_angle", 0.0), 3)
            d["am"] = -1 if am is None else (1 if am else 0)
            d["f"]  = round(getattr(s, "facing", 1.0), 2)
            d["st"] = getattr(s, "state", "moving")
            d["ri"] = bool(getattr(s, "rising", False))
            d["rf"] = bool(getattr(s, "retreat_flip", False))
            d["rl"] = [bool(getattr(s, "bursting", False)),
                       int(getattr(s, "burst_left", 0)),
                       bool(getattr(s, "rearming", False)),
                       round(getattr(s, "rearm_timer", 0.0), 2)]
        ships.append(d)

    # Full render state per round: position, velocity, team, calibre (proj_h),
    # width (proj_w), attack_type (selects the draw branch), age (rocket exhaust
    # glow) and fade timer (-1 = not fading). The client feeds these straight
    # into a real Projectile so its draw() reproduces the host's art exactly.
    proj = []
    for pr in c.projectiles:
        if not getattr(pr, "alive", True):
            continue
        atk = getattr(pr, "attack", None)
        ft  = getattr(pr, "fade_t", None)
        proj.append([
            round(pr.x, 1), round(pr.y, 1),
            round(getattr(pr, "vx", 0.0), 1), round(getattr(pr, "vy", 0.0), 1),
            pr.team,
            int(getattr(atk, "proj_h", 3) or 3),
            int(getattr(atk, "proj_w", 4) or 4),
            getattr(atk, "attack_type", "bullet"),
            round(getattr(pr, "age", 0.0), 2),
            -1.0 if ft is None else round(ft, 2),
        ])

    # Fresh effects born since the last snapshot (client spawns real, self-
    # animating copies). Explosions, heavy-gun muzzle smoke and missile launch/
    # exhaust plumes are all forwarded so both screens show the same bursts –
    # each row is tagged by type: "e"xplosion, "m"uzzle smoke, "l"aunch smoke,
    # "s"hell splash.
    sent = c._net_sent_fx
    fx = []
    live_ids = set()
    for e in c.effects:
        if isinstance(e, MuzzleSmoke):
            tag = "m"
        elif isinstance(e, LaunchSmoke):
            tag = "l"
        elif isinstance(e, Explosion):
            tag = "e"
        elif isinstance(e, Splash):
            tag = "s"
        else:
            continue
        eid = id(e)
        live_ids.add(eid)
        if eid in sent:
            continue
        x, y = round(e.x, 1), round(getattr(e, "y", 0.0), 1)
        if tag == "m":
            fx.append(["m", x, y, round(e.dir_x, 3), round(e.dir_y, 3),
                       round(e.power, 2)])
        else:
            fx.append([tag, x, y])
    c._net_sent_fx = live_ids

    caps = [[round(cp.progress, 3), cp._income_owner, 1 if cp.contested else 0]
            for cp in c.capture_points]

    return {
        "t": "snap",
        "time": round(c.game_time, 2),
        "over": c.game_over or 0,
        "pf": _fac_dict(c.player),
        "ef": _fac_dict(c.enemy),
        "edt": c.enemy_def_tiers,
        "es": {"o": [o is not None for o in c.enemy_over_slots],
               "u": [u is not None for u in c.enemy_under_slots]},
        "ecd":  {k: round(v, 2) for k, v in c.build_cd["enemy"].items() if v > 0},
        "ecdt": {k: round(v, 2) for k, v in c.build_cd_total["enemy"].items()},
        "eup":  {k: [round(pu["timer"], 2), round(pu["total"], 2)]
                 for k, pu in c.pending_upg["enemy"].items()},
        "ships": ships, "proj": proj, "fx": fx, "caps": caps,
        # Central-platform scoreboard: each side's score + the flagship countdown,
        # so the client's HUD reads the same as the host's (the boss loop itself is
        # host-only; the bosses it spawns arrive via `ships`).
        "score": [round(c.score["player"], 1), round(c.score["enemy"], 1)],
        "sbt":   round(c.score_boss_t, 2),
        # Which rung of the boss ladder deploys next, so the client's scoreboard
        # can name/icon the incoming flagship (the ladder itself is identical on
        # both sides; only the host advances the index in `_update_score`).
        "sbi":   c._score_ladder_i,
    }


# ── Client: apply a snapshot into the canvas's render lists ───────────────────
def _apply_fac(fac, d):
    fac.base_hp     = d["hp"]
    fac.max_base_hp = d["mhp"]
    fac.resources   = d["res"]
    fac.upgrades.update(d["upg"])
    fac.bonus_income = d["bi"]


def _make_entity(c, d, team, x):
    """Rebuild a render ghost for a snapshot ship, already team-swapped (`team`)
    and mirrored (`x`) into the client's own point of view."""
    key = d["k"]
    sdef = (c.data.all_units.get(key) or c.data.vehicles.get(key)
            or c.data.bosses.get(key))
    if sdef is None:
        return None
    kind = d["ki"]
    if kind == "boss":
        e = Boss(team, x, sdef, c._water_y())
    elif kind == "rig":
        e = OilRig(team, x, sdef)
        e.set_water(c._water_y())
        e.water_deep = c.level.water_tint
        e.is_rig = True
    else:
        e = Ship(team, x, sdef)
    e._nid = d["n"]
    return e


def apply(c, snap):
    c.game_time = snap["time"]
    over = _swap(snap["over"]) if snap["over"] else snap["over"]
    # Factions are swapped: OUR own base (left/black) is the faction the host tracks
    # as its enemy; the opponent (right/red) is the host's player faction.
    _apply_fac(c.player, snap["ef"])
    _apply_fac(c.enemy,  snap["pf"])
    # Extra fort tiers the fixed defensive guns need – on the host those belong to
    # its enemy fort, which is OUR own fort here (_draw_bases applies it there).
    c.enemy_def_tiers = snap["edt"]

    # OUR fort's used/free slots drive our turret-placement ghosts. On the host
    # these are the enemy fort slots (es); locally they are our own 'player' fort.
    # A sentinel marks occupied, None is free (only None-vs-not is read).
    es = snap["es"]
    c.over_slots  = [_OCC if occ else None for occ in es["o"]]
    c.under_slots = [_OCC if occ else None for occ in es["u"]]

    # OUR production cooldowns / research so our HUD reads true – the host tracked
    # them under the enemy faction; locally we command the 'player' side.
    c.build_cd["player"]       = dict(snap["ecd"])
    c.build_cd_total["player"] = dict(snap["ecdt"])
    c.pending_upg["player"]    = {k: {"timer": t, "total": tot}
                                  for k, (t, tot) in snap["eup"].items()}

    # Ships / bosses / bastions – reuse ghost entities across frames by nid so
    # placement and hit-tests stay stable; rebuild the draw order each snapshot.
    # Each is team-swapped and its x mirrored so it lands on the correct side; the
    # swapped team makes the sprite draw flipped + recoloured for free (see Ship).
    ghosts = c._ghosts
    seen = set()
    new_ships = []
    rigs_by_nid = {}
    for d in snap["ships"]:
        nid = d["n"]
        team = _swap(d["t"])
        x = WORLD_W - d["x"]
        e = ghosts.get(nid)
        if e is None or getattr(e, "team", None) != team or e.sdef.key != d["k"]:
            e = _make_entity(c, d, team, x)
            if e is None:
                continue
            ghosts[nid] = e
        seen.add(nid)
        e.x = x
        e.hp = d["hp"]; e.max_hp = d["mhp"]
        e.alive = True
        if d["ki"] == "rig":
            e.construction_t = d["ct"]
            nt = d["nt"]
            e.node_turrets = [(_OCC if (i < len(nt) and nt[i]) else None)
                              for i in range(len(e.node_turrets))]
            rigs_by_nid[nid] = e
        else:
            try:
                e.top_y = d["y"]
            except AttributeError:
                pass
            # aim_angle is barrel ELEVATION (vertical) – unchanged by a horizontal
            # mirror. facing (heading) and aim_mirror (barrel left/right) both flip.
            e.aim_angle = d["a"]
            e.aim_mirror = (None if d["am"] < 0 else (not bool(d["am"])))
            e.facing = -d["f"]
            e.state = d["st"]
            e.rising = d["ri"]
            e.retreat_flip = d["rf"]
            b, bl, rm, rt = d["rl"]
            e.bursting = b; e.burst_left = bl; e.rearming = rm; e.rearm_timer = rt
        new_ships.append(e)
    for nid in list(ghosts):
        if nid not in seen:
            del ghosts[nid]
    c.ships = new_ships
    c._net_rigs = rigs_by_nid

    # Projectiles: rebuild real Projectiles so draw() renders the host's exact
    # per-type ordnance art; effects spawned as real self-animating copies. Both
    # are mirrored (x, horizontal velocity / plume direction) and team-swapped.
    c.projectiles = [_make_proj(c, row) for row in snap["proj"]]
    for row in snap["fx"]:
        tag = row[0]
        if tag == "m":
            c.effects.append(MuzzleSmoke(WORLD_W - row[1], row[2], -row[3], row[4], row[5]))
        elif tag == "l":
            c.effects.append(LaunchSmoke(WORLD_W - row[1], row[2]))
        elif tag == "s":
            c.effects.append(Splash(WORLD_W - row[1], row[2]))
        else:
            c.effects.append(Explosion(WORLD_W - row[1], row[2]))

    # Capture points exist locally already (mirrored at build time – see
    # _build_capture_points); sync their meter. progress is signed toward the
    # PLAYER, so it flips under the swap; income_owner swaps sides too.
    for cp, (progress, inc, contested) in zip(c.capture_points, snap["caps"]):
        cp.progress = -progress
        cp._income_owner = _swap(inc)
        cp.contested = bool(contested)

    # Central-platform scoreboard. Scores swap sides (our 'player' is the host's
    # enemy); the flagship countdown is side-agnostic. Guarded so an older host
    # without these fields simply leaves the client's scoreboard at rest.
    if "score" in snap:
        c.score["player"] = snap["score"][1]
        c.score["enemy"]  = snap["score"][0]
        c.score_boss_t    = snap["sbt"]
        c._score_ladder_i = snap.get("sbi", c._score_ladder_i)

    if over and not c.game_over:
        c._end(over)


_OCC = object()          # opaque "slot occupied" marker for client-side placement


def _net_attack(c, atp, pw, ph):
    """A minimal Attack carrying only what Projectile.draw() reads – type and
    calibre. Cached per (type, w, h) so rebuilding the projectile list every
    snapshot doesn't allocate a fresh Attack for each round."""
    cache = c._net_atk_cache
    key = (atp, pw, ph)
    a = cache.get(key)
    if a is None:
        a = Attack("net", atp, [-1], 0, 0.0, 1.0, 0.0, proj_w=pw, proj_h=ph)
        cache[key] = a
    return a


def _make_proj(c, row):
    """Rebuild a real Projectile from a snapshot row so it draws through the
    host's identical per-type art. The client never simulates it – only the
    render-relevant fields (position, velocity, age, fade) are restored."""
    x, y, vx, vy, team, ph, pw, atp, age, fade = row
    # Mirror into our point of view: flip x and horizontal velocity, swap the team
    # so the tracer takes our own amber / the opponent's red on the correct side.
    pr = Projectile(WORLD_W - x, y, _swap(team), -vx, vy, _net_attack(c, atp, pw, ph))
    pr.age = age
    pr.fade_t = None if fade < 0 else fade
    return pr
