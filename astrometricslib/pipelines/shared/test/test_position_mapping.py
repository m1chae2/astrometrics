"""Tests for `position_mapping`, the library's one rule for following a pixel.

A frame pixel goes onto its stack (with the crop offset) and back. A frame
Siril left out of the stack, and a spectral stack, cannot be followed and say
why. `QualityRecords.map_position` checks that both files exist and gives
1-based answers.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.api.quality import QualityRecords
from astrometricslib.foundation.errors import NotFoundError, ProcessingError
from astrometricslib.pipelines.shared.position_mapping import (
    RecordCache,
    StackRecord,
    frame_to_stack,
    map_position,
    stack_to_frame,
)


def _write_image(path: Path, shape: tuple[int, int] = (40, 60)) -> None:
    """Write a small FITS image of zeros."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fits.PrimaryHDU(np.zeros(shape, dtype=np.float32)).writeto(path, overwrite=True)


def _write_record(
    stack: Path, frames: list[Path], shift: tuple[float, float], crop: tuple[int, int], **extra: object
) -> None:
    """Write a registration record: every frame moves by ``shift`` (y up)."""
    document = {
        "version": 1,
        "light_paths": [str(frame) for frame in frames],
        "homographies": [[1, 0, shift[0], 0, 1, shift[1], 0, 0, 1] for _ in frames],
        "included": [True] * len(frames),
        "crop_offset": list(crop),
        "reference_frame": str(frames[0]),
        "is_spectral": False,
        **extra,
    }
    stack.with_name(stack.stem + "_registration_frames.json").write_text(
        json.dumps(document), encoding="utf-8"
    )


def _one_frame_record(tmp_path: Path, **extra: object) -> StackRecord:
    """Write and read back a record of one frame shifted by (5, 3).

    Returns
    -------
    record : `StackRecord`
        The record.
    """
    stack = tmp_path / "s.fits"
    _write_image(stack)
    _write_record(stack, [tmp_path / "f.fits"], (5.0, 3.0), (2, 1), **extra)
    record = RecordCache().record_of(str(stack))
    assert record is not None
    return record


def test_homography_round_trip_and_crop_offset(tmp_path: Path) -> None:
    """A frame pixel goes onto the stack and back, with the crop applied."""
    record = _one_frame_record(tmp_path)
    # Frame 40 rows high: pixel (10, 20), 1-based, is column 9, row 19.
    # Siril y = 40 - 1 - 19 = 20. It moves to column 14 and Siril y 23, so
    # row 40 - 1 - 23 = 16; minus the crop (2, 1): column 12, row 15.
    stack_x, stack_y = frame_to_stack(record, 0, 10.0, 20.0, 40)
    assert (stack_x, stack_y) == pytest.approx((13.0, 16.0))
    assert stack_to_frame(record, 0, stack_x, stack_y, 40) == pytest.approx((10.0, 20.0))


def test_a_spectral_stack_cannot_be_followed(tmp_path: Path) -> None:
    """A spectral stack's shifts are not image transforms: refused."""
    record = _one_frame_record(tmp_path, is_spectral=True)
    with pytest.raises(ProcessingError, match="spectral"):
        frame_to_stack(record, 0, 10.0, 20.0, 40)


def test_a_frame_left_out_gives_a_none_answer_with_the_reason(tmp_path: Path) -> None:
    """A pixel `map_position` cannot follow gives ``"none"``, no error."""
    frame = tmp_path / "lights" / "f.fits"
    stack = tmp_path / "stacks" / "S.fits"
    _write_image(frame)
    _write_image(stack)
    _write_record(stack, [frame], (0.0, 0.0), (0, 0))
    record_file = stack.with_name("S_registration_frames.json")
    document = json.loads(record_file.read_text())
    document["included"] = [False]
    record_file.write_text(json.dumps(document))

    mapping = map_position(str(frame), 5.0, 5.0, str(stack), [str(tmp_path / "stacks")])

    assert (mapping.x, mapping.y, mapping.method) == (None, None, "none")
    assert "register" in mapping.note


def test_the_quality_method_checks_the_files_and_searches_the_stacks_folders(tmp_path: Path) -> None:
    """The quality method refuses a missing file and finds the record."""
    config = MagicMock()
    config.get_library_path.return_value = tmp_path
    config.get_stacks_path.return_value = tmp_path / "stacks"
    quality = QualityRecords(config, MagicMock())
    frame = tmp_path / "lights" / "f.fits"
    stack = tmp_path / "stacks" / "t" / "T_Stacked.fits"
    _write_image(frame)
    _write_image(stack, shape=(38, 56))
    _write_record(stack, [frame], (5.0, 3.0), (2, 1))

    with pytest.raises(NotFoundError):
        quality.map_position(str(tmp_path / "none.fits"), 1.0, 1.0, str(stack))
    mapping = quality.map_position(str(frame), 10.0, 20.0, str(stack))

    assert mapping.method == "stack alignment"
    assert (mapping.x, mapping.y) == pytest.approx((13.0, 16.0))
    assert type(mapping.x) is float


@pytest.mark.parametrize(
    ("degrees", "saved_shape", "expected"),
    [
        # Registered stack: 38 rows, 56 columns. The frame pixel lands on
        # column 12, row 15 there (counted from 0; see the round-trip test).
        (0, (38, 56), (13.0, 16.0)),
        # A half turn sends (row 15, column 12) to (38 - 1 - 15, 56 - 1 - 12).
        (180, (38, 56), (44.0, 23.0)),
        # A counterclockwise quarter turn sends (row, column) to
        # (56 - 1 - 12, 15), on a stack 56 rows high and 38 columns wide.
        (90, (56, 38), (16.0, 44.0)),
    ],
)
def test_a_turned_stack_is_followed_on_its_saved_pixels(
    tmp_path: Path, degrees: int, saved_shape: tuple[int, int], expected: tuple[float, float]
) -> None:
    """The quarter turn in the record moves the pixel onto the saved stack."""
    frame = tmp_path / "lights" / "f.fits"
    stack = tmp_path / "stacks" / "t" / "T_Stacked.fits"
    _write_image(frame)
    _write_image(stack, shape=saved_shape)
    _write_record(stack, [frame], (5.0, 3.0), (2, 1), quarter_turn_degrees=degrees)
    roots = [str(tmp_path / "stacks")]

    to_stack = map_position(str(frame), 10.0, 20.0, str(stack), roots)
    back = map_position(str(stack), to_stack.x, to_stack.y, str(frame), roots)

    assert to_stack.method == "stack alignment"
    assert (to_stack.x, to_stack.y) == pytest.approx(expected)
    assert (back.x, back.y) == pytest.approx((10.0, 20.0))


def test_a_cropped_picture_of_a_turned_stack_is_followed(tmp_path: Path) -> None:
    """The picture's crop cards count on the saved (turned) stack."""
    frame = tmp_path / "lights" / "f.fits"
    stack = tmp_path / "stacks" / "t" / "T_Stacked.fits"
    picture = stack.with_name("T_Stacked_processed.fits")
    _write_image(frame)
    _write_image(stack, shape=(38, 56))
    picture.parent.mkdir(parents=True, exist_ok=True)
    header = fits.Header({"CROPX0": 6, "CROPY0": 4, "CROPW": 44, "CROPH": 30})
    fits.PrimaryHDU(np.zeros((30, 44), dtype=np.float32), header=header).writeto(picture)
    _write_record(stack, [frame], (5.0, 3.0), (2, 1), quarter_turn_degrees=180)
    roots = [str(tmp_path / "stacks")]

    to_picture = map_position(str(frame), 10.0, 20.0, str(picture), roots)
    back = map_position(str(picture), to_picture.x, to_picture.y, str(frame), roots)

    assert (to_picture.x, to_picture.y) == pytest.approx((44.0 - 6.0, 23.0 - 4.0))
    assert (back.x, back.y) == pytest.approx((10.0, 20.0))
