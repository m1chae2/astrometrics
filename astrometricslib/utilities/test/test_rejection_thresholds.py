"""Purpose: Unit tests for Chauvenet-criterion adaptive stack-rejection sigma.

Description: Verifies chauvenet_sigma's monotonicity, known reference
values, and input-validation boundaries. Also verifies rejection_bounds, which
adds a floor and a looser low limit for small stacks.
"""

import pytest

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.utilities.rejection_thresholds import chauvenet_sigma, rejection_bounds


def test_chauvenet_sigma_matches_known_reference_values() -> None:
    """Verifies chauvenet_sigma against independently-computed values.

    Reference values computed via
    sqrt(2) * scipy.special.erfcinv(1 / (2 * n)).
    """
    assert chauvenet_sigma(40) == pytest.approx(2.4977, abs=1e-3)
    assert chauvenet_sigma(45) == pytest.approx(2.5392, abs=1e-3)
    assert chauvenet_sigma(70) == pytest.approx(2.6901, abs=1e-3)


def test_chauvenet_sigma_increases_with_frame_count() -> None:
    """Verifies more frames yield a stricter (larger) rejection sigma.

    More samples make an extreme value more likely to occur by chance
    alone, so the threshold needs to rise with n to keep the expected
    false-rejection rate constant.
    """
    frame_counts = [5, 10, 20, 40, 70, 100, 200]
    sigmas = [chauvenet_sigma(n) for n in frame_counts]
    assert sigmas == sorted(sigmas)
    assert len(set(sigmas)) == len(sigmas)


def test_chauvenet_sigma_single_frame_is_valid() -> None:
    """Verifies n_frames=1 (the smallest meaningful stack) doesn't error."""
    sigma = chauvenet_sigma(1)
    assert sigma > 0


@pytest.mark.parametrize("invalid_n", [0, -1, -10])
def test_chauvenet_sigma_rejects_non_positive_frame_counts(invalid_n: int) -> None:
    """Verifies non-positive frame counts raise rather than misbehave."""
    with pytest.raises(InvalidArgumentError):
        chauvenet_sigma(invalid_n)


@pytest.mark.parametrize(
    ("n_frames", "expected_low", "expected_high", "expected_floor_applied"),
    [
        (5, 3.0, 2.5, True),
        (8, 3.0, 2.5, True),
        (15, 3.0, 2.5, True),
    ],
)
def test_rejection_bounds_use_the_floor_for_small_stacks(
    n_frames: int, expected_low: float, expected_high: float, expected_floor_applied: bool
) -> None:
    """Verifies stacks of 5, 8 and 15 frames get the floor as the high limit.

    The Chauvenet limit for these counts (1.64, 1.86 and 2.13) is below 2.5,
    so the floor sets the high limit and the low limit sits 0.5 above it.
    """
    bounds = rejection_bounds(n_frames, floor=2.5, low_extra=0.5)

    assert bounds.low == pytest.approx(expected_low)
    assert bounds.high == pytest.approx(expected_high)
    assert bounds.floor_applied is expected_floor_applied


def test_rejection_bounds_keep_the_chauvenet_limit_when_it_is_above_the_floor() -> None:
    """Verifies a 40-frame stack keeps its own limit and reports no floor.

    The Chauvenet limit for 40 frames is 2.4977, a hair below 2.5, so the
    floor still applies. At 45 frames the limit is 2.5392 and stays.
    """
    at_forty = rejection_bounds(40, floor=2.5, low_extra=0.5)
    at_forty_five = rejection_bounds(45, floor=2.5, low_extra=0.5)

    assert at_forty.high == pytest.approx(2.5)
    assert at_forty.floor_applied is True
    assert at_forty_five.high == pytest.approx(chauvenet_sigma(45))
    assert at_forty_five.low == pytest.approx(chauvenet_sigma(45) + 0.5)
    assert at_forty_five.floor_applied is False


def test_rejection_bounds_with_no_extra_are_symmetric() -> None:
    """Verifies a low_extra of 0 gives equal low and high limits."""
    bounds = rejection_bounds(5, floor=2.5, low_extra=0.0)

    assert bounds.low == bounds.high == pytest.approx(2.5)


def test_rejection_bounds_with_a_floor_below_the_chauvenet_limit_change_nothing() -> None:
    """Verifies a floor of 1 leaves the plain Chauvenet limit in place."""
    bounds = rejection_bounds(5, floor=1.0, low_extra=0.0)

    assert bounds.high == pytest.approx(chauvenet_sigma(5))
    assert bounds.floor_applied is False


@pytest.mark.parametrize(("floor", "low_extra"), [(0.0, 0.5), (-1.0, 0.5), (2.5, -0.1)])
def test_rejection_bounds_reject_invalid_settings(floor: float, low_extra: float) -> None:
    """Verifies a non-positive floor or a negative extra raises."""
    with pytest.raises(InvalidArgumentError):
        rejection_bounds(5, floor=floor, low_extra=low_extra)


def test_rejection_bounds_reject_non_positive_frame_counts() -> None:
    """Verifies zero frames raises, as chauvenet_sigma does."""
    with pytest.raises(InvalidArgumentError):
        rejection_bounds(0)
