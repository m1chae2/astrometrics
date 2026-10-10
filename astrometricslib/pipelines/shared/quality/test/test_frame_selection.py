"""Tests for choosing frames by filter, file range and time.

A quality check must measure the frames the observer asked about. These
tests check the file-range rule (numbers and full names), the filter match,
the spectroscopy default, the time bounds and the folder reader.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.quality.frame_selection import (
    FrameSelection,
    file_in_range,
    parse_iso_time,
    select_folder_paths,
    select_library_frames,
)


def test_a_bare_number_selects_by_the_number_at_the_end_of_the_name() -> None:
    """Frames 013 to 025 are kept whatever the prefix; 100 is not below 25."""
    assert file_in_range("M_57_Light_013.fits", "013", "025")
    assert not file_in_range("M_57_Light_026.fits", "013", "025")
    assert not file_in_range("M_57_Light_012.fits", "013", "025")
    assert file_in_range("M_57_Light_100.fits", "013", None)


def test_a_full_name_bound_is_compared_as_text() -> None:
    """A bound that is a whole file name works as an ordinary text range."""
    assert file_in_range("b.fits", "a.fits", "c.fits")
    assert not file_in_range("d.fits", "a.fits", "c.fits")


def test_parse_iso_time_reads_offsets_and_assumes_utc() -> None:
    """An offset is honoured and a missing offset means UTC."""
    assert parse_iso_time("2026-10-03T00:00:00") == parse_iso_time("2026-10-03T00:00:00Z")
    assert parse_iso_time("2026-10-03T00:00:00-06:00") == parse_iso_time("2026-10-03T06:00:00Z")
    assert parse_iso_time(None) is None
    with pytest.raises(ValueError):
        parse_iso_time("last night")


def _frames() -> list[FrameRecord]:
    """Build light frames of two filters and one spectroscopy frame.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        Frames numbered by time, 0 to 5, filters L, L, R, L, SPEC, L.
    """
    filters = ["Luminance", "Luminance", "R", "Luminance", "Star Analyzer 200", "Luminance"]
    return [
        FrameRecord(
            path=f"/lights/M_57_{number:03d}.fits",
            role="LIGHT",
            filter=name,
            timestamp=1000.0 + number,
        )
        for number, name in enumerate(filters)
    ]


def test_spectroscopy_is_left_out_unless_asked_for() -> None:
    """Spectroscopy is dropped by default; including it brings it back."""
    kept = select_library_frames(_frames(), FrameSelection())
    assert len(kept) == 5
    assert len(select_library_frames(_frames(), FrameSelection(include_spectra=True))) == 6


def test_filter_range_and_time_narrow_together() -> None:
    """The filter, the number range and the time bound all apply."""
    only_l = select_library_frames(_frames(), FrameSelection(filter_name="L"))
    assert [frame.timestamp for frame in only_l] == [1000.0, 1001.0, 1003.0, 1005.0]
    ranged = select_library_frames(_frames(), FrameSelection(filter_name="L", first_file="1", last_file="3"))
    assert [frame.timestamp for frame in ranged] == [1001.0, 1003.0]
    timed = select_library_frames(_frames(), FrameSelection(since=1003.0, until=1005.0))
    assert [frame.timestamp for frame in timed] == [1003.0, 1005.0]


def test_the_folder_reader_uses_header_filter_and_time(tmp_path: Path) -> None:
    """A folder frame is kept or dropped by its FILTER and DATE-OBS."""
    for number, (name, observed) in enumerate([
        ("L", "2026-10-03T03:00:00"),
        ("SA200", "2026-10-03T03:10:00"),
        ("L", "2026-10-03T04:00:00"),
    ]):
        header = fits.Header()
        header["FILTER"] = name
        header["DATE-OBS"] = observed
        fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32), header).writeto(
            tmp_path / f"frame_{number:03d}.fits"
        )
    everything = select_folder_paths(str(tmp_path), FrameSelection(include_spectra=True))
    assert len(everything) == 3
    lights = select_folder_paths(str(tmp_path), FrameSelection(filter_name="L"))
    assert [path[-8:-5] for path in lights] == ["000", "002"]
    late = select_folder_paths(
        str(tmp_path), FrameSelection(filter_name="L", since=parse_iso_time("2026-10-03T03:30:00"))
    )
    assert [path[-8:-5] for path in late] == ["002"]
