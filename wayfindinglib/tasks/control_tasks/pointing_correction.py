"""Purpose: Plate-Solve Pointing Correction.

Description: `compute_pointing_correction` per
`Wayfinding_Library_Architecture.md` -- a pure function of a
commanded position and a plate-solve result (astrometricslib's plate
solve driver, frame-only and therefore science-side per the litmus
test) that returns the angular separation and the per-axis correction
that closes it. Issues nothing: syncing the mount and re-slewing are
a separate, delegation-gated orchestration step layered on top of this
computation, not part of it ("Corrections Are Pure").

The optional `pointing_model` parameter ( the architecture document's
extension, M7b)
tells a genuinely new pointing failure apart from one already explained
by tonight's fitted polar-alignment/index-error model: it predicts the
systematic error `pointing_model` implies at this exact commanded
position (`pointing_model.predict_pointing_error`, the forward
direction of the same geometric fit `run_polar_alignment_assist`
produces) and reports what's left over once that's subtracted out.
`pointing_model` is deliberately never fed forward across sessions --
see `pointing_log_ingestion.py`'s module docstring for why.
"""

import math

from wayfindinglib.analytics.pointing_model import predict_pointing_error
from wayfindinglib.astronomy.coordinate_transforms import (
    angular_separation_arcsec,
    signed_offset_components_arcsec,
)
from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.models.session.correction_result import PointingCorrection
from wayfindinglib.models.session.telemetry import MountPointingModel


def compute_pointing_correction(
    comparison_input_id: str,
    commanded_ra_deg: float,
    commanded_dec_deg: float,
    solved_ra_deg: float,
    solved_dec_deg: float,
    iteration: int,
    config: CorrectionConfig,
    pointing_model: MountPointingModel | None = None,
    latitude_deg: float = 45.0,
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
    pointing_model : `MountPointingModel` | `None`, optional
        Tonight's session-scoped fitted model
        (`control.history.query(kind="pointing_model")`). `None` (default)
        disables the feedforward, reproducing the pre-M7b behavior
        exactly: `converged` is judged against the raw measured error.
        Skipped even when supplied if its `confidence` is
        ``"insufficient_data"`` -- an unfit model predicts nothing.
    latitude_deg : `float`, optional
        Observer latitude in decimal degrees, used only when
        `pointing_model` is supplied, default 45.0.

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

    model_predicted_error_arcsec = None
    unexplained_residual_arcsec = None
    convergence_error_arcsec = pointing_error_arcsec

    if pointing_model is not None and pointing_model.confidence != "insufficient_data":
        measured_delta_ra_arcsec, measured_delta_dec_arcsec = signed_offset_components_arcsec(
            commanded_ra_deg, commanded_dec_deg, solved_ra_deg, solved_dec_deg
        )
        predicted_delta_ra_arcsec, predicted_delta_dec_arcsec = predict_pointing_error(
            commanded_ra_deg, commanded_dec_deg, pointing_model, latitude_deg
        )
        model_predicted_error_arcsec = math.hypot(predicted_delta_ra_arcsec, predicted_delta_dec_arcsec)
        unexplained_residual_arcsec = math.hypot(
            measured_delta_ra_arcsec - predicted_delta_ra_arcsec,
            measured_delta_dec_arcsec - predicted_delta_dec_arcsec,
        )
        convergence_error_arcsec = unexplained_residual_arcsec

    converged = convergence_error_arcsec <= config.alignment_convergence_tolerance_arcsec

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
        model_predicted_error_arcsec=model_predicted_error_arcsec,
        unexplained_residual_arcsec=unexplained_residual_arcsec,
    )
