"""
game.registry – discovers and loads the JSON configs into typed registries.

`GameData()` scans:
    vehicles/*.json          -> deployable units
    vehicles/bosses/*.json   -> bosses
    levels/*.json            -> levels
    vehicles/_deploy_order.json -> order of the deploy bar (optional)

Files whose name starts with "_" are treated as metadata, not vehicles.
"""

from __future__ import annotations
import os, json

from . import config
from .models import ShipDef, LevelDef, ConfigError


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_ship_dir(path: str) -> dict[str, ShipDef]:
    out: dict[str, ShipDef] = {}
    if not os.path.isdir(path):
        return out
    for fn in sorted(os.listdir(path)):
        if not fn.endswith(".json") or fn.startswith("_"):
            continue
        full = os.path.join(path, fn)
        if not os.path.isfile(full):
            continue
        try:
            sdef = ShipDef.from_dict(_read_json(full), source=fn)
        except (ConfigError, json.JSONDecodeError, OSError) as e:
            print(f"[Config] failed to load {fn}: {e}")
            continue
        out[sdef.key] = sdef
    return out


class GameData:
    """One-shot load of every config. Construct once and share."""

    def __init__(self):
        self.vehicles = _load_ship_dir(config.VEHICLE_DIR)
        self.bosses   = _load_ship_dir(config.BOSS_CFG_DIR)
        self.levels   = self._load_levels()
        self.deploy_order = self._load_deploy_order()
        self.all_units = {**self.vehicles, **self.bosses}

    def _load_levels(self) -> dict[str, LevelDef]:
        out: dict[str, LevelDef] = {}
        d = config.LEVEL_DIR
        if not os.path.isdir(d):
            return out
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json") or fn.startswith("_"):
                continue
            full = os.path.join(d, fn)
            try:
                lvl = LevelDef.from_dict(_read_json(full), source=fn)
            except (ConfigError, json.JSONDecodeError, OSError) as e:
                print(f"[Config] failed to load level {fn}: {e}")
                continue
            out[lvl.key] = lvl
        return out

    def _load_deploy_order(self) -> list[str]:
        path = os.path.join(config.VEHICLE_DIR, "_deploy_order.json")
        try:
            order = _read_json(path)
            order = [k for k in order if k in self.vehicles]
        except (OSError, json.JSONDecodeError):
            order = []
        # Append any vehicles not listed so nothing silently disappears.
        for k in self.vehicles:
            if k not in order:
                order.append(k)
        return order

    def levels_sorted(self) -> list[LevelDef]:
        return sorted(self.levels.values(), key=lambda l: (l.order, l.name))

    def campaign_stage(self, level_key: str) -> int:
        """This level's 0-based position in the campaign.

        It doubles as the unlock stage that was reached *before* arriving here:
        on a fresh playthrough the Nth level (index N) is entered having cleared
        N earlier levels, so `progression.unlocked_keys(stage)` is exactly the
        roster the player has available before and during this level. The enemy
        economy is pinned to this – independent of the player's global progress.
        """
        order = [l.key for l in self.levels_sorted()]
        return order.index(level_key) if level_key in order else 0
