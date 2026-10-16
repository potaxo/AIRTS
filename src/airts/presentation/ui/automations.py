"""Automations interface reads a scoped view and emits clickable actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import pygame

from airts.contracts.automations import (
    Automation,
    AutomationKind,
    AutomationStatus,
    ConstructionParameters,
    DefendParameters,
    PatrolParameters,
    ProductionParameters,
)
from airts.presentation.ui import theme
from airts.presentation.ui.drawing import UiPainter
from airts.simulation import Simulation

AUTOMATION_ROW_HEIGHT = 34
AUTOMATION_SCROLLBAR_MIN_THUMB_HEIGHT = 32
AUTOMATION_SCROLLBAR_WIDTH = 12
PANEL_BACKGROUND = theme.PANEL


@dataclass(slots=True)
class AutomationPanelState:
    """Local inspection and scroll state; never part of authoritative automations."""

    selected_id: str | None = None
    details_open: bool = False
    group: AutomationKind | None = AutomationKind.PRODUCTION
    new_area_auto_repair: bool = False
    scroll: int = 0
    buttons: list[tuple[pygame.Rect, str, str]] = field(default_factory=list)
    content_bottom: int = 0
    list_top: int = 0
    visible_rows: int = 6
    scroll_drag_offset: int | None = None
    scrollbar_track: pygame.Rect | None = None
    scrollbar_thumb: pygame.Rect | None = None


class AutomationsView(Protocol):
    automation_panel: AutomationPanelState
    _command_buttons: list[tuple[pygame.Rect, str]]
    canvas_rect: pygame.Rect
    left_panel_rect: pygame.Rect
    notice: str
    paused: bool
    real_fps: float
    right_panel_rect: pygame.Rect
    simulation: Simulation
    ui: UiPainter
    ui_scale: float


def scroll(view: AutomationsView, delta: int, *, visible_rows: int, total_rows: int) -> None:
    maximum = max(0, total_rows - visible_rows)
    view.automation_panel.scroll = max(0, min(maximum, view.automation_panel.scroll + delta))


def panel_rows(view: AutomationsView) -> tuple[Automation, ...]:
    live_automations = tuple(
        automation
        for automation in view.simulation.live_automations
        if automation.owner_id == "player"
    )
    linked_defense_ids = {
        parameters.defend_automation_id
        for automation in live_automations
        if automation.kind is AutomationKind.PRODUCTION
        and isinstance((parameters := automation.parameters), ProductionParameters)
        and parameters.defend_automation_id is not None
    }
    return tuple(
        automation
        for automation in live_automations
        if automation.automation_id not in linked_defense_ids
        and (
            automation.kind is view.automation_panel.group
            if view.automation_panel.group is not None
            else automation.kind not in {AutomationKind.PRODUCTION, AutomationKind.CONSTRUCTION}
        )
    )


def clamp_scroll(view: AutomationsView, *, visible_rows: int, total_rows: int) -> None:
    scroll(view, 0, visible_rows=visible_rows, total_rows=total_rows)


def scroll_from_pointer(view: AutomationsView, pointer_y: int) -> None:
    track = view.automation_panel.scrollbar_track
    thumb = view.automation_panel.scrollbar_thumb
    rows = panel_rows(view)
    maximum = max(0, len(rows) - view.automation_panel.visible_rows)
    if track is None or thumb is None or maximum == 0:
        view.automation_panel.scroll = 0
        return
    travel = track.height - thumb.height
    if travel <= 0:
        view.automation_panel.scroll = 0
        return
    offset = view.automation_panel.scroll_drag_offset
    thumb_top = pointer_y - (thumb.height // 2 if offset is None else offset)
    fraction = max(0.0, min(1.0, (thumb_top - track.top) / travel))
    view.automation_panel.scroll = round(fraction * maximum)


def draw_panel(
    view: AutomationsView,
    screen: pygame.Surface,
    *,
    background_regions: tuple[pygame.Rect, ...] | None = None,
) -> None:
    if view.ui.font is None or view.ui.small_font is None:
        raise RuntimeError("fonts not initialized")
    panel = view.left_panel_rect
    previous_clip = screen.get_clip()
    for region in (panel,) if background_regions is None else background_regions:
        screen.set_clip(previous_clip.clip(region))
        theme.panel(screen, panel)
    screen.set_clip(previous_clip)
    x = 16
    y = 14
    view.ui.text(screen, "AIRTS", (x, y), theme.TEXT)
    pause = pygame.Rect(panel.right - 90, y - 2, 74, 26)
    view.ui.button(screen, pause, "Resume" if view.paused else "Pause")
    view._command_buttons.append((pause, "pause_game"))
    y += 28
    view.ui.small_text(
        screen,
        f"Real FPS {view.real_fps:4.0f} | {'PAUSED' if view.paused else 'RUNNING'}",
        (x, y),
        theme.MUTED,
    )
    y += 25
    for line in view.ui.wrap(view.notice, 46):
        view.ui.small_text(screen, line, (x, y), theme.ACCENT)
        y += 18
    y += 7
    help_button = pygame.Rect(x, y, 72, 26)
    view.ui.button(screen, help_button, "Help")
    view._command_buttons.append((help_button, "help"))
    y += 38
    view.ui.small_text(
        screen,
        f"Resources: {view.simulation.resources.get('player', 0)}",
        (x, y),
        theme.SUCCESS,
    )
    y += 20
    view.ui.text(screen, "Automations", (x, y), theme.TEXT)
    y += 25
    groups = (
        (None, "Units"),
        (AutomationKind.PRODUCTION, "Produce"),
        (AutomationKind.CONSTRUCTION, "Build"),
    )
    tab_width = (panel.width - 32) // 3
    for index, (group, label) in enumerate(groups):
        tab = pygame.Rect(x + index * tab_width, y, tab_width - 3, 26)
        view.ui.button(
            screen,
            tab,
            view.ui.fit_small_text(label, tab.width - 8),
            active=view.automation_panel.group is group,
        )
        view._command_buttons.append(
            (tab, f"automation_group:{group.value if group is not None else 'units'}")
        )
    y += 34
    if view.automation_panel.group is None:
        repair = pygame.Rect(x, y, panel.width - 32, round(28 * view.ui_scale))
        enabled = view.automation_panel.new_area_auto_repair
        view.ui.button(
            screen,
            repair,
            view.ui.fit_small_text(
                f"New area repair: {'ON' if enabled else 'OFF'} (<50%)", repair.width - 8
            ),
            active=enabled,
        )
        view._command_buttons.append((repair, "toggle_new_area_auto_repair"))
        y += repair.height + round(8 * view.ui_scale)
    view.automation_panel.buttons.clear()
    live_automations = panel_rows(view)
    live_ids = {automation.automation_id for automation in live_automations}
    if view.automation_panel.selected_id not in live_ids:
        if view.automation_panel.details_open:
            view.automation_panel.details_open = False
    list_top = y
    view.automation_panel.list_top = list_top
    row_height = round(AUTOMATION_ROW_HEIGHT * view.ui_scale)
    list_bottom = max(list_top + row_height, panel.bottom - 16)
    view.automation_panel.visible_rows = max(1, (list_bottom - list_top) // row_height)
    clamp_scroll(
        view,
        visible_rows=view.automation_panel.visible_rows,
        total_rows=len(live_automations),
    )
    visible_automations = live_automations[
        view.automation_panel.scroll : view.automation_panel.scroll
        + view.automation_panel.visible_rows
    ]
    track = pygame.Rect(
        panel.right - AUTOMATION_SCROLLBAR_WIDTH - 6,
        list_top,
        AUTOMATION_SCROLLBAR_WIDTH,
        view.automation_panel.visible_rows * row_height,
    )
    total_rows = len(live_automations)
    has_overflow = total_rows > view.automation_panel.visible_rows
    view.automation_panel.scrollbar_track = track if has_overflow else None
    if has_overflow:
        pygame.draw.rect(screen, theme.TRACK, track, border_radius=0)
    thumb_height = (
        track.height
        if total_rows <= view.automation_panel.visible_rows
        else max(
            AUTOMATION_SCROLLBAR_MIN_THUMB_HEIGHT,
            round(track.height * view.automation_panel.visible_rows / total_rows),
        )
    )
    maximum = max(0, total_rows - view.automation_panel.visible_rows)
    thumb_y = track.top + (
        0
        if maximum == 0
        else round((track.height - thumb_height) * view.automation_panel.scroll / maximum)
    )
    thumb = pygame.Rect(track.x, thumb_y, track.width, thumb_height)
    view.automation_panel.scrollbar_thumb = thumb if has_overflow else None
    if has_overflow:
        pygame.draw.rect(screen, theme.ACCENT, thumb, border_radius=0)
        pygame.draw.rect(screen, theme.TEXT, thumb, 2, border_radius=0)
    for automation in visible_automations:
        height = round(26 * view.ui_scale)
        gap = round(4 * view.ui_scale)
        details = pygame.Rect(
            track.left - round(36 * view.ui_scale), y, round(30 * view.ui_scale), height
        )
        cancel = pygame.Rect(
            details.left - round(25 * view.ui_scale) - gap, y, round(25 * view.ui_scale), height
        )
        toggle = pygame.Rect(
            cancel.left - round(28 * view.ui_scale) - gap, y, round(28 * view.ui_scale), height
        )
        action = "resume" if automation.status is AutomationStatus.PAUSED else "pause"
        name = pygame.Rect(x, y, max(1, toggle.left - x - 5), 26)
        view.ui.small_text(
            screen,
            view.ui.fit_small_text(automation.title, name.width),
            (x, y + 5),
            theme.MUTED if automation.status is AutomationStatus.PAUSED else theme.TEXT,
        )
        view.ui.button(screen, toggle, ">" if action == "resume" else "||")
        view.ui.button(screen, cancel, "X")
        view.ui.button(screen, details, "...")
        view.automation_panel.buttons.extend(
            (
                (toggle, action, automation.automation_id),
                (cancel, "cancel", automation.automation_id),
                (details, "inspect", automation.automation_id),
            )
        )
        y += row_height
    view.automation_panel.content_bottom = y + 8


def details_lines(view: AutomationsView) -> list[str]:
    automation = view.simulation.automations.get(view.automation_panel.selected_id or "")
    if automation is None or automation.status.terminal:
        return []
    lines = [automation.title, f"{automation.status.value.title()} | {automation.reason_code}"]
    parameters = automation.parameters
    if isinstance(parameters, ProductionParameters):
        lines.extend(
            [
                f"Source: {parameters.factory_id}",
                f"{parameters.current_unit_kind.value.replace('_', ' ').title()} | "
                + (
                    f"Loop | Produced {parameters.produced_count}"
                    if parameters.continuous
                    else f"Remaining {parameters.target_count - parameters.produced_count}"
                ),
                f"Progress {parameters.progress_ticks}/{parameters.build_ticks}",
            ]
        )
    elif isinstance(parameters, ConstructionParameters):
        lines.append(
            f"{parameters.building_kind.value.replace('_', ' ').title()} | "
            f"{parameters.construction_value:.0f}/{parameters.required_value:.0f}"
        )
    else:
        lines.append(f"Units {len(automation.entity_ids)} | Priority {automation.priority}")
    if isinstance(parameters, PatrolParameters | DefendParameters | ProductionParameters):
        lines.append(f"Auto repair: {'ON' if parameters.auto_repair else 'OFF'} | HP <50% (R)")
    return lines


def details_rect(view: AutomationsView) -> pygame.Rect | None:
    if not view.automation_panel.details_open:
        return None
    lines = details_lines(view)
    if not lines:
        return None
    width = min(
        round(340 * view.ui_scale), view.right_panel_rect.left - view.left_panel_rect.right - 16
    )
    height = round(64 * view.ui_scale) + max(20, round(20 * view.ui_scale)) * len(lines)
    automation = view.simulation.automations[view.automation_panel.selected_id or ""]
    if isinstance(
        automation.parameters, PatrolParameters | DefendParameters | ProductionParameters
    ):
        height += round(36 * view.ui_scale)
    row_y = next(
        (
            rectangle.y
            for rectangle, action, automation_id in view.automation_panel.buttons
            if action == "inspect" and automation_id == view.automation_panel.selected_id
        ),
        view.automation_panel.list_top,
    )
    y = max(8, min(row_y, view.canvas_rect.bottom - height - 8))
    return pygame.Rect(view.left_panel_rect.right + 8, y, width, height)


def draw_details(view: AutomationsView, screen: pygame.Surface) -> None:
    panel = details_rect(view)
    if panel is None:
        return
    theme.panel(screen, panel)
    padding = round(16 * view.ui_scale)
    x, y = panel.x + padding, panel.y + padding
    close_size = round(26 * view.ui_scale)
    close = pygame.Rect(panel.right - close_size - 10, panel.y + 10, close_size, close_size)
    view.ui.text(screen, "Automation details", (x, y), theme.ACCENT)
    view.ui.button(screen, close, "X")
    view._command_buttons.append((close, "close_automation_details"))
    y += round(28 * view.ui_scale)
    width = panel.width - 2 * padding
    for line in details_lines(view):
        view.ui.small_text(screen, view.ui.fit_small_text(line, width), (x, y), theme.MUTED)
        y += max(20, round(20 * view.ui_scale))
    automation = view.simulation.automations[view.automation_panel.selected_id or ""]
    if isinstance(
        automation.parameters, PatrolParameters | DefendParameters | ProductionParameters
    ):
        repair = pygame.Rect(x, y + 4, width, round(28 * view.ui_scale))
        view.ui.button(
            screen,
            repair,
            f"Auto repair: {'ON' if automation.parameters.auto_repair else 'OFF'} (<50%)",
        )
        view._command_buttons.append((repair, f"toggle_auto_repair:{automation.automation_id}"))
