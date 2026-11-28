"""Purpose: Test the owner functions of the calibration sub-API.

Description: `match_calibration` groups light frames and runs the stacker's
own calibration selection for each group, so its counts equal what a stack
would use. `scan_calibration_frames` and `add_calibration_frames` rescan the
library and save its index. The libraries are small and real, written under a
temporary folder.
"""

import logging
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers.calibration_library import CalibrationLibrary
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.page import Page
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.stacking.calibration_catalog import match_calibration


def _library(tmp_path: Path) -> CalibrationLibrary:
    """Build an empty calibration library that saves inside `tmp_path`.

    Returns
    -------
    library : `CalibrationLibrary`
        A library with no frames.
    """
    app_config = SimpleNamespace(get_library_file_path=lambda name: str(tmp_path / name))
    return CalibrationLibrary(app_config=app_config)


def _calibration_frame(
    path: Path, exposure: float, offset: float = 30.0, flat_filter: str | None = None
) -> str:
    """Write a small calibration FITS frame at gain 100.

    Returns
    -------
    path : `str`
        The path written.
    """
    hdu = fits.PrimaryHDU(np.full((8, 8), 100.0, dtype=np.float32))
    hdu.header["INSTRUME"] = "Cam"
    hdu.header["EXPTIME"] = exposure
    hdu.header["GAIN"] = 100
    hdu.header["OFFSET"] = offset
    hdu.header["XBINNING"] = 1
    hdu.header["YBINNING"] = 1
    if flat_filter is not None:
        hdu.header["FILTER"] = flat_filter
    hdu.writeto(path)
    return str(path)


def _light_target(offset: str = "30") -> Target:
    """Make a target with two 30 s and one 10 s Luminance lights at gain 100.

    Returns
    -------
    target : `Target`
        The target.
    """
    kwargs = {"filter": "Luminance", "role": "LIGHT", "camera": "Cam", "gain": "100", "offset": offset}
    return Target(
        id="Grouped",
        frames=[
            FrameRecord(path="a.fits", exposure_s=30.0, **kwargs),
            FrameRecord(path="b.fits", exposure_s=30.0, **kwargs),
            FrameRecord(path="c.fits", exposure_s=10.0, **kwargs),
        ],
    )


def test_match_report_what_the_stacker_would_select(tmp_path: Path) -> None:
    """Each group's match equals the selection the stacker runs."""
    library = _library(tmp_path)
    library.add_dark_frame(_calibration_frame(tmp_path / "dark.fits", 30.0))
    library.add_bias_frame(_calibration_frame(tmp_path / "bias.fits", 0.0))
    library.add_flat_frame(_calibration_frame(tmp_path / "flat.fits", 1.0, flat_filter="Luminance"))
    target = _light_target()

    groups = match_calibration(library, target.frames)

    by_exposure = {group.exposure_s: group for group in groups}
    assert by_exposure[30.0].frame_count == 2
    assert by_exposure[30.0].dark.count == 1
    assert by_exposure[10.0].dark.count == 0
    assert by_exposure[10.0].dark.problem
    direct_bias = library.select_bias_frames(camera="Cam", iso="100", offset="30", binning="1x1")
    direct_flat = library.select_flat_frames(
        telescope="Unknown",
        camera="Cam",
        filter_type=target.frames[0].filter,
        iso="100",
        offset="30",
        binning="1x1",
    )
    assert by_exposure[30.0].bias.count == len(direct_bias.frames) == 1
    assert by_exposure[30.0].flat.count == len(direct_flat.frames) == 1
    assert by_exposure[30.0].bias.problem == ""
    assert by_exposure[30.0].bias.fallbacks == []


def test_a_bias_at_the_wrong_offset_is_a_fallback_with_the_reason(tmp_path: Path) -> None:
    """A bias at another offset is a fallback, with a note saying so."""
    library = _library(tmp_path)
    library.add_bias_frame(_calibration_frame(tmp_path / "bias.fits", 0.0, offset=10.0))
    groups = match_calibration(library, _light_target(offset="30").frames)

    bias = groups[0].bias
    assert bias.count == 1
    assert bias.fallbacks
    assert groups[0].flat.count == 0
    assert "no flat frames" in groups[0].flat.problem


def test_match_name_the_lights_and_do_not_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The grouping query returns its fallbacks without warning about them.

    The query reads no light files, so the fallback sentence names the lights
    by their frame records, never as "the 0 light frame(s)".
    """
    library = _library(tmp_path)
    library.add_bias_frame(_calibration_frame(tmp_path / "bias.fits", 0.0, offset=10.0))
    with caplog.at_level(logging.DEBUG):
        groups = match_calibration(library, _light_target(offset="30").frames)

    by_exposure = {group.exposure_s: group for group in groups}
    fallbacks = by_exposure[30.0].bias.fallbacks
    assert fallbacks
    assert "the 2 light frame(s) starting with a.fits" in fallbacks[0]
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_match_split_by_optic_and_report_it(tmp_path: Path) -> None:
    """One camera through two optics gives two groups, one per optic."""
    library = _library(tmp_path)
    kwargs = {
        "filter": "Luminance",
        "role": "LIGHT",
        "camera": "Cam",
        "gain": "100",
        "offset": "10",
        "exposure_s": "30.0",
    }
    target = Target(
        id="TwoOptics",
        frames=[
            FrameRecord(path="a.fits", focalLengthMm=405.2, **kwargs),
            FrameRecord(path="b.fits", focalLengthMm=300.0, **kwargs),
        ],
    )

    groups = match_calibration(library, target.frames)

    assert sorted(group.focal_length_mm for group in groups) == [300, 405]


def test_match_skips_frames_that_are_not_lights(tmp_path: Path) -> None:
    """A dark among the frames is not a group of its own."""
    target = _light_target()
    frames = [*target.frames, FrameRecord(path="d.fits", exposure_s=30.0, role="DARK", camera="Cam")]

    groups = match_calibration(_library(tmp_path), frames)

    assert sum(group.frame_count for group in groups) == 3


def test_match_refuses_a_page_cut_short_and_paths(tmp_path: Path) -> None:
    """Only the whole choice, as records, can be matched."""
    frames = _light_target().frames
    with pytest.raises(InvalidArgumentError, match="cut short"):
        match_calibration(_library(tmp_path), Page.of(frames, limit=1))
    with pytest.raises(InvalidArgumentError, match="target"):
        match_calibration(_library(tmp_path), ["a.fits"])
