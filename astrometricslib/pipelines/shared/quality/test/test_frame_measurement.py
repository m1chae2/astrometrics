"""Tests for the one-frame measurement in `frame_measurement.py`.

The frames are synthetic: Moffat stars of known width, stretch and angle on a
known sky, with a known gain and read noise, drawn with a fixed random seed.
One test also runs the measurement on a real M 13 golden frame (Git LFS) and
only checks that it runs and returns finite numbers.
"""

import math
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.stats import sigma_clipped_stats

from astrometricslib.models.camera_profile import (
    CameraProfile,
    ProvenancedValue,
    ProvenanceKind,
    SensorSettingNoise,
    ValueProvenance,
)
from astrometricslib.models.quality.measurement import MeasurementStatus, QualityMeasurement
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_fwhm_from_data
from astrometricslib.pipelines.shared.quality.frame_measurement import (
    FRAME_MEASUREMENT_TIME_BUDGET_SECONDS,
    PIXEL_BLUR_FWHM_SQUARED_FACTOR,
    RegionWidth,
    StarProfileResult,
    StarWidthSample,
    _mean_ellipticity_angle,
    _Stopwatch,
    effective_star_width_px,
    frame_subject_id,
    look_up_frame_sensor_noise,
    measure_clipped_mean_adu,
    measure_field_region_widths,
    measure_frame,
    measure_sky_electrons,
    measure_star_profiles,
)
from astrometricslib.pipelines.shared.quality.metric_registry import get_metric_registry
from astrometricslib.pipelines.shared.quality.metrics import (
    FRAME_BACKGROUND_LEVEL,
    FRAME_DETECTED_REGION_COUNT,
    FRAME_FOCUSER_TEMPERATURE,
    FRAME_METRICS,
    FRAME_READ_NOISE_E,
    FRAME_SATURATED_PIXEL_FRACTION,
    FRAME_SENSOR_TEMPERATURE,
    FRAME_SKY_NOISE_E,
    FRAME_SKY_RATE_E_PER_S_PER_PX,
    FRAME_STAR_ELONGATION,
    FRAME_STAR_ELONGATION_ANGLE_DEG,
    FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC,
    FRAME_STAR_WIDTH_EFFECTIVE_PX,
    FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
    FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
    FRAME_STAR_WIDTH_MOFFAT_PX,
    FRAME_STAR_WIDTH_REGION_PX,
)
from astrometricslib.pipelines.shared.quality.record_measurements import (
    check_measurement_against_registry,
)
from astrometricslib.test.golden import measurements as golden_measurements

# The camera setting of the synthetic frames, and the numbers the profile holds
# for it. The sky is 400 ADU above a bias level of 100 ADU.
GAIN_SETTING = "100"
GAIN_E_PER_ADU = 0.5
READ_NOISE_E = 2.0
BIAS_LEVEL_ADU = 100.0
SKY_ABOVE_BIAS_ADU = 400.0
EXPOSURE_SECONDS = 20.0
PIXEL_SCALE_ARCSEC = 2.0
SATURATION_ADU = 60000.0
FRAME_SIZE = 700
TRUE_WIDTH_PX = 3.0

# The star width must come back within this fraction of the truth. The fits on
# synthetic stars recovered it within 0.5 percent over widths of 2 to 6 px and
# power indices of 2 to 5; 3 percent leaves room for the random draw.
WIDTH_TOLERANCE_FRACTION = 0.03

MEASURED_AT = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)


def _profile(
    *, with_entry: bool = True, noise_kind: ProvenanceKind = ProvenanceKind.MEASURED
) -> CameraProfile:
    """Build a camera profile for the synthetic frames.

    Parameters
    ----------
    with_entry : `bool`, optional
        Whether the profile holds the noise entry for the frames' setting.
    noise_kind : `ProvenanceKind`, optional
        How the gain and read noise were obtained.

    Returns
    -------
    profile : `CameraProfile`
        A profile with a 16-bit clip ceiling.
    """
    provenance = ValueProvenance(kind=noise_kind, source="test synthetic frames")
    entries = (
        (
            SensorSettingNoise(
                gain_setting=GAIN_SETTING,
                offset="0",
                binning="1x1",
                gain_e_per_adu=ProvenancedValue(value=GAIN_E_PER_ADU, provenance=provenance),
                read_noise_e=ProvenancedValue(value=READ_NOISE_E, provenance=provenance),
            ),
        )
        if with_entry
        else ()
    )
    datasheet = ValueProvenance(kind=ProvenanceKind.DATASHEET, source="test")
    return CameraProfile(
        camera_name="Test Camera",
        clip_ceiling_adu=ProvenancedValue(value=65535.0, provenance=datasheet),
        saturation_threshold_adu=ProvenancedValue(value=SATURATION_ADU, provenance=datasheet),
        sensor_noise=entries,
    )


def _frame_record(path: str, **overrides: object) -> FrameRecord:
    """Build the frame record of a synthetic frame.

    Parameters
    ----------
    path : `str`
        The frame's path.
    **overrides : `object`
        Fields to replace.

    Returns
    -------
    record : `FrameRecord`
        A record with the setting the profile knows.
    """
    fields: dict = {
        "path": path,
        "iso": GAIN_SETTING,
        "offset": "0",
        "exposure": str(EXPOSURE_SECONDS),
        "binning": 1,
        "timestamp": 1_792_000_000.0,
        "pixelScaleArcsec": PIXEL_SCALE_ARCSEC,
        "sensorTemperatureC": -10.0,
        "focuserTemperatureC": 12.5,
    }
    fields.update(overrides)
    return FrameRecord(**fields)


def _draw_stars(
    shape: tuple[int, int],
    star_count: int,
    fwhm_px: float,
    elongation: float,
    angle_rad: float,
    beta: float,
    seed: int,
) -> np.ndarray:
    """Draw Moffat stars onto an empty image, integrated over each pixel.

    Parameters
    ----------
    shape : `tuple` [`int`, `int`]
        The image shape.
    star_count : `int`
        The number of stars, placed at random.
    fwhm_px : `float`
        The geometric-mean width of each star.
    elongation : `float`
        The long axis divided by the short axis.
    angle_rad : `float`
        The long axis angle from the x axis toward the y axis.
    beta : `float`
        The Moffat power index.
    seed : `int`
        The random seed.

    Returns
    -------
    stars : `numpy.ndarray`
        The star light in ADU above the sky.
    """
    generator = np.random.default_rng(seed)
    image = np.zeros(shape)
    scale = 2.0 * math.sqrt(2.0 ** (1.0 / beta) - 1.0)
    alpha_major = fwhm_px * math.sqrt(elongation) / scale
    alpha_minor = fwhm_px / math.sqrt(elongation) / scale
    radius = int(max(fwhm_px * elongation * 6.0, 15))
    subsamples = 5
    offsets = (np.arange(subsamples) + 0.5) / subsamples - 0.5
    for _ in range(star_count):
        center_x = generator.uniform(20, shape[1] - 20)
        center_y = generator.uniform(20, shape[0] - 20)
        flux = 10 ** generator.uniform(4.3, 5.3)
        columns = np.arange(int(center_x) - radius, int(center_x) + radius + 1)
        rows = np.arange(int(center_y) - radius, int(center_y) + radius + 1)
        columns = columns[(columns >= 0) & (columns < shape[1])]
        rows = rows[(rows >= 0) & (rows < shape[0])]
        fine_x = (columns[:, None] + offsets[None, :]).ravel()
        fine_y = (rows[:, None] + offsets[None, :]).ravel()
        delta_x = fine_x[None, :] - center_x
        delta_y = fine_y[:, None] - center_y
        along = delta_x * math.cos(angle_rad) + delta_y * math.sin(angle_rad)
        across = -delta_x * math.sin(angle_rad) + delta_y * math.cos(angle_rad)
        profile = (1.0 + (along / alpha_major) ** 2 + (across / alpha_minor) ** 2) ** (-beta)
        profile = profile.reshape(len(rows), subsamples, len(columns), subsamples).mean(axis=(1, 3))
        profile *= flux / (math.pi * alpha_major * alpha_minor / (beta - 1.0))
        image[np.ix_(rows, columns)] += profile
    return image


def _make_frame_pixels(
    *,
    star_count: int = 90,
    fwhm_px: float = TRUE_WIDTH_PX,
    elongation: float = 1.0,
    angle_rad: float = 0.0,
    beta: float = 3.0,
    seed: int = 7,
) -> np.ndarray:
    """Make a frame in ADU: stars on a sky, with shot noise and read noise.

    Parameters
    ----------
    star_count : `int`, optional
        The number of stars.
    fwhm_px : `float`, optional
        The star width.
    elongation : `float`, optional
        The star stretch.
    angle_rad : `float`, optional
        The stretch angle.
    beta : `float`, optional
        The Moffat power index.
    seed : `int`, optional
        The random seed.

    Returns
    -------
    pixels : `numpy.ndarray`
        The frame in ADU, including the bias level. Shot noise follows the
        electrons (ADU times the gain); read noise is `READ_NOISE_E`.
    """
    shape = (FRAME_SIZE, FRAME_SIZE)
    signal_adu = _draw_stars(shape, star_count, fwhm_px, elongation, angle_rad, beta, seed)
    generator = np.random.default_rng(seed + 1)
    electrons = generator.poisson((signal_adu + SKY_ABOVE_BIAS_ADU) * GAIN_E_PER_ADU)
    electrons = electrons + generator.normal(0.0, READ_NOISE_E, shape)
    return electrons / GAIN_E_PER_ADU + BIAS_LEVEL_ADU


def _write_frame(folder: Path, pixels: np.ndarray, name: str = "M13_light_001.fits") -> str:
    """Write pixels to a FITS file in a lights-style folder.

    Parameters
    ----------
    folder : `pathlib.Path`
        The folder that stands for the frames root.
    pixels : `numpy.ndarray`
        The image.
    name : `str`, optional
        The file name.

    Returns
    -------
    path : `str`
        The file's path.
    """
    target_folder = folder / "lights" / "M 13" / "Scope" / "Camera"
    target_folder.mkdir(parents=True, exist_ok=True)
    path = target_folder / name
    fits.PrimaryHDU(data=pixels.astype(np.float32)).writeto(path)
    return str(path)


def _by_metric(measurements: list[QualityMeasurement]) -> dict[str, list[QualityMeasurement]]:
    """Group measurements by metric name.

    Returns
    -------
    grouped : `dict`
        Metric name to its measurements.
    """
    grouped: dict[str, list[QualityMeasurement]] = {}
    for measurement in measurements:
        grouped.setdefault(measurement.metric, []).append(measurement)
    return grouped


def _run(path: str, **overrides: object) -> list[QualityMeasurement]:
    """Run `measure_frame` on a synthetic frame with the usual arguments.

    Parameters
    ----------
    path : `str`
        The frame's path.
    **overrides : `object`
        Arguments of `measure_frame` to replace.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        What `measure_frame` returned.
    """
    arguments: dict = {
        "path": path,
        "frame": _frame_record(path),
        "target_id": "M 13",
        "camera_profile": _profile(),
        "run_id": "run-1",
        "job_id": "job-1",
        "measured_at": MEASURED_AT,
        "capture_setting_id": 3,
        "bias_level_adu": BIAS_LEVEL_ADU,
        "measure_gaussian_width": measure_fwhm_from_data,
    }
    arguments.update(overrides)
    return measure_frame(**arguments)


@pytest.fixture(scope="module")
def round_frame_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Write one round-star frame for the whole module.

    Returns
    -------
    path : `str`
        The frame's path.
    """
    return _write_frame(tmp_path_factory.mktemp("round"), _make_frame_pixels())


@pytest.fixture(scope="module")
def round_frame_result(round_frame_path: str) -> dict[str, list[QualityMeasurement]]:
    """Measure the round-star frame once.

    Returns
    -------
    grouped : `dict`
        The measurements by metric name.
    """
    return _by_metric(_run(round_frame_path))


def test_subject_id_keeps_the_path_below_the_target_folder() -> None:
    """The id is the target plus the path under its lights folder."""
    subject = frame_subject_id("/mnt/frames/lights/M 13/Scope/Camera/a.fits", "M 13")
    assert subject == "M 13/Scope/Camera/a.fits"


def test_subject_id_without_a_lights_folder_uses_only_the_file_name() -> None:
    """A path outside the library never leaks into the id."""
    assert frame_subject_id("/somewhere/else/a.fits", "M 13") == "M 13/a.fits"


def test_every_declared_frame_metric_is_returned_once(
    round_frame_result: dict[str, list[QualityMeasurement]],
) -> None:
    """A one-plane frame gives one measurement for each declared metric."""
    assert set(round_frame_result) == {spec.name for spec in FRAME_METRICS}
    assert all(len(rows) == 1 for rows in round_frame_result.values())


def test_every_measurement_passes_the_registry_check(
    round_frame_result: dict[str, list[QualityMeasurement]],
) -> None:
    """The write gate accepts everything `measure_frame` returns."""
    for rows in round_frame_result.values():
        for measurement in rows:
            check_measurement_against_registry(measurement)


def test_subject_carries_target_night_setting_and_run(
    round_frame_result: dict[str, list[QualityMeasurement]],
) -> None:
    """The subject and run fields are filled from the arguments."""
    row = round_frame_result[FRAME_BACKGROUND_LEVEL][0]
    assert row.subject.subject_id == "M 13/Scope/Camera/M13_light_001.fits"
    assert row.subject.target_id == "M 13"
    assert row.subject.capture_setting_id == 3
    assert row.subject.channel is None
    assert row.subject.observing_night_id is not None
    assert row.run_id == "run-1"
    assert row.job_id == "job-1"
    assert row.measured_at == MEASURED_AT


def test_star_width_is_recovered_in_pixels_and_arcseconds(
    round_frame_result: dict[str, list[QualityMeasurement]],
) -> None:
    """The Moffat width matches the true 3 px; arcsec uses the scale."""
    width_px = round_frame_result[FRAME_STAR_WIDTH_MOFFAT_PX][0]
    width_arcsec = round_frame_result[FRAME_STAR_WIDTH_MOFFAT_ARCSEC][0]
    assert width_px.status == MeasurementStatus.MEASURED
    assert width_px.value == pytest.approx(TRUE_WIDTH_PX, rel=WIDTH_TOLERANCE_FRACTION)
    assert width_px.sample_count is not None and width_px.sample_count >= 30
    assert width_px.uncertainty is not None and 0.0 < width_px.uncertainty < 0.1
    assert width_arcsec.value == pytest.approx(width_px.value * PIXEL_SCALE_ARCSEC)
    assert "frame record" in width_arcsec.reason


def test_round_stars_read_round(round_frame_result: dict[str, list[QualityMeasurement]]) -> None:
    """Round stars give an elongation near 1 and a weak angle."""
    elongation = round_frame_result[FRAME_STAR_ELONGATION][0]
    assert 1.0 <= elongation.value < 1.06
    assert "coherence" in round_frame_result[FRAME_STAR_ELONGATION_ANGLE_DEG][0].reason


def test_stretched_stars_give_their_elongation_and_angle() -> None:
    """Stars stretched by 1.5 along 0.5 rad are measured, not cut."""
    angle_rad = 0.5
    result = measure_star_profiles(_make_frame_pixels(elongation=1.5, angle_rad=angle_rad), SATURATION_ADU)
    assert result.width_px == pytest.approx(TRUE_WIDTH_PX, rel=WIDTH_TOLERANCE_FRACTION)
    assert result.elongation == pytest.approx(1.5, abs=0.06)
    assert result.angle_deg == pytest.approx(math.degrees(angle_rad), abs=3.0)
    assert result.angle_coherence is not None and result.angle_coherence > 0.95


def test_strongly_trailed_stars_are_still_found() -> None:
    """At a stretch of 2.2, where a roundness cut drops all stars, it works."""
    result = measure_star_profiles(_make_frame_pixels(elongation=2.2, angle_rad=1.0, seed=11), SATURATION_ADU)
    assert result.elongation is not None
    assert result.elongation == pytest.approx(2.2, abs=0.15)
    assert result.width_px == pytest.approx(TRUE_WIDTH_PX, rel=WIDTH_TOLERANCE_FRACTION)


def test_wing_shape_does_not_bias_the_width() -> None:
    """Heavy wings (beta 2) and light wings (beta 5) both read near 3 px."""
    for beta in (2.0, 5.0):
        result = measure_star_profiles(_make_frame_pixels(beta=beta, seed=21), SATURATION_ADU)
        assert result.width_px == pytest.approx(TRUE_WIDTH_PX, rel=WIDTH_TOLERANCE_FRACTION)
        assert result.median_beta == pytest.approx(beta, rel=0.1)


def test_saturated_stars_are_left_out() -> None:
    """With a top level every star reaches, no width is given."""
    result = measure_star_profiles(_make_frame_pixels(), 1000.0)
    assert result.width_px is None
    assert "at least" in result.reason


def test_sky_in_electrons_and_the_two_noise_parts(
    round_frame_result: dict[str, list[QualityMeasurement]],
) -> None:
    """Sky rate and the two noise parts follow from the inputs."""
    sky_electrons = SKY_ABOVE_BIAS_ADU * GAIN_E_PER_ADU
    rate = round_frame_result[FRAME_SKY_RATE_E_PER_S_PER_PX][0]
    noise = round_frame_result[FRAME_SKY_NOISE_E][0]
    read = round_frame_result[FRAME_READ_NOISE_E][0]
    assert rate.status == MeasurementStatus.MEASURED
    assert rate.value == pytest.approx(sky_electrons / EXPOSURE_SECONDS, rel=0.02)
    assert noise.value == pytest.approx(math.sqrt(sky_electrons), rel=0.02)
    assert read.value == READ_NOISE_E
    assert read.status == MeasurementStatus.MEASURED


def test_header_metrics_come_from_the_frame_record(
    round_frame_result: dict[str, list[QualityMeasurement]],
) -> None:
    """Both temperatures are copied from the record."""
    assert round_frame_result[FRAME_SENSOR_TEMPERATURE][0].value == pytest.approx(-10.0)
    assert round_frame_result[FRAME_FOCUSER_TEMPERATURE][0].value == pytest.approx(12.5)


def test_existing_metrics_agree_with_the_functions_that_define_them(
    round_frame_result: dict[str, list[QualityMeasurement]],
) -> None:
    """The four existing frame metrics are reported."""
    background = round_frame_result[FRAME_BACKGROUND_LEVEL][0].value
    assert background == pytest.approx(BIAS_LEVEL_ADU + SKY_ABOVE_BIAS_ADU, rel=0.01)
    assert round_frame_result[FRAME_SATURATED_PIXEL_FRACTION][0].value == pytest.approx(0.0)
    gaussian = round_frame_result[FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX][0]
    assert gaussian.value == pytest.approx(TRUE_WIDTH_PX, rel=0.15)
    assert round_frame_result[FRAME_DETECTED_REGION_COUNT][0].value > 10


def test_assumed_gain_and_read_noise_are_stored_as_assumed(round_frame_path: str) -> None:
    """A gain or read noise that was not measured is stored as assumed."""
    rows = _by_metric(_run(round_frame_path, camera_profile=_profile(noise_kind=ProvenanceKind.ASSUMED)))
    for metric in (FRAME_SKY_RATE_E_PER_S_PER_PX, FRAME_SKY_NOISE_E, FRAME_READ_NOISE_E):
        assert rows[metric][0].status == MeasurementStatus.ASSUMED
        assert rows[metric][0].reason


def test_a_setting_missing_from_the_profile_gives_not_measured_sky_and_read_noise(
    round_frame_path: str,
) -> None:
    """No entry for the setting: no gain is applied and the reason says so."""
    rows = _by_metric(_run(round_frame_path, camera_profile=_profile(with_entry=False)))
    for metric in (FRAME_SKY_RATE_E_PER_S_PER_PX, FRAME_SKY_NOISE_E, FRAME_READ_NOISE_E):
        measurement = rows[metric][0]
        assert measurement.status == MeasurementStatus.NOT_MEASURED
        assert measurement.value is None
        assert "gain 100" in measurement.reason
    assert rows[FRAME_STAR_WIDTH_MOFFAT_PX][0].status == MeasurementStatus.MEASURED


def test_a_nearby_setting_is_not_borrowed(round_frame_path: str) -> None:
    """A frame at gain 101 does not use the entry for gain 100."""
    frame = _frame_record(round_frame_path, iso="101")
    rows = _by_metric(_run(round_frame_path, frame=frame))
    assert rows[FRAME_SKY_RATE_E_PER_S_PER_PX][0].status == MeasurementStatus.NOT_MEASURED


def test_no_camera_profile_gives_not_measured_where_the_profile_is_needed(round_frame_path: str) -> None:
    """Without a profile, the metrics that need it are not measured."""
    rows = _by_metric(_run(round_frame_path, camera_profile=None))
    for metric in (
        FRAME_SATURATED_PIXEL_FRACTION,
        FRAME_SKY_RATE_E_PER_S_PER_PX,
        FRAME_SKY_NOISE_E,
        FRAME_READ_NOISE_E,
    ):
        assert rows[metric][0].status == MeasurementStatus.NOT_MEASURED
        assert rows[metric][0].reason
    assert rows[FRAME_STAR_WIDTH_MOFFAT_PX][0].value is not None


def test_no_bias_level_gives_not_measured_sky_but_still_the_read_noise(round_frame_path: str) -> None:
    """The raw frame does not state its bias level, so the caller gives it."""
    rows = _by_metric(_run(round_frame_path, bias_level_adu=None))
    assert rows[FRAME_SKY_RATE_E_PER_S_PER_PX][0].status == MeasurementStatus.NOT_MEASURED
    assert "bias level" in rows[FRAME_SKY_RATE_E_PER_S_PER_PX][0].reason
    assert rows[FRAME_READ_NOISE_E][0].value == READ_NOISE_E


def test_no_pixel_scale_gives_not_measured_arcseconds_only(round_frame_path: str) -> None:
    """Without a pixel scale only the arcsecond width is not measured."""
    frame = _frame_record(round_frame_path, pixelScaleArcsec=None)
    rows = _by_metric(_run(round_frame_path, frame=frame))
    assert rows[FRAME_STAR_WIDTH_MOFFAT_PX][0].status == MeasurementStatus.MEASURED
    assert rows[FRAME_STAR_WIDTH_MOFFAT_ARCSEC][0].status == MeasurementStatus.NOT_MEASURED
    assert "pixel scale" in rows[FRAME_STAR_WIDTH_MOFFAT_ARCSEC][0].reason


def test_missing_temperatures_are_not_measured(round_frame_path: str) -> None:
    """A record without temperatures gives not_measured rows, not zero."""
    frame = _frame_record(round_frame_path, sensorTemperatureC=None, focuserTemperatureC=None)
    rows = _by_metric(_run(round_frame_path, frame=frame))
    for metric in (FRAME_SENSOR_TEMPERATURE, FRAME_FOCUSER_TEMPERATURE):
        assert rows[metric][0].status == MeasurementStatus.NOT_MEASURED
        assert rows[metric][0].value is None


def test_a_spent_time_budget_skips_the_slow_steps_but_keeps_the_quick_ones(round_frame_path: str) -> None:
    """A spent budget skips the slow steps and keeps the quick ones."""
    rows = _by_metric(_run(round_frame_path, time_budget_seconds=0.0))
    for metric in (
        FRAME_STAR_WIDTH_MOFFAT_PX,
        FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
        FRAME_STAR_ELONGATION,
        FRAME_STAR_ELONGATION_ANGLE_DEG,
        FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
        FRAME_DETECTED_REGION_COUNT,
    ):
        measurement = rows[metric][0]
        assert measurement.status == MeasurementStatus.NOT_MEASURED
        assert measurement.reason.startswith("CPU time budget of 0 s exceeded after ")
    assert rows[FRAME_BACKGROUND_LEVEL][0].value is not None
    assert rows[FRAME_SKY_RATE_E_PER_S_PER_PX][0].value is not None
    assert rows[FRAME_SENSOR_TEMPERATURE][0].value == pytest.approx(-10.0)


def test_without_the_gaussian_function_that_metric_is_not_measured(round_frame_path: str) -> None:
    """The caller passes the width function; this layer cannot import it."""
    rows = _by_metric(_run(round_frame_path, measure_gaussian_width=None))
    measurement = rows[FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX][0]
    assert measurement.status == MeasurementStatus.NOT_MEASURED
    assert "did not pass" in measurement.reason
    assert rows[FRAME_STAR_WIDTH_MOFFAT_PX][0].value is not None


def test_the_time_budget_counts_cpu_time_so_a_waiting_thread_does_not_use_it_up() -> None:
    """Sleeping adds nothing to the stopwatch; computing does."""
    stopwatch = _Stopwatch(0.05)
    time.sleep(0.3)
    assert not stopwatch.expired()
    assert stopwatch.elapsed() < 0.05
    deadline = time.thread_time() + 0.1
    while time.thread_time() < deadline:
        pass
    assert stopwatch.expired()
    assert stopwatch.reason().startswith("CPU time budget of 0.05 s exceeded after ")


def test_the_default_time_budget_is_a_positive_number_of_seconds() -> None:
    """The budget constant is usable as a default."""
    assert FRAME_MEASUREMENT_TIME_BUDGET_SECONDS > 0.0


def test_an_unreadable_file_gives_not_measured_pixel_metrics_and_keeps_the_rest(tmp_path: Path) -> None:
    """A file that is not FITS does not raise; the header metrics return."""
    bad = tmp_path / "lights" / "M 13" / "Scope" / "Camera" / "bad.fits"
    bad.parent.mkdir(parents=True)
    bad.write_text("this is not a FITS file")
    rows = _by_metric(_run(str(bad)))
    assert rows[FRAME_BACKGROUND_LEVEL][0].status == MeasurementStatus.NOT_MEASURED
    assert "could not be read" in rows[FRAME_BACKGROUND_LEVEL][0].reason
    assert rows[FRAME_SENSOR_TEMPERATURE][0].value == pytest.approx(-10.0)
    assert rows[FRAME_READ_NOISE_E][0].value == READ_NOISE_E


def test_a_frame_stored_as_color_planes_is_measured_per_plane(tmp_path: Path) -> None:
    """Three planes give star and sky measurements named R, G and B."""
    plane = _make_frame_pixels(star_count=40)
    cube = np.stack([plane, plane * 1.0, plane * 1.0])
    path = _write_frame(tmp_path, cube)
    rows = _by_metric(_run(path))
    for metric in (FRAME_STAR_WIDTH_MOFFAT_PX, FRAME_STAR_ELONGATION, FRAME_SKY_RATE_E_PER_S_PER_PX):
        assert sorted(row.subject.channel for row in rows[metric]) == ["B", "G", "R"]
    assert [row.subject.channel for row in rows[FRAME_BACKGROUND_LEVEL]] == [None]
    widths = [row.value for row in rows[FRAME_STAR_WIDTH_MOFFAT_PX]]
    assert widths == pytest.approx([TRUE_WIDTH_PX] * 3, rel=0.05)


def test_a_raw_mosaic_is_measured_as_it_is_and_says_so(tmp_path: Path) -> None:
    """A frame with a BAYERPAT card keeps one plane and notes the mix."""
    pixels = _make_frame_pixels(star_count=40)
    target_folder = tmp_path / "lights" / "M 13" / "Scope" / "Camera"
    target_folder.mkdir(parents=True)
    path = target_folder / "mosaic.fits"
    header = fits.Header()
    header["BAYERPAT"] = "RGGB"
    fits.PrimaryHDU(data=pixels.astype(np.float32), header=header).writeto(path)
    rows = _by_metric(_run(str(path)))
    assert [row.subject.channel for row in rows[FRAME_STAR_WIDTH_MOFFAT_PX]] == [None]
    assert "mosaic" in rows[FRAME_STAR_WIDTH_MOFFAT_PX][0].reason
    assert "mosaic" in rows[FRAME_SKY_RATE_E_PER_S_PER_PX][0].reason


# The color filter pattern of the synthetic mosaics: a red, two green and a
# blue pixel per 2 x 2 cell, with the different sensitivities of the real
# filters (red 1.0, green 0.55, blue 0.3).
BAYER_PATTERN_SENSITIVITY = np.array([[1.0, 0.55], [0.55, 0.3]])


def _make_mosaic_pixels(**star_options: object) -> np.ndarray:
    """Make a raw color mosaic: a frame with the filter pattern on its stars.

    Parameters
    ----------
    **star_options : `object`
        Arguments of `_make_frame_pixels` (star width, stretch and so on).

    Returns
    -------
    pixels : `numpy.ndarray`
        The mosaic, with each pixel scaled by its filter's sensitivity.
    """
    pixels = _make_frame_pixels(**star_options)
    tiled = np.tile(BAYER_PATTERN_SENSITIVITY, (pixels.shape[0] // 2, pixels.shape[1] // 2))
    return (pixels - BIAS_LEVEL_ADU) * tiled + BIAS_LEVEL_ADU


def test_a_raw_mosaic_is_measured_on_super_pixels_and_gives_the_true_width() -> None:
    """Summed 2 x 2 cells remove the color pattern; the width is in pixels."""
    mosaic = _make_mosaic_pixels(fwhm_px=4.0)

    binned = measure_star_profiles(mosaic, SATURATION_ADU, bayer_mosaic=True)
    unbinned = measure_star_profiles(mosaic, SATURATION_ADU, bayer_mosaic=False)

    assert binned.width_px == pytest.approx(4.0, rel=0.05)
    assert binned.elongation is not None and binned.elongation < 1.08
    assert binned.star_count >= 20
    # Without the summing, the pattern ruins the fits: too few stars survive.
    assert unbinned.width_px is None


def test_a_stretched_mosaic_star_keeps_its_direction_on_super_pixels() -> None:
    """Summing cells keeps the angle, and reads the stretch a little low."""
    angle_rad = 0.5
    mosaic = _make_mosaic_pixels(fwhm_px=4.0, elongation=1.5, angle_rad=angle_rad)

    result = measure_star_profiles(mosaic, SATURATION_ADU, bayer_mosaic=True)

    assert result.width_px == pytest.approx(4.0, rel=0.05)
    assert result.angle_deg == pytest.approx(math.degrees(angle_rad), abs=3.0)
    # The 2 x 2 sum rounds the star slightly: 1.39 for a true 1.5 at 4 px.
    assert 1.3 < result.elongation < 1.5


def test_the_mosaic_reasons_name_the_super_pixels(tmp_path: Path) -> None:
    """A BAYERPAT frame says its star shapes use 2 x 2 super-pixels."""
    target_folder = tmp_path / "lights" / "M 13" / "Scope" / "Camera"
    target_folder.mkdir(parents=True)
    path = target_folder / "mosaic_binned.fits"
    header = fits.Header()
    header["BAYERPAT"] = "RGGB"
    fits.PrimaryHDU(data=_make_mosaic_pixels(fwhm_px=4.0).astype(np.float32), header=header).writeto(path)

    rows = _by_metric(_run(str(path)))

    width = rows[FRAME_STAR_WIDTH_MOFFAT_PX][0]
    assert width.value == pytest.approx(4.0, rel=0.06)
    assert "2 x 2 super-pixels" in width.reason
    assert "original pixels" in width.reason


def test_the_direction_follows_the_stretched_stars_not_the_round_ones() -> None:
    """A hundred round stars (noise angles) do not hide ten stretched ones."""
    generator = np.random.default_rng(3)
    noise_angle = generator.uniform(0.0, math.pi, 100)
    round_first = 0.01 * np.cos(2.0 * noise_angle)
    round_second = 0.01 * np.sin(2.0 * noise_angle)
    stretch_angle = math.radians(40.0)
    stretched_first = np.full(10, 0.3 * math.cos(2.0 * stretch_angle))
    stretched_second = np.full(10, 0.3 * math.sin(2.0 * stretch_angle))

    angle_deg, coherence = _mean_ellipticity_angle(
        np.concatenate([round_first, stretched_first]), np.concatenate([round_second, stretched_second])
    )

    assert angle_deg == pytest.approx(40.0, abs=3.0)
    assert 0.5 < coherence <= 1.0


def test_a_field_of_exactly_round_stars_has_no_direction_to_report() -> None:
    """With no stretch at all the coherence is 0, not a division error."""
    angle_deg, coherence = _mean_ellipticity_angle(np.zeros(5), np.zeros(5))

    assert 0.0 <= angle_deg < 180.0
    assert coherence == pytest.approx(0.0)


def test_the_star_rows_count_the_sources_each_cut_refused(
    round_frame_result: dict[str, list[QualityMeasurement]],
) -> None:
    """The reason says how many detected sources each cut refused."""
    reason = round_frame_result[FRAME_STAR_WIDTH_MOFFAT_PX][0].reason

    assert "sources detected; refused:" in reason
    for cut in ("near the frame edge", "with a neighbor within 14 px", "near saturation", "by the fit"):
        assert cut in reason


def test_a_crowded_field_refuses_sources_for_having_a_neighbor() -> None:
    """Stars packed closer than 14 px are refused, and the count shows it."""
    result = measure_star_profiles(_make_frame_pixels(star_count=600, seed=5), SATURATION_ADU)

    assert result.refusals.detected > 200
    assert result.refusals.near_neighbor > 50
    assert result.refusals.near_neighbor <= result.refusals.detected


def test_the_sky_is_not_quantized_when_the_sensor_stores_steps_of_four() -> None:
    """A clipped mean varies smoothly; the clipped median jumps in steps."""
    generator = np.random.default_rng(1)
    sky_levels = 800.0 + 0.5 * np.arange(8)
    means = []
    medians = []
    for level in sky_levels:
        electrons = generator.poisson(level * 0.5, (300, 300))
        # A 14-bit sensor in a 16-bit file: stored values are multiples of 4.
        stored = 4.0 * np.round(electrons / 0.5 / 4.0)
        means.append(measure_clipped_mean_adu(stored))
        medians.append(float(sigma_clipped_stats(stored, sigma=3.0)[1]))

    steps = np.diff(means)

    assert np.all(steps > 0.0)
    assert np.allclose(steps, 0.5, atol=0.5)
    assert len({round(value, 2) for value in means}) == len(means)
    # The median of the same data sits on multiples of 4 and takes few values.
    assert all(value == pytest.approx(4.0 * round(value / 4.0)) for value in medians)
    assert len(set(medians)) <= 3


def test_the_clipped_mean_ignores_stars_and_hot_pixels() -> None:
    """Bright outliers are clipped, so they do not raise the level."""
    plane = np.random.default_rng(2).normal(500.0, 5.0, (200, 200))
    plane[:3, :3] = 60000.0

    assert measure_clipped_mean_adu(plane) == pytest.approx(500.0, abs=0.5)


def test_a_frame_with_no_sensor_temperature_has_an_assumed_sky(round_frame_path: str) -> None:
    """No CCD-TEMP means no set-point cooling is known; the sky is assumed."""
    rows = _by_metric(_run(round_frame_path, frame=_frame_record(round_frame_path, sensorTemperatureC=None)))

    for metric in (FRAME_SKY_RATE_E_PER_S_PER_PX, FRAME_SKY_NOISE_E):
        assert rows[metric][0].status == MeasurementStatus.ASSUMED
        assert "uncooled" in rows[metric][0].reason
        assert "dark current" in rows[metric][0].reason
    # Read noise does not depend on the cooling and stays measured.
    assert rows[FRAME_READ_NOISE_E][0].status == MeasurementStatus.MEASURED


def test_measuring_a_frame_writes_nothing(tmp_path: Path) -> None:
    """The file and its folder are unchanged afterwards."""
    path = _write_frame(tmp_path, _make_frame_pixels(star_count=40))
    folder = Path(path).parent

    def listing() -> list[tuple[str, int, int]]:
        """List the folder with each file's change time and size.

        Returns
        -------
        listing : `list` of `tuple`
            Name, change time and size of every entry.
        """
        return sorted(
            (entry.name, entry.stat().st_mtime_ns, entry.stat().st_size) for entry in folder.iterdir()
        )

    before = listing()
    _run(path)
    after = listing()
    assert before == after


def test_sky_electrons_refuses_a_background_below_the_bias() -> None:
    """A background at or under the bias level gives no negative sky."""
    gain = ProvenancedValue(value=0.5, provenance=ValueProvenance(kind=ProvenanceKind.MEASURED, source="x"))
    sky = measure_sky_electrons(90.0, 100.0, gain, 10.0, "")
    assert sky.rate_e_per_s_per_px is None
    assert "not above the bias level" in sky.reason


def test_sky_electrons_refuses_a_missing_exposure_time() -> None:
    """Without an exposure time the rate cannot be given."""
    gain = ProvenancedValue(value=0.5, provenance=ValueProvenance(kind=ProvenanceKind.MEASURED, source="x"))
    sky = measure_sky_electrons(500.0, 100.0, gain, None, "")
    assert sky.rate_e_per_s_per_px is None
    assert "exposure" in sky.reason


def test_lookup_reports_a_profile_entry_that_holds_only_a_gain() -> None:
    """An entry with a gain and no read noise gives the gain and a reason."""
    provenance = ValueProvenance(kind=ProvenanceKind.MEASURED, source="x")
    profile = CameraProfile(
        camera_name="Test Camera",
        clip_ceiling_adu=ProvenancedValue(value=65535.0, provenance=provenance),
        saturation_threshold_adu=ProvenancedValue(value=60000.0, provenance=provenance),
        sensor_noise=(
            SensorSettingNoise(
                gain_setting=GAIN_SETTING,
                gain_e_per_adu=ProvenancedValue(value=0.5, provenance=provenance),
            ),
        ),
    )
    gain, read_noise, reason = look_up_frame_sensor_noise(profile, _frame_record("x.fits"))
    assert gain is not None
    assert gain.value == pytest.approx(0.5)
    assert read_noise is None
    assert "no read noise" in reason


def test_every_new_metric_names_a_function_that_exists() -> None:
    """The seven new declarations each name a function in this module."""
    registry = get_metric_registry()
    for name in (
        FRAME_STAR_WIDTH_MOFFAT_PX,
        FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
        FRAME_STAR_ELONGATION,
        FRAME_STAR_ELONGATION_ANGLE_DEG,
        FRAME_SKY_RATE_E_PER_S_PER_PX,
        FRAME_SKY_NOISE_E,
        FRAME_READ_NOISE_E,
    ):
        assert registry.get(name).computed_by.startswith("pipelines.shared.quality.frame_measurement.")


GOLDEN_FRAME = golden_measurements.SAMPLE_FOLDER / f"{golden_measurements.DETECTION_FRAME}.fits"


def _golden_frame_is_present() -> bool:
    """Say whether the M 13 golden frame is the real file.

    Returns
    -------
    present : `bool`
        `True` when the file exists and is not a Git LFS pointer.
    """
    if not GOLDEN_FRAME.exists():
        return False
    with GOLDEN_FRAME.open("rb") as handle:
        return not handle.read(64).startswith(golden_measurements.LFS_POINTER_PREFIX)


@pytest.mark.skipif(not _golden_frame_is_present(), reason="M 13 golden frame not present (run git lfs pull)")
def test_a_real_golden_frame_runs_and_returns_finite_values() -> None:
    """A real 3008 x 3008 frame measures within the time budget."""
    with fits.open(GOLDEN_FRAME, memmap=False) as hdul:
        header = hdul[0].header
    record = FrameRecord(
        path=str(GOLDEN_FRAME),
        iso="0",
        offset="0",
        exposure=str(header["EXPTIME"]),
        binning=1,
        pixelScaleArcsec=float(header["SCALE"]),
        sensorTemperatureC=float(header["CCD-TEMP"]),
        focuserTemperatureC=float(header["FOCUSTEM"]),
        timestamp=1_792_000_000.0,
    )
    provenance = ValueProvenance(kind=ProvenanceKind.ASSUMED, source="test values, not the camera's")
    datasheet = ValueProvenance(kind=ProvenanceKind.DATASHEET, source="test")
    profile = CameraProfile(
        camera_name="Test ASI533MM",
        clip_ceiling_adu=ProvenancedValue(value=65535.0, provenance=datasheet),
        saturation_threshold_adu=ProvenancedValue(value=65000.0, provenance=datasheet),
        sensor_noise=(
            SensorSettingNoise(
                gain_setting="0",
                offset="0",
                binning="1x1",
                gain_e_per_adu=ProvenancedValue(value=0.9, provenance=provenance),
                read_noise_e=ProvenancedValue(value=3.0, provenance=provenance),
            ),
        ),
    )
    measurements = measure_frame(
        str(GOLDEN_FRAME),
        record,
        "M 13",
        profile,
        run_id="golden",
        job_id=None,
        measured_at=MEASURED_AT,
        capture_setting_id=None,
        bias_level_adu=100.0,
        measure_gaussian_width=measure_fwhm_from_data,
        # A generous budget: this test checks the values, not the speed. The
        # budget's own behavior has its own test, and the speed on real frames
        # is recorded in the validation register. A tight budget here failed
        # when the machine was busy with other test runs.
        time_budget_seconds=600.0,
    )
    by_metric = _by_metric(measurements)
    assert set(by_metric) == {spec.name for spec in FRAME_METRICS}
    for rows in by_metric.values():
        for measurement in rows:
            if measurement.status != MeasurementStatus.NOT_MEASURED:
                assert measurement.value is not None and math.isfinite(measurement.value)
    assert by_metric[FRAME_STAR_WIDTH_MOFFAT_PX][0].status == MeasurementStatus.MEASURED


def test_the_pixel_blur_constant_is_the_theoretical_value() -> None:
    """The constant c is 2 ln 2 / 3, a unit Gaussian's FWHM^2 over 12."""
    fwhm_of_unit_gaussian = 2.0 * math.sqrt(2.0 * math.log(2.0))
    assert PIXEL_BLUR_FWHM_SQUARED_FACTOR == pytest.approx(fwhm_of_unit_gaussian**2 / 12.0)
    assert PIXEL_BLUR_FWHM_SQUARED_FACTOR == pytest.approx(0.4621, abs=1e-4)


def test_the_effective_width_adds_the_pixel_blur_in_quadrature() -> None:
    """The formula adds c p^2 to the squared width, with p = 1 pixel."""
    assert effective_star_width_px(3.0) == pytest.approx(math.sqrt(9.0 + PIXEL_BLUR_FWHM_SQUARED_FACTOR))
    # Near the Nyquist limit (2 px) the pixel term is a 6 percent widening.
    assert effective_star_width_px(2.0) / 2.0 == pytest.approx(1.0562, abs=1e-3)


def test_the_effective_width_rows_carry_the_model_label_and_the_propagated_error(
    round_frame_result: dict[str, list[QualityMeasurement]],
) -> None:
    """Both effective-width rows follow from the Moffat width and say so."""
    moffat = round_frame_result[FRAME_STAR_WIDTH_MOFFAT_PX][0]
    effective_px = round_frame_result[FRAME_STAR_WIDTH_EFFECTIVE_PX][0]
    effective_arcsec = round_frame_result[FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC][0]
    expected = effective_star_width_px(moffat.value)
    assert effective_px.value == pytest.approx(expected)
    assert effective_px.uncertainty == pytest.approx(moffat.uncertainty * moffat.value / expected)
    assert effective_arcsec.value == pytest.approx(expected * PIXEL_SCALE_ARCSEC)
    assert effective_arcsec.uncertainty == pytest.approx(effective_px.uncertainty * PIXEL_SCALE_ARCSEC)
    assert "model-derived" in effective_px.reason
    assert "not fitted" in effective_px.reason
    assert effective_px.sample_count == moffat.sample_count


def test_the_effective_width_in_arcseconds_needs_a_pixel_scale(round_frame_path: str) -> None:
    """Without a pixel scale only the arcsecond row is not measured."""
    rows = _by_metric(_run(round_frame_path, frame=_frame_record(round_frame_path, pixelScaleArcsec=None)))
    assert rows[FRAME_STAR_WIDTH_EFFECTIVE_PX][0].status == MeasurementStatus.MEASURED
    assert rows[FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC][0].status == MeasurementStatus.NOT_MEASURED


def _lattice_frame(wide_corner_width_px: float, narrow_width_px: float = 3.0) -> np.ndarray:
    """Draw a lattice of stars that are wider in the high-x, high-y corner.

    Parameters
    ----------
    wide_corner_width_px : `float`
        The width of the stars whose cell lies in the high-x, high-y corner
        block (cells 10 to 13 of 14 along both axes).
    narrow_width_px : `float`, optional
        The width of all other stars.

    Returns
    -------
    pixels : `numpy.ndarray`
        The frame in ADU with a flat sky, no noise and a bias level.
    """
    size = FRAME_SIZE
    image = np.full((size, size), BIAS_LEVEL_ADU + SKY_ABOVE_BIAS_ADU)
    spacing = 35
    for center_y in range(spacing // 2 + 10, size - 20, spacing):
        for center_x in range(spacing // 2 + 10, size - 20, spacing):
            in_corner = int(center_x / size * 14) >= 10 and int(center_y / size * 14) >= 10
            width = wide_corner_width_px if in_corner else narrow_width_px
            scale = 2.0 * math.sqrt(2.0 ** (1.0 / 3.0) - 1.0)
            alpha = width / scale
            rows, columns = np.mgrid[center_y - 15 : center_y + 16, center_x - 15 : center_x + 16]
            profile = (1.0 + ((columns - center_x) ** 2 + (rows - center_y) ** 2) / alpha**2) ** (-3.0)
            image[rows, columns] += 20000.0 * profile
    return image


def test_the_corner_widths_find_a_wider_corner_and_keep_the_parts() -> None:
    """Stars 50 percent wider in one corner read wider there only."""
    result = measure_star_profiles(_lattice_frame(4.5), SATURATION_ADU)
    regions = {region.region: region for region in measure_field_region_widths(result)}
    assert set(regions) == set(FRAME_STAR_WIDTH_REGION_PX)
    assert regions["xhigh_yhigh"].width_px == pytest.approx(4.5, rel=0.05)
    for name in ("center", "xlow_ylow", "xhigh_ylow", "xlow_yhigh"):
        assert regions[name].width_px == pytest.approx(3.0, rel=0.05)
        assert regions[name].star_count >= 5
    assert regions["xhigh_yhigh"].width_px_uncertainty is not None
    assert sum(region.star_count for region in regions.values()) <= result.star_count


def test_the_region_rows_give_counts_and_errors_per_region(tmp_path: Path) -> None:
    """The five region rows hold the median, its error and the star count."""
    path = _write_frame(tmp_path, _lattice_frame(4.5))
    rows = _by_metric(_run(path))
    corner = rows[FRAME_STAR_WIDTH_REGION_PX["xhigh_yhigh"]][0]
    center = rows[FRAME_STAR_WIDTH_REGION_PX["center"]][0]
    assert corner.status == MeasurementStatus.MEASURED
    assert corner.value == pytest.approx(4.5, rel=0.05)
    assert corner.value > 1.3 * center.value
    assert corner.sample_count is not None and corner.sample_count >= 5
    # The lattice has no noise and identical stars per block, so the stars'
    # widths agree to rounding and the bootstrap error is zero or a few 1e-15,
    # depending on the CPU. Only "not negative" holds on every machine.
    assert corner.uncertainty is not None and corner.uncertainty >= 0.0
    assert "lie in this region" in corner.reason
    for name in FRAME_STAR_WIDTH_REGION_PX.values():
        check_measurement_against_registry(rows[name][0])


def test_a_region_with_too_few_stars_is_not_measured_but_still_counted() -> None:
    """Four stars in a region give no median, only the count."""
    samples = tuple(StarWidthSample(10.0 + index, 10.0, 3.0) for index in range(4))
    result = StarProfileResult(width_px=3.0, star_count=4, samples=samples, field_shape_px=(700, 700))
    regions = {region.region: region for region in measure_field_region_widths(result)}
    assert regions["xlow_ylow"] == RegionWidth("xlow_ylow", 4)
    assert regions["center"].star_count == 0


def test_a_star_between_the_regions_belongs_to_none() -> None:
    """A star at the middle of an edge, outside every block, is left out."""
    samples = tuple(StarWidthSample(350.0, 100.0, 3.0) for _ in range(6))
    result = StarProfileResult(width_px=3.0, star_count=6, samples=samples, field_shape_px=(700, 700))
    assert all(region.star_count == 0 for region in measure_field_region_widths(result))


def test_the_mosaic_star_positions_are_given_in_original_pixels() -> None:
    """A mosaic's stars lie across the whole frame, not its half-size copy."""
    result = measure_star_profiles(_make_mosaic_pixels(fwhm_px=4.0), SATURATION_ADU, bayer_mosaic=True)
    assert result.field_shape_px == (FRAME_SIZE, FRAME_SIZE)
    assert max(sample.x_px for sample in result.samples) > 0.75 * FRAME_SIZE
    assert max(sample.y_px for sample in result.samples) > 0.75 * FRAME_SIZE


def test_the_samples_hold_the_stars_behind_the_median() -> None:
    """Every accepted star is a sample; the median is over their widths."""
    result = measure_star_profiles(_make_frame_pixels(), SATURATION_ADU)
    assert len(result.samples) == result.star_count
    assert float(np.median([sample.width_px for sample in result.samples])) == pytest.approx(result.width_px)
