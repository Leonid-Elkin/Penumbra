"""
game.ballistics – pure projectile-aiming math (no Qt, no game state).
"""

from __future__ import annotations
import math


def ballistic_velocity(dx, dy, speed, g, high=True):
    """Launch velocity (vx, vy) to lob a projectile from (0,0) onto (dx, dy) under
    gravity g, in screen coords (y grows downward). Returns None if out of range.

    high=True picks the lofted arc; high=False the flat, faster trajectory.
    """
    R = abs(dx)
    if R < 1.0:
        return (0.0, -speed)                # essentially overhead → fire straight up
    h = -dy                                 # target height above launch (up positive)
    k = g * R * R / (2.0 * speed * speed)
    disc = R * R - 4.0 * k * (h + k)
    if disc < 0:
        return None                         # unreachable at this speed
    root = math.sqrt(disc)
    u = (R + root) / (2.0 * k) if high else (R - root) / (2.0 * k)
    ang = math.atan(u)
    vx = speed * math.cos(ang) * math.copysign(1.0, dx)
    vy = -speed * math.sin(ang)             # upward in screen space
    return (vx, vy)


def velocity_at_angle(dx, dy, angle, g):
    """Muzzle SPEED needed so a shell fired at a fixed elevation `angle` (radians,
    above horizontal toward the target) lands on (dx, dy) under gravity g, in
    screen coords (y down). Returns None if no positive solution.

    Solves dy = -R·tanθ + g·R² / (2·v²·cos²θ)  for v.
    """
    R = abs(dx)
    if R < 1.0:
        return None
    denom = 2.0 * math.cos(angle) ** 2 * (dy + R * math.tan(angle))
    if denom <= 0.0:
        return None
    v2 = g * R * R / denom
    if v2 <= 0.0:
        return None
    return math.sqrt(v2)
