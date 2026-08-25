"""Purpose: Unit tests for the session background-homogeneity split detector.

Description: Verifies detect_background_split distinguishes a sharp
two-condition split from ordinary gradual drift or noise, using
patterns modeled on real sessions.
"""

from dataclasses import dataclass

from astrometricslib.tasks.target_tasks.background_homogeneity_tasks import (
    detect_background_split,
    find_dominant_background_subset,
)


def test_detect_background_split_flags_a_real_cloud_event_pattern():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies a pattern modeled on the real NGC 2403 cloud event is detected.

    15 frames at ~240-488 ADU, then 55 frames at ~2150-2360 ADU -- the real
    magnitude/shape of the NGC 2403 (2026-02-22) background step-change.
    """
    low_group = [240, 484, 484, 480, 484, 488, 480, 480, 480, 476, 476, 472, 472, 468, 468]
    high_group = [2360, 2356, 2344, 2300, 2292, 2292, 2280, 2272, 2264, 2248, 2244, 2236, 2228] * 4 + [
        2152,
        2200,
    ]
    result = detect_background_split(low_group + high_group)
    assert result is not None
    assert result["low_group_count"] == len(low_group)
    assert result["high_group_count"] == len(high_group)
    assert result["gap_ratio"] > 4.0


def test_detect_background_split_ignores_homogeneous_session():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a tight, homogeneous background range isn't flagged.

    Modeled on M 13's clean session.
    """
    backgrounds = [300, 304, 308, 312, 316, 320, 324, 328, 332, 336, 340, 344, 348, 320, 316]
    assert detect_background_split(backgrounds) is None


def test_detect_background_split_ignores_gradual_drift():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify wide but gradual, continuous background drift isn't flagged.

    Modeled on M 81: its real session ranged 244-4512 ADU across
    changing sky altitude, but the largest gap between consecutive
    sorted values was comparable to the typical spread (ratio 0.875)
    -- it's a smooth ramp, not a discrete jump.
    """
    backgrounds = [244 + i * 100 for i in range(45)]  # smooth, evenly-spaced ramp
    assert detect_background_split(backgrounds) is None


def test_detect_background_split_handles_too_few_frames():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify empty and single-frame inputs are handled without raising."""
    assert detect_background_split([]) is None
    assert detect_background_split([500.0]) is None


@dataclass
class _Frame:
    """Minimal stand-in for a FrameRecord's background fields."""

    path: str
    background_level: float | None


def test_a_lone_washed_out_frame_is_excluded_not_stacked():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the real M 42 pattern keeps the 22 good frames.

    22 frames at ~236 ADU and a single frame at ~2324 -- the split that
    cost M 42 its whole stack on 2026-08-25 when the outlier became the
    registration reference and no stars could be found in it.
    """
    frames = [_Frame(f"good_{i}.fits", 236 + i) for i in range(22)]
    outlier = _Frame("washed_out.fits", 2324.0)
    kept, excluded, summary = find_dominant_background_subset([*frames, outlier])

    assert [f.path for f in excluded] == ["washed_out.fits"]
    assert len(kept) == 22
    assert outlier not in kept
    assert summary is not None
    assert summary["high_group_count"] == 1


def test_a_homogeneous_session_keeps_every_frame():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify no frames are dropped when there is no split to act on."""
    frames = [_Frame(f"f_{i}.fits", 300 + i * 4) for i in range(15)]
    kept, excluded, summary = find_dominant_background_subset(frames)

    assert kept == frames
    assert excluded == []
    assert summary is None


def test_the_cloudy_majority_wins_over_a_clean_minority():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the larger group is kept even when it's the brighter sky.

    Homogeneity is the goal, not the lowest background -- a session that
    clouded over and stayed there should still stack its majority.
    """
    clean = [_Frame(f"clean_{i}.fits", 240 + i) for i in range(4)]
    cloudy = [_Frame(f"cloudy_{i}.fits", 2300 + i) for i in range(20)]
    kept, excluded, summary = find_dominant_background_subset([*clean, *cloudy])

    assert len(kept) == 20
    assert {f.path for f in excluded} == {f.path for f in clean}
    assert summary is not None


def test_unmeasured_frames_are_never_excluded():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a failed background measurement doesn't discard a frame.

    background_level is None only when measurement raised, which is not
    evidence the frame itself is bad.
    """
    frames = [_Frame(f"good_{i}.fits", 236 + i) for i in range(22)]
    unmeasured = _Frame("unmeasured.fits", None)
    outlier = _Frame("washed_out.fits", 2324.0)
    kept, excluded, _ = find_dominant_background_subset([*frames, unmeasured, outlier])

    assert unmeasured in kept
    assert [f.path for f in excluded] == ["washed_out.fits"]


def test_an_empty_frame_list_is_handled():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the empty case returns empty groups rather than raising."""
    assert find_dominant_background_subset([]) == ([], [], None)
