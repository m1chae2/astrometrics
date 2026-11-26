"""Purpose: Tests of the shared block medians and clipped plane fit.

Description: Checks how an image is cut into blocks (evenly, or in whole
blocks of a fixed size), that a block with too few trusted pixels is left
out, and that the clipped plane fit recovers a known tilt with a bright
object on it, clipping on both sides or only above.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.shared.binned_sky_plane import (
    block_medians,
    even_block_edges,
    fit_clipped_plane,
    least_squares_fit,
    whole_block_edges,
)


def test_even_edges_cover_every_pixel() -> None:
    """Even edges run from 0 to the full length, with no pixel left out."""
    edges = even_block_edges(10, 3)
    assert edges[0] == 0
    assert edges[-1] == 10
    assert len(edges) == 4


def test_whole_edges_leave_out_the_last_part_block() -> None:
    """Fixed-size blocks stop at the last whole block."""
    assert list(whole_block_edges(10, 4)) == [0, 4, 8]
    assert list(whole_block_edges(3, 4)) == [0]


def test_block_medians_ignore_stars_and_empty_pixels() -> None:
    """A block holds the median of its finite pixels; an empty one is NaN."""
    image = np.ones((8, 8))
    image[1, 1] = 1000.0
    image[4:, 4:] = np.nan
    medians = block_medians(image, even_block_edges(8, 2), even_block_edges(8, 2))

    assert medians[0, 0] == pytest.approx(1.0)
    assert np.isnan(medians[1, 1])


def test_a_block_with_too_few_trusted_pixels_is_left_out() -> None:
    """The valid share decides whether a block is measured."""
    image = np.arange(64, dtype=float).reshape(8, 8)
    valid = np.ones((8, 8), dtype=bool)
    valid[:4, :2] = False
    medians = block_medians(
        image, whole_block_edges(8, 4), whole_block_edges(8, 4), valid=valid, minimum_valid_fraction=0.9
    )

    assert np.isnan(medians[0, 0])
    assert medians[1, 1] == pytest.approx(float(np.median(image[4:, 4:])))


def test_least_squares_fit_recovers_a_line() -> None:
    """A straight line is fitted exactly, with no misfit."""
    x = np.linspace(-1.0, 1.0, 11)
    coefficients, rms = least_squares_fit([np.ones_like(x), x], 2.0 + 3.0 * x)

    assert coefficients == pytest.approx([2.0, 3.0])
    assert rms == pytest.approx(0.0, abs=1e-12)


def _tilted_points(bright: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Make a noisy tilted plane of points with a patch lifted by ``bright``.

    Returns
    -------
    first, second, values : `numpy.ndarray`
        The coordinates and values of 400 points.
    """
    first, second = (axis.ravel() for axis in np.meshgrid(np.linspace(-1, 1, 20), np.linspace(-1, 1, 20)))
    values = 5.0 + 0.3 * first - 0.2 * second + np.random.default_rng(4).normal(0.0, 0.01, first.size)
    values[(np.abs(first) < 0.3) & (np.abs(second) < 0.3)] += bright
    return first, second, values


@pytest.mark.parametrize("side", ["both", "above"])
def test_the_clipped_plane_drops_a_bright_patch(side: str) -> None:
    """A bright patch is clipped out, and the tilt is recovered."""
    first, second, values = _tilted_points(1.0)
    plane, kept = fit_clipped_plane(
        first,
        second,
        values,
        side=side,
        clip_sigmas=3.0,
        rounds=8,
        minimum_kept=10,  # type: ignore[arg-type]
    )

    assert plane == pytest.approx([5.0, 0.3, -0.2], abs=0.01)
    patch = (np.abs(first) < 0.3) & (np.abs(second) < 0.3)
    assert not kept[patch].any()
    assert kept[~patch].mean() > 0.95


def test_clipping_above_keeps_a_dark_patch() -> None:
    """Only bright points are dropped when clipping above; dark ones stay."""
    first, second, values = _tilted_points(-1.0)
    _plane, above = fit_clipped_plane(
        first, second, values, side="above", clip_sigmas=3.0, rounds=8, minimum_kept=10
    )
    _plane, both = fit_clipped_plane(
        first, second, values, side="both", clip_sigmas=3.0, rounds=8, minimum_kept=10
    )
    patch = (np.abs(first) < 0.3) & (np.abs(second) < 0.3)

    assert above[patch].all()
    assert not both[patch].any()


def test_the_last_good_set_is_kept_when_a_round_keeps_too_few() -> None:
    """A round that would keep fewer points than allowed is not taken."""
    first, second, values = _tilted_points(1.0)
    _plane, kept = fit_clipped_plane(
        first, second, values, side="both", clip_sigmas=3.0, rounds=8, minimum_kept=values.size
    )

    assert kept.all()
