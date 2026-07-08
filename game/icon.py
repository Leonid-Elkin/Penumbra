"""
game.icon — the application / taskbar icon, drawn in-engine.

Rather than ship a stray .png, the launcher icon is composited from the same parts
the game already owns: the actual in-game **battleship** sprite, tinted to signal
amber, riding a sonar-green waterline on a gunmetal steel tile with corner rivets —
all in the "plotting-table" material language of game/ui/theme.py. Rendered fresh at
every icon size so it stays crisp from 16 px (taskbar) to 256 px (Alt-Tab).

Run this module directly to also emit standalone icon.png / icon.ico for packaging
(PyInstaller --icon, a desktop shortcut, etc.):

    python -m game.icon
"""

from __future__ import annotations
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui  import (QIcon, QPixmap, QPainter, QColor, QPen, QBrush,
                          QPainterPath, QPolygonF, QLinearGradient, QTransform)

from . import config
from .ui import theme

# The sizes Windows/Qt actually ask for (taskbar, title-bar, Alt-Tab, shortcut).
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)

SPRITE_FILE = "battleship.png"          # the capital-ship hero sprite
_sprite_src: QPixmap | None = None      # cached raw load (needs a QApplication)


def _battleship(target_w: float) -> QPixmap | None:
    """The battleship sprite, flipped to face right and re-tinted with a vertical
    amber gradient (so a flat black silhouette reads on the dark plate). Scaled to
    `target_w` px wide, aspect preserved. None if the texture can't be loaded."""
    global _sprite_src
    if _sprite_src is None:
        src = QPixmap(config.find_texture(SPRITE_FILE))
        if src.isNull():
            return None
        _sprite_src = src.transformed(QTransform().scale(-1, 1),          # face right
                                      Qt.TransformationMode.SmoothTransformation)
    w = max(1, int(round(target_w)))
    scaled = _sprite_src.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)

    tinted = QPixmap(scaled.size())
    tinted.fill(Qt.GlobalColor.transparent)
    q = QPainter(tinted)
    q.drawPixmap(0, 0, scaled)
    q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    g = QLinearGradient(0, 0, 0, scaled.height())
    g.setColorAt(0.0, QColor(theme.AMBER_HI))
    g.setColorAt(1.0, QColor("#c07f10"))
    q.fillRect(tinted.rect(), QBrush(g))
    q.end()
    return tinted


def _draw_vector_ship(p: QPainter, S: int, water_y: float):
    """Fallback warship, drawn from vectors — used only if the sprite is missing."""
    def poly(pts):
        path = QPainterPath(); path.moveTo(pts[0][0] * S, pts[0][1] * S)
        for x, y in pts[1:]:
            path.lineTo(x * S, y * S)
        path.closeSubpath(); return path
    ship = poly([(0.11, 0.55), (0.83, 0.525), (0.925, 0.605),
                 (0.855, 0.635), (0.16, 0.635), (0.11, 0.60)])
    for part in (poly([(0.45, 0.525), (0.575, 0.525), (0.555, 0.40), (0.475, 0.40)]),
                 poly([(0.59, 0.525), (0.675, 0.525), (0.66, 0.44), (0.605, 0.44)]),
                 poly([(0.70, 0.525), (0.805, 0.525), (0.795, 0.485), (0.71, 0.485)]),
                 poly([(0.24, 0.525), (0.345, 0.525), (0.335, 0.485), (0.25, 0.485)])):
        ship = ship.united(part)
    sg = QLinearGradient(0, S * 0.36, 0, water_y)
    sg.setColorAt(0.0, QColor(theme.AMBER_HI)); sg.setColorAt(1.0, QColor("#c07f10"))
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(sg)); p.drawPath(ship)
    line = QPen(QColor(theme.ACCENT), max(1.0, S * 0.022))
    line.setCapStyle(Qt.PenCapStyle.RoundCap); p.setPen(line)
    p.drawLine(QPointF(S * 0.79, S * 0.50), QPointF(S * 0.905, S * 0.455))
    p.drawLine(QPointF(S * 0.255, S * 0.50), QPointF(S * 0.145, S * 0.455))
    mast = QPen(QColor(theme.AMBER_HI), max(1.0, S * 0.016))
    mast.setCapStyle(Qt.PenCapStyle.RoundCap); p.setPen(mast)
    p.drawLine(QPointF(S * 0.515, S * 0.40), QPointF(S * 0.50, S * 0.255))
    p.drawLine(QPointF(S * 0.455, S * 0.315), QPointF(S * 0.565, S * 0.315))


def _paint(p: QPainter, S: int):
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # ── Steel tile (rounded so it reads as an app icon on any wallpaper) ──────
    inset = S * 0.02
    rad = S * 0.185
    tile = QPainterPath()
    tile.addRoundedRect(QRectF(inset, inset, S - 2 * inset, S - 2 * inset), rad, rad)
    p.save()
    p.setClipPath(tile)

    g = QLinearGradient(0, 0, 0, S)
    g.setColorAt(0.0, QColor("#1e2831"))
    g.setColorAt(0.5, QColor(theme.BG))
    g.setColorAt(1.0, QColor(theme.BG_DEEP))
    p.fillRect(QRectF(0, 0, S, S), g)

    # ── Sea band under the waterline, to ground the hull ─────────────────────
    water_y = S * 0.60
    sea = QLinearGradient(0, water_y, 0, S)
    sea.setColorAt(0.0, QColor(theme.SEA_TOP))
    sea.setColorAt(1.0, QColor(theme.SEA_DEEP))
    p.fillRect(QRectF(0, water_y, S, S - water_y), sea)

    # ── The battleship (real sprite, tinted amber), hull sitting in the water.
    # Sized past the tile width so the hero fills the frame; the long low hull's
    # bow/stern tips clip softly against the rounded plate.
    ship = _battleship(S * 1.16)
    if ship is not None:
        bx = (S - ship.width()) / 2.0
        by = water_y + S * 0.018 - ship.height()    # hull dips just below the line
        p.drawPixmap(QPointF(bx, by), ship)
    else:
        _draw_vector_ship(p, S, water_y)

    # phosphor waterline — the one 'live telemetry' note, drawn over the hull
    p.setPen(QPen(QColor(theme.PHOSPHOR), max(1.0, S * 0.012)))
    p.setOpacity(0.5)
    p.drawLine(QPointF(S * 0.06, water_y), QPointF(S * 0.94, water_y))
    p.setOpacity(1.0)

    p.restore()

    # ── Corner rivets + tile edge (the war-room material tell) ────────────────
    if S >= 32:
        m = S * 0.115
        p.setPen(Qt.PenStyle.NoPen)
        for cx, cy in ((m, m), (S - m, m), (m, S - m), (S - m, S - m)):
            p.setBrush(QColor(theme.EDGE_LO))
            p.drawEllipse(QPointF(cx, cy), S * 0.018, S * 0.018)
            p.setBrush(QColor(theme.EDGE_HI))
            p.drawEllipse(QPointF(cx - S * 0.004, cy - S * 0.004), S * 0.012, S * 0.012)

    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(theme.LINE_HI), max(1.0, S * 0.014)))
    p.drawPath(tile)


def render(size: int) -> QPixmap:
    """Draw the icon at `size`×`size` px onto a transparent pixmap."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    _paint(p, size)
    p.end()
    return pm


def app_icon() -> QIcon:
    """A multi-resolution QIcon; Qt picks the best size per surface."""
    ic = QIcon()
    for s in ICON_SIZES:
        ic.addPixmap(render(s))
    return ic


def _export(png_path: str, ico_path: str):
    """Write standalone icon.png (256) and a multi-size icon.ico for packaging."""
    import io
    from PIL import Image
    from PyQt6.QtCore import QBuffer, QByteArray

    def _png_bytes(pm: QPixmap) -> bytes:
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        pm.save(buf, "PNG")
        buf.close()
        return bytes(ba)

    render(256).save(png_path, "PNG")
    frames = [Image.open(io.BytesIO(_png_bytes(render(s)))).convert("RGBA")
              for s in ICON_SIZES]
    frames[-1].save(ico_path, format="ICO",
                    sizes=[(s, s) for s in ICON_SIZES])
    for pth in (png_path, ico_path):
        try:
            print(f"wrote {pth}")
        except UnicodeEncodeError:
            print("wrote " + pth.encode("ascii", "replace").decode("ascii"))


if __name__ == "__main__":
    import os
    from PyQt6.QtWidgets import QApplication
    _app = QApplication([])                      # a QApplication is needed to paint
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _export(os.path.join(here, "icon.png"), os.path.join(here, "icon.ico"))
