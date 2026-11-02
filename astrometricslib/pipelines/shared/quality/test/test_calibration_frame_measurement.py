"""Tests for the dark and bias frame measurement.

The code under test is `calibration_frame_measurement.py`.

The frames are small synthetic arrays with a known level, noise, zero share
and light leak, so each metric can be checked against the number that built it.
"""

import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.camera_profile import (
    CameraProfile,
    ProvenancedValue,
    ProvenanceKind,
    ValueProvenance,
)
from astrometricslib.models.quality.measurement import MeasurementStatus, QualityMeasurement
from astrometricslib.pipelines.shared.quality.calibration_frame_measurement import (
    calibration_metric_set_fingerprint,
    calibration_subject_id,
    measure_block_range,
    measure_calibration_frame,
    measure_neighbor_noise,
    measure_value_step,
)
from astrometricslib.pipelines.shared.quality.frame_measurement import UNREADABLE_FRAME_REASON_PREFIX
from astrometricslib.pipelines.shared.quality.metrics import (
    CALIBRATION_FRAME_METRIC_NAMES,
    FRAME_BACKGROUND_LEVEL,
    FRAME_BLOCK_RANGE,
    FRAME_CLIPPED_MEAN_LEVEL,
    FRAME_NEIGHBOR_NOISE,
    FRAME_PIXEL_CHECKSUM,
    FRAME_SATURATED_PIXEL_FRACTION,
    FRAME_SENSOR_TEMPERATURE,
    FRAME_VALUE_STEP,
    FRAME_ZERO_PIXEL_FRACTION,
)
from astrometricslib.pipelines.shared.quality.record_measurements import check_measurement_against_registry

MEASURED_AT = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
BIAS_LEVEL_ADU = 400.0
VALUE_STEP_ADU = 4
NOISE_ADU = 5.0
FRAME_SHAPE = (600, 800)
SATURATION_ADU = 65000.0

# The noise estimate of a few hundred thousand pixels is good to well under
# one percent; 3 percent leaves room for the random draw.
NOISE_TOLERANCE_FRACTION = 0.03


def _profile() -> CameraProfile:
    """Build a camera profile with a saturation level.

    Returns
    -------
    profile : `CameraProfile`
        A profile whose pixels saturate at `SATURATION_ADU`.
    """
    datasheet = ValueProvenance(kind=ProvenanceKind.DATASHEET, source="test")
    return CameraProfile(
        camera_name="Test Camera",
        clip_ceiling_adu=ProvenancedValue(value=65535.0, provenance=datasheet),
        saturation_threshold_adu=ProvenancedValue(value=SATURATION_ADU, provenance=datasheet),
    )


def _bias_pixels(seed: int = 1) -> np.ndarray:
    """Draw a bias-like frame with a known level, noise and value step.

    Parameters
    ----------
    seed : `int`, optional
        The random seed.

    Returns
    -------
    pixels : `numpy.ndarray`
        Whole-number values in steps of `VALUE_STEP_ADU` around
        `BIAS_LEVEL_ADU`.
    """
    generator = np.random.default_rng(seed)
    noisy = BIAS_LEVEL_ADU + generator.normal(0.0, NOISE_ADU, FRAME_SHAPE)
    return (np.round(noisy / VALUE_STEP_ADU) * VALUE_STEP_ADU).astype(np.uint16)


def _write(path: Path, pixels: np.ndarray, **cards: object) -> str:
    """Write pixels to a FITS file with the given header cards.

    Returns
    -------
    path : `str`
        The file's path.
    """
    header = fits.Header()
    for key, value in cards.items():
        header[key.replace("_", "-")] = value
    fits.PrimaryHDU(pixels, header=header).writeto(path, overwrite=True)
    return str(path)


def _measure(
    path: str, kind: str = "bias", profile: CameraProfile | None = None
) -> dict[str, QualityMeasurement]:
    """Measure a frame and index the rows by metric name.

    Returns
    -------
    by_metric : `dict`
        Metric name to measurement.
    """
    rows = measure_calibration_frame(
        path,
        kind,
        profile if profile is not None else _profile(),
        run_id="run",
        job_id=None,
        measured_at=MEASURED_AT,
    )
    return {row.metric: row for row in rows}


def test_every_calibration_metric_is_returned_and_registered(tmp_path: Path) -> None:
    """Each declared metric gets one row that the registry accepts."""
    (tmp_path / "darks" / "ZWO").mkdir(parents=True)
    path = _write(tmp_path / "darks" / "ZWO" / "Dark_001.fits", _bias_pixels(), CCD_TEMP=-10.0)
    rows = measure_calibration_frame(
        path, "dark", _profile(), run_id="run", job_id=None, measured_at=MEASURED_AT
    )
    assert [row.metric for row in rows] == list(CALIBRATION_FRAME_METRIC_NAMES)
    for row in rows:
        check_measurement_against_registry(row)
        assert row.subject.target_id is None
        assert row.subject.subject_id == "calibration/dark/ZWO/Dark_001.fits"


def test_values_match_the_frame_that_built_them(tmp_path: Path) -> None:
    """Level, noise, step, zero share and temperature match the input."""
    by_metric = _measure(_write(tmp_path / "Bias_001.fits", _bias_pixels(), CCD_TEMP=-9.5))
    assert by_metric[FRAME_CLIPPED_MEAN_LEVEL].value == pytest.approx(BIAS_LEVEL_ADU, abs=0.5)
    assert by_metric[FRAME_BACKGROUND_LEVEL].value == pytest.approx(BIAS_LEVEL_ADU, abs=VALUE_STEP_ADU)
    assert by_metric[FRAME_NEIGHBOR_NOISE].value == pytest.approx(NOISE_ADU, rel=NOISE_TOLERANCE_FRACTION)
    assert by_metric[FRAME_VALUE_STEP].value == VALUE_STEP_ADU
    assert by_metric[FRAME_ZERO_PIXEL_FRACTION].value == pytest.approx(0.0)
    assert by_metric[FRAME_SATURATED_PIXEL_FRACTION].value == pytest.approx(0.0)
    assert by_metric[FRAME_BLOCK_RANGE].value <= 2 * VALUE_STEP_ADU
    assert by_metric[FRAME_SENSOR_TEMPERATURE].value == pytest.approx(-9.5)


def test_a_zero_clipped_frame_reports_its_zero_share(tmp_path: Path) -> None:
    """A frame cut off at zero reports a large zero share."""
    generator = np.random.default_rng(3)
    clipped = np.maximum(np.round(generator.normal(-10.0, NOISE_ADU, FRAME_SHAPE)), 0).astype(np.uint16)
    by_metric = _measure(_write(tmp_path / "Bias_zero.fits", clipped))
    assert by_metric[FRAME_ZERO_PIXEL_FRACTION].value > 0.9
    assert by_metric[FRAME_SENSOR_TEMPERATURE].status is MeasurementStatus.NOT_MEASURED
    assert by_metric[FRAME_SENSOR_TEMPERATURE].reason


def test_a_light_leak_raises_the_block_range() -> None:
    """A bright band across the top third raises the block range."""
    pixels = _bias_pixels().astype(np.float32)
    flat_range = measure_block_range(pixels)
    pixels[: FRAME_SHAPE[0] // 3, :] += 60.0
    assert measure_block_range(pixels) > flat_range + 50.0


def test_neighbor_noise_ignores_a_smooth_gradient() -> None:
    """A smooth ramp cancels in the neighbor difference."""
    generator = np.random.default_rng(5)
    ramp = np.linspace(0.0, 200.0, FRAME_SHAPE[1])[None, :] + np.zeros(FRAME_SHAPE)
    pixels = ramp + generator.normal(0.0, NOISE_ADU, FRAME_SHAPE)
    assert measure_neighbor_noise(pixels) == pytest.approx(NOISE_ADU, rel=0.1)
    assert measure_neighbor_noise(np.zeros((4, 2))) is None


def test_value_step_is_none_for_a_constant_block() -> None:
    """A block that holds one value has no step."""
    assert measure_value_step(np.full((400, 400), 7.0)) is None


def test_the_checksum_tells_a_copy_from_another_exposure(tmp_path: Path) -> None:
    """Two copies share a checksum; another exposure differs."""
    checksums = [
        _measure(_write(tmp_path / name, _bias_pixels(seed)))[FRAME_PIXEL_CHECKSUM].value
        for name, seed in (("a.fits", 1), ("b.fits", 1), ("c.fits", 2))
    ]
    assert checksums[0] == checksums[1]
    assert checksums[0] != checksums[2]
    assert all(value is not None and math.isfinite(value) and value >= 0 for value in checksums)


def test_an_unreadable_frame_is_marked_so_the_job_retries_it(tmp_path: Path) -> None:
    """A missing file gives rows that start with the unreadable text."""
    rows = measure_calibration_frame(
        str(tmp_path / "missing.fits"), "dark", _profile(), run_id="run", job_id=None, measured_at=MEASURED_AT
    )
    assert len(rows) == len(CALIBRATION_FRAME_METRIC_NAMES)
    assert all(row.status is MeasurementStatus.NOT_MEASURED for row in rows)
    assert all(row.reason.startswith(UNREADABLE_FRAME_REASON_PREFIX) for row in rows)


def test_a_missing_profile_leaves_only_the_saturated_share_unmeasured(tmp_path: Path) -> None:
    """Without a profile only the saturated share is lost."""
    rows = measure_calibration_frame(
        _write(tmp_path / "Bias_001.fits", _bias_pixels()),
        "bias",
        None,
        run_id="run",
        job_id=None,
        measured_at=MEASURED_AT,
    )
    by_metric = {row.metric: row for row in rows}
    assert by_metric[FRAME_SATURATED_PIXEL_FRACTION].status is MeasurementStatus.NOT_MEASURED
    assert by_metric[FRAME_CLIPPED_MEAN_LEVEL].status is MeasurementStatus.MEASURED


@pytest.mark.parametrize(
    ("path", "kind", "expected"),
    [
        (
            "/mnt/a/frames/darks/ZWO ASI 533MM Pro/Dark_1.fits",
            "dark",
            "calibration/dark/ZWO ASI 533MM Pro/Dark_1.fits",
        ),
        ("/mnt/b/other/frames/biases/Nikon/x/Bias_2.fits", "bias", "calibration/bias/Nikon/x/Bias_2.fits"),
        ("/somewhere/Dark_3.fits", "dark", "calibration/dark/Dark_3.fits"),
    ],
)
def test_subject_id_ignores_where_the_frames_root_is(path: str, kind: str, expected: str) -> None:
    """The id keeps what follows the kind folder, so a moved root is fine."""
    assert calibration_subject_id(path, kind) == expected


def test_the_metric_set_fingerprint_is_stable() -> None:
    """The fingerprint is a short hexadecimal string that stays the same."""
    assert calibration_metric_set_fingerprint() == calibration_metric_set_fingerprint()
    assert len(calibration_metric_set_fingerprint()) == 12
