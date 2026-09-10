"""
game.entities.explosion – short-lived particle bursts: a fiery death burst, a
heavy-gun muzzle blast and a VLS launch smoke cloud.
"""

from __future__ import annotations
import math, random
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui  import QColor


class Explosion:
    def __init__(self, x, y):
        self.x = x; self.y = y; self.t = 0.0; self.dur = 0.70; self.alive = True
        self.pts = [{"vx": random.uniform(-90, 90), "vy": random.uniform(-105, -15),
                     "r": random.uniform(2.5, 7.5), "h": random.uniform(0.0, 0.11)}
                    for _ in range(14)]

    def update(self, dt):
        self.t += dt; self.alive = self.t < self.dur

    def draw(self, p, cam_x):
        prog = 1.0 - self.t / self.dur; sx = self.x - cam_x
        for pt in self.pts:
            px = sx + pt["vx"] * self.t
            py = self.y + pt["vy"] * self.t + 145.0 * self.t ** 2
            p.save(); p.setOpacity(min(1.0, prog * 0.9))
            r = pt["r"] * (0.35 + prog * 0.65)
            p.setBrush(QColor.fromHslF(pt["h"], 0.95, min(0.92, 0.28 + prog * 0.45)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(px - r), int(py - r), int(r * 2), int(r * 2))
            p.restore()


class MuzzleSmoke:
    """The blast cloud of a heavy gun: a burst of grey powder smoke thrown out
    of the muzzle along the bore. Each puff erupts fast, drags to a hang, then
    billows and fades – so the cloud lingers at the gun after the shell is long
    gone. `power` scales puff count, throw and size (1.0 ≈ a battleship main
    battery; the MLRS uses a smaller per-rocket blast that stacks up over the
    salvo into one rolling cloud around the launcher)."""

    def __init__(self, x, y, dir_x=0.0, dir_y=-1.0, power=1.0):
        self.x = x; self.y = y; self.t = 0.0
        self.dur = random.uniform(0.9, 1.35) * (0.75 + 0.35 * power)
        self.alive = True
        # kept so the LAN host can serialize this cloud and the client rebuild
        # an equivalent one (see game.netsync).
        self.dir_x = dir_x; self.dir_y = dir_y; self.power = power
        m = math.hypot(dir_x, dir_y) or 1.0
        nx, ny = dir_x / m, dir_y / m
        self.pts = [{"vx": nx * k + random.uniform(-24.0, 24.0),
                     "vy": ny * k + random.uniform(-26.0, 8.0),
                     "r":  random.uniform(3.0, 7.0) * (0.65 + 0.5 * power),
                     "g":  random.uniform(0.50, 0.80),
                     "drag": random.uniform(2.2, 3.6)}
                    for k in (random.uniform(55.0, 185.0) * power
                              for _ in range(int(round(7 + 6 * power))))]

    def update(self, dt):
        self.t += dt; self.alive = self.t < self.dur

    def draw(self, p, cam_x):
        prog = self.t / self.dur
        p.save(); p.setPen(Qt.PenStyle.NoPen)
        for pt in self.pts:
            # exponential drag: travelled distance saturates, so the puff blasts
            # out of the bore then hangs (with a slight buoyant rise) to dissipate
            k = (1.0 - math.exp(-pt["drag"] * self.t)) / pt["drag"]
            px = self.x + pt["vx"] * k - cam_x
            py = self.y + pt["vy"] * k - 12.0 * self.t
            r = pt["r"] * (0.5 + prog * 2.0)
            p.setOpacity(max(0.0, 0.55 * (1.0 - prog)))
            p.setBrush(QColor.fromRgbF(pt["g"], pt["g"], pt["g"]))
            p.drawEllipse(int(px - r), int(py - r), int(r * 2), int(r * 2))
        p.restore()


class Splash:
    """The plume a heavy shell throws up when it falls short and strikes the
    sea: a pale foam ring spreading flat on the surface plus a few spray
    droplets kicked up and arcing back down under gravity. This is the same
    splash the menu's flagship raises when its salvo lands (see ui.menu) –
    lifted here so an in-battle shell that splashes into open water gets the
    same flourish instead of vanishing on a single frame."""

    G   = 520.0                                    # px/s² for the falling droplets
    DUR = 0.45                                     # splash life (matches the menu)

    def __init__(self, x, y):
        # (x, y) is the impact point on the waterline.
        self.x = x; self.y = y; self.t = 0.0; self.alive = True
        # A symmetric fan of droplets thrown up and out from the impact, each
        # kicked up at a slightly random speed so no two splashes look identical.
        self.drops = [{"vx": k * random.uniform(20.0, 30.0),
                       "vy": random.uniform(-84.0, -58.0),
                       "r":  random.uniform(1.6, 2.6)}
                      for k in (-1.0, -0.5, 0.5, 1.0)]

    def update(self, dt):
        self.t += dt; self.alive = self.t < self.DUR

    def draw(self, p, cam_x):
        t  = self.t
        f  = 1.0 - t / self.DUR                    # 1 → 0 over the splash life
        sx = self.x - cam_x
        p.save(); p.setPen(Qt.PenStyle.NoPen)
        # pale foam spreading flat on the surface
        p.setOpacity(0.5 * f)
        p.setBrush(QColor(214, 230, 236))
        p.drawEllipse(QPointF(sx, self.y), 7 * (1.2 - f) + 3, 3 * f + 2)
        # spray droplets kicked up and falling back
        p.setOpacity(0.75 * f)
        for d in self.drops:
            dx = d["vx"] * t
            dy = d["vy"] * t + 0.5 * self.G * t * t
            r  = d["r"] * f + 1.0
            p.drawEllipse(QPointF(sx + dx, self.y + dy), r, r)
        p.restore()


class Wake:
    """A foam ripple shed off a moving hull at the waterline. Each one is peeled
    off the stern as the ship makes way, then spreads flat along the surface –
    widening and fading – so a train of them traces the ship's wake. Purely
    cosmetic and cheap: each side spawns its own from local ship motion (never
    networked), so a wake appears behind every moving ship on both screens."""

    def __init__(self, x, y, size=1.0, drift=0.0):
        self.x = x; self.y = y; self.t = 0.0; self.alive = True
        self.dur    = random.uniform(0.85, 1.4)
        self.drift  = drift                                   # slow world drift (px/s)
        self.w0     = random.uniform(5.0, 9.0)  * size        # starting half-width
        self.spread = random.uniform(22.0, 38.0) * size       # how far it widens
        self.h      = random.uniform(1.3, 2.4)  * (0.7 + 0.3 * size)   # foam thickness
        self.jy     = random.uniform(-1.5, 2.5)               # tiny vertical scatter
        self.a      = random.uniform(0.32, 0.48)              # peak opacity

    def update(self, dt):
        self.t += dt
        self.x += self.drift * dt
        self.alive = self.t < self.dur

    def draw(self, p, cam_x):
        f = self.t / self.dur                                 # 0 → 1 over its life
        half_w = self.w0 + self.spread * f                    # spreads outward
        # ripple in fast on the first frames, then ebb away slowly
        op = self.a * (1.0 - f) * min(1.0, f * 5.0)
        if op <= 0.0:
            return
        sx = self.x - cam_x; y = self.y + self.jy
        p.save(); p.setPen(Qt.PenStyle.NoPen)
        p.setOpacity(op)
        p.setBrush(QColor(228, 240, 246))
        p.drawEllipse(QPointF(sx, y), half_w, self.h)
        p.restore()


class LaunchSmoke:
    """A single exhaust puff from a VLS missile's motor. One is dropped from the
    tail every frame while the motor is burning and the round is accelerating, so
    together they trail a plume of grey smoke that drifts, billows and fades
    behind the missile as it blasts off."""

    def __init__(self, x, y):
        self.x = x; self.y = y; self.t = 0.0
        self.dur = random.uniform(0.5, 0.85); self.alive = True
        # gentle self-drift (buoyant rise + a little spread) once shed
        self.vx = random.uniform(-14.0, 14.0)
        self.vy = random.uniform(-26.0, -6.0)
        self.r0 = random.uniform(2.5, 5.5)
        self.g  = random.uniform(0.55, 0.85)          # greyscale value

    def update(self, dt):
        self.t += dt
        self.x += self.vx * dt; self.y += self.vy * dt
        self.alive = self.t < self.dur

    def draw(self, p, cam_x):
        prog = self.t / self.dur                       # 0 → 1 over its life
        r = self.r0 * (0.6 + prog * 2.2)               # billows outward as it ages
        sx = self.x - cam_x
        p.save(); p.setOpacity(max(0.0, 0.6 * (1.0 - prog)))
        p.setBrush(QColor.fromRgbF(self.g, self.g, self.g))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(sx - r), int(self.y - r), int(r * 2), int(r * 2))
        p.restore()
