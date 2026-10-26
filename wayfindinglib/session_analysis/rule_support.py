"""Purpose: Shared helpers for the rules of the per-night analyses.

Description: The guiding, capture and sky analyses each turn measurements
into `TargetStatement` objects, the same shape the target report uses. Every
statement carries a confidence and the limits it was compared with. Three small
things are the same in all three, so they live here:

* A statement is built from its row of the session rule table
  (`session_statement`), so its kind, severity and action class come from the
  table and cannot drift from it.
* Statements are put in order, most serious first (`sort_by_severity`).

* The confidence of a statement comes from the validation register entry
  behind the analysis that made it (`PIPELINE_REGISTER_ENTRIES`), using the
  register's status labels. A statement that rests on a proxy, or on limits
  from equipment that only partly matches the night's, is stepped down one
  label (`rule_confidence`).
* A limit that is a fixed design choice, not one derived from the equipment,
  is described as a `QualityLimit` of the design-estimate tier
  (`design_limit`), so every number a statement was compared with has the same
  shape and names its register row.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from astrometricslib import LimitTier, MeasurementReference, QualityLimit
from wayfindinglib.analytics.result_quality.plain_text import format_plain
from wayfindinglib.analytics.result_quality.session_rules import SESSION_RULES_BY_NAME
from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
from wayfindinglib.models.quality.statements import (
    Confidence,
    ProposedAction,
    Severity,
    TargetStatement,
)

PIPELINE_REGISTER_ENTRIES = {
    "guiding": "WAY-45",
    "capture": "WAY-46",
    "sky": "WAY-47",
}
"""The register entry that backs the rules of each per-night analysis."""

PIPELINE_CONFIDENCE = {
    "guiding": Confidence.PARTLY_MEASURED,
    "capture": Confidence.PARTLY_MEASURED,
    "sky": Confidence.DESIGN_ESTIMATE,
}
"""The status label of each analysis's register entry (WAY-45, WAY-46, WAY-47).

A test checks that these match the Status line of the entries in
`validation/wayfinding.md`, so a change of status there is followed here.
"""

_STEPPED_DOWN = {
    Confidence.MEASURED: Confidence.PARTLY_MEASURED,
    Confidence.PARTLY_MEASURED: Confidence.DESIGN_ESTIMATE,
    Confidence.DESIGN_ESTIMATE: Confidence.DESIGN_ESTIMATE,
}
"""One step toward "design estimate", which is as low as the labels go."""


def rule_confidence(pipeline: str, degraded: bool = False) -> Confidence:
    """Give the confidence of a statement made by one of the analyses.

    Parameters
    ----------
    pipeline : `str`
        ``"guiding"``, ``"capture"`` or ``"sky"``.
    degraded : `bool`, optional
        `True` when the evidence is weaker than the rule assumes: the guide
        signal was poor, the limits come from equipment that only partly
        matches the night's, or the statement rests on a proxy. The label is
        then one step lower.

    Returns
    -------
    confidence : `Confidence`
        The status label of the analysis's register entry, or one step lower.
    """
    base = PIPELINE_CONFIDENCE[pipeline]
    return _STEPPED_DOWN[base] if degraded else base


def design_limit(value: float | None, unit: str, derivation: str, register_entry: str) -> QualityLimit:
    """Describe a fixed design choice as a limit.

    Parameters
    ----------
    value : `float` or `None`
        The number.
    unit : `str`
        Its unit, such as ``"fraction"`` or ``"s"``.
    derivation : `str`
        Where the number comes from, in plain words.
    register_entry : `str`
        The register row (a ``WAY-Dnn`` design estimate) that records it.

    Returns
    -------
    limit : `QualityLimit`
        The limit, of the design-estimate tier.
    """
    return QualityLimit(
        value=value,
        unit=unit,
        tier=LimitTier.DESIGN_ESTIMATE,
        derivation=derivation,
        register_entry=register_entry,
    )


def envelope_limits(
    envelope: PerformanceEnvelope | None, names: tuple[str, ...], applies: bool = True
) -> list[QualityLimit]:
    """Collect the envelope's limits a statement was compared with.

    Parameters
    ----------
    envelope : `PerformanceEnvelope` or `None`
        The equipment-derived limits.
    names : `tuple` [`str`]
        The limit names, in the order to list them.
    applies : `bool`, optional
        `False` when the night's equipment does not match the envelope, in
        which case none of its limits was used and the list is empty.

    Returns
    -------
    limits : `list` [`QualityLimit`]
        Each named limit the envelope holds, even one that could not be
        worked out (its value is then `None` and its derivation says why).
    """
    if envelope is None or not applies:
        return []
    return [limit for name in names if (limit := envelope.limit(name)) is not None]


_SEVERITY_ORDER = {Severity.WARNING: 0, Severity.ADVICE: 1, Severity.INFO: 2}
"""Sort position of each severity: warnings first."""


def sort_by_severity(statements: Iterable[TargetStatement]) -> list[TargetStatement]:
    """Put statements in order, most serious first.

    Parameters
    ----------
    statements : `Iterable` [`TargetStatement`]
        The statements of one analysis.

    Returns
    -------
    ordered : `list` [`TargetStatement`]
        The same statements. Ones with equal severity keep their order, and a
        statement without a severity sorts with the information level.
    """
    return sorted(
        statements,
        key=lambda statement: _SEVERITY_ORDER[statement.severity or Severity.INFO],
    )


def session_statement(
    rule_name: str,
    *,
    message: str,
    confidence: Confidence,
    reason: str = "",
    evidence: Iterable[MeasurementReference] = (),
    limits: Iterable[QualityLimit] = (),
    values: dict[str, float | str | None] | None = None,
    proposed_action: ProposedAction | None = None,
    plain_values: Mapping[str, Any] | None = None,
    plain_message: str | None = None,
) -> TargetStatement:
    """Build a statement from its row of the session rule table.

    Parameters
    ----------
    rule_name : `str`
        The row's name, a key of `SESSION_RULES_BY_NAME`. An unknown name
        raises a ``KeyError``, which is a bug in the caller.
    message : `str`
        What was found and what to do, in plain language.
    confidence : `Confidence`
        How well the statement is backed. The row gives the base label; the
        caller passes a lower one when the evidence is weaker than the rule
        assumes.
    reason : `str`, optional
        Why. Required for a statement about missing data: what is missing and
        how to get it.
    evidence : `Iterable` [`MeasurementReference`], optional
        The stored measurements the statement rests on.
    limits : `Iterable` [`QualityLimit`], optional
        The limits the evidence was compared with.
    values : `dict` [`str`, `float` or `str` or `None`], optional
        The numbers it rests on that are not rows of the measurement store,
        such as guide-log values and counts.
    proposed_action : `ProposedAction` or `None`, optional
        The values proposed, when the statement proposes some.
    plain_values : `Mapping` [`str`, `Any`], optional
        Fields for the row's plain template beyond ``values``.
    plain_message : `str` or `None`, optional
        The plain message, when the caller writes it itself (the row's
        template does not fit this use of the row). Otherwise the row's
        plain template is filled from ``values`` and ``plain_values``.

    Returns
    -------
    statement : `TargetStatement`
        The statement. Its kind, severity and action class are the row's.

    """
    rule = SESSION_RULES_BY_NAME[rule_name]
    if plain_message is None:
        plain_message = format_plain(rule, {**(values or {}), **(plain_values or {})})
    return TargetStatement(
        rule=rule.name,
        kind=rule.statement_kind,
        severity=rule.severity,
        action_class=rule.action_class,
        message=message,
        plain_message=plain_message,
        reason=reason,
        proposed_action=proposed_action,
        evidence=list(evidence),
        limits=list(limits),
        values=values or {},
        confidence=confidence,
    )
