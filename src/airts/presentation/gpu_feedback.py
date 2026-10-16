"""World feedback batches; camera transforms and rasterization stay on the GPU."""

from __future__ import annotations

from array import array
from typing import Any, Protocol

import pygame

from airts.contracts.automations import AutomationKind, ConstructionParameters
from airts.contracts.spatial import Point, PointTarget, PolygonRegion, SpatialTarget
from airts.presentation.build_placement import BuildingLineDraft
from airts.presentation.palette import RECLAIM_COLOR, REPAIR_COLOR, WORK_BEAM_WIDTH
from airts.presentation.rendering import reclaim_links, work_links
from airts.presentation.ui.drawing import UiPainter
from airts.simulation import Simulation
from airts.world.units.catalog import EntityKind


class FeedbackView(Protocol):
    @property
    def simulation(self) -> Simulation: ...

    @property
    def canvas_rect(self) -> pygame.Rect: ...

    @property
    def map_origin(self) -> tuple[int, int]: ...

    @property
    def tile_size(self) -> float: ...

    @property
    def mode(self) -> str: ...

    @property
    def selected_points(self) -> set[str]: ...

    @property
    def selected_routes(self) -> set[str]: ...

    @property
    def selected_regions(self) -> set[str]: ...

    @property
    def active_target(self) -> SpatialTarget | None: ...

    @property
    def line_points(self) -> list[Point]: ...

    @property
    def freehand_points(self) -> list[Point]: ...

    @property
    def drag_start(self) -> Point | None: ...

    @property
    def placement_kind(self) -> EntityKind | None: ...

    @property
    def building_line_draft(self) -> BuildingLineDraft | None: ...

    @property
    def ui(self) -> UiPainter: ...

    def _map_point(self, position: tuple[int, int]) -> Point: ...
    def _construction_previews(self, pointer: Point | None) -> tuple[tuple[Point, bool], ...]: ...


def _triangles(region: PolygonRegion) -> list[Point]:
    """Ear clipping of validated simple polygons, including concave regions."""
    points = list(region.points if region.signed_area > 0 else region.points[::-1])
    result: list[Point] = []

    def cross(a: Point, b: Point, c: Point) -> float:
        return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)

    while len(points) > 3:
        for i, b in enumerate(points):
            a, c = points[i - 1], points[(i + 1) % len(points)]
            turn = cross(a, b, c)
            if abs(turn) < 1e-12:
                points.pop(i)
                break
            if turn < 0:
                continue
            if any(
                cross(a, b, p) >= -1e-12 and cross(b, c, p) >= -1e-12 and cross(c, a, p) >= -1e-12
                for j, p in enumerate(points)
                if j not in {i, (i - 1) % len(points), (i + 1) % len(points)}
            ):
                continue
            result.extend((a, b, c))
            points.pop(i)
            break
        else:
            raise ValueError("validated region could not be triangulated")
    result.extend(points)
    return result


class GPUFeedback:
    """Persistent world buffers and a small text atlas, independent of viewport area."""

    def __init__(self, module: Any, context: Any) -> None:
        self.module, self.context = module, context
        self.resources: list[Any] = []
        self.key: tuple[object, ...] | None = None
        self.text_key: tuple[object, ...] | None = None
        self.texture: Any = None
        self.counts = (0, 0, 0, 0)
        try:
            self.shape_program = self._own(
                context.program(vertex_shader=_SHAPE_VERTEX, fragment_shader=_SHAPE_FRAGMENT)
            )
            self.line_program = self._own(
                context.program(vertex_shader=_LINE_VERTEX, fragment_shader=_LINE_FRAGMENT)
            )
            self.segment_program = self._own(
                context.program(vertex_shader=_SEGMENT_VERTEX, fragment_shader=_LINE_FRAGMENT)
            )
            self.text_program = self._own(
                context.program(vertex_shader=_TEXT_VERTEX, fragment_shader=_TEXT_FRAGMENT)
            )
            self.quad = self._own(
                context.buffer(array("f", (-1, -1, 1, -1, -1, 1, 1, 1)).tobytes())
            )
            self.shapes = self._own(context.buffer(reserve=4096, dynamic=True))
            self.lines = self._own(context.buffer(reserve=4096, dynamic=True))
            self.triangles = self._own(context.buffer(reserve=4096, dynamic=True))
            self.text = self._own(context.buffer(reserve=4096, dynamic=True))
            self.shape_vao = self._own(
                context.vertex_array(
                    self.shape_program,
                    [
                        (self.quad, "2f", "corner"),
                        (
                            self.shapes,
                            "2f 2f 2f 2f 4f 2f 4f /i",
                            "center",
                            "half_world",
                            "offset",
                            "half_pixel",
                            "color",
                            "style",
                            "outline",
                        ),
                    ],
                )
            )
            self.line_vao = self._own(
                context.vertex_array(
                    self.segment_program,
                    [
                        (self.quad, "2f", "corner"),
                        (self.lines, "2f 2f 4f 1f /i", "start", "end", "color", "width"),
                    ],
                )
            )
            self.triangle_vao = self._own(
                context.vertex_array(
                    self.line_program, [(self.triangles, "2f 4f", "position", "color")]
                )
            )
            self.text_vao = self._own(
                context.vertex_array(
                    self.text_program,
                    [
                        (self.quad, "2f", "corner"),
                        (self.text, "2f 2f 2f 4f /i", "center", "offset", "size", "uv_rect"),
                    ],
                )
            )
        except Exception:
            self.release()
            raise

    def _own(self, resource: Any) -> Any:
        self.resources.append(resource)
        return resource

    def release(self) -> None:
        if self.texture is not None:
            self.texture.release()
            self.texture = None
        for resource in reversed(self.resources):
            resource.release()
        self.resources.clear()

    @staticmethod
    def _write(buffer: Any, values: array[float]) -> None:
        data = values.tobytes()
        if not data:
            return
        if len(data) > buffer.size:
            buffer.orphan(max(len(data), buffer.size * 2))
        if data:
            buffer.write(data)

    def draw(self, app: FeedbackView, size: tuple[int, int]) -> None:
        mouse = pygame.mouse.get_pos() if pygame.display.get_init() else (-1, -1)
        pointer = (
            app._map_point(mouse)
            if (
                app.canvas_rect.collidepoint(mouse)
                or (app.mode == "select" and app.drag_start is not None)
            )
            and (app.placement_kind is not None or app.drag_start is not None or app.mode == "line")
            else None
        )
        key = (
            id(app.simulation),
            app.simulation.tick,
            app.simulation.command_count,
            frozenset(app.selected_points),
            frozenset(app.selected_routes),
            frozenset(app.selected_regions),
            app.active_target,
            tuple(app.line_points),
            tuple(app.freehand_points),
            app.drag_start,
            app.mode,
            app.placement_kind,
            None
            if app.building_line_draft is None
            else (
                app.building_line_draft.kind,
                app.building_line_draft.start,
                app.building_line_draft.end,
            ),
            pointer,
            id(app.ui.small_font),
        )
        if key != self.key:
            self._build(app, pointer)
            self.key = key
        for program in (
            self.shape_program,
            self.line_program,
            self.segment_program,
            self.text_program,
        ):
            program["viewport"].value = size
            program["origin"].value = app.map_origin
            program["tile"].value = app.tile_size
        rect = app.canvas_rect
        self.context.scissor = (rect.x, size[1] - rect.bottom, rect.width, rect.height)
        try:
            shapes, lines, triangles, text = self.counts
            if triangles:
                self.triangle_vao.render(mode=self.module.TRIANGLES, vertices=triangles)
            if shapes:
                self.shape_vao.render(mode=self.module.TRIANGLE_STRIP, vertices=4, instances=shapes)
            if lines:
                self.line_vao.render(mode=self.module.TRIANGLE_STRIP, vertices=4, instances=lines)
            if text:
                self.texture.use(location=0)
                self.text_vao.render(mode=self.module.TRIANGLE_STRIP, vertices=4, instances=text)
        finally:
            self.context.scissor = None

    def _build(self, app: FeedbackView, pointer: Point | None) -> None:
        shapes, lines, triangles = array("f"), array("f"), array("f")
        labels: list[tuple[str, Point, bool]] = []

        def rgba(color: tuple[int, ...]) -> tuple[float, ...]:
            return tuple(c / 255 for c in (*color, 255)[:4])

        def shape(
            center: Point,
            half: tuple[float, float],
            fill: tuple[int, ...],
            border: tuple[int, ...] = (0, 0, 0, 0),
            width: float = 0,
            circle: bool = False,
            offset: tuple[float, float] = (0, 0),
            pixels: tuple[float, float] = (0, 0),
        ) -> None:
            shapes.extend(
                (
                    center.x,
                    center.y,
                    *half,
                    *offset,
                    *pixels,
                    *rgba(fill),
                    float(circle),
                    width,
                    *rgba(border),
                )
            )

        def line(
            points: tuple[Point, ...],
            color: tuple[int, ...],
            closed: bool = False,
            width: float = 2.0,
        ) -> None:
            vertices = points + points[:1] if closed else points
            for a, b in zip(vertices, vertices[1:], strict=False):
                lines.extend((a.x, a.y, b.x, b.y, *rgba(color), width))

        def target(geometry: SpatialTarget, color: tuple[int, ...], selected: bool) -> None:
            width = 4 if selected else 2
            if isinstance(geometry, PointTarget):
                shape(
                    geometry.point,
                    (geometry.radius, geometry.radius),
                    (0, 0, 0, 0),
                    color,
                    width,
                    True,
                )
                shape(geometry.point, (0, 0), color, circle=True, pixels=(4, 4))
            else:
                line(geometry.points, color, isinstance(geometry, PolygonRegion), width)
                if isinstance(geometry, PolygonRegion):
                    for p in _triangles(geometry):
                        triangles.extend((p.x, p.y, *rgba((*color[:3], 55))))

        for site in app.simulation.game_map.resource_sites or ():
            shape(
                Point(site.x + 1, site.y + 1),
                (1, 1),
                (0, 0, 0, 0),
                (244, 63, 66),
                2,
                True,
                pixels=(3, 3),
            )
        for automation in app.simulation.live_automations:
            if automation.kind is AutomationKind.CONSTRUCTION and automation.owner_id == "player":
                params = automation.parameters
                assert isinstance(params, ConstructionParameters)
                if params.site_entity_id is None:
                    w, h = params.building_kind.profile.footprint
                    shape(
                        Point(params.position.x + w / 2, params.position.y + h / 2),
                        (w / 2, h / 2),
                        (94, 176, 220, 80),
                        (164, 222, 250, 230),
                        2,
                    )
        for entity in app.simulation.entities.values():
            progress = entity.construction_progress
            if progress is None:
                continue
            w, h = entity.kind.profile.footprint
            x, y = entity.position.x, entity.position.y
            shape(Point(x + w / 2, y + h / 2), (w / 2, h / 2), (0, 0, 0, 0), (240, 186, 92, 245), 2)
            line((Point(x, y), Point(x + w, y + h)), (240, 186, 92, 180))
            line((Point(x + w, y), Point(x, y + h)), (240, 186, 92, 180))
            shape(
                Point(x + w / 2, y + h),
                (w / 2, 0),
                (30, 38, 46, 230),
                offset=(0, 5.5),
                pixels=(0, 2.5),
            )
            shape(
                Point(x + w * progress / 2, y + h),
                (w * progress / 2, 0),
                (102, 222, 156, 245),
                offset=(0, 5.5),
                pixels=(0, 2.5),
            )
            labels.append((f"{round(progress * 100)}%", entity.position, False))
        kind = (
            app.building_line_draft.kind
            if app.building_line_draft is not None
            else app.placement_kind
        )
        if kind is not None:
            for position, valid in app._construction_previews(pointer):
                w, h = kind.profile.footprint
                color = (95, 224, 145) if valid else (235, 88, 88)
                shape(
                    Point(position.x + w / 2, position.y + h / 2),
                    (w / 2, h / 2),
                    (*color, 72),
                    (*color, 245),
                    3,
                )
        selected = app.selected_points | app.selected_routes | app.selected_regions
        for reference in app.simulation.spatial.references.values():
            target(reference.geometry, (92, 184, 222), reference.reference_id in selected)
            if isinstance(reference.geometry, PolygonRegion) and reference.name:
                points = reference.geometry.points
                labels.append(
                    (
                        reference.name,
                        Point(
                            sum(p.x for p in points) / len(points),
                            sum(p.y for p in points) / len(points),
                        ),
                        True,
                    )
                )
        if app.active_target is not None:
            target(app.active_target, (229, 96, 155), True)
        drafts = app.line_points or app.freehand_points
        if drafts:
            points = tuple(drafts) + (
                (pointer,) if app.mode == "line" and pointer is not None else ()
            )
            line(points, (255, 170, 210))
            for p in drafts:
                shape(p, (0, 0), (255, 170, 210), circle=True, pixels=(3, 3))
        if (
            app.mode in {"select", "rectangle"}
            and app.drag_start is not None
            and pointer is not None
        ):
            a, b = app.drag_start, pointer
            shape(
                Point((a.x + b.x) / 2, (a.y + b.y) / 2),
                (abs(a.x - b.x) / 2, abs(a.y - b.y) / 2),
                (0, 0, 0, 0),
                (245, 245, 245),
                1,
            )
        for source, destination in work_links(app.simulation):
            line((source, destination), REPAIR_COLOR, width=WORK_BEAM_WIDTH)
        for source, destination in reclaim_links(app.simulation):
            line((source, destination), RECLAIM_COLOR, width=WORK_BEAM_WIDTH)
        self._write(self.shapes, shapes)
        self._write(self.lines, lines)
        self._write(self.triangles, triangles)
        count = self._build_labels(app, labels)
        self.counts = (len(shapes) // 18, len(lines) // 9, len(triangles) // 6, count)

    def _build_labels(self, app: FeedbackView, labels: list[tuple[str, Point, bool]]) -> int:
        if app.ui.small_font is None or not labels:
            return 0
        texts = tuple(dict.fromkeys(text for text, _, _ in labels))
        key = (id(app.ui.small_font), texts)
        if key != self.text_key:
            images = [app.ui.small_font.render(text, True, (245, 250, 252)) for text in texts]
            width, height = (
                max(image.get_width() for image in images),
                sum(image.get_height() for image in images),
            )
            atlas = pygame.Surface((max(1, width), max(1, height)), pygame.SRCALPHA)
            self.label_rects: dict[str, tuple[int, int, int, int]] = {}
            y = 0
            for text, image in zip(texts, images, strict=True):
                atlas.blit(image, (0, y))
                self.label_rects[text] = (0, y, image.get_width(), image.get_height())
                y += image.get_height()
            if self.texture is not None:
                self.texture.release()
            self.texture = self.context.texture(
                atlas.get_size(), 4, pygame.image.tobytes(atlas, "RGBA", False)
            )
            self.texture.filter = (self.module.LINEAR, self.module.LINEAR)
            self.texture.repeat_x = self.texture.repeat_y = False
            self.atlas_size = atlas.get_size()
            self.text_key = key
        values = array("f")
        width, height = self.atlas_size
        for text, p, centered in labels:
            x, y, w, h = self.label_rects[text]
            values.extend(
                (
                    p.x,
                    p.y,
                    -w / 2 if centered else 0,
                    -h / 2 if centered else -h,
                    w,
                    h,
                    x / width,
                    y / height,
                    w / width,
                    h / height,
                )
            )
        self._write(self.text, values)
        return len(labels)


_SHAPE_VERTEX = """
#version 330
uniform vec2 viewport; uniform vec2 origin; uniform float tile;
in vec2 corner; in vec2 center; in vec2 half_world; in vec2 offset; in vec2 half_pixel;
in vec4 color; in vec2 style; in vec4 outline;
out vec2 local; flat out vec2 half_size; flat out vec4 fill; flat out vec2 flags; flat out vec4 border;
void main() {
    half_size = max(half_world*tile,half_pixel);
    vec2 p = origin+center*tile+offset+corner*half_size;
    gl_Position = vec4(p.x*2.0/viewport.x-1.0,1.0-p.y*2.0/viewport.y,0,1);
    local=corner; fill=color; flags=style; border=outline;
}
"""
_SHAPE_FRAGMENT = """
#version 330
in vec2 local; flat in vec2 half_size; flat in vec4 fill; flat in vec2 flags; flat in vec4 border;
out vec4 fragment;
void main() {
    float d=flags.x>0.5 ? (1.0-length(local))*min(half_size.x,half_size.y)
        : min((1.0-abs(local.x))*half_size.x,(1.0-abs(local.y))*half_size.y);
    float aa=max(fwidth(d),0.75);
    float outer=smoothstep(-aa,aa,d);
    float inner=flags.y>0.0 ? smoothstep(-aa,aa,d-flags.y) : outer;
    float a=fill.a*inner; float b=border.a*(outer-inner);
    if(a+b<=0.001) discard;
    fragment=vec4((fill.rgb*a+border.rgb*b)/max(a+b,0.001),a+b);
}
"""
_LINE_VERTEX = """
#version 330
uniform vec2 viewport; uniform vec2 origin; uniform float tile;
in vec2 position; in vec4 color; out vec4 line_color;
void main() {
    vec2 p=origin+position*tile;
    gl_Position=vec4(p.x*2.0/viewport.x-1.0,1.0-p.y*2.0/viewport.y,0,1);
    line_color=color;
}
"""
_LINE_FRAGMENT = """
#version 330
in vec4 line_color; out vec4 fragment;
void main() { fragment=line_color; }
"""
_TEXT_VERTEX = """
#version 330
uniform vec2 viewport; uniform vec2 origin; uniform float tile;
in vec2 corner; in vec2 center; in vec2 offset; in vec2 size; in vec4 uv_rect;
out vec2 uv;
void main() {
    vec2 t=(corner+1.0)*0.5;
    vec2 p=origin+center*tile+offset+t*size;
    gl_Position=vec4(p.x*2.0/viewport.x-1.0,1.0-p.y*2.0/viewport.y,0,1);
    uv=uv_rect.xy+t*uv_rect.zw;
}
"""
_TEXT_FRAGMENT = """
#version 330
uniform sampler2D atlas; in vec2 uv; out vec4 fragment;
void main() { fragment=texture(atlas,uv); }
"""

_SEGMENT_VERTEX = """
#version 330
uniform vec2 viewport; uniform vec2 origin; uniform float tile;
in vec2 corner; in vec2 start; in vec2 end; in vec4 color; in float width;
out vec4 line_color;
void main() {
    vec2 a=origin+start*tile; vec2 b=origin+end*tile;
    vec2 direction=b-a;
    float distance=length(direction);
    vec2 normal=distance>0.0001 ? vec2(-direction.y,direction.x)/distance : vec2(0,1);
    vec2 p=mix(a,b,(corner.x+1.0)*0.5)+normal*corner.y*width*0.5;
    gl_Position=vec4(p.x*2.0/viewport.x-1.0,1.0-p.y*2.0/viewport.y,0,1);
    line_color=color;
}
"""
