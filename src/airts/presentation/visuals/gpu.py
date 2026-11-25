"""One entity record; the GPU expands sprites, bars, selections and minimap contacts."""

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import product
from struct import Struct
from typing import Protocol

from airts.contracts.spatial import Point
from airts.presentation.palette import (
    ENTITY_COLORS,
    PROGRESS_COLOR,
    PROGRESS_TRACK,
    TEAM_COLORS,
)
from airts.presentation.progress import building_progress
from airts.presentation.visuals.catalog import APPEARANCES, COMPOSITE_PATHS, SPRITE_INDICES
from airts.presentation.visuals.facing import FacingHistory
from airts.simulation import Simulation
from airts.world.units.catalog import EntityKind

ENTITY_FLOATS = 32
# Position, headings and health vary per entity. Selection and layout come
# from a fixed set of GPU-ready records per unit kind and owner color.
_ENTITY_RECORD = Struct("=4f40s5f52s")
_STYLE_PREFIX = Struct("=10f")
_STYLE_TAIL = Struct("=13f")
_PROGRESS = Struct("=2f")
ENTITY_ATTRIBUTES = (
    "in_centers",
    "in_layout",
    "in_tint",
    "in_sprites",
    "in_weapon_health",
    "in_selection",
    "in_progress",
    "in_minimap_tint",
)
_COLORS = {kind: (*[value / 255 for value in color], 1.0) for kind, color in ENTITY_COLORS.items()}
_TEAM_TINTS = {team: (*[v / 255 for v in color], 1.0) for team, color in TEAM_COLORS.items()}


@dataclass(frozen=True, slots=True)
class _Style:
    prefix: bytes
    tails: tuple[bytes, ...]
    movable: bool
    base_health: int


def _entity_styles(
    tint: tuple[float, ...] | None, contact: tuple[float, ...]
) -> dict[EntityKind, _Style]:
    styles = {}
    for kind, art in APPEARANCES.items():
        minimum = 7.0 if kind.profile.movable else 0.0
        weapon = SPRITE_INDICES[art.weapon] if art.weapon is not None else -1
        combined = SPRITE_INDICES[COMPOSITE_PATHS[kind]] if art.weapon is not None else -1
        movable = kind.profile.movable
        width, height = kind.profile.footprint
        tails = []
        for large, selected, inspected in product((False, True), repeat=3):
            if selected:
                outline = (-1.0 if large else 1.0) if movable else 2.0
            else:
                outline = float(inspected and movable)
            tails.append(
                _STYLE_TAIL.pack(
                    not large or inspected,
                    width / 2,
                    height / 2,
                    outline,
                    selected,
                    -1.0,
                    -1.0,
                    not movable,
                    combined,
                    *contact,
                )
            )
        styles[kind] = _Style(
            _STYLE_PREFIX.pack(
                art.size[0] / 2,
                art.size[1] / 2,
                minimum,
                minimum,
                *(tint or _COLORS[kind]),
                SPRITE_INDICES[art.body],
                weapon,
            ),
            tuple(tails),
            movable,
            kind.profile.max_health,
        )
    return styles


_MATCH_STYLES = {team: _entity_styles(tint, tint) for team, tint in _TEAM_TINTS.items()}
_PLAYER_STYLES = _entity_styles(None, _TEAM_TINTS[1])
_ENEMY_STYLES = _entity_styles((218 / 255, 78 / 255, 78 / 255, 1.0), _TEAM_TINTS[2])


class EntityView(Protocol):
    facing: FacingHistory

    @property
    def simulation(self) -> Simulation: ...

    @property
    def selected_entities(self) -> set[str]: ...

    @property
    def inspected_entity_id(self) -> str | None: ...

    @property
    def previous_entity_positions(self) -> Mapping[str, Point]: ...


def pack_entities(
    view: EntityView, *, entity_ids: tuple[str, ...] | None = None
) -> tuple[bytes, int, int, int, bool]:
    """Upload state once, without CPU expansion into per-sprite/per-bar primitives."""
    values = bytearray()
    pack = _ENTITY_RECORD.pack
    simulation = view.simulation
    selected = view.selected_entities
    selection_offset = 4 if len(selected) > 128 else 0
    inspected = view.inspected_entity_id
    previous_position = view.previous_entity_positions.get
    styles = {slot.owner_id: _MATCH_STYLES[slot.team] for slot in simulation.game_map.match_slots}
    items = (
        simulation.entities.items()
        if entity_ids is None
        else ((key, simulation.entities[key]) for key in entity_ids)
    )
    if entity_ids is None:
        view.facing.sync(simulation)
    else:
        view.facing.seed_births(simulation, entity_ids)
    units = buildings = selected_units = 0
    interpolates = False
    for key, entity in items:
        kind = entity.kind
        style = (
            styles[entity.owner_id]
            if styles
            else (_PLAYER_STYLES if entity.owner_id == "player" else _ENEMY_STYLES)
        )[kind]
        movable = style.movable
        center = entity.position if movable else entity.selection_position
        previous = previous_position(key, center) if movable else center
        is_selected = key in selected
        tail = style.tails[selection_offset + 2 * is_selected + (key == inspected)]
        if not movable and (progress := building_progress(simulation, entity)):
            # Replace the two progress floats following the five selection floats.
            tail = (
                tail[:20]
                + _PROGRESS.pack(progress[0], progress[1] if len(progress) > 1 else -1.0)
                + tail[28:]
            )
        pose = view.facing.pose(key)
        interpolates = (
            interpolates
            or (center is not previous and center != previous)
            or pose.previous_body != pose.body
            or pose.previous_weapon != pose.weapon
        )
        max_health = style.base_health if entity.upgrade.tier == 1 else entity.profile.max_health
        values.extend(
            pack(
                center.x,
                center.y,
                previous.x,
                previous.y,
                style.prefix,
                pose.previous_body,
                pose.body,
                pose.previous_weapon,
                pose.weapon,
                entity.health / max_health,
                tail,
            )
        )
        units += movable
        buildings += not movable
        selected_units += movable and is_selected
    return bytes(values), units, buildings, selected_units, interpolates


ENTITY_VERTEX_SHADER = """
#version 330
in vec4 in_centers;
in vec4 in_layout;
in vec4 in_tint;
in vec4 in_sprites;
in vec4 in_weapon_health;
in vec4 in_selection;
in vec4 in_progress;
in vec4 in_minimap_tint;
out Entity {
    vec4 centers; vec4 art; vec4 tint; vec4 sprites;
    vec4 weapon_health; vec4 selection; vec4 progress; vec4 minimap_tint;
} entity;
void main() {
    entity.centers=in_centers; entity.art=in_layout; entity.tint=in_tint;
    entity.sprites=in_sprites; entity.weapon_health=in_weapon_health;
    entity.selection=in_selection; entity.progress=in_progress;
    entity.minimap_tint=in_minimap_tint;
    gl_Position=vec4(0.0,0.0,0.0,1.0);
}
"""

ENTITY_GEOMETRY_SHADER = """
#version 330
layout(points) in;
layout(triangle_strip,max_vertices=36) out;
in Entity {
    vec4 centers; vec4 art; vec4 tint; vec4 sprites;
    vec4 weapon_health; vec4 selection; vec4 progress; vec4 minimap_tint;
} entity[];
uniform vec2 viewport_size;
uniform float interpolation_alpha;
uniform vec2 terrain_origin;
uniform float terrain_tile_size;
uniform int minimap_mode;
uniform vec4 minimap_rect;
uniform vec4 minimap_viewport;
uniform vec2 world_size;
out vec2 local_position;
out vec2 world_position;
flat out vec2 half_size;
flat out vec4 fill_color;
flat out float circle_shape;
flat out float outline_width;
flat out vec4 outline_color;

void quad(vec2 center,vec2 size,vec4 tint,float shape,float width,vec4 edge,float angle) {
    float c=cos(angle),s=sin(angle);
    for (int i=0;i<4;++i) {
        vec2 corner=vec2(i%2==0 ? -1.0:1.0,i<2 ? -1.0:1.0);
        vec2 offset=corner*size;
        vec2 pixel=center+vec2(c*offset.x-s*offset.y,s*offset.x+c*offset.y);
        gl_Position=vec4(pixel.x*2.0/viewport_size.x-1.0,1.0-pixel.y*2.0/viewport_size.y,0.0,1.0);
        local_position=corner; world_position=(pixel-terrain_origin)/terrain_tile_size;
        half_size=size; fill_color=tint; circle_shape=shape;
        outline_width=width; outline_color=edge;
        EmitVertex();
    }
    EndPrimitive();
}
void bar(vec2 center,float anchor,float offset,float fraction,vec4 color) {
    float width=max(0.7*terrain_tile_size,6.0);
    center+=vec2(width*(fraction-1.0),anchor+offset);
    quad(center,vec2(width*fraction,1.5),color,0.0,0.0,vec4(0.0),0.0);
}
void main() {
    vec4 centers=entity[0].centers,art=entity[0].art;
    vec4 sprites=entity[0].sprites,weapon=entity[0].weapon_health;
    vec4 selected=entity[0].selection,progress=entity[0].progress;
    vec4 tint=entity[0].tint;
    bool building=progress.z>0.5;
    if (minimap_mode!=0) {
        if (minimap_mode==3) {
            quad(minimap_viewport.xy+minimap_viewport.zw/2.0,minimap_viewport.zw/2.0,
                 vec4(0.0),5.0,1.0,vec4(232.0/255.0,234.0/255.0,216.0/255.0,1.0),0.0);
            return;
        }
        if ((minimap_mode==1)!=building) return;
        vec2 point=minimap_rect.xy+clamp(floor(centers.xy/world_size*minimap_rect.zw),
                   vec2(0.0),minimap_rect.zw-vec2(1.0))+vec2(0.5);
        float side=building ? 5.0:3.0;
        if (selected.w>0.5) quad(point,vec2((side+2.0)/2.0),vec4(232.0/255.0,234.0/255.0,216.0/255.0,1.0),5.0,0.0,vec4(0.0),0.0);
        quad(point,vec2(side/2.0),entity[0].minimap_tint,5.0,0.0,vec4(0.0),0.0);
        return;
    }
    vec2 center=terrain_origin+mix(centers.zw,centers.xy,interpolation_alpha)*terrain_tile_size;
    vec2 size=max(art.xy*terrain_tile_size,art.zw);
    float anchor=max(art.y*terrain_tile_size,7.0);
    vec2 outline=selected.z>1.5 ? (selected.xy+vec2(3.0))*terrain_tile_size
                 : max(vec2(0.42*terrain_tile_size),vec2(5.0))+vec2(3.0);
    // Bound the rotated sprite, health/progress bars, and selection together.
    // Keep even a partial intersection, including sprites behind translucent UI.
    float bar_width=max(0.7*terrain_tile_size,6.0);
    float fraction=max(abs(weapon.z),max(abs(progress.x),abs(progress.y)));
    vec2 extent=max(vec2(size.x+size.y),
                    vec2(bar_width*max(1.0,2.0*fraction+1.0),anchor+13.0));
    if (selected.z>0.5) extent=max(extent,outline);
    extent+=vec2(1.0);
    if (any(lessThan(center+extent,vec2(0.0))) ||
        any(greaterThan(center-extent,viewport_size))) return;
    if (selected.z < -0.5) tint.rgb=min(vec3(1.0),tint.rgb+vec3(45.0/255.0));
    bool combined=progress.w>=0.0 && sprites.z==weapon.x && sprites.w==weapon.y;
    quad(center,size,tint,3.0,combined ? progress.w:sprites.x,vec4(0.0),mix(sprites.z,sprites.w,interpolation_alpha));
    if (!combined && sprites.y>=0.0)
        quad(center,size,tint,3.0,sprites.y,vec4(0.0),mix(weapon.x,weapon.y,interpolation_alpha));
    if (selected.z>0.5) {
        quad(center,outline,vec4(0.0),building ? 0.0:1.0,2.0,
             selected.w>0.5 ? vec4(1.0):vec4(1.0,210.0/255.0,90.0/255.0,1.0),0.0);
    }
    if (weapon.w>0.5) {
        bar(center,-anchor,-4.5,1.0,vec4(70.0/255.0,35.0/255.0,35.0/255.0,1.0));
        bar(center,-anchor,-4.5,weapon.z,vec4(74.0/255.0,218.0/255.0,111.0/255.0,1.0));
    }
    for (int i=0;i<2;++i) {
        float fraction=i==0 ? progress.x:progress.y;
        if (fraction>=0.0) {
            bar(center,anchor,5.5+float(i)*6.0,1.0,PROGRESS_TRACK);
            bar(center,anchor,5.5+float(i)*6.0,fraction,PROGRESS_COLOR);
        }
    }
}
""".replace(
    "PROGRESS_TRACK", "vec4(" + ",".join(str(v / 255) for v in PROGRESS_TRACK) + ",1.0)"
).replace("PROGRESS_COLOR", "vec4(" + ",".join(str(v / 255) for v in PROGRESS_COLOR) + ",1.0)")
