"""Purpose: Tests that session identification keeps the solver's fit numbers.

Description: A plate solver may return a `PlateSolveHeader`, a FITS header
that also carries the fit residual (how far, on average, the fitted star
positions sit from the reference stars) and the count of matched stars.
`identify_session_stars` must copy both numbers into its result when its
sky map comes from a fresh solve. They stay unknown (`None`) when the map
was reused from the image header, when the solver returns a plain header,
or when a re-solve was rejected. The solver here is a fake, so no network or
solve-field program is needed.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib.drivers.interfaces.plate_solve_driver import PlateSolveHeader
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier
from astrometricslib.pipelines.shared import session_identification
from astrometricslib.pipelines.shared.session_identification import (
    identify_session_stars,
    resolve_frame_wcs,
)

FIELD_RA_DEG = 250.42
FIELD_DEC_DEG = 36.46
KNOWN_RESIDUAL_ARCSEC = 0.37
KNOWN_MATCHED_STARS = 83


def _make_wcs_header() -> fits.Header:
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


def _make_solved_header(
    residual: float | None = KNOWN_RESIDUAL_ARCSEC, matched: int | None = KNOWN_MATCHED_STARS
) -> PlateSolveHeader:
    """Build the header a fake solver returns, with known fit statistics.

    Parameters
    ----------
    residual : `float` or `None`, optional
        The fit residual in arcseconds.
    matched : `int` or `None`, optional
        The matched-star count.

    Returns
    -------
    header : `PlateSolveHeader`
        The sky map with the statistics attached.
    """
    return PlateSolveHeader.from_header(
        _make_wcs_header(), fit_residual_rms_arcsec=residual, matched_star_count=matched
    )


def _make_identifier(solver_answer: fits.Header | None) -> StarIdentifier:
    """Build a star identifier whose solver always gives one answer.

    Parameters
    ----------
    solver_answer : `astropy.io.fits.Header` or `None`
        What the fake solver's ``solve`` returns.

    Returns
    -------
    identifier : `StarIdentifier`
        An identifier with two stars "detected" and a fake solver.
    """
    config = MagicMock()
    config.get_value.return_value = None
    config.get_focal_length_mm.return_value = None
    identifier = StarIdentifier(config=config)
    sources = [{"xcentroid": float(i * 10), "ycentroid": float(i * 10), "flux": 1000.0 - i} for i in range(2)]
    identifier.detector.detect = MagicMock(return_value=sources)
    identifier.detector.deduplicate = MagicMock(return_value=sources)
    identifier.solver.solve = MagicMock(return_value=solver_answer)
    return identifier


def _make_image(wcs: object = None, path: str = "/fake/reference.fits") -> MagicMock:
    """Build a fake reference frame.

    Parameters
    ----------
    wcs : `object`, optional
        The sky map already in the image header, or `None`.
    path : `str`, optional
        The image's file path.

    Returns
    -------
    image : `unittest.mock.MagicMock`
        A stand-in for `AstrometricsImage`.
    """
    image = MagicMock()
    image.wcs = wcs
    image.path = path
    image.data = np.zeros((100, 100), dtype=np.float32)
    image.header = {}
    return image


def _celestial_wcs() -> MagicMock:
    """Build a stand-in sky map that passes the "is celestial" check.

    Returns
    -------
    wcs : `unittest.mock.MagicMock`
        A fake WCS.
    """
    wcs = MagicMock()
    wcs.is_celestial = True
    return wcs


def _identify_marking(matched_count: int) -> Callable[..., Any]:
    """Build a stand-in for ``identify_stars_with_wcs`` that marks N matches.

    Parameters
    ----------
    matched_count : `int`
        How many stars to mark as catalog-identified.

    Returns
    -------
    identify : `Callable`
        The stand-in.
    """

    def _identify(
        stellar_objects: list[StellarObject], wcs: object, width: int, height: int
    ) -> list[StellarObject]:
        """Mark the first stars as identified and hand the list back.

        Parameters
        ----------
        stellar_objects : `list` [`StellarObject`]
            The stars to mark.
        wcs : `object`
            Ignored.
        width, height : `int`
            Ignored.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            The same list.
        """
        for star in stellar_objects[:matched_count]:
            star.is_catalog_identified = True
        return stellar_objects

    return _identify


def test_fresh_solve_statistics_reach_the_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake solver's residual and star count appear on the result."""
    identifier = _make_identifier(_make_solved_header())
    monkeypatch.setattr(identifier, "identify_stars_with_wcs", MagicMock(side_effect=_identify_marking(2)))

    result = identify_session_stars(_make_image(), identifier, write_back=False)

    assert result.solve_attempted is True
    assert result.plate_solve_succeeded is True
    assert result.fit_residual_rms_arcsec == pytest.approx(KNOWN_RESIDUAL_ARCSEC)
    assert result.matched_star_count == KNOWN_MATCHED_STARS


def test_a_plain_header_leaves_the_statistics_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """A solver that reports no statistics gives `None`, not zero."""
    identifier = _make_identifier(_make_wcs_header())
    monkeypatch.setattr(identifier, "identify_stars_with_wcs", MagicMock(side_effect=_identify_marking(2)))

    result = identify_session_stars(_make_image(), identifier, write_back=False)

    assert result.plate_solve_succeeded is True
    assert result.fit_residual_rms_arcsec is None
    assert result.matched_star_count is None


def test_statistics_can_be_partly_known(monkeypatch: pytest.MonkeyPatch) -> None:
    """A residual without a star count keeps the residual only."""
    identifier = _make_identifier(_make_solved_header(residual=1.25, matched=None))
    monkeypatch.setattr(identifier, "identify_stars_with_wcs", MagicMock(side_effect=_identify_marking(2)))

    result = identify_session_stars(_make_image(), identifier, write_back=False)

    assert result.fit_residual_rms_arcsec == pytest.approx(1.25)
    assert result.matched_star_count is None


def test_a_reused_header_wcs_has_no_statistics(monkeypatch: pytest.MonkeyPatch) -> None:
    """A map read from the image file was never measured: both are `None`."""
    identifier = _make_identifier(_make_solved_header())
    monkeypatch.setattr(identifier, "identify_stars_with_wcs", MagicMock(side_effect=_identify_marking(2)))

    result = identify_session_stars(_make_image(wcs=_celestial_wcs()), identifier)

    assert result.reused_existing_header_wcs is True
    assert result.fit_residual_rms_arcsec is None
    assert result.matched_star_count is None
    identifier.solver.solve.assert_not_called()


def test_a_failed_solve_has_no_statistics() -> None:
    """When the solver finds nothing there is no fit to report."""
    identifier = _make_identifier(None)

    result = identify_session_stars(_make_image(), identifier, write_back=False)

    assert result.wcs is None
    assert result.fit_residual_rms_arcsec is None
    assert result.matched_star_count is None


def test_a_re_solve_that_replaces_the_header_wcs_brings_its_statistics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh solve that replaces a bad header map brings its numbers."""
    identifier = _make_identifier(_make_solved_header())
    sources = [{"xcentroid": float(i), "ycentroid": float(i), "flux": 1000.0 - i} for i in range(40)]
    identifier.detector.detect = MagicMock(return_value=sources)
    identifier.detector.deduplicate = MagicMock(return_value=sources)
    # The header map matches 1 of 40 stars; the fresh solve matches 25.
    marks = iter([_identify_marking(1), _identify_marking(25)])
    monkeypatch.setattr(
        identifier,
        "identify_stars_with_wcs",
        MagicMock(side_effect=lambda *args, **keywords: next(marks)(*args, **keywords)),
    )
    monkeypatch.setattr(session_identification, "write_wcs_to_fits_header", MagicMock())

    result = identify_session_stars(_make_image(wcs=_celestial_wcs()), identifier)

    assert result.header_wcs_replaced_after_verification is True
    assert result.fit_residual_rms_arcsec == pytest.approx(KNOWN_RESIDUAL_ARCSEC)
    assert result.matched_star_count == KNOWN_MATCHED_STARS


def test_a_re_solve_that_is_rejected_leaves_the_statistics_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected re-solve leaves the numbers unknown (header map kept)."""
    identifier = _make_identifier(_make_solved_header())
    sources = [{"xcentroid": float(i), "ycentroid": float(i), "flux": 1000.0 - i} for i in range(40)]
    identifier.detector.detect = MagicMock(return_value=sources)
    identifier.detector.deduplicate = MagicMock(return_value=sources)
    # The fresh solve matches no more stars than the header map did.
    marks = iter([_identify_marking(1), _identify_marking(1)])
    monkeypatch.setattr(
        identifier,
        "identify_stars_with_wcs",
        MagicMock(side_effect=lambda *args, **keywords: next(marks)(*args, **keywords)),
    )
    write_back_spy = MagicMock()
    monkeypatch.setattr(session_identification, "write_wcs_to_fits_header", write_back_spy)

    result = identify_session_stars(_make_image(wcs=_celestial_wcs()), identifier)

    assert result.header_wcs_replaced_after_verification is False
    assert result.reused_existing_header_wcs is True
    assert result.fit_residual_rms_arcsec is None
    assert result.matched_star_count is None
    write_back_spy.assert_not_called()


def test_resolve_frame_wcs_still_returns_three_values() -> None:
    """The public helper keeps its three-value result for its callers."""
    identifier = _make_identifier(_make_solved_header())

    answer = resolve_frame_wcs(_make_image(), identifier, write_back=False)

    assert len(answer) == 3
    wcs, reused, solve_attempted = answer
    assert wcs is not None
    assert reused is False
    assert solve_attempted is True


def test_write_back_still_saves_the_solved_wcs_to_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The solved map is still written into the file header."""
    path = tmp_path / "reference.fits"
    fits.PrimaryHDU(np.zeros((100, 100), dtype=np.float32)).writeto(path)
    identifier = _make_identifier(_make_solved_header())
    monkeypatch.setattr(identifier, "identify_stars_with_wcs", MagicMock(side_effect=_identify_marking(2)))

    result = identify_session_stars(_make_image(path=str(path)), identifier)

    assert result.fit_residual_rms_arcsec == pytest.approx(KNOWN_RESIDUAL_ARCSEC)
    with fits.open(path, memmap=False) as hdul:
        header = hdul[0].header
        assert header["CRVAL1"] == pytest.approx(FIELD_RA_DEG)
        assert header["CRVAL2"] == pytest.approx(FIELD_DEC_DEG)
