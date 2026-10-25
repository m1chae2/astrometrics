"""Purpose: Turn the sky-position comparisons into statements.

Description: Reads the coverage and the comparisons and says what, if
anything, to change about where the telescope observes. Every statement is a
`TargetStatement` built from its row of the session rule table
(`wayfindinglib.analytics.result_quality.session_rules`). Every statement
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

Confidence is the register status of the sky rules (WAY-47), which is a
design estimate: the data is observational, not a designed test, and altitude
is linked to time of night, so a poor region can be a poor hour.
"""

from astrometricslib import QualityLimit
from wayfindinglib.analytics.performance_envelope import BASELINE_SPREAD_MULTIPLIER, MINIMUM_BASELINE_SESSIONS
from wayfindinglib.models.quality.statements import TargetStatement
from wayfindinglib.models.session.sky_quality import SkyBinResult, SkyInputQuality, SkyPerformance
from wayfindinglib.session_analysis.rule_support import (
    design_limit,
    rule_confidence,
    session_statement,
    sort_by_severity,
)

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


def _tolerance_limit(tolerance: float) -> QualityLimit:
    """Describe the blur tolerance a comparison used as a limit.

    Returns
    -------
    limit : `QualityLimit`
        The tolerance, as a design-estimate limit.
    """
    return design_limit(
        tolerance,
        "fraction",
        "The most a star may widen before a part of the sky counts as worse (the blur tolerance).",
        "WAY-D40",
    )


def _minimum_nights_limit(input_quality: SkyInputQuality) -> QualityLimit:
    """Describe the fewest nights a part of the sky needs as a limit.

    Returns
    -------
    limit : `QualityLimit`
        The minimum number of nights per part, as a design-estimate limit.
    """
    return design_limit(
        float(input_quality.minimum_nights_per_bin),
        "nights",
        "Fewest nights that must have reached a part of the sky before it is judged.",
        "WAY-D42",
    )


def _insufficient_data(input_quality: SkyInputQuality) -> TargetStatement:
    """Say that too few nights exist to compare parts of the sky.

    Returns
    -------
    statement : `TargetStatement`
        An information-level statement of the shortfall.
    """
    return session_statement(
        "sky_insufficient_data",
        message=(
            f"Only {input_quality.nights} nights have measurements with a known pointing; at least "
            f"{MINIMUM_BASELINE_SESSIONS} are needed to compare parts of the sky."
        ),
        reason=(
            "Too few nights with a known pointing. Image on more nights and sync, then the parts "
            "of the sky can be compared."
        ),
        values={"nights": float(input_quality.nights)},
        plain_values={"minimum": MINIMUM_BASELINE_SESSIONS},
        limits=[
            design_limit(
                float(MINIMUM_BASELINE_SESSIONS),
                "nights",
                "Fewest nights with a known pointing that a comparison of parts of the sky needs.",
                "WAY-D42",
            )
        ],
        confidence=rule_confidence("sky"),
    )


def _poor_region(metric: str, bin_: SkyBinResult, tolerance: float) -> TargetStatement:
    """Describe one part of the sky that is measurably worse.

    Returns
    -------
    statement : `TargetStatement`
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
    return session_statement(
        "sky_region_poor",
        message=(
            f"{what} {where} than in the rest of the same nights, over {bin_.nights} nights "
            f"({bin_.samples} measurements). The tolerance is {tolerance:.0%}.{caveat}"
        ),
        plain_values={"what": what, "where": where},
        values={
            "metric": metric,
            "dimension": bin_.dimension,
            "region": bin_.label,
            "worse_by": worse_by,
            "z_score": bin_.z_score,
            "night_value_spread": bin_.spread,
            "nights": float(bin_.nights),
            "samples": float(bin_.samples),
        },
        limits=[
            _tolerance_limit(tolerance),
            design_limit(
                BASELINE_SPREAD_MULTIPLIER,
                "sigma",
                "How many robust standard deviations of the nights' own values a part must be worse by.",
                "WAY-D42",
            ),
        ],
        confidence=rule_confidence("sky"),
    )


def _minimum_altitude(
    input_quality: SkyInputQuality, performance: SkyPerformance, tolerance: float
) -> TargetStatement | None:
    """Advise a higher minimum altitude when low bands gave wider stars.

    Returns
    -------
    statement : `TargetStatement` or `None`
        Advice, or `None` if no altitude band was poor or the suggested
        altitude is not above the configured minimum.
    """
    suggested = performance.suggested_minimum_altitude_degrees
    configured = input_quality.configured_minimum_altitude_degrees
    if suggested is None or (configured is not None and suggested <= configured):
        return None
    return session_statement(
        "minimum_altitude_suggested",
        message=(
            f"Stars were measurably wider below {suggested:g} degrees altitude. The telescope is "
            f"configured to observe down to {configured:g} degrees. Consider a minimum altitude of "
            f"{suggested:g} degrees for work that needs sharp stars."
        ),
        values={
            "suggested_minimum_altitude_degrees": suggested,
            "configured_minimum_altitude_degrees": configured,
        },
        limits=[_tolerance_limit(tolerance)],
        confidence=rule_confidence("sky"),
    )


def _coverage_gaps(input_quality: SkyInputQuality) -> TargetStatement | None:
    """Say which parts of the sky too few nights reached.

    Returns
    -------
    statement : `TargetStatement` or `None`
        An information-level list of the gaps, or `None` if every reachable
        part was covered.
    """
    if not input_quality.gaps:
        return None
    lowest = input_quality.altitude_range_degrees[0] if input_quality.altitude_range_degrees else None
    note = f" The lowest pointing measured was {lowest:.0f} degrees altitude." if lowest is not None else ""
    return session_statement(
        "sky_coverage_gap",
        message=(
            f"{len(input_quality.gaps)} parts of the sky were reached on fewer than "
            f"{input_quality.minimum_nights_per_bin} nights, so they were not judged: "
            f"{', '.join(input_quality.gaps)}.{note} Observing there on more nights would show "
            "whether the telescope performs differently."
        ),
        reason=(
            "Too few nights reached these parts of the sky. Observing there on more nights gives a verdict."
        ),
        values={"parts_not_judged": float(len(input_quality.gaps))},
        plain_values={"count": len(input_quality.gaps)},
        limits=[_minimum_nights_limit(input_quality)],
        confidence=rule_confidence("sky"),
    )


def _dimensions_not_compared(
    input_quality: SkyInputQuality, performance: SkyPerformance
) -> TargetStatement | None:
    """Say which dimensions no comparison was possible along, and why.

    A comparison between two parts of the sky needs nights that observed both.
    A dimension with no judged part for star width means too few nights did.

    Returns
    -------
    statement : `TargetStatement` or `None`
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
    return session_statement(
        "sky_coverage_gap",
        message=(
            f"Star width could not be compared along: {names}. Fewer than "
            f"{input_quality.minimum_nights_per_bin} nights observed more than one part of the sky "
            "along each, and a night that stayed in one part cannot say how it compares with another. "
            "Images of the same target early and late in one night, or on both sides of the meridian, "
            "would give that comparison."
        ),
        reason=(
            "No night observed more than one part of the sky along these dimensions, so no two parts "
            "could be compared. Image one target early and late, or on both sides of the meridian."
        ),
        values={"dimensions_not_compared": float(len(missing))},
        plain_message=(
            f"We could not compare star width along: {names}. Image one target early and late in a night, "
            "or on both sides of the meridian, to get that comparison."
        ),
        limits=[_minimum_nights_limit(input_quality)],
        confidence=rule_confidence("sky"),
    )


def recommend_for_sky(
    input_quality: SkyInputQuality, performance: SkyPerformance, blur_tolerance_fraction: float
) -> list[TargetStatement]:
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
    statements : `list` [`TargetStatement`]
        Warnings first, then advice, then information.
    """
    if not input_quality.has_enough_data:
        return [_insufficient_data(input_quality)]
    recommendations: list[TargetStatement] = []
    for result in performance.metrics:
        for bin_ in result.bins:
            if bin_.is_poor:
                recommendations.append(_poor_region(result.metric, bin_, blur_tolerance_fraction))
    for extra in (
        _minimum_altitude(input_quality, performance, blur_tolerance_fraction),
        _coverage_gaps(input_quality),
        _dimensions_not_compared(input_quality, performance),
    ):
        if extra is not None:
            recommendations.append(extra)
    return sort_by_severity(recommendations)
