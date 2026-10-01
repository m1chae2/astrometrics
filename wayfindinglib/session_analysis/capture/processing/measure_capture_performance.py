"""Purpose: Measure what a night's frames show.

Description: Works out three things from the night's light frames.

1. Clipping. For each target and exposure length, whether a star clips at
   the camera's ceiling. The science library has already judged this on the
   pixels when it stacked the target, so its verdict is used whenever it has
   one. Otherwise the frames' saturated-pixel counts decide, by the science
   library's own rule: a star clips when at least 4 connected pixels sit at
   the ceiling, and an exposure length clips when at least half its frames do.
   The count here is of saturated pixels, not of connected blobs, which
   agreed with the science library's verdict for 67 of the 69 exposure groups
   where both exist. The two that differed are Moon frames, where an extended
   bright disc, not a star, reaches the ceiling.
2. Star quality. The median star width and roundness of the imaging frames,
   compared with the limits from this equipment's earlier nights.
3. Efficiency. How much of the night's span went to exposing.

Every step is a pure function of the frames and limits passed in.
"""

import statistics
from collections import defaultdict
from collections.abc import Sequence

from astrometricslib import SATURATED_BLOB_MINIMUM_PIXELS, SATURATED_FRAME_FRACTION
from wayfindinglib.analytics.performance_envelope import MINIMUM_FRAMES_PER_NIGHT
from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
from wayfindinglib.models.session.capture_frame import CaptureFrame, StackSaturationVerdict
from wayfindinglib.models.session.capture_quality import (
    CaptureEfficiency,
    CapturePerformance,
    ExposureClipping,
    StarQuality,
)
from wayfindinglib.tasks.control_tasks.capture_analysis_tasks import usable_star_frames

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


def measure_clipping(
    frames: Sequence[CaptureFrame],
    verdicts: Sequence[StackSaturationVerdict],
    sensor_pixel_count: int | None,
) -> list[ExposureClipping]:
    """Find, for each target and exposure length, whether a star clips.

    Parameters
    ----------
    frames : `Sequence` [`CaptureFrame`]
        The night's light frames.
    verdicts : `Sequence` [`StackSaturationVerdict`]
        The science library's verdicts for stacked exposures.
    sensor_pixel_count : `int` or `None`
        Pixels on the camera's sensor with no binning. Without it a frame's
        saturated-pixel fraction cannot be turned into a count.

    Returns
    -------
    clipping : `list` [`ExposureClipping`]
        One entry per target, spectral or imaging, and exposure length, in
        that order. A group with no frame that has a measured clipping value
        and no science verdict is left out.
    """
    science = {
        (verdict.target_id, verdict.is_spectral, round(verdict.exposure_seconds, _EXPOSURE_DECIMALS)): verdict
        for verdict in verdicts
    }
    groups: dict[tuple[str, bool, float], list[CaptureFrame]] = defaultdict(list)
    for frame in frames:
        key = (frame.target_id, frame.is_spectral, round(frame.exposure_seconds, _EXPOSURE_DECIMALS))
        groups[key].append(frame)

    results = []
    for key, group in sorted(groups.items()):
        target_id, is_spectral, exposure_seconds = key
        counts = (
            [
                round(frame.saturated_pixel_fraction * sensor_pixel_count / frame.binning**2)
                for frame in group
                if frame.saturated_pixel_fraction is not None
            ]
            if sensor_pixel_count
            else []
        )
        clipped_frames = sum(1 for count in counts if count >= SATURATED_BLOB_MINIMUM_PIXELS)
        verdict = science.get(key)
        if not counts and verdict is None:
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
                is_clipped=verdict.saturated if verdict else clipped_fraction >= SATURATED_FRAME_FRACTION,
                basis="science_stack" if verdict else "frame_pixel_count",
                science_recommended_exposure_seconds=verdict.recommended_exposure_seconds
                if verdict
                else None,
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
    roundness = [frame.roundness for frame in usable if frame.roundness is not None]
    if len(roundness) >= MINIMUM_FRAMES_PER_NIGHT:
        quality.median_roundness = statistics.median(roundness)
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
    verdicts: Sequence[StackSaturationVerdict],
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
        clipping=measure_clipping(frames, verdicts, sensor_pixel_count),
        star_quality=measure_star_quality(frames, envelope, limits_equipment_match),
        efficiency=measure_efficiency(frames),
    )
