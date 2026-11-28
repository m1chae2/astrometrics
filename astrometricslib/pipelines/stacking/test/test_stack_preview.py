"""Tests for the flattened, stretched picture written beside a finished stack.

Neither Siril nor GraXpert is started. Stand-ins replace the functions that
run them, or the `run_registered` call inside them, so the tests check what
the module asks each program to do and how it reacts when one succeeds or
fails.
"""

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers.siril.core import script_run
from astrometricslib.pipelines.shared.stack_preview_path import preview_path_for, preview_settings_path_for
from astrometricslib.pipelines.stacking.post_processing import (
    preview_color,
    preview_graxpert,
    preview_settings,
    preview_steps,
    stack_preview,
    starnet_stretch,
)
from astrometricslib.pipelines.stacking.post_processing.preview_color import (
    is_colour_image,
)
from astrometricslib.pipelines.stacking.post_processing.preview_settings import (
    PreviewSettings,
    preview_is_out_of_date,
    read_preview_settings,
)
from astrometricslib.pipelines.stacking.post_processing.sky_level import SkyLevelChoice
from astrometricslib.pipelines.stacking.post_processing.stack_preview import write_stack_preview
from astrometricslib.pipelines.stacking.post_processing.sun_like_balance import SunLikeBalance


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
    star_width_match: bool = False,
) -> None:
    """Give the module the commands for Siril, GraXpert and Cosmic Clarity.

    The star toning and the star width matching are off unless asked for, so
    the tests of the other steps see the pipeline without them.
    """
    configuration = SimpleNamespace(
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
        get_preview_star_width_match_enabled=lambda: star_width_match,
    )
    for module in (stack_preview, preview_settings):
        monkeypatch.setattr(module, "get_configuration", lambda: configuration)


class _Programs:
    """Stand-ins for Siril and GraXpert that record what they were asked."""

    def __init__(
        self,
        flatten_result: bool = True,
        siril_result: bool = True,
        denoise_result: bool = True,
        pcc_result: bool = True,
        starnet_result: bool = True,
        pcc_output: str = "",
    ) -> None:
        """Choose how each program will behave."""
        self.flatten_result = flatten_result
        self.siril_result = siril_result
        self.denoise_result = denoise_result
        self.pcc_result = pcc_result
        self.starnet_result = starnet_result
        self.pcc_output = pcc_output
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
        if any(command.startswith("pcc -catalog=") for command in commands):
            if self.pcc_result:
                image = np.random.default_rng(0).normal(0.01, 0.001, (3, 16, 16)).astype(np.float32)
                fits.writeto(Path(directory) / "colour.fits", image)
            script_run._last_siril_output.set(self.pcc_output)
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
        monkeypatch.setattr(preview_graxpert, "flatten_background", self.flatten)
        monkeypatch.setattr(stack_preview, "denoise_with_cosmic_clarity", self.denoise)
        for module in (stack_preview, starnet_stretch):
            monkeypatch.setattr(module, "run_siril_script", self.siril)
        monkeypatch.setattr(stack_preview, "tone_stars_in_file", self.tone)


def test_the_preview_is_named_after_the_stack() -> None:
    """Verify the picture sits beside the stack with a suffix and .jpg."""
    assert preview_path_for("/lib/M 13/M_13_L_Stacked.fits") == "/lib/M 13/M_13_L_Stacked_preview.jpg"


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
    skipped: list[preview_steps.SkippedPreviewStep] = []

    assert write_stack_preview(str(stack), skipped_steps=skipped) is not None

    assert skipped == [preview_steps.SkippedPreviewStep("GraXpert", "GraXpert did not finish")]


def test_a_working_graxpert_reports_no_skipped_step(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify nothing is reported when every step worked."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu")
    _Programs().install(monkeypatch)
    skipped: list[preview_steps.SkippedPreviewStep] = []

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

    monkeypatch.setattr(preview_graxpert, "flatten_background", cannot_start)

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
        "Preview steps: GraXpert done; Colour calibration not used; "
        "Remove Green Noise not used (the stack has a single channel); "
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

    monkeypatch.setattr(stack_preview, "run_siril_script", cannot_start)

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
    monkeypatch.setattr(preview_steps, "_is_usable_image", lambda path: False)

    assert write_stack_preview(str(stack)) is not None
    assert 'load "stack.fits"' in programs.siril_calls[0][1]


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


def test_a_calibrated_colour_stack_is_stretched_with_one_shared_stretch(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify calibrated colours get one stretch for all channels."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True)
    monkeypatch.setattr(preview_color, "balance_sun_like_stars_in_file", _fake_white_balance(True, []))
    programs = _Programs()
    programs.install(monkeypatch)

    write_stack_preview(str(stack))

    commands = programs.siril_calls[-2][1]
    assert 'load "colour.fits"' in commands
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
    assert "platesolve -catalog=gaia -noflip" in pcc_commands
    final_commands = programs.siril_calls[-2][1]
    assert 'load "colour.fits"' in final_commands
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

    assert "platesolve -catalog=gaia -noflip" not in programs.siril_calls[0][1]
    assert "pcc -catalog=gaia" in programs.siril_calls[0][1]


def test_a_failed_pcc_falls_back_to_the_stars_like_the_sun(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Verify no network or no catalog still gives a calibrated picture."""
    _use_online_catalog(monkeypatch, reachable=True)
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True, colour_method="pcc")
    monkeypatch.setattr(preview_color, "balance_sun_like_stars_in_file", _fake_white_balance(True, []))
    programs = _Programs(pcc_result=False)
    programs.install(monkeypatch)

    with caplog.at_level("INFO", logger=stack_preview.logger.name):
        assert write_stack_preview(str(stack)) is not None

    final_commands = programs.siril_calls[-2][1]
    assert 'load "colour.fits"' in final_commands
    assert any(command.startswith("mtf ") for command in final_commands)
    summary = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Preview steps:"))
    assert "Colour calibration (from the stars like the Sun) done" in summary


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
    monkeypatch.setattr(preview_color, "balance_sun_like_stars_in_file", _fake_white_balance(True, []))
    programs = _Programs()
    programs.install(monkeypatch)

    with caplog.at_level("INFO", logger=stack_preview.logger.name):
        assert write_stack_preview(str(stack)) is not None

    assert not any(any(c.startswith("pcc ") for c in commands) for _, commands in programs.siril_calls)
    messages = [r.getMessage() for r in caplog.records]
    assert any("Siril PCC could not run (no local Gaia catalog" in m for m in messages)
    assert any("Colour calibration (from the stars like the Sun) done" in m for m in messages)


def test_without_gaia_colours_the_colours_are_left_uncalibrated(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a field with no white point is reported as uncalibrated."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True, colour_method="stars")
    programs = _Programs()
    programs.install(monkeypatch)
    steps: list[str] = []
    skipped: list[preview_steps.SkippedPreviewStep] = []

    assert write_stack_preview(str(stack), steps_log=steps, skipped_steps=skipped) is not None

    assert "Colour calibration not applied: the colours are left uncalibrated" in steps
    status = read_preview_settings(str(stack))["settings_used"]["colour_calibration_status"]
    assert status["status"] == "uncalibrated"
    assert status["reason"] == "the image has no plate solution"
    assert [step.step_name for step in skipped if step.step_name.startswith("Colour calibration")] == [
        "Colour calibration from the stars like the Sun"
    ]
    assert 'load "stack.fits"' in programs.siril_calls[-2][1]


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
    monkeypatch.setattr(preview_color, "balance_sun_like_stars_in_file", _fake_white_balance(True, []))
    programs = _Programs()
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack), settings=PreviewSettings(starnet=True)) is not None

    commands = [call[1] for call in programs.siril_calls]
    assert any("starnet -stretch" in c for c in commands)
    stretch_of_starless = next(c for c in commands if 'save "starless_stretched"' in c)
    assert 'load "starless.fits"' in stretch_of_starless
    assert any(x.startswith("mtf ") for x in stretch_of_starless)
    # The final script saves the blend, and nothing stretches it again.
    assert 'load "levelled.fits"' in commands[-1]
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

    assert any(x.startswith("autostretch") for x in programs.siril_calls[-2][1])
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
    assert record["settings_used"].pop("remove_green_noise")["ran"] is False
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
        "star_width_match": False,
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
    monkeypatch.setattr(preview_settings, "preview_settings_path_for", lambda path: unwritable)

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
    skipped: list[preview_steps.SkippedPreviewStep] = []

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
    monkeypatch.setattr(preview_graxpert, "subtract_sky_plane_in_file", lambda *arguments: False)
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


def test_a_calibrated_colour_stack_is_not_given_green_removal(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `rmgreen` is left out once colour calibration has run."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True)
    monkeypatch.setattr(preview_color, "balance_sun_like_stars_in_file", _fake_white_balance(True, []))
    programs = _Programs()
    programs.install(monkeypatch)
    steps: list[str] = []

    write_stack_preview(str(stack), steps_log=steps)

    assert not any("rmgreen" in call[1] for call in programs.siril_calls)
    assert any(step.startswith("Remove Green Noise not used (the colours were calibrated") for step in steps)
    recorded = read_preview_settings(str(stack))["settings_used"]["remove_green_noise"]
    assert recorded["ran"] is False
    assert "calibrated" in recorded["reason"]


def test_a_calibrated_colour_stack_is_stretched_from_its_measured_sky(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a calibrated colour stack gets a measured sky and a record."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True)
    monkeypatch.setattr(preview_color, "balance_sun_like_stars_in_file", _fake_white_balance(True, []))
    programs = _Programs()
    programs.install(monkeypatch)
    steps: list[str] = []

    write_stack_preview(str(stack), steps_log=steps)

    final_commands = programs.siril_calls[-2][1]
    assert any(command.startswith("mtf ") for command in final_commands)
    record = read_preview_settings(str(stack))
    assert record is not None
    assert record["settings_used"]["stretch_sky"]["sky_noise"] > 0.0


def test_a_failed_colour_calibration_keeps_the_green_removal(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `rmgreen` still runs when no calibration worked."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True)
    monkeypatch.setattr(
        preview_color,
        "balance_sun_like_stars_in_file",
        lambda *args, **kwargs: SunLikeBalance(applied=False, reason="the field has no Gaia star colors"),
    )
    programs = _Programs()
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None

    final_commands = programs.siril_calls[-2][1]
    assert "rmgreen" in final_commands
    assert not any(command.startswith("autostretch -linked") for command in final_commands)
    recorded = read_preview_settings(str(stack))["settings_used"]["remove_green_noise"]
    assert recorded == {"ran": True, "reason": "the colours were not calibrated"}


def test_the_green_removal_switch_wins_over_the_calibration_rule(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the setting stays the master switch when calibration failed."""
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True, remove_green=False)
    monkeypatch.setattr(
        preview_color,
        "balance_sun_like_stars_in_file",
        lambda *args, **kwargs: SunLikeBalance(applied=False, reason="the field has no Gaia star colors"),
    )
    programs = _Programs()
    programs.install(monkeypatch)

    write_stack_preview(str(stack))

    assert not any("rmgreen" in call[1] for call in programs.siril_calls)
    recorded = read_preview_settings(str(stack))["settings_used"]["remove_green_noise"]
    assert recorded == {"ran": False, "reason": "the setting is off"}


def _fake_white_balance(applied: bool, calls: list[str]) -> object:
    """Make a stand-in for the white-balance check.

    Parameters
    ----------
    applied : `bool`
        Whether the stand-in corrects the image (it copies the input) or
        leaves it alone.
    calls : `list` [`str`]
        Receives the input path of each call.

    Returns
    -------
    check : `Callable`
        The stand-in, with the signature of `balance_sun_like_stars_in_file`.
        Used as the calibration (``as_calibration=True``) it always corrects,
        as with Gaia colors at hand.
    """

    def check(input_path: str, output_path: str, *_: object, as_calibration: bool = False) -> SunLikeBalance:
        """Act like the check, or like the calibration from sun-like stars.

        Returns
        -------
        result : `SunLikeBalance`
            An applied result with gains, or one that says nothing changed.
        """
        calls.append(input_path)
        if not applied and not as_calibration:
            return SunLikeBalance(applied=False, reason="only 3 isolated sun-like stars")
        shutil.copyfile(input_path, output_path)
        return SunLikeBalance(
            applied=True,
            reason="done",
            star_count=41,
            red_to_green_before=1.14,
            blue_to_green_before=0.97,
            red_gain=0.877,
            blue_gain=1.031,
            red_to_green_after=1.0,
            blue_to_green_after=1.0,
        )

    return check


def test_the_white_balance_check_feeds_the_stretch_when_it_corrects(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the check after Siril's calibration feeds the stretch."""
    _write_colour_stack_with_stars(stack)
    _use_online_catalog(monkeypatch, reachable=True)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True, colour_method="pcc")
    calls: list[str] = []
    monkeypatch.setattr(preview_color, "balance_sun_like_stars_in_file", _fake_white_balance(True, calls))
    programs = _Programs()
    programs.install(monkeypatch)
    steps: list[str] = []
    skipped: list[preview_steps.SkippedPreviewStep] = []

    assert write_stack_preview(str(stack), steps_log=steps, skipped_steps=skipped) is not None

    assert calls[0].endswith("colour.fits")
    assert 'load "balanced.fits"' in programs.siril_calls[-2][1]
    assert "White balance check on 41 sun-like stars: red x0.877, blue x1.031" in steps
    recorded = read_preview_settings(str(stack))["settings_used"]["white_balance_check"]
    assert recorded["applied"] is True
    assert recorded["star_count"] == 41
    assert recorded["red_gain"] == pytest.approx(0.877)
    # The stand-in Siril writes no real picture, so the sky levelling skips.
    assert [step for step in skipped if step.step_name != "Sky levelling"] == []


def test_a_check_that_changes_nothing_keeps_the_calibrated_image_and_says_why(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a skipped check keeps the calibrated image and adds a notice."""
    _write_colour_stack_with_stars(stack)
    _use_online_catalog(monkeypatch, reachable=True)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True, colour_method="pcc")
    monkeypatch.setattr(preview_color, "balance_sun_like_stars_in_file", _fake_white_balance(False, []))
    programs = _Programs()
    programs.install(monkeypatch)
    steps: list[str] = []
    skipped: list[preview_steps.SkippedPreviewStep] = []

    assert write_stack_preview(str(stack), steps_log=steps, skipped_steps=skipped) is not None

    assert 'load "colour.fits"' in programs.siril_calls[-2][1]
    assert "White balance check not applied (only 3 isolated sun-like stars)" in steps
    assert [(step.step_name, step.reason) for step in skipped if step.step_name == "White balance check"] == [
        ("White balance check", "only 3 isolated sun-like stars")
    ]
    used = read_preview_settings(str(stack))["settings_used"]
    assert used["white_balance_check"]["applied"] is False
    # The stand-in Siril writes no real picture, so levelling is skipped.
    assert used["sky_levelling"]["applied"] is False
    assert any(sentence.startswith("Sky levelling not applied") for sentence in steps)


def test_the_white_balance_check_does_not_run_without_calibrated_colors(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a stack with no calibration, and a mono stack, skip the check."""
    calls: list[str] = []
    monkeypatch.setattr(preview_color, "balance_sun_like_stars_in_file", _fake_white_balance(True, calls))
    _configure(monkeypatch, "siril-cli", None, colour_calibration=False)
    _Programs().install(monkeypatch)
    _write_colour_stack_with_stars(stack)
    write_stack_preview(str(stack))
    fits.writeto(stack, np.full((32, 32), 0.01, dtype=np.float32), overwrite=True)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True)
    write_stack_preview(str(stack))

    assert calls == []


def test_siril_saying_its_calibration_is_imprecise_becomes_a_notice(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Siril's warning is logged, recorded and kept as a notice."""
    _use_online_catalog(monkeypatch, reachable=True)
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True, colour_method="pcc")
    programs = _Programs(
        pcc_output="log: The photometric color calibration seems to have found an imprecise solution"
    )
    programs.install(monkeypatch)
    skipped: list[preview_steps.SkippedPreviewStep] = []

    assert write_stack_preview(str(stack), skipped_steps=skipped) is not None

    warned = [step for step in skipped if step.still_used]
    assert [step.step_name for step in warned] == ["Siril PCC"]
    assert "imprecise solution" in warned[0].reason
    recorded = read_preview_settings(str(stack))["settings_used"]
    assert "imprecise solution" in recorded["colour_calibration_warning"]


def test_a_precise_pcc_gives_no_warning(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a calibration without Siril's warning adds none."""
    _use_online_catalog(monkeypatch, reachable=True)
    _write_colour_stack_with_stars(stack)
    _configure(monkeypatch, "siril-cli", None, colour_calibration=True, colour_method="pcc")
    _Programs(pcc_output="log: Photometric Color Calibration succeeded.").install(monkeypatch)
    skipped: list[preview_steps.SkippedPreviewStep] = []

    write_stack_preview(str(stack), skipped_steps=skipped)

    assert not any(step.still_used for step in skipped)
    assert "colour_calibration_warning" not in read_preview_settings(str(stack))["settings_used"]
