"""Purpose: Unit tests for the session background-homogeneity split detector.

Description: Verifies detect_background_split distinguishes a sharp
two-condition split from ordinary gradual drift or noise, using
patterns modeled on real sessions.
"""

from astrometricslib.tasks.target_tasks.background_homogeneity_tasks import detect_background_split


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
