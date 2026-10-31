"""Tests for measuring gain and read noise from the calibration library.

They write small synthetic flat and bias frames into a temporary frames
folder, scan them into a library, and check that the right frames are found,
that reading writes nothing, and that ``save`` writes one entry into a
temporary config.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.api.processing import CalibrationCatalog
from astrometricslib.drivers import camera_profile_store
from astrometricslib.drivers.camera_profile_store import load_camera_profiles
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.camera_profile import ProvenancedValue, ValueProvenance
from astrometricslib.models.quality.measurement import MeasurementStatus
from astrometricslib.pipelines.stacking import sensor_noise_run
from astrometricslib.pipelines.stacking.sensor_noise_run import measure_sensor_noise_from_library

CAMERA = "Test Camera"
TRUE_GAIN = 0.8
TRUE_READ_NOISE_ADU = 4.0
FRAME_SIDE = 200


def write_frame(
    path: Path,
    generator: np.random.Generator,
    is_flat: bool,
    gain: int = 0,
    exposure: float = 0.5,
    taken_after_seconds: float = 0.0,
) -> None:
    """Write one synthetic calibration frame with the headers Ekos records.

    Parameters
    ----------
    path : `pathlib.Path`
        File to write. Its folder is created.
    generator : `numpy.random.Generator`
        The seeded source of noise.
    is_flat : `bool`
        `True` for a flat (8000 electrons of signal), `False` for a bias.
    gain : `int`, optional
        The ``GAIN`` header value.
    exposure : `float`, optional
        The ``EXPTIME`` header value.
    taken_after_seconds : `float`, optional
        Seconds after a fixed start time that the ``DATE-OBS`` card records.
    """
    electrons = generator.poisson(8000.0, (FRAME_SIDE, FRAME_SIDE)) if is_flat else 0.0
    data = (
        1000.0 + electrons / TRUE_GAIN + generator.normal(0.0, TRUE_READ_NOISE_ADU, (FRAME_SIDE, FRAME_SIDE))
    )
    header = fits.Header()
    header["INSTRUME"] = CAMERA
    header["FILTER"] = "Luminance"
    header["GAIN"] = gain
    header["OFFSET"] = 10
    header["EXPTIME"] = exposure
    header["CCD-TEMP"] = -10.0
    header["DATE-OBS"] = (
        (datetime(2026, 10, 3, 3, 0, 0, tzinfo=UTC) + timedelta(seconds=taken_after_seconds))
        .replace(tzinfo=None)
        .isoformat(timespec="milliseconds")
    )
    header["IMAGETYP"] = "Flat Frame" if is_flat else "Bias Frame"
    path.parent.mkdir(parents=True, exist_ok=True)
    fits.writeto(path, np.round(data).astype(np.uint16), header, overwrite=True)


@pytest.fixture(autouse=True)
def profile_without_linearity_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every camera a profile with no linearity limit.

    The tests pass a stand-in configuration that cannot be used to look up
    profiles, and the synthetic flats (about 11,000 ADU) are far below any
    real limit anyway.
    """
    generic = next(p for p in load_camera_profiles() if p.is_generic_fallback)
    monkeypatch.setattr(sensor_noise_run, "resolve_camera_profile", lambda name, config=None: generic)


@pytest.fixture
def frames_folder(tmp_path: Path) -> Path:
    """Write 12 flats (6 pairs) and 8 biases (4 pairs) at gain 0.

    Returns
    -------
    folder : `pathlib.Path`
        The frames folder the library scans.
    """
    generator = np.random.default_rng(42)
    for index in range(12):
        write_frame(
            tmp_path / "frames" / "flats" / "Scope" / CAMERA / "L" / f"flat_{index:02d}.fits",
            generator,
            True,
            taken_after_seconds=10.0 * index,
        )
    for index in range(8):
        write_frame(tmp_path / "frames" / "biases" / CAMERA / f"bias_{index:02d}.fits", generator, False)
    return tmp_path


@pytest.fixture
def catalog(frames_folder: Path) -> CalibrationCatalog:
    """Make a catalog over the synthetic frames, with its library scanned.

    Returns
    -------
    catalog : `CalibrationCatalog`
        A catalog on a stub configuration.
    """
    configuration = SimpleNamespace(
        get_frames_path=lambda: frames_folder / "frames",
        get_library_file_path=lambda name: str(frames_folder / "library" / name),
    )
    calibration_catalog = CalibrationCatalog(configuration, None)
    calibration_catalog.refresh("flat")
    calibration_catalog.refresh("bias")
    return calibration_catalog


def test_the_catalog_measures_a_setting_from_its_frames(catalog: CalibrationCatalog) -> None:
    """Frames at gain 0 and offset 10 give the made-up values."""
    result = catalog.measure_sensor_noise(CAMERA, gain=0, offset=10.0, register_job=False)

    assert result.status == MeasurementStatus.MEASURED, result.reason
    assert result.gain_e_per_adu == pytest.approx(TRUE_GAIN, rel=0.03)
    assert result.read_noise_adu == pytest.approx(TRUE_READ_NOISE_ADU, rel=0.03)
    assert result.flat_pair_count == 6
    assert result.bias_pair_count == 4
    assert (result.gain_setting, result.offset, result.binning) == ("0", "10", "1x1")
    assert result.saved is False


def test_a_setting_with_no_frames_is_not_measured(catalog: CalibrationCatalog) -> None:
    """A setting with no flats is not measured or borrowed."""
    result = catalog.measure_sensor_noise(CAMERA, gain=100, offset=10, register_job=False)

    assert result.status == MeasurementStatus.NOT_MEASURED
    assert "no flats" in result.reason


def test_a_setting_with_flats_but_no_biases_is_not_measured(
    catalog: CalibrationCatalog, frames_folder: Path
) -> None:
    """Biases at another gain are not used in place of biases at this gain."""
    generator = np.random.default_rng(7)
    for index in range(12):
        flat_path = frames_folder / "frames" / "flats" / "Scope" / CAMERA / "L" / f"high_{index:02d}.fits"
        write_frame(flat_path, generator, True, gain=100, taken_after_seconds=10.0 * index)
    catalog.refresh("flat")

    result = catalog.measure_sensor_noise(CAMERA, gain=100, offset=10, register_job=False)

    assert result.status == MeasurementStatus.NOT_MEASURED
    assert "bias" in result.reason


def test_mixed_flat_exposures_need_an_exposure_to_choose(
    catalog: CalibrationCatalog, frames_folder: Path
) -> None:
    """Flats of two exposures are refused until the caller names one."""
    generator = np.random.default_rng(8)
    for index in range(12):
        flat_path = frames_folder / "frames" / "flats" / "Scope" / CAMERA / "L" / f"long_{index:02d}.fits"
        write_frame(flat_path, generator, True, exposure=1.0, taken_after_seconds=20000.0 + 10.0 * index)
    catalog.refresh("flat")

    mixed = catalog.measure_sensor_noise(CAMERA, gain=0, offset=10, register_job=False)
    chosen = catalog.measure_sensor_noise(CAMERA, gain=0, offset=10, exposure_seconds=1.0, register_job=False)

    assert mixed.status == MeasurementStatus.NOT_MEASURED
    assert "exposure" in mixed.reason
    assert chosen.status == MeasurementStatus.MEASURED
    assert chosen.flat_pair_count == 6


def test_reading_writes_nothing_and_saving_writes_one_entry(
    catalog: CalibrationCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading leaves the config alone; saving adds one entry."""
    config_path = tmp_path / "astrometrics.config.toml"
    config_path.write_text(
        '["Observatory.Camera.Fallback"]\nname = "Fallback"\nis_generic_fallback = "true"\n'
        'clip_ceiling_adu = { value = 65535.0, kind = "assumed", source = "a test" }\n'
        'saturation_threshold_adu = { value = 65000.0, kind = "assumed", source = "a test" }\n'
        f'["Observatory.Camera.{CAMERA}"]\nname = "{CAMERA}"\n'
        'clip_ceiling_adu = { value = 65535.0, kind = "assumed", source = "a test" }\n'
        'saturation_threshold_adu = { value = 65000.0, kind = "assumed", source = "a test" }\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    camera_profile_store.load_profiles_from_config.cache_clear()
    config = AppConfiguration()
    original_text = config_path.read_text(encoding="utf-8")

    unsaved = measure_sensor_noise_from_library(
        catalog.library, config, CAMERA, 0, 10, measured_on=date(2026, 10, 22), save=False
    )
    assert unsaved.status == MeasurementStatus.MEASURED
    assert config_path.read_text(encoding="utf-8") == original_text
    assert list(tmp_path.glob("*.bak")) == []

    saved = measure_sensor_noise_from_library(
        catalog.library, config, CAMERA, 0, 10, measured_on=date(2026, 10, 22), save=True
    )

    assert saved.saved is True
    assert saved.backup_path is not None and Path(saved.backup_path).is_file()
    profile = next(p for p in load_camera_profiles(AppConfiguration()) if p.camera_name == CAMERA)
    entry = profile.sensor_noise_for("0", "10", "1x1")
    assert entry.gain_e_per_adu.value == pytest.approx(saved.gain_e_per_adu)
    assert entry.read_noise_e.value == pytest.approx(saved.read_noise_e)
    assert entry.gain_e_per_adu.provenance.kind == "measured"
    assert "measure_sensor_noise" in entry.gain_e_per_adu.provenance.source
    # The spread, the gain of each pair and the robust read noise are kept.
    assert entry.gain_spread_e_per_adu == pytest.approx(saved.gain_spread_e_per_adu)
    assert list(entry.pair_gains_e_per_adu) == pytest.approx(saved.pair_gains_e_per_adu)
    assert entry.read_noise_adu_robust == pytest.approx(saved.read_noise_adu_robust)
    assert list(entry.pair_read_noises_adu) == pytest.approx(saved.pair_read_noises_adu)
    assert len(entry.pair_gains_e_per_adu) == saved.flat_pair_count


def test_flats_without_a_time_cannot_be_paired_and_say_so(
    catalog: CalibrationCatalog, frames_folder: Path
) -> None:
    """Flats with no DATE-OBS are left out and counted, not paired by name."""
    for path in (frames_folder / "frames" / "flats").rglob("*.fits"):
        with fits.open(path, mode="update") as hdul:
            del hdul[0].header["DATE-OBS"]
    catalog.refresh("flat")

    result = catalog.measure_sensor_noise(CAMERA, gain=0, offset=10, register_job=False)

    assert result.status == MeasurementStatus.NOT_MEASURED
    assert any("no readable DATE-OBS" in note for note in result.notes)


def test_flats_above_the_profile_linearity_limit_are_refused(
    catalog: CalibrationCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The profile's linearity limit reaches the measurement."""
    generic = next(p for p in load_camera_profiles() if p.is_generic_fallback)
    limited = generic.model_copy(
        update={
            "photometric_linearity_limit_adu": ProvenancedValue(
                value=3000.0, provenance=ValueProvenance(kind="datasheet", source="a test")
            )
        }
    )
    monkeypatch.setattr(sensor_noise_run, "resolve_camera_profile", lambda name, config=None: limited)

    result = catalog.measure_sensor_noise(CAMERA, gain=0, offset=10, register_job=False)

    assert result.status == MeasurementStatus.NOT_MEASURED
    assert "photometric linearity limit of 3000 ADU" in result.reason
