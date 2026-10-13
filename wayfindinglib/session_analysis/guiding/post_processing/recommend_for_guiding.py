"""Purpose: Turn a night's guiding measurements into recommendations.

Description: Reads the pre-processing verdicts and the processing
measurements and says what, if anything, to change. Every recommendation
carries the evidence it rests on and the equipment-derived limit it was
compared with, so a reader can check it, and a confidence that says how far
the evidence alone supports it.

Rules applied, in order:

1. Too few samples: say so and stop. Nothing else can be judged.
2. Impossible calibrated mount speed: recalibrate the guider.
3. Weak guide star, many lost frames or frequent excursions: check the
   guide signal.
4. Guide optics in the log differ from the configuration: update the
   configuration.
5. Guiding error against the acceptable guiding error.
6. The longest exposure length in use against how often guiding stayed clean
   for a whole exposure of that length.
Confidence is never "high" when the limits come from equipment that only
partly matches the night's, or when the recommendation rests on a proxy.
"""

from wayfindinglib.analytics.performance_envelope import MINIMUM_SAMPLES_PER_SESSION
from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
from wayfindinglib.models.session.session_quality import (
    GuidingInputQuality,
    GuidingPerformance,
    Recommendation,
    RecommendationKind,
    RecommendationSeverity,
)
from wayfindinglib.session_analysis.guiding.processing.measure_exposure_feasibility import (
    RELIABLE_CLEAN_FRACTION,
)

_SEVERITY_ORDER = {
    RecommendationSeverity.WARNING: 0,
    RecommendationSeverity.ADVICE: 1,
    RecommendationSeverity.INFO: 2,
}


def _applicable_limit(
    envelope: PerformanceEnvelope | None, name: str, input_quality: GuidingInputQuality
) -> float | None:
    """Read one limit if it applies to this night's equipment.

    Returns
    -------
    limit : `float` or `None`
        The limit, or `None` if there is no envelope, the night's equipment
        does not match it, or the limit could not be worked out.
    """
    if envelope is None or input_quality.limits_equipment_match == "none":
        return None
    return envelope.value(name)


def _insufficient_data(input_quality: GuidingInputQuality) -> Recommendation:
    """Say that the night has too little data to judge.

    Returns
    -------
    recommendation : `Recommendation`
        An information-level statement of the shortfall.
    """
    return Recommendation(
        kind=RecommendationKind.INSUFFICIENT_DATA,
        severity=RecommendationSeverity.INFO,
        message=(
            f"Only {input_quality.samples_analyzed} measured guide samples exist for this night; "
            f"at least {MINIMUM_SAMPLES_PER_SESSION} are needed to judge it."
        ),
        evidence={"samples": float(input_quality.samples_analyzed)},
        compared_to={"minimum_samples": float(MINIMUM_SAMPLES_PER_SESSION)},
        confidence="high",
    )


def _recalibrate_guider(
    input_quality: GuidingInputQuality, envelope: PerformanceEnvelope | None
) -> Recommendation | None:
    """Warn when a calibrated mount speed is impossible.

    Returns
    -------
    recommendation : `Recommendation` or `None`
        A warning, or `None` if every calibrated speed was possible.
    """
    if not input_quality.calibration_problems:
        return None
    return Recommendation(
        kind=RecommendationKind.RECALIBRATE_GUIDER,
        severity=RecommendationSeverity.WARNING,
        message=(
            "The guider's calibration measured a mount speed that cannot be right, so its "
            "corrections were wrong. Recalibrate under a clear sky with a bright, "
            "well-focused guide star. " + " ".join(input_quality.calibration_problems)
        ),
        evidence={"problem_count": float(len(input_quality.calibration_problems))},
        compared_to={
            "max_credible_guide_speed": _applicable_limit(envelope, "max_credible_guide_speed", input_quality)
        },
        confidence="high",
    )


def _dim_star_text(input_quality: GuidingInputQuality) -> str:
    """Describe how much fainter the guide star was than usual.

    The text says how many times fainter the star was, and whether a shorter
    guide cycle could explain that. A guide exposure cut in half halves the
    light, so a cycle shorter by a factor explains at most that factor. A
    bigger drop has another cause.

    Returns
    -------
    text : `str`
        A clause for the warning message.
    """
    mass = input_quality.median_star_mass or 0.0
    typical = input_quality.typical_star_mass
    text = f"the guide star's brightness was low ({mass:,.0f} counts"
    if typical and mass > 0:
        text += f", {typical / mass:.0f} times fainter than this equipment's usual {typical:,.0f}"
    text += ")"
    cycle, usual_cycle = input_quality.cadence_seconds, input_quality.typical_cadence_seconds
    if typical and mass > 0 and cycle and usual_cycle:
        explained = max(usual_cycle / cycle, 1.0)
        text += (
            f". The guide cycle was {cycle:.1f} s against a usual {usual_cycle:.1f} s, so a shorter "
            f"guide exposure explains at most a factor of {explained:.1f} of that"
        )
    return text


def _check_guide_signal(input_quality: GuidingInputQuality) -> Recommendation | None:
    """Warn when the guide star was weak, often lost, or often jumped.

    Returns
    -------
    recommendation : `Recommendation` or `None`
        A warning that names which checks failed, or `None` if none did.
    """
    earlier_nights = "the level that 90% of earlier nights stayed under"
    failed = []
    if input_quality.has_low_signal:
        failed.append(
            f"the guide star's signal was low (SNR {input_quality.median_snr:.0f}, below the "
            f"{input_quality.snr_limit:.0f} that is unusual for this equipment)"
        )
    if input_quality.has_dim_star:
        failed.append(_dim_star_text(input_quality))
    if input_quality.has_high_loss:
        failed.append(
            f"the guider lost {input_quality.lost_fraction:.1%} of its frames (more than "
            f"{input_quality.lost_fraction_limit:.1%}, {earlier_nights})"
        )
    if input_quality.has_frequent_excursions:
        failed.append(
            f"{input_quality.excursion_fraction:.1%} of guide samples were excursions (more than "
            f"{input_quality.excursion_fraction_limit:.1%}, {earlier_nights})"
        )
    if not failed:
        return None
    return Recommendation(
        kind=RecommendationKind.CHECK_GUIDE_SIGNAL,
        severity=RecommendationSeverity.WARNING,
        message=(
            "The guiding data was gathered poorly: " + "; ".join(failed) + ". The logs cannot say why. "
            "The usual causes are a guide camera exposure, gain or binning set too low, poor guide "
            "focus, dew or an obstruction on the guide scope, haze or cloud, or a dim guide star. "
            "Guiding error numbers from this night describe a poor measurement, not the mount."
        ),
        evidence={
            "median_snr": input_quality.median_snr,
            "median_star_mass": input_quality.median_star_mass,
            "lost_fraction": input_quality.lost_fraction,
            "excursion_fraction": input_quality.excursion_fraction,
        },
        compared_to={
            "snr_limit": input_quality.snr_limit,
            "star_mass_limit": input_quality.star_mass_limit,
            "typical_star_mass": input_quality.typical_star_mass,
            "lost_fraction_limit": input_quality.lost_fraction_limit,
            "excursion_fraction_limit": input_quality.excursion_fraction_limit,
        },
        confidence="high" if input_quality.limits_equipment_match == "exact" else "medium",
    )


def _update_guide_optics(
    input_quality: GuidingInputQuality, logged_scale: float | None, configured_scale: float | None
) -> Recommendation | None:
    """Warn when the guide log's optics differ from the configuration.

    Returns
    -------
    recommendation : `Recommendation` or `None`
        A warning, or `None` if the optics agree or cannot be compared.
    """
    if input_quality.guide_scale_matches_configuration is not False:
        return None
    return Recommendation(
        kind=RecommendationKind.UPDATE_GUIDE_OPTICS_CONFIGURATION,
        severity=RecommendationSeverity.WARNING,
        message=(
            "The guide log's plate scale differs from the configured guide equipment. Update the "
            "configured guide scope or guide camera so the limits and calibration use the real optics."
        ),
        evidence={"logged_guide_scale_arcsec_per_px": logged_scale},
        compared_to={"configured_guide_scale_arcsec_per_px": configured_scale},
        confidence="high",
    )


def _confidence(input_quality: GuidingInputQuality, signal_problem: bool) -> str:
    """Decide how far a guiding-error conclusion can be trusted.

    Returns
    -------
    confidence : `str`
        ``"low"`` when the guide signal was poor (the error numbers describe
        a bad measurement), ``"medium"`` when the limits come from equipment
        that only partly matches the night's, otherwise ``"high"``.
    """
    if signal_problem:
        return "low"
    return "high" if input_quality.limits_equipment_match == "exact" else "medium"


def _guiding_against_limit(
    input_quality: GuidingInputQuality,
    performance: GuidingPerformance,
    envelope: PerformanceEnvelope | None,
    signal_problem: bool,
) -> Recommendation | None:
    """Compare the guiding error with the error this equipment can absorb.

    Returns
    -------
    recommendation : `Recommendation` or `None`
        Advice if the error is above the limit, otherwise an information
        statement. `None` if the error or the limit is unknown.
    """
    limit = _applicable_limit(envelope, "guiding_rms_limit", input_quality)
    error = performance.rms_per_axis_arcsec
    if error is None or limit is None:
        return None
    above = error > limit
    widening = performance.expected_star_widening_fraction
    widening_text = f" That widens a star by about {widening:.0%}." if widening is not None else ""
    if above:
        verdict = (
            f"above the {limit:.2f} arcsec this equipment can absorb.{widening_text} Look at mount "
            "balance, guide aggressiveness and seeing, and shorten exposures if stars are soft."
        )
        kind, severity = RecommendationKind.GUIDING_ABOVE_LIMIT, RecommendationSeverity.ADVICE
    else:
        verdict = f"within the {limit:.2f} arcsec this equipment can absorb.{widening_text}"
        kind, severity = RecommendationKind.GUIDING_WITHIN_LIMIT, RecommendationSeverity.INFO
    return Recommendation(
        kind=kind,
        severity=severity,
        message=f"Guiding error was {error:.2f} arcsec per axis, {verdict}",
        evidence={"rms_per_axis_arcsec": error, "expected_star_widening_fraction": widening},
        compared_to={"guiding_rms_limit": limit},
        confidence=_confidence(input_quality, signal_problem),
    )


def _exposure_length_limited(
    input_quality: GuidingInputQuality,
    performance: GuidingPerformance,
    envelope: PerformanceEnvelope | None,
    signal_problem: bool,
) -> Recommendation | None:
    """Advise when the longest exposure in use rarely had clean guiding.

    The longest exposure length in use is compared with how often guiding
    stayed clean for a whole exposure of that length. If fewer than half would
    have been clean, the advice names the main cause, the longest length that
    would have worked, and what to check.

    Returns
    -------
    recommendation : `Recommendation` or `None`
        Advice, or `None` if no exposure length could be measured or at least
        half the exposures of the longest length would have been clean.
    """
    feasibility = performance.exposure_feasibility
    if not feasibility:
        return None
    longest = feasibility[-1]
    if longest.clean_fraction is None or longest.clean_fraction >= RELIABLE_CLEAN_FRACTION:
        return None
    causes = {
        "the guide star was lost": longest.lost_fraction or 0.0,
        "the guider jumped": longest.jump_fraction or 0.0,
        "the error scatter was above what the equipment can absorb": longest.wobble_fraction or 0.0,
    }
    main_cause, main_share = max(causes.items(), key=lambda item: item[1])
    reliable = performance.longest_reliable_exposure_seconds
    alternative = (
        f"The longest length that would have worked for at least half the exposures was {reliable:g} s."
        if reliable is not None
        else "No exposure length in use would have worked for half the exposures."
    )
    return Recommendation(
        kind=RecommendationKind.EXPOSURE_LENGTH_LIMITED,
        severity=RecommendationSeverity.ADVICE,
        message=(
            f"Only {longest.clean_fraction:.0%} of {longest.exposure_seconds:g} s exposures would have "
            f"had clean guiding for their whole length. The main reason was that {main_cause} "
            f"({main_share:.0%} of the windows). {alternative}"
        ),
        evidence={
            "exposure_seconds": longest.exposure_seconds,
            "clean_fraction": longest.clean_fraction,
            "lost_fraction": longest.lost_fraction,
            "jump_fraction": longest.jump_fraction,
            "wobble_fraction": longest.wobble_fraction,
            "windows": float(longest.windows),
        },
        compared_to={
            "reliable_clean_fraction": RELIABLE_CLEAN_FRACTION,
            "guiding_rms_limit": _applicable_limit(envelope, "guiding_rms_limit", input_quality),
            "guide_excursion_limit": _applicable_limit(envelope, "guide_excursion_limit", input_quality),
        },
        confidence="low" if signal_problem else "medium",
    )


def recommend_for_guiding(
    input_quality: GuidingInputQuality,
    performance: GuidingPerformance,
    envelope: PerformanceEnvelope | None,
    logged_guide_scale: float | None = None,
    configured_guide_scale: float | None = None,
) -> list[Recommendation]:
    """Say what to change, given one night's guiding analysis.

    Parameters
    ----------
    input_quality : `GuidingInputQuality`
        The pre-processing result.
    performance : `GuidingPerformance`
        The processing result.
    envelope : `PerformanceEnvelope` or `None`
        The equipment-derived limits.
    logged_guide_scale : `float` or `None`, optional
        The guide plate scale the log recorded, in arcseconds per pixel, for
        the mismatch message.
    configured_guide_scale : `float` or `None`, optional
        The guide plate scale the configuration gives.

    Returns
    -------
    recommendations : `list` [`Recommendation`]
        Most serious first. A night with too little data gets only the
        statement that it cannot be judged.
    """
    if not input_quality.has_enough_samples:
        return [_insufficient_data(input_quality)]

    signal = _check_guide_signal(input_quality)
    signal_problem = signal is not None
    candidates = [
        _recalibrate_guider(input_quality, envelope),
        signal,
        _update_guide_optics(input_quality, logged_guide_scale, configured_guide_scale),
        _guiding_against_limit(input_quality, performance, envelope, signal_problem),
        _exposure_length_limited(input_quality, performance, envelope, signal_problem),
    ]
    recommendations = [candidate for candidate in candidates if candidate is not None]
    return sorted(recommendations, key=lambda item: _SEVERITY_ORDER[item.severity])
