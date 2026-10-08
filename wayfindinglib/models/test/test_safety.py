"""Purpose: Unit tests for environmental safety domain models.

Description: Verifies SafetyAssessment.permits_observing() treats only
SAFE as permissive -- UNKNOWN, UNSAFE, and MARGINAL all block, per the
fail-closed posture this subsystem applies throughout.
"""

from wayfindinglib.models.policy.safety import SafetyAssessment, SafetyRule, SafetyVerdict


def test_permits_observing_true_only_for_safe() -> None:
    """Verify SAFE is the only verdict that permits observing."""
    assert SafetyAssessment(verdict=SafetyVerdict.SAFE).permits_observing() is True


def test_permits_observing_false_for_unknown() -> None:
    """Verify UNKNOWN is treated exactly as unsafe."""
    assert SafetyAssessment(verdict=SafetyVerdict.UNKNOWN).permits_observing() is False


def test_permits_observing_false_for_unsafe() -> None:
    """Verify UNSAFE blocks observing."""
    assert SafetyAssessment(verdict=SafetyVerdict.UNSAFE).permits_observing() is False


def test_permits_observing_false_for_marginal() -> None:
    """Verify MARGINAL does not by itself permit observing."""
    assert SafetyAssessment(verdict=SafetyVerdict.MARGINAL).permits_observing() is False


def test_safety_rule_marginal_threshold_optional() -> None:
    """Verify a SafetyRule constructs with no marginal_threshold configured."""
    rule = SafetyRule(id="r1", measurement="wind_speed_kph", comparison="greater_than", unsafe_threshold=40.0)
    assert rule.marginal_threshold is None
    assert rule.staleness_bound_sec == 120


def test_safety_assessment_carries_triggering_rule() -> None:
    """Verify the triggering rule identifier is carried through."""
    assessment = SafetyAssessment(verdict=SafetyVerdict.UNSAFE, triggering_rule_id="r1")
    assert assessment.triggering_rule_id == "r1"
