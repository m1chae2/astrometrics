"""Purpose: Truth-known tests for how the tuner places absorption dips.

Description: The spectroscopy pipeline drops leading samples that are off the
image or shorter than the camera's shortest wavelength. The calibration tuner
must place each dip using the distance the pipeline recorded for that sample.
If it counts from a fixed start instead, every dip moves by the number of
dropped samples, and the fitted grating distance comes out wrong.

These tests build a synthetic spectral frame (`make_spectral_frame`) whose
Balmer lines sit at pixel offsets computed from the real grating equation
with a known grating distance. They then run the tuner's extraction, dip
search, and fit, and compare the result with the known truth. Each case runs
once with leading samples dropped and once with none dropped, on both
extraction paths (plain dispersion line and flare mask).

Two later cases are covered too. A noisy frame at a realistic brightness
(3000 ADU continuum, Poisson noise) must give few dip candidates, contain the
three true lines, and finish the search over groups of three in seconds. And
the sub-sample dip centres (parabolic refinement) must bring the fitted
grating distance closer to the truth than the whole-sample positions do.
"""

import time

import numpy as np
import pytest

from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.spectroscopy.pre_processing.optics_physics import (
    BALMER_SERIES_NM,
    calculate_pixel_offset,
)
from astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner import (
    _MAX_DIP_CANDIDATES,
    SpectroscopyCalibrationTuner,
)
from astrometricslib.test.synthetic import SyntheticSpectralFrame, make_spectral_frame
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig

# The grating distance the synthetic frame is built with, in millimeters.
TRUE_GRATING_DISTANCE_MM = 17.2
# The pipeline starts from a different (wrong) guess, as it does before tuning.
INITIAL_GRATING_DISTANCE_MM = 16.0
GRATING_LINES_PER_MM = 200.0
PIXEL_SIZE_UM = 3.76
# The camera cannot see below this wavelength, so samples shorter than it are
# dropped. With the initial guess above, this wavelength sits about 324 px
# from the zero order.
SENSOR_MIN_WAVELENGTH_NM = 380.0
# Dispersion starts at this offset when leading samples must be dropped.
# About 25 samples fall below the sensor's shortest wavelength.
START_PX_WITH_DROPS = 300.0
# Dispersion starts at this offset when no sample is dropped.
START_PX_WITHOUT_DROPS = 330.0

# The three reference lines the tuner searches for, shortest wavelength first.
LINE_NAMES = ("H-delta", "H-gamma", "H-beta")
TARGET_WAVELENGTHS_NM = np.array([BALMER_SERIES_NM[name] for name in LINE_NAMES])
ZERO_ORDER_XY = (60.0, 128.0)

# Accuracy the tuner must reach.
POSITION_TOLERANCE_PX = 0.5
DISTANCE_TOLERANCE_FRACTION = 0.01
# Accuracy after the parabolic refinement of the dip centres, on both paths.
REFINED_DISTANCE_TOLERANCE_FRACTION = 2e-4
REFINED_POSITION_TOLERANCE_PX = 0.05
# How far a recorded sample distance may sit from the truth on a noise-free
# frame, in pixels.
SAMPLE_DISTANCE_TOLERANCE_PX = 0.01
# Fractional parts of the zero order's x position. The zero order sits
# between pixels in real frames, and a centroid that rounds or is pulled by
# the trail shows up as an error that changes with this fraction.
ZERO_ORDER_X_VALUES = (60.0, 60.25, 60.5, 60.75)


class ArrayImage(AstrometricsImage):
    """An `AstrometricsImage` that wraps an array held in memory.

    Attributes
    ----------
    _data : `numpy.ndarray`
        The pixel values.
    """

    def __init__(self, data: np.ndarray) -> None:
        """Wrap an array without reading any file.

        Parameters
        ----------
        data : `numpy.ndarray`
            The pixel values, shape `(ny, nx)`.
        """
        self._data = data
        self._header = {}
        self._wcs = None

    @property
    def data(self) -> np.ndarray:
        """`numpy.ndarray`: The pixel values."""
        return self._data

    @property
    def header(self) -> dict:
        """`dict`: An empty header."""
        return self._header


def _true_line_offsets_px() -> np.ndarray:
    """Compute where the three Balmer lines fall on the real instrument.

    Returns
    -------
    offsets_px : `numpy.ndarray`
        The distance of each line from the zero order along the dispersion
        direction, in pixels, from the grating equation with the true
        grating distance.
    """
    return calculate_pixel_offset(
        wavelength_nm=TARGET_WAVELENGTHS_NM,
        grating_distance_mm=TRUE_GRATING_DISTANCE_MM,
        lines_per_mm=GRATING_LINES_PER_MM,
        pixel_size_um=PIXEL_SIZE_UM,
    )


def _build_frame(
    angle_deg: float,
    add_noise: bool = False,
    seed: int = 0,
    zero_order_xy: tuple[float, float] = ZERO_ORDER_XY,
) -> SyntheticSpectralFrame:
    """Draw a spectral frame with the Balmer lines in place.

    The generator spaces lines with a straight-line wavelength model, so
    each line is given a made-up wavelength of `column offset * 10 A`.
    That puts the line at the column offset we want. The made-up wavelength
    is never used. Only the line columns matter.

    The generator places lines by column offset. A tilted trail is longer
    than its column offset by a factor of 1 / cos(tilt), so each column
    offset is the true distance times cos(tilt). The line then sits at the
    true distance along the trail.

    Parameters
    ----------
    angle_deg : `float`
        The trail tilt, in degrees.
    add_noise : `bool`, optional
        If `True`, add Poisson and read noise (the trail is 3000 ADU per
        column). The default is a noise-free frame.
    seed : `int`, optional
        Seed for the noise.
    zero_order_xy : `tuple` [`float`, `float`], optional
        The zero order's true position. The default is a whole-pixel x.

    Returns
    -------
    frame : `SyntheticSpectralFrame`
        The image and its truth values.
    """
    column_offsets_px = _true_line_offsets_px() * np.cos(np.radians(angle_deg))
    dispersion_a_per_px = 10.0
    return make_spectral_frame(
        zero_order_xy=zero_order_xy,
        angle_deg=angle_deg,
        dispersion_a_per_px=dispersion_a_per_px,
        trail_length_px=900,
        lines=tuple((float(offset) * dispersion_a_per_px, 0.4) for offset in column_offsets_px),
        shape=(256, 1000),
        add_noise=add_noise,
        seed=seed,
    )


def _build_pipeline(start_px: float, angle_deg: float, use_flare_mask: bool) -> SpectroscopyPipeline:
    """Build a pipeline that still has the untuned grating distance.

    Parameters
    ----------
    start_px : `float`
        The configured distance of the first sample from the zero order.
    angle_deg : `float`
        The trail tilt of the frame, in the generator's convention. The
        pipeline's own angle has the opposite sign, so the config receives
        its negative.
    use_flare_mask : `bool`
        Whether to use the flare-mask extraction path.

    Returns
    -------
    pipeline : `SpectroscopyPipeline`
        A pipeline for horizontal dispersion with the untuned distance.
    """
    camera = CameraConfig(
        name="TestCam",
        pixel_size_um=PIXEL_SIZE_UM,
        sensor_width_px=1000,
        sensor_height_px=256,
        sensor_min_wavelength=SENSOR_MIN_WAVELENGTH_NM,
        sensor_max_wavelength=1000.0,
    )
    config = SpectroscopyConfig(
        camera=camera,
        grating_lines_per_mm=GRATING_LINES_PER_MM,
        grating_distance_mm=INITIAL_GRATING_DISTANCE_MM,
        dispersion_orientation="horizontal",
        dispersion_direction="positive",
        dispersion_start_px=start_px,
        dispersion_angle_degrees=-angle_deg,
        extraction_radius=8,
        max_extraction_length_px=900.0,
        use_flare_mask_extraction=use_flare_mask,
    )
    return SpectroscopyPipeline(config=config)


def _nearest_dips(dips: list[int], sample_distances_px: np.ndarray) -> list[int]:
    """Pick the dip closest to each true line.

    The tilted frames show extra small ripples that the dip search also
    reports. The tuner tries every combination of three dips, which is slow
    with many candidates. Choosing the nearest dip to each true line keeps
    the test fast and still tests the dip-to-distance mapping.

    Parameters
    ----------
    dips : `list` [`int`]
        The candidate dip indices.
    sample_distances_px : `numpy.ndarray`
        The recorded distance of each sample, in pixels.

    Returns
    -------
    nearest : `list` [`int`]
        One dip index per true line, shortest wavelength first.
    """
    return [
        min(dips, key=lambda index: abs(sample_distances_px[index] - true_offset))
        for true_offset in _true_line_offsets_px()
    ]


@pytest.mark.parametrize("use_flare_mask", [False, True], ids=["dispersion-line", "flare-mask"])
@pytest.mark.parametrize("angle_deg", [0.0, 3.0], ids=["level", "tilted"])
@pytest.mark.parametrize(
    ("start_px", "expect_drops"),
    [(START_PX_WITH_DROPS, True), (START_PX_WITHOUT_DROPS, False)],
    ids=["leading-samples-dropped", "no-samples-dropped"],
)
def test_tuner_recovers_lines_and_grating_distance(
    start_px: float, expect_drops: bool, angle_deg: float, use_flare_mask: bool
) -> None:
    """The dips land on the true lines and the fit recovers the true distance.

    Checks that the number of dropped leading samples matches the case,
    that each dip's recorded distance is within half a pixel of its true
    position, and that the fitted grating distance is within one percent of
    the true value. Also checks that the report's pixel offsets and
    calibrated wavelengths agree with the truth.

    Parameters
    ----------
    start_px : `float`
        The configured distance of the first requested sample.
    expect_drops : `bool`
        Whether this start should make the pipeline drop leading samples.
    angle_deg : `float`
        The trail tilt, in degrees.
    use_flare_mask : `bool`
        Whether the pipeline uses the flare-mask extraction path.
    """
    frame = _build_frame(angle_deg)
    pipeline = _build_pipeline(start_px, angle_deg, use_flare_mask)

    _, smoothed, sample_distances_px = SpectroscopyCalibrationTuner._extract_smoothed_spectrum(
        pipeline, ArrayImage(frame.image), frame.zero_order_xy
    )

    dropped_px = sample_distances_px[0] - start_px
    if expect_drops:
        assert dropped_px > 20.0
    else:
        assert abs(dropped_px) < 1.0

    dips = _nearest_dips(SpectroscopyCalibrationTuner._detect_absorption_dips(smoothed), sample_distances_px)
    np.testing.assert_allclose(
        sample_distances_px[dips], _true_line_offsets_px(), atol=POSITION_TOLERANCE_PX, rtol=0.0
    )

    rms_nm, fitted_distance_mm, combo = SpectroscopyCalibrationTuner._fit_grating_distance(
        dips, sample_distances_px, pipeline, TARGET_WAVELENGTHS_NM
    )
    assert fitted_distance_mm == pytest.approx(TRUE_GRATING_DISTANCE_MM, rel=DISTANCE_TOLERANCE_FRACTION)
    assert rms_nm < 1.0

    summary = SpectroscopyCalibrationTuner._build_calibration_summary(
        "TestCam",
        round(float(fitted_distance_mm), 2),
        300.0,
        rms_nm,
        angle_deg,
        sample_distances_px,
        combo,
        TARGET_WAVELENGTHS_NM,
        pipeline,
        use_flare_mask,
        None,
    )
    for line, true_offset in zip(summary["detailed_calibration"], _true_line_offsets_px(), strict=True):
        assert line["pixel_offset"] == pytest.approx(true_offset, abs=POSITION_TOLERANCE_PX)
        assert abs(line["deviation_nm"]) < 1.0


def test_tuner_full_dip_search_gives_the_same_distance_with_and_without_drops() -> None:
    """Dropping leading samples does not change the fit when all dips compete.

    Runs the real three-line combination search over every dip the search
    reports (a level, noise-free frame has exactly the three true lines) for
    both starts. The fitted distances must agree.
    """
    frame = _build_frame(0.0)
    fitted = []
    for start_px in (START_PX_WITH_DROPS, START_PX_WITHOUT_DROPS):
        pipeline = _build_pipeline(start_px, 0.0, use_flare_mask=False)
        _, smoothed, sample_distances_px = SpectroscopyCalibrationTuner._extract_smoothed_spectrum(
            pipeline, ArrayImage(frame.image), frame.zero_order_xy
        )
        dips = SpectroscopyCalibrationTuner._detect_absorption_dips(smoothed)
        assert len(dips) == 3
        _, distance_mm, _ = SpectroscopyCalibrationTuner._fit_grating_distance(
            dips, sample_distances_px, pipeline, TARGET_WAVELENGTHS_NM
        )
        fitted.append(distance_mm)

    assert fitted[0] == pytest.approx(TRUE_GRATING_DISTANCE_MM, rel=DISTANCE_TOLERANCE_FRACTION)
    assert fitted[0] == pytest.approx(fitted[1], rel=1e-6)


def test_flare_mask_distances_follow_the_tilted_trail_not_the_column_step() -> None:
    """The flare-mask path records distance along the trail, not the axis.

    The extractor steps one column per sample. On a trail tilted by theta,
    each step covers 1 / cos(theta) of trail. The recorded distances must
    grow by that factor per sample, which is the distance the grating
    equation uses.
    """
    angle_deg = 5.0
    frame = _build_frame(angle_deg)
    pipeline = _build_pipeline(START_PX_WITHOUT_DROPS, angle_deg, use_flare_mask=True)

    result = pipeline._process_single_star(
        ArrayImage(frame.image), frame.zero_order_xy, auto_detect_angle=False
    )
    distances = np.asarray(result["sample_distances_px"])

    step_px = np.diff(distances)
    np.testing.assert_allclose(step_px, 1.0 / np.cos(np.radians(angle_deg)), rtol=1e-9)
    assert len(distances) == len(result["wavelengths"])
    # The flare-mask path does not give the neighbour-wing stage its geometry.
    assert "distances_from_zero_order_px" not in result


def test_dispersion_line_distances_start_where_the_pipeline_says() -> None:
    """The plain extraction records one pixel per sample from its start.

    Without drops the first distance equals the configured start. With
    drops, the first distance is larger by the number of dropped samples.
    """
    frame = _build_frame(0.0)

    kept = _build_pipeline(START_PX_WITHOUT_DROPS, 0.0, use_flare_mask=False)._process_single_star(
        ArrayImage(frame.image), frame.zero_order_xy, auto_detect_angle=False
    )
    dropped = _build_pipeline(START_PX_WITH_DROPS, 0.0, use_flare_mask=False)._process_single_star(
        ArrayImage(frame.image), frame.zero_order_xy, auto_detect_angle=False
    )

    assert kept["sample_distances_px"][0] == pytest.approx(START_PX_WITHOUT_DROPS)
    assert dropped["sample_distances_px"][0] > START_PX_WITH_DROPS + 20.0
    np.testing.assert_allclose(np.diff(dropped["sample_distances_px"]), 1.0)
    assert kept["sample_distances_px"] == kept["distances_from_zero_order_px"]
    # The first kept wavelength is at or above the camera limit.
    assert dropped["wavelengths"][0] >= SENSOR_MIN_WAVELENGTH_NM


def test_calibrate_at_distances_matches_calibrate_for_unit_steps() -> None:
    """`calibrate_at_distances` matches `calibrate` for one-pixel steps."""
    pipeline = _build_pipeline(START_PX_WITHOUT_DROPS, 0.0, use_flare_mask=False)
    pixels = np.linspace(1.0, 2.0, 25)

    by_offset, _ = pipeline.calibrator.calibrate(pixels, 330.0)
    by_distance, returned_pixels = pipeline.calibrator.calibrate_at_distances(pixels, 330.0 + np.arange(25.0))

    np.testing.assert_allclose(by_distance, by_offset)
    np.testing.assert_array_equal(returned_pixels, pixels)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_noisy_adu_frame_gives_few_candidates_and_finishes_quickly(seed: int) -> None:
    """A noisy 3000 ADU frame gives at most 12 dips and a fast fit.

    The frame has Poisson and read noise on a 3000 ADU continuum, like a
    real exposure. The old dip search used a prominence of 0.001 in camera
    counts and reported about 60 dips here, so the search over groups of
    three (about 34,000 groups) ran for minutes. The test checks that the
    candidates number at most `_MAX_DIP_CANDIDATES`, that each true line has
    a candidate within 1 pixel, that the search takes under 10 seconds, and
    that the fit still recovers the true grating distance to 0.2 percent.

    Parameters
    ----------
    seed : `int`
        Seed for the frame's noise.
    """
    frame = _build_frame(0.0, add_noise=True, seed=seed)
    pipeline = _build_pipeline(START_PX_WITH_DROPS, 0.0, use_flare_mask=False)
    _, smoothed, sample_distances_px = SpectroscopyCalibrationTuner._extract_smoothed_spectrum(
        pipeline, ArrayImage(frame.image), frame.zero_order_xy
    )

    started = time.perf_counter()
    dips = SpectroscopyCalibrationTuner._detect_absorption_dips(smoothed)
    centers = SpectroscopyCalibrationTuner._refine_dip_centers(smoothed, dips)
    _, fitted_distance_mm, _ = SpectroscopyCalibrationTuner._fit_grating_distance(
        centers, sample_distances_px, pipeline, TARGET_WAVELENGTHS_NM
    )
    elapsed_s = time.perf_counter() - started

    assert 3 <= len(dips) <= _MAX_DIP_CANDIDATES
    candidate_distances_px = SpectroscopyCalibrationTuner._positions_to_distances_px(
        centers, sample_distances_px
    )
    for true_offset in _true_line_offsets_px():
        assert np.min(np.abs(candidate_distances_px - true_offset)) < 1.0
    assert elapsed_s < 10.0
    assert fitted_distance_mm == pytest.approx(TRUE_GRATING_DISTANCE_MM, rel=2e-3)


@pytest.mark.parametrize("use_flare_mask", [False, True], ids=["dispersion-line", "flare-mask"])
@pytest.mark.parametrize("angle_deg", [0.0, 3.0], ids=["level", "tilted"])
def test_refined_dip_centres_improve_the_fitted_distance(angle_deg: float, use_flare_mask: bool) -> None:
    """Sub-sample dip centres bring the fit within 0.02 percent of the truth.

    Whole-sample dip positions are up to half a pixel off, which left a
    0.08 percent error in the fitted grating distance. With the parabolic
    refinement both extraction paths must land within 0.02 percent and
    their dips within 0.05 pixel of the true lines. The refined fit must
    also be closer to the truth than the whole-sample fit.

    Parameters
    ----------
    angle_deg : `float`
        The trail tilt, in degrees.
    use_flare_mask : `bool`
        Whether the pipeline uses the flare-mask extraction path.
    """
    frame = _build_frame(angle_deg)
    pipeline = _build_pipeline(START_PX_WITH_DROPS, angle_deg, use_flare_mask)
    _, smoothed, sample_distances_px = SpectroscopyCalibrationTuner._extract_smoothed_spectrum(
        pipeline, ArrayImage(frame.image), frame.zero_order_xy
    )
    dips = SpectroscopyCalibrationTuner._detect_absorption_dips(smoothed)
    assert len(dips) == 3
    centers = SpectroscopyCalibrationTuner._refine_dip_centers(smoothed, dips)

    _, whole_sample_distance_mm, _ = SpectroscopyCalibrationTuner._fit_grating_distance(
        dips, sample_distances_px, pipeline, TARGET_WAVELENGTHS_NM
    )
    _, refined_distance_mm, refined_combo = SpectroscopyCalibrationTuner._fit_grating_distance(
        centers, sample_distances_px, pipeline, TARGET_WAVELENGTHS_NM
    )

    whole_sample_error = abs(whole_sample_distance_mm / TRUE_GRATING_DISTANCE_MM - 1.0)
    refined_error = abs(refined_distance_mm / TRUE_GRATING_DISTANCE_MM - 1.0)
    assert refined_error < REFINED_DISTANCE_TOLERANCE_FRACTION
    assert refined_error < whole_sample_error
    refined_offsets_px = SpectroscopyCalibrationTuner._positions_to_distances_px(
        refined_combo, sample_distances_px
    )
    np.testing.assert_allclose(
        refined_offsets_px, _true_line_offsets_px(), atol=REFINED_POSITION_TOLERANCE_PX, rtol=0.0
    )

    summary = SpectroscopyCalibrationTuner._build_calibration_summary(
        "TestCam",
        round(float(refined_distance_mm), 2),
        300.0,
        0.0,
        angle_deg,
        sample_distances_px,
        refined_combo,
        TARGET_WAVELENGTHS_NM,
        pipeline,
        use_flare_mask,
        None,
    )
    for line, offset_px in zip(summary["detailed_calibration"], refined_offsets_px, strict=True):
        assert line["pixel_offset"] == pytest.approx(offset_px)


@pytest.mark.parametrize("use_flare_mask", [False, True], ids=["dispersion-line", "flare-mask"])
@pytest.mark.parametrize("angle_deg", [0.0, 3.0], ids=["level", "tilted"])
@pytest.mark.parametrize("zero_order_x", ZERO_ORDER_X_VALUES)
def test_refined_dip_positions_hold_for_a_zero_order_between_pixels(
    zero_order_x: float, angle_deg: float, use_flare_mask: bool
) -> None:
    """Both paths place the dips on the true lines for any zero-order fraction.

    The zero order is moved by a fraction of a pixel in x (and by 0.4 pixel
    in y). The refined dip distances must stay within 0.02 pixel of the
    true line distances on both extraction paths, and the fitted grating
    distance must stay within 0.02 percent of the truth.

    Parameters
    ----------
    zero_order_x : `float`
        The zero order's true x position, in pixels.
    angle_deg : `float`
        The trail tilt, in degrees.
    use_flare_mask : `bool`
        Whether the pipeline uses the flare-mask extraction path.
    """
    frame = _build_frame(angle_deg, zero_order_xy=(zero_order_x, 128.4))
    pipeline = _build_pipeline(START_PX_WITH_DROPS, angle_deg, use_flare_mask)
    _, smoothed, sample_distances_px = SpectroscopyCalibrationTuner._extract_smoothed_spectrum(
        pipeline, ArrayImage(frame.image), frame.zero_order_xy
    )
    dips = SpectroscopyCalibrationTuner._detect_absorption_dips(smoothed)
    assert len(dips) == 3
    centers = SpectroscopyCalibrationTuner._refine_dip_centers(smoothed, dips)

    _, distance_mm, combo = SpectroscopyCalibrationTuner._fit_grating_distance(
        centers, sample_distances_px, pipeline, TARGET_WAVELENGTHS_NM
    )
    offsets_px = SpectroscopyCalibrationTuner._positions_to_distances_px(combo, sample_distances_px)

    np.testing.assert_allclose(offsets_px, _true_line_offsets_px(), atol=0.02, rtol=0.0)
    assert distance_mm == pytest.approx(TRUE_GRATING_DISTANCE_MM, rel=REFINED_DISTANCE_TOLERANCE_FRACTION)


@pytest.mark.parametrize("angle_deg", [0.0, 3.0], ids=["level", "tilted"])
@pytest.mark.parametrize("zero_order_x", ZERO_ORDER_X_VALUES)
def test_flare_mask_sample_distances_match_the_true_zero_order(zero_order_x: float, angle_deg: float) -> None:
    """The flare-mask distances measure from the true zero order.

    Every sample sits on a whole column, so its true distance from the zero
    order is `(column - zero_order_x) / cos(tilt)`. Converting each
    recorded distance back to a column (`distance * cos(tilt) + zero_order_x`)
    must land within 0.01 pixel of a whole number. A zero-order anchor that
    the spectrum trail pulled 0.08 pixel toward itself would fail this for
    every sample, and so would an anchor rounded to a whole pixel.

    Parameters
    ----------
    zero_order_x : `float`
        The zero order's true x position, in pixels.
    angle_deg : `float`
        The trail tilt, in degrees.
    """
    frame = _build_frame(angle_deg, zero_order_xy=(zero_order_x, 128.4))
    pipeline = _build_pipeline(START_PX_WITHOUT_DROPS, angle_deg, use_flare_mask=True)

    result = pipeline._process_single_star(
        ArrayImage(frame.image), frame.zero_order_xy, auto_detect_angle=False
    )

    distances = np.asarray(result["sample_distances_px"])
    columns = distances * np.cos(np.radians(angle_deg)) + zero_order_x
    np.testing.assert_allclose(columns, np.round(columns), atol=SAMPLE_DISTANCE_TOLERANCE_PX, rtol=0.0)
