"""Tests for the tuner of the longest plausible registration step."""

import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.foundation.tuning import TuningInputs, TuningSkippedError
from astrometricslib.pipelines.stacking.post_processing.registration_step_tuning import (
    REGISTRATION_STEP_TUNER,
    jump_threshold_px,
    step_limit_px,
)

SETUP = SetupConfig(name="Setup", camera_name="Camera", optic_name="Optic")


def test_jump_threshold_is_the_median_plus_robust_deviations() -> None:
    """A jump is a step above the median plus the robust deviations."""
    assert jump_threshold_px(2.0, 1.0, jump_sigmas=5.0, minimum_scale_px=0.1) == pytest.approx(
        2.0 + 5 * 1.4826
    )


def test_jump_threshold_uses_the_floor_for_a_pure_drift() -> None:
    """A stack with no spread uses the floor."""
    assert jump_threshold_px(0.4, 0.0, jump_sigmas=5.0, minimum_scale_px=0.1) == pytest.approx(0.9)


def test_limit_is_the_longest_threshold_times_the_factor() -> None:
    """The limit follows the stack that reaches the farthest."""
    assert step_limit_px([1.5, 9.6, 3.0], safety_factor=5.0) == pytest.approx(48.0)


def test_tuner_skips_without_the_library() -> None:
    """With no library service given, the tuner raises the skip error."""
    with pytest.raises(TuningSkippedError):
        REGISTRATION_STEP_TUNER.tune(TuningInputs(SETUP, None, None, {}))
