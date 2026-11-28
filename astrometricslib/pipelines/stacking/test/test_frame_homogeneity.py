"""Purpose: Unit tests for gain homogeneity within a single stack.

Description: Verifies find_dominant_gain_subset's dominant-subset
selection.
"""

from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.stacking.pre_processing.frame_homogeneity import find_dominant_gain_subset


def _frame(gain: int | str = 100, path: str = "frame.fits") -> FrameRecord:
    """Build a frame record at one gain.

    Parameters
    ----------
    gain : `int` or `str`, optional
        The gain, as a number or as text an older record kept.
    path : `str`, optional
        The frame's path.

    Returns
    -------
    frame : `FrameRecord`
        The frame record.
    """
    return FrameRecord(path=path, gain=gain)


def test_find_dominant_gain_subset_all_same_gain() -> None:
    """Verify a fully homogeneous set returns everything as dominant."""
    frames = [_frame(gain=100) for _ in range(5)]
    dominant, excluded = find_dominant_gain_subset(frames)
    assert len(dominant) == 5
    assert excluded == []


def test_find_dominant_gain_subset_excludes_minority() -> None:
    """Verify a minority-gain subset is excluded, not silently stacked."""
    frames = [_frame(gain=100) for _ in range(8)] + [_frame(gain=200) for _ in range(2)]
    dominant, excluded = find_dominant_gain_subset(frames)
    assert len(dominant) == 8
    assert all(f.gain == 100 for f in dominant)
    assert len(excluded) == 2
    assert all(f.gain == 200 for f in excluded)


def test_find_dominant_gain_subset_empty_input() -> None:
    """Verifies an empty frame list doesn't raise."""
    dominant, excluded = find_dominant_gain_subset([])
    assert dominant == []
    assert excluded == []


def test_the_same_iso_written_two_ways_is_one_group() -> None:
    """Old records say 800.0 and newer ones say 800; they are one setting."""
    frames = [_frame(gain=800) for _ in range(6)] + [_frame(gain="800.0") for _ in range(2)]
    dominant, excluded = find_dominant_gain_subset(frames)
    assert len(dominant) == 8
    assert excluded == []
