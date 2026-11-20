"""Purpose: Derive the live-status thresholds from the mount's own records.

Description: The live session status (`live_status`) flags a guide error that
runs far above normal (an excursion), a frame with too few stars and a frame
whose guide error is high compared with its neighbors. Each threshold
should come from how this mount behaves, so this module reads the stored
guiding samples and the stored Ekos sessions and derives four families of
values:

- **Excursion limit** (`wayfinding.live_status_guide_limit`). For each night,
  the guide error that 99.9 percent of its samples stay under. The multiple
  is the median over nights of that level divided by the night's median error.
  The floor is the smallest level of any night.
- **Excursion join and jump** (`wayfinding.live_status_excursions`). With the
  limit above, the gaps between over-limit samples fall into two groups: short
  gaps inside one event and long gaps between events. The join time is the
  middle of the widest gap. Each excursion's first sample, as a share of its
  peak, again falls into two groups, drifts and jumps, and the jump fraction
  is the middle of the widest gap between them.
- **Star baseline** (`wayfinding.live_status_star_baseline`). The usual star
  count is the median of the earlier frames. The most frames to look back
  over is the count that predicts the next frame's star count best. The fewest
  is the smallest count whose prediction is nearly as good.

- **Early exposure length** (`wayfinding.early_exposure_seconds`). The Ekos
  session records hold each successful dither. For each one, the time until
  the guide error is back within 1.5 times the night's median error. The
  length is the median of those times.
- **Stale record time** (`wayfinding.stale_record_seconds`). The 99.9th
  percentile of the gaps between consecutive guide samples in a night,
  times 3. A longer silence than any pause of normal guiding is a stop.

Values with no honest source in the stored data are not derived: how many
samples a line needs (a statistical minimum). How long before an exposure a
dither counts is worked out on each run from the exposure and the measured
settle (`live_status.dither_follow_seconds`). Whether a drift matches a
stopped mount is a statistics test (`live_status.matches_stopped_mount`).

The statistics are pure functions of plain numbers, so a test needs no
library.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections.abc import Sequence
from typing import Any

from astrometricslib.parameters import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    Tuner,
    TuningInputs,
    TuningResult,
    TuningSkippedError,
    resolve_parameter,
)
from wayfindinglib.analytics.tuning_support import (
    butler_of,
    largest_log_gap_threshold,
    percentile,
    records_of,
)
from wayfindinglib.models.session.telemetry import MEASURED_GUIDING_SAMPLE_SOURCES
from wayfindinglib.session_analysis.live_status import (
    EARLY_EXPOSURE_SECONDS,
    EXCURSION_FLOOR_ARCSEC,
    EXCURSION_JOIN_SECONDS,
    EXCURSION_MEDIAN_MULTIPLE,
    JUMP_FIRST_SAMPLE_FRACTION,
    MAXIMUM_FRAMES_IN_STAR_BASELINE,
    MINIMUM_FRAMES_FOR_STAR_BASELINE,
    STALE_RECORD_SECONDS,
    _baseline_key,
)

__all__ = [
    "EARLY_EXPOSURE_TUNER",
    "EXCURSIONS_TUNER",
    "GUIDE_LIMIT_TUNER",
    "STALE_RECORD_TUNER",
    "STAR_BASELINE_TUNER",
    "dither_recovery_seconds",
    "excursion_runs",
    "first_sample_ratios",
    "join_threshold",
    "night_quiet_levels",
    "sample_gaps",
    "star_baseline_errors",
]

EXCURSION_QUIET_PERCENT = TunedParameter(
    name="wayfinding.excursion_quiet_percent",
    default=99.9,
    unit="percent",
    description="Share of a night's guide samples that the quiet level of the night must cover.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="WAY-D136",
)
"""Share of a night's guide samples that the quiet level must cover.

Guiding samples come every few seconds, so a night holds thousands. The 99.9th
percentile leaves about one sample in a thousand above the quiet level, which
is the rate of a stray spike, while a real excursion holds many samples in a
row. Register entry WAY-D136."""

MINIMUM_SAMPLES_FOR_QUIET_LEVEL = TunedParameter(
    name="wayfinding.minimum_samples_for_quiet_level",
    default=1000,
    unit="samples",
    description="A night needs at least this many guide samples to give a 99.9th percentile.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="WAY-D136",
)
"""A night needs at least this many guide samples to give a 99.9th percentile.

Below a thousand samples the 99.9th percentile is the single largest sample,
so it measures one spike, not a level. Register entry WAY-D136."""

MINIMUM_EXCURSION_GAP_RATIO = TunedParameter(
    name="wayfinding.minimum_excursion_gap_ratio",
    default=3.0,
    unit="ratio",
    description="The widest gap between two groups of values must be at least this ratio to split them.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="WAY-D136",
)
"""The widest gap between two groups of values must be at least this ratio.

A ratio of 3 is the smallest gap that is plainly wider than the spread inside
either group (short gaps inside an event, drift against jump). Register entry
WAY-D136."""

TOLERANCE_ON_BEST_BASELINE_ERROR = TunedParameter(
    name="wayfinding.tolerance_on_best_baseline_error",
    default=0.25,
    unit="fraction",
    description="The fewest baseline frames are the fewest whose error is within this share of the best.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="WAY-D136",
)
"""The fewest baseline frames are the fewest whose error is within this share
of the best. A quarter more error than the best window is a baseline that is
still usable for a flag that fires at half the usual stars. Register entry
WAY-D136."""

GUIDE_ERROR_NORMAL_MULTIPLE = TunedParameter(
    name="wayfinding.guide_error_normal_multiple",
    default=1.5,
    unit="multiple",
    description="After a dither the guide error is back to normal within this multiple of the median.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="WAY-D136",
)
"""Guide error is normal within this multiple of the median after a dither.

Ordinary guiding scatters about its median, with single samples well above it,
so a level of exactly the median would never be reached for good. One and a
half times the median is above that scatter and far below the 2 to 5 times
seen right after a dither (the 2026-10-02 M 27 session). Register entry
WAY-D136."""

STALE_RECORD_GAP_MULTIPLE = TunedParameter(
    name="wayfinding.stale_record_gap_multiple",
    default=3.0,
    unit="multiple",
    description="A file is stale after this multiple of the longest ordinary gap between guide samples.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="WAY-D136",
)
"""A file is stale after this multiple of the longest ordinary gap.

The 99.9th percentile of the gaps is the longest pause that normal guiding
makes (a re-lock, a dither, a lost star found again). Three times that is a
silence that is clearly not one of them, so the warning does not fire during
normal pauses. Register entry WAY-D136."""


def night_quiet_levels(
    night_errors: Sequence[Sequence[float]], percent: float, minimum_samples: int
) -> list[tuple[float, float]]:
    """Find the median and the quiet level of each night's guide error.

    Parameters
    ----------
    night_errors : `list` [`list` [`float`]]
        The total guide error of each sample, one list per night.
    percent : `float`
        The percentile that sets the quiet level.
    minimum_samples : `int`
        A night with fewer samples is left out.

    Returns
    -------
    levels : `list` [`tuple` [`float`, `float`]]
        For each night kept, its median error and its quiet level.
    """
    levels = []
    for errors in night_errors:
        if len(errors) < minimum_samples:
            continue
        median = statistics.median(errors)
        if median > 0.0:
            levels.append((median, percentile(errors, percent)))
    return levels


def excursion_runs(
    times: Sequence[float], errors: Sequence[float], limit: float, join_seconds: float
) -> list[list[tuple[float, float]]]:
    """Group over-limit samples into excursions.

    Parameters
    ----------
    times, errors : `~collections.abc.Sequence` [`float`]
        The time and total error of each sample, in time order.
    limit : `float`
        Errors above this are over the limit.
    join_seconds : `float`
        Two over-limit samples no further apart than this are one excursion.

    Returns
    -------
    runs : `list` [`list` [`tuple` [`float`, `float`]]]
        Each excursion as its ``(time, error)`` samples.
    """
    runs: list[list[tuple[float, float]]] = []
    for time, error in zip(times, errors, strict=True):
        if error <= limit:
            continue
        if runs and time - runs[-1][-1][0] <= join_seconds:
            runs[-1].append((time, error))
        else:
            runs.append([(time, error)])
    return runs


def join_threshold(over_limit_gaps: Sequence[float], minimum_ratio: float) -> float | None:
    """Find the gap that separates one event's samples from the next event.

    Parameters
    ----------
    over_limit_gaps : `~collections.abc.Sequence` [`float`]
        The time between each over-limit sample and the next one, in seconds.
    minimum_ratio : `float`
        The least ratio across the widest gap.

    Returns
    -------
    seconds : `float` or `None`
        The middle of the widest gap, or `None` when there is none.
    """
    return largest_log_gap_threshold(over_limit_gaps, minimum_ratio)


def first_sample_ratios(runs: Sequence[Sequence[tuple[float, float]]]) -> list[float]:
    """Give each excursion's first error as a share of its peak.

    Parameters
    ----------
    runs : `list` [`list` [`tuple` [`float`, `float`]]]
        Excursions, as from `excursion_runs`.

    Returns
    -------
    ratios : `list` [`float`]
        First error divided by the largest error, one per excursion.
    """
    return [run[0][1] / max(error for _time, error in run) for run in runs]


def star_baseline_errors(
    star_count_runs: Sequence[Sequence[int]], largest_window: int
) -> dict[int, tuple[float, int]]:
    """Measure how well the median of the last k frames predicts the next.

    Parameters
    ----------
    star_count_runs : `list` [`list` [`int`]]
        Star counts of unbroken runs of frames of one target, filter and
        exposure, oldest first.
    largest_window : `int`
        The largest k to try. The smallest is 2.

    Returns
    -------
    errors : `dict` [`int`, `tuple` [`float`, `int`]]
        For each k, the median over frames of ``|count / usual - 1|`` (the
        usual count being the median of the last k frames), and how many
        frames it rests on. A k with no frame is left out.
    """
    errors: dict[int, tuple[float, int]] = {}
    for window in range(2, largest_window + 1):
        relative_errors = []
        for counts in star_count_runs:
            for index in range(window, len(counts)):
                usual = statistics.median(counts[index - window : index])
                if usual > 0:
                    relative_errors.append(abs(counts[index] / usual - 1.0))
        if relative_errors:
            errors[window] = (statistics.median(relative_errors), len(relative_errors))
    return errors


def dither_recovery_seconds(
    dither_times: Sequence[float],
    sample_times: Sequence[float],
    sample_errors: Sequence[float],
    normal_error: float,
) -> list[float]:
    """Measure how long the guide error takes to recover after dithers.

    For each dither, the guide samples after it (up to the next dither) are
    read in order. The error is back to normal at the first sample that is
    within `normal_error`, together with the sample after it (a lone low
    sample among noisy ones does not count). A dither with no such sample
    before the next dither is left out.

    Parameters
    ----------
    dither_times : `~collections.abc.Sequence` [`float`]
        When each dither succeeded, in time order.
    sample_times, sample_errors : `~collections.abc.Sequence` [`float`]
        The time and total error of each guide sample, in time order.
    normal_error : `float`
        The error at or below which guiding is normal, in arcseconds.

    Returns
    -------
    seconds : `list` [`float`]
        The time from each dither to its return to normal.
    """
    recoveries = []
    for index, dither_time in enumerate(dither_times):
        end = dither_times[index + 1] if index + 1 < len(dither_times) else math.inf
        window = [
            (time, error)
            for time, error in zip(sample_times, sample_errors, strict=True)
            if dither_time <= time < end
        ]
        for position, (time, error) in enumerate(window):
            next_is_normal = position + 1 >= len(window) or window[position + 1][1] <= normal_error
            if error <= normal_error and next_is_normal:
                recoveries.append(time - dither_time)
                break
    return recoveries


def sample_gaps(night_times: Sequence[Sequence[float]]) -> list[float]:
    """List the gaps between consecutive guide samples, night by night.

    Parameters
    ----------
    night_times : `list` [`list` [`float`]]
        The time of each guide sample, one list per night, in time order.

    Returns
    -------
    gaps : `list` [`float`]
        The seconds from each sample to the next within a night.
    """
    return [later - earlier for times in night_times for earlier, later in itertools.pairwise(times)]


def _guide_nights(records: Any) -> list[str]:
    """List the nights that have stored guiding samples.

    Parameters
    ----------
    records : `ControlRecordStore`
        The stored records.

    Returns
    -------
    nights : `list` [`str`]
        The session ids of the nights with a guiding run or an Ekos session.

    Raises
    ------
    TuningSkippedError
        If the recorded nights cannot be read.
    """
    sessions = records.recorded_session_nights()
    if sessions is None:
        raise TuningSkippedError("the recorded nights cannot be read")
    return sorted(sessions[0] | sessions[1])


def _night_series(records: Any, night: str) -> tuple[list[float], list[float]]:
    """Read one night's measured guide error and its times.

    Parameters
    ----------
    records : `ControlRecordStore`
        The stored records.
    night : `str`
        The night.

    Returns
    -------
    times, errors : `tuple` [`list` [`float`], `list` [`float`]]
        The time and total error of each measured sample, in time order.
    """
    sources = [source.value for source in MEASURED_GUIDING_SAMPLE_SOURCES]
    rows = records.get_guiding_samples(session_id=night, limit=500_000, sources=sources)
    times = [float(row["timestamp"]) for row in rows]
    errors = [math.hypot(float(row["dra"]), float(row["ddec"])) for row in rows]
    return times, errors


def tune_guide_limit(inputs: TuningInputs) -> TuningResult:
    """Derive the excursion multiple and floor from each night's quiet level.

    Parameters
    ----------
    inputs : `TuningInputs`
        The tuning run's inputs. Reads the ``"wayfinder"`` service.

    Returns
    -------
    result : `TuningResult`
        The multiple of the median and the floor, in arcseconds.

    Raises
    ------
    TuningSkippedError
        If no night has enough measured guide samples.
    """
    records = records_of(inputs.service("wayfinder"))
    night_errors = [_night_series(records, night)[1] for night in _guide_nights(records)]
    levels = night_quiet_levels(
        night_errors,
        resolve_parameter(EXCURSION_QUIET_PERCENT).value,
        resolve_parameter(MINIMUM_SAMPLES_FOR_QUIET_LEVEL).value,
    )
    if not levels:
        raise TuningSkippedError("no night has enough measured guide samples")
    multiple = statistics.median(level / median for median, level in levels)
    floor = min(level for _median, level in levels)
    return TuningResult(
        values={EXCURSION_MEDIAN_MULTIPLE.name: multiple, EXCURSION_FLOOR_ARCSEC.name: floor},
        sample=len(levels),
        source=(
            f"The median over {len(levels)} nights of the 99.9th percentile of the guide error divided by "
            "the night's median (the multiple), and the smallest of those percentiles (the floor)."
        ),
    )


def tune_excursions(inputs: TuningInputs) -> TuningResult:
    """Derive the join time and the jump fraction from the excursions found.

    Parameters
    ----------
    inputs : `TuningInputs`
        The tuning run's inputs. Reads the ``"wayfinder"`` service.

    Returns
    -------
    result : `TuningResult`
        The join time in seconds and the jump fraction, each left out when
        its data show no two groups.

    Raises
    ------
    TuningSkippedError
        If no night has enough samples or no over-limit samples exist.
    """
    records = records_of(inputs.service("wayfinder"))
    series = [_night_series(records, night) for night in _guide_nights(records)]
    levels = night_quiet_levels(
        [errors for _times, errors in series],
        resolve_parameter(EXCURSION_QUIET_PERCENT).value,
        resolve_parameter(MINIMUM_SAMPLES_FOR_QUIET_LEVEL).value,
    )
    if not levels:
        raise TuningSkippedError("no night has enough measured guide samples")
    multiple = statistics.median(level / median for median, level in levels)
    floor = min(level for _median, level in levels)
    minimum_ratio = resolve_parameter(MINIMUM_EXCURSION_GAP_RATIO).value

    gaps: list[float] = []
    limits: list[float | None] = []
    for times, errors in series:
        if len(errors) < resolve_parameter(MINIMUM_SAMPLES_FOR_QUIET_LEVEL).value:
            limits.append(None)
            continue
        limit = max(multiple * statistics.median(errors), floor)
        limits.append(limit)
        over = [time for time, error in zip(times, errors, strict=True) if error > limit]
        gaps.extend(later - earlier for earlier, later in itertools.pairwise(over))
    join = join_threshold(gaps, minimum_ratio)
    if join is None:
        raise TuningSkippedError(f"the {len(gaps)} gaps between over-limit samples show no separate events")

    runs: list[list[tuple[float, float]]] = []
    for (times, errors), limit in zip(series, limits, strict=True):
        if limit is not None:
            runs.extend(excursion_runs(times, errors, limit, join))
    values = {EXCURSION_JOIN_SECONDS.name: join}
    jump = largest_log_gap_threshold(first_sample_ratios(runs), minimum_ratio)
    if jump is not None:
        values[JUMP_FIRST_SAMPLE_FRACTION.name] = jump
    return TuningResult(
        values=values,
        sample=len(runs),
        source=(
            f"The middle of the widest gap among {len(gaps)} gaps between over-limit guide samples, and of "
            f"the widest gap among the first-sample shares of {len(runs)} excursions, in "
            f"{len(levels)} nights."
        ),
    )


def tune_star_baseline(inputs: TuningInputs) -> TuningResult:
    """Derive the star-count baseline length from the stored captures.

    Parameters
    ----------
    inputs : `TuningInputs`
        The tuning run's inputs. Reads the ``"wayfinder"`` service.

    Returns
    -------
    result : `TuningResult`
        The fewest and the most earlier frames of the baseline.

    Raises
    ------
    TuningSkippedError
        If the stored Ekos sessions hold too few frames to compare.
    """
    contexts = butler_of(inputs.service("wayfinder")).get_all("ekos_session_context")
    runs: list[list[int]] = []
    for context in contexts:
        captures = sorted(context.captures, key=lambda capture: capture.completed_at)
        key = None
        for capture in captures:
            capture_key = _baseline_key(capture)
            if capture_key != key:
                runs.append([])
                key = capture_key
            runs[-1].append(capture.star_count)
    high = int(MAXIMUM_FRAMES_IN_STAR_BASELINE.allowed_range[1])
    errors = star_baseline_errors(runs, high)
    if not errors:
        raise TuningSkippedError("no recorded run of frames is long enough to test a baseline")
    best_error = min(error for error, _count in errors.values())
    best_window = min(window for window, (error, _count) in errors.items() if error == best_error)
    tolerance = resolve_parameter(TOLERANCE_ON_BEST_BASELINE_ERROR).value
    fewest = min(
        window for window, (error, _count) in errors.items() if error <= best_error * (1.0 + tolerance)
    )
    frames = errors[best_window][1]
    return TuningResult(
        values={
            MINIMUM_FRAMES_FOR_STAR_BASELINE.name: fewest,
            MAXIMUM_FRAMES_IN_STAR_BASELINE.name: best_window,
        },
        sample=frames,
        source=(
            f"The window whose median of earlier star counts predicts the next frame best ({best_window} "
            f"frames, median error {best_error:.3f}) and the smallest window within {tolerance:.0%} of "
            f"that error, over {frames} frames of {len(runs)} runs."
        ),
    )


def tune_early_exposure(inputs: TuningInputs) -> TuningResult:
    """Derive the early-exposure length from the recovery after a dither.

    Parameters
    ----------
    inputs : `TuningInputs`
        The tuning run's inputs. Reads the ``"wayfinder"`` service.

    Returns
    -------
    result : `TuningResult`
        The median recovery time in seconds.

    Raises
    ------
    TuningSkippedError
        If the stored sessions hold no successful dither with guide samples
        that return to normal after it.
    """
    wayfinder = inputs.service("wayfinder")
    records = records_of(wayfinder)
    contexts = butler_of(wayfinder).get_all("ekos_session_context")
    multiple = resolve_parameter(GUIDE_ERROR_NORMAL_MULTIPLE).value
    series: dict[str, tuple[list[float], list[float]]] = {}
    recoveries: list[float] = []
    for context in contexts:
        dither_times = sorted(
            event.timestamp for event in context.guide_state_events if event.state == "Dithering successful"
        )
        if not dither_times:
            continue
        if context.session_id not in series:
            series[context.session_id] = _night_series(records, context.session_id)
        times, errors = series[context.session_id]
        if not errors:
            continue
        normal_error = multiple * statistics.median(errors)
        recoveries.extend(dither_recovery_seconds(dither_times, times, errors, normal_error))
    if not recoveries:
        raise TuningSkippedError("no stored dither has guide samples that return to normal after it")
    return TuningResult(
        values={EARLY_EXPOSURE_SECONDS.name: statistics.median(recoveries)},
        sample=len(recoveries),
        source=(
            f"The median time after {len(recoveries)} successful dithers until the guide error was back "
            f"within {multiple:g} times the night's median."
        ),
    )


def tune_stale_record(inputs: TuningInputs) -> TuningResult:
    """Derive the stale-record time from the gaps between guide samples.

    Parameters
    ----------
    inputs : `TuningInputs`
        The tuning run's inputs. Reads the ``"wayfinder"`` service.

    Returns
    -------
    result : `TuningResult`
        The multiple of the 99.9th percentile of the gaps, in seconds.

    Raises
    ------
    TuningSkippedError
        If there are too few gaps for a 99.9th percentile.
    """
    records = records_of(inputs.service("wayfinder"))
    gaps = sample_gaps([_night_series(records, night)[0] for night in _guide_nights(records)])
    minimum_gaps = resolve_parameter(MINIMUM_SAMPLES_FOR_QUIET_LEVEL).value
    if len(gaps) < minimum_gaps:
        raise TuningSkippedError(
            f"only {len(gaps)} gaps between guide samples, fewer than the {minimum_gaps} "
            "a 99.9th percentile needs"
        )
    quiet_percent = resolve_parameter(EXCURSION_QUIET_PERCENT).value
    multiple = resolve_parameter(STALE_RECORD_GAP_MULTIPLE).value
    longest_ordinary_gap = percentile(gaps, quiet_percent)
    return TuningResult(
        values={STALE_RECORD_SECONDS.name: multiple * longest_ordinary_gap},
        sample=len(gaps),
        source=(
            f"{multiple:g} times the {quiet_percent:g}th percentile ({longest_ordinary_gap:.0f} s) of "
            f"{len(gaps)} gaps between consecutive guide samples."
        ),
    )


EARLY_EXPOSURE_TUNER = Tuner(
    name="wayfinding.live_status_early_exposure",
    parameters=(EARLY_EXPOSURE_SECONDS,),
    method=(
        "For each successful dither in the stored Ekos sessions, the time until the guide error is back "
        "within 1.5 times the night's median error. The length is the median over dithers."
    ),
    tune=tune_early_exposure,
)

STALE_RECORD_TUNER = Tuner(
    name="wayfinding.live_status_stale_record",
    parameters=(STALE_RECORD_SECONDS,),
    method=(
        "Three times the 99.9th percentile of the gaps between consecutive measured guide samples within "
        "a night."
    ),
    tune=tune_stale_record,
)

GUIDE_LIMIT_TUNER = Tuner(
    name="wayfinding.live_status_guide_limit",
    parameters=(EXCURSION_MEDIAN_MULTIPLE, EXCURSION_FLOOR_ARCSEC),
    method=(
        "Reads the measured guide samples of every recorded night. The multiple is the median over nights "
        "of the 99.9th percentile of the night's guide error divided by its median, and the floor is the "
        "smallest such percentile."
    ),
    tune=tune_guide_limit,
)

EXCURSIONS_TUNER = Tuner(
    name="wayfinding.live_status_excursions",
    parameters=(EXCURSION_JOIN_SECONDS, JUMP_FIRST_SAMPLE_FRACTION),
    method=(
        "Finds the over-limit guide samples of every recorded night. The join time is the middle of the "
        "widest gap, on a logarithmic scale, between the gaps of those samples; the jump fraction is the "
        "same for the first-sample share of each excursion's peak."
    ),
    tune=tune_excursions,
)

STAR_BASELINE_TUNER = Tuner(
    name="wayfinding.live_status_star_baseline",
    parameters=(MINIMUM_FRAMES_FOR_STAR_BASELINE, MAXIMUM_FRAMES_IN_STAR_BASELINE),
    method=(
        "Reads the star counts of the recorded captures. The most frames is the window whose median of "
        "earlier counts predicts the next frame's count best; the fewest is the smallest window whose "
        "error is within a quarter of the best."
    ),
    tune=tune_star_baseline,
)
