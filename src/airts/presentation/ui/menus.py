"""Menus interface reads a scoped view and emits clickable actions."""

from __future__ import annotations

from typing import Protocol

import pygame

from airts.presentation.metrics import PresentationMetrics
from airts.presentation.ui import theme
from airts.presentation.ui.drawing import UiPainter


class MenusView(Protocol):
    _settings_buttons: list[tuple[pygame.Rect, str]]
    canvas_rect: pygame.Rect
    command_bar_rect: pygame.Rect
    presentation_metrics: PresentationMetrics
    ui: UiPainter
    window_size: tuple[int, int]
    developer_settings_open: bool
    show_ai_income: bool
    game_speed: float

    @property
    def ai_income_multiplier(self) -> int: ...

    @property
    def developer_mode(self) -> bool: ...


def draw_settings(view: MenusView, screen: pygame.Surface) -> None:
    if view.developer_settings_open:
        draw_developer_settings(view, screen)
        return
    x = view.command_bar_rect.x + 14
    y = view.command_bar_rect.y - 216
    view._settings_buttons.clear()
    theme.panel(screen, pygame.Rect(x, y, 268, 210))
    view.ui.small_text(
        screen,
        f"Resolution {view.window_size[0]} x {view.window_size[1]}",
        (x + 10, y + 8),
        theme.TEXT,
    )
    lower = pygame.Rect(x + 8, y + 30, 120, 24)
    higher = pygame.Rect(x + 140, y + 30, 120, 24)
    view.ui.button(screen, lower, "Lower")
    view.ui.button(screen, higher, "Higher")
    view._settings_buttons.extend(((lower, "resolution_lower"), (higher, "resolution_higher")))
    metrics = view.presentation_metrics
    view.ui.small_text(
        screen,
        f"Frame p95 {metrics.frame_p95_ms:5.1f} ms  Present {metrics.present_p95_ms:5.1f} ms",
        (x + 10, y + 62),
        theme.MUTED,
    )
    view.ui.small_text(
        screen,
        f"Render {metrics.render_p95_ms:5.1f} ms  Sim {metrics.simulation_p95_ms:5.1f} ms",
        (x + 10, y + 82),
        theme.MUTED,
    )
    view.ui.small_text(
        screen,
        f"Real FPS {metrics.one_percent_low_fps:4.0f}  Submit FPS {metrics.submit_fps:4.0f}",
        (x + 10, y + 102),
        theme.SUCCESS,
    )
    y += 122
    for action, label in (
        ("developer_settings", "Developer settings >"),
        ("saves", "Save manager"),
        ("new", "New game"),
    ):
        rectangle = pygame.Rect(x + 8, y, 252, 24)
        view.ui.button(screen, rectangle, label)
        view._settings_buttons.append((rectangle, action))
        y += 26


def draw_developer_settings(view: MenusView, screen: pygame.Surface) -> None:
    panel = pygame.Rect(view.command_bar_rect.x + 14, view.command_bar_rect.y - 282, 336, 276)
    panel.clamp_ip(screen.get_rect())
    x, y = panel.topleft
    theme.panel(screen, panel)
    view._settings_buttons.clear()
    view.ui.text(screen, "Developer settings", (x + 12, y + 8), theme.TEXT)
    toggle = pygame.Rect(x + 8, y + 36, 320, 26)
    state = "ON" if view.developer_mode else "OFF"
    view.ui.button(screen, toggle, f"Player resource bonus: {state}", active=view.developer_mode)
    view._settings_buttons.append((toggle, "developer_mode"))
    view.ui.small_text(
        screen, "+2000 player resources / game second", (x + 12, y + 66), theme.MUTED
    )
    view.ui.small_text(screen, "Game speed", (x + 12, y + 88), theme.TEXT)
    for index, speed in enumerate((0.5, 1, 2, 4)):
        rectangle = pygame.Rect(x + 8 + index * 82, y + 108, 74, 26)
        view.ui.button(screen, rectangle, f"{speed:g}x", active=view.game_speed == speed)
        view._settings_buttons.append((rectangle, f"speed:{speed:g}"))
    ai_toggle = pygame.Rect(x + 8, y + 144, 320, 28)
    boosted = view.ai_income_multiplier == 5
    view.ui.button(screen, ai_toggle, f"AI income 5x: {'ON' if boosted else 'OFF'}", active=boosted)
    view._settings_buttons.append((ai_toggle, "ai_income"))
    view.ui.small_text(
        screen, "All AI building income; OFF restores 1x", (x + 12, y + 176), theme.MUTED
    )
    observe = pygame.Rect(x + 8, y + 204, 320, 26)
    view.ui.button(
        screen,
        observe,
        f"Show AI income: {'ON' if view.show_ai_income else 'OFF'}",
        active=view.show_ai_income,
    )
    view._settings_buttons.append((observe, "show_ai_income"))
    back = pygame.Rect(x + 8, y + 240, 320, 26)
    view.ui.button(screen, back, "< Back to settings")
    view._settings_buttons.append((back, "settings_back"))


def draw_help(view: MenusView, screen: pygame.Surface) -> None:
    rectangle = pygame.Rect(
        view.canvas_rect.x + 40,
        80,
        max(320, view.canvas_rect.width - 80),
        316,
    )
    theme.panel(screen, rectangle)
    view.ui.text(screen, "Controls", (rectangle.x + 18, rectangle.y + 16), theme.TEXT)
    lines = (
        "1 Select   2 Line   3 Rectangle   4 Freehand",
        "Right-click Move/Attack or finish a line   Middle-drag Pan",
        "Single right-click preserves formation; double right-click gathers.",
        "WASD Camera   T Patrol   Y Defend   X Stop   H Hold",
        "R Repair   Space Pause   Wheel Zoom",
        "Minimap click/drag: Camera",
        "Shift+Right-click Queue move   Shift+Left-click/drag Select",
        "N Name   E Edit   U Retarget   [ ] Priority",
        "Choose a selected type in the right rail to show its actions.",
    )
    for index, line in enumerate(lines):
        view.ui.small_text(
            screen,
            line,
            (rectangle.x + 18, rectangle.y + 55 + index * 28),
            theme.MUTED,
        )
