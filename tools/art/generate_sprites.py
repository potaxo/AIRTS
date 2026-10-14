"""Author the bundled north-facing pixel sprites. Run with the repository Python.

This is an offline asset tool, never imported by a game renderer. The neutral
material ramp is multiplied by the owner's presentation color at runtime.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pygame

from airts.presentation.visuals.catalog import APPEARANCES, TILE_PIXELS
from airts.world.units.catalog import EntityKind

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "src" / "airts" / "data" / "sprites"

INK = 28
TRACK = 46
EDGE = 89
STEEL = 142
ARMOR = 188
LIGHT = 222
WHITE = 255


def rect(surface: pygame.Surface, shade: int, box: tuple[int, int, int, int]) -> None:
    pygame.draw.rect(surface, (shade, shade, shade, 255), box)


def polygon(surface: pygame.Surface, shade: int, points: list[tuple[int, int]]) -> None:
    pygame.draw.polygon(surface, (shade, shade, shade, 255), points)


def plate(surface: pygame.Surface, box: tuple[int, int, int, int], shade: int = ARMOR) -> None:
    x, y, w, h = box
    rect(surface, INK, box)
    rect(surface, shade, (x + 2, y + 2, w - 4, h - 4))
    rect(surface, LIGHT, (x + 2, y + 2, w - 4, 2))
    rect(surface, EDGE, (x + 2, y + h - 4, w - 4, 2))


def tracks(
    surface: pygame.Surface, left: int, right: int, top: int, height: int, width: int
) -> None:
    for x in (left, right):
        rect(surface, INK, (x, top, width, height))
        rect(surface, TRACK, (x + 1, top + 1, width - 2, height - 2))
        for y in range(top + 3, top + height - 2, 5):
            rect(surface, EDGE, (x + 1, y, width - 2, 2))
            rect(surface, STEEL, (x + 1, y, 1, 2))


def mobile(kind: EntityKind, weapon: bool) -> pygame.Surface:
    surface = pygame.Surface((48, 48), pygame.SRCALPHA, 32)
    if weapon:
        if kind is EntityKind.SCOUT:
            plate(surface, (20, 20, 9, 10), LIGHT)
            rect(surface, INK, (23, 10, 4, 14))
            rect(surface, WHITE, (24, 11, 2, 14))
        elif kind is EntityKind.ARTILLERY:
            rect(surface, INK, (20, 0, 8, 31))
            rect(surface, LIGHT, (22, 0, 4, 29))
            rect(surface, WHITE, (22, 1, 2, 25))
            plate(surface, (17, 22, 14, 13), ARMOR)
            rect(surface, INK, (18, 1, 12, 4))
            rect(surface, STEEL, (20, 2, 8, 2))
        else:
            heavy = kind is EntityKind.HEAVY_TANK
            rect(surface, INK, (20 if heavy else 21, 1 if heavy else 4, 8 if heavy else 6, 25))
            rect(surface, LIGHT, (22, 2 if heavy else 5, 4 if heavy else 3, 23))
            rect(surface, WHITE, (22, 3 if heavy else 6, 2, 20))
            polygon(
                surface,
                INK,
                [(14, 20), (19, 15), (29, 15), (34, 20), (34, 31), (29, 35), (19, 35), (14, 31)]
                if heavy
                else [(17, 21), (21, 17), (28, 17), (32, 22), (30, 31), (19, 31)],
            )
            plate(surface, (17, 20, 15 if heavy else 13, 12), ARMOR)
        rect(surface, WHITE, (22, 23, 5, 4))
    elif kind is EntityKind.SCOUT:
        for x in (10, 32):
            for y in (13, 30):
                rect(surface, INK, (x, y, 6, 9))
                rect(surface, EDGE, (x + 1, y + 2, 3, 5))
        polygon(surface, INK, [(17, 11), (22, 6), (27, 6), (32, 11), (32, 39), (17, 39)])
        plate(surface, (17, 12, 15, 27), ARMOR)
        rect(surface, LIGHT, (21, 8, 7, 6))
        rect(surface, TRACK, (20, 15, 10, 4))
        rect(surface, WHITE, (22, 24, 5, 5))
        rect(surface, WHITE, (19, 34, 3, 2))
        rect(surface, WHITE, (27, 34, 3, 2))
    elif kind is EntityKind.BUILDER:
        tracks(surface, 8, 32, 15, 26, 8)
        plate(surface, (15, 13, 19, 28), ARMOR)
        rect(surface, TRACK, (18, 17, 12, 6))
        rect(surface, LIGHT, (19, 18, 10, 2))
        rect(surface, WHITE, (22, 24, 5, 5))
        # An asymmetric articulated arm and an open claw distinguish the worker.
        rect(surface, INK, (29, 6, 8, 23))
        rect(surface, LIGHT, (31, 7, 4, 20))
        plate(surface, (28, 22, 9, 9), STEEL)
        rect(surface, INK, (24, 3, 16, 5))
        rect(surface, LIGHT, (25, 4, 14, 2))
        rect(surface, LIGHT, (24, 0, 3, 7))
        rect(surface, LIGHT, (37, 0, 3, 7))
        for x in (18, 24, 30):
            rect(surface, TRACK, (x, 34, 2, 4))
    elif kind is EntityKind.ARTILLERY:
        tracks(surface, 8, 32, 18, 27, 8)
        plate(surface, (15, 19, 19, 25), STEEL)
        rect(surface, LIGHT, (18, 34, 13, 6))
        rect(surface, TRACK, (18, 35, 13, 2))
        rect(surface, INK, (3, 37, 11, 5))
        rect(surface, INK, (35, 37, 10, 5))
        rect(surface, EDGE, (4, 38, 9, 2))
        rect(surface, EDGE, (36, 38, 8, 2))
        rect(surface, WHITE, (22, 23, 5, 5))
    else:
        heavy = kind is EntityKind.HEAVY_TANK
        tracks(
            surface,
            3 if heavy else 8,
            35 if heavy else 33,
            8 if heavy else 11,
            36 if heavy else 30,
            10 if heavy else 7,
        )
        plate(surface, (11, 8, 26, 35) if heavy else (14, 11, 20, 29), ARMOR)
        rect(surface, LIGHT, (14 if heavy else 17, 11 if heavy else 14, 20 if heavy else 14, 5))
        rect(surface, TRACK, (15 if heavy else 18, 35 if heavy else 33, 18 if heavy else 12, 4))
        for x in (14, 30) if heavy else (16, 29):
            rect(surface, WHITE, (x, 18, 3, 2))
        if heavy:
            rect(surface, EDGE, (12, 21, 3, 11))
            rect(surface, LIGHT, (33, 21, 2, 11))
        rect(surface, WHITE, (22, 24, 5, 5))
    canvas = pygame.Surface((TILE_PIXELS, TILE_PIXELS), pygame.SRCALPHA, 32)
    canvas.blit(surface, (8, 8))
    return canvas


def building(kind: EntityKind, weapon: bool) -> pygame.Surface:
    surface = pygame.Surface((TILE_PIXELS, TILE_PIXELS), pygame.SRCALPHA, 32)
    if weapon:
        rect(surface, INK, (28, 2, 9, 33))
        rect(surface, LIGHT, (30, 3, 5, 29))
        rect(surface, WHITE, (30, 3, 2, 26))
        polygon(
            surface,
            INK,
            [(19, 25), (25, 19), (39, 19), (45, 25), (45, 40), (39, 46), (25, 46), (19, 40)],
        )
        plate(surface, (23, 25, 19, 16), ARMOR)
        rect(surface, TRACK, (26, 28, 3, 9))
        rect(surface, WHITE, (30, 30, 6, 6))
        return surface
    if kind is EntityKind.TURRET:
        polygon(
            surface,
            INK,
            [(17, 8), (47, 8), (58, 19), (58, 48), (48, 58), (16, 58), (6, 48), (6, 19)],
        )
        polygon(
            surface,
            EDGE,
            [(19, 11), (45, 11), (55, 21), (55, 46), (46, 55), (18, 55), (9, 46), (9, 21)],
        )
        plate(surface, (16, 19, 33, 31), STEEL)
        for x, y in ((11, 23), (49, 23), (11, 44), (49, 44)):
            rect(surface, WHITE, (x, y, 4, 4))
    else:
        plate(surface, (3, 7, 58, 54), EDGE)
        for x in (5, 54):
            for y in (9, 54):
                rect(surface, LIGHT, (x, y, 5, 5))
        if kind is EntityKind.FACTORY:
            polygon(
                surface,
                INK,
                [
                    (8, 25),
                    (8, 13),
                    (22, 6),
                    (22, 13),
                    (37, 6),
                    (37, 13),
                    (55, 6),
                    (55, 49),
                    (8, 49),
                ],
            )
            plate(surface, (9, 19, 46, 31), ARMOR)
            for x in (13, 27, 41):
                rect(surface, TRACK, (x, 23, 10, 7))
                rect(surface, LIGHT, (x + 1, 24, 8, 2))
            rect(surface, INK, (19, 39, 27, 18))
            for x in range(21, 46, 5):
                rect(surface, STEEL, (x, 41, 3, 14))
            rect(surface, WHITE, (28, 32, 9, 5))
            plate(surface, (48, 3, 9, 17), STEEL)
        elif kind is EntityKind.REPAIR_HUB:
            plate(surface, (10, 13, 44, 41), ARMOR)
            rect(surface, TRACK, (15, 18, 34, 30))
            rect(surface, LIGHT, (17, 20, 30, 26))
            rect(surface, INK, (27, 19, 11, 29))
            rect(surface, INK, (18, 28, 29, 11))
            rect(surface, WHITE, (29, 21, 7, 25))
            rect(surface, WHITE, (20, 30, 25, 7))
            for x in (13, 47):
                rect(surface, WHITE, (x, 10, 4, 4))
        elif kind is EntityKind.COMMAND_CENTER:
            plate(surface, (8, 17, 48, 37), ARMOR)
            plate(surface, (16, 11, 32, 37), LIGHT)
            for x in (20, 27, 34, 41):
                rect(surface, TRACK, (x, 17, 3, 8))
            polygon(surface, INK, [(32, 19), (46, 32), (32, 45), (18, 32)])
            polygon(surface, WHITE, [(32, 23), (42, 32), (32, 41), (22, 32)])
            rect(surface, INK, (7, 2, 4, 27))
            rect(surface, LIGHT, (8, 3, 2, 24))
            rect(surface, WHITE, (3, 4, 14, 3))
            plate(surface, (25, 48, 15, 11), STEEL)
        elif kind is EntityKind.RESOURCE_GENERATOR:
            plate(surface, (10, 38, 44, 17), STEEL)
            plate(surface, (17, 13, 31, 33), ARMOR)
            for y in (19, 27, 35):
                rect(surface, TRACK, (20, y, 25, 4))
                rect(surface, LIGHT, (21, y, 10, 2))
            plate(surface, (24, 7, 15, 9), LIGHT)
            rect(surface, INK, (6, 29, 8, 25))
            rect(surface, LIGHT, (8, 31, 4, 19))
            rect(surface, INK, (44, 17, 13, 6))
            rect(surface, LIGHT, (45, 18, 12, 3))
            rect(surface, WHITE, (30, 30, 6, 6))
        elif kind is EntityKind.MATTER_SYNTHESIZER:
            polygon(surface, INK, [(32, 5), (55, 31), (32, 57), (9, 31)])
            polygon(surface, ARMOR, [(32, 9), (51, 31), (32, 53), (13, 31)])
            polygon(surface, TRACK, [(32, 15), (45, 31), (32, 47), (19, 31)])
            polygon(surface, WHITE, [(32, 21), (40, 31), (32, 41), (24, 31)])
            for x in (9, 47):
                plate(surface, (x, 18, 8, 29), LIGHT)
            rect(surface, STEEL, (23, 53, 19, 4))
        else:
            raise ValueError(f"no sprite authoring definition for {kind.value}")
    return surface


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Verify packaged pixels without writing"
    )
    args = parser.parse_args()
    for kind, appearance in APPEARANCES.items():
        for weapon, path in ((False, appearance.body), (True, appearance.weapon)):
            if path is None:
                continue
            surface = mobile(kind, weapon) if kind.profile.movable else building(kind, weapon)
            destination = DESTINATION / path
            if args.check:
                actual = pygame.image.load(destination)
                if pygame.image.tobytes(actual, "RGBA") != pygame.image.tobytes(surface, "RGBA"):
                    raise ValueError(f"sprite is out of date: {destination}")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                pygame.image.save(surface, destination)
    print(f"{'Verified' if args.check else 'Wrote'} pixel sprites in {DESTINATION}")


if __name__ == "__main__":
    main()
