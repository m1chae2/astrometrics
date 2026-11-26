"""Purpose: Test that a new stack's plate solution is saved in its header.

Description: The stacking stage solves a stack with no plate solution and
writes the solution into the stack's header, so the image viewer can draw star
names on it. These tests replace the solve with a stand-in and check what is
written, what is skipped, and that a failed solve changes nothing.
"""

import logging
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Never

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib._internal.drivers.fits_access import header_is_plate_solved
from astrometricslib.pipelines.stacking import stage
from astrometricslib.pipelines.stacking.post_processing import stack_noise
from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
    PLATE_SOLVE_UNREFINED_WCS_SOURCE,
)
from astrometricslib.test.synthetic.sky_map import tan_sky_map

_NO_SOLUTION = "plate-solving the stack itself was tried and found no solution"


def _never_solve(stack_path: str, drivers: object) -> Never:
    """Fail the test if a solve is attempted.

    Parameters
    ----------
    stack_path : `str`
        The stack the step tried to solve.
    drivers : `object`
        The outside services passed along.

    Raises
    ------
    AssertionError
        Always: the step must not solve this stack.
    """
    raise AssertionError(f"{stack_path} was solved")


def _solve_to(
    wcs: WCS | None, reason: str = "", calls: list[str] | None = None
) -> Callable[[str, object], tuple[WCS | None, str, str]]:
    """Make a stand-in solve that gives a fixed answer.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS` or `None`
        The solution to give.
    reason : `str`, optional
        Why there is none.
    calls : `list` [`str`], optional
        Collects the stacks the stand-in was asked to solve.

    Returns
    -------
    solve : `Callable`
        A function with the signature of `_plate_solve_stack_wcs`.
    """

    def solve(stack_path: str, drivers: object) -> tuple[WCS | None, str, str]:
        """Give the fixed answer and remember the call.

        Parameters
        ----------
        stack_path : `str`
            The stack to solve.
        drivers : `object`
            The outside services passed along.

        Returns
        -------
        answer : `tuple`
            The solution, the reason and the source.
        """
        if calls is not None:
            calls.append(stack_path)
        return wcs, reason, PLATE_SOLVE_UNREFINED_WCS_SOURCE

    return solve


def _solved_wcs() -> WCS:
    """Make a simple sky map like the one a plate solve gives.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        A tangent-plane map near M 31 at about 1.9 arcsec per pixel.
    """
    wcs = tan_sky_map(10.68, 41.27, (32.0, 32.0), cd=[[-0.00053, 0.0], [0.0, 0.00053]])
    return wcs


def _write_stack(path: Path, header: fits.Header | None = None) -> str:
    """Write a small stand-in stack.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write it.
    header : `astropy.io.fits.Header`, optional
        Header cards to start from.

    Returns
    -------
    path : `str`
        The file written.
    """
    fits.writeto(path, np.zeros((64, 64), dtype=np.float32), header=header, overwrite=True)
    return str(path)


def _run(has_spectral: bool = False) -> SimpleNamespace:
    """Make the parts of a stacking run the step reads.

    Parameters
    ----------
    has_spectral : `bool`, optional
        Whether the run made a spectral stack.

    Returns
    -------
    run : `types.SimpleNamespace`
        A stand-in for `StackRun`.
    """
    return SimpleNamespace(has_spectral=has_spectral, drivers=None)


def test_unsolved_stack_gets_its_solution_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stack with no solution is solved once and marked as plate-solved."""
    path = _write_stack(tmp_path / "stack.fits")
    calls: list[str] = []
    monkeypatch.setattr(stack_noise, "_plate_solve_stack_wcs", _solve_to(_solved_wcs(), calls=calls))
    stage._save_stack_plate_solution(_run(), path)

    header = fits.getheader(path, memmap=False)
    assert calls == [path]
    assert header_is_plate_solved(header)
    assert header["CTYPE1"].startswith("RA---TAN")


def test_already_solved_stack_is_not_solved_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stack that carries a plate solution is left as it is."""
    header = _solved_wcs().to_header()
    header["PLTSOLVD"] = True
    path = _write_stack(tmp_path / "stack.fits", header)
    monkeypatch.setattr(stack_noise, "_plate_solve_stack_wcs", _never_solve)
    stage._save_stack_plate_solution(_run(), path)


def test_pointing_only_header_is_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A header with only the commanded pointing is not a solution."""
    pointing = _solved_wcs()
    pointing.wcs.crval = [10.80, 41.30]
    path = _write_stack(tmp_path / "stack.fits", pointing.to_header())
    monkeypatch.setattr(stack_noise, "_plate_solve_stack_wcs", _solve_to(_solved_wcs()))
    stage._save_stack_plate_solution(_run(), path)

    header = fits.getheader(path, memmap=False)
    assert header_is_plate_solved(header)
    assert header["CRVAL1"] == pytest.approx(10.68)


def test_failed_solve_changes_nothing_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed solve leaves the stack alone and logs why."""
    path = _write_stack(tmp_path / "stack.fits")
    monkeypatch.setattr(stack_noise, "_plate_solve_stack_wcs", _solve_to(None, _NO_SOLUTION))
    with caplog.at_level(logging.WARNING):
        stage._save_stack_plate_solution(_run(), path)

    assert not header_is_plate_solved(fits.getheader(path, memmap=False))
    assert "found no solution" in caplog.text


def test_spectral_stack_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A spectral stack has no star field, so no solve is tried."""
    path = _write_stack(tmp_path / "stack.fits")
    monkeypatch.setattr(stack_noise, "_plate_solve_stack_wcs", _never_solve)
    stage._save_stack_plate_solution(_run(has_spectral=True), path)


def test_viewer_copy_gets_the_solution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The stretched FITS the viewer shows gets the same solution."""
    path = _write_stack(tmp_path / "stack.fits")
    processed = _write_stack(tmp_path / "stack_processed.fits")
    monkeypatch.setattr(stack_noise, "_plate_solve_stack_wcs", _solve_to(_solved_wcs()))
    stage._save_stack_plate_solution(_run(), path)

    header = fits.getheader(processed, memmap=False)
    assert header_is_plate_solved(header)
    assert header["CRVAL2"] == pytest.approx(41.27)


def test_solved_stack_fills_an_unsolved_viewer_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A solved stack's solution reaches its viewer copy with no new solve."""
    header = _solved_wcs().to_header()
    header["PLTSOLVD"] = True
    path = _write_stack(tmp_path / "stack.fits", header)
    processed = _write_stack(tmp_path / "stack_processed.fits")
    monkeypatch.setattr(stack_noise, "_plate_solve_stack_wcs", _never_solve)
    stage._save_stack_plate_solution(_run(), path)

    assert header_is_plate_solved(fits.getheader(processed, memmap=False))


def test_viewer_copy_of_another_size_is_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A viewer copy on a different pixel grid does not get the solution."""
    path = _write_stack(tmp_path / "stack.fits")
    processed = tmp_path / "stack_processed.fits"
    fits.writeto(processed, np.zeros((32, 32), dtype=np.float32))
    monkeypatch.setattr(stack_noise, "_plate_solve_stack_wcs", _solve_to(_solved_wcs()))
    stage._save_stack_plate_solution(_run(), path)

    assert not header_is_plate_solved(fits.getheader(processed, memmap=False))


def test_no_stack_path_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run with no stack (Siril made none) tries no solve."""
    monkeypatch.setattr(stack_noise, "_plate_solve_stack_wcs", _never_solve)
    stage._save_stack_plate_solution(_run(), None)


def test_cropped_viewer_copy_gets_the_solution_moved_by_its_crop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cropped picture gives a star the same sky as the stack does."""
    header = _solved_wcs().to_header()
    header["PLTSOLVD"] = True
    path = _write_stack(tmp_path / "stack.fits", header)
    crop_cards = fits.Header()
    crop_cards["CROPX0"], crop_cards["CROPY0"], crop_cards["CROPW"], crop_cards["CROPH"] = 8, 6, 40, 36
    processed = tmp_path / "stack_processed.fits"
    fits.writeto(processed, np.zeros((36, 40), dtype=np.float32), crop_cards)
    monkeypatch.setattr(stack_noise, "_plate_solve_stack_wcs", _never_solve)

    stage._save_stack_plate_solution(_run(), path)

    picture_header = fits.getheader(processed, memmap=False)
    assert header_is_plate_solved(picture_header)
    on_stack = _solved_wcs().all_pix2world([[20.0, 25.0]], 0)
    on_picture = WCS(picture_header).celestial.all_pix2world([[20.0 - 8, 25.0 - 6]], 0)
    assert np.allclose(on_stack, on_picture, atol=1e-9)
    # The star overlay reads the picture's own size and sky map, so a star
    # lands on the picture pixel that is its stack pixel minus the crop.
    from astrometricslib.pipelines.shared.star_overlay import _project, _read_reference_geometry

    width, height, picture_wcs, _ = _read_reference_geometry(str(processed))
    assert (width, height) == (40, 36)
    ra, dec = on_stack[0]
    column, row = _project(picture_wcs, ra, dec)
    assert (column, row) == pytest.approx((20.0 - 8, 25.0 - 6), abs=1e-6)


def _refit_wcs() -> WCS:
    """Make a sky map that differs a little from `_solved_wcs`.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The same map moved 2 arcseconds in declination.
    """
    wcs = _solved_wcs()
    wcs.wcs.crval = [10.68, 41.27 + 2.0 / 3600.0]
    return wcs


def _solved_stack_header(refit: bool) -> fits.Header:
    """Make the header of a plate-solved stack.

    Parameters
    ----------
    refit : `bool`
        Whether the header says the solution was refit to Gaia.

    Returns
    -------
    header : `astropy.io.fits.Header`
        The sky map, the plate-solved card and the refit card.
    """
    header = _solved_wcs().to_header()
    header["PLTSOLVD"] = True
    header["WCSREFIT"] = refit
    return header


def test_a_refit_solution_replaces_an_unrefined_one_in_the_stack_and_its_copy(tmp_path: Path) -> None:
    """An analysis refit is saved in the stack and its copy, and marked."""
    path = _write_stack(tmp_path / "stack.fits", _solved_stack_header(refit=False))
    processed = _write_stack(tmp_path / "stack_processed.fits", _solved_stack_header(refit=False))

    saved = stage.save_refit_stack_plate_solution(path, _refit_wcs())

    assert saved
    for file_path in (path, processed):
        header = fits.getheader(file_path, memmap=False)
        assert header["WCSREFIT"] is True
        assert header["CRVAL2"] == pytest.approx(41.27 + 2.0 / 3600.0)


def test_a_stack_that_is_already_refit_is_left_alone(tmp_path: Path) -> None:
    """A refined solution is kept, and the viewer copy is not touched."""
    path = _write_stack(tmp_path / "stack.fits", _solved_stack_header(refit=True))
    processed = _write_stack(tmp_path / "stack_processed.fits", _solved_stack_header(refit=False))

    saved = stage.save_refit_stack_plate_solution(path, _refit_wcs())

    assert not saved
    assert fits.getheader(path, memmap=False)["CRVAL2"] == pytest.approx(41.27)
    assert fits.getheader(processed, memmap=False)["CRVAL2"] == pytest.approx(41.27)


@pytest.mark.parametrize(("source_is_refit"), [True, False])
def test_the_stacks_own_solve_records_whether_it_was_refit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_is_refit: bool
) -> None:
    """The header card follows the source the stack's solve reports."""
    from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
        PLATE_SOLVE_REFIT_WCS_SOURCE,
    )

    source = PLATE_SOLVE_REFIT_WCS_SOURCE if source_is_refit else PLATE_SOLVE_UNREFINED_WCS_SOURCE
    path = _write_stack(tmp_path / "stack.fits")
    monkeypatch.setattr(
        stack_noise, "_plate_solve_stack_wcs", lambda stack_path, drivers: (_solved_wcs(), "", source)
    )

    stage._save_stack_plate_solution(_run(), path)

    assert fits.getheader(path, memmap=False)["WCSREFIT"] is source_is_refit


def test_the_analysis_run_saves_an_accepted_refit_over_an_unrefined_solution(tmp_path: Path) -> None:
    """A refit is saved; a later solver-only solution does not replace it."""
    from astrometricslib.pipelines.astrometry import runner

    path = _write_stack(tmp_path / "stack.fits", _solved_stack_header(refit=False))
    runner._write_solved_wcs_to_fits_header(path, SimpleNamespace(wcs=_refit_wcs()), True)
    header = fits.getheader(path, memmap=False)
    assert header["WCSREFIT"] is True
    assert header["CRVAL2"] == pytest.approx(41.27 + 2.0 / 3600.0)

    runner._write_solved_wcs_to_fits_header(path, SimpleNamespace(wcs=_solved_wcs()), False)
    runner._write_solved_wcs_to_fits_header(path, SimpleNamespace(wcs=_solved_wcs()), True)
    header = fits.getheader(path, memmap=False)
    assert header["CRVAL2"] == pytest.approx(41.27 + 2.0 / 3600.0)


def test_the_analysis_run_still_saves_a_solver_solution_over_an_unrefined_one(tmp_path: Path) -> None:
    """With no refit on file, a solver-only solution is written as before."""
    from astrometricslib.pipelines.astrometry import runner

    path = _write_stack(tmp_path / "stack.fits", _solved_stack_header(refit=False))
    runner._write_solved_wcs_to_fits_header(path, SimpleNamespace(wcs=_refit_wcs()), False)

    header = fits.getheader(path, memmap=False)
    assert header["CRVAL2"] == pytest.approx(41.27 + 2.0 / 3600.0)
    assert header["WCSREFIT"] is False


def _identification_inputs(
    gaia_stars_match: bool,
) -> tuple[WCS, list[SimpleNamespace], object, object, fits.Header]:
    """Build the inputs of the identify step's refit.

    Parameters
    ----------
    gaia_stars_match : `bool`
        True for Gaia stars at the detected stars' places; False for stars
        that are nowhere near them.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        A solution 2.7 arcseconds from the truth.
    detections : `list` [`types.SimpleNamespace`]
        Detections with 1-based ``x`` and ``y``.
    gaia_table : `astropy.table.Table`
        The Gaia stars, all of magnitude 12.
    gaia_coords : `astropy.coordinates.SkyCoord`
        Their positions.
    header : `astropy.io.fits.Header`
        A header with the image size.
    """
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from astropy.table import Table

    from astrometricslib.pipelines.astrometry.test.processing.test_wcs_refinement import (
        IMAGE_SHAPE,
        alnath_like_solution,
        make_field,
    )

    measured, gaia, _ = make_field()
    if not gaia_stars_match:
        gaia = SkyCoord((gaia.ra.deg + 0.5) * u.deg, (gaia.dec.deg + 0.5) * u.deg)
    detections = [SimpleNamespace(x=float(x) + 1.0, y=float(y) + 1.0) for x, y in measured]
    table = Table({"phot_g_mean_mag": np.full(len(gaia), 12.0)})
    header = fits.Header()
    header["NAXIS1"], header["NAXIS2"] = IMAGE_SHAPE[1], IMAGE_SHAPE[0]
    return alnath_like_solution(), detections, table, gaia, header


def test_the_identify_step_refits_and_saves_an_unrefined_stack(tmp_path: Path) -> None:
    """With complete Gaia stars, the saved solution is the better one."""
    from astrometricslib.pipelines.astrometry.processing.stack_identification import _refit_and_save_solution

    wcs, detections, table, gaia, header = _identification_inputs(gaia_stars_match=True)
    path = _write_stack(tmp_path / "stack.fits", _solved_stack_header(refit=False))
    notes: list[str] = []

    refit = _refit_and_save_solution(path, wcs, header, detections, table, gaia, 18.0, notes)

    assert refit is not wcs
    assert fits.getheader(path, memmap=False)["WCSREFIT"] is True
    assert len(notes) == 1 and "refit" in notes[0]


def test_the_identify_step_leaves_a_refit_stack_alone(tmp_path: Path) -> None:
    """A header marked as refit is not refit or written again."""
    from astrometricslib.pipelines.astrometry.processing.stack_identification import _refit_and_save_solution

    wcs, detections, table, gaia, header = _identification_inputs(gaia_stars_match=True)
    header["WCSREFIT"] = True
    path = _write_stack(tmp_path / "stack.fits", _solved_stack_header(refit=True))
    before = Path(path).read_bytes()
    notes: list[str] = []

    refit = _refit_and_save_solution(path, wcs, header, detections, table, gaia, 18.0, notes)

    assert refit is wcs
    assert Path(path).read_bytes() == before
    assert notes == []


def test_the_identify_step_saves_nothing_when_the_refit_is_rejected(tmp_path: Path) -> None:
    """A refit that is not accepted saves nothing."""
    from astrometricslib.pipelines.astrometry.processing.stack_identification import _refit_and_save_solution

    wcs, detections, table, gaia, header = _identification_inputs(gaia_stars_match=False)
    path = _write_stack(tmp_path / "stack.fits", _solved_stack_header(refit=False))
    before = Path(path).read_bytes()
    notes: list[str] = []

    refit = _refit_and_save_solution(path, wcs, header, detections, table, gaia, 18.0, notes)

    assert refit is wcs
    assert Path(path).read_bytes() == before
    assert notes == []
