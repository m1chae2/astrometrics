"""Purpose: Unit tests for _wire_star_click_selection.

Description: Verifies the click-to-select wiring shared by every
interactive target-level plot (photometry, spectroscopy, the combined
dashboard) -- extracted so that behavior only needs testing once.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import matplotlib.pyplot as plt
from matplotlib.backend_bases import MouseEvent

from astrometricslib.visualization.helpers import _wire_star_click_selection
from astrometricslib.visualization.visualization_config import VisualizationConfig


def _make_star(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(star_data={"xcentroid": x, "ycentroid": y})


def _click_at(fig, inaxes, xdata: float, ydata: float) -> None:  # ruff: ignore[missing-type-function-argument]
    """Fire a real button_press_event, with xdata/ydata forced to given values.

    A genuine `MouseEvent` is used (not a bare stand-in) because
    matplotlib's own default handlers are on the same callback
    registry and read fields -- `button`, `dblclick`, `canvas` -- a
    minimal fake object wouldn't have.
    """
    event = MouseEvent("button_press_event", fig.canvas, x=0, y=0)
    event.inaxes = inaxes
    event.xdata = xdata
    event.ydata = ydata
    fig.canvas.callbacks.process("button_press_event", event)


def test_click_near_a_star_selects_it_and_calls_on_select():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A click within a star's radius highlights it and reports its index."""
    fig, ax = plt.subplots()
    config = VisualizationConfig()
    stars = [_make_star(10.0, 10.0), _make_star(50.0, 50.0)]
    patch0, patch1 = MagicMock(), MagicMock()
    on_select = MagicMock()

    _wire_star_click_selection(fig, ax, stars, [patch0, patch1], config, on_select)
    _click_at(fig, ax, 50.0, 50.0)

    on_select.assert_called_once_with(1)
    patch1.set_edgecolor.assert_called_with(config.active_color)
    patch0.set_edgecolor.assert_called_with(config.inactive_color)
    plt.close(fig)


def test_click_far_from_any_star_does_nothing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A click outside every star's radius selects nothing."""
    fig, ax = plt.subplots()
    config = VisualizationConfig()
    patch0 = MagicMock()
    on_select = MagicMock()

    _wire_star_click_selection(fig, ax, [_make_star(10.0, 10.0)], [patch0], config, on_select)
    _click_at(fig, ax, 500.0, 500.0)

    on_select.assert_not_called()
    patch0.set_edgecolor.assert_not_called()
    plt.close(fig)


def test_click_outside_the_star_field_axis_is_ignored():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A click reported against a different axis (a side panel) is ignored."""
    fig, (ax, other_ax) = plt.subplots(1, 2)
    config = VisualizationConfig()
    on_select = MagicMock()

    _wire_star_click_selection(fig, ax, [_make_star(10.0, 10.0)], [MagicMock()], config, on_select)
    _click_at(fig, other_ax, 10.0, 10.0)

    on_select.assert_not_called()
    plt.close(fig)


def test_a_star_missing_pixel_coordinates_is_skipped_not_crashed():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A star with no centroid data can't be clicked, but nothing crashes."""
    fig, ax = plt.subplots()
    config = VisualizationConfig()
    stars = [SimpleNamespace(star_data={}), _make_star(50.0, 50.0)]
    patch0, patch1 = MagicMock(), MagicMock()
    on_select = MagicMock()

    _wire_star_click_selection(fig, ax, stars, [patch0, patch1], config, on_select)
    _click_at(fig, ax, 50.0, 50.0)

    on_select.assert_called_once_with(1)
    plt.close(fig)
