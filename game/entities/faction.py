"""
game.entities.faction — per-side economy, upgrades, base HP and boss state.
"""

from __future__ import annotations
from ..config import (INCOME_T, STORAGE_T, BASEHP_T, UPGRADE_COSTS)


class Faction:
    def __init__(self, is_player: bool):
        self.is_player   = is_player
        # An opening purse — enough for a couple of early hulls, not a fleet.
        self.resources   = 180.0 if is_player else 140.0
        self.upgrades    = {k: 0 for k in UPGRADE_COSTS}
        self.base_hp     = float(BASEHP_T[0])
        self.max_base_hp = float(BASEHP_T[0])
        self.upg_timer   = 18.0

        # ── Boss / immunity state ──────────────────────────────────────────
        self.boss_spawned = False    # has this base's boss been deployed yet?
        self.boss_alive   = False    # is that boss currently on the field?
        self.immune       = False    # base takes no damage while True
        self.bonus_income = 0        # from live oil rigs

    @property
    def income(self) -> int:  return INCOME_T[self.upgrades["resource"]] + self.bonus_income
    @property
    def max_res(self) -> int: return STORAGE_T[self.upgrades["storage"]]
    @property
    def hlv(self) -> int:     return self.upgrades["health"]
