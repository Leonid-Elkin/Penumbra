"""
game.entities.base — the fortress as a real, targetable enemy.

Instead of draining HP on contact, units now treat the base like any other
target: they fire at it and projectiles deal damage. BaseTarget is a thin
stand-in that plugs into the same targeting/collision code as ships, delegating
its HP to the owning faction. The fort visuals are still drawn by the battle's
_draw_bases, so this entity draws nothing.
"""

from __future__ import annotations
from ..config import FORT_D_W


class _BaseSDef:
    """Minimal ShipDef-shaped descriptor so targeting/collision code works, and so
    the HUD info box can render a readout when the fort is hovered."""
    def __init__(self, key, team="player"):
        self.key          = key
        friendly          = team == "player"
        self.name         = "Command Fortress" if friendly else "Hostile Fortress"
        self.unit_type    = "ship"          # surface target: anti-surface weapons hit it
        self.aircraft_type = "loop"
        self.tags         = ["base", "surface", "structure"]
        self.display_w    = FORT_D_W
        self.display_h    = 220
        self.flip_player  = False
        self.flip_enemy   = False
        self.is_boss      = False
        self.cost         = 0
        self.speed        = 0
        self.hp           = 0               # live HP is shown via the field readout
        self.attacks      = []
        self.fire_points  = []
        self.description  = (
            "Your seat of command — lose it and the battle is lost. Armour upgrades "
            "add gun tiers and hull HP."
            if friendly else
            "The enemy stronghold. Batter down its HP to win the level; it is immune "
            "while a boss stands guard.")


class BaseTarget:
    """A stationary target whose HP is the faction's base HP."""
    is_base = True

    def __init__(self, team: str, faction, x: float):
        self.team    = team
        self.faction = faction
        self.x       = float(x)
        self.sdef    = _BaseSDef(f"{team}_base", team)
        self.water_y = 0.0
        # Negative id keeps it distinct from real ships (which use positive ids).
        self.id      = -10 if team == "player" else -11
        # The fort view + effective-tier lookup that define the hit silhouette;
        # bound by the battle once the FortressViews exist (see bind_fort).
        self._fort   = None
        self._hlv_fn = None

    # HP is the faction base HP; collision code reads/writes .hp directly.
    @property
    def hp(self): return self.faction.base_hp
    @hp.setter
    def hp(self, v): self.faction.base_hp = max(0.0, float(v))

    @property
    def max_hp(self): return self.faction.max_base_hp

    @property
    def alive(self): return self.faction.base_hp > 0
    @alive.setter
    def alive(self, _v): pass        # life is governed purely by base HP

    # While immune (boss alive) the base can't be targeted or hit.
    @property
    def invulnerable(self): return self.faction.immune

    @property
    def disp_w(self): return self.sdef.display_w
    @property
    def disp_h(self): return self.sdef.display_h

    @property
    def mid_y(self): return self.water_y - 70.0

    # Aim point for homing rounds — same fallback ships use when unmasked.
    @property
    def hit_com(self): return self.x, self.mid_y

    def set_water(self, wy: float): self.water_y = wy

    def bind_fort(self, fort, hlv_fn):
        """Wire this target to the FortressView that draws it and a callable giving
        the fort's current effective tier count, so the hitbox tracks the visible
        silhouette as armour upgrades add tiers."""
        self._fort = fort; self._hlv_fn = hlv_fn

    def hit_test(self, x: float, y: float) -> bool:
        """True if world point (x, y) strikes the fort. Uses the same silhouette the
        fort is drawn from, so the hitbox matches the base instead of the old padded
        bounding box (which reached into the sky above and the water in front). Falls
        back to that padded box only if no fort has been bound yet."""
        if self._fort is None:
            hw = self.disp_w * 0.52; hh = self.disp_h * 0.70
            return abs(x - self.x) < hw and abs(self.mid_y - y) < hh
        l, r, t, b = self._fort.hit_bounds(self.water_y, self._hlv_fn())
        return l <= x <= r and t <= y <= b

    # In the ships list but inert — the fort sprite is drawn elsewhere.
    def update(self, *a, **k): pass
    def draw(self, *a, **k): pass
