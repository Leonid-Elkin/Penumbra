"""
game.entities.capturepoint – a capturable midfield oil platform.

Three of these neutral derricks stand in the open water between the two bases
(from the campaign level set in capture_points.json). Ownership is a single
signed meter: -1 fully ENEMY … 0 neutral … +1 fully PLAYER. When only one side's
surface ships are in a rig's capture lane, the meter is dragged toward that side;
more ships nearby drag it faster (up to a cap). Because the meter is a single bar
through zero, a rig always has to be neutralised before the other side can flip
it. A rig you hold pays out `income_per_rig` gold/sec to your economy.

These stand OUTSIDE the ship list – they are objectives, not targets – so no
weapon fires on them and no AI/collision code has to know they exist. The battle
canvas owns the list, ticks their capture state, feeds income to each owner and
paints them (see BattleCanvas._update_capture / _draw_capture_points).
"""

from __future__ import annotations
import math
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui  import QColor, QPen, QFont

# Owner signal colours mirror the rest of the war-room theme: amber = player,
# red = hostile, steel = uncontested neutral.
_AMBER   = QColor("#f0a81e")
_RED     = QColor("#d24338")
_STEEL   = QColor("#7c8b98")
_BODY    = QColor("#2b3742")   # neutral gunmetal derrick
_DECK    = QColor("#3a4a56")

DISP_W = 84
DISP_H = 96


def diminishing_total(unit: float, k: int, falloff: float) -> float:
    """Total value of holding `k` rigs when each successive rig is worth `falloff`×
    the previous one: unit·(1 + f + f² + …). `falloff` == 1 gives the linear k·unit;
    < 1 makes the 2nd/3rd rig pay progressively less (the anti-hoard lever)."""
    total = 0.0
    w = 1.0
    for _ in range(max(0, int(k))):
        total += unit * w
        w *= falloff
    return total


class CapturePoint:
    is_capture_point = True

    def __init__(self, x: float, water_y: float, cfg: dict, water_deep=None,
                 home: str = "neutral"):
        self.x        = float(x)
        self.water_y  = float(water_y)
        self.water_deep = water_deep
        self.home     = home                 # "player" | "enemy" | "neutral" home water
        self.alive    = True                 # never destroyed – an objective, not a target

        self.progress = 0.0                  # -1 (enemy) … 0 (neutral) … +1 (player)
        # Sticky income holder: whoever last drove the meter fully to their side keeps
        # earning until it is dragged ALL THE WAY back through neutral. Decapping a rig
        # only stops paying its old owner once the meter reaches 0 – not the instant a
        # rival ship starts prying it back. Set at full capture, cleared at neutral.
        self._income_owner = "neutral"
        self.contested = False
        self.near     = {"player": 0, "enemy": 0}
        self._flash   = 0.0                  # brief bloom when ownership changes

        self.cap_seconds  = max(0.5, float(cfg.get("capture_seconds", 9.0)))
        self.radius       = max(40.0, float(cfg.get("capture_radius", 320.0)))
        self.ship_speedup = max(0.0, float(cfg.get("ship_speedup", 0.35)))
        self.max_speedup  = max(1.0, float(cfg.get("max_ship_speedup", 3.0)))
        self.income_per_rig = max(0.0, float(cfg.get("income_per_rig", 12.0)))
        self.decay_seconds  = max(0.0, float(cfg.get("decay_seconds", 0.0)))
        # Comeback: the side holding FEWER rigs seizes faster (anti-snowball).
        self.comeback_per_rig = max(0.0, float(cfg.get("comeback_per_rig", 0.6)))
        self.max_comeback     = max(1.0, float(cfg.get("max_comeback", 2.5)))
        # Home-field: the side that owns this half of the map seizes/holds faster.
        self.home_advantage   = max(1.0, float(cfg.get("home_advantage", 2.0)))

    # ── State ────────────────────────────────────────────────────────────────
    @property
    def owner(self) -> str:
        if self.progress >= 1.0:  return "player"
        if self.progress <= -1.0: return "enemy"
        return "neutral"

    @property
    def income_owner(self) -> str:
        """Who this rig PAYS. Unlike `owner` (which flips to neutral the instant the
        meter leaves ±1), this stays with the last side to fully capture the rig until
        it is decapped all the way back through neutral – so partial decapping steals
        no income, you have to neutralise the platform to cut its payout."""
        return self._income_owner

    @property
    def disp_w(self): return DISP_W
    @property
    def disp_h(self): return DISP_H

    @property
    def top_y(self): return self.water_y - DISP_H

    def set_water(self, wy: float): self.water_y = float(wy)

    def neutralize(self):
        """Force the platform COMPLETELY neutral this frame: meter snapped to 0, no
        live owner, no sticky income holder, no contest, no ships credited. Used
        while a punishment flagship boss is live on either side so the midfield is
        taken fully off the board – nobody holds, captures or earns from it – until
        the boss cycle is resolved. Blooms the ownership flash if it was held."""
        if self.owner != "neutral":
            self._flash = 1.0
        self.progress = 0.0
        self._income_owner = "neutral"
        self.contested = False
        self.near = {"player": 0, "enemy": 0}

    def update(self, dt: float, player_near: int, enemy_near: int,
               player_rigs: int = 0, enemy_rigs: int = 0,
               player_weight: float | None = None, enemy_weight: float | None = None):
        """Advance the capture meter for one frame given how many of each side's
        ships are in the lane, plus how many rigs each side currently holds (for the
        comeback bonus). `player_weight`/`enemy_weight` are the summed per-hull
        capture_weights of those ships (a Hovercraft pulls as 2 hulls); when omitted
        they default to the plain counts, so weight-1 fleets behave exactly as before.
        Returns True when the OWNER changed this frame."""
        self.near = {"player": player_near, "enemy": enemy_near}
        self.contested = player_near > 0 and enemy_near > 0
        prev = self.owner

        if player_weight is None: player_weight = float(player_near)
        if enemy_weight  is None: enemy_weight  = float(enemy_near)

        if player_near > 0 and enemy_near == 0:
            n, d, w = player_near, 1.0, player_weight
        elif enemy_near > 0 and player_near == 0:
            n, d, w = enemy_near, -1.0, enemy_weight
        else:
            n, d, w = 0, 0.0, 0.0

        if d != 0.0:
            # More ships → a faster seize, capped so a swarm can't take it instantly.
            # The crowd bonus is scaled by the lane's AVERAGE capture weight, so a lone
            # Hovercraft (weight 2) seizes at 2× a lone destroyer's pace.
            avg_weight = w / n if n > 0 else 1.0
            mult = min(self.max_speedup, (1.0 + (n - 1) * self.ship_speedup) * avg_weight)
            # Comeback: the capturing side seizes faster the more rigs it is BEHIND,
            # so a team that lost the midfield can fight one back (anti-snowball).
            behind = (enemy_rigs - player_rigs) if d > 0 else (player_rigs - enemy_rigs)
            mult *= min(self.max_comeback, 1.0 + self.comeback_per_rig * max(0, behind))
            # Home-field: the side whose waters this rig sits in captures it faster;
            # the invader crossing over is slowed by the reciprocal. So you keep your
            # own rig even when out-numbered, and the centre rig is fought fair.
            if self.home != "neutral" and self.home_advantage != 1.0:
                capturing = "player" if d > 0 else "enemy"
                mult *= self.home_advantage if capturing == self.home else 1.0 / self.home_advantage
            self.progress = max(-1.0, min(1.0, self.progress + d * (mult / self.cap_seconds) * dt))
        elif not self.contested and self.decay_seconds > 0.0 and self.progress != 0.0:
            # Unattended rig slowly bleeds back toward neutral (if enabled).
            step = dt / self.decay_seconds
            if self.progress > 0.0: self.progress = max(0.0, self.progress - step)
            else:                   self.progress = min(0.0, self.progress + step)

        # Sticky income holder. A full capture (meter pinned to a side) claims the
        # payout; it is only surrendered once the meter is pried back to neutral (0).
        # In between – the rig half-decapped – the last full owner keeps earning.
        if self.progress >= 1.0:
            self._income_owner = "player"
        elif self.progress <= -1.0:
            self._income_owner = "enemy"
        elif (self._income_owner == "player" and self.progress <= 0.0) or \
             (self._income_owner == "enemy"  and self.progress >= 0.0):
            self._income_owner = "neutral"

        if self._flash > 0.0:
            self._flash = max(0.0, self._flash - dt * 1.6)
        changed = self.owner != prev
        if changed:
            self._flash = 1.0
        return changed

    # ── Draw (world space; the canvas has already viewport-culled us) ──────────
    def draw(self, p, cam_x: float):
        from .fortress import _SeaFog
        cx = self.x - cam_x
        wy = self.water_y
        owner = self.owner
        sig = _AMBER if owner == "player" else _RED if owner == "enemy" else _STEEL

        deck_y = wy - 42.0
        half   = 34
        dev = p.device()
        bottom = float(dev.height()) if dev is not None else wy + 240.0
        fog = _SeaFog(wy, max(bottom, wy + 60.0), self.water_deep)

        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        # Two caisson legs down to the seabed, fogging into the water with depth.
        leg_xs = (int(cx - half + 8), int(cx + half - 8))
        for lxc in leg_xs:
            y = deck_y + 6
            while y < bottom:
                h = min(20, bottom - y)
                p.fillRect(lxc - 6, int(y), 12, int(h), fog.tint(_BODY.darker(135), y, 0.86, 0.55))
                y += 20
        # Cross-brace at the waterline + foam where the legs break the surface.
        p.fillRect(leg_xs[0], int(wy + 8), leg_xs[1] - leg_xs[0], 5,
                   fog.tint(_BODY.darker(135), wy + 8, 0.86, 0.55))
        foam = QColor(255, 255, 255, 110)
        for lxc in leg_xs:
            p.fillRect(lxc - 8, int(wy - 1), 16, 2, foam)

        # Platform deck slab with a signal leading edge in the owner's colour.
        p.fillRect(int(cx - half), int(deck_y), half * 2, 12, _DECK)
        p.fillRect(int(cx - half), int(deck_y), half * 2, 4, _DECK.lighter(150))
        p.setPen(QPen(sig, 2)); p.drawLine(int(cx - half), int(deck_y), int(cx + half), int(deck_y))
        p.setPen(Qt.PenStyle.NoPen)

        # Derrick: an X-braced steel tower tapering to an apex, oil-rig style.
        apex_y = deck_y - 66.0
        base_l, base_r = cx - half + 8, cx + half - 8
        top_l,  top_r  = cx - 8, cx + 8
        p.setPen(QPen(_BODY.lighter(150), 2))
        p.drawLine(int(base_l), int(deck_y), int(top_l), int(apex_y))
        p.drawLine(int(base_r), int(deck_y), int(top_r), int(apex_y))
        # Horizontal rungs + alternating cross-braces up the tower.
        rungs = 4
        for i in range(1, rungs + 1):
            t0 = i / (rungs + 1)
            lx = base_l + (top_l - base_l) * t0; rx = base_r + (top_r - base_r) * t0
            ry = deck_y + (apex_y - deck_y) * t0
            p.setPen(QPen(_BODY.lighter(140), 1.4))
            p.drawLine(int(lx), int(ry), int(rx), int(ry))
            t1 = (i - 1) / (rungs + 1)
            plx = base_l + (top_l - base_l) * t1; prx = base_r + (top_r - base_r) * t1
            pry = deck_y + (apex_y - deck_y) * t1
            p.setPen(QPen(_BODY.lighter(120), 1))
            if i % 2: p.drawLine(int(plx), int(pry), int(rx), int(ry))
            else:     p.drawLine(int(prx), int(pry), int(lx), int(ry))

        # Crown block + beacon lamp at the apex, lit in the owner's colour.
        p.setPen(Qt.PenStyle.NoPen)
        p.fillRect(int(cx - 9), int(apex_y - 6), 18, 6, _BODY.lighter(150))
        halo = QColor(sig); halo.setAlpha(70 + int(60 * self._flash))
        p.setBrush(halo); p.drawEllipse(int(cx - 6), int(apex_y - 15), 13, 13)
        p.setBrush(sig);  p.drawEllipse(int(cx - 3), int(apex_y - 12), 7, 7)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # Capture readout floating clear above the derrick.
        self._draw_meter(p, cx, apex_y - 26.0, sig, owner)
        p.restore()

    def _draw_meter(self, p, cx: float, y: float, sig: QColor, owner: str):
        w = 76.0; h = 7.0
        x0 = cx - w / 2.0
        # Recessed track + neutral centre notch.
        p.setPen(Qt.PenStyle.NoPen)
        p.fillRect(QRectF(x0, y, w, h), QColor(8, 12, 15, 210))
        half = w / 2.0
        # Signed fill from the centre toward the leading side.
        frac = max(-1.0, min(1.0, self.progress))
        if frac > 0:
            fill = QColor(_AMBER); p.fillRect(QRectF(cx, y, half * frac, h), fill)
        elif frac < 0:
            fw = half * (-frac); fill = QColor(_RED); p.fillRect(QRectF(cx - fw, y, fw, h), fill)
        # Centre tick + bezel (yellow, pulsing, when contested).
        p.setPen(QPen(QColor(230, 236, 240, 200), 1)); p.drawLine(int(cx), int(y), int(cx), int(y + h))
        if self.contested:
            a = 140 + int(90 * (0.5 + 0.5 * math.sin(self._flash * 6.0)))
            p.setPen(QPen(QColor(255, 210, 60, min(255, a)), 1.4))
        else:
            p.setPen(QPen(QColor(sig).darker(110), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(x0, y, w, h))
        # Label: owner, contest state, or the ship tug-of-war count.
        if self.contested:
            txt = f"CONTESTED  {self.near['player']}v{self.near['enemy']}"
            col = QColor(255, 210, 60)
        elif owner == "player":
            txt = "OIL PLATFORM · YOURS"; col = _AMBER
        elif owner == "enemy":
            txt = "OIL PLATFORM · ENEMY"; col = _RED
        else:
            tag = ("YOUR WATERS" if self.home == "player"
                   else "ENEMY WATERS" if self.home == "enemy" else "NEUTRAL")
            txt = "OIL PLATFORM · " + tag
            col = _AMBER if self.home == "player" else _RED if self.home == "enemy" else _STEEL
        p.setPen(col); p.setFont(QFont("Arial", 7, QFont.Weight.Bold))
        p.drawText(QRectF(cx - 90, y - 15, 180, 12),
                   int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), txt)
