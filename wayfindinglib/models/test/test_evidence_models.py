"""Purpose: Unit tests for divergence, meridian flip, and safe-state models.

Description: Verifies DivergenceRecord's signed-magnitude field,
MeridianFlipOutcome's unresumed-by-default construction, and
SafeStateOutcome's failed_step tracking.
"""

import pytest

from wayfindinglib.models.policy.delegation import ObservatoryCapability
from wayfindinglib.models.session.divergence import DivergenceRecord
from wayfindinglib.models.session.meridian_flip import MeridianFlipOutcome
from wayfindinglib.models.session.safe_state import SafeStateOutcome


def test_divergence_record_signed_magnitude():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify divergence_magnitude is signed, intended-minus-observed."""
    record = DivergenceRecord(
        id="d1",
        observation_session_id="s1",
        capability=ObservatoryCapability.AUTOGUIDING,
        comparison_input_id="guide-step-123",
        intended_value=500.0,
        observed_value=520.0,
        divergence_magnitude=-20.0,
        divergence_unit="ms",
        tolerance=10.0,
        within_tolerance=True,
    )
    assert record.divergence_magnitude == pytest.approx(-20.0)


def test_divergence_record_defaults_no_queue_entry():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify queued_observation_package_id defaults to None."""
    record = DivergenceRecord(
        id="d1",
        observation_session_id="s1",
        capability=ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
        comparison_input_id="solve-456",
        intended_value=1.0,
        observed_value=1.0,
        divergence_magnitude=0.0,
        divergence_unit="arcsec",
        tolerance=5.0,
        within_tolerance=True,
    )
    assert record.queued_observation_package_id is None


def test_meridian_flip_outcome_defaults_unresumed():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a fresh MeridianFlipOutcome defaults to not resumed."""
    outcome = MeridianFlipOutcome(
        id="flip1", queued_observation_package_id="qp1", hour_angle_at_trigger_deg=1.0
    )
    assert outcome.resumed is False
    assert outcome.flip_completed is False
    assert outcome.failure_detail is None


def test_safe_state_outcome_tracks_failed_step():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify SafeStateOutcome records the first failed step distinctly."""
    outcome = SafeStateOutcome(
        trigger="unsafe_verdict",
        exposure_abandoned=True,
        guiding_stopped=True,
        mount_parked=False,
        failed_step="mount_parked",
    )
    assert outcome.mount_parked is False
    assert outcome.enclosure_closed is False
    assert outcome.failed_step == "mount_parked"
