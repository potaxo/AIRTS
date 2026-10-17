"""Shared, read-only shell arcs and impact geometry for both rendering backends."""

from math import cos, pi, sin

from airts.contracts.spatial import Point
from airts.presentation.palette import Color
from airts.world.entities import SHELL_BLAST_TICKS, Projectile, ProjectileTrace

SHELL_CORE_RADIUS = 4
SHELL_RIM_RADIUS = 6


def shell_visual_position(projectile: Projectile, position: Point) -> Point:
    """Lift the display along a parabola; authoritative travel stays on the ground plane."""

    start = projectile.trajectory[0]
    distance = start.distance_to(projectile.destination)
    progress = min(1.0, max(0.0, start.distance_to(position) / distance)) if distance else 1.0
    height = min(3.0, distance * 0.3) * 4 * progress * (1 - progress)
    return Point(position.x, position.y - height)


def blast_circles(trace: ProjectileTrace, tick: int) -> tuple[tuple[Point, float, Color, int], ...]:
    """Fast range marker, expanding shockwave, flash and radial embers.

    Radii use map cells and outline widths use pixels. Age derives from the saved trace,
    so pauses freeze effects and load resumes them without presentation-owned timers.
    """

    age = min(1.0, max(0.0, 1 - (trace.expires_tick - tick) / SHELL_BLAST_TICKS))
    radius = trace.weapon_kind.profile.splash_radius
    center = trace.points[-1]
    fade = 1 - age
    circles: list[tuple[Point, float, Color, int]] = [
        (center, radius, (int(190 * fade), int(115 * fade), int(50 * fade)), 1),
        (center, radius * (0.15 + 0.85 * age), (int(255 * fade), int(190 * fade), 60), 2),
    ]
    if age < 0.5:
        circles.append((center, 0.7 * (1 - age * 2), (255, 220 - int(age * 240), 65), 0))
    for index in range(6):
        angle = index * pi / 3 + 0.3
        travel = radius * (0.15 + 0.65 * age)
        ember = Point(center.x + cos(angle) * travel, center.y + sin(angle) * travel)
        circles.append((ember, 0.1 * fade, (int(255 * fade), int(150 * fade), 45), 0))
    return tuple(circles)
