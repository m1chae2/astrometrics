"""Purpose: Unit tests for the stacking engine contract and its Siril adapter.

Description: The stacking pipeline talks to an engine through `StackingEngine`.
These tests check the settings and result objects, that the Siril adapter
passes the settings to the driver and returns the driver's diagnostics in the
result, and that the pipeline's runner works with an engine that is not Siril
at all.
"""

from pathlib import Path
from typing import Any

import pytest

from astrometricslib.drivers.siril_stacking_engine import SirilStackingEngine
from astrometricslib.drivers.stacking_engine import StackRunResult, StackSettings
from astrometricslib.pipelines.stacking.stack_runner import run_stack


class FakeSirilDriver:
    """A stand-in for `ImageProcessing` that records its call."""

    siril_executable = "siril-not-installed"

    def __init__(self) -> None:
        """Start with no calls and empty diagnostics."""
        self.calls: list[dict[str, Any]] = []
        self.last_run_diagnostics: dict[str, Any] = {"num_lights": 3}

    def process_target(self, **options: Any) -> str:
        """Record the options and return a path.

        Returns
        -------
        path : `str`
            A made-up stack path.
        """
        self.calls.append(options)
        return "/stacks/M_42_L_Stacked.fits"


class OtherEngine:
    """A stacking engine that is not Siril. It writes a small FITS file."""

    name = "OtherStacker"

    def __init__(self, directory: Path) -> None:
        """Remember where to write stacks."""
        self.directory = directory
        self.batches: list[tuple[int, str | None]] = []

    def version(self) -> str | None:
        """Report a made-up version.

        Returns
        -------
        version : `str`
            The version.
        """
        return "9.9"

    def read_stack_artifacts(self, stacked_path: str) -> dict[str, Any]:
        """Report one frame's registration and a rejected share.

        Returns
        -------
        artifacts : `dict`
            Made-up values.
        """
        return {"registration_frames": [{"dx": 0.0}], "rejected_pixel_fraction": 0.02}

    def stack_batch(
        self,
        frames: list[Any],
        target_id: str,
        output_file: str,
        log_file: str | None,
        is_spectral: bool,
        settings: StackSettings,
        registration: str | None = None,
        job_id: str | None = None,
    ) -> StackRunResult:
        """Write a stack and report it.

        Returns
        -------
        result : `StackRunResult`
            A stack of constant pixels.
        """
        import numpy as np
        from astropy.io import fits

        self.batches.append((len(frames), registration))
        path = self.directory / output_file
        fits.writeto(path, np.full((20, 20), 5.0, dtype=np.float32), overwrite=True)
        return StackRunResult(str(path), {"num_lights": len(frames)}, self.name, self.version())


def test_settings_list_only_the_options_that_were_given() -> None:
    """`as_options` leaves out the settings that are `None`."""
    options = StackSettings(rejection_sigma=(3.0, 3.0), generate_rejmap=True).as_options()
    assert options == {"rejection_sigma": (3.0, 3.0), "generate_rejmap": True}


def test_the_siril_adapter_passes_settings_and_returns_diagnostics() -> None:
    """Settings reach `process_target`; diagnostics come back in the result."""
    driver = FakeSirilDriver()
    engine = SirilStackingEngine(driver)
    result = engine.stack_batch(
        [{"path": "a.fits"}],
        "M 42",
        "M_42_L_Stacked.fits",
        None,
        False,
        StackSettings(filter_wfwhm="90%"),
        registration="relaxed",
    )
    assert result.stacked_path == "/stacks/M_42_L_Stacked.fits"
    assert result.diagnostics == {"num_lights": 3}
    assert result.engine_name == "Siril"
    call = driver.calls[0]
    assert call["filter_wfwhm"] == "90%"
    assert call["spectral_star_detection"] == "relaxed"
    assert call["is_spectral"] is False


def test_the_adapter_leaves_registration_out_when_not_given() -> None:
    """No registration name means no ``spectral_star_detection`` option."""
    driver = FakeSirilDriver()
    SirilStackingEngine(driver).stack_batch([], "M 42", "out.fits", None, False, StackSettings())
    assert "spectral_star_detection" not in driver.calls[0]


def test_the_runner_works_with_an_engine_that_is_not_siril(tmp_path: Path) -> None:
    """The runner needs only the engine contract, and records the engine."""
    engine = OtherEngine(tmp_path)
    frames = [{"path": "a.fits", "exposure": "30"}, {"path": "b.fits", "exposure": "30"}]
    path, diagnostics = run_stack(engine, frames, "M 42", "M_42_L_Stacked.fits", None, False)
    assert path == str(tmp_path / "M_42_L_Stacked.fits")
    assert engine.batches == [(2, None)]
    assert diagnostics["stacking_engine"] == "OtherStacker"
    assert diagnostics["stacking_engine_version"] == "9.9"
    assert diagnostics["rejected_pixel_fraction"] == pytest.approx(0.02)
    assert diagnostics["registration_frames"] == [{"dx": 0.0}]


def test_the_job_id_reaches_the_engine() -> None:
    """A tracked job's id is passed through to `process_target`."""
    driver = FakeSirilDriver()
    SirilStackingEngine(driver).stack_batch(
        [], "M 42", "out.fits", None, False, StackSettings(), job_id="job-7"
    )
    assert driver.calls[0]["job_id"] == "job-7"
