"""Tests for trimming the noisy edges off a finished stack.

The synthetic stacks have flat sky and a known noise, with extra noise added in
strips along chosen edges, as fewer overlapping frames would make.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.stacking.post_processing import stack_crop as sc

SIZE = 1000
SKY = 1000.0
NOISE = 0.01


def make_stack(left_noisy: int = 0, bottom_noisy: int = 0, seed: int = 0) -> np.ndarray:
    """Build a flat sky whose first columns and rows are noisier.

    Parameters
    ----------
    left_noisy : `int`
        Number of columns at the left edge with 1.4 times the noise.
    bottom_noisy : `int`
        Number of rows at the start of the array with 1.4 times the noise.
    seed : `int`
        Seed of the noise.

    Returns
    -------
    stack : `numpy.ndarray`
        A square image with the given sky level.
    """
    scale = np.full((SIZE, SIZE), NOISE)
    scale[:, :left_noisy] *= 1.4
    scale[:bottom_noisy, :] *= 1.4
    return SKY * (1.0 + scale * np.random.default_rng(seed).standard_normal((SIZE, SIZE)))


def test_a_clean_stack_is_not_trimmed() -> None:
    """With no noisy edge, the whole image is kept."""
    box = sc.find_edge_crop_box(make_stack())

    assert (box.x0, box.x1, box.y0, box.y1) == (0, SIZE, 0, SIZE)


def test_noisy_edges_are_trimmed_and_the_stack_stays_square() -> None:
    """The result is square, clear of the noisy strips, and near the centre."""
    box = sc.find_edge_crop_box(make_stack(left_noisy=100, bottom_noisy=200))

    assert box.x1 - box.x0 == box.y1 - box.y0
    assert box.x0 >= 75
    assert box.y0 >= 175
    assert box.x1 <= SIZE
    assert box.y1 <= SIZE
    assert box.y1 - box.y0 >= SIZE - 250
    assert abs((box.x0 + box.x1) / 2 - SIZE / 2) <= abs(SIZE / 2 - (75 + SIZE) / 2) + 1


def test_the_square_is_placed_as_near_the_centre_as_the_clean_area_allows() -> None:
    """A wide clean area gives a centred square; a narrow one a fitted one."""
    wide = sc._same_shape_box(sc.CropBox(0, 1000, 300, 1000), 1000, 1000)
    narrow = sc._same_shape_box(sc.CropBox(400, 1000, 0, 1000), 1000, 1000)

    assert (wide.y0, wide.y1) == (300, 1000)
    assert wide.x1 - wide.x0 == 700
    assert 0 <= wide.x0 <= 300
    assert (narrow.x0, narrow.x1) == (400, 1000)
    assert narrow.y1 - narrow.y0 == 600


def test_a_non_square_stack_keeps_its_proportions() -> None:
    """A 2:1 image trimmed on one side is still 2:1."""
    box = sc._same_shape_box(sc.CropBox(100, 2000, 0, 1000), 2000, 1000)

    assert (box.x1 - box.x0) == 2 * (box.y1 - box.y0)
    assert box.x0 >= 100


def test_a_slightly_noisier_edge_is_kept() -> None:
    """An edge only 5% noisier than the middle is within the limit."""
    scale = np.full((SIZE, SIZE), NOISE)
    scale[:, :100] *= 1.05
    stack = SKY * (1.0 + scale * np.random.default_rng(3).standard_normal((SIZE, SIZE)))

    assert sc.find_edge_crop_box(stack).x0 == 0


def test_a_stack_that_is_mostly_noisy_edge_is_left_whole() -> None:
    """If a side would lose more than a quarter, something else is wrong."""
    box = sc.find_edge_crop_box(make_stack(left_noisy=400))

    assert box.x0 == 0


def test_a_blank_stack_is_left_whole() -> None:
    """With no sky to measure, nothing is trimmed."""
    box = sc.find_edge_crop_box(np.zeros((SIZE, SIZE)))

    assert (box.x0, box.x1, box.y0, box.y1) == (0, SIZE, 0, SIZE)


def test_cropping_a_file_trims_the_stack_and_its_rejection_map_and_moves_the_reference(
    tmp_path: Path,
) -> None:
    """Both files shrink alike and the sky-coordinate reference follows."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    rejection = tmp_path / "M_27_L_Stacked_RejMap.fits"
    header = fits.Header()
    header["CRPIX1"] = 500.0
    header["CRPIX2"] = 600.0
    data = make_stack(left_noisy=100, bottom_noisy=200).astype(np.float32)
    fits.writeto(stack, data, header)
    fits.writeto(rejection, np.ones((SIZE, SIZE), dtype=np.float32), header)

    box = sc.crop_stack_edges(str(stack))

    assert box is not None
    with fits.open(stack, memmap=False) as hdul:
        assert hdul[0].data.shape == (box.y1 - box.y0, box.x1 - box.x0)
        assert hdul[0].data.shape[0] == hdul[0].data.shape[1]
        assert hdul[0].header["CRPIX1"] == pytest.approx(500.0 - box.x0)
        assert hdul[0].header["CRPIX2"] == pytest.approx(600.0 - box.y0)
        assert hdul[0].data[0, 0] == pytest.approx(data[box.y0, box.x0])
    with fits.open(rejection, memmap=False) as hdul:
        assert hdul[0].data.shape == (box.y1 - box.y0, box.x1 - box.x0)


def test_cropping_a_clean_file_changes_nothing(tmp_path: Path) -> None:
    """A stack with clean edges is not rewritten."""
    stack = tmp_path / "M_27_L_Stacked.fits"
    fits.writeto(stack, make_stack().astype(np.float32))
    before = stack.stat().st_mtime_ns

    assert sc.crop_stack_edges(str(stack)) is None
    assert stack.stat().st_mtime_ns == before
