"""Selection interface reads a scoped view and emits clickable actions."""

from __future__ import annotations

import json
from collections import Counter
from typing import Protocol

import pygame

from airts.contracts.automations import (
    ProductionParameters,
)
from airts.presentation.palette import PROGRESS_COLOR, PROGRESS_TRACK
from airts.presentation.progress import current_production
from airts.presentation.ui import theme
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
    placement_kind: EntityKind | None
    reclaim_mode: bool
    selection_panel_rect: pygame.Rect
    selection_scroll: int
    selection_content_height: int
    simulation: Simulation
    ui: UiPainter
    ui_scale: float

    def _selected_entities_for_draw(self) -> tuple[Entity, ...]: ...
    def _selected_producer_ids(self) -> tuple[str, ...]: ...


def content_rect(view: SelectionView) -> pygame.Rect:
    panel = view.selection_panel_rect
    return pygame.Rect(panel.x + 4, panel.y + 40, panel.width - 8, max(1, panel.height - 44))


def scroll(view: SelectionView, steps: int) -> None:
    maximum = max(0, view.selection_content_height - content_rect(view).height)
    view.selection_scroll = max(
        0, min(maximum, view.selection_scroll + steps * round(64 * view.ui_scale))
    )


def draw_selection(view: SelectionView, screen: pygame.Surface) -> None:
    panel = view.selection_panel_rect
    theme.panel(screen, panel)
    view.ui.text(screen, "Selection", (panel.x + 12, panel.y + 10), theme.TEXT)
    visible = content_rect(view)
    previous_clip = screen.get_clip()
    screen.set_clip(previous_clip.clip(visible))
    start = len(view._command_buttons)
    try:
        for _ in range(2):
            bottom = _draw_contents(view, screen)
            view.selection_content_height = bottom - visible.top + view.selection_scroll + 8
            old_scroll = view.selection_scroll
            scroll(view, 0)
            if old_scroll == view.selection_scroll:
                break
            screen.fill(theme.PANEL, visible)
            del view._command_buttons[start:]
    finally:
        screen.set_clip(previous_clip)
    view._command_buttons[start:] = [
        (clipped, action)
        for rectangle, action in view._command_buttons[start:]
        if (clipped := rectangle.clip(visible)).width and clipped.height
    ]
    view._type_buttons[:] = [
        (clipped, kind)
        for rectangle, kind in view._type_buttons
        if (clipped := rectangle.clip(visible)).width and clipped.height
    ]
    if view.selection_content_height > visible.height:
        track = pygame.Rect(panel.right - 6, visible.y, 3, visible.height)
        pygame.draw.rect(screen, theme.TRACK, track)
        height = max(20, round(visible.height**2 / view.selection_content_height))
        maximum = view.selection_content_height - visible.height
        y = track.y + round((track.height - height) * view.selection_scroll / maximum)
        pygame.draw.rect(screen, theme.MUTED, (track.x, y, track.width, height))


def _draw_contents(view: SelectionView, screen: pygame.Surface) -> int:
    panel = view.selection_panel_rect
    x = panel.x + 12
    y = content_rect(view).top - view.selection_scroll
    row_height = max(24, round(26 * view.ui_scale))
    row_gap = max(4, round(5 * view.ui_scale))
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
        view.ui.small_text(screen, f"{label} | HP {health}/{maximum}", (x, y), theme.MUTED)
        y += 24
        if read_only:
            teams = {slot.owner_id: slot.team for slot in view.simulation.game_map.match_slots}
            for owner, count in sorted(Counter(entity.owner_id for entity in selected).items()):
                faction = f"Faction: {owner}"
                if owner in teams:
                    faction += f" | Team {teams[owner]}"
                view.ui.small_text(screen, f"{faction} | x{count}", (x, y), theme.MUTED)
                y += 24
    elif inspected is not None:
        view.ui.text(screen, inspected.kind.value.replace("_", " ").title(), (x, y), theme.ACCENT)
        y += 28
        view.ui.small_text(
            screen,
            f"{inspected.owner_id} | HP {inspected.health}/{inspected.kind.profile.max_health}",
            (x, y),
            theme.MUTED,
        )
        y = draw_unit_details(view, screen, inspected.kind, x, y + 28)
        if inspected.kind.upgrades:
            y = draw_upgrade_controls(view, screen, (inspected,), x, y, read_only=True)
        view._type_buttons.clear()
        return y
    kinds: dict[EntityKind, int] = {}
    for entity in view._selected_entities_for_draw():
        kinds[entity.kind] = kinds.get(entity.kind, 0) + 1
    view._type_buttons.clear()
    if not kinds:
        view.ui.small_text(screen, "Select a unit or building.", (x, y), theme.MUTED)
        return y + 24
    if len(kinds) > 1:
        for kind, count in sorted(kinds.items(), key=lambda item: item[0].value):
            rectangle = pygame.Rect(x, y, panel.width - 24, row_height)
            view.ui.button(
                screen,
                rectangle,
                f"{kind.value.replace('_', ' ').title()}  x{count}",
                active=view.inspected_kind is kind,
            )
            view._type_buttons.append((rectangle, kind))
            y += row_height + row_gap
    else:
        view.inspected_kind = next(iter(kinds))
    detail_kind = view.inspected_kind if view.inspected_kind in kinds else None
    if detail_kind is None:
        view.ui.small_text(
            screen,
            "Choose a type to inspect." if read_only else "Choose a type to show its controls.",
            (x, y + 4),
            theme.MUTED,
        )
        return y + 28
    if read_only:
        y = draw_unit_details(view, screen, detail_kind, x, y + 12)
        if detail_kind.upgrades:
            y = draw_upgrade_controls(
                view,
                screen,
                tuple(e for e in selected if e.kind is detail_kind),
                x,
                y,
                read_only=True,
            )
        return y
    y += 8
    profile = detail_kind.profile
    count = kinds[detail_kind]
    detail_title = (
        "Resource Extractor"
        if detail_kind is EntityKind.RESOURCE_GENERATOR
        else detail_kind.value.replace("_", " ").title()
    )
    if count > 1:
        detail_title += f" x{count}"
    view.ui.text(screen, detail_title, (x, y), theme.ACCENT)
    y += 28
    if detail_kind.profile.category is EntityCategory.BUILDING:
        price = (
            f"Cost {profile.construction_cost}"
            if profile.construction_cost
            else "Starting building"
        )
        view.ui.small_text(screen, price, (x, y), theme.MUTED)
        y += 24
    if profile.movable and not profile.can_build:
        view.ui.small_text(
            screen,
            f"Damage {profile.attack_damage} | Range {profile.attack_range} | Speed {profile.movement_speed}",
            (x, y),
            theme.MUTED,
        )
        y += 28
    if profile.can_build or profile.can_reclaim:
        view.ui.small_text(
            screen,
            f"Build {profile.build_range:g} | Repair {profile.repair_range:g} | Work {profile.build_speed:g}/tick",
            (x, y - 10),
            theme.SUCCESS,
        )
        y += 22
        for kind in BUILDABLE_KINDS if profile.can_build else ():
            rectangle = pygame.Rect(x, y, panel.width - 24, row_height)
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
                active=view.placement_kind is kind,
            )
            view._command_buttons.append((rectangle, f"build:{kind.value}"))
            y += row_height + row_gap
        if profile.can_reclaim:
            rectangle = pygame.Rect(x, y, panel.width - 24, row_height)
            view.ui.button(screen, rectangle, "Reclaim building (70%)", active=view.reclaim_mode)
            view._command_buttons.append((rectangle, "reclaim"))
            y += row_height
    elif detail_kind.production_kinds:
        factories = view._selected_producer_ids()
        current_jobs = tuple(
            current_job
            for factory_id in factories
            if (current_job := current_production(view.simulation, factory_id)) is not None
        )
        for kind in detail_kind.production_kinds:
            row_width = panel.width - 24
            height = row_height
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
                screen,
                name,
                view.ui.fit_small_text(f"{label} ({pending})", name.width - 8),
                active=view.production_detail_kind is kind,
            )
            view.ui.button(screen, plus, "+")
            view.ui.button(screen, minus, "-")
            view.ui.button(screen, loop, "Loop", active=looping)
            view._command_buttons.extend(
                (
                    (name, f"unit_details:{kind.value}"),
                    (plus, f"queue:{kind.value}"),
                    (minus, f"reduce:{kind.value}"),
                    (loop, f"loop:{kind.value}"),
                )
            )
            progress = [
                min(1.0, parameters.progress_ticks / parameters.build_ticks)
                for parameters in current_jobs
                if parameters.current_unit_kind is kind
            ]
            if progress:
                bar = pygame.Rect(x, y + height, row_width, max(3, round(3 * view.ui_scale)))
                pygame.draw.rect(screen, PROGRESS_TRACK, bar)
                pygame.draw.rect(
                    screen,
                    PROGRESS_COLOR,
                    (bar.x, bar.y, round(bar.width * min(progress)), bar.height),
                )
            y += height + row_gap
        if view.production_detail_kind in detail_kind.production_kinds:
            y = draw_unit_details(view, screen, view.production_detail_kind, x, y + 12)
    elif profile.income_per_second:
        y = draw_upgrade_controls(
            view, screen, tuple(entity for entity in selected if entity.kind is detail_kind), x, y
        )
    elif detail_kind is EntityKind.REPAIR_HUB:
        view.ui.small_text(
            screen,
            f"Repair range {profile.repair_range:g} | One target at a time",
            (x, y),
            theme.SUCCESS,
        )
        view.ui.small_text(
            screen, "Repairs units and completed buildings", (x, y + 20), theme.MUTED
        )
        y += 40
    elif profile.attack_damage:
        view.ui.small_text(
            screen,
            f"Damage {profile.attack_damage} | Range {profile.attack_range:g}",
            (x, y),
            theme.MUTED,
        )
        y += 24
    return y


def draw_upgrade_controls(
    view: SelectionView,
    screen: pygame.Surface,
    entities: tuple[Entity, ...],
    x: int,
    y: int,
    *,
    read_only: bool = False,
) -> int:
    """One row per selected tier/progress group, with independent building commands."""
    groups: dict[tuple[int, bool, bool], list[Entity]] = {}
    for entity in entities:
        key = (entity.upgrade.tier, bool(entity.upgrade.remaining_ticks), entity.is_complete)
        groups.setdefault(key, []).append(entity)
    for (tier, upgrading, complete), members in sorted(groups.items()):
        entity = members[0]
        suffix = f" x{len(members)}" if len(members) > 1 else ""
        view.ui.small_text(
            screen,
            f"T{tier}{suffix}"
            + (
                f" | {entity.profile.income_per_second} each / s"
                if entity.profile.income_per_second
                else ""
            )
            + ("" if complete else " | Unfinished"),
            (x, y),
            theme.MUTED,
        )
        y += 24
        definition = entity.next_upgrade
        if not complete:
            continue
        if definition is None:
            view.ui.small_text(screen, "Maximum tier", (x, y), theme.SUCCESS)
            y += 28
            continue
        if upgrading:
            remaining = max(item.upgrade.remaining_ticks for item in members)
            view.ui.small_text(
                screen,
                f"To T{tier + 1}: up to {remaining / Simulation.TICKS_PER_SECOND:.1f}s",
                (x, y),
                theme.ACCENT,
            )
            y += 22
            label = "Cancel upgrade (full refund)"
            operation = "cancel_upgrade"
            enabled = True
            progress = 1 - remaining / definition.duration_ticks
            bar = pygame.Rect(x, y, view.selection_panel_rect.width - 24, 6)
            pygame.draw.rect(screen, PROGRESS_TRACK, bar)
            pygame.draw.rect(
                screen, PROGRESS_COLOR, (bar.x, bar.y, round(bar.width * progress), bar.height)
            )
            y += 12
        else:
            total_cost = definition.cost * len(members)
            label = f"Upgrade T{tier + 1}: {total_cost} | {definition.duration_ticks / Simulation.TICKS_PER_SECOND:g}s"
            operation = "upgrade"
            enabled = view.simulation.resources.get(entity.owner_id, 0) >= total_cost
            if not enabled:
                label += " (need resources)"
        if read_only:
            continue
        rectangle = pygame.Rect(x, y, view.selection_panel_rect.width - 24, 28)
        view.ui.button(screen, rectangle, label, enabled=enabled)
        if enabled:
            view._command_buttons.append(
                (rectangle, f"{operation}:" + json.dumps([item.entity_id for item in members]))
            )
        y += 34
    return y


def draw_unit_details(
    view: SelectionView, screen: pygame.Surface, kind: EntityKind, x: int, y: int
) -> int:
    profile = kind.profile
    view.ui.text(screen, kind.value.replace("_", " ").title(), (x, y), theme.ACCENT)
    y += 28
    lines = [
        f"Cost {profile.production_cost or profile.construction_cost} | HP {profile.max_health}",
        f"Damage {profile.attack_damage} | Range {profile.attack_range}",
        f"Speed {profile.movement_speed or 0} | Vision {profile.vision_range}",
    ]
    if profile.movable:
        lines.insert(1, f"Build time {kind.profile.production_ticks * Simulation.TICK_SECONDS:g}s")
        lines.append(f"Mass {profile.mass:g} | Collision radius {profile.collision_radius:g}")
    if profile.attack_damage:
        lines.append(
            f"Fire interval {profile.attack_interval_ticks / Simulation.TICKS_PER_SECOND:g}s"
        )
    if profile.splash_radius:
        lines.append(f"Splash radius {profile.splash_radius:g}")
    if profile.can_build or profile.can_repair:
        lines.append(
            f"Build {profile.build_range:g} | Repair {profile.repair_range:g} | Work {profile.build_speed:g}/tick"
        )
    for line in lines:
        view.ui.small_text(screen, line, (x, y), theme.MUTED)
        y += 20
    return y + 12
