"""Purpose: Tests for the shared angle helpers.

Description: Checks `wrapped_ra_difference_deg`, which subtracts two Right
Ascension (RA) values and wraps the result into (-180, 180] degrees. The
checks cover pairs on both sides of RA = 0 deg, the +/-180 deg edges,
arrays, and the name still being importable from the asteroid detector.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.shared.angles import wrapped_ra_difference_deg


@pytest.mark.parametrize(
    ("ra1_deg", "ra2_deg", "expected_deg"),
    [
        (359.9, 0.1, -0.2),
        (0.1, 359.9, 0.2),
        (10.0, 350.0, 20.0),
        (350.0, 10.0, -20.0),
        (150.01, 150.0, 0.01),
        (0.0, 0.0, 0.0),
        (190.0, 0.0, -170.0),
        (180.0, 0.0, 180.0),
        (0.0, 180.0, 180.0),
        (-0.1, 0.1, -0.2),
        (360.1, 0.0, 0.1),
    ],
)
def test_wrapped_ra_difference_takes_the_short_way_around(
    ra1_deg: float, ra2_deg: float, expected_deg: float
) -> None:
    """Check the signed value near the wrap, away from it, and at the edges."""
    assert wrapped_ra_difference_deg(ra1_deg, ra2_deg) == pytest.approx(expected_deg, abs=1e-9)


def test_wrapped_ra_difference_edge_is_plus_180_never_minus_180() -> None:
    """Check that exactly half a circle gives +180 in both directions."""
    assert wrapped_ra_difference_deg(180.0, 0.0) == pytest.approx(180.0, abs=0.0)
    assert wrapped_ra_difference_deg(0.0, 180.0) == pytest.approx(180.0, abs=0.0)


def test_wrapped_ra_difference_returns_float_for_scalars() -> None:
    """Check that two floats give a plain Python float."""
    assert isinstance(wrapped_ra_difference_deg(359.9, 0.1), float)


def test_wrapped_ra_difference_accepts_arrays() -> None:
    """Check that the helper wraps every element of an array."""
    result = wrapped_ra_difference_deg(np.array([359.9, 10.0, 0.3]), 0.1)

    assert isinstance(result, np.ndarray)
    assert result == pytest.approx([-0.2, 9.9, 0.2], abs=1e-9)


def test_wrapped_ra_difference_accepts_two_arrays() -> None:
    """Check element-by-element subtraction of two arrays."""
    result = wrapped_ra_difference_deg(np.array([359.9, 10.0]), np.array([0.1, 350.0]))

    assert result == pytest.approx([-0.2, 20.0], abs=1e-9)


def test_wrapped_ra_difference_is_still_importable_from_the_detector() -> None:
    """Check that the detector module re-exports the same function."""
    from astrometricslib.pipelines.asteroid_detection import detection

    assert detection.wrapped_ra_difference_deg is wrapped_ra_difference_deg
