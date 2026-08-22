"""Purpose: Plate-Solve Pointing Correction.

Description: `compute_pointing_correction` per
`Wayfinding_Library_Architecture.md` §2.5.3 -- a pure function of a
commanded position and a plate-solve result (astrometricslib's
`PlateSolver`, frame-only and therefore science-side per the litmus
test) that returns the angular separation and the per-axis correction
that closes it. Issues nothing: syncing the mount and re-slewing are
a separate, delegation-gated orchestration step layered on top of this
computation, not part of it (§2.5.9, "Corrections Are Pure").
"""

from wayfindinglib.astronomy.coordinate_transforms import (
    angular_separation_arcsec,
    signed_offset_components_arcsec,
)
from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.models.session.correction_result import PointingCorrection


def compute_pointing_correction(
    comparison_input_id: str,
    commanded_ra_deg: float,
    commanded_dec_deg: float,
    solved_ra_deg: float,
    solved_dec_deg: float,
    iteration: int,
    config: CorrectionConfig,
) -> PointingCorrection:
    """Compute one iteration's pointing error and closing correction.

    Parameters
    ----------
    comparison_input_id : `str`
        Identifier of the shared measurement (the plate-solved frame)
        this correction was computed from, for divergence pairing.
    commanded_ra_deg, commanded_dec_deg : `float`
        The position the mount was commanded to.
    solved_ra_deg, solved_dec_deg : `float`
        The position a plate solve of the captured frame found.
    iteration : `int`
        Which alignment iteration this is (1-indexed).
    config : `CorrectionConfig`
        Supplies `alignment_convergence_tolerance_arcsec`.

    Returns
    -------
    correction : `PointingCorrection`
        The measured pointing error and the per-axis correction that
        closes it, with `converged` set per the configured tolerance.
        Issues nothing -- syncing/re-slewing is a separate step.
    """
    pointing_error_arcsec = angular_separation_arcsec(
        commanded_ra_deg, commanded_dec_deg, solved_ra_deg, solved_dec_deg
    )
    correction_ra_arcsec, correction_dec_arcsec = signed_offset_components_arcsec(
        solved_ra_deg, solved_dec_deg, commanded_ra_deg, commanded_dec_deg
    )
    converged = pointing_error_arcsec <= config.alignment_convergence_tolerance_arcsec

    return PointingCorrection(
        comparison_input_id=comparison_input_id,
        commanded_ra_deg=commanded_ra_deg,
        commanded_dec_deg=commanded_dec_deg,
        solved_ra_deg=solved_ra_deg,
        solved_dec_deg=solved_dec_deg,
        pointing_error_arcsec=pointing_error_arcsec,
        correction_ra_arcsec=correction_ra_arcsec,
        correction_dec_arcsec=correction_dec_arcsec,
        iteration=iteration,
        converged=converged,
    )
