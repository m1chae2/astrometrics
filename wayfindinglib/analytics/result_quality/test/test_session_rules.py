"""Purpose: Tests for the rule table of the per-night analyses.

Description: Checks that the table has one row per thing the analyses can say,
that the rows fit the statement kinds, and that a statement built from a row
takes its kind, severity and action class from it.
"""

import pytest
from pydantic import ValidationError

from wayfindinglib.analytics.result_quality.all_rules import ALL_RULES
from wayfindinglib.analytics.result_quality.session_rules import SESSION_RULES, SESSION_RULES_BY_NAME
from wayfindinglib.models.quality.statements import ActionClass, Confidence, Severity, StatementKind
from wayfindinglib.session_analysis.rule_support import session_statement, sort_by_severity

EXPECTED_RULE_NAMES = {
    "guiding_insufficient_data",
    "recalibrate_guider",
    "check_guide_signal",
    "update_guide_optics_configuration",
    "guiding_above_limit",
    "exposure_length_limited",
    "capture_insufficient_data",
    "captures_not_in_library",
    "frames_missing_measurements",
    "frequent_aborted_captures",
    "sensor_temperature_drift",
    "spectral_star_clipped",
    "star_width_above_baseline",
    "stars_elongated",
    "sky_insufficient_data",
    "sky_region_poor",
    "minimum_altitude_suggested",
    "sky_coverage_gap",
}


def test_a_good_night_has_no_within_limits_statement() -> None:
    """No warning means within limits; a constraint is a hardware limit."""
    for name in ("guiding_within_limit", "capture_within_limits", "sky_within_limits"):
        assert name not in SESSION_RULES_BY_NAME


def test_the_table_has_one_row_for_each_thing_the_analyses_say() -> None:
    """Every former kind has a row, and all of them are in the one list."""
    assert {rule.name for rule in SESSION_RULES} == EXPECTED_RULE_NAMES
    assert len(SESSION_RULES) == len(EXPECTED_RULE_NAMES)
    assert all(rule in ALL_RULES for rule in SESSION_RULES)


def test_a_statement_takes_its_shape_from_its_row() -> None:
    """The kind, severity and action class are the row's, not the caller's."""
    statement = session_statement("recalibrate_guider", message="m", confidence=Confidence.PARTLY_MEASURED)
    assert statement.kind == StatementKind.WARNING
    assert statement.severity == Severity.WARNING
    assert statement.action_class == ActionClass.SESSION_PROCEDURE
    assert statement.rule == "recalibrate_guider"


def test_missing_data_needs_a_reason() -> None:
    """A statement about missing data without a reason is refused."""
    with pytest.raises(ValidationError):
        session_statement("sky_coverage_gap", message="m", confidence=Confidence.DESIGN_ESTIMATE)
    statement = session_statement(
        "sky_coverage_gap", message="m", reason="Image on more nights.", confidence=Confidence.DESIGN_ESTIMATE
    )
    assert statement.action_class is None


def test_an_unknown_rule_name_is_an_error() -> None:
    """A name that is not in the table is a bug in the caller."""
    with pytest.raises(KeyError):
        session_statement("not_a_rule", message="m", confidence=Confidence.MEASURED)


def test_statements_sort_warnings_first() -> None:
    """Warnings come before advice, and advice before information."""
    confidence = Confidence.PARTLY_MEASURED
    info = session_statement("guiding_insufficient_data", message="i", reason="r", confidence=confidence)
    advice = session_statement("guiding_above_limit", message="a", confidence=confidence)
    warning = session_statement("recalibrate_guider", message="w", confidence=confidence)
    assert sort_by_severity([info, advice, warning]) == [warning, advice, info]


def test_only_warning_rules_have_warning_severity() -> None:
    """A warning-severity row is a warning, so flags and counts agree."""
    for rule in SESSION_RULES:
        assert (rule.severity == Severity.WARNING) == (rule.statement_kind == StatementKind.WARNING), (
            rule.name
        )


def test_the_lookup_matches_the_table() -> None:
    """The lookup by name holds the same rows."""
    assert SESSION_RULES_BY_NAME == {rule.name: rule for rule in SESSION_RULES}
