"""Sprite records use the renderer's existing instanced primitive stream."""

from array import array

from airts.presentation.gpu_shapes import FloatColor, append_sprite
from airts.presentation.visuals.catalog import APPEARANCES, COMPOSITE_PATHS, SPRITE_INDICES
from airts.presentation.visuals.facing import Facing
from airts.world.entities import Entity

_LAYOUTS = {
    kind: (
        art.size[0] / 2,
        art.size[1] / 2,
        7.0 if kind.profile.movable else 0.0,
        7.0 if kind.profile.movable else 0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        1.0,
    )
    for kind, art in APPEARANCES.items()
}
_INDICES = {
    kind: (
        float(SPRITE_INDICES[art.body]),
        None if art.weapon is None else float(SPRITE_INDICES[art.weapon]),
        None if art.weapon is None else float(SPRITE_INDICES[COMPOSITE_PATHS[kind]]),
    )
    for kind, art in APPEARANCES.items()
}


def append_entity_sprites(
    values: array[float],
    entity: Entity,
    color: FloatColor,
    pose: Facing,
    center: tuple[float, float],
    previous_center: tuple[float, float],
) -> None:
    body, weapon, combined = _INDICES[entity.kind]
    layout = _LAYOUTS[entity.kind]
    if (
        combined is not None
        and pose.previous_body == pose.previous_weapon
        and pose.body == pose.weapon
    ):
        append_sprite(
            values, center, previous_center, layout, color, combined, pose.previous_body, pose.body
        )
        return
    append_sprite(
        values, center, previous_center, layout, color, body, pose.previous_body, pose.body
    )
    if weapon is not None:
        append_sprite(
            values,
            center,
            previous_center,
            layout,
            color,
            weapon,
            pose.previous_weapon,
            pose.weapon,
        )
