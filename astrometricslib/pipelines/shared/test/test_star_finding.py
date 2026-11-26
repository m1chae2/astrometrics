"""Tests of the shared star finder and its two filters.

Small synthetic pictures with known stars check that the finder returns
them brightest first, that the isolation filter drops a close pair and keeps
a lone star, and that the saturation filter drops a star whose box is burnt
out, runs off the picture or holds empty pixels.
"""

import numpy as np

from astrometricslib.pipelines.shared.star_finding import (
    find_sources_above_background,
    isolated_sources,
    source_positions,
    unsaturated_boxes,
)


def _picture(stars: list[tuple[float, float, float]], size: int = 120) -> np.ndarray:
    """Draw round Gaussian stars on a flat, noisy sky of zero.

    Parameters
    ----------
    stars : `list` [`tuple` [`float`, `float`, `float`]]
        The x, y and peak height of each star.
    size : `int`, optional
        The picture's side, in pixels.

    Returns
    -------
    picture : `numpy.ndarray`
        The picture, with noise of 1.
    """
    yy, xx = np.mgrid[0:size, 0:size]
    picture = np.random.default_rng(2).normal(0.0, 1.0, (size, size))
    for x, y, height in stars:
        picture += height * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.5**2))
    return picture


def test_the_finder_gives_the_stars_brightest_first() -> None:
    """Two stars are found, the brighter one first."""
    picture = _picture([(30.0, 30.0, 200.0), (80.0, 70.0, 400.0)])
    sources = find_sources_above_background(
        picture, noise=1.0, expected_fwhm_px=3.5, threshold_sigma=10.0, roundness_range=None
    )

    positions = source_positions(sources)
    assert positions.shape == (2, 2)
    assert np.allclose(positions[0], (80.0, 70.0), atol=0.3)


def test_the_isolation_filter_drops_a_close_pair_and_keeps_a_lone_star() -> None:
    """Stars 11 px apart drop out at a 15 px isolation; a lone star stays."""
    picture = _picture([(30.0, 30.0, 300.0), (41.0, 30.0, 300.0), (90.0, 90.0, 300.0)])
    sources = find_sources_above_background(
        picture,
        noise=1.0,
        expected_fwhm_px=3.5,
        threshold_sigma=10.0,
        roundness_range=None,
        isolation_px=15.0,
    )

    positions = source_positions(sources)
    assert len(positions) == 1
    assert np.allclose(positions[0], (90.0, 90.0), atol=0.3)


def test_isolated_sources_handles_one_or_no_source() -> None:
    """A single source is isolated, and no source gives an empty answer."""
    assert isolated_sources(np.array([[1.0, 1.0]]), 5.0).tolist() == [True]
    assert isolated_sources(np.empty((0, 2)), 5.0).size == 0


def test_unsaturated_boxes_drops_burnt_out_edge_and_empty_stars() -> None:
    """Only the star with a whole, unsaturated, filled box is kept."""
    planes = np.full((2, 60, 60), 10.0)
    planes[1, 28:33, 18:23] = 100.0  # burnt out in the second plane only
    planes[0, 40:45, 40:45] = 0.0  # no data around the third star
    positions = np.array([[30.0, 15.0], [20.0, 30.0], [42.0, 42.0], [2.0, 30.0]])

    usable = unsaturated_boxes(planes, positions, 4, ceiling=50.0, floor=0.0)

    assert usable.tolist() == [True, False, False, False]
    assert unsaturated_boxes(planes[0], positions, 4, ceiling=50.0).tolist() == [True, True, True, False]
