"""Tests for the listing of frames in a target's `_excluded` folders.

Two things are checked:

1. A set-aside frame shows the same camera, filter, capture time and
   exposure that the target's own frame record shows for the same file.
2. The listing keeps what it read from a folder's files. A second listing of
   an unchanged folder opens no file, and adding, removing or touching a
   file makes the next listing read the folder again.
"""

import json
import os
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from pytest_mock import MockerFixture

from astrometricslib.pipelines.shared.frame_scanning import create_frame_record_from_fits
from astrometricslib.pipelines.stacking.pre_processing import set_aside_listing
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import MANIFEST_FILE_NAME
from astrometricslib.pipelines.stacking.pre_processing.set_aside_listing import (
    build_set_aside_records,
    clear_set_aside_cache,
)


@pytest.fixture(autouse=True)
def _empty_cache() -> Iterator[None]:
    """Start and end every test with no kept folder snapshots.

    Yields
    ------
    None
        Control passes to the test between the two clears.
    """
    clear_set_aside_cache()
    yield
    clear_set_aside_cache()


def _write_frame(path: Path, *, two_hdus: bool = False, **cards: object) -> None:
    """Write a small made-up light frame with the given header cards.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write the file.
    two_hdus : `bool`, optional
        If True, write a bare HDU 0 and put the image and cards in HDU 1.
    **cards : `object`
        Header cards to set on the HDU that holds the image.
    """
    image = np.zeros((8, 12), dtype=np.uint16)
    if two_hdus:
        hdu = fits.ImageHDU(image)
        hdu_list = fits.HDUList([fits.PrimaryHDU(), hdu])
    else:
        hdu = fits.PrimaryHDU(image)
        hdu_list = fits.HDUList([hdu])
    for name, value in cards.items():
        hdu.header[name.replace("_", "-")] = value
    hdu_list.writeto(path)


def _excluded_folder(tmp_path: Path) -> Path:
    """Make an empty `_excluded` folder.

    Returns
    -------
    folder : `pathlib.Path`
        The folder, created.
    """
    folder = tmp_path / "lights" / "M 1" / "Scope" / "Camera" / "_excluded"
    folder.mkdir(parents=True)
    return folder


def test_set_aside_frames_show_the_same_values_as_frame_records(tmp_path: Path) -> None:
    """Verify camera, filter, time and exposure match the frame record's.

    The frames cover a plain file, a file with the image in HDU 1, a file
    with no capture time in its header but one in its name, a file that
    names no camera, and a file that is not a FITS file at all.
    """
    folder = _excluded_folder(tmp_path)
    _write_frame(
        folder / "a.fits",
        INSTRUME="ZWO ASI533MM Pro",
        FILTER="Ha",
        DATE_OBS="2026-10-03T04:05:06",
        EXPTIME=120.0,
    )
    _write_frame(
        folder / "b.fits",
        two_hdus=True,
        INSTRUME="Nikon D5300",
        FILTER="Luminance",
        DATE_OBS="2026-10-04T01:02:03.5",
        EXPTIME=30,
    )
    _write_frame(folder / "M_1_Light_2026-09-24T21-14-05.fits", INSTRUME="ZWO ASI533MM Pro", EXPTIME=60.0)
    _write_frame(folder / "d.fits", FILTER="OIII", DATE_OBS="2026-10-05T00:00:00")
    (folder / "e.fits").write_bytes(b"not a FITS file")

    records = build_set_aside_records("M 1", [str(folder)], [], {})

    assert [record.file for record in records] == [
        "M_1_Light_2026-09-24T21-14-05.fits",
        "a.fits",
        "b.fits",
        "d.fits",
        "e.fits",
    ]
    for record in records:
        frame = create_frame_record_from_fits(record.path)
        expected_camera = "" if frame.camera == "Unknown" else frame.camera
        expected_filter = "None" if frame.filter.name == "NONE" else frame.filter.name
        assert record.camera == expected_camera, record.file
        assert record.filter == expected_filter, record.file
        assert record.date == frame.date, record.file
    exposures = {record.file: record.exposure_s for record in records}
    assert exposures == {
        "M_1_Light_2026-09-24T21-14-05.fits": 60.0,
        "a.fits": 120.0,
        "b.fits": 30.0,
        "d.fits": None,
        "e.fits": None,
    }
    assert records[0].date == "2026-09-24 21:14:05"


def test_an_unchanged_folder_is_not_read_again(tmp_path: Path, mocker: MockerFixture) -> None:
    """Verify a second listing of an unchanged folder opens no file."""
    folder = _excluded_folder(tmp_path)
    for name in ("a.fits", "b.fits"):
        _write_frame(folder / name, INSTRUME="Camera", EXPTIME=10.0)
    (folder / MANIFEST_FILE_NAME).write_text(json.dumps([]))
    fits_opens = mocker.spy(fits, "open")
    manifest_reads = mocker.spy(set_aside_listing, "read_quarantine_manifest")

    first = build_set_aside_records("M 1", [str(folder)], [], {})
    assert fits_opens.call_count == 2
    assert manifest_reads.call_count == 1

    second = build_set_aside_records("M 1", [str(folder)], [], {})
    assert fits_opens.call_count == 2
    assert manifest_reads.call_count == 1
    assert second == first


def test_adding_removing_or_touching_a_file_reads_the_folder_again(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """Verify any change to the folder's files makes the listing read again."""
    folder = _excluded_folder(tmp_path)
    _write_frame(folder / "a.fits", EXPTIME=10.0)
    fits_opens = mocker.spy(fits, "open")

    assert [record.file for record in build_set_aside_records("M 1", [str(folder)], [], {})] == ["a.fits"]
    opens_after_first = fits_opens.call_count

    _write_frame(folder / "b.fits", EXPTIME=20.0)
    added = build_set_aside_records("M 1", [str(folder)], [], {})
    assert [record.file for record in added] == ["a.fits", "b.fits"]
    assert fits_opens.call_count > opens_after_first

    opens_before_touch = fits_opens.call_count
    status = (folder / "a.fits").stat()
    os.utime(folder / "a.fits", ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000_000))
    build_set_aside_records("M 1", [str(folder)], [], {})
    assert fits_opens.call_count > opens_before_touch

    (folder / "b.fits").unlink()
    removed = build_set_aside_records("M 1", [str(folder)], [], {})
    assert [record.file for record in removed] == ["a.fits"]
