"""Purpose: Unit tests for Chauvenet-criterion adaptive stack-rejection sigma.

Description: Verifies chauvenet_sigma's monotonicity, known reference
values, and input-validation boundaries.
"""

import pytest

from astrometricslib.pipelines.stacking.rejection_thresholds import chauvenet_sigma


def test_chauvenet_sigma_matches_known_reference_values():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies chauvenet_sigma against independently-computed values.

    Reference values computed via
    sqrt(2) * scipy.special.erfcinv(1 / (2 * n)).
    """
    assert chauvenet_sigma(40) == pytest.approx(2.4977, abs=1e-3)
    assert chauvenet_sigma(45) == pytest.approx(2.5392, abs=1e-3)
    assert chauvenet_sigma(70) == pytest.approx(2.6901, abs=1e-3)


def test_chauvenet_sigma_increases_with_frame_count():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies more frames yield a stricter (larger) rejection sigma.

    More samples make an extreme value more likely to occur by chance
    alone, so the threshold needs to rise with n to keep the expected
    false-rejection rate constant.
    """
    frame_counts = [5, 10, 20, 40, 70, 100, 200]
    sigmas = [chauvenet_sigma(n) for n in frame_counts]
    assert sigmas == sorted(sigmas)
    assert len(set(sigmas)) == len(sigmas)


def test_chauvenet_sigma_single_frame_is_valid():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies n_frames=1 (the smallest meaningful stack) doesn't error."""
    sigma = chauvenet_sigma(1)
    assert sigma > 0


@pytest.mark.parametrize("invalid_n", [0, -1, -10])
def test_chauvenet_sigma_rejects_non_positive_frame_counts(invalid_n):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verifies non-positive frame counts raise rather than misbehave."""
    with pytest.raises(ValueError):
        chauvenet_sigma(invalid_n)
