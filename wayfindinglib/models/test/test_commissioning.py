"""Purpose: Unit tests for commissioning evidence domain models.

Description: Verifies CommissioningRun.all_passed() correctly combines
aborted status with per-observation pass/fail.
"""

from wayfindinglib.models.policy.commissioning import CommissioningObservation, CommissioningRun


def test_all_passed_true_when_every_observation_passes():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify all_passed() is True when every observation passed."""
    run = CommissioningRun(
        id="run1",
        phase=1,
        drill_name="phase1_device_state_survey",
        observations=[
            CommissioningObservation(
                criterion="c1", observed_value="ENABLED", expected="ENABLED", passed=True
            )
        ],
    )
    assert run.all_passed() is True


def test_all_passed_false_when_any_observation_fails():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify all_passed() is False when any single observation failed."""
    run = CommissioningRun(
        id="run1",
        phase=1,
        drill_name="phase1_device_state_survey",
        observations=[
            CommissioningObservation(
                criterion="c1", observed_value="ENABLED", expected="ENABLED", passed=True
            ),
            CommissioningObservation(
                criterion="c2", observed_value="OFFLINE", expected="ENABLED", passed=False
            ),
        ],
    )
    assert run.all_passed() is False


def test_all_passed_false_when_aborted_even_if_all_passed_so_far():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an aborted run never reports all_passed()."""
    run = CommissioningRun(
        id="run1",
        phase=1,
        drill_name="phase1_mount_command_drill",
        observations=[
            CommissioningObservation(criterion="c1", observed_value="20", expected=">=20", passed=True)
        ],
        aborted=True,
        abort_reason="operator stopped drill",
    )
    assert run.all_passed() is False


def test_all_passed_true_with_no_observations_and_not_aborted():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an empty, non-aborted run vacuously passes."""
    run = CommissioningRun(id="run1", phase=1, drill_name="phase1_device_state_survey")
    assert run.all_passed() is True
