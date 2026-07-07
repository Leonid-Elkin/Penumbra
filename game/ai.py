"""
game.ai — EnemyDirector: decides what reinforcements the enemy sends and when.

Responsibilities:
  · Escalation   — spawn rate climbs with elapsed time.
  · Difficulty   — a multiplier on how many units are sent.
  · Counterattack— crossing base-HP thresholds triggers an immediate surge.
  · Adaptation   — biases unit choice to counter the player's air / sub threats.
  · Diversity    — penalises spamming the same unit.
  · Boss escort  — a large wave is launched alongside the boss.

The director never makes the units appear on-screen itself; it asks the canvas
to spawn them off the map edge so they sail/fly in (see BattleCanvas.spawn_enemy).
"""

from __future__ import annotations
import json, random

from .config import WORLD_W, WAVES_FILE

# Base-HP fractions that trigger a one-time counterattack surge (before boss).
_COUNTER_THRESHOLDS = [0.85, 0.65, 0.45]

# Per-stage category weighting for unit choice. RESPONSE (player passive) leans on
# cheap counters and light probes; ATTACK (player on the offensive) builds a varied
# defensive fleet — expensive + support + light hulls and diverse aircraft.
_RESPONSE_W = {"light": 1.5, "support": 1.3, "air": 1.0, "sub": 1.0, "capital": 0.4}
_ATTACK_W   = {"capital": 1.8, "support": 1.4, "air": 1.5, "sub": 1.1, "light": 0.8}


class EnemyDirector:
    def __init__(self, canvas, data, difficulty: float = 1.0):
        self.c = canvas
        self.data = data
        self.difficulty = max(0.3, float(difficulty))
        #self.difficulty = 0.1
        self.spawn_accum = 0.0
        self.aggression  = 1.0            # transient surge multiplier, decays to 1
        self.triggered: set = set()
        self.recent: list[str] = []       # last few spawned keys (per-unit diversity)
        self.recent_cat: list[str] = []   # last few spawned categories (role diversity)
        self.attacking = False            # latched once the player goes on the offensive

        # Spawn-rate PULSES (counterattacks / boss waves) — config in waves.json.
        cfg = self._load_waves()
        ca = cfg.get('counterattack', {}); bo = cfg.get('boss', {})
        self.ca_pulse   = (float(ca.get('peak', 4.0)),
                           float(ca.get('rise_seconds', 2.5)), float(ca.get('fall_seconds', 6.0)))
        self.boss_pulse = (float(bo.get('peak', 8.0)),
                           float(bo.get('rise_seconds', 2.0)), float(bo.get('fall_seconds', 8.0)))
        self.pulses: list = []            # active rate pulses
        self._cur_pulse = 1.0             # this frame's combined pulse multiplier

        self.spawnable = [k for k, s in data.vehicles.items()
                          if getattr(s, 'unit_type', 'ship') not in ('turret', 'structure')]
        self.role = {k: self._roles(data.vehicles[k]) for k in self.spawnable}
        self.cat = {k: self._category(self.role[k]) for k in self.spawnable}

    @staticmethod
    def _roles(s) -> dict:
        return {
            "air":  any(a.can_hit_plane for a in s.attacks),
            "sub":  any(a.can_hit_sub for a in s.attacks),
            "surf": any(a.can_hit_surface for a in s.attacks),
            "medium": getattr(s, 'unit_type', 'ship'),
            "cost": s.cost,
        }

    @staticmethod
    def _category(r: dict) -> str:
        """Coarse role bucket used to diversify waves (so they don't read as a
        single unit spammed): air / sub / capital / support / light."""
        if r["medium"] == "plane":     return "air"
        if r["medium"] == "submarine": return "sub"
        if r["cost"] >= 800:           return "capital"
        if r["cost"] <= 130:           return "light"
        return "support"

    # ── Stages: RESPONSE (player passive) → ATTACK (player on the offensive) ──────
    def _update_stage(self):
        """Latch into ATTACK mode once the player is obviously pushing — its base
        is taking real damage, or several player units are deep in enemy waters."""
        if self.attacking:
            return
        en = self.c.enemy
        if en.max_base_hp and en.base_hp < 0.85 * en.max_base_hp:
            self.attacking = True; return
        push = sum(1 for s in self.c.ships
                   if s.team == 'player' and s.alive
                   and not getattr(s, 'is_base', False) and not getattr(s, 'is_rig', False)
                   and getattr(s.sdef, 'unit_type', 'ship') != 'turret'
                   and s.x > WORLD_W * 0.6)
        if push >= 3:
            self.attacking = True

    # ── Per-frame ───────────────────────────────────────────────────────────────
    def update(self, dt):
        c = self.c
        if c.game_over:
            return
        # aggression decays back toward 1
        self.aggression += (1.0 - self.aggression) * min(1.0, dt * 0.4)
        self._update_stage()
        self._cur_pulse = self._pulse_mult()             # combined active-pulse multiplier

        en = c.enemy
        frac = en.base_hp / en.max_base_hp if en.max_base_hp else 1.0
        for th in getattr(c.level, 'counter_thresholds', _COUNTER_THRESHOLDS):
            if frac <= th and th not in self.triggered:
                self.triggered.add(th)
                self._counterattack(th)

        # Spend cadence — the spawn rate (incl. any pulse) sets how many units come.
        # During a strong pulse, spawns are free and ignore cooldowns so the rate is
        # actually reached; the on-field unit cap still bounds the result.
        self.spawn_accum += self._spawn_rate() * dt
        flooding = self._flooding()
        while self.spawn_accum >= 1.0:
            self.spawn_accum -= 1.0
            if not self._spawn_one(ignore_reserve=flooding, ignore_cd=flooding):
                break

        # Occasionally invest in an oil rig to grow its own economy.
        if self.c.game_time > 30 and random.random() < 0.04 * dt * 10:
            self.c.build_oilrig("enemy")

    # ── Spawn-rate pulses (counterattacks / boss waves) ──────────────────────────
    @staticmethod
    def _load_waves() -> dict:
        try:
            with open(WAVES_FILE, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError, ValueError):
            return {}

    def _pulse_params(self, kind: str):
        """Resolve a pulse's (peak, rise, fall) — the level's `waves` override (if
        any) layered over the global waves.json default."""
        peak, rise, fall = self.ca_pulse if kind == 'counterattack' else self.boss_pulse
        lvl = getattr(self.c.level, 'waves', None)
        cfg = lvl.get(kind, {}) if isinstance(lvl, dict) else {}
        return (float(cfg.get('peak', peak)),
                float(cfg.get('rise_seconds', rise)),
                float(cfg.get('fall_seconds', fall)))

    def _trigger_pulse(self, peak: float, rise: float, fall: float):
        """Start a spawn-rate pulse: rate climbs to `peak`× over `rise` seconds,
        then falls back to normal over `fall` seconds."""
        self.pulses.append({"t0": self.c.game_time, "peak": max(1.0, peak),
                            "rise": max(0.05, rise), "fall": max(0.05, fall)})
        self.pulses = self.pulses[-6:]

    def _pulse_mult(self) -> float:
        """Combined multiplier of all active pulses (max), pruning finished ones."""
        now = self.c.game_time; best = 1.0; alive = []
        for p in self.pulses:
            el = now - p["t0"]
            if el >= p["rise"] + p["fall"]:
                continue
            if el < p["rise"]:
                m = 1.0 + (p["peak"] - 1.0) * (el / p["rise"])
            else:
                m = p["peak"] + (1.0 - p["peak"]) * ((el - p["rise"]) / p["fall"])
            best = max(best, m); alive.append(p)
        self.pulses = alive
        return best

    def _flooding(self) -> bool:
        return self._cur_pulse > 1.5         # strong pulse → free, cooldown-free spawns

    def _spawn_rate(self) -> float:
        # Per-level base cadence (level JSON `spawn_rate`), falling back to engine
        # defaults: a sparse opening trickle that ramps up as the battle drags on.
        # `multiplier` is a flat per-level scalar on the whole rate — the simplest
        # knob to make one level harder or easier without retuning base/ramp.
        cfg = getattr(self.c.level, 'spawn_rate', None) or {}
        b0   = float(cfg.get('base', 0.14))
        ramp = float(cfg.get('ramp', 0.26))
        secs = max(1.0, float(cfg.get('ramp_seconds', 180.0)))
        mult = float(cfg.get('multiplier', 1.0))
        t = self.c.game_time
        base = b0 + min(t / secs, 1.0) * ramp            # RESPONSE: a sparse trickle
        rate = base * self.aggression                    # difficulty rides on income now
        if self.attacking:
            rate *= 1.35                                 # ATTACK: an active defence
        return rate * self._cur_pulse * mult             # pulses × per-level scalar

    # ── Economy ──────────────────────────────────────────────────────────────────
    def _reserve(self) -> float:
        """Resources the director holds back so the base keeps upgrading. It saves
        the full next-upgrade cost when an upgrade tick is imminent, otherwise a
        light reserve so units still trickle out."""
        nxt = self.c.enemy_next_upgrade_cost()
        if nxt <= 0:
            return 0.0
        return nxt if self.c.enemy.upg_timer < 5.0 else nxt * 0.25

    # ── Surges ──────────────────────────────────────────────────────────────────
    def _counterattack(self, th):
        # Each base-HP threshold fires a spawn-rate PULSE (ramp up, then back to
        # normal) rather than dumping a wave all at once. Params are level-specific.
        peak, rise, fall = self._pulse_params('counterattack')
        self._trigger_pulse(peak, rise, fall)
        self.aggression = max(self.aggression, 2.2)
        # Counterattacks are no longer announced — the surge happens silently.

    def boss_escort(self):
        """The boss wave: a bigger spawn-rate pulse (peak scales with difficulty),
        ramping up and then falling back to normal — same mechanic as a
        counterattack, just heavier. Params are level-specific."""
        self.attacking = True                    # the boss fight is the attack stage
        peak, rise, fall = self._pulse_params('boss')
        self._trigger_pulse(peak * self.difficulty, rise, fall)
        self.aggression = max(self.aggression, 2.5)

    # ── Choosing & spawning ──────────────────────────────────────────────────────
    def _commit(self, key: str):
        self.c.spawn_enemy(key)
        self.recent = (self.recent + [key])[-8:]
        self.recent_cat = (self.recent_cat + [self.cat[key]])[-6:]

    def _spawn_one(self, prefer_strong=False, ignore_reserve=False, ignore_cd=False) -> bool:
        free = self._flooding()                              # free spawns during a strong pulse
        key = self._choose(prefer_strong, ignore_reserve or free, free=free, ignore_cd=ignore_cd)
        if not key:
            return False
        if not free:
            self.c.enemy.resources -= self.role[key]["cost"] # pay for it
        self._commit(key)                                    # spawn + track diversity
        return True

    def _available(self, ignore_reserve=False, free=False, ignore_cd=False) -> list[str]:
        # The enemy may only field units the campaign had unlocked by this level
        # (BattleCanvas.enemy_unlocked), minus base turrets — fixed to the level's
        # place in the campaign, so a replay with everything unlocked still faces a
        # period-appropriate enemy. Units on production cooldown or that the enemy
        # cannot currently afford (after holding an upgrade reserve) are skipped, so
        # the AI is income-limited and must upgrade its base to escalate.
        cd = self.c.build_cd.get('enemy', {})
        ready = lambda k: ignore_cd or cd.get(k, 0.0) <= 0
        if free:                                             # boss flood: money is no object
            return [k for k in self.c.enemy_unlocked if k in self.role and ready(k)]
        budget = self.c.enemy.resources - (0.0 if ignore_reserve else self._reserve())
        return [k for k in self.c.enemy_unlocked
                if k in self.role and ready(k) and self.role[k]["cost"] <= budget]

    def _threat(self):
        planes = subs = surf = tot = 0
        for s in self.c.ships:
            if s.team != "player" or not s.alive: continue
            if getattr(s.sdef, 'is_boss', False): continue
            tot += 1
            ut = getattr(s.sdef, 'unit_type', 'ship')
            if ut == 'plane': planes += 1
            elif ut == 'submarine': subs += 1
            else: surf += 1
        if tot == 0:
            return 0.0, 0.0, 0.0
        return planes / tot, subs / tot, surf / tot

    def _choose(self, prefer_strong=False, ignore_reserve=False, free=False, ignore_cd=False) -> str | None:
        pool = self._available(ignore_reserve, free, ignore_cd)
        if not pool:
            return None
        air, sub, _surf = self._threat()
        attacking = self.attacking or prefer_strong
        cat_w = _ATTACK_W if attacking else _RESPONSE_W
        weights = []
        for k in pool:
            r = self.role[k]; cat = self.cat[k]; w = 1.0
            # Adaptation: answer the player's dominant threat (AA vs aircraft,
            # depth-charges/torpedoes vs subs). Strong in the RESPONSE stage.
            if air > 0:
                w += air * (2.2 if r["air"] else -0.4)
            if sub > 0:
                w += sub * (2.0 if r["sub"] else -0.1)
            # Diversity: heavily discourage repeating the same unit OR role, so a
            # wave reads as a mixed fleet rather than one vehicle spammed.
            w *= 0.30 ** self.recent.count(k)
            w *= 0.55 ** self.recent_cat.count(cat)
            # Stage composition: light counters when responding, a varied mix of
            # capital / support / light / aircraft when defending an assault.
            w *= cat_w.get(cat, 1.0)
            if prefer_strong:
                w *= max(1.0, r["cost"] / 150.0)
            weights.append(max(0.03, w))
        return random.choices(pool, weights=weights, k=1)[0]
