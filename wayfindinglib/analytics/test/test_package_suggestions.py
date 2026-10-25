"""Purpose: Unit tests for the shared pieces of the package suggestions.

Description: Checks the evidence fingerprint (rounded values, the rule's
metric versions, no dependence on row ids), the source keys, the reading of a
target's noise (newest library stack only, exposure-group stacks and old
metric versions left out, unknown rather than zero when missing), and the
default noise goal. Uses in-memory rows, never real data.
"""

from datetime import UTC, datetime, timedelta

import pytest

from astrometricslib import (
    CaptureSetting,
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from wayfindinglib.analytics.package_suggestions import (
    DEFAULT_NOISE_GOAL,
    RANKING_SUGGESTIONS_PER_USE,
    capture_setting_key,
    evidence_fingerprint,
    package_fingerprint,
    ranking_fingerprint,
    ranking_source_key,
    read_target_noise,
    row_source_key,
)
from wayfindinglib.analytics.result_quality.rules import rule_by_name
from wayfindinglib.analytics.test.frame_counting_helpers import FakeLibrary
from wayfindinglib.models.quality.statements import ProposedAction

NOISE_METRIC = "stacking.empty_aperture_noise_normalized"
START = datetime(2026, 10, 24, 20, 0, tzinfo=UTC)


def _row(
    value: float | None,
    *,
    subject_id: str = "M 81/stack",
    version: int = 3,
    age_days: int = 0,
    row_id: int = 1,
) -> QualityMeasurement:
    """Build a stack noise row.

    Parameters
    ----------
    value : `float` or `None`
        The noise. `None` makes a not-measured row.
    subject_id : `str`, optional
        The stack's id.
    version : `int`, optional
        The metric definition version.
    age_days : `int`, optional
        How many days before `START` it was measured.
    row_id : `int`, optional
        The row id.

    Returns
    -------
    row : `QualityMeasurement`
        The row.
    """
    return QualityMeasurement(
        metric=NOISE_METRIC,
        definition_version=version,
        subject=QualitySubject(level=SubjectLevel.STACK, subject_id=subject_id, target_id="M 81"),
        status=MeasurementStatus.MEASURED if value is not None else MeasurementStatus.NOT_MEASURED,
        value=value,
        reason="" if value is not None else "no apertures",
        origin=MeasurementOrigin.MEASURED,
        run_id="run",
        measured_at=START - timedelta(days=age_days),
        row_id=row_id,
    )


def test_the_default_goal_and_the_ranking_count_are_the_registered_values() -> None:
    """The tuned values are those of register rows WAY-D83 and WAY-D85."""
    assert DEFAULT_NOISE_GOAL == pytest.approx(1.9e-4)
    assert RANKING_SUGGESTIONS_PER_USE == 3


def test_the_fingerprint_ignores_row_ids_and_small_count_changes_but_not_real_ones() -> None:
    """Evidence is the same for 118 and 121 frames and different for 200."""
    rule = rule_by_name("more_frames")
    base = evidence_fingerprint(
        rule, "more_frames", "recommendation", ProposedAction(exposure_seconds=60.0, frame_count=118)
    )
    same = evidence_fingerprint(
        rule, "more_frames", "recommendation", ProposedAction(exposure_seconds=60.2, frame_count=121)
    )
    different = evidence_fingerprint(
        rule, "more_frames", "recommendation", ProposedAction(exposure_seconds=60.0, frame_count=200)
    )
    assert base == same != different
    assert len(base) == 16


def test_the_fingerprint_depends_on_the_rule_and_the_kind() -> None:
    """The same action under another rule or kind is other evidence."""
    action = ProposedAction(exposure_seconds=60.0, frame_count=40)
    first = evidence_fingerprint(rule_by_name("more_frames"), "more_frames", "recommendation", action)
    assert first != evidence_fingerprint(rule_by_name("more_frames"), "more_frames", "warning", action)
    assert first != evidence_fingerprint(None, "other_rule", "recommendation", action)


def test_a_package_fingerprint_does_not_depend_on_row_order() -> None:
    """The package code is the same for the same rows in any order."""
    assert package_fingerprint(["a", "b"]) == package_fingerprint(["b", "a"])
    assert package_fingerprint(["a"]) != package_fingerprint(["a", "b"])


def test_source_keys_name_rule_target_and_setting() -> None:
    """A row key names rule, target and setting; ranking: use, night."""
    setting = CaptureSetting(
        camera="ASI533", telescope="RedCat", filter_name="L", exposure_seconds=60.0, gain_setting=120
    )
    assert capture_setting_key(setting) == "ASI533/RedCat/L/60/120/-/-"
    assert capture_setting_key(None) == "-"
    assert row_source_key("more_frames", "M 81", setting) == "more_frames|M 81|ASI533/RedCat/L/60/120/-/-"
    assert ranking_source_key("M 81", "imaging", "2026-10-24") == "ranking|M 81|imaging|2026-10-24"
    assert ranking_fingerprint("M 81", "imaging", "2026-10-24") != ranking_fingerprint(
        "M 81", "imaging", "2026-10-25"
    )


def test_the_noise_is_the_newest_library_stack_row_of_the_pinned_version() -> None:
    """Group stacks, old metric versions and not-measured rows are left out."""
    rows = [
        _row(5e-4, age_days=10, row_id=1),
        _row(3e-4, age_days=2, row_id=2),
        _row(1e-4, age_days=0, subject_id="M 81/groups/60s/stack", row_id=3),
        _row(1e-4, age_days=0, version=2, row_id=4),
        _row(None, age_days=0, row_id=5),
    ]
    noise = read_target_noise(FakeLibrary({}, rows), "M 81")
    assert noise is not None and (noise.value, noise.row_id) == (3e-4, 2)
    assert noise.measured_at == START - timedelta(days=2)


def test_a_target_with_no_usable_row_has_unknown_noise() -> None:
    """No row means unknown (`None`), never a number below the goal."""
    assert read_target_noise(FakeLibrary({}, []), "M 81") is None
