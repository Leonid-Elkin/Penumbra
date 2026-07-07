"""
game.battle — BattleCanvas: simulation, rendering, camera and input for one level.

Owns the ship/projectile/effect lists and the two factions, runs the fixed-step
update loop, draws the world, and handles the boss trigger (enemy base at 25% HP
spawns its boss and goes immune until the boss dies).
"""

from __future__ import annotations
import math, random
from typing import Optional
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtCore  import Qt, QTimer, QDateTime, QRectF, pyqtSignal
from PyQt6.QtGui   import (QPainter, QColor, QPen, QLinearGradient,
                           QPainterPath, QFontMetrics)

from .config import (WORLD_W, FORT_D_W, MAX_LVL,
                     PLANE_ALT, BOSS_HP_TRIGGER, BASEHP_T, UPGRADE_COSTS,
                     FACTORY_CD_MULT, UPGRADE_TIME, SPAWN_STAGGER, MAX_UNITS,
                     FLEET_T, SALVAGE_T, DEBUG_INFINITE_MONEY, load_capture_config,
                     SURGE_CD_MULT, SURGE_UNLOCK_LEVEL,
                     BULK_SIZE, deploy_unit_cost,
                     SCORE_BOSS_INTERVAL, SCORE_BOSS_FIRST_PVP, SCORE_PER_SEC,
                     SCORE_HOLD_INCOME)
from .entities import (Ship, Boss, Projectile, Faction, Explosion, BaseTarget,
                       OilRig, CapturePoint, diminishing_total, FortressView, Wake)
from .models import Attack
from .ballistics import ballistic_velocity
from .ai import EnemyDirector
from .save import compute_result
from .settings import SETTINGS

# Right-clicking a friendly placed turret sells it back for this fraction of its
# purchase cost (the freed pad/deck node can then take a fresh turret).
SELL_FRACTION = 0.40

# When a base is battered to 0 HP the fort doesn't just vanish and cut to the
# verdict: the field freezes and the doomed fort plays a short collapse — a
# rolling series of explosions with a camera push-in and a fading blast flash —
# for this many seconds before the VICTORY / DEFEAT card is shown (see
# _begin_collapse / _update_collapse).
BASE_DEATH_ANIM = 2.4

# Downing a boss doesn't freeze the fight, but it does flash the same bright blast
# over the whole field as a base collapse — this is how long that bloom takes to
# fade (see boss_flash_t / _draw_boss_flash).
BOSS_FLASH_DUR = 0.5


class BattleCanvas(QOpenGLWidget):
    """The live battlefield. A QOpenGLWidget so the per-frame QPainter draw runs
    on the GPU (hardware-accelerated OpenGL paint engine) — the whole scene is
    repainted every tick, so the 60 fps sim stays smooth even when the field is
    crowded with ships, projectiles and effects."""
    sig_ui    = pyqtSignal()
    sig_boss  = pyqtSignal(str)       # "incoming" | "down"
    sig_over  = pyqtSignal(str)       # "player" | "enemy"
    sig_menu  = pyqtSignal()          # request leaving the battle (campaign / menu)
    sig_main_menu = pyqtSignal()      # request return all the way to the main menu
    sig_alert = pyqtSignal(str)       # transient banner text (counterattacks, etc.)
    sig_hover = pyqtSignal(object)    # the field ship under the cursor (or None)
    sig_peer_joined = pyqtSignal()    # host: an opponent joined our room, battle begins

    def __init__(self, level, data, sprites, difficulty: float = 1.0, unlocked=None,
                 enemy_unlocked=None, sandbox=False,
                 difficulty_name: str = "normal", cleared: int = 99,
                 net_role=None, net_link=None, net_pending=None, net_session=None,
                 net_name=None, net_opponent=None, vs_bot=False):
        super().__init__()
        # ── LAN / WAN multiplayer ────────────────────────────────────────────
        # net_role: None (single-player) | "host" (P1, runs the authoritative
        # sim, commands the player faction) | "client" (P2, commands the enemy
        # faction, renders host snapshots — no local simulation). PvP mode is
        # implied by any net role: the enemy AI is off and a human drives it.
        #
        # net_pending (host only): a NetRelayHost that has opened a room but not
        # yet been paired. The battle is entered immediately in an "awaiting
        # player" hold — the sim is frozen and input blocked — and the moment the
        # relay pairs a joiner we adopt the live link and the fight begins.
        self.net_role = net_role
        self.net      = net_link
        self._net_pending = net_pending
        self.awaiting_peer = net_pending is not None
        self._await_error: Optional[str] = None
        # "Play with a bot" (Captain Bob): a solo match that runs the head-to-head
        # PvP ruleset — symmetric economy, the one-turret opening, no campaign
        # director/HP-scaling, the central score platform — but with the enemy
        # faction driven by a LOCAL bot instead of a networked human. net_role stays
        # None (no link, no snapshots); the bot ticks inside _update and issues the
        # same enemy-faction orders a P2 client would (see game.bot.CaptainBob).
        self.vs_bot   = bool(vs_bot)
        self.pvp      = net_role in ("host", "client") or self.vs_bot
        # BOTH players command the "player" faction from THEIR OWN point of view:
        # the host runs the sim with itself as player; the client renders every
        # host snapshot through a horizontal mirror + team swap (see netsync.apply)
        # so its own base always sits on the LEFT in player black/amber and the
        # opponent on the RIGHT in danger red — regardless of who is host.
        self.my_team  = "player"
        self.mirror_view = (net_role == "client")   # client renders the world mirrored
        # Commander names shown on the two base HP gauges (PvP only). My own name
        # rides the LEFT/FRIENDLY bar; the opponent's the RIGHT/HOSTILE one. The
        # client already learned the host's name from the lobby 'hello'; the host
        # learns the client's from the 'ident' the client sends once in-battle.
        self.player_name   = net_name or None
        self.opponent_name = net_opponent or None
        if self.vs_bot and not self.opponent_name:
            from .bot import CaptainBob
            self.opponent_name = CaptainBob.NAME     # labels the HOSTILE gauge
        self._ident_sent   = False                  # client: name-to-host, once
        # Session token: the host mints one and announces it in the 'hello'; the
        # client is handed the host's token. On a mid-match disconnect both sides
        # rendezvous through the relay under this token to reconnect (see
        # _on_net_lost / _reconnect_pump). None for single-player.
        if net_role == "host":
            from . import net as _net
            self._net_session = _net.new_session_token()
        else:
            self._net_session = net_session
        self._hello_sent = False
        # Password-protected rooms are enforced HERE, host-side — the relay is
        # deliberately dumb and byte-transparent (see relay.py), so we never rely on
        # it to gate a locked room. After the relay pairs a joiner, the host holds
        # the match until that joiner proves the room password over the link; only
        # then do we send the 'hello' (which carries the reconnect token) and begin.
        # An impostor is dropped and the room re-opened. Open rooms admit anyone.
        self._net_password  = (getattr(net_pending, "password", "") or "") \
            if net_role == "host" else ""
        self._net_room_name = getattr(net_pending, "name", "") if net_role == "host" else ""
        self._peer_authed   = not (net_role == "host" and bool(self._net_password))
        self._auth_started_ms = 0
        self._reconnecting = False
        self._reconnector = None
        self._reconnect_started_ms = 0
        self._nid_seq      = 0          # host: stable ids stamped on entities at send
        self._net_sent_fx  = set()      # host: explosion ids already broadcast
        self._ghosts: dict = {}         # client: nid -> reconstructed render entity
        self._net_rigs: dict = {}       # client: nid -> ghost bastion (for placement)
        self._net_atk_cache: dict = {}  # client: (type,w,h) -> Attack for proj draw
        self._net_accum    = 0.0        # host: snapshot-send throttle accumulator
        # PvP rematch (RE-ENGAGE on the game-over card): a mutual-consent restart.
        # Pressing R flags OUR wish and tells the opponent; once BOTH sides have
        # asked, the host re-inits the authoritative sim and signals the client to
        # reset its view. Cleared each time a rematch actually begins.
        self._rematch_local = False     # this side pressed RE-ENGAGE
        self._rematch_peer  = False     # opponent has asked for a rematch
        self.level      = level
        # Campaign levels the profile has cleared so far — gates the Surge Rush
        # ability (see surge_unlocked). Defaults high so one-off / test battles get
        # it; the campaign passes the real count and the sandbox is always unlocked.
        self.cleared    = cleared
        self.data       = data
        self.sprites    = sprites
        self.difficulty = difficulty
        # "easy" | "normal" | "hard" — selects a boss's exact per-difficulty HP.
        self.difficulty_name = difficulty_name if difficulty_name in (
            "easy", "normal", "hard") else "normal"
        # Sandbox: spawn any ally OR enemy instantly with no cooldowns and free.
        self.sandbox    = sandbox
        self.spawn_team = 'player'      # which side the deploy bar spawns (sandbox toggle)
        # Vehicle keys the player may deploy (campaign unlocks); default = all.
        self.unlocked   = set(unlocked) if unlocked is not None else set(data.vehicles.keys())
        # Vehicle keys the enemy may field — pinned to this level's campaign
        # stage so it never fields units the player hadn't unlocked by here,
        # even on a replay with everything unlocked. Defaults to the player's
        # roster when unspecified (e.g. a one-off battle outside the campaign).
        self.enemy_unlocked = (set(enemy_unlocked) if enemy_unlocked is not None
                               else set(self.unlocked))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(820, 380)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setMouseTracking(True)
        self.clouds = [{"wx": random.uniform(0, WORLD_W), "y": random.uniform(18, 90),
                        "w": random.uniform(90, 170), "h": random.uniform(28, 52)}
                       for _ in range(10)]
        # Time-of-day backdrop, skinned in the main-menu's war-room register and
        # ramped across the campaign: early operations fight under daylight, the
        # late ones at a full menu-dark night (see game.ui.backdrop). Later levels
        # are darker. A level that pins its own water_tint keeps it, and a level may
        # name a special skin (e.g. the "abyss" finale) to bypass the day→night ramp.
        from .ui import backdrop as _backdrop
        self._backdrop = _backdrop.for_level(level.order, level.water_tint,
                                             level.backdrop)
        # Runtime cheat: the ' key toggles infinite player money (seeded from the
        # DEBUG_INFINITE_MONEY config default). Survives a restart.
        self.infinite_money = bool(DEBUG_INFINITE_MONEY) or sandbox
        self._init_state()
        self.cam_x = 0.0; self.wave_t = 0.0
        self.drag_x: Optional[float] = None; self.drag_cam = 0.0
        self._keys: set = set(); self._prev_ms: Optional[int] = None
        # HUD refresh is throttled off the 60 fps sim (see _tick): the console only
        # needs to tick a few times a second, so we coalesce sig_ui to ~20 Hz.
        self._ui_accum = 0.0
        # FPS meter state: frames counted over a short window (see _tick).
        self._fps = 0.0; self._fps_frames = 0; self._fps_accum = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick); self._timer.start(16)

    # ── State ─────────────────────────────────────────────────────────────────
    def _init_state(self):
        self.player = Faction(True); self.enemy = Faction(False)
        # PvP is a fair duel between two humans, so both commanders open with the
        # same purse. (Single-player keeps the player's 180-vs-140 head start: the
        # AI enemy offsets it with a time-ramped income no human enemy ever gets —
        # see the pvp branch in _update.)
        if self.pvp:
            self.enemy.resources = self.player.resources
            # Multiplayer duel: both commanders share a fixed 10k hull. Health
            # upgrades stay pinned off in PvP (see _apply_upgrade), so this value
            # holds for the whole match. Set on both sides; enemy_base_mult is
            # 1.0 in PvP, so the ×mult below leaves this untouched.
            self.player.max_base_hp = self.player.base_hp = 10000.0
            self.enemy.max_base_hp  = self.enemy.base_hp  = 10000.0
        # Sandbox: the player starts with every upgrade maxed out (base HP, income,
        # storage, fleet, warehouse), so downstream setup — armour slots,
        # base HP — reads the max-tier values below.
        if self.sandbox:
            for k in self.player.upgrades:
                self.player.upgrades[k] = MAX_LVL
            self.player.max_base_hp = float(BASEHP_T[MAX_LVL])
            self.player.base_hp     = self.player.max_base_hp
            # Symmetric sandbox: raise the enemy fort to its full height too, so its
            # overwater staircase offers a complete row of mounts for the player to
            # seat enemy fortifications on (see enemy_over_slots / start_placing).
            self.enemy.upgrades['health'] = MAX_LVL
            self.enemy.max_base_hp = float(BASEHP_T[MAX_LVL])
            self.enemy.base_hp     = self.enemy.max_base_hp
        # The enemy base is tougher the further into the campaign you are. The same
        # multiplier scales its health upgrades (see _apply_upgrade) so the level
        # bonus is never wiped out when the AI buys Health.
        # Highest base-upgrade tier researchable this battle (both sides). Early
        # levels can lock off the top tier via the level's max_upgrade_level; the
        # sandbox (everything pre-maxed) always allows the full ladder.
        cap = getattr(self.level, 'max_upgrade_level', None)
        self.max_upg_lvl = MAX_LVL if (self.sandbox or cap is None) \
            else max(1, min(MAX_LVL, cap))
        stage = self.data.campaign_stage(self.level.key)
        # PvP is a level playing field: no campaign HP scaling, so both bases
        # start at the same level-1 hull (see also _apply_upgrade, which keeps
        # base HP pinned to level 1 while armour upgrades still add turret slots).
        self.enemy_base_mult = 1.0 if self.pvp else (1.0 + 0.5 * stage)
        # Campaign scaling stacks on top of the per-tier Health ladder, so on late
        # levels the multiplier could push the hostile hull far past the 25k design
        # ceiling (BASEHP_T's top tier). Clamp it so no enemy base ever exceeds it.
        base_hp_cap = float(BASEHP_T[MAX_LVL])
        self.enemy.max_base_hp = min(self.enemy.max_base_hp * self.enemy_base_mult,
                                     base_hp_cap)
        self.enemy.base_hp = self.enemy.max_base_hp
        self._defenses_placed = False         # enemy base turrets placed on 1st tick
        self.enemy_def_tiers = 0              # extra fort tiers the enemy guns need
        self.ships:       list = []
        self.projectiles: list[Projectile] = []
        self.effects:     list[Explosion] = []
        self.game_over: Optional[str] = None
        # A base at 0 HP first plays a collapse animation before the verdict:
        # `dying` is the losing team while it runs, `dying_win` the winner to
        # declare when it finishes (see _begin_collapse / _update_collapse).
        self.dying: Optional[str] = None
        self.dying_win: Optional[str] = None
        self.dying_t = 0.0
        self._dying_spawn_t = 0.0
        # Downing a boss fires the same bright blast flash as a base collapse, but
        # without freezing the fight — this just counts down while the bloom fades.
        self.boss_flash_t = 0.0
        self.paused = False             # Escape holds the battle (see _draw_pause)
        self.tutorial_freeze = False    # first-run guide is up: turrets held in a
                                        # calm resting pose (see set_tutorial_freeze)
        self.game_time = 0.0
        self.clear_time = 0.0
        self._hover_id = None
        self.boss_ship = None
        self.director = EnemyDirector(self, self.data, self.difficulty)
        # Captain Bob commands the enemy faction in two cases:
        #   • a "play with a bot" match (vs_bot), always; and
        #   • the campaign, once oil rigs have entered the enemy roster — from that
        #     stage on he TAKES OVER from the scripted director, so the late game is
        #     fought against a live, economy-driven opponent that fortifies, upgrades
        #     and pushes exactly as the bot-match Bob does.
        # Built here (not in __init__) so a rematch, which re-runs _init_state, gets a
        # fresh commander. None in every other mode.
        self.bob_campaign = (not self.sandbox and not self.pvp
                             and "oilrig" in self.enemy_unlocked)
        if getattr(self, 'vs_bot', False) or self.bob_campaign:
            from .bot import CaptainBob
            self.bot = CaptainBob(self)
        else:
            self.bot = None
        # Bases are real targets in the ship list (units fire on them).
        self.player_base = BaseTarget('player', self.player, FORT_D_W / 2)
        self.enemy_base  = BaseTarget('enemy', self.enemy, WORLD_W - FORT_D_W / 2)
        # Procedural sea-forts (geometry + rendering); the same mount points feed
        # both the drawing and the turret placement so they can never drift apart.
        self._fort       = FortressView('player')
        self._enemy_fort = FortressView('enemy')
        # Bind each base's hitbox to the fort that draws it, so incoming rounds
        # collide with the visible silhouette (tracking armour tiers) rather than a
        # fixed padded box. The enemy fort also grows tiers to carry its fixed guns.
        self.player_base.bind_fort(self._fort, lambda: self.player.hlv)
        self.enemy_base.bind_fort(self._enemy_fort,
                                  lambda: max(self.enemy.hlv, self.enemy_def_tiers))
        self.ships.append(self.player_base); self.ships.append(self.enemy_base)
        # Fortress placement slots: overwater staircase (grows with armour) + a
        # fixed underwater section (2). Each entry holds its occupant or None.
        self.over_slots: list = [None] * (2 + self.player.hlv)
        self.under_slots: list = [None, None]
        # Mirror slots on the enemy fort — used in sandbox so the player can seat
        # enemy fortifications on the enemy base exactly like their own turrets.
        self.enemy_over_slots:  list = [None] * (2 + self.enemy.hlv)
        self.enemy_under_slots: list = [None, None]
        self.placing = None             # (key, sdef, team) being placed, or None
        # A friendly turret the player left-clicked: shows an on-canvas SELL button
        # (see _draw_sell_button). _sell_rect is that button's screen rect for the
        # click hit-test, recomputed each paint so it tracks the camera.
        self.sel_turret = None
        self._sell_rect = None
        # Clickable pause-menu buttons: list of (QRectF, action) rebuilt each paint
        # while held, so mousePressEvent can dispatch resume / restart / withdraw.
        self._pause_rects: list = []
        # Per-unit production cooldowns (key -> seconds remaining), per team.
        self.build_cd = {"player": {}, "enemy": {}}
        # The FULL cooldown each of those was started with — captured at deploy time
        # (Factory level + any surge ×), so the HUD shows the TRUE cooldown and a
        # later Factory upgrade only shortens the NEXT order, never a unit already
        # cooling. Ticked down alongside build_cd; cleared when a unit comes ready.
        self.build_cd_total = {"player": {}, "enemy": {}}
        # Upgrades in progress per team, keyed by track: {key: {"timer", "total"}}.
        # Each track researches independently — you can advance fleet and income at
        # the same time — but the SAME track can't be queued twice at once. Upgrades
        # are bought now and only take effect once their timer elapses.
        self.pending_upg = {"player": {}, "enemy": {}}
        # Buffered spawns released one at a time (SPAWN_STAGGER apart) so waves and
        # rapid clicks trickle onto the field instead of stacking on one spot.
        self.spawn_buffer  = {"player": [], "enemy": []}
        self.spawn_release = {"player": 0.0, "enemy": 0.0}
        # Per-unit deploy queue: pressing a unit that's on cooldown buys it now and
        # deploys it when the cooldown ends (max 10 stacks per unit type).
        self.unit_queue: dict = {}
        # Parallel FIFO ledger of what was actually PAID for each queued stack (in
        # buy order), so a right-click dequeue can hand back exactly that — the price
        # may differ per hull (bulk discount). Oldest deploys first (pop(0)); a
        # dequeue cancels the most-recent purchase (pop()).
        self.unit_queue_cost: dict = {}
        # Parallel "rush" queue for hulls bought while surging (CTRL held). Shown as a
        # RED tally beside the phosphor one; each rush hull cost SURGE_COST_MULT× and
        # releases ahead of the standard queue, rebuilding in 1/SURGE_CD_MULT of the
        # normal cooldown. Same {key:count} + FIFO cost-ledger shape as the pair above.
        self.unit_rush_queue: dict = {}
        self.unit_rush_queue_cost: dict = {}
        # "Surge Rush" ability: there is no toggle — holding CTRL engages it live
        # (see surge_active). While surging, unit prices rise SURGE_COST_MULT× and a
        # ready hull rebuilds in 1/SURGE_CD_MULT of its normal cooldown. Unlocks at
        # SURGE_UNLOCK_LEVEL (see surge_unlocked). Read straight from the global
        # modifier state, so it carries no per-battle flag of its own.
        self.coastal_aim = 0.72        # radians (~41°)
        # Capturable oil platforms (see game.entities.capturepoint).
        self.capture_cfg = load_capture_config()
        self._capture_falloff     = float(self.capture_cfg["holdings_falloff"])
        self.capture_points: list = []
        # Score → punishment-boss mechanic. In MULTIPLAYER a SINGLE oil platform
        # stands dead-centre: holding it feeds score to your
        # OPPONENT, and every SCORE_BOSS_INTERVAL the score leader draws an
        # escalating boss down on their own base (see _update_score). Holding it
        # ALSO pays the holder a flat SCORE_HOLD_INCOME gold/sec bounty (credited
        # alongside the campaign rig income below), so pushing for the midfield
        # directly funds a capital ship. The host runs
        # this loop authoritatively; the bosses it spawns ride the normal ship
        # snapshot to the client, and the score/countdown are netsynced for the HUD.
        # The CAMPAIGN instead fields the midfield income rigs (capture_points.json),
        # which are purely economic and carry no punishment bosses. The SANDBOX gets
        # neither — it's a clean-slate arena where you spawn everything by hand.
        self.score_mode = self.pvp
        self.score = {"player": 0.0, "enemy": 0.0}
        # PvP gives both commanders a longer runway before the first flagship (3
        # min), then settles into the standard 1.5-min cycle; the sandbox keeps the
        # short cadence throughout.
        self.score_boss_t = float(SCORE_BOSS_FIRST_PVP if self.pvp
                                  else SCORE_BOSS_INTERVAL)
        self.score_bosses: list = []           # live punishment bosses (either side)
        self._score_ladder = self._build_boss_ladder()   # weakest→strongest boss keys
        self._score_ladder_i = 0               # next rung of the ladder to deploy
        if self.score_mode:
            self._build_score_platform()
        elif not self.sandbox:            # sandbox stays a clean slate: no midfield rigs
            self._build_capture_points()

    def reset(self):
        # In a LAN battle a MID-MATCH local restart would desync the two clients,
        # so it's a no-op here — either side leaves to the menu to end the match.
        # (A post-match RE-ENGAGE instead runs the coordinated _rematch_reset, only
        # after both sides agree — see _request_rematch.) A vs-bot match has no peer
        # to desync, so a local restart is fine and reruns _init_state (fresh bot).
        if self.pvp and not self.vs_bot:
            return
        self._init_state(); self._prev_ms = None; self.cam_x = 0.0
        self.coastal_aim = 0.72

    def _rematch_reset(self):
        """Restart the battle for a PvP rematch. Re-inits the sim state exactly as
        reset() does but is safe to call in PvP because both sides run it in
        lock-step: the host after both players agreed, the client on the host's
        'start' signal. Network state (link, roles, session token) is untouched by
        _init_state, so the live connection carries straight into the new match."""
        self._init_state(); self._prev_ms = None; self.cam_x = 0.0
        self.coastal_aim = 0.72
        self._rematch_local = False
        self._rematch_peer  = False

    def _water_y(self) -> float:
        return self.height() * 0.58

    # ── Deploy / build queue (player) ───────────────────────────────────────────
    def enqueue(self, type_key: str = None):
        """Deploy a unit. If it's ready it deploys at once; if it's on cooldown the
        press BUYS it now and queues it to deploy when the cooldown ends (up to 10
        stacks per unit type). Turrets are click-placed on the fort."""
        if self.game_over or self.paused or self.dying is not None: return
        if type_key is None:
            type_key = self.data.deploy_order[0]
        if type_key in self.data.bosses:            # sandbox: bosses listed as units
            if self.sandbox: self.spawn_boss(type_key)
            return
        if type_key not in self.data.vehicles: return
        if type_key not in self.unlocked: return    # not yet unlocked in the campaign
        sdef = self.data.vehicles[type_key]
        placed = getattr(sdef, 'unit_type', '') in ('turret', 'structure')
        if self.sandbox:
            # Sandbox: spawn this unit for the selected side instantly — no cost,
            # no cooldown, no queue. Click-placed units (turrets / oil rig) are
            # seated by hand on the selected side's fort — including enemy
            # fortifications on the enemy base.
            if placed:
                self.start_placing(type_key, self.spawn_team)
                return
            self._queue_spawn(self.spawn_team, sdef)
            self.sig_ui.emit()
            return
        if placed:
            self.start_placing(type_key); return    # click-placed on the fort / pier
        if self._committed('player') >= self._fleet_cap('player'):
            return                                  # at the unit limit — don't charge, the
                                                    # press is simply ignored until a slot frees

        # Surge (Ctrl) and Bulk (Alt) compose — read both once, up front, so every
        # hull in this press is priced identically to what the HUD showed:
        #   • Bulk  (Alt held)  → ONE press buys a batch of BULK_SIZE hulls; the first
        #     fills the ready slot, the rest stack into the queue.
        #   • Surge (Ctrl held) → every purchase costs more (see deploy_unit_cost) and
        #     the ready-slot fill rebuilds in 1/SURGE_CD_MULT of the usual time.
        #   • Both  → a surged batch: 1.35× price each, BULK_SIZE count, surged cooldown.
        # The per-hull price is deploy_unit_cost() — the same function the HUD calls —
        # and it no longer depends on ready-ness, so the label never lies. Buy as many
        # of the batch as money / fleet cap / stack cap allow.
        surge = self.surge_active()
        bulk  = self.bulk_active()
        cost  = deploy_unit_cost(sdef.cost, surge, bulk)
        count = BULK_SIZE if bulk else 1
        # A bulk press is all-or-nothing: unless money is infinite, the player must
        # be able to afford the WHOLE batch (count × cost) before any hull is bought.
        # Without this the loop below would buy as many hulls as funds allowed and
        # stop mid-batch — a partial bulk order. Fleet/stack caps may still trim the
        # batch, but running out of credits no longer does.
        if bulk and not self.infinite_money and self.player.resources < cost * count:
            return
        bought = False
        for _ in range(count):
            if self._committed('player') >= self._fleet_cap('player'):
                break                               # hit the fleet cap mid-batch
            on_cd  = self.build_cd['player'].get(type_key, 0.0) > 0
            queued = self.unit_queue.get(type_key, 0)
            rushed = self.unit_rush_queue.get(type_key, 0)
            ready  = not on_cd and queued == 0 and rushed == 0

            if not (self.infinite_money or self.player.resources >= cost):
                break
            if not ready:
                # Surged hulls stack into the RED rush queue (extra cost, shorter
                # cooldown on release); everything else into the standard queue.
                q     = self.unit_rush_queue      if surge else self.unit_queue
                qcost = self.unit_rush_queue_cost if surge else self.unit_queue_cost
                have  = rushed if surge else queued
                if have >= 10: break                # stack cap (per queue)
                paid = 0 if self.infinite_money else cost
                if not self.infinite_money: self.player.resources -= cost
                q[type_key] = have + 1               # bought now, deploys after cooldown
                qcost.setdefault(type_key, []).append(paid)
            else:
                if not self.infinite_money: self.player.resources -= cost
                self._queue_spawn('player', sdef)   # released a moment later (staggered)
                self._start_build_cd('player', type_key,
                                      1.0 / SURGE_CD_MULT if surge else 1.0)
            bought = True
        if bought:
            self.sig_ui.emit()

    def surge_unlocked(self) -> bool:
        """Surge Rush is available in the sandbox and once the campaign has reached
        SURGE_UNLOCK_LEVEL (1-based). On a fresh run the level number == cleared + 1,
        so clearing SURGE_UNLOCK_LEVEL-1 levels unlocks it for good."""
        return self.sandbox or (self.cleared + 1) >= SURGE_UNLOCK_LEVEL

    def surge_active(self) -> bool:
        """True ONLY while the player is physically holding CTRL and the ability is
        unlocked. We query the live OS key state (queryKeyboardModifiers) rather than
        keyboardModifiers(), which only reflects the last processed Qt event and gets
        stuck "on" when the CTRL release is lost — a deploy-bar button stealing focus,
        an Alt-Tab, or the Windows Alt-menu quirk. queryKeyboardModifiers reports the
        keys actually down at call time, so the mode drops the instant CTRL is released.
        Drives both the raised prices shown in the HUD and the shortened deploy cooldown."""
        if not self.surge_unlocked():
            return False
        mods = QApplication.queryKeyboardModifiers()
        return bool(mods & Qt.KeyboardModifier.ControlModifier)

    def bulk_active(self) -> bool:
        """True ONLY while the player is physically holding ALT. One deploy press then
        buys a batch of BULK_SIZE hulls at BULK_COST_MULT× price each (a volume
        discount). Like surge_active, we query the live OS key state
        (queryKeyboardModifiers) instead of keyboardModifiers() so the mode never
        sticks on after ALT is released — even when a deploy-bar button owns focus or
        the window lost/regained it. Drives the discounted HUD prices and the batch
        deploy. Always available — no campaign unlock."""
        mods = QApplication.queryKeyboardModifiers()
        return bool(mods & Qt.KeyboardModifier.AltModifier)

    def unit_queued(self, key: str) -> int:
        return self.unit_queue.get(key, 0)

    def unit_rush_queued(self, key: str) -> int:
        return self.unit_rush_queue.get(key, 0)

    def dequeue_refund(self, key: str) -> int:
        """Credits handed back if the player right-clicks to cancel one queued hull
        of `key` right now — the price of the most-recent purchase in the stack.
        Rush hulls are cancelled first (they're the pricier, most-recent buys)."""
        costs = self.unit_rush_queue_cost.get(key) or self.unit_queue_cost.get(key)
        return costs[-1] if costs else 0

    def dequeue(self, type_key: str = None):
        """Right-click a deploy button to cancel the most-recently queued hull of
        that type, refunding every credit it cost (bulk discount / surge premium
        included). The RED rush stack is cancelled first, then the standard queue.
        Only queued hulls are cancellable — one already deploying/deployed is not.
        No-op when nothing of that type is queued."""
        if self.game_over or self.paused or self.dying is not None: return
        if type_key is None:
            return
        # Prefer the rush stack (pricier, bought last); fall back to the standard one.
        if self.unit_rush_queue.get(type_key, 0) > 0:
            queue, ledger = self.unit_rush_queue, self.unit_rush_queue_cost
        elif self.unit_queue.get(type_key, 0) > 0:
            queue, ledger = self.unit_queue, self.unit_queue_cost
        else:
            return
        costs = ledger.get(type_key)
        refund = costs.pop() if costs else 0    # cancel the last purchase
        queue[type_key] -= 1
        if queue[type_key] <= 0:
            del queue[type_key]
            ledger.pop(type_key, None)
        if refund and not self.infinite_money:
            self.player.resources = min(self.player.resources + refund,
                                        float(self.player.max_res))
        sdef = self.data.vehicles.get(type_key)
        name = sdef.name if sdef else type_key
        self.sig_alert.emit(f"CANCELLED {name}  +{refund}")
        self.sig_ui.emit()

    def cmd_dequeue(self, key: str = None):
        """A dequeue order from THIS player's console (right-click on a deploy
        button). The cooldown queue is a host/single-player-only construct — the
        client never accrues one (its deploys resolve straight on the host), so this
        simply applies locally to the player faction."""
        if self.awaiting_peer or self._reconnecting: return
        if self.net_role != 'client':
            self.dequeue(key)

    # ── Multiplayer: team-aware command layer ───────────────────────────────────
    # The HUD/input speak these three verbs; each routes by role. Host and
    # single-player apply to the local faction at once (my_team == 'player'); the
    # client packages the order and sends it to the host, which applies it to the
    # enemy faction authoritatively — the result comes back in the next snapshot.
    def faction(self, team: str):
        return self.player if team == 'player' else self.enemy

    def cmd_deploy(self, key: str = None):
        """A deploy order from THIS player's console (Space / HUD / hotkey)."""
        if self.awaiting_peer or self._reconnecting: return
        if self.net_role != 'client':
            self.enqueue(key)                       # host / SP: full player behaviour
            return
        if key is None:
            key = self.data.deploy_order[0]
        sdef = self.data.vehicles.get(key)
        if sdef is None:
            return
        if getattr(sdef, 'unit_type', '') in ('turret', 'structure'):
            # Placement is resolved on OUR own (mirrored) fort — locally the 'player'
            # side — then the slot index is sent; the host seats it on its enemy fort
            # at the symmetric slot. See start_placing / _try_place.
            self.start_placing(key, 'player')
            return
        self.net.send({"t": "cmd", "a": "deploy", "key": key})

    def cmd_upgrade(self, key: str) -> bool:
        """A base-upgrade order from THIS player's console."""
        if self.awaiting_peer or self._reconnecting: return False
        if self.net_role == 'client':
            self.net.send({"t": "cmd", "a": "upgrade", "key": key})
            return False
        return self.request_upgrade(self.my_team, key)

    def cmd_sell_at(self, mx: float, my: float):
        """Right-click: sell the turret under the cursor on THIS player's fort."""
        if self.awaiting_peer or self._reconnecting: return
        if self.net_role != 'client':
            self.sell_turret_at(mx, my)
            return
        ship = self._my_turret_at(mx, my)
        if ship is not None and getattr(ship, '_nid', None) is not None:
            self.net.send({"t": "cmd", "a": "sell", "nid": ship._nid})

    def _my_turret_at(self, mx, my):
        """Topmost living turret of my own team under screen point (mx, my)."""
        for s in reversed(self.ships):
            if getattr(s, 'team', '') != self.my_team:              continue
            if getattr(getattr(s, 'sdef', None), 'unit_type', '') != 'turret': continue
            if not getattr(s, 'alive', True):                      continue
            sx = s.x - s.disp_w * 0.5 - self.cam_x
            if sx <= mx <= sx + s.disp_w and s.top_y <= my <= s.top_y + s.disp_h:
                return s
        return None

    # ── Host-side: apply an order received from the client (enemy faction) ───────
    def _apply_deploy(self, team: str, key: str):
        """Muster one mobile unit for `team`, honouring roster / fleet cap / cost /
        cooldown — the symmetric counterpart of a single (non-surge) enqueue."""
        sdef = self.data.vehicles.get(key)
        if sdef is None or getattr(sdef, 'unit_type', '') in ('turret', 'structure'):
            return
        roster = self.enemy_unlocked if team == 'enemy' else self.unlocked
        if key not in roster:
            return
        if self._committed(team) >= self._fleet_cap(team):
            return
        if self.build_cd[team].get(key, 0.0) > 0:
            return
        fac = self.faction(team)
        if fac.resources < sdef.cost:
            return
        fac.resources -= sdef.cost
        self._queue_spawn(team, sdef)
        self._start_build_cd(team, key)

    def _apply_place(self, team: str, key: str, kind: str,
                     idx: int = 0, rig_nid=None, node=None):
        """Seat a turret / structure for `team` at a resolved fort slot — the
        network counterpart of the interactive _try_place tail."""
        sdef = self.data.vehicles.get(key)
        roster = self.enemy_unlocked if team == 'enemy' else self.unlocked
        if sdef is None or key not in roster:
            return
        if not self.sandbox and self.build_cd[team].get(key, 0.0) > 0:
            return
        fac = self.faction(team)
        if fac.resources < sdef.cost:
            return
        wy = self._water_y()
        if getattr(sdef, 'unit_type', '') == 'structure':
            if self._has_oilrig(team):
                return
            rx, _ = (self._enemy_rig_pos() if team == 'enemy' else self._rig_pos())
            rig = OilRig(team, rx, sdef); rig.set_water(wy)
            rig.water_deep = self._backdrop.water_tint
            self.ships.append(rig)
        elif kind == 'rig_node':
            rig = next((r for r in self._team_rigs(team)
                        if getattr(r, '_nid', None) == rig_nid), None)
            if (rig is None or node is None or node >= len(rig.node_turrets)
                    or rig.node_turrets[node] is not None
                    or getattr(rig, 'under_construction', False)):
                return
            nx, ny = rig.node_positions()[node]
            ship = Ship(team, nx, sdef); ship.top_y = ny - sdef.display_h
            rig.node_turrets[node] = ship
            self.ships.append(ship)
        else:
            slots = self._under_slots(team) if kind == 'under' else self._over_slots(team)
            if idx >= len(slots) or slots[idx] is not None:
                return
            x, y = self._slot_pos('under' if kind == 'under' else 'over', idx, team)
            ship = Ship(team, x, sdef); ship.top_y = y - sdef.display_h
            ship.y_anchor = ship.top_y - wy
            slots[idx] = ship
            self.ships.append(ship)
        self._start_build_cd(team, key)
        fac.resources -= sdef.cost

    def _apply_sell(self, team: str, nid):
        ship = next((s for s in self.ships if getattr(s, '_nid', None) == nid), None)
        if (ship is not None and getattr(ship, 'team', '') == team
                and getattr(getattr(ship, 'sdef', None), 'unit_type', '') == 'turret'):
            self._sell_turret(ship)

    # ── Host: wait in-game for the relay to pair a joiner into our room ──────────
    # ── PvP rematch (RE-ENGAGE): a mutual-consent post-match restart ─────────
    def _request_rematch(self):
        """This side pressed RE-ENGAGE after a PvP match. Flag our own wish and
        tell the opponent. The host coordinates: once BOTH sides have asked it
        restarts the authoritative sim and signals the client (see
        _maybe_start_rematch / _on_net_rematch)."""
        if not self.pvp or not self.game_over:
            return
        if self._rematch_local:
            return                                     # already asked — ignore repeats
        self._rematch_local = True
        if self.net is not None and self.net.alive:
            self.net.send({"t": "rematch"})
        self.sig_alert.emit("REMATCH REQUESTED — AWAITING OPPONENT")
        self._maybe_start_rematch()

    def _maybe_start_rematch(self):
        """Host-only: begin the rematch the moment both sides have asked."""
        if self.net_role != 'host':
            return
        if self._rematch_local and self._rematch_peer:
            self._begin_rematch()

    def _begin_rematch(self):
        """Host: both commanders agreed — tell the client to reset, then restart
        our own authoritative sim. The 'start' goes out before the reset so it
        arrives ahead of the first fresh snapshot."""
        if self.net is not None and self.net.alive:
            self.net.send({"t": "rematch", "a": "start"})
        self._rematch_reset()
        self.sig_alert.emit("REMATCH — ENGAGING")
        self.sig_ui.emit()

    def _on_net_rematch(self, msg):
        """A rematch message from the opponent. The host's explicit 'start' resets
        the client's view; a bare request records their wish (and, on the host,
        may trigger the restart once we've asked too)."""
        if not self.pvp:
            return
        if msg.get("a") == "start":
            if self.net_role == 'client':              # host restarted — follow it
                self._rematch_reset()
                self.sig_alert.emit("REMATCH — ENGAGING")
                self.sig_ui.emit()
            return
        self._rematch_peer = True
        if self.net_role == 'host':
            self._maybe_start_rematch()
        else:
            self.sig_alert.emit("OPPONENT WANTS A REMATCH — [R] TO ACCEPT")
        self.sig_ui.emit()

    # ── Host: wait in-game for the relay to pair a joiner into our room ──────
    def _await_pump(self):
        self._await_ticks = getattr(self, "_await_ticks", 0) + 1
        p = self._net_pending
        if p is None:
            self.awaiting_peer = False
            return
        if p.error:
            self._await_error = p.error              # overlay shows it; Esc leaves
            return
        link = p.poll()
        if link is not None:
            self.net = link
            self._net_pending = None
            self.awaiting_peer = False
            self._auth_started_ms = QDateTime.currentMSecsSinceEpoch()
            if self._peer_authed:
                # Open room: the host tick sends the 'hello' (level + session token)
                # on the next frame now that the link is live; the fight begins here.
                self.sig_peer_joined.emit()
            # Locked room: stay held until the joiner proves the room password —
            # see _await_auth(), driven from tick() while _peer_authed is False.

    # ── Host: gate a locked room on the joiner proving the room password ─────────
    AUTH_SECS = 8            # a paired joiner must prove the password within this window

    def _await_auth(self):
        """A joiner has been paired into our LOCKED room. Admit them only once they
        present the correct room password over the link — host-authoritative, so a
        dumb/old relay that skips its own password check can't let anyone through,
        and no 'hello' (nor the reconnect token) is revealed until they pass. A wrong
        password, or silence past AUTH_SECS, drops them and re-opens the room so a
        legitimate opponent can still join."""
        if self.net is None or not self.net.alive:
            self._reopen_room("the joiner dropped before entering the password")
            return
        for msg in self.net.poll():
            if msg.get("t") != "auth":
                continue                                # ignore anything before auth
            if msg.get("pw", "") == self._net_password:
                self._peer_authed = True                # correct — the match begins now
                self.sig_peer_joined.emit()
            else:
                self._reject_peer("wrong password")
            return
        if QDateTime.currentMSecsSinceEpoch() - self._auth_started_ms > self.AUTH_SECS * 1000:
            self._reject_peer("timed out waiting for the room password")

    def _reject_peer(self, reason: str):
        """Turn away a joiner who failed the room-password check, then re-open."""
        if self.net is not None:
            try:
                self.net.send({"t": "denied", "msg": reason})
            except Exception:
                pass
            self.net.close()
        self.net = None
        self._reopen_room(reason)

    def _reopen_room(self, reason: str = ""):
        """Re-list the locked room on the relay and go back to 'awaiting player', so a
        rejected/timed-out joiner never ends the host's session."""
        from . import net as _net
        host, port = _net.relay_endpoint()
        self._net_pending = _net.NetRelayHost(
            host, port, name=self._net_room_name or "Room",
            map_=self.level.name, password=self._net_password)
        self._net_pending.start()
        self.awaiting_peer = True
        self._peer_authed  = not bool(self._net_password)
        self._hello_sent   = False
        self._await_error  = None
        if reason:
            self.sig_alert.emit(f"JOIN REJECTED — {reason.upper()}")
            self.sig_ui.emit()

    # ── Transport: pump the link, apply orders / snapshots, broadcast state ──────
    def _net_pump(self):
        if self.net is None:
            return
        if not self.net.alive:
            self._on_net_lost(); return
        for msg in self.net.poll():
            self._handle_net(msg)

    def _handle_net(self, msg):
        t = msg.get("t")
        if t == "rematch":                              # RE-ENGAGE coordination
            self._on_net_rematch(msg); return
        if self.net_role == 'host':
            if t == "ident":                            # client → host: its name
                nm = str(msg.get("name", "")).strip()[:16]
                if nm:
                    self.opponent_name = nm
                    self.sig_ui.emit()
                return
            if t != "cmd" or self.game_over:
                return
            a = msg.get("a")
            if a == "deploy":
                self._apply_deploy('enemy', msg.get("key"))
            elif a == "place":
                self._apply_place('enemy', msg.get("key"), msg.get("kind"),
                                  idx=msg.get("idx", 0), rig_nid=msg.get("rig"),
                                  node=msg.get("node"))
            elif a == "upgrade":
                self.request_upgrade('enemy', msg.get("key"))
            elif a == "sell":
                self._apply_sell('enemy', msg.get("nid"))
            self.sig_ui.emit()
        else:                                           # client
            if t == "snap":
                from . import netsync
                netsync.apply(self, msg)
            elif t == "alert":
                self.sig_alert.emit(msg.get("text", ""))
            elif t == "boss_warn":
                # Host: a punishment boss was called down. Fire the same flashing
                # klaxon here, with the target named from the client's own side.
                self.sig_boss.emit("strike:" + msg.get("text", ""))
            elif t == "collapse" and self.dying is None and not self.game_over:
                # Host: a base is collapsing. Mirror the winner into our frame and
                # play the same fort-death animation locally before the verdict.
                who = msg.get("who", "player")
                win = "enemy" if who == "player" else "player" if who == "enemy" else who
                self._begin_collapse("player" if win == "enemy" else "enemy", win)
            elif t == "over" and not self.game_over and self.dying is None:
                # 'who' is in the host's frame; mirror it into ours so our own
                # victory reads as a 'player' win (see my_team / _draw_game_over).
                # If a collapse is already playing it will end the match itself;
                # this is the fallback path when the collapse cue was missed.
                who = msg.get("who", "player")
                self._end("enemy" if who == "player" else "player" if who == "enemy" else who)

    # Host broadcasts a world snapshot ~30×/s (a fresh sim frame every ~2 ticks) —
    # ample for smooth LAN play without flooding the link with 60 Hz state.
    _SNAP_DT = 1.0 / 30.0

    def _net_broadcast(self, dt: float):
        if self.net is None or not self.net.alive:
            return
        self._net_accum += dt
        if self._net_accum < self._SNAP_DT:
            return
        self._net_accum = 0.0
        from . import netsync
        self.net.send(netsync.serialize(self))

    def _client_update(self, dt: float):
        """The client runs no simulation — it only advances local-only visuals
        (explosion animation) and its own camera; the world comes from snapshots."""
        if self.dying is not None:               # host signalled a base collapse —
            self._update_collapse(dt); return    # play it locally, then the verdict
        # Wakes are cosmetic and never networked — the client peels its own off the
        # snapshot ships' motion, so both screens show a wake behind every ship.
        if not SETTINGS.no_effects:
            self._spawn_wakes(self._water_y())
        for e in self.effects:
            e.update(dt)
        self.effects = [e for e in self.effects if e.alive]
        self.wave_t += dt
        spd = 500.0
        if any(k in self._keys for k in SETTINGS.keys_for("pan_left")):
            self.cam_x = max(0.0, self.cam_x - spd * dt)
        if any(k in self._keys for k in SETTINGS.keys_for("pan_right")):
            self.cam_x = min(WORLD_W - self.width(), self.cam_x + spd * dt)

    # ── Ship wakes ───────────────────────────────────────────────────────────────
    # Spacing along a hull's track between successive foam ripples. A ship emits one
    # each time it has made this many pixels of headway, so the wake density follows
    # the distance travelled (and is thus frame-rate independent) rather than time.
    _WAKE_STEP = 19.0

    def _spawn_wakes(self, wy: float):
        """Peel a foam ripple off the stern of every surface ship that is making
        way. Runs on the host AND the client off each side's own ship motion, so it
        needs no networking — a purely local, cosmetic trail. Only water craft that
        ride the surface leave one: planes, turrets, forts, rigs, submarines and
        air/sub bosses are all skipped. The ripple is laid at the waterline behind
        the hull and left in the water as the ship sails on (see entities.Wake)."""
        for s in self.ships:
            if not getattr(s, 'alive', True):
                continue
            if getattr(s, 'is_base', False) or getattr(s, 'is_rig', False):
                continue
            sd = getattr(s, 'sdef', None)
            if sd is None:
                continue
            if getattr(sd, 'unit_type', 'ship') in ('plane', 'turret', 'structure', 'submarine'):
                continue
            if getattr(s, 'boss_type', None) in ('air', 'sub'):     # only surface/carrier bosses
                continue
            if getattr(s, 'entering', False) or getattr(s, 'rising', False):
                continue

            prev = getattr(s, '_wake_prev_x', None)
            if prev is None:
                s._wake_prev_x = s.x; s._wake_accum = 0.0
                continue
            dx = s.x - prev
            s._wake_prev_x = s.x
            if abs(dx) < 0.05:                         # holding station — no wake
                s._wake_accum = 0.0
                continue
            acc = getattr(s, '_wake_accum', 0.0) + abs(dx)
            if acc < self._WAKE_STEP:
                s._wake_accum = acc
                continue
            s._wake_accum = 0.0
            # Lay the ripple off the stern (the hull edge trailing its travel) and
            # let it drift slowly astern so it stays put in the sea as the ship goes.
            heading = 1.0 if dx >= 0 else -1.0
            stern_x = s.x - heading * s.disp_w * 0.42
            size    = max(0.7, min(2.4, s.disp_w / 120.0))
            self.effects.append(Wake(stern_x, wy, size=size, drift=-heading * 6.0))

    # ── Host: announce the match once, when the link first goes live ─────────────
    def _send_hello(self):
        """Host → client handshake: the level (so the client builds the same map),
        the session token (so a later disconnect can rendezvous to reconnect) and
        the host's commander name (shown on the client's HOSTILE gauge).
        Idempotent — fires exactly once per match, never on a reconnect."""
        if self._hello_sent or self.net is None or not self.net.alive:
            return
        if not self._peer_authed:
            return                      # never reveal the token until the joiner auths
        self.net.send({"t": "hello", "level": self.level.key,
                       "session": self._net_session,
                       "name": self.player_name or ""})
        self._hello_sent = True

    def _send_ident(self):
        """Client → host handshake: the client's commander name, so the host can
        label its HOSTILE gauge. Idempotent — sent once, when the link is live."""
        if self._ident_sent or self.net is None or not self.net.alive:
            return
        self.net.send({"t": "ident", "name": self.player_name or ""})
        self._ident_sent = True

    # ── Disconnect → 10-second reconnect grace ───────────────────────────────────
    RECONNECT_SECS = 10

    def _on_net_lost(self):
        """The live link dropped. If this is a PvP match with a known session token,
        freeze and open a 10-second window in which both sides rendezvous through
        the relay to resume; otherwise fall back to the old 'connection lost' hold."""
        if self.game_over or self._reconnecting:
            return
        if not self.pvp or not self._net_session:
            if not getattr(self, '_net_lost', False):
                self._net_lost = True
                self.paused = True
                self.sig_alert.emit("CONNECTION LOST — OPPONENT DISCONNECTED")
                self.sig_ui.emit()
            return
        self.net = None
        self._reconnecting = True
        self._reconnect_started_ms = QDateTime.currentMSecsSinceEpoch()
        from . import net as _net
        host, port = _net.relay_endpoint()
        # Roles are fixed for the match: the host re-opens the hidden reconnect slot
        # (keyed by the session token); the client keeps retrying to rejoin it.
        if self.net_role == 'host':
            self._reconnector = _net.NetRelayHost(host, port, reconnect=self._net_session)
        else:
            self._reconnector = _net.NetReconnectClient(host, self._net_session, port)
        self._reconnector.start()
        self.sig_alert.emit("CONNECTION LOST — RECONNECTING…")
        self.sig_ui.emit()

    def _reconnect_pump(self):
        """Called each frozen frame during the grace window: adopt the fresh link
        the moment we rendezvous, or end the match if the window elapses."""
        r = self._reconnector
        if r is not None:
            link = r.poll()
            if link is not None:
                self.net = link
                self._reconnector = None
                self._reconnecting = False
                self._net_lost = False
                self.sig_alert.emit("RECONNECTED")
                self.sig_ui.emit()
                return
        elapsed = QDateTime.currentMSecsSinceEpoch() - self._reconnect_started_ms
        if elapsed >= self.RECONNECT_SECS * 1000:
            self._stop_reconnector()
            self._reconnecting = False
            # Opponent never came back — the match ends in our favour (forfeit).
            self._end(self.my_team)

    def _stop_reconnector(self):
        r = self._reconnector
        self._reconnector = None
        if r is None:
            return
        if hasattr(r, 'cancel'):
            r.cancel()
        elif hasattr(r, 'stop'):
            r.stop()

    def _reconnect_remaining(self) -> int:
        elapsed = QDateTime.currentMSecsSinceEpoch() - self._reconnect_started_ms
        return max(0, self.RECONNECT_SECS - int(elapsed / 1000))

    @property
    def opponent_gone(self) -> bool:
        """True while an active PvP match is holding for a dropped opponent — either
        the relay reconnect grace window (_reconnecting) or the non-session fallback
        hold (_net_lost). In this state, walking out isn't a clean exit: it forfeits."""
        return (self.pvp and not self.vs_bot and not self.game_over
                and (self._reconnecting or getattr(self, '_net_lost', False)))

    def _forfeit_disconnect(self):
        """The player chose to abandon a match whose opponent has dropped. You don't
        get to duck the result by leaving — end it here as a loss (the opponent wins),
        which surfaces the DEFEAT card rather than a silent bail to the menu."""
        self._stop_reconnector()
        self._reconnecting = False
        self._net_lost = False
        self.paused = False
        enemy = "enemy" if self.my_team == "player" else "player"
        self._end(enemy)
        self.sig_ui.emit()

    def _release_unit_queue(self):
        """When a unit's cooldown clears, deploy the next stack waiting for it. RED
        rush hulls (bought while surging) release ahead of the standard queue and
        rebuild in 1/SURGE_CD_MULT of the normal cooldown; standard hulls rebuild at
        the full cooldown."""
        for key in set(self.unit_queue) | set(self.unit_rush_queue):
            # Prune emptied stacks in either queue.
            if self.unit_queue.get(key, 0) <= 0:
                self.unit_queue.pop(key, None); self.unit_queue_cost.pop(key, None)
            if self.unit_rush_queue.get(key, 0) <= 0:
                self.unit_rush_queue.pop(key, None)
                self.unit_rush_queue_cost.pop(key, None)
            waiting = self.unit_queue.get(key, 0) + self.unit_rush_queue.get(key, 0)
            if waiting <= 0 or self.build_cd['player'].get(key, 0.0) > 0:
                continue
            rush = self.unit_rush_queue.get(key, 0) > 0   # rush hulls jump the queue
            queue  = self.unit_rush_queue      if rush else self.unit_queue
            ledger = self.unit_rush_queue_cost if rush else self.unit_queue_cost
            sdef = self.data.vehicles.get(key)
            if sdef:
                self._queue_spawn('player', sdef)
                self._start_build_cd('player', key,
                                      1.0 / SURGE_CD_MULT if rush else 1.0)
            queue[key] -= 1
            costs = ledger.get(key)
            if costs: costs.pop(0)                  # oldest purchase deploys first
            if queue[key] <= 0:
                del queue[key]
                ledger.pop(key, None)

    def _factory_cd_mult(self, team: str) -> float:
        fac = self.player if team == 'player' else self.enemy
        lvl = min(fac.upgrades.get('warehouse', 0), len(FACTORY_CD_MULT) - 1)
        return FACTORY_CD_MULT[lvl]

    def _cooldown_total(self, key: str, team: str = 'player') -> float:
        sdef = self.data.vehicles.get(key)
        base = getattr(sdef, 'cooldown', 0.0) if sdef else 0.0
        if key == 'oilrig':
            return base          # Bastion re-placement is a flat cooldown, not factory-scaled
        return base * self._factory_cd_mult(team)

    def _start_build_cd(self, team: str, key: str, mult: float = 1.0):
        if self.sandbox: return         # sandbox: nothing cools down, ever
        cd = self._cooldown_total(key, team) * mult   # mult<1 for a Surge Rush deploy
        if cd > 0:
            self.build_cd[team][key]       = cd
            self.build_cd_total[team][key] = cd       # remember the full cooldown set

    def build_cooldown(self, key: str, team: str = 'player'):
        """(remaining, total) production cooldown for a unit type — for the HUD.
        `total` is the cooldown this unit was ACTUALLY started with (locked in at
        deploy time), so the wipe reads true even after a Factory upgrade or a
        surge-shortened order; only a brand-new order recomputes it against the new Factory."""
        rem = self.build_cd[team].get(key, 0.0)
        total = self.build_cd_total[team].get(key) or self._cooldown_total(key, team)
        return rem, total

    # ── Upgrades (bought now, applied after a research delay) ────────────────────
    def request_upgrade(self, team: str, key: str) -> bool:
        """Pay for and START an upgrade. It only takes effect after UPGRADE_TIME
        seconds. Tracks research independently — one in progress PER TRACK, so
        different tracks (e.g. fleet + income) can run at the same time."""
        fac = self.player if team == 'player' else self.enemy
        if team == 'player' and (self.paused or self.dying is not None): return False  # held — no orders
        if key not in fac.upgrades: return False
        if key in self.pending_upg[team]: return False               # this track already researching
        lv = fac.upgrades[key]
        if lv >= self.max_upg_lvl: return False
        cost = UPGRADE_COSTS[key][lv]
        free = self.infinite_money and team == 'player'
        if not free and fac.resources < cost: return False
        if not free: fac.resources -= cost
        t = UPGRADE_TIME[min(lv, len(UPGRADE_TIME) - 1)]
        self.pending_upg[team][key] = {"timer": t, "total": t}
        self.sig_ui.emit()
        return True

    def _apply_upgrade(self, team: str, key: str):
        fac = self.player if team == 'player' else self.enemy
        if fac.upgrades[key] >= self.max_upg_lvl: return
        fac.upgrades[key] += 1
        if key == "health" and not self.pvp:
            # Campaign: armour raises both turret-slot count (2 + hlv) and hull HP.
            # In PvP we skip the HP bump — the higher hlv still adds a turret slot
            # (see _sync_slots), but every base stays pinned to its level-1 hull.
            mult = self.enemy_base_mult if team == 'enemy' else 1.0
            # Match _init_state's clamp: campaign scaling must never lift a hull
            # past the 25k ceiling. Clamping both tiers means once the base is at
            # the cap, buying Health adds a turret slot but no further HP.
            cap = float(BASEHP_T[MAX_LVL])
            nm = min(float(BASEHP_T[fac.upgrades["health"]]) * mult, cap)
            pm = min(float(BASEHP_T[fac.upgrades["health"] - 1]) * mult, cap)
            fac.max_base_hp = nm; fac.base_hp = min(fac.base_hp + (nm - pm), nm)

    def _tick_upgrades(self, dt: float):
        for team, tracks in self.pending_upg.items():
            for key in list(tracks):           # copy: keys are removed as they finish
                pu = tracks[key]
                pu["timer"] -= dt
                if pu["timer"] <= 0:
                    self._apply_upgrade(team, key)
                    del tracks[key]

    def upgrade_progress(self, key: str, team: str = 'player'):
        """(remaining, total) if this upgrade is researching, else None — for HUD."""
        pu = self.pending_upg[team].get(key)
        if pu:
            return pu["timer"], pu["total"]
        return None

    def _place_enemy_defenses(self, wy: float):
        """Seat the enemy base's fixed turret defences at the start of the battle
        (mirrored onto the enemy fort). These are standing fortifications — present
        from the first second, growing with the campaign level, never added later.

        Each gun is seated on a real fort mount (exactly like the player's coastal
        turrets) so it stands on a visible platform instead of floating in the air."""
        stage = self.data.campaign_stage(self.level.key)
        loadout = ['torpedo_battery', 'aa_turret']          # every level is defended
        if stage >= 4: loadout += ['torpedo_battery', 'missile_battery']
        if stage >= 6: loadout += ['aa_turret']
        if stage >= 8: loadout += ['missile_battery']
        surf, under = [], []
        for key in loadout:
            sdef = self.data.vehicles.get(key)
            if not sdef:
                continue
            (under if getattr(sdef, 'turret_slot', 'surface') == 'underwater'
             else surf).append(sdef)
        surf = surf[:3]; under = under[:2]        # never more than the fort can seat
        # Grow the enemy fort's overwater staircase so every surface gun has a tier
        # to stand on; _draw_bases reads this so drawing and seating stay in lockstep.
        self.enemy_def_tiers = max(0, len(surf) - 2)
        hlv = max(self.enemy.hlv, self.enemy_def_tiers)
        s_mounts = self._enemy_fort.surface_mounts(wy, hlv)
        u_mounts = self._enemy_fort.underwater_mounts(wy)
        # These fixed guns must OCCUPY the enemy fort's slot arrays, exactly like the
        # player's coastal turrets do (see _place_player_defenses). In the late
        # campaign a live commander (CaptainBob) fights the enemy faction and reads
        # these slots to find a free mount; if the fixed guns aren't registered it
        # treats every mount as empty and stacks new turrets right on top of them.
        # Grow the surface-slot array so it can hold every extra tier these guns need.
        while len(self.enemy_over_slots) < len(s_mounts):
            self.enemy_over_slots.append(None)
        for i, sdef in enumerate(surf):
            x, y = s_mounts[i]
            ship = Ship('enemy', x, sdef); ship.top_y = y - sdef.display_h
            ship.y_anchor = ship.top_y - wy       # keep it pinned to the pad on resize
            self.ships.append(ship)
            self.enemy_over_slots[i] = ship       # claim the mount so nothing stacks on it
        for j, sdef in enumerate(under):
            x, y = u_mounts[j]
            ship = Ship('enemy', x, sdef); ship.top_y = y - sdef.display_h
            ship.y_anchor = ship.top_y - wy
            self.ships.append(ship)
            self.enemy_under_slots[j] = ship      # claim the underwater step too

    def _place_player_defenses(self, wy: float):
        """The allied fort always musters two coastal-artillery turrets, seated on
        its two front surface mounts from the first second (aim them with ↑/↓)."""
        sdef = self.data.vehicles.get('coastal_artillery')
        if not sdef:
            return
        mounts = self._fort.surface_mounts(wy, self.player.hlv)
        for i in range(min(2, len(mounts), len(self.over_slots))):
            if self.over_slots[i] is not None:
                continue
            x, y = mounts[i]
            ship = Ship('player', x, sdef); ship.top_y = y - sdef.display_h
            ship.y_anchor = ship.top_y - wy       # keep it pinned to the pad on resize
            ship.aim_angle = self.coastal_aim
            self.ships.append(ship)
            self.over_slots[i] = ship

    def _place_pvp_start(self, wy: float):
        """PvP start: each side musters exactly one missile turret and nothing
        else — a perfectly symmetric opening. Both are seated on their fort's
        front surface mount and registered in that side's over-slot."""
        sdef = self.data.vehicles.get('missile_battery')
        if not sdef:
            return
        for team, fort, hlv, slots in (
                ('player', self._fort,       self.player.hlv, self.over_slots),
                ('enemy',  self._enemy_fort, self.enemy.hlv,  self.enemy_over_slots)):
            mounts = fort.surface_mounts(wy, hlv)
            if not mounts or not slots:
                continue
            x, y = mounts[0]
            ship = Ship(team, x, sdef); ship.top_y = y - sdef.display_h
            ship.y_anchor = ship.top_y - wy       # keep it pinned to the pad on resize
            ship.aim_angle = self.coastal_aim
            self.ships.append(ship)
            slots[0] = ship

    # ── Fortress placement slots ────────────────────────────────────────────────
    def _team_fort(self, team: str):
        return self._enemy_fort if team == 'enemy' else self._fort

    def _over_slots(self, team: str):
        return self.enemy_over_slots if team == 'enemy' else self.over_slots

    def _under_slots(self, team: str):
        return self.enemy_under_slots if team == 'enemy' else self.under_slots

    def _slot_pos(self, kind: str, idx: int, team: str = 'player'):
        """World pad-surface position of a surface (over) / underwater slot."""
        wy = self._water_y()
        # The enemy fort can stand taller than its Health tier when fixed defensive
        # guns need extra steps (enemy_def_tiers); use that same effective height the
        # fort is drawn at, so a bot-placed turret lands on a real (distinct) mount
        # rather than clamping onto one already carrying a gun.
        if team == 'enemy':
            hlv = max(self.enemy.hlv, self.enemy_def_tiers)
        else:
            hlv = self.player.hlv
        if kind == 'over':
            mounts = self._team_fort(team).surface_mounts(wy, hlv)
        else:
            mounts = self._team_fort(team).underwater_mounts(wy)
        return mounts[min(idx, len(mounts) - 1)]

    def _rig_pos(self, team: str = 'player'):
        return self._team_fort(team).rig_mount(self._water_y())

    def _sync_slots(self):
        # Armour upgrades add overwater steps; free slots whose occupant died.
        # Both forts are kept in step so sandbox enemy fortifications behave
        # exactly like the player's own (grow with armour, free on death).
        for fac, over, under in (
                (self.player, self.over_slots, self.under_slots),
                (self.enemy, self.enemy_over_slots, self.enemy_under_slots)):
            want = 2 + fac.hlv
            while len(over) < want: over.append(None)
            for arr in (over, under):
                for i, occ in enumerate(arr):
                    if occ is not None and not getattr(occ, 'alive', False):
                        arr[i] = None
        # Bastion turret nodes: keep each mounted turret seated on the deck, and
        # free a node when its turret dies.
        for rig in self._team_rigs('player') + self._team_rigs('enemy'):
            positions = rig.node_positions()
            for ni, nt in enumerate(rig.node_turrets):
                if nt is None:
                    continue
                if not getattr(nt, 'alive', False):
                    rig.node_turrets[ni] = None
                else:
                    nx, ny = positions[ni]
                    nt.x = nx; nt.top_y = ny - nt.sdef.display_h

    def _team_rigs(self, team: str):
        return [s for s in self.ships if getattr(s, 'is_rig', False)
                and getattr(s, 'team', '') == team and s.alive]

    def _has_oilrig(self, team: str = 'player') -> bool:
        return bool(self._team_rigs(team))

    def placement_targets(self):
        """Empty, valid (x, y, kind, idx) slots for whatever is being placed."""
        if not self.placing: return []
        key, sdef, team = self.placing
        out = []
        if getattr(sdef, 'unit_type', '') == 'structure':     # the oil rig
            if not self._has_oilrig(team):
                x, _ = self._rig_pos(team); out.append((x, self._water_y() - 30, 'rig', 0))
            return out
        slot = getattr(sdef, 'turret_slot', 'surface')
        if slot == 'underwater':
            for i, occ in enumerate(self._under_slots(team)):
                if occ is None:
                    x, y = self._slot_pos('under', i, team); out.append((x, y, 'under', i))
        else:
            for i, occ in enumerate(self._over_slots(team)):
                if occ is None:
                    x, y = self._slot_pos('over', i, team); out.append((x, y, 'over', i))
            # A surface turret may also go on a free Bastion deck node — but not
            # while the platform is still being raised.
            for ri, rig in enumerate(self._team_rigs(team)):
                if getattr(rig, 'under_construction', False):
                    continue
                positions = rig.node_positions()
                for ni, nt in enumerate(rig.node_turrets):
                    if nt is None:
                        nx, ny = positions[ni]
                        out.append((nx, ny, 'rig_node', (ri, ni)))
        return out

    def start_placing(self, key: str, team: str = 'player'):
        if self.dying is not None: return                # base collapsing — no orders
        sdef = self.data.vehicles.get(key)
        roster = self.enemy_unlocked if team == 'enemy' else self.unlocked
        if not sdef or key not in roster: return
        if getattr(sdef, 'unit_type', '') == 'structure':
            if self._has_oilrig(team): return
        if not self.sandbox and self.build_cd[team].get(key, 0.0) > 0:
            return                                                 # still cooling down
        fac = self.faction(team)
        free = self.infinite_money and team == 'player'
        if not (free or fac.resources >= sdef.cost): return
        self.placing = (key, sdef, team)
        self.sel_turret = None; self._sell_rect = None   # placing dismisses the SELL button
        self.sig_ui.emit()

    def cancel_placing(self):
        if self.placing: self.placing = None; self.sig_ui.emit()

    def _try_place(self, mx, my) -> bool:
        targets = self.placement_targets()
        best = None; bd = 9999.0
        for (x, y, kind, idx) in targets:
            d = math.hypot((x - self.cam_x) - mx, y - my)
            if d < bd: bd, best = d, (x, y, kind, idx)
        if best is None or bd > 60:
            self.cancel_placing(); return True       # click off a slot → cancel
        x, y, kind, idx = best
        key, sdef, team = self.placing
        # Client (P2): placement is resolved against the local snapshot view, then
        # sent to the host to apply authoritatively — the result returns in a snap.
        if self.net_role == 'client':
            msg = {"t": "cmd", "a": "place", "key": key, "kind": kind}
            if kind == 'rig_node':
                ri, ni = idx
                rigs = self._team_rigs(team)
                msg["rig"]  = getattr(rigs[ri], '_nid', -1) if ri < len(rigs) else -1
                msg["node"] = ni
            else:
                msg["idx"] = idx
            self.net.send(msg)
            self.placing = None; self.sig_ui.emit()
            return True
        fac = self.enemy if team == 'enemy' else self.player
        if getattr(sdef, 'unit_type', '') == 'structure':         # the oil rig
            rx, _ = self._rig_pos(team)
            rig = OilRig(team, rx, sdef); rig.set_water(self._water_y())
            rig.water_deep = self._backdrop.water_tint
            self.ships.append(rig)
            self._start_build_cd(team, key)
            if not self.infinite_money: fac.resources -= sdef.cost
        else:
            # Seat the turret so its base rests on the pad surface (y).
            ship = Ship(team, x, sdef); ship.top_y = y - sdef.display_h
            self.ships.append(ship)
            if kind == 'rig_node':
                rigs = self._team_rigs(team)
                ri, ni = idx
                if ri < len(rigs) and ni < len(rigs[ri].node_turrets):
                    rigs[ri].node_turrets[ni] = ship
            else:
                (self._under_slots(team) if kind == 'under'
                 else self._over_slots(team))[idx] = ship
                # Fort-pad turret: pin it to the pad so it tracks the fort on resize.
                ship.y_anchor = ship.top_y - self._water_y()
            self._start_build_cd(team, key)
            if not self.infinite_money: fac.resources -= sdef.cost
        self.placing = None; self.sig_ui.emit()
        return True

    # ── Selling placed turrets ───────────────────────────────────────────────────
    def _friendly_turret_at(self, mx, my) -> Optional[Ship]:
        """Topmost living *player* turret whose sprite is under the screen point
        (mx, my), or None. Mirrors the hit test used for the hover readout."""
        for s in reversed(self.ships):
            if getattr(s, 'team', '') != 'player':                continue
            if getattr(s, 'sdef', None) is None:                 continue
            if getattr(s.sdef, 'unit_type', 'ship') != 'turret': continue
            if not getattr(s, 'alive', True):                    continue
            sx = s.x - s.disp_w * 0.5 - self.cam_x
            sy = s.top_y
            if sx <= mx <= sx + s.disp_w and sy <= my <= sy + s.disp_h:
                return s
        return None

    def _free_turret_slot(self, ship) -> None:
        """Release whatever mount this turret occupied (fort surface / underwater
        pad or a Bastion deck node) so the slot can take a new turret."""
        for slots in (self.over_slots, self.under_slots,
                      self.enemy_over_slots, self.enemy_under_slots):
            for i, occ in enumerate(slots):
                if occ is ship:
                    slots[i] = None
        for rig in self.ships:
            nodes = getattr(rig, 'node_turrets', None)
            if not nodes:
                continue
            for i, nt in enumerate(nodes):
                if nt is ship:
                    nodes[i] = None

    @staticmethod
    def sell_price(ship) -> int:
        """The refund for selling `ship`: SELL_FRACTION of what it cost."""
        return int(round(ship.sdef.cost * SELL_FRACTION))

    def _sell_turret(self, ship) -> bool:
        """Sell `ship`, refunding sell_price() into the player's coffers. Shared by
        the on-canvas SELL button and the right-click shortcut."""
        if self.game_over or self.paused or self.dying is not None: return False
        refund = self.sell_price(ship)
        fac = self.faction(getattr(ship, 'team', 'player'))
        if not (self.infinite_money and ship.team == 'player'):
            fac.resources = min(fac.resources + refund, float(fac.max_res))
        ship.alive = False
        self._free_turret_slot(ship)
        try:    self.ships.remove(ship)
        except ValueError: pass
        if self.sel_turret is ship:
            self.sel_turret = None; self._sell_rect = None
        self.sig_alert.emit(f"SOLD {ship.sdef.name}  +{refund}")
        self.sig_ui.emit()
        return True

    def sell_turret_at(self, mx, my) -> bool:
        """Right-click shortcut: sell the friendly turret under the cursor. Returns
        True if a turret was sold."""
        ship = self._friendly_turret_at(mx, my)
        if ship is None: return False
        return self._sell_turret(ship)

    # ── Off-screen spawning ─────────────────────────────────────────────────────
    def _spawn_mobile(self, team: str, sdef) -> Ship:
        """Create a mobile unit just beyond the map edge so it enters from
        off-screen — the player never sees it pop into existence."""
        wy = self._water_y()
        is_enemy = (team == 'enemy')
        d = -1 if is_enemy else 1
        is_plane = getattr(sdef, 'unit_type', 'ship') == 'plane'
        # Everything starts off the map edge (a small random push so a batch fans
        # out instead of stacking on one spot).
        spread = random.uniform(0, 60.0)
        x = (float(WORLD_W + sdef.display_w * 0.5 + 30 + spread) if is_enemy
             else float(-sdef.display_w * 0.5 - 30 - spread))
        ship = Ship(team, x, sdef)
        ship.plane_dir = d; ship.facing = float(d)
        if is_enemy:
            # Difficulty's main lever: tougher (or softer) enemy hulls.
            ship.max_hp *= self.difficulty
            ship.hp = ship.max_hp
        # Planes fly in from off-screen via the `entering` handler: the plane AI
        # clamps x to the playfield edge on frame one, so without this they'd snap
        # straight onto the field and pop into mid-air. Other mobile units enter at
        # the waterline and just act under normal AI straight away.
        ship.entering = is_plane
        if is_plane:
            ship.top_y = wy - PLANE_ALT - sdef.display_h * 0.5
        self.ships.append(ship)
        return ship

    def spawn_enemy(self, key: str):
        """Director hook: send one enemy unit in from off-screen (buffered so a
        whole wave trickles in over time rather than stacking at the edge)."""
        sdef = self.data.vehicles.get(key)
        if sdef is not None:
            self._queue_spawn('enemy', sdef)
            self._start_build_cd('enemy', key)

    def _boss_hp(self, sdef) -> float:
        """A boss's HP for the active difficulty: its exact per-difficulty value
        when the JSON gives one, else the legacy `hp` × difficulty multiplier."""
        bucket = {"easy": sdef.hp_easy, "normal": sdef.hp_normal,
                  "hard": sdef.hp_hard}.get(self.difficulty_name, 0)
        if bucket and bucket > 0:
            return float(bucket)
        return float(sdef.hp) * self.difficulty

    def spawn_boss(self, boss_key: str):
        """Sandbox: drop an enemy boss onto the field (it marches on the player
        base). Multiple bosses can coexist; each fights independently."""
        sdef = self.data.bosses.get(boss_key)
        if sdef is None:
            return
        wy = self._water_y()
        x = float(WORLD_W + sdef.display_w * 0.6)
        boss = Boss('enemy', x, sdef, wy)
        boss.max_hp = self._boss_hp(sdef); boss.hp = boss.max_hp
        self.ships.append(boss)
        self.sig_boss.emit("incoming")

    def _queue_spawn(self, team: str, sdef):
        if len(self.spawn_buffer[team]) < MAX_UNITS[team]:  # don't let the buffer balloon
            self.spawn_buffer[team].append(sdef)

    def _process_deaths(self, pl, en):
        """Explode units destroyed this frame and settle structure bookkeeping.

        A downed oil rig resets its owner's rebuild cooldown to full so it can be
        re-mustered. The Salvage upgrade refunds a fraction of a destroyed mobile
        unit's cost to its owner (SALVAGE_T by level; level 0 pays nothing) —
        downing the boss still maxes your bank, handled at the boss trigger. Units
        that merely sail off-screen (hp still > 0) aren't counted as kills."""
        # A bastion's deck turrets fall with it.
        for s in self.ships:
            if getattr(s, 'is_rig', False) and not s.alive:
                for nt in getattr(s, 'node_turrets', []):
                    if nt is not None:
                        nt.alive = False
        for s in self.ships:
            if s.alive or getattr(s, '_reward_done', False):
                continue
            if getattr(s, 'is_base', False) or getattr(s.sdef, 'is_boss', False):
                continue                                    # bases never die; boss handled at trigger
            if getattr(s, 'hp', 0) > 0:
                continue                                    # left the field, not destroyed
            s._reward_done = True
            if getattr(s, 'is_rig', False):
                # Bastion down — reset its rebuild cooldown so it can be re-mustered.
                cd = self._cooldown_total('oilrig', s.team)
                self.build_cd[s.team]['oilrig']       = cd
                self.build_cd_total[s.team]['oilrig'] = cd
            else:
                # Salvage: refund the owner a % of a lost mobile hull's cost.
                # Structures (turrets/rigs) are excluded — turrets have their own
                # manual sell refund, and a rig isn't a vehicle.
                ut = getattr(s.sdef, 'unit_type', 'ship')
                if ut not in ('turret', 'structure'):
                    fac  = pl if s.team == 'player' else en
                    frac = SALVAGE_T[fac.upgrades['salvage']]
                    if frac and not (self.infinite_money and s.team == 'player'):
                        refund = int(round(s.sdef.cost * frac))
                        fac.resources = min(fac.resources + refund,
                                            float(fac.max_res))
            self.effects.append(Explosion(s.x, getattr(s, 'mid_y', s.top_y)))

    def _mobile_count(self, team: str) -> int:
        """Live mobile units for a side (bases, rigs, turrets and bosses don't count).
        Bosses are excluded so a punishment boss (which may be on the player's own
        team) never consumes a side's Fleet cap and blocks its deploys."""
        return sum(1 for s in self.ships
                   if s.team == team and s.alive
                   and not getattr(s, 'is_base', False) and not getattr(s, 'is_rig', False)
                   and not getattr(s.sdef, 'is_boss', False)
                   and getattr(s.sdef, 'unit_type', 'ship') != 'turret')

    def _committed(self, team: str) -> int:
        """Units this side has already committed to fielding: live mobiles, plus any
        bought and waiting in the spawn buffer, plus (player) the cooldown queue.
        Used to gate builds against the Fleet cap so pressing at the limit neither
        charges nor over-buffers — the units already in flight fill the cap."""
        n = self._mobile_count(team) + len(self.spawn_buffer[team])
        if team == 'player':
            n += sum(self.unit_queue.values()) + sum(self.unit_rush_queue.values())
        return n

    def _fleet_cap(self, team: str) -> int:
        """Max live mobile units this side may field right now: its Fleet-upgrade
        level (FLEET_T), clamped to the side's hard MAX_UNITS ceiling. Fleet is the
        upgrade that used to be Salvage — buying it raises this cap toward MAX_UNITS."""
        fac = self.player if team == 'player' else self.enemy
        # Enemy AI always fields its maximum fleet: ignore its purchased Fleet level
        # and use the top tier. In PvP a human commands the enemy, so it earns its
        # cap from its own Fleet upgrades exactly like the player.
        if team == 'enemy' and not self.pvp:
            lvl = len(FLEET_T) - 1
        else:
            lvl = fac.upgrades['fleet']
        return min(FLEET_T[lvl], MAX_UNITS[team])

    def _release_spawns(self, dt: float):
        """Let at most one buffered unit per side enter the field every
        SPAWN_STAGGER seconds, so batches stagger instead of stacking. A side at
        its Fleet cap holds its spawns until the field thins out."""
        for team in ('player', 'enemy'):
            self.spawn_release[team] = max(0.0, self.spawn_release[team] - dt)
            buf = self.spawn_buffer[team]
            if buf and self.spawn_release[team] <= 0.0 and self._mobile_count(team) < self._fleet_cap(team):
                sdef = buf.pop(0)
                self._spawn_mobile(team, sdef)
                self.spawn_release[team] = SPAWN_STAGGER

    # ── Oil rig (deployable income structure) ────────────────────────────────────
    def build_oilrig(self, team: str):
        """AI / sandbox: auto-build an oil rig on a team's pier (the player places
        it by hand on the rig node — see _try_place). The enemy may only build it
        once it is unlocked for this level's campaign stage."""
        if team == 'enemy' and 'oilrig' not in self.enemy_unlocked:
            return                                  # not unlocked yet at this stage
        if not self.sandbox and self.build_cd[team].get('oilrig', 0.0) > 0:
            return                                  # still cooling down from the last one's loss
        if any(getattr(s, 'is_rig', False) and getattr(s, 'team', '') == team and s.alive
               for s in self.ships):
            return
        sdef = self.data.vehicles.get('oilrig')
        x, _ = self._enemy_rig_pos() if team == 'enemy' else self._rig_pos()
        rig = OilRig(team, x, sdef); rig.set_water(self._water_y())
        rig.water_deep = self._backdrop.water_tint
        self.ships.append(rig)
        self._start_build_cd(team, 'oilrig')

    def _enemy_rig_pos(self):
        return self._enemy_fort.rig_mount(self._water_y())

    # ── Capturable midfield oil platforms ────────────────────────────────────────
    def _build_capture_points(self):
        """Seat the neutral oil platforms across the open water between the forts —
        but only from the campaign level set in capture_points.json. Positions come
        from the config's `positions` fractions when their count matches, else the
        rigs are spread evenly across the midfield."""
        cfg = self.capture_cfg
        level_no = self.data.campaign_stage(self.level.key) + 1     # 1-based
        if level_no < int(cfg["enabled_from_level"]):
            return
        count = max(0, int(cfg["count"]))
        if count <= 0:
            return
        # Open water lane: seaward of one fort to seaward of the other, with a margin
        # so a rig never sits on top of a base.
        lo = FORT_D_W + 140.0
        hi = WORLD_W - FORT_D_W - 140.0
        positions = cfg.get("positions", [])
        if isinstance(positions, list) and len(positions) == count:
            fracs = [max(0.0, min(1.0, float(f))) for f in positions]
        else:
            fracs = [(i + 1) / (count + 1) for i in range(count)]
        wy = self._water_y()
        mid = WORLD_W / 2.0
        dead = 100.0        # rigs within this of centre are neutral ground (no home)
        for f in fracs:
            x = lo + f * (hi - lo)
            home = ("player" if x < mid - dead
                    else "enemy" if x > mid + dead else "neutral")
            # The client renders the world mirrored (its own base on the left), so a
            # platform in the host's player waters must appear in OUR opponent waters:
            # flip its world position and home ownership to match the mirrored view.
            if self.mirror_view:
                x = WORLD_W - x
                home = "enemy" if home == "player" else "player" if home == "enemy" else "neutral"
            self.capture_points.append(
                CapturePoint(x, wy, cfg, self._backdrop.water_tint, home))

    # ── Central score platform + punishment bosses ───────────────────────────────
    def _build_score_platform(self):
        """Seat ONE capturable oil platform dead-centre between the forts. It reuses
        the full CapturePoint contest/draw/income machinery; the score→boss loop
        rides on top of it (see _update_score)."""
        wy = self._water_y()
        self.capture_points.append(
            CapturePoint(WORLD_W / 2.0, wy, self.capture_cfg,
                         self._backdrop.water_tint, "neutral"))

    def _build_boss_ladder(self):
        """Every boss key ordered by CAMPAIGN level progression, so the punishment
        bosses march down in the same sequence a player meets them in the campaign:
        Potemkin (level 1) first, then each next level's flagship, and so on. Any
        boss not fielded by a level (should be none) is appended afterwards, ordered
        weakest→strongest by its normal-difficulty HP so the tail still ramps."""
        ladder, seen = [], set()
        for lvl in self.data.levels_sorted():
            key = lvl.boss
            if key in self.data.bosses and key not in seen:
                ladder.append(key); seen.add(key)
        def strength(sd):
            return float(getattr(sd, 'hp_normal', 0) or getattr(sd, 'hp', 0))
        for sd in sorted(self.data.bosses.values(), key=strength):
            if sd.key not in seen:
                ladder.append(sd.key); seen.add(sd.key)
        return ladder

    def _next_boss_sdef(self):
        """The flagship the ladder will call down next. Host and client share the
        same ladder built from the level data; the client's rung index rides in on
        the snapshot (see netsync `sbi`), so both read the same 'next' boss. Returns
        its ShipDef, or None if the ladder is empty."""
        if not self._score_ladder:
            return None
        key = self._score_ladder[min(self._score_ladder_i,
                                      len(self._score_ladder) - 1)]
        return self.data.bosses.get(key)

    def _update_score(self, dt: float):
        """Credit score to whoever HOLDS the platform, retire any downed flagship
        bosses, and every SCORE_BOSS_INTERVAL award the next flagship to whichever
        side leads — then wipe both scores. Runs in multiplayer only
        (host-authoritative); skipped in the campaign and the sandbox (score_mode
        is False there)."""
        if not self.score_mode:
            return
        # Holding the platform earns YOU score (sticky income_owner, so a
        # half-decapped rig keeps paying its last full owner until neutralised).
        for cp in self.capture_points:
            o = cp.income_owner
            if o == 'player':   self.score['player'] += SCORE_PER_SEC * dt
            elif o == 'enemy':  self.score['enemy']  += SCORE_PER_SEC * dt
        # Retire downed flagship bosses: a spread of explosions, one alert, cull.
        for b in self.score_bosses:
            if not b.alive and not getattr(b, 'death_handled', False):
                b.death_handled = True
                b.death_explosions(self.effects)
                self.sig_alert.emit(("HOSTILE FLAGSHIP DESTROYED" if b.team == 'enemy'
                                     else "ALLIED FLAGSHIP LOST"))
        self.score_bosses = [b for b in self.score_bosses if b.alive]
        # The 1.5-minute cycle: award a flagship to the leader, then reset the tally.
        # The countdown only advances while NO flagship boss is on the field — a live
        # boss must be resolved before the next one is called, so the leading side
        # never gets a second flagship stacked on top of the first (anti-snowball).
        if self.score_bosses:
            return
        self.score_boss_t -= dt
        if self.score_boss_t <= 0.0:
            self.score_boss_t += float(SCORE_BOSS_INTERVAL)
            self._spawn_score_boss()
            self.score['player'] = 0.0
            self.score['enemy']  = 0.0

    def _spawn_score_boss(self):
        """Award the next rung of the boss ladder to the side with the MOST score
        (a tie or an all-zero board spawns nothing). The boss fights on the leader's
        OWN team and marches on the opponent's base."""
        ps, es = self.score['player'], self.score['enemy']
        if max(ps, es) <= 0.0 or ps == es:
            return                                  # no clear leader this cycle
        if not self._score_ladder:
            return
        leader = 'player' if ps > es else 'enemy'
        key  = self._score_ladder[min(self._score_ladder_i, len(self._score_ladder) - 1)]
        self._score_ladder_i += 1                   # climb toward the strongest, then hold
        sdef = self.data.bosses.get(key)
        if sdef is None:
            return
        wy = self._water_y()
        boss_team = leader                          # the leader's reward fights for them
        # Enter from the far edge on the boss's own side of the map.
        x = (float(WORLD_W + sdef.display_w * 0.6) if boss_team == 'enemy'
             else float(-sdef.display_w * 0.6))
        boss = Boss(boss_team, x, sdef, wy)
        boss.max_hp = self._boss_hp(sdef); boss.hp = boss.max_hp
        self.ships.append(boss)
        self.score_bosses.append(boss)
        # The same flashing klaxon a campaign boss fires, but it names WHOSE base is
        # under threat. Text is written in the HOST's frame; the client renders the
        # world mirrored, so it's told the target from ITS side (host base = the
        # client's enemy, and vice-versa) and shown the alarm via a relayed message.
        name = sdef.name.upper()
        strikes_host = (boss_team == 'enemy')       # marches on the player's (host's) base
        host_txt   = f"BATTLESHIP {name} IS COMING FOR " + (
            "YOUR BASE" if strikes_host else "THE ENEMY BASE")
        client_txt = f"BATTLESHIP {name} IS COMING FOR " + (
            "THE ENEMY BASE" if strikes_host else "YOUR BASE")
        self.sig_boss.emit(f"strike:{host_txt}")
        if self.net_role == 'host' and self.net is not None and self.net.alive:
            self.net.send({"t": "boss_warn", "text": client_txt})

    def _score_boss_active(self) -> bool:
        """True while any punishment flagship boss is live on EITHER side. Downed
        bosses are only culled from score_bosses in _update_score (which runs after
        _update_capture), so key off each boss's own `alive` flag rather than the
        list being non-empty — a boss that died this frame must NOT still count."""
        return any(getattr(b, 'alive', False) for b in self.score_bosses)

    def _update_capture(self, dt: float, wy: float):
        """Tick the capture meter of every oil platform from the surface ships in its
        lane, and announce any ownership change. Only ground (surface) ships hold a
        rig — submarines run too deep and aircraft too high to plant a boarding crew,
        so they contribute nothing to the capture meter."""
        if not self.capture_points:
            return
        # While a punishment flagship boss is live on either side, the central oil
        # platform is frozen COMPLETELY NEUTRAL: no side holds, captures or earns
        # from it, so the boss cycle must be resolved before the midfield reopens.
        # Host-authoritative — the neutral meter/income rides the snapshot to the
        # client (netsync caps/income_owner), so both ends read it the same.
        if self.score_mode and self._score_boss_active():
            neutralized = False
            for cp in self.capture_points:
                cp.set_water(wy)
                if cp.owner != 'neutral':
                    neutralized = True
                cp.neutralize()
            if neutralized:
                self.sig_alert.emit("OIL PLATFORM OFFLINE — FLAGSHIP INBOUND")
            return
        counts = [[0, 0] for _ in self.capture_points]      # [player, enemy] ship count per rig
        weights = [[0.0, 0.0] for _ in self.capture_points]  # summed capture_weight per side
        r2 = [cp.radius * cp.radius for cp in self.capture_points]
        for s in self.ships:
            if not getattr(s, 'alive', False):
                continue
            if getattr(s, 'is_base', False) or getattr(s, 'is_rig', False):
                continue
            sdef = getattr(s, 'sdef', None)
            # Surface hulls only: submarines, planes, turrets and structures can't capture.
            if getattr(sdef, 'unit_type', 'ship') != 'ship':
                continue
            ti = 0 if getattr(s, 'team', '') == 'player' else 1
            w = float(getattr(sdef, 'capture_weight', 1.0))
            sx = s.x
            for i, cp in enumerate(self.capture_points):
                dx = sx - cp.x
                if dx * dx <= r2[i]:
                    counts[i][ti] += 1
                    weights[i][ti] += w
        # Current holdings feed the comeback bonus (the side behind captures faster).
        p_rigs = sum(1 for cp in self.capture_points if cp.owner == 'player')
        e_rigs = sum(1 for cp in self.capture_points if cp.owner == 'enemy')
        for cp, (pn, en), (pw, ew) in zip(self.capture_points, counts, weights):
            cp.set_water(wy)
            if cp.update(dt, pn, en, p_rigs, e_rigs, pw, ew):
                o = cp.owner
                if o == 'player':  self.sig_alert.emit("OIL PLATFORM SECURED")
                elif o == 'enemy': self.sig_alert.emit("OIL PLATFORM LOST")
                else:              self.sig_alert.emit("OIL PLATFORM CONTESTED")

    def rig_income(self, owner: str) -> float:
        """Diminishing income for the rigs a side holds: the first rig pays full,
        each further rig `holdings_falloff`× the previous — so hoarding all three
        is worth little at the margin (anti-snowball). Payout keys off the STICKY
        `income_owner`, not the live `owner`: once you fully capture a rig you keep
        earning from it until it is decapped all the way back to neutral, so an enemy
        merely prying the meter down doesn't cut your income until they neutralise it."""
        k = sum(1 for cp in self.capture_points if cp.income_owner == owner)
        unit = float(self.capture_cfg["income_per_rig"])
        return diminishing_total(unit, k, self._capture_falloff)

    def _aim_coastal(self, dt):
        """↑/↓ adjust the shared coastal-artillery elevation; push it to the guns."""
        if any(k in self._keys for k in SETTINGS.keys_for("aim_up")):   self.coastal_aim = min(1.35, self.coastal_aim + 0.9 * dt)
        if any(k in self._keys for k in SETTINGS.keys_for("aim_down")): self.coastal_aim = max(0.30, self.coastal_aim - 0.9 * dt)
        for s in self.ships:
            if getattr(getattr(s, 'sdef', None), 'manual_aim', False):
                s.aim_angle = self.coastal_aim

    def set_tutorial_freeze(self, on: bool):
        """Hold (or release) the battle for the first-run guide. The main tick is
        already stopped while the guide is up, but a turret can freeze mid-shot —
        barrel swung onto a target, muzzle flash lit — which reads as 'firing' in
        the still frame behind the overlay. So when the guide opens we settle every
        fort gun back to a calm resting pose and clear shells / flashes in flight,
        then repaint once so the guide sits over a quiet battlefield."""
        self.tutorial_freeze = on
        if not on:
            return
        for s in self.ships:
            if not getattr(s, 'alive', False):
                continue
            sd = getattr(s, 'sdef', None)
            if getattr(sd, 'unit_type', '') != 'turret':
                continue
            s.aim_mirror = None                    # face home, don't point at a target
            s.bursting = False                     # abort any salvo in progress
            rest = getattr(sd, 'barrel_rest', None)
            if rest is not None:                   # auto guns ease back to rest…
                s.aim_angle = rest
            elif getattr(sd, 'manual_aim', False): # …manual guns sit at their set elevation
                s.aim_angle = self.coastal_aim
            s.state = "moving"
        self.projectiles.clear()                   # no shells frozen in the air
        self.effects.clear()                       # no explosions/flashes frozen mid-bloom
        self.update()

    # ── Game loop ────────────────────────────────────────────────────────────────
    def _tick(self):
        now = QDateTime.currentMSecsSinceEpoch()
        if self._prev_ms is None: self._prev_ms = now; return
        raw = (now - self._prev_ms) / 1000.0
        dt = min(raw, 0.05); self._prev_ms = now
        # FPS meter: average over ~half-second windows so the readout is steady.
        # Uses the raw delta — the sim clamp above would flatter slow frames.
        self._fps_frames += 1; self._fps_accum += raw
        if self._fps_accum >= 0.5:
            self._fps = self._fps_frames / self._fps_accum
            self._fps_frames = 0; self._fps_accum = 0.0
        # Host awaiting an opponent: the field is drawn but frozen behind the
        # "awaiting player" overlay. No sim, no input — just watch for the pairing.
        if self.awaiting_peer:
            self._await_pump()
            self.update()
            return
        # Link dropped: hold everything and try to rendezvous within the grace window.
        if self._reconnecting:
            self._reconnect_pump()
            self.update()
            return
        # Locked room: a joiner is paired but not yet admitted. Hold the match (no
        # sim, no 'hello', no token) until they prove the room password.
        if self.net_role == 'host' and not self._peer_authed:
            self._await_auth()
            self.update()
            return
        # Client (P2): no local sim — drain host snapshots, animate local effects.
        if self.net_role == 'client':
            self._send_ident()          # tell the host our name, once
            self._net_pump()
            if not self.paused:
                self._client_update(dt)
            self.update()
            self._ui_accum += dt
            if self._ui_accum >= 0.05:
                self._ui_accum = 0.0; self.sig_ui.emit()
            return
        if not self.paused:
            self._update(dt)
        # Host (P1): send the one-time hello, apply the client's orders, broadcast.
        if self.net_role == 'host':
            self._send_hello()
            self._net_pump()
            if not self.paused:
                self._net_broadcast(dt)
        self.update()
        # Repaint the battlefield every frame (above), but refresh the Qt HUD
        # widgets only ~20×/s. Rebuilding every unit button + upgrade card at 60 fps
        # was the dominant CPU cost; the numbers/cooldowns read fine at 20 Hz.
        # (Direct player actions still emit sig_ui at once for instant feedback.)
        self._ui_accum += dt
        if self._ui_accum >= 0.05:
            self._ui_accum = 0.0
            self.sig_ui.emit()

    def _update(self, dt):
        if self.game_over: return
        if self.dying is not None:               # a base is collapsing — freeze the
            self._update_collapse(dt); return    # fight and play the death animation
        if self.tutorial_freeze: return          # first-run guide up — hold everything
        self.game_time += dt; self.wave_t += dt
        if self.boss_flash_t > 0.0:                  # fade the boss-down blast flash
            self.boss_flash_t = max(0.0, self.boss_flash_t - dt)
        pl = self.player; en = self.enemy; wy = self._water_y()

        if not self._defenses_placed:            # place once, when the view is sized
            if self.pvp:                         # PvP: each side starts with one missile turret, nothing else
                self._place_pvp_start(wy)
            else:
                if not self.sandbox:             # sandbox starts as a clean slate
                    self._place_enemy_defenses(wy)
                self._place_player_defenses(wy)  # the allied fort always has two artillery
            self._defenses_placed = True

        pl.resources = min(pl.resources + pl.income * dt, pl.max_res)
        if self.infinite_money: pl.resources = 1_000_000.0

        # The enemy plays the same economy game as the player (difficulty no longer
        # touches its income): base income plus a slow time ramp, so it must still
        # upgrade its base to keep escalating. In PvP a human commands the enemy, so
        # it earns plain income with no AI time-ramp handout — symmetric with P1.
        if self.pvp:
            en.resources = min(en.resources + en.income * dt, en.max_res)
        else:
            en_rate = en.income + min(self.game_time / 90.0, 20.0)
            en.resources = min(en.resources + en_rate * dt, en.max_res + 1500)

        # Keep the base targets at the current waterline
        self.player_base.set_water(wy); self.enemy_base.set_water(wy)

        # Production cooldowns, oil-rig income, coastal aiming
        for team in self.build_cd:
            for k in list(self.build_cd[team]):
                if self.build_cd[team][k] > 0:
                    self.build_cd[team][k] = max(0.0, self.build_cd[team][k] - dt)
                    if self.build_cd[team][k] <= 0:
                        self.build_cd_total[team].pop(k, None)   # ready → forget its total
        self._tick_upgrades(dt)
        self._release_unit_queue()
        self._release_spawns(dt)
        # Capturable oil platforms: advance each rig's contest, then credit its
        # owner's income below alongside the Oil Rig turrets.
        self._update_capture(dt, wy)
        # Central score platform → escalating punishment bosses (multiplayer only).
        self._update_score(dt)
        pl.bonus_income = en.bonus_income = 0
        # Campaign midfield rigs are ECONOMIC — holding one pays diminishing income.
        if self.capture_points and not self.score_mode:   # diminishing per-side rig income
            pl.bonus_income += self.rig_income('player')
            en.bonus_income += self.rig_income('enemy')
        # In multiplayer the single central platform drives the score→boss
        # loop (see _update_score) AND pays its holder a flat aggression bounty in
        # gold/sec — seizing the midfield directly funds a capital ship, so pushing
        # for the platform pays off at once instead of only after a boss cycle.
        elif self.capture_points and self.score_mode:
            for cp in self.capture_points:
                o = cp.income_owner                       # sticky: pays until neutralised
                if o == 'player':  pl.bonus_income += SCORE_HOLD_INCOME
                elif o == 'enemy': en.bonus_income += SCORE_HOLD_INCOME
        for s in self.ships:
            if not getattr(s, 'alive', False):
                continue
            if getattr(s, 'is_rig', False):
                s.set_water(wy)                 # keep the bastion pinned to the waterline
                inc = getattr(s, 'income', 0)   # 0 — the bastion produces no income
            else:
                inc = getattr(getattr(s, 'sdef', None), 'income', 0)  # Oil Rig turret
            if inc:
                (pl if s.team == "player" else en).bonus_income += inc
        self._aim_coastal(dt)

        # Keep fortress placement slots in sync with armour / casualties
        self._sync_slots()

        # Enemy reinforcements are decided by the director (escalation, difficulty,
        # counterattacks, threat adaptation). It spawns units off-screen. In the
        # sandbox the AI is off — you spawn both sides yourself.
        if not self.sandbox and not self.pvp:
            if self.bob_campaign and self.bot is not None:
                # Oil-rig-era campaign: the enemy is commanded by Captain Bob rather
                # than the scripted director. He runs his own economy and upgrade
                # plan, so the director's spawn cadence and _enemy_upgrade tick are
                # both retired here (the director object survives only so the boss
                # wave's boss_escort() call stays valid — a harmless no-op now).
                self.bot.update(dt)
            else:
                self.director.update(dt)
                en.upg_timer -= dt
                if en.upg_timer <= 0:
                    interval = max(6.0, 18.0 - self.game_time / 60.0)
                    en.upg_timer = interval + random.uniform(0, 6)
                    self._enemy_upgrade()
        # Play-with-a-bot: Captain Bob commands the enemy faction in place of a
        # networked human, on the very same PvP economy (spawns, turret placement
        # and upgrades all paid out of the enemy bank via the normal apply paths).
        elif self.vs_bot and self.bot is not None:
            self.bot.update(dt)

        # Carrier / boss-carrier aircraft launch. The aircraft enter from OFF-SCREEN
        # (off the carrier's home map edge) and fly in — never popped into mid-air.
        for s in self.ships:
            if not s.alive: continue
            interval = getattr(s.sdef, 'spawn_interval', 0.0)
            if interval <= 0: continue
            s.spawn_timer -= dt
            if s.spawn_timer <= 0:
                s.spawn_timer = interval
                keys = getattr(s.sdef, 'spawn_unit_keys', [])
                if keys:
                    key = keys[s.spawn_index % len(keys)]; s.spawn_index += 1
                    csdef = self.data.all_units.get(key)
                    if csdef:
                        self._spawn_mobile(s.team, csdef)

        # Y-sync per unit type (bosses, bases and rigs position themselves)
        for s in self.ships:
            if getattr(s, 'is_base', False) or getattr(s, 'is_rig', False) \
               or getattr(s.sdef, 'is_boss', False): continue
            ut = getattr(s.sdef, 'unit_type', 'ship')
            if ut == 'turret':
                # Fort-mounted turrets are pinned to their pad, whose y is always
                # `water_y + a fixed offset`. Re-derive top_y from the CURRENT
                # water_y each frame so they track the fort when the window is
                # resized / fullscreened (rig-node turrets are re-seated in
                # _sync_slots instead, so they carry no anchor).
                anchor = getattr(s, 'y_anchor', None)
                if anchor is not None:
                    s.top_y = wy + anchor
            elif ut == 'submarine':
                s.top_y = wy + s.disp_h * 0.40 + s.y_jitter
            elif ut == 'plane':
                at = getattr(s.sdef, 'aircraft_type', '')
                # Bombers (carpet) cruise a bit higher; hovercraft get an altitude
                # lane so a group of helicopters spreads vertically as well.
                extra_alt = 55 if at == 'carpet' else 0
                lane = getattr(s, 'hover_lane', 0.0) if at == 'hover' else 0.0
                base_y = wy - PLANE_ALT - extra_alt - s.disp_h * 0.5 + lane
                if s.rising:
                    s.top_y = max(base_y, s.top_y - 90 * dt)
                    if s.top_y <= base_y: s.rising = False
                else:
                    s.top_y = base_y + getattr(s, 'y_off', 0.0)
            else:
                # Surface boats all ride the same waterline — no random vertical
                # deviation, otherwise a static per-unit offset seats half of them
                # above the water and they appear to float in the air. (y_jitter is
                # kept for submarines, where it de-clumps depth.)
                s.top_y = wy - s.disp_h * 0.85

        # Rebuild the per-frame x-sorted target index so each unit scans only the
        # ships within weapon reach instead of the whole field (O(N·window), not
        # O(N²)). Nothing is culled by the camera — every unit, on- or off-screen,
        # is indexed and keeps fighting.
        Ship.set_frame_index(self.ships)

        # Update ships. Stamp the current waterline on each so ballistic anti-sub
        # ordnance (depth charges) can lob its carrier onto the sea surface above a
        # submerged target instead of at the target's underwater depth.
        for s in self.ships:
            s.water_y = wy
            s.update(dt, self.ships, self.projectiles, self.effects, pl, en)

        # Update projectiles (flak bursts append shrapnel into `spawned`). Damage
        # is applied here, so deaths are settled AFTER this, not before.
        spawned = []
        for pr in self.projectiles: pr.update(dt, self.ships, wy, spawned, self.effects)
        self.projectiles = [pr for pr in self.projectiles if pr.alive]
        self.projectiles += spawned

        # Explode everything destroyed this frame (no salvage refund anymore), then
        # drop the dead from the field.
        self._process_deaths(pl, en)
        self.ships = [s for s in self.ships if s.alive]

        # Peel a foam ripple off every ship making way (see _spawn_wakes).
        if not SETTINGS.no_effects:
            self._spawn_wakes(wy)

        # NO EFFECTS (settings): drop every particle effect the frame it appears —
        # ships/projectiles keep appending into the list, so clearing here covers
        # every source (explosions, muzzle smoke, missile exhaust) in one place.
        if SETTINGS.no_effects:
            self.effects.clear()
        else:
            for e in self.effects: e.update(dt)
            self.effects = [e for e in self.effects if e.alive]

        # ── Boss trigger / immunity (skipped in the sandbox and in PvP) ───────
        if not self.sandbox and not self.pvp:
            boss_trigger = getattr(self.level, 'boss_trigger', BOSS_HP_TRIGGER)
            if not en.boss_spawned and en.base_hp <= boss_trigger * en.max_base_hp:
                self._spawn_boss(en, wy)
            if en.immune:
                en.base_hp = max(en.base_hp, boss_trigger * en.max_base_hp)
            # Boss defeated → lift the base's immunity so it can be finished off.
            if self.boss_ship is not None and not self.boss_ship.alive:
                en.immune = False; en.boss_alive = False
                self.boss_ship.death_explosions(self.effects)
                self.boss_flash_t = BOSS_FLASH_DUR      # bright blast flash over the field
                pl.resources = float(pl.max_res)        # downing the boss maxes your bank
                self.sig_boss.emit("down")
                self.boss_ship = None

        pl.base_hp = max(0.0, min(pl.base_hp, pl.max_base_hp))
        en.base_hp = max(0.0, min(en.base_hp, en.max_base_hp))
        if not self.sandbox and self.dying is None and not self.game_over:
            if pl.base_hp <= 0:   self._begin_collapse("player", "enemy")
            elif en.base_hp <= 0: self._begin_collapse("enemy", "player")

        # Keyboard camera pan
        spd = 500.0
        if any(k in self._keys for k in SETTINGS.keys_for("pan_left")):
            self.cam_x = max(0.0, self.cam_x - spd * dt)
        if any(k in self._keys for k in SETTINGS.keys_for("pan_right")):
            self.cam_x = min(WORLD_W - self.width(), self.cam_x + spd * dt)

    def _begin_collapse(self, loser: str, winner: str):
        """A base has been battered to 0 HP. Rather than cut straight to the
        verdict, freeze the fight and play a short collapse — rolling explosions
        across the doomed fort with a camera push-in — then declare the winner
        (see _update_collapse). The clock is frozen here, at the killing blow, so
        the debrief time isn't padded by the animation."""
        self.dying = loser
        self.dying_win = winner
        self.dying_t = 0.0
        self._dying_spawn_t = 0.0
        self.clear_time = self.game_time
        # Host is authoritative: tell the client to play the same collapse now so
        # both sides watch the fort fall in step before the verdict lands.
        if self.net_role == 'host' and self.net is not None and self.net.alive:
            self.net.send({"t": "collapse", "who": winner})
        # A last, big burst so the fort is already erupting on the first frame.
        self._spawn_base_explosions(loser, n=6)

    def _spawn_base_explosions(self, team: str, n: int = 3):
        """Scatter `n` explosions across the given team's fort silhouette. The tier
        count mirrors the fort's hitbox binding (see bind_fort) so the bursts land
        on the visible structure as armour upgrades grow it taller."""
        if team == "player":
            fort, hlv = self._fort, self.player.hlv
        else:
            fort, hlv = self._enemy_fort, max(self.enemy.hlv, self.enemy_def_tiers)
        l, r, t, b = fort.hit_bounds(self._water_y(), hlv)
        for _ in range(n):
            self.effects.append(
                Explosion(random.uniform(l, r), random.uniform(t, b)))

    def _update_collapse(self, dt: float):
        """Advance the base-collapse animation: roll fresh explosions across the
        doomed fort, ease the camera onto it, and let existing effects bloom while
        the rest of the field holds. When it finishes, announce the winner."""
        self.dying_t += dt
        # Keep erupting for the first ~80% of the window, then let the smoke clear.
        self._dying_spawn_t -= dt
        if self._dying_spawn_t <= 0.0 and self.dying_t < BASE_DEATH_ANIM * 0.8:
            self._dying_spawn_t = 0.11
            self._spawn_base_explosions(self.dying)
        # Ease the camera to centre the collapsing fort so the player watches it go.
        base = self.player_base if self.dying == "player" else self.enemy_base
        target = max(0.0, min(WORLD_W - self.width(), base.x - self.width() / 2.0))
        self.cam_x += (target - self.cam_x) * min(1.0, dt * 3.0)
        # Effects still animate (explosions/smoke fade) while the field is frozen.
        if SETTINGS.no_effects:
            self.effects.clear()
        else:
            for e in self.effects: e.update(dt)
            self.effects = [e for e in self.effects if e.alive]
        if self.dying_t >= BASE_DEATH_ANIM:
            who, self.dying = self.dying_win, None
            self._end(who)

    def _end(self, who: str):
        self.game_over = who
        self.clear_time = self.game_time
        # Host is authoritative over the result — tell the client at once (the
        # throttled snapshot also carries `over`, but this makes it immediate).
        if self.net_role == 'host' and self.net is not None and self.net.alive:
            self.net.send({"t": "over", "who": who})
        self.sig_over.emit(who)

    def result(self) -> dict:
        """Score the just-finished battle (call after a player victory)."""
        frac = (self.player.base_hp / self.player.max_base_hp) if self.player.max_base_hp else 0.0
        return compute_result(self.clear_time, frac)

    # ── Boss ────────────────────────────────────────────────────────────────────
    def _spawn_boss(self, fac, wy):
        sdef = self.data.bosses.get(self.level.boss)
        if not sdef:
            fac.boss_spawned = True; return
        x = float(WORLD_W + sdef.display_w * 0.6)        # enters from off the enemy edge
        boss = Boss('enemy', x, sdef, wy)
        boss.max_hp = self._boss_hp(sdef); boss.hp = boss.max_hp   # exact per-difficulty HP
        self.ships.append(boss); self.boss_ship = boss
        fac.boss_spawned = True; fac.boss_alive = True; fac.immune = True
        # A large escort wave arrives alongside the boss
        self.director.boss_escort()
        # Leave the camera where the player put it — the boss announces itself via
        # the "incoming" banner rather than yanking the view over to the enemy base.
        # Carry the boss's name so the campaign banner can flash "BATTLESHIP <x> IS
        # COMING" (the sandbox spawn stays the plain generic "incoming").
        self.sig_boss.emit(f"incoming:{sdef.name}")

    # ── Enemy AI helpers ──────────────────────────────────────────────────────────
    ENEMY_UPGRADE_PRIO = ["warehouse", "resource", "health", "storage", "fleet"]

    def _enemy_target_levels(self) -> dict:
        """How high the enemy wants each upgrade — it LEVELS WITH the player,
        mirroring your per-track investment exactly (difficulty no longer shifts
        this; it only affects the final wave and enemy hull HP)."""
        return {u: max(0, min(self.max_upg_lvl, self.player.upgrades[u]))
                for u in self.enemy.upgrades}

    def _enemy_wanted_upgrade(self):
        """The next track (economy-first) where the enemy is behind its target."""
        targets = self._enemy_target_levels()
        en = self.enemy
        for u in self.ENEMY_UPGRADE_PRIO:
            if en.upgrades[u] < targets[u]:
                return u
        return None

    def enemy_next_upgrade_cost(self) -> float:
        """Cost of the upgrade the enemy wants next (0 if it has caught up to the
        player). The director saves toward this."""
        u = self._enemy_wanted_upgrade()
        return float(UPGRADE_COSTS[u][self.enemy.upgrades[u]]) if u is not None else 0.0

    def _enemy_upgrade(self):
        # Only research what keeps it level with the player; request_upgrade
        # handles cost + the research delay. The enemy still researches one track
        # at a time (it saves toward a single target), so bail if any is pending.
        if self.pending_upg['enemy']: return
        u = self._enemy_wanted_upgrade()
        if u is not None:
            self.request_upgrade('enemy', u)

    # ── Paint (runs on the GPU via QOpenGLWidget's OpenGL paint engine) ──────────
    def paintGL(self):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W = self.width(); H = self.height(); WY = round(self._water_y())
        self._draw_sky(p, W, H, WY); self._draw_water(p, W, H, WY)
        self._draw_waves(p, W, WY)
        # Off-screen units still fight (their sim ran in _update); they just don't
        # need to be PAINTED. Skipping the ones outside the viewport keeps a wide,
        # crowded field cheap to render — the camera only shows a slice of WORLD_W.
        cam = self.cam_x
        # Ships carry a moonlit rim after dark (see Ship._draw_night_rim) so
        # near-black player hulls stay visible against the dark night sea; the
        # rim ramps in with the same time-of-day `night` that lights ordnance.
        night = self._backdrop.night
        # Water craft (boats, subs, bosses) sail BEHIND the forts so they are
        # occluded when passing over one; the forts, their mounted turrets, oil
        # rigs and aircraft draw in front.
        for s in self.ships:
            if self._behind_fort(s) and self._on_screen(s.x, W, s.disp_w * 0.5 + 90):
                s.draw(p, cam, self.sprites.sprites, night)
        self._draw_bases(p, W, H, WY)
        self._draw_capture_points(p, W, WY)
        for s in self.ships:
            if not self._behind_fort(s) and self._on_screen(s.x, W, s.disp_w * 0.5 + 90):
                s.draw(p, cam, self.sprites.sprites, night)
        p.setPen(Qt.PenStyle.NoPen)
        # Ordnance renders night-aware: muzzle tracers and launched orbs only
        # "light up" as the level's time-of-day band darkens (game.ui.backdrop),
        # so bullets read as glowing streaks and shells/rockets as incandescent
        # orbs after dark, but as plain solid rounds under daylight.
        for pr in self.projectiles:
            if self._on_screen(pr.x, W, 60): pr.draw(p, cam, night)
        if not SETTINGS.no_effects:       # (also hides leftovers while paused)
            for e in self.effects:
                if self._on_screen(e.x, W, 80): e.draw(p, cam)
        if self.placing: self._draw_placement(p)
        else: self._draw_sell_button(p)
        if self.boss_flash_t > 0.0: self._draw_boss_flash(p, W, H)
        if self.dying is not None: self._draw_collapse_flash(p, W, H)
        self._draw_hud(p, W, H)
        if self.game_over: self._draw_game_over(p, W, H)
        elif self.awaiting_peer: self._draw_awaiting(p, W, H)
        elif self._reconnecting: self._draw_reconnect(p, W, H)
        elif self.paused: self._draw_pause(p, W, H)

    def _draw_collapse_flash(self, p, W, H):
        """Blast flash over the field while a fort collapses: a bright ignition
        bloom on the first frames that eases away, over a low red pall that rises
        and fades across the whole animation — so the freeze reads as a
        catastrophe, not a dropped frame, before the verdict card appears."""
        t = self.dying_t
        bloom = max(0.0, 1.0 - t / 0.35)          # bright ignition, first ~0.35 s
        if bloom > 0.0:
            p.fillRect(0, 0, W, H, QColor(255, 236, 200, int(150 * bloom)))
        env = math.sin(min(1.0, t / BASE_DEATH_ANIM) * math.pi)   # 0→1→0 pall
        p.fillRect(0, 0, W, H, QColor(150, 30, 18, int(46 * env)))

    def _draw_boss_flash(self, p, W, H):
        """The boss-down blast: the same bright ignition bloom the collapse flash
        opens on, played over the un-frozen field when the boss is destroyed — so
        downing it lands with the same screen-flash punch as a fort's death."""
        bloom = self.boss_flash_t / BOSS_FLASH_DUR    # full at the kill, fading out
        p.fillRect(0, 0, W, H, QColor(255, 236, 200, int(150 * bloom)))

    def _draw_reconnect(self, p, W, H):
        """Grace-window overlay: the field is frozen while both sides try to re-link;
        a live countdown shows how long until the match is forfeited."""
        from .ui import theme
        p.save()
        p.fillRect(0, 0, W, H, QColor(6, 9, 12, 214))
        cx, cy = W // 2, H // 2
        remain = self._reconnect_remaining()
        p.setPen(QColor(theme.DANGER)); p.setFont(theme.head(28, 4))
        p.drawText(QRectF(0, cy - 62, W, 42), Qt.AlignmentFlag.AlignCenter,
                   "CONNECTION LOST")
        p.setPen(QColor(theme.ACCENT)); p.setFont(theme.mono(14))
        p.drawText(QRectF(0, cy - 6, W, 22), Qt.AlignmentFlag.AlignCenter,
                   f"Reconnecting…   match ends in {remain}s")
        p.setPen(QColor(theme.TEXT_FAINT)); p.setFont(theme.mono(11))
        p.drawText(QRectF(0, cy + 34, W, 20), Qt.AlignmentFlag.AlignCenter,
                   "Press  Esc  to leave")
        p.restore()

    def _draw_awaiting(self, p, W, H):
        """Host lobby-in-game: dim the frozen field and show that we're holding for
        an opponent to join our room (or the error that ended the wait)."""
        from .ui import theme
        p.save()
        p.fillRect(0, 0, W, H, QColor(6, 9, 12, 210))
        cx, cy = W // 2, H // 2
        if self._await_error:
            title, sub = "CONNECTION LOST", self._await_error
            col = QColor(theme.DANGER)
        else:
            # A simple animated ellipsis so it reads as "live, still waiting".
            # Driven off the await-tick counter since the sim clock is frozen here.
            dots = "." * (1 + (getattr(self, "_await_ticks", 0) // 20) % 3)
            title = "AWAITING PLAYER" + dots
            # Name the room the commander chose (falls back to the relay id), and
            # note if it's password-locked so they know joiners need the password.
            name = getattr(self._net_pending, "name", None) \
                or (f"Room {getattr(self._net_pending, 'room_id', None)}"
                    if getattr(self._net_pending, "room_id", None) else None)
            locked = bool(getattr(self._net_pending, "password", ""))
            if name:
                lock = "  🔒 password-protected" if locked else ""
                sub = f"“{name}” is open — waiting for an opponent to join{lock}"
            else:
                sub = "Opening your room…"
            col = QColor(theme.ACCENT)
        p.setPen(col)
        p.setFont(theme.head(30, 4))
        p.drawText(QRectF(0, cy - 60, W, 44), Qt.AlignmentFlag.AlignCenter, title)
        p.setPen(QColor(theme.TEXT_DIM))
        p.setFont(theme.mono(13))
        p.drawText(QRectF(0, cy - 8, W, 22), Qt.AlignmentFlag.AlignCenter, sub)
        p.setPen(QColor(theme.TEXT_FAINT))
        p.setFont(theme.mono(11))
        p.drawText(QRectF(0, cy + 34, W, 20), Qt.AlignmentFlag.AlignCenter,
                   "Press  Esc  to cancel")
        p.restore()

    def _draw_placement(self, p):
        from .ui import theme
        targets = self.placement_targets()
        key, sdef, team = self.placing
        acc = QColor(theme.ACCENT)
        p.save()
        for (x, y, kind, idx) in targets:
            sx = x - self.cam_x
            # Ghost footprint standing on the pad surface (y), plus a bright
            # platform line so it's clear which mount the click will use.
            ghost = QColor(acc); ghost.setAlpha(45)
            p.setPen(QPen(acc, 2)); p.setBrush(ghost)
            p.drawRect(int(sx - 24), int(y - 46), 48, 46)
            p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(QColor(theme.AMBER_HI), 2))
            p.drawLine(int(sx - 26), int(y), int(sx + 26), int(y))
        label = f"PLACE {sdef.name.upper()}"
        if not targets:
            label += "  —  NO FREE SLOTS"
        # Sits below the boss-banner band (y≈54..100) so the two never overlap.
        p.setPen(acc); p.setFont(theme.head(13, 1))
        p.drawText(0, 108, self.width(), 20, Qt.AlignmentFlag.AlignHCenter,
                   label + "   (click a slot · right-click to cancel)")
        p.restore()

    def _draw_sell_button(self, p):
        """Floating SELL button over the currently-selected friendly turret. Clicking
        it (see mousePressEvent) sells the turret for sell_price(). The button rect is
        stashed in _sell_rect each frame so the hit-test tracks the camera."""
        from .ui import theme
        ship = self.sel_turret
        # Drop the selection if the turret died, was sold, or the battle ended.
        if (ship is None or not getattr(ship, 'alive', False) or ship not in self.ships
                or self.game_over or self.paused):
            self.sel_turret = None; self._sell_rect = None
            return

        refund = self.sell_price(ship)
        label  = f"SELL  +{refund}"
        f = theme.head(11, 1)
        tw = QFontMetrics(f).horizontalAdvance(label)
        w  = tw + 26; h = 24
        cx = ship.x - self.cam_x
        # Prefer sitting just above the turret; flip below if it would clip the top.
        by = ship.top_y - h - 8
        if by < 6: by = ship.top_y + ship.disp_h + 8
        bx = cx - w * 0.5
        bx = max(4.0, min(self.width() - w - 4.0, bx))

        p.save()
        # A selection reticle around the turret so it's clear what will be sold.
        sx = ship.x - ship.disp_w * 0.5 - self.cam_x
        p.setPen(QPen(QColor(theme.PHOSPHOR), 1.4)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.setOpacity(0.85)
        p.drawRect(QRectF(sx - 2, ship.top_y - 2, ship.disp_w + 4, ship.disp_h + 4))
        p.setOpacity(1.0)
        theme.plate(p, bx, by, w, h, base=theme.PANEL_HI, cut=6,
                    corners=(True, False, True, False),
                    accent=theme.PHOSPHOR, accent_side="top", border=theme.PHOSPHOR)
        p.setPen(QColor(theme.PHOSPHOR)); p.setFont(f)
        p.drawText(QRectF(bx, by, w, h), int(Qt.AlignmentFlag.AlignCenter), label)
        p.restore()
        self._sell_rect = QRectF(bx, by, w, h)

    def _draw_sky(self, p, W, H, WY):
        # Sky, celestial disc, parallax clouds, sea and the amber horizon rule —
        # all skinned from this level's time-of-day band (game.ui.backdrop).
        from .ui import backdrop
        backdrop.paint(p, self._backdrop, W, H, WY, self.cam_x, self.clouds, self.wave_t)

    def _draw_water(self, p, W, H, WY):
        # The sea + horizon are painted together with the sky in _draw_sky so the
        # backdrop stays one coherent scene; kept as a no-op for paintGL's call order.
        pass

    def _draw_waves(self, p, W, WY):
        from .ui import backdrop
        backdrop.paint_waves(p, self._backdrop, W, WY, self.cam_x, self.wave_t)

    def _on_screen(self, x: float, W: int, pad: float) -> bool:
        """Is world-x `x` (± pad) within the current viewport? Used only to skip
        PAINTING units the camera can't see — never to skip their simulation."""
        sx = x - self.cam_x
        return -pad <= sx <= W + pad

    @staticmethod
    def _behind_fort(s) -> bool:
        """True for mobile water craft that should be occluded by a fort they pass
        over. Forts, their mounted turrets, oil rigs and aircraft stay in front."""
        if getattr(s, 'is_base', False) or getattr(s, 'is_rig', False):
            return False
        return getattr(s.sdef, 'unit_type', 'ship') not in ('turret', 'structure', 'plane')

    def _draw_bases(self, p, W, H, WY):
        # Procedural tiered sea-forts: stepped gun decks rising to a keep, with
        # an oil-rig pier. The silhouette grows taller as the armour upgrade adds
        # tiers (its mounts are the turret slots). The level's deep-water tint is
        # passed so the submerged caisson fogs into THIS level's sea colour.
        deep = self._backdrop.water_tint
        # enemy_def_tiers = the extra tiers the fixed defensive guns need. On the host
        # those guns sit on its enemy fort; on the mirrored client they belong to OUR
        # own fort (the left/player one), so the def-tier term follows the perspective.
        p_tiers = max(self.player.hlv, self.enemy_def_tiers) if self.mirror_view \
            else self.player.hlv
        e_tiers = self.enemy.hlv if self.mirror_view \
            else max(self.enemy.hlv, self.enemy_def_tiers)
        self._fort.draw(p, self.cam_x, WY, p_tiers, H, deep)
        self._enemy_fort.draw(p, self.cam_x, WY, e_tiers, H, deep)

    def _draw_capture_points(self, p, W, WY):
        """Paint the midfield oil platforms behind the fleet (ships passing over
        them occlude the legs); their capture meter floats clear above the deck."""
        for cp in self.capture_points:
            if self._on_screen(cp.x, W, cp.disp_w * 0.5 + 120):
                cp.set_water(WY)
                cp.draw(p, self.cam_x)

    def _draw_hud(self, p, W, H):
        from .ui import theme
        pl = self.player; en = self.enemy
        bw = min(320, int(W * 0.28)); bh = 22; mt = 10
        # In PvP the commanders' names label their own gauges (FRIENDLY/HOSTILE
        # otherwise, and while the opponent's name hasn't yet arrived).
        self._hp_bar(p, mt, mt, bw, bh, pl.base_hp, pl.max_base_hp, "FRIENDLY", True, False,
                     name=self.player_name)
        self._hp_bar(p, W - mt - bw, mt, bw, bh, en.base_hp, en.max_base_hp, "HOSTILE", False, en.immune,
                     name=self.opponent_name)
        # FPS readout, centred in the gap between the two base gauges.
        p.setFont(theme.mono(9, True)); p.setPen(QColor(theme.TEXT_DIM))
        p.drawText(0, mt, W, bh,
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   f"{self._fps:3.0f} FPS")
        # Fleet-strength readouts, tucked under each base gauge: how many live
        # mobile units that side has on the field versus its Fleet-upgrade cap.
        self._unit_tally(p, mt, mt + bh, bw, "FRIENDLY", True)
        self._unit_tally(p, W - mt - bw, mt + bh, bw, "HOSTILE", False)
        # Central oil-platform scoreboard + punishment-boss countdown.
        if self.score_mode and self.capture_points:
            self._draw_scoreboard(p, W, mt + bh + 6)

    def _boss_silhouette(self, src, w, h, color):
        """Render a ship sprite as a flat single-colour silhouette fitted within
        w×h (aspect kept) — a clean radar contact for the inbound-flagship scope,
        rather than the busy full-colour portrait. Every opaque pixel is repainted
        `color` via a source-in composite, so only the hull outline survives."""
        from PyQt6.QtGui import QPixmap
        sc = src.scaled(max(1, int(w)), max(1, int(h)),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
        out = QPixmap(sc.size()); out.fill(Qt.GlobalColor.transparent)
        q = QPainter(out)
        q.drawPixmap(0, 0, sc)
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        q.fillRect(out.rect(), QColor(color))
        q.end()
        return out

    def _draw_scoreboard(self, p, W, y):
        """A war-room readout for the central oil platform: each side's score
        (whoever leads calls the next flagship), the countdown to that flagship, and
        a recessed target-scope showing the silhouette + name of the inbound boss.
        Built from the theme's plate primitive so it sits in the same material
        language as the rest of the console; text is engraved (a 1px black drop)
        so every figure stands proud of the brushed steel."""
        from .ui import theme
        ps = int(self.score['player']); es = int(self.score['enemy'])
        secs = max(0, int(self.score_boss_t + 0.999))
        clock = f"{secs // 60}:{secs % 60:02d}"
        lead = ("YOU" if ps > es else "ENEMY" if es > ps else "STALEMATE")
        nb = self._next_boss_sdef()            # the flagship due to deploy next
        soon = secs <= 15                       # imminent — light the alarm red

        w, h = 366, 88
        x = (W - w) / 2.0
        pad = 10
        theme.plate(p, x, y, w, h, base=theme.PANEL_HI, cut=8,
                    corners=(True, False, True, False), riveted=True,
                    accent=(theme.DANGER if soon else theme.ACCENT),
                    accent_side="top", border=theme.LINE_HI)

        # Recessed target scope on the right (drawn first so the left column reads
        # against the plate, not the well).
        vpw, vph = 146, h - 22 - pad
        vpx = x + w - pad - vpw
        vpy = y + 22
        self._draw_inbound_viewport(p, vpx, vpy, vpw, vph, nb, soon)

        # ── Title + hairline divider (left column) ──────────────────────────────
        theme.engraved_label(p, "OIL PLATFORM", x + pad, y + 4,
                             vpx - x - pad, theme.head(9, 2), color=theme.TEXT)
        p.setPen(QPen(QColor(theme.LINE_HI), 1)); p.setOpacity(0.5)
        p.drawLine(int(x + pad), int(y + 20), int(vpx - 8), int(y + 20))
        p.setOpacity(1.0)

        # ── Countdown: LED + FLAGSHIP m:ss, engraved for legibility ─────────────
        cy = y + 12
        cd_col = theme.DANGER_HI if soon else theme.ACCENT
        theme.led(p, x + pad + 3, cy, cd_col, r=3.0, on=True)
        p.setFont(theme.mono(9, True))
        cbox = QRectF(x + pad + 12, cy - 8, vpx - (x + pad + 12) - 6, 16)
        al = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        p.setPen(QColor(0, 0, 0, 170)); p.drawText(cbox.translated(1, 1), al, f"FLAGSHIP  {clock}")
        p.setPen(QColor(cd_col)); p.drawText(cbox, al, f"FLAGSHIP  {clock}")

        # ── Scores: big engraved figures with a lit lamp on the leader ──────────
        colw = vpx - x - pad - 6
        self._score_row(p, x + pad, y + 30, colw, "YOU", ps, theme.PHOSPHOR, ps >= es)
        self._score_row(p, x + pad, y + 54, colw, "ENEMY", es, theme.DANGER, es > ps)

        # ── Status line: who currently leads (and will call the next flagship) ──
        sbox = QRectF(x + pad, y + h - 16, colw, 12)
        lal = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        lead_col = (theme.PHOSPHOR if lead == "YOU" else
                    theme.DANGER if lead == "ENEMY" else theme.TEXT_FAINT)
        p.setFont(theme.mono(8))
        p.setPen(QColor(0, 0, 0, 150)); p.drawText(sbox.translated(1, 1), lal, f"LEADER  {lead}")
        p.setPen(QColor(lead_col)); p.drawText(sbox, lal, f"LEADER  {lead}")

    def _score_row(self, p, x, y, w, label, value, colour, leading):
        """One score line — an engraved DIN label and a large mono figure, each with
        a 1px black drop so it lifts off the brushed plate; the leading side's figure
        carries a lit lamp so the board reads at a glance."""
        from .ui import theme
        x, y, w = int(x), int(y), int(w)
        la = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        p.setFont(theme.head(9, 1))
        p.setPen(QColor(0, 0, 0, 160)); p.drawText(x + 1, y + 1, 70, 20, la, label)
        p.setPen(QColor(theme.TEXT_DIM)); p.drawText(x, y, 70, 20, la, label)
        if leading:
            theme.led(p, x + 60, y + 10, colour, r=2.6, on=True)
        ra = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        p.setFont(theme.mono(15, True))
        p.setPen(QColor(0, 0, 0, 190)); p.drawText(x + 1, y + 1, w, 20, ra, str(value))
        p.setPen(QColor(colour)); p.drawText(x, y, w, 20, ra, str(value))

    def _draw_inbound_viewport(self, p, x, y, w, h, nb, soon):
        """A recessed glass target-scope: a near-black well with CRT scanlines, the
        inbound flagship rendered as a red radar silhouette inside amber target
        brackets, and a stencilled nameplate strip beneath it."""
        from .ui import theme
        x, y, w, h = int(x), int(y), int(w), int(h)
        rect = QRectF(x, y, w, h)
        p.fillRect(rect, QColor(5, 8, 11, 240))                 # recessed well
        g = QLinearGradient(0, y, 0, y + 12)                    # inner top shadow
        g.setColorAt(0.0, QColor(0, 0, 0, 130)); g.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(x, y, w, 12), g)
        theme.scanlines(p, rect, gap=3, alpha=26)

        nph = 15                                                # nameplate strip
        stage = QRectF(x, y, w, h - nph)                        # ship sits here
        bc = QColor(theme.DANGER_HI if soon else theme.ACCENT)  # bracket / alert
        if nb is not None:
            icon = self.sprites.hi('enemy', nb.key)
            if icon and not icon.isNull():
                sil = self._boss_silhouette(icon, w - 22, h - nph - 14,
                                            QColor("#d2584a"))
                p.drawPixmap(int(stage.center().x() - sil.width() / 2),
                             int(stage.center().y() - sil.height() / 2), sil)
        else:
            p.setFont(theme.mono(8)); p.setPen(QColor(theme.TEXT_FAINT))
            p.drawText(stage, int(Qt.AlignmentFlag.AlignCenter), "NO CONTACT")

        # amber corner target brackets around the stage
        p.setPen(QPen(bc, 1)); p.setOpacity(0.85); c = 9
        for ox, oy, sx, sy in ((3, 3, 1, 1), (w - 3, 3, -1, 1),
                               (3, h - nph - 3, 1, -1), (w - 3, h - nph - 3, -1, -1)):
            px, py = x + ox, y + oy
            p.drawLine(int(px), int(py), int(px + sx * c), int(py))
            p.drawLine(int(px), int(py), int(px), int(py + sy * c))
        p.setOpacity(1.0)

        # 'INBOUND' tab, top-left of the scope
        p.setFont(theme.mono(7, True)); p.setPen(QColor(bc))
        p.drawText(QRectF(x + 6, y + 3, w - 12, 12),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                   "◤ INBOUND")

        # nameplate strip
        npr = QRectF(x, y + h - nph, w, nph)
        p.fillRect(npr, QColor(theme.DANGER).darker(260))
        p.setPen(QPen(QColor(theme.LINE_HI), 1)); p.setOpacity(0.6)
        p.drawLine(int(x), int(y + h - nph), int(x + w), int(y + h - nph)); p.setOpacity(1.0)
        name = nb.name.upper() if nb is not None else "—"
        ca = int(Qt.AlignmentFlag.AlignCenter)
        p.setFont(theme.mono(8, True))
        p.setPen(QColor(0, 0, 0, 180)); p.drawText(npr.translated(1, 1), ca, name)
        p.setPen(QColor(theme.DANGER_HI)); p.drawText(npr, ca, name)

        # scope bezel
        p.setPen(QPen(QColor(theme.LINE_HI), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)

    def _unit_tally(self, p, x, y, w, label, left):
        """A compact 'units on field / fleet cap' counter beneath a base gauge —
        phosphor for the player's fleet, danger-red for the enemy's — so the
        current strength of each side is always visible at a glance."""
        from .ui import theme
        team = 'player' if left else 'enemy'
        n = self._mobile_count(team); cap = self._fleet_cap(team)
        colour = QColor(theme.PHOSPHOR if left else theme.DANGER)
        al = (Qt.AlignmentFlag.AlignLeft if left else Qt.AlignmentFlag.AlignRight) \
            | Qt.AlignmentFlag.AlignVCenter
        p.setFont(theme.mono(9, True)); p.setPen(colour)
        p.drawText(int(x + 6), int(y + 3), int(w - 12), 16, int(al),
                   f"UNITS {n}/{cap}")

    def _hp_bar(self, p, x, y, w, h, hp, max_hp, label, left, immune, name=None):
        """A stepped command gauge for a fortress: phosphor when the FRIENDLY base
        is healthy, escalating amber → danger as it's hit; the HOSTILE base reads
        red. Steel bezel, mono figure — matches the console HUD below. In PvP the
        commander's `name` replaces the FRIENDLY/HOSTILE tag on the gauge."""
        from .ui import theme
        from PyQt6.QtGui import QLinearGradient, QBrush
        x, y, w, h = int(x), int(y), int(w), int(h)
        f = max(0.0, hp / max_hp) if max_hp else 0.0
        if left:
            fill = theme.PHOSPHOR if f > .5 else (theme.ACCENT if f > .25 else theme.DANGER)
        else:
            fill = theme.DANGER
        # recessed track
        p.fillRect(x, y, w, h, QColor(6, 9, 12, 220))
        fw = int(w * f); fx = x if left else x + w - fw
        c = QColor(fill)
        grad = QLinearGradient(0, y, 0, y + h)
        grad.setColorAt(0.0, c.lighter(140)); grad.setColorAt(0.5, c)
        grad.setColorAt(1.0, c.darker(120))
        p.fillRect(fx, y, fw, h, QBrush(grad))
        # segment ticks
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        for i in range(1, 10):
            xi = x + int(w * i / 10); p.drawLine(xi, y + 1, xi, y + h - 1)
        # a lit leading edge on the active end
        p.setPen(QPen(c.lighter(170), 1))
        edge = x + fw if left else x + w - fw
        p.drawLine(edge, y + 1, edge, y + h - 1)
        # steel bezel + top highlight
        p.setPen(QPen(QColor(theme.LINE_HI), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(x, y, w - 1, h - 1)
        p.setPen(QPen(QColor(theme.EDGE_HI), 1)); p.setOpacity(0.4)
        p.drawLine(x + 1, y + 1, x + w - 2, y + 1); p.setOpacity(1.0)
        # label (engraved DIN) on the anchored side, figure (mono) opposite —
        # a commander name (PvP) takes the tag's place when one is known.
        lab = (name.upper() if name else label) + ("  [IMMUNE]" if immune else "")
        p.setFont(theme.head(9, 2)); p.setPen(QColor(0, 0, 0, 160))
        la = (Qt.AlignmentFlag.AlignLeft if left else Qt.AlignmentFlag.AlignRight) | Qt.AlignmentFlag.AlignVCenter
        p.drawText(x + 7, y + 1, w - 12, h, int(la), lab)
        p.setPen(QColor(theme.TEXT)); p.drawText(x + 6, y, w - 12, h, int(la), lab)
        p.setFont(theme.mono(9, True))
        va = (Qt.AlignmentFlag.AlignRight if left else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter
        p.setPen(QColor(theme.TEXT)); p.drawText(x + 6, y, w - 12, h, int(va), f"{int(hp)}/{int(max_hp)}")

    def _draw_game_over(self, p, W, H):
        from .ui import theme
        p.save(); p.fillRect(0, 0, W, H, QColor(4, 7, 10, 205))
        theme.scanlines(p, QRectF(0, 0, W, H), gap=4, alpha=26)
        win = self.game_over == self.my_team
        col = theme.PHOSPHOR if win else theme.DANGER
        # a rule above and below the verdict, hazard-marked
        cy = H // 2
        p.setPen(QPen(QColor(col), 1)); p.setOpacity(0.5)
        p.drawLine(int(W * 0.3), cy - 60, int(W * 0.7), cy - 60)
        p.drawLine(int(W * 0.3), cy + 34, int(W * 0.7), cy + 34); p.setOpacity(1.0)
        p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.head(13, 8))
        p.drawText(0, cy - 92, W, 22, Qt.AlignmentFlag.AlignCenter,
                   "ENGAGEMENT CONCLUDED")
        theme.engraved_label(p, "VICTORY" if win else "DEFEAT",
                             0, cy - 56, W, theme.stencil(58, 6), col,
                             flags=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        hint_y = cy + 46
        if win and not self.sandbox and not self.pvp:
            # The debrief: stars earned, clear time and score — the same numbers
            # recorded on the profile, so the campaign screen holds no surprises.
            r = self.result()
            theme.draw_stars(p, W / 2 - 28, cy + 46, 16, r["stars"], total=3)
            p.setPen(QColor(theme.TEXT)); p.setFont(theme.mono(12, True))
            p.drawText(0, cy + 74, W, 18, Qt.AlignmentFlag.AlignCenter,
                       f"TIME {r['time']:.0f} S    ·    SCORE {r['score']}")
            hint_y = cy + 104
        p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.mono(12))
        if self.pvp and self.vs_bot:
            hint = "[ R ]  RE-ENGAGE      [ ESC ]  COMMAND"
        elif self.pvp and self._rematch_local:
            hint = "REMATCH REQUESTED — AWAITING OPPONENT      [ ESC ]  COMMAND"
        elif self.pvp and self._rematch_peer:
            hint = "[ R ]  ACCEPT REMATCH      [ ESC ]  COMMAND"
        elif self.pvp:
            hint = "[ R ]  REMATCH      [ ESC ]  COMMAND"
        else:
            hint = "[ R ]  RE-ENGAGE      [ ESC ]  COMMAND"
        p.drawText(0, hint_y, W, 24, Qt.AlignmentFlag.AlignCenter, hint)
        p.restore()

    def _draw_pause(self, p, W, H):
        """The battle held: a dark veil in the war-room idiom, mirroring the
        game-over card — amber verdict, mono key legend."""
        from .ui import theme
        p.save(); p.fillRect(0, 0, W, H, QColor(4, 7, 10, 170))
        theme.scanlines(p, QRectF(0, 0, W, H), gap=4, alpha=20)
        cy = H // 2
        p.setPen(QPen(QColor(theme.ACCENT), 1)); p.setOpacity(0.5)
        p.drawLine(int(W * 0.3), cy - 60, int(W * 0.7), cy - 60)
        p.drawLine(int(W * 0.3), cy + 34, int(W * 0.7), cy + 34); p.setOpacity(1.0)
        p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.head(13, 8))
        p.drawText(0, cy - 92, W, 22, Qt.AlignmentFlag.AlignCenter, "OPERATION HELD")
        theme.engraved_label(p, "PAUSED", 0, cy - 56, W, theme.stencil(58, 6), theme.ACCENT,
                             flags=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        # Three clickable command buttons (also driven by the ESC / R / Q keys).
        # Each is a chamfered plate; rects are stashed in _pause_rects for the
        # click hit-test in mousePressEvent.
        buttons = [("RESUME",    "ESC",                                   theme.PHOSPHOR, "resume"),
                   ("RESTART",   SETTINGS.keys_display_for("restart"),    theme.ACCENT,   "restart"),
                   ("MAIN MENU", SETTINGS.keys_display_for("main_menu"),  theme.TEXT_DIM, "main_menu")]
        f = theme.head(12, 3)
        fm = QFontMetrics(f)
        h = 34; pad = 22; gap = 16
        widths = [fm.horizontalAdvance(lbl) + pad * 2 for lbl, _, _, _ in buttons]
        total = sum(widths) + gap * (len(buttons) - 1)
        bx = (W - total) / 2.0
        by = cy + 40
        self._pause_rects = []
        for (label, keycap, col, action), w in zip(buttons, widths):
            theme.plate(p, bx, by, w, h, base=theme.PANEL_HI, cut=7,
                        corners=(True, False, True, False),
                        accent=col, accent_side="top", border=col)
            p.setPen(QColor(col)); p.setFont(f)
            p.drawText(QRectF(bx, by, w, h), int(Qt.AlignmentFlag.AlignCenter), label)
            # The key shortcut as a small cap beneath the button.
            p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.mono(10))
            p.drawText(QRectF(bx, by + h + 4, w, 16),
                       int(Qt.AlignmentFlag.AlignCenter), f"[ {keycap} ]")
            self._pause_rects.append((QRectF(bx, by, w, h), action))
            bx += w + gap
        p.restore()

    # ── Input ─────────────────────────────────────────────────────────────────────
    def toggle_pause(self):
        """Hold / resume the battle (Escape). The sim stops, the field freezes;
        the pause overlay offers resume / restart / withdraw.

        Disabled in PvP: the match is host-authoritative and runs continuously on
        the other side, so a local pause can't actually stop the fight — it would
        only freeze one player's own view (client) or unilaterally freeze both
        (host). ESC is inert during a live network match; the connection-lost hold
        in _on_net_lost still sets self.paused directly and is unaffected."""
        if self.pvp and not self.vs_bot: return    # a solo bot match CAN pause locally
        if self.game_over or self.awaiting_peer or self._reconnecting: return
        if self.dying is not None: return             # base collapsing — can't pause
        self.paused = not self.paused
        self.sig_ui.emit()

    @staticmethod
    def _phys_key(ev, letter):
        """True if `ev` is that physical letter key regardless of the active
        keyboard layout — a Cyrillic layout reports Й in ev.key() where the
        keycap says Q, but the native virtual key stays the Latin letter."""
        return (ev.key() == getattr(Qt.Key, 'Key_' + letter)
                or ev.nativeVirtualKey() == ord(letter))

    @staticmethod
    def _key_is(ev, action):
        """True if this key event triggers any key bound to `action`. Matches by
        Qt key code and, for a letter binding, additionally by physical letter so
        it still fires on a non-Latin layout (see _phys_key)."""
        k = ev.key(); nvk = ev.nativeVirtualKey()
        for kc in SETTINGS.keys_for(action):
            if k == kc:
                return True
            if int(Qt.Key.Key_A) <= kc <= int(Qt.Key.Key_Z) and nvk == kc:
                return True
        return False

    def keyPressEvent(self, ev):
        self._keys.add(ev.key())
        if self.awaiting_peer:                        # in-game hold: only Esc, to cancel
            if ev.key() == Qt.Key.Key_Escape:
                if self._net_pending is not None:
                    self._net_pending.cancel()
                self.sig_main_menu.emit()
            return
        if self._reconnecting:                        # grace window: Esc leaves — but
            if ev.key() == Qt.Key.Key_Escape:         # bailing on a dropped opponent
                self._forfeit_disconnect()            # forfeits, it doesn't escape free
            return
        if self.paused:                               # held: only pause-menu keys act
            if ev.key() == Qt.Key.Key_Escape:  self._pause_action("resume")
            elif self._key_is(ev, "restart"):  self._pause_action("restart")
            elif self._key_is(ev, "main_menu"): self._pause_action("main_menu")
            return
        if self._key_is(ev, "deploy"): self.cmd_deploy()
        if self._key_is(ev, "restart") and self.game_over:
            # RE-ENGAGE: single-player (and a vs-bot match) restart at once; a
            # networked PvP match asks the opponent for a rematch (and the second
            # press, once they've asked too, accepts it).
            if self.pvp and not self.vs_bot: self._request_rematch()
            else:                            self.reset()
            self.sig_ui.emit()
        if ev.key() == Qt.Key.Key_Apostrophe:
            self.infinite_money = not self.infinite_money
            self.sig_alert.emit("INFINITE MONEY: " + ("ON" if self.infinite_money else "OFF"))
            self.sig_ui.emit()
        if ev.key() == Qt.Key.Key_Escape:
            if self.placing: self.cancel_placing()
            elif self.game_over: self.sig_menu.emit()
            else: self.toggle_pause()                 # hold the battle, don't dump out

    def keyReleaseEvent(self, ev): self._keys.discard(ev.key())

    def _pause_action(self, name):
        """Run a pause-menu command — shared by the ESC/R/Q keys and the on-canvas
        buttons so both paths stay in lock-step."""
        if   name == "resume":    self.toggle_pause()
        elif name == "restart":   self.reset(); self.sig_ui.emit()
        elif name == "main_menu": self.sig_main_menu.emit()

    def mousePressEvent(self, ev):
        if self.awaiting_peer or self._reconnecting:  # nothing is interactive yet
            return
        if self.dying is not None:                    # base collapsing — field frozen
            return
        # While held, the pause overlay owns the canvas: a left-click hits one of
        # its buttons (or nothing) — never pans the field or sells a turret.
        if self.paused:
            if ev.button() == Qt.MouseButton.LeftButton:
                pos = ev.position()
                for rect, action in self._pause_rects:
                    if rect.contains(pos):
                        self._pause_action(action); break
            return
        if ev.button() == Qt.MouseButton.RightButton:
            if self.placing:
                self.cancel_placing()                 # right-click aborts placement
            else:
                self.cmd_sell_at(ev.position().x(), ev.position().y())
            return
        if ev.button() == Qt.MouseButton.LeftButton:
            if self.placing:
                self._try_place(ev.position().x(), ev.position().y()); return
            mx, my = ev.position().x(), ev.position().y()
            # A turret is selected and its SELL button was clicked → sell it.
            if (self.sel_turret is not None and self._sell_rect is not None
                    and self._sell_rect.contains(int(mx), int(my))):
                if self.net_role == 'client':       # host is authoritative — order it
                    nid = getattr(self.sel_turret, '_nid', None)
                    if nid is not None and self.net is not None:
                        self.net.send({"t": "cmd", "a": "sell", "nid": nid})
                    self.sel_turret = None; self._sell_rect = None
                else:
                    self._sell_turret(self.sel_turret)
                return
            # Clicking a friendly turret selects it (raising its SELL button);
            # clicking anywhere else dismisses the button and pans the camera.
            hit = self._friendly_turret_at(mx, my)
            if hit is not None and not self.game_over and not self.paused:
                self.sel_turret = hit; self._sell_rect = None; return
            self.sel_turret = None; self._sell_rect = None
            self.drag_x = mx; self.drag_cam = self.cam_x
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, ev):
        if self.drag_x is not None:
            self.cam_x = max(0.0, min(WORLD_W - self.width(),
                                      self.drag_cam - (ev.position().x() - self.drag_x)))
        else:
            self._update_hover(ev.position().x(), ev.position().y())

    def mouseReleaseEvent(self, _):
        self.drag_x = None; self.setCursor(Qt.CursorShape.OpenHandCursor)

    def leaveEvent(self, _ev):
        if self._hover_id is not None:
            self._hover_id = None; self.sig_hover.emit(None)

    def _update_hover(self, mx, my):
        """Emit the field unit under the cursor (topmost ship first; the fort behind
        them if no ship is hit) so the HUD can show its readout."""
        hit = None
        for s in reversed(self.ships):
            if getattr(s, 'is_base', False): continue
            sx = s.x - s.disp_w * 0.5 - self.cam_x
            sy = s.top_y
            if sx <= mx <= sx + s.disp_w and sy <= my <= sy + s.disp_h:
                hit = s; break
        # No unit under the cursor → check the two sea-forts (drawn behind the
        # fleet). The tier count mirrors _draw_bases so the box tracks the sprite.
        if hit is None:
            WY = self._water_y()
            forts = ((self._fort, self.player.hlv, self.player_base),
                     (self._enemy_fort, max(self.enemy.hlv, self.enemy_def_tiers),
                      self.enemy_base))
            for fort, hlv, base in forts:
                if fort.screen_bounds(self.cam_x, WY, hlv).contains(int(mx), int(my)):
                    hit = base; break
        hid = id(hit) if hit is not None else None
        if hid != self._hover_id:
            self._hover_id = hid
            self.sig_hover.emit(hit)
