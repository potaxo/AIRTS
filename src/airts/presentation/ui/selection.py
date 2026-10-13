"""Selection interface reads a scoped view and emits clickable actions."""

from __future__ import annotations

from typing import Protocol

import pygame

from airts.contracts.automations import (
    ProductionParameters,
)
from airts.presentation.ui.drawing import UiPainter
from airts.simulation import Simulation
from airts.world.entities import Entity
from airts.world.units.catalog import BUILDABLE_KINDS, EntityKind
from airts.world.units.schema import EntityCategory


class SelectionView(Protocol):
    _command_buttons: list[tuple[pygame.Rect, str]]
    _type_buttons: list[tuple[pygame.Rect, EntityKind]]
    inspected_entity_id: str | None
    inspected_kind: EntityKind | None
    production_detail_kind: EntityKind | None
    right_panel_rect: pygame.Rect
    simulation: Simulation
    ui: UiPainter
    ui_scale: float

    def _selected_entities_for_draw(self) -> tuple[Entity, ...]: ...
    def _selected_producer_ids(self) -> tuple[str, ...]: ...


def draw_selection(view: SelectionView, screen: pygame.Surface) -> None:
    panel = view.right_panel_rect
    x = panel.x
    pygame.draw.rect(screen, (22, 27, 34), panel)
    x += 16
    y = 16
    view.ui.text(screen, "Selection", (x, y), (245, 245, 245))
    y += 28
    selected = view._selected_entities_for_draw()
    read_only = any(entity.owner_id != "player" for entity in selected)
    inspected = view.simulation.entities.get(view.inspected_entity_id or "")
    if selected:
        health = sum(entity.health for entity in selected)
        maximum = sum(entity.kind.profile.max_health for entity in selected)
        label = (
            f"Enemy selected: {len(selected)}"
            if read_only
            else f"{len(selected)} selected"
            if len(selected) > 1
            else selected[0].state.value.title()
        )
        view.ui.small_text(screen, f"{label} | HP {health}/{maximum}", (x, y), (180, 198, 214))
        y += 24
    elif inspected is not None:
        view.ui.text(
            screen, inspected.kind.value.replace("_", " ").title(), (x, y), (244, 216, 118)
        )
        y += 28
        view.ui.small_text(
            screen,
            f"{inspected.owner_id} | HP {inspected.health}/{inspected.kind.profile.max_health}",
            (x, y),
            (180, 198, 214),
        )
        draw_unit_details(view, screen, inspected.kind, x, y + 28)
        view._type_buttons.clear()
        return
    kinds: dict[EntityKind, int] = {}
    for entity in view._selected_entities_for_draw():
        kinds[entity.kind] = kinds.get(entity.kind, 0) + 1
    view._type_buttons.clear()
    if not kinds:
        view.ui.small_text(screen, "Select a unit or building.", (x, y), (164, 184, 202))
        return
    if len(kinds) > 1:
        for kind, count in sorted(kinds.items(), key=lambda item: item[0].value):
            rectangle = pygame.Rect(x, y, panel.width - 32, 30)
            view.ui.button(screen, rectangle, f"{kind.value.replace('_', ' ').title()}  x{count}")
            view._type_buttons.append((rectangle, kind))
            y += 38
    else:
        view.inspected_kind = next(iter(kinds))
    detail_kind = view.inspected_kind if view.inspected_kind in kinds else None
    if detail_kind is None:
        view.ui.small_text(
            screen,
            "Choose a type to inspect." if read_only else "Choose a type to show its controls.",
            (x, y + 4),
            (164, 184, 202),
        )
        return
    if read_only:
        draw_unit_details(view, screen, detail_kind, x, y + 12)
        return
    y += 12
    profile = detail_kind.profile
    count = kinds[detail_kind]
    detail_title = (
        "Resource Extractor"
        if detail_kind is EntityKind.RESOURCE_GENERATOR
        else detail_kind.value.replace("_", " ").title()
    )
    if count > 1:
        detail_title += f" x{count}"
    view.ui.text(screen, detail_title, (x, y), (244, 216, 118))
    y += 34
    if detail_kind.profile.category is EntityCategory.BUILDING:
        price = (
            f"Cost {profile.construction_cost}"
            if profile.construction_cost
            else "Starting building"
        )
        view.ui.small_text(screen, price, (x, y), (180, 198, 214))
        y += 28
    if profile.movable and detail_kind is not EntityKind.BUILDER:
        view.ui.small_text(
            screen,
            f"Damage {profile.attack_damage} | Range {profile.attack_range} | Speed {profile.movement_speed}",
            (x, y),
            (180, 198, 214),
        )
        y += 28
    if detail_kind is EntityKind.BUILDER:
        view.ui.small_text(
            screen,
            f"Build {profile.build_range:g} | Repair {profile.repair_range:g} | Work {profile.build_speed:g}/tick",
            (x, y - 10),
            (105, 222, 172),
        )
        y += 22
        for kind in BUILDABLE_KINDS:
            rectangle = pygame.Rect(x, y, panel.width - 32, 30)
            label = (
                "Resource Extractor"
                if kind is EntityKind.RESOURCE_GENERATOR
                else kind.value.replace("_", " ").title()
            )
            view.ui.button(
                screen,
                rectangle,
                view.ui.fit_small_text(
                    f"{label} ({kind.profile.construction_cost})", rectangle.width - 8
                ),
            )
            view._command_buttons.append((rectangle, f"build:{kind.value}"))
            y += 38
        rectangle = pygame.Rect(x, y, panel.width - 32, 30)
        view.ui.button(screen, rectangle, "Reclaim building (70%)")
        view._command_buttons.append((rectangle, "reclaim"))
    elif detail_kind.production_kinds:
        factories = view._selected_producer_ids()
        for kind in detail_kind.production_kinds:
            row_width = panel.width - 32
            height = round(30 * view.ui_scale)
            gap = round(4 * view.ui_scale)
            loop_width = round(52 * view.ui_scale)
            loop = pygame.Rect(x + row_width - loop_width, y, loop_width, height)
            minus = pygame.Rect(loop.left - height - gap, y, height, height)
            plus = pygame.Rect(minus.left - height - gap, y, height, height)
            name = pygame.Rect(x, y, plus.left - x - gap, height)
            pending = sum(
                parameters.remaining_count(kind)
                for factory_id in factories
                for job in view.simulation.production_queue(factory_id)
                if isinstance((parameters := job.parameters), ProductionParameters)
                and not parameters.continuous
            )
            looping = any(
                (job := view.simulation.continuous_production(factory_id)) is not None
                and isinstance(job.parameters, ProductionParameters)
                and job.parameters.unit_kind is kind
                for factory_id in factories
            )
            label = kind.value.replace("_", " ").title()
            view.ui.button(
                screen, name, view.ui.fit_small_text(f"{label} ({pending})", name.width - 8)
            )
            view.ui.button(screen, plus, "+")
            view.ui.button(screen, minus, "-")
            view.ui.button(screen, loop, "Loop")
            if looping:
                pygame.draw.rect(screen, (111, 221, 151), loop, 2, border_radius=4)
            view._command_buttons.extend(
                (
                    (name, f"unit_details:{kind.value}"),
                    (plus, f"queue:{kind.value}"),
                    (minus, f"reduce:{kind.value}"),
                    (loop, f"loop:{kind.value}"),
                )
            )
            y += height + round(8 * view.ui_scale)
        if view.production_detail_kind in detail_kind.production_kinds:
            y = draw_unit_details(view, screen, view.production_detail_kind, x, y + 12)
    elif profile.income_per_second:
        income = profile.income_per_second
        if (
            detail_kind is EntityKind.RESOURCE_GENERATOR
            and not view.simulation.game_map.match_slots
        ):
            income = 1000
        view.ui.small_text(screen, f"Income {income}/s when complete", (x, y), (180, 198, 214))
    elif detail_kind is EntityKind.REPAIR_HUB:
        view.ui.small_text(
            screen,
            f"Repair range {profile.repair_range:g} | One unit at a time",
            (x, y),
            (105, 222, 172),
        )
    elif profile.attack_damage:
        view.ui.small_text(
            screen,
            f"Damage {profile.attack_damage} | Range {profile.attack_range:g}",
            (x, y),
            (180, 198, 214),
        )


def draw_unit_details(
    view: SelectionView, screen: pygame.Surface, kind: EntityKind, x: int, y: int
) -> int:
    profile = kind.profile
    view.ui.text(screen, kind.value.replace("_", " ").title(), (x, y), (244, 216, 118))
    y += 28
    lines = [
        f"Cost {profile.production_cost or profile.construction_cost} | HP {profile.max_health}",
        f"Damage {profile.attack_damage} | Range {profile.attack_range}",
        f"Speed {profile.movement_speed or 0} | Vision {profile.vision_range}",
    ]
    if profile.movable:
        lines.insert(
            1, f"Build time {Simulation.PRODUCTION_BUILD_TICKS * Simulation.TICK_SECONDS:g}s"
        )
        lines.append(f"Mass {profile.mass:g} | Collision radius {profile.collision_radius:g}")
    if profile.attack_damage:
        lines.append(
            f"Fire interval {profile.attack_interval_ticks / Simulation.TICKS_PER_SECOND:g}s"
        )
    if kind is EntityKind.BUILDER:
        lines.append(
            f"Build {profile.build_range:g} | Repair {profile.repair_range:g} | Work {profile.build_speed:g}/tick"
        )
    for line in lines:
        view.ui.small_text(screen, line, (x, y), (180, 198, 214))
        y += 20
    return y + 12
