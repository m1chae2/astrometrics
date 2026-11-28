"""Tests for the measurement building blocks, `astrometrics.quality.blocks`.

The star widths by color, ``quality.detections.measure_widths``, are tested
here too, with the same made images.

The images are made here: a flat sky with known noise and Gaussian stars at
known places. Each test checks that the public method gives the answer of
the library function it wraps, takes a path or an array alike, returns plain
Python numbers in 1-based pixels, and refuses a raw color mosaic unless asked
to debayer it.
"""

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib import AppConfiguration
from astrometricslib.api._quality_building_blocks import QualityBlocks, QualityDetections
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
def records() -> QualityBlocks:
    """Build the blocks of a `QualityRecords` with no database behind it.

    Returns
    -------
    blocks : `QualityBlocks`
        The namespace under test.
    """
    return QualityRecords(AppConfiguration(), MagicMock()).blocks


@pytest.fixture
def detections() -> QualityDetections:
    """Build the detections object of a `QualityRecords`.

    Returns
    -------
    detections : `QualityDetections`
        The namespace that measures star widths by color.
    """
    return QualityRecords(AppConfiguration(), MagicMock()).detections


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
    records: QualityBlocks, image_path: Path
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


def test_a_missing_file_and_a_flat_list_are_refused(records: QualityBlocks, tmp_path: Path) -> None:
    """Verify a missing path and a 1-D array raise the library's errors."""
    with pytest.raises(NotFoundError):
        records.measure_sky(str(tmp_path / "none.fits"))
    with pytest.raises(InvalidArgumentError):
        records.measure_sky(np.zeros(10))


def test_find_stars_gives_each_star_in_one_based_pixels(records: QualityBlocks, image_path: Path) -> None:
    """Verify the found stars sit at the made ones, counted from 1."""
    for finder in ("astrometry", "daofind"):
        found = records.measure_stars(str(image_path), finder=finder, fwhm_px=3.5, threshold_sigma=10.0)

        assert found is not None and found.finder == finder
        assert found.star_count == len(STARS) == len(found.stars)
        positions = sorted((round(star.x - 1.0), round(star.y - 1.0)) for star in found.stars)
        assert positions == sorted((round(x), round(y)) for x, y in STARS)
        assert type(found.stars[0].flux) is float
        assert found.kernel_fwhm_px == pytest.approx(3.5)


def test_the_default_finder_is_the_astrometry_finder(records: QualityBlocks, image_path: Path) -> None:
    """Verify the default search is the astrometry run's."""
    found = records.measure_stars(str(image_path))

    assert found is not None
    assert found.finder == "astrometry"
    assert found.sky_level is None
    assert found.star_count == len(STARS)
    assert found.threshold_sigma == pytest.approx(5.0)


def test_detect_point_sources_is_the_astrometry_search(image_path: Path) -> None:
    """Verify the script call gives the block's answer, in 1-based pixels."""
    from astrometricslib.api.script_calls import detect_point_sources

    data = fits.getdata(image_path).astype(np.float64)
    pipelines = MagicMock()
    pipelines._config = AppConfiguration()

    found = detect_point_sources(pipelines, data, threshold_sigma=10.0, fwhm=3.5)
    expected = QualityBlocks().measure_stars(data, fwhm_px=3.5, threshold_sigma=10.0)

    assert expected is not None
    assert found == expected


def test_find_stars_refuses_a_width_of_zero(records: QualityBlocks) -> None:
    """Verify a star width that is not above zero is refused."""
    with pytest.raises(InvalidArgumentError):
        records.measure_stars(_image(), fwhm_px=0.0, threshold_sigma=5.0)
    with pytest.raises(InvalidArgumentError):
        records.measure_stars(_image(), finder="sextractor")  # type: ignore[arg-type]


def test_star_widths_measures_the_made_star_width(
    records: QualityBlocks, detections: QualityDetections
) -> None:
    """Verify the width of the found stars is the made Gaussian's FWHM."""
    image = _image()
    found = records.measure_stars(image, fwhm_px=3.5, threshold_sigma=10.0)
    assert found is not None

    widths = detections.measure_widths(image, [(star.x, star.y) for star in found.stars])

    assert len(widths.widths_px) == 1
    assert widths.widths_px[0] == pytest.approx(2.3548 * STAR_SIGMA, rel=0.15)
    assert widths.estimator == "preview channel second moment"
    assert "widths_on_scale_px" not in widths.model_dump()


def test_star_widths_of_a_color_image_gives_one_width_per_channel(detections: QualityDetections) -> None:
    """Verify a three-channel image gives three widths."""
    image = _image()
    positions = [(x + 1.0, y + 1.0) for x, y in STARS]

    widths = detections.measure_widths(np.stack([image, image, image]), positions).widths_px

    assert len(widths) == 3
    assert widths[0] == pytest.approx(widths[1])


def test_star_widths_with_no_stars_is_empty(detections: QualityDetections) -> None:
    """Verify an empty star list gives no widths."""
    assert detections.measure_widths(_image(), []).widths_px == []


def test_a_tilted_sky_reports_its_gradient(records: QualityBlocks) -> None:
    """Verify the measured tilt follows the made one."""
    flat = records.measure_sky_gradient(_image())
    tilted = records.measure_sky_gradient(_image(tilt=0.02))

    assert flat.gradient_fraction is not None and tilted.gradient_fraction is not None
    assert tilted.gradient_fraction > flat.gradient_fraction
    assert tilted.gradient_fraction == pytest.approx(0.02, abs=0.005)
    assert tilted.limit_fraction > 0.0


def test_sky_corners_reads_the_sky_map(records: QualityBlocks, tmp_path: Path) -> None:
    """Verify a solved image gives four corners and an unsolved one none."""
    header = fits.Header()
    header.update({
        "PLTSOLVD": True,
        "RADESYS": "ICRS",
        "EQUINOX": 2000.0,
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

    corners = records.measure_sky_corners(str(solved))

    assert corners is not None and len(corners.corners) == 4
    assert all(abs(ra - 10.0) < 0.2 and abs(dec - 20.0) < 0.2 for ra, dec in corners.corners)
    assert (corners.radesys, corners.equinox) == ("ICRS", 2000.0)
    assert records.measure_sky_corners(str(plain)) is None


def test_pointing_cards_alone_give_no_sky_corners(records: QualityBlocks, tmp_path: Path) -> None:
    """Verify a sky map that is no plate solution gives no corners."""
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
    pointed = tmp_path / "pointed.fits"
    fits.writeto(pointed, np.zeros((200, 200), dtype=np.float32), header)

    assert records.measure_sky_corners(str(pointed)) is None


def test_robust_sigma_and_clipping_are_the_library_rules(records: QualityBlocks) -> None:
    """Verify both give the answers of `robust_statistics` as plain numbers."""
    values = [1.0, 2.0, 3.0, 2.0, 1.0, 2.0, 50.0]

    assert records.measure_robust_sigma(values) == pytest.approx(robust_sigma(values))
    kept = records.measure_clipped_values(values)
    expected = sigma_clipped_values(values, minimum_count=3, maximum_passes=5, clip_sigma=3.0)
    assert expected is not None
    assert kept == expected.tolist()
    assert 50.0 not in kept
    assert records.measure_clipped_values([1.0]) is None
    assert records.measure_clipped_values(values, clip_sigma=100.0) == values


def test_the_sky_in_electrons_takes_off_the_bias(records: QualityBlocks) -> None:
    """Verify a bias and a gain turn the ADU sky into electrons."""
    adu = records.measure_sky(_image())
    electrons = records.measure_sky(_image(), bias_adu=200.0, gain=2.0)

    assert adu is not None and electrons is not None
    assert adu.unit == "ADU" and electrons.unit == "electrons"
    assert electrons.level == pytest.approx((adu.level - 200.0) * 2.0)
    assert electrons.noise == pytest.approx(adu.noise * 2.0)
    with pytest.raises(InvalidArgumentError):
        records.measure_sky(_image(), gain=2.0)


def test_a_color_sky_is_given_per_channel(records: QualityBlocks) -> None:
    """Verify a three-channel image gives each channel's sky too."""
    image = _image()
    sky = records.measure_sky(np.stack([image, image + 100.0, image + 200.0]))

    assert sky is not None
    assert [channel.channel for channel in sky.channels] == ["red", "green", "blue"]
    assert sky.channels[2].level == pytest.approx(sky.channels[0].level + 200.0, abs=1.0)


def _mosaic(tmp_path: Path) -> Path:
    """Write the test image as a raw color mosaic (a BAYERPAT card).

    Returns
    -------
    path : `pathlib.Path`
        The file.
    """
    header = fits.Header()
    header["BAYERPAT"] = "RGGB"
    path = tmp_path / "mosaic.fits"
    fits.writeto(path, _image().astype(np.float32), header)
    return path


def test_a_raw_mosaic_is_refused_unless_debayered(records: QualityBlocks, tmp_path: Path) -> None:
    """Verify a BAYERPAT frame is refused, and debayered when asked."""
    path = _mosaic(tmp_path)
    with pytest.raises(InvalidArgumentError, match="BAYERPAT"):
        records.measure_sky(str(path))
    with pytest.raises(InvalidArgumentError, match="BAYERPAT"):
        records.measure_stars(str(path))

    sky = records.measure_sky(str(path), debayer=True)
    found = records.measure_stars(
        str(path), finder="daofind", fwhm_px=3.5, threshold_sigma=10.0, debayer=True
    )

    assert sky is not None and len(sky.channels) == 3
    assert sky.level == pytest.approx(SKY, abs=2.0)
    assert found is not None and found.debayered
    positions = sorted((star.x - 1.0, star.y - 1.0) for star in found.stars)
    for (x, y), (star_x, star_y) in zip(positions, sorted(STARS), strict=True):
        assert (x, y) == pytest.approx((star_x, star_y), abs=1.0)


def test_a_debayered_mosaic_noise_follows_the_true_noise(records: QualityBlocks, tmp_path: Path) -> None:
    """Verify super-pixel noise from whole-number mosaics follows the noise.

    The super-pixels are averages of whole numbers, so they lie on a grid of
    a fraction of an ADU. The old noise landed on one grid value for every
    frame (the Nikon D5300 case, 11.18). Frames with a different true noise
    now give different, correct values.
    """
    header = fits.Header()
    header["BAYERPAT"] = "RGGB"
    noises = (8.0, 8.4, 8.8, 9.2)
    found = []
    for index, noise in enumerate(noises):
        rng = np.random.default_rng(40 + index)
        mosaic = np.round(rng.normal(700.0, noise, (1600, 1600))).astype(np.uint16)
        path = tmp_path / f"nikon_{index}.fits"
        fits.writeto(path, mosaic, header)
        sky = records.measure_sky(str(path), debayer=True)
        assert sky is not None
        found.append(sky.noise)
    # A super-pixel is the mean of four numbers (the green is two of them), so
    # its noise is about half of one pixel's.
    expected = np.array(noises) / 2.0
    np.testing.assert_allclose(found, expected, rtol=0.06)
    assert len(set(np.round(found, 6))) == len(noises)


def test_debayer_needs_a_mosaic(records: QualityBlocks, image_path: Path) -> None:
    """Verify debayer=True on an array or a mono frame is refused."""
    with pytest.raises(InvalidArgumentError):
        records.measure_sky(_image(), debayer=True)
    with pytest.raises(InvalidArgumentError):
        records.measure_sky(str(image_path), debayer=True)


def test_aperture_photometry_gives_flux_and_uncertainty(records: QualityBlocks, image_path: Path) -> None:
    """Verify a made star's aperture flux and its uncertainty."""
    total = 3000.0 * 2.0 * np.pi * STAR_SIGMA**2
    result = records.measure_aperture(
        str(image_path), [(x + 1.0, y + 1.0) for x, y in STARS], radius_px=6.0, gain=1.0, read_noise_e=0.0
    )

    assert result.noise_source == "given"
    assert len(result.stars) == len(STARS)
    for star in result.stars:
        assert star.flux is not None and star.flux_error is not None
        assert star.flux == pytest.approx(total, rel=0.03)
        assert star.flux_error > 0.0
        assert star.signal_to_noise == pytest.approx(star.flux / star.flux_error)


def test_aperture_photometry_says_when_the_gain_was_assumed(records: QualityBlocks) -> None:
    """Verify an unknown gain is assumed, and a color array needs a channel."""
    image = _image()
    assert records.measure_aperture(image, [(41.0, 51.0)]).noise_source == "assumed"
    with pytest.raises(InvalidArgumentError):
        records.measure_aperture(np.stack([image, image, image]), [(41.0, 51.0)])
    picked = records.measure_aperture(np.stack([image, image, image]), [(41.0, 51.0)], channel=1)
    assert picked.stars[0].flux is not None


def test_a_frame_record_is_measured_as_its_file(records: QualityBlocks, image_path: Path) -> None:
    """Verify a block takes a frame record and measures the file it names."""
    from astrometricslib.models.target import FrameRecord

    frame = FrameRecord(path=str(image_path))

    assert records.measure_sky(frame) == records.measure_sky(str(image_path))
    assert records.measure_sky_corners(frame) is None
