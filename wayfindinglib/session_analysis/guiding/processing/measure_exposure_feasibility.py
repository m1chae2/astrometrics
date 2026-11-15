"""Purpose: Measure how often guiding stays clean for a whole exposure.

Description: A long exposure needs the guiding to hold for its whole length. A
single lost star, jump or stretch of large error inside it ruins the frame, and
a 5 minute frame has ten times the chance of catching one as a 30 second frame.
This stage asks, for each exposure length the equipment is used with, how often
the night's guiding would have stayed clean.

An exposure of length T that starts at time t is clean when, for t to t + T:

1. The star is not lost. There is no gap of more than four guide cycles
   between samples. A lost frame never becomes a sample, so a lost star
   shows as a gap.
2. There is no jump. No sample's error exceeds the excursion limit, which is
   the larger of five times the acceptable guiding error and one guide pixel.
3. There is no wobble. The scatter of the error about its own mean, per axis,
   does not exceed the acceptable guiding error. The mean is left out because
   a steady offset from the lock position moves the whole image and does not
   blur a star.

Start times are tried every quarter of the exposure length. A start time counts
only if the whole exposure would fit inside one guiding run. An exposure that
would run past the end of guiding, or start before it began, is a fault of the
session routine and not of the guider, so it is not counted. A star that is
lost while the run is still active shows as a gap inside the run and is
counted. The limits are the equipment's own (`guiding_rms_limit` and
`guide_excursion_limit`).

This is a model of the exposures, not a measurement of them. It does not see
the real frames, and it assumes the error that blurs a star is the error the
guider measured.
"""

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from astrometricslib.parameters import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    resolve_parameter,
)
from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.models.session.session_quality import ExposureFeasibility

LOST_STAR_CYCLES = TunedParameter(
    name="wayfinding.lost_star_cycles",
    default=4,
    unit="× guide cycle",
    description="A gap longer than this many guide cycles means the star was lost.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="WAY-D135",
    pending_conversion=True,
)
"""A gap longer than this many guide cycles means the star was lost.

Design estimate. One missed frame (two cycles between samples) happens in
normal guiding; three in a row is a recovery. Four leaves room for one more.
"""

RELIABLE_CLEAN_FRACTION = TunedParameter(
    name="wayfinding.reliable_clean_fraction",
    default=0.5,
    unit="fraction",
    description="The clean share at which a length counts as reliable: most exposures work.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="WAY-D46",
)
"""The clean share at which a length counts as reliable: most exposures work.

Design estimate. Below it, more exposures of that length are ruined than kept.
"""

_STRIDE_FRACTION = TunedParameter(
    name="wayfinding.stride_fraction",
    default=0.25,
    unit="× exposure length",
    description="Start times are tried every this fraction of the exposure length.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="WAY-D135",
    pending_conversion=True,
)
"""Start times are tried every this fraction of the exposure length."""

_MINIMUM_WINDOWS = TunedParameter(
    name="wayfinding.minimum_windows",
    default=10,
    unit="windows per exposure length",
    description="Fewest windows for a length to be reported.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="WAY-D135",
    pending_conversion=True,
)
"""Fewest windows for a length to be reported. Fewer would be a few exposures'
worth of night."""

_MAXIMUM_CADENCE_GAP_SECONDS = TunedParameter(
    name="wayfinding.measure_exposure_feasibility.maximum_cadence_gap_seconds",
    default=60.0,
    unit="s (scales with the guide cycle)",
    description="A gap longer than this between samples is a stop in guiding.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    register_entry="WAY-D126",
    pending_conversion=True,
)
"""A gap longer than this between samples is a stop in guiding.

It is excluded when working out the guide cycle, and it splits the samples into
runs when no runs are given.
"""


def _run_spans(times: np.ndarray, runs: Sequence[GuidingRunSummary]) -> list[tuple[float, float]]:
    """Find when guiding was running.

    Parameters
    ----------
    times : `numpy.ndarray`
        Sorted sample times.
    runs : `Sequence` [`GuidingRunSummary`]
        The night's guiding runs. A run that has no end time ends at its last
        sample.

    Returns
    -------
    spans : `list` [`tuple` [`float`, `float`]]
        Start and end of each stretch of guiding. Without runs, the samples are
        split wherever they pause for more than `_MAXIMUM_CADENCE_GAP_SECONDS`.
    """
    spans = []
    ordered = sorted(runs, key=lambda run: run.started_at)
    for index, run in enumerate(ordered):
        following = ordered[index + 1].started_at if index + 1 < len(ordered) else np.inf
        end = run.ended_at
        if end is None:
            inside = times[(times >= run.started_at) & (times < following)]
            end = float(inside[-1]) if inside.size else run.started_at
        spans.append((run.started_at, end))
    if spans:
        return spans
    breaks = np.flatnonzero(np.diff(times) >= resolve_parameter(_MAXIMUM_CADENCE_GAP_SECONDS).value)
    starts = np.concatenate([[0], breaks + 1])
    ends = np.concatenate([breaks, [times.size - 1]])
    return [(float(times[first]), float(times[last])) for first, last in zip(starts, ends, strict=True)]


def measure_exposure_feasibility(
    samples: Sequence[dict[str, Any]],
    exposure_lengths_seconds: Sequence[float],
    envelope: PerformanceEnvelope | None,
    limits_equipment_match: str,
    runs: Sequence[GuidingRunSummary] = (),
) -> tuple[list[ExposureFeasibility], float | None]:
    """Find how often guiding stays clean for each exposure length.

    Parameters
    ----------
    samples : `Sequence` [`dict`]
        The night's measured guide samples (``timestamp``, ``dra``, ``ddec``;
        errors in arcseconds).
    exposure_lengths_seconds : `Sequence` [`float`]
        The exposure lengths the equipment is used with.
    envelope : `PerformanceEnvelope` or `None`
        The equipment-derived limits.
    limits_equipment_match : `str`
        ``"exact"``, ``"guide_optics_only"`` or ``"none"``. With ``"none"``
        no limit applies and nothing is measured.
    runs : `Sequence` [`GuidingRunSummary`], optional
        The night's guiding runs, which say when guiding was running.

    Returns
    -------
    feasibility : `list` [`ExposureFeasibility`]
        One entry per exposure length with enough windows, shortest first.
    longest_reliable : `float` or `None`
        The longest length whose clean share reaches
        `RELIABLE_CLEAN_FRACTION`, or `None`.
    """
    if envelope is None or limits_equipment_match == "none" or len(samples) < 2:
        return [], None
    rms_limit = envelope.value("guiding_rms_limit")
    excursion_limit = envelope.value("guide_excursion_limit")
    if rms_limit is None or excursion_limit is None:
        return [], None

    ordered = sorted(samples, key=lambda sample: sample["timestamp"])
    times = np.array([sample["timestamp"] for sample in ordered])
    dra = np.array([sample["dra"] for sample in ordered])
    ddec = np.array([sample["ddec"] for sample in ordered])
    gaps = np.diff(times)
    short_gaps = gaps[gaps < resolve_parameter(_MAXIMUM_CADENCE_GAP_SECONDS).value]
    if short_gaps.size == 0:
        return [], None
    cadence = float(np.median(short_gaps))
    jump = np.hypot(dra, ddec) > excursion_limit

    spans = _run_spans(times, runs)
    results = []
    for length in sorted({float(value) for value in exposure_lengths_seconds if value > 0}):
        counts = {"windows": 0, "clean": 0, "lost": 0, "jump": 0, "wobble": 0}
        for span_start, span_end in spans:
            for start in np.arange(
                span_start, span_end - length + 1e-6, length * resolve_parameter(_STRIDE_FRACTION).value
            ):
                first, last = np.searchsorted(times, [start, start + length])
                window_times = np.concatenate([[start], times[first:last], [start + length]])
                lost = bool(np.diff(window_times).max() > resolve_parameter(LOST_STAR_CYCLES).value * cadence)
                jumped = bool(jump[first:last].any())
                wobble = (
                    last - first >= 2
                    and math.sqrt((float(dra[first:last].var()) + float(ddec[first:last].var())) / 2.0)
                    > rms_limit
                )
                counts["windows"] += 1
                counts["lost"] += lost
                counts["jump"] += jumped
                counts["wobble"] += wobble
                counts["clean"] += not (lost or jumped or wobble)
        if counts["windows"] < resolve_parameter(_MINIMUM_WINDOWS).value:
            continue
        total = counts["windows"]
        results.append(
            ExposureFeasibility(
                exposure_seconds=length,
                windows=total,
                clean_fraction=counts["clean"] / total,
                lost_fraction=counts["lost"] / total,
                jump_fraction=counts["jump"] / total,
                wobble_fraction=counts["wobble"] / total,
            )
        )
    reliable = [
        result.exposure_seconds
        for result in results
        if result.clean_fraction is not None
        and result.clean_fraction >= resolve_parameter(RELIABLE_CLEAN_FRACTION).value
    ]
    return results, max(reliable) if reliable else None
