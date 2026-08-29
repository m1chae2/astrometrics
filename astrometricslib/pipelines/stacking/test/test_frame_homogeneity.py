"""Purpose: Unit tests for gain homogeneity within a single stack.

Description: Verifies find_dominant_gain_subset's dominant-subset
selection.
"""

from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.stacking.frame_homogeneity import find_dominant_gain_subset


def _frame(iso="100", path="frame.fits"):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    return FrameRecord(path=path, iso=iso)


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
