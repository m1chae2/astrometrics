"""Tests for the calibration inventory models.

Checks that a row from `CalibrationLibrary.get_stats` fits `CalibrationEntry`
with its binning and dark temperature kept, and that `FlatSetAssessment` keeps
the binning of the flats it describes.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.drivers.calibration_library import CalibrationLibrary
from astrometricslib.models.calibration_ingest import FlatSetAssessment
from astrometricslib.models.calibration_inventory import CalibrationEntry, CalibrationStats


def make_dark(path: Path) -> str:
    """Write a small 2x2-binned dark frame cooled to -10 C.

    Parameters
    ----------
    path : `Path`
        Where to write the FITS file.

    Returns
    -------
    path : `str`
        The file path as text.
    """
    header = fits.Header()
    header["IMAGETYP"] = "Dark"
    header["INSTRUME"] = "ZWO CCD ASI533MM Pro"
    header["EXPTIME"] = 30.0
    header["GAIN"] = 100
    header["OFFSET"] = 30
    header["CCD-TEMP"] = -10.0
    header["XBINNING"] = 2
    header["YBINNING"] = 2
    fits.PrimaryHDU(np.zeros((8, 8), dtype=np.float32), header=header).writeto(path)
    return str(path)


def test_a_stats_row_keeps_binning_and_temperature(tmp_path: Path) -> None:
    """A dark's stats row goes through the model with both new fields."""
    app_config = SimpleNamespace(get_library_file_path=lambda name: str(tmp_path / name))
    library = CalibrationLibrary(app_config=app_config)
    library.add_dark_frame(make_dark(tmp_path / "dark.fits"))

    stats = CalibrationStats.model_validate(library.get_stats())
    entry = stats.darks[0]

    assert entry.binning == "2x2"
    assert entry.temperature_c == pytest.approx(-9.0)
    dumped = entry.model_dump(by_alias=True)
    assert dumped["binning"] == "2x2"
    assert dumped["temperatureC"] == pytest.approx(-9.0)
    assert CalibrationEntry.model_validate(dumped).temperature_c == pytest.approx(-9.0)


def test_an_entry_without_binning_or_temperature_still_loads() -> None:
    """Rows saved before the fields existed leave both as `None`."""
    entry = CalibrationEntry.model_validate({"camera": "Cam", "iso": "100", "count": 3})

    assert entry.binning is None
    assert entry.temperature_c is None


def test_a_flat_set_assessment_keeps_its_binning() -> None:
    """The assessment records the binning the flats were taken at."""
    assessment = FlatSetAssessment(
        telescope="Scope",
        camera="Cam",
        filter="L",
        gain="100",
        offset=30.0,
        binning="2x2",
        frameCount=20,
        passes=True,
    )

    assert assessment.binning == "2x2"
    assert FlatSetAssessment.model_validate(assessment.model_dump(by_alias=True)).binning == "2x2"
