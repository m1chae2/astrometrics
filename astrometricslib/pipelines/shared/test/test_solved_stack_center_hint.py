"""Purpose: Unit tests for the spectral position hint from a solved stack.

Description: A spectral stack's FITS position is only the mount's report and
can be tens of arcminutes wrong. On the Vega session it labelled Vega's zero
order as a magnitude-10.6 neighbour. These tests pin the fix: the hint is the
centre of the target's plate-solved stack for the same camera and telescope,
and nothing is guessed when there is none.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.shared.target_center_hint import (
    resolve_solved_stack_center_hint,
    resolve_solved_stack_wcs,
)

CAMERA = "ZWO CCD ASI533MM Pro"


def _write_stack(path: Path, focal_length: float = 405.0, camera: str = CAMERA, solved: bool = True) -> str:
    """Write a small stack with optional celestial WCS keywords.

    Returns
    -------
    path : `str`
        The file written.
    """
    header = fits.Header()
    header["INSTRUME"] = camera
    header["FOCALLEN"] = focal_length
    if solved:
        # The reference pixel is the image centre, so the centre coordinate
        # is exactly CRVAL.
        header["CTYPE1"], header["CTYPE2"] = "RA---TAN", "DEC--TAN"
        header["CRPIX1"], header["CRPIX2"] = 50.0, 50.0
        header["CRVAL1"], header["CRVAL2"] = 279.2414, 38.7530
        header["CD1_1"], header["CD1_2"], header["CD2_1"], header["CD2_2"] = -5e-4, 0.0, 0.0, 5e-4
    fits.PrimaryHDU(np.zeros((100, 100), dtype=np.float32), header=header).writeto(path)
    return str(path)


def _target(stacked_image: str = "", **configurations: str) -> SimpleNamespace:
    return SimpleNamespace(
        stacked_image=stacked_image,
        stacks_by_configuration={
            key: SimpleNamespace(stacked_image=path) for key, path in configurations.items()
        },
    )


def test_the_hint_is_the_centre_of_the_matching_solved_stack(tmp_path: Path) -> None:
    """The reference pixel is at the centre, so its sky position comes back."""
    spectral = _write_stack(tmp_path / "spec.fits", solved=False)
    solved = _write_stack(tmp_path / "L.fits")

    center_ra, center_dec = resolve_solved_stack_center_hint(_target(solved), spectral)

    assert center_ra == pytest.approx(279.2414, abs=1e-3)
    assert center_dec == pytest.approx(38.7530, abs=1e-3)


def test_a_stack_from_a_different_telescope_is_not_used(tmp_path: Path) -> None:
    """A stack at another focal length has another field, so it is skipped."""
    spectral = _write_stack(tmp_path / "spec.fits", solved=False)
    other_scope = _write_stack(tmp_path / "L300.fits", focal_length=300.0)

    assert resolve_solved_stack_center_hint(_target(other_scope), spectral) == (None, None)


def test_a_stack_from_a_different_camera_is_not_used(tmp_path: Path) -> None:
    """The camera name has to match as well."""
    spectral = _write_stack(tmp_path / "spec.fits", solved=False)
    other_camera = _write_stack(tmp_path / "L.fits", camera="Other Camera")

    assert resolve_solved_stack_center_hint(_target(other_camera), spectral) == (None, None)


def test_an_unsolved_stack_gives_no_hint(tmp_path: Path) -> None:
    """A matching stack without a plate solution cannot give a position."""
    spectral = _write_stack(tmp_path / "spec.fits", solved=False)
    unsolved = _write_stack(tmp_path / "L.fits", solved=False)

    assert resolve_solved_stack_center_hint(_target(unsolved), spectral) == (None, None)


def test_a_configuration_stack_is_found_when_the_main_one_is_missing(tmp_path: Path) -> None:
    """Stacks recorded per configuration are searched too."""
    spectral = _write_stack(tmp_path / "spec.fits", solved=False)
    solved = _write_stack(tmp_path / "L.fits")

    center_ra, _ = resolve_solved_stack_center_hint(
        _target(stacked_image=str(tmp_path / "gone.fits"), config=solved), spectral
    )

    assert center_ra == pytest.approx(279.2414, abs=1e-3)


def test_a_target_with_no_stacks_or_an_unreadable_spectral_file_gives_no_hint(tmp_path: Path) -> None:
    """Missing files never raise; they just leave the caller without a hint."""
    spectral = _write_stack(tmp_path / "spec.fits", solved=False)

    assert resolve_solved_stack_center_hint(_target(), spectral) == (None, None)
    assert resolve_solved_stack_center_hint(_target(), str(tmp_path / "missing.fits")) == (None, None)


def test_the_solved_stacks_plate_solution_is_returned(tmp_path: Path) -> None:
    """The reference pixel of the matching solved stack maps to its CRVAL."""
    spectral = _write_stack(tmp_path / "spec.fits", solved=False)
    solved = _write_stack(tmp_path / "L.fits")

    wcs = resolve_solved_stack_wcs(_target(solved), spectral)

    center_ra, center_dec = wcs.wcs_pix2world(49.0, 49.0, 0)
    assert center_ra == pytest.approx(279.2414, abs=1e-3)
    assert center_dec == pytest.approx(38.7530, abs=1e-3)


def test_no_plate_solution_is_returned_without_a_matching_solved_stack(tmp_path: Path) -> None:
    """Another telescope's solved stack must not be used."""
    spectral = _write_stack(tmp_path / "spec.fits", solved=False)
    other_scope = _write_stack(tmp_path / "L.fits", focal_length=1000.0)

    assert resolve_solved_stack_wcs(_target(other_scope), spectral) is None
