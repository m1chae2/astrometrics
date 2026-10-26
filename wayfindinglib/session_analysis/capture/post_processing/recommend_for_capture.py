"""Purpose: Turn a night's capture measurements into statements.

Description: Reads the pre-processing verdicts and the processing
measurements and says what, if anything, to change. Every statement is a
`TargetStatement` built from its row of the session rule table
(`wayfindinglib.analytics.result_quality.session_rules`). Every statement
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
7. If nothing above applied, there is no statement. No warning means
   the night is within limits.

Saturation advice covers spectroscopy only. In a deep imaging field the
brightest stars clip at any useful exposure, and avoiding that would make the
exposure too short for the faint target, so clipping is reported for imaging
frames but never recommended against.

Statements that rest on stored measurements cite them in `evidence`, as
references to the exact rows. Counts and values that are not rows go in
`values`. Confidence is the register status of the capture rules (WAY-46).
"""

from astrometricslib import DEFAULT_DARK_TEMPERATURE_TOLERANCE_C, SATURATED_BLOB_MINIMUM_PIXELS
from wayfindinglib.analytics.performance_envelope import MINIMUM_FRAMES_PER_NIGHT
from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
from wayfindinglib.models.quality.statements import TargetStatement
from wayfindinglib.models.session.capture_quality import (
    CaptureInputQuality,
    CapturePerformance,
    ExposureClipping,
)
from wayfindinglib.session_analysis.rule_support import (
    design_limit,
    envelope_limits,
    rule_confidence,
    session_statement,
    sort_by_severity,
)


def _insufficient_frames(input_quality: CaptureInputQuality) -> TargetStatement:
    """Say that the night has too few frames to judge.

    Returns
    -------
    statement : `TargetStatement`
        An information-level statement of the shortfall.
    """
    return session_statement(
        "capture_insufficient_data",
        message=(
            f"Only {input_quality.light_frames} light frames from this equipment exist for this "
            f"night; at least {MINIMUM_FRAMES_PER_NIGHT} are needed to judge it."
        ),
        reason=(
            "Too few light frames from this equipment. Sync the night's frames into the library, "
            "or image longer, then run the analysis again."
        ),
        values={"light_frames": float(input_quality.light_frames)},
        plain_message=(
            f"Only {input_quality.light_frames} light frames from this equipment exist for this night, "
            f"and we need at least {MINIMUM_FRAMES_PER_NIGHT} to judge it. "
            "Image longer or sync the frames, then check again."
        ),
        limits=[
            design_limit(
                float(MINIMUM_FRAMES_PER_NIGHT),
                "frames",
                "Fewest light frames a night needs for its medians to rest on more than a few frames.",
                "WAY-D45",
            )
        ],
        confidence=rule_confidence("capture"),
    )


def _captures_not_in_library(input_quality: CaptureInputQuality) -> TargetStatement | None:
    """Advise when Ekos finished exposures that have no frame in the library.

    Returns
    -------
    statement : `TargetStatement` or `None`
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
    return session_statement(
        "captures_not_in_library",
        message=(
            f"Ekos finished {missing} light exposures this night that have no frame in the library "
            f"({input_quality.light_frames} frames are there). Download or sort the missing frames, "
            f"or this analysis describes only part of the night.{caveat}"
        ),
        values={
            "captures_without_frame": float(missing),
            "unclassified_captures_without_frame": float(unknown_kind),
            "ekos_light_captures": float(input_quality.ekos_light_captures or 0),
            "library_frames": float(input_quality.light_frames),
        },
        limits=[
            design_limit(
                input_quality.match_window_seconds,
                "s",
                "Farthest apart an Ekos exposure and a frame may start and still be the same exposure.",
                "WAY-D47",
            )
        ],
        confidence=rule_confidence("capture"),
    )


def _missing_measurements(input_quality: CaptureInputQuality) -> TargetStatement | None:
    """Advise when frames lack measurements later steps need.

    Returns
    -------
    statement : `TargetStatement` or `None`
        Advice naming each missing measurement, or `None` if none is missing.
    """
    if not input_quality.has_missing_measurements:
        return None
    described = ", ".join(
        f"{name} ({count} frames)"
        for name, count in sorted(input_quality.frames_missing_measurements.items())
    )
    return session_statement(
        "frames_missing_measurements",
        message=(
            f"Some frames lack measurements that later steps use: {described}. Re-scan the frames, "
            "and check that the camera writes these fields into the FITS header."
        ),
        values={
            **{name: float(count) for name, count in input_quality.frames_missing_measurements.items()},
            "light_frames": float(input_quality.light_frames),
        },
        confidence=rule_confidence("capture"),
    )


def _frequent_aborts(
    input_quality: CaptureInputQuality, envelope: PerformanceEnvelope | None
) -> TargetStatement | None:
    """Advise when an unusual share of exposures was cancelled.

    Returns
    -------
    statement : `TargetStatement` or `None`
        Advice, or `None` if the share was not above the limit.
    """
    if not input_quality.has_frequent_aborts:
        return None
    return session_statement(
        "frequent_aborted_captures",
        message=(
            f"{input_quality.aborted_captures} exposures were cancelled "
            f"({input_quality.abort_fraction:.0%} of those started), more than 90% of this equipment's "
            "earlier nights. Ekos cancels an exposure when the guider loses its star, focusing "
            "starts, or the operator stops the sequence; check the session log for the cause."
        ),
        values={
            "aborted_captures": float(input_quality.aborted_captures or 0),
            "abort_fraction": input_quality.abort_fraction,
        },
        limits=envelope_limits(
            envelope,
            ("capture_abort_fraction_high_limit",),
            applies=input_quality.limits_equipment_match != "none",
        ),
        confidence=rule_confidence("capture", degraded=True),
    )


def _temperature_spread(input_quality: CaptureInputQuality) -> TargetStatement | None:
    """Note when one set of dark frames cannot suit every frame of the night.

    Returns
    -------
    statement : `TargetStatement` or `None`
        An information-level note, or `None` if every frame is within the
        dark-temperature tolerance of the night's median.
    """
    if not input_quality.frames_outside_dark_tolerance:
        return None
    return session_statement(
        "sensor_temperature_drift",
        message=(
            f"{input_quality.frames_outside_dark_tolerance} frames were taken more than "
            f"{input_quality.dark_temperature_tolerance_c:g} C from the night's median sensor "
            f"temperature (the spread was {input_quality.sensor_temperature_spread_c:.1f} C). A dark "
            "frame calibrates a light well only within that range, so take darks at several "
            "temperatures, or let the sensor settle before imaging."
        ),
        values={
            "frames_outside_tolerance": float(input_quality.frames_outside_dark_tolerance),
            "temperature_spread_c": input_quality.sensor_temperature_spread_c,
        },
        limits=[
            design_limit(
                input_quality.dark_temperature_tolerance_c,
                "degC",
                "How far a dark frame's temperature may differ from a light frame's and still calibrate it "
                f"(the library default is {DEFAULT_DARK_TEMPERATURE_TOLERANCE_C:g}).",
                "WAY-D47",
            )
        ],
        confidence=rule_confidence("capture"),
    )


def _format_exposure(seconds: float) -> str:
    """Write an exposure length without trailing zeros.

    Returns
    -------
    text : `str`
        For example ``"0.25 s"`` or ``"30 s"``.
    """
    return f"{seconds:.4g} s"


def _spectral_star_clipped(performance: CapturePerformance) -> list[TargetStatement]:
    """Advise on each spectroscopy target whose star clipped.

    For a target, the exposure lengths that clipped are compared with the
    ones that did not. The advice depends on which exist: if every length
    clipped, go shorter than the shortest tried; if some did not, name the
    longest that stayed below the ceiling and how many frames were spent on
    lengths that clipped.

    Returns
    -------
    statements : `list` [`TargetStatement`]
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
        if unclipped:
            longest_clear = max(group.exposure_seconds for group in unclipped)
            plain_message = None
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
            plain_message = (
                f"{target_id}: the star was too bright at every exposure length tried, even the shortest "
                f"({_format_exposure(shortest)}). If that star is the target, try a shorter exposure."
            )
        recommendations.append(
            session_statement(
                "spectral_star_clipped",
                message=message,
                plain_message=plain_message,
                evidence=[reference for group in clipped for reference in group.evidence],
                values={
                    "target": target_id,
                    "exposure_lengths_clipped": float(len(clipped)),
                    "exposure_lengths_tried": float(len(groups)),
                    "frames_at_clipped_lengths": float(frames_clipped),
                    "frames_total": float(frames_total),
                },
                limits=[
                    design_limit(
                        float(SATURATED_BLOB_MINIMUM_PIXELS),
                        "px",
                        "Fewest saturated pixels that make a frame's star count as clipped; an exposure "
                        "length clips when at least half its frames do.",
                        "WAY-D48",
                    )
                ],
                confidence=rule_confidence("capture"),
            )
        )
    return recommendations


def _star_shape(
    input_quality: CaptureInputQuality, performance: CapturePerformance, envelope: PerformanceEnvelope | None
) -> list[TargetStatement]:
    """Compare the night's star width and roundness with earlier nights.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        A warning for each of width and roundness that is outside the limit,
        or a note that no limit exists yet if the night has measurements but
        the equipment has too little history.
    """
    quality = performance.star_quality
    applies = input_quality.limits_equipment_match != "none"
    recommendations = []
    if quality.median_star_width_arcsec is None:
        return recommendations
    if input_quality.limits_equipment_match == "none" or (
        quality.star_width_limit_arcsec is None and quality.roundness_limit is None
    ):
        recommendations.append(
            session_statement(
                "capture_insufficient_data",
                message=(
                    f"The median star width was {quality.median_star_width_arcsec:.1f} arcsec over "
                    f"{quality.frames} frames, but this equipment has too few earlier nights to say "
                    "whether that is usual."
                ),
                reason=(
                    "This equipment has too few earlier nights to set a limit for star width. "
                    "Image on more nights with it, then the night can be compared."
                ),
                evidence=quality.width_evidence,
                values={"median_star_width_arcsec": quality.median_star_width_arcsec},
                plain_message=(
                    f"Stars were about {quality.median_star_width_arcsec:.1f} arcsec wide, but we have "
                    "too few earlier nights to say whether that is usual. "
                    "Image on more nights with this equipment."
                ),
                confidence=rule_confidence("capture"),
            )
        )
        return recommendations
    if (
        quality.star_width_limit_arcsec is not None
        and quality.median_star_width_arcsec > quality.star_width_limit_arcsec
    ):
        recommendations.append(
            session_statement(
                "star_width_above_baseline",
                message=(
                    f"Stars were wider than usual: a median of {quality.median_star_width_arcsec:.1f} "
                    f"arcsec over {quality.frames} frames, above the {quality.star_width_limit_arcsec:.1f} "
                    "arcsec that is unusual for this equipment. Poor seeing, focus that drifted, or "
                    "tracking error can each cause it; compare the autofocus record and the guiding "
                    "analysis for this night."
                ),
                evidence=quality.width_evidence,
                values={
                    "median_star_width_arcsec": quality.median_star_width_arcsec,
                    "frames": float(quality.frames),
                },
                limits=envelope_limits(envelope, ("night_star_width_high_limit",), applies),
                confidence=rule_confidence("capture", degraded=True),
            )
        )
    if (
        quality.median_roundness is not None
        and quality.roundness_limit is not None
        and quality.median_roundness < quality.roundness_limit
    ):
        recommendations.append(
            session_statement(
                "stars_elongated",
                message=(
                    f"Stars were more elongated than usual: a median roundness of "
                    f"{quality.median_roundness:.2f}, below the {quality.roundness_limit:.2f} that is "
                    "unusual for this equipment. Trailing from guiding error or polar misalignment "
                    "elongates stars in one direction; compare the guiding analysis for this night."
                ),
                evidence=quality.roundness_evidence,
                values={"median_roundness": quality.median_roundness},
                limits=envelope_limits(envelope, ("night_star_roundness_low_limit",), applies),
                confidence=rule_confidence("capture", degraded=True),
            )
        )
    return recommendations


def recommend_for_capture(
    input_quality: CaptureInputQuality,
    performance: CapturePerformance,
    envelope: PerformanceEnvelope | None = None,
) -> list[TargetStatement]:
    """Recommend what, if anything, to change after a night of capture.

    Parameters
    ----------
    input_quality : `CaptureInputQuality`
        The pre-processing result.
    performance : `CapturePerformance`
        The processing result.
    envelope : `PerformanceEnvelope` or `None`, optional
        The equipment-derived limits. The statements list the ones they
        were compared with; without it they list none.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        Warnings first, then advice, then information.
    """
    if not input_quality.has_enough_frames:
        return [_insufficient_frames(input_quality)]
    found = [
        _captures_not_in_library(input_quality),
        _missing_measurements(input_quality),
        _frequent_aborts(input_quality, envelope),
        _temperature_spread(input_quality),
        *_spectral_star_clipped(performance),
        *_star_shape(input_quality, performance, envelope),
    ]
    recommendations = [item for item in found if item is not None]
    return sort_by_severity(recommendations)
