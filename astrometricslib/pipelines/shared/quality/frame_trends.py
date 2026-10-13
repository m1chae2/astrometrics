"""Purpose: Spot a slow, steady change across a run of frames.

Description: Dew, thin cloud and a drifting focus do not spoil one frame;
they change a number a little more with every frame. A single-frame check
cannot see that. This module looks at one number over the most recent
frames, in time order, and says how far it moved and whether it moved
steadily. It uses the median of all pairwise slopes (the Theil-Sen slope),
so one bad frame does not fake or hide a trend.

A trend is flagged only when both tests hold: the fitted change across the
window is at least the caller's threshold, and most steps between
neighbouring frames go the same way. The threshold is the caller's choice,
because what counts as a worry depends on the target and the night.
"""

import statistics
from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Any

MINIMUM_FRAMES = 4
"""Fewest frames a trend can be judged from. Fewer say nothing."""

STEADY_STEP_FRACTION = 0.7
"""The share of neighbouring-frame steps that must go the same way as the
fitted change. Random scatter goes up and down about equally, so about half
the steps agree; a steady drift has most steps agreeing. Chosen so a clean
60-frame run with no drift is not flagged while a ramp with scatter is."""

DIRECTIONS = {"rising": "risen", "falling": "fallen"}
"""Words for the two directions of change."""


def _theil_sen_slope(values: Sequence[float]) -> float:
    """Give the median slope between every pair of points.

    Parameters
    ----------
    values : `Sequence` [`float`]
        The numbers, one per frame, in time order.

    Returns
    -------
    slope : `float`
        The typical change per frame.
    """
    slopes = [
        (values[later] - values[earlier]) / (later - earlier)
        for earlier in range(len(values))
        for later in range(earlier + 1, len(values))
    ]
    return statistics.median(slopes)


def describe_trend(values: Sequence[float], threshold_percent: float) -> dict[str, Any]:
    """Say whether a series of numbers is drifting steadily.

    Parameters
    ----------
    values : `Sequence` [`float`]
        One number per frame, in time order, with missing values removed.
    threshold_percent : `float`
        The change across the window, as a percentage of its starting level,
        that counts as a trend.

    Returns
    -------
    trend : `dict` [`str`, `Any`]
        ``frames``, the ``first_level`` and ``last_level`` of the fitted
        line, ``change_percent``, ``steady_fraction`` and ``flagged``. With
        too few frames only ``frames`` and ``flagged: False`` come back.
    """
    count = len(values)
    if count < MINIMUM_FRAMES:
        return {"frames": count, "flagged": False}
    slope = _theil_sen_slope(values)
    middle = statistics.median(values)
    first_level = middle - slope * (count - 1) / 2.0
    last_level = middle + slope * (count - 1) / 2.0
    change_percent = 100.0 * (last_level - first_level) / first_level if first_level else 0.0
    steps = [later - earlier for earlier, later in pairwise(values)]
    agreeing = sum(1 for step in steps if step * slope > 0)
    steady_fraction = agreeing / len(steps)
    flagged = abs(change_percent) >= threshold_percent and steady_fraction >= STEADY_STEP_FRACTION
    return {
        "frames": count,
        "first_level": round(first_level, 4),
        "last_level": round(last_level, 4),
        "change_percent": round(change_percent, 1),
        "steady_fraction": round(steady_fraction, 2),
        "flagged": flagged,
    }


def find_trends(
    rows: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, str],
    window: int,
    threshold_percent: float,
) -> dict[str, Any]:
    """Check several per-frame numbers for a steady drift.

    Parameters
    ----------
    rows : `Sequence` [`Mapping`]
        One row per frame, in time order.
    metrics : `Mapping` [`str`, `str`]
        The row key to check, mapped to the direction that is a worry:
        ``"rising"``, ``"falling"`` or ``"either"``. Drift the other way is
        reported but never flagged.
    window : `int`
        How many of the newest frames to look at.
    threshold_percent : `float`
        See `describe_trend`.

    Returns
    -------
    trends : `dict` [`str`, `Any`]
        ``window``, ``threshold_percent``, a ``metrics`` entry per key (see
        `describe_trend`, plus ``direction`` and ``worrying``), and
        ``alerts``: one plain sentence for each worrying trend.
    """
    recent = list(rows)[-max(window, MINIMUM_FRAMES) :]
    results: dict[str, Any] = {}
    alerts = []
    for name, worrying_direction in metrics.items():
        values = [float(row[name]) for row in recent if row.get(name) is not None]
        trend = describe_trend(values, threshold_percent)
        change = trend.get("change_percent")
        direction = None if change is None else ("rising" if change > 0 else "falling")
        trend["direction"] = direction
        trend["worrying"] = bool(trend["flagged"] and worrying_direction in (direction, "either"))
        results[name] = trend
        if trend["worrying"]:
            alerts.append(
                f"{name} has {DIRECTIONS[direction]} steadily over the last {trend['frames']} frames: "
                f"{trend['first_level']:g} to {trend['last_level']:g} ({change:+.0f}%)."
            )
    return {"window": window, "threshold_percent": threshold_percent, "metrics": results, "alerts": alerts}
