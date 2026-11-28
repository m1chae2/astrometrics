"""Tests for Siril's plate solve of every frame of a session.

Siril is not started: a stand-in takes the place of the short script run,
reads the script it is given, and writes the converted, solved frames and the
conversion list the way Siril does. The tests check the commands, that the
frames are only linked, that a frame Siril cannot solve gets a reason and does
not stop the others, and that the first frame's focal length is the one the
group uses. Every test uses the temporary Siril settings home of the folder's
``conftest.py``, so the user's own Siril settings are never read or changed.
"""

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers.interfaces.plate_solve_driver import PlateSolveHeader
from astrometricslib._internal.drivers.siril.astrometry import sequence_solve
from astrometricslib._internal.drivers.siril.astrometry.plate_solve import seqplatesolve_command
from astrometricslib._internal.drivers.siril.astrometry.sequence_solve import solve_frames
from astrometricslib._internal.drivers.siril.core import script_run

SOLVED_FOCAL_LENGTH = 404.0


def write_frame(path: Path) -> str:
    """Write a small FITS frame with a start position and an optic.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write the frame.

    Returns
    -------
    path_text : `str`
        The path as text.
    """
    header = fits.Header({"RA": 250.4197, "DEC": 36.44781, "FOCALLEN": 405.0, "XPIXSZ": 3.76})
    fits.PrimaryHDU(np.zeros((8, 8), dtype=np.float32), header).writeto(path)
    return str(path)


def solved_header() -> fits.Header:
    """Build the header Siril writes into a solved frame.

    Returns
    -------
    header : `astropy.io.fits.Header`
        A sky map and the focal length the solution fits.
    """
    return fits.Header({
        "CTYPE1": "RA---TAN",
        "CTYPE2": "DEC--TAN",
        "CRVAL1": 250.4,
        "CRVAL2": 36.5,
        "FOCALLEN": SOLVED_FOCAL_LENGTH,
    })


class FakeSiril:
    """A stand-in for `run_siril_script` that solves like Siril.

    Attributes
    ----------
    group_calls : `list` [`dict`]
        What each sequence run was given: the commands, the keyword
        arguments and the folder's contents when it started.
    """

    def __init__(self, unsolved_numbers: tuple[int, ...] = ()) -> None:
        """Say which frames of a group Siril cannot solve.

        Parameters
        ----------
        unsolved_numbers : `tuple` [`int`], optional
            The numbers of the frames in each group, counted from 1, that
            Siril leaves unsolved.
        """
        self.unsolved_numbers = unsolved_numbers
        self.group_calls: list[dict[str, Any]] = []

    def __call__(self, directory: str, commands: list[str], siril_executable: str, **options: Any) -> bool:
        """Act like one Siril run: link the frames, solve them and print.

        Returns
        -------
        succeeded : `bool`
            Always `True`.
        """
        folder = Path(directory)
        staged = sorted(path.name for path in folder.iterdir() if path.name.startswith("frame_"))
        self.group_calls.append({
            "commands": commands,
            "options": options,
            "executable": siril_executable,
            "files": staged,
            "were_links": [(folder / name).is_symlink() for name in staged],
        })
        lines = []
        conversion = []
        for number, name in enumerate(staged, start=1):
            converted = f"frames_{number:05d}.fits"
            conversion.append(f"'{folder / name}' -> '{converted}'")
            fits.PrimaryHDU(np.zeros((8, 8), dtype=np.float32), solved_header()).writeto(folder / converted)
            if number not in self.unsolved_numbers:
                lines.append(f"log: Image {converted} platesolved and updated")
        (folder / "frames_conversion.txt").write_text("\n".join(conversion) + "\n")
        output = "\n".join(lines) + "\n"
        script_run._last_siril_output.set(output)
        script_run._last_siril_messages.set(output)
        return True

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace the real script run in the sequence-solve module."""
        monkeypatch.setattr(sequence_solve, "run_siril_script", self)


@pytest.fixture
def catalog(tmp_path: Path) -> str:
    """Make a stand-in Gaia catalog file.

    Returns
    -------
    path : `str`
        The file's path.
    """
    path = tmp_path / "siril_cat_healpix8_astro.dat"
    path.write_bytes(b"catalog")
    return str(path)


@pytest.fixture
def first_frame_solves(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Make the solve of the first frame succeed with a known focal length.

    Returns
    -------
    calls : `list` [`dict`]
        The arguments each call of the first-frame solve received.
    """
    calls: list[dict[str, Any]] = []

    def fake_solve_image(image_path: str, siril_executable: str, hints: dict, *args: Any) -> PlateSolveHeader:
        calls.append({"path": image_path, "executable": siril_executable, "hints": hints})
        return PlateSolveHeader.from_header(solved_header())

    monkeypatch.setattr(sequence_solve, "solve_image", fake_solve_image)
    return calls


def test_the_sequence_command_always_forces_and_keeps_the_image_the_right_way_up() -> None:
    """Verify the command carries the options a sequence solve needs."""
    command = seqplatesolve_command(
        "frames", "localgaia", ra_deg=250.4197, dec_deg=36.44781, focal_length_mm=405.0, pixel_size_um=3.76
    )
    assert command == (
        "seqplatesolve frames 250.419700,36.447810 -catalog=localgaia -focal=405.000 "
        "-pixelsize=3.7600 -force -noflip"
    )


def test_every_frame_is_solved_in_one_run_with_the_first_frames_focal_length(
    tmp_path: Path, catalog: str, monkeypatch: pytest.MonkeyPatch, first_frame_solves: list[dict[str, Any]]
) -> None:
    """Verify one run solves the later frames and each gets its own header."""
    paths = [write_frame(tmp_path / f"f{index}.fits") for index in range(4)]
    before = [Path(path).read_bytes() for path in paths]
    fake = FakeSiril()
    fake.install(monkeypatch)

    solves = solve_frames(paths, "siril-cli", {}, catalog_path=catalog)

    assert list(solves) == paths
    assert all(solve.header is not None and solve.reason is None for solve in solves.values())
    assert first_frame_solves[0]["path"] == paths[0]
    assert len(fake.group_calls) == 1
    call = fake.group_calls[0]
    assert call["commands"] == [
        "requires 1.4.0",
        f"set core.catalogue_gaia_astro={catalog}",
        "link frames",
        "seqplatesolve frames 250.419700,36.447810 -catalog=localgaia -focal=404.000 "
        "-pixelsize=3.7600 -force -noflip",
        "close",
    ]
    assert call["files"] == ["frame_00001.fits", "frame_00002.fits", "frame_00003.fits"]
    # The library's frames are only linked, never copied or changed.
    assert all(call["were_links"])
    assert call["options"]["wait_for_slot"] is False
    assert call["options"]["offline"] is True
    assert [Path(path).read_bytes() for path in paths] == before


def test_a_frame_siril_cannot_solve_gets_a_reason_and_the_others_are_solved(
    tmp_path: Path, catalog: str, monkeypatch: pytest.MonkeyPatch, first_frame_solves: list[dict[str, Any]]
) -> None:
    """Verify an unsolved frame is named with a reason and not tried again."""
    paths = [write_frame(tmp_path / f"f{index}.fits") for index in range(4)]
    fake = FakeSiril(unsolved_numbers=(2,))
    fake.install(monkeypatch)

    solves = solve_frames(paths, "siril-cli", {}, catalog_path=catalog)

    assert solves[paths[2]].header is None
    assert "no solution" in (solves[paths[2]].reason or "")
    assert solves[paths[1]].header is not None
    assert solves[paths[3]].header is not None
    assert len(fake.group_calls) == 1


def test_a_long_session_is_solved_in_groups(
    tmp_path: Path, catalog: str, monkeypatch: pytest.MonkeyPatch, first_frame_solves: list[dict[str, Any]]
) -> None:
    """Verify the frames after the first go in groups of the set size."""
    monkeypatch.setattr(sequence_solve, "FRAMES_PER_GROUP", 2)
    paths = [write_frame(tmp_path / f"f{index}.fits") for index in range(6)]
    fake = FakeSiril()
    fake.install(monkeypatch)

    solves = solve_frames(paths, "siril-cli", {}, catalog_path=catalog)

    assert [len(call["files"]) for call in fake.group_calls] == [2, 2, 1]
    assert all(solve.header is not None for solve in solves.values())


def test_when_the_first_frame_cannot_be_solved_no_frame_is(
    tmp_path: Path, catalog: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify every frame is mapped through the first, so none is solved."""
    paths = [write_frame(tmp_path / f"f{index}.fits") for index in range(3)]
    monkeypatch.setattr(sequence_solve, "solve_image", lambda *args, **kwargs: None)
    fake = FakeSiril()
    fake.install(monkeypatch)

    solves = solve_frames(paths, "siril-cli", {}, catalog_path=catalog)

    assert all(solve.header is None for solve in solves.values())
    assert all("reference frame" in (solve.reason or "") for solve in solves.values())
    assert not fake.group_calls


def test_a_first_frame_with_no_start_position_gives_no_solutions(tmp_path: Path, catalog: str) -> None:
    """Verify a header with no position is reported, not solved."""
    path = tmp_path / "blind.fits"
    fits.PrimaryHDU(np.zeros((8, 8), dtype=np.float32), fits.Header({"XPIXSZ": 3.76})).writeto(path)

    solves = solve_frames([str(path)], "siril-cli", {}, catalog_path=catalog)

    assert solves[str(path)].header is None
    assert "no start position" in (solves[str(path)].reason or "")


def test_no_catalog_gives_no_solutions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a missing Gaia catalog is reported as the reason."""
    path = write_frame(tmp_path / "f0.fits")
    monkeypatch.setattr(sequence_solve, "find_local_gaia_catalog", lambda: None)

    solves = solve_frames([path], "siril-cli", {})

    assert solves[path].header is None
    assert "catalog" in (solves[path].reason or "")


def test_no_frames_give_no_solutions() -> None:
    """Verify an empty session gives an empty answer."""
    assert solve_frames([], "siril-cli", {}) == {}
