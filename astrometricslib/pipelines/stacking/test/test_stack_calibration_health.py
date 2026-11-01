"""Tests for the stack's dark and bias gates in `stack_calibration_health.py`.

A small library of synthetic darks and biases is measured by the job, and the
gates are then built for a stack that used them. The camera is the shipped ASI
533 profile (bias level about 395 ADU, read noise about 4.85 ADU at gain 0 and
offset 10).
"""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers.calibration_library import (
    BIAS_FRAME_PATHS_KEY,
    DARK_FRAME_PATHS_KEY,
    CalibrationLibrary,
)
from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib.foundation.config import get_configuration
from astrometricslib.models.quality.gate_result import GateResult, GateStatus
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.stacking.calibration_measurement_run import measure_calibration_frames
from astrometricslib.pipelines.stacking.post_processing.record_quality import GATE_LIMIT_SOURCES
from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import (
    assess_input_quality,
    calibration_gates,
)
from astrometricslib.pipelines.stacking.pre_processing.stack_calibration_health import (
    DARK_MISSING_GATE_NAME,
    TEMPERATURE_SPREAD_GATE_NAME,
    StackCalibrationHealth,
    assess_stack_calibration,
    store_in_diagnostics,
)

MEASURED_AT = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
BIAS_LEVEL_ADU = 395.0
READ_NOISE_ADU = 4.85
VALUE_STEP = 4
FRAME_SHAPE = (150, 200)
LIGHT_EXPOSURE = "30.0"


class _Configuration:
    """A hashable configuration that gives the test camera profiles."""

    def __init__(self) -> None:
        self.app_config = get_configuration().app_config

    def get_minimum_calibration_frames(self) -> int:
        """Give the minimum number of frames for a master.

        Returns
        -------
        minimum : `int`
            Three, the default.
        """
        return 3


def _write(path: Path, seed: int, level: float, band: float = 0.0, **cards: object) -> str:
    """Write one frame with noise in steps of 4 ADU and an optional band.

    Returns
    -------
    path : `str`
        The file's path.
    """
    generator = np.random.default_rng(seed)
    noisy = level + generator.normal(0.0, READ_NOISE_ADU, FRAME_SHAPE)
    noisy[: FRAME_SHAPE[0] // 3, :] += band
    header = fits.Header()
    header["INSTRUME"] = "ZWO CCD ASI533MM Pro"
    header["GAIN"] = 0
    header["OFFSET"] = 10
    for key, value in cards.items():
        header[key.replace("_", "-")] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    fits.PrimaryHDU((np.round(noisy / VALUE_STEP) * VALUE_STEP).astype(np.uint16), header=header).writeto(
        path, overwrite=True
    )
    return str(path)


def _library(tmp_path: Path, *, dark_count: int = 12, leak: bool = False, measure: bool = True) -> tuple:
    """Build and measure a library of biases and darks.

    Returns
    -------
    fixture : `tuple`
        The library, the store, the dark paths and the bias paths.
    """
    from types import SimpleNamespace

    library = CalibrationLibrary(
        app_config=SimpleNamespace(get_library_file_path=lambda name: str(tmp_path / "library" / name))
    )
    biases = []
    for index in range(12):
        path = _write(
            tmp_path / "frames" / "biases" / f"Bias_{index}.fits", index, BIAS_LEVEL_ADU, EXPTIME=0.0
        )
        library.add_bias_frame(path)
        biases.append(path)
    darks = []
    for index in range(dark_count):
        band = 80.0 if leak and index == dark_count - 1 else 0.0
        path = _write(
            tmp_path / "frames" / "darks" / f"Dark_{index}.fits",
            100 + index,
            BIAS_LEVEL_ADU + 0.06,
            band,
            EXPTIME=30.0,
            CCD_TEMP=-10.0,
        )
        library.add_dark_frame(path)
        darks.append(path)
    store = QualityStore(str(tmp_path / "quality.db"))
    if measure:
        measure_calibration_frames(library, store, None, run_id="run", job_id=None, measured_at=MEASURED_AT)
    return library, store, darks, biases


def _lights(
    temperatures: tuple[float, ...] = (-10.0, -9.5, -10.2), exposure: str = LIGHT_EXPOSURE
) -> list[FrameRecord]:
    """Make light frame records of one exposure.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        The records.
    """
    return [
        FrameRecord(path=f"/lights/L_{index}.fits", exposure=exposure, sensorTemperatureC=temperature)
        for index, temperature in enumerate(temperatures)
    ]


def _gates(health: StackCalibrationHealth) -> dict[str, GateResult]:
    """Index the gates by name.

    Returns
    -------
    gates : `dict`
        Gate name to result.
    """
    return {gate.name: gate for gate in health.gates}


def _assess(
    library: CalibrationLibrary,
    store: QualityStore | None,
    darks: list,
    biases: list,
    lights: list,
    **extra: object,
) -> StackCalibrationHealth:
    """Run the stack check with the paths a stack would report.

    Returns
    -------
    health : `StackCalibrationHealth`
        The result.
    """
    diagnostics = {
        DARK_FRAME_PATHS_KEY: darks,
        BIAS_FRAME_PATHS_KEY: biases,
        "calibration_applied": {"dark": bool(darks), "bias": bool(biases)},
        **extra,
    }
    return assess_stack_calibration(diagnostics, lights, library, store, _Configuration())


def test_a_clean_stack_passes_every_calibration_gate(tmp_path: Path) -> None:
    """Measured darks and biases at the profile's level and noise pass."""
    library, store, darks, biases = _library(tmp_path)
    health = _assess(library, store, darks, biases, _lights())
    statuses = {name: gate.status for name, gate in _gates(health).items()}
    assert set(statuses) == {
        "dark_level",
        "dark_noise",
        "dark_zero_pixel_fraction",
        "dark_light_leak",
        "bias_level",
        "bias_noise",
        "bias_zero_pixel_fraction",
        DARK_MISSING_GATE_NAME,
        TEMPERATURE_SPREAD_GATE_NAME,
    }
    assert set(statuses.values()) == {GateStatus.PASSED}
    assert health.unique_counts == {"dark": 12, "bias": 12}
    assert health.count_flags == []


def test_a_light_struck_dark_fails_only_the_light_leak_and_level_gates(tmp_path: Path) -> None:
    """One dark with a bright band is found, and the others stay clean."""
    library, store, darks, biases = _library(tmp_path, leak=True)
    gates = _gates(_assess(library, store, darks, biases, _lights()))
    assert gates["dark_light_leak"].status is GateStatus.FAILED
    assert gates["dark_light_leak"].measured_value > 2.0
    assert gates["dark_zero_pixel_fraction"].status is GateStatus.PASSED
    assert gates["bias_level"].status is GateStatus.PASSED


def test_unmeasured_frames_give_not_checked_with_the_reason(tmp_path: Path) -> None:
    """The stack never measures calibration frames itself."""
    library, store, darks, biases = _library(tmp_path, measure=False)
    gates = _gates(_assess(library, store, darks, biases, _lights()))
    for name in (
        "dark_level",
        "dark_noise",
        "dark_zero_pixel_fraction",
        "dark_light_leak",
        "bias_level",
        "bias_noise",
        "bias_zero_pixel_fraction",
    ):
        assert gates[name].status is GateStatus.NOT_CHECKED
        assert "measuring job" in gates[name].detail


def test_a_stack_with_no_quality_store_checks_only_what_needs_none(tmp_path: Path) -> None:
    """Without a store the frame gates say so; the dark gate still runs."""
    library, _, darks, biases = _library(tmp_path, measure=False)
    gates = _gates(_assess(library, None, darks, biases, _lights()))
    assert gates["dark_level"].status is GateStatus.NOT_CHECKED
    assert "no quality store" in gates["dark_level"].detail
    assert gates[DARK_MISSING_GATE_NAME].status is GateStatus.PASSED


def test_no_library_leaves_every_gate_not_checked(tmp_path: Path) -> None:
    """A stack run without a library records nothing it cannot back."""
    health = assess_stack_calibration({}, _lights(), None, None, _Configuration())
    assert {gate.status for gate in health.gates} == {GateStatus.NOT_CHECKED}


def test_lights_with_no_dark_at_their_exposure_fail_the_missing_dark_gate(tmp_path: Path) -> None:
    """Darks of 30 s do not serve lights of 50 s; the gate counts lights."""
    library, store, darks, biases = _library(tmp_path)
    gates = _gates(_assess(library, store, darks, biases, _lights(exposure="50.0")))
    gate = gates[DARK_MISSING_GATE_NAME]
    assert gate.status is GateStatus.FAILED
    assert gate.measured_value == pytest.approx(3.0)
    assert "50 s" in gate.detail


def test_a_stack_with_no_dark_at_all_fails_the_missing_dark_gate(tmp_path: Path) -> None:
    """Lights stacked with only a bias, or nothing, are warned about."""
    library, store, _, biases = _library(tmp_path)
    gates = _gates(_assess(library, store, [], biases, _lights()))
    assert gates[DARK_MISSING_GATE_NAME].status is GateStatus.FAILED
    assert gates["dark_level"].status is GateStatus.NOT_CHECKED
    assert gates[TEMPERATURE_SPREAD_GATE_NAME].status is GateStatus.NOT_CHECKED


def test_a_temperature_spread_over_three_degrees_fails(tmp_path: Path) -> None:
    """The mean can hide drift: lights from -10.7 to -2.9 C span 7.8 C."""
    library, store, darks, biases = _library(tmp_path)
    gates = _gates(_assess(library, store, darks, biases, _lights((-10.7, -6.0, -2.9))))
    gate = gates[TEMPERATURE_SPREAD_GATE_NAME]
    assert gate.status is GateStatus.FAILED
    assert gate.measured_value == pytest.approx(7.8)


def test_lights_with_no_temperature_cannot_be_checked_for_spread(tmp_path: Path) -> None:
    """A DSLR records no sensor temperature, so the gate is not checked."""
    library, store, darks, biases = _library(tmp_path)
    lights = [FrameRecord(path=f"/lights/L_{index}.fits", exposure=LIGHT_EXPOSURE) for index in range(3)]
    gates = _gates(_assess(library, store, darks, biases, lights))
    assert gates[TEMPERATURE_SPREAD_GATE_NAME].status is GateStatus.NOT_CHECKED


def test_a_thin_dark_master_extends_the_frame_count_gate(tmp_path: Path) -> None:
    """Five unique darks reach the minimum of three but are thin."""
    library, store, darks, biases = _library(tmp_path, dark_count=5)
    health = _assess(library, store, darks, biases, _lights())
    assert len(health.count_flags) == 1
    assert "thin set" in health.count_flags[0]
    diagnostics: dict = {"calibration_applied": {"dark": True}}
    store_in_diagnostics(diagnostics, health)
    gates = {gate.name: gate for gate in calibration_gates(diagnostics)}
    assert gates["calibration_frame_count"].status is GateStatus.FAILED
    assert gates["calibration_frame_count"].measured_value == pytest.approx(1.0)
    assert gates["dark_level"].status is GateStatus.PASSED


def test_a_master_under_the_minimum_keeps_its_old_flag_only(tmp_path: Path) -> None:
    """Two darks are flagged by the engine already, so no second flag."""
    library, store, darks, biases = _library(tmp_path, dark_count=2)
    health = _assess(library, store, darks, biases, _lights())
    assert health.count_flags == []


def test_the_input_quality_carries_the_unique_counts(tmp_path: Path) -> None:
    """The numbers a rule re-judges the thin limit with are stored."""
    library, store, darks, biases = _library(tmp_path, dark_count=5)
    diagnostics: dict = {}
    store_in_diagnostics(diagnostics, _assess(library, store, darks, biases, _lights()))
    quality = assess_input_quality(3, 3, [], None, diagnostics)
    assert quality.dark_unique_frame_count == 5
    assert quality.bias_unique_frame_count == 12


def test_a_stack_without_calibration_health_has_only_the_old_gates() -> None:
    """A caller that did not judge the frames sees none of the new gates."""
    names = {gate.name for gate in calibration_gates({})}
    assert names == {"flat_level", "flat_noise", "calibration_metadata", "calibration_frame_count"}


def test_every_new_gate_has_a_limit_source_and_a_metric() -> None:
    """Each new gate has a limit source and a declared metric."""
    from astrometricslib.pipelines.stacking.metrics import GATE_METRIC_BY_NAME

    names = (
        "dark_level",
        "dark_noise",
        "dark_zero_pixel_fraction",
        "dark_light_leak",
        "bias_level",
        "bias_noise",
        "bias_zero_pixel_fraction",
        DARK_MISSING_GATE_NAME,
        TEMPERATURE_SPREAD_GATE_NAME,
    )
    for name in names:
        assert name in GATE_LIMIT_SOURCES
        assert name in GATE_METRIC_BY_NAME
