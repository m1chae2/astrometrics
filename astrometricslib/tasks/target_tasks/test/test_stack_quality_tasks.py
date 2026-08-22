"""Purpose: Unit tests for stack_quality_tasks's pure decision logic.

Description: Verifies resolve_filter_wfwhm_with_floor's loosening
ladder, is_stacked_fwhm_degraded's ratio comparison, and
is_rejected_fraction_significant's threshold comparison.
"""

from astrometricslib.tasks.target_tasks.stack_quality_tasks import (
    is_rejected_fraction_significant,
    is_stacked_fwhm_degraded,
    resolve_filter_wfwhm_with_floor,
)


def test_resolve_filter_wfwhm_no_filter_requested():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify no filter requested means no filter, never flagged loosened."""
    assert resolve_filter_wfwhm_with_floor(num_lights=40, requested_filter_wfwhm=None) == (None, False)


def test_resolve_filter_wfwhm_stays_at_requested_when_above_floor():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a large session keeps the requested percentage unchanged."""
    # 40 frames * 80% = 32, comfortably above the default floor of 5.
    effective, loosened = resolve_filter_wfwhm_with_floor(num_lights=40, requested_filter_wfwhm="80%")
    assert effective == "80%"
    assert loosened is False


def test_resolve_filter_wfwhm_loosens_one_step_when_below_floor():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a small session loosens 80% -> 90% when 80% drops below floor."""
    # 6 frames * 80% = 4.8 -> 5 (rounds to exactly the floor, so
    # let's use 5 frames instead)
    # 5 frames * 80% = 4 (below floor=5); 5 * 90% = 4.5 -> 4 (still
    # below); escalate to unfiltered.
    effective, loosened = resolve_filter_wfwhm_with_floor(
        num_lights=5, requested_filter_wfwhm="80%", minimum_surviving_frames=5
    )
    assert effective is None
    assert loosened is True


def test_resolve_filter_wfwhm_loosens_exactly_one_step_when_that_suffices():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify loosening stops at the first step that satisfies the floor."""
    # 7 frames * 80% = 5.6 -> 6 (below floor=7... use a case where
    # 80% fails but 90% passes)
    # 8 frames: 80% -> 6.4 -> 6 (below floor=7); 90% -> 7.2 -> 7
    # (meets floor=7).
    effective, loosened = resolve_filter_wfwhm_with_floor(
        num_lights=8, requested_filter_wfwhm="80%", minimum_surviving_frames=7
    )
    assert effective == "90%"
    assert loosened is True


def test_resolve_filter_wfwhm_falls_back_to_unfiltered_when_no_step_suffices():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify escalation to unfiltered when the loosest step is still low."""
    effective, loosened = resolve_filter_wfwhm_with_floor(
        num_lights=3, requested_filter_wfwhm="80%", minimum_surviving_frames=5
    )
    assert effective is None
    assert loosened is True


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
