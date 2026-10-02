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

The optional `mount_model`/`elapsed_guiding_seconds`/
`dec_direction_reversal` parameters (§2.5.1a's §6a extension, M7b) add
a feedforward component on top of the reactive pulse above, learned
from `guiding_log_ingestion.py`'s persisted, cross-night
`GuidingSpectrumAnalysis`:

- **Periodic error (RA)**: the dominant worm harmonic is modeled as a
  single sinusoid of the measured peak-to-peak amplitude (a
  simplification -- real worm error is rarely a pure sinusoid, but a
  single dominant harmonic is what the spectrum analysis already
  reports, and a fuller multi-harmonic reconstruction is not something
  the existing `GuidingSpectrumAnalysis` model captures). A pulse
  anticipating and canceling the predicted error is issued every call,
  independent of the deadband -- PEC's whole point is correcting
  *before* the error is observed, not after it has already grown large
  enough to clear the reactive deadband.
- **Backlash (Dec)**: on a caller-flagged direction reversal, a
  lead-in pulse of the learned reversal delay is issued in the new
  direction, taking up the mechanical slack before the reactive
  correction has to fight it.

Both remain optional and default to no feedforward (identical output
to the pre-M7b behavior) when `mount_model` is `None`.
"""

import math

from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.models.session.correction_result import GuidingCorrection
from wayfindinglib.models.session.telemetry import GuidingSpectrumAnalysis


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


def _periodic_error_feedforward_ms(
    mount_model: GuidingSpectrumAnalysis,
    elapsed_guiding_seconds: float | None,
    rate_arcsec_per_sec: float,
    aggressiveness: float,
    max_pulse_ms: int,
) -> tuple[int, bool]:
    """Anticipatory RA pulse countering the mount's modeled periodic error.

    Models the dominant worm harmonic as a single sinusoid of the
    measured peak-to-peak amplitude and issues a pulse that cancels
    the predicted error at the current phase of the worm cycle.

    Returns
    -------
    feedforward_ms, clamped : `tuple` [`int`, `bool`]
        The signed feedforward pulse, and whether it was clamped.
        ``(0, False)`` if the model has no usable dominant period or
        `elapsed_guiding_seconds` was not supplied.
    """
    if elapsed_guiding_seconds is None or not mount_model.dominant_period_seconds:
        return 0, False
    if mount_model.periodic_error_peak_to_peak_arcsec <= 0.0:
        return 0, False

    phase = (elapsed_guiding_seconds % mount_model.dominant_period_seconds) / (
        mount_model.dominant_period_seconds
    )
    predicted_error_arcsec = (mount_model.periodic_error_peak_to_peak_arcsec / 2.0) * math.sin(
        2.0 * math.pi * phase
    )
    # Anticipate and cancel the predicted error, not chase it.
    return _signed_pulse_ms(-predicted_error_arcsec, rate_arcsec_per_sec, aggressiveness, max_pulse_ms)


def _backlash_feedforward_ms(
    mount_model: GuidingSpectrumAnalysis, direction_sign: float, max_pulse_ms: int
) -> int:
    """Lead-in Dec pulse taking up backlash slack on a direction reversal.

    Returns
    -------
    feedforward_ms : `int`
        The signed lead-in pulse, `0` if the model has no usable
        backlash estimate.
    """
    if not mount_model.dec_backlash_estimate_ms or mount_model.dec_backlash_estimate_ms <= 0.0:
        return 0
    magnitude_ms = min(mount_model.dec_backlash_estimate_ms, float(max_pulse_ms))
    sign = 1.0 if direction_sign >= 0.0 else -1.0
    return round(sign * magnitude_ms)


def _clamp_to_max_pulse(pulse_ms: int, max_pulse_ms: int) -> tuple[int, bool]:
    """Clamp a combined pulse's magnitude to `max_pulse_ms`.

    Returns
    -------
    clamped_pulse_ms, clamped : `tuple` [`int`, `bool`]
        The clamped signed pulse, and whether clamping changed it.
    """
    if abs(pulse_ms) <= max_pulse_ms:
        return pulse_ms, False
    return (max_pulse_ms if pulse_ms > 0 else -max_pulse_ms), True


def compute_guiding_correction(
    comparison_input_id: str,
    drift_x_px: float,
    drift_y_px: float,
    calibration: GuiderCalibration,
    config: CorrectionConfig,
    mount_model: GuidingSpectrumAnalysis | None = None,
    elapsed_guiding_seconds: float | None = None,
    dec_direction_reversal: bool = False,
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
    mount_model : `GuidingSpectrumAnalysis` | `None`, optional
        The active telescope's persisted, cross-night periodic-error/
        backlash model (`ObservatoryControl.active_guiding_spectrum_analysis`).
        `None` (default) disables all feedforward, reproducing the
        pre-M7b reactive-only behavior exactly.
    elapsed_guiding_seconds : `float` | `None`, optional
        Seconds since this guiding run began, needed to locate the
        current phase of the worm cycle for the periodic-error
        feedforward. No periodic-error feedforward without it.
    dec_direction_reversal : `bool`, optional
        Whether this call's Dec correction reverses the previous
        pulse's direction, triggering the backlash lead-in feedforward.

    Returns
    -------
    correction : `GuidingCorrection`
        Signed per-axis pulse durations (reactive plus any
        feedforward). Both are zero and `suppressed_by_deadband` is
        `True` only when the combined pulse is zero on both axes --
        distinguishing "agreed to do nothing" from a pulse that was
        merely computed as zero. Issues nothing.
    """
    ra_px, dec_px = _rotate_by_camera_angle(drift_x_px, drift_y_px, calibration.camera_angle_deg)
    drift_ra_arcsec = ra_px * calibration.arcsec_per_pixel
    drift_dec_arcsec = dec_px * calibration.arcsec_per_pixel

    ra_within_deadband = abs(drift_ra_arcsec) < config.guiding_deadband_arcsec
    dec_within_deadband = abs(drift_dec_arcsec) < config.guiding_deadband_arcsec

    reactive_ra_ms, ra_clamped = (
        (0, False)
        if ra_within_deadband
        else _signed_pulse_ms(
            drift_ra_arcsec,
            calibration.ra_rate_arcsec_per_sec,
            config.guiding_aggressiveness,
            config.guiding_max_pulse_ms,
        )
    )
    reactive_dec_ms, dec_clamped = (
        (0, False)
        if dec_within_deadband
        else _signed_pulse_ms(
            drift_dec_arcsec,
            calibration.dec_rate_arcsec_per_sec,
            config.guiding_aggressiveness,
            config.guiding_max_pulse_ms,
        )
    )

    feedforward_ra_ms, ra_feedforward_clamped = (0, False)
    feedforward_dec_ms = 0
    if mount_model is not None:
        feedforward_ra_ms, ra_feedforward_clamped = _periodic_error_feedforward_ms(
            mount_model,
            elapsed_guiding_seconds,
            calibration.ra_rate_arcsec_per_sec,
            config.guiding_aggressiveness,
            config.guiding_max_pulse_ms,
        )
        if dec_direction_reversal:
            feedforward_dec_ms = _backlash_feedforward_ms(
                mount_model, drift_dec_arcsec, config.guiding_max_pulse_ms
            )

    pulse_ra_ms, ra_total_clamped = _clamp_to_max_pulse(
        reactive_ra_ms + feedforward_ra_ms, config.guiding_max_pulse_ms
    )
    pulse_dec_ms, dec_total_clamped = _clamp_to_max_pulse(
        reactive_dec_ms + feedforward_dec_ms, config.guiding_max_pulse_ms
    )

    return GuidingCorrection(
        comparison_input_id=comparison_input_id,
        drift_ra_arcsec=drift_ra_arcsec,
        drift_dec_arcsec=drift_dec_arcsec,
        pulse_ra_ms=pulse_ra_ms,
        pulse_dec_ms=pulse_dec_ms,
        aggressiveness_applied=config.guiding_aggressiveness,
        suppressed_by_deadband=(pulse_ra_ms == 0 and pulse_dec_ms == 0),
        clamped_by_max_move=(
            ra_clamped or dec_clamped or ra_feedforward_clamped or ra_total_clamped or dec_total_clamped
        ),
    )
