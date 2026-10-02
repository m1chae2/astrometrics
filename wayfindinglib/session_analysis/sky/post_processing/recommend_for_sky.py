"""Purpose: Turn the sky-position comparisons into recommendations.

Description: Reads the coverage and the comparisons and says what, if
anything, to change about where the telescope observes. Every recommendation
carries the evidence it rests on and the tolerance it was compared with.

Rules applied, in order:

1. Fewer than 5 nights with a known position: say so and stop.
2. Each part of the sky that is measurably worse than the rest of the same
   nights: advise, naming the metric and how much worse it is.
3. If star width is poor in the low altitude bands: advise a minimum altitude
   when it is above the configured one.
4. Parts of the sky that too few nights reached, and dimensions along which no
   comparison was possible: say they were not judged, and how to get the data.
5. If parts were judged and none is poor: say so.

Confidence is never `high`. The data is observational, not a designed test,
and altitude is linked to time of night, so a poor region can be a poor hour.
"""

from wayfindinglib.analytics.performance_envelope import BASELINE_SPREAD_MULTIPLIER, MINIMUM_BASELINE_SESSIONS
from wayfindinglib.models.session.session_quality import (
    Recommendation,
    RecommendationKind,
    RecommendationSeverity,
)
from wayfindinglib.models.session.sky_quality import SkyBinResult, SkyInputQuality, SkyPerformance

_SEVERITY_ORDER = {
    RecommendationSeverity.WARNING: 0,
    RecommendationSeverity.ADVICE: 1,
    RecommendationSeverity.INFO: 2,
}

_METRIC_PHRASES = {
    "star_width": "Stars were {amount} wider",
    "star_roundness": "Stars were {amount} less round",
    "guiding_error": "The guiding error was {amount} larger",
}
"""How each metric's deficit is worded."""

_DIMENSION_PHRASES = {
    "altitude": "at altitude {label}",
    "azimuth": "when pointing {label}",
    "pier_side": "on the {label} side of the pier",
}
"""How each dimension's part of the sky is worded."""


def _insufficient_data(input_quality: SkyInputQuality) -> Recommendation:
    """Say that too few nights exist to compare parts of the sky.

    Returns
    -------
    recommendation : `Recommendation`
        An information-level statement of the shortfall.
    """
    return Recommendation(
        kind=RecommendationKind.INSUFFICIENT_DATA,
        severity=RecommendationSeverity.INFO,
        message=(
            f"Only {input_quality.nights} nights have measurements with a known pointing; at least "
            f"{MINIMUM_BASELINE_SESSIONS} are needed to compare parts of the sky."
        ),
        evidence={"nights": float(input_quality.nights)},
        compared_to={"minimum_nights": float(MINIMUM_BASELINE_SESSIONS)},
        confidence="high",
    )


def _poor_region(metric: str, bin_: SkyBinResult, tolerance: float) -> Recommendation:
    """Describe one part of the sky that is measurably worse.

    Returns
    -------
    recommendation : `Recommendation`
        Advice naming the metric, the region and how much worse it is.
    """
    worse_by = bin_.worse_by or 0.0
    where = _DIMENSION_PHRASES[bin_.dimension].format(label=bin_.label)
    what = _METRIC_PHRASES[metric].format(amount=f"{worse_by:.0%}")
    caveat = (
        " Altitude is linked to time of night, so this may be a poor hour (seeing, or focus that "
        "drifted since the last autofocus) and not a poor region."
        if bin_.dimension == "altitude"
        else ""
    )
    return Recommendation(
        kind=RecommendationKind.SKY_REGION_POOR,
        severity=RecommendationSeverity.ADVICE,
        message=(
            f"{what} {where} than in the rest of the same nights, over {bin_.nights} nights "
            f"({bin_.samples} measurements). The tolerance is {tolerance:.0%}.{caveat}"
        ),
        evidence={
            "metric": metric,
            "dimension": bin_.dimension,
            "region": bin_.label,
            "worse_by": worse_by,
            "z_score": bin_.z_score,
            "nights": float(bin_.nights),
            "samples": float(bin_.samples),
        },
        compared_to={
            "blur_tolerance_fraction": tolerance,
            "minimum_z_score": BASELINE_SPREAD_MULTIPLIER,
            "night_value_spread": bin_.spread,
        },
        confidence="medium",
    )


def _minimum_altitude(
    input_quality: SkyInputQuality, performance: SkyPerformance, tolerance: float
) -> Recommendation | None:
    """Advise a higher minimum altitude when low bands gave wider stars.

    Returns
    -------
    recommendation : `Recommendation` or `None`
        Advice, or `None` if no altitude band was poor or the suggested
        altitude is not above the configured minimum.
    """
    suggested = performance.suggested_minimum_altitude_degrees
    configured = input_quality.configured_minimum_altitude_degrees
    if suggested is None or (configured is not None and suggested <= configured):
        return None
    return Recommendation(
        kind=RecommendationKind.MINIMUM_ALTITUDE_SUGGESTED,
        severity=RecommendationSeverity.ADVICE,
        message=(
            f"Stars were measurably wider below {suggested:g} degrees altitude. The telescope is "
            f"configured to observe down to {configured:g} degrees. Consider a minimum altitude of "
            f"{suggested:g} degrees for work that needs sharp stars."
        ),
        evidence={"suggested_minimum_altitude_degrees": suggested},
        compared_to={
            "configured_minimum_altitude_degrees": configured,
            "blur_tolerance_fraction": tolerance,
        },
        confidence="medium",
    )


def _coverage_gaps(input_quality: SkyInputQuality) -> Recommendation | None:
    """Say which parts of the sky too few nights reached.

    Returns
    -------
    recommendation : `Recommendation` or `None`
        An information-level list of the gaps, or `None` if every reachable
        part was covered.
    """
    if not input_quality.gaps:
        return None
    lowest = input_quality.altitude_range_degrees[0] if input_quality.altitude_range_degrees else None
    note = f" The lowest pointing measured was {lowest:.0f} degrees altitude." if lowest is not None else ""
    return Recommendation(
        kind=RecommendationKind.SKY_COVERAGE_GAP,
        severity=RecommendationSeverity.INFO,
        message=(
            f"{len(input_quality.gaps)} parts of the sky were reached on fewer than "
            f"{input_quality.minimum_nights_per_bin} nights, so they were not judged: "
            f"{', '.join(input_quality.gaps)}.{note} Observing there on more nights would show "
            "whether the telescope performs differently."
        ),
        evidence={"parts_not_judged": float(len(input_quality.gaps))},
        compared_to={"minimum_nights_per_part": float(input_quality.minimum_nights_per_bin)},
        confidence="high",
    )


def _dimensions_not_compared(
    input_quality: SkyInputQuality, performance: SkyPerformance
) -> Recommendation | None:
    """Say which dimensions no comparison was possible along, and why.

    A comparison between two parts of the sky needs nights that observed both.
    A dimension with no judged part for star width means too few nights did.

    Returns
    -------
    recommendation : `Recommendation` or `None`
        An information-level note with advice on how to get the comparison, or
        `None` if every dimension had at least one judged part.
    """
    star_width = next((result for result in performance.metrics if result.metric == "star_width"), None)
    if star_width is None:
        return None
    judged_by_dimension: dict[str, int] = {}
    for bin_ in star_width.bins:
        judged_by_dimension[bin_.dimension] = judged_by_dimension.get(bin_.dimension, 0) + bin_.judged
    missing = [dimension for dimension, judged in judged_by_dimension.items() if judged == 0]
    if not missing:
        return None
    names = ", ".join(dimension.replace("_", " ") for dimension in missing)
    return Recommendation(
        kind=RecommendationKind.SKY_COVERAGE_GAP,
        severity=RecommendationSeverity.INFO,
        message=(
            f"Star width could not be compared along: {names}. Fewer than "
            f"{input_quality.minimum_nights_per_bin} nights observed more than one part of the sky "
            "along each, and a night that stayed in one part cannot say how it compares with another. "
            "Images of the same target early and late in one night, or on both sides of the meridian, "
            "would give that comparison."
        ),
        evidence={"dimensions_not_compared": float(len(missing))},
        compared_to={"minimum_nights_per_part": float(input_quality.minimum_nights_per_bin)},
        confidence="high",
    )


def recommend_for_sky(
    input_quality: SkyInputQuality, performance: SkyPerformance, blur_tolerance_fraction: float
) -> list[Recommendation]:
    """Recommend what, if anything, to change about where to observe.

    Parameters
    ----------
    input_quality : `SkyInputQuality`
        The pre-processing result.
    performance : `SkyPerformance`
        The processing result.
    blur_tolerance_fraction : `float`
        The tolerance the comparisons used.

    Returns
    -------
    recommendations : `list` [`Recommendation`]
        Warnings first, then advice, then information.
    """
    if not input_quality.has_enough_data:
        return [_insufficient_data(input_quality)]
    recommendations: list[Recommendation] = []
    judged = 0
    for result in performance.metrics:
        for bin_ in result.bins:
            judged += bin_.judged
            if bin_.is_poor:
                recommendations.append(_poor_region(result.metric, bin_, blur_tolerance_fraction))
    for extra in (
        _minimum_altitude(input_quality, performance, blur_tolerance_fraction),
        _coverage_gaps(input_quality),
        _dimensions_not_compared(input_quality, performance),
    ):
        if extra is not None:
            recommendations.append(extra)
    if judged and not any(r.kind == RecommendationKind.SKY_REGION_POOR for r in recommendations):
        recommendations.append(
            Recommendation(
                kind=RecommendationKind.SKY_WITHIN_LIMITS,
                severity=RecommendationSeverity.INFO,
                message=(
                    f"{judged} comparisons of a part of the sky with the rest of the same nights were "
                    f"made, and none was measurably worse by {blur_tolerance_fraction:.0%} or more."
                ),
                evidence={"comparisons_judged": float(judged)},
                compared_to={"blur_tolerance_fraction": blur_tolerance_fraction},
                confidence="medium",
            )
        )
    return sorted(recommendations, key=lambda recommendation: _SEVERITY_ORDER[recommendation.severity])
