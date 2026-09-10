"""
game.entities.fortress – procedural sea-fort: geometry + rendering.

Single source of truth for the player/enemy base. The same mount points that the
fortress is *drawn* from are the ones turrets are *placed* on, so a turret in slot
`i` always sits on the visible platform for slot `i`. The fort is a stepped
casemate: a low gun deck at the waterline rising, tier by tier, to a tall keep at
the shore – and it grows taller as the armour (Health) upgrade adds slots. The
hull runs all the way down to the seabed (screen bottom), and a small strip of
open water is left BEHIND the shore face so aircraft can loop around it.

Visual language follows the game's "Plotting Table" theme (see game.ui.theme):
painted battleship plate in horizontal courses with rivet seams, a single signal
colour per side (amber = player command, red = hostile), hazard chevrons on the
waterline apron, lit windows up the keep and a beacon on the mast. Below the
waterline the hull becomes a concrete caisson whose plate courses fade into the
deep-water colour with depth, spreading into a footing where it meets the seabed,
so the whole fort reads as one mass rooted on the sea floor.

The underwater launcher mounts are a flight of exterior STAIRS that step seaward
and down from the hull face; each step is the cap of a solid caisson column that
itself runs down to the seabed, so an undersea turret stands clear of the fort
body on its own foundation rather than hanging off a pile.

Coordinates are expressed in a local frame whose origin is the shore edge of the
base and whose +x points out to sea; `team` decides which way that maps in world
space (player on the left, enemy mirrored on the right). y is screen-space (the
camera only pans horizontally), so the waterline `water_y` is passed in per frame.
"""

from __future__ import annotations
from PyQt6.QtCore import Qt, QRect, QPoint
from PyQt6.QtGui  import QColor, QPen, QPolygon

from ..config import WORLD_W, FORT_D_W

# ── Tier layout (local coords; i = 0 is the low, sea-facing deck) ──────────────
FRONT_X   = 350.0    # local x of the front (lowest) pad
STEP_X    = 40.0     # each higher tier steps this far toward shore
BASE_RISE = 34.0     # height of the front pad above the waterline
STEP_Y    = 28.0     # each higher tier rises this much more
PAD_W     = 56       # platform width a turret stands on
COL_W     = 60       # structural column width under each pad (overlaps → solid mass)
BEHIND    = 60.0     # leave this much open water BEHIND the shore face

# ── Underwater section: an exterior staircase descending SEAWARD of the hull ──
UW_FRONT  = FRONT_X + COL_W / 2 + 18.0   # first step sits just seaward of the hull
UW_STEP_X = 48.0                          # each deeper step moves further seaward
UW_Y0     = 60.0                          # first step depth below the waterline
UW_STEP_Y = 74.0                          # each step sits this much deeper
UW_PAD_W  = 50                            # step platform width
UW_COL_W  = 34                            # caisson column carrying each step to the seabed

COURSE_H  = 22       # height of one plate course (above and below the waterline)

RIG_X     = FORT_D_W + 60.0   # local x of the oil-rig pier head


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """Linear blend a→b; used to fog underwater plate into the deep-water colour."""
    t = max(0.0, min(1.0, t))
    return QColor(round(a.red()   + (b.red()   - a.red())   * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue()  + (b.blue()  - a.blue())  * t))


class _SeaFog:
    """Underwater visibility model: knows the water colour at any depth (matching
    battle's sea gradient) and how much a structure at that depth blends into it.
    Massive members keep more of their own colour (cap ~0.8); thin members (piles,
    legs) all but vanish into the deep (cap ~0.93)."""

    def __init__(self, water_y: float, bottom_y: float, water_deep=None):
        self.wy = water_y
        self.by = max(water_y + 1.0, bottom_y)
        if water_deep:
            self.deep = QColor(water_deep)
            self.top  = QColor(water_deep).lighter(150)
        else:
            self.deep = QColor("#0a4e82"); self.top = QColor("#1a88cc")

    def water_at(self, y: float) -> QColor:
        return _mix(self.top, self.deep, (y - self.wy) / (self.by - self.wy))

    def fade(self, y: float, cap: float = 0.80, span_frac: float = 0.70) -> float:
        span = max(120.0, (self.by - self.wy) * span_frac)
        return cap * max(0.0, min(1.0, (y - self.wy) / span))

    def tint(self, c: QColor, y: float, cap: float = 0.80,
             span_frac: float = 0.70) -> QColor:
        """`c` as seen through the water column at depth y."""
        return _mix(c, self.water_at(y), self.fade(y, cap, span_frac))


class FortressView:
    """Drawable, mirror-aware view of one team's fortress."""

    def __init__(self, team: str):
        self.team = team
        if team == "player":
            self.origin_x = 0.0;     self.sign = 1.0
            # Gunmetal plate + signal amber (theme ACCENT), amber-lit windows.
            self.body   = QColor("#20282f"); self.accent = QColor("#f0a81e")
            self.window = QColor("#ffc648")
        else:
            self.origin_x = float(WORLD_W); self.sign = -1.0
            # Rust-dark plate + danger red (theme DANGER), ember-lit windows.
            self.body   = QColor("#33201b"); self.accent = QColor("#d24338")
            self.window = QColor("#ff6a5c")

    # ── Geometry (returns WORLD coords) ──────────────────────────────────────
    def _wx(self, lx: float) -> float:
        return self.origin_x + self.sign * lx

    def surface_mounts(self, water_y: float, hlv: int):
        """World (x, y) pad-surface points for the 2 + hlv surface turrets."""
        n = 2 + hlv
        return [(self._wx(FRONT_X - i * STEP_X),
                 water_y - BASE_RISE - i * STEP_Y) for i in range(n)]

    def underwater_mounts(self, water_y: float):
        """World (x, y) of the two exterior staircase steps (seaward of the hull)."""
        return [(self._wx(UW_FRONT + j * UW_STEP_X),
                 water_y + UW_Y0 + j * UW_STEP_Y) for j in range(2)]

    def rig_mount(self, water_y: float):
        """World (x, y) of the oil-rig pier head."""
        return (self._wx(RIG_X), water_y)

    def _silhouette(self, water_y: float, hlv: int):
        """Local-frame extents of the above-water fort – (foot_lx, front_lx, top_y)
        – shared by the on-screen silhouette (hover) and the collision box, so both
        track the stepped body exactly as `draw` lays it down and grow together as
        armour adds tiers. `top_y` clears the crenellations, mast and beacon."""
        n = 2 + hlv
        back_lx  = FRONT_X - (n - 1) * STEP_X - COL_W / 2
        front_lx = FRONT_X + COL_W / 2
        foot_lx  = min(back_lx, BEHIND)
        keep_y   = water_y - BASE_RISE - (n - 1) * STEP_Y
        return foot_lx, front_lx, keep_y - 50

    def screen_bounds(self, cam_x: float, water_y: float, hlv: int) -> QRect:
        """Screen-space rect of the above-water fort silhouette, for hover
        hit-testing. Spans the stepped body from its shore back to the front deck,
        and from the top of the keep down to the waterline apron."""
        foot_lx, front_lx, top = self._silhouette(water_y, hlv)
        a = self._sx(foot_lx, cam_x); b = self._sx(front_lx, cam_x)
        left, right = min(a, b), max(a, b)
        bottom = water_y + 14         # down to the waterline apron
        return QRect(round(left), round(top), round(right - left), round(bottom - top))

    def hit_bounds(self, water_y: float, hlv: int):
        """World-space collision rect (left, right, top, bottom) of the fort: the
        visible silhouette above water, its bottom carried a short way BELOW the
        waterline to cover the apron and upper caisson where shells splash and
        torpedoes strike. Mirrors `screen_bounds` so what reads as the base is what
        rounds actually hit – no more detonations in the empty sky above it or the
        open water in front of it."""
        foot_lx, front_lx, top = self._silhouette(water_y, hlv)
        a = self._wx(foot_lx); b = self._wx(front_lx)
        left, right = min(a, b), max(a, b)
        return left, right, top, water_y + 40.0

    # ── Rendering ────────────────────────────────────────────────────────────
    def _sx(self, lx: float, cam_x: float) -> float:
        return self.origin_x + self.sign * lx - cam_x

    def _rect(self, cam_x, lx, lw, ty, h) -> QRect:
        """QRect from a local x-span, mirror-aware."""
        a = self._sx(lx, cam_x); b = self._sx(lx + lw, cam_x)
        return QRect(round(min(a, b)), round(ty), round(abs(b - a)), round(h))

    # -- underwater plate: horizontal courses fogging into the deep with depth --
    def _caisson(self, p, cam_x, lx, lw, top_y, bottom_y, fog: "_SeaFog",
                 cap: float = 0.80, span_frac: float = 0.70):
        """A column of underwater plate courses from top_y to bottom_y. Each course
        is blended further into the water colour AT ITS OWN DEPTH the deeper it
        sits, so the structure recedes into the sea instead of pasting a flat
        black wall over it. Thin members pass a higher cap / shorter span so they
        all but dissolve into the deep."""
        base = self.body.darker(135)
        y = top_y
        while y < bottom_y:
            h = min(COURSE_H, bottom_y - y)
            p.fillRect(self._rect(cam_x, lx, lw, y, h),
                       fog.tint(base, y, cap, span_frac))
            # recessed seam between courses
            p.fillRect(self._rect(cam_x, lx, lw, y, 2),
                       fog.tint(base.darker(130), y, cap, span_frac))
            y += COURSE_H

    def _pad_cap(self, p, cam_x, lx, w, surf_y):
        """A turret platform cap: lit plate slab with the signal colour on its
        leading edge – the visible 'this is a mount' marker."""
        p.fillRect(self._rect(cam_x, lx - w / 2, w, surf_y - 7, 8), self.body.lighter(185))
        p.fillRect(self._rect(cam_x, lx - w / 2, w, surf_y - 3, 4), self.body.lighter(140))
        p.setPen(QPen(self.accent, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        edge = self._rect(cam_x, lx - w / 2, w, surf_y - 7, 1)
        p.drawLine(edge.left(), edge.top(), edge.right(), edge.top())
        p.setPen(Qt.PenStyle.NoPen)

    def draw(self, p, cam_x: float, water_y: float, hlv: int,
             bottom_y: float = None, water_deep=None):
        n = 2 + hlv
        body   = self.body
        cap    = body.lighter(185)
        deep   = body.darker(135)
        seam   = body.darker(160)
        edge_l = body.lighter(135)          # light catch on the seaward face
        acc    = self.accent
        if bottom_y is None:
            bottom_y = water_y + 200.0
        # Underwater visibility model, tinted to this level's sea colour.
        fog = _SeaFog(water_y, bottom_y, water_deep)

        # Footprint spans from a little behind the shore face to the front deck.
        back_lx  = FRONT_X - (n - 1) * STEP_X - COL_W / 2
        front_lx = FRONT_X + COL_W / 2
        foot_lx  = min(back_lx, BEHIND)
        foot_w   = front_lx - foot_lx

        p.save()
        p.setPen(Qt.PenStyle.NoPen)

        # ── Below the waterline ──────────────────────────────────────────────
        # Caisson hull: the FULL fort footprint continued as coursed underwater
        # plate, from just under the apron all the way down to the seabed (a
        # couple of px past the screen bottom so it never leaves a gap).
        self._caisson(p, cam_x, foot_lx, foot_w, water_y + 14, bottom_y + 4, fog)

        # Spread footing where the caisson meets the seabed – two plinth steps
        # that root the fort on the sea floor.
        fy0, fy1 = bottom_y - 30, bottom_y - 14
        p.fillRect(self._rect(cam_x, foot_lx - 14, foot_w + 28, fy0, 32),
                   fog.tint(deep.lighter(115), fy0))
        p.fillRect(self._rect(cam_x, foot_lx - 30, foot_w + 60, fy1, 16),
                   fog.tint(deep.lighter(125), fy1))
        p.fillRect(self._rect(cam_x, foot_lx - 14, foot_w + 28, fy0, 2),
                   fog.tint(cap, fy0))

        # Exterior underwater staircase: each step is the cap of its own solid
        # caisson column running to the seabed, tied back to the hull (or the
        # previous column) by a box girder, so an undersea turret stands clear
        # of the fort body on a real foundation.
        prev_lx = front_lx
        for j, (wx, wy) in enumerate(self.underwater_mounts(water_y)):
            lx = (wx - self.origin_x) / self.sign
            # caisson column: step cap → seabed
            self._caisson(p, cam_x, lx - UW_COL_W / 2, UW_COL_W, wy + 8,
                          bottom_y + 4, fog)
            # column footing pad on the seabed
            p.fillRect(self._rect(cam_x, lx - UW_COL_W / 2 - 8, UW_COL_W + 16,
                                  bottom_y - 12, 14),
                       fog.tint(deep.lighter(120), bottom_y - 12))
            # box girder tying the column back toward the hull, just under the cap
            gx0, gx1 = min(prev_lx, lx), max(prev_lx, lx)
            p.fillRect(self._rect(cam_x, gx0, gx1 - gx0, wy + 14, 9),
                       fog.tint(deep, wy + 14))
            # the step platform itself: plate slab + signal leading edge
            p.fillRect(self._rect(cam_x, lx - UW_PAD_W / 2, UW_PAD_W, wy, 12),
                       fog.tint(body, wy, 0.55))
            p.fillRect(self._rect(cam_x, lx - UW_PAD_W / 2, UW_PAD_W, wy, 3),
                       fog.tint(cap, wy, 0.55))
            p.setPen(QPen(acc, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
            edge = self._rect(cam_x, lx - UW_PAD_W / 2, UW_PAD_W, wy, 1)
            p.drawLine(edge.left(), edge.top(), edge.right(), edge.top())
            p.setPen(Qt.PenStyle.NoPen)
            prev_lx = lx

        # ── Above the waterline ──────────────────────────────────────────────
        # Stepped body: one column per tier, overlapping into a solid staircase,
        # each faced in horizontal plate courses with recessed seams and a light
        # catch down the seaward edge.
        mounts = self.surface_mounts(water_y, hlv)
        for i, (wx, surf_y) in enumerate(mounts):
            lx = (wx - self.origin_x) / self.sign
            col = self._rect(cam_x, lx - COL_W / 2, COL_W, surf_y, water_y + 12 - surf_y)
            p.fillRect(col, body)
            # plate courses (seams every COURSE_H, skipping right at the pad cap)
            y = surf_y + COURSE_H
            while y < water_y + 8:
                p.fillRect(self._rect(cam_x, lx - COL_W / 2, COL_W, y, 2), seam)
                y += COURSE_H
            # rivet pairs along each seam line, inboard of the column edges
            y = surf_y + COURSE_H
            while y < water_y + 8:
                for rx in (lx - COL_W / 2 + 6, lx + COL_W / 2 - 8):
                    p.fillRect(self._rect(cam_x, rx, 2, y + 4, 2), edge_l)
                y += COURSE_H
            # light catch on the seaward face of the tier
            p.fillRect(self._rect(cam_x, lx + COL_W / 2 - 3, 3, surf_y,
                                  water_y + 12 - surf_y), edge_l)

        # Waterline apron ties the columns together at the sea surface: plate
        # slab, hazard chevrons along its face, and a foam line where it meets
        # the water.
        p.fillRect(self._rect(cam_x, foot_lx, foot_w, water_y - 6, 22), body)
        p.fillRect(self._rect(cam_x, foot_lx, foot_w, water_y - 6, 3), cap)
        chev = self._rect(cam_x, foot_lx, foot_w, water_y + 2, 10)
        p.save()
        p.setClipRect(chev)
        p.fillRect(chev, seam)
        p.setPen(QPen(acc, 5))
        x0 = chev.left() - 20
        while x0 < chev.right() + 20:
            p.drawLine(x0, chev.bottom() + 6, x0 + 14, chev.top() - 6)
            x0 += 22
        p.restore()
        p.setPen(Qt.PenStyle.NoPen)
        foam = QColor(255, 255, 255, 130)
        p.fillRect(self._rect(cam_x, foot_lx - 6, foot_w + 12, water_y - 1, 2), foam)

        # Lit windows: portholes down the tall shore keep and a row on the
        # second tier, so the fort reads inhabited at battle zoom.
        win = self.window
        bx, _by = mounts[-1]
        blx = (bx - self.origin_x) / self.sign
        for r in range(3):
            wy = water_y - 24 - r * 26
            p.fillRect(self._rect(cam_x, blx - 8, 6, wy, 9), win)
            p.fillRect(self._rect(cam_x, blx + 4, 6, wy, 9), win)
        if n >= 2:
            mx, my = mounts[-2]
            mlx = (mx - self.origin_x) / self.sign
            for dx in (-10, 2):
                p.fillRect(self._rect(cam_x, mlx + dx, 6, my + 14, 8), win)

        # Platform caps – the visible mounts. Drawn front-to-back so higher tiers
        # overlap lower ones cleanly.
        for i, (wx, surf_y) in enumerate(mounts):
            lx = (wx - self.origin_x) / self.sign
            self._pad_cap(p, cam_x, lx, PAD_W, surf_y)

        # Crenellations, beacon mast and flag on the tall keep (highest tier).
        keep_x, keep_y = mounts[-1]
        klx = (keep_x - self.origin_x) / self.sign
        for c in range(3):
            p.fillRect(self._rect(cam_x, klx - 20 + c * 16, 9, keep_y - 18, 12), body)
            p.fillRect(self._rect(cam_x, klx - 20 + c * 16, 9, keep_y - 18, 2), cap)
        p.setPen(QPen(self.body.lighter(150), 2))
        # Tall mast: the flag flies high above the keep so a turret seated on this
        # top mount (and its elevated barrel) can't cover it.
        pole = self._rect(cam_x, klx, 1, keep_y - 58, 40)
        p.drawLine(pole.left(), pole.top(), pole.left(), pole.bottom())
        p.setPen(Qt.PenStyle.NoPen)
        # beacon lamp: signal-colour point with a soft halo
        halo = QColor(acc); halo.setAlpha(70)
        p.setBrush(halo)
        p.drawEllipse(pole.left() - 5, pole.top() - 5, 10, 10)
        p.setBrush(acc)
        p.drawEllipse(pole.left() - 2, pole.top() - 2, 4, 4)
        # Stream the pennant LANDWARD (away from the guns' seaward firing arc) so
        # the barrels never cross the cloth – otherwise the mirrored enemy fort's
        # turrets sit right under a seaward-streaming flag.
        flag_dir = -1 if self.team == "player" else 1
        fx = pole.left(); fy = pole.top() + 4
        p.drawPolygon(QPolygon([QPoint(fx, fy), QPoint(fx + 18 * flag_dir, fy + 5),
                                QPoint(fx, fy + 11)]))

        # Pier out to the oil-rig pier head: plated deck on piles that run all
        # the way down to the seabed. The piles are thin members, so they fog
        # out into the deep quickly rather than striping the whole water column.
        rig_wx, _ = self.rig_mount(water_y)
        rig_lx = (rig_wx - self.origin_x) / self.sign
        deck_lx = FRONT_X + 6
        deck_w  = rig_lx + 14 - deck_lx
        p.fillRect(self._rect(cam_x, deck_lx, deck_w, water_y - 7, 8), body)
        p.fillRect(self._rect(cam_x, deck_lx, deck_w, water_y - 7, 2), cap)
        for k in range(4):
            plx = deck_lx + 10 + k * (deck_w - 12) / 3.0
            self._caisson(p, cam_x, plx - 3, 6, water_y + 1, bottom_y + 4,
                          fog, cap=0.93, span_frac=0.40)

        p.restore()
