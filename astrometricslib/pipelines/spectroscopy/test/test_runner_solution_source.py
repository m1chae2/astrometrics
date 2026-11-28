"""Purpose: Unit tests for the plate solution the spectroscopy adapter reads.

Description: Spectroscopy never starts an astrometry run. It names a
spectral stack's stars with the solution of the target's solved imaging
stack of the same camera and optic, shifted onto the spectral stack, else
with the spectral stack's own solution when astrometry solved that stack.
These tests run `SpectroscopyPipelineAdapter.run` with the star detection,
the reference stars and the extraction replaced by stand-ins, and check
which solution names the stars and which position hint the detection gets.
The case with no solution at all keeps the older geometry match: the stage
runner skips it, so only a direct call reaches it.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.target import Target
from astrometricslib.pipelines.pipeline_base import PipelineRequest, Result
from astrometricslib.pipelines.spectroscopy import runner
from astrometricslib.pipelines.spectroscopy.pre_processing import spectral_star_registration

CAMERA = "ZWO CCD ASI533MM Pro"
IMAGING_CENTER = (279.2414, 38.7530)
SPECTRAL_CENTER = (279.3000, 38.8000)


def _write_stack(path: Path, center: tuple[float, float] | None, *, plate_solved_card: bool = True) -> str:
    """Write a small stack of the test camera and optic.

    Parameters
    ----------
    path : `Path`
        Where to write it.
    center : `tuple` [`float`, `float`] or `None`
        The solved centre, or `None` for a stack with no solution.
    plate_solved_card : `bool`, optional
        Whether to write the "plate solved" card astrometry writes.

    Returns
    -------
    path : `str`
        The file written.
    """
    header = fits.Header()
    header["INSTRUME"] = CAMERA
    header["FOCALLEN"] = 405.0
    if center is not None:
        # The reference pixel is the image centre, so the centre coordinate
        # is exactly CRVAL.
        header["CTYPE1"], header["CTYPE2"] = "RA---TAN", "DEC--TAN"
        header["CRPIX1"], header["CRPIX2"] = 50.0, 50.0
        header["CRVAL1"], header["CRVAL2"] = center
        header["CD1_1"], header["CD1_2"], header["CD2_1"], header["CD2_2"] = -5e-4, 0.0, 0.0, 5e-4
        if plate_solved_card:
            header["PLTSOLVD"] = True
    fits.PrimaryHDU(np.zeros((100, 100), dtype=np.float32), header=header).writeto(path)
    return str(path)


class _Calls:
    """What the stand-ins were asked to do."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.process_hint: tuple[Any, Any] | None = None
        self.solution_calls: list[dict] = []
        self.sky_map_calls = 0
        self.extra_stack_paths: tuple[str, ...] | None = None


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> _Calls:
    """Replace detection, reference stars, naming and extraction.

    Returns
    -------
    calls : `_Calls`
        The record of what was asked.
    """
    recorded = _Calls()

    class _StandInAstrometry:
        """Stands in for the blind-detection pass."""

        def __init__(self, drivers: Any = None) -> None:
            """Take no drivers."""

        def process(self, path: str, **kwargs: Any) -> SimpleNamespace:
            """Record the hint and return an empty context.

            Returns
            -------
            context : `SimpleNamespace`
                A context with no stars and no extended-source hint.
            """
            recorded.process_hint = (kwargs.get("target_ra"), kwargs.get("target_dec"))
            return SimpleNamespace(stellar_objects=[], extended_source_hint="kept", image=None)

        def build_extended_source_hint(self, *args: Any) -> None:
            """Build nothing."""

    class _StandInSpectroscopy:
        """Stands in for the extraction."""

        def __init__(self, drivers: Any = None) -> None:
            """Take no drivers."""

        def process(self, context: Any, limit: Any = None) -> list:
            """Extract nothing.

            Returns
            -------
            stars : `list`
                Empty.
            """
            return []

    def identify_via_solution(spectral: list, reference: list, wcs: Any, **kwargs: Any) -> int:
        """Record a naming from a solution.

        Returns
        -------
        matched : `int`
            One named star.
        """
        recorded.solution_calls.append({"wcs": wcs, **kwargs})
        return 1

    def identify_via_sky_map(spectral: list, reference: list) -> tuple[int, None]:
        """Record a geometry match.

        Returns
        -------
        result : `tuple`
            No match and no map.
        """
        recorded.sky_map_calls += 1
        return 0, None

    def reference_candidates(
        target: Target, catalog_access: Any, field_center: Any = None, extra_stack_paths: tuple = ()
    ) -> list:
        """Give one reference star and record the extra stacks read.

        Returns
        -------
        stars : `list`
            One stand-in star.
        """
        recorded.extra_stack_paths = tuple(extra_stack_paths)
        return [SimpleNamespace(id="* alf Lyr")]

    monkeypatch.setattr(
        "astrometricslib.pipelines.astrometry.pipeline.AstrometryPipeline", _StandInAstrometry
    )
    monkeypatch.setattr(
        "astrometricslib.pipelines.spectroscopy.pipeline.SpectroscopyPipeline", _StandInSpectroscopy
    )
    monkeypatch.setattr(
        spectral_star_registration, "identify_spectral_stars_via_solution", identify_via_solution
    )
    monkeypatch.setattr(
        spectral_star_registration, "identify_spectral_stars_via_registered_sky_map", identify_via_sky_map
    )
    monkeypatch.setattr(
        spectral_star_registration, "solution_shifted_onto_spectral_image", lambda *_args: None
    )
    monkeypatch.setattr(runner, "_registration_reference_candidates", reference_candidates)
    monkeypatch.setattr(runner, "_recover_extended_source_hint", lambda *_args: None)
    monkeypatch.setattr(
        runner, "record_and_flag_spectroscopy_stars", lambda *_args, **_kwargs: ([], None, [])
    )
    return recorded


def _run(target: Target, spectral_path: str) -> None:
    """Run the adapter's main stage on a spectral stack.

    Parameters
    ----------
    target : `Target`
        The target.
    spectral_path : `str`
        The spectral stack.
    """
    request = PipelineRequest(target=target, catalog_access=None, path=spectral_path, options={})
    runner.SpectroscopyPipelineAdapter().run(request, Result())


def test_the_solved_imaging_stack_names_the_stars_first(tmp_path: Path, calls: _Calls) -> None:
    """Verify the borrowed solution wins, even over the stack's own."""
    imaging = _write_stack(tmp_path / "imaging.fits", IMAGING_CENTER)
    spectral = _write_stack(tmp_path / "spectral.fits", SPECTRAL_CENTER)
    target = Target(id="Vega", stacked_image=imaging, stacked_spectral_target=spectral)

    _run(target, spectral)

    assert calls.process_hint == pytest.approx(IMAGING_CENTER, abs=0.01)
    assert len(calls.solution_calls) == 1
    assert not calls.solution_calls[0].get("solution_fits_this_image", False)
    assert calls.solution_calls[0]["wcs"].wcs.crval[0] == pytest.approx(IMAGING_CENTER[0])
    assert calls.extra_stack_paths == ()


def test_the_spectral_stacks_own_solution_names_the_stars_next(tmp_path: Path, calls: _Calls) -> None:
    """Verify a solved spectral stack is read as it is, with no shift."""
    imaging = _write_stack(tmp_path / "imaging.fits", None)
    spectral = _write_stack(tmp_path / "spectral.fits", SPECTRAL_CENTER)
    target = Target(id="Vega", stacked_image=imaging, stacked_spectral_target=spectral)

    _run(target, spectral)

    assert calls.process_hint == pytest.approx(SPECTRAL_CENTER, abs=0.01)
    assert len(calls.solution_calls) == 1
    assert calls.solution_calls[0]["solution_fits_this_image"] is True
    assert calls.solution_calls[0]["wcs"].wcs.crval[0] == pytest.approx(SPECTRAL_CENTER[0])
    assert calls.sky_map_calls == 0
    assert calls.extra_stack_paths == (spectral,)


def test_a_mount_sky_map_is_not_taken_for_a_solution(tmp_path: Path, calls: _Calls) -> None:
    """Verify a sky map without the plate-solved card does not count."""
    spectral = _write_stack(tmp_path / "spectral.fits", SPECTRAL_CENTER, plate_solved_card=False)
    target = Target(id="Vega", stacked_spectral_target=spectral)

    _run(target, spectral)

    assert calls.solution_calls == []
    assert calls.extra_stack_paths == ()


def test_with_no_solution_a_direct_call_keeps_the_geometry_match(tmp_path: Path, calls: _Calls) -> None:
    """Verify the geometry match stays for a direct call with no solution."""
    spectral = _write_stack(tmp_path / "spectral.fits", None)
    target = Target(id="Vega", stacked_spectral_target=spectral)

    _run(target, spectral)

    assert calls.solution_calls == []
    assert calls.sky_map_calls == 1
