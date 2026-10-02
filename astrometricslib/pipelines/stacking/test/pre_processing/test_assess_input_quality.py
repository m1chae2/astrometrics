"""Purpose: Unit tests for the stacking input-quality judgement.

Description: The builder counts the frames the pre-checks set aside, words a
background split, copies the flat assessment, and turns every problem into
one flag reason. A clean input has no reasons.
"""

import pytest

from astrometricslib.models.quality_summary import ExcludedFrame
from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import (
    BACKGROUND_EXCLUSION_REASON,
    GAIN_EXCLUSION_REASON,
    assess_input_quality,
    describe_background_splits,
)

SPLIT = {
    "exposure_seconds": 60.0,
    "low_group_count": 8,
    "low_group_median": 1000.0,
    "high_group_count": 2,
    "high_group_median": 4000.0,
    "gap_ratio": 4.0,
}


def test_a_clean_input_has_no_flag_reasons() -> None:
    """No exclusions, split or flat issue leaves the input unflagged."""
    quality = assess_input_quality(10, 10, [], None, {})
    assert not quality.is_flagged
    assert quality.flag_reasons == []
    assert quality.flat_frame_count is None


def test_excluded_frames_are_counted_by_reason() -> None:
    """Gain and background exclusions are counted separately."""
    excluded = [
        ExcludedFrame(path="a.fits", reason=GAIN_EXCLUSION_REASON),
        ExcludedFrame(path="b.fits", reason=BACKGROUND_EXCLUSION_REASON),
        ExcludedFrame(path="c.fits", reason=BACKGROUND_EXCLUSION_REASON),
    ]
    quality = assess_input_quality(10, 7, excluded, None, {})
    assert quality.frames_excluded_for_gain == 1
    assert quality.frames_excluded_for_background == 2
    assert quality.frames_accepted == 7


def test_a_background_split_is_described_and_flagged() -> None:
    """A split is worded with its exposure length and gap."""
    quality = assess_input_quality(10, 10, [], SPLIT, {})
    assert quality.background_split_detected
    assert quality.flag_reasons == [f"background split: {quality.background_split_detail}"]
    assert "60 s frames: 8 frame(s) at background~1000" in quality.background_split_detail


def test_a_split_without_an_exposure_length_has_no_prefix() -> None:
    """The single-dictionary shape names no exposure length."""
    split = {key: value for key, value in SPLIT.items() if key != "exposure_seconds"}
    assert describe_background_splits(split).startswith("8 frame(s)")
    assert describe_background_splits(None) is None
    assert describe_background_splits([]) is None


def test_flat_issues_and_mismatches_become_reasons() -> None:
    """The flat assessment is copied and each issue is a reason."""
    diagnostics = {
        "flat_calibration": {
            "frame_count": 1,
            "noise_fraction": 0.045,
            "smoothing_sigma_pixels": 2.5,
            "issues": ["master flat noise is 4.49% from 1 frame(s)"],
        },
        "calibration_mismatch_flags": ["dark gain differs"],
    }
    quality = assess_input_quality(10, 10, [], None, diagnostics)
    assert quality.flat_smoothing_sigma_px == pytest.approx(2.5)
    assert "1 calibration metadata mismatch(es)" in quality.flag_reasons
    assert "flat calibration: master flat noise is 4.49% from 1 frame(s)" in quality.flag_reasons
