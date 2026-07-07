"""
game.ui.backdrop — the battlefield sky/sea, skinned in the menu's war-room register.

The main menu (game.ui.scenery) paints a moody nocturnal navy scene: a steel sky
gradient, faint drifting clouds, a celestial disc, a single amber horizon rule and
subtle white wave lines. The battle map used to be a bright, cartoon-blue daylight
scene that clashed with it. This module gives the battle the same instrument-panel
mood — and ramps it across the campaign as a *time of day*: the opening operations
fight under an overcast steel daylight, the mid-campaign at dusk, the late fights at
a burning sunset, and the final operations at full night — the exact palette of the
main-menu backdrop. Later levels are darker. One level goes further still: the "No
Contact" finale opts into the ABYSS skin — past night, an oil-black dead sea under a
smouldering ember horizon and a cold wan moon.

A `Backdrop` is a flat bundle of colours + one celestial body; `for_level` picks the
band from the level's campaign `order` (or a named skin the level requests). `paint` / `paint_waves` reproduce the battle's
old parallax clouds and animated wave lines, just re-skinned from the chosen band.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, replace
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui  import QColor, QPen, QLinearGradient, QRadialGradient, QPainterPath

from . import theme


@dataclass(frozen=True)
class Backdrop:
    """A single time-of-day skin for the battle backdrop. Colours are hex strings;
    the celestial body is a low disc (sun or moon) with a soft halo."""
    name:          str
    night:         float    # 0.0 = full daylight ‥ 1.0 = full night. Drives the
                            #   night-only glow of tracer rounds and launched orbs
                            #   (see game.entities.projectile) — muzzle fire and
                            #   incandescent shells only "light up" after dark.
    sky_top:       str      # upper sky
    sky_horizon:   str      # sky colour meeting the waterline
    sea_top:       str      # water just below the horizon
    sea_deep:      str      # deep water — also the tint forts/rigs/mines fog into
    cloud:         str      # cloud fill colour
    cloud_alpha:   int      # how present the cloud banks read (night = barely there)
    wave_alpha:    int      # brightest wave line's alpha (steps down per line)
    horizon:       str      # the single horizon rule accent (amber at night)
    horizon_alpha: int
    # Celestial disc: kind, position (fraction of W / of the waterline WY), radius
    # (fraction of H) and its two-stop disc gradient + halo colour.
    cel_kind:  str          # "sun" | "moon"
    cel_fx:    float
    cel_fy:    float        # fraction of the waterline height (0 = top, 1 = waterline)
    cel_r:     float
    cel_lit:   str          # disc centre (lit side)
    cel_dark:  str          # disc edge
    cel_halo:  str          # halo bloom colour
    cel_eclipse: bool = False  # if set, the disc is drawn as a total lunar eclipse:
                            #   a dark umbral shadow bitten across the blood-red disc
                            #   with a fiery refraction crescent on its lit limb.

    @property
    def water_tint(self) -> str:
        """The deep-water hex the rest of the scene fogs into — forts' submerged
        caissons, oil-rig legs, sea mines. Kept in lockstep with the water gradient."""
        return self.sea_deep


# ── The four bands, brightest (early campaign) → darkest (late) ───────────────────
# NIGHT is the main menu's own palette (theme.SKY_TOP/…/SEA_DEEP + amber rule + a
# pale steel moon), so the final operations look exactly like the home screen.

_DAY = Backdrop(
    name="DAY", night=0.0,
    sky_top="#47617a", sky_horizon="#9db9cc",
    sea_top="#1d5476", sea_deep="#082a3f",
    cloud="#dce7ee", cloud_alpha=46, wave_alpha=95,
    horizon="#aebfca", horizon_alpha=110,
    cel_kind="sun", cel_fx=0.80, cel_fy=0.28, cel_r=0.05,
    cel_lit="#eef4f8", cel_dark="#c6d4de", cel_halo="#e2edf4",
)

_DUSK = Backdrop(
    name="DUSK", night=0.4,
    sky_top="#2b3a4e", sky_horizon="#b98a5e",
    sea_top="#234a63", sea_deep="#071f2e",
    cloud="#c9b48f", cloud_alpha=40, wave_alpha=76,
    horizon="#e0a24a", horizon_alpha=140,
    cel_kind="sun", cel_fx=0.75, cel_fy=0.66, cel_r=0.055,
    cel_lit="#ffe6b0", cel_dark="#f0a94e", cel_halo="#ffc98a",
)

_SUNSET = Backdrop(
    name="SUNSET", night=0.7,
    sky_top="#231d33", sky_horizon="#d1592e",
    sea_top="#3f2f42", sea_deep="#0a1420",
    cloud="#e08a4f", cloud_alpha=44, wave_alpha=62,
    horizon="#ff7a34", horizon_alpha=170,
    cel_kind="sun", cel_fx=0.72, cel_fy=0.90, cel_r=0.075,
    cel_lit="#ffd888", cel_dark="#ff6a2c", cel_halo="#ff7a3a",
)

_NIGHT = Backdrop(
    name="NIGHT", night=1.0,
    sky_top=theme.SKY_TOP, sky_horizon=theme.SKY_LOW,
    sea_top=theme.SEA_TOP, sea_deep=theme.SEA_DEEP,
    cloud="#ffffff", cloud_alpha=14, wave_alpha=70,
    horizon=theme.ACCENT, horizon_alpha=128,
    cel_kind="moon", cel_fx=0.80, cel_fy=0.28, cel_r=0.045,
    cel_lit="#eaf1f8", cel_dark="#becbda", cel_halo="#cedaea",
)

# ── The finale skin ──────────────────────────────────────────────────────────────
# ABYSS is *past* night: not a time of day but a mood. The "No Contact" finale — a
# floating fortress nothing on record matches — fights under a dead, oil-black sea and
# a heavy overcast, its horizon smouldering a dull ember-red. Overhead hangs a moon in
# total eclipse: a blood-red disc bitten by the Earth's umbral shadow, ringed by a
# fiery refraction crescent — an omen over the last operation. Levels opt in by naming
# `"backdrop": "abyss"` in their JSON; it is NOT reached from `order`, so ordinary
# night levels and sandbox battles never see it.
_ABYSS = Backdrop(
    name="ABYSS", night=1.0,
    sky_top="#0a0d12", sky_horizon="#2c1519",
    sea_top="#0c161a", sea_deep="#04090c",
    cloud="#161a20", cloud_alpha=64, wave_alpha=32,
    horizon="#7d2b28", horizon_alpha=150,
    cel_kind="moon", cel_fx=0.80, cel_fy=0.30, cel_r=0.05,
    cel_lit="#b8402a", cel_dark="#5a140e", cel_halo="#d1281a", cel_eclipse=True,
)

# Campaign order → band. Orders run 1‥12; four even bands, darkening as they climb.
# Anything past the campaign (sandbox / one-off battles default order 100) fights at
# night, matching the menu.
_BANDS = [_DAY, _DUSK, _SUNSET, _NIGHT]

# Named skins a level may request explicitly via `LevelDef.backdrop`.
_NAMED = {"abyss": _ABYSS}

# Human-facing time-of-day names, keyed by the band's internal `name`. Used where a
# map is chosen for its light rather than its campaign story — the multiplayer map
# picker, where the boss/operation title means nothing to two duelling fleets.
_TOD_LABELS = {
    "DAY":    "Day",
    "DUSK":   "Early Sunset",
    "SUNSET": "Sunset",
    "NIGHT":  "Night",
    "ABYSS":  "Dead of Night",
}


def label_for_level(order: int, skin: str | None = None) -> str:
    """The time-of-day name for a level's map, mirroring `for_level`'s band choice.
    This is the label shown in multiplayer, where fleets pick a battleground by its
    time of day, not its campaign operation."""
    return _TOD_LABELS.get(for_level(order, skin=skin).name, "Night")


def for_level(order: int, water_tint: str | None = None,
              skin: str | None = None) -> Backdrop:
    """Pick the backdrop for a level. Normally this is the time-of-day band for the
    campaign `order` (1-based); a level may instead name a special `skin` (e.g.
    "abyss" for the ominous finale) to bypass the ramp entirely. Either way, a level
    that pins an explicit `water_tint` keeps it — its deep water (and everything that
    fogs into it) uses that colour while still taking the skin's sky and light."""
    bd = _NAMED.get(skin) if skin else None
    if bd is None:
        idx = min(len(_BANDS) - 1, max(0, (int(order) - 1) // 3))
        bd = _BANDS[idx]
    if water_tint:
        deep = QColor(water_tint)
        bd = replace(bd, sea_deep=water_tint, sea_top=deep.lighter(150).name())
    return bd


# ── Painting ─────────────────────────────────────────────────────────────────────
def _paint_celestial(p, bd: Backdrop, W: int, WY: int):
    cx = bd.cel_fx * W
    cy = bd.cel_fy * WY
    r  = max(14.0, bd.cel_r * (WY / 0.58))     # r spec is a fraction of full height
    p.setPen(Qt.PenStyle.NoPen)
    if bd.cel_eclipse:
        _paint_eclipse(p, bd, cx, cy, r)
        return
    # Soft halo bleeding into the sky.
    halo = QColor(bd.cel_halo)
    g = QRadialGradient(QPointF(cx, cy), r * 3.6)
    g.setColorAt(0.0, QColor(halo.red(), halo.green(), halo.blue(), 70))
    g.setColorAt(0.4, QColor(halo.red(), halo.green(), halo.blue(), 26))
    g.setColorAt(1.0, QColor(halo.red(), halo.green(), halo.blue(), 0))
    p.setBrush(g); p.drawEllipse(QPointF(cx, cy), r * 3.6, r * 3.6)
    # The disc, lit a touch brighter toward the upper-left.
    disc = QRadialGradient(QPointF(cx - r * 0.3, cy - r * 0.3), r * 1.7)
    disc.setColorAt(0.0, QColor(bd.cel_lit)); disc.setColorAt(1.0, QColor(bd.cel_dark))
    p.setBrush(disc); p.drawEllipse(QPointF(cx, cy), r, r)
    if bd.cel_kind == "moon":
        # A few faint maria so it reads as a moon, not a flat dot.
        p.setBrush(QColor(176, 190, 207, 70))
        p.drawEllipse(QPointF(cx + r * 0.30, cy - r * 0.16), r * 0.26, r * 0.22)
        p.drawEllipse(QPointF(cx - r * 0.28, cy + r * 0.26), r * 0.18, r * 0.16)


def _paint_eclipse(p, bd: Backdrop, cx: float, cy: float, r: float):
    """A moon in total eclipse: a black body fully occluded, ringed by a glowing red
    corona — a dark disc with a burning red outline. cel_halo drives the ring colour."""
    ring = QColor(bd.cel_halo)
    rr, rg, rb = ring.red(), ring.green(), ring.blue()

    # Outer corona bleeding into the black sky, brightest right at the rim.
    outer = QRadialGradient(QPointF(cx, cy), r * 2.6)
    outer.setColorAt(0.0, QColor(rr, rg, rb, 0))
    outer.setColorAt(r / (r * 2.6), QColor(rr, rg, rb, 0))   # transparent inside the body
    outer.setColorAt(0.42, QColor(rr, rg, rb, 130))          # peak just outside the rim
    outer.setColorAt(0.7, QColor(rr, rg, rb, 40))
    outer.setColorAt(1.0, QColor(rr, rg, rb, 0))
    p.setBrush(outer); p.drawEllipse(QPointF(cx, cy), r * 2.6, r * 2.6)

    # The bright ring of fire hugging the limb — a thin band drawn as an annulus.
    ringw = max(2.0, r * 0.14)
    band = QRadialGradient(QPointF(cx, cy), r + ringw)
    inner_stop = (r - ringw) / (r + ringw)
    band.setColorAt(0.0, QColor(rr, rg, rb, 0))
    band.setColorAt(max(0.0, inner_stop - 0.02), QColor(rr, rg, rb, 0))
    band.setColorAt(inner_stop, QColor(255, 90, 60, 90))
    band.setColorAt(r / (r + ringw), QColor(255, 140, 90, 255))   # crest at the true rim
    band.setColorAt(1.0, QColor(rr, rg, rb, 0))
    p.setBrush(band); p.drawEllipse(QPointF(cx, cy), r + ringw, r + ringw)

    # The fully occluded body — near-black, faintly warm so it isn't a dead hole.
    body = QRadialGradient(QPointF(cx, cy), r)
    body.setColorAt(0.0, QColor(6, 3, 3))
    body.setColorAt(0.82, QColor(9, 4, 4))
    body.setColorAt(1.0, QColor(20, 6, 5))
    p.setBrush(body); p.drawEllipse(QPointF(cx, cy), r, r)


def paint(p, bd: Backdrop, W: int, H: int, WY: int, cam_x: float, clouds, wave_t: float):
    """Sky gradient → celestial disc → parallax clouds → sea gradient → horizon rule.
    `clouds` is the battle's list of {wx,y,w,h} banks; they drift with the camera."""
    from ..config import WORLD_W

    # Sky
    sky = QLinearGradient(0, 0, 0, WY)
    sky.setColorAt(0.0, QColor(bd.sky_top)); sky.setColorAt(1.0, QColor(bd.sky_horizon))
    p.fillRect(0, 0, W, WY, sky)

    _paint_celestial(p, bd, W, WY)

    # Parallax cloud banks (three-lobe puffs, faint), colour from the band.
    p.save(); p.setPen(Qt.PenStyle.NoPen)
    col = QColor(bd.cloud); col.setAlpha(bd.cloud_alpha); p.setBrush(col)
    for c in clouds:
        sx = (c["wx"] - cam_x * 0.15) % (WORLD_W + 200)
        if -250 < sx < W + 250:
            cw, ch = c["w"], c["h"]
            p.drawEllipse(int(sx - cw * .5),  int(c["y"] - ch * .5),  int(cw),       int(ch))
            p.drawEllipse(int(sx - cw * .72), int(c["y"] - ch * .42), int(cw * .60), int(ch * .76))
            p.drawEllipse(int(sx + cw * .02), int(c["y"] - ch * .38), int(cw * .52), int(ch * .68))
    p.restore()

    # Sea
    wg = QLinearGradient(0, WY, 0, H)
    wg.setColorAt(0.0, QColor(bd.sea_top)); wg.setColorAt(1.0, QColor(bd.sea_deep))
    p.fillRect(0, WY, W, H - WY, wg)

    # The single horizon rule — the menu's amber hairline (band-coloured), plus a
    # faint glow just under it where sky light catches the water.
    glow = QColor(bd.horizon)
    hg = QLinearGradient(0, WY - 10, 0, WY + 6)
    hg.setColorAt(0.0, QColor(glow.red(), glow.green(), glow.blue(), 0))
    hg.setColorAt(1.0, QColor(glow.red(), glow.green(), glow.blue(), 70))
    p.fillRect(0, WY - 10, W, 16, hg)
    pen = QPen(QColor(bd.horizon), 1); p.setPen(pen)
    p.setOpacity(bd.horizon_alpha / 255.0)
    p.drawLine(0, WY, W, WY); p.setOpacity(1.0)


def paint_waves(p, bd: Backdrop, W: int, WY: int, cam_x: float, wave_t: float):
    """The animated white wave lines — same motion as before, alpha from the band
    (night is subtlest, matching the menu)."""
    for i in range(4):
        amp = 3.5 - i * .55; wl = 105 + i * 32; speed = 1.25 - i * .22
        alpha = max(0, bd.wave_alpha - i * 40)
        p.setPen(QPen(QColor(255, 255, 255, alpha), 1.5 - i * .22))
        path = QPainterPath()
        for sx in range(0, W + 8, 7):
            wy = WY + i * 8 + amp * math.sin((sx + cam_x) / wl * math.pi * 2 - wave_t * speed)
            path.moveTo(sx, wy) if sx == 0 else path.lineTo(sx, wy)
        p.drawPath(path)
