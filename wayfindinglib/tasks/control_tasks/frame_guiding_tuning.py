"""Purpose: Derive how much worse than its group a frame must guide.

Description: The frame guiding review (`frame_guiding`) lists a frame whose
guide error is above a multiple of its group's median. The multiple should
leave ordinary frames alone. This module joins the exposure window of every
stored capture to the guide log, groups the frames of one target in one Ekos
session, divides each frame's root-mean-square guide error by the group's
median, and takes the ratio that 99 percent of frames stay under.

The statistic is a pure function of a list of frame errors.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

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
from wayfindinglib.analytics.result_quality.guiding_join import FrameWindow, join_frames_to_guiding
from wayfindinglib.analytics.tuning_support import butler_of, percentile, records_of
from wayfindinglib.session_analysis.live_status import _target_name_from_path
from wayfindinglib.tasks.control_tasks.frame_guiding import SLOW_FRAME_RATIO

__all__ = ["SLOW_FRAME_TUNER", "group_guide_ratios"]

SLOW_FRAME_COVERAGE_PERCENT = TunedParameter(
    name="wayfinding.slow_frame_coverage_percent",
    default=99.0,
    unit="percent",
    description="Share of frames whose guide error must stay under the slow-frame limit.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="WAY-D142",
)
"""Share of frames whose guide error must stay under the slow-frame limit.

A frame is a flag for a person to look at, so one ordinary frame in a hundred
may be listed. Register entry WAY-D142."""

MINIMUM_FRAMES_PER_GROUP = TunedParameter(
    name="wayfinding.minimum_frames_per_group",
    default=5,
    unit="frames",
    description="A group of frames needs at least this many to give a median to compare with.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="WAY-D142",
)
"""A group of frames needs at least this many to give a median to compare with.

With fewer, the median is one frame's own error and the ratio is about 1 by
construction. Register entry WAY-D142."""

MINIMUM_GUIDE_SAMPLES_PER_FRAME = TunedParameter(
    name="wayfinding.minimum_guide_samples_per_frame",
    default=3,
    unit="samples",
    description="A frame needs at least this many guide samples inside its exposure to give an error.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="WAY-D142",
)
"""A frame needs at least this many guide samples inside its exposure.

An error rests on its samples; with one or two it is a single reading, not a
root-mean-square. Register entry WAY-D142."""


def group_guide_ratios(frame_errors: Sequence[float], minimum_frames: int) -> list[float]:
    """Give each frame's guide error as a multiple of its group's median.

    Parameters
    ----------
    frame_errors : `~collections.abc.Sequence` [`float`]
        The guide error of each frame of one group.
    minimum_frames : `int`
        A group with fewer frames gives no ratios.

    Returns
    -------
    ratios : `list` [`float`]
        Each frame's error divided by the group median.
    """
    if len(frame_errors) < minimum_frames:
        return []
    median = statistics.median(frame_errors)
    if median <= 0.0:
        return []
    return [error / median for error in frame_errors]


def tune_slow_frame_ratio(inputs: TuningInputs) -> TuningResult:
    """Derive the slow-frame ratio from the guide error of stored frames.

    Parameters
    ----------
    inputs : `TuningInputs`
        The tuning run's inputs. Reads the ``"wayfinder"`` service.

    Returns
    -------
    result : `TuningResult`
        The ratio that 99 percent of frames stay under.

    Raises
    ------
    TuningSkippedError
        If no group has enough frames with guiding.
    """
    wayfinder = inputs.service("wayfinder")
    records = records_of(wayfinder)
    contexts = butler_of(wayfinder).get_all("ekos_session_context")
    minimum_frames = resolve_parameter(MINIMUM_FRAMES_PER_GROUP).value
    minimum_samples = resolve_parameter(MINIMUM_GUIDE_SAMPLES_PER_FRAME).value
    ratios: list[float] = []
    groups = 0
    for context in contexts:
        rows = records.get_guiding_samples(
            start_time=context.started_at,
            limit=500_000,
            sources=["ekos_guide_log"],
        )
        rows = [row for row in rows if row["timestamp"] <= context.ended_at]
        by_target: dict[str, list[FrameWindow]] = {}
        for capture in context.captures:
            by_target.setdefault(_target_name_from_path(capture.file_path), []).append(
                FrameWindow(
                    frame_id=capture.file_path,
                    start_epoch_seconds=capture.completed_at - capture.exposure_seconds,
                    exposure_seconds=capture.exposure_seconds,
                )
            )
        for windows in by_target.values():
            joined = join_frames_to_guiding(windows, rows)
            frame_errors = [
                row["rms_total_arcsec"]
                for row in joined
                if row.get("rms_total_arcsec") is not None and row.get("guide_samples", 0) >= minimum_samples
            ]
            group_ratios = group_guide_ratios(frame_errors, minimum_frames)
            if group_ratios:
                groups += 1
                ratios.extend(group_ratios)
    if not ratios:
        raise TuningSkippedError("no group of frames has guide samples to compare")
    percent = resolve_parameter(SLOW_FRAME_COVERAGE_PERCENT).value
    return TuningResult(
        values={SLOW_FRAME_RATIO.name: percentile(ratios, percent)},
        sample=len(ratios),
        source=(
            f"The {percent:g}th percentile of the guide error of {len(ratios)} frames as a multiple of "
            f"their group's median, in {groups} groups of frames of one target and session."
        ),
    )


SLOW_FRAME_TUNER = Tuner(
    name="wayfinding.live_status_slow_frame_ratio",
    parameters=(SLOW_FRAME_RATIO,),
    method=(
        "Joins the recorded captures to the guide log and takes the 99th percentile of each frame's guide "
        "error as a multiple of the median of its group of frames."
    ),
    tune=tune_slow_frame_ratio,
)
