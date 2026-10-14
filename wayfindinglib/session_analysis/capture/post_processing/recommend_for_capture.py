"""Purpose: Turn a night's capture measurements into recommendations.

Description: Reads the pre-processing verdicts and the processing
measurements and says what, if anything, to change. Every recommendation
carries the evidence it rests on and the equipment-derived limit it was
compared with, so a reader can check it, and a confidence that says how far
the evidence alone supports it.

Rules applied, in order:

1. Too few frames: say so and stop. Nothing else can be judged.
2. Exposures Ekos finished that are not in the library.
3. Frames missing measurements, and frequent cancelled exposures.
4. Sensor temperatures too far apart for one set of dark frames.
5. A spectroscopy star that clips at the exposure lengths used.
6. Star width and roundness against this equipment's earlier nights.
7. If nothing above applied, a statement that the night was within limits.

Saturation advice covers spectroscopy only. In a deep imaging field the
brightest stars clip at any useful exposure, and avoiding that would make the
exposure too short for the faint target, so clipping is reported for imaging
frames but never recommended against.
"""

from wayfindinglib.analytics.performance_envelope import MINIMUM_FRAMES_PER_NIGHT
from wayfindinglib.models.session.capture_quality import (
    CaptureInputQuality,
    CapturePerformance,
    ExposureClipping,
)
from wayfindinglib.models.session.session_quality import (
    Recommendation,
    RecommendationKind,
    RecommendationSeverity,
)

_SEVERITY_ORDER = {
    RecommendationSeverity.WARNING: 0,
    RecommendationSeverity.ADVICE: 1,
    RecommendationSeverity.INFO: 2,
}


def _insufficient_frames(input_quality: CaptureInputQuality) -> Recommendation:
    """Say that the night has too few frames to judge.

    Returns
    -------
    recommendation : `Recommendation`
        An information-level statement of the shortfall.
    """
    return Recommendation(
        kind=RecommendationKind.INSUFFICIENT_DATA,
        severity=RecommendationSeverity.INFO,
        message=(
            f"Only {input_quality.light_frames} light frames from this equipment exist for this "
            f"night; at least {MINIMUM_FRAMES_PER_NIGHT} are needed to judge it."
        ),
        evidence={"light_frames": float(input_quality.light_frames)},
        compared_to={"minimum_frames": float(MINIMUM_FRAMES_PER_NIGHT)},
        confidence="high",
    )


def _captures_not_in_library(input_quality: CaptureInputQuality) -> Recommendation | None:
    """Advise when Ekos finished exposures that have no frame in the library.

    Returns
    -------
    recommendation : `Recommendation` or `None`
        Advice to download the missing frames, or `None` if every exposure
        has a frame or there is no Ekos record to compare with.
    """
    missing = input_quality.captures_without_frame
    if not missing:
        return None
    unknown_kind = input_quality.captures_without_frame_of_unknown_kind or 0
    caveat = (
        f" A further {unknown_kind} exposures have no saved path in the Ekos log and are not counted."
        if unknown_kind
        else ""
    )
    return Recommendation(
        kind=RecommendationKind.CAPTURES_NOT_IN_LIBRARY,
        severity=RecommendationSeverity.ADVICE,
        message=(
            f"Ekos finished {missing} light exposures this night that have no frame in the library "
            f"({input_quality.light_frames} frames are there). Download or sort the missing frames, "
            f"or this analysis describes only part of the night.{caveat}"
        ),
        evidence={
            "captures_without_frame": float(missing),
            "unclassified_captures_without_frame": float(unknown_kind),
            "ekos_light_captures": float(input_quality.ekos_light_captures or 0),
            "library_frames": float(input_quality.light_frames),
        },
        compared_to={"match_window_seconds": input_quality.match_window_seconds},
        confidence="high",
    )


def _missing_measurements(input_quality: CaptureInputQuality) -> Recommendation | None:
    """Advise when frames lack measurements later steps need.

    Returns
    -------
    recommendation : `Recommendation` or `None`
        Advice naming each missing measurement, or `None` if none is missing.
    """
    if not input_quality.has_missing_measurements:
        return None
    described = ", ".join(
        f"{name} ({count} frames)"
        for name, count in sorted(input_quality.frames_missing_measurements.items())
    )
    return Recommendation(
        kind=RecommendationKind.FRAMES_MISSING_MEASUREMENTS,
        severity=RecommendationSeverity.ADVICE,
        message=(
            f"Some frames lack measurements that later steps use: {described}. Re-scan the frames, "
            "and check that the camera writes these fields into the FITS header."
        ),
        evidence={name: float(count) for name, count in input_quality.frames_missing_measurements.items()},
        compared_to={"light_frames": float(input_quality.light_frames)},
        confidence="high",
    )


def _frequent_aborts(input_quality: CaptureInputQuality) -> Recommendation | None:
    """Advise when an unusual share of exposures was cancelled.

    Returns
    -------
    recommendation : `Recommendation` or `None`
        Advice, or `None` if the share was not above the limit.
    """
    if not input_quality.has_frequent_aborts:
        return None
    return Recommendation(
        kind=RecommendationKind.FREQUENT_ABORTED_CAPTURES,
        severity=RecommendationSeverity.ADVICE,
        message=(
            f"{input_quality.aborted_captures} exposures were cancelled "
            f"({input_quality.abort_fraction:.0%} of those started), more than 90% of this equipment's "
            "earlier nights. Ekos cancels an exposure when the guider loses its star, focusing "
            "starts, or the operator stops the sequence; check the session log for the cause."
        ),
        evidence={
            "aborted_captures": float(input_quality.aborted_captures or 0),
            "abort_fraction": input_quality.abort_fraction,
        },
        compared_to={"abort_fraction_limit": input_quality.abort_fraction_limit},
        confidence="medium",
    )


def _temperature_spread(input_quality: CaptureInputQuality) -> Recommendation | None:
    """Note when one set of dark frames cannot suit every frame of the night.

    Returns
    -------
    recommendation : `Recommendation` or `None`
        An information-level note, or `None` if every frame is within the
        dark-temperature tolerance of the night's median.
    """
    if not input_quality.frames_outside_dark_tolerance:
        return None
    return Recommendation(
        kind=RecommendationKind.SENSOR_TEMPERATURE_DRIFT,
        severity=RecommendationSeverity.INFO,
        message=(
            f"{input_quality.frames_outside_dark_tolerance} frames were taken more than "
            f"{input_quality.dark_temperature_tolerance_c:g} C from the night's median sensor "
            f"temperature (the spread was {input_quality.sensor_temperature_spread_c:.1f} C). A dark "
            "frame calibrates a light well only within that range, so take darks at several "
            "temperatures, or let the sensor settle before imaging."
        ),
        evidence={
            "frames_outside_tolerance": float(input_quality.frames_outside_dark_tolerance),
            "temperature_spread_c": input_quality.sensor_temperature_spread_c,
        },
        compared_to={"dark_temperature_tolerance_c": input_quality.dark_temperature_tolerance_c},
        confidence="high",
    )


def _format_exposure(seconds: float) -> str:
    """Write an exposure length without trailing zeros.

    Returns
    -------
    text : `str`
        For example ``"0.25 s"`` or ``"30 s"``.
    """
    return f"{seconds:.4g} s"


def _spectral_star_clipped(performance: CapturePerformance) -> list[Recommendation]:
    """Advise on each spectroscopy target whose star clipped.

    For a target, the exposure lengths that clipped are compared with the
    ones that did not. The advice depends on which exist: if every length
    clipped, go shorter than the shortest tried; if some did not, name the
    longest that stayed below the ceiling and how many frames were spent on
    lengths that clipped.

    Returns
    -------
    recommendations : `list` [`Recommendation`]
        One per target with at least one clipped exposure length.
    """
    by_target: dict[str, list[ExposureClipping]] = {}
    for group in performance.clipping:
        if group.is_spectral:
            by_target.setdefault(group.target_id, []).append(group)
    recommendations = []
    for target_id, groups in sorted(by_target.items()):
        clipped = [group for group in groups if group.is_clipped]
        if not clipped:
            continue
        unclipped = [group for group in groups if not group.is_clipped]
        frames_total = sum(group.frames for group in groups)
        frames_clipped = sum(group.frames for group in clipped)
        science_recommended = next(
            (
                g.science_recommended_exposure_seconds
                for g in groups
                if g.science_recommended_exposure_seconds
            ),
            None,
        )
        rests_on_science = all(group.basis == "science_stack" for group in clipped)
        if unclipped:
            longest_clear = max(group.exposure_seconds for group in unclipped)
            message = (
                f"{target_id}: a star clipped at {len(clipped)} of {len(groups)} exposure lengths, "
                f"{frames_clipped} of {frames_total} frames. The longest length that stayed below the "
                f"ceiling was {_format_exposure(longest_clear)}."
            )
        else:
            shortest = min(group.exposure_seconds for group in groups)
            message = (
                f"{target_id}: a star clipped at every exposure length tried, the shortest being "
                f"{_format_exposure(shortest)}. If that star is the target, try a shorter exposure."
            )
        if science_recommended:
            message += (
                f" The stacking analysis estimates that {_format_exposure(science_recommended)} keeps "
                "the brightest star below the ceiling."
            )
        recommendations.append(
            Recommendation(
                kind=RecommendationKind.SPECTRAL_STAR_CLIPPED,
                severity=RecommendationSeverity.ADVICE,
                message=message,
                evidence={
                    "target": target_id,
                    "exposure_lengths_clipped": float(len(clipped)),
                    "exposure_lengths_tried": float(len(groups)),
                    "frames_at_clipped_lengths": float(frames_clipped),
                    "frames_total": float(frames_total),
                },
                compared_to={
                    "science_recommended_exposure_seconds": science_recommended,
                    "verdict_basis": "science_stack" if rests_on_science else "frame_pixel_count",
                },
                confidence="high" if rests_on_science else "medium",
            )
        )
    return recommendations


def _star_shape(input_quality: CaptureInputQuality, performance: CapturePerformance) -> list[Recommendation]:
    """Compare the night's star width and roundness with earlier nights.

    Returns
    -------
    recommendations : `list` [`Recommendation`]
        A warning for each of width and roundness that is outside the limit,
        or a note that no limit exists yet if the night has measurements but
        the equipment has too little history.
    """
    quality = performance.star_quality
    recommendations = []
    if quality.median_star_width_arcsec is None:
        return recommendations
    if input_quality.limits_equipment_match == "none" or (
        quality.star_width_limit_arcsec is None and quality.roundness_limit is None
    ):
        recommendations.append(
            Recommendation(
                kind=RecommendationKind.INSUFFICIENT_DATA,
                severity=RecommendationSeverity.INFO,
                message=(
                    f"The median star width was {quality.median_star_width_arcsec:.1f} arcsec over "
                    f"{quality.frames} frames, but this equipment has too few earlier nights to say "
                    "whether that is usual."
                ),
                evidence={"median_star_width_arcsec": quality.median_star_width_arcsec},
                compared_to={},
                confidence="high",
            )
        )
        return recommendations
    if (
        quality.star_width_limit_arcsec is not None
        and quality.median_star_width_arcsec > quality.star_width_limit_arcsec
    ):
        recommendations.append(
            Recommendation(
                kind=RecommendationKind.STAR_WIDTH_ABOVE_BASELINE,
                severity=RecommendationSeverity.WARNING,
                message=(
                    f"Stars were wider than usual: a median of {quality.median_star_width_arcsec:.1f} "
                    f"arcsec over {quality.frames} frames, above the {quality.star_width_limit_arcsec:.1f} "
                    "arcsec that is unusual for this equipment. Poor seeing, focus that drifted, or "
                    "tracking error can each cause it; compare the autofocus record and the guiding "
                    "analysis for this night."
                ),
                evidence={
                    "median_star_width_arcsec": quality.median_star_width_arcsec,
                    "frames": float(quality.frames),
                },
                compared_to={"night_star_width_high_limit": quality.star_width_limit_arcsec},
                confidence="medium",
            )
        )
    if (
        quality.median_roundness is not None
        and quality.roundness_limit is not None
        and quality.median_roundness < quality.roundness_limit
    ):
        recommendations.append(
            Recommendation(
                kind=RecommendationKind.STARS_ELONGATED,
                severity=RecommendationSeverity.WARNING,
                message=(
                    f"Stars were more elongated than usual: a median roundness of "
                    f"{quality.median_roundness:.2f}, below the {quality.roundness_limit:.2f} that is "
                    "unusual for this equipment. Trailing from guiding error or polar misalignment "
                    "elongates stars in one direction; compare the guiding analysis for this night."
                ),
                evidence={"median_roundness": quality.median_roundness},
                compared_to={"night_star_roundness_low_limit": quality.roundness_limit},
                confidence="medium",
            )
        )
    return recommendations


def recommend_for_capture(
    input_quality: CaptureInputQuality, performance: CapturePerformance
) -> list[Recommendation]:
    """Recommend what, if anything, to change after a night of capture.

    Parameters
    ----------
    input_quality : `CaptureInputQuality`
        The pre-processing result.
    performance : `CapturePerformance`
        The processing result.

    Returns
    -------
    recommendations : `list` [`Recommendation`]
        Warnings first, then advice, then information.
    """
    if not input_quality.has_enough_frames:
        return [_insufficient_frames(input_quality)]
    found = [
        _captures_not_in_library(input_quality),
        _missing_measurements(input_quality),
        _frequent_aborts(input_quality),
        _temperature_spread(input_quality),
        *_spectral_star_clipped(performance),
        *_star_shape(input_quality, performance),
    ]
    recommendations = [item for item in found if item is not None]
    if not recommendations:
        recommendations.append(
            Recommendation(
                kind=RecommendationKind.CAPTURE_WITHIN_LIMITS,
                severity=RecommendationSeverity.INFO,
                message=(
                    f"All {input_quality.light_frames} light frames are in the library and none of the "
                    "checks found a problem."
                ),
                evidence={"light_frames": float(input_quality.light_frames)},
                compared_to={},
                confidence="medium",
            )
        )
    return sorted(recommendations, key=lambda recommendation: _SEVERITY_ORDER[recommendation.severity])
