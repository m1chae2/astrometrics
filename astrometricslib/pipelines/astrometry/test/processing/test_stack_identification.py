"""Purpose: Tests for naming the detected stars of a stack.

Description: A stack's detections are matched to the Gaia stars of its cache
tiles and, when online, to SIMBAD, whose most familiar name wins. Unmatched
detections are "Unnamed star", and bright Gaia stars with no detection are
listed too. These tests use a small made-up stack and stand-ins for the
detections, the tiles and SIMBAD, so no network is needed.
"""

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.quality.detections import DetectionsMeasurement, StarDetection
from astrometricslib.pipelines.astrometry.processing import stack_identification as module
from astrometricslib.pipelines.astrometry.processing.gaia_field_stars import FieldGaiaStars

SIZE = 200


def _wcs() -> WCS:
    """Build a north-up solution of a 200-pixel stack at 2 arcsec per pixel.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The solution.
    """
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [100.0, 30.0]
    wcs.wcs.crpix = [SIZE / 2.0, SIZE / 2.0]
    wcs.wcs.cdelt = [-2.0 / 3600.0, 2.0 / 3600.0]
    return wcs


def _stack(tmp_path: Path, frames: int = 20, solved: bool = True) -> str:
    """Write a small stack with a plate solution.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The test folder.
    frames : `int`, optional
        The ``STACKCNT`` card.
    solved : `bool`, optional
        Mark the solution as a real plate solution.

    Returns
    -------
    path : `str`
        The file.
    """
    header = _wcs().to_header()
    header["STACKCNT"] = frames
    header["DATE-OBS"] = "2026-10-04T07:36:15"
    if solved:
        header["PLTSOLVD"] = True
    path = tmp_path / "Test_L_Stacked.fits"
    fits.PrimaryHDU(np.zeros((SIZE, SIZE), dtype=np.float32), header=header).writeto(path)
    return str(path)


def _sky(x: float, y: float) -> tuple[float, float]:
    """Turn a 0-based pixel into a sky position under the test solution.

    Parameters
    ----------
    x, y : `float`
        The pixel.

    Returns
    -------
    ra, dec : `float`
        Degrees.
    """
    ra, dec = _wcs().wcs_pix2world([x], [y], 0)
    return float(ra[0]), float(dec[0])


def _setup(
    monkeypatch: pytest.MonkeyPatch, path: str, simbad: tuple[Any, list[str], list[Any], str] | None = None
) -> None:
    """Stand in for the detections, the Gaia tiles and SIMBAD.

    Three detections at pixels (50, 50), (100, 100) and (150, 150). Gaia has
    stars at the first two and one bright star at (30, 170) with no
    detection.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the steps.
    path : `str`
        The stack.
    simbad : `tuple`, optional
        What the SIMBAD step returns; by default an offline note.
    """
    detections = [
        StarDetection(x=x + 1.0, y=x + 1.0, flux=1000.0 / (index + 1))
        for index, x in enumerate((50, 100, 150))
    ]
    monkeypatch.setattr(
        module,
        "measure_detections_in_file",
        lambda _path: DetectionsMeasurement(
            path=path,
            width=SIZE,
            height=SIZE,
            detections=detections,
            detection_count=3,
            merged_count=0,
            kernel_fwhm_px=3.0,
        ),
    )
    positions = [_sky(50, 50), _sky(100, 100), _sky(30, 170)]
    table = Table({
        "source_id": ["11", "22", "33"],
        "ra": [p[0] for p in positions],
        "dec": [p[1] for p in positions],
        "phot_g_mean_mag": [12.0, 14.0, 10.0],
        "DESIGNATION": ["Gaia DR3 11", "Gaia DR3 22", "Gaia DR3 33"],
        "pmra": [0.0, 0.0, 0.0],
        "pmdec": [0.0, 0.0, 0.0],
    })
    coords = SkyCoord(ra=table["ra"] * u.deg, dec=table["dec"] * u.deg)
    monkeypatch.setattr(
        module,
        "load_field_gaia_stars",
        lambda config, tiles, depth, fetch, **_k: FieldGaiaStars(
            table=table, coords=coords, depth=depth, tiles=[1], complete_tiles=[1]
        ),
    )
    monkeypatch.setattr(module, "make_gaia_tile_fetcher", lambda: (None, "offline test"))
    monkeypatch.setattr(module, "tiles_under_image", lambda *_a, **_k: [1])
    monkeypatch.setattr(
        module,
        "_simbad_names",
        lambda *_a: simbad or (None, [], [], "Offline: the names come from Gaia only."),
    )


def test_offline_every_detection_gets_a_gaia_name_or_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gaia names two detections, the third is unnamed, a missed star shows."""
    path = _stack(tmp_path)
    _setup(monkeypatch, path)

    result = module.identify_stack_stars(path, config=None)

    names = [star.name for star in result.stars]
    kinds = [star.kind for star in result.stars]
    assert names[:3] == ["Gaia DR3 11", "Gaia DR3 22", module.UNNAMED_STAR]
    assert kinds == ["matched", "matched", "unmatched", "catalog_only"]
    assert result.stars[3].gaia_source_id == "33"
    assert result.solution_reused
    assert any("Gaia only" in note for note in result.notes)
    assert (result.matched_count, result.unmatched_count, result.catalog_only_count) == (2, 1, 1)


def test_a_simbad_name_wins_over_the_gaia_number(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SIMBAD's familiar name replaces the Gaia number of the same star."""
    path = _stack(tmp_path)
    ra, dec = _sky(50, 50)
    _setup(monkeypatch, path, (SkyCoord([ra] * u.deg, [dec] * u.deg), ["Vega"], ["11"], ""))

    result = module.identify_stack_stars(path, config=None)

    assert result.stars[0].name == "Vega"
    assert result.stars[0].name_source == "simbad"
    assert result.simbad_used
    assert result.notes == []


def test_the_result_is_saved_and_read_back_until_the_stack_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The saved names are read until the stack changes."""
    path = _stack(tmp_path)
    _setup(monkeypatch, path)
    result = module.identify_stack_stars(path, config=None)

    assert module.read_stack_identification(path) == result
    with fits.open(path, mode="update", memmap=False) as hdul:
        hdul[0].header["NEWCARD"] = 1
    assert module.read_stack_identification(path) is None


def test_a_single_frame_is_refused(tmp_path: Path) -> None:
    """Raw frames never get this treatment."""
    path = _stack(tmp_path, frames=1)

    with pytest.raises(InvalidArgumentError, match="stacks only"):
        module.identify_stack_stars(path, config=None)


def test_a_stop_request_stops_the_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stop asked for before the matching ends the call with an error."""
    path = _stack(tmp_path)
    _setup(monkeypatch, path)

    with pytest.raises(module.StackIdentificationStoppedError):
        module.identify_stack_stars(path, config=None, should_stop=lambda: True)
    assert module.read_stack_identification(path) is None
