"""Fixed symmetric skirmish geometry and immutable opening configuration."""

from __future__ import annotations

from airts.world.map import GameMap, load_map_data

DEFAULT_SETUP = (("player", 1), ("ai", 2), ("ai", 1), ("ai", 2))
SIZE = 320


def create_skirmish_map(setup: tuple[tuple[str, int], ...] = DEFAULT_SETUP) -> GameMap:
    """A/B/C/D are bottom-left/top-right/top-left/bottom-right respectively."""

    if len(setup) != 4 or any(
        role not in {"player", "ai", "closed"} or type(team) is not int or team not in range(1, 5)
        for role, team in setup
    ):
        raise ValueError("configure exactly four slots with teams 1 through 4")
    if sum(role == "player" for role, _ in setup) != 1:
        raise ValueError("choose exactly one human player")
    if len({team for role, team in setup if role != "closed"}) < 2:
        raise ValueError("at least two opposing teams are required")
    patches: list[list[object]] = [[136, 0, 48, SIZE, "water"]]
    patches.extend([[136, 36, 48, 20, "bridge"], [136, 264, 48, 20, "bridge"]])
    patches.append([136, 112, 48, 96, "bridge"])
    # Mirror complete rectangles: both axes have exactly identical travel costs.
    for x, y, width, height, terrain in (
        (50, 70, 28, 14, "forest"),
        (100, 26, 12, 18, "rock"),
        (20, 124, 16, 24, "forest"),
        (96, 120, 12, 16, "rock"),
        (0, 42, 136, 8, "road"),
        (78, 50, 8, 110, "road"),
        (86, 156, 50, 8, "road"),
    ):
        for flip_x in (False, True):
            for flip_y in (False, True):
                patches.append(
                    [
                        SIZE - x - width if flip_x else x,
                        SIZE - y - height if flip_y else y,
                        width,
                        height,
                        terrain,
                    ]
                )
    sites = [
        [SIZE - x - 2 if fx else x, SIZE - y - 2 if fy else y]
        for x, y in ((32, 32), (100, 88), (146, 126))
        for fx in (False, True)
        for fy in (False, True)
    ]
    entities: list[dict[str, object]] = []
    slots: list[dict[str, object]] = []
    prototype: tuple[tuple[str, float, float, int], ...] = (
        ("command_center", 24, 24, 5),
        ("factory", 42, 24, 4),
        ("resource_generator", 32, 32, 2),
        ("builder", 40.5, 34.5, 0),
        ("scout", 44.5, 34.5, 0),
    )
    for label, (fx, fy), (role, team) in zip(
        "ABCD", ((False, True), (True, False), (False, False), (True, True)), setup, strict=True
    ):
        if role == "closed":
            continue
        owner = "player" if role == "player" else f"ai_{label.lower()}"
        slots.append({"owner": owner, "controller": role, "team": team})
        for kind, entity_x, entity_y, footprint in prototype:
            entities.append(
                {
                    "id": f"{label.lower()}_{kind}",
                    "kind": kind,
                    "owner": owner,
                    "position": [
                        SIZE - entity_x - footprint if fx else entity_x,
                        SIZE - entity_y - footprint if fy else entity_y,
                    ],
                }
            )
    return load_map_data(
        {
            "id": "symmetric-river",
            "version": 1,
            "name": "Four Corners River",
            "width": SIZE,
            "height": SIZE,
            "terrain": {"default": "grass", "rectangles": patches},
            "entities": entities,
            "resource_sites": sites,
            "match_slots": slots,
        }
    )
