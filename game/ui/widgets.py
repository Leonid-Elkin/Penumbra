"""
game.ui.widgets — small reusable in-battle widgets: the unit deploy button and
the minimap.
"""

from __future__ import annotations
from PyQt6.QtWidgets import (QWidget, QSizePolicy, QVBoxLayout, QHBoxLayout,
                             QGridLayout, QLabel, QPushButton, QLineEdit,
                             QScrollArea, QFrame)
from PyQt6.QtCore  import Qt, QRectF, QTimer, QEvent, pyqtSignal
from PyQt6.QtGui   import QPainter, QColor, QPen

from ..config import WORLD_W, FORT_D_W, BULK_SIZE
from ..       import progression
from ..settings import (SETTINGS, KEYBINDS, parse_keys, default_deploy_key)
from . import theme


# ─── Command button — an armed steel control, not a web CTA ────────────────────
class CommandButton(QWidget):
    """A chamfered steel-plate command switch: engraved label + sub-caption, an
    indicator lamp that lights on hover, and (for the primary variant) a hazard
    chevron underline that reads 'armed'. Used for the game's headline actions."""
    clicked = pyqtSignal()

    def __init__(self, label: str, sub: str = "", variant: str = "primary",
                 icon: str | None = None, w: int | None = 360, h: int = 70):
        super().__init__()
        self.label = label; self.sub = sub; self.variant = variant; self.icon = icon
        self._hover = False; self._t = 0.0; self._enabled = True
        if w is None:                    # expand to fill its layout cell
            self.setFixedHeight(h)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setFixedSize(w, h)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # a slow lamp pulse so the primary control feels 'live' while idle
        self._pt = QTimer(self); self._pt.timeout.connect(self._pulse); self._pt.start(60)

    def _pulse(self):
        self._t += 0.06
        if self.variant == "primary" or self._hover:
            self.update()

    def set_enabled(self, on: bool): self._enabled = on; self.update()
    def set_label(self, text: str): self.label = text; self.update()

    def enterEvent(self, _e): self._hover = True; self.update()
    def leaveEvent(self, _e): self._hover = False; self.update()
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self._enabled: self.clicked.emit()

    def paintEvent(self, _):
        import math
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        if not self._enabled:            # locked / unavailable control
            theme.plate(p, 0, 0, W, H, base=theme.BG, cut=11,
                        corners=(True, False, True, False))
            theme.engraved_label(p, self.label, 48, H / 2 - 13, W - 64,
                                 theme.head(19, 3.5), theme.TEXT_FAINT)
            theme.led(p, 26, H / 2, theme.TEXT_FAINT, r=3.6, on=False)
            return
        primary = self.variant == "primary"
        acc = theme.ACCENT if primary else theme.LINE_HI
        lit = self._hover
        base = theme.STEEL if lit else theme.PANEL
        theme.plate(p, 0, 0, W, H, base=base, cut=11, lit=lit,
                    corners=(True, False, True, False),
                    accent=acc if (primary or lit) else None, accent_side="left",
                    border=theme.ACCENT if lit else None)
        # indicator lamp, left gutter
        pulse = (math.sin(self._t) * 0.5 + 0.5) if primary else 1.0
        lamp = theme.ACCENT if primary else (theme.PHOSPHOR if lit else theme.TEXT_FAINT)
        theme.led(p, 26, H / 2, lamp, r=3.6, on=(primary or lit))
        if primary:  # a second dim pulse ring for life
            p.setOpacity(0.25 + 0.35 * pulse); theme.led(p, 26, H / 2, lamp, r=2.2, on=True); p.setOpacity(1.0)

        tx = 48
        col = theme.TEXT if (lit or primary) else theme.TEXT_DIM
        theme.engraved_label(p, self.label, tx, H / 2 - (21 if self.sub else 13),
                             W - tx - 16, theme.head(19, 3.5), col)
        if self.sub:
            p.setFont(theme.font(9)); p.setPen(QColor(theme.TEXT_DIM if not lit else theme.TEXT))
            p.drawText(int(tx + 1), int(H / 2 + 6), int(W - tx - 16), 15,
                       int(Qt.AlignmentFlag.AlignVCenter), self.sub)
        # right-edge chevron marker ">>" that brightens on hover
        p.setFont(theme.head(15, 0)); p.setPen(QColor(acc if lit else theme.TEXT_FAINT))
        p.drawText(int(W - 34), 0, 26, H, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter), "›")
        # primary: hazard-chevron underline strip = 'armed'
        if primary:
            theme.hazard_bar(p, QRectF(11, H - 6, W - 22, 5),
                             color=theme.ACCENT, alpha=200 if lit else 120)


# ─── Icon button — a small square steel toggle for screen chrome ───────────────
class IconButton(QWidget):
    """A small chamfered steel-plate control that paints a single vector icon and
    lights amber on hover. Used for chrome toggles (e.g. fullscreen) where a full
    labelled CommandButton would be too heavy."""
    clicked = pyqtSignal()

    def __init__(self, icon: str, tip: str = "", size: int = 40):
        super().__init__()
        self.icon = icon; self._hover = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tip: self.setToolTip(tip)

    def set_icon(self, icon: str):
        if icon != self.icon:
            self.icon = icon; self.update()

    def enterEvent(self, _e): self._hover = True; self.update()
    def leaveEvent(self, _e): self._hover = False; self.update()
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton: self.clicked.emit()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        lit = self._hover
        theme.plate(p, 0, 0, W, H, base=theme.STEEL if lit else theme.PANEL, cut=7, lit=lit,
                    corners=(True, False, True, False),
                    accent=theme.ACCENT if lit else None, accent_side="top",
                    border=theme.ACCENT if lit else None)
        col = theme.ACCENT if lit else theme.TEXT_DIM
        m = W * 0.28
        theme.draw_icon(p, self.icon, QRectF(m, m, W - 2*m, H - 2*m), QColor(col))


# The key-entry field: a dark readout in which the player types comma-separated
# key names ("Left, A"). Amber text on near-black, brightening on focus.
_KEY_FIELD_QSS = (
    "QLineEdit{{background:#0b1016;color:{acc};border:1px solid {ln};"
    "padding:3px 6px;}}"
    "QLineEdit:focus{{border:1px solid {acc};background:#0e141b;}}"
).format(acc=theme.ACCENT, ln=theme.LINE_HI)


# ─── Settings tab — the same panel on every screen ─────────────────────────────
class SettingsOverlay(QWidget):
    """The shared SETTINGS tab: a dark veil holding the game's option switches and
    the full keybind list. One instance is parented to each screen (the menus and
    the battle), tracks its parent's size itself, and writes straight to the global
    SETTINGS — so anything changed anywhere applies everywhere at once.

    Every control (and every deployable ship) can hold more than one key: type key
    names separated by commas and press Enter. Escape stays a fixed cancel/back key
    and can never be bound."""
    closed = pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)   # own Escape while open
        self._bind_fields: dict[str, QLineEdit] = {}          # fixed controls
        self._unit_fields: dict[str, tuple] = {}              # deploy:<unit> -> (field, index)
        self._ships_built = False

        # A scroll area so the (long) keybind list never overflows a short window;
        # CLOSE lives in a fixed footer beneath it so it's always reachable.
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{background:transparent;}"
                             "QScrollArea>QWidget>QWidget{background:transparent;}")
        outer.addWidget(scroll, 1)
        content = QWidget()
        v = QVBoxLayout(content); v.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        v.setSpacing(12); v.setContentsMargins(24, 22, 24, 14)
        scroll.setWidget(content)

        title = QLabel("SETTINGS"); title.setFont(theme.head(18, 5))
        title.setStyleSheet(f"color:{theme.TEXT};background:transparent;")
        v.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        sub = QLabel("APPLIES EVERYWHERE — SAVED AT ONCE"); sub.setFont(theme.head(9, 3))
        sub.setStyleSheet(f"color:{theme.ACCENT};background:transparent;")
        v.addWidget(sub, alignment=Qt.AlignmentFlag.AlignCenter)
        v.addSpacing(6)

        self.b_fx = QPushButton(); self.b_fx.setStyleSheet(theme.BTN); self.b_fx.setFixedWidth(360)
        self.b_fx.clicked.connect(self._toggle_fx)
        v.addWidget(self.b_fx, alignment=Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._caption("Skips explosions, gun smoke, missile exhaust and menu gunfire."))

        self.b_sil = QPushButton(); self.b_sil.setStyleSheet(theme.BTN); self.b_sil.setFixedWidth(360)
        self.b_sil.clicked.connect(self._toggle_sil)
        v.addWidget(self.b_sil, alignment=Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._caption("Moonlit rim around hulls on night levels, so ships read against the dark sea."))
        v.addSpacing(6)

        # ── KEYBINDS ─────────────────────────────────────────────────────────
        v.addWidget(self._section("KEYBINDS"))
        v.addWidget(self._caption("Type key names separated by commas (e.g. Left, A), then press Enter.  "
                                  "Escape is a fixed cancel/back key."))
        v.addWidget(self._key_grid([(a, lbl) for a, lbl, _ in KEYBINDS], self._bind_fields),
                    alignment=Qt.AlignmentFlag.AlignCenter)

        # ── SHIPS (populated on first open, once GameData is reachable) ────────
        v.addWidget(self._section("SHIPS"))
        v.addWidget(self._caption("The deploy key for every ship in your roster."))
        self._ships_note = self._caption("Roster loads with the game.")
        v.addWidget(self._ships_note)
        self._ships_holder = QVBoxLayout(); self._ships_holder.setContentsMargins(0, 0, 0, 0)
        self._ships_holder.setSpacing(0)
        hw = QWidget(); hw.setLayout(self._ships_holder); hw.setStyleSheet("background:transparent;")
        v.addWidget(hw, alignment=Qt.AlignmentFlag.AlignCenter)
        v.addSpacing(6)

        reset = QPushButton("RESET KEYS"); reset.setStyleSheet(theme.BTN_SMALL); reset.setFixedWidth(140)
        reset.clicked.connect(self._reset_keys)
        v.addWidget(reset, alignment=Qt.AlignmentFlag.AlignCenter)

        footer = QHBoxLayout(); footer.setContentsMargins(0, 8, 0, 10)
        c = QPushButton("CLOSE"); c.setStyleSheet(theme.BTN_SMALL); c.setFixedWidth(120)
        c.clicked.connect(self._close)
        footer.addStretch(); footer.addWidget(c); footer.addStretch()
        outer.addLayout(footer)

        parent.installEventFilter(self)     # follow the parent screen's size
        self._sync()
        self.hide()

    # ── small builders ───────────────────────────────────────────────────────
    def _section(self, text):
        lab = QLabel(text); lab.setFont(theme.head(12, 4))
        lab.setStyleSheet(f"color:{theme.TEXT};background:transparent;")
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lab

    def _caption(self, text):
        lab = QLabel(text); lab.setFont(theme.font(10))
        lab.setStyleSheet(f"color:{theme.TEXT_DIM};background:transparent;")
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lab

    def _key_field(self, action, width):
        le = QLineEdit(); le.setFixedWidth(width); le.setFont(theme.mono(10))
        le.setAlignment(Qt.AlignmentFlag.AlignCenter); le.setStyleSheet(_KEY_FIELD_QSS)
        le.editingFinished.connect(lambda a=action, w=le: self._commit(a, w))
        return le

    def _key_grid(self, rows, field_map):
        """A two-column grid of (label, key field) for `rows` = [(action, label)];
        each field is registered into `field_map`."""
        grid = QGridLayout(); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(6)
        for i, (action, label) in enumerate(rows):
            r, c = i // 2, (i % 2) * 2
            lab = QLabel(label + ":"); lab.setFont(theme.font(11))
            lab.setStyleSheet(f"color:{theme.TEXT_DIM};background:transparent;")
            lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            le = self._key_field(action, 132)
            field_map[action] = le
            grid.addWidget(lab, r, c); grid.addWidget(le, r, c + 1)
        grid.setColumnStretch(0, 1); grid.setColumnStretch(2, 1)
        gw = QWidget(); gw.setLayout(grid); gw.setStyleSheet("background:transparent;")
        return gw

    def _build_ships(self):
        """Fill the SHIPS grid from the live roster — done once, on first open, when
        self.window() has resolved to the AppWindow that carries GameData."""
        if self._ships_built:
            return
        data = getattr(self.window(), "data", None)
        if data is None:
            return
        roster = [k for k in getattr(data, "deploy_order", []) if k in progression.UNLOCK]
        if not roster:
            return
        grid = QGridLayout(); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(6)
        for i, key in enumerate(roster):
            action = f"deploy:{key}"
            name = getattr(data.vehicles.get(key), "name", key)
            r, c = i // 2, (i % 2) * 2
            lab = QLabel(name + ":"); lab.setFont(theme.font(11))
            lab.setStyleSheet(f"color:{theme.TEXT_DIM};background:transparent;")
            lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            le = self._key_field(action, 118)
            self._unit_fields[action] = (le, i)   # keep the roster index for defaults
            grid.addWidget(lab, r, c); grid.addWidget(le, r, c + 1)
        grid.setColumnStretch(0, 1); grid.setColumnStretch(2, 1)
        gw = QWidget(); gw.setLayout(grid); gw.setStyleSheet("background:transparent;")
        self._ships_holder.addWidget(gw)
        self._ships_note.hide()
        self._ships_built = True
        self._sync_fields()

    # ── lifecycle ──────────────────────────────────────────────────────────────
    def eventFilter(self, obj, ev):
        if obj is self.parent() and ev.type() == QEvent.Type.Resize:
            self.setGeometry(self.parent().rect())
        return False

    def open(self):
        self.setGeometry(self.parent().rect())
        self._build_ships()
        self._sync(); self.show(); self.raise_(); self.setFocus()

    def _toggle_fx(self):
        SETTINGS.no_effects = not SETTINGS.no_effects
        SETTINGS.save(); self._sync()

    def _toggle_sil(self):
        SETTINGS.night_silhouette = not SETTINGS.night_silhouette
        SETTINGS.save(); self._sync()

    # ── keybind editing ──────────────────────────────────────────────────────
    def _commit(self, action, le):
        """Parse the field's comma-separated text and store it, then re-sync every
        field (a key taken from another control is stripped there)."""
        SETTINGS.set_keys(action, parse_keys(le.text()))
        w = self.window()
        if hasattr(w, "refresh_keybinds"):
            w.refresh_keybinds()
        self._sync_fields()

    def _reset_keys(self):
        SETTINGS.reset_keys()
        w = self.window()
        if hasattr(w, "refresh_keybinds"):
            w.refresh_keybinds()
        self._sync_fields()

    def _sync(self):
        self.b_fx.setText("NO EFFECTS:  ON" if SETTINGS.no_effects else "NO EFFECTS:  OFF")
        self.b_sil.setText("NIGHT SILHOUETTES:  ON" if SETTINGS.night_silhouette
                           else "NIGHT SILHOUETTES:  OFF")
        self._sync_fields()

    def _sync_fields(self):
        for action, le in self._bind_fields.items():
            le.setText(SETTINGS.keys_display_for(action, empty=""))
        for action, (le, idx) in self._unit_fields.items():
            le.setText(SETTINGS.keys_display_for(action, empty="")
                       if SETTINGS.has_binding(action) else default_deploy_key(idx))

    def _close(self):
        self.hide(); self.closed.emit()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape: self._close()
        else: super().keyPressEvent(ev)

    def paintEvent(self, _):
        QPainter(self).fillRect(self.rect(), QColor(6, 9, 12, 235))


# ─── Unit info helpers (shared by the buy-menu box and field hover) ───────────
def role_label(sdef) -> str:
    ut = getattr(sdef, 'unit_type', 'ship')
    if 'base' in getattr(sdef, 'tags', []): return "Fortress"
    if ut == 'plane':     return "Aircraft"
    if ut == 'submarine': return "Submarine"
    if ut == 'structure': return "Fortress"
    if ut == 'turret':
        if getattr(sdef, 'income', 0): return "Income platform"
        return "Coastal turret"
    if getattr(sdef, 'is_boss', False): return "BOSS"
    return "Surface ship"

def _targets(attack) -> str:
    t = []
    if getattr(attack, 'can_hit_surface', False): t.append("ships")
    if getattr(attack, 'can_hit_plane', False):   t.append("air")
    if getattr(attack, 'can_hit_sub', False):     t.append("subs")
    return "/".join(t) if t else "—"

def weapon_lines(sdef) -> list[str]:
    out = []
    for a in getattr(sdef, 'attacks', []):
        out.append(f"{a.name}: {a.damage} dmg · rng {int(a.combat_range)} · vs {_targets(a)}")
    return out


class UnitButton(QWidget):
    """A clickable card showing a unit's sprite, name, cost, hotkey and the
    number currently queued."""
    deploy = pyqtSignal(str)
    dequeue = pyqtSignal(str)           # right-click → cancel one queued hull (full refund)
    W = 72; H = 66

    hovered = pyqtSignal(object)        # emits sdef on enter, None on leave

    def __init__(self, sdef, hotkey: str = "", hide_cost: bool = False):
        super().__init__()
        self.sdef = sdef
        self.hotkey = hotkey
        self.hide_cost = hide_cost
        self.sprite = None
        self._scaled = None            # cached display-scaled sprite (see paintEvent)
        self.queued = 0; self.can_afford = False
        self.rush_queued = 0           # hulls bought while surging → drawn as a RED tally
        self.locked = False; self.stage = 0
        self.cd_rem = 0.0; self.cd_total = 0.0
        self.held = False              # deployed & unique (e.g. Bastion) — held unavailable
        self.cost = getattr(sdef, 'cost', 0)   # price shown (raised while surging)
        self.surge = False             # Ctrl-held surge → price elevated, drawn hot
        self.bulk = False              # Alt-held bulk → batch of BULK_SIZE, total shown w/ ×N tag
                                       # (both held → elevated batch, drawn hot amber)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, _e): self.hovered.emit(self.sdef)
    def leaveEvent(self, _e): self.hovered.emit(None)

    def set_state(self, can_afford, queued=0, sprite=None, locked=False, stage=0,
                  cd_rem=0.0, cd_total=0.0, held=False, cost=None, surge=False,
                  bulk=False, rush_queued=0):
        # Only repaint when something visible actually changed — this is called for
        # every button on every HUD refresh, so skipping no-op updates keeps idle
        # buttons from repainting. (A cooling-down button's cd_rem changes each tick,
        # so its wipe still animates.)
        if cost is None:
            cost = getattr(self.sdef, 'cost', 0)
        changed = (can_afford != self.can_afford or queued != self.queued
                   or rush_queued != self.rush_queued
                   or locked != self.locked or stage != self.stage
                   or cd_rem != self.cd_rem or cd_total != self.cd_total
                   or held != self.held or cost != self.cost or surge != self.surge
                   or bulk != self.bulk)
        self.can_afford = can_afford; self.queued = queued
        self.rush_queued = rush_queued
        self.locked = locked; self.stage = stage
        self.cd_rem = cd_rem; self.cd_total = cd_total
        self.held = held
        self.cost = cost; self.surge = surge; self.bulk = bulk
        if sprite is not None and sprite is not self.sprite:
            self.sprite = sprite; self._scaled = None; changed = True
        if changed:
            self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        ready = self.can_afford and not self.locked and not self.held
        reloading = (self.cd_rem > 0 and self.cd_total > 0) or self.held
        # Domain-tinted steel plate; a phosphor left edge means 'ready to deploy'.
        ut = getattr(self.sdef, 'unit_type', 'ship')
        base = {"turret": "#152430", "submarine": "#132430", "plane": "#12211f",
                "structure": "#1d2117"}.get(ut, "#182833") if ready else theme.BG
        edge = theme.PHOSPHOR if (ready and not reloading) else None
        path = theme.plate(p, 0, 0, W, H, base=base, cut=6,
                           corners=(True, False, True, False), brushed=ready,
                           accent=edge, accent_side="left",
                           border=theme.LINE_HI if ready else theme.LINE)
        p.setClipPath(path)

        cost_h = 0 if self.hide_cost else 11
        cap_h = 13 + cost_h
        spr_bottom = H - cap_h - 2
        if self.sprite and not self.sprite.isNull():
            # The button size is fixed, so the smooth-scaled sprite is identical
            # every paint — scale it once and cache it (this rescale used to run on
            # every one of ~25 buttons, 60×/s).
            avail_w = W - 10; avail_h = max(1, spr_bottom - 4)
            if self._scaled is None:
                self._scaled = self.sprite.scaled(
                    avail_w, avail_h, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
            sc = self._scaled
            p.setOpacity(0.3 if self.locked else 1.0)
            p.drawPixmap((W - sc.width()) // 2, 4 + (avail_h - sc.height()) // 2, sc)
            p.setOpacity(1.0)

        # caption strip → the name is always legible over any sprite
        p.fillRect(3, H - cap_h - 1, W - 6, cap_h, QColor(0, 0, 0, 190))
        name_col = theme.TEXT if not self.locked else theme.TEXT_FAINT
        p.setPen(QColor(name_col)); p.setFont(theme.head(8, 0.3))
        p.drawText(2, H - cap_h - 1, W - 4, 13, int(Qt.AlignmentFlag.AlignCenter),
                   self.sdef.name.upper())

        if self.locked:
            # A locked-until-Level-N sign: padlock, a LOCKED tag, and the campaign
            # level this hull unlocks on (stage = cleared levels needed, so it is
            # first fieldable one level later).
            lvl = self.stage + 1
            p.fillRect(3, 3, W - 6, H - 6, QColor(0, 0, 0, 120))
            theme.draw_icon(p, "lock", QRectF(W/2 - 9, 7, 18, 18), QColor(theme.TEXT_DIM))
            p.setPen(QColor(theme.TEXT_FAINT)); p.setFont(theme.head(7, 1))
            p.drawText(2, 26, W - 4, 11, int(Qt.AlignmentFlag.AlignCenter), "LOCKED")
            p.setPen(QColor(theme.ACCENT)); p.setFont(theme.mono(8, True))
            p.drawText(2, H - cost_h - 1, W - 4, cost_h or 11, int(Qt.AlignmentFlag.AlignCenter),
                       f"LVL {lvl}")
            return
        if not self.hide_cost:
            # The price reflects the live Ctrl/Alt modifiers (self.cost is already the
            # per-hull price the sim will charge). A bulk press buys BULK_SIZE hulls,
            # so for bulk — and for surge+bulk — we show the BATCH TOTAL with an "(×N)"
            # tag; surge-only stays a single elevated price. Colour reads the mode:
            #   surge+bulk → hot amber (elevated batch)   surge → red (elevated)
            #   bulk       → cool phosphor (discount)      plain → amber
            if not self.can_afford:            col = theme.TEXT_FAINT
            elif self.surge and self.bulk:     col = theme.AMBER_HI
            elif self.surge:                   col = theme.DANGER_HI
            elif self.bulk:                    col = theme.PHOSPHOR
            else:                              col = theme.ACCENT
            if self.bulk:
                label = f"{self.cost * BULK_SIZE} (×{BULK_SIZE})"   # batch total
            else:
                label = f"{self.cost}"
            p.setPen(QColor(col)); p.setFont(theme.mono(8, True))
            p.drawText(2, H - cost_h - 1, W - 4, cost_h, int(Qt.AlignmentFlag.AlignCenter),
                       label)
        if self.hotkey:
            p.setBrush(QColor(0, 0, 0, 190)); p.setPen(QPen(QColor(theme.LINE_HI), 1))
            p.drawRect(3, 3, 14, 14)
            p.setPen(QColor(theme.ACCENT)); p.setFont(theme.head(8, 0))
            p.drawText(3, 3, 14, 14, int(Qt.AlignmentFlag.AlignCenter), self.hotkey)
        if self.queued > 0:
            # queued count as a phosphor tally badge with a lamp
            p.setBrush(QColor(theme.PHOSPHOR)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(W - 17, 3, 14, 14)
            p.setPen(QColor("#08120c")); p.setFont(theme.head(9, 0))
            p.drawText(W - 17, 3, 14, 14, int(Qt.AlignmentFlag.AlignCenter), str(self.queued))
        if self.rush_queued > 0:
            # RED rush tally, seated just left of the standard queue badge: these
            # hulls cost the surge premium and reload fast (see unit_rush_queue).
            p.setBrush(QColor(theme.DANGER)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(W - 33, 3, 14, 14)
            p.setPen(QColor("#120806")); p.setFont(theme.head(9, 0))
            p.drawText(W - 33, 3, 14, 14, int(Qt.AlignmentFlag.AlignCenter),
                       str(self.rush_queued))
        # Production cooldown: a dark 'reload' wipe that recedes from the top, an
        # amber fill line marking progress, and the remaining seconds in mono.
        if reloading:
            # `held` (a unique unit already deployed, e.g. the Bastion) reads as a
            # full wipe with no ticking clock — it's not counting down, it's occupied.
            frac = 1.0 if self.held else max(0.0, min(1.0, self.cd_rem / self.cd_total))
            p.setBrush(QColor(6, 9, 12, 200)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(2, 2, W - 4, int((H - 4) * frac))
            p.setBrush(QColor(theme.ACCENT))
            p.drawRect(2, int(2 + (H - 4) * frac) - 1, W - 4, 2)
            if self.held:
                p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.head(8, 0.3))
                p.drawText(0, 0, W, H, int(Qt.AlignmentFlag.AlignCenter), "DEPLOYED")
            else:
                secs = self.cd_rem
                label = f"{secs:.0f}" if secs >= 1 else f"{secs:.1f}"
                p.setPen(QColor(theme.ACCENT)); p.setFont(theme.mono(13, True))
                p.drawText(0, 0, W, H, int(Qt.AlignmentFlag.AlignCenter), label)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and not self.locked:
            self.deploy.emit(self.sdef.key)
        elif ev.button() == Qt.MouseButton.RightButton and (self.queued > 0
                                                             or self.rush_queued > 0):
            self.dequeue.emit(self.sdef.key)   # cancel one queued hull, full refund


class Minimap(QWidget):
    """Strategic overview strip; click/drag to jump the camera."""

    def __init__(self, canvas):
        super().__init__(); self.canvas = canvas
        self.setFixedHeight(26); self.setMinimumWidth(120)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height(); sc = W / WORLD_W
        # tactical scope: a dark sea washed in a cold phosphor tint, steel bezel
        p.fillRect(0, 0, W, H, QColor("#08161d"))
        p.fillRect(0, H // 2, W, H - H // 2, QColor("#0a2130"))
        # friendly shore (left) vs hostile shore (right) as signal blocks
        p.fillRect(0, 0, round(FORT_D_W * sc), H, QColor(20, 60, 44))
        p.fillRect(round((WORLD_W - FORT_D_W) * sc), 0, round(FORT_D_W * sc), H, QColor(70, 24, 22))
        theme.scanlines(p, QRectF(0, 0, W, H), gap=3, alpha=30)
        for s in self.canvas.ships:
            if getattr(s, 'is_base', False) or getattr(s, 'is_rig', False):
                continue
            # friendly = phosphor blip, hostile = danger blip (bosses read as hostile)
            col = QColor(theme.PHOSPHOR) if s.team == "player" else QColor(theme.DANGER)
            p.fillRect(round(s.x * sc) - 1, round(H * .22), 2, round(H * .62), col)
        cam_x = self.canvas.cam_x; vw = self.canvas.width() * sc
        p.setPen(QPen(QColor(theme.ACCENT), 1.4)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(round(cam_x * sc), 1, round(vw), H - 3)
        # steel bezel
        p.setPen(QPen(QColor(theme.LINE_HI), 1)); p.drawRect(0, 0, W - 1, H - 1)

    def mousePressEvent(self, ev): self._jump(ev.position().x())
    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.MouseButton.LeftButton: self._jump(ev.position().x())

    def _jump(self, mx):
        W = self.canvas.width()
        self.canvas.cam_x = max(0.0, min(WORLD_W - W, mx / max(1, self.width()) * WORLD_W - W * .5))


# ─── Resource gauge — the pulse of the match, the HUD's focal readout ─────────
class ResourceGauge(QWidget):
    """A command-console resource gauge: the current bank in big amber mono, a
    segmented tank showing fill against capacity, and income in live phosphor."""
    def __init__(self):
        super().__init__()
        self.res = 0; self.cap = 200; self.income = 0; self.infinite = False
        self.hide_income = False
        self.setFixedHeight(50); self.setMinimumWidth(240)

    def set_values(self, res, cap, income, infinite=False, hide_income=False):
        self.res = res; self.cap = max(1, cap); self.income = income
        self.infinite = infinite; self.hide_income = hide_income; self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        theme.plate(p, 0, 0, W, H, base=theme.PANEL, cut=8,
                    corners=(True, False, True, False), accent=theme.ACCENT, accent_side="left")
        # label
        p.setPen(QColor(theme.TEXT_FAINT)); p.setFont(theme.mono(8))
        p.drawText(12, 5, 120, 11, int(Qt.AlignmentFlag.AlignLeft), "RESOURCES")
        # big bank figure
        cur = "∞" if self.infinite else f"{int(self.res)}"
        p.setPen(QColor(theme.ACCENT)); p.setFont(theme.mono(19, True))
        p.drawText(11, 15, 150, 24, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), cur)
        fm_w = p.fontMetrics().horizontalAdvance(cur)
        p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.mono(10))
        p.drawText(16 + fm_w, 17, 90, 22, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   f"/ {self.cap}")
        # income, right-aligned, phosphor (live gain) — hidden in the sandbox,
        # where income is maxed/irrelevant and the readout would just be noise.
        if not self.hide_income:
            p.setPen(QColor(theme.PHOSPHOR)); p.setFont(theme.mono(12, True))
            p.drawText(W - 92, 8, 82, 18, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                       f"+{self.income}/s")
            theme.led(p, W - 14, 15, theme.PHOSPHOR, r=2.4, on=True)
        # segmented tank bar — the leading segment lights up gradually as it
        # fills rather than popping in, so the bar reads as a continuous fill.
        bx, by, bw, bh = 12, H - 12, W - 24, 6
        p.setBrush(QColor(theme.BG)); p.setPen(QPen(QColor(theme.LINE), 1)); p.drawRect(bx, by, bw, bh)
        frac = 1.0 if self.infinite else max(0.0, min(1.0, self.res / self.cap))
        segs = 24; sw = (bw - 2) / segs
        filled = frac * segs
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(segs):
            seg = filled - i                 # 0..1: how much of this segment is lit
            if seg <= 0: break
            col = QColor(theme.ACCENT if i / segs < 0.85 else theme.DANGER)
            if seg < 1.0:                     # leading edge glows in instead of snapping on
                col.setAlphaF(max(0.0, min(1.0, seg)))
            p.setBrush(col)
            p.drawRect(int(bx + 1 + i * sw), by + 1, max(1, int(sw - 1)), bh - 2)


# ─── Info box (to the right of the ship selection) ────────────────────────────
class InfoBox(QWidget):
    """A fixed panel that shows details of the unit currently hovered — in the
    buy menu or on the battlefield."""

    def __init__(self, sprites=None):
        super().__init__()
        self.sprites = sprites
        self.sdef = None
        self.live_hp = None          # (hp, max_hp) when hovering a field unit
        self.locked = False; self.stage = 0
        self.sell_refund = None      # +refund shown when hovering a sellable turret
        self.queue_refund = None     # +refund shown when hovering a button with a queue
        self.setFixedWidth(212)

    def show_unit(self, sdef, live_hp=None, locked=False, stage=0, sell_refund=None,
                  queue_refund=None):
        self.sdef = sdef; self.live_hp = live_hp
        self.locked = locked; self.stage = stage
        self.sell_refund = sell_refund
        self.queue_refund = queue_refund
        self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        path = theme.plate(p, 0, 0, W, H, base=theme.PANEL_HI, cut=9,
                           corners=(True, False, True, False),
                           accent=theme.ACCENT, accent_side="top")
        p.setClipPath(path)                  # never bleed text into neighbouring panels

        # header strip label
        p.setPen(QColor(theme.TEXT_FAINT)); p.setFont(theme.mono(8))
        p.drawText(10, 6, W - 20, 12, int(Qt.AlignmentFlag.AlignLeft), "UNIT READOUT")

        if self.sdef is None:
            theme.draw_icon(p, "anchor", QRectF(W/2 - 16, H/2 - 26, 32, 32), QColor(theme.TEXT_FAINT))
            p.setPen(QColor(theme.TEXT_FAINT)); p.setFont(theme.font(10))
            p.drawText(0, int(H/2) + 12, W, 30, int(Qt.AlignmentFlag.AlignHCenter),
                       "Hover a unit for details")
            return

        sd = self.sdef
        spr = self.sprites.hi("player", sd.key) if self.sprites else None
        if spr and not spr.isNull():
            tw = 68; th = max(1, round(tw * sd.display_h / max(1, sd.display_w)))
            th = min(th, 38)
            sc = spr.scaled(tw, th, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(W - sc.width() - 8, 20, sc)

        x = 10; y = 20
        theme.engraved_label(p, sd.name.upper(), x, y, W - 82, theme.head(14, 1), theme.TEXT)
        y += 20
        p.setPen(QColor(theme.ACCENT)); p.setFont(theme.head(9, 1))
        p.drawText(x, y, W - 82, 14, int(Qt.AlignmentFlag.AlignLeft), role_label(sd).upper())
        y += 20

        # stat readout in the plotting-room mono font
        p.setFont(theme.mono(9)); p.setPen(QColor(theme.TEXT_DIM))
        if self.live_hp is not None:
            hp, mhp = self.live_hp
            frac = max(0.0, min(1.0, hp / max(1, mhp)))
            p.drawText(x, y, W - 16, 13, int(Qt.AlignmentFlag.AlignLeft),
                       f"HP {int(hp)}/{int(mhp)}"); y += 15
            # a live hull-integrity bar
            p.setBrush(QColor(theme.BG)); p.setPen(QPen(QColor(theme.LINE), 1))
            p.drawRect(x, y, W - 20, 5)
            col = theme.PHOSPHOR if frac > .5 else (theme.ACCENT if frac > .25 else theme.DANGER)
            p.setBrush(QColor(col)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(x + 1, y + 1, int((W - 22) * frac), 3); y += 14
        else:
            stat = f"COST {sd.cost}   HP {sd.hp}"
            if getattr(sd, 'speed', 0): stat += f"   SPD {int(sd.speed)}"
            p.drawText(x, y, W - 16, 13, int(Qt.AlignmentFlag.AlignLeft), stat); y += 17

        if self.sell_refund is not None:
            p.setPen(QColor(theme.PHOSPHOR)); p.setFont(theme.mono(8))
            p.drawText(x, y, W - 16, 12, int(Qt.AlignmentFlag.AlignLeft),
                       f"CLICK TO SELL  +{self.sell_refund}"); y += 14

        if self.queue_refund is not None:
            p.setPen(QColor(theme.PHOSPHOR)); p.setFont(theme.mono(8))
            p.drawText(x, y, W - 16, 12, int(Qt.AlignmentFlag.AlignLeft),
                       f"RIGHT-CLICK TO CANCEL  +{self.queue_refund}"); y += 14

        if self.locked:
            lvl = self.stage + 1
            p.setPen(QColor(theme.ACCENT)); p.setFont(theme.head(10, 1))
            p.drawText(x, y, W - 16, 14, int(Qt.AlignmentFlag.AlignLeft), "CLASSIFIED"); y += 16
            p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.font(9))
            p.drawText(QRectF(x, y, W - 18, H - y - 6),
                       int(Qt.TextFlag.TextWordWrap) | int(Qt.AlignmentFlag.AlignTop),
                       f"Locked — unlocks at Level {lvl}. Clear operations to declassify.")
            return

        p.setPen(QColor(theme.ACCENT)); p.setFont(theme.head(9, 1))
        p.drawText(x, y, W - 16, 13, int(Qt.AlignmentFlag.AlignLeft), "ARMAMENT")
        p.setPen(QPen(QColor(theme.LINE), 1)); p.drawLine(x + 74, y + 7, W - 10, y + 7); y += 15
        p.setPen(QColor(theme.TEXT)); p.setFont(theme.mono(8))
        lines = weapon_lines(sd) or ["—"]
        for ln in lines[:3]:
            p.drawText(x, y, W - 16, 12, int(Qt.AlignmentFlag.AlignLeft), ln); y += 12
        y += 4
        p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.font(8))
        p.drawText(QRectF(x, y, W - 18, H - y - 6),
                   int(Qt.TextFlag.TextWordWrap) | int(Qt.AlignmentFlag.AlignTop),
                   sd.description)


# ─── Upgrade card (styled like a unit purchase) ───────────────────────────────
class UpgradeCard(QWidget):
    clicked  = pyqtSignal(str)
    hovered  = pyqtSignal(object)
    W = 100; H = 58

    def __init__(self, key: str, name: str, icon: str):
        super().__init__()
        self.key = key; self.name = name; self.icon = icon
        self.level = 0; self.cost = 0; self.affordable = False; self.maxed = False
        self.pending = None        # (remaining, total) while researching, else None
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_state(self, level, cost, affordable, maxed, pending=None):
        # Skip the repaint when nothing changed (called for every card each HUD
        # refresh). While researching, `pending` counts down so the bar animates.
        changed = (level != self.level or cost != self.cost
                   or affordable != self.affordable or maxed != self.maxed
                   or pending != self.pending)
        self.level = level; self.cost = cost
        self.affordable = affordable; self.maxed = maxed
        self.pending = pending
        if changed:
            self.update()

    def enterEvent(self, _e): self.hovered.emit(self)
    def leaveEvent(self, _e): self.hovered.emit(None)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.W, self.H
        ready = self.affordable and not self.maxed
        researching = bool(self.pending)
        base = theme.STEEL if ready else theme.PANEL
        edge = theme.PHOSPHOR if self.maxed else (theme.ACCENT if ready else None)
        theme.plate(p, 0, 0, W, H, base=base, cut=7, lit=ready,
                    corners=(True, False, True, False),
                    accent=edge, accent_side="left",
                    border=theme.LINE_HI if ready else None)
        icol = theme.ACCENT if ready else (theme.PHOSPHOR if self.maxed else theme.TEXT_FAINT)
        theme.draw_icon(p, self.icon, QRectF(9, 8, 19, 19), QColor(icol))
        p.setPen(QColor(theme.TEXT if (ready or self.maxed) else theme.TEXT_DIM))
        p.setFont(theme.head(10, 1))
        p.drawText(34, 7, W - 38, 16, int(Qt.AlignmentFlag.AlignVCenter), self.name.upper())
        # pip ladder showing the upgrade level (0..5) — instrument feel
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(5):
            on = i < self.level
            p.setBrush(QColor(theme.ACCENT if on else theme.LINE))
            p.drawRect(9 + i * 9, H - 24, 7, 4)
        if self.maxed:
            p.setPen(QColor(theme.PHOSPHOR)); p.setFont(theme.mono(10, True))
            p.drawText(9, H - 17, W - 14, 13, int(Qt.AlignmentFlag.AlignRight), "MAX")
        elif not researching:
            p.setPen(QColor(theme.GOLD) if self.affordable else QColor(theme.TEXT_FAINT))
            p.setFont(theme.mono(10, True))
            p.drawText(9, H - 17, W - 14, 13, int(Qt.AlignmentFlag.AlignRight), f"{self.cost}")
        # Research in progress: a bottom progress bar + remaining seconds.
        if researching:
            rem, total = self.pending
            done = 0.0 if total <= 0 else max(0.0, min(1.0, 1.0 - rem / total))
            by = H - 5
            p.setBrush(QColor(0, 0, 0, 150)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(4, by, W - 8, 3)
            p.setBrush(QColor(theme.ACCENT)); p.drawRect(4, by, int((W - 8) * done), 3)
            p.setPen(QColor(theme.ACCENT)); p.setFont(theme.mono(10, True))
            p.drawText(9, H - 17, W - 14, 13, int(Qt.AlignmentFlag.AlignRight), f"{rem:.0f}s")

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
