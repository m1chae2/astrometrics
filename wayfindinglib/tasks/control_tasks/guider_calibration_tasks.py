"""Purpose: Guider Calibration Measurement.

Description: Derives a `GuiderCalibration` from a measured calibration
run, per `Wayfinding_Library_Architecture.md` §2.5.1 Table 6. The
standard procedure: pulse-guide a fixed duration on each axis and
measure the resulting guide-star pixel displacement (astrometricslib's
centroid measurement, before and after). `camera_angle_deg` is the
angle of the RA-only move's pixel-space vector -- the mount's RA axis
is the reference direction the sensor's rotation is measured against,
the same convention PHD2 and similar guiders use. `arcsec_per_pixel`
is supplied rather than derived from the run itself, since it is
already exactly known from `EquipmentConfiguration.plate_scale_arcsec_per_px`.
"""

import math

from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration


def _pixel_distance(start_xy: tuple[float, float], end_xy: tuple[float, float]) -> float:
    """Return the Euclidean pixel distance between two centroid positions.

    Returns
    -------
    distance_px : `float`
        The straight-line pixel distance between the two positions.
    """
    return math.hypot(end_xy[0] - start_xy[0], end_xy[1] - start_xy[1])


def compute_guider_calibration(
    calibration_id: str,
    camera_id: str,
    telescope_id: str,
    arcsec_per_pixel: float,
    ra_pulse_duration_sec: float,
    ra_start_xy: tuple[float, float],
    ra_end_xy: tuple[float, float],
    dec_pulse_duration_sec: float,
    dec_start_xy: tuple[float, float],
    dec_end_xy: tuple[float, float],
) -> GuiderCalibration:
    """Compute a `GuiderCalibration` from one RA-axis and Dec-axis pulse run.

    Parameters
    ----------
    calibration_id : `str`
        Identifier for the resulting calibration record.
    camera_id, telescope_id : `str`
        The camera/telescope pairing this calibration applies to.
    arcsec_per_pixel : `float`
        The pairing's known plate scale.
    ra_pulse_duration_sec : `float`
        Duration of the RA-axis calibration pulse.
    ra_start_xy, ra_end_xy : `tuple` [`float`, `float`]
        The guide star's measured centroid before and after the
        RA-axis pulse.
    dec_pulse_duration_sec : `float`
        Duration of the Dec-axis calibration pulse.
    dec_start_xy, dec_end_xy : `tuple` [`float`, `float`]
        The guide star's measured centroid before and after the
        Dec-axis pulse.

    Returns
    -------
    calibration : `GuiderCalibration`
        The derived camera angle and per-axis rates.

    Raises
    ------
    ValueError
        If either pulse produced no measurable star displacement --
        a calibration cannot be derived from a run that moved nothing.
    """
    ra_pixel_distance = _pixel_distance(ra_start_xy, ra_end_xy)
    dec_pixel_distance = _pixel_distance(dec_start_xy, dec_end_xy)
    if ra_pixel_distance <= 0.0 or dec_pixel_distance <= 0.0:
        raise ValueError("Calibration run produced no measurable star displacement on one or both axes")

    camera_angle_deg = math.degrees(math.atan2(ra_end_xy[1] - ra_start_xy[1], ra_end_xy[0] - ra_start_xy[0]))

    ra_rate_arcsec_per_sec = (ra_pixel_distance * arcsec_per_pixel) / ra_pulse_duration_sec
    dec_rate_arcsec_per_sec = (dec_pixel_distance * arcsec_per_pixel) / dec_pulse_duration_sec

    return GuiderCalibration(
        id=calibration_id,
        camera_id=camera_id,
        telescope_id=telescope_id,
        arcsec_per_pixel=arcsec_per_pixel,
        camera_angle_deg=camera_angle_deg,
        ra_rate_arcsec_per_sec=ra_rate_arcsec_per_sec,
        dec_rate_arcsec_per_sec=dec_rate_arcsec_per_sec,
    )
