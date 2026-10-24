"""Purpose: The rule table of the per-night analyses.

Description: The guiding, capture and sky analyses of one observing night
(`wayfindinglib.session_analysis`) used to have a shape of their own for the
things they say. They now make `TargetStatement` objects, the same shape the
target report uses, so there is one advice shape in the library. This module
holds one `QualityRule` row for each thing they can say. A row gives the
statement kind, the severity of a warning, the action class, the validation
register entry behind it and the confidence that entry gives.

The three analyses are backed by three register entries:

* WAY-45, the guiding rules (partly measured).
* WAY-46, the capture rules (partly measured).
* WAY-47, the sky rules (design estimate).

A statement built from a row can have a lower confidence than the row when
its evidence is weaker than the rule assumes (a poor guide signal, limits from
partly matching equipment, a proxy). The row gives the base label only.

A statement that says a night is within its limits is a ``constraint`` with
``info`` severity. It states where the night sits against a limit and asks
for nothing. Missing data is an ``insufficient_data`` statement with its
reason, as everywhere else.

This module imports only models, so Control and Planning code can both read
the table. The statements are built in `wayfindinglib.session_analysis`.
"""

from wayfindinglib.models.quality.rule import QualityRule
from wayfindinglib.models.quality.statements import ActionClass, Confidence, Severity, StatementKind

__all__ = ["SESSION_RULES", "SESSION_RULES_BY_NAME"]

_GUIDING_ENTRY = "WAY-45"
_CAPTURE_ENTRY = "WAY-46"
_SKY_ENTRY = "WAY-47"


def _guiding_rule(
    name: str,
    kind: StatementKind,
    severity: Severity,
    action_class: ActionClass | None,
    wording: str,
    purpose: str,
) -> QualityRule:
    """Build one row of the guiding analysis.

    Returns
    -------
    rule : `QualityRule`
        The row, backed by register entry WAY-45 (partly measured).
    """
    return QualityRule(
        name=name,
        statement_kind=kind,
        action_class=action_class,
        severity=severity,
        register_entry=_GUIDING_ENTRY,
        confidence=Confidence.PARTLY_MEASURED,
        wording=wording,
        purpose=purpose,
        computed_by="recommend_for_guiding",
    )


def _capture_rule(
    name: str,
    kind: StatementKind,
    severity: Severity,
    action_class: ActionClass | None,
    wording: str,
    purpose: str,
) -> QualityRule:
    """Build one row of the capture analysis.

    Returns
    -------
    rule : `QualityRule`
        The row, backed by register entry WAY-46 (partly measured).
    """
    return QualityRule(
        name=name,
        statement_kind=kind,
        action_class=action_class,
        severity=severity,
        register_entry=_CAPTURE_ENTRY,
        confidence=Confidence.PARTLY_MEASURED,
        wording=wording,
        purpose=purpose,
        computed_by="recommend_for_capture",
    )


def _sky_rule(
    name: str,
    kind: StatementKind,
    severity: Severity,
    action_class: ActionClass | None,
    wording: str,
    purpose: str,
) -> QualityRule:
    """Build one row of the sky analysis.

    Returns
    -------
    rule : `QualityRule`
        The row, backed by register entry WAY-47 (design estimate).
    """
    return QualityRule(
        name=name,
        statement_kind=kind,
        action_class=action_class,
        severity=severity,
        register_entry=_SKY_ENTRY,
        confidence=Confidence.DESIGN_ESTIMATE,
        wording=wording,
        purpose=purpose,
        computed_by="recommend_for_sky",
    )


_WARNING = StatementKind.WARNING
_RECOMMENDATION = StatementKind.RECOMMENDATION
_CONSTRAINT = StatementKind.CONSTRAINT
_MISSING = StatementKind.INSUFFICIENT_DATA

SESSION_RULES: tuple[QualityRule, ...] = (
    _guiding_rule(
        "guiding_insufficient_data",
        _MISSING,
        Severity.INFO,
        None,
        "Only {samples} measured guide samples exist for this night; at least {minimum} are needed.",
        "A night with too few guide samples cannot be judged, so the analysis says so and stops.",
    ),
    _guiding_rule(
        "recalibrate_guider",
        _WARNING,
        Severity.WARNING,
        ActionClass.SESSION_PROCEDURE,
        "The guider's calibration measured a mount speed that cannot be right. Recalibrate it.",
        "Corrections from a wrong calibration move the mount the wrong amount.",
    ),
    _guiding_rule(
        "check_guide_signal",
        _WARNING,
        Severity.WARNING,
        ActionClass.ADJUSTMENT,
        "The guiding data was gathered poorly: {failed_checks}.",
        "Error numbers from a weak, lost or jumping guide star describe the measurement, not the mount.",
    ),
    _guiding_rule(
        "update_guide_optics_configuration",
        _WARNING,
        Severity.WARNING,
        ActionClass.ADJUSTMENT,
        "The guide log's plate scale differs from the configured guide equipment.",
        "Limits and calibration use the configured optics, so a wrong configuration makes them wrong.",
    ),
    _guiding_rule(
        "guiding_above_limit",
        _RECOMMENDATION,
        Severity.ADVICE,
        ActionClass.ADJUSTMENT,
        "Guiding error was {rms:.2f} arcsec per axis, above the {limit:.2f} arcsec this equipment absorbs.",
        "Guiding error above the limit widens stars more than the blur tolerance allows.",
    ),
    _guiding_rule(
        "exposure_length_limited",
        _RECOMMENDATION,
        Severity.ADVICE,
        ActionClass.CAPTURE_SETTINGS,
        "Only {clean_fraction:.0%} of {exposure_seconds:g} s exposures would have had clean guiding.",
        "An exposure length that rarely has clean guiding wastes frames; a shorter one may work.",
    ),
    _capture_rule(
        "capture_insufficient_data",
        _MISSING,
        Severity.INFO,
        None,
        "Too few frames, or too few earlier nights, to judge this night.",
        "A night with too few frames, or without a baseline from earlier nights, cannot be judged.",
    ),
    _capture_rule(
        "captures_not_in_library",
        _RECOMMENDATION,
        Severity.ADVICE,
        ActionClass.SESSION_PROCEDURE,
        "Ekos finished {captures_without_frame} light exposures that have no frame in the library.",
        "Without all the frames, the analysis describes only part of the night.",
    ),
    _capture_rule(
        "frames_missing_measurements",
        _RECOMMENDATION,
        Severity.ADVICE,
        ActionClass.SESSION_PROCEDURE,
        "Some frames lack measurements that later steps use: {described}.",
        "Frames without measurements cannot be judged or selected.",
    ),
    _capture_rule(
        "frequent_aborted_captures",
        _RECOMMENDATION,
        Severity.ADVICE,
        ActionClass.SESSION_PROCEDURE,
        "{aborted_captures} exposures were cancelled, more than on 90% of this equipment's earlier nights.",
        "Many cancelled exposures point at a cause to find in the session log.",
    ),
    _capture_rule(
        "sensor_temperature_drift",
        _RECOMMENDATION,
        Severity.INFO,
        ActionClass.CALIBRATION,
        "{frames_outside_tolerance} frames were taken far from the night's median sensor temperature.",
        "One set of dark frames suits only frames near its own temperature.",
    ),
    _capture_rule(
        "spectral_star_clipped",
        _RECOMMENDATION,
        Severity.ADVICE,
        ActionClass.CAPTURE_SETTINGS,
        "{target}: a star clipped at {exposure_lengths_clipped} exposure lengths.",
        "A clipped spectroscopy star loses its brightest part, so its spectrum is wrong.",
    ),
    _capture_rule(
        "star_width_above_baseline",
        _WARNING,
        Severity.WARNING,
        ActionClass.SESSION_PROCEDURE,
        "Stars were wider than usual: a median of {median_star_width_arcsec:.1f} arcsec.",
        "Wide stars lose resolution; seeing, focus or tracking can each cause it.",
    ),
    _capture_rule(
        "stars_elongated",
        _WARNING,
        Severity.WARNING,
        ActionClass.ADJUSTMENT,
        "Stars were more elongated than usual: a median roundness of {median_roundness:.2f}.",
        "Elongated stars point at trailing from guiding error or polar misalignment.",
    ),
    _sky_rule(
        "sky_insufficient_data",
        _MISSING,
        Severity.INFO,
        None,
        "Only {nights} nights have measurements with a known pointing; at least {minimum} are needed.",
        "Parts of the sky cannot be compared without enough nights.",
    ),
    _sky_rule(
        "sky_region_poor",
        _RECOMMENDATION,
        Severity.ADVICE,
        ActionClass.SCHEDULING,
        "{what} {where} than in the rest of the same nights.",
        "A part of the sky that is measurably worse can be avoided when planning.",
    ),
    _sky_rule(
        "minimum_altitude_suggested",
        _RECOMMENDATION,
        Severity.ADVICE,
        ActionClass.SCHEDULING,
        "Stars were measurably wider below {suggested:g} degrees altitude.",
        "A higher minimum altitude avoids the low bands where stars were wider.",
    ),
    _sky_rule(
        "sky_coverage_gap",
        _MISSING,
        Severity.INFO,
        None,
        "{count} parts of the sky were reached on too few nights, so they were not judged.",
        "Says which parts of the sky have no verdict and how more nights would give one.",
    ),
)
"""One row for each thing the per-night analyses can say.

The names are the former ``RecommendationKind`` values, except that the three
``insufficient_data`` kinds carry the name of their analysis, so every name is
unique across the library.
"""

SESSION_RULES_BY_NAME: dict[str, QualityRule] = {rule.name: rule for rule in SESSION_RULES}
"""The rows by name, for building a statement from its rule."""
