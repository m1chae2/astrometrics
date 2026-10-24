"""Purpose: Tests for the plain-rule judge, ordering and attention count.

Description: Builds made-up stored measurements and checks that each plain
rule fires or stays quiet at its limit, that the evidence cites the exact
rows, that missing, not-measured and other-version rows give
``insufficient_data`` statements, and that ordering and the attention count
follow the rules every view shares.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from astrometricslib import (
    CaptureSetting,
    LimitTier,
    QualityLimit,
    QualityMeasurement,
    QualitySubject,
)
from wayfindinglib.analytics.result_quality.judge_results import (
    PLAIN_RULE_NAMES,
    judge_results,
    needs_attention,
    order_statements,
)
from wayfindinglib.analytics.result_quality.rules import RESULT_QUALITY_RULES
from wayfindinglib.models.quality.statements import (
    ActionClass,
    Severity,
    StatementKind,
    TargetStatement,
)

_NOW = datetime(2026, 10, 20, 22, 0, tzinfo=UTC)
_SETTING = CaptureSetting(
    camera="ASI533MM Pro", exposure_seconds=60.0, gain_setting=100, offset=50, binning=1
)

_next_row_id = 0


def _row(
    metric: str,
    value: float | None,
    *,
    level: str = "frame",
    version: int = 1,
    subject_id: str | None = None,
    setting_id: int | None = 1,
    status: str = "measured",
    reason: str = "",
    minutes: int = 0,
    check_outcome: str | None = None,
    check_limit: QualityLimit | None = None,
    variant: str | None = None,
) -> QualityMeasurement:
    """Build one stored measurement with a fresh row id.

    Returns
    -------
    row : `QualityMeasurement`
        The measurement.
    """
    global _next_row_id
    _next_row_id += 1
    return QualityMeasurement(
        metric=metric,
        definition_version=version,
        subject=QualitySubject(
            level=level,
            subject_id=subject_id or f"T/{metric}/{_next_row_id}",
            target_id="T",
            capture_setting_id=setting_id,
            variant=variant,
        ),
        status=status,
        value=value,
        reason=reason,
        check_outcome=check_outcome,
        check_limit=check_limit,
        origin="measured",
        run_id="run",
        measured_at=_NOW + timedelta(minutes=minutes),
        row_id=_next_row_id,
    )


def _judge(rows: list[QualityMeasurement], **keywords: Any) -> list[TargetStatement]:
    """Judge rows against setting 1.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        The statements.
    """
    return judge_results(rows, capture_settings={1: _SETTING}, **keywords)


def _of_rule(statements: list[TargetStatement], name: str) -> list[TargetStatement]:
    """Pick the statements of one rule.

    Returns
    -------
    picked : `list` [`TargetStatement`]
        The statements whose rule is `name`.
    """
    return [statement for statement in statements if statement.rule == name]


def test_every_plain_rule_has_a_judge() -> None:
    """Every rule with no ``computed_by`` has a judging function."""
    plain = {rule.name for rule in RESULT_QUALITY_RULES if rule.computed_by is None}
    assert plain == PLAIN_RULE_NAMES


def test_overexposed_fires_at_the_ceiling_and_proposes_a_shorter_exposure() -> None:
    """A peak at 2 times the room warns and proposes 60 s * 0.8 / 2 = 24 s."""
    row = _row("stacking.group_brightest_star_peak_fraction", 2.0, level="stack")
    statements = _of_rule(_judge([row]), "overexposed_setting")
    assert len(statements) == 1
    statement = statements[0]
    assert statement.kind == StatementKind.WARNING
    assert statement.severity == Severity.WARNING
    assert statement.action_class == ActionClass.CAPTURE_SETTINGS
    assert statement.proposed_action.exposure_seconds == pytest.approx(24.0)
    assert [reference.row_id for reference in statement.evidence] == [row.row_id]
    assert "60 s" in statement.message


def test_overexposed_is_quiet_below_the_ceiling() -> None:
    """A peak at 0.9 of the room makes no overexposure statement."""
    row = _row("stacking.group_brightest_star_peak_fraction", 0.9, level="stack")
    assert _of_rule(_judge([row]), "overexposed_setting") == []


def test_assumed_peak_says_so_in_the_reason() -> None:
    """A peak estimated from the star's wings is labeled as assumed."""
    row = _row(
        "stacking.group_brightest_star_peak_fraction",
        3.0,
        level="stack",
        status="assumed",
        reason="estimated from the wings",
    )
    statement = _of_rule(_judge([row]), "overexposed_setting")[0]
    assert "assumed" in statement.reason
    assert "estimated from the wings" in statement.reason


def test_underexposed_proposes_a_longer_exposure() -> None:
    """Sky 1.5 and read noise 3 need (3/0.5)^2 = 36 times the exposure."""
    rows = [
        _row("frame.sky_noise_e", 1.5, version=2),
        _row("frame.read_noise_e", 3.0, status="assumed", reason="profile value"),
    ]
    statement = _of_rule(_judge(rows), "underexposed_setting")[0]
    assert statement.kind == StatementKind.RECOMMENDATION
    assert statement.proposed_action.exposure_seconds == pytest.approx(60.0 * 36.0)
    assert "profile value" in statement.reason


def test_underexposed_is_quiet_when_sky_dominates() -> None:
    """A sky noise of 10 against read noise of 3 makes no statement."""
    rows = [_row("frame.sky_noise_e", 10.0, version=2), _row("frame.read_noise_e", 3.0)]
    assert _of_rule(_judge(rows), "underexposed_setting") == []


def test_frame_readings_use_the_median_over_frames() -> None:
    """The reading of a frame metric is the median over the frames."""
    rows = [_row("frame.star_elongation", value) for value in (1.0, 1.5, 1.6)]
    statement = _of_rule(_judge(rows), "trailed_stars")[0]
    assert "1.50" in statement.message
    assert len(statement.evidence) == 3


def test_only_the_newest_row_of_a_subject_counts() -> None:
    """A re-measurement of the same frame replaces the older row."""
    old = _row("frame.star_elongation", 1.6, subject_id="T/f1", minutes=0)
    new = _row("frame.star_elongation", 1.0, subject_id="T/f1", minutes=5)
    assert _of_rule(_judge([old, new]), "trailed_stars") == []


def test_offset_rule_follows_the_stored_self_check() -> None:
    """A failed stored check warns and cites the limit the check used."""
    limit = QualityLimit(
        value=0.2, unit="fraction", tier=LimitTier.VALIDATED_DATASET, derivation="d", registerEntry="STK-22"
    )
    failed = _row(
        "stacking.gate_raw_frame_clipping", 0.35, level="stack", check_outcome="failed", check_limit=limit
    )
    statement = _of_rule(_judge([failed]), "offset_too_low")[0]
    assert statement.kind == StatementKind.WARNING
    assert limit in statement.limits
    passed = _row(
        "stacking.gate_raw_frame_clipping", 0.01, level="stack", check_outcome="passed", check_limit=limit
    )
    assert _of_rule(_judge([passed]), "offset_too_low") == []


def test_flat_noise_limits_stack_counts_flats_to_take() -> None:
    """A flat adding 30% of stack noise asks for more flats."""
    rows = [
        _row("stacking.flat_noise_in_aperture_stack_unit", 0.3, level="stack"),
        _row("stacking.empty_aperture_noise_stack_unit", 1.0, level="stack", version=3),
        _row("stacking.flat_frame_count", 20.0, level="stack"),
    ]
    statement = _of_rule(_judge(rows), "flat_noise_limits_stack")[0]
    # Variance share 0.09, aim 0.01: 20 * 0.09 / 0.01 = 180 flats, so 160 more.
    assert statement.proposed_action.frame_count == 160
    assert statement.proposed_action.calibration_frame_type == "flat"
    assert statement.expected_effect.noise_change_fraction < 0
    assert len(statement.evidence) == 3


def test_master_flat_noisy_uses_the_register_limit() -> None:
    """A master flat at 1% noise from 10 flats needs 40 flats, 30 more."""
    rows = [
        _row("stacking.flat_noise_fraction", 0.01, level="stack"),
        _row("stacking.flat_frame_count", 10.0, level="stack"),
    ]
    statement = _of_rule(_judge(rows), "master_flat_noisy")[0]
    assert statement.proposed_action.frame_count == 30


def test_missing_metric_gives_insufficient_data_that_names_it() -> None:
    """With no rows, each plain rule says what is missing and how to get it."""
    statements = _judge([])
    assert {statement.rule for statement in statements} == PLAIN_RULE_NAMES
    assert all(statement.kind == StatementKind.INSUFFICIENT_DATA for statement in statements)
    overexposed = _of_rule(statements, "overexposed_setting")[0]
    assert "stacking.group_brightest_star_peak_fraction" in overexposed.reason
    assert "Stack the target again" in overexposed.reason


def test_other_definition_version_is_not_used() -> None:
    """A row of another definition version is refused; remeasure."""
    row = _row("frame.star_elongation", 2.0, version=9)
    statement = _of_rule(_judge([row]), "trailed_stars")[0]
    assert statement.kind == StatementKind.INSUFFICIENT_DATA
    assert "Remeasure" in statement.reason
    assert [reference.row_id for reference in statement.evidence] == [row.row_id]


def test_not_measured_row_gives_its_reason() -> None:
    """A not-measured row is not used as a number; its reason is passed on."""
    row = _row(
        "stacking.group_brightest_star_peak_fraction",
        None,
        level="stack",
        status="not_measured",
        reason="estimate beyond the checked range",
    )
    statement = _of_rule(_judge([row]), "overexposed_setting")[0]
    assert statement.kind == StatementKind.INSUFFICIENT_DATA
    assert "estimate beyond the checked range" in statement.reason


def test_subset_stack_rows_are_ignored() -> None:
    """Rows of a subset stack never stand in for the library stack."""
    row = _row("stacking.group_brightest_star_peak_fraction", 5.0, level="stack", variant="subset-a")
    statement = _of_rule(_judge([row]), "overexposed_setting")[0]
    assert statement.kind == StatementKind.INSUFFICIENT_DATA


def test_stack_rows_join_a_setting_through_the_subject_map() -> None:
    """Stack rows with no setting id use the caller's subject map."""
    row = _row(
        "stacking.group_brightest_star_peak_fraction", 2.0, level="stack", setting_id=None, subject_id="T/g60"
    )
    statements = _judge([row], subject_capture_settings={"T/g60": 1})
    statement = _of_rule(statements, "overexposed_setting")[0]
    assert statement.kind == StatementKind.WARNING
    assert statement.capture_setting == _SETTING


def test_limits_override_replaces_the_rule_limits() -> None:
    """A caller's limit changes when the rule fires."""
    rows = [_row("frame.star_elongation", 1.1)]
    assert _of_rule(_judge(rows), "trailed_stars") == []
    tighter = QualityLimit(value=1.05, unit="", tier=LimitTier.OWN_BASELINE, derivation="own best nights")
    statements = _judge(rows, limits_override={"trailed_stars": [tighter]})
    assert _of_rule(statements, "trailed_stars")[0].kind == StatementKind.WARNING


def test_row_without_row_id_is_refused() -> None:
    """A measurement that was not read back from the store cannot be cited."""
    row = _row("frame.star_elongation", 2.0).model_copy(update={"row_id": None})
    with pytest.raises(ValueError, match="row_id"):
        _judge([row])


def test_ordering_puts_warnings_first_and_missing_data_last() -> None:
    """Warnings come first, then recommendations, then missing data."""
    rows = [
        _row("stacking.group_brightest_star_peak_fraction", 2.0, level="stack"),
        _row("frame.star_elongation", 1.5),
        _row("frame.sky_noise_e", 1.5, version=2),
        _row("frame.read_noise_e", 3.0),
    ]
    statements = _judge(rows)
    kinds = [statement.kind for statement in statements]
    assert kinds == sorted(kinds, key=["warning", "recommendation", "constraint", "insufficient_data"].index)
    assert statements[0].rule == "overexposed_setting"
    assert statements[1].rule == "trailed_stars"
    assert statements == order_statements(reversed(statements))


def test_needs_attention_counts_warnings_and_finds_the_worst() -> None:
    """The count is of warnings only; the worst severity is the most severe."""
    rows = [
        _row("stacking.group_brightest_star_peak_fraction", 2.0, level="stack"),
        _row("frame.star_elongation", 1.5),
    ]
    attention = needs_attention(_judge(rows))
    assert attention.warning_count == 2
    assert attention.worst_severity == Severity.WARNING
    assert needs_attention([]).warning_count == 0
    assert needs_attention([]).worst_severity is None
