"""Tests for the Cosmic Clarity denoise of the stack preview.

A small stand-in program takes Cosmic Clarity's place, so the tests check
which files the module stages in the program's folders and which it removes.
"""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib.pipelines.stacking.post_processing import (
    preview_cosmic_clarity,
    stack_preview,
)
from astrometricslib.pipelines.stacking.post_processing.preview_cosmic_clarity import (
    denoise_with_cosmic_clarity,
)


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
    monkeypatch.setattr(preview_cosmic_clarity, "get_configuration", lambda: SimpleNamespace())
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

    monkeypatch.setattr(preview_cosmic_clarity, "run_registered", fake_run)
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
