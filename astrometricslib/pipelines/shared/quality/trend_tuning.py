"""Purpose: Tune the change that counts as a trend from this setup's nights.

Description: The frame trend check (`frame_trends`) flags a number that moves
steadily by more than `TREND_THRESHOLD_PERCENT` across the newest
`TREND_WINDOW_FRAMES` frames. How far a number moves by chance depends on the
setup and the sky: star width scatters more on a long focal length in poor
seeing than on a short one. This tuner reads the stored star widths of the
setup's camera, groups them by target and night, and slides the trend window
along each group. A window whose steps do not mostly go one way is ordinary
scatter, not a drift, so its fitted change shows how far the star width moves
by chance. The threshold is the 95th percentile of those changes: a change
that ordinary windows reach only one time in twenty.

The star width is the number the trend check worries about most (focus drift
and dew), so its scatter sets the one threshold that all trended numbers
share.
"""

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

from astrometricslib._internal.drivers.tuning_support import camera_rows
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningResult, TuningSkippedError
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.quality.frame_trends import (
    STEADY_STEP_FRACTION,
    TREND_THRESHOLD_PERCENT,
    TREND_WINDOW_FRAMES,
    describe_trend,
)
from astrometricslib.pipelines.shared.quality.metrics import FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX

# A change that ordinary windows reach one time in twenty: the usual
# one-sided 5% level for "larger than chance".
TREND_THRESHOLD_PERCENTILE = TunedParameter(
    name="shared.trend_threshold_percentile",
    default=95.0,
    unit="percent",
    description="Percentile of the chance changes of ordinary windows that sets the trend threshold.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D21",
)


def nightly_series(rows: Iterable[tuple[Any, Any]]) -> list[list[float]]:
    """Group stored per-frame values into one series per target and night.

    Parameters
    ----------
    rows : `Iterable` [`tuple`]
        ``(measurement, capture setting)`` pairs, oldest first.

    Returns
    -------
    series : `list` [`list` [`float`]]
        The finite values of each target and night, in stored order. Rows
        with no night are left out, because frames of different nights must
        not share a window.
    """
    groups: dict[tuple[str | None, str], list[float]] = defaultdict(list)
    for measurement, _setting in rows:
        subject = measurement.subject
        night = getattr(subject, "observing_night_id", None)
        value = measurement.value
        if night is None or value is None or not math.isfinite(float(value)):
            continue
        groups[getattr(subject, "target_id", None), night].append(float(value))
    return list(groups.values())


def chance_changes(series: Iterable[Sequence[float]], window: int, steady_fraction: float) -> list[float]:
    """Collect the fitted changes of ordinary (not steady) windows.

    Parameters
    ----------
    series : `Iterable` [`Sequence` [`float`]]
        One series of values per target and night.
    window : `int`
        Frames per window.
    steady_fraction : `float`
        A window whose share of steps going one way reaches this is a drift
        and is left out.

    Returns
    -------
    changes : `list` [`float`]
        The size of each ordinary window's fitted change, in percent.
    """
    changes = []
    for values in series:
        for start in range(len(values) - window + 1):
            trend = describe_trend(values[start : start + window], math.inf)
            if "change_percent" not in trend or trend["steady_fraction"] >= steady_fraction:
                continue
            changes.append(abs(trend["change_percent"]))
    return changes


def tune_trend_threshold(inputs: TuningInputs) -> TuningResult:
    """Derive the trend threshold from the setup's stored star widths.

    Parameters
    ----------
    inputs : `TuningInputs`
        The setup and config.

    Returns
    -------
    result : `TuningResult`
        The percentile of the ordinary windows' changes, resting on the
        number of windows.

    Raises
    ------
    TuningSkippedError
        If too few ordinary windows are stored.
    """
    rows = camera_rows(inputs.configuration, inputs.setup.camera_name, FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX)
    window = int(resolve(TREND_WINDOW_FRAMES).value)
    changes = chance_changes(nightly_series(rows), window, float(resolve(STEADY_STEP_FRACTION).value))
    minimum = TREND_THRESHOLD_PERCENT.minimum_sample or 0
    if len(changes) < minimum:
        raise TuningSkippedError(
            f"only {len(changes)} ordinary {window}-frame windows of star width (need {minimum})"
        )
    percentile = float(resolve(TREND_THRESHOLD_PERCENTILE).value)
    threshold = round(float(np.percentile(changes, percentile)), 1)
    return TuningResult(
        values={TREND_THRESHOLD_PERCENT.name: threshold},
        sample=len(changes),
        source=(
            f"{percentile:g}th percentile of the star-width change across {len(changes)} ordinary "
            f"{window}-frame windows of this camera's stored nights"
        ),
    )


TREND_THRESHOLD_TUNER = Tuner(
    name="shared.trend_threshold",
    parameters=(TREND_THRESHOLD_PERCENT,),
    method=(
        "The 95th percentile of the star-width change across the ordinary (not steady) windows of the "
        "setup camera's stored nights."
    ),
    tune=tune_trend_threshold,
)


__all__ = [
    "TREND_THRESHOLD_TUNER",
    "chance_changes",
    "nightly_series",
]
