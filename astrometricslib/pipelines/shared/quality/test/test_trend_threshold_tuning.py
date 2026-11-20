"""Tests for the trend-threshold tuner (#175).

The tuner groups stored star widths by target and night, keeps the ordinary
(not steady) windows, and takes a percentile of their fitted changes.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.foundation.tuning import collect_tuners
from astrometricslib.pipelines.shared.quality.trend_tuning import chance_changes, nightly_series


def _row(target: str | None, night: str | None, value: float | None) -> tuple:
    """Build one stored measurement row and its capture setting.

    Returns
    -------
    row : `tuple`
        ``(measurement, capture setting)``.
    """
    subject = SimpleNamespace(target_id=target, observing_night_id=night)
    return SimpleNamespace(subject=subject, value=value), SimpleNamespace()


def test_rows_are_grouped_by_target_and_night() -> None:
    """Each target and night is one series; rows without one are left out."""
    rows = [
        _row("M 31", "n1", 2.0),
        _row("M 31", "n2", 3.0),
        _row("M 31", "n1", 2.1),
        _row("M 13", "n1", 4.0),
        _row("M 31", None, 9.0),
        _row("M 31", "n1", None),
    ]
    assert sorted(nightly_series(rows)) == [[2.0, 2.1], [3.0], [4.0]]


def test_a_steady_drift_is_left_out_and_scatter_is_kept() -> None:
    """A clean ramp is a drift, not chance; an up-and-down series is chance."""
    ramp = [2.0 + 0.1 * index for index in range(10)]
    scatter = [2.0, 2.2, 1.9, 2.3, 2.0, 2.2, 1.9, 2.1, 2.0, 2.2]
    assert chance_changes([ramp], 10, 0.7) == []
    changes = chance_changes([scatter], 10, 0.7)
    assert len(changes) == 1
    assert changes[0] >= 0.0


def test_windows_slide_along_a_night() -> None:
    """A 12-frame night gives three 10-frame windows."""
    scatter = [2.0, 2.2, 1.9, 2.3, 2.0, 2.2, 1.9, 2.1, 2.0, 2.2, 1.9, 2.3]
    assert len(chance_changes([scatter], 10, 0.7)) == 3


def test_the_tuner_is_found() -> None:
    """`collect_tuners` finds the trend-threshold tuner."""
    names = {tuner.name for tuner in collect_tuners(("astrometricslib",))}
    assert "shared.trend_threshold" in names


@pytest.mark.parametrize("window", [4, 10])
def test_short_nights_give_no_window(window: int) -> None:
    """A night shorter than the window gives nothing."""
    assert chance_changes([[2.0] * (window - 1)], window, 0.7) == []
