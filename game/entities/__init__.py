"""Game entities: projectiles, ships, bosses, explosions and factions."""

from .projectile import Projectile, attack_can_hit
from .explosion import Explosion, LaunchSmoke, MuzzleSmoke, Splash, Wake
from .faction import Faction
from .ship import Ship
from .boss import Boss
from .base import BaseTarget
from .oilrig import OilRig
from .capturepoint import CapturePoint, diminishing_total
from .fortress import FortressView

__all__ = ["Projectile", "attack_can_hit", "Explosion", "LaunchSmoke", "MuzzleSmoke", "Splash", "Wake",
           "Faction", "Ship", "Boss", "BaseTarget", "OilRig", "CapturePoint",
           "diminishing_total", "FortressView"]
