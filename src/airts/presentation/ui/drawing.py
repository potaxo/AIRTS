"""Font lifetime, text layout, and shared interface primitives."""

import pygame


class UiPainter:
    def __init__(self) -> None:
        self.font: pygame.font.Font | None = None
        self.small_font: pygame.font.Font | None = None

    def button(self, screen: pygame.Surface, rectangle: pygame.Rect, label: str) -> None:
        pygame.draw.rect(screen, (59, 72, 88), rectangle, border_radius=3)
        pygame.draw.rect(screen, (104, 126, 149), rectangle, 1, border_radius=3)
        if self.small_font is None:
            raise RuntimeError("font not initialized")
        label = self.fit_small_text(label, rectangle.width - 12)
        width, height = self.small_font.size(label)
        self.small_text(
            screen,
            label,
            (rectangle.centerx - width // 2, rectangle.centery - height // 2),
            (240, 240, 240),
        )

    def text(
        self,
        screen: pygame.Surface,
        text: str,
        position: tuple[int, int],
        color: tuple[int, int, int],
    ) -> None:
        if self.font is None:
            raise RuntimeError("font not initialized")
        screen.blit(self.font.render(text, True, color), position)

    def small_text(
        self,
        screen: pygame.Surface,
        text: str,
        position: tuple[int, int],
        color: tuple[int, int, int],
    ) -> None:
        if self.small_font is None:
            raise RuntimeError("font not initialized")
        screen.blit(self.small_font.render(text, True, color), position)

    def fit_small_text(self, text: str, width: int) -> str:
        if self.small_font is None or self.small_font.size(text)[0] <= width:
            return text
        suffix = "..."
        available = max(0, width - self.small_font.size(suffix)[0])
        fitted = text
        while fitted and self.small_font.size(fitted)[0] > available:
            fitted = fitted[:-1]
        return fitted.rstrip() + suffix

    @staticmethod
    def wrap(text: str, width: int) -> list[str]:
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines
