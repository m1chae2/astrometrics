"""Purpose: Unit tests for compute_pointing_correction.

Description: Verifies zero correction for a matching solve, a correctly
signed correction for a known offset, and that convergence is judged
against the configured tolerance -- the cases
`Wayfinding_Library_Architecture.md` §2.5.11 calls out.
"""

import pytest

from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.tasks.control_tasks.pointing_correction import compute_pointing_correction


def test_matching_solve_yields_zero_error_and_converges():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a solve exactly at the commanded position yields zero error."""
    config = CorrectionConfig()
    correction = compute_pointing_correction("frame-1", 180.0, 45.0, 180.0, 45.0, iteration=1, config=config)
    assert correction.pointing_error_arcsec < 1e-6
    assert correction.converged is True


def test_known_ra_offset_yields_signed_correction():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a known RA-only offset yields a correctly signed correction."""
    config = CorrectionConfig(alignment_convergence_tolerance_arcsec=30.0)
    # Solved 10 arcsec east of commanded (at dec=0, cos(dec)=1) -- the mount
    # needs a negative RA correction to move back to the commanded position.
    solved_ra_deg = 180.0 + (10.0 / 3600.0)
    correction = compute_pointing_correction(
        "frame-2", 180.0, 0.0, solved_ra_deg, 0.0, iteration=1, config=config
    )
    assert correction.pointing_error_arcsec == pytest.approx(10.0, rel=1e-3)
    assert correction.correction_ra_arcsec < 0.0
    assert abs(correction.correction_dec_arcsec) < 1e-6


def test_large_offset_does_not_converge():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an offset beyond the tolerance is not marked converged."""
    config = CorrectionConfig(alignment_convergence_tolerance_arcsec=5.0)
    correction = compute_pointing_correction("frame-3", 180.0, 0.0, 180.1, 0.0, iteration=3, config=config)
    assert correction.converged is False
    assert correction.iteration == 3
