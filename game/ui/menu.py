"""
game.ui.menu — front-end screens over a procedural navy backdrop.

Designed as a naval command terminal: asymmetric layouts, an industrial heading
face, hard 1px rules and a single amber accent.

  SaveSelectScreen — three profile bays (create / continue / erase).
  CampaignScreen   — a dominant boss dossier on the left, an ordered operations
                     list on the right; bosses are redacted until defeated.
"""

from __future__ import annotations
import math, random
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QLabel, QPushButton, QFrame, QSizePolicy,
                             QLineEdit, QComboBox, QRadioButton, QButtonGroup,
                             QListWidget, QListWidgetItem)
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtCore  import (Qt, QTimer, QElapsedTimer, QRect, QRectF, QPointF, QSize,
                          pyqtSignal)
from PyQt6.QtGui   import (QPainter, QColor, QPen, QPixmap, QIcon, QTransform,
                          QLinearGradient, QFontMetrics)

from . import theme
from .scenery import paint_navy_scene, paint_bomber_scene
from .widgets import weapon_lines, CommandButton, IconButton, SettingsOverlay
from ..save import DIFFICULTY_ORDER
from ..settings import SETTINGS
from .. import progression
from ..progression import ROSTER
from ..entities.explosion import MuzzleSmoke


_LOCK_ICON_CACHE: dict = {}


def _lock_pixmap(size: int, color: str) -> QPixmap:
    """A crisp painted lock (theme.draw_icon 'lock'), cached per size/colour —
    used in place of the 🔒 emoji so protected rooms read in the war-room style."""
    key = (size, color)
    px = _LOCK_ICON_CACHE.get(key)
    if px is None:
        px = QPixmap(size, size)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        theme.draw_icon(p, "lock", QRectF(0, 0, size, size), QColor(color))
        p.end()
        _LOCK_ICON_CACHE[key] = px
    return px


def _boss_pixmap(sprites, boss_key, w, h):
    px = sprites.hi("enemy", boss_key) if sprites else None
    if not px or px.isNull():
        return None
    return px.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)


# Player sprites are near-black silhouettes (#0a0a0a) — right on bright water, but
# nearly invisible on the dark war-room plate. For the menu we recolour the hull to
# a light gunmetal so it reads clearly against the panel. The in-game sprite is left
# untouched, so this only affects the catalog display.
_SHIP_DISPLAY_TINT = QColor(0xc0, 0xcd, 0xd8)

def _ship_display_pixmap(sprites, key, w, h):
    px = sprites.hi("player", key) if sprites else None
    if not px or px.isNull():
        return None
    sc = px.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                   Qt.TransformationMode.SmoothTransformation)
    res = QPixmap(sc.size()); res.fill(Qt.GlobalColor.transparent)
    q = QPainter(res)
    q.drawPixmap(0, 0, sc)
    q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    q.fillRect(res.rect(), _SHIP_DISPLAY_TINT)
    q.end()
    return res


class _Scene(QOpenGLWidget):
    """Base screen that paints the animated navy scene behind its children.

    A QOpenGLWidget so the continuously-animated backdrop is drawn by the GPU
    (OpenGL paint engine) rather than the CPU raster engine. Subclasses paint
    their own foreground by overriding `_paint_overlay`, not `paintGL`, so the
    scene + wash always render underneath."""
    # ~60 fps redraw. The scene clock (_t) is wall-clock time, not a per-tick
    # increment: coarse QTimer slack and event-loop delays then show up as a
    # slightly late frame at the *right* position instead of a position jump —
    # which is what made fast motion (the flagship's shell) look laggy.
    _FRAME_MS = 16

    # The full-screen toggle (mirrors F11) sits in the SAME spot on every screen:
    # a floating chrome icon pinned to the top-right corner. Subclasses inherit
    # the `fullscreen` signal, the button and its placement from here so they can
    # never drift apart. Size/margin are fixed so the corner reads identically as
    # the screens slide past one another.
    fullscreen = pyqtSignal()
    _FS_SIZE = 42
    _FS_MARGIN = 28

    def __init__(self, sprites):
        super().__init__()
        self.sprites = sprites
        self._t = 0.0
        self._clock = QElapsedTimer(); self._clock.start()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick); self._timer.start(self._FRAME_MS)
        # Top-right full-screen toggle, shared by every screen. Parented to the
        # scene (not a layout) so it floats over the foreground; _place_fs_btn
        # keeps it in the corner and on top after each layout pass.
        self.fs_btn = IconButton("fullscreen", "Toggle fullscreen  (F11)", size=self._FS_SIZE)
        self.fs_btn.setParent(self); self.fs_btn.clicked.connect(self.fullscreen.emit)

    def _place_fs_btn(self):
        """Pin the toggle to the top-right corner and lift it above the layout."""
        self.fs_btn.move(self.width() - self._FS_MARGIN - self.fs_btn.width(), self._FS_MARGIN)
        self.fs_btn.raise_()

    def set_fullscreen(self, on: bool):
        """Swap the corner toggle between the expand / restore glyphs."""
        self.fs_btn.set_icon("windowed" if on else "fullscreen")

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place_fs_btn()

    def _tick(self):
        self._t = self._clock.elapsed() / 1000.0; self.update()

    # Don't burn frames on screens the QStackedWidget has hidden; _t is wall-clock
    # so animations stay in phase across hide/show.
    def showEvent(self, ev):
        super().showEvent(ev)
        self._timer.start(self._FRAME_MS)

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self._timer.stop()

    def paintGL(self):
        p = QPainter(self)
        self._paint_scene(p)
        # darken for legibility — flat wash, no vignette/glow
        p.fillRect(self.rect(), QColor(7, 10, 13, 150))
        self._paint_overlay(p)

    def _paint_scene(self, p):
        """The animated backdrop. Defaults to the sky/sea navy scene; subclasses
        swap in their own (e.g. the save screen's panned-up bomber run)."""
        paint_navy_scene(p, self.width(), self.height(), self.sprites, self._t)

    def _paint_overlay(self, p):
        """Foreground drawn over the navy scene — overridden by subclasses."""
        pass


def _title(text, size, color=theme.TEXT, spacing=4.0):
    """A heading label set with the SAME QFont the painted headings use
    (theme.head, point-sized) — so a label title and a painted title of the
    same size render identically, e.g. either side of a screen slide."""
    lbl = QLabel(text)
    lbl.setFont(theme.head(size, spacing))
    lbl.setStyleSheet(f"color:{color};background:transparent;")
    return lbl


def _hero_silhouette(sprites, keys, target_w):
    """A large, dark, amber-rimmed capital-ship silhouette for the menu focal
    point. Tries each key in order; returns (pixmap, native_key) or (None, None)."""
    for k in keys:
        px = (sprites.get(f"hi_{k}") or sprites.get(k)) if sprites else None
        if px and not px.isNull():
            h = max(1, round(target_w * px.height() / px.width()))
            sil = QPixmap(px.size()); sil.fill(Qt.GlobalColor.transparent)
            q = QPainter(sil); q.drawPixmap(0, 0, px)
            q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            q.fillRect(sil.rect(), QColor(9, 14, 20, 248)); q.end()
            return sil.scaled(target_w, h, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation), k
    return None, None


class MainMenuScreen(_Scene):
    """The top-level menu: an asymmetric command console. Title lockup anchored
    top-left, a control column of armed switches, a hero flagship holding the
    lower-right as the single focal point, and a telemetry footer for world flavour."""
    saves       = pyqtSignal()
    sandbox     = pyqtSignal()
    multiplayer = pyqtSignal()
    quit_game   = pyqtSignal()

    def __init__(self, sprites):
        super().__init__(sprites)
        self._hero = None
        # Custom-painted command switches (see widgets.CommandButton).
        self.b_play = CommandButton("PLAY GAME", "Play the campaign", "primary", w=340, h=76)
        self.b_sand = CommandButton("SANDBOX", "Try out strategies and interactions", "steel", w=340, h=64)
        self.b_lan  = CommandButton("PLAY ONLINE", "Head-to-head over a local network", "steel", w=340, h=64)
        # QUIT is a full command switch in the control column, in style with the rest.
        self.b_quit = CommandButton("QUIT", "Exit to desktop", "steel", w=340, h=64)
        self.b_play.clicked.connect(self.saves.emit)
        self.b_sand.clicked.connect(self.sandbox.emit)
        self.b_lan.clicked.connect(self.multiplayer.emit)
        self.b_quit.clicked.connect(self.quit_game.emit)
        for b in (self.b_play, self.b_sand, self.b_lan, self.b_quit): b.setParent(self)
        # The full-screen toggle (self.fs_btn) is the shared top-right corner icon
        # from _Scene; the settings tab (gear) sits just to its left.
        self.settings_btn = IconButton("settings", "Settings", size=42)
        self.settings_btn.setParent(self)
        self.settings_panel = SettingsOverlay(self)
        self.settings_btn.clicked.connect(self.settings_panel.open)
        # Live muzzle-smoke clouds — the SAME particle burst the guns throw in
        # battle (game.entities.explosion.MuzzleSmoke), spawned once per salvo and
        # then ticked/drawn each frame. `_last_salvo` gates one spawn per firing;
        # `_prev_t` gives us a per-frame dt off the scene's wall-clock `_t`.
        self._smoke = []
        self._last_salvo = -1
        self._prev_t = 0.0

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        W, H = self.width(), self.height()
        x = 72; y = int(H * 0.40)
        self.b_play.move(x, y)
        y2 = y + self.b_play.height() + 12
        self.b_sand.move(x, y2)
        y3 = y2 + self.b_sand.height() + 12
        self.b_lan.move(x, y3)
        self.b_quit.move(x, y3 + self.b_lan.height() + 12)
        # fs_btn is pinned to the top-right corner by _Scene.resizeEvent (super);
        # tuck the settings gear just to its left at the same baseline.
        self.settings_btn.move(self.fs_btn.x() - 10 - self.settings_btn.width(), self.fs_btn.y())
        self._hero, self._hero_key = _hero_silhouette(
            self.sprites, ["enemy_yamato", "enemy_bismarck", "enemy_iowa",
                           "player_carrier", "player_battleship"],
            min(760, int(W * 0.56)))

    # Main-battery salvo cadence, in the same units as self._t (~seconds).
    FIRE_PERIOD = 20.0

    def _paint_gunfire(self, p, hx, hy, hw, hh, W, H):
        """Draw the flagship's forward main battery firing once every
        FIRE_PERIOD. The gun sits over the forward turret (the ship sails left,
        so 'forward' is its left side) and the shell arcs out to port."""
        phase = self._t % self.FIRE_PERIOD          # seconds since last salvo
        # Muzzle over the forward turret, just above the deck line.
        mx = hx + hw * 0.30
        my = hy + hh * 0.26

        # Launch velocity matches the barrel elevation: 61° above horizontal,
        # firing forward (leftward). Speed is deliberately modest so the round
        # arcs and falls back rather than shooting off the top of the screen.
        SHELL_SPEED = 640.0
        _a = math.radians(61.0)
        vx = -SHELL_SPEED * math.cos(_a)             # forward / left
        vy0 = -SHELL_SPEED * math.sin(_a)            # up (screen y grows down)

        # ── Muzzle smoke — the real battle particle burst ───────────────────
        # Advance any live clouds by this frame's dt (clamped, since `_t` is
        # wall-clock and jumps after the screen is hidden), then spawn a fresh
        # MuzzleSmoke at the muzzle the instant a new salvo fires — identical to
        # what the ships throw in game.entities.ship._muzzle_smoke.
        dt = min(0.05, max(0.0, self._t - self._prev_t)); self._prev_t = self._t
        salvo = int(self._t / self.FIRE_PERIOD)
        if salvo != self._last_salvo:                # crossed into a new salvo
            self._last_salvo = salvo
            # Bore direction = the shell's launch velocity (forward/left, up at
            # 61°); power 1.0 ≈ a battleship main battery.
            self._smoke.append(MuzzleSmoke(mx, my, vx, vy0, power=1.0))
        for s in self._smoke:
            s.update(dt)
        self._smoke = [s for s in self._smoke if s.alive]
        for s in self._smoke:                        # menu coords are screen-space
            s.draw(p, 0.0)                            # → no camera offset

        # ── Muzzle flash — a brief bright bloom firing forward/left ──────────
        if phase < 0.16:
            f = 1.0 - phase / 0.16                   # 1 → 0
            p.setPen(Qt.PenStyle.NoPen)
            p.setOpacity(0.85 * f)
            p.setBrush(QColor(255, 236, 190))
            p.drawEllipse(QPointF(mx - 10, my), 16 * f + 6, 9 * f + 4)
            p.setOpacity(0.55 * f)
            p.setBrush(QColor(255, 176, 74))
            p.drawEllipse(QPointF(mx - 26, my), 26 * f + 8, 6 * f + 3)
            p.setOpacity(1.0)

        # ── Shell — a steep ballistic arc at the barrel's 61° elevation ──────
        # It climbs, arcs over, and falls back down toward the sea. The waterline
        # is the scene's horizon (paint_navy_scene's default horizon_frac=0.62);
        # the round splashes down and vanishes the instant it reaches it, rather
        # than continuing off the bottom of the frame.
        st = phase                                   # seconds into flight
        g = 520.0                                    # px/s² (vx, vy0 set above)
        water_y = H * 0.62                           # sea surface (scene horizon)
        # Solve my + vy0·t + ½g·t² = water_y for the descending crossing — the
        # larger (later) root. my sits above the water, so the discriminant is
        # always positive and t_hit is real.
        t_hit = (-vy0 + math.sqrt(vy0 * vy0 + 2.0 * g * (water_y - my))) / g
        sx = mx + vx * st
        sy = my + vy0 * st + 0.5 * g * st * st
        if st < t_hit and sx > -20:                  # still in the air, on-screen
            p.setPen(Qt.PenStyle.NoPen)
            # faint tracer trailing the shell
            p.setOpacity(0.30)
            p.setBrush(QColor(theme.ACCENT))
            for k in range(1, 6):
                tt = max(0.0, st - k * 0.012)
                tx = mx + vx * tt
                ty = my + vy0 * tt + 0.5 * g * tt * tt
                p.drawEllipse(QPointF(tx, ty), 2.6 - k * 0.35, 2.6 - k * 0.35)
            p.setOpacity(0.95)
            p.setBrush(QColor(255, 230, 180))
            p.drawEllipse(QPointF(sx, sy), 3.0, 3.0)
            p.setOpacity(1.0)
        elif st - t_hit < 0.45:                       # brief splash where it hit
            wx = mx + vx * t_hit                      # impact x on the waterline
            f = 1.0 - (st - t_hit) / 0.45             # 1 → 0 over the splash life
            p.setPen(Qt.PenStyle.NoPen)
            p.setOpacity(0.5 * f)
            p.setBrush(QColor(214, 230, 236))         # pale foam
            p.drawEllipse(QPointF(wx, water_y), 7 * (1.2 - f) + 3, 3 * f + 2)
            # a couple of spray droplets kicked up and falling back
            p.setOpacity(0.75 * f)
            for k in (-1, 1):
                dt2 = (st - t_hit)
                dx = k * 26 * dt2
                dy = -70 * dt2 + 0.5 * g * dt2 * dt2
                p.drawEllipse(QPointF(wx + dx, water_y + dy), 2.0 * f + 1, 2.0 * f + 1)
            p.setOpacity(1.0)

    def _paint_overlay(self, p):                     # drawn over the navy scene + wash
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = self.width(), self.height()

        # ── Hero flagship: the focal point, holding the lower-right, sailing left ──
        if self._hero is not None:
            hx = W - self._hero.width() + 70
            hy = int(H * 0.60) - self._hero.height() // 2 + int(math.sin(self._t * 0.5) * 3)
            p.setOpacity(0.9); p.drawPixmap(hx, hy, self._hero); p.setOpacity(1.0)
            # The flagship fires its main battery every 20 seconds — a muzzle
            # flash, a ballistic shell arcing forward (leftward), and drifting
            # smoke. self._t advances ~1.0 per real second, so t % 20 is a clean
            # 20s cadence and the sub-second phase drives the animation.
            if not SETTINGS.no_effects:
                self._paint_gunfire(p, hx, hy, self._hero.width(), self._hero.height(), W, H)

        # ── Title lockup, top-left, framed by a vertical amber rule ──────────────
        lx = 72; ty = 66
        p.fillRect(lx - 18, ty - 4, 4, 128, QColor(theme.ACCENT))   # command rule
        p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.head(12, 6))
        p.drawText(lx, ty - 26, 600, 18, int(Qt.AlignmentFlag.AlignLeft), "NAVAL STRATEGY GAME")
        theme.engraved_label(p, "PENUMBRA V1.0", lx, ty, 900, theme.head(58, 4), theme.TEXT)
        # "FLEET COMMAND" subtitle with a trailing rule, well below the title
        sy = ty + 84
        tx = lx
        p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.head(15, 6))
        p.drawText(tx, sy + 8, 400, 22, int(Qt.AlignmentFlag.AlignVCenter), "BY LEONID ELKIN")
        tw = p.fontMetrics().horizontalAdvance("FLEET COMMAND")
        p.setPen(QPen(QColor(theme.LINE_HI), 1))          # rule trails the label, not over it
        p.drawLine(tx + tw + 18, sy + 20, lx + 340, sy + 20)

        # ── Control-column header above the buttons ──────────────────────────────
        cy = self.b_play.y() - 26
        p.setPen(QColor(theme.TEXT_FAINT)); p.setFont(theme.head(11, 4))
        p.drawText(lx, cy, 400, 16, int(Qt.AlignmentFlag.AlignVCenter), "SELECT COMMAND")
        p.setPen(QPen(QColor(theme.LINE), 1)); p.drawLine(lx, cy + 20, lx + 340, cy + 20)


# ─── Multiplayer lobby (online room browser + LAN direct) ────────────────────────
class MultiplayerScreen(_Scene):
    """Host or join a head-to-head battle.

    Online: CREATE a room and you drop straight into the battle, held behind an
    "awaiting player" overlay until someone joins — no codes to share. The other
    player picks OPEN ROOMS from a live, auto-refreshing list and clicks to join.
    LAN: the classic direct path — host on a port, the joiner types the address.

    Either way the host announces the chosen level so both sides build the same
    battle; `launch` then fires (with a live NetLink, or — for an online host — a
    still-pending NetRelayHost the battle adopts once paired)."""
    launch   = pyqtSignal(str, object, str, str, str, str)   # role, NetLink|NetRelayHost, level, session, my_name, opp_name
    play_bot = pyqtSignal(str, str)                          # level_key, commander_name — solo match vs Captain Bob
    back     = pyqtSignal()

    def _paint_scene(self, p):
        # A moon hangs over the head-to-head staging sky.
        paint_navy_scene(p, self.width(), self.height(), self.sprites, self._t, moon=True)

    _FIELD_CSS = (f"QLineEdit{{background:{theme.BG};border:1px solid {theme.LINE_HI};"
                  f"color:{theme.TEXT};font-family:'{theme.MONO_FAMILY}';font-size:14px;"
                  f"padding:8px 10px;}}"
                  f"QLineEdit:focus{{border:1px solid {theme.ACCENT};}}")
    _COMBO_CSS = (f"QComboBox{{background:{theme.BG};border:1px solid {theme.LINE_HI};"
                  f"color:{theme.TEXT};font-family:'{theme.MONO_FAMILY}';font-size:13px;"
                  f"padding:6px 10px;}}"
                  f"QComboBox QAbstractItemView{{background:{theme.PANEL};color:{theme.TEXT};"
                  f"selection-background-color:{theme.ACCENT};}}")
    _RADIO_CSS = (f"QRadioButton{{color:{theme.TEXT};font-family:'{theme.MONO_FAMILY}';"
                  f"font-size:12px;background:transparent;spacing:6px;}}"
                  f"QRadioButton::indicator{{width:12px;height:12px;}}"
                  f"QRadioButton::indicator:checked{{background:{theme.ACCENT};"
                  f"border:1px solid {theme.ACCENT};}}"
                  f"QRadioButton::indicator:unchecked{{background:{theme.BG};"
                  f"border:1px solid {theme.LINE_HI};}}")
    _LIST_CSS = (f"QListWidget{{background:{theme.BG};border:1px solid {theme.LINE_HI};"
                 f"color:{theme.TEXT};font-family:'{theme.MONO_FAMILY}';font-size:12px;"
                 f"outline:0;}}"
                 f"QListWidget::item{{padding:7px 10px;border-bottom:1px solid {theme.LINE};}}"
                 f"QListWidget::item:selected{{background:{theme.ACCENT};color:{theme.BG};}}")

    def __init__(self, data, sprites):
        super().__init__(sprites)
        self.data = data
        self._host = None; self._client = None
        self._client_auth_sent = False               # client: room-password proof sent once
        self._level_key = None
        self._local_name = ""                        # this commander's name, for the HP bar
        self._browser = None                        # NetRoomBrowser while browsing
        self._pw_room = None                        # id of the locked room the pw box is armed for

        panel = QFrame(self); panel.setObjectName("mp")
        panel.setStyleSheet(f"QFrame#mp{{background:rgba(10,14,18,220);"
                            f"border:1px solid {theme.LINE_HI};}}")
        panel.setFixedWidth(560)
        v = QVBoxLayout(panel); v.setContentsMargins(30, 26, 30, 26); v.setSpacing(12)

        v.addWidget(_title("HEAD-TO-HEAD BATTLE", 24, theme.TEXT, 4.0))
        sub = QLabel("Create a room and wait for an opponent, join an open one, "
                     "or connect directly on a LAN.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color:{theme.TEXT_DIM};font-family:'{theme.MONO_FAMILY}';"
                          f"font-size:12px;background:transparent;")
        v.addWidget(sub)

        # ── Mode: Online (relay) vs LAN (direct) ─────────────────────────────
        mode_row = QHBoxLayout(); mode_row.setSpacing(18)
        self.rb_online = QRadioButton("Online — over the internet")
        self.rb_lan    = QRadioButton("LAN — same network")
        for rb in (self.rb_online, self.rb_lan):
            rb.setStyleSheet(self._RADIO_CSS)
        self.rb_online.setChecked(True)
        self._mode_grp = QButtonGroup(self)
        self._mode_grp.addButton(self.rb_online); self._mode_grp.addButton(self.rb_lan)
        self.rb_online.toggled.connect(self._on_mode_changed)
        mode_row.addWidget(self.rb_online); mode_row.addWidget(self.rb_lan)
        mode_row.addStretch(1)
        v.addLayout(mode_row)

        # ── Commander name (shared, required) ────────────────────────────────
        # Whoever you are — host or joiner, online or LAN — this is the name that
        # rides on your health bar in the battle. Required before either action;
        # remembered between launches so it need only be typed once.
        v.addWidget(self._hdr("NAME"))
        self.pname_field = QLineEdit(); self.pname_field.setStyleSheet(self._FIELD_CSS)
        self.pname_field.setPlaceholderText("your name  (required)")
        self.pname_field.setMaxLength(16)
        self.pname_field.setText(SETTINGS.player_name)
        v.addWidget(self.pname_field)

        # ── Battle map theme (shared) ────────────────────────────────────────
        # Whoever starts a match — online CREATE, LAN HOST, or the bot game —
        # picks the map both fleets fight on. The joiner never chooses; the host
        # announces this level's key in its hello and the client rebuilds the
        # very same backdrop from it. The two fleets fight on the same arena whatever
        # is picked — the only thing a map choice changes is the sky/sea skin — so we
        # list the choices by *time of day* (Day ‥ Dead of Night), not by campaign
        # operation: a boss dossier means nothing to two duelling players. Levels that
        # share a time-of-day band render an identical backdrop, so we keep just the
        # first of each and key it back to that representative level (see backdrop).
        from . import backdrop
        v.addWidget(self._hdr("BATTLE MAP"))
        self.theme_combo = QComboBox(); self.theme_combo.setStyleSheet(self._COMBO_CSS)
        _seen_tod = set()
        for lv in self.data.levels_sorted():
            tod = backdrop.label_for_level(lv.order, lv.backdrop)
            if tod in _seen_tod:
                continue
            _seen_tod.add(tod)
            self.theme_combo.addItem(tod, lv.key)
        v.addWidget(self.theme_combo)

        # ── Host / create (shared) ───────────────────────────────────────────
        self.host_hdr = self._hdr("")
        v.addWidget(self.host_hdr)
        # Online only: name the room others see in the browser, and optionally lock
        # it behind a password. Meaningless for a direct LAN link, so it hides in LAN.
        self.create_meta_box = QWidget()
        cm = QHBoxLayout(self.create_meta_box); cm.setContentsMargins(0, 0, 0, 0)
        cm.setSpacing(8)
        self.name_field = QLineEdit(); self.name_field.setStyleSheet(self._FIELD_CSS)
        self.name_field.setPlaceholderText("room name  (optional)")
        self.name_field.setMaxLength(40)
        cm.addWidget(self.name_field, 2)
        self.pass_field = QLineEdit(); self.pass_field.setStyleSheet(self._FIELD_CSS)
        self.pass_field.setPlaceholderText("password  (optional)")
        self.pass_field.setMaxLength(64)
        self.pass_field.setEchoMode(QLineEdit.EchoMode.Password)
        cm.addWidget(self.pass_field, 1)
        v.addWidget(self.create_meta_box)
        row = QHBoxLayout(); row.setSpacing(8)
        row.addStretch(1)
        self.host_btn = QPushButton("HOST"); self.host_btn.setStyleSheet(theme.BTN)
        self.host_btn.setFixedWidth(120); self.host_btn.clicked.connect(self._on_host)
        row.addWidget(self.host_btn)
        v.addLayout(row)

        # ── Join header (text swaps per mode) ────────────────────────────────
        self.join_hdr = self._hdr("JOIN A MATCH")
        v.addWidget(self.join_hdr)

        # Online join: a browsable, auto-refreshing list of open rooms.
        self.online_browse_box = QWidget()
        ob = QVBoxLayout(self.online_browse_box); ob.setContentsMargins(0, 0, 0, 0)
        ob.setSpacing(6)
        self.room_list = QListWidget(); self.room_list.setStyleSheet(self._LIST_CSS)
        self.room_list.setFixedHeight(132)
        self.room_list.setIconSize(QSize(15, 15))    # painted padlock on locked rooms
        self.room_list.itemDoubleClicked.connect(lambda *_: self._on_browse_join())
        self.room_list.currentItemChanged.connect(lambda *_: self._update_pw_box())
        ob.addWidget(self.room_list)
        # Selecting a locked room reveals this inline password box directly under the
        # list; the JOIN request only fires once a password is typed here — see
        # _update_pw_box / _on_browse_join. Hidden for open rooms and until selected.
        self.pw_box = QWidget()
        pw = QHBoxLayout(self.pw_box); pw.setContentsMargins(0, 2, 0, 0); pw.setSpacing(8)
        self.pw_lock = QLabel()
        self.pw_lock.setPixmap(_lock_pixmap(15, theme.ACCENT))
        self.pw_lock.setFixedWidth(18)
        self.pw_lock.setStyleSheet("background:transparent;")
        pw.addWidget(self.pw_lock)
        self.pw_input = QLineEdit(); self.pw_input.setStyleSheet(self._FIELD_CSS)
        self.pw_input.setPlaceholderText("enter room password to join")
        self.pw_input.setMaxLength(64)
        self.pw_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw_input.returnPressed.connect(self._on_browse_join)
        pw.addWidget(self.pw_input, 1)
        self.pw_box.setVisible(False)
        ob.addWidget(self.pw_box)
        brow = QHBoxLayout(); brow.setSpacing(8)
        self.browse_hint = QLabel("Looking for open rooms…")
        self.browse_hint.setStyleSheet(f"color:{theme.TEXT_DIM};"
                                       f"font-family:'{theme.MONO_FAMILY}';font-size:11px;"
                                       f"background:transparent;")
        brow.addWidget(self.browse_hint, 1)
        self.browse_join_btn = QPushButton("JOIN"); self.browse_join_btn.setStyleSheet(theme.BTN)
        self.browse_join_btn.setFixedWidth(120)
        self.browse_join_btn.clicked.connect(self._on_browse_join)
        brow.addWidget(self.browse_join_btn)
        ob.addLayout(brow)
        v.addWidget(self.online_browse_box)

        # LAN join: type the host's address.
        self.lan_join_box = QWidget()
        lb = QHBoxLayout(self.lan_join_box); lb.setContentsMargins(0, 0, 0, 0); lb.setSpacing(8)
        self.ip_field = QLineEdit(); self.ip_field.setStyleSheet(self._FIELD_CSS)
        self.ip_field.setPlaceholderText("host address  (e.g. 127.0.0.1)")
        self.ip_field.setText("127.0.0.1")
        self.ip_field.returnPressed.connect(self._on_join)
        lb.addWidget(self.ip_field, 1)
        self.join_btn = QPushButton("JOIN"); self.join_btn.setStyleSheet(theme.BTN)
        self.join_btn.setFixedWidth(120); self.join_btn.clicked.connect(self._on_join)
        lb.addWidget(self.join_btn)
        v.addWidget(self.lan_join_box)

        # ── Solo: fight the bot ──────────────────────────────────────────────
        # No opponent around? Take the enemy fleet's usual seat against Captain Bob,
        # a local commander that plays the very same head-to-head ruleset — identical
        # purse, income, upgrades and turret slots — defending with turrets and
        # pressing back with a fleet, all paid out of its own bank.
        v.addWidget(self._hdr("NO OPPONENT?  ·  FIGHT THE COMPUTER"))
        bot_row = QHBoxLayout(); bot_row.setSpacing(8)
        bot_hint = QLabel("Captain Bob defends and counter-attacks on equal footing.")
        bot_hint.setWordWrap(True)
        bot_hint.setStyleSheet(f"color:{theme.TEXT_DIM};"
                               f"font-family:'{theme.MONO_FAMILY}';font-size:11px;"
                               f"background:transparent;")
        bot_row.addWidget(bot_hint, 1)
        self.bot_btn = QPushButton("FIGHT CAPTAIN BOB")
        self.bot_btn.setStyleSheet(theme.BTN); self.bot_btn.setFixedWidth(200)
        self.bot_btn.clicked.connect(self._on_play_bot)
        bot_row.addWidget(self.bot_btn)
        v.addLayout(bot_row)

        # ── Status + back ───────────────────────────────────────────────────
        self.status = QLabel(""); self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{theme.ACCENT};font-family:'{theme.MONO_FAMILY}';"
                                  f"font-size:12px;background:transparent;")
        self.status.setMinimumHeight(40)
        v.addWidget(self.status)

        foot = QHBoxLayout(); foot.setSpacing(8)
        self.back_btn = QPushButton("← BACK"); self.back_btn.setStyleSheet(theme.BTN_SMALL)
        self.back_btn.setFixedWidth(110); self.back_btn.clicked.connect(self._on_back)
        foot.addWidget(self.back_btn)
        foot.addStretch(1)
        # New commanders can read up on the two things that decide a head-to-head
        # match — capturing the midfield platform and the flagship bosses it buys —
        # in a briefing overlay drawn over this screen (see RulesOverlay).
        self.rules_btn = QPushButton("HOW TO PLAY"); self.rules_btn.setStyleSheet(theme.BTN_SMALL)
        self.rules_btn.setFixedWidth(150); self.rules_btn.clicked.connect(self._show_rules)
        foot.addWidget(self.rules_btn)
        v.addLayout(foot)

        self.panel = panel

        # Handshake poll (LAN host wait / any client connect) — separate from the
        # scene's render clock so it never blocks the UI.
        self._net_timer = QTimer(self); self._net_timer.setInterval(80)
        self._net_timer.timeout.connect(self._poll)
        # Repaints the open-room list from the background browser's latest fetch.
        self._browse_timer = QTimer(self); self._browse_timer.setInterval(500)
        self._browse_timer.timeout.connect(self._refresh_rooms)

        self._on_mode_changed()          # show the right join UI for the default mode

    def _paint_scene(self, p):
        # Match the campaign (save-select) sky: panned up off the water — no sea,
        # just clouds and a lone bomber crossing behind the title.
        paint_bomber_scene(p, self.width(), self.height(), self.sprites, self._t)

    def _hdr(self, text) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{theme.TEXT_FAINT};font-family:'{theme.HEAD_FAMILY}';"
                          f"font-size:10px;font-weight:bold;letter-spacing:2px;"
                          f"background:transparent;margin-top:6px;")
        return lbl

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.panel.adjustSize()
        self.panel.move((self.width() - self.panel.width()) // 2,
                        max(40, (self.height() - self.panel.height()) // 2))

    # ── Screen lifecycle ────────────────────────────────────────────────────
    def showEvent(self, ev):
        super().showEvent(ev)
        self._reset()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self._net_timer.stop()
        self._stop_browsing()

    def _reset(self):
        """Tear down any in-flight host/connect attempt and re-arm the controls."""
        self._net_timer.stop()
        if self._host is not None:
            self._host.cancel(); self._host = None
        if self._client is not None:
            link = self._client.poll()          # drop a connected-but-unlaunched link
            if link is not None:
                link.close()
            self._client = None
        self._client_auth_sent = False
        self._level_key = None
        self._set_busy(False)
        self.pw_input.clear()
        self.pw_box.setVisible(False)
        self._pw_room = None
        self.status.setText("")
        self._sync_browser()

    def _set_busy(self, busy: bool):
        for w in (self.host_btn, self.join_btn, self.ip_field, self.bot_btn,
                  self.rb_online, self.rb_lan, self.room_list, self.browse_join_btn,
                  self.pname_field, self.name_field, self.pass_field, self.pw_input):
            w.setEnabled(not busy)

    def _commander_name(self):
        """The typed commander name, cleaned up. Returns None (and posts a prompt)
        when it's blank, so a match can't start nameless; persists it on success so
        it's pre-filled next time."""
        name = " ".join(self.pname_field.text().split())[:16]
        if not name:
            self.status.setText("enter your commander name first — it's shown on your health bar")
            self.pname_field.setFocus()
            return None
        self.pname_field.setText(name)
        if name != SETTINGS.player_name:
            SETTINGS.player_name = name; SETTINGS.save()
        self._local_name = name
        return name

    def _on_back(self):
        self._reset(); self.back.emit()

    def _show_rules(self):
        """Open the head-to-head briefing over this screen — how the midfield
        platform is captured and how the flagship bosses work."""
        from .tutorial import RulesOverlay
        RulesOverlay(self)

    # ── Mode toggle ───────────────────────────────────────────────────────────
    def _online(self) -> bool:
        return self.rb_online.isChecked()

    def _on_mode_changed(self, *_):
        online = self._online()
        self.online_browse_box.setVisible(online)
        self.lan_join_box.setVisible(not online)
        self.create_meta_box.setVisible(online)   # name/password: online rooms only
        if online:
            self.host_hdr.setText("CREATE A ROOM")
            self.join_hdr.setText("OPEN ROOMS")
            self.host_btn.setText("CREATE")
        else:
            self.host_hdr.setText("HOST A MATCH")
            self.join_hdr.setText("JOIN A MATCH")
            self.host_btn.setText("HOST")
        self._sync_browser()

    # ── Open-room browser (online mode) ─────────────────────────────────────────
    def _sync_browser(self):
        """Browse the relay's open rooms exactly while we're idle in online mode."""
        want = (self._online() and self.isVisible()
                and self._host is None and self._client is None)
        if want and self._browser is None:
            self._start_browsing()
        elif not want and self._browser is not None:
            self._stop_browsing()

    def _start_browsing(self):
        from .. import net
        self._rooms_sig = None                       # force one rebuild on (re)entry
        host, port = net.relay_endpoint()
        self._browser = net.NetRoomBrowser(host, port)
        self._browser.start()
        self.browse_hint.setText("Looking for open rooms…")
        self._browse_timer.start()

    def _stop_browsing(self):
        self._browse_timer.stop()
        if self._browser is not None:
            self._browser.stop(); self._browser = None

    def _refresh_rooms(self):
        if self._browser is None:
            return
        rooms = list(self._browser.rooms)
        # Rebuild the list ONLY when the open-room set actually changed. The browser
        # ticks 2×/s; rebuilding every tick would fight the user's current selection,
        # so a stable list is left untouched and the highlighted room stays put.
        sig = [(r.get("id"), r.get("name"), r.get("map"), bool(r.get("locked")))
               for r in rooms]
        if sig != getattr(self, "_rooms_sig", None):
            self._rooms_sig = sig
            cur = self.room_list.currentItem()
            sel_id = cur.data(Qt.ItemDataRole.UserRole) if cur is not None else None
            self.room_list.blockSignals(True)       # rebuild silently, preserving selection
            self.room_list.clear()
            for r in rooms:
                locked = bool(r.get("locked"))
                name = r.get("name", "room")
                # The host advertises its chosen map theme in `map`; show it as a
                # dim suffix so joiners know the battleground before committing.
                theme_name = (r.get("map") or "").strip()
                label = f"{name}   ·   {theme_name}" if theme_name and theme_name != "?" else name
                item = QListWidgetItem(label)
                if locked:
                    # A painted padlock (not an emoji) marks protected rooms.
                    item.setIcon(QIcon(_lock_pixmap(15, theme.ACCENT)))
                item.setData(Qt.ItemDataRole.UserRole, r.get("id"))
                item.setData(Qt.ItemDataRole.UserRole + 1, locked)   # drives the pw box
                item.setData(Qt.ItemDataRole.UserRole + 2, name)     # clean room label
                self.room_list.addItem(item)
                if r.get("id") == sel_id:
                    self.room_list.setCurrentItem(item)
            self.room_list.blockSignals(False)
            self._update_pw_box()      # keep the pw box in sync after a rebuild
        if self._browser.error and not rooms:
            self.browse_hint.setText(self._browser.error)
        elif not rooms:
            self.browse_hint.setText("No open rooms yet...")
        else:
            self.browse_hint.setText(f"{len(rooms)} open room(s) — select one and JOIN.")

    def _update_pw_box(self):
        """Show the inline password box directly under the list iff the selected
        room is locked; hide it (and clear any typed secret) otherwise. Clearing on
        deselect means a password never lingers when you move to a different room."""
        item = self.room_list.currentItem()
        rid = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        locked = bool(item.data(Qt.ItemDataRole.UserRole + 1)) if item is not None else False
        armed = rid if locked else None
        if armed == getattr(self, "_pw_room", None):
            return          # same room + state (e.g. a refresh tick) — leave the field alone
        self._pw_room = armed
        self.pw_box.setVisible(locked)
        self.pw_input.clear()
        if locked:
            self.pw_input.setFocus()

    # ── Host / create ─────────────────────────────────────────────────────────
    def _selected_level(self):
        """The battle-map theme picked in the selector, keyed back to its LevelDef.
        Falls back to the first level should the combo be empty or its key stale."""
        key = self.theme_combo.currentData()
        return self.data.levels.get(key) or self.data.levels_sorted()[0]

    def _on_host(self):
        from .. import net
        if self._commander_name() is None:
            return
        # The host's chosen map theme; announced to the joiner via the hello.
        level = self._selected_level()
        level_key = level.key
        if self._online():
            # Create the room and drop straight into the battle: the canvas holds
            # on "awaiting player" until the relay pairs a joiner into this room.
            host, port = net.relay_endpoint()
            # Use the commander's chosen name, or fall back to a friendly code.
            name = self.name_field.text().strip() or f"Room {self._make_room_code()}"
            password = self.pass_field.text()       # "" ⇒ an open room
            self._stop_browsing()
            from . import backdrop
            pending = net.NetRelayHost(host, port, name=name,
                                       map_=backdrop.label_for_level(level.order,
                                                                     level.backdrop),
                                       password=password)
            pending.start()
            self._launch("host", pending, level_key, name=self._local_name)
            return
        # LAN: bind a port and wait in the lobby for a direct connection.
        self._reset()
        self._level_key = level_key
        self._host = net.NetHost(net.DEFAULT_PORT)
        if not self._host.start():
            self.status.setText(self._host.error or "could not start hosting")
            self._host = None; return
        ips = "  /  ".join(net.local_ips())
        self._set_busy(True)
        self.status.setText(f"Hosting on  {ips}  ·  port {net.DEFAULT_PORT}\n"
                            f"Waiting for an opponent to join…")
        self._net_timer.start()

    # ── Solo vs the bot ─────────────────────────────────────────────────────
    def _on_play_bot(self):
        """Start a solo head-to-head against Captain Bob — no networking at all,
        just the PvP battle with the enemy fleet driven by the local bot."""
        if self._commander_name() is None:
            return
        self._stop_browsing()
        level = self._selected_level()               # same map theme as a hosted match
        self.play_bot.emit(level.key, self._local_name)

    # ── Join ────────────────────────────────────────────────────────────────
    def _on_browse_join(self):
        """Online: join the room selected in the open-room list."""
        from .. import net
        if self._commander_name() is None:
            return
        item = self.room_list.currentItem()
        if item is None:
            self.status.setText("select a room to join first"); return
        rid = item.data(Qt.ItemDataRole.UserRole)
        locked = bool(item.data(Qt.ItemDataRole.UserRole + 1))
        room_label = item.data(Qt.ItemDataRole.UserRole + 2) or item.text()
        password = ""
        if locked:
            # The join only fires once a password is typed into the inline box —
            # reveal it and hold if it's still empty.
            self._update_pw_box()
            password = self.pw_input.text()
            if not password:
                self.pw_box.setVisible(True)
                self.pw_input.setFocus()
                self.status.setText("this room is locked — enter its password to join")
                return
        self._stop_browsing()
        host, port = net.relay_endpoint()
        self._client = net.NetRelayClient(host, rid, port, password=password)
        self._client.start()
        self._set_busy(True)
        self.status.setText(f"Joining {room_label}…")
        self._net_timer.start()

    def _on_join(self):
        """LAN: connect directly to the host address typed in."""
        from .. import net
        if self._commander_name() is None:
            return
        addr = self.ip_field.text().strip()
        if not addr:
            self.status.setText("enter the host's address first"); return
        host, port = addr, net.DEFAULT_PORT
        if ":" in addr:
            host, _, p = addr.rpartition(":")
            try:    port = int(p)
            except ValueError: pass
        self._reset()
        self._client = net.NetClient(host, port); self._client.start()
        self._set_busy(True)
        self.status.setText(f"Connecting to {host}:{port}…")
        self._net_timer.start()

    @staticmethod
    def _make_room_code() -> str:
        # 4 chars from an unambiguous alphabet (no 0/O, 1/I) — a friendly room label.
        return "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
                        for _ in range(4))

    # ── Handshake poll ──────────────────────────────────────────────────────
    def _poll(self):
        if self._host is not None:
            if self._host.error:
                self.status.setText(self._host.error); self._reset(); return
            link = self._host.poll()
            if link is not None:
                self._host = None
                # The host battle sends the 'hello' (level + session token, plus its
                # commander name) itself once its link is live, so the lobby no longer does.
                self._launch("host", link, self._level_key, name=self._local_name)
            return
        if self._client is not None:
            if self._client.error:
                self.status.setText(self._client.error); self._reset(); return
            link = self._client.poll()
            if link is None:
                return                                  # still connecting
            if not link.alive:
                self.status.setText("connection dropped"); self._reset(); return
            # Prove the room password to the host before it will admit us. This is
            # host-authoritative (the relay is dumb, see relay.py), so it holds even
            # if the relay skipped its own check. Sent once, the instant we're paired;
            # an open room / LAN host has no password so an empty proof passes.
            if not self._client_auth_sent:
                link.send({"t": "auth", "pw": getattr(self._client, "password", "")})
                self._client_auth_sent = True
            for msg in link.poll():                     # wait for the host's hello
                if msg.get("t") == "denied":            # host rejected our password
                    self.status.setText(msg.get("msg", "wrong password"))
                    self._reset(); return
                if msg.get("t") == "hello":
                    self._client = None
                    # The hello carries the host's commander name — the client learns
                    # its opponent here; its own name it sends back once in-battle.
                    self._launch("client", link, msg.get("level"),
                                 msg.get("session", ""), name=self._local_name,
                                 opp=msg.get("name", ""))
                    return

    def _launch(self, role, link, level_key, session="", name="", opp=""):
        self._net_timer.stop()
        self._stop_browsing()
        self._host = None; self._client = None      # ownership passes to the battle
        self.launch.emit(role, link, level_key or self.data.levels_sorted()[0].key,
                         session or "", name or "", opp or "")


# ─── Save select ──────────────────────────────────────────────────────────────
class _SlotRow(QFrame):
    continue_ = pyqtSignal(int)
    new_      = pyqtSignal(int)
    erase_    = pyqtSignal(int)

    # Erasing is irreversible, so the button arms first: ERASE → SURE? (danger-lit)
    # and only a second press within the window actually wipes the profile.
    _ARM_MS = 2500
    _ERASE_ARMED = (f"QPushButton{{background:{theme.BG};border:1px solid {theme.DANGER};"
                    f"border-radius:0;color:{theme.DANGER_HI};font-family:'{theme.HEAD_FAMILY}';"
                    f"font-size:11px;font-weight:bold;letter-spacing:1px;padding:5px 14px;}}")

    def __init__(self, slot: int):
        super().__init__()
        self.slot = slot; self.save = None
        self.setFixedSize(780, 84)
        h = QHBoxLayout(self); h.setContentsMargins(360, 0, 18, 0); h.setSpacing(10)
        h.addStretch()
        self.primary = QPushButton(); self.primary.setStyleSheet(theme.BTN); self.primary.setFixedWidth(150)
        self.erase = QPushButton("ERASE"); self.erase.setStyleSheet(theme.BTN_SMALL); self.erase.setFixedWidth(76)
        self.erase.clicked.connect(self._on_erase)
        self._arm_timer = QTimer(self); self._arm_timer.setSingleShot(True)
        self._arm_timer.timeout.connect(self._disarm)
        h.addWidget(self.primary); h.addWidget(self.erase)

    def _on_erase(self):
        if self._arm_timer.isActive():               # second press: really erase
            self._disarm()
            self.erase_.emit(self.slot)
        else:                                        # first press: arm the switch
            self.erase.setText("SURE?")
            self.erase.setStyleSheet(self._ERASE_ARMED)
            self._arm_timer.start(self._ARM_MS)

    def _disarm(self):
        self.erase.setText("ERASE")
        self.erase.setStyleSheet(theme.BTN_SMALL)

    def set_save(self, save):
        self.save = save
        self._arm_timer.stop(); self._disarm()
        try: self.primary.clicked.disconnect()
        except TypeError: pass
        if save is None:
            self.primary.setText("NEW")
            self.primary.clicked.connect(lambda: self.new_.emit(self.slot)); self.erase.hide()
        else:
            self.primary.setText("CONTINUE")
            self.primary.clicked.connect(lambda: self.continue_.emit(self.slot)); self.erase.show()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        filled = self.save is not None
        theme.plate(p, 0, 0, W, H, base=theme.PANEL_HI if filled else theme.PANEL, cut=11,
                    corners=(True, False, True, False),
                    accent=theme.ACCENT if filled else theme.LINE_HI, accent_side="left")
        theme.led(p, 22, H / 2, theme.PHOSPHOR if filled else theme.TEXT_FAINT, r=3, on=filled)
        p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.mono(10))
        p.drawText(38, 12, 200, 14, int(Qt.AlignmentFlag.AlignLeft), f"PROFILE {self.slot}")
        if self.save is None:
            p.setPen(QColor(theme.TEXT_FAINT)); p.setFont(theme.stencil(22, 2))
            p.drawText(38, 34, 320, 34, int(Qt.AlignmentFlag.AlignLeft), "EMPTY SAVE")
        else:
            theme.engraved_label(p, self.save.difficulty.upper(), 38, 30, 330,
                                 theme.head(22, 2), theme.ACCENT)
            p.setPen(QColor(theme.TEXT)); p.setFont(theme.mono(10))
            p.drawText(38, 58, 200, 16, int(Qt.AlignmentFlag.AlignVCenter),
                       f"{self.save.cleared_count()}/12 COMPLETED")
            # star tally as a vector glyph so it never depends on a font's ★
            sx = 200
            theme.draw_stars(p, sx, 56, 13, 1, total=1)
            p.setPen(QColor(theme.STAR)); p.setFont(theme.mono(10, True))
            p.drawText(sx + 20, 58, 90, 16, int(Qt.AlignmentFlag.AlignVCenter),
                       f"{self.save.total_stars()}")


class _DifficultyOverlay(QWidget):
    chosen    = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent)
        v = QVBoxLayout(self); v.setAlignment(Qt.AlignmentFlag.AlignCenter); v.setSpacing(14)
        v.addWidget(_title("SELECT DIFFICULTY", 18, theme.TEXT, 5),
                    alignment=Qt.AlignmentFlag.AlignCenter)
        warn = _title("LOCKED FOR THIS PROFILE", 9, theme.ACCENT, 3)
        v.addWidget(warn, alignment=Qt.AlignmentFlag.AlignCenter); v.addSpacing(10)
        desc = {"easy": "Weaker more timid enemies", "normal": "Standard engagement", "hard": "More health, more enemies"}
        for d in DIFFICULTY_ORDER:
            b = QPushButton(f"{d.upper()}    {desc[d]}"); b.setStyleSheet(theme.BTN); b.setFixedWidth(360)
            b.clicked.connect(lambda _=False, dd=d: self.chosen.emit(dd))
            row = QHBoxLayout(); row.addStretch(); row.addWidget(b); row.addStretch(); v.addLayout(row)
        c = QPushButton("CANCEL"); c.setStyleSheet(theme.BTN_SMALL); c.setFixedWidth(120)
        c.clicked.connect(self.cancelled.emit)
        row = QHBoxLayout(); row.addStretch(); row.addWidget(c); row.addStretch(); v.addLayout(row)

    def paintEvent(self, _):
        QPainter(self).fillRect(self.rect(), QColor(6, 9, 12, 235))


class SaveSelectScreen(_Scene):
    chosen     = pyqtSignal(int)
    create     = pyqtSignal(int, str)
    quit_game  = pyqtSignal()

    def __init__(self, manager, data, sprites):
        super().__init__(sprites)
        self.manager = manager; self.data = data
        lay = QVBoxLayout(self); lay.setContentsMargins(72, 56, 72, 40); lay.setSpacing(0)

        # The same face and point size as the main menu's painted lockup, so the
        # two titles match as the screens slide past each other.
        lay.addWidget(_title("PENUMBRA", 58, theme.TEXT, 4))
        lay.addSpacing(40)
        lay.addWidget(_title("SELECT SAVE", 11, theme.TEXT_DIM, 4))
        lay.addSpacing(10)

        self.rows = []
        for slot in (1, 2, 3):
            r = _SlotRow(slot)
            r.continue_.connect(self.chosen.emit); r.new_.connect(self._new); r.erase_.connect(self._erase)
            self.rows.append(r)
            wrap = QHBoxLayout(); wrap.addWidget(r); wrap.addStretch()
            lay.addLayout(wrap); lay.addSpacing(12)

        lay.addStretch()
        q = QPushButton("← MENU"); q.setStyleSheet(theme.BTN_SMALL); q.setFixedWidth(120)
        q.clicked.connect(self.quit_game.emit)
        s = QPushButton("SETTINGS"); s.setStyleSheet(theme.BTN_SMALL); s.setFixedWidth(120)
        self.settings_panel = SettingsOverlay(self)
        s.clicked.connect(self.settings_panel.open)
        # The full-screen toggle is the shared top-right corner icon from _Scene;
        # the footer keeps just the settings + menu text buttons.
        foot = QHBoxLayout(); foot.addStretch()
        foot.addWidget(s); foot.addSpacing(8)
        foot.addWidget(q); lay.addLayout(foot)

        self.overlay = _DifficultyOverlay(self); self.overlay.hide()
        self.overlay.chosen.connect(self._make); self.overlay.cancelled.connect(self.overlay.hide)
        self._pending = None
        self.refresh()

    def _paint_scene(self, p):
        # Panned up off the water: sea at the very bottom, no battleship, a bomber
        # crossing the sky and dropping its payload one bomb at a time.
        paint_bomber_scene(p, self.width(), self.height(), self.sprites, self._t)

    def refresh(self):
        for r in self.rows: r.set_save(self.manager.load(r.slot))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)          # pins the top-right full-screen toggle
        self.overlay.setGeometry(self.rect())
    def _new(self, slot):
        self._pending = slot; self.overlay.setGeometry(self.rect()); self.overlay.show(); self.overlay.raise_()
    def _make(self, diff):
        self.overlay.hide()
        if self._pending is not None: self.create.emit(self._pending, diff); self._pending = None
    def _erase(self, slot): self.manager.erase(slot); self.refresh()


# ─── Campaign ─────────────────────────────────────────────────────────────────
class _LevelCard(QFrame):
    selected = pyqtSignal(str)
    play     = pyqtSignal(str)
    H = 92

    def __init__(self, level, order_n, sprites):
        super().__init__()
        self.level = level; self.order_n = order_n; self.sprites = sprites
        self.state = "locked"; self.info = None; self.is_selected = False
        self.setFixedHeight(self.H); self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_state(self, state, info, selected):
        self.state = state; self.info = info; self.is_selected = selected; self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton: self.selected.emit(self.level.key)
    def mouseDoubleClickEvent(self, ev):
        if self.state != "locked": self.play.emit(self.level.key)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        on = self.is_selected
        cleared = self.state == "cleared"
        # A chamfered steel dossier tab. Selected = lit plate + amber edge; the
        # top signal edge encodes status (amber selected / phosphor cleared).
        edge = theme.ACCENT if cleared else theme.LINE_HI
        theme.plate(p, 0, 0, W, H, base=theme.PANEL, cut=9, lit=on,
                    corners=(True, False, True, False),
                    accent=(theme.ACCENT if on else (theme.PHOSPHOR if cleared else None)),
                    accent_side="top",
                    border=theme.ACCENT if on else None)

        # Stencilled operation number, top-left — a stamped hull marking
        num_col = theme.ACCENT if on else (theme.TEXT_DIM if cleared else theme.TEXT_FAINT)
        p.setPen(QColor(num_col)); p.setFont(theme.stencil(21, 1))
        p.drawText(11, 8, 60, 26, int(Qt.AlignmentFlag.AlignLeft), f"{self.order_n:02d}")

        # status lamp, top-right
        lamp = theme.ACCENT if on else (theme.PHOSPHOR if cleared else theme.TEXT_FAINT)
        theme.led(p, W - 16, 16, lamp, r=3, on=(on or cleared))

        if self.state == "locked":
            theme.draw_icon(p, "lock", QRectF(W - 34, H/2 + 2, 20, 20), QColor(theme.TEXT_FAINT))
            p.setPen(QColor(theme.TEXT_FAINT)); p.setFont(theme.head(12, 3))
            p.drawText(11, H - 30, W - 20, 18, int(Qt.AlignmentFlag.AlignLeft), "CLASSIFIED")
            p.setFont(theme.mono(8)); p.setPen(QColor(theme.TEXT_FAINT))
            p.drawText(11, H - 16, W - 20, 12, int(Qt.AlignmentFlag.AlignLeft), "LOCKED — NO INTEL")
            return
        if self.state == "new":
            p.setPen(QColor(theme.PHOSPHOR)); p.setFont(theme.head(15, 2))
            p.drawText(58, 9, W - 64, 22, int(Qt.AlignmentFlag.AlignVCenter), "READY")
            p.setPen(QColor(theme.ACCENT)); p.setFont(theme.head(11, 0.5))
            p.drawText(11, H - 42, W - 20, 16, int(Qt.AlignmentFlag.AlignLeft), "AWAITING ORDERS")
            p.setFont(theme.mono(8)); p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(11, H - 22, W - 20, 12, int(Qt.AlignmentFlag.AlignLeft), "TARGET UNIDENTIFIED")
            return
        # cleared — boss revealed as a dark recon silhouette (kept clear of the
        # status lamp in the top-right corner)
        bp = _boss_pixmap(self.sprites, self.level.boss, W - 84, 30)
        if bp:
            p.setOpacity(0.85); p.drawPixmap(58, 8, bp); p.setOpacity(1.0)
        p.setPen(QColor(theme.TEXT)); p.setFont(theme.head(13, 1))
        nm = (self.info.get("name") if self.info else None) or self.level.boss.upper()
        p.drawText(11, H - 46, W - 16, 16, int(Qt.AlignmentFlag.AlignLeft), nm.upper())
        theme.draw_stars(p, 11, H - 28, 12, self.info.get("stars", 0) if self.info else 0, total=3)
        p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.mono(8))
        bt = self.info.get("best_time") if self.info else None
        if bt is not None:
            p.drawText(66, H - 26, W - 72, 14, int(Qt.AlignmentFlag.AlignLeft),
                       f"{bt:.0f}s  ·  {self.info.get('best_score',0)}")


class _Dossier(QWidget):
    """The large left-hand boss showcase."""
    def __init__(self, sprites):
        super().__init__(); self.sprites = sprites
        self.level = None; self.state = "locked"; self.info = None
        self.setMinimumWidth(500)

    def set_level(self, level, state, info):
        self.level = level; self.state = state; self.info = info; self.update()

    def _stamp(self, p, W, H, text, color):
        """A rubber-stamped classification marking, lower-right, slightly rotated —
        as if pressed onto the file after filing."""
        p.save()
        f = theme.head(16, 3); p.setFont(f)
        # Size the box to the text so long markings (e.g. DECLASSIFIED) fit,
        # keeping the right edge anchored so it never clips off the panel.
        tw = QFontMetrics(f).horizontalAdvance(text) + 24
        p.translate(W - 50 - tw, H - 84); p.rotate(-8)
        p.setPen(QPen(QColor(color), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.setOpacity(0.8); p.drawRect(0, 0, tw, 32)
        p.drawText(0, 0, tw, 32, int(Qt.AlignmentFlag.AlignCenter), text)
        p.setOpacity(1.0); p.restore()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        # The whole dossier is one riveted steel plate — an intelligence folder.
        theme.plate(p, 0, 0, W, H, base=theme.PANEL, cut=14,
                    corners=(True, False, True, False), riveted=True,
                    accent=theme.ACCENT, accent_side="top", textured=True)
        if self.level is None: return

        M = 30
        # ── Header: operation dossier line + classification stamp ────────────────
        p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.mono(11))
        p.drawText(M, 22, W - 2*M, 16, int(Qt.AlignmentFlag.AlignLeft),
                   f"INTELLIGENCE DOSSIER  //  FILE N-{self.level.order:02d}")
        theme.engraved_label(p, f"OPERATION {self.level.order:02d} — {self.level.name.upper()}",
                             M, 40, W - 2*M, theme.head(20, 2), theme.TEXT)
        p.setPen(QPen(QColor(theme.LINE), 1)); p.drawLine(M, 74, W - M, 74)

        # ── Recon window: a rivet-framed screen with scanlines ───────────────────
        fx, fy = M, 92; fw = W - 2*M; fh = int(H * 0.44)
        theme.plate(p, fx, fy, fw, fh, base="#0a0f13", cut=10,
                    corners=(True, True, True, True), brushed=False, riveted=True,
                    border=theme.LINE_HI)
        p.save(); p.setClipPath(theme.chamfer_path(fx, fy, fw, fh, 10))
        # subtle recon grid
        p.setPen(QPen(QColor(theme.LINE), 1)); p.setOpacity(0.4)
        for gx in range(fx + 40, fx + fw, 60): p.drawLine(gx, fy, gx, fy + fh)
        for gy in range(fy + 34, fy + fh, 46): p.drawLine(fx, gy, fx + fw, gy)
        p.setOpacity(1.0)

        cx_center = fx + fw / 2; cy_center = fy + fh / 2
        if self.state == "cleared":
            bp = _boss_pixmap(self.sprites, self.level.boss, fw - 90, fh - 44)
            if bp:
                p.drawPixmap(int(cx_center - bp.width()/2), int(cy_center - bp.height()/2), bp)
            p.setPen(QColor(theme.PHOSPHOR)); p.setFont(theme.mono(9))
            p.drawText(fx + 10, fy + 8, fw - 20, 14, int(Qt.AlignmentFlag.AlignLeft), "◉ TARGET ACQUIRED")
        elif self.state == "new":
            p.setPen(QColor(theme.LINE_HI)); p.setFont(theme.stencil(150, 0))
            p.drawText(fx, fy, fw, fh, int(Qt.AlignmentFlag.AlignCenter), "?")
            p.setPen(QColor(theme.ACCENT)); p.setFont(theme.mono(9))
            p.drawText(fx + 10, fy + 8, fw - 20, 14, int(Qt.AlignmentFlag.AlignLeft), "◉ NO VISUAL — INBOUND")
        else:
            theme.draw_icon(p, "lock", QRectF(cx_center - 30, cy_center - 34, 60, 60), QColor(theme.TEXT_FAINT))
            p.setPen(QColor(theme.TEXT_FAINT)); p.setFont(theme.mono(9))
            p.drawText(fx + 10, fy + 8, fw - 20, 14, int(Qt.AlignmentFlag.AlignLeft), "◉ SIGNAL LOST")
        theme.scanlines(p, QRectF(fx, fy, fw, fh), gap=3, alpha=26)
        p.restore()

        # ── Name plate + intel readout below the window ──────────────────────────
        ty = fy + fh + 22
        if self.state == "cleared":
            self._stamp(p, W, H, "DECLASSIFIED", theme.PHOSPHOR)
            p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.head(11, 4))
            p.drawText(M, ty, W - 2*M, 14, int(Qt.AlignmentFlag.AlignLeft), "HOSTILE FLAGSHIP")
            theme.engraved_label(p, (self.info.get("name") or self.level.boss).upper(),
                                 M, ty + 16, W - 2*M, theme.stencil(40, 2), theme.TEXT)
            theme.draw_stars(p, M, ty + 74, 22, self.info.get("stars", 0), total=3)
            # telemetry read-out block, mono
            ry = ty + 108
            for lbl, val, col in (("BEST TIME", f"{self.info.get('best_time', 0):.1f} S", theme.TEXT),
                                  ("BEST SCORE", f"{self.info.get('best_score', 0)}", theme.ACCENT)):
                p.setPen(QColor(theme.TEXT_FAINT)); p.setFont(theme.mono(10))
                p.drawText(M, ry, 140, 16, int(Qt.AlignmentFlag.AlignLeft), lbl)
                p.setPen(QColor(col)); p.setFont(theme.mono(13, True))
                p.drawText(M + 150, ry, 220, 16, int(Qt.AlignmentFlag.AlignLeft), val)
                ry += 24
        elif self.state == "new":
            self._stamp(p, W, H, "SECRET", theme.ACCENT)
            p.setPen(QColor(theme.ACCENT)); p.setFont(theme.head(11, 4))
            p.drawText(M, ty, W - 2*M, 14, int(Qt.AlignmentFlag.AlignLeft), "THREAT ASSESSMENT")
            theme.engraved_label(p, "ENEMY UNIDENTIFIED", M, ty + 16,
                                 W - 2*M, theme.stencil(30, 1), theme.TEXT)
        else:
            self._stamp(p, W, H, "CLASSIFIED", theme.DANGER)
            p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.stencil(26, 2))
            p.drawText(M, ty + 10, W - 2*M, 34, int(Qt.AlignmentFlag.AlignLeft), "ACCESS DENIED")
            p.setPen(QColor(theme.TEXT_FAINT)); p.setFont(theme.mono(11))
            p.drawText(M, ty + 52, W - 2*M, 18, int(Qt.AlignmentFlag.AlignLeft),
                       "Clear the previous operation to unlock this file.")


class _CatalogPanel(QWidget):
    """Browsable ship catalog: pick a unit (top selector) and read what it does
    plus a usage tip (detail below)."""
    SEL_TOP = 8; SEL_H = 44

    def __init__(self, data, sprites):
        super().__init__(); self.data = data; self.sprites = sprites
        self.cleared = 0; self.sel = 0
        self.setMinimumHeight(176)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_cleared(self, n): self.cleared = n; self.update()
    def _cw(self): return (self.width() - 16) / len(ROSTER)

    def mousePressEvent(self, ev):
        y = ev.position().y()
        if self.SEL_TOP <= y <= self.SEL_TOP + self.SEL_H:
            i = int((ev.position().x() - 8) / self._cw())
            if 0 <= i < len(ROSTER): self.sel = i; self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        theme.plate(p, 0, 0, W, H, base=theme.PANEL, cut=11,
                    corners=(True, False, True, False), accent=theme.ACCENT,
                    accent_side="top", textured=True)

        # ── Selector row of unit thumbnails ───────────────────────────────────
        cw = self._cw()
        for i, (key, stg, label) in enumerate(ROSTER):
            sdef = self.data.vehicles.get(key)
            if sdef is None: continue
            x = 8 + i * cw; unlocked = stg <= self.cleared; chosen = (i == self.sel)
            p.setPen(QPen(QColor(theme.ACCENT if chosen else theme.LINE),
                          2 if chosen else 1)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(x + 1, self.SEL_TOP, cw - 3, self.SEL_H))
            sw = int(cw - 10); sh = max(1, round(sw * sdef.display_h / max(1, sdef.display_w)))
            sh = min(sh, self.SEL_H - 14)
            sc = _ship_display_pixmap(self.sprites, key, sw, sh)
            if sc:
                p.setOpacity(1.0 if unlocked else 0.3)
                p.drawPixmap(int(x + (cw - sc.width()) / 2),
                             int(self.SEL_TOP + (self.SEL_H - sc.height()) / 2), sc)
                p.setOpacity(1.0)
            if not unlocked:
                theme.draw_icon(p, "lock", QRectF(x + cw - 16, self.SEL_TOP + self.SEL_H - 14, 11, 11),
                                QColor(theme.ACCENT))

        # ── Detail of the selected unit ───────────────────────────────────────
        key, stg, label = ROSTER[self.sel]
        sdef = self.data.vehicles.get(key)
        if sdef is None: return
        unlocked = stg <= self.cleared
        dy = self.SEL_TOP + self.SEL_H + 12
        p.setPen(QPen(QColor(theme.LINE), 1))
        p.drawLine(8, dy - 6, W - 8, dy - 6)

        # left: large sprite
        spr = self.sprites.get(f"player_{key}")
        sx = 14
        sw = min(150, int(W * 0.28))
        sh = max(1, round(sw * sdef.display_h / max(1, sdef.display_w)))
        if spr and not spr.isNull():
            sc = _ship_display_pixmap(self.sprites, key, sw, sh)
            if sc:
                p.setOpacity(1.0 if unlocked else 0.35)
                p.drawPixmap(sx, int(dy + 6), sc)
                p.setOpacity(1.0)
        tx = 14 + sw + 16

        p.setPen(QColor(theme.TEXT)); p.setFont(theme.head(16, 1))
        p.drawText(tx, dy + 2, W - tx - 10, 22, Qt.AlignmentFlag.AlignVCenter, label.upper())
        typ = "AIRCRAFT" if getattr(sdef, 'unit_type', 'ship') == 'plane' else "SHIP"
        status = "READY" if unlocked else f"LOCKED — UNLOCKS AT LEVEL {stg + 1}"
        p.setPen(QColor(theme.GOOD if unlocked else theme.ACCENT)); p.setFont(theme.head(9, 1))
        p.drawText(tx, dy + 26, W - tx - 10, 14, Qt.AlignmentFlag.AlignVCenter, f"{typ}  ·  {status}")

        p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.font(10))
        spd = f"   SPD {int(sdef.speed)}" if getattr(sdef, 'speed', 0) else ""
        p.drawText(tx, dy + 46, W - tx - 10, 16, Qt.AlignmentFlag.AlignVCenter,
                   f"COST ${sdef.cost}    HP {sdef.hp}{spd}")
        yy = dy + 64
        if unlocked:
            wl = weapon_lines(sdef)
            p.setPen(QColor(theme.TEXT)); p.setFont(theme.font(9))
            for ln in wl[:2]:
                # 15px line box: a 9pt line needs room for descenders (g, y).
                p.drawText(tx, yy, W - tx - 10, 15, Qt.AlignmentFlag.AlignLeft, ln); yy += 15

        # tip — below the sprite, full width. Locked units stay classified.
        ty = max(dy + 6 + sh if (spr and not spr.isNull()) else dy + 90, yy + 8)
        if unlocked:
            p.setPen(QColor(theme.ACCENT)); p.setFont(theme.head(9, 1))
            p.drawText(14, ty, W - 28, 14, Qt.AlignmentFlag.AlignLeft, "TIP")
            p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.font(10))
            p.drawText(QRectF(14, ty + 14, W - 28, H - ty - 18),
                       int(Qt.TextFlag.TextWordWrap), progression.tip(key))
        else:
            p.setPen(QColor(theme.ACCENT)); p.setFont(theme.head(9, 1))
            p.drawText(14, ty, W - 28, 14, Qt.AlignmentFlag.AlignLeft, "CLASSIFIED")
            p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.font(10))
            p.drawText(QRectF(14, ty + 14, W - 28, H - ty - 18),
                       int(Qt.TextFlag.TextWordWrap),
                       f"Locked — unlocks at Level {stg + 1}. Clear operations to declassify.")


class CampaignScreen(_Scene):
    play       = pyqtSignal(str)
    back       = pyqtSignal()
    quit_game  = pyqtSignal()
    tutorial   = pyqtSignal()

    def _paint_scene(self, p):
        # Open sky only over the campaign operations — no moon, no sea, horizon
        # or drifting ships; the sky gradient fills the whole frame.
        paint_navy_scene(p, self.width(), self.height(), self.sprites, self._t, sea=False)

    def __init__(self, data, sprites):
        super().__init__(sprites)
        self.data = data; self.save = None; self.selected_key = None
        self.ordered = [l.key for l in data.levels_sorted()]

        lay = QVBoxLayout(self); lay.setContentsMargins(30, 22, 30, 22); lay.setSpacing(16)
        head = QHBoxLayout()
        back_btn = QPushButton("← BACK"); back_btn.setStyleSheet(theme.BTN_SMALL); back_btn.setFixedWidth(100)
        back_btn.clicked.connect(self.back.emit)
        head.addWidget(back_btn); head.addSpacing(16)
        head.addWidget(_title("CAMPAIGN", 22, theme.TEXT, 4))
        head.addStretch()
        self.diff_lbl = _title("", 11, theme.ACCENT, 2); head.addWidget(self.diff_lbl); head.addSpacing(14)
        howto_btn = QPushButton("HOW TO PLAY"); howto_btn.setStyleSheet(theme.BTN_SMALL); howto_btn.setFixedWidth(120)
        howto_btn.clicked.connect(self.tutorial.emit); head.addWidget(howto_btn); head.addSpacing(8)
        set_btn = QPushButton("SETTINGS"); set_btn.setStyleSheet(theme.BTN_SMALL); set_btn.setFixedWidth(100)
        self.settings_panel = SettingsOverlay(self)
        set_btn.clicked.connect(self.settings_panel.open)
        head.addWidget(set_btn); head.addSpacing(8)
        quit_btn = QPushButton("QUIT"); quit_btn.setStyleSheet(theme.BTN_SMALL); quit_btn.setFixedWidth(90)
        quit_btn.clicked.connect(self.quit_game.emit); head.addWidget(quit_btn)
        # Leave the top-right corner clear for the shared floating full-screen
        # toggle (_Scene.fs_btn) so the header buttons never sit under it.
        head.addSpacing(self._FS_SIZE + 24)
        lay.addLayout(head)

        body = QHBoxLayout(); body.setSpacing(22)
        self.dossier = _Dossier(self.sprites)
        body.addWidget(self.dossier, 4)

        right = QVBoxLayout(); right.setSpacing(8)
        right.addWidget(_title("OPERATIONS", 10, theme.TEXT_DIM, 3))
        grid = QGridLayout(); grid.setSpacing(12)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        COLS = 4
        for c in range(COLS): grid.setColumnStretch(c, 1)
        self.cards = {}
        for i, l in enumerate(data.levels_sorted()):
            card = _LevelCard(l, l.order, self.sprites)
            card.selected.connect(self._select); card.play.connect(self._try_play)
            self.cards[l.key] = card
            grid.addWidget(card, i // COLS, i % COLS)
        right.addLayout(grid)
        right.addSpacing(6)
        right.addWidget(_title("SHIP CATALOG", 10, theme.TEXT_DIM, 3))
        self.catalog = _CatalogPanel(self.data, self.sprites)
        right.addWidget(self.catalog, 1)
        self.deploy_btn = CommandButton("DEPLOY", "Commit the fleet to this operation",
                                        variant="primary", w=None, h=56)
        self.deploy_btn.clicked.connect(self._deploy)
        right.addWidget(self.deploy_btn)
        body.addLayout(right, 6)
        lay.addLayout(body)

    def set_save(self, save):
        self.save = save
        self.diff_lbl.setText(f" SAVE {save.slot}  ·  {save.difficulty.upper()}")
        self.catalog.set_cleared(save.cleared_count())
        sel = None
        for k in self.ordered:
            if save.is_unlocked(k, self.ordered) and not save.completed(k):
                sel = k; break
        self.selected_key = sel or self.ordered[0]
        self.refresh()

    def _state(self, key):
        if not self.save.is_unlocked(key, self.ordered): return "locked"
        return "cleared" if self.save.completed(key) else "new"

    def refresh(self):
        for key, card in self.cards.items():
            card.set_state(self._state(key), self.save.info(key), key == self.selected_key)
        lvl = self.data.levels[self.selected_key]
        st = self._state(self.selected_key)
        self.dossier.set_level(lvl, st, self.save.info(self.selected_key))
        self.deploy_btn.set_enabled(st != "locked")
        self.deploy_btn.set_label("DEPLOY" if st != "locked" else "OPERATION LOCKED")

    def _select(self, key): self.selected_key = key; self.refresh()
    def _try_play(self, key):
        if self._state(key) != "locked": self.play.emit(key)
    def _deploy(self):
        if self.selected_key and self._state(self.selected_key) != "locked":
            self.play.emit(self.selected_key)
