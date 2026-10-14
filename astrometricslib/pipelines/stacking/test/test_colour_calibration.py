"""Tests for the star-based colour calibration of colour stacks.

Each test builds a small synthetic sky: Gaussian stars of known colour on
noise, seen through channels with known sensitivities and sky levels. The
calibration must find the sensitivities back from the stars alone.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.stacking.post_processing import colour_calibration

SIZE = 400
STAR_COUNT = 150
# How strongly each channel records the same light, and the sky each adds.
SENSITIVITY = np.array([0.55, 1.0, 0.62])
SKY = np.array([0.004, 0.008, 0.005])
NOISE = 0.0004


def make_colour_sky(seed: int = 0, star_count: int = STAR_COUNT) -> tuple[np.ndarray, np.ndarray]:
    """Make a colour image of stars through unequal channels.

    The stars' own colours are neutral on average (red and blue to green
    ratios scatter around 1 by 25 percent), the way a field of typical stars
    is.

    Returns
    -------
    image : `numpy.ndarray`
        The image, shape (3, SIZE, SIZE).
    true_colours : `numpy.ndarray`
        The stars' real red-to-green and blue-to-green ratios, shape (n, 2).
    """
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    image = np.zeros((3, SIZE, SIZE))
    colours = np.exp(rng.normal(0.0, 0.25, (star_count, 2)))
    margin = 20
    for index in range(star_count):
        cx, cy = rng.uniform(margin, SIZE - margin, 2)
        peak = 10 ** rng.uniform(-2.3, -1.0)
        shape = np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * 1.4**2))
        weights = np.array([colours[index, 0], 1.0, colours[index, 1]])
        image += peak * shape * (weights * SENSITIVITY)[:, None, None]
    image += SKY[:, None, None] + rng.normal(0.0, NOISE, image.shape)
    return image, colours


def test_the_gains_undo_the_channel_sensitivities() -> None:
    """The red and blue gains match what would make the median star neutral."""
    image, colours = make_colour_sky()

    calibration = colour_calibration.measure_gains(image)

    assert calibration is not None
    expected_red = 1.0 / (SENSITIVITY[0] / SENSITIVITY[1] * np.median(colours[:, 0]))
    expected_blue = 1.0 / (SENSITIVITY[2] / SENSITIVITY[1] * np.median(colours[:, 1]))
    assert calibration.red_gain == pytest.approx(expected_red, rel=0.05)
    assert calibration.blue_gain == pytest.approx(expected_blue, rel=0.05)
    assert calibration.star_count >= colour_calibration.MINIMUM_STARS


def test_the_sky_is_measured_per_channel() -> None:
    """The sky levels that are removed are the ones each channel was given."""
    image, _ = make_colour_sky()

    calibration = colour_calibration.measure_gains(image)

    assert calibration is not None
    assert calibration.sky_levels == pytest.approx(tuple(SKY), rel=0.1)


def test_calibrated_image_has_one_sky_level_and_neutral_median_star() -> None:
    """Calibration gives one sky level and a neutral median star."""
    image, _ = make_colour_sky(seed=1)

    calibrated, calibration = colour_calibration.calibrate_colours(image)

    assert calibration is not None
    sky = [np.median(channel) for channel in calibrated]
    assert sky[0] == pytest.approx(sky[1], rel=0.01)
    assert sky[2] == pytest.approx(sky[1], rel=0.01)
    again = colour_calibration.measure_gains(calibrated.astype(np.float64))
    assert again is not None
    assert again.red_gain == pytest.approx(1.0, abs=0.04)
    assert again.blue_gain == pytest.approx(1.0, abs=0.04)


def test_green_is_the_reference_and_is_not_scaled() -> None:
    """Green keeps its scale: only its sky level is moved to the common one."""
    image, _ = make_colour_sky(seed=2)

    calibrated, _ = colour_calibration.calibrate_colours(image)

    green_before = image[1] - np.median(image[1])
    green_after = calibrated[1] - np.median(calibrated[1])
    assert green_after == pytest.approx(green_before, abs=1e-6)


def test_too_few_stars_changes_nothing() -> None:
    """With almost no stars the image is returned as it was."""
    rng = np.random.default_rng(5)
    image = SKY[:, None, None] + rng.normal(0.0, NOISE, (3, SIZE, SIZE))

    calibrated, calibration = colour_calibration.calibrate_colours(image)

    assert calibration is None
    assert calibrated == pytest.approx(image.astype(np.float32))


def test_a_file_is_calibrated_and_keeps_its_header(tmp_path: Path) -> None:
    """The calibrated copy is written with the input's header."""
    image, _ = make_colour_sky(seed=3)
    source, target = tmp_path / "linear.fits", tmp_path / "colour.fits"
    header = fits.Header()
    header["OBJECT"] = "M 51"
    fits.writeto(source, image.astype(np.float32), header)

    assert colour_calibration.calibrate_colours_in_file(str(source), str(target)) is True

    with fits.open(target) as written:
        assert written[0].header["OBJECT"] == "M 51"
        assert written[0].data.shape == image.shape


def test_a_single_channel_file_is_refused(tmp_path: Path) -> None:
    """A mono image has no colours to balance, so nothing is written."""
    source, target = tmp_path / "mono.fits", tmp_path / "colour.fits"
    fits.writeto(source, np.zeros((SIZE, SIZE), dtype=np.float32))

    assert colour_calibration.calibrate_colours_in_file(str(source), str(target)) is False
    assert not target.exists()


def test_an_unreadable_file_is_refused(tmp_path: Path) -> None:
    """A file that is not FITS gives `False` and does not raise."""
    source = tmp_path / "broken.fits"
    source.write_bytes(b"not a fits file")

    assert colour_calibration.calibrate_colours_in_file(str(source), str(tmp_path / "out.fits")) is False
