"""Tests for surviving a wrong pixel-scale hint during plate solving.

Scale hints come from FOCALLEN/XPIXSZ, and a wrong focal length makes
the hinted window exclude the truth, at which point no amount of good
data can solve the field. Measured on 2026-08-24: the DSLR's "Nikkor
300mm" frames actually resolve at ~404mm, so the computed 2.68
arcsec/px window of 2.03-3.37 excluded M 31's real 1.996 arcsec/px.
Same image, same solver -- constrained did not solve, blind solved in
seconds. Four DSLR targets were lost that way in one run, each with a
perfectly good stack already on disk.
"""

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.tasks.stellar_tasks.astrometry_tasks import plate_solver
from astrometricslib.tasks.stellar_tasks.astrometry_tasks.plate_solver import PlateSolver


@pytest.fixture
def solver():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Return a PlateSolver with no API key, so online paths stay off.

    Returns
    -------
    solver : `PlateSolver`
        Solver instance for local-path tests.
    """
    return PlateSolver()


def test_a_failed_hinted_solve_is_retried_blind(solver, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The regression: a bad hint must not be the end of the attempt."""
    commands = []

    def _fake_run(self, command, working_directory, timeout):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        commands.append(command)
        # Fail while constrained, succeed once the hints are gone.
        if "--scale-low" in command:
            return None
        return fits.Header({"CRVAL1": 10.7, "CRVAL2": 41.27})

    monkeypatch.setattr(PlateSolver, "_run_solve_field", _fake_run)

    header = solver._solve_locally(
        "/nonexistent/image.fits",
        scale_units="arcsecperpix",
        scale_lower=2.54,
        scale_upper=2.81,
    )

    assert header is not None
    assert header["CRVAL1"] == pytest.approx(10.7)
    assert len(commands) == 2
    assert "--scale-low" in commands[0]
    assert "--scale-low" not in commands[1]


def test_position_hints_alone_also_trigger_the_blind_retry(solver, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A wrong RA/Dec constrains the search just as fatally as scale."""
    commands = []

    def _fake_run(self, command, working_directory, timeout):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        commands.append(command)
        return None if "--ra" in command else fits.Header({"CRVAL1": 1.0})

    monkeypatch.setattr(PlateSolver, "_run_solve_field", _fake_run)

    assert solver._solve_locally("/nonexistent/image.fits", center_ra=10.0, center_dec=41.0) is not None
    assert len(commands) == 2


def test_a_successful_hinted_solve_does_not_retry(solver, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Hints are usually right; a second solve would double the cost."""
    calls = []

    def _fake_run(self, command, working_directory, timeout):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        calls.append(command)
        return fits.Header({"CRVAL1": 10.7})

    monkeypatch.setattr(PlateSolver, "_run_solve_field", _fake_run)
    solver._solve_locally(
        "/nonexistent/image.fits", scale_units="arcsecperpix", scale_lower=2.0, scale_upper=2.1
    )

    assert len(calls) == 1


def test_an_unhinted_solve_is_attempted_only_once(solver, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """With no hints to blame, a failure is the field's, not the window's."""
    calls = []

    def _fake_run(self, command, working_directory, timeout):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        calls.append(command)
        return None

    monkeypatch.setattr(PlateSolver, "_run_solve_field", _fake_run)

    assert solver._solve_locally("/nonexistent/image.fits") is None
    assert len(calls) == 1


def test_solve_field_success_requires_the_output_file(solver, tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A zero exit status alone does not mean the field was solved."""

    class _Result:
        returncode = 0

    monkeypatch.setattr(plate_solver.subprocess, "run", lambda *a, **k: _Result())

    assert solver._run_solve_field(["solve-field"], str(tmp_path), 10) is None


def test_a_colour_stack_is_flattened_before_upload(solver, tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """astrometry.net's uploader crashes on a 3D array.

    It raised "weights.ndim (2) must match len(axes) (3)" for every DSLR
    stack, so the online fallback was unusable for exactly the targets
    most likely to need it.
    """
    colour_path = tmp_path / "colour.fits"
    fits.PrimaryHDU(np.ones((3, 8, 6), dtype=np.float32)).writeto(colour_path)
    uploaded = {}

    solver.api_key = "test-key"

    def _fake_solve_from_image(path, **kwargs: object):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        with fits.open(path) as hdul:
            uploaded["ndim"] = hdul[0].data.ndim
        return fits.Header({"CRVAL1": 1.0})

    monkeypatch.setattr(solver.astrometry_net, "solve_from_image", _fake_solve_from_image)

    assert solver._solve_online_image(str(colour_path)) is not None
    assert uploaded["ndim"] == 2


def test_a_mono_stack_is_uploaded_unchanged(solver, tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Monochrome frames must not pay for a rewrite they do not need."""
    mono_path = tmp_path / "mono.fits"
    fits.PrimaryHDU(np.ones((8, 6), dtype=np.float32)).writeto(mono_path)
    uploaded = {}

    solver.api_key = "test-key"

    def _fake_solve_from_image(path, **kwargs: object):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        uploaded["path"] = path
        return fits.Header({"CRVAL1": 1.0})

    monkeypatch.setattr(solver.astrometry_net, "solve_from_image", _fake_solve_from_image)
    solver._solve_online_image(str(mono_path))

    assert uploaded["path"] == str(mono_path)
