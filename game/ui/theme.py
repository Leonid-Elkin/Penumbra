"""
game.ui.theme — the visual system: "The Plotting Table".

Aesthetic: a naval war-room, not a website. We are stood over a steel plotting
table in a fortress command centre, directing capital ships against named
leviathans. Everything is built from painted battleship plate: chamfered
(corner-cut) panels, rivets, brushed gunmetal, engraved name-plates, stencilled
hull markings and hazard chevrons.

Palette is a THREE-SIGNAL instrument system, never decoration:
  • signal amber   — command / action / the player's hand
  • phosphor green  — friendly / ready / live telemetry
  • danger red      — hostile / warning

Typography is an instrument too:
  • Bahnschrift (DIN)  — labels & headers
  • Stencil            — hull markings, class names, the wordmark
  • Consolas (mono)    — every numeric readout (the plotting-room data font)
  • Segoe UI           — body copy

Deliberately NOT Inter / Roboto / Arial, no rounded cards, no glassmorphism,
no purple, no floating white panels.
"""

from __future__ import annotations
import math, os
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui  import (QColor, QPen, QBrush, QPainterPath, QFont, QPolygonF,
                          QLinearGradient, QPixmap, QPainter, QRadialGradient,
                          QImage)

# ─── Palette: cold gunmetal + a three-signal instrument set ───────────────────
BG_DEEP   = "#070a0d"            # the void behind the plate
BG        = "#0d1216"
PANEL     = "#151b21"            # painted plate base
PANEL_HI  = "#1d252c"
STEEL     = "#232d35"            # a raised / lit plate
STEEL_HI  = "#324049"
LINE      = "#2b343c"
LINE_HI   = "#475762"
EDGE_HI   = "#5f7280"            # bevel highlight (top-left catch of light)
EDGE_LO   = "#0a0e12"            # bevel shadow  (bottom-right)
TEXT      = "#e9eef1"
TEXT_DIM  = "#8794a0"
TEXT_FAINT = "#4a5762"

ACCENT    = "#f0a81e"            # signal amber — command / action
GOLD      = ACCENT
AMBER_HI  = "#ffc648"
STAR      = "#f5b52b"
PHOSPHOR  = "#46d18a"            # sonar green — friendly / ready / live
GOOD      = "#3fae6b"
DANGER    = "#d24338"
DANGER_HI = "#ff6a5c"

SEA_TOP   = "#123650"
SEA_DEEP  = "#06141d"
SKY_TOP   = "#101820"
SKY_LOW   = "#25323c"

HEAD_FAMILY    = "Bahnschrift"   # industrial DIN-style
BODY_FAMILY    = "Segoe UI"
STENCIL_FAMILY = "Stencil"       # military stencil — hull markings & wordmark
MONO_FAMILY    = "Consolas"      # plotting-room data font (all numerics)


def head(size: int, spacing: float = 1.5) -> QFont:
    f = QFont(HEAD_FAMILY, size); f.setBold(True)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f

def font(size: int, bold: bool = False) -> QFont:
    f = QFont(BODY_FAMILY, size); f.setBold(bold)
    return f

def stencil(size: int, spacing: float = 2.0) -> QFont:
    f = QFont(STENCIL_FAMILY, size)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    # Bahnschrift is the graceful fallback if Stencil is unavailable.
    f.setStyleHint(QFont.StyleHint.SansSerif)
    return f

def mono(size: int, bold: bool = False) -> QFont:
    f = QFont(MONO_FAMILY, size); f.setBold(bold)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


# ─── Brushed-gunmetal texture (generated once, tiled everywhere) ──────────────
_metal_cache: dict = {}

def _brushed_tile(base: QColor) -> QPixmap:
    """A small vertically-brushed metal tile keyed by base colour. Cheap streaks
    of light/dark so plate fills read as painted steel, not flat colour."""
    key = base.name()
    hit = _metal_cache.get(key)
    if hit is not None:
        return hit
    W, H = 64, 64
    pm = QPixmap(W, H); pm.fill(base)
    q = QPainter(pm)
    # Deterministic pseudo-noise (no RNG → identical every run/frame).
    for x in range(W):
        n = (math.sin(x * 12.9898) * 43758.5453)
        n -= math.floor(n)                       # fract → 0..1
        d = int((n - 0.5) * 22)                  # ±11 lightness streak
        c = QColor(base).lighter(100 + d) if d >= 0 else QColor(base).darker(100 - d)
        c.setAlpha(70)
        q.setPen(QPen(c, 1)); q.drawLine(x, 0, x, H)
    q.end()
    _metal_cache[key] = pm
    return pm


# ─── Photographic steel textures (real plate scans, retuned to the palette) ───
# Two scanned surfaces live in Textures/:  a worn painted-steel plate and a
# diamond tread ("chequer") plate.  Raw they are bright bare aluminium — far too
# light for our cold gunmetal war-room — so we never blit them straight.  Both are
# desaturated to neutral grey and used only as *relief*: the steel scan is Overlay-
# composited onto a plate's own colour (worn mottling, no colour of its own), and
# the tread plate is laid down then Multiply-tinted to gunmetal (a dark structural
# kick-plate).  This keeps them material tells, not wallpaper.
_TEX_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "Textures")
STEEL_TEX   = "steel.jpg"
DIAMOND_TEX = "Steel diamond.jpg"
_tex_cache: dict = {}

def _grey_tile(filename: str, tile: int) -> QPixmap:
    """Load a texture scan, centre-crop to square, scale to a `tile`-px tile and
    desaturate to neutral grey (its luminance carries the relief; colour is dropped
    so it composites cleanly over any palette colour). Cached; degrades to a flat
    mid-grey tile if the file is missing so the UI never hard-fails."""
    key = (filename, tile)
    hit = _tex_cache.get(key)
    if hit is not None:
        return hit
    src = QImage(os.path.join(_TEX_DIR, filename))
    if src.isNull():
        pm = QPixmap(tile, tile); pm.fill(QColor(128, 128, 128))
        _tex_cache[key] = pm
        return pm
    s = min(src.width(), src.height())                     # centre square crop
    src = src.copy((src.width() - s) // 2, (src.height() - s) // 2, s, s)
    src = src.scaled(tile, tile, Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    grey = src.convertToFormat(QImage.Format.Format_Grayscale8)
    pm = QPixmap.fromImage(grey)
    _tex_cache[key] = pm
    return pm

def steel_texture(tile: int = 220) -> QPixmap:
    """Grey worn-plate tile — Overlay-composited onto plate fills as subtle wear."""
    return _grey_tile(STEEL_TEX, tile)

def diamond_texture(tile: int = 128) -> QPixmap:
    """Grey diamond tread-plate tile — the structural kick-plate relief."""
    return _grey_tile(DIAMOND_TEX, tile)


def steel_wear(p: QPainter, rect: QRectF, opacity: float = 0.13, tile: int = 220):
    """Overlay real worn-steel relief onto an already-painted surface. Caller must
    have the target region clipped (e.g. a chamfer path) before calling — this only
    adds light/dark mottling, it does not fill or colour."""
    p.save()
    p.setOpacity(opacity)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Overlay)
    p.drawTiledPixmap(rect, steel_texture(tile))
    p.restore()


def tread_band(p: QPainter, rect: QRectF, *, tint=STEEL, strength: float = 1.0,
               edges: bool = True, hazard=None):
    """A diamond tread-plate strip — a real floor/kick-plate for structural bands
    (footers, sills, header rails). The bright scan is laid down then Multiply-tinted
    to gunmetal so the chequer relief survives but the colour is ours. `tint` sets
    the plate colour; `hazard` (a QColor) stripes the top few px as a caution rail;
    `edges` adds the machined highlight/shadow lip top and bottom."""
    p.save()
    p.setClipRect(rect)
    tex = diamond_texture()
    p.drawTiledPixmap(rect, tex)                                  # bright relief
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
    p.fillRect(rect, QColor(tint))                                # tint to gunmetal
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    if strength < 1.0:                                            # flatten contrast
        f = QColor(tint); f.setAlpha(int((1.0 - strength) * 255))
        p.fillRect(rect, f)
    # a soft top-lit gradient so the band reads as a horizontal surface catching light
    g = QLinearGradient(0, rect.top(), 0, rect.bottom())
    g.setColorAt(0.0, QColor(255, 255, 255, 20)); g.setColorAt(0.5, QColor(0, 0, 0, 0))
    g.setColorAt(1.0, QColor(0, 0, 0, 60)); p.fillRect(rect, g)
    if hazard is not None:
        hazard_bar(p, QRectF(rect.left(), rect.top(), rect.width(), 3),
                   color=hazard, alpha=180)
    if edges:
        p.setPen(QPen(QColor(EDGE_HI), 1)); p.setOpacity(0.5)
        p.drawLine(QPointF(rect.left(), rect.top() + 0.5),
                   QPointF(rect.right(), rect.top() + 0.5))
        p.setPen(QPen(QColor(EDGE_LO), 1)); p.setOpacity(0.7)
        p.drawLine(QPointF(rect.left(), rect.bottom() - 0.5),
                   QPointF(rect.right(), rect.bottom() - 0.5))
    p.restore()


# ─── Geometry: chamfered (corner-cut) plate silhouette ────────────────────────
def chamfer_path(x, y, w, h, cut=9, corners=(True, False, False, True)) -> QPainterPath:
    """A rectangle with selected corners sliced off. Default cuts the top-left and
    bottom-right — an asymmetric, mechanical silhouette that reads as hardware.
    corners = (top-left, top-right, bottom-right, bottom-left)."""
    tl, tr, br, bl = corners
    path = QPainterPath()
    path.moveTo(x + (cut if tl else 0), y)
    path.lineTo(x + w - (cut if tr else 0), y)
    if tr: path.lineTo(x + w, y + cut)
    path.lineTo(x + w, y + h - (cut if br else 0))
    if br: path.lineTo(x + w - cut, y + h)
    path.lineTo(x + (cut if bl else 0), y + h)
    if bl: path.lineTo(x, y + h - cut)
    path.lineTo(x, y + (cut if tl else 0))
    if tl: path.lineTo(x + cut, y)
    path.closeSubpath()
    return path


def rivets(p: QPainter, x, y, w, h, inset=7, step=34, r=1.6, color=None):
    """A row of countersunk rivets around a plate edge — the material tell that
    says 'welded steel'. Drawn as a dark pit with a faint top-left highlight."""
    col = color or QColor(EDGE_HI)
    def dot(cx, cy):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(EDGE_LO)); p.drawEllipse(QPointF(cx, cy), r + 0.7, r + 0.7)
        p.setBrush(col);             p.drawEllipse(QPointF(cx - 0.4, cy - 0.4), r, r)
    xs = list(range(int(x + inset), int(x + w - inset) + 1, step))
    ys = list(range(int(y + inset), int(y + h - inset) + 1, step))
    for cx in xs:
        dot(cx, y + inset); dot(cx, y + h - inset)
    for cy in ys[1:-1] if len(ys) > 2 else []:
        dot(x + inset, cy); dot(x + w - inset, cy)


def hazard_bar(p: QPainter, rect: QRectF, color=None, bg=None, stripe=11, alpha=210):
    """Diagonal hazard chevrons — the 'live / armed / caution' marking on a control.
    Clipped to `rect`; only painted where it matters (never wallpaper)."""
    col = QColor(color or ACCENT); col.setAlpha(alpha)
    p.save(); p.setClipRect(rect)
    if bg is not None:
        p.fillRect(rect, QColor(bg))
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(col)
    x0 = int(rect.left() - rect.height()); x1 = int(rect.right() + rect.height())
    for x in range(x0, x1, stripe * 2):
        poly = QPolygonF([
            QPointF(x, rect.bottom()), QPointF(x + stripe, rect.bottom()),
            QPointF(x + stripe + rect.height(), rect.top()),
            QPointF(x + rect.height(), rect.top())])
        p.drawPolygon(poly)
    p.restore()


def plate(p: QPainter, x, y, w, h, *, base=PANEL, cut=9,
          corners=(True, False, False, True), lit=False, brushed=True,
          accent=None, accent_side="top", riveted=False, border=None,
          textured=False, tex_opacity=0.13):
    """The core material primitive: paint a chamfered steel plate with a brushed
    fill, a top-left→bottom-right bevel (embossed metal), an optional coloured
    signal edge and optional rivets. Everything else in the UI is built from this.

    `textured=True` Overlay-composites a real worn-steel scan into the fill — reserve
    it for large, calm backing plates where the extra grain reads (not small chips)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = chamfer_path(x, y, w, h, cut, corners)
    p.setClipPath(path)

    b = QColor(base).lighter(116) if lit else QColor(base)
    p.fillRect(QRectF(x, y, w, h), b)
    if brushed:
        p.setOpacity(0.5)
        p.drawTiledPixmap(QRectF(x, y, w, h), _brushed_tile(b))
        p.setOpacity(1.0)
    if textured:
        steel_wear(p, QRectF(x, y, w, h), opacity=tex_opacity)
    # top-down painted-steel gradient for depth
    g = QLinearGradient(0, y, 0, y + h)
    g.setColorAt(0.0, QColor(255, 255, 255, 16))
    g.setColorAt(0.10, QColor(255, 255, 255, 6))
    g.setColorAt(0.55, QColor(0, 0, 0, 0))
    g.setColorAt(1.0, QColor(0, 0, 0, 55))
    p.fillRect(QRectF(x, y, w, h), g)
    p.setClipping(False)

    # bevel: light catches the top/left edge, shadow on bottom/right
    p.setBrush(Qt.BrushStyle.NoBrush)
    edge = QColor(border) if border else QColor(LINE_HI if lit else LINE)
    p.setPen(QPen(edge, 1)); p.drawPath(path)
    p.setPen(QPen(QColor(EDGE_HI if lit else EDGE_LO), 1))
    p.setOpacity(0.55 if lit else 0.35)
    p.drawLine(QPointF(x + cut, y + 1), QPointF(x + w - 2, y + 1))     # top catch
    p.drawLine(QPointF(x + 1, y + cut), QPointF(x + 1, y + h - 2))     # left catch
    p.setOpacity(1.0)

    if accent:
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(accent))
        if accent_side == "top":
            p.drawRect(QRectF(x + cut, y, w - cut - (cut if corners[1] else 0), 3))
        elif accent_side == "left":
            p.drawRect(QRectF(x, y + cut, 3, h - cut - (cut if corners[3] else 0)))
    if riveted:
        rivets(p, x, y, w, h)
    p.restore()
    return path


def engraved_label(p: QPainter, text: str, x, y, w, f: QFont,
                   color=TEXT, shadow=True, flags=None):
    """Text with a 1px dark drop — an engraved / stamped plate reading. `y` is the
    top of the text; height is kept tight so callers can position deterministically."""
    p.setFont(f)
    flags = (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop) if flags is None else flags
    h = int(f.pointSize() * 1.5) + 10
    if shadow:
        p.setPen(QColor(0, 0, 0, 160))
        p.drawText(QRectF(x + 1, y + 1, w, h), int(flags), text)
    p.setPen(QColor(color))
    p.drawText(QRectF(x, y, w, h), int(flags), text)


def led(p: QPainter, cx, cy, color, r=3.2, on=True):
    """A small indicator lamp with a soft phosphor bloom — the only 'glow' we
    allow, because instrument lamps genuinely glow."""
    c = QColor(color)
    if on:
        g = QRadialGradient(cx, cy, r * 3.2)
        g.setColorAt(0.0, QColor(c.red(), c.green(), c.blue(), 150))
        g.setColorAt(1.0, QColor(c.red(), c.green(), c.blue(), 0))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(cx, cy), r * 3.2, r * 3.2)
        p.setBrush(c)
    else:
        p.setBrush(QColor(c.red(), c.green(), c.blue(), 60))
    p.setPen(QPen(QColor(0, 0, 0, 120), 1))
    p.drawEllipse(QPointF(cx, cy), r, r)


def scanlines(p: QPainter, rect: QRectF, gap=3, alpha=18):
    """Faint horizontal CRT lines for glass/screen surfaces (minimap, dossier
    photo). Used sparingly — a screen, not the whole UI."""
    p.save(); p.setClipRect(rect)
    p.setPen(QPen(QColor(0, 0, 0, alpha), 1))
    yy = int(rect.top())
    while yy < rect.bottom():
        p.drawLine(QPointF(rect.left(), yy), QPointF(rect.right(), yy)); yy += gap
    p.restore()


# ─── Stylesheets (kept for QPushButton consumers; hard-edged, steel + amber) ───
SCREEN_BG = f"background:{BG_DEEP};"

BTN = f"""
    QPushButton{{background:{PANEL};border:1px solid {LINE_HI};border-radius:0;
                color:{TEXT_DIM};font-family:'{HEAD_FAMILY}';font-size:13px;font-weight:bold;
                letter-spacing:2px;padding:9px 22px;}}
    QPushButton:hover{{border-color:{ACCENT};color:{ACCENT};background:{PANEL_HI};}}
    QPushButton:disabled{{border-color:{LINE};color:{TEXT_FAINT};background:{BG};}}
"""
BTN_PRIMARY = f"""
    QPushButton{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 {AMBER_HI}, stop:0.5 {ACCENT}, stop:1 #b47d0e);
                border:1px solid #7a560a;border-top:1px solid {AMBER_HI};border-radius:0;
                color:#100b02;font-family:'{HEAD_FAMILY}';font-size:14px;font-weight:bold;
                letter-spacing:3px;padding:11px 28px;}}
    QPushButton:hover{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #ffd873, stop:0.5 {AMBER_HI}, stop:1 {ACCENT});}}
    QPushButton:disabled{{background:{PANEL};border:1px solid {LINE};color:{TEXT_FAINT};}}
"""
BTN_SMALL = f"""
    QPushButton{{background:{PANEL};border:1px solid {LINE};border-radius:0;
                color:{TEXT_DIM};font-family:'{HEAD_FAMILY}';font-size:11px;
                letter-spacing:1px;padding:5px 14px;}}
    QPushButton:hover{{border-color:{ACCENT};color:{ACCENT};background:{PANEL_HI};}}
    QPushButton:disabled{{border-color:{LINE};color:{TEXT_FAINT};}}
"""

# ─── Stars ────────────────────────────────────────────────────────────────────
def star_path(cx, cy, r) -> QPainterPath:
    path = QPainterPath()
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = r if i % 2 == 0 else r * 0.45
        pt = QPointF(cx + math.cos(ang) * rad, cy + math.sin(ang) * rad)
        path.moveTo(pt) if i == 0 else path.lineTo(pt)
    path.closeSubpath()
    return path

def draw_stars(p, x, y, size, count, total=3):
    p.save()
    for i in range(total):
        cx = x + size * 0.5 + i * (size + 4); cy = y + size * 0.5
        path = star_path(cx, cy, size * 0.5)
        if i < count:
            # earned stars get a faint amber bloom so a 3-star clear reads as a medal
            g = QRadialGradient(cx, cy, size)
            g.setColorAt(0.0, QColor(245, 181, 43, 90)); g.setColorAt(1.0, QColor(245, 181, 43, 0))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(cx, cy), size, size)
            p.setBrush(QColor(STAR)); p.setPen(QPen(QColor(120, 84, 12), 1)); p.drawPath(path)
        else:
            p.setBrush(QColor(0, 0, 0, 60)); p.setPen(QPen(QColor(TEXT_FAINT), 1)); p.drawPath(path)
    p.restore()

# ─── Vector icons (hard line-art, no glows) ───────────────────────────────────
def draw_icon(p, name: str, rect: QRectF, color: QColor | None = None):
    color = color or QColor(TEXT)
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    cx, cy = x + w / 2, y + h / 2
    p.save()
    pen = QPen(color, max(1.4, w * 0.09)); pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    pen.setCapStyle(Qt.PenCapStyle.SquareCap)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)

    if name == "income":
        p.drawLine(QPointF(cx, y + h * .85), QPointF(cx, y + h * .25))
        p.drawLine(QPointF(cx - w * .18, y + h * .43), QPointF(cx, y + h * .25))
        p.drawLine(QPointF(cx + w * .18, y + h * .43), QPointF(cx, y + h * .25))
        p.setBrush(QColor(color)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRectF(cx - w * .1, y + h * .72, w * .2, w * .2))
    elif name == "fleet":
        # Two boat hulls stacked into the distance → the unit-count (Fleet) cap.
        for dx, dy, sc in ((w*.10, h*.24, 1.0), (-w*.06, -h*.04, 0.72)):
            hull = QPainterPath()
            hull.moveTo(cx - w*.30*sc + dx, cy + dy)
            hull.lineTo(cx + w*.30*sc + dx, cy + dy)
            hull.lineTo(cx + w*.18*sc + dx, cy + h*.17*sc + dy)
            hull.lineTo(cx - w*.18*sc + dx, cy + h*.17*sc + dy)
            hull.closeSubpath()
            p.drawPath(hull)
            p.drawLine(QPointF(cx + dx, cy + dy), QPointF(cx + dx, cy - h*.22*sc + dy))
    elif name == "storage":
        r = QRectF(x + w*.22, y + h*.3, w*.56, h*.48)
        p.drawRect(r); p.drawLine(QPointF(r.left(), r.top()+r.height()*.32),
                                  QPointF(r.right(), r.top()+r.height()*.32))
    elif name == "factory":
        p.drawRect(QRectF(x+w*.22, y+h*.45, w*.56, h*.33))
        for fx in (.34, .5, .66):
            p.drawLine(QPointF(x+w*fx, y+h*.45), QPointF(x+w*fx, y+h*.28))
    elif name == "armor":
        path = QPainterPath(); path.moveTo(cx, y + h*.18)
        path.lineTo(x + w*.78, y + h*.32); path.lineTo(x + w*.74, y + h*.62)
        path.lineTo(cx, y + h*.84); path.lineTo(x + w*.26, y + h*.62)
        path.lineTo(x + w*.22, y + h*.32); path.closeSubpath()
        p.drawPath(path)
    elif name == "lock":
        p.drawArc(QRectF(x+w*.32, y+h*.2, w*.36, h*.36).toRect(), 0, 180*16)
        p.setBrush(QColor(color)); p.setPen(QPen(color, 1))
        p.drawRect(QRectF(x+w*.28, y+h*.44, w*.44, h*.34))
    elif name == "play":
        tri = QPolygonF([QPointF(x+w*.3, y+h*.22), QPointF(x+w*.3, y+h*.78), QPointF(x+w*.8, cy)])
        p.setBrush(QColor(color)); p.setPen(Qt.PenStyle.NoPen); p.drawPolygon(tri)
    elif name == "back":
        p.drawLine(QPointF(x+w*.7, cy), QPointF(x+w*.3, cy))
        p.drawLine(QPointF(x+w*.45, cy-h*.18), QPointF(x+w*.3, cy))
        p.drawLine(QPointF(x+w*.45, cy+h*.18), QPointF(x+w*.3, cy))
    elif name == "anchor":
        p.drawEllipse(QPointF(cx, y+h*.24), w*.06, w*.06)
        p.drawLine(QPointF(cx, y+h*.3), QPointF(cx, y+h*.8))
        p.drawLine(QPointF(x+w*.34, y+h*.42), QPointF(x+w*.66, y+h*.42))
        p.drawArc(QRectF(x+w*.24, y+h*.5, w*.52, h*.42).toRect(), 200*16, 140*16)
    elif name == "mine":
        p.setBrush(QColor(color)); p.setPen(QPen(color, 1))
        p.drawEllipse(QPointF(cx, cy), w*.22, w*.22)
        p.setPen(QPen(color, max(1.0, w*.06)))
        for k in range(8):
            a = k * math.pi / 4
            p.drawLine(QPointF(cx+math.cos(a)*w*.24, cy+math.sin(a)*w*.24),
                       QPointF(cx+math.cos(a)*w*.36, cy+math.sin(a)*w*.36))
    elif name == "icbm":
        path = QPainterPath(); path.moveTo(cx, y+h*.16)
        path.lineTo(x+w*.64, y+h*.5); path.lineTo(x+w*.36, y+h*.5); path.closeSubpath()
        p.setBrush(QColor(color)); p.setPen(Qt.PenStyle.NoPen); p.drawPath(path)
        p.setPen(pen)
        p.drawLine(QPointF(cx, y+h*.5), QPointF(cx, y+h*.82))
        p.drawLine(QPointF(x+w*.4, y+h*.84), QPointF(x+w*.6, y+h*.84))
    elif name == "oilrig":
        p.drawLine(QPointF(x+w*.3, y+h*.82), QPointF(cx, y+h*.2))
        p.drawLine(QPointF(x+w*.7, y+h*.82), QPointF(cx, y+h*.2))
        p.drawLine(QPointF(x+w*.38, y+h*.55), QPointF(x+w*.62, y+h*.55))
        p.drawLine(QPointF(x+w*.32, y+h*.82), QPointF(x+w*.68, y+h*.82))
    elif name == "fullscreen":
        # four corner brackets opening outward — 'expand to full screen'
        c = 0.24
        for ox, oy, sx, sy in ((.2, .2, 1, 1), (.8, .2, -1, 1),
                               (.2, .8, 1, -1), (.8, .8, -1, -1)):
            px, py = x + w*ox, y + h*oy
            p.drawLine(QPointF(px, py), QPointF(px + sx*w*c, py))
            p.drawLine(QPointF(px, py), QPointF(px, py + sy*h*c))
    elif name == "windowed":
        # four corner brackets folded inward — 'restore window'
        c = 0.24
        for ox, oy, sx, sy in ((.42, .42, -1, -1), (.58, .42, 1, -1),
                               (.42, .58, -1, 1), (.58, .58, 1, 1)):
            px, py = x + w*ox, y + h*oy
            p.drawLine(QPointF(px, py), QPointF(px + sx*w*c, py))
            p.drawLine(QPointF(px, py), QPointF(px, py + sy*h*c))
    elif name == "settings":
        # a gear: toothed ring around a hub
        r = w * 0.26
        for k in range(8):
            a = k * math.pi / 4
            p.drawLine(QPointF(cx + math.cos(a) * r * 0.8, cy + math.sin(a) * r * 0.8),
                       QPointF(cx + math.cos(a) * r * 1.35, cy + math.sin(a) * r * 1.35))
        p.drawEllipse(QPointF(cx, cy), r * 0.8, r * 0.8)
        p.drawEllipse(QPointF(cx, cy), r * 0.3, r * 0.3)
    elif name == "salvage":
        # Value dropping back into the coffer — a % refund on every wreck.
        p.drawLine(QPointF(cx, y + h*.16), QPointF(cx, y + h*.5))
        p.drawLine(QPointF(cx - w*.14, y + h*.36), QPointF(cx, y + h*.52))
        p.drawLine(QPointF(cx + w*.14, y + h*.36), QPointF(cx, y + h*.52))
        p.drawLine(QPointF(x + w*.24, y + h*.56), QPointF(x + w*.24, y + h*.8))
        p.drawLine(QPointF(x + w*.24, y + h*.8), QPointF(x + w*.76, y + h*.8))
        p.drawLine(QPointF(x + w*.76, y + h*.8), QPointF(x + w*.76, y + h*.56))
    elif name == "surge":
        # A lightning bolt — the all-in Surge Deploy alpha strike.
        path = QPainterPath(); path.moveTo(x + w*.58, y + h*.12)
        path.lineTo(x + w*.30, y + h*.54); path.lineTo(x + w*.47, y + h*.54)
        path.lineTo(x + w*.40, y + h*.88); path.lineTo(x + w*.72, y + h*.42)
        path.lineTo(x + w*.53, y + h*.42); path.closeSubpath()
        p.setBrush(QColor(color)); p.setPen(Qt.PenStyle.NoPen); p.drawPath(path)
    elif name == "boss":
        # A flagship battleship crowned with a rank chevron — the score-reward
        # boss. A broad hull, a bridge tower with twin raised main guns, and a
        # commander's chevron above, so it reads as 'the enemy's flagship'.
        hull = QPainterPath()
        hull.moveTo(x + w*.14, y + h*.58); hull.lineTo(x + w*.86, y + h*.58)
        hull.lineTo(x + w*.72, y + h*.76); hull.lineTo(x + w*.28, y + h*.76)
        hull.closeSubpath(); p.drawPath(hull)
        p.drawRect(QRectF(cx - w*.09, y + h*.40, w*.18, h*.18))      # bridge tower
        p.drawLine(QPointF(cx - w*.035, y + h*.40), QPointF(cx - w*.035, y + h*.28))
        p.drawLine(QPointF(cx + w*.035, y + h*.40), QPointF(cx + w*.035, y + h*.28))
        p.drawLine(QPointF(cx - w*.17, y + h*.26), QPointF(cx, y + h*.13))  # rank chevron
        p.drawLine(QPointF(cx + w*.17, y + h*.26), QPointF(cx, y + h*.13))
    else:
        p.setBrush(QColor(color)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRectF(cx - w*.16, cy - w*.16, w*.32, w*.32))
    p.restore()
