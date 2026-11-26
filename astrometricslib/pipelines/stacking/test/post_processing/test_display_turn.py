"""Purpose: Tests of the quarter turn that shows a raw frame like its stack.

Description: NGC 7331's frames were taken on both sides of a meridian flip.
Its stack's top points 344 degrees from north toward east and matches the
frames from the east side of the pier. These tests check the turn worked out
from a frame's ``CROTA2`` and row order, the rounding to a quarter turn, the
fallback to the side of the pier, the cases that give no turn, and the
target API that reads both headers.
"""

import math
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.api.targets import TargetCatalog
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.stacking.post_processing.display_turn import (
    display_turn_degrees,
    frame_north_angle_degrees,
    nearest_quarter_turn,
)

STACK_UP_DEGREES = 344.0


def stack_header(pier_side: str | None = "EAST") -> fits.Header:
    """Build a solved stack header whose top points 344 degrees east of north.

    Returns
    -------
    header : `astropy.io.fits.Header`
        The header, drawn with row 1 at the bottom.
    """
    scale = 1.0 / 3600.0
    angle = math.radians(STACK_UP_DEGREES)
    header = fits.Header()
    header["NAXIS"] = 2
    header["NAXIS1"] = 100
    header["NAXIS2"] = 80
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CRVAL1"] = 339.3
    header["CRVAL2"] = 34.4
    header["CRPIX1"] = 50.0
    header["CRPIX2"] = 40.0
    header["CD1_1"] = -scale * math.cos(angle)
    header["CD1_2"] = scale * math.sin(angle)
    header["CD2_1"] = scale * math.sin(angle)
    header["CD2_2"] = scale * math.cos(angle)
    header["PLTSOLVD"] = True
    if pier_side:
        header["PIERSIDE"] = pier_side
    return header


FRAME_001 = {"ROWORDER": "TOP-DOWN", "CROTA2": 16.2, "PIERSIDE": "WEST"}
"""A raw NGC 7331 frame from the west side of the pier (frames 001 to 045)."""

FRAME_080 = {"ROWORDER": "TOP-DOWN", "CROTA2": 196.1, "PIERSIDE": "EAST"}
"""A raw NGC 7331 frame from the east side of the pier (frames 046 to 135)."""


def test_the_west_side_frame_is_turned_half_a_turn_and_the_east_side_frame_is_not() -> None:
    """Frame 001 is half a turn from the stack; frame 080 already matches."""
    assert display_turn_degrees(FRAME_001, stack_header()) == 180
    assert display_turn_degrees(FRAME_080, stack_header()) == 0


def test_the_north_angle_reads_crota2_with_the_row_order() -> None:
    """A top-down file measures its angle from the other end of the axis."""
    assert frame_north_angle_degrees(FRAME_080) == pytest.approx(343.9)
    assert frame_north_angle_degrees({"CROTA2": 30}) == pytest.approx(30.0)
    assert frame_north_angle_degrees({"ROWORDER": "TOP-DOWN"}) is None


def test_the_turn_is_rounded_to_the_nearest_quarter() -> None:
    """A rotation a quarter of a circle away gives a quarter turn."""
    assert display_turn_degrees({"ROWORDER": "TOP-DOWN", "CROTA2": 196.1 - 90}, stack_header()) == 270
    assert display_turn_degrees({"ROWORDER": "TOP-DOWN", "CROTA2": 196.1 + 90}, stack_header()) == 90
    assert nearest_quarter_turn(-44) == 0
    assert nearest_quarter_turn(-46) == 270
    assert nearest_quarter_turn(360 + 179.7) == 180
    assert nearest_quarter_turn(45) == 90


def test_without_crota2_the_side_of_the_pier_decides() -> None:
    """A frame from the other side of the pier is turned half a turn."""
    assert display_turn_degrees({"ROWORDER": "TOP-DOWN", "PIERSIDE": "WEST"}, stack_header()) == 180
    assert display_turn_degrees({"ROWORDER": "TOP-DOWN", "PIERSIDE": "EAST"}, stack_header()) == 0
    assert display_turn_degrees({"ROWORDER": "TOP-DOWN"}, stack_header()) == 0
    assert display_turn_degrees({"PIERSIDE": "WEST"}, stack_header(pier_side=None)) == 0


def test_no_turn_without_a_solved_stack_for_a_solved_frame_or_a_turned_frame() -> None:
    """No solved stack, a solved frame or a frame already turned: no turn."""
    unsolved = stack_header()
    del unsolved["PLTSOLVD"]
    solved_frame = {**dict(stack_header()), **FRAME_001}
    assert display_turn_degrees(FRAME_001, None) == 0
    assert display_turn_degrees(None, stack_header()) == 0
    assert display_turn_degrees(FRAME_001, unsolved) == 0
    assert display_turn_degrees(solved_frame, stack_header()) == 0
    assert display_turn_degrees({**FRAME_001, "QTURN": 180}, stack_header()) == 0


def test_the_target_api_reads_the_frame_and_the_stack(tmp_path: Path) -> None:
    """The catalog reads both headers; a file of another target is refused."""
    stack = tmp_path / "NGC_7331_Stacked.fits"
    fits.PrimaryHDU(np.zeros((80, 100), dtype=np.float32), header=stack_header()).writeto(stack)
    frame = tmp_path / "NGC_7331_001.fits"
    frame_header = fits.Header()
    for key, value in FRAME_001.items():
        frame_header[key] = value
    fits.PrimaryHDU(np.zeros((80, 100), dtype=np.float32), header=frame_header).writeto(frame)
    target = Target(
        id="NGC 7331", frames=[FrameRecord(path=str(frame)), FrameRecord(path=str(tmp_path / "gone.fits"))]
    )
    target.stacking.stacked_image = str(stack)
    catalog = TargetCatalog.__new__(TargetCatalog)

    assert catalog.display_turn(str(frame), target) == 180
    assert catalog.display_turn(str(stack), target) == 0
    assert catalog.display_turn(str(tmp_path / "gone.fits"), target) == 0
    with pytest.raises(InvalidArgumentError):
        catalog.display_turn("/elsewhere.fits", target)
