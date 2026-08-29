"""Purpose: Unit tests for record_divergence.

Description: Verifies divergence records are written for agreeing and
disagreeing comparisons alike, the sign of divergence_magnitude
reflects intended-minus-observed, and the three uncomparable
capabilities are rejected -- the cases
`Wayfinding_Library_Architecture.md` §2.4.11 calls out.
"""

import pytest

from wayfindinglib.models.policy.delegation import ObservatoryCapability
from wayfindinglib.tasks.execution_tasks.divergence_recording import record_divergence


def test_agreeing_comparison_is_within_tolerance():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a comparison within tolerance is recorded as within_tolerance."""
    record = record_divergence(
        "div-1",
        "session-1",
        "entry-1",
        ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
        "frame-1",
        intended_value=10.0,
        observed_value=10.3,
        tolerance=0.5,
        divergence_unit="arcsec",
    )
    assert record.within_tolerance is True
    assert record.divergence_magnitude == pytest.approx(-0.3)


def test_disagreeing_comparison_is_still_recorded():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a comparison outside tolerance is still written to the record."""
    record = record_divergence(
        "div-2",
        "session-1",
        "entry-1",
        ObservatoryCapability.AUTOGUIDING,
        "guide-step-1",
        intended_value=100.0,
        observed_value=50.0,
        tolerance=10.0,
        divergence_unit="ms",
    )
    assert record.within_tolerance is False
    assert record.divergence_magnitude == pytest.approx(50.0)


def test_divergence_magnitude_is_signed_intended_minus_observed():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify divergence_magnitude is signed, not an absolute value."""
    positive = record_divergence(
        "div-3",
        "session-1",
        None,
        ObservatoryCapability.AUTOFOCUS,
        "sweep-1",
        intended_value=5000.0,
        observed_value=4990.0,
        tolerance=50.0,
        divergence_unit="steps",
    )
    negative = record_divergence(
        "div-4",
        "session-1",
        None,
        ObservatoryCapability.AUTOFOCUS,
        "sweep-2",
        intended_value=4990.0,
        observed_value=5000.0,
        tolerance=50.0,
        divergence_unit="steps",
    )
    assert positive.divergence_magnitude == pytest.approx(10.0)
    assert negative.divergence_magnitude == pytest.approx(-10.0)


@pytest.mark.parametrize(
    "capability",
    [
        ObservatoryCapability.MOUNT_CONTROL,
        ObservatoryCapability.CAPTURE_ORCHESTRATION,
        ObservatoryCapability.OBSERVATORY_SAFETY,
    ],
)
def test_uncomparable_capabilities_are_rejected(capability):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify MOUNT_CONTROL/CAPTURE_ORCHESTRATION/OBSERVATORY_SAFETY raise."""
    with pytest.raises(ValueError, match="no shadowed counterpart"):
        record_divergence(
            "div-5",
            "session-1",
            None,
            capability,
            "input-1",
            intended_value=1.0,
            observed_value=1.0,
            tolerance=0.1,
            divergence_unit="arcsec",
        )


def test_optional_queued_package_id_may_be_none():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify queued_observation_package_id may be None at session level."""
    record = record_divergence(
        "div-6",
        "session-1",
        None,
        ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
        "frame-2",
        intended_value=1.0,
        observed_value=1.0,
        tolerance=0.1,
        divergence_unit="arcsec",
    )
    assert record.queued_observation_package_id is None


def test_tolerance_is_stored_on_the_record():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the tolerance a comparison was evaluated against is recorded."""
    record = record_divergence(
        "div-7",
        "session-1",
        None,
        ObservatoryCapability.AUTOGUIDING,
        "guide-step-2",
        intended_value=10.0,
        observed_value=9.0,
        tolerance=2.5,
        divergence_unit="ms",
    )
    assert record.tolerance == pytest.approx(2.5)


def test_converged_defaults_to_none():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify converged defaults to None when the caller does not supply it."""
    record = record_divergence(
        "div-8",
        "session-1",
        None,
        ObservatoryCapability.AUTOFOCUS,
        "sweep-3",
        intended_value=5000.0,
        observed_value=5000.0,
        tolerance=50.0,
        divergence_unit="steps",
    )
    assert record.converged is None


def test_converged_is_stored_when_supplied():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a supplied converged value is recorded on the record."""
    record = record_divergence(
        "div-9",
        "session-1",
        None,
        ObservatoryCapability.PLATE_SOLVE_ALIGNMENT,
        "frame-3",
        intended_value=1.0,
        observed_value=1.0,
        tolerance=0.1,
        divergence_unit="arcsec",
        converged=True,
    )
    assert record.converged is True
