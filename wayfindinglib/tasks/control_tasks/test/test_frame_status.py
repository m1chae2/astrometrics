"""Tests for the one-call frame status of a target.

The status compares three places: the telescope computer, the frames drive
and the library database. The tests use a fake telescope and a fake library
so each kind of difference can be set up by hand.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from wayfindinglib.tasks.control_tasks import frame_status


class FakeDriver:
    """A telescope computer holding three named frames of one byte count."""

    def list_remote_files_with_sizes(self, folder: str) -> list[tuple[str, int]]:
        """List the remote frames.

        Returns
        -------
        files : `list` [`tuple` [`str`, `int`]]
            Relative path and size of each frame.
        """
        return [("Light/a.fits", 4), ("Light/b.fits", 4), ("Light/c.fits", 4)]


#: The stand-in science library the observatory holds. The `library` fixture
#: fills in its targets.
_FAKE_LIBRARY = SimpleNamespace(targets=None)


def make_observatory(frames_root: Path) -> SimpleNamespace:
    """Build a stand-in observatory whose frames drive is `frames_root`.

    Returns
    -------
    observatory : `types.SimpleNamespace`
        With the configuration and driver the status reads.
    """
    config = SimpleNamespace(get_frames_path=lambda: str(frames_root))
    return SimpleNamespace(config=config, remote_transfer_driver=FakeDriver(), astrometrics=_FAKE_LIBRARY)


@pytest.fixture
def library(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the library with one holding frames `a` and `d`.

    Returns
    -------
    names : `list` [`str`]
        The file names the fake library holds; a test may change it.
    """
    names = ["a.fits", "d.fits"]

    def frames() -> list[SimpleNamespace]:
        """Give the library's frame records.

        Returns
        -------
        records : `list` [`types.SimpleNamespace`]
            One light-frame record per name.
        """
        return [SimpleNamespace(path=f"/anywhere/{name}", role="LIGHT") for name in names]

    fake_targets = SimpleNamespace(list=lambda: [], get=lambda target_id: SimpleNamespace(frames=frames()))
    monkeypatch.setattr(_FAKE_LIBRARY, "targets", fake_targets)
    monkeypatch.setattr(
        frame_status.remote_transfer_tasks,
        "plan_target_download",
        lambda observatory, target_id: {"remote_folder": "M_57"},
    )
    return names


def test_each_place_and_each_difference_is_reported(tmp_path: Path, library: list[str]) -> None:
    """Name each frame that is in one place and not another."""
    folder = tmp_path / "lights" / "M 57" / "Scope" / "Camera"
    folder.mkdir(parents=True)
    (folder / "a.fits").write_bytes(b"1234")
    (folder / "b.fits").write_bytes(b"1234")
    (folder / "e.fits").write_bytes(b"12")
    status = frame_status.build_frame_status(make_observatory(tmp_path), "M 57")
    assert status["counts"] == {"telescope": 3, "disk": 3, "set_aside_on_disk": 0, "library": 2}
    assert status["on_telescope_not_on_disk"]["examples"] == ["c.fits"]
    assert status["on_disk_not_on_telescope"]["examples"] == ["e.fits"]
    assert status["on_disk_not_in_library"]["examples"] == ["b.fits", "e.fits"]
    assert status["in_library_not_on_disk"]["examples"] == ["d.fits"]


def test_set_aside_frames_are_counted_apart(tmp_path: Path, library: list[str]) -> None:
    """A frame in an _excluded folder is not missing, and not in place."""
    folder = tmp_path / "lights" / "M 57" / "_excluded"
    folder.mkdir(parents=True)
    (folder / "c.fits").write_bytes(b"1234")
    status = frame_status.build_frame_status(make_observatory(tmp_path), "M 57")
    assert status["counts"]["set_aside_on_disk"] == 1
    assert "c.fits" not in status["on_telescope_not_on_disk"]["examples"]


def test_an_unreachable_telescope_leaves_the_other_places(
    tmp_path: Path, library: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drive and library parts are still reported."""

    def refuse(observatory: object, target_id: str) -> dict:
        """Fail the way an unreachable telescope does.

        Raises
        ------
        ValueError
            Always.
        """
        raise ValueError("telescope computer not reachable")

    monkeypatch.setattr(frame_status.remote_transfer_tasks, "plan_target_download", refuse)
    status = frame_status.build_frame_status(make_observatory(tmp_path), "M 57")
    assert "not reachable" in status["telescope_error"]
    assert status["counts"]["telescope"] is None
    assert "on_disk_not_in_library" in status
