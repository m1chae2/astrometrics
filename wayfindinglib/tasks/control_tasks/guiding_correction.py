"""Purpose: Autoguiding Pulse Correction.

Description: `compute_guiding_correction` per
`Wayfinding_Library_Architecture.md` §2.5.4 -- a pure function of a
guide-star pixel drift (astrometricslib's centroid measurement,
frame-only and therefore science-side per the litmus test) and the
active `GuiderCalibration`, returning signed per-axis pulse durations
via one clearly stated control law (Eq. 2) rather than a tuned
multi-mode controller. Issues nothing: sending the pulse through
`pulse_guide` is a separate, delegation-gated step (§2.5.9, "Corrections
Are Pure").
"""

import math

from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.models.session.correction_result import GuidingCorrection


def _rotate_by_camera_angle(x_px: float, y_px: float, camera_angle_deg: float) -> tuple[float, float]:
    """Rotate a pixel-space displacement into the mount's RA/Dec axes.

    The guide camera's sensor is generally not exactly aligned with
    the mount's RA/Dec axes; `camera_angle_deg` is the measured
    rotation between them.

    Returns
    -------
    ra_px, dec_px : `tuple` [`float`, `float`]
        The displacement rotated onto the RA and Dec axes.
    """
    angle_rad = math.radians(camera_angle_deg)
    cos_theta, sin_theta = math.cos(angle_rad), math.sin(angle_rad)
    ra_px = x_px * cos_theta - y_px * sin_theta
    dec_px = x_px * sin_theta + y_px * cos_theta
    return ra_px, dec_px


def _signed_pulse_ms(
    drift_arcsec: float, rate_arcsec_per_sec: float, aggressiveness: float, max_pulse_ms: int
) -> tuple[int, bool]:
    """Compute one axis's signed, clamped guide-pulse duration.

    Returns
    -------
    signed_pulse_ms, clamped : `tuple` [`int`, `bool`]
        The signed pulse duration in milliseconds, and whether the
        unclamped magnitude exceeded `max_pulse_ms`.
    """
    magnitude_ms = (aggressiveness * abs(drift_arcsec) / rate_arcsec_per_sec) * 1000.0
    clamped = magnitude_ms > max_pulse_ms
    magnitude_ms = min(magnitude_ms, float(max_pulse_ms))
    signed_ms = magnitude_ms if drift_arcsec >= 0.0 else -magnitude_ms
    return round(signed_ms), clamped


def compute_guiding_correction(
    comparison_input_id: str,
    drift_x_px: float,
    drift_y_px: float,
    calibration: GuiderCalibration,
    config: CorrectionConfig,
) -> GuidingCorrection:
    """Compute a signed per-axis guiding pulse from a measured pixel drift.

    Parameters
    ----------
    comparison_input_id : `str`
        Identifier of the shared measurement (the guide frame) this
        correction was computed from, for divergence pairing.
    drift_x_px, drift_y_px : `float`
        The guide star's measured pixel displacement from its
        expected position (astrometricslib's centroid measurement).
    calibration : `GuiderCalibration`
        The measured pixel-to-mount-motion relationship for the
        active camera/telescope pairing.
    config : `CorrectionConfig`
        Supplies `guiding_aggressiveness`, `guiding_deadband_arcsec`,
        and `guiding_max_pulse_ms`.

    Returns
    -------
    correction : `GuidingCorrection`
        Signed per-axis pulse durations. Both are zero and
        `suppressed_by_deadband` is `True` when both axes' drift falls
        within the deadband -- distinguishing "agreed to do nothing"
        from a pulse that was merely computed as zero. Issues nothing.
    """
    ra_px, dec_px = _rotate_by_camera_angle(drift_x_px, drift_y_px, calibration.camera_angle_deg)
    drift_ra_arcsec = ra_px * calibration.arcsec_per_pixel
    drift_dec_arcsec = dec_px * calibration.arcsec_per_pixel

    ra_within_deadband = abs(drift_ra_arcsec) < config.guiding_deadband_arcsec
    dec_within_deadband = abs(drift_dec_arcsec) < config.guiding_deadband_arcsec
    suppressed_by_deadband = ra_within_deadband and dec_within_deadband

    if suppressed_by_deadband:
        return GuidingCorrection(
            comparison_input_id=comparison_input_id,
            drift_ra_arcsec=drift_ra_arcsec,
            drift_dec_arcsec=drift_dec_arcsec,
            pulse_ra_ms=0,
            pulse_dec_ms=0,
            aggressiveness_applied=config.guiding_aggressiveness,
            suppressed_by_deadband=True,
            clamped_by_max_move=False,
        )

    if ra_within_deadband:
        pulse_ra_ms, ra_clamped = 0, False
    else:
        pulse_ra_ms, ra_clamped = _signed_pulse_ms(
            drift_ra_arcsec,
            calibration.ra_rate_arcsec_per_sec,
            config.guiding_aggressiveness,
            config.guiding_max_pulse_ms,
        )

    if dec_within_deadband:
        pulse_dec_ms, dec_clamped = 0, False
    else:
        pulse_dec_ms, dec_clamped = _signed_pulse_ms(
            drift_dec_arcsec,
            calibration.dec_rate_arcsec_per_sec,
            config.guiding_aggressiveness,
            config.guiding_max_pulse_ms,
        )

    return GuidingCorrection(
        comparison_input_id=comparison_input_id,
        drift_ra_arcsec=drift_ra_arcsec,
        drift_dec_arcsec=drift_dec_arcsec,
        pulse_ra_ms=pulse_ra_ms,
        pulse_dec_ms=pulse_dec_ms,
        aggressiveness_applied=config.guiding_aggressiveness,
        suppressed_by_deadband=False,
        clamped_by_max_move=ra_clamped or dec_clamped,
    )
