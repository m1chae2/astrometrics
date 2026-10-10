"""Purpose: Tests that the plate solver's fit statistics reach the identifier.

Description: A plate solver may report how well its fit matched the field
stars to its reference catalog (a fit residual and a matched-star count) by
returning a `PlateSolveHeader`. `StarIdentifier.process_image` keeps those
numbers beside the catalog match separation, the separate and weaker number
measured against SIMBAD and Gaia. A solver that returns a plain header leaves
the fit numbers unknown. The identifier runs here with a fake solver and a
fake SIMBAD, so no network or solve-field is needed.
"""

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS

from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.drivers.interfaces import PlateSolveDriver, SimbadDriver, plate_solve_driver
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier

FIELD_RA_DEG = 250.42
FIELD_DEC_DEG = 36.46


def make_wcs_header() -> fits.Header:
    """Build a header holding a flat sky map, 2 arcseconds per pixel.

    Returns
    -------
    header : `astropy.io.fits.Header`
        The map, centred on the test field.
    """
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [FIELD_RA_DEG, FIELD_DEC_DEG]
    wcs.wcs.crpix = [50.0, 50.0]
    wcs.wcs.cdelt = [-2.0 / 3600.0, 2.0 / 3600.0]
    return wcs.to_header()


class FakeSolver(PlateSolveDriver):
    """A plate solver that answers with a fixed header."""

    def __init__(self, answer: fits.Header | None) -> None:
        """Remember the header to return.

        Parameters
        ----------
        answer : `astropy.io.fits.Header` or `None`
            What every `solve` call returns.
        """
        self.answer = answer

    def solve(self, *args: Any, **hints: Any) -> fits.Header | None:
        """Return the fixed header.

        Parameters
        ----------
        *args
            Ignored.
        **hints
            Ignored.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The header given at construction.
        """
        return self.answer


class EmptySimbad(SimbadDriver):
    """A SIMBAD client that knows no stars."""

    def query_region(self, *args: Any, **keywords: Any) -> Table:
        """Answer with an empty table.

        Parameters
        ----------
        *args
            Ignored.
        **keywords
            Ignored.

        Returns
        -------
        table : `astropy.table.Table`
            A table with no rows.
        """
        return Table()

    def query_object(self, *args: Any, **keywords: Any) -> None:
        """Know no object.

        Parameters
        ----------
        *args
            Ignored.
        **keywords
            Ignored.
        """
        return

    def query_tap(self, *args: Any, **keywords: Any) -> Table:
        """Answer with an empty table.

        Parameters
        ----------
        *args
            Ignored.
        **keywords
            Ignored.

        Returns
        -------
        table : `astropy.table.Table`
            A table with no rows.
        """
        return Table()


def run_identifier(monkeypatch: pytest.MonkeyPatch, answer: fits.Header | None) -> StarIdentifier:
    """Run `process_image` on ten fake detections with a fake solver.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to switch off the Gaia query.
    answer : `astropy.io.fits.Header` or `None`
        What the fake solver returns.

    Returns
    -------
    identifier : `StarIdentifier`
        The identifier after the run.
    """
    monkeypatch.setattr(StarIdentifier, "_query_gaia_region", staticmethod(lambda *a, **k: (None, None)))
    config = MagicMock()
    config.get_maximum_identified_stars.return_value = None
    config.get_value.return_value = None
    config.get_focal_length_mm.side_effect = ValueError("no focal length configured")
    identifier = StarIdentifier(
        config=config, drivers=Drivers(plate_solve=FakeSolver(answer), simbad=EmptySimbad())
    )
    sources = [{"x_centroid": 10.0 + 8 * i, "y_centroid": 10.0 + 8 * i, "flux": 100.0 - i} for i in range(10)]
    identifier.detector = MagicMock()
    identifier.detector.detect.return_value = sources
    identifier.detector.deduplicate.return_value = sources
    identifier.process_image(np.zeros((100, 100)), attempt_plate_solving=True)
    return identifier


def test_the_solvers_fit_statistics_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `PlateSolveHeader` gives the identifier its fit numbers."""
    header = plate_solve_driver.PlateSolveHeader.from_header(
        make_wcs_header(), fit_residual_rms_arcsec=0.8, matched_star_count=31
    )

    identifier = run_identifier(monkeypatch, header)

    assert identifier.plate_solve_fit_residual_rms_arcsec == pytest.approx(0.8)
    assert identifier.plate_solve_matched_star_count == 31


def test_a_plain_header_leaves_the_fit_statistics_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """A solver with no statistics gives `None`, not zero."""
    identifier = run_identifier(monkeypatch, make_wcs_header())

    assert identifier.plate_solve_fit_residual_rms_arcsec is None
    assert identifier.plate_solve_matched_star_count is None


def test_the_catalog_separation_is_a_separate_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """The catalog proxy is not replaced by the fit residual."""
    header = plate_solve_driver.PlateSolveHeader.from_header(
        make_wcs_header(), fit_residual_rms_arcsec=0.8, matched_star_count=31
    )
    identifier = run_identifier(monkeypatch, header)
    identifier.catalog_match_separations_arcsec = [3.0, 4.0]

    assert identifier.get_catalog_match_separation_rms_arcsec() == pytest.approx(np.sqrt(12.5), rel=1e-3)
    assert (
        identifier.get_astrometric_residual_rms_arcsec()
        == identifier.get_catalog_match_separation_rms_arcsec()
    )
    assert identifier.plate_solve_fit_residual_rms_arcsec == pytest.approx(0.8)


def test_the_statistics_reset_between_images(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second solve with no statistics does not keep the first's."""
    header = plate_solve_driver.PlateSolveHeader.from_header(
        make_wcs_header(), fit_residual_rms_arcsec=0.8, matched_star_count=31
    )
    identifier = run_identifier(monkeypatch, header)
    identifier.solver = FakeSolver(make_wcs_header())

    identifier.process_image(np.zeros((100, 100)), attempt_plate_solving=True)

    assert identifier.plate_solve_fit_residual_rms_arcsec is None
    assert identifier.plate_solve_matched_star_count is None
