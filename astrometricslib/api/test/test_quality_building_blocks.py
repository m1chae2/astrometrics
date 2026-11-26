"""Tests for the measurement building blocks of `QualityRecords`.

The images are made here: a flat sky with known noise and Gaussian stars at
known places. Each test checks that the public method gives the answer of
the library function it wraps, takes a path or an array alike, and returns
plain Python numbers.
"""

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib import AppConfiguration
from astrometricslib.api.quality import QualityRecords
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.pipelines.shared.image_scaling import measure_sky_background
from astrometricslib.utilities.robust_statistics import robust_sigma, sigma_clipped_values

SKY = 1000.0
NOISE = 5.0
STAR_SIGMA = 1.5
STARS = ((40.0, 50.0), (120.0, 90.0), (80.0, 150.0), (160.0, 30.0))


def _image(tilt: float = 0.0) -> np.ndarray:
    """Make a 200 by 200 sky with four Gaussian stars.

    Parameters
    ----------
    tilt : `float`, optional
        How much the sky rises from the left edge to the right edge, as a
        share of the sky level.

    Returns
    -------
    image : `numpy.ndarray`
        The image, 2-D, in counts.
    """
    rng = np.random.default_rng(3)
    y, x = np.mgrid[0:200, 0:200].astype(float)
    image = SKY * (1.0 + tilt * (x / 199.0 - 0.5)) + rng.normal(0.0, NOISE, (200, 200))
    for star_x, star_y in STARS:
        image += 3000.0 * np.exp(-((x - star_x) ** 2 + (y - star_y) ** 2) / (2.0 * STAR_SIGMA**2))
    return image


@pytest.fixture
def records() -> QualityRecords:
    """Build a `QualityRecords` with no database behind it.

    Returns
    -------
    records : `QualityRecords`
        The sub-API under test.
    """
    return QualityRecords(AppConfiguration(), MagicMock())


@pytest.fixture
def image_path(tmp_path: Path) -> Path:
    """Write the test image to a FITS file.

    Returns
    -------
    path : `pathlib.Path`
        The file.
    """
    path = tmp_path / "field.fits"
    fits.writeto(path, _image().astype(np.float32))
    return path


def test_the_sky_of_a_path_and_of_an_array_is_the_library_measurement(
    records: QualityRecords, image_path: Path
) -> None:
    """Verify `measure_sky` gives `measure_sky_background`'s plain numbers."""
    from_path = records.measure_sky(str(image_path))
    from_array = records.measure_sky(fits.getdata(image_path))
    expected = measure_sky_background(fits.getdata(image_path).astype(np.float64))

    assert from_path is not None and from_array is not None and expected is not None
    assert from_path == from_array
    assert (from_path.level, from_path.noise) == pytest.approx(expected)
    assert from_path.level == pytest.approx(SKY, abs=1.0)
    assert from_path.noise == pytest.approx(NOISE, rel=0.1)
    assert type(from_path.level) is float


def test_a_missing_file_and_a_flat_list_are_refused(records: QualityRecords, tmp_path: Path) -> None:
    """Verify a missing path and a 1-D array raise the library's errors."""
    with pytest.raises(NotFoundError):
        records.measure_sky(str(tmp_path / "none.fits"))
    with pytest.raises(InvalidArgumentError):
        records.measure_sky(np.zeros(10))


def test_find_stars_gives_each_star_in_one_based_pixels(records: QualityRecords, image_path: Path) -> None:
    """Verify the found stars sit at the made ones, counted from 1."""
    found = records.find_stars(str(image_path), fwhm_px=3.5, threshold_sigma=10.0)

    assert found is not None
    assert found.star_count == len(STARS) == len(found.stars)
    positions = sorted((round(star.x - 1.0), round(star.y - 1.0)) for star in found.stars)
    assert positions == sorted((round(x), round(y)) for x, y in STARS)
    assert type(found.stars[0].flux) is float


def test_find_stars_refuses_a_width_of_zero(records: QualityRecords) -> None:
    """Verify a star width that is not above zero is refused."""
    with pytest.raises(InvalidArgumentError):
        records.find_stars(_image(), fwhm_px=0.0, threshold_sigma=5.0)


def test_star_widths_measures_the_made_star_width(records: QualityRecords) -> None:
    """Verify the width of the found stars is the made Gaussian's FWHM."""
    image = _image()
    found = records.find_stars(image, fwhm_px=3.5, threshold_sigma=10.0)
    assert found is not None

    widths = records.star_widths(image, [(star.x, star.y) for star in found.stars])

    assert len(widths) == 1
    assert widths[0] == pytest.approx(2.3548 * STAR_SIGMA, rel=0.15)


def test_star_widths_of_a_color_image_gives_one_width_per_channel(records: QualityRecords) -> None:
    """Verify a three-channel image gives three widths."""
    image = _image()
    positions = [(x + 1.0, y + 1.0) for x, y in STARS]

    widths = records.star_widths(np.stack([image, image, image]), positions)

    assert len(widths) == 3
    assert widths[0] == pytest.approx(widths[1])


def test_star_widths_with_no_stars_is_empty(records: QualityRecords) -> None:
    """Verify an empty star list gives no widths."""
    assert records.star_widths(_image(), []) == []


def test_a_tilted_sky_reports_its_gradient(records: QualityRecords) -> None:
    """Verify the measured tilt follows the made one."""
    flat = records.measure_sky_gradient(_image())
    tilted = records.measure_sky_gradient(_image(tilt=0.02))

    assert flat.gradient_fraction is not None and tilted.gradient_fraction is not None
    assert tilted.gradient_fraction > flat.gradient_fraction
    assert tilted.gradient_fraction == pytest.approx(0.02, abs=0.005)
    assert tilted.limit_fraction > 0.0


def test_sky_corners_reads_the_sky_map(records: QualityRecords, tmp_path: Path) -> None:
    """Verify a solved image gives four corners and an unsolved one none."""
    header = fits.Header()
    header.update({
        "CTYPE1": "RA---TAN",
        "CTYPE2": "DEC--TAN",
        "CRVAL1": 10.0,
        "CRVAL2": 20.0,
        "CRPIX1": 100.5,
        "CRPIX2": 100.5,
        "CDELT1": -0.001,
        "CDELT2": 0.001,
    })
    solved = tmp_path / "solved.fits"
    plain = tmp_path / "plain.fits"
    fits.writeto(solved, np.zeros((200, 200), dtype=np.float32), header)
    fits.writeto(plain, np.zeros((200, 200), dtype=np.float32))

    corners = records.sky_corners(str(solved))

    assert corners is not None and len(corners) == 4
    assert all(abs(ra - 10.0) < 0.2 and abs(dec - 20.0) < 0.2 for ra, dec in corners)
    assert records.sky_corners(str(plain)) is None


def test_a_stack_with_no_saved_star_list_has_no_solved_stars(
    records: QualityRecords, image_path: Path
) -> None:
    """Verify a stack the astrometry run never named gives an empty list."""
    assert records.solved_stars(str(image_path)) == []


def test_robust_sigma_and_sigma_clip_are_the_library_rules(records: QualityRecords) -> None:
    """Verify both give the answers of `robust_statistics` as plain numbers."""
    values = [1.0, 2.0, 3.0, 2.0, 1.0, 2.0, 50.0]

    assert records.robust_sigma(values) == pytest.approx(robust_sigma(values))
    kept = records.sigma_clip(values, clip_sigma=3.0, maximum_passes=5, minimum_count=3)
    expected = sigma_clipped_values(values, minimum_count=3, maximum_passes=5, clip_sigma=3.0)
    assert expected is not None
    assert kept == expected.tolist()
    assert 50.0 not in kept
    assert records.sigma_clip([1.0], clip_sigma=3.0, maximum_passes=5, minimum_count=3) is None
