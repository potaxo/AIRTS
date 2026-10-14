"""Packed shape-instance layout shared by scene primitives and textured sprites."""

from array import array

type FloatColor = tuple[float, float, float, float]

SHAPE_FLOATS = 24


def append_sprite(
    values: array[float],
    center: tuple[float, float],
    previous: tuple[float, float],
    layout: tuple[float, ...],
    color: FloatColor,
    index: float,
    previous_angle: float,
    angle: float,
) -> None:
    """The same record layout with static dimensions already packed for its kind."""
    values.extend(
        (*center, *previous, *layout, *color, 3.0, index, previous_angle, angle, 0.0, 0.0)
    )


def append_shape(
    values: array[float],
    *,
    center: tuple[float, float],
    half_size: tuple[float, float],
    color: FloatColor,
    circle: bool,
    previous_center: tuple[float, float] | None = None,
    minimum_half_size: tuple[float, float] = (0.0, 0.0),
    pixel_offset: tuple[float, float] = (0.0, 0.0),
    pixel_padding: tuple[float, float] = (0.0, 0.0),
    size_fraction: tuple[float, float] = (1.0, 1.0),
    health_bar: bool = False,
    outline_width: float = 0.0,
    outline_color: FloatColor = (0.0, 0.0, 0.0, 0.0),
) -> None:
    previous = center if previous_center is None else previous_center
    values.extend(
        (
            center[0],
            center[1],
            previous[0],
            previous[1],
            half_size[0],
            half_size[1],
            *minimum_half_size,
            *pixel_offset,
            *pixel_padding,
            *size_fraction,
            *color,
            2.0 if health_bar else 1.0 if circle else 0.0,
            outline_width,
            *outline_color,
        )
    )
