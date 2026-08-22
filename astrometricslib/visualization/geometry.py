"""Geometry utilities for rotated visualization elements.

Enforces consistent rotation and mapping logic across the visualization
suite.
"""

import math


def rotate_point(x: float, y: float, angle_deg: float) -> tuple:
    """Rotates a point (x, y) around (0, 0) by angle_deg.

    Returns
    -------
    rotated_point : `tuple`
        The rotated ``(x, y)`` coordinates.
    """
    angle_rad = math.radians(angle_deg)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    return x * cos_a - y * sin_a, x * sin_a + y * cos_a


def get_rotated_rectangle_bottom_left(
    center_x: float, center_y: float, width: float, height: float, angle_deg: float
) -> tuple:
    """Calculate the bottom-left corner of a rotated rectangle.

    Matplotlib's Rectangle patch rotates around its bottom-left corner.

    Returns
    -------
    bottom_left : `tuple`
        The ``(x, y)`` coordinates of the rotated rectangle's
        bottom-left corner.
    """
    # Relative bottom-left corner before rotation
    rx, ry = -width / 2, -height / 2

    # Rotate this offset
    dx, dy = rotate_point(rx, ry, angle_deg)

    return center_x + dx, center_y + dy


def hit_test_rectangle(
    px: float,
    py: float,
    rect_center_x: float,
    rect_center_y: float,
    rect_width: float,
    rect_height: float,
    angle_deg: float,
) -> bool:
    """Check if point (px, py) is inside a rotated rectangle.

    Returns
    -------
    is_inside : `bool`
        `True` if the point falls within the rotated rectangle's
        bounds.
    """
    # 1. Translate point to rectangle origin
    dx = px - rect_center_x
    dy = py - rect_center_y

    # 2. Undo rotation for hit test
    rx, ry = rotate_point(dx, dy, -angle_deg)

    # 3. Check bounds
    return abs(rx) <= rect_width / 2 and abs(ry) <= rect_height / 2


def map_point_to_relative_x(
    px: float, py: float, rect_center_x: float, rect_center_y: float, angle_deg: float
) -> float:
    """Map a world coordinate (px, py) to a relative X coordinate.

    The relative coordinate is measured along the rectangle's primary
    axis.

    Returns
    -------
    relative_x : `float`
        The point's coordinate along the rectangle's primary axis.
    """
    dx = px - rect_center_x
    dy = py - rect_center_y
    rx, _ = rotate_point(dx, dy, -angle_deg)
    return rx
