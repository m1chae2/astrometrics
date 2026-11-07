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

from astrometricslib.pipelines.shared.session_identification import header_is_plate_solved
from astrometricslib.pipelines.stacking import stage
from astrometricslib.pipelines.stacking.post_processing import stack_noise
from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
    PLATE_SOLVE_UNREFINED_WCS_SOURCE,
)

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
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [10.68, 41.27]
    wcs.wcs.crpix = [32.0, 32.0]
    wcs.wcs.cd = [[-0.00053, 0.0], [0.0, 0.00053]]
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
