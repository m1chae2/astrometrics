"""Purpose: End-to-end tests for AsteroidRecoveryPipeline.

Description: Verifies the full pipeline (per-frame WCS estimation,
point-source detection, discrimination cascade, ephemeris cross-match)
against synthetic FITS frames containing a single point source moving
linearly across the field. astroquery.imcce.Skybot.cone_search is
mocked so these tests never make a real network call.
"""

import astropy.units as u
import numpy as np
import pytest
from astropy.io import fits
from astropy.modeling.models import Gaussian2D
from astropy.table import QTable

from astrometricslib.models.moving_object import CascadeStage
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.tasks.moving_object_tasks.moving_object_pipeline_tasks import (
    AsteroidRecoveryPipeline,
)

_STAR_PIXEL_POSITIONS = [(20.0, 20.0), (25.0, 23.0), (30.0, 26.0), (35.0, 29.0)]
_FRAME_TIMESTAMPS = [0.0, 600.0, 1200.0, 1800.0]


def _write_frame_fits(  # ruff: ignore[missing-return-type-private-function]
    path,  # ruff: ignore[missing-type-function-argument]
    star_pixel_xy,  # ruff: ignore[missing-type-function-argument]
    right_ascension_deg=150.0,  # ruff: ignore[missing-type-function-argument]
    declination_deg=0.0,  # ruff: ignore[missing-type-function-argument]
    include_radec=True,  # ruff: ignore[missing-type-function-argument]
):
    """Write a synthetic 64x64 frame FITS file with one Gaussian source."""
    rng = np.random.default_rng(0)
    data = rng.normal(100.0, 5.0, (64, 64)).astype(np.float32)
    yy, xx = np.mgrid[0:64, 0:64]
    data += Gaussian2D(5000.0, star_pixel_xy[0], star_pixel_xy[1], 2.0, 2.0)(xx, yy)
    header = fits.Header()
    if include_radec:
        header["RA"] = right_ascension_deg
        header["DEC"] = declination_deg
    fits.PrimaryHDU(data.astype(np.float32), header=header).writeto(path, overwrite=True)


def _write_stack_fits(path):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Write a synthetic 64x64 stack FITS file with a real TAN WCS header."""
    header = fits.Header()
    header["NAXIS1"] = 64
    header["NAXIS2"] = 64
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CRVAL1"] = 150.0
    header["CRVAL2"] = 0.0
    header["CRPIX1"] = 32.0
    header["CRPIX2"] = 32.0
    header["CD1_1"] = -0.0005
    header["CD1_2"] = 0.0
    header["CD2_1"] = 0.0
    header["CD2_2"] = 0.0005
    header["CUNIT1"] = "deg"
    header["CUNIT2"] = "deg"
    fits.PrimaryHDU(np.zeros((64, 64), dtype=np.float32), header=header).writeto(path, overwrite=True)


def _build_moving_target(tmp_path, include_radec=True):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Build a Target with 4 light frames of a linearly moving source.

    Returns
    -------
    Target
        A target with 4 light frames and a stacked image set.
    """
    frames = []
    zipped_frames = zip(_STAR_PIXEL_POSITIONS, _FRAME_TIMESTAMPS, strict=False)
    for index, (star_pixel, timestamp) in enumerate(zipped_frames):
        frame_path = tmp_path / f"frame{index}.fits"
        _write_frame_fits(frame_path, star_pixel, include_radec=include_radec)
        frames.append(FrameRecord(path=str(frame_path), timestamp=timestamp))

    stack_path = tmp_path / "stack.fits"
    _write_stack_fits(stack_path)

    target = Target(id="TestAsteroidTarget", frames=frames)
    target.stacked_image = str(stack_path)
    return target


def test_process_raises_when_target_has_no_stacked_image():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify process() raises rather than silently doing nothing."""
    target = Target(id="NoStackTarget", frames=[])
    pipeline = AsteroidRecoveryPipeline(MovingObjectConfig())
    with pytest.raises(ValueError, match="stacked_image"):
        pipeline.process(target)


def test_process_confirms_a_moving_source_with_no_ephemeris_match(tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the pipeline confirms a genuinely moving point source.

    Ends at RATE_LINEARITY_CONFIRMED when no known body matches.
    """
    mocker.patch("astroquery.imcce.Skybot.cone_search", return_value=None)

    target = _build_moving_target(tmp_path)
    pipeline = AsteroidRecoveryPipeline(MovingObjectConfig())

    candidates = pipeline.process(target)

    assert len(candidates) == 1
    assert candidates[0].cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED
    assert candidates[0].track is not None
    assert candidates[0].track.total_rate_arcsec_per_hour == pytest.approx(62.98, rel=0.01)
    assert pipeline.last_run_metrics["frames_with_wcs_estimate"] == 4
    assert pipeline.last_run_metrics["frames_excluded_missing_pointing_metadata"] == 0
    assert pipeline.last_run_metrics["candidates_rate_linearity_confirmed"] == 1
    assert pipeline.last_run_metrics["candidates_ephemeris_matched"] == 0


def test_process_matches_a_moving_source_against_a_known_body(tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the pipeline reaches EPHEMERIS_MATCHED for a known body.

    SkyBoT returns a known body close to the candidate's mean position.
    """
    field_table = QTable({
        "Number": [-1],
        "Name": ["2003 XY99"],
        "RA": [150.002] * u.deg,
        "DEC": [-0.003] * u.deg,
        "V": [15.0],
        "RA_rate": [-54.0] * (u.arcsec / u.hour),
        "DEC_rate": [32.0] * (u.arcsec / u.hour),
    })
    mocker.patch("astroquery.imcce.Skybot.cone_search", return_value=field_table)

    target = _build_moving_target(tmp_path)
    pipeline = AsteroidRecoveryPipeline(MovingObjectConfig())

    candidates = pipeline.process(target)

    assert len(candidates) == 1
    assert candidates[0].cascade_stage == CascadeStage.EPHEMERIS_MATCHED
    assert candidates[0].ephemeris_match is not None
    assert candidates[0].ephemeris_match.designation == "2003 XY99"
    assert pipeline.last_run_metrics["candidates_ephemeris_matched"] == 1


def test_process_excludes_frames_missing_pointing_metadata(tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify frames missing RA/DEC headers are excluded and counted."""
    mocker.patch("astroquery.imcce.Skybot.cone_search", return_value=None)

    target = _build_moving_target(tmp_path, include_radec=False)
    pipeline = AsteroidRecoveryPipeline(MovingObjectConfig())

    candidates = pipeline.process(target)

    assert candidates == []
    assert pipeline.last_run_metrics["frames_with_wcs_estimate"] == 0
    assert pipeline.last_run_metrics["frames_excluded_missing_pointing_metadata"] == 4
