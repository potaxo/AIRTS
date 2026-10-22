"""One entity record; the GPU expands sprites, bars, selections and minimap contacts."""

from array import array
from typing import Protocol

from airts.contracts.spatial import Point
from airts.presentation.palette import (
    ENTITY_COLORS,
    PROGRESS_COLOR,
    PROGRESS_TRACK,
    TEAM_COLORS,
    match_color,
)
from airts.presentation.progress import building_progress
from airts.presentation.visuals.catalog import APPEARANCES, COMPOSITE_PATHS, SPRITE_INDICES
from airts.presentation.visuals.facing import FacingHistory
from airts.simulation import Simulation

ENTITY_FLOATS = 32
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
_LAYOUTS = {
    kind: (
        art.size[0] / 2,
        art.size[1] / 2,
        7.0 if kind.profile.movable else 0.0,
        7.0 if kind.profile.movable else 0.0,
    )
    for kind, art in APPEARANCES.items()
}
_INDICES = {
    kind: (
        float(SPRITE_INDICES[art.body]),
        -1.0 if art.weapon is None else float(SPRITE_INDICES[art.weapon]),
        -1.0 if art.weapon is None else float(SPRITE_INDICES[COMPOSITE_PATHS[kind]]),
    )
    for kind, art in APPEARANCES.items()
}
_COLORS = {kind: (*[value / 255 for value in color], 1.0) for kind, color in ENTITY_COLORS.items()}


class EntityView(Protocol):
    facing: FacingHistory

    @property
    def simulation(self) -> Simulation: ...

    @property
    def selected_entities(self) -> set[str]: ...

    @property
    def inspected_entity_id(self) -> str | None: ...

    def previous_entity_position(self, entity_id: str) -> Point: ...


def pack_entities(view: EntityView) -> tuple[bytes, int, int, int]:
    """Upload state once, without CPU expansion into per-sprite/per-bar primitives."""
    values = array("f")
    simulation = view.simulation
    selected = view.selected_entities
    large = len(selected) > 128
    colors = {
        slot.owner_id: (
            *[value / 255 for value in match_color(simulation.game_map, slot.owner_id)],
            1.0,
        )
        for slot in simulation.game_map.match_slots
    }
    minimap_colors = {
        owner: colors[owner]
        if colors
        else (*[v / 255 for v in TEAM_COLORS[1 if owner == "player" else 2]], 1.0)
        for owner in {entity.owner_id for entity in simulation.entities.values()}
    }
    view.facing.sync(simulation)
    units = buildings = selected_units = 0
    for key, entity in simulation.entities.items():
        kind = entity.kind
        movable = entity.is_movable
        building = not movable
        center = entity.selection_position
        previous = view.previous_entity_position(key) if movable else center
        is_selected = key in selected
        outline = (
            -1.0
            if is_selected and large and movable
            else 2.0
            if is_selected and building
            else 1.0
            if is_selected or key == view.inspected_entity_id and movable
            else 0.0
        )
        pose = view.facing.pose(key)
        body, weapon, combined = _INDICES[kind]
        color = (
            colors.get(entity.owner_id)
            if colors
            else (
                _COLORS[kind]
                if entity.owner_id == "player"
                else (218 / 255, 78 / 255, 78 / 255, 1.0)
            )
        )
        assert color is not None
        progress = building_progress(simulation, entity) if building else ()
        width, height = kind.profile.footprint
        values.extend(
            (
                center.x,
                center.y,
                previous.x,
                previous.y,
                *_LAYOUTS[kind],
                *color,
                body,
                weapon,
                pose.previous_body,
                pose.body,
                pose.previous_weapon,
                pose.weapon,
                entity.health / entity.profile.max_health,
                float(not large or key == view.inspected_entity_id),
                width / 2,
                height / 2,
                outline,
                float(is_selected),
                progress[0] if progress else -1.0,
                progress[1] if len(progress) > 1 else -1.0,
                float(building),
                combined,
                *minimap_colors[entity.owner_id],
            )
        )
        units += movable
        buildings += building
        selected_units += movable and is_selected
    return values.tobytes(), units, buildings, selected_units


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
    if (selected.z < -0.5) tint.rgb=min(vec3(1.0),tint.rgb+vec3(45.0/255.0));
    bool combined=progress.w>=0.0 && sprites.z==weapon.x && sprites.w==weapon.y;
    quad(center,size,tint,3.0,combined ? progress.w:sprites.x,vec4(0.0),mix(sprites.z,sprites.w,interpolation_alpha));
    if (!combined && sprites.y>=0.0)
        quad(center,size,tint,3.0,sprites.y,vec4(0.0),mix(weapon.x,weapon.y,interpolation_alpha));
    if (selected.z>0.5) {
        vec2 outline=selected.z>1.5 ? (selected.xy+vec2(3.0))*terrain_tile_size
                     : max(vec2(0.42*terrain_tile_size),vec2(5.0))+vec2(3.0);
        quad(center,outline,vec4(0.0),building ? 0.0:1.0,2.0,
             selected.w>0.5 ? vec4(1.0):vec4(1.0,210.0/255.0,90.0/255.0,1.0),0.0);
    }
    float anchor=max(art.y*terrain_tile_size,7.0);
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
