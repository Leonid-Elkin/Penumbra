"""
game.entities.ship — the live unit entity and all of its movement/combat AI.

A Ship is data-driven: every stat, weapon and muzzle comes from its ShipDef. The
master update() dispatches to a behaviour method per unit_type (ship/submarine,
turret, plane).
"""

from __future__ import annotations
import math, random, bisect
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui  import QPainter, QColor, QPen, QFont

from ..config import (WORLD_W, FORT_D_W, STORAGE_T, TURRET_REAR_RANGE_PER_PX)
from ..settings import SETTINGS
from ..ballistics import ballistic_velocity, velocity_at_angle
from .projectile import Projectile, attack_can_hit, attack_gravity
from .explosion import MuzzleSmoke

# Shell calibre (proj_h) at/above which firing throws a muzzle smoke cloud —
# the big naval rifles qualify, light quick-firing guns fire clean.
LARGE_SHELL_CAL = 7

# Freshly placed artillery (the manually-aimed coastal battery) arms for this
# long before its first shot, so the player has a beat to set elevation rather
# than the gun loosing a round the instant it drops onto the pad.
ARTILLERY_DEPLOY_DELAY = 2.0


def _reaches_air(attack) -> bool:
    """Can this attack actually elevate fire onto an aircraft? A flat horizontal
    gun cannot, so a ship with one shouldn't stop to 'shoot' planes it'll miss."""
    return (getattr(attack, 'omnidirectional', False) or getattr(attack, 'track_target', False)
            or getattr(attack, 'homing', False) or getattr(attack, 'ballistic', False)
            or attack.attack_type in ('flak', 'missile', 'aa_missile'))


class Ship:
    """Live unit. All stats come from sdef (ShipDef)."""
    _ctr = 0

    # ── Per-frame spatial index (sorted by x) ────────────────────────────────
    # Target selection used to scan the whole ship list for every unit, every
    # attack, every frame — O(N²) with a 180-unit field. Instead the battle loop
    # rebuilds this x-sorted index once per frame and each unit only scans the
    # slice within weapon reach (see _candidates). Nothing is culled by camera:
    # every unit on the field — on- or off-screen — is indexed and keeps fighting.
    _frame_ships: list = []
    _frame_xs: list = []
    _frame_max_half_w: float = 0.0

    @classmethod
    def set_frame_index(cls, ships):
        """Rebuild the per-frame x-sorted target index from the live ship list."""
        arr = sorted(ships, key=lambda s: s.x)
        cls._frame_ships = arr
        cls._frame_xs = [s.x for s in arr]
        mh = 0.0
        for s in arr:
            hw = getattr(s, 'disp_w', 0) * 0.5
            if hw > mh:
                mh = hw
        cls._frame_max_half_w = mh

    def _candidates(self, reach: float, fallback):
        """Ships whose x lies within `reach` of ours — a superset of everything a
        weapon of that range could possibly hit. The window folds in both hulls'
        half-widths (edge-to-edge ranging) plus a little slack for intra-frame
        movement, so it never drops a target the full scan would have found; it
        only skips ones already out of range. Falls back to the full list when the
        index isn't built (e.g. unit tests calling update() directly)."""
        xs = Ship._frame_xs
        if not xs:
            return fallback
        span = reach + self.disp_w * 0.5 + Ship._frame_max_half_w + 60.0
        lo = bisect.bisect_left(xs, self.x - span)
        hi = bisect.bisect_right(xs, self.x + span)
        return Ship._frame_ships[lo:hi]

    def __init__(self, team: str, x: float, sdef):
        Ship._ctr += 1
        self.id       = Ship._ctr
        self.team     = team
        self.x        = x
        self.sdef     = sdef
        self.hp       = sdef.hp
        self.max_hp   = sdef.hp
        self.alive    = True
        self.fire_ts  = [0.0] * len(sdef.attacks)
        # A hand-aimed coastal battery holds fire for a short arming beat after
        # being placed, giving the player time to set elevation first.
        if getattr(sdef, 'manual_aim', False):
            self.fire_ts = [ARTILLERY_DEPLOY_DELAY] * len(sdef.attacks)
        self.state    = "moving"
        self.y_jitter = random.uniform(-3, 3)
        self.top_y    = 0.0
        self.water_y  = 0.0    # current waterline, stamped each frame by the battle loop

        # Plane attributes
        self.plane_dir       = 1
        self.plane_target_id = -1
        self.facing          = 1.0    # smooth visual heading (-1..1) for banking turns
        self.y_off           = 0.0
        self.y_off_target    = 0.0
        self.y_off_timer     = 0.0
        self.standoff_retreat = False  # 'standoff' planes: peeling back from a target
        # Each plane holds a slightly different standoff (re-rolled per engagement)
        # plus a lateral bias and an altitude lane, so they don't all stack up.
        self.standoff_mult   = random.uniform(0.55, 0.98)
        self.hover_jitter    = random.uniform(-45.0, 45.0)
        self.hover_lane      = random.uniform(-42.0, 42.0)   # vertical de-clump
        # Smoothed forward velocity, so a unit eases to a stop (and coasts a moment
        # after opening fire) instead of halting on a dime.
        self.move_vel        = 0.0
        # Smoothed world velocity (px/s), measured from frame-to-frame movement so
        # an AA gun can LEAD its aim — shoot where a crossing plane WILL be, not
        # where it is. Sampled at the top of update(); _prev_py None until first.
        self.vel_x           = 0.0
        self.vel_y           = 0.0
        self._prev_px        = x
        self._prev_py        = None

        # Carrier spawn attributes
        self.spawn_timer = max(0.0, getattr(sdef, 'spawn_interval', 0.0))
        self.spawn_index = 0
        self.rising      = False

        # Off-screen entry: while True the unit is sailing/flying in from beyond
        # the map edge and ignores combat until it reaches the playfield.
        self.entering    = False

        # Special behaviours
        self.rearming    = False     # MLRS: retreating to base to rearm
        self.rearm_timer = 0.0
        self.retreat_flip = False    # surface unit faced about (sprite mirrored)
        self.minelaying  = False     # minelayer: running back off-screen to despawn
        self.aim_angle   = 0.7       # barrel elevation (radians): manual for artillery,
                                     # target-tracked for the AA gun
        self.aim_mirror  = None      # barrel horizontal flip; None = follow base facing
        self.bursting    = False     # firing a series of rounds (e.g. rocket salvo)
        self.burst_left  = 0
        self.burst_timer = 0.0
        self.burst_attack_idx = 0
        self.burst_target = None

    # ── Geometry ────────────────────────────────────────────────────────────
    @property
    def mid_y(self) -> float:
        return self.top_y + self.sdef.display_h * 0.5

    @property
    def hit_com(self) -> tuple:
        """World (x, y) of the collision hitbox's centre of mass — the spot a
        homing round should steer AT so it strikes solid plating. Differs from
        (`x`, `mid_y`): a surface ship's mass sits low (near/under the waterline)
        and a sub's hull is offset within its padded frame. Falls back to the
        geometric centre for unmasked units."""
        mask = getattr(self.sdef, 'hitmask', None)
        if mask is None:
            return self.x, self.mid_y
        u, v = mask.centroid()
        if self._display_mirror():
            u = 1.0 - u
        cx = (self.x - self.disp_w * 0.5) + u * self.disp_w
        cy = self.top_y + v * self.disp_h
        return cx, cy

    @property
    def disp_w(self) -> int: return self.sdef.display_w
    @property
    def disp_h(self) -> int: return self.sdef.display_h

    def _is_flipped(self) -> bool:
        return self.sdef.flip_player if self.team == "player" else self.sdef.flip_enemy

    def _display_mirror(self) -> bool:
        """True when the sprite is drawn mirrored relative to the source image the
        hitmask was built from — so the mask's columns must be flipped to match.
        Folds in the team flip, a fire-and-scoot turnabout, and a plane's banking
        heading (see draw())."""
        m = self._is_flipped()
        if getattr(self, 'retreat_flip', False):
            m = not m
        if getattr(self.sdef, 'unit_type', 'ship') == 'plane' and not self.rising:
            forward = 1.0 if self.team == 'player' else -1.0
            if self.facing * forward < 0:
                m = not m
        return m

    def hit_test(self, x: float, y: float) -> bool:
        """Does world point (x, y) strike this unit? Uses the hull/superstructure
        silhouette when one was built for the sprite, so rounds pass through masts
        and empty space; falls back to a padded box for unmasked units."""
        mask = getattr(self.sdef, 'hitmask', None)
        if mask is None:
            hw = self.disp_w * 0.52; hh = self.disp_h * 0.70
            return abs(x - self.x) < hw and abs(y - self.mid_y) < hh
        rel_x = (x - (self.x - self.disp_w * 0.5)) / self.disp_w
        rel_y = (y - self.top_y) / self.disp_h
        if self._display_mirror():
            rel_x = 1.0 - rel_x
        return mask.contains(rel_x, rel_y)

    # ── Firing helpers ────────────────────────────────────────────────────────
    def _select_fire_point(self, attack, target_x):
        flipped    = self._is_flipped()
        target_dir = 1 if target_x > self.x else -1
        indices    = attack.fire_point_indices
        candidates = range(len(self.sdef.fire_points)) if indices == [-1] else \
                     [i for i in indices if 0 <= i < len(self.sdef.fire_points)]
        best_fp    = self.sdef.fire_points[candidates[0] if candidates else 0]
        best_score = float("-inf")
        for i in candidates:
            fp  = self.sdef.fire_points[i]
            adx = -fp.dir_x if flipped else fp.dir_x
            score = adx * target_dir
            if score > best_score: best_score, best_fp = score, fp
        return best_fp

    def _make_projectile(self, attack, fp, target_x, target_y=None):
        flipped   = self._is_flipped()
        actual_rx = (1.0 - fp.rel_x) if flipped else fp.rel_x
        actual_dx = (-fp.dir_x)      if flipped else fp.dir_x
        actual_dy = fp.dir_y
        fire_x = (self.x - self.disp_w * 0.5) + actual_rx * self.disp_w
        fire_y = self.top_y + fp.rel_y * self.disp_h

        spd = attack.speed

        # VLS ("Tor") launch: cold-eject the round straight UP out of its tube at a
        # modest pop speed, ignoring the fire-point heading. It coasts, then tips
        # over ~90° toward the target and boosts to full `speed` in flight (see
        # Projectile.update). A hair of horizontal drift toward the target fans
        # multi-tube salvoes apart instead of stacking them into one column.
        if getattr(attack, 'vls', False):
            pop = getattr(attack, 'vls_pop_speed', 130.0)
            drift = 0.0
            if target_x is not None:
                drift = 20.0 if target_x >= fire_x else -20.0
            return Projectile(fire_x, fire_y, self.team, drift, -pop, attack)

        # fixed-elevation gun: same launch angle every shot, muzzle SPEED varies
        # with range (naval main battery). Falls back to a flat shot if too close.
        if getattr(attack, 'gun_angle', 0.0) > 0.0 and target_y is not None:
            dx = target_x - fire_x; dy = target_y - fire_y
            v = velocity_at_angle(dx, dy, attack.gun_angle, attack_gravity(attack, 220.0))
            if v is not None:
                v = max(140.0, min(spd, v))
                dirx = 1.0 if dx >= 0 else -1.0
                ang = attack.gun_angle
                return Projectile(fire_x, fire_y, self.team,
                                  math.cos(ang) * v * dirx, -math.sin(ang) * v, attack)

        # flak: launch straight AT the target — it then homes + air-bursts
        if attack.attack_type == "flak" and target_y is not None:
            dx = target_x - fire_x; dy = target_y - fire_y
            dist = math.hypot(dx, dy) or 1.0
            return Projectile(fire_x, fire_y, self.team, dx / dist * spd, dy / dist * spd, attack)

        # bomb: a plane releases it with forward momentum and a slight downward
        # lean — so it leaves nearly level with the plane's heading and arcs ahead
        # of it. (A ship/boss bomb is instead lobbed at a fixed gun_angle, handled
        # by the gun_angle branch above.) On hitting the water a bomb switches to a
        # straight-down sink (see Projectile.update), so it can also strike subs.
        if attack.attack_type == "bomb":
            fwd = self.facing if abs(self.facing) > 0.2 else (1.0 if self.team == "player" else -1.0)
            vx = fwd * self.sdef.speed
            vy = abs(vx) * 0.35 + 12.0
            return Projectile(fire_x, fire_y, self.team, vx, vy, attack)

        # ballistic: lob a gravity arc onto the target (artillery, depth charges)
        if getattr(attack, 'ballistic', False) and target_y is not None:
            g = attack_gravity(attack, 200.0 if attack.attack_type in ("bomb", "depth_charge") else 220.0)
            # A depth-charge carrier BURSTS the instant it hits the sea surface (see
            # Projectile._update_dc_carrier), so it must be lobbed to land ON the
            # waterline directly above the submerged target — not at the sub's own
            # underwater depth, which would drop the pattern short (the arc crosses
            # the surface while still descending toward that deeper point). If no
            # arc reaches that point, return None so the ship holds fire instead of
            # plopping a charge straight down onto itself.
            aim_y = self.water_y if attack.attack_type == "depth_charge" else target_y
            bv = ballistic_velocity(target_x - fire_x, aim_y - fire_y, spd, g)
            if bv is not None:
                return Projectile(fire_x, fire_y, self.team, bv[0], bv[1], attack)
            if attack.attack_type == "depth_charge":
                return None

        # plane forward gun: fire down the jet's heading out of the leading (nose)
        # muzzle, but PITCH the burst onto the target's altitude. Enemy planes
        # cruise in different vertical lanes (bombers, hover lanes) and bob up and
        # down, so a dead-level shot sails over or under them across the gun's
        # reach. Keep the horizontal component along the facing (never fire
        # backwards) and add just the vertical slope needed to converge on the
        # target — bullets carry no gravity, so a straight line meets it. Fixed-
        # wing guns only; homing/tracking/omnidirectional weapons are elsewhere.
        if (attack.attack_type == 'bullet'
                and getattr(self.sdef, 'unit_type', 'ship') == 'plane'
                and not getattr(attack, 'track_target', False)
                and not getattr(attack, 'omnidirectional', False)):
            fwd   = 1.0 if self.facing >= 0 else -1.0
            rel_x = (1.0 - fp.rel_x) if fwd > 0 else fp.rel_x      # muzzle at the nose
            mx    = (self.x - self.disp_w * 0.5) + rel_x * self.disp_w
            vx, vy = fwd * spd, 0.0
            if target_y is not None:
                dx = target_x - mx
                if dx * fwd > 1.0:                     # target genuinely ahead
                    vy = (target_y - fire_y) / abs(dx) * spd
                    sc = spd / (math.hypot(vx, vy) or spd)   # keep muzzle speed
                    vx *= sc; vy *= sc
            return Projectile(mx, fire_y, self.team, vx, vy, attack)

        # track_target: aim launch direction at the target's actual position
        if getattr(attack, 'track_target', False) and target_y is not None:
            dx = target_x - fire_x; dy = target_y - fire_y
            dist = math.hypot(dx, dy)
            if dist > 0: actual_dx = dx / dist; actual_dy = dy / dist

        # Launch inaccuracy: rotate the aimed heading by a small random angle so a
        # rapid-fire tracking gun (the AA machine guns) fans its rounds slightly
        # around the target instead of stitching every shot dead-on. Rotation
        # preserves the direction's magnitude, so muzzle speed is unchanged.
        spread = getattr(attack, 'spread_deg', 0.0)
        if spread > 0.0:
            j = math.radians(random.uniform(-spread, spread))
            cj, sj = math.cos(j), math.sin(j)
            actual_dx, actual_dy = (actual_dx * cj - actual_dy * sj,
                                    actual_dx * sj + actual_dy * cj)

        return Projectile(fire_x, fire_y, self.team, actual_dx * spd, actual_dy * spd, attack)

    def _muzzle_smoke(self, attack, mx, my, vx, vy, effects):
        """Blow a powder-smoke cloud out of the muzzle at (mx, my), along the
        shot's departure (vx, vy). Only heavy ordnance smokes: a large-calibre
        shell throws a big blast scaled by calibre — and a boss's artillery
        always counts as large, whatever its calibre; each MLRS rocket leaving
        its tube adds a smaller exhaust puff (a full salvo stacks them into one
        rolling cloud around the launcher). Everything else fires clean."""
        if effects is None:
            return
        is_boss = hasattr(self, 'boss_type')
        if attack.attack_type == "shell" and (is_boss or attack.proj_h >= LARGE_SHELL_CAL):
            power = max(attack.proj_h / 8.0, 0.85 if is_boss else 0.0)
        elif attack.attack_type == "rocket":
            power = 0.55
        else:
            return
        spd = math.hypot(vx, vy) or 1.0
        effects.append(MuzzleSmoke(mx, my, vx / spd, vy / spd, power))

    def _lead_point(self, attack, target):
        """Predictive aim for a direct-fire tracking gun: the point a straight
        round at this attack's muzzle speed should be sent to INTERCEPT the target,
        derived from the target's smoothed velocity. A plane crossing at speed
        otherwise flies out from under a shot aimed where it currently is — which
        is why light AA feels like it can never connect. Only `track_target` guns
        lead (homing/ballistic/flak solve interception their own way); everything
        else keeps aiming at the present position, so this is a no-op for them."""
        tx, ty = target.x, target.mid_y
        spd = attack.speed
        if not getattr(attack, 'track_target', False) or spd <= 0:
            return tx, ty
        vx = getattr(target, 'vel_x', 0.0)
        vy = getattr(target, 'vel_y', 0.0)
        # Cap the lead horizon at the round's own lifetime: it can't land later
        # than that, so predicting further only risks flinging the aim off the map
        # on a noisy velocity estimate.
        tmax = getattr(attack, 'max_lifetime', 0.0) or 1.5
        ax, ay = tx, ty
        for _ in range(2):                     # fixed-point intercept — converges fast
            t = math.hypot(ax - self.x, ay - self.mid_y) / spd
            if t > tmax: t = tmax
            ax, ay = tx + vx * t, ty + vy * t
        return ax, ay

    def _fire_attack(self, attack, fp, best, projectiles, effects=None):
        """Fire one shot. Depth charges fire a single arcing carrier (it splits in
        flight); other scatter attacks spray at the muzzle."""
        n  = getattr(attack, 'scatter_count', 1)
        sp = getattr(attack, 'scatter_spread', 0.0)
        aim_x, aim_y = self._lead_point(attack, best)   # predict a crosser's future
        first = None
        if attack.attack_type == "depth_charge" or not (n > 1 and sp > 0):
            pr = self._make_projectile(attack, fp, aim_x, aim_y)
            if pr is None:            # no ballistic arc reaches the sub — hold fire
                return
            pr.owner_id = self.id
            projectiles.append(pr); first = pr
        elif getattr(attack, 'ballistic', False):
            # Inaccurate arcing salvo (MLRS): each round arcs to a scattered point.
            for _ in range(n):
                ox = random.uniform(-sp * 0.5, sp * 0.5)
                pr = self._make_projectile(attack, fp, aim_x + ox, aim_y)
                pr.owner_id = self.id
                projectiles.append(pr)
                if first is None: first = pr
        else:
            dx_dir = math.copysign(1.0, best.x - self.x)
            for _ in range(n):
                pr = self._make_projectile(attack, fp, aim_x, aim_y)
                horiz = dx_dir * random.uniform(0, sp * 0.55) + \
                        random.uniform(-sp * 0.45, sp * 0.45)
                pr.vx = horiz
                pr.vy = abs(pr.vy if pr.vy != 0 else attack.speed) * random.uniform(0.8, 1.25)
                pr.owner_id = self.id
                projectiles.append(pr)
                if first is None: first = pr
        if first is not None:
            # one cloud per trigger pull, from the round's actual spawn point
            self._muzzle_smoke(attack, first.x, first.y, first.vx, first.vy, effects)

    # ── Master update dispatcher ───────────────────────────────────────────────
    def update(self, dt, ships, projectiles, effects, pl_f, en_f):
        if not self.alive: return
        # Track smoothed velocity from last frame's net motion (this frame's move
        # happens below, so the one-frame lag is harmless). Used to lead AA aim.
        if dt > 0.0 and self._prev_py is not None:
            ivx = (self.x - self._prev_px) / dt
            ivy = (self.mid_y - self._prev_py) / dt
            a = min(1.0, dt * 12.0)
            self.vel_x += (ivx - self.vel_x) * a
            self.vel_y += (ivy - self.vel_y) * a
        self._prev_px = self.x
        self._prev_py = self.mid_y
        if self.entering:
            self._update_entering(dt, ships); return
        fac = pl_f if self.team == "player" else en_f
        opp = en_f if self.team == "player" else pl_f
        ut  = getattr(self.sdef, 'unit_type', 'ship')

        if self.bursting:
            self._update_burst(dt, ships, projectiles, fac, effects); return
        if self.rearming:
            self._update_rearm(dt); self._check_death(fac, effects); return
        if ut == 'turret': self._update_turret(dt, ships, projectiles, effects, fac); return
        if ut == 'plane':  self._update_plane(dt, ships, projectiles, effects, fac); return
        if getattr(self.sdef, 'minelayer', False):
            self._update_minelayer(dt, ships, projectiles, effects, fac); return

        self._update_surface(dt, ships, projectiles, effects, fac)

    # ── Minelayer ───────────────────────────────────────────────────────────────
    def _update_minelayer(self, dt, ships, projectiles, effects, fac):
        """Pop out, run forward laying a trail of mines, and the moment it nears
        the enemy turn about and steam off the map edge — gone for good."""
        is_pl = self.team == "player"
        fwd = 1 if is_pl else -1
        trigger_x = (WORLD_W - FORT_D_W - 360) if is_pl else (FORT_D_W + 360)

        if not self.minelaying:
            nd = float('inf')
            for s in self._candidates(430.0, ships):
                if s.team == self.team or not s.alive: continue
                if getattr(s, 'is_base', False) or getattr(s, 'is_rig', False): continue
                if getattr(s.sdef, 'unit_type', 'ship') == 'plane': continue
                nd = min(nd, abs(s.x - self.x))
            past_trigger = (self.x >= trigger_x) if is_pl else (self.x <= trigger_x)
            if nd < 430.0 or past_trigger:
                self.minelaying = True

        self.state = "moving"
        if self.minelaying:
            # Turn about and run off-screen, then vanish.
            self.retreat_flip = True
            self.x += -fwd * self.sdef.speed * dt
            if self.x < -self.disp_w - 40 or self.x > WORLD_W + self.disp_w + 40:
                self.alive = False; return
        else:
            self.x += fwd * self.sdef.speed * dt
            for ai, attack in enumerate(self.sdef.attacks):
                if attack.attack_type != 'mine':
                    continue
                self.fire_ts[ai] -= dt
                if self.fire_ts[ai] <= 0:
                    self.fire_ts[ai] = attack.fire_interval
                    self._drop_mine(attack, projectiles)
        self._check_death(fac, effects)

    def _drop_mine(self, attack, projectiles):
        """Roll one mine off the stern: it sinks to the surface and arms there."""
        m = Projectile(self.x, self.mid_y, self.team, 0.0, 40.0, attack)
        m.armed = False; m.owner_id = self.id
        projectiles.append(m)

    def _update_burst(self, dt, ships, projectiles, fac, effects):
        """Fire a salvo as a series — one rocket every burst_interval — while the
        launcher holds station, then hand over to the rearm/scoot."""
        self.state = "fighting"
        self.burst_timer -= dt
        if self.burst_timer <= 0 and self.burst_left > 0:
            tgt = self.burst_target
            if tgt is None or not getattr(tgt, 'alive', False):
                tgt = self._pick_target(ships, self.sdef.attacks[self.burst_attack_idx],
                                        self.team == "player")
                self.burst_target = tgt
            if tgt is not None:
                self._fire_one_rocket(self.sdef.attacks[self.burst_attack_idx], tgt,
                                      projectiles, effects)
            self.burst_left -= 1
            self.burst_timer = self.sdef.burst_interval
        if self.burst_left <= 0:
            self.bursting = False
            if self.sdef.rearms:
                self.rearming = True; self.rearm_timer = self.sdef.rearm_time
        self._check_death(fac, effects)

    def _fire_one_rocket(self, attack, target, projectiles, effects=None):
        """Launch one rocket on a clean lofted ballistic arc onto the target,
        scattered a little so the salvo is satisfyingly inaccurate."""
        fp = self.sdef.fire_points[0] if self.sdef.fire_points else None
        flipped = self._is_flipped()
        rx = (1.0 - fp.rel_x) if (fp and flipped) else (fp.rel_x if fp else 0.5)
        ry = fp.rel_y if fp else 0.3
        fire_x = (self.x - self.disp_w * 0.5) + rx * self.disp_w
        fire_y = self.top_y + ry * self.disp_h
        spread = getattr(attack, 'scatter_spread', 200.0)
        tx = target.x + random.uniform(-spread * 0.5, spread * 0.5)   # impact scatter
        ty = target.mid_y
        bv = ballistic_velocity(tx - fire_x, ty - fire_y, attack.speed,
                                attack_gravity(attack, 240.0), high=True)
        if bv is None:                                  # out of arc range — lob toward it
            dirx = 1.0 if target.x >= self.x else -1.0
            bv = (math.cos(1.0) * attack.speed * dirx, -math.sin(1.0) * attack.speed)
        pr = Projectile(fire_x, fire_y, self.team, bv[0], bv[1], attack)
        pr.owner_id = self.id
        projectiles.append(pr)
        self._muzzle_smoke(attack, fire_x, fire_y, bv[0], bv[1], effects)

    def _update_rearm(self, dt):
        """Fire-and-scoot: after a salvo the unit backs off while reloading, then
        re-advances with its guns ready (it does NOT trek all the way home).

        With `retreat_turn` the hull turns ABOUT (its sprite mirrors) and drives
        forward toward home, rather than sliding backwards in reverse."""
        is_pl = self.team == "player"
        home_x = (FORT_D_W + self.disp_w) if is_pl else (WORLD_W - FORT_D_W - self.disp_w)
        self.state = "moving"
        self.retreat_flip = getattr(self.sdef, 'retreat_turn', False)
        self.x += (-1 if is_pl else 1) * self.sdef.speed * 0.85 * dt
        self.x = max(self.x, float(home_x)) if is_pl else min(self.x, float(home_x))
        self.rearm_timer -= dt
        if self.rearm_timer <= 0:
            self.rearming = False
            self.retreat_flip = False           # turn back to face the enemy
            self.fire_ts = [0.0] * len(self.sdef.attacks)

    def _update_entering(self, dt, ships=()):
        """Sail/fly in from off-screen at the unit's own speed, then hand over to
        normal AI once inside the playfield (no firing while still entering)."""
        is_pl = self.team == "player"
        spd = self.sdef.speed
        self.x += (1 if is_pl else -1) * spd * dt
        self.state = "moving"
        if getattr(self.sdef, 'unit_type', 'ship') == 'plane':
            # Hand over to combat AI at the plane's roam edge. That edge is normally
            # just past its own fort — but when an enemy is pressing the base it
            # opens up to overfly it (see _plane_bounds). Respect that here too, so
            # a plane flying in doesn't cruise (unable to fire) straight over an
            # enemy sitting on the base and only engage after it has cleared it.
            home_x, far_x = self._plane_bounds(ships)
            if (is_pl and self.x >= home_x) or (not is_pl and self.x <= far_x):
                self.entering = False
        else:
            if (is_pl and self.x >= FORT_D_W) or (not is_pl and self.x <= WORLD_W - FORT_D_W):
                self.entering = False

    def _pick_target(self, ships, attack, is_pl):
        """Choose a valid in-range target by the attack's priority (nearest, or
        'last' = the newest enemy by spawn id)."""
        omni = getattr(attack, 'omnidirectional', False)
        priority = getattr(attack, 'target_priority', 'near')
        best = None; best_score = None
        for s in self._candidates(attack.combat_range, ships):
            if s.team == self.team or not s.alive: continue
            if not omni:
                if is_pl and s.x <= self.x: continue
                if not is_pl and s.x >= self.x: continue
            if not attack_can_hit(attack, s): continue
            if getattr(s.sdef, 'unit_type', '') == 'plane' and not _reaches_air(attack):
                continue
            dist = self._edge_dist(s)
            if dist > attack.combat_range: continue
            # Ballistic anti-sub ordnance (depth charges) can only engage a target
            # its arc can actually reach onto the sea above it — otherwise the ship
            # would halt in "combat" and never manage to throw a charge that lands.
            if getattr(attack, 'ballistic', False) and not self._ballistic_reaches(attack, s):
                continue
            score = getattr(s, 'id', 0) if priority == 'last' else -dist
            if best_score is None or score > best_score:
                best_score = score; best = s
        return best

    def _edge_dist(self, s) -> float:
        """Horizontal gap measured from OUR furthermost point (the edge of this
        hull facing the target) to the nearest edge of the target's sprite — so a
        long ship ranges from its bow, not its centre, and reaches as soon as its
        leading edge is in range."""
        gap = abs(s.x - self.x)
        gap -= getattr(self, 'disp_w', 0.0) * 0.5      # our leading edge toward the target
        gap -= getattr(s, 'disp_w', 0.0) * 0.5         # the target's nearest edge
        return max(0.0, gap)

    def _ballistic_reaches(self, attack, target) -> bool:
        """True if a ballistic arc for `attack`, thrown from its chosen muzzle, can
        actually land on `target`. Depth charges aim at the waterline directly
        above the submerged target (where the carrier bursts and drops its sinking
        pattern); other ballistic ordnance aims at the target's mid-body. Mirrors
        the geometry in `_make_projectile` so targeting and firing agree."""
        fp = self._select_fire_point(attack, target.x)
        flipped   = self._is_flipped()
        actual_rx = (1.0 - fp.rel_x) if flipped else fp.rel_x
        fire_x = (self.x - self.disp_w * 0.5) + actual_rx * self.disp_w
        fire_y = self.top_y + fp.rel_y * self.disp_h
        aim_y  = self.water_y if attack.attack_type == "depth_charge" else target.mid_y
        g = attack_gravity(attack, 200.0 if attack.attack_type in ("bomb", "depth_charge") else 220.0)
        return ballistic_velocity(target.x - fire_x, aim_y - fire_y,
                                  attack.speed, g) is not None

    # ── Surface ship / submarine ───────────────────────────────────────────────
    def _update_surface(self, dt, ships, projectiles, effects, fac):
        is_pl     = self.team == "player"
        # The enemy base is a real target now: a unit advances until something
        # (a ship, the boss, or the base itself) comes into range, then fires.
        assault_x = (WORLD_W - FORT_D_W) if is_pl else FORT_D_W

        # A surface ship only HALTS for a surface/undersea target; it keeps
        # advancing while merely firing on aircraft (anti-air guns don't stop the
        # hull). `surface_combat` gates movement; `in_combat` only drives state.
        in_combat = False; surface_combat = False
        for ai, attack in enumerate(self.sdef.attacks):
            best = self._pick_target(ships, attack, is_pl)
            if best and self._edge_dist(best) <= attack.combat_range:
                in_combat = True
                if getattr(best.sdef, 'unit_type', 'ship') != 'plane':
                    surface_combat = True
                self.fire_ts[ai] -= dt
                if self.fire_ts[ai] <= 0:
                    self.fire_ts[ai] = attack.fire_interval
                    if self.sdef.burst_count > 1:     # MLRS: fire a series, then rearm
                        self.bursting = True
                        self.burst_left = self.sdef.burst_count
                        self.burst_timer = 0.0
                        self.burst_attack_idx = ai
                        self.burst_target = best
                        return
                    self._fire_indices(attack, best, projectiles, effects)
                    if self.sdef.rearms:           # salvo fired → go rearm
                        self.rearming = True
                        self.rearm_timer = self.sdef.rearm_time
                        return

        # Momentum: roll toward full speed when advancing, ease to 0 when holding
        # to fire — so a unit keeps creeping for a moment after it opens up and
        # decelerates smoothly rather than stopping instantly.
        advancing = (not surface_combat) or getattr(self.sdef, 'advance_while_firing', False)
        target_v  = ((1 if is_pl else -1) * self.sdef.speed) if advancing else 0.0
        self.move_vel += (target_v - self.move_vel) * min(1.0, dt * 1.8)
        self.x += self.move_vel * dt
        # Hold at the assault line — never drive onto or past the base.
        self.x = min(self.x, float(assault_x)) if is_pl else max(self.x, float(assault_x))
        at_base = (self.x == float(assault_x))
        self.state = ("assaulting" if (at_base and in_combat)
                      else "fighting" if in_combat else "moving")
        self._check_death(fac, effects)

    def _fire_indices(self, attack, best, projectiles, effects=None):
        """Fire the auto-selected best muzzle ([-1]) or every listed muzzle.

        For plain direction-flown guns (a bullet flies the way its muzzle points)
        we skip muzzles aimed away from the target, so they never fire backwards.
        Target-seeking attacks (ballistic / homing / tracking / flak / omni) head
        for the target regardless of muzzle orientation, so all of them fire.
        """
        indices = attack.fire_point_indices
        if indices == [-1]:
            self._fire_attack(attack, self._select_fire_point(attack, best.x), best,
                              projectiles, effects)
            return
        aims_at_target = (getattr(attack, 'ballistic', False) or getattr(attack, 'homing', False)
                          or getattr(attack, 'track_target', False) or attack.omnidirectional
                          or getattr(attack, 'gun_angle', 0.0) > 0.0
                          or attack.attack_type in ('flak', 'bomb'))
        flipped = self._is_flipped()
        target_dir = 1 if best.x > self.x else -1
        valid = [i for i in indices if 0 <= i < len(self.sdef.fire_points)]
        fired = False
        for i in valid:
            fp = self.sdef.fire_points[i]
            adx = -fp.dir_x if flipped else fp.dir_x
            if aims_at_target or fp.dir_x == 0 or adx * target_dir > 0:
                self._fire_attack(attack, fp, best, projectiles, effects); fired = True
        if not fired and valid:
            self._fire_attack(attack, self._select_fire_point(attack, best.x), best,
                              projectiles, effects)

    # ── Manually-aimed base battery (Coastal Artillery) ─────────────────────────
    def _update_manual_turret(self, dt, projectiles, fac, effects):
        self.state = "fighting"
        for ai, attack in enumerate(self.sdef.attacks):
            self.fire_ts[ai] -= dt
            if self.fire_ts[ai] <= 0:
                self.fire_ts[ai] = attack.fire_interval
                self._fire_aimed(attack, projectiles, effects)
        self._check_death(fac, effects)

    def _barrel_tip(self):
        """World (x, y) of the muzzle end of a drawn elevating barrel. Replays
        _draw_barrel's translate→mirror→rotate transform on the far end of the
        barrel sprite (the art points RIGHT, so the muzzle is its right edge at
        the vertical centre) — the point where smoke should erupt. None when the
        unit has no barrel art loaded."""
        sd = self.sdef
        if not getattr(sd, 'barrel_file', None) or not getattr(sd, 'barrel_disp', None):
            return None
        base_mirror = self._is_flipped()
        pvx, pvy = sd.barrel_pivot
        eff = (1.0 - pvx) if base_mirror else pvx
        px = (self.x - self.disp_w * 0.5) + eff * self.disp_w
        py = self.top_y + pvy * self.disp_h
        ax, ay = sd.barrel_anchor
        bw, bh = sd.barrel_disp
        lx = (1.0 - ax) * bw                 # muzzle: barrel-local, right of the anchor
        ly = (0.5 - ay) * bh
        c, s = math.cos(self.aim_angle), math.sin(self.aim_angle)
        rx = lx * c + ly * s                 # rotate(-aim_angle), screen y down
        ry = -lx * s + ly * c
        mirror = base_mirror if self.aim_mirror is None else self.aim_mirror
        return (px - rx if mirror else px + rx), py + ry

    def _fire_aimed(self, attack, projectiles, effects=None):
        """Lob a shell along the player-set elevation (self.aim_angle)."""
        fp = self.sdef.fire_points[0] if self.sdef.fire_points else None
        flipped = self._is_flipped()
        rx = (1.0 - fp.rel_x) if (fp and flipped) else (fp.rel_x if fp else 0.5)
        ry = fp.rel_y if fp else 0.1
        fire_x = (self.x - self.disp_w * 0.5) + rx * self.disp_w
        fire_y = self.top_y + ry * self.disp_h
        ang = self.aim_angle
        dirx = 1.0 if self.team == "player" else -1.0
        vx = math.cos(ang) * attack.speed * dirx
        vy = -math.sin(ang) * attack.speed
        pr = Projectile(fire_x, fire_y, self.team, vx, vy, attack)
        pr.owner_id = self.id
        projectiles.append(pr)
        mx, my = self._barrel_tip() or (fire_x, fire_y)
        self._muzzle_smoke(attack, mx, my, vx, vy, effects)

    # ── Rear-guard reach ────────────────────────────────────────────────────────
    def _rear_range_bonus(self) -> float:
        """Extra combat range earned by seating a turret far behind its base's
        waterline front line. Each pixel of setback behind that line adds
        TURRET_REAR_RANGE_PER_PX of range; forward-mounted turrets (and every
        mobile unit) get nothing. The turret is static, so this is constant once
        placed — a battery high up the staircase reaches further than one at the
        front pad, trading forward coverage for reach."""
        if getattr(self.sdef, 'unit_type', 'ship') != 'turret':
            return 0.0
        front_x = FORT_D_W if self.team == 'player' else (WORLD_W - FORT_D_W)
        setback = (front_x - self.x) if self.team == 'player' else (self.x - front_x)
        if setback <= 0.0:
            return 0.0
        return setback * TURRET_REAR_RANGE_PER_PX

    # ── Static turret ──────────────────────────────────────────────────────────
    def _update_turret(self, dt, ships, projectiles, effects, fac):
        if getattr(self.sdef, 'manual_aim', False):
            self._update_manual_turret(dt, projectiles, fac, effects); return
        in_combat = False
        rear_bonus = self._rear_range_bonus()     # extra reach for a far-back mount
        aim_target = None; aim_d = float('inf')   # nearest live target: barrel tracks it
        for ai, attack in enumerate(self.sdef.attacks):
            reach = attack.combat_range + rear_bonus
            best = None; best_d = float('inf')
            for s in self._candidates(reach, ships):
                if s.team == self.team or not s.alive: continue
                if not attack_can_hit(attack, s): continue
                d = self._edge_dist(s)
                if d < best_d: best_d, best = d, s
            if best and best_d <= reach and best_d < aim_d:
                aim_d = best_d; aim_target = best
            if best and best_d <= reach:
                in_combat = True; self.fire_ts[ai] -= dt
                if self.fire_ts[ai] <= 0:
                    self.fire_ts[ai] = attack.fire_interval
                    indices = attack.fire_point_indices
                    if indices == [-1]:
                        fp = self._select_fire_point(attack, best.x)
                    elif self.sdef.fire_points:
                        idx = indices[0] if indices[0] >= 0 else 0
                        fp = self.sdef.fire_points[min(idx, len(self.sdef.fire_points) - 1)]
                    else:
                        continue
                    self._fire_attack(attack, fp, best, projectiles, effects)
        if getattr(self.sdef, 'barrel_file', None):
            self._update_aim(dt, aim_target)
        self.state = "fighting" if in_combat else "moving"
        self._check_death(fac, effects)

    def _update_aim(self, dt, target):
        """Swivel/elevate an auto-tracking barrel (the AA gun) onto its target,
        easing the elevation and mirroring the barrel to the target's side. With
        no target the barrel eases back to its resting elevation."""
        sd = self.sdef
        base_mirror = self._is_flipped()
        pvx, pvy = sd.barrel_pivot
        eff = (1.0 - pvx) if base_mirror else pvx
        gx = (self.x - self.disp_w * 0.5) + eff * self.disp_w
        gy = self.top_y + pvy * self.disp_h
        if target is not None:
            dx = target.x - gx
            dy = target.mid_y - gy
            ang = math.atan2(-dy, max(1.0, abs(dx)))     # elevation above horizontal
            ang = max(0.05, min(1.48, ang))
            if abs(dx) > 6.0:
                self.aim_mirror = (dx < 0.0)
        else:
            ang = getattr(sd, 'barrel_rest', 0.6)
            self.aim_mirror = base_mirror
        self.aim_angle += (ang - self.aim_angle) * min(1.0, dt * 7.0)

    # ── Aircraft ────────────────────────────────────────────────────────────────
    def _update_plane(self, dt, ships, projectiles, effects, fac):
        if self.rising:
            # Launched off the carrier already at forward speed, so it pulls clear
            # of the (same-coloured) deck immediately instead of rising in place.
            self.x += self.facing * self.sdef.speed * dt
            return
        at = getattr(self.sdef, 'aircraft_type', 'loop')
        is_pl = self.team == "player"

        # Find / keep a target
        target = None
        if self.plane_target_id >= 0:
            for s in ships:
                if s.id == self.plane_target_id and s.alive and s.team != self.team:
                    target = s; break
        if target is None:
            best_d = float('inf'); prev_id = self.plane_target_id
            for s in ships:
                if s.team == self.team or not s.alive: continue
                if not any(attack_can_hit(a, s) for a in self.sdef.attacks): continue
                d = self._edge_dist(s)
                if d < best_d: best_d, target = d, s
            self.plane_target_id = target.id if target else -1
            if target is not None and self.plane_target_id != prev_id:
                self.standoff_mult = random.uniform(0.60, 0.95)   # new engagement → new standoff

        home_x, far_x = self._plane_bounds(ships)
        orbit_r = 280
        spd = self.sdef.speed

        # ── Choose heading (plane_dir). Always turn at the playfield edges so a
        #    plane can never wedge against the base and freeze. ─────────────────
        if self.x >= far_x:
            self.plane_dir = -1
        elif self.x <= home_x:
            self.plane_dir = 1
        elif at == 'loop':                 # strafe back and forth across the target
            cx = target.x if target else (home_x + 800 if is_pl else far_x - 800)
            if self.x > cx + orbit_r:   self.plane_dir = -1
            elif self.x < cx - orbit_r: self.plane_dir = 1
        elif at == 'standoff':             # close to drop, then peel back from the front
            adv = 1 if is_pl else -1       # toward the enemy front
            if target:
                dx = target.x - self.x
                near = 250.0 * self.standoff_mult     # randomised per engagement
                far  = 1250.0 * self.standoff_mult
                if abs(dx) <= near:        # overhead the enemy → run back home
                    self.standoff_retreat = True
                elif abs(dx) >= far:       # opened up enough → make another run
                    self.standoff_retreat = False
                self.plane_dir = (-adv) if self.standoff_retreat else (1 if dx > 0 else -1)
            else:
                self.standoff_retreat = False
                self.plane_dir = adv       # no target: drift toward the front
        # 'carpet' only ever turns at the edges (handled above)

        if at == 'hover':
            # Helicopters keep distance with a SMOOTH proportional velocity (see
            # _hover_kite): speed scales with how far off the set standoff they are,
            # so they ease to a stop at range instead of lurching back and forth.
            self._hover_kite(dt, target, home_x, far_x, orbit_r, ships)
        else:
            # Smooth banking turn (long, no velocity snap): the visual heading eases
            # toward plane_dir, and MOVEMENT follows `facing` — so the plane glides
            # to a stop, banks through edge-on, then accelerates the other way.
            self.facing += (self.plane_dir - self.facing) * min(1.0, dt * 1.7)
            self.x += self.facing * spd * dt
            self.x = self._clamp_plane_x(self.x)

        # Gentle altitude bob → pitch in draw (cruise level set by battle Y-sync)
        self.y_off_timer -= dt
        if self.y_off_timer <= 0:
            self.y_off_timer = random.uniform(2.5, 6.0)
            max_d = 10 if at == 'carpet' else 18 if at == 'loop' else 30
            self.y_off_target = random.uniform(-max_d, max_d)
        self.y_off += (self.y_off_target - self.y_off) * 1.2 * dt

        # Fire weapons
        in_combat = False
        for ai, attack in enumerate(self.sdef.attacks):
            # Fixed forward guns (e.g. the fighter's twin MGs) shoot straight down
            # the nose: they only fire when flying level (NOT mid-turn) and only at
            # a target that is ahead in the facing direction.
            forward_gun = (attack.attack_type == 'bullet'
                           and not getattr(attack, 'track_target', False)
                           and not getattr(attack, 'omnidirectional', False))
            best = None; best_d = float('inf')
            for s in self._candidates(attack.combat_range, ships):
                if s.team == self.team or not s.alive: continue
                if not attack_can_hit(attack, s): continue
                d = self._edge_dist(s)
                if d < best_d: best_d, best = d, s
            if best and best_d <= attack.combat_range:
                in_combat = True
                if forward_gun:
                    turning = abs(self.facing) < 0.85
                    ahead   = (best.x - self.x) * self.facing > 0
                    if turning or not ahead:
                        continue                      # can't shoot while banking / target behind
                self.fire_ts[ai] -= dt
                if self.fire_ts[ai] <= 0:
                    self.fire_ts[ai] = attack.fire_interval
                    if self.sdef.fire_points:
                        idx = attack.fire_point_indices
                        if forward_gun and idx != [-1]:
                            pts = [i for i in idx if 0 <= i < len(self.sdef.fire_points)] or [0]
                        else:
                            pts = [idx[0] if idx != [-1] and idx else 0]
                        for fpi in pts:               # both barrels for twin guns
                            self._fire_attack(attack, self.sdef.fire_points[fpi], best,
                                              projectiles, effects)
        self.state = "fighting" if in_combat else "moving"
        self._check_death(fac, effects)

    def _plane_bounds(self, ships):
        """The x-range a plane may roam. Normally it turns at its base edges, but
        when an enemy presses in close to a base the plane is allowed to overfly
        that base and slip OFF-SCREEN past the map edge (then loop back) instead
        of wedging against the fort."""
        home = float(FORT_D_W + self.disp_w)
        far  = float(WORLD_W - FORT_D_W - self.disp_w)
        THRESH = 820.0
        near_player_base = near_enemy_base = False
        for s in ships:
            if s.team == self.team or not s.alive: continue
            if getattr(s, 'is_base', False) or getattr(s, 'is_rig', False): continue
            if s.x < FORT_D_W + THRESH:               near_player_base = True
            if s.x > WORLD_W - FORT_D_W - THRESH:      near_enemy_base  = True
        lo = (-self.disp_w - 40.0) if near_player_base else home
        hi = (WORLD_W + self.disp_w + 40.0) if near_enemy_base else far
        return lo, hi

    def _clamp_plane_x(self, x):
        """Last-resort safety clamp for a plane's x. Uses FIXED off-screen walls,
        NOT the turn-around bounds from _plane_bounds — those flip inward when an
        enemy retreats from a base, and clamping against them would teleport a
        plane that had legitimately flown off-screen back onto the field edge in
        one frame. The turn logic (plane_dir) already flies such a plane home
        smoothly; these walls only catch a genuine runaway, well past where the
        plane would ever turn, so they never cause a visible snap."""
        lo = -self.disp_w - 120.0
        hi = WORLD_W + self.disp_w + 120.0
        return max(lo, min(hi, x))

    def _hover_kite(self, dt, target, home_x, far_x, orbit_r, ships=()):
        """Smoothly hold a standoff a bit inside max range. Velocity scales with
        the distance error, so the helo eases to a stop at the set range instead
        of lurching toward/away; the nose tracks the target so backing off never
        flips the sprite."""
        spd = self.sdef.speed
        if target is None:
            # No valid target (e.g. an anti-sub helo with no subs around) → keep
            # advancing toward the enemy front instead of hovering in place.
            fwd = 1.0 if self.team == 'player' else -1.0
            self.facing += (fwd - self.facing) * min(1.0, dt * 2.5)
            self.x += fwd * spd * dt
            self.x  = self._clamp_plane_x(self.x)
            return
        rng = max((a.combat_range for a in self.sdef.attacks
                   if attack_can_hit(a, target)), default=orbit_r)
        # Per-helo standoff (randomised each engagement) + a lateral bias, so a
        # group spreads out across a band instead of stacking into one clump.
        desired = rng * self.standoff_mult + self.hover_jitter
        dx      = target.x - self.x
        toward  = 1.0 if dx >= 0 else -1.0
        err     = abs(dx) - desired                       # + too far → close in, − too close → back off
        frac    = max(-1.0, min(1.0, err / 130.0))        # proportional; eases to 0 near the set range
        sep     = self._hover_separation(ships)           # push off nearby same-type helos
        self.x += (toward * frac * spd + sep) * dt
        self.x  = self._clamp_plane_x(self.x)
        self.facing += (toward - self.facing) * min(1.0, dt * 2.5)   # keep the nose on the target

    def _hover_separation(self, ships) -> float:
        """A gentle shove away from a too-close friendly helo so they don't pile
        onto the exact same spot."""
        push = 0.0
        for s in self._candidates(95.0, ships):
            if s is self or s.team != self.team or not s.alive:
                continue
            if getattr(s.sdef, 'aircraft_type', '') != 'hover':
                continue
            gap = self.x - s.x
            if abs(gap) < 95.0:
                push += (1.0 if gap >= 0 else -1.0) * (95.0 - abs(gap)) * 3.0
        return max(-220.0, min(220.0, push))

    # ── Death ─────────────────────────────────────────────────────────────────
    def _check_death(self, fac, effects):
        # The death explosion is settled centrally once all damage for the frame is
        # in (see BattleCanvas._process_deaths). Here we only mark the unit destroyed.
        if self.hp <= 0:
            self.alive = False

    # ── Draw ──────────────────────────────────────────────────────────────────
    def draw(self, p: QPainter, cam_x: float, sprites: dict, night: float = 0.0):
        if not self.alive: return
        sx = int(self.x - self.disp_w * 0.5 - cam_x)
        sy = int(self.top_y)
        ut = getattr(self.sdef, 'unit_type', 'ship')
        spr = sprites.get(f"{self.team}_{self.sdef.key}")

        # At the dead of night a near-black player hull reads as a hole in the dark
        # sea. Lay a moonlit rim around the sprite so every hull keeps a lit edge —
        # drawn under the sprite so only the protruding fringe shows. Restricted to
        # the full-night bands (NIGHT/ABYSS, night==1.0); dusk and sunset levels
        # keep their hulls unlit. Player-toggleable (SETTINGS.night_silhouette).
        if night >= 0.99 and ut != 'turret' and SETTINGS.night_silhouette:
            glow = sprites.get(f"{self.team}_{self.sdef.key}_glow")
            if glow is not None and not glow.isNull():
                self._draw_night_rim(p, glow, sx, sy, ut, night)

        # Enemy (red) hulls fade toward the dark as the level's time-of-day band
        # darkens: lowering their opacity blends them into the ever-darker night
        # sea (game.ui.backdrop), so the enemy fleet reads dimmer the later the
        # hour while the player's own ships stay at full brightness.
        dim = 1.0 - 0.45 * night if self.team == 'enemy' else 1.0
        if dim < 1.0:
            p.save()
            p.setOpacity(dim)

        self._blit(p, spr, sx, sy, ut)

        # The barrel rides on top of the base only in normal (unmirrored,
        # non-plane) orientation — matching where _blit drew the hull upright.
        normal_orient = (ut != 'plane' or self.rising) and not self.retreat_flip
        if normal_orient and getattr(self.sdef, 'barrel_file', None):
            self._draw_barrel(p, sx, sy, sprites)

        if dim < 1.0:
            p.restore()

        self._draw_hpbar(p, sx, sy)
        self._draw_reloadbar(p, sx, sy)

    def _blit(self, p: QPainter, spr, sx: int, sy: int, ut: str,
              dx: float = 0.0, dy: float = 0.0):
        """Draw `spr` (or the placeholder if it's missing) in this unit's current
        orientation, shifted by an optional (dx, dy) screen offset used to stamp
        the night rim around the hull."""
        cx = sx + self.disp_w // 2; cy = sy + self.disp_h // 2
        if ut == 'plane' and not self.rising:
            # The cached sprite faces the team's forward direction. Mirror it by
            # `facing`, which eases between +1/-1 during a turn — passing through
            # ~0 (edge-on) for a smooth bank instead of a snap or spin.
            forward = 1.0 if self.team == 'player' else -1.0
            scale_x = self.facing * forward
            if abs(scale_x) < 0.06:
                scale_x = 0.06 if scale_x >= 0 else -0.06
            # Subtle pitch from climb/dive (nose follows travel direction)
            pitch = max(-13.0, min(13.0, (self.y_off_target - self.y_off) * 0.5))
            p.save(); p.translate(cx + dx, cy + dy)
            p.rotate(pitch)
            p.scale(scale_x, 1.0)
            if spr and not spr.isNull():
                p.drawPixmap(-self.disp_w // 2, -self.disp_h // 2, spr)
            else:
                self._placeholder(p, -self.disp_w // 2, -self.disp_h // 2)
            p.restore()
        elif self.retreat_flip:
            # Hull turned about (fire-and-scoot retreat): mirror the sprite so it
            # faces home and drives forward instead of sliding backwards.
            p.save(); p.translate(cx + dx, cy + dy); p.scale(-1.0, 1.0)
            if spr and not spr.isNull():
                p.drawPixmap(-self.disp_w // 2, -self.disp_h // 2, spr)
            else:
                self._placeholder(p, -self.disp_w // 2, -self.disp_h // 2)
            p.restore()
        else:
            if spr and not spr.isNull():
                p.drawPixmap(QPoint(int(sx + dx), int(sy + dy)), spr)
            else:
                self._placeholder(p, int(sx + dx), int(sy + dy))

    def _draw_night_rim(self, p: QPainter, glow, sx: int, sy: int, ut: str,
                        night: float):
        """Stamp the moonlit silhouette around the hull in eight directions to
        form an even lit outline. Only the full-night bands (night==1.0) reach
        here, so black hulls separate from the dark sea at the dead of night
        without any hull glowing in dusk, sunset or daylight fights."""
        if night < 0.99:
            return
        r = 2.2
        dirs = ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0),
                (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7))
        p.save()
        p.setOpacity(0.16)
        for ox, oy in dirs:
            self._blit(p, glow, sx, sy, ut, ox * r, oy * r)
        p.restore()

    def _draw_barrel(self, p, sx, sy, sprites):
        """Draw the elevating barrel on top of the base: seat its anchor on the
        base pivot, mirror to the aimed side, then rotate by the elevation angle
        (screen y is down, so a positive elevation rotates counter-clockwise)."""
        sd  = self.sdef
        spr = sprites.get(f"{self.team}_{sd.key}_barrel")
        if not spr or spr.isNull():
            return
        base_mirror = self._is_flipped()
        pvx, pvy = sd.barrel_pivot
        eff = (1.0 - pvx) if base_mirror else pvx
        px  = sx + eff * self.disp_w
        py  = sy + pvy * self.disp_h
        mirror = base_mirror if self.aim_mirror is None else self.aim_mirror
        ax, ay = sd.barrel_anchor
        bw, bh = sd.barrel_disp if sd.barrel_disp else (spr.width(), spr.height())
        p.save()
        p.translate(px, py)
        if mirror:
            p.scale(-1.0, 1.0)
        p.rotate(-math.degrees(self.aim_angle))
        p.drawPixmap(int(-ax * bw), int(-ay * bh), spr)
        p.restore()

    def _placeholder(self, p, x, y):
        p.fillRect(x, y, self.disp_w, self.disp_h, QColor("#000000"))
        p.setPen(QColor("#aaaaaa")); p.setFont(QFont("Arial", 7))
        p.drawText(x, y, self.disp_w, self.disp_h,
                   Qt.AlignmentFlag.AlignCenter, self.sdef.name)

    def _draw_hpbar(self, p, sx, sy):
        bw, bh = 60, 5; bx = sx + self.disp_w // 2 - bw // 2; by = sy - 9
        f = max(0.0, self.hp / self.max_hp)
        p.fillRect(bx, by, bw, bh, QColor("#3a3a3a"))
        p.fillRect(bx, by, round(bw * f), bh,
                   QColor("#33bb33") if f > .6 else
                   QColor("#ddaa00") if f > .3 else QColor("#dd2222"))
        p.setPen(QPen(QColor("#555555"), 0.8)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(bx, by, bw, bh)
        if self.state in ("fighting", "assaulting"):
            p.setBrush(QColor(255, 170, 0, 200)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(sx + self.disp_w // 2 - 3, by - 8, 6, 6)

    def _draw_reloadbar(self, p, sx, sy):
        """A slim bar above the HP bar for units that reload between salvoes:
        it DRAINS as a burst empties its tubes (ammo depletion), then FILLS back
        up as the unit reloads (reload progress)."""
        bc = max(1, getattr(self.sdef, 'burst_count', 1))
        rt = getattr(self.sdef, 'rearm_time', 0.0)
        if self.bursting:                                  # ammo emptying
            frac = max(0.0, min(1.0, self.burst_left / bc)); col = QColor("#ffcc33")
        elif self.rearming and rt > 0:                     # reloading
            frac = max(0.0, min(1.0, 1.0 - self.rearm_timer / rt)); col = QColor("#4aa3ff")
        elif self.rearming:
            frac = 0.0; col = QColor("#4aa3ff")
        else:
            return
        bw, bh = 60, 4
        bx = sx + self.disp_w // 2 - bw // 2; by = sy - 16
        p.fillRect(bx, by, bw, bh, QColor("#222a33"))
        p.fillRect(bx, by, round(bw * frac), bh, col)
        p.setPen(QPen(QColor("#11161c"), 0.8)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(bx, by, bw, bh)
