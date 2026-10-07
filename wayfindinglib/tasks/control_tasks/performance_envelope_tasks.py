"""Purpose: Gather the inputs for the performance envelope.

Description: Reads what `wayfindinglib.analytics.performance_envelope`
needs from the places it lives: the camera's stored profile, the
equipment's own image frames (for how sharp its star images really are),
and the guiding sessions already recorded for the same equipment (for its
baseline). It also reads every recorded plate solve, to score the sky
for tracking risk. The derivations themselves stay pure functions; this
module only fetches and shapes their inputs.

Every input is read for the equipment in use now, so the envelope follows
the equipment without any step that updates stored limits.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import numpy as np

from astrometricslib import resolve_camera_profile
from wayfindinglib.analytics.alignment_sessions import group_alignment_attempts
from wayfindinglib.analytics.performance_envelope import (
    MINIMUM_SAMPLES_PER_SESSION,
    MeasuredImageQuality,
    SensorLimits,
    robust_median_and_spread,
)
from wayfindinglib.analytics.tracking_risk import TrackedTarget, build_tracking_risk_map
from wayfindinglib.models.session.capture_frame import CaptureFrame
from wayfindinglib.models.session.ekos_session import EkosSessionContext
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.models.session.telemetry import MEASURED_GUIDING_SAMPLE_SOURCES
from wayfindinglib.tasks.control_tasks.capture_analysis_tasks import (
    collect_capture_frames,
    usable_star_frames,
)

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext
    from wayfindinglib.models.equipment_and_site.performance_envelope import TrackingRiskMap
    from wayfindinglib.models.session.telemetry import AlignmentTargetSession

SESSION_WINDOW_MARGIN_SECONDS = 1800.0
"""How far outside a session's own time span a guide sample still counts."""

_MAXIMUM_CADENCE_GAP_SECONDS = 60.0
"""A gap between samples longer than this is a pause, not the guide cadence."""

_SAMPLE_READ_LIMIT = 200000
"""Most samples read for one night. A long night holds about 10,000."""


def _describe_provenance(provenanced_value: Any) -> str:
    """Say where a profile value came from.

    Returns
    -------
    description : `str`
        The value's kind (measured, datasheet, assumed) and its source.
    """
    provenance = provenanced_value.provenance
    return f"{provenance.kind}: {provenance.source}"


def sensor_limits_for_camera(camera_name: str, config: Any = None) -> SensorLimits:
    """Read a camera's saturation facts from its stored profile.

    Parameters
    ----------
    camera_name : `str`
        The camera's name, written in any spelling.
    config : `AppConfiguration`, optional
        The configuration holding the camera profiles. The process-wide
        one is used when left out.

    Returns
    -------
    sensor_limits : `SensorLimits`
        The clip ceiling and saturation threshold with where each came
        from. A camera with no profile gets the generic stand-in, marked
        as such.
    """
    profile = resolve_camera_profile(camera_name, config)
    return SensorLimits(
        camera_name=profile.camera_name,
        clip_ceiling_adu=profile.clip_ceiling_adu.value,
        clip_ceiling_source=_describe_provenance(profile.clip_ceiling_adu),
        saturation_threshold_adu=profile.saturation_threshold_adu.value,
        saturation_threshold_source=_describe_provenance(profile.saturation_threshold_adu),
        is_generic_fallback=profile.is_generic_fallback,
    )


def measure_image_quality(
    astrometrics: Any,
    telescope_name: str,
    camera_name: str,
    minimum_exposure_seconds: float,
    config: Any = None,
) -> MeasuredImageQuality | None:
    """Measure how sharp this equipment's own star images are.

    Reads the light frames taken with this telescope and camera, then takes
    the median of their registration star widths (see
    `image_quality_from_frames`).

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface (``Astrometrics``).
    telescope_name : `str`
        Name of the imaging telescope.
    camera_name : `str`
        Name of the imaging camera. Every spelling the camera's profile
        knows (its aliases and the library's record name) is accepted.
    minimum_exposure_seconds : `float`
        Shortest exposure to include.
    config : `AppConfiguration`, optional
        The configuration holding the camera profiles. The process-wide
        one is used when left out.

    Returns
    -------
    image_quality : `MeasuredImageQuality` or `None`
        The median star width and how many frames it rests on, or `None`
        if no frame of this equipment has a measured width.
    """
    frames = collect_capture_frames(astrometrics, telescope_name, camera_name, config)
    return image_quality_from_frames(frames, minimum_exposure_seconds)


def image_quality_from_frames(
    frames: Iterable[CaptureFrame], minimum_exposure_seconds: float
) -> MeasuredImageQuality | None:
    """Take the median star width of the frames that can show guiding error.

    Each frame's registration star width is already in arcseconds, using that
    frame's own plate scale. Frames taken through a spectroscopy filter are
    left out, since a dispersed star has no meaningful width. So are frames
    shorter than `minimum_exposure_seconds`: in a short exposure guiding error
    has no time to widen the star, and the bright single stars usually shot
    that briefly are saturated, which inflates their measured width.

    Parameters
    ----------
    frames : `Iterable` [`CaptureFrame`]
        The equipment's light frames.
    minimum_exposure_seconds : `float`
        Shortest exposure to include.

    Returns
    -------
    image_quality : `MeasuredImageQuality` or `None`
        The median star width and how many frames it rests on, or `None`
        if no frame qualifies.
    """
    widths_arcsec = [
        frame.star_width_arcsec
        for frame in usable_star_frames(frames, minimum_exposure_seconds)
        if frame.star_width_arcsec is not None
    ]
    if not widths_arcsec:
        return None
    return MeasuredImageQuality(
        fwhm_arcsec=statistics.median(widths_arcsec),
        sample_count=len(widths_arcsec),
        minimum_exposure_seconds=minimum_exposure_seconds,
    )


def _matching_windows_by_night(
    session_contexts: Iterable[EkosSessionContext], equipment_fingerprint: str
) -> dict[str, list[tuple[float, float]]]:
    """Find the time windows of the sessions that used this equipment.

    Returns
    -------
    windows_by_night : `dict` [`str`, `list` [`tuple` [`float`, `float`]]]
        For each observing night, the (start, end) of each matching session,
        widened by `SESSION_WINDOW_MARGIN_SECONDS`.
    """
    windows_by_night: dict[str, list[tuple[float, float]]] = {}
    for context in session_contexts:
        if context.equipment is None or context.equipment.equipment_fingerprint != equipment_fingerprint:
            continue
        windows_by_night.setdefault(context.session_id, []).append((
            context.started_at - SESSION_WINDOW_MARGIN_SECONDS,
            context.ended_at + SESSION_WINDOW_MARGIN_SECONDS,
        ))
    return windows_by_night


def _night_samples(
    logger_interface: Any, night: str, windows: list[tuple[float, float]]
) -> list[dict[str, Any]]:
    """Read one night's measured guide samples that fall inside `windows`.

    Returns
    -------
    samples : `list` [`dict`]
        Measured samples only; estimates are never returned.
    """
    measured_sources = [source.value for source in MEASURED_GUIDING_SAMPLE_SOURCES]
    return [
        sample
        for sample in logger_interface.get_guiding_logs(
            session_id=night, limit=_SAMPLE_READ_LIMIT, sources=measured_sources
        )
        if any(start <= sample["timestamp"] <= end for start, end in windows)
    ]


def collect_baseline_values(
    logger_interface: Any,
    session_contexts: Iterable[EkosSessionContext],
    equipment_fingerprint: str,
    guiding_runs: Iterable[GuidingRunSummary] = (),
    before_night: str | None = None,
) -> dict[str, list[float]]:
    """Summarise each earlier session that used this equipment.

    For every observing night that has a session record with this
    equipment's fingerprint, takes the measured guide samples that fall
    inside those records' time spans and reduces them to one median guide
    SNR, one guiding error and one share of lost frames. Sessions of other
    equipment are not read at all, so a change of equipment starts with an
    empty history.

    Parameters
    ----------
    logger_interface : `astrometricslib.LoggerInterface`
        Source of the recorded guiding samples.
    session_contexts : `Iterable` [`EkosSessionContext`]
        Every recorded session.
    equipment_fingerprint : `str`
        The equipment in use now.
    guiding_runs : `Iterable` [`GuidingRunSummary`], optional
        Every recorded guiding run. They give each night's share of lost
        frames.
    before_night : `str` or `None`, optional
        If given, only nights earlier than this one are read. Judging a night
        against a history that includes that night, or later ones, would let
        a bad night make itself look normal.

    Returns
    -------
    baseline_values : `dict` [`str`, `list` [`float`]]
        ``"guide_snr"`` (median guide-star SNR), ``"guide_star_mass"`` (median
        guide-star brightness, in camera counts), ``"guiding_rms"`` (robust
        guiding error per axis, in arcseconds), ``"guide_cadence_seconds"``
        (median time between guide samples) and ``"guide_lost_fraction"``
        (share of guide frames lost), one value per qualifying night.
    """
    windows_by_night = _matching_windows_by_night(session_contexts, equipment_fingerprint)
    runs = list(guiding_runs)
    snr_values: list[float] = []
    star_mass_values: list[float] = []
    rms_values: list[float] = []
    cadence_values: list[float] = []
    lost_fraction_values: list[float] = []
    for night, windows in sorted(windows_by_night.items()):
        if before_night is not None and night >= before_night:
            continue
        samples = _night_samples(logger_interface, night, windows)
        if len(samples) < MINIMUM_SAMPLES_PER_SESSION:
            continue
        snr = [sample["snr"] for sample in samples if sample.get("snr") is not None]
        _, ra_spread = robust_median_and_spread([sample["dra"] for sample in samples])
        _, dec_spread = robust_median_and_spread([sample["ddec"] for sample in samples])
        if snr:
            snr_values.append(float(np.median(snr)))
        star_mass = [sample["star_mass"] for sample in samples if sample.get("star_mass")]
        if star_mass:
            star_mass_values.append(float(np.median(star_mass)))
        rms_values.append(float(np.sqrt((ra_spread**2 + dec_spread**2) / 2.0)))
        times = np.sort([sample["timestamp"] for sample in samples])
        gaps = np.diff(times)
        # A gap over a minute is a break between runs, not the cadence.
        cadence_values.append(float(np.median(gaps[gaps < _MAXIMUM_CADENCE_GAP_SECONDS])))
        night_runs = [
            run
            for run in runs
            if run.session_id == night and any(start <= run.started_at <= end for start, end in windows)
        ]
        frames_total = sum(run.frames_total for run in night_runs)
        if frames_total:
            lost_fraction_values.append(sum(run.frames_lost for run in night_runs) / frames_total)
    return {
        "guide_snr": snr_values,
        "guide_star_mass": star_mass_values,
        "guiding_rms": rms_values,
        "guide_cadence_seconds": cadence_values,
        "guide_lost_fraction": lost_fraction_values,
    }


def collect_excursion_fraction_baseline(
    logger_interface: Any,
    session_contexts: Iterable[EkosSessionContext],
    equipment_fingerprint: str,
    excursion_limit_arcsec: float,
    before_night: str | None = None,
) -> list[float]:
    """Find each earlier night's share of guide-error excursions.

    An excursion is a guide sample whose total error exceeds
    `excursion_limit_arcsec`. The limit depends on the equipment's measured
    star width, so this runs after the rest of the envelope is known.

    Parameters
    ----------
    logger_interface : `astrometricslib.LoggerInterface`
        Source of the recorded guiding samples.
    session_contexts : `Iterable` [`EkosSessionContext`]
        Every recorded session.
    equipment_fingerprint : `str`
        The equipment in use now.
    excursion_limit_arcsec : `float`
        Total guide error above which a sample is an excursion.
    before_night : `str` or `None`, optional
        If given, only nights earlier than this one are read.

    Returns
    -------
    fractions : `list` [`float`]
        One share of excursions per qualifying night.
    """
    fractions = []
    for night, windows in sorted(_matching_windows_by_night(session_contexts, equipment_fingerprint).items()):
        if before_night is not None and night >= before_night:
            continue
        samples = _night_samples(logger_interface, night, windows)
        if len(samples) < MINIMUM_SAMPLES_PER_SESSION:
            continue
        excursions = sum(
            1 for sample in samples if math.hypot(sample["dra"], sample["ddec"]) > excursion_limit_arcsec
        )
        fractions.append(excursions / len(samples))
    return fractions


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
    logs = context.logger_interface
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
