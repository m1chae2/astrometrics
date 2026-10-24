"""Purpose: Measure what a night's frames show.

Description: Works out three things from the night's light frames.

1. Clipping. For each target and exposure length, whether a star clips at
   the camera's ceiling. The frames' stored saturated-pixel measurements
   decide, by the science library's own rule: a star clips when at least 4
   connected pixels sit at the ceiling, and an exposure length clips when at
   least half its frames do. The count here is of saturated pixels, not of
   connected blobs, which agreed with the stacking summary's verdict for 67
   of the 69 exposure groups where both existed (measured before the stacking
   summary's verdict was dropped as an input). The two that differed are Moon
   frames, where an extended bright disc, not a star, reaches the ceiling.
2. Star quality. The median star width and roundness of the imaging frames,
   compared with the limits from this equipment's earlier nights.
3. Efficiency. How much of the night's span went to exposing.

Every step is a pure function of the frames and limits passed in.
"""

import statistics
from collections import defaultdict
from collections.abc import Sequence

from astrometricslib import SATURATED_BLOB_MINIMUM_PIXELS, SATURATED_FRAME_FRACTION, MeasurementReference
from wayfindinglib.analytics.capture_history import (
    SATURATED_FRACTION_METRIC,
    STAR_ELONGATION_METRIC,
    STAR_WIDTH_METRIC,
    usable_star_frames,
)
from wayfindinglib.analytics.performance_envelope import MINIMUM_FRAMES_PER_NIGHT
from wayfindinglib.analytics.result_quality.evidence_order import distance_beyond_limit
from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
from wayfindinglib.models.session.capture_frame import CaptureFrame
from wayfindinglib.models.session.capture_quality import (
    CaptureEfficiency,
    CapturePerformance,
    ExposureClipping,
    StarQuality,
)

_EXPOSURE_DECIMALS = 4
"""Exposure lengths are rounded to this many decimals before grouping, so
0.010000000000000002 and 0.01 fall in one group."""


def _limit(envelope: PerformanceEnvelope | None, name: str, limits_equipment_match: str) -> float | None:
    """Read one limit, or `None` if none applies to this night's equipment.

    Returns
    -------
    limit : `float` or `None`
        The limit's value, or `None` if the night's equipment does not match
        the equipment the limits were worked out for, or the limit could not
        be worked out.
    """
    if envelope is None or limits_equipment_match == "none":
        return None
    return envelope.value(name)


def _references(frames: Sequence[CaptureFrame], metric: str) -> list[MeasurementReference]:
    """Point at the stored rows of one metric for these frames.

    Returns
    -------
    references : `list` [`MeasurementReference`]
        One per frame that has a stored row of the metric.
    """
    return [reference for frame in frames if (reference := frame.reference(metric)) is not None]


def _most_extreme_first(
    frames: Sequence[CaptureFrame],
    values: dict[str, float],
    limit: float | None,
    *,
    higher_is_beyond: bool,
) -> list[CaptureFrame]:
    """Sort frames so the one furthest beyond a limit comes first.

    Parameters
    ----------
    frames : `Sequence` [`CaptureFrame`]
        The frames to sort.
    values : `dict` [`str`, `float`]
        The value judged for each frame, by the frame's path.
    limit : `float` or `None`
        The night's limit for this value. With ``None`` the frames go newest
        first.
    higher_is_beyond : `bool`
        True when a value above the limit is the bad side (star width);
        False when a value below it is (roundness).

    Returns
    -------
    ordered : `list` [`CaptureFrame`]
        The frames, furthest beyond the limit first, ties newest first.
    """

    def key(frame: CaptureFrame) -> tuple[float, float]:
        """Give the sort key of one frame: distance beyond the limit, then age.

        Parameters
        ----------
        frame : `CaptureFrame`
            The frame.

        Returns
        -------
        key : `tuple` [`float`, `float`]
            Minus the distance beyond the limit, then minus the time stamp.
        """
        distance = (
            distance_beyond_limit(values[frame.path], limit, higher_is_beyond=higher_is_beyond)
            if limit is not None
            else 0.0
        )
        return (-distance, -frame.timestamp)

    return sorted(frames, key=key)


def measure_clipping(
    frames: Sequence[CaptureFrame], sensor_pixel_count: int | None
) -> list[ExposureClipping]:
    """Find, for each target and exposure length, whether a star clips.

    Parameters
    ----------
    frames : `Sequence` [`CaptureFrame`]
        The night's light frames.
    sensor_pixel_count : `int` or `None`
        Pixels on the camera's sensor with no binning. Without it a frame's
        saturated-pixel fraction cannot be turned into a count.

    Returns
    -------
    clipping : `list` [`ExposureClipping`]
        One entry per target, spectral or imaging, and exposure length, in
        that order. A group with no frame that has a stored saturated-pixel
        measurement is left out.
    """
    groups: dict[tuple[str, bool, float], list[CaptureFrame]] = defaultdict(list)
    for frame in frames:
        key = (frame.target_id, frame.is_spectral, round(frame.exposure_seconds, _EXPOSURE_DECIMALS))
        groups[key].append(frame)

    results = []
    for key, group in sorted(groups.items()):
        target_id, is_spectral, exposure_seconds = key
        measured = [frame for frame in group if frame.saturated_pixel_fraction is not None]
        counts = (
            [
                round(frame.saturated_pixel_fraction * sensor_pixel_count / frame.binning**2)
                for frame in measured
            ]
            if sensor_pixel_count
            else []
        )
        clipped_frames = sum(1 for count in counts if count >= SATURATED_BLOB_MINIMUM_PIXELS)
        if not counts:
            continue
        clipped_fraction = clipped_frames / len(counts) if counts else 0.0
        results.append(
            ExposureClipping(
                target_id=target_id,
                is_spectral=is_spectral,
                exposure_seconds=exposure_seconds,
                frames=len(counts),
                clipped_frames=clipped_frames,
                clipped_fraction=clipped_fraction,
                is_clipped=clipped_fraction >= SATURATED_FRAME_FRACTION,
                evidence=_references(measured, SATURATED_FRACTION_METRIC),
            )
        )
    return results


def measure_star_quality(
    frames: Sequence[CaptureFrame],
    envelope: PerformanceEnvelope | None,
    limits_equipment_match: str,
) -> StarQuality:
    """Measure how sharp and round the night's stars were.

    Parameters
    ----------
    frames : `Sequence` [`CaptureFrame`]
        The night's light frames.
    envelope : `PerformanceEnvelope` or `None`
        The equipment-derived limits. They give the shortest exposure long
        enough for guiding error to show in it (a few guide cycles of this
        equipment's guider). Until the equipment has guided, no frame
        qualifies.
    limits_equipment_match : `str`
        How well the night's equipment matches the one the limits are for.

    Returns
    -------
    star_quality : `StarQuality`
        The medians and the limits they are judged against. The medians are
        `None` when fewer than `MINIMUM_FRAMES_PER_NIGHT` frames qualify.
    """
    minimum_exposure_seconds = _limit(envelope, "minimum_star_measurement_exposure", limits_equipment_match)
    quality = StarQuality(
        minimum_exposure_seconds=minimum_exposure_seconds,
        star_width_limit_arcsec=_limit(envelope, "night_star_width_high_limit", limits_equipment_match),
        roundness_limit=_limit(envelope, "night_star_roundness_low_limit", limits_equipment_match),
    )
    if minimum_exposure_seconds is None:
        return quality
    usable = usable_star_frames(frames, minimum_exposure_seconds)
    quality.frames = len(usable)
    if len(usable) < MINIMUM_FRAMES_PER_NIGHT:
        return quality
    quality.median_star_width_arcsec = statistics.median(frame.star_width_arcsec for frame in usable)
    widths = {frame.path: frame.star_width_arcsec for frame in usable}
    quality.width_evidence = _references(
        _most_extreme_first(usable, widths, quality.star_width_limit_arcsec, higher_is_beyond=True),
        STAR_WIDTH_METRIC,
    )
    with_roundness = [frame for frame in usable if frame.roundness is not None]
    if len(with_roundness) >= MINIMUM_FRAMES_PER_NIGHT:
        quality.median_roundness = statistics.median(frame.roundness for frame in with_roundness)
        roundness_values = {frame.path: frame.roundness for frame in with_roundness}
        quality.roundness_evidence = _references(
            _most_extreme_first(
                with_roundness, roundness_values, quality.roundness_limit, higher_is_beyond=False
            ),
            STAR_ELONGATION_METRIC,
        )
    return quality


def measure_efficiency(frames: Sequence[CaptureFrame]) -> CaptureEfficiency:
    """Measure how much of the night's span went to exposing.

    Returns
    -------
    efficiency : `CaptureEfficiency`
        Total exposure time, the span from first start to last end, and their
        ratio. The ratio is `None` for fewer than two frames.
    """
    if not frames:
        return CaptureEfficiency()
    light_seconds = sum(frame.exposure_seconds for frame in frames)
    span = max(frame.timestamp + frame.exposure_seconds for frame in frames) - min(
        frame.timestamp for frame in frames
    )
    return CaptureEfficiency(
        light_exposure_seconds=light_seconds,
        span_seconds=span,
        duty_cycle=light_seconds / span if len(frames) > 1 and span > 0 else None,
    )


def measure_capture_performance(
    frames: Sequence[CaptureFrame],
    sensor_pixel_count: int | None,
    envelope: PerformanceEnvelope | None,
    limits_equipment_match: str,
) -> CapturePerformance:
    """Measure what the night's frames show.

    Returns
    -------
    performance : `CapturePerformance`
        Clipping, star quality and efficiency.
    """
    return CapturePerformance(
        clipping=measure_clipping(frames, sensor_pixel_count),
        star_quality=measure_star_quality(frames, envelope, limits_equipment_match),
        efficiency=measure_efficiency(frames),
    )
