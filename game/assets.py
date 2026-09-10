"""
game.assets – image helpers and the sprite cache.

Loads PNGs (via Pillow), recolours them per team, and hands back QPixmaps. All
Qt/PIL image fiddling lives here so the rest of the engine deals only in cached
pixmaps.
"""

from __future__ import annotations
import io, os, math
from PyQt6.QtCore import Qt, QRect, QBuffer, QByteArray
from PyQt6.QtGui  import QPixmap, QImage, QPainter, QColor, QTransform

from . import config
from .models import ShipDef
from .hitmask import HitMask

try:
    from PIL import Image as PILImage
    PIL_OK = True
except ImportError:
    PIL_OK = False
    print("Pillow missing: pip install Pillow")


def _log(msg: str):
    """Print a diagnostic without ever crashing on a non-cp1252 console
    (the install path may contain non-Latin characters)."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))


# ─── Pixel helpers ──────────────────────────────────────────────────────────
def remove_white(img, thresh=228):
    img = img.convert("RGBA"); d = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = d[x, y]
            if a > 180 and r > thresh and g > thresh and b > thresh:
                d[x, y] = (0, 0, 0, 0)
    return img

def pil_to_q(img) -> QPixmap:
    buf = io.BytesIO(); img.save(buf, "PNG")
    return QPixmap.fromImage(QImage.fromData(buf.getvalue()))

def tint(px: QPixmap, color: QColor) -> QPixmap:
    res = QPixmap(px.size()); res.fill(Qt.GlobalColor.transparent)
    p = QPainter(res)
    p.drawPixmap(0, 0, px)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(res.rect(), color); p.end()
    return res

# Moonlit rim colour: a cool steel-white cast a black hull's edge catches after
# dark. A same-shape silhouette tinted this colour is stamped around a sprite at
# night (see Ship._draw_night_rim) so near-black player hulls don't vanish into
# the dark sea.
NIGHT_RIM = "#bcd2ea"


def flip_h(px: QPixmap) -> QPixmap:
    t = QTransform(); t.scale(-1, 1)
    return px.transformed(t, Qt.TransformationMode.SmoothTransformation)

def scale(px: QPixmap, w: int, h: int) -> QPixmap:
    return px.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)


def load_ship_sprites(sdef: ShipDef) -> tuple[QPixmap, QPixmap]:
    """Return (player_pixmap, enemy_pixmap) for a vehicle definition.

    Mutates sdef.display_h when it was left 0, deriving it from the source aspect.
    """
    path = config.find_texture(sdef.sprite_file)
    pil  = PILImage.open(path).convert("RGBA")
    if sdef.sprite_crop:
        pil = pil.crop(tuple(sdef.sprite_crop))
    if sdef.remove_white:
        pil = remove_white(pil)
    if sdef.display_h == 0:
        sdef.display_h = max(1, round(sdef.display_w * pil.height / pil.width))
    # Build the collision silhouette from the finished (cropped/cleaned) source so
    # shells strike only the hull + superstructure, not masts or empty sky. A JSON
    # `hitbox` rectangle, when present, overrides the derived shape.
    if sdef.hitbox and len(sdef.hitbox) == 4:
        sdef.hitmask = HitMask.from_rect(*(float(v) for v in sdef.hitbox))
    else:
        sdef.hitmask = HitMask.from_image(pil)
    base_px = scale(pil_to_q(pil), sdef.display_w, sdef.display_h)
    pl_raw = flip_h(base_px) if sdef.flip_player else base_px
    en_raw = flip_h(base_px) if sdef.flip_enemy  else base_px
    # Remember the base source width so an elevating barrel can be scaled into the
    # same coordinate space as the base (see load_barrel_sprites).
    sdef._base_src_w = pil.width
    return tint(pl_raw, QColor(sdef.player_tint)), tint(en_raw, QColor(sdef.enemy_tint))


def load_barrel_sprites(sdef: ShipDef) -> tuple[QPixmap, QPixmap]:
    """Return (player, enemy) pixmaps for a unit's elevating barrel.

    The barrel art is drawn pointing RIGHT and is NOT team-flipped here – the
    engine mirrors and rotates it about its pivot at draw time. It is scaled by
    the same base_width→display_w factor as the base, so both share one scale.
    Sets sdef.barrel_disp to the scaled (w, h).
    """
    path = config.find_texture(sdef.barrel_file)
    pil  = PILImage.open(path).convert("RGBA")
    factor = sdef.display_w / max(1, getattr(sdef, "_base_src_w", pil.width))
    bw = max(1, round(pil.width  * factor))
    bh = max(1, round(pil.height * factor))
    sdef.barrel_disp = (bw, bh)
    px = scale(pil_to_q(pil), bw, bh)
    return tint(px, QColor(sdef.player_tint)), tint(px, QColor(sdef.enemy_tint))


# ─── High-resolution menu portraits ─────────────────────────────────────────
# The in-game sprite cache stores every unit already shrunk to its small combat
# `display_w` (e.g. 54 px for a turret). The menu then upscales *that* tiny pixmap
# to ~150 px, which is where the level-select previews go soft and pixelated.
# These portraits instead render from the ORIGINAL PNG at full resolution, so the
# menu scales DOWN from a crisp source, and they composite a turret's elevating
# barrel at its resting angle so a gun turret actually shows its gun.

PORTRAIT_MAX = 512      # cap the long side of a portrait so a big hull stays bounded


def _trim(px: QPixmap) -> QPixmap:
    """Crop transparent margins so a padded composite centres cleanly in the menu."""
    if not PIL_OK or px.isNull():
        return px
    ba = QByteArray(); buf = QBuffer(ba); buf.open(QBuffer.OpenModeFlag.WriteOnly)
    px.save(buf, "PNG"); buf.close()
    bbox = PILImage.open(io.BytesIO(bytes(ba))).getbbox()
    if not bbox:
        return px
    x0, y0, x1, y1 = bbox
    return px.copy(QRect(x0, y0, x1 - x0, y1 - y0))


def load_portrait_sprites(sdef: ShipDef) -> tuple[QPixmap, QPixmap]:
    """Return crisp (player, enemy) menu portraits built from the source art.

    A unit with an elevating barrel gets that barrel drawn on top at its resting
    elevation, mirrored per team exactly as the battlefield renderer seats it.
    """
    path = config.find_texture(sdef.sprite_file)
    pil  = PILImage.open(path).convert("RGBA")
    if sdef.sprite_crop:
        pil = pil.crop(tuple(sdef.sprite_crop))
    if sdef.remove_white:
        pil = remove_white(pil)
    long = max(pil.width, pil.height)
    s    = 1.0 if long <= PORTRAIT_MAX else PORTRAIT_MAX / long
    bw   = max(1, round(pil.width  * s))
    bh   = max(1, round(pil.height * s))
    base_px = scale(pil_to_q(pil), bw, bh)

    barrel_px = None; brW = brH = 0
    if sdef.barrel_file:
        try:
            bpil = PILImage.open(config.find_texture(sdef.barrel_file)).convert("RGBA")
            brW = max(1, round(bpil.width  * s)); brH = max(1, round(bpil.height * s))
            barrel_px = scale(pil_to_q(bpil), brW, brH)
        except Exception:                                # noqa: BLE001
            barrel_px = None

    pad = (brW + brH + 8) if barrel_px else 4
    cw, ch = bw + 2 * pad, bh + 2 * pad

    def compose(tint_color: str, flip: bool) -> QPixmap:
        canvas = QPixmap(cw, ch); canvas.fill(Qt.GlobalColor.transparent)
        q = QPainter(canvas)
        q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # base hull – mirrored for the flipped team, matching the in-game sprite
        if flip:
            q.save(); q.translate(pad + bw, pad); q.scale(-1.0, 1.0)
            q.drawPixmap(0, 0, base_px); q.restore()
        else:
            q.drawPixmap(pad, pad, base_px)
        # elevating barrel at rest, seated on the base pivot (mirror of _draw_barrel)
        if barrel_px:
            pvx, pvy = sdef.barrel_pivot
            ax,  ay  = sdef.barrel_anchor
            eff = (1.0 - pvx) if flip else pvx
            q.save()
            q.translate(pad + eff * bw, pad + pvy * bh)
            if flip:
                q.scale(-1.0, 1.0)
            q.rotate(-math.degrees(sdef.barrel_rest))
            q.drawPixmap(int(-ax * brW), int(-ay * brH), barrel_px)
            q.restore()
        q.end()
        return tint(_trim(canvas), QColor(tint_color))

    return (compose(sdef.player_tint, sdef.flip_player),
            compose(sdef.enemy_tint,  sdef.flip_enemy))


def bastion_portrait(sdef: ShipDef, team: str) -> QPixmap:
    """The Bastion has no unit sprite – it is drawn procedurally on the base. Render
    that same fort-style artwork into a standalone portrait for the catalog."""
    from .entities.oilrig import render_bastion         # local import avoids a cycle
    w = sdef.display_w or 150
    h = sdef.display_h or 110
    cw, ch = w + 56, h + 56
    px = QPixmap(cw, ch); px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    render_bastion(p, cw / 2, ch - 22, w, h, team)
    p.end()
    return _trim(px)


# ─── Sprite cache ───────────────────────────────────────────────────────────
class SpriteCache:
    """Loads and holds every team-coloured pixmap plus the fortress sprites."""

    def __init__(self):
        self.sprites:  dict[str, QPixmap] = {}
        self.pl_fort:  dict[int, QPixmap] = {}
        self.en_fort:  dict[int, QPixmap] = {}

    def load_units(self, registry: dict[str, ShipDef]):
        if not PIL_OK:
            return
        for key, sdef in registry.items():
            try:
                pl, en = load_ship_sprites(sdef)
                self.sprites[f"player_{key}"] = pl
                self.sprites[f"enemy_{key}"]  = en
                # Moonlit silhouette (same alpha shape, one flat rim colour) used
                # to draw a lit edge around dark hulls at night.
                rim = QColor(NIGHT_RIM)
                self.sprites[f"player_{key}_glow"] = tint(pl, rim)
                self.sprites[f"enemy_{key}_glow"]  = tint(en, rim)
                if sdef.barrel_file:
                    bpl, ben = load_barrel_sprites(sdef)
                    self.sprites[f"player_{key}_barrel"] = bpl
                    self.sprites[f"enemy_{key}_barrel"]  = ben
            except Exception as e:                       # noqa: BLE001
                _log(f"[Sprites] {sdef.sprite_file}: {e}")

    def load_portraits(self, registry: dict[str, ShipDef]):
        """Build the crisp high-resolution menu portraits (barrels composited,
        Bastion rendered from its fort artwork). Stored as `hi_<team>_<key>`."""
        if not PIL_OK:
            return
        for key, sdef in registry.items():
            try:
                if "bastion" in getattr(sdef, "tags", []):
                    self.sprites[f"hi_player_{key}"] = bastion_portrait(sdef, "player")
                    self.sprites[f"hi_enemy_{key}"]  = bastion_portrait(sdef, "enemy")
                    continue
                pl, en = load_portrait_sprites(sdef)
                self.sprites[f"hi_player_{key}"] = pl
                self.sprites[f"hi_enemy_{key}"]  = en
            except Exception as e:                       # noqa: BLE001
                _log(f"[Portraits] {sdef.sprite_file}: {e}")

    def hi(self, team: str, key: str) -> QPixmap | None:
        """A high-res menu portrait, falling back to the small in-game sprite."""
        return self.sprites.get(f"hi_{team}_{key}") or self.sprites.get(f"{team}_{key}")

    def load_fortress(self):
        if not PIL_OK:
            return
        path = config.find_texture(config.FORTRESS_FILE)
        if not os.path.exists(path):
            return                                       # optional atlas; bases fall back to rectangles
        try:
            fort_pil = remove_white(PILImage.open(path))
            fort_px  = pil_to_q(fort_pil)
            FH, SH = config.FORT_HALF, config.FORT_SRC_H
            DW, DH = config.FORT_D_W, config.FORT_D_H_MAX
            for lv in range(config.MAX_LVL + 1):
                frac  = config.stair_frac(lv)
                src_h = max(1, round(SH * frac)); src_y = SH - src_h
                dh    = max(1, round(DH * frac))
                pl_c  = fort_px.copy(QRect(FH, src_y, FH, src_h))
                en_c  = fort_px.copy(QRect(0,  src_y, FH, src_h))
                self.pl_fort[lv] = tint(
                    pl_c.scaled(DW, dh, Qt.AspectRatioMode.IgnoreAspectRatio,
                                Qt.TransformationMode.SmoothTransformation),
                    QColor(0x08, 0x08, 0x08))
                self.en_fort[lv] = tint(
                    en_c.scaled(DW, dh, Qt.AspectRatioMode.IgnoreAspectRatio,
                                Qt.TransformationMode.SmoothTransformation),
                    QColor(0xcc, 0x11, 0x11))
        except Exception as e:                           # noqa: BLE001
            _log(f"[Fortress sprites] {e}")

    # Ordnance (torpedoes, bombs, shells, depth charges, …) is drawn entirely as
    # cheap procedural vector shapes in Projectile.draw – no projectile pixmaps are
    # loaded, scaled or rotated, which keeps a screenful of rounds cheap to render.

    def get(self, name: str) -> QPixmap | None:
        return self.sprites.get(name)
