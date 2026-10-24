"""Purpose: Judge whether one star's science result can be trusted.

Description: `get_advisory(kind="result", target_id=..., object_id=...)` tells
the user how far to trust a spectrum's classification or a variable star's
period (`QUALITY_METRICS_PLAN.md` Sections 3.3 and 6.1, use case UC5). The
numbers sit in the star's record, not in the measurement store, so a per-star
result has no row id. A statement points at it by star, run and statistic
(`StarResultReference`).

The rules here use only what the star record already holds. They add no
measurement and no new limit of their own beyond one design estimate:

* A spectrum. The record holds the relative RMS of the best match and the
  gaps to the second-best match and to the next class. Whether the match is
  poor, or the class ambiguous, uses the limits of the spectroscopy pipeline
  (`NO_GOOD_MATCH_RMS` and `AMBIGUOUS_RMS_GAP`, register entries SPE-16 and
  SPE-18). A copy of each value is kept here so the statement can quote it, and
  a test compares the copy with the pipeline. The answer "more time will not
  separate these classes at this resolution" needs a calibration of the class
  gap against the signal-to-noise ratio that does not exist yet, so it is an
  explicit `insufficient_data` statement and never a guess.
* A period or a repeating dip. The record holds the search's verdict, its
  false-alarm probability (the chance that noise alone looks this strong), the
  number of cycles seen, and the checks made on data the period was not found
  from. A failed check, an unconfirmed result and fewer than two full cycles
  are warnings.

Every statement gives the raw number with it, so no result shows a bare yes or
no. A probability is shown only as the pipeline stored it. Nothing here makes
a probability of its own.

The rules never read an exploratory metric, and the layering matches the rest
of the quality code: this module imports the science library from its root.
"""

from typing import Any

from astrometricslib import (
    AMBIGUOUS_RMS_GAP,
    NO_GOOD_MATCH_RMS,
    LimitTier,
    MeasurementReference,
    StarResultReference,
)
from wayfindinglib.analytics.result_quality.rules import build_limit
from wayfindinglib.models.quality.rule import QualityRule
from wayfindinglib.models.quality.statements import (
    ActionClass,
    Confidence,
    ProposedAction,
    Severity,
    StatementKind,
    TargetStatement,
)

STAR_RESULT_ENTRY = "WAY-73"
"""Register entry behind the judgment of per-star results."""

STAR_RESULT_ROW = "WAY-D73"
"""Register row behind the design estimates below."""

MINIMUM_CYCLES_FOR_PERIOD = 2.0
"""The fewest full cycles of a period that the data should cover. One cycle
shows a pattern once; two show that it repeats. This is the usual minimum
for a period claim. Design estimate, register row WAY-D73."""

NO_RUN_ID = "not recorded"
"""The run id given to a result saved before its job was recorded."""

SPECTRAL_TYPE_POOR_MATCH = "spectral_type_poor_match"
SPECTRAL_CLASS_AMBIGUOUS = "spectral_class_ambiguous"
SPECTRAL_TYPE_NOT_FOUND = "spectral_type_not_found"
SPECTRUM_USE_RESULT = "spectrum_use_result"
SPECTRAL_REACHABILITY = "spectral_reachability"
PERIOD_FAILED_CHECK = "period_failed_check"
PERIOD_UNCONFIRMED = "period_unconfirmed"
PERIOD_FEW_CYCLES = "period_few_cycles"
PERIOD_USE_RESULT = "period_use_result"
PERIOD_NOT_DETECTED = "period_not_detected"
PERIOD_NOT_TESTABLE = "period_not_testable"
NO_STAR_RESULT = "no_star_result"


_POOR_MATCH_LIMIT = build_limit(
    NO_GOOD_MATCH_RMS,
    "relative RMS",
    LimitTier.VALIDATED_DATASET,
    (
        "Matches within three spectral-type steps scored 0.046 to 0.105 and matches to the wrong type "
        "scored 0.181 to 0.283 on 14 stars. Partly measured: the score was calibrated with the response "
        "fitted on one of them."
    ),
    "SPE-16",
    14,
)
_AMBIGUOUS_GAP_LIMIT = build_limit(
    AMBIGUOUS_RMS_GAP,
    "relative RMS",
    LimitTier.VALIDATED_DATASET,
    (
        "The smallest distance between two neighbouring rungs of the reference ladder. A gap below it means "
        "the spectrum cannot prefer one rung over its neighbour."
    ),
    "SPE-18",
)
_CYCLES_LIMIT = build_limit(
    MINIMUM_CYCLES_FOR_PERIOD,
    "cycles",
    LimitTier.DESIGN_ESTIMATE,
    "One cycle shows a pattern once. Two show that it repeats. The usual minimum for a period claim.",
    STAR_RESULT_ROW,
)

STAR_RESULT_RULES: tuple[QualityRule, ...] = (
    QualityRule(
        name=SPECTRAL_TYPE_POOR_MATCH,
        statement_kind=StatementKind.WARNING,
        action_class=ActionClass.DATA_SELECTION,
        severity=Severity.WARNING,
        limits=[_POOR_MATCH_LIMIT],
        register_entry="SPE-16",
        confidence=Confidence.PARTLY_MEASURED,
        wording=(
            "The best reference differs from this spectrum by {rms:.2f} (relative RMS), above {limit:.2f}."
        ),
        purpose="A poor match means the named spectral type should not be claimed.",
    ),
    QualityRule(
        name=SPECTRAL_CLASS_AMBIGUOUS,
        statement_kind=StatementKind.WARNING,
        action_class=ActionClass.DATA_SELECTION,
        severity=Severity.ADVICE,
        limits=[_AMBIGUOUS_GAP_LIMIT],
        register_entry="SPE-18",
        confidence=Confidence.MEASURED,
        wording="Another class fits within {gap:.3f} (relative RMS) of the best one.",
        purpose="When the next class fits almost as well, the class letter itself is uncertain.",
    ),
    QualityRule(
        name=SPECTRAL_TYPE_NOT_FOUND,
        statement_kind=StatementKind.INSUFFICIENT_DATA,
        register_entry=STAR_RESULT_ROW,
        confidence=Confidence.DESIGN_ESTIMATE,
        wording="No spectral type was found for this spectrum.",
        purpose="Says that the classifier named no type, and why.",
    ),
    QualityRule(
        name=SPECTRUM_USE_RESULT,
        statement_kind=StatementKind.RECOMMENDATION,
        action_class=ActionClass.DATA_SELECTION,
        limits=[_POOR_MATCH_LIMIT, _AMBIGUOUS_GAP_LIMIT],
        register_entry=STAR_RESULT_ROW,
        confidence=Confidence.DESIGN_ESTIMATE,
        wording="The best match is {spectral_type}, with a relative RMS of {rms:.2f}.",
        purpose="Gives the number behind a spectral type that passed both checks.",
    ),
    QualityRule(
        name=SPECTRAL_REACHABILITY,
        statement_kind=StatementKind.INSUFFICIENT_DATA,
        register_entry=STAR_RESULT_ROW,
        confidence=Confidence.DESIGN_ESTIMATE,
        wording="Whether more exposure would separate the best classes is not known.",
        purpose=(
            "Says that 'not reachable at this resolution' cannot be judged until the class gap is "
            "calibrated against the signal-to-noise ratio."
        ),
    ),
    QualityRule(
        name=PERIOD_FAILED_CHECK,
        statement_kind=StatementKind.WARNING,
        action_class=ActionClass.DATA_SELECTION,
        severity=Severity.WARNING,
        register_entry=STAR_RESULT_ROW,
        confidence=Confidence.DESIGN_ESTIMATE,
        wording="The {label} failed a check made on data it was not found from: {checks}.",
        purpose="A result that fails a held-out check is lowered a level, and the user should see which.",
    ),
    QualityRule(
        name=PERIOD_UNCONFIRMED,
        statement_kind=StatementKind.WARNING,
        action_class=ActionClass.SESSION_PROCEDURE,
        severity=Severity.ADVICE,
        register_entry=STAR_RESULT_ROW,
        confidence=Confidence.DESIGN_ESTIMATE,
        wording="The {label} has not been confirmed on data it was not found from.",
        purpose="Too few nights to leave one out: the result stands, but it is unchecked.",
    ),
    QualityRule(
        name=PERIOD_FEW_CYCLES,
        statement_kind=StatementKind.WARNING,
        action_class=ActionClass.SESSION_PROCEDURE,
        severity=Severity.ADVICE,
        limits=[_CYCLES_LIMIT],
        register_entry=STAR_RESULT_ROW,
        confidence=Confidence.DESIGN_ESTIMATE,
        wording="The data cover {cycles:.1f} cycles of the {label}; two are needed to show it repeats.",
        purpose="A period seen for less than two cycles may be a trend.",
    ),
    QualityRule(
        name=PERIOD_USE_RESULT,
        statement_kind=StatementKind.RECOMMENDATION,
        action_class=ActionClass.DATA_SELECTION,
        register_entry=STAR_RESULT_ROW,
        confidence=Confidence.DESIGN_ESTIMATE,
        wording=(
            "The search found a {label} of {period:.4g} days; the chance that noise alone looks this "
            "strong is {probability:.2g}."
        ),
        purpose="Gives the number behind a detected or possible period that passed every check.",
    ),
    QualityRule(
        name=PERIOD_NOT_DETECTED,
        statement_kind=StatementKind.RECOMMENDATION,
        action_class=ActionClass.DATA_SELECTION,
        register_entry=STAR_RESULT_ROW,
        confidence=Confidence.DESIGN_ESTIMATE,
        wording="The search found no {label} stronger than noise would give.",
        purpose="A search with enough data that found nothing is a result: do not claim a period.",
    ),
    QualityRule(
        name=PERIOD_NOT_TESTABLE,
        statement_kind=StatementKind.INSUFFICIENT_DATA,
        register_entry=STAR_RESULT_ROW,
        confidence=Confidence.DESIGN_ESTIMATE,
        wording="The data cannot test for a {label}.",
        purpose="Says that the measurements are too few or too short, and what the search reported.",
    ),
    QualityRule(
        name=NO_STAR_RESULT,
        statement_kind=StatementKind.INSUFFICIENT_DATA,
        register_entry=STAR_RESULT_ROW,
        confidence=Confidence.DESIGN_ESTIMATE,
        wording="This star has no result to judge.",
        purpose="Names the results that are missing, so the user knows what to run.",
    ),
)
"""The rules that judge a per-star result, one row each."""

_RULES_BY_NAME = {rule.name: rule for rule in STAR_RESULT_RULES}


def star_rule_by_name(name: str) -> QualityRule:
    """Find a per-star rule by name.

    Parameters
    ----------
    name : `str`
        The rule's name.

    Returns
    -------
    rule : `QualityRule`
        The rule.
    """
    return _RULES_BY_NAME[name]


def _reference(star_id: str, run_id: str | None, metric: str) -> MeasurementReference:
    """Point at one statistic of a star's result.

    Parameters
    ----------
    star_id : `str`
        The star.
    run_id : `str` or `None`
        The job that produced the result, or ``None`` for an unrecorded one.
    metric : `str`
        The name of the statistic (a field of the star's record).

    Returns
    -------
    reference : `MeasurementReference`
        A reference to the per-star result.
    """
    return MeasurementReference(
        star_result=StarResultReference(star_id=star_id, run_id=run_id or NO_RUN_ID, metric=metric)
    )


def _statement(
    name: str,
    message: str,
    reason: str,
    evidence: list[MeasurementReference],
    *,
    proposed_note: str | None = None,
) -> TargetStatement:
    """Build a statement from a rule row.

    Parameters
    ----------
    name : `str`
        The rule's name.
    message : `str`
        One plain sentence with the numbers filled in.
    reason : `str`
        Why, or for missing data, what is missing.
    evidence : `list` [`MeasurementReference`]
        The statistics used.
    proposed_note : `str`, optional
        A short plain action for a warning or recommendation. It becomes the
        note of the proposed action.

    Returns
    -------
    statement : `TargetStatement`
        The statement, with the rule's kind, severity, action class, limits
        and confidence.
    """
    rule = star_rule_by_name(name)
    return TargetStatement(
        rule=rule.name,
        kind=rule.statement_kind,
        severity=rule.severity,
        action_class=rule.action_class,
        message=message,
        reason=reason,
        proposed_action=ProposedAction(note=proposed_note) if proposed_note else None,
        evidence=evidence,
        limits=list(rule.limits),
        confidence=rule.confidence,
    )


def judge_spectrum(star_id: str, spectroscopy: Any) -> list[TargetStatement]:
    """Judge one star's spectral classification.

    Parameters
    ----------
    star_id : `str`
        The star.
    spectroscopy : `SpectroscopyResult`
        The star's spectrum result. It must hold a spectrum.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        The warnings found, then, if there are none, the number behind the
        type; always the note that reachability cannot be judged yet.
    """
    run = spectroscopy.generated_by_job_id
    rms = spectroscopy.self_determined_spectral_type_rms
    statements: list[TargetStatement] = []
    if not spectroscopy.self_determined_spectral_type or rms is None:
        note = spectroscopy.self_determined_spectral_type_note or "The classifier gave no reason."
        statements.append(
            _statement(
                SPECTRAL_TYPE_NOT_FOUND,
                "No spectral type was found for this spectrum.",
                note,
                [_reference(star_id, run, "spectroscopy.self_determined_spectral_type_rms")],
            )
        )
        return statements
    spectral_type = spectroscopy.self_determined_spectral_type
    rms_reference = _reference(star_id, run, "spectroscopy.self_determined_spectral_type_rms")
    if spectroscopy.is_poor_match:
        statements.append(
            _statement(
                SPECTRAL_TYPE_POOR_MATCH,
                (
                    f"The best reference ({spectral_type}) differs from this spectrum by {rms:.2f} "
                    f"(relative RMS), above the {NO_GOOD_MATCH_RMS:.2f} limit for a good match."
                ),
                "Do not claim the type: even the closest reference fits badly.",
                [rms_reference],
                proposed_note=f"Do not report {spectral_type} as the star's type.",
            )
        )
    gap = spectroscopy.rms_gap_to_next_class
    if spectroscopy.is_class_ambiguous and gap is not None:
        statements.append(
            _statement(
                SPECTRAL_CLASS_AMBIGUOUS,
                (
                    f"Another spectral class fits within {gap:.3f} (relative RMS) of the best one "
                    f"({spectral_type}), below the {AMBIGUOUS_RMS_GAP:.2f} gap needed to tell them apart."
                ),
                "The class letter is uncertain, not only the subtype.",
                [rms_reference, _reference(star_id, run, "spectroscopy.rms_gap_to_next_class")],
                proposed_note="Report the class as uncertain, with the neighbouring class.",
            )
        )
    if not statements:
        gap_text = f", and the next class is {gap:.3f} further away" if gap is not None else ""
        statements.append(
            _statement(
                SPECTRUM_USE_RESULT,
                f"The best match is {spectral_type}, with a relative RMS of {rms:.2f}{gap_text}.",
                "The match is within the limit and the class is not ambiguous.",
                [rms_reference]
                + ([_reference(star_id, run, "spectroscopy.rms_gap_to_next_class")] if gap else []),
                proposed_note=f"The type {spectral_type} may be used.",
            )
        )
    statements.append(
        _statement(
            SPECTRAL_REACHABILITY,
            "Whether more exposure would separate the best classes at this resolution is not known.",
            (
                "Judging 'not reachable at this resolution' needs the class gap measured against the "
                "signal-to-noise ratio for this grating, and that calibration does not exist yet."
            ),
            [rms_reference],
        )
    )
    return statements


def judge_period_search(
    star_id: str, search: Any, search_label: str, metric_prefix: str, run_id: str | None = None
) -> list[TargetStatement]:
    """Judge one period search or repeating-dip search.

    Parameters
    ----------
    star_id : `str`
        The star.
    search : `PeriodogramResult` or `TransitCandidate`
        The search result. It must have a verdict.
    search_label : `str`
        How the statements name it: ``"period"`` or ``"repeating dip"``.
    metric_prefix : `str`
        The prefix of the statistics' names, such as
        ``"photometry.periodogram"``.
    run_id : `str`, optional
        The job that wrote the light curve, if it was recorded.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        The warnings, then the number behind the result when there is none.
    """
    verdict = search.verdict
    verdict_reference = _reference(star_id, run_id, f"{metric_prefix}.verdict")
    if verdict == "insufficient_data" or not verdict:
        return [
            _statement(
                PERIOD_NOT_TESTABLE,
                f"The data cannot test for a {search_label}.",
                search.note or "The search reported too few or too short measurements.",
                [verdict_reference],
            )
        ]
    if verdict == "not_detected":
        return [
            _statement(
                PERIOD_NOT_DETECTED,
                f"The search found no {search_label} stronger than noise would give.",
                search.note or "More nights, or longer exposures, may reveal a weaker signal.",
                [verdict_reference],
                proposed_note=f"Do not report a {search_label} for this star.",
            )
        ]
    probability = (
        search.family_wise_false_alarm_probability
        if search.family_wise_false_alarm_probability is not None
        else search.false_alarm_probability
    )
    period = getattr(search, "best_period_days", None) or getattr(search, "period_days", 0.0)
    evidence = [
        verdict_reference,
        _reference(star_id, run_id, f"{metric_prefix}.false_alarm_probability"),
    ]
    statements: list[TargetStatement] = []
    failed = [check for check in search.verdict_checks if check.status.value == "failed"]
    if failed:
        names = "; ".join(
            f"{check.name} ({check.detail})" if check.detail else check.name for check in failed
        )
        statements.append(
            _statement(
                PERIOD_FAILED_CHECK,
                f"The {search_label} failed a check made on data it was not found from: {names}.",
                "The pipeline lowered the verdict one level for each failed check.",
                [*evidence, _reference(star_id, run_id, f"{metric_prefix}.verdict_checks")],
                proposed_note=f"Do not rely on this {search_label} until it passes the check.",
            )
        )
    if search.unconfirmed:
        statements.append(
            _statement(
                PERIOD_UNCONFIRMED,
                f"The {search_label} has not been confirmed on data it was not found from.",
                "Too few nights to leave one out, so the check could not be made.",
                [*evidence, _reference(star_id, run_id, f"{metric_prefix}.unconfirmed")],
                proposed_note="Observe on another night so the result can be checked on held-out data.",
            )
        )
    cycles = getattr(search, "cycles_observed", None)
    if cycles is not None and cycles < MINIMUM_CYCLES_FOR_PERIOD:
        statements.append(
            _statement(
                PERIOD_FEW_CYCLES,
                (
                    f"The data cover {cycles:.1f} cycles of the {search_label}; "
                    f"{MINIMUM_CYCLES_FOR_PERIOD:.0f} are needed to show it repeats."
                ),
                "A pattern seen for less than two cycles may be a trend.",
                [*evidence, _reference(star_id, run_id, f"{metric_prefix}.cycles_observed")],
                proposed_note="Observe over more cycles, on more nights.",
            )
        )
    if not statements:
        statements.append(
            _statement(
                PERIOD_USE_RESULT,
                (
                    f"The search found a {search_label} of {period:.4g} days ({verdict}); the chance that "
                    f"noise alone looks this strong is {probability:.2g}."
                ),
                "The result passed every check that could be made.",
                evidence,
                proposed_note=f"The {search_label} may be used, with its probability.",
            )
        )
    return statements


def judge_star_record(
    star_id: str, spectroscopy: Any | None, photometry: Any | None
) -> list[TargetStatement]:
    """Judge every per-star result a star record holds.

    Parameters
    ----------
    star_id : `str`
        The star.
    spectroscopy : `SpectroscopyResult` or `None`
        The star's spectrum result.
    photometry : `PhotometryResult` or `None`
        The star's light curve and period searches.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        The statements of every result that exists. When none exists, one
        ``insufficient_data`` statement names what is missing.
    """
    statements: list[TargetStatement] = []
    missing: list[str] = []
    run = getattr(photometry, "generated_by_job_id", None)
    if spectroscopy is not None and spectroscopy.intensities:
        statements += judge_spectrum(star_id, spectroscopy)
    else:
        missing.append("no extracted spectrum (run spectroscopy on a frame that holds the star)")
    periodogram = getattr(photometry, "periodogram", None)
    if periodogram is not None and periodogram.verdict:
        statements += judge_period_search(star_id, periodogram, "period", "photometry.periodogram", run)
    else:
        missing.append("no period search (run photometry with a period search on the star)")
    transit = getattr(photometry, "transit_candidate", None)
    if transit is not None and transit.verdict:
        statements += judge_period_search(
            star_id, transit, "repeating dip", "photometry.transit_candidate", run
        )
    if not statements:
        statements.append(
            _statement(
                NO_STAR_RESULT,
                "This star has no spectrum or period search to judge.",
                "Missing: " + "; ".join(missing) + ".",
                [],
            )
        )
    return statements


__all__ = [
    "MINIMUM_CYCLES_FOR_PERIOD",
    "STAR_RESULT_ENTRY",
    "STAR_RESULT_ROW",
    "STAR_RESULT_RULES",
    "judge_period_search",
    "judge_spectrum",
    "judge_star_record",
    "star_rule_by_name",
]
