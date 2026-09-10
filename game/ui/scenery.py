"""
game.ui.scenery – a procedural navy backdrop drawn from the existing sprites.

No external image files: the menus paint a sky/sea scene with a horizon line of
dark fleet silhouettes reused from the unit pixmaps.
"""

from __future__ import annotations
import math
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui  import (QPainter, QColor, QLinearGradient, QRadialGradient, QPen,
                          QPainterPath, QPixmap, QTransform)

from . import theme

_CLOUDS = [(0.12, 0.16, 1.0), (0.34, 0.10, 0.7), (0.62, 0.20, 1.2),
           (0.80, 0.13, 0.8), (0.5, 0.26, 0.6)]

# The sky is one gradient in *world* space, shared by every menu screen so it stays
# continuous when they slide past each other: SKY_TOP sits at the main menu's top
# edge (world 0) and SKY_LOW at its horizon (world _SKY_REF_FRAC·h). Screens higher
# up (the panned-up save sky) pass a negative `sky_base_offset` and simply sample the
# same line further up – see paint_navy_scene.
_SKY_REF_FRAC = 0.62


def _lerp_qcolor(c0: QColor, c1: QColor, u: float) -> QColor:
    """Blend c0→c1 at fraction u, extrapolating past the ends (u<0 or u>1) and
    clamping each channel to 0–255 – lets the one sky gradient extend beyond its
    two anchor colours without banding at a hard edge."""
    def ch(a, b):
        return max(0, min(255, round(a + (b - a) * u)))
    return QColor(ch(c0.red(), c1.red()), ch(c0.green(), c1.green()), ch(c0.blue(), c1.blue()))

# Ships that drift across the sea, each on its own lane. Params are fixed (no
# per-frame randomness) so the motion is smooth and stateless: only x depends on
# time. Lane = depth into the sea band (0 = far/near horizon, 1 = close/bottom);
# nearer ships are larger and move faster. dir = travel direction (+1 → right).
#           key,          lane, speed, dir
_MOVERS = [
    ("battleship",  0.48,  16, +1),
]

# Silhouettes are constant-size per (sprite, w, h, flip) – only their x drifts –
# so cache them instead of rebuilding every frame.
_sil_cache: dict = {}


_SEA_SIL_TINT = QColor(22, 37, 52, 224)          # ships melt into the dark water


def _mover_pixmap(px: QPixmap, w: int, h: int, flip: bool,
                  tint: QColor = _SEA_SIL_TINT) -> QPixmap:
    """A steel-tinted, cached silhouette of a drifting ship (optionally flipped
    to face its travel direction). `tint` lifts to a lighter steel for craft that
    must read against the sky rather than the sea."""
    key = (id(px), w, h, flip, tint.rgba())
    hit = _sil_cache.get(key)
    if hit is not None:
        return hit
    res = QPixmap(px.size()); res.fill(Qt.GlobalColor.transparent)
    q = QPainter(res); q.drawPixmap(0, 0, px)
    q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    q.fillRect(res.rect(), tint); q.end()
    out = res.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    if flip:
        out = out.transformed(QTransform().scale(-1, 1),
                              Qt.TransformationMode.SmoothTransformation)
    _sil_cache[key] = out
    return out


def _paint_moon(p: QPainter, w: int, h: int, sky_base_offset: float):
    """A low steel moon hanging in the upper sky. Positioned in the shared sky
    world-space (via sky_base_offset) so it holds its place while screens slide
    past one another; drawn before the clouds so the cloud banks drift in front."""
    mx = 0.80 * w
    my = 0.17 * h - sky_base_offset          # world-anchored, like the sky gradient
    mr = max(15.0, h * 0.045)
    p.setPen(Qt.PenStyle.NoPen)
    # Soft halo – a faint bloom bleeding into the sky.
    halo = QRadialGradient(QPointF(mx, my), mr * 3.4)
    halo.setColorAt(0.0, QColor(206, 219, 234, 55))
    halo.setColorAt(0.35, QColor(206, 219, 234, 22))
    halo.setColorAt(1.0, QColor(206, 219, 234, 0))
    p.setBrush(halo); p.drawEllipse(QPointF(mx, my), mr * 3.4, mr * 3.4)
    # The disc – a pale steel-white, lit a touch brighter toward the upper-left.
    disc = QRadialGradient(QPointF(mx - mr * 0.3, my - mr * 0.3), mr * 1.7)
    disc.setColorAt(0.0, QColor(234, 241, 248))
    disc.setColorAt(1.0, QColor(190, 203, 218))
    p.setBrush(disc); p.drawEllipse(QPointF(mx, my), mr, mr)
    # A few faint maria so it reads as a moon, not a flat dot.
    p.setBrush(QColor(176, 190, 207, 70))
    p.drawEllipse(QPointF(mx + mr * 0.30, my - mr * 0.16), mr * 0.26, mr * 0.22)
    p.drawEllipse(QPointF(mx - mr * 0.28, my + mr * 0.26), mr * 0.18, mr * 0.16)
    p.drawEllipse(QPointF(mx + mr * 0.04, my + mr * 0.08), mr * 0.13, mr * 0.12)


def paint_navy_scene(p: QPainter, w: int, h: int, sprites=None, t: float = 0.0,
                     horizon_frac: float = 0.62, movers=_MOVERS, sea: bool = True,
                     sky_base_offset: float = 0.0, moon: bool = False):
    """Paint the sky/sea backdrop. `horizon_frac` sets how far down the waterline
    sits (raise it toward 1.0 to drop the sea to a strip at the very bottom, as if
    the camera has panned up into the sky); `movers` is the list of ships drifting
    on the water (pass () for empty seas). Pass `sea=False` to drop the water,
    horizon and waves entirely – the sky gradient then fills the whole frame (the
    camera has panned all the way up). `sky_base_offset` shifts this screen within
    the shared sky world-space (see _SKY_REF_FRAC): the panned-up save sky passes
    −h so its sky is the exact upward continuation of the menu's and the two meet
    seamlessly where the screens slide past each other."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    horizon = int(h * horizon_frac) if sea else h

    # Sky – one world-space gradient (SKY_TOP → SKY_LOW), sampled over this screen's
    # slice of it so it lines up with the neighbouring screen during a slide.
    ref = _SKY_REF_FRAC * h
    top = QColor(theme.SKY_TOP); low = QColor(theme.SKY_LOW)
    sky = QLinearGradient(0, 0, 0, horizon)
    sky.setColorAt(0.0, _lerp_qcolor(top, low, sky_base_offset / ref))
    sky.setColorAt(1.0, _lerp_qcolor(top, low, (sky_base_offset + horizon) / ref))
    p.fillRect(0, 0, w, horizon, sky)

    # A moon hanging in the sky (opt-in per screen), behind the drifting clouds.
    if moon:
        _paint_moon(p, w, h, sky_base_offset)

    # Faint cloud banks (flat, no glow)
    p.setPen(Qt.PenStyle.NoPen)
    for i, (fx, fy, sc) in enumerate(_CLOUDS):
        cxp = (fx * w + t * (5 + i * 2)) % (w + 240) - 120
        cy = fy * h; cw = 130 * sc; ch = 26 * sc
        p.setBrush(QColor(255, 255, 255, 14))
        p.drawEllipse(QPointF(cxp, cy), cw, ch)

    if not sea:
        return                                    # sky only – no water, horizon or waves

    # Sea
    sea_grad = QLinearGradient(0, horizon, 0, h)
    sea_grad.setColorAt(0.0, QColor(theme.SEA_TOP)); sea_grad.setColorAt(1.0, QColor(theme.SEA_DEEP))
    p.fillRect(0, horizon, w, h - horizon, sea_grad)

    # Hard amber horizon rule – the single accent
    p.setPen(QPen(QColor(theme.ACCENT), 1)); p.setOpacity(0.5)
    p.drawLine(0, horizon, w, horizon); p.setOpacity(1.0)

    # Ships drifting across the water, each on its own lane. Only x depends on
    # time, so they slide steadily and wrap around off-screen. (No stationary
    # horizon fleet – the menu's single hero flagship is the only anchored ship.)
    if sprites is not None:
        for i, (key, lane, spd, dr) in enumerate(movers):
            px = sprites.get(f"player_{key}")
            if not px or px.isNull():
                continue
            # Size from the sprite's own width so ships keep their true relative
            # scale (a battleship dwarfs a patrol boat); depth only nudges nearer
            # lanes a little larger. Height box is generous so width always binds
            # and the real aspect ratio is preserved.
            fw = int(px.width() * (0.30 + lane * 0.45))
            mv = _mover_pixmap(px, fw, fw, flip=(dr < 0))
            span = w + mv.width() + 80                # off-screen margin both ends
            off = (spd * t + i * 227.0) % span         # staggered so they spread out
            x = (off - mv.width() - 40) if dr > 0 else (w + 40 - off)
            bob = math.sin(t * 0.8 + i * 1.7) * 2.5
            # Float ON the surface using the SAME waterline the battle uses for a
            # surface ship (top = water_y − 0.85·height, i.e. ~15% of the hull
            # under water). Nearer lanes ride a hair lower for depth.
            y = horizon - mv.height() * 0.85 + lane * 10
            p.drawPixmap(int(x), int(y + bob), mv)

    # Wave lines – 12px sampling is plenty for the ≥150px wavelengths and keeps
    # this per-frame Python loop cheap at the 60fps menu tick.
    for i in range(4):
        amp = 3.0 - i * .5; wl = 150 + i * 50; alpha = 70 - i * 12
        p.setPen(QPen(QColor(255, 255, 255, alpha), 1.4 - i * .2))
        path = QPainterPath()
        for xx in range(0, w + 12, 12):
            yy = horizon + 14 + i * 16 + amp * math.sin(xx / wl * math.pi * 2 + t * (1.0 - i * .15))
            path.moveTo(xx, yy) if xx == 0 else path.lineTo(xx, yy)
        p.drawPath(path)


# ── Bomber run over the save-select sky ─────────────────────────────────────────
# The camera has panned all the way up off the waterline: nothing but open sky,
# and a lone carpet bomber holds it, crossing on a loop.
_BOMB_SPEED  = 74.0                     # bomber ground speed, px/s
_SKY_SIL_TINT = QColor(60, 80, 99, 236)  # lighter steel so the bomber reads on sky


def paint_bomber_scene(p: QPainter, w: int, h: int, sprites=None, t: float = 0.0):
    # Pure sky – the water is gone, so is the horizon; just clouds and the bomber.
    # This screen sits one full frame above the menu, so it samples the shared sky
    # gradient a frame higher (offset −h): its bottom edge is SKY_TOP, exactly the
    # colour of the menu's top edge, so the two are seamless as they slide past.
    paint_navy_scene(p, w, h, sprites, t, movers=(), sea=False, sky_base_offset=-float(h))
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # A small, distant bomber – a fraction of its old size.
    bw = max(56, int(w * 0.055)); bh = bw
    px = sprites.get("player_bomber") if sprites else None
    mv = None
    if px and not px.isNull():
        mv = _mover_pixmap(px, bw, bw, flip=False, tint=_SKY_SIL_TINT)
        bw, bh = mv.width(), mv.height()
    span = w + bw + 160                  # wrap span, with off-screen margin
    # Cruise through the title band so it passes *behind* the "PENUMBRA"
    # lockup – the title is a child widget, drawn over this backdrop, so the
    # bomber slips behind the letters. ~92px is the title's vertical centre.
    alt = 92 - bh / 2

    # ── The bomber itself, crossing left→right and bobbing gently ────────────
    if mv is not None:
        bx = ((_BOMB_SPEED * t) % span) - bw - 80
        by = alt + math.sin(t * 0.9) * 3.0
        p.setOpacity(0.96); p.drawPixmap(int(bx), int(by), mv); p.setOpacity(1.0)
