"""Purpose: Score the sky for tracking risk, and re-export the envelope inputs.

Description: The performance envelope is assembled in Foundation
(`wayfindinglib.analytics.envelope_assembly`), so Planning can read it too.
What stays here is the one part that needs the Control context: reading every
recorded plate solve to score the sky for tracking risk
(`tracking_risk_map`). `sensor_limits_for_camera` is re-exported because the
guide-exposure ladder in `api/control/guiding.py` reads the guide camera's
clip ceiling through this module.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from wayfindinglib.analytics.alignment_sessions import group_alignment_attempts
from wayfindinglib.analytics.envelope_assembly import sensor_limits_for_camera
from wayfindinglib.analytics.tracking_risk import TrackedTarget, build_tracking_risk_map

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext
    from wayfindinglib.models.equipment_and_site.performance_envelope import TrackingRiskMap
    from wayfindinglib.models.session.telemetry import AlignmentTargetSession

__all__ = ["sensor_limits_for_camera", "tracking_risk_map"]


def _mount_hour_angles_deg(
    sessions: list[AlignmentTargetSession], longitude_deg: float
) -> list[float | None]:
    """Work out where on the mount each target sat while it was observed.

    Parameters
    ----------
    sessions : `list` [`AlignmentTargetSession`]
        Measured targets, each with its plate solves.
    longitude_deg : `float`
        Observer longitude, east positive.

    Returns
    -------
    hour_angles : `list` [`float` or `None`]
        Per target, the circular mean of the hour angle (local sidereal
        time minus right ascension) at each timed solve, -180 to 180
        degrees, or `None` if no solve has a time.
    """
    from astropy.time import Time

    times = [a.timestamp for s in sessions for a in s.attempts if a.timestamp is not None]
    if not times:
        return [None] * len(sessions)
    lst_deg = iter(Time(times, format="unix").sidereal_time("mean", longitude=longitude_deg).deg.tolist())
    hour_angles: list[float | None] = []
    for session in sessions:
        angles = [math.radians(next(lst_deg) - a.ra) for a in session.attempts if a.timestamp is not None]
        if not angles:
            hour_angles.append(None)
            continue
        mean = math.degrees(math.atan2(sum(map(math.sin, angles)), sum(map(math.cos, angles))))
        hour_angles.append(mean)
    return hour_angles


def tracking_risk_map(context: ControlContext) -> TrackingRiskMap:
    """Score the sky for tracking risk from every recorded plate solve.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the active equipment (for the plate scale), the observer
        location and the log database.

    Returns
    -------
    risk_map : `TrackingRiskMap`
        The grid. Without a known observer location, or without plate
        solves, it holds the geometric prior only (at 45 degrees latitude
        when the location is unknown). Without an active telescope and
        camera, the jitter limits are 1.2 and 2.0 arcseconds.
    """
    from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog
    from wayfindinglib.models.equipment_and_site.equipment import EquipmentConfiguration
    from wayfindinglib.tasks.control_tasks.alignment_history import attempt_models

    catalog = get_equipment_catalog(context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    plate_scale = (
        EquipmentConfiguration(telescope=telescope, camera=camera).plate_scale_arcsec_per_px
        if telescope is not None and camera is not None
        else None
    )
    location = context.observer_location()
    latitude = location["latitude"] if location else context.observer_latitude_deg()
    logs = context.records
    rows = logs.get_session_alignment_attempts("all") if logs is not None else []
    sessions = [
        s
        for s in group_alignment_attempts(attempt_models(rows))
        if s.frame_count >= 2 and s.rms_total_arcsec > 0
    ]
    targets = []
    if location is not None:
        for session, hour_angle in zip(
            sessions, _mount_hour_angles_deg(sessions, location["longitude"]), strict=True
        ):
            if hour_angle is not None:
                targets.append(
                    TrackedTarget(
                        ha_deg=hour_angle,
                        dec_deg=session.mean_dec_deg,
                        rms_arcsec=session.rms_total_arcsec,
                        solve_count=session.frame_count,
                    )
                )
    solve_count = sum(s.frame_count for s in sessions) if targets else 0
    return build_tracking_risk_map(latitude, targets, plate_scale, solve_count)
