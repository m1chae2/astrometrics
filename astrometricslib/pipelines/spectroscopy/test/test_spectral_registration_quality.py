"""Purpose: Unit tests for spectral (SA200) registration quality evaluation.

Description: Verifies flag_outliers' z-score thresholding and
evaluate_spectral_registration_quality's combined per-frame flagging,
using a pattern modeled on the real M 13 SA200 session where frames 13
and 15 were flagged for a zero-order star position jump correlated
with a matched-star-count drop.
"""

from astrometricslib.pipelines.spectroscopy.registration_quality import (
    evaluate_spectral_registration_quality,
    flag_outliers,
)


def test_flag_outliers_flags_deviation_in_either_direction():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a value far from the mean is flagged regardless of sign."""
    values = [10.0, 11.0, 9.0, 10.5, 9.5, 50.0]
    flags = flag_outliers(values, sigma_threshold=2.0)
    assert flags[-1] is True
    assert not any(flags[:-1])


def test_flag_outliers_low_is_bad_only_flags_below_mean():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify low_is_bad only flags values falling below the mean."""
    values = [10.0, 11.0, 9.0, 10.5, 9.5, -50.0]
    flags = flag_outliers(values, sigma_threshold=2.0, low_is_bad=True)
    assert flags[-1] is True
    assert not any(flags[:-1])


def test_flag_outliers_handles_too_few_values():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify flag_outliers handles empty and single-value inputs safely."""
    assert flag_outliers([], sigma_threshold=2.0) == []
    assert flag_outliers([5.0], sigma_threshold=2.0) == [False]


def test_evaluate_spectral_registration_quality_flags_zero_order_position_jump():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an isolated zero-order star position anomaly gets flagged.

    Modeled on the real M 13 SA200 session, where 2 of 40 frames showed
    the zero-order star briefly jumping to a different star (~35px)
    correlated with a matched-star-count drop. A single isolated
    anomalous frame inherently flags both the jump into it and the
    jump back out on the following frame (both are large
    consecutive-difference jumps), so this checks the anomalous frame
    is flagged without over-specifying its neighbors.
    """
    n = 20
    paths = [f"frame_{i}.fits" for i in range(n)]
    seq_frames = [{"nb_stars": 120, "rmse": 0.0043} for _ in range(n)]
    seq_frames[13]["nb_stars"] = 99  # real session's star-count drop on its anomalous frame

    zero_order_stars = [
        {"x": 2018.0 + i * 0.3, "y": 1563.0 + i * 0.2, "peak_to_background_ratio": 150.0} for i in range(n)
    ]
    # Frame 13 alone jumps ~35px to a different star, then frame 14
    # reverts to the normal trajectory.
    zero_order_stars[13] = {"x": 2018.0, "y": 1563.0 + 35.0, "peak_to_background_ratio": 128.7}

    flagged = evaluate_spectral_registration_quality(paths, seq_frames, zero_order_stars)
    flagged_paths = {f["path"] for f in flagged}

    assert "frame_13.fits" in flagged_paths
    assert "frame_0.fits" not in flagged_paths


def test_evaluate_spectral_registration_quality_flags_low_star_count():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a frame with a matched-star-count below minimum is flagged."""
    n = 15
    paths = [f"frame_{i}.fits" for i in range(n)]
    seq_frames = [{"nb_stars": 120, "rmse": 0.004} for _ in range(n)]
    seq_frames[5]["nb_stars"] = 3  # below MIN_MATCHED_STAR_PAIRS
    zero_order_stars = [{"x": 100.0, "y": 100.0, "peak_to_background_ratio": 150.0} for _ in range(n)]

    flagged = evaluate_spectral_registration_quality(paths, seq_frames, zero_order_stars)
    flagged_paths = {f["path"] for f in flagged}
    assert "frame_5.fits" in flagged_paths
    assert "low matched star count" in next(f["reason"] for f in flagged if f["path"] == "frame_5.fits")


def test_evaluate_spectral_registration_quality_handles_mismatched_lengths():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies misaligned input lists return no flags rather than raising."""
    assert evaluate_spectral_registration_quality(["a.fits"], [{"nb_stars": 1}], []) == []
