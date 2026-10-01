"""Tests for the flattened, stretched picture written beside a finished stack.

Neither Siril nor GraXpert is started. Stand-ins replace the functions that
run them, or the `subprocess.run` call inside them, so the tests check what
the module asks each program to do and how it reacts when one succeeds or
fails.
"""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib.pipelines.shared.stack_preview_path import preview_path_for
from astrometricslib.pipelines.stacking import stack_preview
from astrometricslib.pipelines.stacking.stack_preview import (
    build_preview_script,
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
) -> None:
    """Give the module the commands for Siril, GraXpert and Cosmic Clarity."""
    monkeypatch.setattr(
        stack_preview,
        "get_configuration",
        lambda: SimpleNamespace(
            get_siril_executable=lambda: siril,
            get_graxpert_executable=lambda: graxpert,
            get_cosmic_clarity_denoise_executable=lambda: cosmic_clarity,
            get_cosmic_clarity_denoise_strength=lambda: strength,
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

    def flatten(self, graxpert_executable: str, input_path: str, output_stem: str) -> bool:
        """Act like GraXpert, writing the flattened copy if told to succeed.

        Returns
        -------
        succeeded : `bool`
            Whether this stand-in was told to succeed.
        """
        self.flatten_calls.append((graxpert_executable, input_path, output_stem))
        if self.flatten_result:
            Path(f"{output_stem}.fits").write_bytes(b"flat")
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

    def siril(self, directory: str, commands: list[str], siril_executable: str) -> bool:
        """Act like Siril, writing the picture if told to succeed.

        Returns
        -------
        succeeded : `bool`
            Whether this stand-in was told to succeed.
        """
        self.siril_calls.append((directory, commands))
        if self.siril_result:
            (Path(directory) / "preview.jpg").write_bytes(b"jpeg")
        return self.siril_result

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Put the stand-ins in place of the real runners."""
        monkeypatch.setattr(stack_preview, "flatten_background", self.flatten)
        monkeypatch.setattr(stack_preview, "denoise_with_cosmic_clarity", self.denoise)
        monkeypatch.setattr(stack_preview, "run_preview_script", self.siril)


def test_the_preview_is_named_after_the_stack() -> None:
    """Verify the picture sits beside the stack with a suffix and .jpg."""
    assert preview_path_for("/lib/M 13/M_13_L_Stacked.fits") == "/lib/M 13/M_13_L_Stacked_preview.jpg"


def test_the_script_stretches_and_saves_without_touching_the_stack() -> None:
    """Verify the script saves a JPEG and never saves a FITS."""
    commands = build_preview_script("M_13_L_Stacked.fits", "M_13_L_Stacked_preview")

    assert commands[1:4] == [
        'load "M_13_L_Stacked.fits"',
        "autostretch",
        'savejpg "M_13_L_Stacked_preview" 90',
    ]
    assert not any(command.startswith(("save ", "savefits")) for command in commands)


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


def test_the_steps_run_in_order_and_each_starts_from_the_last(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify GraXpert runs first, Cosmic Clarity next, then the stretch."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu", "/opt/cc/denoise", strength=0.7)
    programs = _Programs()
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None

    executable, denoise_input, denoise_output, strength = programs.denoise_calls[0]
    assert executable == "/opt/cc/denoise"
    assert Path(denoise_input).name == "flat.fits"
    assert Path(denoise_output).name == "denoised.fits"
    assert strength == pytest.approx(0.7)
    assert 'load "denoised.fits"' in programs.siril_calls[0][1]
    assert stack.read_bytes() == b"stack"
    assert list(scratch_root.iterdir()) == []


def test_cosmic_clarity_alone_denoises_the_plain_copy(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Cosmic Clarity works without GraXpert."""
    _configure(monkeypatch, "siril-cli", None, "/opt/cc/denoise")
    programs = _Programs()
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None
    assert programs.flatten_calls == []
    assert Path(programs.denoise_calls[0][1]).name == "stack.fits"
    assert 'load "denoised.fits"' in programs.siril_calls[0][1]


def test_a_failed_denoise_keeps_the_flattened_copy(
    stack: Path, scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a Cosmic Clarity failure falls back to the GraXpert result."""
    _configure(monkeypatch, "siril-cli", "graxpert-gpu", "/opt/cc/denoise")
    programs = _Programs(denoise_result=False)
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None
    assert 'load "flat.fits"' in programs.siril_calls[0][1]


def test_a_denoise_program_that_cannot_start_keeps_the_previous_copy(
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
    assert 'load "stack.fits"' in programs.siril_calls[0][1]


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
    monkeypatch.setattr(
        "datastore.process_locks.acquire_resource_slot", lambda *args, **kwargs: nullcontext()
    )
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
