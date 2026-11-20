"""Unit tests for the light target list (`TargetService.get_targets`).

The target list of the app shows each target by name and picture, so it does
not need the thousands of frame records. ``detail="summary"`` returns every
target with an empty frame list, and everything else the same.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from astrometricslib import FrameRecord, Target
from astrometricslib.exceptions import InvalidArgumentError
from backend.services.data.target_service import TargetService


def _service_with(*targets: Target) -> TargetService:
    """Build a TargetService whose library holds `targets`.

    Parameters
    ----------
    *targets : `Target`
        The targets the fake library knows.

    Returns
    -------
    service : `TargetService`
        A service with a mocked library.
    """
    service = TargetService.__new__(TargetService)
    service.astrometrics = MagicMock()
    service.astrometrics.targets.list.return_value = list(targets)
    service.astrometrics.targets.get.side_effect = lambda target_id: next(
        (target for target in targets if target.id == target_id), None
    )
    return service


def _target(target_id: str) -> Target:
    """Build a target with two frames and an exposure total.

    Returns
    -------
    target : `Target`
        The target.
    """
    target = Target(
        id=target_id,
        ra="01 33 50.9",
        dec="+30 39 37",
        frames=[FrameRecord(path="/a.fits"), FrameRecord(path="/b.fits")],
    )
    target.exposure_sec = 120.0
    target.stacking.stacked_image = f"/stacks/{target_id}.fits"
    return target


def test_the_full_list_is_the_default() -> None:
    """With no detail the targets come back as they are, frames and all."""
    first = _target("M 33")
    result = _service_with(first).get_targets()

    assert result == [first]
    assert len(result[0].frames) == 2


def test_the_summary_list_has_no_frames_and_the_same_everything_else() -> None:
    """The summary keeps id, position, exposure and stack paths."""
    original = _target("M 33")
    summary = _service_with(original, _target("M 31")).get_targets(detail="summary")

    assert [target.id for target in summary] == ["M 33", "M 31"]
    assert summary[0].frames == []
    assert summary[0].ra == original.ra
    assert summary[0].exposure_sec == pytest.approx(120.0)
    assert summary[0].stacking.stacked_image == "/stacks/M 33.fits"
    dumped = summary[0].model_dump(mode="json", by_alias=True)
    expected = original.model_dump(mode="json", by_alias=True)
    assert dumped == {**expected, "frames": []}


def test_the_summary_leaves_the_held_target_alone() -> None:
    """The copy has no frames; the held target keeps its frames."""
    original = _target("M 33")
    _service_with(original).get_targets(detail="summary")

    assert len(original.frames) == 2


def test_one_target_can_be_asked_for_as_a_summary() -> None:
    """A single target by id, or `None` for an unknown one."""
    service = _service_with(_target("M 33"))

    assert service.get_targets("M 33", detail="summary").frames == []
    assert len(service.get_targets("M 33").frames) == 2
    assert service.get_targets("M 99", detail="summary") is None


def test_an_unknown_detail_is_refused() -> None:
    """Only ``full`` and ``summary`` are offered."""
    with pytest.raises(InvalidArgumentError, match="detail"):
        _service_with(_target("M 33")).get_targets(detail="frames")


def test_the_file_list_sends_each_frames_focal_length() -> None:
    """Each file carries its focal length as a float, or None if unknown."""
    target = _target("M 27")
    target.frames = [
        FrameRecord(path="/lens.fits", focalLengthMm=300.0),
        FrameRecord(path="/scope.fits", focalLengthMm=405.2),
        FrameRecord(path="/unknown.fits"),
    ]
    files = _service_with(target).get_file_list("M 27")["files"]

    assert [file["focalLengthMm"] for file in files] == [300.0, 405.2, None]
    assert all(value is None or type(value) is float for value in (file["focalLengthMm"] for file in files))


def test_file_list_reports_only_stacks_that_exist(tmp_path: Path) -> None:
    """A missing stack gives none; a moved one is found in the stacks."""
    moved = tmp_path / "stacks" / "lights" / "Mars" / "Mars_SPEC_Stacked.fits"
    moved.parent.mkdir(parents=True)
    moved.write_text("x")
    target = _target("Mars")
    target.stacking.stacked_image = str(tmp_path / "lights" / "Mars" / "Mars_NONE_Stacked.fits")
    target.spectral_stacking.stacked_image = str(tmp_path / "lights" / "Mars" / "Mars_SPEC_Stacked.fits")
    service = _service_with(target)
    service.config = MagicMock()
    service.config.get_stacks_path.return_value = str(tmp_path / "stacks")
    service.get_targets = lambda target_id: target

    result = service.get_file_list("Mars")

    assert result["stackedImage"] is None
    assert result["stackedSpectralTarget"] == str(moved)
