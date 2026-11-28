"""Purpose: Analyse recorded observing nights for `review`.

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

Three slow reads are kept in the context's `review_cache` between calls,
each until its own data change, and each answer equals a fresh read:

* the stored measurements of every frame (`kept_frame_measurements`). When
  rows were only added (a new frame was measured), only the added rows are
  read and merged in;
* the Ekos session records and guiding runs (`recorded_sessions`), until a
  write to either table;
* each night's guiding baseline (`_night_baseline_provider`), which reads
  only wayfinding records. A new live sample works out again only the nights
  its samples belong to (tonight), and a changed session record or guiding
  run only the nights whose windows or runs changed. A guide log sync or a
  change of configuration works out every night again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from astrometricslib.drivers import get_frame_metric_data_versions
from wayfindinglib.analytics import capture_history
from wayfindinglib.analytics.data_versions import (
    guiding_baseline_data_version,
    recorded_sessions_data_version,
)
from wayfindinglib.analytics.envelope_assembly import (
    NightBaselineTable,
    NightGuidingBaseline,
    assemble_performance_envelope,
    read_capture_frames,
    read_night_baseline_table,
    update_night_baseline_table,
)
from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog
from wayfindinglib.drivers.control_record_store import BULK_GUIDING_SAMPLES_KIND

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping, Sequence

    from astrometricslib import Target
    from wayfindinglib.api.control.context import ControlContext
    from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
    from wayfindinglib.models.session.capture_frame import CaptureFrame
    from wayfindinglib.models.session.capture_quality import CaptureSessionAnalysis
    from wayfindinglib.models.session.ekos_session import EkosSessionContext
    from wayfindinglib.models.session.guiding_run import GuidingRunSummary
    from wayfindinglib.models.session.recurring_issue import RecurringIssue
    from wayfindinglib.models.session.session_quality import GuidingSessionAnalysis
    from wayfindinglib.models.session.sky_quality import SkyAnalysis

LibraryCache = dict[tuple[str, str, str], Any]
"""Frame records read once and shared by every night of one call."""


def capture_library(
    context: ControlContext,
    telescope_name: str,
    camera_name: str,
    library_cache: LibraryCache,
    targets: Sequence[Target] | None = None,
) -> list[CaptureFrame]:
    """Read the equipment's light frames once.

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
    targets : `Sequence` [`Target`], optional
        The library's targets, when the caller has just listed them, so the
        library is not asked again.

    Returns
    -------
    frames : `list` [`CaptureFrame`]
        The equipment's light frames, oldest first, with their stored
        measurements. Empty if the frame library could not be read.
    """
    key = ("library", telescope_name, camera_name)
    if key not in library_cache:
        library_cache[key] = read_capture_frames(
            context.astrometrics,
            telescope_name,
            camera_name,
            targets,
            read_measurements=lambda: kept_frame_measurements(context).values,
        )
    return library_cache[key]


def frame_measurement_version(context: ControlContext) -> dict[str, Any] | None:
    """Read the data version of the stored frame measurements.

    This is one short query per pinned metric
    (`capture_history.PINNED_FRAME_METRICS`), with no frame rows read. It
    changes whenever a frame row of one of those metrics is added or
    removed, so equal versions mean `kept_frame_measurements` gives the same
    numbers.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the configuration.

    Returns
    -------
    version : `dict` or `None`
        See ``get_frame_metric_data_versions``. `None` when the database
        could not be read.
    """
    return get_frame_metric_data_versions(context.config, tuple(capture_history.PINNED_FRAME_METRICS))


def kept_frame_measurements(
    context: ControlContext, version: Mapping[str, Any] | None = None
) -> capture_history.FrameMeasurements:
    """Give the stored measurements of every frame, read again only on change.

    The capture history reads four metrics of every frame in the library
    (`capture_history.PINNED_FRAME_METRICS`), about half a second of reading.
    They are kept in the context's review cache with the data version of
    those metrics (``get_frame_metric_data_versions``). When the version
    changed only because rows were added, the kept numbers are brought up to
    date by reading the added rows alone
    (`capture_history.update_frame_measurements`). Anything else, such as a
    deleted frame, reads every row again. The answer is the same as a full
    read (`capture_history.read_frame_measurements`).

    Parameters
    ----------
    context : `ControlContext`
        Supplies the science library, the configuration and the cache.
    version : `~collections.abc.Mapping`, optional
        The data version the caller has just read
        (`frame_measurement_version`), so it is not read again. It is read
        here when omitted.

    Returns
    -------
    measurements : `capture_history.FrameMeasurements`
        The numbers. Every caller gets the same object while the rows stay
        the same; it is not changed by anyone.

    Notes
    -----
    A library that cannot be read raises the library's error, as a full
    read does.
    """

    def read_version() -> Any:
        """Read the data version of the pinned frame metrics.

        Returns
        -------
        version : `Any`
            See ``get_frame_metric_data_versions``.
        """
        return frame_measurement_version(context)

    def update(
        _kept_version: Any, kept: capture_history.FrameMeasurements, version: Any
    ) -> capture_history.FrameMeasurements | None:
        """Read only the rows added since the kept numbers were read.

        Returns
        -------
        measurements : `capture_history.FrameMeasurements` or `None`
            The numbers for `version`, or `None` to read them all again.
        """
        return capture_history.update_frame_measurements(context.astrometrics, kept, version)

    return context.review_cache.get_or_build(
        ("frame_measurements",),
        read_version if version is None else version,
        lambda: capture_history.read_frame_measurement_table(context.astrometrics, read_version),
        copy_result=False,
        update=update,
    )


def recorded_sessions(context: ControlContext) -> tuple[list[EkosSessionContext], list[GuidingRunSummary]]:
    """Read every recorded Ekos session and guiding run, kept until changed.

    Decoding every record takes about a fifth of a second, and they change
    rarely (an Ekos log sync, a saved guiding run). So they are kept in the
    context's review cache until the counter of either table changes
    (`recorded_sessions_data_version`).

    Parameters
    ----------
    context : `ControlContext`
        Supplies the butler, the record store and the cache.

    Returns
    -------
    contexts : `list` [`EkosSessionContext`]
        Every Ekos session record.
    runs : `list` [`GuidingRunSummary`]
        Every guiding run.

    Notes
    -----
    Each call gets its own lists, but the records in them are shared by
    every caller: treat them as read-only.
    """
    contexts, runs = context.review_cache.get_or_build(
        ("recorded_sessions",),
        lambda: recorded_sessions_data_version(context),
        lambda: (
            tuple(context.butler.get_all("ekos_session_context")),
            tuple(context.butler.get_all("guiding_run")),
        ),
        copy_result=False,
    )
    return list(contexts), list(runs)


def performance_envelope(
    context: ControlContext,
    blur_tolerance_fraction: float | None,
    before_night: str | None,
    contexts: list[EkosSessionContext],
    runs: list[GuidingRunSummary],
    library_cache: LibraryCache,
    targets: Sequence[Target] | None = None,
    exclude_nights: Collection[str] | None = None,
) -> PerformanceEnvelope | None:
    """Work out the performance limits of the active equipment.

    The work is done in Foundation by `assemble_performance_envelope`, so
    Planning reads the same limits. This function only supplies what the
    Control context holds: the configuration, the butler, the record store
    and the frame read shared between nights.

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
    targets : `Sequence` [`Target`], optional
        The library's targets, when the caller has just listed them.
    exclude_nights : `Collection` [`str`], optional
        Nights to leave out of every source of the limits.

    Returns
    -------
    envelope : `PerformanceEnvelope` or `None`
        The limits, or `None` if no telescope and camera are active.

    Notes
    -----
    Each earlier night's guiding is reduced to a few numbers once and kept in
    the context's review cache until the records change, so judging a night
    does not read every earlier night's samples again.
    """
    catalog = get_equipment_catalog(context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    if telescope is None or camera is None:
        return None
    return assemble_performance_envelope(
        config=context.config,
        butler=context.butler,
        astrometrics=context.astrometrics,
        blur_tolerance_fraction=blur_tolerance_fraction,
        before_night=before_night,
        contexts=contexts,
        runs=runs,
        capture_frames=capture_library(context, telescope.name, camera.name, library_cache, targets),
        records=context.records,
        baseline_provider=_night_baseline_provider(context, contexts, runs),
        exclude_nights=exclude_nights,
    )


def _night_baseline_provider(
    context: ControlContext, contexts: list[EkosSessionContext], runs: list[GuidingRunSummary]
) -> Callable[[str], Sequence[NightGuidingBaseline]]:
    """Make the function that gives the guiding baselines, kept in the cache.

    The baselines read only wayfinding records, so they are kept with
    `guiding_baseline_data_version`, which a new frame does not change. When
    the version changed, only the nights whose inputs changed are worked out
    again (`update_night_baseline_table`): the nights of the guide samples
    added since (`ControlRecordStore.guiding_sample_nights_after`), and the
    nights whose session windows or guiding runs differ. A guide log sync
    (which replaces samples) or a change of configuration works out every
    night again. The answer equals a full `collect_night_baselines`, so the
    median guide cadence over all nights is the same too.

    Returns
    -------
    provide : `Callable`
        Takes the equipment fingerprint and gives one `NightGuidingBaseline`
        for each earlier night that used the equipment. The baselines are
        read-only; the cache hands every caller the same list.
    """

    def provide(fingerprint: str) -> Sequence[NightGuidingBaseline]:
        """Give the baselines for one equipment fingerprint.

        Returns
        -------
        baselines : `Sequence` [`NightGuidingBaseline`]
            The kept baselines, or new ones when the records changed.
        """

        def update(kept_version: Any, kept: NightBaselineTable, version: Any) -> NightBaselineTable | None:
            """Work out again only the nights whose inputs changed.

            Returns
            -------
            table : `NightBaselineTable` or `None`
                The table for `version`, or `None` to work out every night.
            """
            kept_bulk = dict(kept_version.records).get(BULK_GUIDING_SAMPLES_KIND)
            if (
                kept_version.configuration != version.configuration
                or dict(version.records).get(BULK_GUIDING_SAMPLES_KIND) != kept_bulk
                or version.newest_sample_id < kept_version.newest_sample_id
            ):
                return None
            sample_nights = context.records.guiding_sample_nights_after(kept_version.newest_sample_id)
            if sample_nights is None:
                return None
            return update_night_baseline_table(
                context.records, contexts, fingerprint, runs, kept, sample_nights
            )

        table = context.review_cache.get_or_build(
            ("night_baselines", fingerprint),
            lambda: guiding_baseline_data_version(context),
            lambda: read_night_baseline_table(context.records, contexts, fingerprint, runs),
            copy_result=False,
            update=update,
        )
        return table.nights

    return provide


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
    minimum = envelope.value("minimum_star_measurement_exposure") if envelope else None
    catalog = get_equipment_catalog(context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    if minimum is None or telescope is None or camera is None:
        return []
    frames = capture_library(context, telescope.name, camera.name, library_cache)
    return capture_history.exposure_lengths_in_use(frames, minimum)


def guiding_night_analysis(
    context: ControlContext,
    session_id: str,
    contexts: list[EkosSessionContext] | None = None,
    runs: list[GuidingRunSummary] | None = None,
    library_cache: LibraryCache | None = None,
    targets: Sequence[Target] | None = None,
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
    targets : `Sequence` [`Target`], optional
        The library's targets, when the caller has just listed them.

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
    samples = tasks.measured_night_samples(context.records, session_id)
    if not (night_contexts or night_runs or samples):
        return None
    envelope = performance_envelope(context, None, session_id, contexts, runs, library_cache, targets)
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
    from wayfindinglib.session_analysis.capture.limits import read_capture_limits
    from wayfindinglib.session_analysis.capture.pipeline import (
        CaptureAnalysisRequest,
        analyze_capture_session_request,
    )

    if contexts is None or runs is None:
        contexts, runs = recorded_sessions(context)
    library_cache = {} if library_cache is None else library_cache
    catalog = get_equipment_catalog(context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    if telescope is None or camera is None:
        return None
    frames = capture_library(context, telescope.name, camera.name, library_cache)
    night_frames = capture_history.frames_by_night(frames).get(session_id, [])
    captures, aborted, has_record = capture_history.night_captures(contexts, session_id)
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
        sensor_pixel_count=camera.sensor_width_px * camera.sensor_height_px,
        limits=read_capture_limits(context.astrometrics),
    )
    return analyze_capture_session_request(request)


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
    frames = capture_library(context, telescope.name, camera.name, library_cache)
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

    contexts, runs = recorded_sessions(context)
    library_cache: LibraryCache = {}
    catalog = get_equipment_catalog(context.config)
    telescope, camera = catalog.active_telescope(), catalog.active_camera()
    capture_nights: set[str] = {record.session_id for record in contexts if record.captures}
    if telescope is not None and camera is not None:
        frames = capture_library(context, telescope.name, camera.name, library_cache)
        capture_nights |= set(capture_history.frames_by_night(frames))
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
