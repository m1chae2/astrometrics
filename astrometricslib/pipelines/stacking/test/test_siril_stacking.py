"""Purpose: Unit tests for the two safeguards around a Siril stack.

Description: `run_siril_stack` retries a spectral registration that loses too
many frames with a more relaxed star detection (keeping the better run and
cleaning up the other), and stacks a session shot at several exposure
lengths one length at a time before combining the results. These tests use
a fake Siril driver that writes small real FITS files, so the file handling
(names, cleanup, merged registration files, headers) is exercised too.
"""

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.stacking.siril_stacking import (
    MINIMUM_REGISTERED_FRACTION,
    run_siril_stack,
)


class FakeSirilDriver:
    """Stands in for `ImageProcessing`, writing tiny stacks, not running Siril.

    Each call to `process_target` takes the next scripted outcome.
    """

    def __init__(self, library: Path, outcomes: list[dict[str, Any]]) -> None:
        """Remember where to write files and what each call should do.

        Parameters
        ----------
        library : `pathlib.Path`
            The folder the stacked files are written to.
        outcomes : `list` [`dict`]
            One entry per expected call: ``fill`` (brightness of the stack),
            ``failed`` and ``registered`` (Siril's registration totals),
            ``ok`` (`False` for a run that produces nothing).
        """
        self.library = library
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.last_run_diagnostics: dict[str, Any] = {}

    def process_target(self, **kwargs: Any) -> str | None:
        """Pretend to run Siril.

        Returns
        -------
        path : `str` or `None`
            The stacked file this call wrote, or `None` for a failed run.
        """
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        failed, registered = outcome.get("failed", 0), outcome.get("registered", 10)
        self.last_run_diagnostics = {
            "registration_failed_frames": failed,
            "registered_frames": registered,
            "images_stacked": registered,
            "symlinked_light_paths": [f"{kwargs['output_file']}:{index}" for index in range(registered)],
            "zero_order_stars": [{"star": index} for index in range(registered)],
            "corrupt_frames_skipped": [],
            "stacking_duration_seconds": 2.0,
        }
        if not outcome.get("ok", True):
            return None
        generator = np.random.default_rng(len(self.calls))
        image = outcome.get("fill", 0.2) + generator.normal(0.0, 0.002, (32, 32))
        stack_path = self.library / kwargs["output_file"]
        fits.writeto(stack_path, image.astype(np.float32), overwrite=True)
        stem = stack_path.with_suffix("")
        fits.writeto(f"{stem}_RejMap.fits", np.full((32, 32), 0.02, np.float32), overwrite=True)
        Path(f"{stem}_Registration.seq").write_text(
            "".join(f"R{index} 3.0 3.0 0.9 0 8e-05 19 H 1 0 0 0 1 0 0 0 1\n" for index in range(registered))
        )
        return str(stack_path)


def _frame(exposure: object = None, name: str = "frame") -> SimpleNamespace:
    """Build a frame record that can describe itself like a real one.

    Returns
    -------
    frame : `types.SimpleNamespace`
        A frame with an exposure length and a `model_dump` method.
    """
    return SimpleNamespace(
        name=name,
        exposure=exposure,
        model_dump=lambda name=name, exposure=exposure: {"path": name, "exposure": exposure},
    )


def _stack_summary(path: Path) -> float:
    """Read the average brightness of a stacked file.

    Returns
    -------
    mean : `float`
        The mean pixel value.
    """
    return float(np.mean(fits.getdata(path)))


def test_standard_imaging_is_one_plain_run(tmp_path: Path) -> None:
    """Non-spectral frames go to the driver once, with no detection setting."""
    driver = FakeSirilDriver(tmp_path, [{"fill": 0.3}])

    path, _ = run_siril_stack(driver, [_frame("30")] * 3, "M 42", "M_42_L_Stacked.fits", None, False)

    assert path == str(tmp_path / "M_42_L_Stacked.fits")
    assert len(driver.calls) == 1
    assert "spectral_star_detection" not in driver.calls[0]
    assert driver.calls[0]["is_spectral"] is False


def test_spectral_frames_that_register_are_stacked_once_with_standard_detection(tmp_path: Path) -> None:
    """When nearly every frame registers there is no retry."""
    driver = FakeSirilDriver(tmp_path, [{"failed": 0, "registered": 100}])

    path, diagnostics = run_siril_stack(driver, [_frame("30")] * 4, "Vega", "Vega_SPEC.fits", None, True)

    assert path == str(tmp_path / "Vega_SPEC.fits")
    assert len(driver.calls) == 1
    assert driver.calls[0]["spectral_star_detection"] == "standard"
    assert diagnostics["spectral_star_detection"] == "standard"
    assert diagnostics["registered_fraction"] == pytest.approx(1.0)


def test_a_poor_registration_is_retried_and_the_better_run_is_kept(tmp_path: Path) -> None:
    """60% registered with standard detection, 95% with relaxed: keep relaxed.

    The kept run's stack, rejection map and registration sequence must all
    end up under the requested name, and the other run's files must be gone.
    """
    driver = FakeSirilDriver(
        tmp_path,
        [{"failed": 40, "registered": 60, "fill": 0.2}, {"failed": 5, "registered": 95, "fill": 0.7}],
    )

    path, diagnostics = run_siril_stack(driver, [_frame("30")] * 4, "Vega", "Vega_SPEC.fits", None, True)

    assert [call["spectral_star_detection"] for call in driver.calls] == ["standard", "relaxed"]
    assert driver.calls[1]["output_file"] == "Vega_SPEC_relaxed.fits"
    assert path == str(tmp_path / "Vega_SPEC.fits")
    assert diagnostics["spectral_star_detection"] == "relaxed"
    assert _stack_summary(tmp_path / "Vega_SPEC.fits") == pytest.approx(0.7, abs=0.01)
    assert (tmp_path / "Vega_SPEC_RejMap.fits").exists()
    assert len((tmp_path / "Vega_SPEC_Registration.seq").read_text().splitlines()) == 95
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [
        "Vega_SPEC.fits",
        "Vega_SPEC_Registration.seq",
        "Vega_SPEC_RejMap.fits",
    ]


def test_the_standard_run_is_kept_when_it_registered_more(tmp_path: Path) -> None:
    """If relaxed detection does worse, the standard run's files are kept."""
    driver = FakeSirilDriver(
        tmp_path,
        [{"failed": 30, "registered": 70, "fill": 0.2}, {"failed": 50, "registered": 50, "fill": 0.7}],
    )

    path, diagnostics = run_siril_stack(driver, [_frame("30")] * 4, "Vega", "Vega_SPEC.fits", None, True)

    assert diagnostics["spectral_star_detection"] == "standard"
    assert _stack_summary(Path(path)) == pytest.approx(0.2, abs=0.01)
    assert len((tmp_path / "Vega_SPEC_Registration.seq").read_text().splitlines()) == 70
    assert not list(tmp_path.glob("*relaxed*"))


def test_frames_lost_after_every_attempt_are_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If both detections lose too many frames, a warning says so."""
    driver = FakeSirilDriver(tmp_path, [{"failed": 40, "registered": 60}, {"failed": 30, "registered": 70}])

    with caplog.at_level(logging.WARNING):
        run_siril_stack(driver, [_frame("30")] * 4, "Vega", "Vega_SPEC.fits", None, True)

    assert "70% of the 4 frames" in caplog.text
    assert "Vega" in caplog.text


def test_a_run_that_produces_nothing_falls_back_to_the_next_detection(tmp_path: Path) -> None:
    """If the standard run fails outright, the relaxed one is still tried."""
    driver = FakeSirilDriver(tmp_path, [{"ok": False}, {"failed": 2, "registered": 98, "fill": 0.5}])

    path, diagnostics = run_siril_stack(driver, [_frame("30")] * 4, "Vega", "Vega_SPEC.fits", None, True)

    assert path == str(tmp_path / "Vega_SPEC.fits")
    assert diagnostics["spectral_star_detection"] == "relaxed"


def test_when_every_attempt_fails_there_is_no_stack(tmp_path: Path) -> None:
    """Nothing produced by either detection means `None`."""
    driver = FakeSirilDriver(tmp_path, [{"ok": False}, {"ok": False}])

    path, _ = run_siril_stack(driver, [_frame("30")] * 4, "Vega", "Vega_SPEC.fits", None, True)

    assert path is None


def test_a_missing_output_name_uses_the_targets_default_name(tmp_path: Path) -> None:
    """The name falls back to the driver's own default for the target."""
    driver = FakeSirilDriver(tmp_path, [{"fill": 0.3}])

    path, _ = run_siril_stack(driver, [{"path": "a.fits"}, {"path": "b.fits"}], "M 42", None, None, False)

    assert path == str(tmp_path / "M_42_Stacked.fits")


def test_frames_may_be_plain_dictionaries(tmp_path: Path) -> None:
    """The backend passes frame dictionaries, which must work as they are."""
    driver = FakeSirilDriver(tmp_path, [{"fill": 0.3}])
    frames = [{"path": "a.fits", "exposure": "30"}, {"path": "b.fits", "exposure": "30"}]

    run_siril_stack(driver, frames, "Arcturus", "Arcturus_SPEC.fits", None, True)

    assert driver.calls[0]["image_files"] == frames


def test_a_bracketed_session_is_stacked_per_exposure_and_combined(tmp_path: Path) -> None:
    """Six 0.5 s and six 5 s frames become two stacks and one combined file."""
    frames = [_frame("0.5", f"short_{i}") for i in range(6)] + [_frame("5.0", f"long_{i}") for i in range(6)]
    driver = FakeSirilDriver(
        tmp_path,
        [{"registered": 6, "failed": 0, "fill": 0.02}, {"registered": 6, "failed": 0, "fill": 0.2}],
    )

    path, _ = run_siril_stack(driver, frames, "Vega", "Vega_SPEC.fits", None, True)

    assert [call["output_file"] for call in driver.calls] == [
        "Vega_SPEC_exp0p5s.fits",
        "Vega_SPEC_exp5s.fits",
    ]
    assert [len(call["image_files"]) for call in driver.calls] == [6, 6]
    assert path == str(tmp_path / "Vega_SPEC.fits")
    header = fits.getheader(path)
    assert header["EXPTIME"] == pytest.approx(6 * 0.5 + 6 * 5.0)
    assert header["STACKCNT"] == 12
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [
        "Vega_SPEC.fits",
        "Vega_SPEC_Registration.seq",
        "Vega_SPEC_RejMap.fits",
    ]
    assert len((tmp_path / "Vega_SPEC_Registration.seq").read_text().splitlines()) == 12


def test_a_group_whose_frames_cannot_be_read_is_still_combined(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If frame noise cannot be measured, weighting falls back to exposure."""
    frames = [_frame("0.5", f"s{i}") for i in range(5)] + [_frame("5.0", f"l{i}") for i in range(5)]
    driver = FakeSirilDriver(tmp_path, [{"registered": 5, "failed": 0}, {"registered": 5, "failed": 0}])

    with caplog.at_level(logging.WARNING):
        path, _ = run_siril_stack(driver, frames, "Vega", "Vega_SPEC.fits", None, True)

    assert path == str(tmp_path / "Vega_SPEC.fits")
    assert "weighting by exposure only" in caplog.text


def test_group_diagnostics_are_joined_in_group_order(tmp_path: Path) -> None:
    """The per-frame lists stay lined up with the merged registration file."""
    frames = [_frame("0.5", f"s{i}") for i in range(5)] + [_frame("5.0", f"l{i}") for i in range(7)]
    driver = FakeSirilDriver(tmp_path, [{"registered": 5, "failed": 0}, {"registered": 7, "failed": 0}])

    _, diagnostics = run_siril_stack(driver, frames, "Vega", "Vega_SPEC.fits", None, True)

    assert len(diagnostics["symlinked_light_paths"]) == 12
    assert len(diagnostics["zero_order_stars"]) == 12
    assert diagnostics["symlinked_light_paths"][0].startswith("Vega_SPEC_exp0p5s")
    assert diagnostics["symlinked_light_paths"][-1].startswith("Vega_SPEC_exp5s")
    assert diagnostics["stacking_duration_seconds"] == pytest.approx(4.0)
    assert diagnostics["images_stacked"] == 12
    assert diagnostics["registered_frames"] == 12
    assert diagnostics["registration_failed_frames"] == 0
    assert diagnostics["registered_fraction"] == pytest.approx(1.0)
    assert [group["frames"] for group in diagnostics["exposure_groups"]] == [5, 7]
    assert [group["exposure_seconds"] for group in diagnostics["exposure_groups"]] == pytest.approx([
        0.5,
        5.0,
    ])


def test_a_group_that_cannot_be_stacked_is_left_out_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One failed group does not sink the whole stack."""
    frames = [_frame("0.5", f"s{i}") for i in range(5)] + [_frame("5.0", f"l{i}") for i in range(5)]
    driver = FakeSirilDriver(
        tmp_path, [{"ok": False}, {"ok": False}, {"registered": 5, "failed": 0, "fill": 0.2}]
    )

    with caplog.at_level(logging.WARNING):
        path, diagnostics = run_siril_stack(driver, frames, "Vega", "Vega_SPEC.fits", None, True)

    assert path == str(tmp_path / "Vega_SPEC.fits")
    assert "0.5 s exposure group" in caplog.text
    assert len(diagnostics["exposure_groups"]) == 1
    assert fits.getheader(path)["EXPTIME"] == pytest.approx(5 * 5.0)


def test_when_no_group_can_be_stacked_there_is_no_result(tmp_path: Path) -> None:
    """If every group fails, the result is `None`."""
    frames = [_frame("0.5", f"s{i}") for i in range(5)] + [_frame("5.0", f"l{i}") for i in range(5)]
    driver = FakeSirilDriver(tmp_path, [{"ok": False}] * 4)

    path, _ = run_siril_stack(driver, frames, "Vega", "Vega_SPEC.fits", None, True)

    assert path is None


def test_the_registration_threshold_tolerates_a_few_lost_frames() -> None:
    """One frame in ten may be lost without a retry."""
    assert 0.5 < MINIMUM_REGISTERED_FRACTION <= 0.95


def test_a_failed_standard_run_is_reported_as_a_failure_not_a_percentage(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A run that gives up without totals is not reported as a share."""
    frames = [_frame("5.0", f"l{i}") for i in range(6)]
    driver = FakeSirilDriver(tmp_path, [{"ok": False}, {"registered": 6, "failed": 0}])

    with caplog.at_level(logging.WARNING):
        path, _ = run_siril_stack(driver, frames, "Vega", "Vega_SPEC.fits", None, True)

    assert path == str(tmp_path / "Vega_SPEC.fits")
    assert "failed with standard star detection; trying relaxed" in caplog.text
    assert "Only 100%" not in caplog.text
