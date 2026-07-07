"""
game.entities.oilrig — the Bastion: a deployable auxiliary sea-fort.

Despite the module name (kept so the campaign/unlock key `oilrig` is stable), this
structure no longer produces income. It is a heavily-armoured BULWARK moored to the
base pier, seaward of the main fort, so enemy fire strikes it first — an auxiliary
fortress that SHIELDS the main one. It is extremely tanky, and its wide deck carries
TWO surface-turret nodes to bristle with guns.

Income now comes from the separate "Oil Rig" income turret (vehicles/oil_rig.json).
"""

from __future__ import annotations
import math
from PyQt6.QtCore import Qt, QRect, QRectF
from PyQt6.QtGui  import QColor, QPen, QFont

RIG_HP   = 3000          # very tanky — it exists to soak fire for the main fort
NODE_DX  = 34            # horizontal offset of each deck turret node from centre

BUILD_TIME     = 6.0     # seconds to raise the platform out of the sea
BUILD_DMG_MULT = 4.0     # 400% damage taken while under construction (fragile)


def render_bastion(p, cx: float, wy: float, disp_w: float, disp_h: float, team: str,
                   bottom_y: float = None, water_deep=None):
    """Draw the Bastion's structure centred on world-x `cx`, its pier deck sitting at
    waterline `wy`. Deliberately mirrors the main fort's look (game.entities.fortress)
    so the bulwark reads as part of the same base rather than a stray derrick: the
    same gunmetal plate, a single amber (player) / red (enemy) signal edge, bright
    platform caps, lit windows and a crenellated keep. Its legs are solid caisson
    piles that run all the way down to the seabed (`bottom_y`), fogging into the
    deep-water colour with depth like the fort's own hull; when `bottom_y` is None
    (the catalog portrait) the piles stop just below the waterline instead.
    HP bar/label are drawn by the caller."""
    from .fortress import _SeaFog
    if team == "player":
        body = QColor("#20282f"); acc = QColor("#f0a81e"); win = QColor("#ffc648")
    else:
        body = QColor("#33201b"); acc = QColor("#d24338"); win = QColor("#ff6a5c")
    cap  = body.lighter(185)      # bright platform caps, as on the fort mounts
    deep = body.darker(135)       # shaded piles/foundation
    deck_y = int(wy - disp_h * 0.42)
    half_w = int(disp_w * 0.36)

    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    # Caisson legs: solid plated piles from the deck straight down into the sea.
    # In battle they reach the seabed, drawn in courses that fog into the water
    # colour at their own depth (Maunsell-fort style), with footing pads where
    # they land and cross-braces tying them together underwater.
    leg_bot = int(bottom_y + 4) if bottom_y is not None else int(wy + 26)
    fog = _SeaFog(wy, max(leg_bot, wy + 60), water_deep)
    leg_xs = (int(cx - half_w + 14), int(cx + half_w - 14))
    for lxc in leg_xs:
        y = deck_y + 8
        while y < leg_bot:
            h = min(20, leg_bot - y)
            p.fillRect(lxc - 7, y, 14, h, fog.tint(deep, y, 0.86, 0.55))
            p.fillRect(lxc - 7, y, 14, 2, fog.tint(deep.darker(130), y, 0.86, 0.55))
            y += 20
        if bottom_y is not None:
            p.fillRect(lxc - 12, leg_bot - 16, 24, 16,
                       fog.tint(deep.lighter(120), leg_bot - 16, 0.86, 0.55))
    # Cross-braces between the legs: at the waterline and again mid-depth.
    bw = leg_xs[1] - leg_xs[0]
    p.fillRect(leg_xs[0], int(wy + 10), bw, 6, fog.tint(deep, wy + 10, 0.86, 0.55))
    if bottom_y is not None and leg_bot - wy > 140:
        my = int(wy + (leg_bot - wy) * 0.45)
        p.fillRect(leg_xs[0], my, bw, 6, fog.tint(deep, my, 0.86, 0.55))
    # Foam where the legs break the surface.
    foam = QColor(255, 255, 255, 120)
    for lxc in leg_xs:
        p.fillRect(lxc - 10, int(wy - 1), 20, 2, foam)
    # Armoured deck slab: body mass, a lighter cap band, a signal leading edge.
    p.fillRect(int(cx - half_w), deck_y, half_w * 2, 16, body)
    p.fillRect(int(cx - half_w), deck_y, half_w * 2, 5,  cap)
    p.setPen(QPen(acc, 2)); p.drawLine(int(cx - half_w), deck_y, int(cx + half_w), deck_y)
    p.setPen(Qt.PenStyle.NoPen)
    # Hazard chevrons along the deck fascia — the same waterline marking the fort
    # apron carries, so the bulwark reads as part of the base works.
    fascia = QRect(int(cx - half_w), deck_y + 9, half_w * 2, 6)
    p.save(); p.setClipRect(fascia)
    p.fillRect(fascia, body.darker(160))
    p.setPen(QPen(acc, 3))
    x0 = fascia.left() - 12
    while x0 < fascia.right() + 12:
        p.drawLine(x0, fascia.bottom() + 4, x0 + 9, fascia.top() - 4)
        x0 += 15
    p.restore()
    p.setPen(Qt.PenStyle.NoPen)
    # Two turret-node pads — bright caps with a signal edge, matching the fort mounts.
    for dx in (-NODE_DX, NODE_DX):
        px0 = int(cx + dx - 16)
        p.fillRect(px0, deck_y - 7, 32, 8, cap)
        p.setPen(QPen(acc, 2)); p.drawLine(px0, deck_y - 7, px0 + 32, deck_y - 7)
        p.setPen(Qt.PenStyle.NoPen)
    # Central keep tower with lit windows and a crenellated cap (fort motif).
    tw = 22; th = int(disp_h * 0.5); keep_y = deck_y - th
    p.fillRect(int(cx - tw // 2), keep_y, tw, th, body)
    p.fillRect(int(cx + tw // 2 - 3), keep_y, 3, th, body.lighter(135))
    for r in range(2):
        wyr = deck_y - 13 - r * 15
        p.fillRect(int(cx - 6), wyr, 4, 7, win)
        p.fillRect(int(cx + 2), wyr, 4, 7, win)
    for c in range(3):
        p.fillRect(int(cx - tw // 2 + c * (tw // 2 - 1)), keep_y - 7, 7, 7, body)
        p.fillRect(int(cx - tw // 2 + c * (tw // 2 - 1)), keep_y - 7, 7, 2, cap)
    p.setPen(QPen(acc, 1)); p.drawLine(int(cx - tw // 2), keep_y, int(cx + tw // 2 - 1), keep_y)
    # Beacon lamp on the keep — signal-colour point with a soft halo.
    p.setPen(Qt.PenStyle.NoPen)
    halo = QColor(acc); halo.setAlpha(70); p.setBrush(halo)
    p.drawEllipse(int(cx - 4), keep_y - 15, 9, 9)
    p.setBrush(acc)
    p.drawEllipse(int(cx - 2), keep_y - 13, 4, 4)
    p.restore()


class _RigSDef:
    """Minimal ShipDef-shaped descriptor so targeting/collision code works (used
    only as a fallback when no vehicle definition is supplied)."""
    def __init__(self):
        self.key = "oilrig"; self.name = "Bastion"
        self.unit_type = "structure"; self.aircraft_type = "loop"
        self.tags = ["surface", "structure", "rig", "bastion"]
        self.display_w = 150; self.display_h = 110
        self.flip_player = False; self.flip_enemy = False
        self.is_boss = False; self.cost = 0; self.speed = 0
        self.hp = RIG_HP; self.income = 0
        self.attacks = []; self.fire_points = []
        self.turret_slot = "surface"
        self.description = ("Armoured auxiliary sea-fort moored seaward of the base. "
                            "It soaks enemy fire to shield the main fort, is extremely "
                            "tanky, and its deck mounts TWO surface turrets. Defend it.")


class OilRig:
    is_rig = True
    income = 0               # the Bastion produces NO income

    def __init__(self, team: str, x: float, sdef=None):
        self.team = team
        self.x = float(x)
        self.sdef = sdef if sdef is not None else _RigSDef()
        hp = getattr(self.sdef, 'hp', RIG_HP) or RIG_HP
        self.hp = hp; self.max_hp = hp
        self.alive = True
        self.water_y = 0.0
        self.water_deep = None   # level water tint; set by battle so the legs fog right
        self.id = -20 if team == "player" else -21
        # TWO surface-turret nodes on the platform deck.
        self.node_turrets = [None, None]
        # Construction: the platform is RAISED over `build_time` seconds. While it
        # rises it takes 400% damage — defend it as it builds.
        self.build_time    = float(getattr(self.sdef, 'build_time', BUILD_TIME) or BUILD_TIME)
        self.construction_t = self.build_time

    @property
    def disp_w(self): return self.sdef.display_w
    @property
    def disp_h(self): return self.sdef.display_h

    @property
    def under_construction(self) -> bool:
        return self.construction_t > 0.0

    @property
    def build_frac(self) -> float:
        """0.0 at ground-breaking → 1.0 when the platform is fully raised."""
        if self.build_time <= 0: return 1.0
        return max(0.0, min(1.0, 1.0 - self.construction_t / self.build_time))

    @property
    def damage_mult(self) -> float:
        """400% damage while the shell of the platform is still going up."""
        return BUILD_DMG_MULT if self.under_construction else 1.0

    def _deck_y(self) -> float:
        return self.water_y - self.disp_h * 0.42

    @property
    def top_y(self): return self.water_y - self.disp_h * 0.62
    @property
    def mid_y(self): return self.water_y - self.disp_h * 0.25

    # Aim point for homing rounds — the rig has no hitmask centre-of-mass, so
    # fall back to (x, mid_y) like the fort does. Steers missiles into the deck.
    @property
    def hit_com(self): return self.x, self.mid_y

    def node_positions(self):
        """World (x, y) of each deck turret node (deck surface a turret sits on)."""
        dy = self._deck_y()
        return [(self.x - NODE_DX, dy), (self.x + NODE_DX, dy)]

    def set_water(self, wy): self.water_y = wy

    def update(self, dt=0.0, *a, **k):
        # Raise the platform. It only becomes a fully-fledged bastion once built.
        if self.construction_t > 0.0:
            self.construction_t = max(0.0, self.construction_t - dt)
        if self.hp <= 0: self.alive = False

    def draw(self, p, cam_x: float, sprites: dict, night: float = 0.0):
        if not self.alive: return
        wy = self.water_y
        cx = self.x - cam_x
        acc = QColor("#f0a81e") if self.team == "player" else QColor("#d24338")
        # The structure itself — kept in a shared routine so the catalog portrait
        # (assets.bastion_portrait) renders the exact same fort-style artwork.
        # The paint device's height is the seabed (screen bottom), so the caisson
        # legs run all the way down into the sea.
        dev = p.device()
        bottom = float(dev.height()) if dev is not None else None
        if self.under_construction:
            self._draw_constructing(p, cx, wy, bottom, acc)
        else:
            render_bastion(p, cx, wy, self.disp_w, self.disp_h, self.team, bottom,
                           self.water_deep)
        by = int(wy - self.disp_h - 10)
        if self.under_construction:
            # Build-progress bar + a warning that it's fragile right now.
            frac = self.build_frac
            bx = int(cx - 32)
            p.fillRect(bx, by, 64, 4, QColor("#333333"))
            p.fillRect(bx, by, int(64 * frac), 4, QColor("#e8c33a"))
            p.setPen(QColor("#e8c33a")); p.setFont(QFont("Arial", 7, QFont.Weight.Bold))
            p.drawText(bx - 12, by - 10, 88, 10, Qt.AlignmentFlag.AlignCenter,
                       f"BUILDING {int(frac * 100)}%")
        else:
            # HP bar — lifted clear of the keep and of any turret mounted on a
            # deck node, so it never overlaps a unit sitting on the Bastion.
            top = float(by)
            positions = self.node_positions()
            for ni, nt in enumerate(self.node_turrets):
                if nt is not None and getattr(nt, 'alive', False):
                    _, dy = positions[ni]
                    dh = getattr(getattr(nt, 'sdef', None), 'display_h', 0) or 0
                    top = min(top, dy - dh - 6)
            by = int(top)
            f = max(0.0, self.hp / self.max_hp)
            bx = int(cx - 32)
            p.fillRect(bx, by, 64, 4, QColor("#333333"))
            p.fillRect(bx, by, int(64 * f), 4, QColor("#33bb33") if f > .4 else QColor("#dd2222"))

    def _draw_constructing(self, p, cx: float, wy: float, bottom, acc):
        """Raise the Bastion out of the sea: the fort-style structure is revealed
        bottom-up as it builds, wrapped in scaffolding with a swinging crane jib and
        a flashing hazard line at the current build height. The caisson legs (its
        foundation) are laid first, so they stand full-height from the start."""
        disp_w = self.disp_w; disp_h = self.disp_h
        frac = self.build_frac
        deck_y     = wy - disp_h * 0.42
        struct_top = deck_y - disp_h * 0.5 - 16     # topmost point of the finished keep
        reveal_top = deck_y - (deck_y - struct_top) * frac
        half = int(disp_w * 0.36)

        p.save()
        # Reveal the finished artwork clipped to everything below the current build
        # line, so the superstructure appears to rise straight up out of the deck.
        clip_bot = bottom if bottom is not None else wy + 240
        p.setClipRect(QRectF(cx - disp_w, reveal_top, disp_w * 2.0, clip_bot - reveal_top))
        render_bastion(p, cx, wy, disp_w, disp_h, self.team, bottom, self.water_deep)
        p.setClipping(False)

        # Scaffolding around the part still going up: two poles + horizontal rungs.
        yellow = QColor("#e8c33a")
        sx0 = int(cx - half - 6); sx1 = int(cx + half + 6)
        p.setPen(QPen(QColor(232, 195, 58, 200), 2))
        p.drawLine(sx0, int(deck_y), sx0, int(struct_top))
        p.drawLine(sx1, int(deck_y), sx1, int(struct_top))
        rungs = 5
        for i in range(rungs + 1):
            ry = int(deck_y + (struct_top - deck_y) * i / rungs)
            p.drawLine(sx0, ry, sx1, ry)

        # Crane: a mast beside the platform with a jib that swings as it works, a
        # cable and a hook hanging over the build line. Phase is driven by the
        # build clock so it animates without any wall-clock/random source.
        phase = self.construction_t * 2.2
        mast_x = sx1 + 12
        mast_top = int(struct_top - 10)
        p.setPen(QPen(QColor(120, 128, 136), 3))
        p.drawLine(mast_x, int(deck_y), mast_x, mast_top)
        jib_len = half + 22
        jx = int(mast_x - jib_len * (0.55 + 0.45 * abs(math.sin(phase))))
        p.setPen(QPen(QColor(150, 158, 166), 3))
        p.drawLine(mast_x, mast_top, jx, mast_top)
        hook_y = int(reveal_top - 6 + 4 * math.sin(phase * 1.7))
        p.setPen(QPen(QColor(90, 96, 102), 1))
        p.drawLine(jx, mast_top, jx, hook_y)
        p.fillRect(jx - 4, hook_y, 8, 5, QColor("#c8a832"))

        # Flashing hazard bar at the current build height.
        if int(self.construction_t * 4) % 2 == 0:
            glow = QColor(yellow); glow.setAlpha(150)
            p.fillRect(int(cx - half - 4), int(reveal_top - 2), (half + 4) * 2, 3, glow)
        p.restore()
