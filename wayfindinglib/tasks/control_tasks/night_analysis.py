"""Purpose: Analyse recorded observing nights for `control.history`.

Description: The capture, guiding and sky-coverage analyses, the list of
findings that recur across nights, and the performance envelope they are
judged against. Each function reads the recorded data through the shared
`ControlContext` (the Ekos session records and guiding runs in the
`DiskButler`, the guiding samples in the log database, and the light
frames in the science library). Nothing here writes.

Each night is judged against limits built only from the equipment's
earlier nights. When many nights are analysed in one call, the science
library's frame records are read once and shared through a
`library_cache` dictionary.
"""

from __future__ import annotations

import logging
import statistics
from typing import TYPE_CHECKING, Any

from astrometricslib import ConfigurationError
from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext
    from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
    from wayfindinglib.models.session.capture_quality import CaptureSessionAnalysis
    from wayfindinglib.models.session.ekos_session import EkosSessionContext
    from wayfindinglib.models.session.guiding_run import GuidingRunSummary
    from wayfindinglib.models.session.recurring_issue import RecurringIssue
    from wayfindinglib.models.session.session_quality import GuidingSessionAnalysis
    from wayfindinglib.models.session.sky_quality import SkyAnalysis

logger = logging.getLogger(__name__)

LibraryCache = dict[tuple[str, str, str], Any]
"""Frame records read once and shared by every night of one call."""


def capture_library(
    context: ControlContext, telescope_name: str, camera_name: str, library_cache: LibraryCache
) -> tuple[list[Any], list[Any]]:
    """Read the equipment's light frames and stack verdicts once.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the science library and the configuration.
    telescope_name : `str`
        Name of the imaging telescope.
    camera_name : `str`
        Name of the imaging camera.
    library_cache : `dict`
        Holds earlier reads, so many nights share one read.

    Returns
    -------
    frames : `list` [`CaptureFrame`]
        The equipment's light frames, oldest first. Empty if the frame
        library could not be read.
    verdicts : `list` [`StackSaturationVerdict`]
        The science library's saturation verdicts for stacked exposures.
    """
    from wayfindinglib.tasks.control_tasks import capture_analysis_tasks

    key = ("library", telescope_name, camera_name)
    if key not in library_cache:
        try:
            astrometrics = context.astrometrics
            library_cache[key] = (
                capture_analysis_tasks.collect_capture_frames(
                    astrometrics, telescope_name, camera_name, context.config
                ),
                capture_analysis_tasks.collect_stack_saturation_verdicts(astrometrics),
            )
        except Exception as error:
            logger.warning("Could not read this equipment's frames from the frame library: %s", error)
            library_cache[key] = ([], [])
    return library_cache[key]


def recorded_sessions(context: ControlContext) -> tuple[list[EkosSessionContext], list[GuidingRunSummary]]:
    """Read every recorded Ekos session and guiding run.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the butler.

    Returns
    -------
    contexts : `list` [`EkosSessionContext`]
        Every Ekos session record.
    runs : `list` [`GuidingRunSummary`]
        Every guiding run.
    """
    return context.butler.get_all("ekos_session_context"), context.butler.get_all("guiding_run")


def performance_envelope(
    context: ControlContext,
    blur_tolerance_fraction: float | None,
    before_night: str | None,
    contexts: list[EkosSessionContext],
    runs: list[GuidingRunSummary],
    library_cache: LibraryCache,
) -> PerformanceEnvelope | None:
    """Work out the performance limits of the active equipment.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the active equipment and the recorded data.
    blur_tolerance_fraction : `float` or `None`
        The most guiding error may widen a star, as a fraction of its
        width. 0.10 when `None`.
    before_night : `str` or `None`
        Use only the nights before this one for the baseline limits.
    contexts : `list` [`EkosSessionContext`]
        Every Ekos session record.
    runs : `list` [`GuidingRunSummary`]
        Every guiding run.
    library_cache : `dict`
        Shares frame reads between calls.

    Returns
    -------
    envelope : `PerformanceEnvelope` or `None`
        The limits, or `None` if no telescope and camera are active.
    """
    from wayfindinglib.analytics.performance_envelope import (
        DEFAULT_BLUR_TOLERANCE_FRACTION,
        MINIMUM_GUIDE_CYCLES_PER_EXPOSURE,
        derive_performance_envelope,
    )
    from wayfindinglib.models.equipment_and_site.equipment import EquipmentConfiguration
    from wayfindinglib.models.equipment_and_site.equipment_fingerprint import build_equipment_fingerprint
    from wayfindinglib.tasks.control_tasks import capture_analysis_tasks, performance_envelope_tasks

    catalog = get_equipment_catalog(context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    if telescope is None or camera is None:
        return None
    guide_scope, guide_camera = catalog.active_guide_scope(), catalog.active_guide_camera()
    equipment = EquipmentConfiguration(telescope=telescope, camera=camera)
    guide_scale = equipment.guider_plate_scale_arcsec_per_px(guide_scope, guide_camera)
    guide_focal_length = guide_scope.focal_length_mm if guide_scope else telescope.focal_length_mm
    fingerprint = build_equipment_fingerprint(telescope.name, camera.name, guide_focal_length, guide_scale)
    logger_interface = context.logger_interface

    # The guide cycle is a property of the equipment, not of one night,
    # so it is read from every recorded night. The baseline limits use
    # only the nights before `before_night`.
    every_night = performance_envelope_tasks.collect_baseline_values(
        logger_interface, contexts, fingerprint, runs
    )
    baseline_values = (
        every_night
        if before_night is None
        else performance_envelope_tasks.collect_baseline_values(
            logger_interface, contexts, fingerprint, runs, before_night
        )
    )

    # Only frames long enough for guiding error to show in them say how
    # sharp this equipment's images are. "Long enough" is a few cycles
    # of the equipment's own guider, known only once it has guided.
    measured_image_quality = None
    cadences = every_night["guide_cadence_seconds"]
    guide_cadence = statistics.median(cadences) if cadences else None
    capture_frames, _ = capture_library(context, telescope.name, camera.name, library_cache)
    if guide_cadence is not None:
        measured_image_quality = performance_envelope_tasks.image_quality_from_frames(
            capture_frames, MINIMUM_GUIDE_CYCLES_PER_EXPOSURE * guide_cadence
        )
    baseline_values = {
        **baseline_values,
        **capture_analysis_tasks.collect_capture_baseline_values(
            capture_frames,
            contexts,
            None if guide_cadence is None else MINIMUM_GUIDE_CYCLES_PER_EXPOSURE * guide_cadence,
            before_night,
        ),
    }

    try:
        sensor_limits = performance_envelope_tasks.sensor_limits_for_camera(camera.name, context.config)
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
        baseline_values["guide_excursion_fraction"] = (
            performance_envelope_tasks.collect_excursion_fraction_baseline(
                logger_interface, contexts, fingerprint, excursion_limit, before_night
            )
        )
        envelope = derive(baseline_values)
    return envelope


def _exposure_lengths_in_use(
    context: ControlContext, envelope: PerformanceEnvelope | None, library_cache: LibraryCache
) -> list[float]:
    """List the exposure lengths the active equipment is used with.

    Returns
    -------
    lengths : `list` [`float`]
        The lengths, shortest first. Empty if no equipment is active, the
        equipment has not guided yet, or no length has enough frames.
    """
    from wayfindinglib.tasks.control_tasks import capture_analysis_tasks

    minimum = envelope.value("minimum_star_measurement_exposure") if envelope else None
    catalog = get_equipment_catalog(context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    if minimum is None or telescope is None or camera is None:
        return []
    frames, _ = capture_library(context, telescope.name, camera.name, library_cache)
    return capture_analysis_tasks.exposure_lengths_in_use(frames, minimum)


def guiding_night_analysis(
    context: ControlContext,
    session_id: str,
    contexts: list[EkosSessionContext] | None = None,
    runs: list[GuidingRunSummary] | None = None,
    library_cache: LibraryCache | None = None,
) -> GuidingSessionAnalysis | None:
    """Analyse one night's guiding in three stages.

    Pre-processing asks whether the guiding data is good: lost frames, a
    weak guide star, jumps to the wrong star, impossible calibrations.
    Processing measures the guiding error, what it does to star width, and
    the declination drift. Post-processing turns those into
    recommendations, each with the evidence and limit behind it.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the recorded data.
    session_id : `str`
        The observing night, such as ``"2026-09-24"``.
    contexts, runs : `list`, optional
        Already-read records. Read here when omitted.
    library_cache : `dict`, optional
        Shares frame reads between nights.

    Returns
    -------
    analysis : `GuidingSessionAnalysis` or `None`
        The analysis, or `None` if the night has no runs, samples or
        session records at all.
    """
    from wayfindinglib.session_analysis.guiding.pipeline import (
        GuidingAnalysisRequest,
        analyze_guiding_session_request,
    )
    from wayfindinglib.tasks.control_tasks import session_analysis_tasks as tasks

    if contexts is None or runs is None:
        contexts, runs = recorded_sessions(context)
    library_cache = {} if library_cache is None else library_cache
    night_contexts = [record for record in contexts if record.session_id == session_id]
    night_runs = [run for run in runs if run.session_id == session_id]
    samples = tasks.measured_night_samples(context.logger_interface, session_id)
    if not (night_contexts or night_runs or samples):
        return None
    envelope = performance_envelope(context, None, session_id, contexts, runs, library_cache)
    exposure_lengths = _exposure_lengths_in_use(context, envelope, library_cache)
    fingerprints = [c.equipment.equipment_fingerprint for c in night_contexts if c.equipment is not None]
    match = tasks.equipment_match_level(
        envelope.equipment_fingerprint if envelope else None, fingerprints, night_runs
    )
    request = GuidingAnalysisRequest(
        session_id=session_id,
        equipment_fingerprint=envelope.equipment_fingerprint if envelope else "unknown",
        envelope=envelope,
        limits_equipment_match=match,
        samples=samples,
        runs=night_runs,
        guide_scale_matches_configuration=tasks.guide_scale_agreement(night_contexts),
        logged_guide_scale=tasks.logged_guide_scale(night_runs),
        configured_guide_scale=context.guider_plate_scale_arcsec_per_px(),
        exposure_lengths_seconds=exposure_lengths,
    )
    return analyze_guiding_session_request(request)


def guiding_night_summaries(
    context: ControlContext, latest_nights: int | None = None
) -> list[dict[str, Any]]:
    """Summarise the guiding analysis of every recorded night.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the recorded data.
    latest_nights : `int` or `None`, optional
        Cover only the most recent nights. Each night is analysed against
        the nights before it, so this bounds the cost.

    Returns
    -------
    summaries : `list` [`dict`]
        One row per night, oldest first: whether it was flagged and why,
        its samples, the share of lost frames, the guide signal, the
        guiding error, and the kinds of its recommendations.
    """
    contexts, runs = recorded_sessions(context)
    library_cache: LibraryCache = {}
    rows = []
    for night in sorted({run.session_id for run in runs})[-latest_nights if latest_nights else None :]:
        analysis = guiding_night_analysis(context, night, contexts, runs, library_cache)
        if analysis is None:
            continue
        rows.append({
            "sessionId": night,
            "flagged": analysis.flagged,
            "flagReasons": analysis.flag_reasons,
            "limitsEquipmentMatch": analysis.input_quality.limits_equipment_match,
            "samples": analysis.input_quality.samples_analyzed,
            "lostFraction": analysis.input_quality.lost_fraction,
            "medianSnr": analysis.input_quality.median_snr,
            "rmsPerAxisArcsec": analysis.performance.rms_per_axis_arcsec,
            "recommendations": [r.kind.value for r in analysis.recommendations],
        })
    return rows


def capture_night_analysis(
    context: ControlContext,
    session_id: str,
    contexts: list[EkosSessionContext] | None = None,
    runs: list[GuidingRunSummary] | None = None,
    library_cache: LibraryCache | None = None,
) -> CaptureSessionAnalysis | None:
    """Analyse one night's captured frames in three stages.

    Pre-processing asks whether the capture data is good: whether every
    exposure Ekos finished reached the frame library, whether frames carry
    the measurements later steps need, and whether exposures were
    cancelled often. Processing measures clipped stars, star sharpness and
    roundness, and how much of the night was spent exposing.
    Post-processing turns those into recommendations.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the recorded data.
    session_id : `str`
        The observing night, such as ``"2026-09-24"``.
    contexts, runs : `list`, optional
        Already-read records. Read here when omitted.
    library_cache : `dict`, optional
        Shares frame reads between nights.

    Returns
    -------
    analysis : `CaptureSessionAnalysis` or `None`
        The analysis, or `None` if no equipment is active, or the night has
        neither frames from this equipment nor an Ekos record.
    """
    from wayfindinglib.session_analysis.capture.pipeline import (
        CaptureAnalysisRequest,
        analyze_capture_session_request,
    )
    from wayfindinglib.tasks.control_tasks import capture_analysis_tasks

    if contexts is None or runs is None:
        contexts, runs = recorded_sessions(context)
    library_cache = {} if library_cache is None else library_cache
    catalog = get_equipment_catalog(context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    if telescope is None or camera is None:
        return None
    frames, verdicts = capture_library(context, telescope.name, camera.name, library_cache)
    night_frames = capture_analysis_tasks.frames_by_night(frames).get(session_id, [])
    captures, aborted, has_record = capture_analysis_tasks.night_captures(contexts, session_id)
    if not (night_frames or has_record):
        return None
    envelope = performance_envelope(context, None, session_id, contexts, runs, library_cache)
    request = CaptureAnalysisRequest(
        session_id=session_id,
        equipment_fingerprint=envelope.equipment_fingerprint if envelope else "unknown",
        envelope=envelope,
        limits_equipment_match="exact" if night_frames and envelope else "none",
        frames=night_frames,
        captures=captures,
        aborted_captures=aborted,
        has_ekos_record=has_record,
        stack_saturation=verdicts,
        sensor_pixel_count=camera.sensor_width_px * camera.sensor_height_px,
    )
    return analyze_capture_session_request(request)


def capture_night_summaries(
    context: ControlContext, latest_nights: int | None = None
) -> list[dict[str, Any]]:
    """Summarise the capture analysis of every recorded night.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the recorded data.
    latest_nights : `int` or `None`, optional
        Cover only the most recent nights, to bound the cost.

    Returns
    -------
    summaries : `list` [`dict`]
        One row per night, oldest first: whether it was flagged and why,
        its light frames, Ekos exposures with no frame, the median star
        width, and the kinds of its recommendations.
    """
    from wayfindinglib.tasks.control_tasks import capture_analysis_tasks

    contexts, runs = recorded_sessions(context)
    library_cache: LibraryCache = {}
    catalog = get_equipment_catalog(context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    if telescope is None or camera is None:
        return []
    frames, _ = capture_library(context, telescope.name, camera.name, library_cache)
    nights = set(capture_analysis_tasks.frames_by_night(frames)) | {
        record.session_id for record in contexts if record.captures
    }
    rows = []
    for night in sorted(nights)[-latest_nights if latest_nights else None :]:
        analysis = capture_night_analysis(context, night, contexts, runs, library_cache)
        if analysis is None:
            continue
        rows.append({
            "sessionId": night,
            "flagged": analysis.flagged,
            "flagReasons": analysis.flag_reasons,
            "lightFrames": analysis.input_quality.light_frames,
            "capturesWithoutFrame": analysis.input_quality.captures_without_frame,
            "medianStarWidthArcsec": analysis.performance.star_quality.median_star_width_arcsec,
            "dutyCycle": analysis.performance.efficiency.duty_cycle,
            "recommendations": [r.kind.value for r in analysis.recommendations],
        })
    return rows


def sky_coverage_analysis(context: ControlContext) -> SkyAnalysis | None:
    """Compare how the equipment performs in different parts of the sky.

    Across every recorded night, asks whether stars were wider or less
    round, or the guiding error larger, at low altitude, in one azimuth
    direction, or on one side of the pier. Each measurement is compared
    with the typical value of its own night, which removes the night's
    seeing. The result also maps the parts of the sky no night reached.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the recorded data.

    Returns
    -------
    analysis : `SkyAnalysis` or `None`
        The analysis, or `None` if no telescope and camera are active.
    """
    from wayfindinglib.session_analysis.sky.pipeline import SkyAnalysisRequest, analyze_sky_request
    from wayfindinglib.tasks.control_tasks import sky_analysis_tasks

    catalog = get_equipment_catalog(context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    if telescope is None or camera is None:
        return None
    contexts, runs = recorded_sessions(context)
    library_cache: LibraryCache = {}
    envelope = performance_envelope(context, None, None, contexts, runs, library_cache)
    frames, _ = capture_library(context, telescope.name, camera.name, library_cache)
    frame_samples, frames_without_position = sky_analysis_tasks.sky_samples_from_frames(
        frames, envelope.value("minimum_star_measurement_exposure") if envelope else None
    )
    guiding_analyses = [
        analysis
        for night in sorted({run.session_id for run in runs})
        if (analysis := guiding_night_analysis(context, night, contexts, runs, library_cache)) is not None
    ]
    run_samples, runs_without_position, nights_excluded = sky_analysis_tasks.sky_samples_from_guiding(
        guiding_analyses
    )
    samples = [*frame_samples, *run_samples]
    nights = sorted({sample.night for sample in samples})
    request = SkyAnalysisRequest(
        session_id=f"{nights[0]}..{nights[-1]}" if nights else "none",
        equipment_fingerprint=envelope.equipment_fingerprint if envelope else "unknown",
        envelope=envelope,
        limits_equipment_match="exact",
        samples=samples,
        samples_without_position=frames_without_position + runs_without_position,
        guiding_nights_excluded=nights_excluded,
        minimum_altitude_degrees=telescope.min_altitude_deg,
        maximum_altitude_degrees=telescope.max_altitude_deg,
        blur_tolerance_fraction=envelope.blur_tolerance_fraction if envelope else 0.10,
    )
    return analyze_sky_request(request)


def recurring_issues(context: ControlContext, latest_nights: int | None = None) -> list[RecurringIssue]:
    """List the findings that repeat across nights.

    Runs the guiding and capture analyses on every night and reports each
    finding of advice or warning level that appears on at least two. One
    bad night can be weather; the same finding on many nights points at
    the equipment or the routine.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the recorded data.
    latest_nights : `int` or `None`, optional
        Look only at the most recent nights of each kind, to bound the cost.

    Returns
    -------
    issues : `list` [`RecurringIssue`]
        Each recurring finding with its nights, most frequent first.
    """
    from wayfindinglib.session_analysis.recurring_issues import find_recurring_issues
    from wayfindinglib.tasks.control_tasks import capture_analysis_tasks

    contexts, runs = recorded_sessions(context)
    library_cache: LibraryCache = {}
    catalog = get_equipment_catalog(context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    capture_nights: set[str] = {record.session_id for record in contexts if record.captures}
    if telescope is not None and camera is not None:
        frames, _ = capture_library(context, telescope.name, camera.name, library_cache)
        capture_nights |= set(capture_analysis_tasks.frames_by_night(frames))
    per_night: list[tuple[str, str, list[Any]]] = []
    recent = slice(-latest_nights if latest_nights else None, None)
    for night in sorted({run.session_id for run in runs})[recent]:
        guiding = guiding_night_analysis(context, night, contexts, runs, library_cache)
        if guiding is not None:
            per_night.append(("guiding", night, guiding.recommendations))
    for night in sorted(capture_nights)[recent]:
        capture = capture_night_analysis(context, night, contexts, runs, library_cache)
        if capture is not None:
            per_night.append(("capture", night, capture.recommendations))
    return find_recurring_issues(per_night)


def guiding_runs(context: ControlContext, session_id: str | None = None) -> list[GuidingRunSummary]:
    """Return recorded guiding runs, oldest first.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the butler.
    session_id : `str` or `None`, optional
        Keep only the runs of this observing night.

    Returns
    -------
    runs : `list` [`GuidingRunSummary`]
        The matching runs.
    """
    runs = context.butler.get_all("guiding_run")
    return sorted(
        (run for run in runs if session_id is None or run.session_id == session_id),
        key=lambda run: run.started_at,
    )


def ekos_session_summaries(context: ControlContext) -> list[dict[str, Any]]:
    """Summarise every recorded Ekos session, without its full detail.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the butler.

    Returns
    -------
    summaries : `list` [`dict`]
        One entry per session, oldest first: its id, observing night,
        start and end times, equipment fingerprint, and how many exposures,
        aborted exposures, autofocus runs and mount positions it holds.
    """
    records = sorted(context.butler.get_all("ekos_session_context"), key=lambda c: c.started_at)
    return [
        {
            "id": record.id,
            "sessionId": record.session_id,
            "startedAt": record.started_at,
            "endedAt": record.ended_at,
            "equipmentFingerprint": record.equipment.equipment_fingerprint if record.equipment else None,
            "captures": len(record.captures),
            "abortedCaptures": len(record.aborted_captures),
            "autofocusRuns": len(record.autofocus_runs),
            "mountPositions": len(record.mount_positions),
        }
        for record in records
    ]


def ekos_session_record(context: ControlContext, session_file_id: str) -> EkosSessionContext | None:
    """Return one recorded Ekos session.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the butler.
    session_file_id : `str`
        The analyze file's timestamp name, such as ``"2026-09-23T20-31-48"``.

    Returns
    -------
    record : `EkosSessionContext` or `None`
        The session record, or `None` if none has that id.
    """
    return context.butler.get("ekos_session_context", {"id": session_file_id})
