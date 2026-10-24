"""Purpose: Tests for the shared quality statement models.

Description: Checks the validators of `TargetStatement`, `ProposedAction`,
`NeedsAttention` and `QualityRule`, that the camelCase aliases work, and
that every model survives a JSON round trip.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from astrometricslib import CaptureSetting, LimitTier, MeasurementReference, QualityLimit
from wayfindinglib.models.quality.rule import MetricRequirement, QualityRule
from wayfindinglib.models.quality.statements import (
    ActionClass,
    ChecklistReturn,
    Confidence,
    ExpectedEffect,
    NeedsAttention,
    ProposedAction,
    Severity,
    StatementKind,
    TargetStatement,
)


def _statement(**overrides: object) -> TargetStatement:
    """Build a valid recommendation, with any field replaced.

    Returns
    -------
    statement : `TargetStatement`
        A valid statement.
    """
    fields: dict = {
        "rule": "more_frames",
        "kind": StatementKind.RECOMMENDATION,
        "action_class": ActionClass.CAPTURE_SETTINGS,
        "message": "Take 20 more frames.",
        "confidence": Confidence.DESIGN_ESTIMATE,
    }
    fields.update(overrides)
    return TargetStatement(**fields)


def test_severity_has_the_three_levels() -> None:
    """The one severity scale has the three levels every analysis uses."""
    assert [member.value for member in Severity] == ["info", "advice", "warning"]


def test_values_round_trip_under_their_plain_names() -> None:
    """Numbers that are not measurement rows stay readable by machines."""
    statement = _statement(values={"rms_per_axis_arcsec": 1.2, "target": "M 13", "missing": None})
    reloaded = TargetStatement.model_validate(statement.model_dump(by_alias=True, mode="json"))
    assert reloaded.values == {"rms_per_axis_arcsec": 1.2, "target": "M 13", "missing": None}


def test_a_full_statement_round_trips_through_json() -> None:
    """Every nested model survives a dump with aliases and a reload."""
    statement = _statement(
        severity=Severity.ADVICE,
        capture_setting=CaptureSetting(camera="ASI533MM", exposure_seconds=60.0, gain_setting=100),
        reason="Noise falls as the square root of the time.",
        proposed_action=ProposedAction(exposure_seconds=90.0, frame_count=20, refocus_every_degc=1.4),
        expected_effect=ExpectedEffect(noise_change_fraction=-0.29, description="Noise falls by about 29%."),
        evidence=[MeasurementReference(row_id=7)],
        limits=[QualityLimit(value=1.0, unit="arcsec", tier=LimitTier.DESIGN_ESTIMATE, derivation="x")],
        checklist_return=ChecklistReturn(
            item_id="a", ticked_at=datetime(2026, 10, 12, tzinfo=UTC), reason="Stars still wider."
        ),
    )
    dumped = statement.model_dump(by_alias=True, mode="json")
    assert dumped["actionClass"] == "capture_settings"
    assert dumped["proposedAction"]["refocusEveryDegc"] == pytest.approx(1.4)
    assert TargetStatement.model_validate(dumped) == statement


def test_a_warning_needs_a_severity_and_an_action_class() -> None:
    """A warning without a severity or an action class is refused."""
    with pytest.raises(ValidationError, match="severity"):
        _statement(kind=StatementKind.WARNING)
    with pytest.raises(ValidationError, match="action class"):
        _statement(kind=StatementKind.WARNING, severity=Severity.WARNING, action_class=None)
    assert _statement(kind=StatementKind.WARNING, severity=Severity.WARNING).severity == Severity.WARNING


def test_a_recommendation_needs_an_action_class() -> None:
    """A recommendation with no action class is refused."""
    with pytest.raises(ValidationError, match="action class"):
        _statement(action_class=None)


@pytest.mark.parametrize("kind", [StatementKind.CONSTRAINT, StatementKind.INSUFFICIENT_DATA])
def test_constraint_and_insufficient_data_have_no_action(kind: StatementKind) -> None:
    """These kinds refuse an action class and a proposed action."""
    assert _statement(kind=kind, action_class=None, reason="Seeing limits the detail.").action_class is None
    with pytest.raises(ValidationError, match="no action class"):
        _statement(kind=kind, reason="x")
    with pytest.raises(ValidationError, match="no action class"):
        _statement(kind=kind, action_class=None, reason="x", proposed_action=ProposedAction(frame_count=3))


def test_insufficient_data_must_say_what_is_missing() -> None:
    """An insufficient-data statement with an empty reason is refused."""
    with pytest.raises(ValidationError, match="reason"):
        _statement(kind=StatementKind.INSUFFICIENT_DATA, action_class=None, reason=" ")


def test_a_proposed_action_must_set_something_and_order_its_window() -> None:
    """An empty proposal and a backwards window are refused."""
    with pytest.raises(ValidationError, match="at least one"):
        ProposedAction()
    start = datetime(2026, 10, 24, 22, tzinfo=UTC)
    with pytest.raises(ValidationError, match="window_end"):
        ProposedAction(window_start=start, window_end=start)
    with pytest.raises(ValidationError):
        ProposedAction(exposure_seconds=0.0)
    assert (
        ProposedAction(windowStart=start, windowEnd=datetime(2026, 10, 25, 2, tzinfo=UTC)).window_start
        == start
    )


def test_needs_attention_count_and_severity_agree() -> None:
    """A worst severity exists exactly when the count is above zero."""
    assert NeedsAttention(warning_count=0).worst_severity is None
    assert NeedsAttention(warningCount=2, worstSeverity="warning").worst_severity == Severity.WARNING
    with pytest.raises(ValidationError):
        NeedsAttention(warning_count=0, worst_severity=Severity.INFO)
    with pytest.raises(ValidationError):
        NeedsAttention(warning_count=1)


def _rule(**overrides: object) -> QualityRule:
    """Build a valid recommendation rule, with any field replaced.

    Returns
    -------
    rule : `QualityRule`
        A valid rule.
    """
    fields: dict = {
        "name": "more_frames",
        "statement_kind": StatementKind.RECOMMENDATION,
        "action_class": ActionClass.CAPTURE_SETTINGS,
        "requires": [MetricRequirement(name="frame.star_width_moffat_arcsec", definition_version=1)],
        "register_entry": "WAY-D01",
        "confidence": Confidence.DESIGN_ESTIMATE,
        "wording": "Take {count} more frames.",
        "purpose": "Say when more frames still help.",
    }
    fields.update(overrides)
    return QualityRule(**fields)


def test_a_rule_round_trips_and_uses_aliases() -> None:
    """The rule dumps with camelCase aliases and loads back equal."""
    rule = _rule(computed_by="noise_growth")
    dumped = rule.model_dump(by_alias=True, mode="json")
    assert dumped["statementKind"] == "recommendation"
    assert dumped["requires"][0]["definitionVersion"] == 1
    assert dumped["computedBy"] == "noise_growth"
    assert QualityRule.model_validate(dumped) == rule


def test_rule_validators_match_the_statement_validators() -> None:
    """A rule follows the same checks as a statement."""
    with pytest.raises(ValidationError, match="severity"):
        _rule(statement_kind=StatementKind.WARNING)
    with pytest.raises(ValidationError, match="action class"):
        _rule(action_class=None)
    with pytest.raises(ValidationError, match="no action class"):
        _rule(statement_kind=StatementKind.CONSTRAINT)
    with pytest.raises(ValidationError):
        _rule(register_entry="")
    with pytest.raises(ValidationError):
        MetricRequirement(name="x", definition_version=0)
    assert _rule(statement_kind=StatementKind.INSUFFICIENT_DATA, action_class=None).action_class is None
