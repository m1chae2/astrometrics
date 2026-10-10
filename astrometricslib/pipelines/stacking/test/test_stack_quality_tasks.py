"""Purpose: Unit tests for stack_quality_tasks's pure decision logic.

Description: Verifies is_stacked_fwhm_degraded's ratio comparison and
is_rejected_fraction_significant's threshold comparison.
"""

import pytest

from astrometricslib.pipelines.stacking.post_processing.stack_quality import (
    is_rejected_fraction_significant,
    is_stacked_fwhm_degraded,
)


def test_is_stacked_fwhm_degraded_flags_worse_than_ratio() -> None:
    """Verify a stacked FWHM well above the ratio is flagged as degraded."""
    assert is_stacked_fwhm_degraded(stacked_fwhm=6.0, expected_fwhm=4.0, degradation_ratio=1.2)


def test_is_stacked_fwhm_degraded_accepts_within_ratio() -> None:
    """Verify a stacked FWHM within the ratio is not flagged."""
    assert not is_stacked_fwhm_degraded(stacked_fwhm=4.5, expected_fwhm=4.0, degradation_ratio=1.2)


def test_is_stacked_fwhm_degraded_handles_zero_median_safely() -> None:
    """Verify a zero median input FWHM does not raise or false-flag."""
    assert not is_stacked_fwhm_degraded(stacked_fwhm=5.0, expected_fwhm=0.0)


def test_is_rejected_fraction_significant_threshold() -> None:
    """Verify the rejected-fraction significance threshold boundary."""
    assert is_rejected_fraction_significant(0.15)
    assert not is_rejected_fraction_significant(0.10)


def test_expected_stack_fwhm_is_the_root_mean_square_of_the_inputs() -> None:
    """Verify equal frames predict their width, mixed frames a wider one."""
    from astrometricslib.pipelines.stacking.post_processing.stack_quality import expected_stack_fwhm

    assert expected_stack_fwhm([2.0, 2.0, 2.0]) == pytest.approx(2.0)
    assert expected_stack_fwhm([2.0, 4.0]) == pytest.approx(10.0**0.5)
    assert expected_stack_fwhm([]) is None


def test_a_stack_of_nights_with_different_seeing_is_not_called_degraded() -> None:
    """Verify blur from mixing sharp and soft frames is not a failure.

    Half the frames are 2 px wide and half 4 px. The stack is 3.2 px wide,
    which is wider than the median frame (3 px) by only 7%.
    """
    from astrometricslib.pipelines.stacking.post_processing.stack_quality import expected_stack_fwhm

    widths = [2.0] * 10 + [4.0] * 10
    expected = expected_stack_fwhm(widths)

    assert not is_stacked_fwhm_degraded(stacked_fwhm=3.2, expected_fwhm=expected)
    assert is_stacked_fwhm_degraded(stacked_fwhm=4.5, expected_fwhm=expected)
