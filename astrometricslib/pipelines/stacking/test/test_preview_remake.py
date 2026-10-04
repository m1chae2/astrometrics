"""Tests for making a stack's preview again without restacking.

The preview step itself is faked. These tests check that the stack file is
left alone, the old pictures are kept and put back on a failure, the
overrides reach the step, and the target's processed image is updated.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib import Astrometrics, Target
from astrometricslib.pipelines.shared.stack_preview_path import preview_path_for, processed_fits_path_for
from astrometricslib.pipelines.stacking.post_processing import preview_remake
from astrometricslib.pipelines.stacking.post_processing.preview_remake import (
    PREVIOUS_PICTURES_FOLDER,
    remake_stack_preview,
)
from astrometricslib.pipelines.stacking.post_processing.stack_preview import PreviewSettings


@pytest.fixture
def stacked(tmp_path: Path) -> Path:
    """Make a stack with a preview JPEG and a processed FITS beside it.

    Returns
    -------
    stack : `pathlib.Path`
        The stack file.
    """
    folder = tmp_path / "NGC 7331"
    folder.mkdir()
    path = folder / "NGC_7331_L_Stacked.fits"
    path.write_bytes(b"stack")
    Path(preview_path_for(str(path))).write_bytes(b"old jpeg")
    Path(processed_fits_path_for(str(path))).write_bytes(b"old fits")
    return path


def make_target(stack: Path, spectral: bool = False) -> Target:
    """Make a target that shows the given stack.

    Returns
    -------
    target : `Target`
        With the stack set and an old processed picture recorded.
    """
    target = Target(id="NGC 7331")
    stacking = target.spectral_stacking if spectral else target.stacking
    stacking.stacked_image = str(stack)
    stacking.processed_image = processed_fits_path_for(str(stack))
    return target


def fake_preview(monkeypatch: pytest.MonkeyPatch, succeeds: bool = True) -> list[PreviewSettings | None]:
    """Replace the preview step with one that writes new pictures.

    Returns
    -------
    seen : `list`
        The settings each call was given.
    """
    seen: list[PreviewSettings | None] = []

    def write(
        stacked_path: str, settings: PreviewSettings | None = None, steps_log: list | None = None
    ) -> str | None:
        """Remove the old pictures and write new ones, as the real step does.

        Returns
        -------
        preview_path : `str` or `None`
            The new picture, or `None` if told to fail.
        """
        seen.append(settings)
        for old in (preview_path_for(stacked_path), processed_fits_path_for(stacked_path)):
            if os.path.exists(old):
                os.remove(old)
        if steps_log is not None:
            steps_log.extend(["GraXpert done", "Star toning done"])
        if not succeeds:
            return None
        Path(preview_path_for(stacked_path)).write_bytes(b"new jpeg")
        Path(processed_fits_path_for(stacked_path)).write_bytes(b"new fits")
        return preview_path_for(stacked_path)

    monkeypatch.setattr(preview_remake, "write_stack_preview", write)
    return seen


def test_a_remake_replaces_the_pictures_and_leaves_the_stack_alone(
    stacked: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """New pictures replace the old, which are kept; the stack is untouched."""
    seen = fake_preview(monkeypatch)
    target = make_target(stacked)
    settings = PreviewSettings(denoise=False)

    result = remake_stack_preview(target, False, settings)

    assert seen == [settings]
    assert Path(result["preview_path"]).read_bytes() == b"new jpeg"
    assert stacked.read_bytes() == b"stack"
    assert result["stack_file_unchanged"] is True
    assert result["steps_run"] == ["GraXpert done", "Star toning done"]
    assert result["shown_in_viewer"] is True
    kept = {Path(path).name: Path(path).read_bytes() for path in result["previous_pictures"]}
    assert kept == {
        "NGC_7331_L_Stacked_preview.jpg": b"old jpeg",
        "NGC_7331_L_Stacked_processed.fits": b"old fits",
    }
    assert (stacked.parent / PREVIOUS_PICTURES_FOLDER).is_dir()


def test_keep_previous_off_copies_nothing(stacked: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without keep_previous there is no previous-pictures folder."""
    fake_preview(monkeypatch)

    result = remake_stack_preview(make_target(stacked), False, PreviewSettings(), keep_previous=False)

    assert result["previous_pictures"] == []
    assert not (stacked.parent / PREVIOUS_PICTURES_FOLDER).exists()


def test_a_failed_remake_puts_the_old_pictures_back(stacked: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure after the step removed the old pictures restores them."""
    fake_preview(monkeypatch, succeeds=False)

    result = remake_stack_preview(make_target(stacked), False, PreviewSettings())

    assert "put back" in result["error"]
    assert Path(preview_path_for(str(stacked))).read_bytes() == b"old jpeg"
    assert Path(processed_fits_path_for(str(stacked))).read_bytes() == b"old fits"
    assert result["steps_run"] == ["GraXpert done", "Star toning done"]


def test_a_target_without_a_stack_is_an_error(tmp_path: Path) -> None:
    """No stack file means nothing to make a picture of."""
    assert "has no stack" in remake_stack_preview(Target(id="T"), False, PreviewSettings())["error"]
    target = Target(id="T")
    target.stacking.stacked_image = str(tmp_path / "gone.fits")
    assert "is missing" in remake_stack_preview(target, False, PreviewSettings())["error"]
    assert "has no spectral stack" in remake_stack_preview(Target(id="T"), True, PreviewSettings())["error"]


def make_astrometrics(saved: list[int]) -> Astrometrics:
    """Build an `Astrometrics` whose stacking slot and catalog are fakes.

    Returns
    -------
    astrometrics : `Astrometrics`
        The object under test.
    """
    from contextlib import nullcontext

    astrometrics = Astrometrics.__new__(Astrometrics)
    astrometrics.processing = SimpleNamespace(acquire_stacking_slot=nullcontext)
    astrometrics.targets = SimpleNamespace(save=lambda: saved.append(1))
    return astrometrics


def test_the_method_checks_its_inputs_and_saves_the_target(
    stacked: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bad inputs are refused, and a shown picture is saved to the catalog."""
    saved: list[int] = []
    astrometrics = make_astrometrics(saved)
    target = make_target(stacked)
    assert "frame_type" in astrometrics.remake_preview(target, frame_type="all")["error"]
    assert "between 0 and 1" in astrometrics.remake_preview(target, denoise_strength=1.5)["error"]
    assert saved == []

    seen = fake_preview(monkeypatch)
    result = astrometrics.remake_preview(target, denoise=False, denoise_strength=0.4, star_toning=True)

    assert seen == [PreviewSettings(denoise=False, denoise_strength=0.4, star_toning=True)]
    assert result["shown_in_viewer"] is True
    assert saved == [1]
