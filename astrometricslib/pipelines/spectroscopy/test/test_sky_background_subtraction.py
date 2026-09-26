"""Purpose: Unit tests for the sky background subtraction stage.

Description: The spectrum extractor measures the night-sky glow in strips
just outside its reading box and subtracts it, so that later pipeline
stages only see the star's light. These tests build synthetic frames where
the true star light and the sky are known separately, then check that the
subtracted result matches the star alone. They cover a flat sky pedestal, a
sky emission line, a neighbouring streak inside the sky band, the effect on
a measured absorption-line depth, every extraction method in the extractor,
and the edge-of-image and on/off-switch behaviour.
"""

import numpy as np
import pytest

from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.spectroscopy.spectrum_extractor import (
    SKY_BAND_GAP_PX,
    SKY_BAND_MINIMUM_SAMPLE_COUNT,
    SKY_BAND_WIDTH_PX,
    SpectrumExtractor,
    measure_sky_level_per_pixel,
)
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig

IMAGE_HEIGHT_PX = 100
IMAGE_WIDTH_PX = 200
TRAIL_CENTER_Y_PX = 50
TRAIL_SIGMA_PX = 2.0
TRAIL_PEAK = 4000.0
SKY_PEDESTAL = 800.0
EXTRACTION_RADIUS_PX = 5
FIRST_TRAIL_COLUMN = 45
LAST_TRAIL_COLUMN = 180
ZERO_ORDER_X_PX = 30


class MockAstrometricsImage(AstrometricsImage):
    """Mock AstrometricsImage that accepts a direct array input."""

    def __init__(self, data: np.ndarray):  # ruff: ignore[missing-return-type-special-method]
        """Wrap `data` so the extractor can read it like a real image."""
        self._data = data
        self._header = {}
        self._wcs = None

    @property
    def data(self) -> np.ndarray:
        """Image array data."""
        return self._data

    @property
    def header(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Image headers dict."""
        return self._header


def _build_star_only_image(absorption_columns: tuple[int, int] | None = None) -> np.ndarray:
    """Build a horizontal spectrum trail plus a zero-order star, no sky.

    Parameters
    ----------
    absorption_columns : `tuple` [`int`, `int`], optional
        First and last column of a dark absorption line that takes half of
        the trail's light. `None` for no line.

    Returns
    -------
    star_only : `numpy.ndarray`
        The image containing only the star's light.
    """
    rows = np.arange(IMAGE_HEIGHT_PX)
    star_only = np.zeros((IMAGE_HEIGHT_PX, IMAGE_WIDTH_PX))
    row_profile = np.exp(-0.5 * ((rows - TRAIL_CENTER_Y_PX) / TRAIL_SIGMA_PX) ** 2)

    for column in range(FIRST_TRAIL_COLUMN, LAST_TRAIL_COLUMN):
        column_brightness = TRAIL_PEAK
        if absorption_columns is not None and absorption_columns[0] <= column <= absorption_columns[1]:
            column_brightness *= 0.5
        star_only[:, column] += column_brightness * row_profile

    columns = np.arange(IMAGE_WIDTH_PX)
    zero_order_profile = np.exp(
        -0.5
        * (
            ((columns[None, :] - ZERO_ORDER_X_PX) ** 2 + (rows[:, None] - TRAIL_CENTER_Y_PX) ** 2)
            / TRAIL_SIGMA_PX**2
        )
    )
    star_only += 20000.0 * zero_order_profile
    return star_only


def _extract_traced(data: np.ndarray, subtract_sky_background: bool = True) -> np.ndarray:
    """Read the horizontal trail with the traced extraction method.

    Parameters
    ----------
    data : `numpy.ndarray`
        The synthetic image.
    subtract_sky_background : `bool`, optional
        Whether the extractor takes the sky out (default `True`).

    Returns
    -------
    profile : `numpy.ndarray`
        The brightness at each step along the trail.
    """
    extractor = SpectrumExtractor(
        radius=EXTRACTION_RADIUS_PX + 5, subtract_sky_background=subtract_sky_background
    )
    profile, _, _ = extractor.extract_line_traced(
        MockAstrometricsImage(data),
        (float(FIRST_TRAIL_COLUMN), float(TRAIL_CENTER_Y_PX)),
        np.array([1.0, 0.0]),
        LAST_TRAIL_COLUMN - FIRST_TRAIL_COLUMN,
    )
    return profile


def test_measure_sky_level_ignores_the_reading_box_and_the_gap() -> None:
    """The bright star and its gap must not leak into the sky level."""
    cross_section = np.full(80, SKY_PEDESTAL)
    box_half_width = 5
    cross_section[40 - box_half_width - SKY_BAND_GAP_PX : 40 + box_half_width + SKY_BAND_GAP_PX + 1] = 9.0e5

    sky_level = measure_sky_level_per_pixel(cross_section, 40, box_half_width)

    assert sky_level == pytest.approx(SKY_PEDESTAL)


def test_measure_sky_level_is_not_pulled_up_by_a_few_bright_pixels() -> None:
    """A hot pixel or a neighbour's streak must not raise the sky level."""
    cross_section = np.full(80, SKY_PEDESTAL)
    box_half_width = 5
    first_upper_band_index = 40 + box_half_width + SKY_BAND_GAP_PX + 1
    cross_section[first_upper_band_index + 2] = 1.0e7

    sky_level = measure_sky_level_per_pixel(cross_section, 40, box_half_width)

    assert sky_level == pytest.approx(SKY_PEDESTAL)


def test_measure_sky_level_uses_the_cleaner_band_when_one_side_is_crowded() -> None:
    """A neighbour's trail filling one band must not raise the sky level.

    In a crowded field a neighbouring star's spectrum often runs alongside
    ours and fills one whole band. Pooling both bands would put the sky
    level halfway up that trail. The lower band is the one without it.
    """
    cross_section = np.full(120, SKY_PEDESTAL)
    box_half_width = 5
    first_upper_band_index = 60 + box_half_width + SKY_BAND_GAP_PX + 1
    cross_section[first_upper_band_index : first_upper_band_index + SKY_BAND_WIDTH_PX] = 50.0 * SKY_PEDESTAL

    sky_level = measure_sky_level_per_pixel(cross_section, 60, box_half_width)

    assert sky_level == pytest.approx(SKY_PEDESTAL)


def test_measure_sky_level_uses_the_only_band_left_at_the_image_edge() -> None:
    """With one band cut off by the edge, the other still gives a level."""
    cross_section = np.full(60, SKY_PEDESTAL)
    box_half_width = 5
    center = 3  # the lower band is off the image

    assert measure_sky_level_per_pixel(cross_section, center, box_half_width) == pytest.approx(SKY_PEDESTAL)


def test_measure_sky_level_returns_zero_when_too_few_sky_pixels_are_on_the_image() -> None:
    """Near the image edge we subtract nothing rather than guess."""
    box_half_width = 2
    first_upper_band_index = 5 + box_half_width + SKY_BAND_GAP_PX + 1
    cross_section = np.full(first_upper_band_index + SKY_BAND_MINIMUM_SAMPLE_COUNT - 1, SKY_PEDESTAL)

    assert measure_sky_level_per_pixel(cross_section, 5, box_half_width) == pytest.approx(0.0)


def test_measure_sky_level_skips_non_finite_pixels() -> None:
    """A NaN pixel (a masked bad pixel) must not make the sky level NaN."""
    cross_section = np.full(80, SKY_PEDESTAL)
    cross_section[40 + 5 + SKY_BAND_GAP_PX + 1] = np.nan

    sky_level = measure_sky_level_per_pixel(cross_section, 40, 5)

    assert sky_level == pytest.approx(SKY_PEDESTAL)


def test_flat_sky_pedestal_is_removed_from_traced_extraction() -> None:
    """With sky subtraction the spectrum equals the star alone."""
    star_only = _build_star_only_image()
    star_plus_sky = star_only + SKY_PEDESTAL

    star_alone_profile = _extract_traced(star_only, subtract_sky_background=False)
    subtracted_profile = _extract_traced(star_plus_sky)

    np.testing.assert_allclose(subtracted_profile, star_alone_profile, rtol=1e-4)


def test_turning_subtraction_off_keeps_the_sky_in_the_spectrum() -> None:
    """The off switch gives the raw box total, sky included."""
    star_only = _build_star_only_image()
    star_plus_sky = star_only + SKY_PEDESTAL

    star_alone_profile = _extract_traced(star_only, subtract_sky_background=False)
    raw_profile = _extract_traced(star_plus_sky, subtract_sky_background=False)

    assert np.all(raw_profile > star_alone_profile)
    # 2.5 sigma is 5 px, so the box is 11 px tall.
    box_height_px = 2 * round(TRAIL_SIGMA_PX * 2.5) + 1
    np.testing.assert_allclose(
        (raw_profile - star_alone_profile)[10:-10], SKY_PEDESTAL * box_height_px, rtol=0.2
    )


def test_sky_emission_line_is_removed() -> None:
    """A bright sky line (like a street lamp) must not leave a bump."""
    star_only = _build_star_only_image()
    sky = np.full_like(star_only, SKY_PEDESTAL)
    sky[:, 100] += 3000.0  # a sky emission line covering the whole column

    star_alone_profile = _extract_traced(star_only, subtract_sky_background=False)
    raw_profile = _extract_traced(star_only + sky, subtract_sky_background=False)
    subtracted_profile = _extract_traced(star_only + sky)

    emission_step = 100 - FIRST_TRAIL_COLUMN
    assert raw_profile[emission_step] > 1.5 * star_alone_profile[emission_step]
    np.testing.assert_allclose(subtracted_profile, star_alone_profile, rtol=1e-4)


def test_neighbouring_streak_in_the_sky_band_does_not_change_the_result() -> None:
    """One bright row inside a sky band is an outlier, not sky."""
    star_only = _build_star_only_image()
    star_plus_sky = star_only + SKY_PEDESTAL
    neighbour_row = TRAIL_CENTER_Y_PX + EXTRACTION_RADIUS_PX + 5 + SKY_BAND_GAP_PX + 3
    star_plus_sky[neighbour_row, :] += 5000.0

    star_alone_profile = _extract_traced(star_only, subtract_sky_background=False)
    subtracted_profile = _extract_traced(star_plus_sky)

    np.testing.assert_allclose(subtracted_profile, star_alone_profile, rtol=1e-4)


def test_sky_subtraction_restores_the_true_absorption_line_depth() -> None:
    """A sky pedestal makes a dark line look shallow; subtraction fixes it."""
    line_columns = (100, 104)
    star_only = _build_star_only_image(absorption_columns=line_columns)
    star_plus_sky = star_only + SKY_PEDESTAL

    def measured_depth(profile: np.ndarray) -> float:
        line_step = 102 - FIRST_TRAIL_COLUMN
        continuum_step = 130 - FIRST_TRAIL_COLUMN
        return 1.0 - profile[line_step] / profile[continuum_step]

    true_depth = measured_depth(_extract_traced(star_only, subtract_sky_background=False))
    raw_depth = measured_depth(_extract_traced(star_plus_sky, subtract_sky_background=False))
    subtracted_depth = measured_depth(_extract_traced(star_plus_sky))

    assert true_depth == pytest.approx(0.5, abs=0.01)
    assert raw_depth < true_depth - 0.05
    assert subtracted_depth == pytest.approx(true_depth, abs=1e-3)


def test_fixed_box_extraction_removes_the_sky() -> None:
    """The untraced `extract_line` also has the sky taken out."""
    star_only = _build_star_only_image()
    star_plus_sky = star_only + SKY_PEDESTAL
    start = (float(FIRST_TRAIL_COLUMN), float(TRAIL_CENTER_Y_PX))
    length = LAST_TRAIL_COLUMN - FIRST_TRAIL_COLUMN

    star_alone_profile = SpectrumExtractor(
        radius=EXTRACTION_RADIUS_PX, subtract_sky_background=False
    ).extract_line(MockAstrometricsImage(star_only), start, np.array([1.0, 0.0]), length)
    subtracted_profile = SpectrumExtractor(radius=EXTRACTION_RADIUS_PX).extract_line(
        MockAstrometricsImage(star_plus_sky), start, np.array([1.0, 0.0]), length
    )

    np.testing.assert_allclose(subtracted_profile, star_alone_profile, rtol=1e-9)


def test_vertical_spectrum_gets_the_same_sky_subtraction() -> None:
    """A top-to-bottom spectrum is treated like a left-to-right one."""
    star_only = _build_star_only_image()
    star_plus_sky = star_only + SKY_PEDESTAL
    start = (float(TRAIL_CENTER_Y_PX), float(FIRST_TRAIL_COLUMN))
    length = LAST_TRAIL_COLUMN - FIRST_TRAIL_COLUMN

    star_alone_profile = SpectrumExtractor(
        radius=EXTRACTION_RADIUS_PX, subtract_sky_background=False
    ).extract_line(MockAstrometricsImage(star_only.T.copy()), start, np.array([0.0, 1.0]), length)
    subtracted_profile = SpectrumExtractor(radius=EXTRACTION_RADIUS_PX).extract_line(
        MockAstrometricsImage(star_plus_sky.T.copy()), start, np.array([0.0, 1.0]), length
    )

    np.testing.assert_allclose(subtracted_profile, star_alone_profile, rtol=1e-9)


@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
def test_flare_mask_extraction_removes_the_sky(orientation: str) -> None:
    """The flare-masked fixed-box method also has the sky taken out."""
    star_only = _build_star_only_image()
    star_plus_sky = star_only + SKY_PEDESTAL
    start_position = (float(ZERO_ORDER_X_PX), float(TRAIL_CENTER_Y_PX))
    if orientation == "vertical":
        star_only, star_plus_sky = star_only.T.copy(), star_plus_sky.T.copy()
        start_position = start_position[::-1]

    star_alone_profile, _, _ = SpectrumExtractor(subtract_sky_background=False).extract_with_flare_mask(
        MockAstrometricsImage(star_only), start_position, 15.0, 150.0, EXTRACTION_RADIUS_PX, orientation
    )
    subtracted_profile, _, _ = SpectrumExtractor().extract_with_flare_mask(
        MockAstrometricsImage(star_plus_sky), start_position, 15.0, 150.0, EXTRACTION_RADIUS_PX, orientation
    )

    np.testing.assert_allclose(subtracted_profile, star_alone_profile, rtol=1e-6)


def test_traced_flare_mask_extraction_removes_the_sky() -> None:
    """The flare-masked traced method also has the sky taken out."""
    star_only = _build_star_only_image()
    star_plus_sky = star_only + SKY_PEDESTAL
    start_position = (float(ZERO_ORDER_X_PX), float(TRAIL_CENTER_Y_PX))

    star_alone_profile, *_ = SpectrumExtractor(subtract_sky_background=False).extract_with_flare_mask_traced(
        MockAstrometricsImage(star_only), start_position, 15.0, 150.0, 10, "horizontal"
    )
    subtracted_profile, *_ = SpectrumExtractor().extract_with_flare_mask_traced(
        MockAstrometricsImage(star_plus_sky), start_position, 15.0, 150.0, 10, "horizontal"
    )

    np.testing.assert_allclose(subtracted_profile, star_alone_profile, rtol=1e-4)


def test_trail_near_the_image_edge_still_gives_finite_readings() -> None:
    """A trail with its sky band cut off by the image edge is still read."""
    star_only = _build_star_only_image()
    star_plus_sky = (star_only + SKY_PEDESTAL)[: TRAIL_CENTER_Y_PX + EXTRACTION_RADIUS_PX + 2, :]

    subtracted_profile = _extract_traced(star_plus_sky)

    assert np.all(np.isfinite(subtracted_profile))


def test_pipeline_passes_the_config_switch_to_its_extractor() -> None:
    """`subtract_sky_background` in the config reaches the extractor."""
    camera = CameraConfig(name="TestCam", pixel_size_um=5.0, sensor_width_px=2000, sensor_height_px=2000)

    default_pipeline = SpectroscopyPipeline(SpectroscopyConfig(camera=camera, grating_distance_mm=10.0))
    switched_off_pipeline = SpectroscopyPipeline(
        SpectroscopyConfig(camera=camera, grating_distance_mm=10.0, subtract_sky_background=False)
    )

    assert default_pipeline.extractor.subtract_sky_background is True
    assert switched_off_pipeline.extractor.subtract_sky_background is False


def test_sky_band_settings_are_sensible() -> None:
    """The bands must have room for the minimum sample count on one side."""
    assert SKY_BAND_GAP_PX >= 1
    assert SKY_BAND_WIDTH_PX >= SKY_BAND_MINIMUM_SAMPLE_COUNT
