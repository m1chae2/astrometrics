"""Purpose: Unit tests for stack_quality_tasks's pure decision logic.

Description: Verifies is_stacked_fwhm_degraded's ratio comparison and
is_rejected_fraction_significant's threshold comparison.
"""

from astrometricslib.pipelines.stacking.stack_quality import (
    is_rejected_fraction_significant,
    is_stacked_fwhm_degraded,
)


def test_is_stacked_fwhm_degraded_flags_worse_than_ratio():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a stacked FWHM well above the ratio is flagged as degraded."""
    assert is_stacked_fwhm_degraded(stacked_fwhm=6.0, median_input_fwhm=4.0, degradation_ratio=1.2)


def test_is_stacked_fwhm_degraded_accepts_within_ratio():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a stacked FWHM within the ratio is not flagged."""
    assert not is_stacked_fwhm_degraded(stacked_fwhm=4.5, median_input_fwhm=4.0, degradation_ratio=1.2)


def test_is_stacked_fwhm_degraded_handles_zero_median_safely():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a zero median input FWHM does not raise or false-flag."""
    assert not is_stacked_fwhm_degraded(stacked_fwhm=5.0, median_input_fwhm=0.0)


def test_is_rejected_fraction_significant_threshold():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the rejected-fraction significance threshold boundary."""
    assert is_rejected_fraction_significant(0.15)
    assert not is_rejected_fraction_significant(0.10)
