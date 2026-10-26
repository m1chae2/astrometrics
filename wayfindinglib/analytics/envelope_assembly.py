"""Purpose: Assemble the performance envelope from stored records.

Description: Reads what `wayfindinglib.analytics.performance_envelope` needs
from the places it lives: the camera's stored profile, the equipment's own
light frames and their stored quality measurements (for how sharp its star
images really are), and the guiding sessions already recorded for the same
equipment (for its baseline). The derivations themselves stay pure functions
in `performance_envelope.py`; this module only fetches and shapes their
inputs and calls them.

This is Foundation code. The Control analyses and Planning both call
`assemble_performance_envelope`, so they judge against the same limits.

Every input is read for the equipment in use now, so the envelope follows
the equipment without any step that updates stored limits.
"""

import logging
import math
import sqlite3
import statistics
from collections.abc import Iterable
from typing import Any

import numpy as np

from astrometricslib import (
    FITS_READ_ERRORS,
    AstrometricsError,
    ConfigurationError,
    resolve_camera_profile,
)
from wayfindinglib.analytics.capture_history import (
    collect_capture_baseline_values,
    collect_capture_frames,
    usable_star_frames,
)
from wayfindinglib.analytics.performance_envelope import (
    DEFAULT_BLUR_TOLERANCE_FRACTION,
    MINIMUM_GUIDE_CYCLES_PER_EXPOSURE,
    MINIMUM_SAMPLES_PER_SESSION,
    MeasuredImageQuality,
    SensorLimits,
    derive_performance_envelope,
    robust_median_and_spread,
)
from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog
from wayfindinglib.models.equipment_and_site.equipment import EquipmentConfiguration
from wayfindinglib.models.equipment_and_site.equipment_fingerprint import build_equipment_fingerprint
from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
from wayfindinglib.models.session.capture_frame import CaptureFrame
from wayfindinglib.models.session.ekos_session import EkosSessionContext
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.models.session.telemetry import MEASURED_GUIDING_SAMPLE_SOURCES

logger = logging.getLogger(__name__)

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
        saturation_threshold_adu=profile.pixel_saturation_level_adu.value,
        saturation_threshold_source=_describe_provenance(profile.pixel_saturation_level_adu),
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
    the median of their stored star widths (see
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

    Each frame's star width is the stored measurement
    ``frame.star_width_moffat_arcsec``, already in arcseconds. Frames taken
    through a spectroscopy filter are
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


def _night_samples(records: Any, night: str, windows: list[tuple[float, float]]) -> list[dict[str, Any]]:
    """Read one night's measured guide samples that fall inside `windows`.

    Returns
    -------
    samples : `list` [`dict`]
        Measured samples only; estimates are never returned.
    """
    measured_sources = [source.value for source in MEASURED_GUIDING_SAMPLE_SOURCES]
    return [
        sample
        for sample in records.get_guiding_samples(
            session_id=night, limit=_SAMPLE_READ_LIMIT, sources=measured_sources
        )
        if any(start <= sample["timestamp"] <= end for start, end in windows)
    ]


def collect_baseline_values(
    records: Any,
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
    records : `ControlRecordStore`
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
        samples = _night_samples(records, night, windows)
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
    records: Any,
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
    records : `ControlRecordStore`
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
        samples = _night_samples(records, night, windows)
        if len(samples) < MINIMUM_SAMPLES_PER_SESSION:
            continue
        excursions = sum(
            1 for sample in samples if math.hypot(sample["dra"], sample["ddec"]) > excursion_limit_arcsec
        )
        fractions.append(excursions / len(samples))
    return fractions


def read_capture_frames(
    astrometrics: Any, telescope_name: str, camera_name: str, config: Any = None
) -> list[CaptureFrame]:
    """Read the equipment's light frames, or none if they cannot be read.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface (``Astrometrics``).
    telescope_name : `str`
        Name of the imaging telescope.
    camera_name : `str`
        Name of the imaging camera.
    config : `AppConfiguration`, optional
        The configuration holding the camera profiles.

    Returns
    -------
    frames : `list` [`CaptureFrame`]
        The equipment's light frames, oldest first. Empty (with a warning in
        the log) if the frame library or the measurement store could not be
        read.
    """
    try:
        return collect_capture_frames(astrometrics, telescope_name, camera_name, config)
    except (AstrometricsError, sqlite3.Error, *FITS_READ_ERRORS) as error:
        logger.warning("Could not read this equipment's frames from the frame library: %s", error)
        return []


def assemble_performance_envelope(
    *,
    config: Any,
    butler: Any,
    astrometrics: Any,
    blur_tolerance_fraction: float | None = None,
    before_night: str | None = None,
    contexts: list[EkosSessionContext] | None = None,
    runs: list[GuidingRunSummary] | None = None,
    capture_frames: list[CaptureFrame] | None = None,
    records: Any = None,
) -> PerformanceEnvelope | None:
    """Work out the performance limits of the active equipment.

    Parameters
    ----------
    config : `AppConfiguration`
        Gives the active equipment and the camera profiles.
    butler : `DiskButler`
        Holds the Ekos session records, the guiding runs and, through
        ``control_records``, the guiding samples.
    astrometrics : `Astrometrics`
        The science library handle. Its frames and stored quality
        measurements give the equipment's star width and its history.
    blur_tolerance_fraction : `float` or `None`, optional
        The most guiding error may widen a star, as a fraction of its
        width. `DEFAULT_BLUR_TOLERANCE_FRACTION` when `None`.
    before_night : `str` or `None`, optional
        Use only the nights before this one for the baseline limits.
    contexts : `list` [`EkosSessionContext`], optional
        Every Ekos session record. Read from the butler when omitted.
    runs : `list` [`GuidingRunSummary`], optional
        Every guiding run. Read from the butler when omitted.
    capture_frames : `list` [`CaptureFrame`], optional
        The equipment's light frames, when the caller has already read them
        (several nights share one read). Read here when omitted.
    records : `ControlRecordStore`, optional
        The store of guiding samples. The butler's own store when omitted.

    Returns
    -------
    envelope : `PerformanceEnvelope` or `None`
        The limits, or `None` if no telescope and camera are active.
    """
    catalog = get_equipment_catalog(config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    if telescope is None or camera is None:
        return None
    if contexts is None:
        contexts = butler.get_all("ekos_session_context")
    if runs is None:
        runs = butler.get_all("guiding_run")
    guide_scope, guide_camera = catalog.active_guide_scope(), catalog.active_guide_camera()
    equipment = EquipmentConfiguration(telescope=telescope, camera=camera)
    guide_scale = equipment.guider_plate_scale_arcsec_per_px(guide_scope, guide_camera)
    guide_focal_length = guide_scope.focal_length_mm if guide_scope else telescope.focal_length_mm
    fingerprint = build_equipment_fingerprint(telescope.name, camera.name, guide_focal_length, guide_scale)
    if records is None:
        records = butler.control_records

    # The guide cycle is a property of the equipment, not of one night,
    # so it is read from every recorded night. The baseline limits use
    # only the nights before `before_night`.
    every_night = collect_baseline_values(records, contexts, fingerprint, runs)
    baseline_values = (
        every_night
        if before_night is None
        else collect_baseline_values(records, contexts, fingerprint, runs, before_night)
    )

    # Only frames long enough for guiding error to show in them say how
    # sharp this equipment's images are. "Long enough" is a few cycles
    # of the equipment's own guider, known only once it has guided.
    measured_image_quality = None
    cadences = every_night["guide_cadence_seconds"]
    guide_cadence = statistics.median(cadences) if cadences else None
    if capture_frames is None:
        capture_frames = read_capture_frames(astrometrics, telescope.name, camera.name, config)
    minimum_exposure_seconds = (
        None if guide_cadence is None else MINIMUM_GUIDE_CYCLES_PER_EXPOSURE * guide_cadence
    )
    if minimum_exposure_seconds is not None:
        measured_image_quality = image_quality_from_frames(capture_frames, minimum_exposure_seconds)
    baseline_values = {
        **baseline_values,
        **collect_capture_baseline_values(capture_frames, contexts, minimum_exposure_seconds, before_night),
    }

    try:
        sensor_limits = sensor_limits_for_camera(camera.name, config)
    except ConfigurationError as error:
        logger.warning("No usable camera profiles, so no saturation limits: %s", error)
        sensor_limits = None

    def derive(values: dict[str, Any]) -> PerformanceEnvelope:
        """Derive the envelope from one set of baseline values.

        Returns
        -------
        envelope : `PerformanceEnvelope`
            The limits.
        """
        return derive_performance_envelope(
            equipment,
            guide_scope,
            guide_camera,
            fingerprint,
            sensor_limits=sensor_limits,
            measured_image_quality=measured_image_quality,
            baseline_values=values,
            guide_cadence_seconds=guide_cadence,
            blur_tolerance_fraction=(
                DEFAULT_BLUR_TOLERANCE_FRACTION
                if blur_tolerance_fraction is None
                else blur_tolerance_fraction
            ),
        )

    # The excursion limit depends on the measured star width, and each
    # earlier night's share of excursions depends on that limit, so the
    # envelope is derived twice: once to learn the limit, once with the
    # nights' shares included.
    envelope = derive(baseline_values)
    excursion_limit = envelope.value("guide_excursion_limit")
    if excursion_limit is not None:
        baseline_values["guide_excursion_fraction"] = collect_excursion_fraction_baseline(
            records, contexts, fingerprint, excursion_limit, before_night
        )
        envelope = derive(baseline_values)
    return envelope
