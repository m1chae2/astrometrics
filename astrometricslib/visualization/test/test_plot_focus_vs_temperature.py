"""Purpose: Unit tests for plot_focus_vs_temperature.

Description: Verifies the focuser readings are read from a target's light
frames, drawn in time order, that frames without a focuser temperature are
left out, that the star-image width trend is fitted when there is enough to
fit, and that a target with no focuser readings is rejected clearly.
"""

from types import SimpleNamespace

import matplotlib.pyplot as plt
import numpy as np
import pytest

from astrometricslib.models.target import FrameRecord
from astrometricslib.visualization.focus_plots import (
    MINIMUM_FRAMES_FOR_WIDTH_TREND,
    plot_focus_vs_temperature,
)

# 04:00 UTC on 2026-09-25, in seconds since 1970.
START_TIMESTAMP = 1790308800.0


def make_frames(count: int, width_slope_px_per_c: float | None = None) -> list[FrameRecord]:
    """Build light frames whose focuser cools by 4 C over the night.

    Parameters
    ----------
    count : `int`
        How many frames to make.
    width_slope_px_per_c : `float`, optional
        When given, each frame also gets a star-image width that grows by
        this many pixels for every degree the focuser cools.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        The frames, in time order.
    """
    frames = []
    for index in range(count):
        temperature = 22.3 - 4.0 * index / (count - 1)
        fields = {
            "path": f"/fake/frame{index}.fits",
            "timestamp": START_TIMESTAMP + 120.0 * index,
            "focuserPosition": 29987,
            "focuserTemperatureC": round(temperature, 2),
        }
        if width_slope_px_per_c is not None:
            width = 4.2 + width_slope_px_per_c * (22.3 - temperature)
            fields["registrationFwhmXPx"] = width
            fields["registrationFwhmYPx"] = width
        record = FrameRecord(**fields)
        frames.append(record)
    return frames


def test_a_target_with_no_focuser_temperature_is_rejected() -> None:
    """A target whose frames carry no focuser temperature cannot be plotted."""
    target = SimpleNamespace(id="Vega", frames=[FrameRecord(path="/fake/a.fits")])
    with pytest.raises(ValueError, match="no frames with a focuser temperature"):
        plot_focus_vs_temperature(target)


def test_temperature_and_position_are_drawn_one_point_per_frame() -> None:
    """The top panel holds every frame's temperature and position."""
    target = SimpleNamespace(id="Vega", frames=make_frames(12))
    figure = plot_focus_vs_temperature(target)
    try:
        top, scatter_axes = figure.axes[0], figure.axes[2]
        temperature_line = top.get_lines()[0]
        assert len(temperature_line.get_xdata()) == 12
        assert temperature_line.get_ydata()[0] == pytest.approx(22.3)
        assert temperature_line.get_ydata()[-1] == pytest.approx(18.3)
        assert len(scatter_axes.collections[0].get_offsets()) == 12
    finally:
        plt.close(figure)


def test_frames_are_drawn_in_time_order_even_if_given_out_of_order() -> None:
    """Shuffled frames still give a temperature that falls left to right."""
    frames = make_frames(10)
    target = SimpleNamespace(id="Vega", frames=frames[5:] + frames[:5])
    figure = plot_focus_vs_temperature(target)
    try:
        temperatures = figure.axes[0].get_lines()[0].get_ydata()
        assert np.all(np.diff(temperatures) <= 0)
    finally:
        plt.close(figure)


def test_frames_without_a_focuser_temperature_and_dark_frames_are_left_out() -> None:
    """Only light frames that have a focuser temperature are drawn."""
    frames = make_frames(6)
    frames.append(FrameRecord(path="/fake/no_temperature.fits", timestamp=START_TIMESTAMP + 5000.0))
    dark = FrameRecord(
        path="/fake/dark.fits", role="DARK", timestamp=START_TIMESTAMP + 6000.0, focuserTemperatureC=20.0
    )
    frames.append(dark)
    figure = plot_focus_vs_temperature(SimpleNamespace(id="Vega", frames=frames))
    try:
        assert len(figure.axes[0].get_lines()[0].get_xdata()) == 6
    finally:
        plt.close(figure)


def test_the_star_width_trend_is_fitted_and_shown_in_the_title() -> None:
    """A width that grows 0.7 px per degree of cooling gives -0.7 px per C."""
    target = SimpleNamespace(id="Vega", frames=make_frames(20, width_slope_px_per_c=0.7))
    figure = plot_focus_vs_temperature(target)
    try:
        width_axes = figure.axes[-1]
        assert "-0.70 px per C" in width_axes.get_title()
        assert len(width_axes.collections[0].get_offsets()) == 20
    finally:
        plt.close(figure)


def test_without_star_widths_the_panel_says_how_to_get_them() -> None:
    """With no star-image widths the last panel explains what is missing."""
    figure = plot_focus_vs_temperature(SimpleNamespace(id="Vega", frames=make_frames(8)))
    try:
        texts = [text.get_text() for text in figure.axes[-1].texts]
        assert any("No frame has a star-image width" in text for text in texts)
    finally:
        plt.close(figure)


def test_too_few_frames_or_too_little_temperature_change_shows_no_trend_line() -> None:
    """A trend is not fitted from fewer than the minimum frames."""
    frames = make_frames(MINIMUM_FRAMES_FOR_WIDTH_TREND - 1, width_slope_px_per_c=0.7)
    figure = plot_focus_vs_temperature(SimpleNamespace(id="Vega", frames=frames))
    try:
        assert "px per C" not in figure.axes[-1].get_title()
    finally:
        plt.close(figure)
