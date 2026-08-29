"""Tests for acquisition conditions captured from FITS headers at index time.

These describe the sky and equipment state a frame was taken under. They
were previously discarded, leaving no way to tell "the mount misbehaved"
from "the target was low" when a later analysis flagged a frame.
"""

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.catalog_services.frame_scanning import create_frame_record_from_fits


def _write_frame(path, **header_cards):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function, missing-type-kwargs]
    """Write a small FITS frame carrying the given header cards.

    Returns
    -------
    path : `str`
        The path just written.
    """
    header = fits.Header()
    header["EXPTIME"] = 30.0
    for key, value in header_cards.items():
        header[key.replace("_", "-")] = value
    fits.PrimaryHDU(np.zeros((8, 8), dtype=np.float32), header=header).writeto(path)
    return str(path)


def test_captures_pier_side(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Pier side is what separates a meridian flip from a tracking fault."""
    record = create_frame_record_from_fits(_write_frame(tmp_path / "a.fits", PIERSIDE="WEST"))

    assert record.pier_side == "WEST"


def test_captures_sky_position_and_airmass(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Altitude and airmass explain seeing and background variation."""
    record = create_frame_record_from_fits(
        _write_frame(tmp_path / "a.fits", AIRMASS=1.21, OBJCTALT=55.9, OBJCTAZ=160.8)
    )

    assert record.airmass == pytest.approx(1.21)
    assert record.altitude_degrees == pytest.approx(55.9)
    assert record.azimuth_degrees == pytest.approx(160.8)


def test_captures_pixel_scale_and_binning(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Pixel scale is what makes a FWHM in pixels comparable across setups."""
    record = create_frame_record_from_fits(_write_frame(tmp_path / "a.fits", SECPIX1=1.915, XBINNING=2))

    assert record.pixel_scale_arcsec == pytest.approx(1.915)
    assert record.binning == 2


def test_falls_back_to_scale_when_secpix_absent(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Different writers spell the same quantity differently."""
    record = create_frame_record_from_fits(_write_frame(tmp_path / "a.fits", SCALE=2.04))

    assert record.pixel_scale_arcsec == pytest.approx(2.04)


def test_captures_cooled_camera_telemetry(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Sensor and focuser telemetry enable focus-drift analysis."""
    record = create_frame_record_from_fits(
        _write_frame(tmp_path / "a.fits", CCD_TEMP=5.6, FOCUSPOS=29936, FOCUSTEM=13.29)
    )

    assert record.sensor_temperature_c == pytest.approx(5.6)
    assert record.focuser_position == 29936
    assert record.focuser_temperature_c == pytest.approx(13.29)


def test_absent_keys_leave_fields_unset(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A DSLR writes no cooling telemetry; that is not an error."""
    record = create_frame_record_from_fits(_write_frame(tmp_path / "a.fits"))

    assert record.sensor_temperature_c is None
    assert record.focuser_position is None
    assert record.pier_side is None


def test_non_numeric_header_value_does_not_raise(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A malformed value yields None rather than aborting the whole scan."""
    record = create_frame_record_from_fits(_write_frame(tmp_path / "a.fits", AIRMASS="N/A"))

    assert record.airmass is None
    # The rest of the record must still be populated.
    assert record.exposure == "30.0"


def test_refresh_populates_an_already_indexed_frame(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Fields added after a frame was indexed must still reach it.

    scan_target_directory only builds records for files it has not seen,
    so without an explicit refresh a newly added FrameRecord field stays
    None on every existing record forever.
    """
    from astrometricslib.catalog_services.frame_scanning import refresh_acquisition_conditions

    path = _write_frame(tmp_path / "a.fits", PIERSIDE="EAST", AIRMASS=1.4)
    record = create_frame_record_from_fits(path)
    # Simulate a record indexed before these fields existed.
    record.pier_side = None
    record.airmass = None

    assert refresh_acquisition_conditions(record) is True
    assert record.pier_side == "EAST"
    assert record.airmass == pytest.approx(1.4)


def test_refresh_preserves_measured_values(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A header refresh must not discard anything a pipeline measured.

    Registration facts and pixel measurements come from stacking and
    dedicated measurement passes, not the header; re-deriving them would
    cost orders of magnitude more than the ~10ms header read.
    """
    from astrometricslib.catalog_services.frame_scanning import refresh_acquisition_conditions

    record = create_frame_record_from_fits(_write_frame(tmp_path / "a.fits", PIERSIDE="WEST"))
    record.background_level = 4456.0
    record.registration_fwhm_x_px = 2.48
    record.registration_dx_px = 6.32

    refresh_acquisition_conditions(record)

    assert record.background_level == pytest.approx(4456.0)
    assert record.registration_fwhm_x_px == pytest.approx(2.48)
    assert record.registration_dx_px == pytest.approx(6.32)


def test_refresh_of_a_missing_file_reports_failure(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A deleted frame cannot be refreshed, and says so rather than raising."""
    from astrometricslib.catalog_services.frame_scanning import refresh_acquisition_conditions
    from astrometricslib.models.target import FrameRecord

    record = FrameRecord(path=str(tmp_path / "gone.fits"), role="LIGHT", exposure="30.0")

    assert refresh_acquisition_conditions(record) is False
