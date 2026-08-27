"""Purpose: Unit tests for calibration-metadata and gain homogeneity checks.

Description: Verifies
is_calibration_gain_compatible/is_dark_calibration_metadata_compatible's
soft-flag gain/exposure comparison and find_dominant_gain_subset's
dominant-subset selection.
"""

from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.stacking.frame_homogeneity import (
    find_dominant_gain_subset,
    is_calibration_gain_compatible,
    is_dark_calibration_metadata_compatible,
)


def _frame(iso="100", path="frame.fits"):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    return FrameRecord(path=path, iso=iso)


def test_calibration_gain_compatible_matches():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify matching gain values are compatible."""
    assert is_calibration_gain_compatible(light_gain="100", master_gain="100")


def test_calibration_gain_incompatible_on_mismatch():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify any gain mismatch is flagged, independent of calibration type."""
    assert not is_calibration_gain_compatible(light_gain="100", master_gain="200")


def test_dark_calibration_metadata_compatible_matches_within_tolerance():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a small exposure difference at matching gain is accepted."""
    assert is_dark_calibration_metadata_compatible(
        light_exposure=120.0, light_gain="100", master_exposure=120.5, master_gain="100"
    )


def test_dark_calibration_metadata_incompatible_on_gain_mismatch():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify any gain mismatch is flagged regardless of exposure match."""
    assert not is_dark_calibration_metadata_compatible(
        light_exposure=120.0, light_gain="100", master_exposure=120.0, master_gain="200"
    )


def test_dark_calibration_metadata_incompatible_beyond_exposure_tolerance():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a dark's exposure difference beyond tolerance is flagged.

    Even at otherwise matching gain.
    """
    assert not is_dark_calibration_metadata_compatible(
        light_exposure=120.0,
        light_gain="100",
        master_exposure=125.0,
        master_gain="100",
        exposure_tolerance_seconds=1.0,
    )


def test_dark_calibration_metadata_compatible_ignores_bias_like_short_exposure_only_via_gain_check():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify bias/flat-style short exposures flag incompatible for darks.

    This documents why bias/flat masters must use
    is_calibration_gain_compatible instead -- a bias master's
    near-zero exposure would always fail the dark-specific exposure
    tolerance against a real light exposure, which is expected and
    correct for darks but would be a false positive if applied to
    bias/flat masters.
    """
    assert not is_dark_calibration_metadata_compatible(
        light_exposure=30.0, light_gain="0", master_exposure=3.2e-05, master_gain="0"
    )


def test_find_dominant_gain_subset_all_same_gain():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a fully homogeneous set returns everything as dominant."""
    frames = [_frame(iso="100") for _ in range(5)]
    dominant, excluded = find_dominant_gain_subset(frames)
    assert len(dominant) == 5
    assert excluded == []


def test_find_dominant_gain_subset_excludes_minority():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a minority-gain subset is excluded, not silently stacked."""
    frames = [_frame(iso="100") for _ in range(8)] + [_frame(iso="200") for _ in range(2)]
    dominant, excluded = find_dominant_gain_subset(frames)
    assert len(dominant) == 8
    assert all(f.iso == "100" for f in dominant)
    assert len(excluded) == 2
    assert all(f.iso == "200" for f in excluded)


def test_find_dominant_gain_subset_empty_input():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies an empty frame list doesn't raise."""
    dominant, excluded = find_dominant_gain_subset([])
    assert dominant == []
    assert excluded == []
