"""Purpose: Tests for recording the pointing error of plate-solved frames.

Description: After a frame sync, each new frame whose header holds both
where the mount was sent and a plate solve gives one alignment attempt.
The tests write small FITS files and check the offset is a distance on
the sky (scaled by cos(dec), wrapped at 0h/24h), sexagesimal and numeric
header positions are read, frames without both positions are skipped,
and a sync step records the new frames it sorted.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from wayfindinglib.tasks.control_tasks import frame_pointing

WHEN = {"DATE-OBS": "2026-09-24T22:00:00"}
"""An observation time, so no test needs a file on disk for the time."""


def _frame(path: Path, **header: object) -> str:
    """Write a tiny FITS frame with the given header cards.

    Returns
    -------
    path : `str`
        The written file.
    """
    hdu = fits.PrimaryHDU(np.zeros((4, 4), dtype=np.uint16))
    for key, value in header.items():
        hdu.header[key.replace("_", "-")] = value
    hdu.writeto(path)
    return str(path)


def test_the_offset_is_a_sky_distance_from_commanded_to_solved() -> None:
    """0.01 degree of RA at dec 20 is 36 cos(20) arcseconds."""
    header = {"CRVAL1": 100.01, "CRVAL2": 20.02, "OBJCTRA": "06 40 00", "OBJCTDEC": "+20 00 00", **WHEN}
    attempt = frame_pointing.pointing_error_from_header(header, "frame.fits")

    assert attempt["delta_ra_arcsec"] == pytest.approx(36.0 * np.cos(np.radians(20.0)), abs=0.01)
    assert attempt["delta_dec_arcsec"] == pytest.approx(72.0, abs=0.01)
    assert attempt["status"] == "aligned"


def test_the_offset_wraps_across_zero_hours() -> None:
    """A solve just past 0h from a mount just before it is a small offset."""
    header = {"CRVAL1": 0.005, "CRVAL2": 0.0, "RA": 359.995, "DEC": 0.0, **WHEN}
    attempt = frame_pointing.pointing_error_from_header(header, "frame.fits")

    assert attempt["delta_ra_arcsec"] == pytest.approx(36.0, abs=0.01)


def test_a_large_error_is_a_warning() -> None:
    """More than two arcminutes off is not aligned."""
    header = {"CRVAL1": 10.1, "CRVAL2": 20.0, "RA": 10.0, "DEC": 20.0, **WHEN}
    assert frame_pointing.pointing_error_from_header(header, "frame.fits")["status"] == "warning"


@pytest.mark.parametrize(
    "header",
    [
        {"RA": 10.0, "DEC": 20.0},
        {"CRVAL1": 10.0, "CRVAL2": 20.0},
        {"CRVAL1": 1.0, "CRVAL2": 2.0, "RA": "nonsense", "DEC": 2.0},
    ],
)
def test_frames_without_both_positions_are_skipped(header: dict) -> None:
    """No solve, no commanded position, or an unreadable one gives nothing."""
    assert frame_pointing.pointing_error_from_header(header, "frame.fits") is None


def test_only_plate_solved_frames_are_recorded(tmp_path: Path) -> None:
    """Each solved frame is one attempt at its DATE-OBS time; others skip."""
    solved = _frame(
        tmp_path / "solved.fits",
        CRVAL1=150.0,
        CRVAL2=30.0,
        RA=150.0,
        DEC=30.0,
        OBJECT="M 81",
        DATE_OBS="2026-09-24T22:00:00",
    )
    unsolved = _frame(tmp_path / "unsolved.fits", RA=150.0, DEC=30.0)
    context = SimpleNamespace(records=MagicMock())

    recorded = frame_pointing.record_frame_pointing_errors(
        context, [solved, unsolved, str(tmp_path / "notes.txt")]
    )

    assert recorded == 1
    (attempt,) = context.records.record_alignment_attempt.call_args.args
    assert attempt["target_name"] == "M 81"
    assert attempt["timestamp"] == pytest.approx(1790287200.0)
