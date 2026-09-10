"""
game.entities.projectile – flying ordnance and its per-type behaviour/rendering.
"""

from __future__ import annotations
import math, random
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui  import (QPainter, QColor, QPen, QRadialGradient,
                          QLinearGradient, QPainterPath)

from ..config import WORLD_W
from ..models import Attack
from .explosion import Explosion, LaunchSmoke, Splash

# Ordnance is culled once it leaves the world, but with a generous margin on each
# side. A boss opens fire from BEYOND the right edge (it spawns at
# WORLD_W + display_w*0.6 and fires as it sails in), so its shells must be allowed
# to exist off-map and travel inward rather than being killed the instant they spawn.
PROJ_EDGE_MARGIN = 1000.0

# A round that reaches the end of its lifetime doesn't vanish on a single frame
# (a jarring "pop"); it coasts on inertly for this long while its opacity ramps
# to zero, then it is culled.
FADE_TIME = 0.35


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """Linear blend a→b by t (0..1), used to fade a round's day look into its
    night look – a dark metal shell into a white-hot incandescent orb."""
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return QColor(int(a.red()   + (b.red()   - a.red())   * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue()  + (b.blue()  - a.blue())  * t))


def attack_gravity(attack, default: float) -> float:
    """Downward acceleration (px/s²) for an arcing round. A positive `gravity`
    field in the attack's JSON overrides the built-in per-type default, so a
    weapon's shell arc can be tuned without code changes. Launch (ship.py) and
    flight (here) read the same value, so a shell still lands where it aimed."""
    g = getattr(attack, 'gravity', 0.0)
    return float(g) if g and g > 0 else float(default)


def ship_hit(s, x, y) -> bool:
    """True if world point (x, y) strikes target `s`. Real units expose a
    hull/superstructure `hit_test` (so rounds pass through masts and empty space);
    bases/rigs and any other bare target fall back to a padded bounding box."""
    ht = getattr(s, 'hit_test', None)
    if ht is not None:
        return ht(x, y)
    hw = s.sdef.display_w * 0.52; hh = s.sdef.display_h * 0.70
    return abs(x - s.x) < hw and abs(getattr(s, 'mid_y', y) - y) < hh


def apply_damage(ship, dmg) -> None:
    """Deal `dmg` to `ship`, unless it is invulnerable. An immune base (boss
    alive) still stops and absorbs the round – it simply takes no damage. The
    caller consumes the projectile either way, so an immune base "eats" hits."""
    if getattr(ship, 'invulnerable', False):
        return                             # e.g. an immune base while the boss lives
    # A target may be extra-fragile right now (e.g. a Bastion still under
    # construction takes 400% damage); default multiplier is 1.0.
    ship.hp -= dmg * getattr(ship, 'damage_mult', 1.0)
    if ship.hp <= 0:
        ship.hp = 0; ship.alive = False


def attack_can_hit(attack, ship) -> bool:
    """True if `attack` can strike `ship` (medium + tag filter). Note this
    governs targeting/collision, not damage: an immune base is still a valid
    target and blocks rounds (see `apply_damage`), it just takes no damage."""
    ut = getattr(ship.sdef, 'unit_type', 'ship')
    # An underwater-slot turret (the Torpedo Battery) sits below the waterline on
    # the fort's undersea staircase, so it shares the submarine hit-medium: only
    # anti-sub weapons can reach it, exactly as the HUD/tutorial present it.
    underwater = (ut == 'submarine' or
                  (ut == 'turret' and
                   getattr(ship.sdef, 'turret_slot', 'surface') == 'underwater'))
    if ut == 'plane':
        if not getattr(attack, 'can_hit_plane', False): return False
    elif underwater:
        if not getattr(attack, 'can_hit_sub', False): return False
    else:
        if not getattr(attack, 'can_hit_surface', True): return False
    tags = getattr(attack, 'target_tags', [])
    if tags:
        stags = getattr(ship.sdef, 'tags', [])
        if not any(t in stags for t in tags): return False
    return True


class Projectile:
    __slots__ = ("x", "y", "team", "vx", "vy", "attack", "alive", "age",
                 "fired_downward", "armed", "owner_id", "last_dist", "last_hit_id",
                 "_effects", "fade_t")

    def __init__(self, x, y, team, vx, vy, attack):
        self.x = x; self.y = y; self.team = team
        self.vx = vx; self.vy = vy; self.attack = attack
        self.alive = True; self.age = 0.0
        # True when launched downward (air/surface drop), False when horizontal.
        self.fired_downward = (vy > 0.5)
        # Torpedoes: True once the round has entered the water (its environment).
        self.armed = False
        # id of the ship that fired this (so it never collides with its own muzzle)
        self.owner_id = -1
        # flak: previous distance to its target, for closest-approach detonation
        self.last_dist = None
        # piercing rounds: id of the last target damaged (avoid re-hitting it)
        self.last_hit_id = -999
        # effects list handed in each frame by update() (for on-hit flashes)
        self._effects = None
        # None until the round times out; then a countdown of remaining fade
        # seconds, during which it coasts inertly and draws ever fainter.
        self.fade_t = None

    def _can_hit(self, ship) -> bool:
        return attack_can_hit(self.attack, ship)

    def _begin_fade(self) -> None:
        """Start the timed-out fade-out instead of vanishing this frame. The
        round stops hitting / homing and just coasts on its last heading while
        draw() ramps its opacity to zero. Idempotent – never restarts a fade."""
        if self.fade_t is None:
            self.fade_t = FADE_TIME

    # ── Naval mine: tossed out from the base, lands, then detonates on contact ──
    def _update_mine(self, dt, ships, water_y):
        if not self.armed:                              # still in the air after the throw
            self.vy += attack_gravity(self.attack, 300.0) * dt
            self.x += self.vx * dt; self.y += self.vy * dt
            if self.y >= water_y or not (-PROJ_EDGE_MARGIN <= self.x <= WORLD_W + PROJ_EDGE_MARGIN):
                self.y = water_y; self.vx = self.vy = 0.0; self.armed = True
            return
        ml = getattr(self.attack, 'max_lifetime', 0.0)
        if ml > 0 and self.age > ml:
            self._begin_fade(); return
        R = 8.0                                         # the mine's own contact radius
        for s in ships:
            if s.team == self.team or not s.alive: continue
            if getattr(s, 'id', None) == self.owner_id: continue
            if not self._can_hit(s): continue
            # Coarse pass: skip ships whose padded frame can't touch the mine.
            if abs(s.x - self.x) >= s.disp_w * 0.52 + R or abs(s.mid_y - self.y) >= s.disp_h * 0.70 + R:
                continue
            # Contact pass: detonate when any point of the mine's rim (or its
            # centre) touches the hull silhouette – bow/stern included, not
            # just the centre of mass.
            for px, py in ((self.x, self.y),
                           (self.x - R, self.y), (self.x + R, self.y),
                           (self.x, self.y - R), (self.x, self.y + R)):
                if s.hit_test(px, py):
                    apply_damage(s, self.attack.damage)
                    if self._effects is not None:
                        self._effects.append(Explosion(self.x, self.y))
                    self.alive = False; return

    # ── Depth charge: a single arcing carrier that splits when it hits the sea ──
    def _update_dc_carrier(self, dt, water_y, spawn):
        self.vy += attack_gravity(self.attack, 200.0) * dt   # gravity arc toward the target
        self.x += self.vx * dt; self.y += self.vy * dt
        # The carrier is a barrel lobbed at the submarine; it bursts into the
        # sinking charge pattern the instant it strikes the sea. Splitting AT the
        # water surface – centred on the carrier's own position – makes the charges
        # drop exactly where the projectile hits. (It was previously released a
        # little above the water and re-centred on a stored target x, which left
        # the pattern visibly detached from the round that dropped it.)
        if self.vy > 0 and self.y >= water_y:
            self._split_depth(spawn, water_y)
            self.alive = False; return
        if not (-PROJ_EDGE_MARGIN <= self.x <= WORLD_W + PROJ_EDGE_MARGIN) or self.y > 4000:
            self.alive = False

    def _split_depth(self, spawn, water_y):
        """Release N evenly-spaced charges that slowly sink (subs only), centred on
        the carrier's splash point (self.x) so the pattern lands where the round
        struck the water."""
        if spawn is None: return
        n = max(2, getattr(self.attack, 'scatter_count', 10))
        spread = getattr(self.attack, 'scatter_spread', 140.0)
        cx = self.x
        for i in range(n):
            frac = (i / (n - 1)) - 0.5                 # -0.5 .. 0.5, evenly spaced
            b = Projectile(cx + frac * spread, water_y - 6.0, self.team,
                           0.0, random.uniform(34.0, 46.0), self.attack)
            b.armed = True                             # a bomblet – won't split again
            b.owner_id = self.owner_id
            spawn.append(b)

    # ── Flak: home AT the nearest aircraft, burst at closest approach ─────────
    def _update_flak(self, dt, ships, spawn):
        # Find the nearest aircraft this round can hit and steer toward it.
        target = None; td = float('inf')
        for s in ships:
            if s.team == self.team or not s.alive: continue
            if not self._can_hit(s): continue
            d = math.hypot(s.x - self.x, s.mid_y - self.y)
            if d < td: td, target = d, s

        spd = max(math.hypot(self.vx, self.vy), self.attack.speed * 0.7)
        if target is not None:
            dx = target.x - self.x; dy = target.mid_y - self.y
            dist = math.hypot(dx, dy) or 1.0
            cur = math.hypot(self.vx, self.vy) or 1.0
            nx, ny = self.vx / cur, self.vy / cur
            turn = 9.0 * dt                      # strong tracking, aimed AT the plane
            nx += (dx / dist) * turn; ny += (dy / dist) * turn
            m = math.hypot(nx, ny) or 1.0
            self.vx = nx / m * spd; self.vy = ny / m * spd
        else:
            self.vy += 60.0 * dt                 # no target: arc down and time out

        # Burst within lethal radius, or once we pass the closest point of approach.
        burst = False
        if target is not None:
            if td <= 46.0:
                burst = True
            elif self.last_dist is not None and td > self.last_dist and self.last_dist < 150:
                burst = True
        self.last_dist = td if target is not None else None

        fuse = getattr(self.attack, 'fuse', 0.0)
        ml   = getattr(self.attack, 'max_lifetime', 0.0)
        if not burst and ((fuse > 0 and self.age >= fuse) or (ml > 0 and self.age > ml)):
            burst = True

        if burst:
            self._detonate(ships, spawn); return

        self.x += self.vx * dt; self.y += self.vy * dt
        if not (-PROJ_EDGE_MARGIN <= self.x <= WORLD_W + PROJ_EDGE_MARGIN) or self.y > 4000 or self.y < -800:
            self.alive = False

    def _detonate(self, ships, spawn):
        """Blast damage to aircraft in radius + a ring of visible shrapnel.

        Damage is proximity-scaled: a near-direct hit is devastating (enough to
        one-shot light aircraft), falling off toward a light sting at the blast
        edge. `frag_damage` sets the scale – peak is ~18x it, edge ~2x."""
        BLAST = 74.0            # outer radius: past here a plane takes nothing
        CORE  = 30.0            # within here it eats the full peak blast
        base  = max(1, getattr(self.attack, 'frag_damage', 8))
        for s in ships:
            if s.team == self.team or not s.alive: continue
            if not self._can_hit(s): continue
            d = math.hypot(s.x - self.x, s.mid_y - self.y)
            if d > BLAST: continue
            # frac: 1.0 inside the core, ramping linearly to 0.0 at the edge.
            frac = 1.0 if d <= CORE else 1.0 - (d - CORE) / (BLAST - CORE)
            dmg = round(base * (2.0 + 16.0 * frac))   # base*18 core → base*2 edge
            s.hp -= dmg
            if s.hp <= 0: s.hp = 0; s.alive = False
        self._burst_shrapnel(spawn)
        self.alive = False

    def _burst_shrapnel(self, spawn):
        if spawn is None: return
        n = max(1, getattr(self.attack, 'frag_count', 8))
        frag = Attack("Shrapnel", "shrapnel", [-1], 0, 1.0, 0.0, 0,
                      proj_w=3, proj_h=3, max_lifetime=0.4,
                      can_hit_surface=False, can_hit_sub=False, can_hit_plane=True)
        for i in range(n):
            ang = (2.0 * math.pi * i / n) + random.uniform(-0.25, 0.25)
            spd = random.uniform(210, 350)
            spawn.append(Projectile(self.x, self.y, self.team,
                                    math.cos(ang) * spd, math.sin(ang) * spd, frag))

    def _impact(self, spawn):
        """On-hit flourish for effect-tagged rounds: a bright explosion flash at
        the point of impact."""
        if not getattr(self.attack, 'hit_effect', False):
            return
        if self._effects is not None:
            self._effects.append(Explosion(self.x, self.y))

    # ── Master update ─────────────────────────────────────────────────────────
    def update(self, dt: float, ships: list, water_y: float, spawn: list = None,
               effects: list = None):
        self.age += dt
        # stashed so the deep collision/detonation paths can raise an impact
        # flash without threading `effects` through every helper signature.
        self._effects = effects

        # A timed-out round is spent: it no longer homes or collides, it just
        # coasts on its last heading for FADE_TIME while draw() fades it out,
        # then it is culled. Handled before any type dispatch so a fade started
        # by any path (mine, master timeout) runs uniformly.
        if self.fade_t is not None:
            self.fade_t -= dt
            if self.fade_t <= 0.0:
                self.alive = False
            else:
                self.x += self.vx * dt; self.y += self.vy * dt
            return

        atp = self.attack.attack_type

        if atp == "flak":
            self._update_flak(dt, ships, spawn)
            return

        if atp == "depth_charge" and not self.armed:
            self._update_dc_carrier(dt, water_y, spawn)
            return

        if atp == "mine":
            self._update_mine(dt, ships, water_y)
            return

        ml = getattr(self.attack, 'max_lifetime', 0.0)
        if ml > 0 and self.age > ml:
            self._begin_fade(); return

        # ── VLS "Tor" cold-launch eject ───────────────────────────────────────
        # A vls round is ejected straight up (see Ship._make_projectile). For the
        # first vls_pop_time it just climbs out of the tube, gravity slowly
        # bleeding the pop so it hangs at the top before the guidance below tips
        # it over ~90° toward the target and boosts it to full speed.
        vls = getattr(self.attack, 'vls', False)
        if vls and self.age < getattr(self.attack, 'vls_pop_time', 0.35):
            # Cold-eject phase: the motor has NOT lit yet, so no exhaust – it just
            # coasts silently up out of the tube. The smoke plume starts below,
            # once the motor fires and it begins to accelerate.
            self.vy += 260.0 * dt
            self.x += self.vx * dt; self.y += self.vy * dt
            if not (-PROJ_EDGE_MARGIN <= self.x <= WORLD_W + PROJ_EDGE_MARGIN) or self.y < -800:
                self.alive = False
            return

        # ── Torpedo water rules ───────────────────────────────────────────────
        # A torpedo only works in the water. Air-dropped rounds are allowed to
        # fall toward the sea; once any torpedo has entered the water it is
        # "armed" and is destroyed the moment it breaches the surface again.
        if atp == "torpedo":
            SURFACE = water_y - 12               # small grace band at the waterline
            in_water = (self.y >= SURFACE)
            if not in_water:
                if self.fired_downward and not self.armed:
                    # still falling toward the sea – keep travelling
                    self.x += self.vx * dt; self.y += self.vy * dt
                    if not (-PROJ_EDGE_MARGIN <= self.x <= WORLD_W + PROJ_EDGE_MARGIN) or self.y > 4000:
                        self.alive = False
                    return
                # breached the surface after arming → spent, remove it
                self.alive = False; return
            else:
                self.armed = True

        if atp == "bomb":
            if not self.armed:
                self.vy += attack_gravity(self.attack, 200.0) * dt   # airborne: gravity arc
                if self.y >= water_y:
                    # punched into the sea → become a straight-down sinking charge
                    # that runs the depth and damages submarines.
                    self.armed = True
                    self.vx = 0.0
                    self.vy = 120.0
        if atp == "shell":
            self.vy += attack_gravity(self.attack, 220.0) * dt
        if atp == "icbm":
            self.vy += attack_gravity(self.attack, 130.0) * dt   # accelerates as it falls

        if atp == "rocket":
            self.vy += attack_gravity(self.attack, 240.0) * dt   # clean ballistic arc

        # ── Homing guidance ───────────────────────────────────────────────────
        delay = getattr(self.attack, 'homing_delay', 0.0)
        can_home = getattr(self.attack, 'homing', False) and self.age > delay
        if can_home and atp == "aa_missile":
            can_home = (self.y < water_y)
        boosting = False                     # vls motor lit + still gaining speed
        if can_home:
            # Steer at the target's HITBOX centre of mass, not its sprite centre.
            # A ship rides high in its frame and a sub's hull is offset within its
            # padded art, so (x, mid_y) frequently points at empty sprite / above
            # the waterline – a round aimed there sails through blank space and
            # never trips the hitmask. `hit_com` is where the solid plating is.
            best = None; best_d = float('inf'); best_aim = None
            for s in ships:
                if s.team == self.team or not s.alive: continue
                if not self._can_hit(s): continue
                cx, cy = s.hit_com
                d = math.hypot(cx - self.x, cy - self.y)
                if d < best_d: best_d, best, best_aim = d, s, (cx, cy)
            if best:
                ax, ay = best_aim
                if atp == "torpedo":
                    # Keep the torpedo submerged: never steer at a point above the
                    # waterline (it would breach the surface and be culled before
                    # reaching the hull). Aim into the target's underwater plating.
                    ay = max(ay, water_y + 6.0)
                dx = ax - self.x; dy = ay - self.y
                dist = math.hypot(dx, dy)
                if dist > 0:
                    cur = max(math.hypot(self.vx, self.vy), 1)
                    nx, ny = self.vx / cur, self.vy / cur
                    tx, ty = dx / dist, dy / dist
                    if vls:
                        # tip-over + boost: swing hard toward the target while
                        # accelerating from the eject speed up to full `speed`.
                        tr = 7.0 * dt
                        spd = min(self.attack.speed, cur + 900.0 * dt)
                        boosting = spd < self.attack.speed - 1.0
                    elif atp == "torpedo":
                        tr = 6.0 * dt            # tracks hard onto the hull
                        spd = cur
                    else:
                        tr = 3.5 * dt
                        spd = cur
                    nx += tx * tr; ny += ty * tr
                    mag = math.hypot(nx, ny)
                    if mag > 0: self.vx = nx / mag * spd; self.vy = ny / mag * spd

        self.x += self.vx * dt; self.y += self.vy * dt

        # ── VLS motor plume ───────────────────────────────────────────────────
        # `boosting` is set above whenever the motor is lit and the round is still
        # gaining speed toward full – trail an exhaust puff from the tail every
        # such frame, so a plume streams behind it the WHOLE time it accelerates
        # and stops the instant it reaches cruise speed. (No smoke during the
        # silent cold-eject, and none once at full speed / with no target.)
        if boosting and self._effects is not None:
            spd = math.hypot(self.vx, self.vy) or 1.0
            back = self.attack.proj_w * 0.5 + 3.0
            tx = self.x - (self.vx / spd) * back
            ty = self.y - (self.vy / spd) * back
            self._effects.append(LaunchSmoke(tx, ty))

        # ── Cruise-missile exhaust plume ──────────────────────────────────────
        # A missile flagged `exhaust_smoke` trails the SAME grey motor plume the
        # VLS "aegis" round leaves – a LaunchSmoke puff shed from the tail every
        # frame – but for its whole powered flight rather than only while boosting
        # (a plain rocket-motor missile burns the entire way to the target). The
        # VLS eject/boost phases return earlier, so a round never double-plumes.
        if (getattr(self.attack, 'exhaust_smoke', False)
                and self._effects is not None):
            spd = math.hypot(self.vx, self.vy) or 1.0
            back = self.attack.proj_w * 0.5 + 3.0
            tx = self.x - (self.vx / spd) * back
            ty = self.y - (self.vy / spd) * back
            self._effects.append(LaunchSmoke(tx, ty))

        if atp == "icbm" and self.y >= water_y:
            self.alive = False; return                 # does not go through water

        if atp == "shell" and self.y >= water_y:
            # A shell reaching the sea normally splashes and is spent. But a
            # surface ship rides ~15% sunk (battle seats its top at
            # water_y - 0.85*disp_h), so the lower hull of its collision
            # silhouette sits BELOW the waterline – and flat-fire guns/rockets
            # already strike that submerged plating. Cull the shell here only
            # over open water; when it is still coming down onto a hittable hull,
            # let it keep descending so a dead-on shot hits the thick hull at/
            # under the waterline instead of "splashing" on the deck. The
            # collision loop below then registers the hit; once the round drops
            # past the hull's bottom (or misses through a gap) it splashes.
            onto_hull = False
            for s in ships:
                if not s.alive or s.team == self.team:
                    continue
                if not self._can_hit(s):               # only surface targets it can damage
                    continue
                half_w = getattr(s, 'disp_w', 0) * 0.5
                mid    = getattr(s, 'mid_y', None)
                if mid is None or half_w <= 0:
                    continue
                if abs(s.x - self.x) <= half_w and self.y <= mid + getattr(s, 'disp_h', 0) * 0.5:
                    onto_hull = True; break
            if not onto_hull:
                # Splashes into the open sea and is spent – throw up the same
                # foam-and-spray plume the menu flagship's salvo raises on impact.
                if self._effects is not None:
                    self._effects.append(Splash(self.x, water_y))
                self.alive = False; return

        if atp == "missile" and self.y >= water_y:
            # A ground-to-ground missile that reaches the sea is spent – it
            # splashes and is culled rather than skimming on across the water.
            # Mirror the shell rule so a dead-on shot still connects: a surface
            # ship rides ~15% sunk, so the lower hull of its collision
            # silhouette sits below the waterline. Cull here only over open
            # water; when the missile is still descending onto a hittable hull,
            # let it keep going so the collision loop below registers the hit.
            onto_hull = False
            for s in ships:
                if not s.alive or s.team == self.team:
                    continue
                if not self._can_hit(s):               # only targets it can damage
                    continue
                half_w = getattr(s, 'disp_w', 0) * 0.5
                mid    = getattr(s, 'mid_y', None)
                if mid is None or half_w <= 0:
                    continue
                if abs(s.x - self.x) <= half_w and self.y <= mid + getattr(s, 'disp_h', 0) * 0.5:
                    onto_hull = True; break
            if not onto_hull:
                self.alive = False; return             # splashes into the sea and is spent

        if not (-PROJ_EDGE_MARGIN <= self.x <= WORLD_W + PROJ_EDGE_MARGIN) or self.y > 4000 or self.y < -800:
            self.alive = False; return

        piercing = getattr(self.attack, 'piercing', False)
        # A dedicated anti-air gun round (hits planes, not surface) gets a few px
        # of forgiveness against aircraft: a small, fast-crossing plane can sit
        # just off the silhouette when a fast MG round sweeps past. Counting a
        # near-miss within `aa_tol` px of the plane's box as a hit makes AA feel
        # like it connects – paired with the predictive lead on the firing side.
        aa_tol = 6.0 if (self.attack.attack_type == 'bullet'
                         and getattr(self.attack, 'can_hit_plane', False)
                         and not getattr(self.attack, 'can_hit_surface', True)) else 0.0
        for s in ships:
            if not s.alive: continue
            if s.team == self.team:
                continue                              # pass through friendlies – never blocked
            hit = ship_hit(s, self.x, self.y)
            if not hit and aa_tol > 0.0 and getattr(s.sdef, 'unit_type', 'ship') == 'plane':
                hw = getattr(s, 'disp_w', 0) * 0.5 + aa_tol
                hh = getattr(s, 'disp_h', 0) * 0.5 + aa_tol
                if abs(self.x - s.x) < hw and abs(self.y - getattr(s, 'mid_y', self.y)) < hh:
                    hit = True
            if hit:
                if not self._can_hit(s):
                    continue                          # an enemy we can't damage → fly over
                if piercing:
                    if getattr(s, 'id', -1) == self.last_hit_id:
                        continue                      # already hit this one – keep going
                    self.last_hit_id = getattr(s, 'id', -1)
                    apply_damage(s, self.attack.damage)
                    continue                          # pierce through, don't despawn
                apply_damage(s, self.attack.damage)
                self._impact(spawn)
                self.alive = False
                return

    # ── Draw ──────────────────────────────────────────────────────────────────
    def _palette(self):
        """(base, hot) team colours matching the units: the player is black, the
        enemy is red. `hot` is a faintly lifted tint of the same colour so a tip
        or nose still reads against the body without changing team."""
        if self.team == "player":
            return QColor(17, 17, 17), QColor(70, 70, 70)        # black
        return QColor(204, 17, 17), QColor(255, 90, 70)          # red

    def _glow(self):
        """A luminous team-tinted colour for a round's night glow. The team `base`
        (black player / red enemy) is too dark to read as light, so the player
        glows a cool steel-white and the enemy a hot red-orange – muzzle fire that
        still tells the two sides apart in the dark."""
        if self.team == "player":
            return QColor(150, 194, 236)                  # cool steel muzzle glow
        return QColor(255, 122, 92)                       # hot red-orange glow

    def _tracer(self, p, sx, base, length=18.0, width=2.5, alpha=70, night=0.0):
        """A faint team-coloured streak behind the round, along its travel. At
        night the streak brightens and lengthens toward the luminous team glow, so
        every round leaves a hotter trail after dark."""
        spd = math.hypot(self.vx, self.vy)
        if spd < 8.0: return                              # stationary (e.g. armed mine)
        nx, ny = self.vx / spd, self.vy / spd
        L = min(length, max(7.0, spd * 0.045)) * (1.0 + 0.5 * night)
        c = _mix(QColor(base), self._glow(), 0.7 * night)
        c.setAlpha(min(255, int(alpha * (1.0 + 1.4 * night))))
        pen = QPen(c, width + 1.0 * night); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(int(sx - nx * L), int(self.y - ny * L), int(sx), int(self.y))

    def _orb(self, p, sx, r, night, glow=3.4):
        """A launched round drawn as a dark metal sphere by day that becomes an
        incandescent glowing orb at night. The warm bloom and white-hot core fade
        in with `night`; by day only the solid team-tinted shell body remains, so
        battleship shells and MLRS rockets 'ignite' only under the night sky."""
        base, hot = self._palette()
        p.setPen(Qt.PenStyle.NoPen)
        # Warm outer bloom – night only, its opacity ramping with `night`.
        if night > 0.01:
            warm = QColor(255, 214, 150)
            def a(v): return int(v * night)
            bloom = QRadialGradient(QPointF(sx, self.y), r * glow)
            bloom.setColorAt(0.0, QColor(255, 236, 190, a(210)))
            bloom.setColorAt(0.42, QColor(warm.red(), warm.green(), warm.blue(), a(120)))
            bloom.setColorAt(1.0, QColor(warm.red(), warm.green(), warm.blue(), 0))
            p.setBrush(bloom)
            p.drawEllipse(QPointF(sx, self.y), r * glow, r * glow)
        # The sphere itself: a dark team-tinted metal ball by day, interpolating to
        # the white-hot incandescent core at night. Lit from the upper-left either
        # way so it reads as a sphere rather than a flat disc.
        lit  = _mix(base.lighter(150), QColor(255, 252, 240), night)
        edge = _mix(base,             QColor(255, 200, 120), night)
        core = QRadialGradient(QPointF(sx - r * 0.28, self.y - r * 0.28), r * 1.5)
        core.setColorAt(0.0, lit); core.setColorAt(1.0, edge)
        p.setBrush(core)
        p.drawEllipse(QPointF(sx, self.y), r, r)

    def _shell(self, p, sx, L, night):
        """Draw the round as an actual artillery-shell silhouette – a pointed
        ogive nose, a cylindrical body, a copper driving band and a boat-tailed
        base – flown nose-first along its heading, so it arcs like a real shell
        rather than a featureless ball. It keeps the same day/night skin as
        `_orb`: a dark team-tinted steel round by day that ignites into a warm
        incandescent one at night (soft bloom + white-hot nose). `L` is the
        shell's overall length in px; its calibre is derived from it."""
        base, hot = self._palette()
        spd = math.hypot(self.vx, self.vy)
        ang = math.atan2(self.vy, self.vx) if spd > 1e-3 else 0.0
        hh   =  L * 0.30                                  # body half-height (calibre)
        tip  =  L * 0.5                                   # nose apex (local +x = travel)
        sh   =  L * 0.10                                  # shoulder where the ogive meets the body
        tail = -L * 0.5                                   # base
        bt   = hh * 0.72                                  # boat-tail base half-height
        band = -L * 0.24                                  # driving band, just ahead of the base

        p.save()
        p.translate(sx, self.y)
        p.rotate(math.degrees(ang))

        # Warm outer bloom – night only, ramping in with `night`, so the shell
        # glows like a tracer after dark (mirrors `_orb`).
        if night > 0.01:
            warm = QColor(255, 214, 150)
            def a(v): return int(v * night)
            gr = L * 1.05
            bloom = QRadialGradient(QPointF(0, 0), gr)
            bloom.setColorAt(0.0, QColor(255, 236, 190, a(200)))
            bloom.setColorAt(0.45, QColor(warm.red(), warm.green(), warm.blue(), a(110)))
            bloom.setColorAt(1.0, QColor(warm.red(), warm.green(), warm.blue(), 0))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(bloom)
            p.drawEllipse(QPointF(0, 0), gr, gr)

        # Shell body silhouette: base → body → ogive nose → back, closed at the base.
        path = QPainterPath()
        path.moveTo(tail, -bt)
        path.lineTo(sh,   -hh)
        path.quadTo(tip * 0.86, -hh * 0.52, tip, 0.0)     # upper ogive → tip
        path.quadTo(tip * 0.86,  hh * 0.52, sh,  hh)      # lower ogive
        path.lineTo(tail,  bt)
        path.closeSubpath()

        # Cylinder shading across the calibre: lit crown at top, shadow at the
        # bottom, blending toward a white-hot casing at night.
        lit  = _mix(base.lighter(170), QColor(255, 252, 240), night)
        mid  = _mix(base.lighter(112), QColor(255, 208, 140), night)
        edge = _mix(base.darker(135),  QColor(228, 138,  66), night)
        body = QLinearGradient(QPointF(0, -hh), QPointF(0, hh))
        body.setColorAt(0.0, lit); body.setColorAt(0.5, mid); body.setColorAt(1.0, edge)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(body)
        p.drawPath(path)

        # Driving band – a darker copper ring near the base.
        bandc = _mix(base.darker(180), QColor(200, 120, 60), night)
        pen = QPen(bandc, max(1.2, hh * 0.55)); pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.drawLine(QPointF(band, -hh * 0.92), QPointF(band, hh * 0.92))

        # White-hot nose spark after dark – the shell's incandescent tip.
        if night > 0.01:
            nose = QColor(255, 252, 235); nose.setAlpha(int(230 * night))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(nose)
            nr = max(1.4, hh * 0.5)
            p.drawEllipse(QPointF(tip - nr, 0), nr, nr)

        p.restore()

    def draw(self, p: QPainter, cam_x: float, night: float = 0.0):
        if not self.alive: return
        night = 0.0 if night < 0.0 else 1.0 if night > 1.0 else night
        # Ordnance ignites *only* under a full-night sky – the NIGHT band and the
        # "Dead of Night" ABYSS finale (both night==1.0). The lit DUSK/SUNSET bands
        # (night 0.4/0.7) keep their plain daytime metal look, so shells and orbs
        # don't glow at sunset. Snap any partial band down to no glow.
        night = night if night >= 0.99 else 0.0
        # A timed-out round fades: multiply the whole body's opacity (a global
        # factor on top of each shape's own alpha) so tracer, body and glow all
        # dim together, then restore at the end.
        fading = self.fade_t is not None
        if fading:
            p.save()
            p.setOpacity(max(0.0, min(1.0, self.fade_t / FADE_TIME)))
        sx  = self.x - cam_x
        pw  = self.attack.proj_w
        ph  = self.attack.proj_h
        atp = self.attack.attack_type
        base, hot = self._palette()

        # Rounds leave a faint tracer in their team colour, drawn first so the
        # solid body sits on top of the streak – except heavy shells and depth
        # charges, which fly clean. All ordnance is drawn as cheap procedural
        # vector shapes below – no sprite blit / per-frame scale+rotate.
        if atp not in ("shell", "depth_charge"):
            self._tracer(p, sx, base, night=night)

        if atp == "bullet":
            if night <= 0.01:
                # Daylight: a plain thin team-coloured line drawn along the round's
                # flight path (a short tracer streak), rather than a solid slug.
                spd = math.hypot(self.vx, self.vy)
                if spd < 1.0:
                    p.fillRect(int(sx - pw // 2), int(self.y - ph // 2),
                               max(pw, 1), max(ph, 1), base)
                else:
                    nx, ny = self.vx / spd, self.vy / spd
                    L = 9.0 + max(0, pw - 3) * 1.5
                    pen = QPen(base, 1); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawLine(int(sx - nx * L), int(self.y - ny * L),
                               int(sx), int(self.y))
            else:
                # Night: a glowing tracer round – a luminous streak along its
                # flight with a soft bloom and a white-hot head, brightening with
                # `night`. (The faint team tracer above sits underneath it.)
                spd = math.hypot(self.vx, self.vy)
                if spd < 1.0:
                    p.fillRect(int(sx - pw // 2), int(self.y - ph // 2),
                               max(pw, 1), max(ph, 1), base)
                else:
                    nx, ny = self.vx / spd, self.vy / spd
                    L = (14.0 + 12.0 * night) * (1.0 + max(0, pw - 3) * 0.12)
                    hx, hy = sx, self.y
                    tx, ty = sx - nx * L, self.y - ny * L
                    glow = self._glow()
                    # Wide soft bloom down the length of the streak.
                    b = QColor(glow); b.setAlpha(int(70 * night))
                    pen = QPen(b, max(3.0, pw + 2.0)); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawLine(int(tx), int(ty), int(hx), int(hy))
                    # Bright core, a shorter segment toward the head.
                    c = _mix(glow, QColor(255, 255, 255), 0.5 * night)
                    c.setAlpha(min(255, int(200 * night) + 40))
                    pen = QPen(c, max(1.5, pw * 0.7)); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setPen(pen)
                    p.drawLine(int(hx - nx * L * 0.55), int(hy - ny * L * 0.55),
                               int(hx), int(hy))
                    # White-hot head.
                    head = QColor(255, 255, 255); head.setAlpha(min(255, int(210 * night) + 40))
                    hr = max(1.6, pw * 0.7)
                    p.setBrush(head); p.setPen(Qt.PenStyle.NoPen)
                    p.drawEllipse(QPointF(hx, hy), hr, hr)

        elif atp == "flak":
            r = max(2, pw // 2)
            puff = QColor(base); puff.setAlpha(170)
            p.setBrush(puff); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(sx - r), int(self.y - r), r * 2, r * 2)
            faint = QColor(base); faint.setAlpha(70)
            p.setBrush(faint)
            p.drawEllipse(int(sx - self.vx * 0.03 - r), int(self.y - self.vy * 0.03 - r), r, r)

        elif atp == "shrapnel":
            # Gimbaled fragment: a short streak aligned to its flight direction
            # with a bright hot tip, rather than a static dot.
            spd = max(math.hypot(self.vx, self.vy), 1)
            nx, ny = self.vx / spd, self.vy / spd
            L = 6.0
            pen = QPen(base, 2); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(int(sx - nx * L), int(self.y - ny * L), int(sx), int(self.y))
            p.setBrush(hot); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(sx + nx * 1 - 1), int(self.y + ny * 1 - 1), 3, 3)

        elif atp == "rocket":
            # Each MLRS rocket reads as a small metal bead by day that ignites into
            # a warm glowing orb at night – the same incandescent round the
            # battleship's shell throws (the "shell" branch below), just a touch
            # smaller. The glow only lights up after dark; a faint team tracer
            # (drawn above) still tells you whose salvo it is either way.
            r = max(2.0, max(pw, ph) * 0.34)           # smaller than the shell orb
            self._orb(p, sx, r, night)

        elif atp == "mine":
            r = 6
            dark = base.darker(330)
            p.setBrush(dark); p.setPen(QPen(base, 1))
            p.drawEllipse(int(sx - r), int(self.y - r), r * 2, r * 2)
            spoke = QColor(base); spoke.setAlpha(180)
            p.setPen(QPen(spoke, 1))
            for a in range(8):
                ang = a * math.pi / 4
                p.drawLine(int(sx + math.cos(ang) * r), int(self.y + math.sin(ang) * r),
                           int(sx + math.cos(ang) * (r + 3)), int(self.y + math.sin(ang) * (r + 3)))
            p.setBrush(hot); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(sx - 1), int(self.y - r - 3), 2, 2)

        elif atp == "icbm":
            # Falling round with a team-coloured fiery trail (dark → base → hot tip)
            spd = max(math.hypot(self.vx, self.vy), 1)
            nx, ny = self.vx / spd, self.vy / spd
            outer = QColor(base.darker(160)); outer.setAlpha(70)
            mid   = QColor(base);             mid.setAlpha(140)
            inner = QColor(hot);              inner.setAlpha(210)
            for L, col, wdt in ((46, outer, 8), (30, mid, 5), (15, inner, 3)):
                pen = QPen(col, wdt); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawLine(int(sx - nx * L), int(self.y - ny * L), int(sx), int(self.y))
            p.setBrush(hot); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(sx - 3), int(self.y - 3), 7, 7)

        elif atp == "shell":
            # A lobbed battleship shell reads as an actual shell silhouette – a
            # pointed ogive nose, a cylindrical body and a boat-tailed base, flown
            # nose-first along its arc. It is a dark team-tinted steel round by day
            # that ignites into a warm incandescent one at night (soft bloom +
            # white-hot nose), the same day/night skin the MLRS rocket gets above.
            # A faint team tracer keeps it readable as whose salvo it is by day.
            # (The boss's heavy mortar is a "bomb"-type round, drawn separately
            # below, so it is unaffected.)
            L = max(9.0, max(pw, ph) * 1.5)            # overall length ~ calibre
            self._tracer(p, sx, base, length=16.0, width=2.0, alpha=80, night=night)
            self._shell(p, sx, L, night)

        elif atp in ("missile", "aa_missile"):
            # Both surface-to-surface and anti-air missiles share the same compact
            # "small missile" look – a short team-coloured body flown nose-first
            # along its heading, with a hot nose spark and a soft exhaust glow at
            # the tail (rather than the old stubby AA blob).
            spd = max(math.hypot(self.vx, self.vy), 1)
            nx, ny = self.vx / spd, self.vy / spd
            x0 = int(sx - nx * pw / 2); y0 = int(self.y - ny * pw / 2)
            x1 = int(sx + nx * pw / 2); y1 = int(self.y + ny * pw / 2)
            pen = QPen(base, max(2, ph)); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(x0, y0, x1, y1)
            p.setBrush(hot); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(sx + nx * pw / 2 - 1), int(self.y + ny * pw / 2 - 1), 3, 3)
            fx = int(sx - nx * pw / 2 - nx * 4); fy = int(self.y - ny * pw / 2 - ny * 4)
            glow = QColor(base); glow.setAlpha(210)
            p.setBrush(glow)
            p.drawEllipse(fx - 3, fy - 3, 6, 6)

        elif atp in ("torpedo", "depth_charge", "bomb"):
            spd = max(math.hypot(self.vx, self.vy), 1)
            nx, ny = self.vx / spd, self.vy / spd
            if atp == "depth_charge":
                # A depth charge reads as a rounded-rectangle drum/barrel, oriented
                # along its travel (so it stands vertical as it sinks) with a band
                # painted across the middle.
                ang = math.atan2(self.vy, self.vx)
                bw = max(3, (ph // 2) + 1); bh = max(5, int(bw * 1.5))
                p.save(); p.translate(int(sx), int(self.y))
                p.rotate(math.degrees(ang) - 90.0)        # local +y = direction of travel
                p.setBrush(base.darker(140)); p.setPen(QPen(base, 1))
                p.drawRoundedRect(-bw, -bh, bw * 2, bh * 2, bw * 0.6, bw * 0.6)
                p.setPen(QPen(base.darker(180), 2))
                p.drawLine(-bw, 0, bw, 0)                  # band around the drum
                p.restore()
            elif atp == "bomb":
                # Orient along travel: leaves the bomber nearly level (a slight
                # angle off its heading), pitches over as it arcs, and points
                # straight down once it is sinking (velocity is then straight down).
                ang = math.atan2(self.vy, self.vx)
                bw = max(3, pw // 2); bh = max(6, ph // 2)
                p.save(); p.translate(int(sx), int(self.y))
                p.rotate(math.degrees(ang) - 90.0)        # local +y = direction of travel
                p.setBrush(base.darker(150)); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(-bw, -bh, bw * 2, bh * 2)    # body
                p.fillRect(int(-bw * 1.4), int(-bh - 3), int(bw * 2.8), 3, base)  # tail fin
                p.setBrush(hot); p.drawEllipse(-2, int(bh - 2), 4, 4)             # nose
                p.restore()
            else:
                half = pw / 2
                x0 = int(sx - nx * half); y0 = int(self.y - ny * half)
                x1 = int(sx + nx * half); y1 = int(self.y + ny * half)
                pen = QPen(base.darker(125), max(3, ph)); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawLine(x0, y0, x1, y1)
                p.setBrush(hot); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(int(sx + nx * half - 3), int(self.y + ny * half - 3), 6, 6)
                wake = QColor(base); wake.setAlpha(90)
                p.setBrush(wake)
                p.drawEllipse(int(sx - nx * half - 3), int(self.y - ny * half - 3), 6, 6)

        if fading:
            p.restore()
