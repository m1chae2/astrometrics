"""Tests for the Siril scripts that stretch and save the stack preview.

Siril is not started. The tests read the scripts the module writes, and a
stand-in replaces the `run_registered` call that would run Siril.
"""

from contextlib import nullcontext
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.stacking.post_processing import (
    preview_siril,
    stack_preview,
)
from astrometricslib.pipelines.stacking.post_processing.bright_object import BrightObjectStretch
from astrometricslib.pipelines.stacking.post_processing.preview_siril import (
    build_bright_object_script,
    build_picture_script,
    build_preview_script,
    build_stretch_script,
    run_preview_script,
)


def test_the_script_stretches_and_saves_without_touching_the_stack() -> None:
    """Verify the script saves a FITS and a JPEG under new names only."""
    commands = build_preview_script("M_13_L_Stacked.fits", "M_13_L_Stacked_preview", 0.21)

    assert commands[1:6] == [
        'load "M_13_L_Stacked.fits"',
        "autostretch -2.8 0.210",
        'save "processed"',
        "mirrorx",
        'savejpg "M_13_L_Stacked_preview" 90',
    ]
    assert not any('M_13_L_Stacked"' in command for command in commands if command.startswith("save"))


def test_the_stretch_script_saves_a_fits_and_the_picture_script_does_not_stretch() -> None:
    """Verify the two halves of the denoise route."""
    stretch = build_stretch_script("flat.fits", "stretched", 0.15)
    picture = build_picture_script("denoised.fits", "preview")

    assert stretch[1:4] == ['load "flat.fits"', "autostretch -2.8 0.150", 'save "stretched"']
    assert picture[1:5] == ['load "denoised.fits"', 'save "processed"', "mirrorx", 'savejpg "preview" 90']
    assert not any(command.startswith("autostretch") for command in picture)


def _record_subprocess(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace `run_registered` inside the module with a recorder.

    Returns
    -------
    captured : `dict`
        Filled in with ``arguments``, and ``script`` if one was passed, when
        the module runs a program.
    """
    captured: dict = {}

    def fake_subprocess_run(
        arguments: list[str], _timeout_seconds: float
    ) -> stack_preview.subprocess.CompletedProcess:
        """Record the command line, and the script file if there is one.

        Returns
        -------
        completed : `subprocess.CompletedProcess`
            A successful result.
        """
        captured["arguments"] = arguments
        if "-s" in arguments:
            captured["script"] = Path(arguments[-1]).read_text(encoding="utf-8")
        return stack_preview.subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(preview_siril, "run_registered", fake_subprocess_run)
    monkeypatch.setattr(preview_siril, "siril_process_lock", nullcontext)
    return captured


def test_a_flatpak_siril_gets_host_file_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a Flatpak Siril gets host file access and runs in the folder."""
    captured = _record_subprocess(monkeypatch)

    succeeded = run_preview_script(str(tmp_path), ["autostretch"], "flatpak run org.siril.Siril")

    assert succeeded
    assert captured["arguments"][:4] == ["flatpak", "run", "--filesystem=host", "org.siril.Siril"]
    assert captured["arguments"][4:6] == ["-d", str(tmp_path)]
    assert captured["script"] == "autostretch\n"
    assert list(tmp_path.iterdir()) == []


def test_the_bright_object_script_clips_stretches_and_saves_a_jpeg() -> None:
    """The script applies Siril's mtf between two points, then saves a JPEG."""
    stretch = BrightObjectStretch(1.0, 0.0002, 0.361, 0.509, 0.044)
    commands = build_bright_object_script("Moon.fits", "Moon_preview", stretch)
    assert commands[1:6] == [
        'load "Moon.fits"',
        "mtf 0.000200 0.361000 0.509000",
        'save "processed"',
        "mirrorx",
        'savejpg "Moon_preview" 90',
    ]
    assert not any(command.startswith(("fmul", "autostretch")) for command in commands)


def test_a_stack_with_values_above_one_is_multiplied_down_before_the_stretch() -> None:
    """A scale below 1 adds an fmul command ahead of the mtf."""
    stretch = BrightObjectStretch(0.25, 0.0005, 0.4, 1.0, 0.05)
    commands = build_bright_object_script("Moon.fits", "Moon_preview", stretch)
    assert commands[2] == "fmul 0.250000000"
    assert commands[3].startswith("mtf ")


def test_green_removal_is_added_before_the_stretch_only_when_asked() -> None:
    """Verify `rmgreen` sits between load and stretch, and is not a default."""
    plain = build_preview_script("a.fits", "a_preview", 0.2)
    with_green = build_preview_script("a.fits", "a_preview", 0.2, remove_green=True)
    stretch_only = build_stretch_script("a.fits", "stretched", 0.2, remove_green=True)

    assert "rmgreen" not in plain
    assert with_green[1:4] == ['load "a.fits"', "rmgreen", "autostretch -2.8 0.200"]
    assert stretch_only[1:4] == ['load "a.fits"', "rmgreen", "autostretch -2.8 0.200"]
    assert "rmgreen" not in build_stretch_script("a.fits", "stretched", 0.2)


def test_the_linked_stretch_is_asked_for_only_when_wanted() -> None:
    """Verify the shared stretch adds `-linked`, and nothing else does."""
    separate = build_preview_script("a.fits", "a_preview", 0.2)
    shared = build_preview_script("a.fits", "a_preview", 0.2, linked=True)

    assert "autostretch -2.8 0.200" in separate
    assert "autostretch -linked -2.8 0.200" in shared
    assert "autostretch -linked -2.8 0.200" in build_stretch_script("a.fits", "s", 0.2, linked=True)


def test_the_sky_of_a_single_channel_stack_is_measured_as_before(tmp_path: Path) -> None:
    """Verify a mono stack still gets a measured sky with no green step."""
    path = tmp_path / "mono.fits"
    rng = np.random.default_rng(3)
    fits.writeto(path, rng.normal(0.01, 0.0005, (200, 200)).astype(np.float32))

    sky = preview_siril.sky_background_for_file(str(path))

    assert sky is not None
    assert sky[0] == pytest.approx(0.01, abs=0.0005)
    assert sky[1] == pytest.approx(0.0005, rel=0.1)


def test_the_text_siril_printed_can_be_read_after_a_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `last_siril_output` gives the output of the last script."""

    def fake_run(arguments: list[str], _timeout_seconds: float) -> stack_preview.subprocess.CompletedProcess:
        """Give a result with some output.

        Returns
        -------
        completed : `subprocess.CompletedProcess`
            A successful result that printed one line.
        """
        return stack_preview.subprocess.CompletedProcess(arguments, 0, "log: hello from Siril\n", "")

    monkeypatch.setattr(preview_siril, "run_registered", fake_run)
    monkeypatch.setattr(preview_siril, "siril_process_lock", nullcontext)

    run_preview_script(str(tmp_path), ["noop"], "siril-cli")

    assert preview_siril.last_siril_output() == "log: hello from Siril\n"
