"""
game.ui.app — AppWindow: the shell that moves between the save-select screen, the
campaign screen and an active battle, owning the shared data, sprites and saves.
"""

from __future__ import annotations
from PyQt6.QtWidgets import (QMainWindow, QStackedWidget, QWidget, QVBoxLayout, QLabel,
                            QGraphicsOpacityEffect)
from PyQt6.QtCore  import (Qt, QTimer, QPoint, QPropertyAnimation,
                          QParallelAnimationGroup, QEasingCurve, QAbstractAnimation)
from PyQt6.QtGui   import QShortcut, QKeySequence

from ..registry import GameData
from ..assets   import SpriteCache
from ..settings import SETTINGS
from ..battle   import BattleCanvas
from ..save     import SaveManager, DIFFICULTY
from ..        import progression
from .  import theme
from .hud  import BattleHud
from .menu import (MainMenuScreen, SaveSelectScreen, CampaignScreen,
                   MultiplayerScreen)
from .widgets import SettingsOverlay
from .tutorial import TutorialOverlay, TipOverlay


class BattleScreen(QWidget):
    """A battle: canvas on top, command HUD below, plus a banner."""

    def __init__(self, canvas: BattleCanvas, hud: BattleHud):
        super().__init__()
        self.canvas = canvas; self.hud = hud
        lay = QVBoxLayout(self); lay.setSpacing(0); lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(canvas, 1); lay.addWidget(hud)

        self.banner = QLabel("", canvas)
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.setStyleSheet(
            f"color:{theme.ACCENT};font-family:'{theme.HEAD_FAMILY}';font-size:26px;"
            f"font-weight:bold;letter-spacing:4px;"
            f"background:rgba(8,11,14,210);border:1px solid {theme.DANGER};border-radius:0;padding:8px 26px;")
        self.banner.hide()
        self._bt = QTimer(self); self._bt.setSingleShot(True); self._bt.timeout.connect(self._hide_banner)
        # Flashing-alarm state: while active, _flash_tick toggles the banner between a
        # bright and a dim red every beat so a campaign boss reads as a klaxon warning.
        self._flash_timer = QTimer(self); self._flash_timer.setInterval(360)
        self._flash_timer.timeout.connect(self._flash_tick)
        self._flash_on = True
        canvas.sig_boss.connect(self._on_boss)
        canvas.sig_alert.connect(self._show_banner)

        # The settings tab, opened from the HUD gear. The battle is held while it
        # is up (and released on close only if the player hadn't paused already).
        self.settings_panel = SettingsOverlay(self)
        self._was_paused = False
        hud.request_settings.connect(self._open_settings)
        self.settings_panel.closed.connect(self._close_settings)

    def _open_settings(self):
        self._was_paused = self.canvas.paused
        self.canvas.paused = True
        self.settings_panel.open()

    def _close_settings(self):
        self.canvas.paused = self._was_paused
        self.canvas.setFocus()

    def _on_boss(self, state):
        # Campaign spawn carries the boss name ("incoming:<name>") → flashing klaxon
        # alarm. A multiplayer flagship boss carries pre-built team-aware text
        # ("strike:<text>") → the same klaxon. Sandbox spawn and the boss-down cue
        # stay plain static banners.
        if state.startswith("incoming:"):
            name = state.split(":", 1)[1].strip() or "BATTLESHIP"
            self._show_alarm(f"BATTLESHIP {name.upper()} IS COMING")
        elif state.startswith("strike:"):
            self._show_alarm(state.split(":", 1)[1].strip())
        else:
            self._show_banner("WARNING — BOSS INCOMING" if state == "incoming"
                              else "BOSS DOWN — FINISH THEM")

    # ── Banner ──────────────────────────────────────────────────────────────────
    _ALARM_BRIGHT = ("color:%s;background:rgba(30,9,9,225);border:2px solid %s;"
                     % (theme.DANGER_HI, theme.DANGER_HI))
    _ALARM_DIM    = ("color:%s;background:rgba(8,11,14,200);border:2px solid %s;"
                     % (theme.DANGER, theme.DANGER))

    def _alarm_style(self, bright: bool) -> str:
        return (f"font-family:'{theme.HEAD_FAMILY}';font-size:26px;font-weight:bold;"
                f"letter-spacing:4px;border-radius:0;padding:8px 26px;"
                + (self._ALARM_BRIGHT if bright else self._ALARM_DIM))

    def _show_alarm(self, text):
        """A flashing red klaxon banner (campaign boss warning)."""
        self.banner.setText(text)
        self._flash_on = True
        self.banner.setStyleSheet(self._alarm_style(True))
        self.banner.adjustSize(); self._place()
        self.banner.show(); self.banner.raise_()
        self._flash_timer.start(); self._bt.start(3600)

    def _flash_tick(self):
        self._flash_on = not self._flash_on
        self.banner.setStyleSheet(self._alarm_style(self._flash_on))

    def _hide_banner(self):
        self._flash_timer.stop()
        self.banner.hide()

    def _show_banner(self, text):
        # A plain (non-flashing) banner — restore the default amber styling in case a
        # previous alarm left the flashing red style on the shared QLabel.
        self._flash_timer.stop()
        self.banner.setStyleSheet(
            f"color:{theme.ACCENT};font-family:'{theme.HEAD_FAMILY}';font-size:26px;"
            f"font-weight:bold;letter-spacing:4px;"
            f"background:rgba(8,11,14,210);border:1px solid {theme.DANGER};"
            f"border-radius:0;padding:8px 26px;")
        self.banner.setText(text); self.banner.adjustSize()
        self._place(); self.banner.show(); self.banner.raise_(); self._bt.start(2600)

    def _place(self):
        self.banner.move((self.canvas.width() - self.banner.width()) // 2, 54)

    def set_fullscreen(self, on: bool):
        """Forward the full-screen state to the HUD's toggle glyph."""
        self.hud.set_fullscreen(on)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.banner.isVisible(): self._place()


class AppWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Penumbra"); self.resize(1280, 760)
        # The campaign screen's layout bottoms out at ~1098×685 (dossier + four
        # operation columns + catalog); pin the window there so no screen can
        # ever be squeezed into clipping.
        self.setMinimumSize(1100, 690)
        self.setStyleSheet(theme.SCREEN_BG)

        self.data = GameData()
        self.sprites = SpriteCache()
        self.sprites.load_units(self.data.all_units)
        self.sprites.load_portraits(self.data.all_units)
        self.sprites.load_fortress()
        self.saves = SaveManager()

        self.stack = QStackedWidget(); self.setCentralWidget(self.stack)

        self.main_menu = MainMenuScreen(self.sprites)
        self.main_menu.saves.connect(self._to_saves)
        self.main_menu.sandbox.connect(self._start_sandbox)
        self.main_menu.multiplayer.connect(self._to_multiplayer)
        self.main_menu.quit_game.connect(self.close)
        self.main_menu.fullscreen.connect(self._toggle_fullscreen)

        self.multiplayer = MultiplayerScreen(self.data, self.sprites)
        self.multiplayer.launch.connect(self._start_multiplayer)
        self.multiplayer.play_bot.connect(self._start_bot_match)
        self.multiplayer.back.connect(self._to_main_menu)
        self.multiplayer.fullscreen.connect(self._toggle_fullscreen)

        self.save_select = SaveSelectScreen(self.saves, self.data, self.sprites)
        self.save_select.chosen.connect(self._open_save)
        self.save_select.create.connect(self._create_save)
        self.save_select.quit_game.connect(self._to_main_menu)   # "← MENU"
        self.save_select.fullscreen.connect(self._toggle_fullscreen)

        self.campaign = CampaignScreen(self.data, self.sprites)
        self.campaign.play.connect(self._start_level)
        self.campaign.back.connect(self._to_saves)
        self.campaign.quit_game.connect(self.close)
        self.campaign.fullscreen.connect(self._toggle_fullscreen)
        # "HOW TO PLAY" replays the fresh-profile in-battle guide (real HUD, boxed).
        self.campaign.tutorial.connect(self._to_howto)
        self._tutorial_pending = False           # fresh-profile in-battle guide (set on save creation)
        self._tutorial_howto = False             # guide opened from HOW TO PLAY (leave, don't fight)

        self.stack.addWidget(self.main_menu)     # 0
        self.stack.addWidget(self.save_select)   # 1
        self.stack.addWidget(self.campaign)      # 2
        self.stack.addWidget(self.multiplayer)   # 3
        self.battle_screen: BattleScreen | None = None

        # The full-screen keys toggle from any screen — window-level shortcuts so
        # they fire even mid-battle, when the canvas holds keyboard focus. The keys
        # are rebindable (see SETTINGS.keybinds); refresh_keybinds rebuilds them live.
        self._fs_shortcuts = []
        self._install_fs_shortcuts()

        self.active_save = None
        self.active_level = None
        self._transition = None                  # live screen-slide animation, if any
        self._to_main_menu()

    def _install_fs_shortcuts(self):
        for sc in self._fs_shortcuts:
            sc.setParent(None); sc.deleteLater()
        self._fs_shortcuts = []
        for code in SETTINGS.keys_for("fullscreen"):
            sc = QShortcut(QKeySequence(code), self)
            sc.activated.connect(self._toggle_fullscreen)
            self._fs_shortcuts.append(sc)

    def refresh_keybinds(self):
        """Re-point the window-level shortcuts at their current bindings so a key
        rebound in the SETTINGS panel takes effect at once, without a restart. Also
        rebuilds the live battle HUD's per-ship deploy hotkeys."""
        self._install_fs_shortcuts()
        bs = self.battle_screen
        hud = getattr(bs, "hud", None) if bs is not None else None
        if hud is not None and hasattr(hud, "refresh_keybinds"):
            hud.refresh_keybinds()

    def _toggle_fullscreen(self):
        """Borderless full screen (no title bar / close button) ↔ normal window.
        Qt's showFullScreen strips the window frame entirely, so the OS 'X' is
        gone until toggled back. Reachable from anywhere via F11 (see keyPressEvent)."""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        # Keep every screen's toggle glyph (expand ↔ restore) in sync — the button
        # lives on all of them now, so whichever one is up shows the right state.
        on = self.isFullScreen()
        self.main_menu.set_fullscreen(on)
        self.save_select.set_fullscreen(on)
        self.campaign.set_fullscreen(on)
        self.multiplayer.set_fullscreen(on)
        if self.battle_screen is not None:
            self.battle_screen.set_fullscreen(on)

    def keyPressEvent(self, ev):
        # Escape backs out one step on the menu screens: full screen → windowed,
        # campaign → saves, saves → main menu. (A battle consumes Escape itself,
        # opening its pause menu — that's intentional.)
        if ev.key() == Qt.Key.Key_Escape:
            cur = self.stack.currentWidget()
            # In a battle, Escape must always open/close the pause menu, no matter
            # which sub-widget holds keyboard focus. The canvas handles Escape
            # itself, but only receives it while focused — and clicking a HUD
            # button steals that focus. Key events bubble up here when the canvas
            # isn't focused, so forward Escape to the canvas's own handler (which
            # also cancels placement / dismisses game-over as appropriate). The
            # settings overlay owns Escape while it's up, so leave that alone.
            if (cur is self.battle_screen and self.battle_screen is not None
                    and not self.battle_screen.settings_panel.isVisible()):
                self.battle_screen.canvas.keyPressEvent(ev); return
            if self.isFullScreen():
                self._toggle_fullscreen(); return
            if cur is self.save_select:
                if self.save_select.overlay.isVisible():
                    self.save_select.overlay.hide()      # close the difficulty prompt
                else:
                    self._to_main_menu()
                return
            if cur is self.campaign:
                self._to_saves(); return
        super().keyPressEvent(ev)

    def _to_main_menu(self):
        # Mirror of the save entry (see _to_saves): the camera pans back DOWN to the
        # waterline, so the menu rises from below while the save screen lifts off the
        # top. Instant swap when we're not actually leaving the save screen (e.g. the
        # initial startup call, where the menu is already the current widget).
        if self.stack.currentWidget() is self.save_select:
            self._slide_to(self.main_menu, going_up=True)
        else:
            self.stack.setCurrentWidget(self.main_menu)

    def _to_howto(self):
        """Replay the in-game battle guide: the exact overlay a fresh profile sees on
        its first battle — the real HUD with its sections spotlighted, boxed and
        explained. Starts the opening level so the guide has a live battle to point
        at; exit via the HUD's menu button returns to the campaign."""
        if self.active_save is None:
            return
        self._tutorial_pending = True
        self._tutorial_howto = True
        self._start_level(self.data.levels_sorted()[0].key)

    # ── Navigation ──────────────────────────────────────────────────────────────
    def _slide_to(self, target, going_up=True, on_finish=None, axis="v"):
        """Scroll `target` into place over the current screen. On the vertical axis
        (default), going_up=True (entering) pushes the current screen off the top
        while `target` rises from below; going_up=False (backing out) drops the
        current screen off the bottom while `target` descends from above. axis="h"
        pans sideways instead: going_up=True brings `target` in from the right
        (advancing deeper), going_up=False from the left (backing out). `on_finish`
        fires once the slide settles. Falls back to an instant swap if a slide is
        already running or the stack has no size."""
        current = self.stack.currentWidget()
        h, w = self.stack.height(), self.stack.width()
        span = h if axis == "v" else w
        if current is target or self._transition is not None or span <= 0:
            self.stack.setCurrentWidget(target)
            if on_finish is not None: on_finish()
            return

        off = span if going_up else -span        # where `target` waits: below/above or right/left
        start = QPoint(0, off) if axis == "v" else QPoint(off, 0)
        # Both screens must be visible during the slide. The stack normally hides
        # every widget but the current one, so we place and show the target by hand;
        # setCurrentWidget on finish restores the stack's own show/hide bookkeeping.
        target.setGeometry(start.x(), start.y(), w, h)
        target.show(); target.raise_()

        def _finish():
            self.stack.setCurrentWidget(target)
            current.move(0, 0)                   # reset the screen that scrolled out
            self._transition = None
            if on_finish is not None: on_finish()

        group = QParallelAnimationGroup(self)
        for widget, s, e in ((current, QPoint(0, 0), QPoint(0, 0) - start),
                             (target,  start,        QPoint(0, 0))):
            a = QPropertyAnimation(widget, b"pos", self)
            a.setDuration(380); a.setStartValue(s); a.setEndValue(e)
            a.setEasingCurve(QEasingCurve.Type.InOutCubic)
            group.addAnimation(a)
        group.finished.connect(_finish)
        self._transition = group
        group.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _fade_swap(self, on_black, on_finish=None):
        """Fade the whole window to black, run `on_black` at full black to swap the
        underlying screen, then fade back to reveal it. `on_finish` fires once the
        reveal settles. Used for entering/leaving a battle (campaign or sandbox), so
        each fight blinks in and out through black rather than sliding. Falls back to
        running the callbacks instantly if a transition is already live or the window
        has no size yet."""
        if self._transition is not None or self.stack.height() <= 0:
            on_black()
            if on_finish is not None: on_finish()
            return

        veil = QWidget(self)                     # black curtain over the whole window
        veil.setStyleSheet("background-color:#000;")
        veil.setGeometry(self.rect())
        eff = QGraphicsOpacityEffect(veil); eff.setOpacity(0.0)
        veil.setGraphicsEffect(eff)
        veil.show(); veil.raise_()

        fade_in = QPropertyAnimation(eff, b"opacity", self)
        fade_in.setDuration(300); fade_in.setStartValue(1.0); fade_in.setEndValue(0.0)
        fade_in.setEasingCurve(QEasingCurve.Type.InOutCubic)

        def _reveal():
            def _done():
                veil.deleteLater()
                self._transition = None
                if on_finish is not None: on_finish()
            fade_in.finished.connect(_done)
            fade_in.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

        fade_out = QPropertyAnimation(eff, b"opacity", self)
        fade_out.setDuration(260); fade_out.setStartValue(0.0); fade_out.setEndValue(1.0)
        fade_out.setEasingCurve(QEasingCurve.Type.InOutCubic)
        fade_out.finished.connect(lambda: (on_black(), _reveal()))
        self._transition = fade_out              # block other transitions until revealed
        fade_out.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _to_saves(self):
        self.save_select.refresh()
        # Every route in is animated. From the main menu the camera pans UP into
        # the sky, so the save screen descends from above (going_up=False) while
        # the menu drops off the bottom. Backing out of the campaign pans back
        # LEFT along the coast (the mirror of the entry slide).
        cur = self.stack.currentWidget()
        if cur is self.main_menu:
            self._slide_to(self.save_select, going_up=False)
        elif cur is self.campaign:
            self._slide_to(self.save_select, going_up=False, axis="h")
        else:
            self.stack.setCurrentWidget(self.save_select)

    def _open_save(self, slot):
        save = self.saves.load(slot)
        if save is None: return
        self.active_save = save
        self.campaign.set_save(save)
        # Advance deeper: the campaign pans in from the right along the coast.
        self._slide_to(self.campaign, going_up=True, axis="h")

    def _create_save(self, slot, difficulty):
        self.active_save = self.saves.create(slot, difficulty)
        self.campaign.set_save(self.active_save)
        self._tutorial_pending = True            # fresh profile: guide shows in its first battle
        self._slide_to(self.campaign, going_up=True, axis="h")

    def _start_level(self, key):
        level = self.data.levels.get(key)
        if not level or self.active_save is None: return
        self._teardown_battle()
        self.active_level = key
        diff = DIFFICULTY.get(self.active_save.difficulty, 1.3)
        cleared = self.active_save.cleared_count()
        unlocked = progression.unlocked_keys(cleared)
        # The enemy economy is pinned to THIS level's place in the campaign, not
        # the player's global progress — so replaying an early level with the
        # whole roster unlocked still faces a period-appropriate enemy.
        stage = self.data.campaign_stage(key)
        enemy_unlocked = progression.unlocked_keys(stage)

        canvas = BattleCanvas(level, self.data, self.sprites, diff, unlocked,
                              enemy_unlocked,
                              difficulty_name=self.active_save.difficulty,
                              cleared=cleared)
        hud = BattleHud(canvas, self.data)
        hud.request_menu.connect(self._exit_battle)
        hud.request_fullscreen.connect(self._toggle_fullscreen)
        canvas.sig_menu.connect(self._exit_battle)
        canvas.sig_main_menu.connect(self._exit_to_main_menu)
        canvas.sig_over.connect(self._on_over)

        self.battle_screen = BattleScreen(canvas, hud)
        self.battle_screen.set_fullscreen(self.isFullScreen())
        self.stack.addWidget(self.battle_screen)

        def _revealed():
            canvas.setFocus()
            if self._tutorial_pending:           # practical guide, drawn over the HUD
                self._tutorial_pending = False
                howto, self._tutorial_howto = self._tutorial_howto, False
                TutorialOverlay(self.battle_screen, hud,
                                on_exit=self._exit_battle if howto else None)
                return
            # First battle that actually seats oil platforms: a one-card field note
            # teaching that capturing them earns extra income. Once per profile.
            if (self.active_save is not None and canvas.capture_points
                    and not self.active_save.seen("oil_platforms")):
                self.active_save.mark_seen("oil_platforms")
                self.saves.save(self.active_save)
                TipOverlay(
                    self.battle_screen, canvas, "OIL PLATFORMS",
                    "Neutral oil platforms now dot the open water. Hold one with your "
                    "SURFACE SHIPS — submarines and aircraft can't board — to capture "
                    "it. Every platform you hold pays EXTRA INCOME each second on top "
                    "of your base, so seize and defend them to out-build the enemy.",
                    icon="oilrig")

        # Fade to black and reveal the battle rather than snapping in — the canvas
        # timer is already running, so the fight fades up already underway.
        self._fade_swap(lambda: self.stack.setCurrentWidget(self.battle_screen),
                        on_finish=_revealed)

    def _start_sandbox(self):
        """Free-play: spawn any ally OR enemy with no cooldowns, no AI, no win/lose."""
        self._teardown_battle()
        self.active_save = None; self.active_level = None
        full = progression.unlocked_keys(99)
        level = self.data.levels_sorted()[0]     # a base level for the backdrop/theme
        canvas = BattleCanvas(level, self.data, self.sprites, 1.0, full,
                              full, sandbox=True)
        hud = BattleHud(canvas, self.data)
        hud.request_menu.connect(self._exit_battle)
        hud.request_fullscreen.connect(self._toggle_fullscreen)
        canvas.sig_menu.connect(self._exit_battle)
        canvas.sig_main_menu.connect(self._exit_to_main_menu)
        self.battle_screen = BattleScreen(canvas, hud)
        self.battle_screen.set_fullscreen(self.isFullScreen())
        self.stack.addWidget(self.battle_screen)
        # Fade to black and reveal the sandbox, don't snap in.
        self._fade_swap(lambda: self.stack.setCurrentWidget(self.battle_screen),
                        on_finish=canvas.setFocus)

    def _to_multiplayer(self):
        """Open the LAN lobby from the main menu (descends from above, like saves)."""
        if self.stack.currentWidget() is self.main_menu:
            self._slide_to(self.multiplayer, going_up=False)
        else:
            self.stack.setCurrentWidget(self.multiplayer)

    def _start_multiplayer(self, role, link, level_key, session="", name="", opp=""):
        """Launch a head-to-head battle. The host (P1) commands the player fleet
        and runs the authoritative sim; the client (P2) commands the enemy fleet
        and renders host snapshots. Both start from the same level so the map/theme
        match. No campaign save is touched.

        `link` is normally a live NetLink. For an online host it's instead a
        pending `NetRelayHost` (its room is open but nobody has joined): the battle
        is entered in an 'awaiting player' hold with the HUD disabled until the
        relay pairs a joiner, at which point the canvas adopts the live link.

        `session` is the host's match token, carried in the client's launch so a
        mid-match disconnect can rendezvous to reconnect (the host mints its own)."""
        from .. import net
        self._teardown_battle()
        self.active_save = None; self.active_level = None
        level = self.data.levels.get(level_key) or self.data.levels_sorted()[0]
        full = progression.unlocked_keys(99)
        pending = link if isinstance(link, net.NetRelayHost) else None
        net_link = None if pending is not None else link
        canvas = BattleCanvas(level, self.data, self.sprites, 1.0, full,
                              full,
                              difficulty_name="normal",
                              net_role=role, net_link=net_link, net_pending=pending,
                              net_session=(session or None),
                              net_name=(name or None), net_opponent=(opp or None))
        hud = BattleHud(canvas, self.data)
        hud.request_menu.connect(self._exit_to_main_menu)
        hud.request_fullscreen.connect(self._toggle_fullscreen)
        canvas.sig_menu.connect(self._exit_to_main_menu)
        canvas.sig_main_menu.connect(self._exit_to_main_menu)
        if pending is not None:
            # Held in the lobby-in-game: no commands until the opponent arrives.
            hud.setEnabled(False)
            canvas.sig_peer_joined.connect(lambda h=hud: h.setEnabled(True))
        self.battle_screen = BattleScreen(canvas, hud)
        self.battle_screen.set_fullscreen(self.isFullScreen())
        self.stack.addWidget(self.battle_screen)
        self._fade_swap(lambda: self.stack.setCurrentWidget(self.battle_screen),
                        on_finish=canvas.setFocus)

    def _start_bot_match(self, level_key, name=""):
        """Launch a solo head-to-head against Captain Bob. This is a full PvP battle
        — symmetric economy, the one-turret opening, the central score platform — but
        with no networking: the enemy faction is commanded by a local bot (net_role
        stays None; vs_bot flips on the PvP ruleset). No campaign save is touched."""
        self._teardown_battle()
        self.active_save = None; self.active_level = None
        level = self.data.levels.get(level_key) or self.data.levels_sorted()[0]
        full = progression.unlocked_keys(99)
        canvas = BattleCanvas(level, self.data, self.sprites, 1.0, full,
                              full,
                              difficulty_name="normal",
                              net_name=(name or None), vs_bot=True)
        hud = BattleHud(canvas, self.data)
        hud.request_menu.connect(self._exit_to_main_menu)
        hud.request_fullscreen.connect(self._toggle_fullscreen)
        canvas.sig_menu.connect(self._exit_to_main_menu)
        canvas.sig_main_menu.connect(self._exit_to_main_menu)
        self.battle_screen = BattleScreen(canvas, hud)
        self.battle_screen.set_fullscreen(self.isFullScreen())
        self.stack.addWidget(self.battle_screen)
        self._fade_swap(lambda: self.stack.setCurrentWidget(self.battle_screen),
                        on_finish=canvas.setFocus)

    def _on_over(self, who):
        if who == "player" and self.active_save and self.active_level and self.battle_screen:
            r = self.battle_screen.canvas.result()
            self.active_save.record(self.active_level, r["time"], r["score"], r["stars"])
            self.saves.save(self.active_save)

    def _exit_battle(self):
        if self.active_save is not None:         # campaign → back to the campaign screen
            self.campaign.set_save(self.active_save)
            dest = self.campaign
        else:                                    # sandbox → back to the main menu
            dest = self.main_menu
        self._leave_battle(dest)

    def _exit_to_main_menu(self):
        """Pause-menu withdraw (Q) / HUD MENU: straight to the main menu, campaign or
        not — unless the opponent has dropped from an active PvP match, in which case
        leaving forfeits rather than exits cleanly (you take the loss, not a free bail)."""
        c = self.battle_screen.canvas if self.battle_screen is not None else None
        if c is not None and c.opponent_gone:
            c._forfeit_disconnect()
            return
        self._leave_battle(self.main_menu)

    def _leave_battle(self, dest):
        leaving = self.battle_screen
        if leaving is None:
            self.stack.setCurrentWidget(dest); return

        # Fade to black, swap in the destination screen and dispose the battle at
        # full black, then fade up to reveal it — the reverse of the entry fade. The
        # battle's tick is frozen the moment we start leaving.
        self.battle_screen = None                # the fade owns `leaving` now
        leaving.canvas._timer.stop()
        self._fade_swap(lambda: (self.stack.setCurrentWidget(dest),
                                 self._dispose_screen(leaving)))

    def _dispose_screen(self, screen):
        self._close_battle_net(screen)
        self.stack.removeWidget(screen)
        screen.deleteLater()

    @staticmethod
    def _close_battle_net(screen):
        """Drop a battle's net link (if any) so the socket frees and the peer sees
        the disconnect the moment either commander leaves the match. Also cancels a
        still-pending relay room, so leaving the 'awaiting player' hold un-lists it."""
        canvas = getattr(screen, "canvas", None)
        link = getattr(canvas, "net", None)
        if link is not None:
            link.close()
        pending = getattr(canvas, "_net_pending", None)
        if pending is not None:
            pending.cancel()
        recon = getattr(canvas, "_reconnector", None)
        if recon is not None:
            if hasattr(recon, "cancel"): recon.cancel()
            elif hasattr(recon, "stop"): recon.stop()

    def _teardown_battle(self):
        if self.battle_screen is not None:
            self._close_battle_net(self.battle_screen)
            self.battle_screen.canvas._timer.stop()
            self.stack.removeWidget(self.battle_screen)
            self.battle_screen.deleteLater()
            self.battle_screen = None
