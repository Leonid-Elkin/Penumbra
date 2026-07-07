"""
game.entities.boss — the end-of-battle boss entity.

A Boss is a Ship that DEFENDS its base instead of assaulting. It holds station
near the base, fires all of its weapons at any valid target, and drives the
base-immunity flag on its faction (immune while the boss lives). Behaviour and
hit-medium come from boss_type / unit_type:

    air      (Hindenburg) — flies high; only anti-air weapons can hit it
    carrier  (Nimitz)     — floats and launches aircraft (via spawn_units)
    sub      (Nautilus)   — submerged; only anti-sub weapons can hit it
    surface  (the rest)   — floats; long-range arcing artillery
"""

from __future__ import annotations
import math, random
from PyQt6.QtCore import Qt
from PyQt6.QtGui  import QColor, QPen, QFont

from .ship import Ship
from .projectile import attack_can_hit
from .explosion import Explosion
from ..config import FORT_D_W, WORLD_W

BOSS_AIR_ALT = 270.0          # px above water for an airborne boss centre
BOSS_SPEED   = 28.0           # px/s — bosses creep toward the player's base


class Boss(Ship):
    def __init__(self, team, x, sdef, water_y: float):
        super().__init__(team, x, sdef)
        self.water_y  = water_y
        self.anchor_x = x
        self.phase_t  = random.uniform(0.0, 6.28)
        self.boss_type = getattr(sdef, 'boss_type', 'surface')
        self.death_handled = False
        self.patrol_dir = -1          # air boss: horizontal loop heading
        self.standoff = getattr(sdef, 'standoff_range', 0.0)
        self.nearest_ground = float('inf')   # dist to closest surface/undersea foe
        # Which base this boss assaults: an ENEMY boss marches on the player's
        # (left) base, a PLAYER boss marches on the enemy's (right) base. +1 =
        # advancing rightward, -1 = leftward. The whole _move logic is mirrored
        # off this so a punishment boss can be called down on EITHER side.
        self.attack_dir = -1 if team == 'enemy' else 1

    # ── Update ─────────────────────────────────────────────────────────────────
    def update(self, dt, ships, projectiles, effects, pl_f, en_f):
        if not self.alive: return
        self.phase_t += dt
        in_combat, surface_combat = self._fire(dt, ships, projectiles, effects)
        self._move(dt, in_combat, surface_combat)
        self._position(dt)
        # Death (and lifting base immunity) is handled centrally by the battle,
        # because a projectile can set alive=False before this runs again.

    def _move(self, dt, in_combat, surface_combat):
        """Advance toward the base this boss assaults (leftward for an enemy boss,
        rightward for a player boss — see attack_dir). A surface/carrier/sub boss
        only STOPS to bombard when it has a SURFACE or UNDERSEA target in range —
        it keeps creeping while merely strafing aircraft. An AIR boss (Hindenburg)
        never stops: it patrols back and forth raining ordnance."""
        spd = max(self.sdef.speed, BOSS_SPEED)
        d = self.attack_dir
        if self.boss_type == 'air':
            # Patrol band hugging the target base — near the left fort for an enemy
            # boss, near the right fort for a player boss.
            if d < 0:
                lo = float(FORT_D_W + self.disp_w * 0.5 + 40)
                hi = lo + 1150.0
            else:
                hi = float(WORLD_W - FORT_D_W - self.disp_w * 0.5 - 40)
                lo = hi - 1150.0
            if self.x > hi:               # still flying in from the map edge
                self.x = max(hi, self.x - spd * dt); self.patrol_dir = -1
            elif self.x < lo:
                self.x = min(lo, self.x + spd * dt); self.patrol_dir = 1
            else:                          # inside the patrol band → loop
                self.x += self.patrol_dir * spd * dt
                if self.x <= lo:   self.x = lo;  self.patrol_dir = 1
                elif self.x >= hi: self.x = hi;  self.patrol_dir = -1
            self.state = "fighting" if in_combat else "moving"
            return
        if d < 0:
            assault_x = float(FORT_D_W + self.disp_w * 0.5)
            off_field = self.x > WORLD_W - FORT_D_W      # still off the enemy edge
        else:
            assault_x = float(WORLD_W - FORT_D_W - self.disp_w * 0.5)
            off_field = self.x < FORT_D_W                # still off the player edge
        # When a standoff range is set, the boss keeps advancing (firing as it
        # closes) until a ground unit comes within that range, then holds. Without
        # one it falls back to stopping the moment a surface target is in weapon range.
        if self.standoff > 0:
            hold = self.nearest_ground <= self.standoff
        else:
            hold = surface_combat
        # A boss spawns BEYOND its far edge and opens fire as it sails in, but it
        # must NEVER stop to bombard while still off the field — otherwise it freezes
        # off-screen the instant a target enters range, technically "attacking" yet
        # unable to advance. It only holds station once its hull is on the battlefield.
        if off_field:
            hold = False
        if not hold:
            if d < 0 and self.x > assault_x:
                self.x = max(assault_x, self.x - spd * dt)
            elif d > 0 and self.x < assault_x:
                self.x = min(assault_x, self.x + spd * dt)
        at_base = abs(self.x - assault_x) <= 1.0
        self.state = "assaulting" if (at_base and in_combat) else \
                     "fighting" if in_combat else "moving"

    def _position(self, dt):
        """Set vertical position for this boss type (bosses are exempt from the
        battle Y-sync)."""
        wy = self.water_y
        if self.boss_type == 'air':
            bob = math.sin(self.phase_t * 0.8) * 14.0
            self.top_y = wy - BOSS_AIR_ALT - self.disp_h * 0.5 + bob
        elif self.boss_type == 'sub':
            # Mostly submerged, rises a little on a slow cycle to "surface" and fire
            surface = (math.sin(self.phase_t * 0.35) * 0.5 + 0.5)   # 0..1
            self.top_y = wy + self.disp_h * (0.34 - 0.20 * surface)
        else:  # carrier / surface
            # Seat the hull in the water: `submerged_frac` (from the boss JSON) is
            # how much of the sprite HEIGHT sits below the waterline. The default
            # 0.05 keeps only the very bottom dipping under (tall-masted ships stay
            # fully visible); a larger value sinks the boss lower — e.g. the Unknown
            # rides with its hull "base" all the way in the ocean, deck at the water.
            frac = getattr(self.sdef, 'submerged_frac', 0.05)
            self.top_y = wy - self.disp_h * (1.0 - frac)

    def _fire(self, dt, ships, projectiles, effects=None):
        """Fire every weapon at its best target. Returns (in_combat, surface_combat)
        where surface_combat is True only when a non-aircraft target is in range —
        a boss keeps advancing while it merely strafes planes."""
        in_combat = False; surface_combat = False
        # Nearest surface/undersea foe — drives the standoff hold in _move.
        self.nearest_ground = float('inf')
        for s in ships:
            if s.team == self.team or not s.alive: continue
            if getattr(s, 'is_base', False): continue
            if getattr(s.sdef, 'unit_type', 'ship') == 'plane': continue
            self.nearest_ground = min(self.nearest_ground, self._edge_dist(s))
        for ai, attack in enumerate(self.sdef.attacks):
            best = None; best_d = float('inf')
            for s in ships:
                if s.team == self.team or not s.alive: continue
                if not attack_can_hit(attack, s): continue
                d = self._edge_dist(s)
                if d < best_d: best_d, best = d, s
            if best and best_d <= attack.combat_range:
                in_combat = True
                if getattr(best.sdef, 'unit_type', 'ship') != 'plane':
                    surface_combat = True
                self.fire_ts[ai] -= dt
                if self.fire_ts[ai] <= 0:
                    self.fire_ts[ai] = attack.fire_interval
                    self._fire_indices(attack, best, projectiles, effects)
        return in_combat, surface_combat

    def death_explosions(self, effects):
        """A spread of explosions across the hull for a climactic kill."""
        for _ in range(8):
            ox = random.uniform(-self.disp_w * 0.4, self.disp_w * 0.4)
            oy = random.uniform(-self.disp_h * 0.4, self.disp_h * 0.4)
            effects.append(Explosion(self.x + ox, self.mid_y + oy))

    # ── Draw ───────────────────────────────────────────────────────────────────
    def draw(self, p, cam_x: float, sprites: dict, night: float = 0.0):
        if not self.alive: return
        sx = int(self.x - self.disp_w * 0.5 - cam_x)
        sy = int(self.top_y)
        spr = sprites.get(f"{self.team}_{self.sdef.key}")
        if spr and not spr.isNull():
            p.drawPixmap(sx, sy, spr)
        else:
            p.fillRect(sx, sy, self.disp_w, self.disp_h, QColor("#101014"))
            p.setPen(QColor("#cc4444")); p.setFont(QFont("Arial", 12, QFont.Weight.Bold))
            p.drawText(sx, sy, self.disp_w, self.disp_h,
                       Qt.AlignmentFlag.AlignCenter, self.sdef.name)
        # Bosses use the same plain health bar as any other enemy — no special
        # boss banner — so they read as ordinary (if large) hostiles.
        self._draw_hpbar(p, sx, sy)
