"""Purpose: Unit tests for compute_pointing_correction.

Description: Verifies zero correction for a matching solve, a correctly
signed correction for a known offset, and that convergence is judged
against the configured tolerance -- the cases
`Wayfinding_Library_Architecture.md` calls out.
"""

import pytest

from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.models.session.telemetry import MountPointingModel
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


def test_no_pointing_model_leaves_prediction_fields_none():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify omitting pointing_model reproduces the pre-M7b output exactly."""
    config = CorrectionConfig()
    correction = compute_pointing_correction("frame-4", 180.0, 0.0, 180.1, 0.0, iteration=1, config=config)
    assert correction.model_predicted_error_arcsec is None
    assert correction.unexplained_residual_arcsec is None


def test_insufficient_data_model_is_treated_as_no_model():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an unfit model (confidence='insufficient_data') is skipped."""
    unfit_model = MountPointingModel(
        sample_count=2, raw_rms_arcsec=10.0, residual_rms_arcsec=10.0, confidence="insufficient_data"
    )
    config = CorrectionConfig(alignment_convergence_tolerance_arcsec=5.0)

    correction = compute_pointing_correction(
        "frame-5", 180.0, 0.0, 180.1, 0.0, iteration=1, config=config, pointing_model=unfit_model
    )

    assert correction.model_predicted_error_arcsec is None
    assert correction.unexplained_residual_arcsec is None
    assert correction.converged is False


def test_error_fully_explained_by_model_converges_despite_large_raw_error():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a known, model-explained bias converges on its residual.

    An 8 arcsec Dec offset exceeds the 5 arcsec tolerance on its own,
    but a model whose only nonzero term is a matching 8 arcsec Dec
    index error (`id_arcsec`) predicts exactly this offset at every
    position -- the *unexplained* residual is ~0, so this is a known,
    stable bias rather than a real pointing failure still needing
    correction.
    """
    dec_index_error_arcsec = 8.0
    model = MountPointingModel(
        sample_count=10,
        raw_rms_arcsec=8.0,
        residual_rms_arcsec=0.0,
        id_arcsec=dec_index_error_arcsec,
        confidence="high",
    )
    config = CorrectionConfig(alignment_convergence_tolerance_arcsec=5.0)
    solved_dec_deg = 0.0 - dec_index_error_arcsec / 3600.0

    correction = compute_pointing_correction(
        "frame-6",
        180.0,
        0.0,
        180.0,
        solved_dec_deg,
        iteration=1,
        config=config,
        pointing_model=model,
    )

    assert correction.pointing_error_arcsec == pytest.approx(dec_index_error_arcsec, rel=1e-3)
    assert correction.unexplained_residual_arcsec == pytest.approx(0.0, abs=1e-6)
    assert correction.converged is True


def test_unmodeled_error_does_not_converge_even_with_a_model():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a genuinely new error a zero-coefficient model can't explain.

    still fails to converge -- the model predicts no correction, so the
    unexplained residual equals the raw measured error.
    """
    zero_model = MountPointingModel(
        sample_count=10, raw_rms_arcsec=0.0, residual_rms_arcsec=0.0, confidence="high"
    )
    config = CorrectionConfig(alignment_convergence_tolerance_arcsec=5.0)

    correction = compute_pointing_correction(
        "frame-7", 180.0, 0.0, 180.1, 0.0, iteration=1, config=config, pointing_model=zero_model
    )

    assert correction.model_predicted_error_arcsec == pytest.approx(0.0, abs=1e-6)
    assert correction.unexplained_residual_arcsec == pytest.approx(correction.pointing_error_arcsec, rel=1e-3)
    assert correction.converged is False
