"""
game.ui.hud – the bottom control panel during a battle.

Units sit in tabs (Water / Air / Underwater / Upgrades) with hotkeys and a build
queue (max 10). An info box to the right of the selection shows details for the
unit currently hovered – in the menu or on the battlefield.
"""

from __future__ import annotations
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QGridLayout,
                             QLabel, QPushButton, QComboBox,
                             QScrollArea, QSizePolicy, QScroller, QScrollerProperties)
from PyQt6.QtCore  import Qt, QRectF, pyqtSignal
from PyQt6.QtGui   import QShortcut, QKeySequence, QPainter, QColor, QPen

from ..config import UPGRADE_COSTS, BULK_SIZE, deploy_unit_cost
from ..battle import SELL_FRACTION
from ..       import progression
from ..settings import (SETTINGS, key_display, default_deploy_key, parse_keys)
from . import theme
from .widgets import (UnitButton, UpgradeCard, Minimap, InfoBox, ResourceGauge,
                      IconButton)

UPG = [("resource", "Income", "income"), ("fleet", "Fleet", "fleet"),
       ("storage", "Storage", "storage"), ("warehouse", "Factory", "factory"),
       ("health", "Armor", "armor"), ("salvage", "Salvage", "salvage")]


def _category(sdef) -> str:
    ut = getattr(sdef, 'unit_type', 'ship')
    if ut == 'plane':      return "Air"
    if ut == 'submarine':  return "Underwater"
    if ut == 'turret':
        return "Underwater" if getattr(sdef, 'turret_slot', 'surface') == 'underwater' else "Water"
    return "Water"


class _UnitScroll(QScrollArea):
    """The units-bar viewport. Its content only overflows sideways, so the mouse
    wheel (which Qt would otherwise send to the disabled vertical bar) is remapped
    to horizontal panning, and left-drag anywhere pans the row via QScroller –
    which buffers the initial press so a tap still deploys but a drag doesn't."""

    def __init__(self):
        super().__init__()
        QScroller.grabGesture(self.viewport(),
                              QScroller.ScrollerGestureType.LeftMouseButtonGesture)
        sc = QScroller.scroller(self.viewport())
        props = sc.scrollerProperties()
        off = QScrollerProperties.OvershootPolicy.OvershootAlwaysOff
        props.setScrollMetric(
            QScrollerProperties.ScrollMetric.HorizontalOvershootPolicy, off)
        props.setScrollMetric(
            QScrollerProperties.ScrollMetric.VerticalOvershootPolicy, off)
        sc.setScrollerProperties(props)

    def wheelEvent(self, ev):
        bar = self.horizontalScrollBar()
        delta = ev.angleDelta().y() or ev.angleDelta().x()
        if delta:
            bar.setValue(bar.value() - delta)   # wheel up/left -> earlier units
            ev.accept()
        else:
            super().wheelEvent(ev)


class BattleHud(QWidget):
    request_menu       = pyqtSignal()
    request_settings   = pyqtSignal()    # open the settings tab over the battle
    request_fullscreen = pyqtSignal()    # toggle full screen (mirrors F11)

    def __init__(self, canvas, data):
        super().__init__()
        self.canvas = canvas; self.data = data
        self.sandbox = bool(getattr(canvas, 'sandbox', False))
        # Which faction THIS console commands: the player's, except on the LAN
        # client (P2), who commands the enemy. All reads/actions below route
        # through canvas.faction(self.my) / the team-aware cmd_* verbs so the same
        # HUD drives either side.
        self.my = getattr(canvas, 'my_team', 'player')
        self.my_unlocked = (canvas.enemy_unlocked if self.my == 'enemy'
                            else canvas.unlocked)
        self.setMinimumHeight(190)
        bl = QHBoxLayout(self); bl.setContentsMargins(8, 9, 8, 6); bl.setSpacing(6)

        # Player roster = the campaign vehicles + base turrets (excludes enemy-only
        # units like torpedo_boat that the registry may append). In the sandbox the
        # bosses are appended too, so they can be spawned like any other unit.
        self.roster = [k for k in self.data.deploy_order if k in progression.UNLOCK]
        self.boss_roster = sorted(self.data.bosses) if self.sandbox else []
        # Per-ship deploy hotkeys are rebindable (SETTINGS "deploy:<unit>"); a ship
        # with no custom binding falls back to its deploy-order key. self.hotkeys is
        # the glyph shown on each UnitButton; the live QShortcuts are built below.
        self.hotkeys = {k: self._deploy_glyph(i, k) for i, k in enumerate(self.roster)}
        self._deploy_shortcuts = []
        self.unit_btns: dict[str, UnitButton] = {}
        self.info = InfoBox(self.canvas.sprites)

        # ── Units & upgrades shown side by side (no tabs) ─────────────────────
        # (Surge Rush / Bulk deploy have no buttons – surge engages while CTRL is
        # held and bulk while ALT is held; they compose. See _refresh, deploy_unit_cost,
        # and BattleCanvas.surge_active / bulk_active.)
        bl.addWidget(self._titled("UNITS", self._unit_tab()), 1)
        bl.addWidget(self._sep())
        bl.addWidget(self._titled("UPGRADES", self._upgrades_tab()))
        bl.addWidget(self._sep())

        bl.addWidget(self.info)

        # ── Right column ──────────────────────────────────────────────────────
        right = QVBoxLayout(); right.setSpacing(4); right.setContentsMargins(2, 0, 2, 0)
        right.addLayout(self._resources_row())
        self.minimap = Minimap(self.canvas); right.addWidget(self.minimap)
        # Sandbox: the side-selector gets its own full-width row above the controls,
        # so choosing ALLY / ENEMY never crowds the pause / menu / restart buttons.
        if self.sandbox:
            right.addLayout(self._spawn_row())
        right.addLayout(self._controls_row())
        rw = QWidget(); rw.setFixedWidth(286); rw.setLayout(right)
        bl.addWidget(rw)

        # ── Hotkeys ───────────────────────────────────────────────────────────
        self._install_deploy_hotkeys()

        self.canvas.sig_ui.connect(self._refresh)
        self.canvas.sig_ui.connect(self.minimap.update)
        self.canvas.sig_hover.connect(self._on_field_hover)
        self._refresh()

    # ── Deploy hotkeys (per-ship, rebindable) ────────────────────────────────────
    def _deploy_codes(self, i, key):
        """The Qt key codes that deploy the roster ship `key` (at index `i`): its
        custom binding if set, otherwise its default deploy-order hotkey."""
        action = f"deploy:{key}"
        if SETTINGS.has_binding(action):
            return SETTINGS.keys_for(action)
        ch = default_deploy_key(i)
        return parse_keys(ch) if ch else []

    def _deploy_glyph(self, i, key):
        """Short label drawn on the unit button – the first bound key, or ''."""
        codes = self._deploy_codes(i, key)
        return key_display(codes[0]) if codes else ""

    def _install_deploy_hotkeys(self):
        """(Re)build the deploy QShortcuts from the current bindings."""
        for sc in self._deploy_shortcuts:
            sc.setParent(None); sc.deleteLater()
        self._deploy_shortcuts = []
        for i, key in enumerate(self.roster):
            for code in self._deploy_codes(i, key):
                sc = QShortcut(QKeySequence(code), self)
                sc.activated.connect(lambda k=key: self.canvas.cmd_deploy(k))
                self._deploy_shortcuts.append(sc)

    def refresh_keybinds(self):
        """Rebuild the deploy hotkeys and their button glyphs after a rebind, so a
        key changed in the SETTINGS panel takes effect without restarting."""
        self._install_deploy_hotkeys()
        for i, key in enumerate(self.roster):
            btn = self.unit_btns.get(key)
            if btn is not None:
                btn.hotkey = self._deploy_glyph(i, key)
                btn.update()

    # ── The console shell: brushed steel with a live top rail ────────────────────
    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), QColor(theme.PANEL))
        p.setOpacity(0.5)
        p.drawTiledPixmap(self.rect(), theme._brushed_tile(QColor(theme.PANEL)))
        p.setOpacity(1.0)
        # a top-lit bevel so the console reads as bolted below the sea
        p.setPen(QPen(QColor(theme.EDGE_HI), 1)); p.setOpacity(0.4)
        p.drawLine(0, 3, W, 3); p.setOpacity(1.0)
        # live rail: a short hazard mark at the left, a hairline across, rivets
        theme.hazard_bar(p, QRectF(0, 0, 150, 3), color=theme.ACCENT, alpha=170)
        p.setPen(QPen(QColor(theme.ACCENT), 1)); p.setOpacity(0.35)
        p.drawLine(150, 1, W, 1); p.setOpacity(1.0)
        theme.rivets(p, 4, 2, W - 8, 6, inset=3, step=54, r=1.4)

    # ── Builders ────────────────────────────────────────────────────────────────
    def _titled(self, title: str, content: QWidget) -> QWidget:
        """A labelled section (header above its content), so panels read as
        separate groups now that there are no tabs."""
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(4, 2, 4, 2); v.setSpacing(3)
        lbl = QLabel(title)
        lbl.setStyleSheet(f"color:{theme.TEXT_DIM};font-family:'{theme.HEAD_FAMILY}';"
                          f"font-size:9px;font-weight:bold;letter-spacing:2px;"
                          f"background:transparent;")
        v.addWidget(lbl); v.addWidget(content, 1)
        return w

    def _sep(self) -> QWidget:
        """A thin vertical divider between sections."""
        s = QWidget(); s.setFixedWidth(1)
        s.setStyleSheet(f"background:{theme.LINE};")
        return s

    def _unit_tab(self) -> QWidget:
        """Two-row grid of unit buttons in a horizontally-scrolling area, so the
        roster (plus sandbox bosses) never overflows the panel at any resolution."""
        keys = list(self.roster) + list(self.boss_roster)
        grid_w = QWidget()
        g = QGridLayout(grid_w); g.setSpacing(5); g.setContentsMargins(6, 6, 6, 6)
        g.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        for i, key in enumerate(keys):
            is_boss = key in self.data.bosses
            sdef = self.data.bosses[key] if is_boss else self.data.vehicles[key]
            b = UnitButton(sdef, self.hotkeys.get(key, ""), hide_cost=self.sandbox)
            b.deploy.connect(self.canvas.cmd_deploy)
            b.dequeue.connect(self.canvas.cmd_dequeue)
            b.hovered.connect(self._on_btn_hover)
            b.set_state(True, 0, self.canvas.sprites.hi(self.my, key))
            self.unit_btns[key] = b
            g.addWidget(b, i % 2, i // 2)

        scroll = _UnitScroll()
        scroll.setWidget(grid_w); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;}"
            f"QScrollBar:horizontal{{background:{theme.BG};height:9px;margin:0;}}"
            f"QScrollBar::handle:horizontal{{background:{theme.LINE_HI};min-width:30px;}}"
            "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0;}")
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return scroll

    def _upgrades_tab(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w); g.setSpacing(5); g.setContentsMargins(6, 6, 6, 6)
        g.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.upg_cards: dict[str, UpgradeCard] = {}
        for i, (key, name, icon) in enumerate(UPG):
            c = UpgradeCard(key, name, icon)
            c.clicked.connect(self._upgrade)
            self.upg_cards[key] = c
            g.addWidget(c, i % 2, i // 2)
        return w

    def _resources_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(0); row.setContentsMargins(0, 0, 0, 0)
        self.gauge = ResourceGauge()
        row.addWidget(self.gauge, 1)
        return row

    # BTN_SMALL with its side padding halved: the controls row lives in a fixed
    # 286px column, so the buttons must earn their width from text, not padding.
    _BTN_TIGHT = theme.BTN_SMALL.replace("padding:5px 14px;", "padding:5px 8px;")
    # The pause button while the battle is held: same geometry, amber border and
    # text so the held state is visible on the console itself.
    _BTN_HELD = (_BTN_TIGHT
                 .replace(f"border:1px solid {theme.LINE};", f"border:1px solid {theme.ACCENT};")
                 .replace(f"color:{theme.TEXT_DIM};", f"color:{theme.ACCENT};"))

    def _spawn_row(self) -> QHBoxLayout:
        """Sandbox side-selector on its own full-width row: choosing which side the
        deploy bar spawns for is a frequent action, so it gets a large, easy target
        that never steals room from – or gets misclicked instead of – the controls."""
        row = QHBoxLayout(); row.setSpacing(6); row.setContentsMargins(0, 0, 0, 0)
        self.spawn_toggle = QPushButton()
        # Must never take keyboard focus: the canvas keeps Esc/Space/hotkeys.
        self.spawn_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.spawn_toggle.clicked.connect(self._toggle_spawn_team)
        self._update_spawn_toggle()
        row.addWidget(self.spawn_toggle, 1)
        return row

    def _controls_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)
        # Fleet counts are a numeric readout → the plotting-room mono font. The
        # label yields (clips) rather than shoving the buttons off the panel.
        self.status_lbl = QLabel("Ready")
        self.status_lbl.setStyleSheet(f"color:{theme.TEXT_DIM};font-family:'{theme.MONO_FAMILY}';"
                                      f"font-size:10px;background:transparent;")
        self.status_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.status_lbl, 1)
        # (Sandbox spawn-side toggle lives on its own row – see _spawn_row.)
        self.pause_btn = QPushButton("PAUSE"); self.pause_btn.setStyleSheet(self._BTN_TIGHT)
        self.pause_btn.setFixedWidth(78)          # PAUSE ↔ RESUME swap without reflow
        # Must never take keyboard focus: the canvas keeps Esc/Space/hotkeys.
        self.pause_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pause_btn.clicked.connect(self.canvas.toggle_pause)
        self._paused_shown = False                # last state styled onto pause_btn
        menu_btn = QPushButton("MENU"); menu_btn.setStyleSheet(self._BTN_TIGHT)
        menu_btn.clicked.connect(self.request_menu.emit)
        restart = QPushButton("RESTART"); restart.setStyleSheet(self._BTN_TIGHT)
        restart.clicked.connect(self._restart)
        # Settings tab as a compact gear – the row is width-bound, and IconButton
        # never takes keyboard focus, so the canvas keeps its hotkeys.
        gear = IconButton("settings", "Settings", size=26)
        gear.clicked.connect(self.request_settings.emit)
        # Full-screen toggle (mirrors F11), same compact size – pinned to the far
        # right end of the control row.
        self.fs_btn = IconButton("fullscreen", "Toggle fullscreen  (F11)", size=26)
        self.fs_btn.clicked.connect(self.request_fullscreen.emit)
        row.addWidget(gear); row.addWidget(self.pause_btn)
        row.addWidget(menu_btn); row.addWidget(restart); row.addWidget(self.fs_btn)
        return row

    def set_fullscreen(self, on: bool):
        """Swap the control-row toggle between the expand / restore glyphs."""
        self.fs_btn.set_icon("windowed" if on else "fullscreen")

    def _toggle_spawn_team(self):
        self.canvas.spawn_team = 'enemy' if self.canvas.spawn_team == 'player' else 'player'
        self._update_spawn_toggle()

    def _update_spawn_toggle(self):
        enemy = self.canvas.spawn_team == 'enemy'
        self.spawn_toggle.setText("SPAWN: ENEMY" if enemy else "SPAWN: ALLY")
        col = theme.DANGER if enemy else theme.GOOD
        self.spawn_toggle.setStyleSheet(
            f"QPushButton{{background:{theme.BG};border:1px solid {col};color:{col};"
            f"font-family:'{theme.HEAD_FAMILY}';font-size:10px;font-weight:bold;padding:4px;}}")

    # ── Hover wiring ────────────────────────────────────────────────────────────
    def _on_btn_hover(self, sdef):
        if sdef is None:
            self.info.show_unit(None); return
        is_boss = sdef.key in self.data.bosses
        locked = (not is_boss) and (sdef.key not in self.my_unlocked)
        # A queued hull can be right-clicked to cancel for a full refund; surface the
        # amount (the most-recent purchase's price) so it's discoverable.
        q_refund = None
        if (self.my == 'player' and not self.canvas.game_over
                and not self.canvas.paused
                and (self.canvas.unit_queued(sdef.key) > 0
                     or self.canvas.unit_rush_queued(sdef.key) > 0)):
            q_refund = self.canvas.dequeue_refund(sdef.key)
        self.info.show_unit(sdef, locked=locked, stage=progression.stage(sdef.key),
                            queue_refund=q_refund)

    def _on_field_hover(self, ship):
        if ship is None:
            self.info.show_unit(None)
        else:
            # A friendly turret can be right-clicked to sell for a partial refund;
            # surface that (and the amount) in the readout so it's discoverable.
            sell = None
            if (getattr(ship, 'team', '') == self.my
                    and getattr(ship.sdef, 'unit_type', 'ship') == 'turret'
                    and not self.canvas.game_over and not self.canvas.paused):
                sell = int(round(ship.sdef.cost * SELL_FRACTION))
            self.info.show_unit(ship.sdef, (ship.hp, ship.max_hp), sell_refund=sell)

    # ── Refresh ─────────────────────────────────────────────────────────────────
    def _refresh(self):
        infinite = self.canvas.infinite_money and self.my == 'player'
        pl = self.canvas.faction(self.my)
        self.gauge.set_values(pl.resources, pl.max_res, pl.income, infinite,
                              hide_income=self.sandbox)

        # Surge Rush (Ctrl held) climbs every deploy-bar price; Bulk (Alt held) drops
        # it and buys a batch of BULK_SIZE; holding BOTH gives a surged batch (+50%
        # then only −10%). The two now COMPOSE – surge is active whenever Ctrl is held,
        # even alongside Alt – and prices come from deploy_unit_cost(), the exact same
        # function the sim charges with, so the label can never disagree with the buy.
        surging = self.my == 'player' and self.canvas.surge_active()
        bulk    = self.my == 'player' and self.canvas.bulk_active()

        for key, btn in self.unit_btns.items():
            if self.sandbox or key not in self.data.vehicles:
                btn.set_state(True, 0); continue        # sandbox / bosses: always ready
            sdef = self.data.vehicles[key]
            locked = key not in self.my_unlocked
            cd_rem, cd_total = self.canvas.build_cooldown(key, self.my)
            queued = self.canvas.unit_queued(key) if self.my == 'player' else 0
            rush_queued = self.canvas.unit_rush_queued(key) if self.my == 'player' else 0
            # A unique deployed structure (the Bastion) is held unavailable while it
            # stands – the button reads as occupied rather than ready-to-deploy.
            held = getattr(sdef, 'unit_type', '') == 'structure' and self.canvas._has_oilrig(self.my)
            # Bulk (Alt) only buys hulls – turrets and the oil rig are hand-placed one
            # at a time (deploy_unit routes them straight to start_placing, before the
            # batch loop), so they can never come in groups of BULK_SIZE. Suppress bulk
            # on their buttons so the label never advertises a ×N batch the sim won't buy.
            placed = getattr(sdef, 'unit_type', '') in ('turret', 'structure')
            bulk_btn = bulk and not placed
            # Per-hull price under the live modifiers (base price when neither is
            # held). A bulk press is all-or-nothing, so affordability is judged on the
            # WHOLE batch (BULK_SIZE hulls) while Alt is held – the button dims unless
            # you can pay for every hull, matching what the sim will actually buy.
            cost = deploy_unit_cost(sdef.cost, surging, bulk_btn)
            need = cost * BULK_SIZE if bulk_btn else cost
            btn.set_state(pl.resources >= need, queued,
                          locked=locked, stage=progression.stage(key),
                          cd_rem=cd_rem, cd_total=cd_total, held=held,
                          cost=cost, surge=surging, bulk=bulk_btn,
                          rush_queued=rush_queued)

        for key, card in self.upg_cards.items():
            lv = pl.upgrades[key]
            prog = self.canvas.upgrade_progress(key, self.my)
            busy = prog is not None                # only THIS track is tied up
            if lv >= self.canvas.max_upg_lvl:      # per-level cap (may be < MAX_LVL)
                card.set_state(lv, 0, False, True, prog)
            else:
                cost = UPGRADE_COSTS[key][lv]
                afford = ((infinite or pl.resources >= cost)
                          and not busy and not self.canvas.game_over)
                card.set_state(lv, cost, afford, False, prog)

        self.pause_btn.setEnabled(not self.canvas.game_over)
        if self.canvas.paused != self._paused_shown:   # restyle only on change
            self._paused_shown = self.canvas.paused
            self.pause_btn.setText("RESUME" if self._paused_shown else "PAUSE")
            self.pause_btn.setStyleSheet(self._BTN_HELD if self._paused_shown
                                         else self._BTN_TIGHT)

        ps = sum(1 for s in self.canvas.ships if getattr(s, 'team', '') == "player"
                 and not getattr(s, 'is_base', False))
        es = sum(1 for s in self.canvas.ships if getattr(s, 'team', '') == "enemy"
                 and not getattr(s, 'is_base', False))
        self.status_lbl.setText(f"Fleet {ps}   ·   Enemy {es}")

    def _upgrade(self, key):
        # Buying just starts the research; it applies after a delay (see
        # BattleCanvas.request_upgrade). One per track – tracks run in parallel.
        # Routes through the team-aware command layer (host applies locally; the
        # LAN client sends the order to the host).
        if self.canvas.cmd_upgrade(key):
            self._refresh()

    def _restart(self):
        self.canvas.reset(); self._refresh()
