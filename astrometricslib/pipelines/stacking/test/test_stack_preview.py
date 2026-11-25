"""Tests for the flattened, stretched picture written beside a finished stack.

Neither Siril nor GraXpert is started. Stand-ins replace the functions that
run them, or the `run_registered` call inside them, so the tests check what
the module asks each program to do and how it reacts when one succeeds or
fails.
"""

import json
import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers import outside_services
from astrometricslib.pipelines.shared.stack_preview_path import preview_path_for, preview_settings_path_for
from astrometricslib.pipelines.stacking.post_processing import stack_preview
from astrometricslib.pipelines.stacking.post_processing.bright_object import BrightObjectStretch
from astrometricslib.pipelines.stacking.post_processing.sky_level import SkyLevelChoice
from astrometricslib.pipelines.stacking.post_processing.stack_preview import (
    PreviewSettings,
    build_bright_object_script,
    build_pcc_script,
    build_picture_script,
    build_preview_script,
    build_stretch_script,
    denoise_with_cosmic_clarity,
    find_local_gaia_catalog,
    flatten_background,
    has_plate_solution,
    is_colour_image,
    online_catalog_reachable,
    preview_is_out_of_date,
    read_preview_settings,
    run_preview_script,
    write_stack_preview,
)

# Bound at import, before the test setup replaces the module's own copies.


@pytest.fixture
def scratch_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the module's scratch folder at a folder the test owns.

    Returns
    -------
    scratch_root : `pathlib.Path`
        The folder where each preview's scratch folder is made.
    """
    root = tmp_path / "scratch"
    monkeypatch.setattr(stack_preview, "_SCRATCH_ROOT", str(root))
    return root


@pytest.fixture
def stack(tmp_path: Path) -> Path:
    """Make a stand-in stacked file.

    Returns
    -------
    stack : `pathlib.Path`
        A file named like a real stack, in a folder with a space in its name.
    """
    folder = tmp_path / "M 13"
    folder.mkdir()
    path = folder / "M_13_L_Stacked.fits"
    path.write_bytes(b"stack")
    return path


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    siril: str | None,
    graxpert: str | None,
    cosmic_clarity: str | None = None,
    strength: float = 1.0,
    star_tone: bool = False,
    remove_green: bool = True,
    colour_calibration: bool = False,
    colour_method: str = "stars",
) -> None:
    """Give the module the commands for Siril, GraXpert and Cosmic Clarity.

    The star toning is off unless asked for, so the tests of the other steps
    see the pipeline without it.
    """
    monkeypatch.setattr(
        stack_preview,
        "get_configuration",
        lambda: SimpleNamespace(
            get_siril_executable=lambda: siril,
            get_graxpert_executable=lambda: graxpert,
            get_cosmic_clarity_denoise_executable=lambda: cosmic_clarity,
            get_cosmic_clarity_denoise_path=lambda: cosmic_clarity,
            get_cosmic_clarity_denoise_strength=lambda: strength,
            get_preview_color_denoise_strength=lambda: 0.3,
            get_preview_star_tone_enabled=lambda: star_tone,
            get_preview_remove_green_noise_enabled=lambda: remove_green,
            get_preview_colour_calibration_enabled=lambda: colour_calibration,
            get_preview_colour_calibration_method=lambda: colour_method,
        ),
    )


class _Programs:
    """Stand-ins for Siril and GraXpert that record what they were asked."""

    def __init__(
        self,
        flatten_result: bool = True,
        siril_result: bool = True,
        denoise_result: bool = True,
        pcc_result: bool = True,
        starnet_result: bool = True,
        green_result: bool = True,
    ) -> None:
        """Choose how each program will behave."""
        self.green_result = green_result
        self.flatten_result = flatten_result
        self.siril_result = siril_result
        self.denoise_result = denoise_result
        self.pcc_result = pcc_result
        self.starnet_result = starnet_result
        self.flatten_calls: list[tuple[str, str, str]] = []
        self.denoise_calls: list[tuple[str, str, str, float]] = []
        self.denoise_color_strengths: list[float | None] = []
        self.siril_calls: list[tuple[str, list[str]]] = []
        self.tone_calls: list[tuple[str, str]] = []
        self.tone_result = True

    def flatten(self, graxpert_executable: str, input_path: str, output_stem: str) -> bool:
        """Act like GraXpert, writing the flattened copy if told to succeed.

        Returns
        -------
        succeeded : `bool`
            Whether this stand-in was told to succeed.
        """
        self.flatten_calls.append((graxpert_executable, input_path, output_stem))
        if self.flatten_result:
            image = np.random.default_rng(0).normal(0.0, 1.0, (16, 16)).astype(np.float32)
            fits.writeto(f"{output_stem}.fits", image)
        return self.flatten_result

    def denoise(
        self,
        executable: str,
        input_path: str,
        output_path: str,
        strength: float,
        color_strength: float | None = None,
    ) -> bool:
        """Act like Cosmic Clarity, writing a denoised copy if told to succeed.

        Returns
        -------
        succeeded : `bool`
            Whether this stand-in was told to succeed.
        """
        self.denoise_calls.append((executable, input_path, output_path, strength))
        self.denoise_color_strengths.append(color_strength)
        if self.denoise_result:
            Path(output_path).write_bytes(b"denoised")
        return self.denoise_result

    def tone(self, input_path: str, output_path: str) -> bool:
        """Act like the star toning, writing a toned copy if told to succeed.

        Returns
        -------
        succeeded : `bool`
            Whether this stand-in was told to succeed.
        """
        self.tone_calls.append((input_path, output_path))
        if self.tone_result:
            Path(output_path).write_bytes(b"toned")
        return self.tone_result

    def siril(self, directory: str, commands: list[str], siril_executable: str) -> bool:
        """Act like Siril, writing what the script asks for if told to succeed.

        A script that saves ``stretched`` makes the stretched copy. A script
        that saves a JPEG makes the picture.

        Returns
        -------
        succeeded : `bool`
            Whether this stand-in was told to succeed.
        """
        self.siril_calls.append((directory, commands))
        if "starnet -stretch" in commands:
            if self.starnet_result:
                rng = np.random.default_rng(1)
                fits.writeto(
                    Path(directory) / "starless_colour.fit",
                    rng.normal(0.01, 0.001, (3, 16, 16)).astype(np.float32),
                )
                fits.writeto(
                    Path(directory) / "starmask_colour.fit",
                    np.abs(rng.normal(0.0, 0.01, (3, 16, 16))).astype(np.float32),
                )
            return self.starnet_result
        if 'save "starless_stretched"' in commands:
            fits.writeto(
                Path(directory) / "starless_stretched.fits", np.full((3, 16, 16), 0.2, dtype=np.float32)
            )
            return True
        if 'save "greened"' in commands:
            if self.green_result:
                image = np.random.default_rng(2).normal(0.01, 0.001, (3, 64, 64)).astype(np.float32)
                fits.writeto(Path(directory) / "greened.fits", image)
            return self.green_result
        if any(command.startswith("pcc -catalog=") for command in commands):
            if self.pcc_result:
                image = np.random.default_rng(0).normal(0.01, 0.001, (3, 16, 16)).astype(np.float32)
                fits.writeto(Path(directory) / "colour.fits", image)
            return self.pcc_result
        if self.siril_result:
            if 'save "stretched"' in commands:
                (Path(directory) / "stretched.fits").write_bytes(b"stretched")
            if 'save "processed"' in commands:
                (Path(directory) / "processed.fits").write_bytes(b"processed")
            if any(command.startswith("savejpg") for command in commands):
                (Path(directory) / "preview.jpg").write_bytes(b"jpeg")
        return self.siril_result

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Put the stand-ins in place of the real runners."""
        monkeypatch.setattr(stack_preview, "flatten_background", self.flatten)
        monkeypatch.setattr(stack_preview, "denoise_with_cosmic_clarity", self.denoise)
        monkeypatch.setattr(stack_preview, "run_preview_script", self.siril)
        monkeypatch.setattr(stack_preview, "tone_stars_in_file", self.tone)


def test_the_preview_is_named_after_the_stack() -> None:
    """Verify the picture sits beside the stack with a suffix and .jpg."""
    assert preview_path_for("/lib/M 13/M_13_L_Stacked.fits") == "/lib/M 13/M_13_L_Stacked_preview.jpg"


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


def test_without_graxpert_the_stack_copy_is_stretched(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a blank GraXpert setting skips flattening."""
    _configure(monkeypatch, "siril-cli", None)
    programs = _Programs()
    programs.install(monkeypatch)

    picture = write_stack_preview(str(stack))

    assert picture == str(stack.with_name("M_13_L_Stacked_preview.jpg"))
    assert Path(picture).read_bytes() == b"jpeg"
    assert programs.flatten_calls == []
    assert 'load "stack.fits"' in programs.siril_calls[0][1]
    assert stack.read_bytes() == b"stack"
    assert list(scratch_root.iterdir()) == []


def test_the_picture_is_cropped_before_graxpert_runs(
    tmp_path: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify GraXpert gets the cropped copy and the stack is left whole."""
    from astrometricslib.pipelines.shared.registration_record import (
        RegistrationRecord,
        write_registration_record,
    )

    stack_path = tmp_path / "M_13_L_Stacked.fits"
    header = fits.Header()
    header["CRPIX1"] = 50.0
    header["CRPIX2"] = 40.0
    fits.writeto(stack_path, np.zeros((80, 100), dtype=np.float32), header)
    write_registration_record(
        str(stack_path),
        RegistrationRecord(
            ("a.fits",),
            ((1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0),),
            (True,),
            (0, 0),
            None,
            False,
            0,
            (10, 90, 8, 72),
        ),
    )
    _configure(monkeypatch, "siril-cli", "graxpert-cli")
    programs = _Programs()
    seen: list[tuple[tuple[int, ...], dict[str, float]]] = []
    original_flatten = programs.flatten

    def watching_flatten(executable: str, input_path: str, output_stem: str) -> bool:
        """Note the shape and header of the copy GraXpert is given.

        Returns
        -------
        succeeded : `bool`
            What the stand-in answers.
        """
        with fits.open(input_path, memmap=False) as hdul:
            keys = ("CRPIX1", "CRPIX2", "CROPX0", "CROPY0", "CROPW", "CROPH")
            seen.append((hdul[0].data.shape, {key: hdul[0].header[key] for key in keys}))
        return original_flatten(executable, input_path, output_stem)

    programs.flatten = watching_flatten  # type: ignore[method-assign]
    programs.install(monkeypatch)

    write_stack_preview(str(stack_path))

    assert seen[0][0] == (64, 80)
    assert seen[0][1] == {"CRPIX1": 40.0, "CRPIX2": 32.0, "CROPX0": 10, "CROPY0": 8, "CROPW": 80, "CROPH": 64}
    with fits.open(stack_path, memmap=False) as hdul:
        assert hdul[0].data.shape == (80, 100)


def test_graxpert_flattens_a_copy_before_the_stretch(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify GraXpert flattens the scratch copy and Siril stretches that."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu")
    programs = _Programs()
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None

    executable, input_path, output_stem = programs.flatten_calls[0]
    assert executable == "graxpert-gpu"
    assert Path(input_path).parent.parent == scratch_root
    assert Path(input_path).name == "stack.fits"
    assert Path(output_stem).name == "flat"
    assert 'load "flat.fits"' in programs.siril_calls[0][1]
    assert stack.read_bytes() == b"stack"
    assert list(scratch_root.iterdir()) == []


def test_a_failed_graxpert_still_gives_a_picture(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a GraXpert failure falls back to the unflattened copy."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu")
    programs = _Programs(flatten_result=False)
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None
    assert 'load "stack.fits"' in programs.siril_calls[0][1]


def test_a_failed_graxpert_is_reported_as_a_skipped_step(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a skipped step reaches the caller, so it can store a notice."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu")
    _Programs(flatten_result=False).install(monkeypatch)
    skipped: list[stack_preview.SkippedPreviewStep] = []

    assert write_stack_preview(str(stack), skipped_steps=skipped) is not None

    assert skipped == [stack_preview.SkippedPreviewStep("GraXpert", "GraXpert did not finish")]


def test_a_working_graxpert_reports_no_skipped_step(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify nothing is reported when every step worked."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu")
    _Programs().install(monkeypatch)
    skipped: list[stack_preview.SkippedPreviewStep] = []

    assert write_stack_preview(str(stack), skipped_steps=skipped) is not None

    assert skipped == []


def test_a_graxpert_that_cannot_start_still_gives_a_picture(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a missing GraXpert program does not stop the preview."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu")
    programs = _Programs()
    programs.install(monkeypatch)

    def cannot_start(graxpert_executable: str, input_path: str, output_stem: str) -> bool:
        """Act like a missing program.

        Raises
        ------
        FileNotFoundError
            Always.
        """
        raise FileNotFoundError(graxpert_executable)

    monkeypatch.setattr(stack_preview, "flatten_background", cannot_start)

    assert write_stack_preview(str(stack)) is not None
    assert 'load "stack.fits"' in programs.siril_calls[0][1]


def test_the_denoise_runs_on_the_stretched_copy_after_graxpert(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the order: GraXpert, Siril stretch, Cosmic Clarity, JPEG."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu", "/opt/cc/denoise", strength=0.7)
    programs = _Programs()
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None

    assert Path(programs.flatten_calls[0][1]).name == "stack.fits"
    stretch_script, picture_script = programs.siril_calls[0][1], programs.siril_calls[1][1]
    assert 'load "flat.fits"' in stretch_script
    assert any(command.startswith(("autostretch", "mtf")) for command in stretch_script)
    executable, denoise_input, denoise_output, strength = programs.denoise_calls[0]
    assert executable == "/opt/cc/denoise"
    assert Path(denoise_input).name == "stretched.fits"
    assert Path(denoise_output).name == "denoised.fits"
    assert strength == pytest.approx(0.7)
    assert 'load "denoised.fits"' in picture_script
    assert not any(command.startswith("autostretch") for command in picture_script)
    assert stack.read_bytes() == b"stack"
    assert list(scratch_root.iterdir()) == []


def test_cosmic_clarity_alone_stretches_the_plain_copy_first(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Cosmic Clarity works without GraXpert."""
    _configure(monkeypatch, "siril-cli", None, "/opt/cc/denoise")
    programs = _Programs()
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None
    assert programs.flatten_calls == []
    assert 'load "stack.fits"' in programs.siril_calls[0][1]
    assert Path(programs.denoise_calls[0][1]).name == "stretched.fits"
    assert 'load "denoised.fits"' in programs.siril_calls[1][1]


def test_without_cosmic_clarity_one_siril_script_stretches_and_saves(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the picture takes a single Siril run when nothing denoises."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu", None)
    programs = _Programs()
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None
    assert len(programs.siril_calls) == 1
    assert any(command.startswith(("autostretch", "mtf")) for command in programs.siril_calls[0][1])
    assert programs.denoise_calls == []


def test_the_star_toning_runs_after_the_denoise_and_feeds_the_picture(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the order: stretch, Cosmic Clarity, star toning, JPEG."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu", "/opt/cc/denoise", star_tone=True)
    programs = _Programs()
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None

    tone_input, tone_output = programs.tone_calls[0]
    assert Path(tone_input).name == "denoised.fits"
    assert Path(tone_output).name == "toned.fits"
    assert 'load "toned.fits"' in programs.siril_calls[1][1]
    assert not any(command.startswith("autostretch") for command in programs.siril_calls[1][1])


def test_the_star_toning_alone_takes_a_separate_stretch(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the toning works without a denoise, on the stretched copy."""
    _configure(monkeypatch, "siril-cli", None, None, star_tone=True)
    programs = _Programs()
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None

    assert len(programs.siril_calls) == 2
    assert 'save "stretched"' in programs.siril_calls[0][1]
    assert Path(programs.tone_calls[0][0]).name == "stretched.fits"
    assert 'load "toned.fits"' in programs.siril_calls[1][1]


def test_the_log_lists_every_step_that_ran(
    stack: Path,
    scratch_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify that each step that ran is named in the log."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu", "/opt/cc/denoise", star_tone=True)
    _Programs().install(monkeypatch)

    with caplog.at_level("INFO", logger=stack_preview.logger.name):
        assert write_stack_preview(str(stack)) is not None

    messages = [record.getMessage() for record in caplog.records]
    assert "GraXpert finished; the preview uses its result." in messages
    assert "Cosmic Clarity finished; the preview uses its result." in messages
    assert "Star toning finished; the preview uses its result." in messages
    summary = next(message for message in messages if message.startswith("Preview steps:"))
    assert summary == (
        "Preview steps: GraXpert done; Colour calibration not used; Remove Green Noise not used; "
        "Siril stretch done; "
        "Cosmic Clarity done; Star toning done."
    )


def test_the_log_summary_names_a_step_that_failed(
    stack: Path,
    scratch_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify that a failed Cosmic Clarity run is named as skipped."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu", "/opt/cc/denoise", star_tone=True)
    programs = _Programs(denoise_result=False)
    programs.install(monkeypatch)

    with caplog.at_level("INFO", logger=stack_preview.logger.name):
        assert write_stack_preview(str(stack)) is not None

    summary = next(
        record.getMessage() for record in caplog.records if record.getMessage().startswith("Preview steps:")
    )
    assert "Cosmic Clarity skipped after a failure" in summary
    assert "Star toning done" in summary


def test_a_failed_star_toning_keeps_the_picture_before_it(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a toning failure still gives the denoised picture."""
    _configure(monkeypatch, "siril-cli", None, "/opt/cc/denoise", star_tone=True)
    programs = _Programs()
    programs.tone_result = False
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None
    assert 'load "denoised.fits"' in programs.siril_calls[1][1]


def test_the_star_toning_is_skipped_when_the_setting_is_off(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify nothing tones the stars when the setting is off."""
    _configure(monkeypatch, "siril-cli", None, "/opt/cc/denoise", star_tone=False)
    programs = _Programs()
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None
    assert programs.tone_calls == []


def test_a_failed_denoise_keeps_the_stretched_copy(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a Cosmic Clarity failure still gives the stretched picture."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu", "/opt/cc/denoise")
    programs = _Programs(denoise_result=False)
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None
    assert 'load "stretched.fits"' in programs.siril_calls[1][1]
    assert not any(command.startswith("autostretch") for command in programs.siril_calls[1][1])


def test_a_denoise_program_that_cannot_start_keeps_the_stretched_copy(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a missing Cosmic Clarity program does not stop the preview."""
    _configure(monkeypatch, "siril-cli", None, "/opt/cc/denoise")
    programs = _Programs()
    programs.install(monkeypatch)

    def cannot_start(
        executable: str,
        input_path: str,
        output_path: str,
        strength: float,
        color_strength: float | None = None,
    ) -> bool:
        """Act like a missing program.

        Raises
        ------
        FileNotFoundError
            Always.
        """
        raise FileNotFoundError(executable)

    monkeypatch.setattr(stack_preview, "denoise_with_cosmic_clarity", cannot_start)

    assert write_stack_preview(str(stack)) is not None
    assert 'load "stretched.fits"' in programs.siril_calls[1][1]


def test_a_failed_stretch_before_the_denoise_gives_no_picture(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify nothing is denoised and no picture is made if Siril fails."""
    _configure(monkeypatch, "siril-cli", None, "/opt/cc/denoise")
    programs = _Programs(siril_result=False)
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is None
    assert programs.denoise_calls == []
    assert list(scratch_root.iterdir()) == []


def test_the_chosen_sky_level_reaches_siril(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify both routes pass the level chosen for the stack to Siril."""
    monkeypatch.setattr(
        stack_preview, "choose_sky_level_for_file", lambda path: SkyLevelChoice(0.137, 3.0, "test")
    )
    for denoise in (None, "/opt/cc/denoise"):
        _configure(monkeypatch, "siril-cli", None, denoise)
        programs = _Programs()
        programs.install(monkeypatch)

        assert write_stack_preview(str(stack)) is not None
        assert "autostretch -2.8 0.137" in programs.siril_calls[0][1]


def test_the_stretch_script_saves_a_fits_and_the_picture_script_does_not_stretch() -> None:
    """Verify the two halves of the denoise route."""
    stretch = build_stretch_script("flat.fits", "stretched", 0.15)
    picture = build_picture_script("denoised.fits", "preview")

    assert stretch[1:4] == ['load "flat.fits"', "autostretch -2.8 0.150", 'save "stretched"']
    assert picture[1:5] == ['load "denoised.fits"', 'save "processed"', "mirrorx", 'savejpg "preview" 90']
    assert not any(command.startswith("autostretch") for command in picture)


def test_a_failed_siril_leaves_no_picture_and_no_scratch_folder(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify an old picture is removed and nothing is left behind."""
    _configure(monkeypatch, "siril-cli", None)
    _Programs(siril_result=False).install(monkeypatch)
    old_picture = stack.with_name("M_13_L_Stacked_preview.jpg")
    old_picture.write_bytes(b"old")

    assert write_stack_preview(str(stack)) is None
    assert not old_picture.exists()
    assert list(scratch_root.iterdir()) == []


def test_a_missing_stack_starts_nothing(
    tmp_path: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify neither program runs for a stack file that does not exist."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu")
    programs = _Programs()
    programs.install(monkeypatch)

    assert write_stack_preview(str(tmp_path / "missing.fits")) is None
    assert programs.flatten_calls == []
    assert programs.siril_calls == []


def test_siril_not_installed_is_reported_not_raised(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a Siril that cannot start gives no picture and no exception."""
    _configure(monkeypatch, "siril-cli", None)

    def cannot_start(directory: str, commands: list[str], siril_executable: str) -> bool:
        """Act like a missing program.

        Raises
        ------
        FileNotFoundError
            Always.
        """
        raise FileNotFoundError("siril-cli")

    monkeypatch.setattr(stack_preview, "run_preview_script", cannot_start)

    assert write_stack_preview(str(stack)) is None
    assert list(scratch_root.iterdir()) == []


def test_no_configured_siril_gives_no_picture(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a missing Siril setting is handled."""
    _configure(monkeypatch, None, "graxpert-gpu")
    programs = _Programs()
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is None
    assert programs.flatten_calls == []


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

    monkeypatch.setattr(stack_preview, "run_registered", fake_subprocess_run)
    monkeypatch.setattr(stack_preview, "siril_process_lock", nullcontext)
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


def test_graxpert_is_asked_for_a_subtracted_ai_background(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the GraXpert command line asks for AI background subtraction."""
    captured = _record_subprocess(monkeypatch)

    assert flatten_background("/opt/graxpert-gpu.sh", "/work/stack.fits", "/work/flat")

    arguments = captured["arguments"]
    assert arguments[0] == "/opt/graxpert-gpu.sh"
    assert arguments[1:5] == ["-cli", "-cmd", "background-extraction", "-gpu"]
    assert arguments[arguments.index("-correction") + 1] == "Subtraction"
    assert arguments[-3:] == ["-output", "/work/flat", "/work/stack.fits"]


@pytest.fixture
def cosmic_clarity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make a stand-in Cosmic Clarity folder and take its lock out of play.

    Returns
    -------
    executable : `pathlib.Path`
        The path of the stand-in program, with ``input`` and ``output``
        folders beside it.
    """
    folder = tmp_path / "cc"
    (folder / "input").mkdir(parents=True)
    (folder / "output").mkdir()
    slot_path = "astrometricslib.foundation.storage.process_locks.acquire_resource_slot"
    monkeypatch.setattr(slot_path, lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(stack_preview, "get_configuration", lambda: SimpleNamespace())
    return folder / "denoise"


def _fake_denoise_program(
    monkeypatch: pytest.MonkeyPatch, returncode: int = 0, writes_output: bool = True
) -> list[list[str]]:
    """Replace `run_registered` with a program that denoises its `input`.

    Returns
    -------
    calls : `list` [`list` [`str`]]
        The command lines the module ran.
    """
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], _timeout_seconds: float) -> stack_preview.subprocess.CompletedProcess:
        """Write a ``_denoised`` copy of each input file, as the program does.

        Returns
        -------
        completed : `subprocess.CompletedProcess`
            A result with the chosen exit code.
        """
        calls.append(arguments)
        folder = Path(arguments[0]).parent
        if writes_output:
            for staged in (folder / "input").iterdir():
                (folder / "output" / f"{staged.stem}_denoised.fits").write_bytes(b"denoised")
        return stack_preview.subprocess.CompletedProcess(arguments, returncode, "", "")

    monkeypatch.setattr(stack_preview, "run_registered", fake_run)
    return calls


def test_cosmic_clarity_gets_a_copy_and_leaves_its_folders_clean(
    tmp_path: Path, cosmic_clarity: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the result returns and the program's folders end empty."""
    source = tmp_path / "in.fits"
    source.write_bytes(b"flat")
    calls = _fake_denoise_program(monkeypatch)
    result = tmp_path / "denoised.fits"

    assert denoise_with_cosmic_clarity(str(cosmic_clarity), str(source), str(result), 0.6)

    assert result.read_bytes() == b"denoised"
    assert source.read_bytes() == b"flat"
    assert calls[0] == [str(cosmic_clarity), "--denoise_mode=luminance", "--denoise_strength=0.6"]
    assert list((cosmic_clarity.parent / "input").iterdir()) == []
    assert list((cosmic_clarity.parent / "output").iterdir()) == []


def test_cosmic_clarity_runs_in_full_mode_when_given_a_color_strength(
    tmp_path: Path, cosmic_clarity: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a color strength selects the full mode with both strengths."""
    source = tmp_path / "in.fits"
    source.write_bytes(b"flat")
    calls = _fake_denoise_program(monkeypatch)

    assert denoise_with_cosmic_clarity(
        str(cosmic_clarity), str(source), str(tmp_path / "out.fits"), 0.5, color_strength=0.3
    )

    assert calls[0] == [
        str(cosmic_clarity),
        "--denoise_mode=full",
        "--denoise_strength=0.5",
        "--color_denoise_strength=0.3",
    ]


def test_cosmic_clarity_is_not_run_over_someone_elses_files(
    tmp_path: Path, cosmic_clarity: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a file already in `input` stops the run and is left alone."""
    foreign = cosmic_clarity.parent / "input" / "my_own_image.tif"
    foreign.write_bytes(b"mine")
    source = tmp_path / "in.fits"
    source.write_bytes(b"flat")
    calls = _fake_denoise_program(monkeypatch)

    assert not denoise_with_cosmic_clarity(str(cosmic_clarity), str(source), str(tmp_path / "out.fits"), 1.0)

    assert calls == []
    assert foreign.read_bytes() == b"mine"


def test_a_failed_cosmic_clarity_run_leaves_its_folders_clean(
    tmp_path: Path, cosmic_clarity: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a failure removes the staged copy and reports failure."""
    source = tmp_path / "in.fits"
    source.write_bytes(b"flat")
    _fake_denoise_program(monkeypatch, returncode=1, writes_output=False)

    assert not denoise_with_cosmic_clarity(str(cosmic_clarity), str(source), str(tmp_path / "out.fits"), 1.0)

    assert list((cosmic_clarity.parent / "input").iterdir()) == []


def test_files_this_module_left_in_cosmic_clarity_are_cleared_but_others_are_kept(
    tmp_path: Path, cosmic_clarity: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a killed run's leftovers go and a person's files stay."""
    input_folder = cosmic_clarity.parent / "input"
    output_folder = cosmic_clarity.parent / "output"
    (input_folder / "stack_preview_4242.fits").write_bytes(b"left by a killed run")
    (output_folder / "stack_preview_4242_denoised.fits").write_bytes(b"left by a killed run")
    source = tmp_path / "in.fits"
    source.write_bytes(b"flat")
    _fake_denoise_program(monkeypatch)

    assert denoise_with_cosmic_clarity(str(cosmic_clarity), str(source), str(tmp_path / "out.fits"), 1.0)

    # A person's file in `input` still stops the run, and is left alone.
    mine = input_folder / "my_own_image.tif"
    mine.write_bytes(b"mine")
    assert not denoise_with_cosmic_clarity(str(cosmic_clarity), str(source), str(tmp_path / "out2.fits"), 1.0)
    assert mine.read_bytes() == b"mine"


def test_old_scratch_folders_are_removed_and_recent_ones_kept(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a killed run's scratch folder is cleaned up by the next run."""
    _configure(monkeypatch, "siril-cli", None, None)
    _Programs().install(monkeypatch)
    old = scratch_root / "stack_preview_killed"
    recent = scratch_root / "stack_preview_running"
    other = scratch_root / "Albireo__Albireo_SPEC_Stacked"
    for folder in (old, recent, other):
        folder.mkdir(parents=True)
    two_hours_ago = old.stat().st_mtime - 7200
    os.utime(old, (two_hours_ago, two_hours_ago))
    os.utime(other, (two_hours_ago, two_hours_ago))

    assert write_stack_preview(str(stack)) is not None

    assert not old.exists()
    assert recent.exists()
    assert other.exists()


def test_a_graxpert_result_full_of_invalid_pixels_is_not_used(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a mostly not-a-number GraXpert image falls back to the copy."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu", None)
    programs = _Programs()
    programs.install(monkeypatch)
    monkeypatch.setattr(stack_preview, "_is_usable_image", lambda path: False)

    assert write_stack_preview(str(stack)) is not None
    assert 'load "stack.fits"' in programs.siril_calls[0][1]


def test_the_usability_check_accepts_a_good_image_and_rejects_bad_ones(tmp_path: Path) -> None:
    """Verify valid, mostly invalid, flat and unreadable images."""
    good = np.random.default_rng(0).normal(0.0, 1.0, (40, 40)).astype(np.float32)
    mostly_invalid = good.copy()
    mostly_invalid[:30, :] = np.nan
    flat = np.full((40, 40), 0.5, dtype=np.float32)
    paths = {}
    for name, data in (("good", good), ("invalid", mostly_invalid), ("flat", flat)):
        paths[name] = tmp_path / f"{name}.fits"
        fits.writeto(paths[name], data)
    broken = tmp_path / "broken.fits"
    broken.write_bytes(b"not a fits file")

    assert stack_preview._is_usable_image(str(paths["good"]))
    assert not stack_preview._is_usable_image(str(paths["invalid"]))
    assert not stack_preview._is_usable_image(str(paths["flat"]))
    assert not stack_preview._is_usable_image(str(broken))


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


def test_a_bright_object_skips_graxpert_and_the_denoise(
    tmp_path: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Moon gets one Siril script with its own stretch and no cleanup."""
    y, x = np.mgrid[0:300, 0:300]
    generator = np.random.default_rng(4)
    disc = 0.0002 + 0.00004 * generator.standard_normal((300, 300))
    disc[np.hypot(y - 150, x - 150) < 70] += 0.4
    folder = tmp_path / "Moon"
    folder.mkdir()
    path = folder / "Moon_L_Stacked.fits"
    fits.writeto(path, disc.astype(np.float32))
    _configure(monkeypatch, "siril-cli", "graxpert-gpu", "/opt/cc/denoise", star_tone=True)
    programs = _Programs()
    programs.install(monkeypatch)

    picture = write_stack_preview(str(path))

    assert picture is not None
    assert programs.flatten_calls == []
    assert programs.denoise_calls == []
    assert programs.tone_calls == []
    assert len(programs.siril_calls) == 1
    script = programs.siril_calls[0][1]
    assert any(command.startswith("mtf ") for command in script)
    assert not any(command.startswith("autostretch") for command in script)
    assert list(scratch_root.iterdir()) == []


def test_the_stretched_fits_is_saved_beside_the_stack(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the FITS picture lands by the stack and a rerun replaces it."""
    _configure(monkeypatch, "siril-cli", None)
    _Programs().install(monkeypatch)
    processed = stack.with_name("M_13_L_Stacked_processed.fits")
    processed.write_bytes(b"old")

    assert write_stack_preview(str(stack)) is not None

    assert processed.read_bytes() == b"processed"
    assert list(scratch_root.iterdir()) == []


def test_a_failed_siril_removes_the_old_fits_picture_too(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify no FITS picture of an older stack is left behind."""
    _configure(monkeypatch, "siril-cli", None)
    _Programs(siril_result=False).install(monkeypatch)
    processed = stack.with_name("M_13_L_Stacked_processed.fits")
    processed.write_bytes(b"old")

    assert write_stack_preview(str(stack)) is None
    assert not processed.exists()


def test_the_observation_header_is_copied_to_the_stretched_fits(tmp_path: Path) -> None:
    """Verify the stack's keywords are added and the file's own are kept."""
    stack_path = tmp_path / "S_Stacked.fits"
    header = fits.Header()
    header["EXPTIME"] = (300.0, "total exposure")
    header["OBJECT"] = "M 57"
    header["ROWORDER"] = "TOP-DOWN"
    fits.writeto(stack_path, np.ones((8, 8), dtype=np.float32), header)
    processed_path = tmp_path / "S_Stacked_processed.fits"
    own = fits.Header()
    own["ROWORDER"] = "BOTTOM-UP"
    fits.writeto(processed_path, np.full((8, 8), 0.25, dtype=np.float32), own)

    stack_preview._copy_observation_header(str(stack_path), str(processed_path))

    with fits.open(processed_path) as hdul:
        result = hdul[0].header
        assert result["EXPTIME"] == pytest.approx(300.0)
        assert result["OBJECT"] == "M 57"
        assert result["ROWORDER"] == "BOTTOM-UP"
        assert "not linear data" in str(result["HISTORY"])
        assert hdul[0].data.dtype.kind == "f"
        assert hdul[0].data.dtype.itemsize == 4
        assert hdul[0].data[0, 0] == pytest.approx(0.25)


def test_an_unreadable_stretched_fits_is_left_and_logged(tmp_path: Path) -> None:
    """Verify a header copy that cannot work does not raise."""
    stack_path = tmp_path / "S_Stacked.fits"
    fits.writeto(stack_path, np.ones((4, 4), dtype=np.float32))
    processed_path = tmp_path / "S_Stacked_processed.fits"
    processed_path.write_bytes(b"not a fits file")

    stack_preview._copy_observation_header(str(stack_path), str(processed_path))

    assert processed_path.read_bytes() == b"not a fits file"


def test_a_run_can_turn_the_denoise_off_without_changing_the_setting(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify denoise=False skips Cosmic Clarity though it is configured."""
    _configure(monkeypatch, "siril-cli", None, "/opt/cc/denoise")
    programs = _Programs()
    programs.install(monkeypatch)
    steps: list[str] = []

    assert write_stack_preview(str(stack), PreviewSettings(denoise=False), steps) is not None

    assert programs.denoise_calls == []
    assert "Cosmic Clarity off for this run" in steps


def test_a_run_can_set_its_own_denoise_strength_and_star_toning(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the strength and toning overrides reach the steps."""
    _configure(monkeypatch, "siril-cli", None, "/opt/cc/denoise", strength=0.9, star_tone=False)
    programs = _Programs()
    programs.install(monkeypatch)
    steps: list[str] = []

    settings = PreviewSettings(denoise_strength=0.4, star_toning=True)
    assert write_stack_preview(str(stack), settings, steps) is not None

    assert programs.denoise_calls[0][3] == pytest.approx(0.4)
    assert len(programs.tone_calls) == 1
    assert "Star toning done" in steps


def test_the_steps_are_reported_without_any_override(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a plain run reports its steps to a caller that asks."""
    _configure(monkeypatch, "siril-cli", None)
    programs = _Programs()
    programs.install(monkeypatch)
    steps: list[str] = []

    assert write_stack_preview(str(stack), steps_log=steps) is not None

    assert steps[0] == "GraXpert not configured"


def _write_colour_stack(path: Path) -> None:
    """Write a small three-plane FITS image where the stack should be."""
    image = np.random.default_rng(1).normal(0.01, 0.002, (3, 32, 32)).astype(np.float32)
    fits.writeto(path, image, overwrite=True)


def test_green_removal_is_added_before_the_stretch_only_when_asked() -> None:
    """Verify `rmgreen` sits between load and stretch, and is not a default."""
    plain = build_preview_script("a.fits", "a_preview", 0.2)
    with_green = build_preview_script("a.fits", "a_preview", 0.2, remove_green=True)
    stretch_only = build_stretch_script("a.fits", "stretched", 0.2, remove_green=True)

    assert "rmgreen" not in plain
    assert with_green[1:4] == ['load "a.fits"', "rmgreen", "autostretch -2.8 0.200"]
    assert stretch_only[1:4] == ['load "a.fits"', "rmgreen", "autostretch -2.8 0.200"]
    assert "rmgreen" not in build_stretch_script("a.fits", "stretched", 0.2)


def test_only_a_three_plane_fits_counts_as_a_colour_image(tmp_path: Path) -> None:
    """Verify colour is told apart from single-channel and unreadable files."""
    colour, mono, broken = tmp_path / "c.fits", tmp_path / "m.fits", tmp_path / "b.fits"
    _write_colour_stack(colour)
    fits.writeto(mono, np.zeros((32, 32), dtype=np.float32))
    broken.write_bytes(b"not a fits file")

    assert is_colour_image(str(colour)) is True
    assert is_colour_image(str(mono)) is False
    assert is_colour_image(str(broken)) is False
    assert is_colour_image(str(tmp_path / "missing.fits")) is False


def test_a_colour_stack_gets_green_removal_in_its_siril_script(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a colour stack's preview removes green before the stretch."""
    _write_colour_stack(stack)
    _configure(monkeypatch, "siril-cli", None)
    programs = _Programs()
    programs.install(monkeypatch)

    write_stack_preview(str(stack))

    commands = programs.siril_calls[0][1]
    assert commands.index("rmgreen") < next(i for i, c in enumerate(commands) if c.startswith("autostretch"))


def test_a_single_channel_stack_is_never_given_green_removal(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a mono stack gets no `rmgreen`, which would fail on it."""
    fits.writeto(stack, np.full((32, 32), 0.01, dtype=np.float32), overwrite=True)
    _configure(monkeypatch, "siril-cli", None)
    programs = _Programs()
    programs.install(monkeypatch)

    write_stack_preview(str(stack))

    assert "rmgreen" not in programs.siril_calls[0][1]


def test_green_removal_can_be_switched_off(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the setting stops a colour stack getting `rmgreen`."""
    _write_colour_stack(stack)
    _configure(monkeypatch, "siril-cli", None, remove_green=False)
    programs = _Programs()
    programs.install(monkeypatch)

    write_stack_preview(str(stack))

    assert "rmgreen" not in programs.siril_calls[0][1]


def test_a_colour_stack_is_denoised_with_a_color_strength(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a colour stack's denoise is given the color strength."""
    _write_colour_stack(stack)
    _configure(monkeypatch, "siril-cli", None, cosmic_clarity="clarity")
    programs = _Programs()
    programs.install(monkeypatch)

    write_stack_preview(str(stack))

    assert programs.denoise_color_strengths == [pytest.approx(0.3)]


def test_a_single_channel_stack_is_denoised_without_a_color_strength(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a mono stack's denoise keeps the brightness-only mode."""
    fits.writeto(stack, np.full((32, 32), 0.01, dtype=np.float32), overwrite=True)
    _configure(monkeypatch, "siril-cli", None, cosmic_clarity="clarity")
    programs = _Programs()
    programs.install(monkeypatch)

    write_stack_preview(str(stack))

    assert programs.denoise_color_strengths == [None]


def test_the_stretch_before_the_denoise_also_gets_green_removal(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the stretch used before Cosmic Clarity removes green too."""
    _write_colour_stack(stack)
    _configure(monkeypatch, "siril-cli", None, cosmic_clarity="clarity")
    programs = _Programs()
    programs.install(monkeypatch)

    write_stack_preview(str(stack))

    assert "rmgreen" in programs.siril_calls[0][1]


def _write_colour_stack_with_stars(path: Path, star_count: int = 120) -> None:
    """Write a colour FITS with enough stars for the colour calibration."""
    rng = np.random.default_rng(7)
    size = 300
    y, x = np.mgrid[0:size, 0:size]
    image = np.zeros((3, size, size))
    for _ in range(star_count):
        cx, cy = rng.uniform(20, size - 20, 2)
        shape = 10 ** rng.uniform(-2.3, -1.0) * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * 1.4**2))
        image += shape * np.array([0.6, 1.0, 0.7])[:, None, None]
    image += np.array([0.004, 0.008, 0.005])[:, None, None] + rng.normal(0, 0.0004, image.shape)
    fits.writeto(path, image.astype(np.float32), overwrite=True)


def test_the_linked_stretch_is_asked_for_only_when_wanted() -> None:
    """Verify the shared stretch adds `-linked`, and nothing else does."""
    separate = build_preview_script("a.fits", "a_preview", 0.2)
    shared = build_preview_script("a.fits", "a_preview", 0.2, linked=True)

    assert "autostretch -2.8 0.200" in separate
    assert "autostretch -linked -2.8 0.200" in shared
    assert "autostretch -linked -2.8 0.200" in build_stretch_script("a.fits", "s", 0.2, linked=True)


def test_a_calibrated_colour_stack_is_stretched_with_one_shared_stretch(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify calibrated colours get one stretch for all channels."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True)
    programs = _Programs()
    programs.install(monkeypatch)

    write_stack_preview(str(stack))

    commands = programs.siril_calls[-1][1]
    assert 'load "greened.fits"' in commands
    assert any(command.startswith("mtf ") for command in commands)
    assert not any(command.startswith("autostretch") for command in commands)


def test_without_colour_calibration_the_channels_are_stretched_apart(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the old per-channel stretch stays when calibration is off."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=False)
    programs = _Programs()
    programs.install(monkeypatch)

    write_stack_preview(str(stack))

    commands = programs.siril_calls[0][1]
    assert 'load "stack.fits"' in commands
    assert not any("-linked" in command for command in commands)


def test_a_failed_colour_calibration_keeps_the_picture_and_the_separate_stretch(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify too few stars leaves the stack alone and the stretch apart."""
    _write_colour_stack_with_stars(stack, star_count=3)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True)
    programs = _Programs()
    programs.install(monkeypatch)

    picture = write_stack_preview(str(stack))

    assert picture is not None
    commands = programs.siril_calls[0][1]
    assert 'load "stack.fits"' in commands
    assert not any("-linked" in command for command in commands)


def _use_online_catalog(monkeypatch: pytest.MonkeyPatch, reachable: bool) -> None:
    """Make the module see no local Gaia catalog and a set server state."""
    monkeypatch.setattr(stack_preview, "find_local_gaia_catalog", lambda: None)
    monkeypatch.setattr(stack_preview, "online_catalog_reachable", lambda: reachable)


def test_the_pcc_script_solves_the_field_only_when_it_has_no_solution() -> None:
    """Verify `platesolve` comes before `pcc`, and only when asked for."""
    unsolved = build_pcc_script("a.fits", "colour", solve=True)
    solved = build_pcc_script("a.fits", "colour", solve=False)

    assert unsolved[1:5] == [
        'load "a.fits"',
        "platesolve -catalog=gaia",
        "pcc -catalog=gaia",
        'save "colour"',
    ]
    assert solved[1:4] == ['load "a.fits"', "pcc -catalog=gaia", 'save "colour"']


def test_a_header_with_a_projection_and_a_reference_position_counts_as_solved(tmp_path: Path) -> None:
    """Verify the plate solution check reads the header only."""
    solved, unsolved, broken = tmp_path / "s.fits", tmp_path / "u.fits", tmp_path / "b.fits"
    header = fits.Header()
    header["CTYPE1"], header["CRVAL1"] = "RA---TAN", 202.4
    fits.writeto(solved, np.zeros((4, 4), dtype=np.float32), header)
    fits.writeto(unsolved, np.zeros((4, 4), dtype=np.float32))
    broken.write_bytes(b"not fits")

    assert has_plate_solution(str(solved)) is True
    assert has_plate_solution(str(unsolved)) is False
    assert has_plate_solution(str(broken)) is False


def test_pcc_runs_first_and_its_result_feeds_a_shared_stretch(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Siril PCC calibrates, then the stretch shares channels."""
    _use_online_catalog(monkeypatch, reachable=True)
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True, colour_method="pcc")
    programs = _Programs()
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None

    pcc_commands = programs.siril_calls[0][1]
    assert "platesolve -catalog=gaia" in pcc_commands
    final_commands = programs.siril_calls[-1][1]
    assert 'load "greened.fits"' in final_commands
    assert any(command.startswith("mtf ") for command in final_commands)


def test_pcc_does_not_solve_a_field_that_is_already_solved(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a stack with a plate solution goes straight to `pcc`."""
    _use_online_catalog(monkeypatch, reachable=True)
    _write_colour_stack_with_stars(stack)
    with fits.open(stack, mode="update") as opened:
        opened[0].header["CTYPE1"], opened[0].header["CRVAL1"] = "RA---TAN", 202.4
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True, colour_method="pcc")
    programs = _Programs()
    programs.install(monkeypatch)

    write_stack_preview(str(stack))

    assert "platesolve -catalog=gaia" not in programs.siril_calls[0][1]
    assert "pcc -catalog=gaia" in programs.siril_calls[0][1]


def test_a_failed_pcc_falls_back_to_the_stars_in_the_stack(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Verify no network or no catalog still gives a calibrated picture."""
    _use_online_catalog(monkeypatch, reachable=True)
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True, colour_method="pcc")
    programs = _Programs(pcc_result=False)
    programs.install(monkeypatch)

    with caplog.at_level("INFO", logger=stack_preview.logger.name):
        assert write_stack_preview(str(stack)) is not None

    final_commands = programs.siril_calls[-1][1]
    assert 'load "greened.fits"' in final_commands
    assert any(command.startswith("mtf ") for command in final_commands)
    summary = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Preview steps:"))
    assert "Colour calibration (from the stars) done" in summary


def test_the_pcc_script_reads_a_local_catalog_when_given_one() -> None:
    """Verify the script sets the catalog path and asks for `localgaia`."""
    commands = build_pcc_script("a.fits", "colour", solve=True, local_catalog="/data/cat.dat")

    assert commands[1:6] == [
        "set core.catalogue_gaia_astro=/data/cat.dat",
        'load "a.fits"',
        "platesolve -catalog=localgaia",
        "pcc -catalog=localgaia",
        'save "colour"',
    ]
    assert not any("-catalog=gaia" in command for command in commands)


def test_the_local_gaia_catalog_is_found_in_the_first_folder_that_has_it(tmp_path: Path) -> None:
    """Verify the search order and the answer when nothing is installed."""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "siril_cat_healpix8_astro.dat").write_bytes(b"x")

    assert find_local_gaia_catalog([str(first), str(second)]) == str(second / "siril_cat_healpix8_astro.dat")
    assert find_local_gaia_catalog([str(first)]) is None


def _fake_connection(monkeypatch: pytest.MonkeyPatch, error: Exception | None) -> list[str]:
    """Replace the connection the check opens; record each host it is sent to.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Test patcher.
    error : `Exception` or `None`
        What the connection raises, or `None` for a connection that works.

    Returns
    -------
    attempts : `list` [`str`]
        The host of each connection tried.
    """
    attempts: list[str] = []

    def connect(address: tuple[str, int], timeout: float) -> nullcontext:
        """Record the attempt, then fail or hand back a closable stand-in.

        Returns
        -------
        connection : `contextlib.nullcontext`
            A stand-in that closes with nothing to do.
        """
        attempts.append(address[0])
        if error is not None:
            raise error
        return nullcontext()

    monkeypatch.setattr(outside_services.clients.socket, "create_connection", connect)
    return attempts


def test_the_catalog_server_check_goes_through_the_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the check is counted as a live request of its gateway service."""
    attempts = _fake_connection(monkeypatch, None)

    events: list[outside_services.ServiceEvent] = []
    with outside_services.record_service_events(events):
        assert online_catalog_reachable() is True

    assert attempts == ["tapvizier.u-strasbg.fr"]
    assert any(event.service == "Siril Gaia catalog" and event.kind == "live" for event in events)


def test_a_timeout_pauses_the_server_and_the_next_check_makes_no_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify one timeout pauses the host, so the next preview skips."""
    attempts = _fake_connection(monkeypatch, TimeoutError("timed out"))

    assert online_catalog_reachable() is False
    assert online_catalog_reachable() is False

    assert attempts == ["tapvizier.u-strasbg.fr"]


def test_pcc_uses_the_local_catalog_without_asking_the_network(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify an installed catalog is used and the server is not checked."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True, colour_method="pcc")
    monkeypatch.setattr(stack_preview, "find_local_gaia_catalog", lambda: "/data/cat.dat")

    def fail() -> bool:
        """Fail the test if the network is checked.

        Raises
        ------
        AssertionError
            Always.
        """
        raise AssertionError("the server must not be checked")

    monkeypatch.setattr(stack_preview, "online_catalog_reachable", fail)
    programs = _Programs()
    programs.install(monkeypatch)

    write_stack_preview(str(stack))

    assert "pcc -catalog=localgaia" in programs.siril_calls[0][1]
    assert "set core.catalogue_gaia_astro=/data/cat.dat" in programs.siril_calls[0][1]


def test_pcc_is_not_started_when_the_server_does_not_answer(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Verify an unreachable server costs no Siril run and uses the stars."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True, colour_method="pcc")
    _use_online_catalog(monkeypatch, reachable=False)
    programs = _Programs()
    programs.install(monkeypatch)

    with caplog.at_level("INFO", logger=stack_preview.logger.name):
        assert write_stack_preview(str(stack)) is not None

    assert not any(any(c.startswith("pcc ") for c in commands) for _, commands in programs.siril_calls)
    messages = [r.getMessage() for r in caplog.records]
    assert any("Siril PCC could not run (no local Gaia catalog" in m for m in messages)
    assert any("Colour calibration (from the stars) done" in m for m in messages)


def test_the_stars_method_never_starts_pcc(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the offline method makes no Siril call for the calibration."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True, colour_method="stars")
    programs = _Programs()
    programs.install(monkeypatch)

    write_stack_preview(str(stack))

    assert not any(any(c.startswith("pcc ") for c in commands) for _, commands in programs.siril_calls)


def test_starnet_splits_the_image_then_stretches_the_pieces_apart(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify StarNet splits the image and the picture is the blend."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True, colour_method="stars")
    programs = _Programs()
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack), settings=PreviewSettings(starnet=True)) is not None

    commands = [call[1] for call in programs.siril_calls]
    assert any("starnet -stretch" in c for c in commands)
    stretch_of_starless = next(c for c in commands if 'save "starless_stretched"' in c)
    assert 'load "starless.fits"' in stretch_of_starless
    assert any(x.startswith("mtf ") for x in stretch_of_starless)
    # The final script saves the blend, and nothing stretches it again.
    assert 'load "stretched.fits"' in commands[-1]
    assert not any(x.startswith("autostretch") for x in commands[-1])


def test_the_star_toning_is_not_used_with_starnet(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the stars are not dimmed twice."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, star_tone=True)
    programs = _Programs()
    programs.install(monkeypatch)

    write_stack_preview(str(stack), settings=PreviewSettings(starnet=True))

    assert programs.tone_calls == []


def test_a_failed_starnet_falls_back_to_the_normal_stretch(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Verify a StarNet that cannot run still gives a picture."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=False)
    programs = _Programs(starnet_result=False)
    programs.install(monkeypatch)

    with caplog.at_level("INFO", logger=stack_preview.logger.name):
        assert write_stack_preview(str(stack), settings=PreviewSettings(starnet=True)) is not None

    assert any(x.startswith("autostretch") for x in programs.siril_calls[-1][1])
    summary = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Preview steps:"))
    assert "StarNet failed: the normal stretch is used" in summary


def test_starnet_is_not_used_unless_asked_for(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the default preview never starts StarNet."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None)
    programs = _Programs()
    programs.install(monkeypatch)

    write_stack_preview(str(stack))

    assert not any("starnet -stretch" in call[1] for call in programs.siril_calls)


def test_a_preview_writes_a_record_of_the_settings_it_used(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record holds the settings used, the steps and the custom flag."""
    _configure(monkeypatch, "siril-cli", None, "/opt/cc/denoise", strength=0.5, star_tone=True)
    _Programs().install(monkeypatch)
    steps: list[str] = []

    assert write_stack_preview(str(stack), PreviewSettings(denoise_strength=0.8), steps) is not None

    record = read_preview_settings(str(stack))
    assert record is not None
    masked = record["settings_used"].pop("masked_stretch")
    assert masked["used"] is False
    assert "not a nebula or a galaxy" in masked["reason"]
    assert record["settings_used"].pop("background")["method"] == "none"
    assert record["settings_used"].pop("object_category")["category"] is None
    # The test's stack file holds no pixels, so no stretch measurement is made.
    assert record["settings_used"].pop("stretch_sky") == {}
    assert record["settings_used"] == {
        "denoise": True,
        "denoise_strength": 0.8,
        "star_toning": True,
        "starnet": False,
        "bright_object": False,
    }
    assert record["custom"] is True
    assert record["steps_run"] == steps
    assert record["made_at"]


def test_a_preview_with_no_overrides_is_not_custom(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The configured values are recorded, and custom is false."""
    _configure(monkeypatch, "siril-cli", None, None, star_tone=False)
    _Programs().install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None

    record = read_preview_settings(str(stack))
    assert record is not None
    assert record["custom"] is False
    assert record["settings_used"]["denoise"] is False
    assert record["settings_used"]["denoise_strength"] is None


def test_a_preview_records_the_picture_settings_in_force(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record keeps the picture settings, which then match."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu", "/opt/cc/denoise", strength=0.5, star_tone=True)
    _Programs().install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None

    record = read_preview_settings(str(stack))
    assert record is not None
    assert record["configuration"] == {
        "graxpert_executable": "graxpert-gpu",
        "cosmic_clarity_executable": "/opt/cc/denoise",
        "cosmic_clarity_strength": 0.5,
        "colour_calibration": False,
        "colour_calibration_method": "stars",
        "remove_green_noise": True,
        "star_toning": True,
        "background_plane_limit": 0.03,
    }
    assert preview_is_out_of_date(str(stack)) is None


def test_a_picture_is_out_of_date_when_graxpert_is_newly_set_up(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setting up GraXpert after the picture was made calls for a new one."""
    _configure(monkeypatch, "siril-cli", None)
    _Programs().install(monkeypatch)
    assert write_stack_preview(str(stack)) is not None

    _configure(monkeypatch, "siril-cli", "graxpert-gpu")

    assert preview_is_out_of_date(str(stack)) == "the picture settings changed (graxpert executable)"


def test_a_missing_picture_is_out_of_date(stack: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stack with no picture gets one."""
    _configure(monkeypatch, "siril-cli", None)
    assert preview_is_out_of_date(str(stack)) == "the stack has no preview picture"


def test_a_custom_picture_is_never_replaced(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A picture made with one-run choices is kept when settings change."""
    _configure(monkeypatch, "siril-cli", None)
    _Programs().install(monkeypatch)
    assert write_stack_preview(str(stack), PreviewSettings(star_toning=True)) is not None

    _configure(monkeypatch, "siril-cli", "graxpert-gpu")

    assert preview_is_out_of_date(str(stack)) is None


def test_a_picture_with_an_older_record_is_kept(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record without the settings gives nothing to compare: keep it."""
    _configure(monkeypatch, "siril-cli", None)
    _Programs().install(monkeypatch)
    assert write_stack_preview(str(stack)) is not None
    record_path = preview_settings_path_for(str(stack))
    record = json.loads(Path(record_path).read_text(encoding="utf-8"))
    del record["configuration"]
    Path(record_path).write_text(json.dumps(record), encoding="utf-8")

    _configure(monkeypatch, "siril-cli", "graxpert-gpu")

    assert preview_is_out_of_date(str(stack)) is None


def test_a_bright_object_record_says_the_cleanup_was_skipped(
    tmp_path: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record shows no denoise and no star toning for a bright object."""
    y, x = np.mgrid[0:300, 0:300]
    disc = 0.0002 + 0.00004 * np.random.default_rng(4).standard_normal((300, 300))
    disc[np.hypot(y - 150, x - 150) < 70] += 0.4
    path = tmp_path / "Moon_L_Stacked.fits"
    fits.writeto(path, disc.astype(np.float32))
    _configure(monkeypatch, "siril-cli", "graxpert-gpu", "/opt/cc/denoise", star_tone=True)
    _Programs().install(monkeypatch)

    assert write_stack_preview(str(path), PreviewSettings(denoise=True)) is not None

    record = read_preview_settings(str(path))
    assert record is not None
    assert record["settings_used"]["bright_object"] is True
    assert record["settings_used"]["masked_stretch"]["used"] is False
    assert "bright-object" in record["settings_used"]["masked_stretch"]["reason"]
    assert record["settings_used"]["denoise"] is False
    assert record["settings_used"]["star_toning"] is False
    assert record["custom"] is True


def test_a_failed_preview_leaves_no_record_and_removes_the_old_one(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed run deletes the old record with the old pictures."""
    _configure(monkeypatch, "siril-cli", None)
    record_path = Path(preview_settings_path_for(str(stack)))
    record_path.write_text("{}")
    _Programs(siril_result=False).install(monkeypatch)

    assert write_stack_preview(str(stack)) is None

    assert not record_path.exists()
    assert read_preview_settings(str(stack)) is None


def test_a_record_that_cannot_be_saved_does_not_fail_the_preview(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write error is logged and the picture still counts."""
    _configure(monkeypatch, "siril-cli", None)
    _Programs().install(monkeypatch)
    unwritable = str(stack.parent / "no" / "x.json")
    monkeypatch.setattr(stack_preview, "preview_settings_path_for", lambda path: unwritable)

    assert write_stack_preview(str(stack)) is not None
    assert read_preview_settings(str(stack)) is None


def test_a_missing_or_broken_record_reads_as_unknown(stack: Path) -> None:
    """None means unknown, never a default."""
    assert read_preview_settings(str(stack)) is None
    Path(preview_settings_path_for(str(stack))).write_text("not json")
    assert read_preview_settings(str(stack)) is None


def _ring_stack_path(tmp_path: Path) -> Path:
    """Write a stack of a smooth ring nebula, like M 57, where a stack belongs.

    Returns
    -------
    path : `pathlib.Path`
        The FITS file.
    """
    from astrometricslib.pipelines.stacking.test.test_masked_stretch import ring_stack

    folder = tmp_path / "M 57"
    folder.mkdir()
    path = folder / "M_57_L_Stacked.fits"
    fits.writeto(path, ring_stack().astype(np.float32))
    return path


def _install_masked_stretch_stand_in(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Replace the blend with a stand-in that records its calls.

    Returns
    -------
    calls : `list` [`tuple` [`str`, `str`]]
        The (linear stack, picture) paths of each call, added to as it runs.
    """
    calls: list[tuple[str, str]] = []

    def blend(linear_path: str, picture_path: str, output_path: str, plan: object) -> bool:
        """Act like the masked stretch, writing a copy.

        Returns
        -------
        succeeded : `bool`
            Always `True`.
        """
        calls.append((linear_path, picture_path))
        Path(output_path).write_bytes(b"masked")
        return True

    monkeypatch.setattr(stack_preview, "apply_masked_stretch_in_file", blend)
    return calls


def test_a_nebula_with_a_smooth_bright_region_gets_the_masked_stretch(
    tmp_path: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stretch is saved first, the blend runs last, the record says so."""
    path = _ring_stack_path(tmp_path)
    _configure(monkeypatch, "siril-cli", None)
    programs = _Programs()
    programs.install(monkeypatch)
    calls = _install_masked_stretch_stand_in(monkeypatch)
    steps: list[str] = []

    assert write_stack_preview(str(path), steps_log=steps, object_category="nebula") is not None

    assert len(calls) == 1
    assert calls[0][0].endswith("stack.fits")
    assert calls[0][1].endswith("stretched.fits")
    assert len(programs.siril_calls) == 2
    assert 'load "masked.fits"' in programs.siril_calls[1][1]
    assert any(step.startswith("Masked stretch done on 1 bright smooth region") for step in steps)
    record = read_preview_settings(str(path))
    assert record is not None
    masked = record["settings_used"]["masked_stretch"]
    assert masked["used"] is True
    assert masked["region_count"] == 1
    assert masked["white_point"] > 0
    assert masked["reason"] == ""
    assert record["steps_run"] == steps


def test_a_cluster_with_the_same_ring_does_not_get_the_masked_stretch(
    tmp_path: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kind decides: one normal script runs and the record says why."""
    path = _ring_stack_path(tmp_path)
    _configure(monkeypatch, "siril-cli", None)
    programs = _Programs()
    programs.install(monkeypatch)
    calls = _install_masked_stretch_stand_in(monkeypatch)
    steps: list[str] = []

    assert write_stack_preview(str(path), steps_log=steps, object_category="cluster") is not None

    assert calls == []
    assert len(programs.siril_calls) == 1
    assert any(
        command.startswith("mtf ") or command.startswith("autostretch")
        for command in programs.siril_calls[0][1]
    )
    record = read_preview_settings(str(path))
    assert record is not None
    assert record["settings_used"]["masked_stretch"]["used"] is False
    assert "cluster" in record["settings_used"]["masked_stretch"]["reason"]


def test_a_failed_masked_stretch_keeps_the_normal_picture_and_is_reported(
    tmp_path: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blend that fails is skipped like any other step, with a notice."""
    path = _ring_stack_path(tmp_path)
    _configure(monkeypatch, "siril-cli", None)
    _Programs().install(monkeypatch)
    monkeypatch.setattr(stack_preview, "apply_masked_stretch_in_file", lambda *_: False)
    steps: list[str] = []
    skipped: list[stack_preview.SkippedPreviewStep] = []

    picture = write_stack_preview(str(path), steps_log=steps, skipped_steps=skipped, object_category="galaxy")

    assert picture is not None
    assert "Masked stretch skipped after a failure" in steps
    assert [step.step_name for step in skipped] == ["Masked stretch"]


def _write_tilted_stack(path: Path, tilt_fraction: float) -> None:
    """Write a 256 by 256 stack with a tilted sky and a central blob.

    Parameters
    ----------
    path : `pathlib.Path`
        The file to write.
    tilt_fraction : `float`
        The change of the sky from the left to the right edge, as a fraction
        of the sky level (0.05).
    """
    rows, columns = np.mgrid[0:256, 0:256]
    sky = 0.05 * (1.0 + tilt_fraction * (columns / 255.0 - 0.5))
    blob = 0.05 * np.exp(-((rows - 128) ** 2 + (columns - 128) ** 2) / (2 * 15.0**2))
    noise = np.random.default_rng(3).normal(0.0, 0.0002, (256, 256))
    fits.writeto(path, (sky + blob + noise).astype(np.float32), overwrite=True)


def test_a_flat_sky_gets_a_plane_and_graxpert_does_not_run(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stack below the gradient limit skips GraXpert and records why."""
    _write_tilted_stack(stack, 0.01)
    _configure(monkeypatch, "siril-cli", "graxpert-gpu")
    programs = _Programs()
    programs.install(monkeypatch)
    steps: list[str] = []
    background_outcomes: list = []

    assert write_stack_preview(str(stack), steps_log=steps, background_outcomes=background_outcomes)

    assert programs.flatten_calls == []
    assert steps[0].startswith("Background: plane fit (gradient 1.0%, limit 3.0%)")
    assert 'load "stack.fits"' not in programs.siril_calls[-1][1]
    assert 'load "flat.fits"' in programs.siril_calls[-1][1]
    background = read_preview_settings(str(stack))["settings_used"]["background"]
    assert background["method"] == "plane"
    assert background["gradient_fraction"] == pytest.approx(0.01, abs=0.003)
    assert background["limit_fraction"] == pytest.approx(0.03)
    assert background["radial_used"] is False
    assert background["radial_fraction"] is None or abs(background["radial_fraction"]) < 0.002
    assert background["radial_reason"]
    assert len(background_outcomes) == 1 and background_outcomes[0].gradient is None
    assert "plane fit" in background_outcomes[0].reason


def test_a_strong_gradient_still_runs_graxpert(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stack above the gradient limit takes the GraXpert path as before."""
    _write_tilted_stack(stack, 0.10)
    _configure(monkeypatch, "siril-cli", "graxpert-gpu")
    programs = _Programs()
    programs.install(monkeypatch)
    steps: list[str] = []

    assert write_stack_preview(str(stack), steps_log=steps)

    assert len(programs.flatten_calls) == 1
    assert steps[0] == "GraXpert done"
    background = read_preview_settings(str(stack))["settings_used"]["background"]
    assert background["method"] == "graxpert"
    assert background["gradient_fraction"] == pytest.approx(0.10, abs=0.01)
    assert background["limit_fraction"] == pytest.approx(0.03)


def test_a_plane_that_cannot_be_written_falls_back_to_graxpert(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the plane step fails, GraXpert runs and the record says so."""
    _write_tilted_stack(stack, 0.01)
    _configure(monkeypatch, "siril-cli", "graxpert-gpu")
    programs = _Programs()
    programs.install(monkeypatch)
    monkeypatch.setattr(stack_preview, "subtract_sky_plane_in_file", lambda *arguments: False)
    steps: list[str] = []
    skipped: list = []

    assert write_stack_preview(str(stack), steps_log=steps, skipped_steps=skipped)

    assert len(programs.flatten_calls) == 1
    assert steps[0] == "GraXpert done"
    assert [step.step_name for step in skipped] == ["Background plane fit"]
    background = read_preview_settings(str(stack))["settings_used"]["background"]
    assert background["method"] == "graxpert"
    assert "plane could not be subtracted" in background["reason"]


def test_a_picture_made_before_the_plane_gate_is_made_again(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record without the limit differs from the settings now."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu")
    _Programs().install(monkeypatch)
    assert write_stack_preview(str(stack)) is not None
    assert preview_is_out_of_date(str(stack)) is None
    record_path = preview_settings_path_for(str(stack))
    record = json.loads(Path(record_path).read_text(encoding="utf-8"))
    del record["configuration"]["background_plane_limit"]
    Path(record_path).write_text(json.dumps(record), encoding="utf-8")

    reason = preview_is_out_of_date(str(stack))

    assert reason is not None and "background plane limit" in reason


def test_a_cluster_inside_a_nebula_reaches_the_masked_stretch_as_a_nebula(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The masked stretch gets the refined kind, and the record shows it."""
    from astrometricslib.pipelines.stacking.post_processing.cluster_nebula import CategoryVerdict
    from astrometricslib.pipelines.stacking.post_processing.masked_stretch import MaskedStretchPlan

    _configure(monkeypatch, "siril-cli", None)
    _Programs().install(monkeypatch)
    asked: list[str | None] = []
    monkeypatch.setattr(
        stack_preview,
        "refine_object_category",
        lambda category, anchor, path: CategoryVerdict("nebula", category, "Rosette Nebula", "inside it"),
    )

    def plan(path: str, category: str | None, anchor: object) -> MaskedStretchPlan:
        """Remember the kind the masked stretch was given.

        Returns
        -------
        plan : `MaskedStretchPlan`
            A plan that is not used.
        """
        asked.append(category)
        return MaskedStretchPlan(used=False, reason="test", category=category)

    monkeypatch.setattr(stack_preview, "plan_masked_stretch_for_file", plan)

    assert write_stack_preview(str(stack), object_category="cluster")

    assert asked == ["nebula"]
    recorded = read_preview_settings(str(stack))["settings_used"]
    assert recorded["object_category"]["category"] == "nebula"
    assert recorded["object_category"]["catalog_category"] == "cluster"
    assert recorded["object_category"]["nebula"] == "Rosette Nebula"
    assert recorded["masked_stretch"]["category"] == "nebula"


def test_a_calibrated_colour_stack_has_its_green_removed_in_a_step_of_its_own(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `rmgreen` runs alone, so the sky is measured after it."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True)
    programs = _Programs()
    programs.install(monkeypatch)

    write_stack_preview(str(stack))

    green_step = programs.siril_calls[0][1]
    assert green_step[1:5] == ['load "colour.fits"', "rmgreen", 'save "greened"', "close"]
    final_commands = programs.siril_calls[-1][1]
    assert "rmgreen" not in final_commands


def test_a_calibrated_colour_stack_is_stretched_from_its_measured_sky(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a calibrated colour stack gets a measured sky and a record."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True)
    programs = _Programs()
    programs.install(monkeypatch)
    steps: list[str] = []

    write_stack_preview(str(stack), steps_log=steps)

    final_commands = programs.siril_calls[-1][1]
    assert any(command.startswith("mtf ") for command in final_commands)
    assert "Remove Green Noise done before the stretch" in steps
    record = read_preview_settings(str(stack))
    assert record is not None
    assert record["settings_used"]["stretch_sky"]["sky_noise"] > 0.0


def test_a_failed_green_step_leaves_the_green_removal_to_the_stretch(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the picture is still made as before if the green step fails."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True)
    programs = _Programs(green_result=False)
    programs.install(monkeypatch)
    skipped: list[stack_preview.SkippedPreviewStep] = []

    assert write_stack_preview(str(stack), skipped_steps=skipped) is not None

    final_commands = programs.siril_calls[-1][1]
    assert "rmgreen" in final_commands
    assert any(command.startswith("autostretch -linked") for command in final_commands)
    assert [step.step_name for step in skipped] == ["Remove Green Noise"]


def test_the_sky_of_a_single_channel_stack_is_measured_as_before(tmp_path: Path) -> None:
    """Verify a mono stack still gets a measured sky with no green step."""
    path = tmp_path / "mono.fits"
    rng = np.random.default_rng(3)
    fits.writeto(path, rng.normal(0.01, 0.0005, (200, 200)).astype(np.float32))

    sky = stack_preview.sky_background_for_file(str(path))

    assert sky is not None
    assert sky[0] == pytest.approx(0.01, abs=0.0005)
    assert sky[1] == pytest.approx(0.0005, rel=0.1)
