"""Tests for the flattened, stretched picture written beside a finished stack.

Neither Siril nor GraXpert is started. Stand-ins replace the functions that
run them, or the `subprocess.run` call inside them, so the tests check what
the module asks each program to do and how it reacts when one succeeds or
fails.
"""

import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.shared.stack_preview_path import preview_path_for
from astrometricslib.pipelines.stacking.post_processing import stack_preview
from astrometricslib.pipelines.stacking.post_processing.bright_object import BrightObjectStretch
from astrometricslib.pipelines.stacking.post_processing.sky_level import SkyLevelChoice
from astrometricslib.pipelines.stacking.post_processing.stack_preview import (
    PreviewSettings,
    build_bright_object_script,
    build_picture_script,
    build_preview_script,
    build_stretch_script,
    denoise_with_cosmic_clarity,
    flatten_background,
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
            get_preview_star_tone_enabled=lambda: star_tone,
        ),
    )


class _Programs:
    """Stand-ins for Siril and GraXpert that record what they were asked."""

    def __init__(
        self, flatten_result: bool = True, siril_result: bool = True, denoise_result: bool = True
    ) -> None:
        """Choose how each program will behave."""
        self.flatten_result = flatten_result
        self.siril_result = siril_result
        self.denoise_result = denoise_result
        self.flatten_calls: list[tuple[str, str, str]] = []
        self.denoise_calls: list[tuple[str, str, str, float]] = []
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

    def denoise(self, executable: str, input_path: str, output_path: str, strength: float) -> bool:
        """Act like Cosmic Clarity, writing a denoised copy if told to succeed.

        Returns
        -------
        succeeded : `bool`
            Whether this stand-in was told to succeed.
        """
        self.denoise_calls.append((executable, input_path, output_path, strength))
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
    assert any(command.startswith("autostretch") for command in stretch_script)
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
    assert any(command.startswith("autostretch") for command in programs.siril_calls[0][1])
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
        "Preview steps: GraXpert done; Siril stretch done; Cosmic Clarity done; Star toning done."
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

    def cannot_start(executable: str, input_path: str, output_path: str, strength: float) -> bool:
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
    """Replace `subprocess.run` inside the module with a recorder.

    Returns
    -------
    captured : `dict`
        Filled in with ``arguments``, and ``script`` if one was passed, when
        the module runs a program.
    """
    captured: dict = {}

    def fake_subprocess_run(arguments: list[str], **_: object) -> stack_preview.subprocess.CompletedProcess:
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

    monkeypatch.setattr(stack_preview.subprocess, "run", fake_subprocess_run)
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
    """Replace `subprocess.run` with a program that denoises its `input`.

    Returns
    -------
    calls : `list` [`list` [`str`]]
        The command lines the module ran.
    """
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **_: object) -> stack_preview.subprocess.CompletedProcess:
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

    monkeypatch.setattr(stack_preview.subprocess, "run", fake_run)
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
