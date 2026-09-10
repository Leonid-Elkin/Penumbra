"""
game.bot – CaptainBob: a solo opponent for the head-to-head (PvP) mode.

The head-to-head battle is host-authoritative: the host runs the sim as the
"player" faction and a second human, joined over the network, commands the
"enemy" faction by sending {"t":"cmd", ...} orders the host applies (see
battle._handle_net → _apply_deploy / _apply_place / request_upgrade). CaptainBob
stands in for that second human. It runs on the SAME host sim, reads the world
straight off the canvas, and issues the very same enemy-faction orders by calling
those apply methods directly – so it plays by identical rules: the same starting
purse, the same plain (no time-ramp) income, the same upgrade costs and research
delays, the same fort slots, the same production cooldowns. Nothing is handed to
it; every hull, turret and upgrade is paid for out of the enemy bank.

Its doctrine, in priority order each decision tick:
  1. DEFEND    – read the incoming player fleet, weight threats by how close they
                 are to the enemy fort, and answer an uncovered air / sub / surface
                 threat by seating the right turret in a free fort (or Bastion)
                 slot; if it can't yet afford the turret, it musters a cheap hull
                 that counters the same threat instead.
  2. FORTIFY   – a seaward Bastion (a tanky shield that also mounts two turrets)
                 once the bank can spare it, then turrets on its deck nodes.
  3. INVEST    – buy base upgrades on an economy-first plan (income → production →
                 fleet → …), always keeping a reserve so a sudden push still finds
                 the bank ready to fortify.
  4. SEIZE     – fight for the midfield oil platform: whenever it isn't paying the
                 enemy bank, push a SURFACE hull (only surface hulls drag the meter,
                 and a heavy capture_weight hull pulls it faster) to take it back –
                 harder and faster the moment the player starts prying it away. In
                 this mode holding the cap scores the punishment flagship, so the
                 bot contests it directly instead of hoping its fleet drifts through.
  5. ATTACK    – spend the surplus pressuring the player with a diversified fleet,
                 leaning on hulls that also counter the player's air / sub units.

Every decision is gated by the real cost / cooldown / fleet-cap checks the human
opponent faces, so the bot is income-limited and must grow its economy to escalate
– exactly as a person playing this mode would.
"""

from __future__ import annotations
import random

from .config import WORLD_W, FORT_D_W


class CaptainBob:
    NAME = "CAPTAIN BOB"

    # Economy-first upgrade ambition. The bot buys the first track in this order it
    # is behind on and can afford (with a reserve) – income and faster production
    # first, then a bigger fleet / bank, then armour (which also adds a turret slot).
    UPGRADE_PRIO = ["resource", "warehouse", "fleet", "storage", "health", "salvage"]

    # Turret preferences per threat category, best first. A missile battery is the
    # versatile pick (hits air AND surface); torpedoes are the only fort answer to
    # submarines; the AA gun is a cheap-firing air specialist.
    _TURRET_FOR = {
        "air":  ["missile_battery", "aa_turret"],
        "sub":  ["torpedo_battery"],
        "surf": ["missile_battery", "coastal_artillery"],
    }

    def __init__(self, canvas):
        self.c = canvas
        self.team = "enemy"
        self._t = 0.0
        self._next = random.uniform(1.0, 2.0)     # a beat before the first order
        self._recent: list[str] = []              # last few mobile keys, for diversity
        self._last_counter = -999.0               # throttle on chip counter-hulls
        self._last_deploy = -999.0                # throttle on standing-fleet upkeep
        self._last_cap = -999.0                   # throttle on capture-push hulls

        # Catalogue the enemy's buildable mobiles and fort turrets once, tagging each
        # with the target classes its weapons can engage (air / sub / surface).
        c = canvas
        self.mobiles: list[tuple] = []            # (key, sdef, cost, caps)
        for key in c.enemy_unlocked:
            sdef = c.data.vehicles.get(key)
            if sdef is None:
                continue
            if getattr(sdef, "unit_type", "ship") in ("turret", "structure"):
                continue
            self.mobiles.append((key, sdef, sdef.cost, self._caps(sdef)))

        self.turrets: dict[str, tuple] = {}       # key -> (sdef, slot_kind, caps)
        for key in ("missile_battery", "aa_turret", "torpedo_battery",
                    "coastal_artillery"):
            sdef = c.data.vehicles.get(key)
            if sdef is None or key not in c.enemy_unlocked:
                continue
            slot = ("under" if getattr(sdef, "turret_slot", "surface") == "underwater"
                    else "over")
            self.turrets[key] = (sdef, slot, self._caps(sdef))

    # ── Capability read ───────────────────────────────────────────────────────
    @staticmethod
    def _caps(sdef) -> tuple:
        """(can_hit_air, can_hit_sub, can_hit_surface) for a unit/turret def."""
        air = sub = surf = False
        for a in getattr(sdef, "attacks", []):
            if getattr(a, "can_hit_plane", False):   air = True
            if getattr(a, "can_hit_sub", False):     sub = True
            if getattr(a, "can_hit_surface", False): surf = True
        return air, sub, surf

    # ── Per-frame entry point (called from BattleCanvas._update) ───────────────
    def update(self, dt: float):
        c = self.c
        if c.game_over or c.dying is not None:
            return
        self._t += dt
        if self._t < self._next:
            return
        # Act on a loose cadence with jitter so the bot reads as a person issuing
        # orders, not a frame-perfect machine.
        self._next = self._t + random.uniform(0.5, 0.9)
        try:
            self._think()
        except Exception:
            # A bot slip must never take down the live battle – swallow and retry
            # next tick.
            pass

    # ── One decision pass ──────────────────────────────────────────────────────
    # The bot banks toward ONE clear goal at a time – the next income tier, then a
    # turret to wall off a threat, then a Bastion, then the rest of the upgrade plan
    # – and spends only genuine SURPLUS (money beyond that goal) on chip defence and
    # offence. This savings discipline is what lets an income-limited commander ever
    # afford a 1600-credit battery: without it, dribbling every credit onto cheap
    # hulls keeps the bank flat forever. At most one major order per tick, so its
    # order rate reads like a person at the console.
    def _think(self):
        c = self.c
        en = c.enemy
        inc = en.income
        threat = self._assess_threats()
        cov = self._coverage()
        gaps = {k: threat[k] - cov[k] * 1.5 for k in threat}
        worst = max(gaps.values())
        worst_cat = max(gaps, key=lambda k: gaps[k])

        goal = self._savings_goal(worst, worst_cat)

        # A goal the bank can't even HOLD (its cost exceeds the storage cap) is a
        # deadlock – income just overflows at the cap and the purchase never lands.
        # A human lifts the warehouse first; so does the bot, redirecting to a
        # storage upgrade until the cap clears the goal.
        if goal is not None and goal[2] > en.max_res - 40.0:
            scost = self._track_cost("storage")
            if (scost is not None and en.upgrades.get("storage", 0) < c.max_upg_lvl
                    and "storage" not in c.pending_upg["enemy"]):
                goal = ("upgrade", "storage", scost)

        # 1) Buy the goal the moment it's affordable (these all re-check cost and
        #    no-op if we're not there yet, so we simply fall through to save more).
        if goal is not None:
            kind, key, _cost = goal
            if kind == "upgrade" and self._buy_track(key, 0.0):
                return
            if kind == "turret" and self._place_turret_key(key, 0.0):
                return
            if kind == "bastion" and self._build_bastion():
                return

        goal_cost = goal[2] if goal else 0.0

        # 2) A genuine push gets an immediate chip counter-hull, throttled so it can't
        #    nibble the savings away between real attacks.
        if (worst > 1.2 and self._t - self._last_counter > 4.0
                and self._deploy_counter(worst_cat, 60.0)):
            self._last_counter = self._t
            return

        # 2.5) SEIZE the midfield: whenever the scoring platform isn't paying our bank,
        #      push a surface hull to contest it – the mode's win condition (the score
        #      leader is handed a punishment flagship), so the bot fights for the cap
        #      directly rather than hoping its attack fleet drifts through the lane.
        #      Reserve a slice of the savings goal so seizing it doesn't starve the
        #      next turret/upgrade, but keep it a SMALL slice so the push stays real.
        cap = self._cap()
        if (cap is not None and cap.income_owner != "enemy"
                and self._t - self._last_cap > self._cap_interval(cap)
                and self._capture_push(min(goal_cost, 400.0))):
            self._last_cap = self._t
            return

        # 3) Keep a STANDING FLEET on the water – forward defence that meets the
        #    player's hulls in midfield instead of letting them reach the fort, and
        #    the bot's own offence. Throttled and reserved so it grows the fleet
        #    steadily WITHOUT starving the savings goal (turrets stay affordable).
        if self._maintain_fleet(threat, goal_cost):
            return

        # 4) A clear surplus beyond the goal funds an extra offensive hull.
        self._attack(threat, max(goal_cost + 60.0, 200.0 + inc * 2.0))

    # ── 3) Standing fleet upkeep ───────────────────────────────────────────────
    def _maintain_fleet(self, threat: dict, goal_cost: float) -> bool:
        """Deploy toward a modest live-fleet target on a throttle. The reserve keeps
        most of the current savings goal intact, so upkeep skims income for forward
        defence while the bank still climbs toward the next turret/upgrade."""
        c = self.c
        if self._t - self._last_deploy < random.uniform(4.0, 6.0):
            return False
        target = min(c._fleet_cap("enemy"), 3 + int(c.game_time // 30.0))
        if c._committed("enemy") >= target:
            return False
        # Protect the savings goal, but never hoard the whole bank for it – cap the
        # protected slice so a costly goal (a Bastion) can't freeze fleet upkeep.
        reserve = min(goal_cost, 500.0) + 100.0
        if self._attack(threat, reserve):
            self._last_deploy = self._t
            return True
        return False

    # ── What the bot is currently saving toward ────────────────────────────────
    def _savings_goal(self, worst: float, worst_cat: str):
        """The single most valuable purchase the bot is banking toward right now, as
        (kind, key, cost). Priority: rush the early income tiers (the foundation) →
        wall the fort with turrets whenever a threat looms or the base is taking
        damage (the heart of "defend well") → keep the economy plan rolling → and,
        only when safe and the fort is already full, a luxury Bastion for extra
        decks. None when there's nothing left worth saving for."""
        c = self.c
        en = c.enemy
        hp_frac = (en.base_hp / en.max_base_hp) if en.max_base_hp else 1.0
        # 1) Rush income while it's still low – nothing else is affordable until it
        #    climbs, so a human takes the first few tiers straight away.
        if en.upgrades.get("resource", 0) < 3 and "resource" not in c.pending_upg["enemy"]:
            cost = self._track_cost("resource")
            if cost is not None:
                return ("upgrade", "resource", cost)
        # 2) DEFEND: while any threat is bearing down OR the base has taken damage,
        #    fill the fort with turrets – cover the most-threatened class first, then
        #    seat versatile batteries in any remaining slot. Turrets are the most
        #    cost-effective defence per credit, so this comes before further economy.
        if worst > 0.4 or hp_frac < 0.92:
            tg = self._defense_turret_goal(worst_cat)
            if tg is not None:
                return ("turret", tg[0], tg[1])
        # 3) Continue the wider upgrade plan in priority order.
        for key in self.UPGRADE_PRIO:
            if (en.upgrades.get(key, 0) < c.max_upg_lvl
                    and key not in c.pending_upg["enemy"]):
                cost = self._track_cost(key)
                if cost is not None:
                    return ("upgrade", key, cost)
        # 4) Luxury: a Bastion for two more turret decks – only when the base is
        #    healthy and every fort slot is already filled, so it genuinely EXTENDS
        #    the wall rather than delaying it.
        fort_full = (not self._free_slots(c.enemy_over_slots)
                     and not self._free_slots(c.enemy_under_slots))
        if (hp_frac > 0.85 and fort_full and c.game_time > 45.0
                and "oilrig" in c.enemy_unlocked and not c._has_oilrig("enemy")
                and self._ready("oilrig")):
            sdef = c.data.vehicles.get("oilrig")
            if sdef is not None:
                return ("bastion", "oilrig", sdef.cost)
        return None

    def _defense_turret_goal(self, worst_cat: str):
        """(key, cost) of the turret the bot should bank for to shore up its defence:
        first one that covers the most-threatened class, else a versatile battery to
        fill any remaining free slot. None when the fort is full or all fitting
        turrets are cooling."""
        c = self.c
        tg = self._turret_goal(worst_cat)              # cover the pressing class first
        if tg is not None:
            return tg
        # No slot for the threatened class (or it's already covered) – put a general
        # missile battery on any free surface/deck slot, or torpedoes below.
        if (self._free_slots(c.enemy_over_slots) or self._free_rig_node() is not None):
            info = self.turrets.get("missile_battery")
            if info is not None and self._ready("missile_battery"):
                return "missile_battery", info[0].cost
        if self._free_slots(c.enemy_under_slots):
            info = self.turrets.get("torpedo_battery")
            if info is not None and self._ready("torpedo_battery"):
                return "torpedo_battery", info[0].cost
        return None

    def _track_cost(self, key: str):
        c = self.c
        lv = c.enemy.upgrades.get(key, 0)
        try:
            from .config import UPGRADE_COSTS
            return float(UPGRADE_COSTS[key][lv])
        except (KeyError, IndexError):
            return None

    def _turret_goal(self, cat: str):
        """(key, cost) of the best turret for `cat` that has a free slot to stand on
        and is off cooldown, or None. Used both to size the savings goal and to keep
        the bot from banking for a turret it has nowhere to put."""
        c = self.c
        for key in self._TURRET_FOR.get(cat, []):
            info = self.turrets.get(key)
            if info is None or not self._ready(key):
                continue
            sdef, kind, _ = info
            if kind == "under":
                if self._free_slots(c.enemy_under_slots):
                    return key, sdef.cost
            else:
                if (self._free_slots(c.enemy_over_slots)
                        or self._free_rig_node() is not None):
                    return key, sdef.cost
        return None

    # ── Threat & coverage assessment ───────────────────────────────────────────
    def _assess_threats(self) -> dict:
        """Weighted counts of the player units bearing down on the enemy fort, split
        by class. Units closer to our base weigh far more than distant ones, so the
        bot reacts to a real push, not to a stray scout on the far side of the map."""
        c = self.c
        air = sub = surf = 0.0
        for s in c.ships:
            if getattr(s, "team", "") != "player" or not getattr(s, "alive", False):
                continue
            if getattr(s, "is_base", False) or getattr(s, "is_rig", False):
                continue
            sdef = getattr(s, "sdef", None)
            if sdef is None:
                continue
            ut = getattr(sdef, "unit_type", "ship")
            if ut in ("turret", "structure"):
                continue
            frac = s.x / WORLD_W                    # 0 at player shore, 1 at ours
            w = 0.3 + 1.7 * max(0.0, min(1.0, (frac - 0.35) / 0.65))
            if getattr(sdef, "is_boss", False):
                w *= 2.5                            # a flagship is the gravest surface threat
            if ut == "plane":
                air += w
            elif ut == "submarine":
                sub += w
            else:
                surf += w
        return {"air": air, "sub": sub, "surf": surf}

    def _coverage(self) -> dict:
        """How many standing enemy turrets can engage each class right now."""
        c = self.c
        air = sub = surf = 0.0
        for s in c.ships:
            if getattr(s, "team", "") != "enemy" or not getattr(s, "alive", False):
                continue
            sdef = getattr(s, "sdef", None)
            if sdef is None or getattr(sdef, "unit_type", "") != "turret":
                continue
            a, su, sf = self._caps(sdef)
            if a:  air += 1
            if su: sub += 1
            if sf: surf += 1
        return {"air": air, "sub": sub, "surf": surf}

    # ── Defence: turret placement / counter-hull ───────────────────────────────
    def _place_turret_key(self, key: str, reserve: float) -> bool:
        """Seat a specific turret in a fitting free slot – its underwater step for a
        torpedo battery, else a free surface pad or a Bastion deck. Reports whether
        it actually went down (affordability / cooldown / slot are all re-checked)."""
        c = self.c
        info = self.turrets.get(key)
        if info is None:
            return False
        sdef, kind, _ = info
        if c.enemy.resources < sdef.cost + reserve or not self._ready(key):
            return False
        if kind == "under":
            free = self._free_slots(c.enemy_under_slots)
            return bool(free) and self._do_place(key, "under", free[0])
        free = self._free_slots(c.enemy_over_slots)
        if free:
            return self._do_place(key, "over", free[0])
        node = self._free_rig_node()                # a Bastion deck, if we have one
        if node is not None:
            return self._do_place(key, "rig_node", 0, rig_nid=node[0], node=node[1])
        return False

    def _deploy_counter(self, cat: str, reserve: float) -> bool:
        """Muster the cheapest ready hull whose guns answer `cat` – the poor-bot
        response to a threat it can't yet wall off with a turret."""
        capi = {"air": 0, "sub": 1, "surf": 2}[cat]
        best = None
        for key, sdef, cost, caps in self.mobiles:
            if not caps[capi]:
                continue
            if not self._affordable(sdef, reserve) or not self._ready(key):
                continue
            if best is None or cost < best[2]:
                best = (key, sdef, cost)
        if best is None:
            return False
        return self._deploy(best[0])

    # ── 2) Fortify: a Bastion shield ───────────────────────────────────────────
    def _build_bastion(self) -> bool:
        c = self.c
        if c.game_time < 45 or "oilrig" not in c.enemy_unlocked:
            return False
        sdef = c.data.vehicles.get("oilrig")
        if sdef is None or c._has_oilrig("enemy"):
            return False
        if not self._ready("oilrig"):
            return False
        if c.enemy.resources < sdef.cost + 300.0:   # a small cushion past the price
            return False
        before = len(c._team_rigs("enemy"))
        c._apply_place("enemy", "oilrig", "rig")
        return len(c._team_rigs("enemy")) > before

    # ── Investment: base upgrades ──────────────────────────────────────────────
    def _buy_track(self, key: str, buffer: float) -> bool:
        """Buy the next level of one specific upgrade track if it's affordable with
        `buffer` to spare, not already maxed, and not already researching."""
        c = self.c
        en = c.enemy
        lv = en.upgrades.get(key, 0)
        if lv >= c.max_upg_lvl or key in c.pending_upg["enemy"]:
            return False
        try:
            from .config import UPGRADE_COSTS
            cost = UPGRADE_COSTS[key][lv]
        except (KeyError, IndexError):
            return False
        if en.resources < cost + buffer:
            return False
        return c.request_upgrade("enemy", key)

    # ── Capture: seize the midfield oil platform ───────────────────────────────
    def _cap(self):
        """The central scoring platform (there is exactly one in a head-to-head
        match), or None outside that mode. Reading it straight off the canvas lets
        the bot fight for the cap the same way it reads threats off the ship list."""
        c = self.c
        if not getattr(c, "score_mode", False) or not c.capture_points:
            return None
        return c.capture_points[0]

    def _cap_interval(self, cap) -> float:
        """Seconds to wait between capture pushes – short while the player is
        actively prying the platform away (their ships on it, or they hold it), long
        while it merely sits neutral. So the bot floods hulls exactly when the cap is
        in play instead of trickling them out on a flat cadence."""
        losing = (cap.income_owner == "player"
                  or cap.near.get("player", 0) > cap.near.get("enemy", 0))
        return random.uniform(1.5, 2.5) if losing else random.uniform(3.0, 4.5)

    def _capture_push(self, reserve: float) -> bool:
        """Send one SURFACE hull toward the platform to seize/hold it. Submarines,
        planes and turrets never drag the capture meter, so they're skipped; among
        the surface hulls we can afford and that are off cooldown, lean hard toward
        heavy capture_weight (a Hovercraft pulls as two hulls) while keeping a little
        diversity. Reports whether a hull actually launched."""
        c = self.c
        if c._committed("enemy") >= c._fleet_cap("enemy"):
            return False
        budget = c.enemy.resources - reserve
        if budget <= 0:
            return False
        pool, weights = [], []
        for key, sdef, cost, _caps in self.mobiles:
            if getattr(sdef, "unit_type", "ship") != "ship":
                continue                                  # only surface hulls capture
            if cost > budget or not self._ready(key):
                continue
            cw = float(getattr(sdef, "capture_weight", 1.0))
            w = cw ** 1.5                                  # bias hard toward heavy cappers
            w *= 0.5 ** self._recent.count(key)            # but don't clone one boat
            pool.append(key); weights.append(max(0.05, w))
        if not pool:
            return False
        key = random.choices(pool, weights=weights, k=1)[0]
        return self._deploy(key)

    # ── Offence: pressure the player ───────────────────────────────────────────
    def _attack(self, threat: dict, reserve: float) -> bool:
        c = self.c
        if c._committed("enemy") >= c._fleet_cap("enemy"):
            return False
        budget = c.enemy.resources - reserve
        if budget <= 0:
            return False
        pool, weights = [], []
        for key, sdef, cost, caps in self.mobiles:
            if cost > budget or not self._ready(key):
                continue
            air_c, sub_c, _ = caps
            w = 1.0
            # Lean toward hulls that also answer what the player is fielding.
            if threat["air"] > 1.0 and air_c:
                w += 1.0
            if threat["sub"] > 1.0 and sub_c:
                w += 0.8
            # Diversity: discourage spamming the same hull so a push reads as a
            # mixed task force rather than one boat cloned.
            w *= 0.4 ** self._recent.count(key)
            pool.append(key)
            weights.append(max(0.05, w))
        if not pool:
            return False
        key = random.choices(pool, weights=weights, k=1)[0]
        return self._deploy(key)

    # ── Order primitives (mirror the human client's cmd verbs) ─────────────────
    def _ready(self, key: str) -> bool:
        return self.c.build_cd["enemy"].get(key, 0.0) <= 0

    def _affordable(self, sdef, reserve: float) -> bool:
        return self.c.enemy.resources >= sdef.cost + reserve

    @staticmethod
    def _free_slots(slots) -> list:
        return [i for i, occ in enumerate(slots) if occ is None]

    def _free_rig_node(self):
        """(rig_nid, node_idx) of a free deck node on a finished enemy Bastion, or
        None. Stamps the rig with a network id first if it lacks one, since in a
        solo match the snapshot serializer (which normally assigns ids) never runs
        – _apply_place looks the rig up by that id."""
        c = self.c
        for rig in c._team_rigs("enemy"):
            if getattr(rig, "under_construction", False):
                continue
            nid = getattr(rig, "_nid", None)
            if nid is None:
                c._nid_seq += 1
                nid = c._nid_seq
                rig._nid = nid
            for ni, nt in enumerate(getattr(rig, "node_turrets", [])):
                if nt is None:
                    return nid, ni
        return None

    def _do_place(self, key: str, kind: str, idx: int, rig_nid=None, node=None) -> bool:
        """Seat a turret and report whether the slot actually took it (the host-side
        _apply_place silently drops an order that fails a cost / cooldown / slot
        re-check, so we confirm before trusting it)."""
        c = self.c
        before = len(self._enemy_turrets())
        c._apply_place("enemy", key, kind, idx=idx, rig_nid=rig_nid, node=node)
        return len(self._enemy_turrets()) > before

    def _enemy_turrets(self) -> list:
        return [s for s in self.c.ships
                if getattr(s, "team", "") == "enemy" and getattr(s, "alive", False)
                and getattr(getattr(s, "sdef", None), "unit_type", "") == "turret"]

    def _deploy(self, key: str) -> bool:
        c = self.c
        if c._committed("enemy") >= c._fleet_cap("enemy"):
            return False
        if not self._ready(key):
            return False
        sdef = c.data.vehicles.get(key)
        if sdef is None or c.enemy.resources < sdef.cost:
            return False
        c._apply_deploy("enemy", key)
        self._recent = (self._recent + [key])[-6:]
        return True
