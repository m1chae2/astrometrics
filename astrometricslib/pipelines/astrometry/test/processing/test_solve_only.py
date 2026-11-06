"""Purpose: Tests that the solve-only path finds the WCS and asks no catalog.

Description: The stack noise step needs only the sky coordinates of a stack
(its WCS, the map from pixels to the sky). Running the whole astrometry
pipeline for them waited minutes on Gaia and SIMBAD time-outs on 2026-11-04.
`StarIdentifier.process_image(solve_only=True)` and
`AstrometryPipeline.solve_wcs` stop after the plate solver answers. These
tests use a fake solver and make every catalog, refit and cache-seeding step
fail the test if it is reached, so no network or solve-field is needed.
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib._internal.drivers.interfaces import plate_solve_driver
from astrometricslib.pipelines.astrometry.pipeline import AstrometryPipeline
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier
from astrometricslib.pipelines.astrometry.test.processing.test_plate_solve_fit_statistics import (
    FIELD_DEC_DEG,
    FIELD_RA_DEG,
    EmptySimbad,
    FakeSolver,
    make_wcs_header,
)


def _fail_if_called(*arguments: Any, **keywords: Any) -> None:
    """Fail the test: a step that asks a catalog was reached.

    Parameters
    ----------
    *arguments
        Ignored.
    **keywords
        Ignored.

    Raises
    ------
    AssertionError
        Always.
    """
    raise AssertionError("the solve-only path must not reach this step")


def _forbid_catalog_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every refit, naming, query and seeding step fail if called.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace the steps.
    """
    for name in (
        "_refine_wcs_against_gaia",
        "_identify_stars_with_simbad",
        "_query_gaia_region",
        "_query_simbad_region",
        "_fetch_gaia_cone",
        "_seed_gaia_cache_for_field",
    ):
        if hasattr(StarIdentifier, name):
            monkeypatch.setattr(StarIdentifier, name, _fail_if_called)
    monkeypatch.setattr(
        "astrometricslib.pipelines.astrometry.processing.star_identifier._fetch_gaia_cone", _fail_if_called
    )
    for name in ("_seed_gaia_cache_from_wcs", "_build_extended_source_hint", "check_extended_source"):
        monkeypatch.setattr(AstrometryPipeline, name, _fail_if_called)


def _identifier(answer: fits.Header | None) -> StarIdentifier:
    """Build an identifier with ten fake detections and a fake solver.

    Parameters
    ----------
    answer : `astropy.io.fits.Header` or `None`
        What the fake solver returns.

    Returns
    -------
    identifier : `StarIdentifier`
        The identifier, ready to run.
    """
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
    return identifier


def test_solve_only_returns_the_solvers_solution_and_asks_no_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`solve_only` returns the solver's WCS and keeps its fit numbers."""
    _forbid_catalog_work(monkeypatch)
    header = plate_solve_driver.PlateSolveHeader.from_header(
        make_wcs_header(), fit_residual_rms_arcsec=0.8, matched_star_count=31
    )
    identifier = _identifier(header)

    _, wcs = identifier.process_image(np.zeros((100, 100)), attempt_plate_solving=True, solve_only=True)

    assert wcs is not None
    assert wcs.wcs.crval[0] == pytest.approx(FIELD_RA_DEG)
    assert wcs.wcs.crval[1] == pytest.approx(FIELD_DEC_DEG)
    assert identifier.plate_solve_fit_residual_rms_arcsec == pytest.approx(0.8)
    assert identifier.astrometry_flags == []


def test_solve_only_with_an_unsolved_field_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A solver with no answer fails as in the full process."""
    from astrometricslib.utilities.exceptions import PlateSolveFailedError

    _forbid_catalog_work(monkeypatch)
    identifier = _identifier(None)

    with pytest.raises(PlateSolveFailedError):
        identifier.process_image(np.zeros((100, 100)), attempt_plate_solving=True, solve_only=True)


def test_the_pipeline_solve_wcs_skips_refit_naming_and_seeding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`AstrometryPipeline.solve_wcs` reads a file and returns only the WCS."""
    _forbid_catalog_work(monkeypatch)
    path = tmp_path / "stack.fits"
    fits.PrimaryHDU(np.zeros((100, 100), dtype=np.float32)).writeto(path)
    config = MagicMock()
    config.get_maximum_identified_stars.return_value = None
    config.get_value.return_value = None
    config.get_focal_length_mm.side_effect = ValueError("no focal length configured")
    pipeline = AstrometryPipeline(
        app_config=config, drivers=Drivers(plate_solve=FakeSolver(make_wcs_header()), simbad=EmptySimbad())
    )
    identifier = _identifier(make_wcs_header())
    pipeline.star_identifier.detector = identifier.detector

    wcs = pipeline.solve_wcs(str(path))

    assert wcs is not None
    assert wcs.wcs.crval[0] == pytest.approx(FIELD_RA_DEG)


def _allow_only_the_cached_refit(
    monkeypatch: pytest.MonkeyPatch, cached: tuple[Any, Any] | None
) -> list[str]:
    """Allow the refit step but forbid every remote query.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace the steps.
    cached : `tuple` or `None`
        What the cache lookup returns: a table and coordinates, or nothing.

    Returns
    -------
    calls : `list` [`str`]
        Filled with ``"cache"`` for each cache lookup.
    """
    from astrometricslib.pipelines.astrometry.processing import star_identifier

    calls: list[str] = []

    def cache_lookup(*arguments: Any, **keywords: Any) -> tuple[Any, Any] | None:
        """Record the lookup and give the prepared answer.

        Parameters
        ----------
        *arguments
            Ignored.
        **keywords
            Ignored.

        Returns
        -------
        cached : `tuple` or `None`
            The prepared answer.
        """
        calls.append("cache")
        return cached

    monkeypatch.setattr(StarIdentifier, "_refine_wcs_against_gaia", _REAL_REFINE)
    monkeypatch.setattr(StarIdentifier, "_query_gaia_region_from_cache", staticmethod(cache_lookup))
    monkeypatch.setattr(StarIdentifier, "_query_gaia_region", staticmethod(_fail_if_called))
    monkeypatch.setattr(star_identifier, "_fetch_gaia_cone", _fail_if_called)
    return calls


_REAL_REFINE = StarIdentifier._refine_wcs_against_gaia


def test_refit_from_cache_without_a_complete_download_keeps_the_solution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no complete cached download, nothing is sent."""
    _forbid_catalog_work(monkeypatch)
    calls = _allow_only_the_cached_refit(monkeypatch, None)
    identifier = _identifier(plate_solve_driver.PlateSolveHeader.from_header(make_wcs_header()))

    _, wcs = identifier.process_image(
        np.zeros((100, 100)), attempt_plate_solving=True, solve_only=True, refit_from_cache=True
    )

    assert wcs is not None and wcs.wcs.crval[0] == pytest.approx(FIELD_RA_DEG)
    assert calls == ["cache"]
    assert identifier.astrometry_flags == ["wcs_refinement_skipped"]
    assert identifier.wcs_refinement is None


def test_refit_from_cache_with_a_cached_download_uses_the_refit_solution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A complete cached download gives a refit with no remote query."""
    from types import SimpleNamespace

    from astropy.coordinates import SkyCoord
    from astropy.wcs import WCS

    from astrometricslib.pipelines.astrometry.processing import star_identifier

    _forbid_catalog_work(monkeypatch)
    stars = SkyCoord([FIELD_RA_DEG] * 3, [FIELD_DEC_DEG] * 3, unit="deg")
    calls = _allow_only_the_cached_refit(monkeypatch, (SimpleNamespace(meta={}), stars))
    refit_wcs = WCS(make_wcs_header(), naxis=2)
    refit_wcs.wcs.crval = [FIELD_RA_DEG + 0.001, FIELD_DEC_DEG]
    refinement = SimpleNamespace(
        applied=True,
        wcs=refit_wcs,
        star_count=3,
        rms_before_arcsec=1.0,
        rms_after_arcsec=0.2,
        shift_arcsec=3.6,
        reason="",
        fit_star_indices=(),
    )
    monkeypatch.setattr(star_identifier, "refine_wcs_with_gaia", lambda *arguments, **keywords: refinement)
    monkeypatch.setattr(
        StarIdentifier, "_move_gaia_coords_to_observation_epoch", lambda self, table, coords: coords
    )
    identifier = _identifier(plate_solve_driver.PlateSolveHeader.from_header(make_wcs_header()))

    _, wcs = identifier.process_image(
        np.zeros((100, 100)), attempt_plate_solving=True, solve_only=True, refit_from_cache=True
    )

    assert wcs is refit_wcs
    assert calls == ["cache"]
    assert identifier.astrometry_flags == ["wcs_refined_against_gaia"]
