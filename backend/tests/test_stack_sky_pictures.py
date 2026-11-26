"""Purpose: Unit tests for the cache of stack pictures placed on the sky.

Description: The library names the files and places the picture; the
backend keeps each answer until the stretched stack or the picture changes.
These tests use a stand-in for the library and check that a target without
files is left out, that a stack is placed only once until its file changes,
and that the entry carries the target's id.
"""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from backend.services.data.stack_sky_pictures import StackSkyPictures


class FakeVisualization:
    """Stand in for the library's visualization API and count placements.

    Parameters
    ----------
    processed, picture : `str`
        The files the library would name.
    placed : `bool`
        Whether the stack has a plate solution.
    """

    def __init__(self, processed: str, picture: str, placed: bool = True) -> None:
        """Remember the files and start counting."""
        self.files = (processed, picture)
        self.placed = placed
        self.calls: list[str] = []

    def stack_sky_picture_files(self, target: Any) -> tuple[str, str]:
        """Name the files.

        Returns
        -------
        files : `tuple` [`str`, `str`]
            The stretched stack and the picture.
        """
        return self.files

    def place_stack_picture_on_sky(self, processed: str, picture: str) -> Any:
        """Count the call and place the picture, or not.

        Returns
        -------
        placement : `SimpleNamespace` or `None`
            A stand-in placement, or `None` with no plate solution.
        """
        self.calls.append(processed)
        if not self.placed:
            return None
        return SimpleNamespace(
            model_dump=lambda by_alias: {
                "picturePath": picture,
                "width": 4,
                "height": 3,
                "gridSize": 2,
                "grid": [],
            }
        )


def library(visualization: FakeVisualization) -> SimpleNamespace:
    """Wrap a stand-in visualization API as the library.

    Returns
    -------
    astrometrics : `SimpleNamespace`
        An object whose ``visualization`` is the stand-in.
    """
    return SimpleNamespace(visualization=visualization)


def test_a_target_without_files_or_solution_is_left_out(tmp_path: Path) -> None:
    """No files, a missing picture, or an unsolved stack each give no entry."""
    processed = tmp_path / "M_1_Stacked_processed.fits"
    processed.write_bytes(b"fits")
    picture = tmp_path / "M_1_Stacked_preview.jpg"
    target = SimpleNamespace(id="M 1")

    assert StackSkyPictures(library(FakeVisualization("", "")), lambda path: path).entry_for(target) is None
    missing_picture = FakeVisualization(str(processed), str(picture))
    assert StackSkyPictures(library(missing_picture), lambda path: path).entry_for(target) is None
    assert missing_picture.calls == []
    picture.write_bytes(b"jpeg")
    unsolved = FakeVisualization(str(processed), str(picture), placed=False)
    assert StackSkyPictures(library(unsolved), lambda path: path).entry_for(target) is None


def test_the_answer_is_kept_until_a_file_changes(tmp_path: Path) -> None:
    """A second ask places nothing; a newer stack is placed again."""
    processed = tmp_path / "M_31_Stacked_processed.fits"
    processed.write_bytes(b"fits")
    picture = tmp_path / "M_31_Stacked_color_preview.jpg"
    picture.write_bytes(b"jpeg")
    visualization = FakeVisualization(str(processed), str(picture))
    pictures = StackSkyPictures(library(visualization), lambda path: path)
    target = SimpleNamespace(id="M 31")

    first = pictures.entry_for(target)
    second = pictures.entry_for(target)

    assert first is not None
    assert first["targetId"] == "M 31"
    assert first["picturePath"] == str(picture)
    assert second == first
    assert visualization.calls == [str(processed)]
    os.utime(processed, (os.path.getmtime(processed) + 10, os.path.getmtime(processed) + 10))
    pictures.entry_for(target)
    assert len(visualization.calls) == 2
