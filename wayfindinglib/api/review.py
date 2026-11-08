"""Purpose: `ObservationReview`, how the nights went and what the results say.

Description: `Wayfinder.review` is an `ObservationReview`. It answers
questions about recorded observing nights (the capture, guiding and
sky-coverage analyses, findings that recur, Ekos session records, guiding
runs and the pointing model), reports how the session in progress is
going, groups plate-solve alignment attempts into one session per target,
matches each light frame to the guide error during its exposure, works
out the performance limits of the active equipment, and measures what the
recorded nights say about the equipment itself: the mount's unguided error,
the guiding spectrum, the focus-versus-temperature slope and the sky
background. Only
`save_ekos_session_context` writes. The analyses live in
`tasks.control_tasks.night_analysis` and `night_history`.

Review reads the same records and context as `control`, so it is built
from the `Wayfinder`'s `ObservatoryControl` and shares its context. The
advisory (`get_advisory`) judges data and results that already exist, so it
is answered here. The advice that looks ahead is in `planning`
(`get_tonight_advice` and `get_calibration_advice`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from astrometricslib import Target
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from astrometricslib.jobs import background_job, get_current_job, registered_job
from wayfindinglib.analytics.alignment_sessions import group_alignment_attempts
from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.equipment_and_site.focus_temperature_fit import FocusSource, FocusTemperatureFit
from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
from wayfindinglib.models.planning.target_report import TargetDatasetReport
from wayfindinglib.models.quality.statements import TargetStatement
from wayfindinglib.models.session.alignment_records import AlignmentRecords
from wayfindinglib.models.session.control_results import FrameGuidingReport
from wayfindinglib.models.session.ekos_session import EkosSessionContext
from wayfindinglib.models.session.live_session_status import LiveSessionStatus
from wayfindinglib.models.session.night_list import NightListEntry, TargetChange
from wayfindinglib.models.session.night_quality import NightQuality
from wayfindinglib.models.session.review_query import ReviewQueryResult
from wayfindinglib.models.session.sky_rate_prediction import SkyRatePrediction
from wayfindinglib.models.session.telemetry import (
    AlignmentAttempt,
    AlignmentSessionsResult,
    GuidingSpectrumAnalysis,
    PolarAlignmentStatus,
)
from wayfindinglib.models.session.unguided_mount_error import UnguidedMountError

if TYPE_CHECKING:
    from wayfindinglib.api.control.control import ObservatoryControl
    from wayfindinglib.tasks.planning_tasks.advisory_tasks import AdvisoryKind

__all__ = ["ObservationReview"]

HistoryKind = Literal[
    "capture",
    "guiding",
    "sky_coverage",
    "recurring_issues",
    "ekos_sessions",
    "guiding_runs",
    "pointing_model",
    "alignment",
    "nights",
    "target_changes",
    "night_quality",
]
"""The kinds of question `ObservationReview.query` answers."""

TYPED_KINDS = ("nights", "target_changes", "night_quality")
"""The kinds that answer with a typed list of models, not a dictionary.

`ObservationReview._answer_typed` serves them. A new typed night read (for
example ``"night_quality"``) is added in two places: here, and as a new
``case`` in `_answer_typed`, with its arguments in
`night_history.TYPED_KIND_ARGUMENTS`."""

TYPED_KIND_FIELDS = {
    "nights": "night_list",
    "target_changes": "target_changes",
    "night_quality": "night_quality",
}
"""The `ReviewQueryResult` field each typed kind fills."""

FRAME_GUIDING_SECTIONS = ("quality",)
"""Optional sections `ObservationReview.frame_guiding` can add."""


ALIGNMENT_STATUSES = ("solving", "failed", "warning", "aligned", "idle")
"""The statuses an alignment attempt can have."""


def _attempt_from_row(row: dict[str, Any], with_session: bool) -> AlignmentAttempt:
    """Turn one stored alignment row into an `AlignmentAttempt`.

    Parameters
    ----------
    row : `dict` [`str`, `Any`]
        A row of the stored alignment attempts, keyed by column name.
    with_session : `bool`
        Whether to keep the row's session id.

    Returns
    -------
    attempt : `AlignmentAttempt`
        A status the model does not know reads as ``aligned``.
    """
    status = row.get("status")
    return AlignmentAttempt(
        status=status if status in ALIGNMENT_STATUSES else "aligned",
        delta_ra_arcsec=row.get("delta_ra_arcsec"),
        delta_dec_arcsec=row.get("delta_dec_arcsec"),
        ra=row.get("mount_ra"),
        dec=row.get("mount_dec"),
        pointing_error_arcsec=row.get("pointing_error_arcsec"),
        timestamp=row.get("timestamp"),
        target_name=row.get("target_name"),
        session_id=row.get("session_id") if with_session else None,
    )


def _polar_status_from_row(row: dict[str, Any]) -> PolarAlignmentStatus:
    """Turn one stored polar alignment row into a `PolarAlignmentStatus`.

    Parameters
    ----------
    row : `dict` [`str`, `Any`]
        A row of the stored polar alignment runs.

    Returns
    -------
    status : `PolarAlignmentStatus`
        The run. A status the model does not know reads as ``aligned``.
    """
    status = row.get("status", "aligned")
    return PolarAlignmentStatus(
        status=status if status in ("idle", "in_progress", "aligned", "warning") else "aligned",
        total_error_arcsec=row.get("total_error_arcsec"),
        alt_error_arcsec=row.get("alt_error_arcsec"),
        az_error_arcsec=row.get("az_error_arcsec"),
        pole_ra=row.get("pole_ra"),
        pole_dec=row.get("pole_dec"),
        paa_points=row.get("paa_points", []),
        timestamp=row.get("timestamp"),
    )


class ObservationReview(ControlChild):
    """Read and analyse past and current observing sessions.

    Parameters
    ----------
    control : `ObservatoryControl`
        The `Wayfinder`'s hardware sub-API. Review shares its context
        (configuration, storage and records) and holds no other state.
    """

    def __init__(self, control: ObservatoryControl) -> None:
        """Share the context of `control`."""
        super().__init__(control._context)

    @background_job("diagnostics", grace_period_seconds=20.0)
    def query(
        self,
        kind: HistoryKind,
        session_id: str | None = None,
        ekos_file_id: str | None = None,
        include: list[str] | None = None,
        limit: int = 10,
        register_job: bool = True,
    ) -> ReviewQueryResult:
        """Analyse or list past observing nights, in replies of bounded size.

        One front door for the observatory's history. It only reads. The
        plain data in a reply is measured and shrunk if needed, so it never
        exceeds 40,000 characters of JSON. The kinds in `TYPED_KINDS`
        fill a typed field instead (`night_list`, `target_changes` or
        `night_quality`), each bounded by `limit`.

        Parameters
        ----------
        kind : `str`
            ``"capture"`` or ``"guiding"`` (a night's analysis with
            `session_id`, otherwise one summary row per recent night),
            ``"sky_coverage"`` (all nights; slow), ``"recurring_issues"``,
            ``"ekos_sessions"`` (a list, or one session with
            `ekos_file_id`), ``"guiding_runs"``, ``"pointing_model"``
            (needs `session_id`), or ``"alignment"`` (the plate solves that
            checked the mount's pointing: one summary per night, with the
            night's mean pointing and tracking jitter, or, with
            `session_id`, one night's attempts and the same attempts
            grouped into one `AlignmentTargetSession` per target, with
            jitter and drift rates), ``"nights"`` (a typed list of the
            recorded nights and what data each has), or
            ``"target_changes"`` (a typed list of the slews that moved the
            mount to a new place in the sky, each matched to the nearest
            library target), or ``"night_quality"`` (needs `session_id`: a
            typed review of one night, with where each target's time went,
            its warnings and a guiding summary).
        session_id : `str`, optional
            An observing night, named for the local date on which it began,
            for example ``"2026-09-24"``. Used by ``capture``,
            ``guiding``, ``ekos_sessions``, ``guiding_runs``,
            ``pointing_model``, ``alignment``, ``target_changes`` and
            ``night_quality`` (which requires it).
        ekos_file_id : `str`, optional
            One Ekos session's id. Used only by ``ekos_sessions``.
        include : `list` [`str`], optional
            Sections of that one Ekos session to return: ``captures``,
            ``aborted_captures``, ``autofocus_runs``, ``align_events``,
            ``guide_state_events``, ``mount_state_events``,
            ``temperatures``, ``mount_positions``, ``equipment``. Used
            only by ``ekos_sessions`` with `ekos_file_id`. Without it, only
            an overview comes back.
        limit : `int`, optional
            How many of the most recent nights, runs, sessions or target
            changes to cover, and how many items of each Ekos section. From
            1 to 50.
        register_job : `bool`, optional
            Record the analysis as a job in the job history, with its log.
            Defaults to `True`.

        Returns
        -------
        result : `ReviewQueryResult`
            The answer. Each kind fills its own fields: ``nights`` fills
            ``night_list`` with `NightListEntry` rows, ``target_changes``
            fills ``target_changes`` with `TargetChange` rows and
            ``night_quality`` fills ``night_quality`` with a `NightQuality`.
            If plain data had to be cut to fit, ``truncated`` is true. An
            unknown `kind`, or an argument that `kind` does not use, is
            refused with `InvalidArgumentError`. A night or Ekos session
            with nothing recorded raises `NotFoundError`.
        """
        from wayfindinglib.tasks.control_tasks import night_history

        with registered_job(
            enabled=register_job and get_current_job() is None,
            job_type="diagnostics",
            target_id=kind,
        ):
            if kind in TYPED_KINDS:
                typed = self._answer_typed(kind, session_id, ekos_file_id, include, limit)
                return ReviewQueryResult(kind=kind, **{TYPED_KIND_FIELDS[kind]: typed})
            return ReviewQueryResult.model_validate(
                night_history.build_night_history(
                    self._context, kind, session_id, ekos_file_id, include, limit
                )
            )

    def _answer_typed(
        self,
        kind: str,
        session_id: str | None,
        ekos_file_id: str | None,
        include: list[str] | None,
        limit: int,
    ) -> list[NightListEntry] | list[TargetChange] | NightQuality:
        """Answer a question whose reply is a typed model.

        Parameters
        ----------
        kind : `str`
            One of `TYPED_KINDS`.
        session_id, ekos_file_id, include, limit
            As in `query`.

        Returns
        -------
        reply : `list` or `NightQuality`
            The typed reply for `kind`.

        Raises
        ------
        InvalidArgumentError
            If an argument is given that `kind` does not use, or
            ``night_quality`` is asked without a night.
        """
        from wayfindinglib.tasks.control_tasks import night_history, night_quality_task, target_changes

        night_history.refuse_unused_arguments(
            kind, night_history.TYPED_KIND_ARGUMENTS[kind], session_id, ekos_file_id, include
        )
        limit = night_history.clamp_limit(limit)
        match kind:
            case "nights":
                return night_history.build_night_list(self._context, limit)
            case "target_changes":
                return target_changes.find_target_changes(self._context, session_id, limit)
            case "night_quality":
                if session_id is None:
                    raise InvalidArgumentError("kind 'night_quality' needs session_id, the night to review.")
                return night_quality_task.build_night_quality(self._context, session_id)
        raise InvalidArgumentError(f"kind {kind!r} is not a typed kind.")

    def get_live_session_status(
        self,
        window_minutes: float = 10.0,
        refresh: bool = True,
        destination_dir: str | None = None,
        include: list[str] | None = None,
        limit: int = 10,
    ) -> LiveSessionStatus:
        """Report how the current observing session is going, right now.

        Reads the observatory computer's latest Ekos analyze log and KStars
        text log, and reports the guiding accuracy over the last few
        minutes, the latest exposures with any that look ruined, each
        dither and whether it worked, guiding excursions, the pier side,
        the camera temperature, and exposure counts. Nothing is stored in
        the library; ``refresh`` only copies the newest logs into a local
        folder.

        Parameters
        ----------
        window_minutes : `float`, optional
            Length of the recent-guiding window, in minutes.
        refresh : `bool`, optional
            Whether to download the latest logs first. `False` reads what
            is already in `destination_dir`.
        destination_dir : `str`, optional
            Local folder for the logs. ``ekos_logs`` inside the wayfinding
            library's data folder when omitted.
        include : `list` [`str`], optional
            Sections of tonight's Ekos record to add under ``details``:
            ``captures``, ``aborted_captures``, ``autofocus_runs``,
            ``align_events``, ``guide_state_events``,
            ``mount_state_events``, ``temperatures`` and
            ``mount_positions``. Times are Unix seconds.
        limit : `int`, optional
            Most items per section, from 1 to 50. A longer list is sampled
            evenly, keeping the first and last.

        Returns
        -------
        status : `LiveSessionStatus`
            Kept under 30,000 characters of JSON: if the exposures,
            excursions, dithers or detail sections would be longer, the
            oldest items are dropped and a line in ``unavailable`` says so.

        Raises
        ------
        NotFoundError
            If no readable Ekos analyze log exists.
        """
        from wayfindinglib.tasks.control_tasks import live_session_status_task

        if destination_dir is None:
            destination_dir = self._context.ekos_log_directory()
        status = live_session_status_task.get_live_session_status(
            self._context,
            destination_dir,
            window_minutes=window_minutes,
            refresh=refresh,
            include=include,
            limit=limit,
        )
        if status is None:
            raise NotFoundError(
                f"No readable Ekos analyze log found in {destination_dir}.",
                details={"destination_dir": destination_dir},
            )
        return live_session_status_task.fit_status_to_budget(status)

    def frame_guiding(
        self,
        target: str | Target,
        filter_name: str | None = None,
        first_file: str | None = None,
        last_file: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 60,
        include: list[str] | None = None,
    ) -> FrameGuidingReport:
        """Report the guide error during each light frame's exposure.

        Cuts the stored guide-log samples to the window of each frame and
        gives the sample count, how much of the window the samples cover,
        and the RMS and peak error in arcseconds. Use it to tell whether
        wind, a dither or a drift spoiled a frame. Nothing is stored.

        Parameters
        ----------
        target : `str` or `Target`
            The library target whose light frames to match.
        filter_name : `str`, optional
            Only frames of this filter, such as ``"L"``. Spectroscopy
            frames are left out.
        first_file : `str`, optional
            Only frames from this file onward. A bare number such as
            ``"013"`` means the frame numbered 013.
        last_file : `str`, optional
            Only frames up to this file or number.
        since : `str`, optional
            Only frames taken at or after this ISO 8601 time. No offset
            means UTC.
        until : `str`, optional
            Only frames taken at or before this ISO 8601 time.
        limit : `int`, optional
            How many frames to cover, from 1 to 200. With a range or time
            these are the first frames inside it; otherwise the newest.
        include : `list` [`str`], optional
            ``["quality"]`` also puts each frame's image measurements on
            its row: star count, star width, roundness, longest trail, sky
            level, saturated pixels and quality flags. Slow (about a
            second a frame), so at most 60 frames are measured.

        Returns
        -------
        report : `FrameGuidingReport`
            ``frames`` (one row per frame) and ``group`` (the median error
            and the frames well above it). An unknown target raises
            `NotFoundError`. A `since` or `until` that is not an ISO 8601
            time, or an `include` that names an unknown section, raises
            `InvalidArgumentError`.
        """
        from wayfindinglib.tasks.control_tasks import frame_guiding, hardware_operations

        sections = hardware_operations.check_sections(include or [], FRAME_GUIDING_SECTIONS)
        selection = frame_guiding.FrameRange(
            filter_name=filter_name, first_file=first_file, last_file=last_file, since=since, until=until
        )
        target_id = target if isinstance(target, str) else target.id
        return FrameGuidingReport.model_validate(
            frame_guiding.link_frames_to_guiding(
                self._context, target_id, selection, limit, "quality" in sections
            )
        )

    def query_alignment_sessions(self, attempts: list[dict[str, Any]]) -> AlignmentSessionsResult:
        """Group plate-solve alignment attempts into one session per target.

        A pure calculation on the attempts you pass in. Nothing is read
        from or stored in the library. Solves with the same target name
        form one session. A solve without a name joins the session whose
        first solve lies within 0.5 degrees, or starts its own. See
        `AlignmentTargetSession` for the statistics each session carries.

        Parameters
        ----------
        attempts : `list` [`dict`]
            Plate solves in any order. Each dictionary may hold
            ``status`` (``"solving"``, ``"failed"``, ``"warning"``,
            ``"aligned"`` or ``"idle"``; required), ``delta_ra_arcsec``,
            ``delta_dec_arcsec``, ``ra`` and ``dec`` in degrees,
            ``pointing_error_arcsec``, ``timestamp`` (Unix seconds),
            ``target_name`` and ``session_id``. The camelCase spellings
            (``deltaRaArcsec`` and so on) are accepted too.

        Returns
        -------
        result : `AlignmentSessionsResult`
            ``attempts`` (the attempts as the library read them) and
            ``targets`` (one `AlignmentTargetSession` per target, in the
            order the targets were first solved; empty when no solve has a
            position).

        Raises
        ------
        InvalidArgumentError
            If an attempt has an unknown status, or a field of the wrong
            type.
        """
        from pydantic import ValidationError

        from wayfindinglib.analytics.alignment_sessions import group_alignment_attempts

        try:
            models = [AlignmentAttempt.model_validate(attempt) for attempt in attempts]
        except ValidationError as error:
            raise InvalidArgumentError(f"An alignment attempt is not valid: {error}") from error
        return AlignmentSessionsResult(attempts=models, targets=group_alignment_attempts(models))

    def query_alignment_records(
        self,
        session_id: str | None = None,
        limit: int = 50,
        since: float | None = None,
        targets: list[Target] | None = None,
    ) -> AlignmentRecords:
        """Read the stored alignment attempts, grouped by target.

        With no `session_id` this is the live view: the `limit` newest
        attempts, oldest first. With a `session_id` it is the night view:
        every attempt of that night, a tracking row for each target imaged
        that night (when `targets` is given), and the latest polar
        alignment run.

        Parameters
        ----------
        session_id : `str`, optional
            An observing night, such as ``"2026-09-24"``. ``"all"`` reads
            every night. `None` reads the newest attempts instead.
        limit : `int`, optional
            Most attempts to read when `session_id` is `None`. Defaults
            to 50.
        since : `float`, optional
            When `session_id` is `None`, drop attempts older than this Unix
            time. For a night view it is ignored.
        targets : `list` [`Target`], optional
            The library targets whose frames show when each was tracked.
            Used only with a `session_id`.

        Returns
        -------
        records : `AlignmentRecords`
            The attempts, their grouping by target, and the polar run.
            A status the attempt model does not know reads as ``aligned``.
            Nothing is returned for records that cannot be read.
        """
        records = self._context.records
        if session_id is None:
            rows = list(reversed(records.get_alignment_attempts(limit=limit)))
            if since is not None:
                rows = [row for row in rows if (row.get("timestamp") or 0) >= since]
            attempts = [_attempt_from_row(row, with_session=False) for row in rows]
            polar = None
        else:
            attempts = [
                _attempt_from_row(row, with_session=True)
                for row in records.get_session_alignment_attempts(session_id)
            ]
            if targets is not None:
                attempts.extend(
                    AlignmentAttempt.model_validate(row)
                    for row in records.get_session_target_telemetry(session_id, targets)
                )
            polar_session = None if session_id in ("all", "*") else session_id
            runs = records.get_polar_alignments(session_id=polar_session, limit=1)
            polar = _polar_status_from_row(runs[0]) if runs else None
        return AlignmentRecords(
            attempts=attempts, targets=group_alignment_attempts(attempts), polar_alignment=polar
        )

    def get_performance_envelope(
        self,
        blur_tolerance_fraction: float | None = None,
        before_night: str | None = None,
        exclude_nights: list[str] | None = None,
    ) -> PerformanceEnvelope | None:
        """Work out the performance limits for the equipment in use now.

        Every limit is derived on the spot from the active equipment, the
        camera's stored profile, the star width this equipment's own
        frames show, and how it behaved in earlier sessions. Nothing is
        stored, so changing the active equipment changes every limit. A
        limit without enough data says so instead of giving a guess.

        Parameters
        ----------
        blur_tolerance_fraction : `float`, optional
            The most guiding error and trailing may widen a star image, as
            a fraction of its width. Defaults to 0.10.
        before_night : `str`, optional
            Use only this equipment's nights earlier than this one (such as
            ``"2026-09-24"``) for the baseline limits. Every recorded night
            when omitted.
        exclude_nights : `list` [`str`], optional
            Nights to leave out of every source of the limits (guiding,
            session records, frames and plate solves), such as
            ``["2026-09-24"]``. Use it to fit the equipment without one
            night, then compare that night with the result.

        Returns
        -------
        envelope : `PerformanceEnvelope` or `None`
            The limits, with ``tracking_risk``: a grid scoring how risky
            each hour angle and declination is for tracking, from the
            mount's geometry and the jitter measured in every recorded
            plate solve, judged against this camera's plate scale. `None`
            if no telescope and camera are active.
        """
        from wayfindinglib.tasks.control_tasks import night_analysis, performance_envelope_tasks

        contexts, runs = night_analysis.recorded_sessions(self._context)
        envelope = night_analysis.performance_envelope(
            self._context,
            blur_tolerance_fraction,
            before_night,
            contexts,
            runs,
            {},
            exclude_nights=exclude_nights,
        )
        if envelope is None:
            return None
        risk = performance_envelope_tasks.tracking_risk_map(self._context, exclude_nights)
        return envelope.model_copy(update={"tracking_risk": risk})

    def measure_unguided_mount_error(
        self, session_id: str | None = None, exclude_nights: list[str] | None = None
    ) -> UnguidedMountError:
        """Measure the mount's own error from the recorded guide logs.

        A guide log holds the error the guide star showed and every
        correction the guider sent. Adding the corrections back (signed pulse
        length times the guide rate) gives the error the mount would have made
        unguided. This fits, for each guiding run, the mount's periodic error
        in right ascension (period, amplitude and phase, each with its
        uncertainty) and, on both axes, the slow drift and the random jitter
        that remain. It then pools the runs. Nothing is stored.

        The numbers are what a simulated mount needs: amplitudes are zero to
        peak in arcseconds on the sky, periods are in seconds, phases are in
        radians, drifts are in arcseconds per minute. The size of the
        reconstructed error rests on the guide rate the log states (PHD2
        measures it at calibration). The random terms include the guide
        star's measurement noise, so they are upper bounds on the mount's
        own jitter.

        Parameters
        ----------
        session_id : `str`, optional
            Measure only this observing night, such as ``"2026-09-24"``.
            Every recorded night when omitted.
        exclude_nights : `list` [`str`], optional
            Nights to leave out, for a leave-one-out check.

        Returns
        -------
        result : `UnguidedMountError`
            ``runs`` (one entry per guiding run with samples and guide
            rates), ``summary`` (the pooled repeating error and the pooled
            drift and random terms) and ``excluded_nights``. A run too short
            or too sparse for a repeating-error fit says so in its ``notes``.
        """
        from wayfindinglib.tasks.control_tasks import mount_error_tasks

        return mount_error_tasks.measure_unguided_mount_error(self._context, session_id, exclude_nights)

    def measure_guiding_spectrum(
        self, session_id: str | None = None, exclude_nights: list[str] | None = None
    ) -> GuidingSpectrumAnalysis:
        """Work out the guiding spectrum, storing nothing.

        This is the analysis `control.guiding.refit_spectrum` runs, without
        saving the result as the mount's periodic error model. It shows what
        the guider left uncorrected, not the mount's unguided error (use
        `measure_unguided_mount_error` for that).

        Parameters
        ----------
        session_id : `str`, optional
            Use only this observing night. Every recorded night when omitted.
        exclude_nights : `list` [`str`], optional
            Nights to leave out, for a leave-one-out check.

        Returns
        -------
        analysis : `GuidingSpectrumAnalysis`
            The repeating-error size, the dominant periods and the Dec
            backlash estimate. Its ``message`` says when there were too few
            samples.
        """
        from wayfindinglib.tasks.control_tasks import mount_error_tasks

        return mount_error_tasks.measure_guiding_spectrum(self._context, session_id, exclude_nights)

    def measure_focus_temperature_coefficient(
        self, exclude_nights: list[str] | None = None, source: FocusSource = "both"
    ) -> FocusTemperatureFit:
        """Fit how the best focus moves with temperature.

        Uses the active telescope and camera's own history: the position Ekos
        chose in each recorded autofocus run, and the focuser position and
        temperature in the light-frame headers each time the position
        changed. Each filter gets its own offset and all share one slope.
        Nothing is stored.

        Parameters
        ----------
        exclude_nights : `list` [`str`], optional
            Nights to leave out, for a leave-one-out check.
        source : `str`, optional
            ``"autofocus_runs"``, ``"frames"`` or ``"both"`` (the default: a
            night with autofocus runs uses them, and frame headers fill the
            other nights).

        Returns
        -------
        fit : `FocusTemperatureFit`
            The slope in focuser steps per degree Celsius with its
            uncertainty, the pairs and nights used, and the temperature range
            it covers. With too little history the slope is `None` and
            ``message`` says why. An unknown `source` raises
            `InvalidArgumentError`.
        """
        from wayfindinglib.tasks.control_tasks import focus_model_tasks

        return focus_model_tasks.measure_focus_temperature_coefficient(self._context, exclude_nights, source)

    def get_sky_rate_prediction(
        self,
        epoch_seconds: float,
        ra_deg: float,
        dec_deg: float,
        filter_name: str | None = None,
        exclude_nights: list[str] | None = None,
    ) -> SkyRatePrediction:
        """Predict the sky background rate for a pointing and a time.

        Uses the sky model fitted to the sky rates of the active telescope
        and camera's own frames: a dark-sky level, an airmass term and a
        Moon term. The prediction is a model, labeled with its error on
        nights it was not fitted to. Nothing is stored.

        Parameters
        ----------
        epoch_seconds : `float`
            The time, in seconds since the Unix epoch.
        ra_deg : `float`
            Right ascension the telescope points at, in degrees.
        dec_deg : `float`
            Declination the telescope points at, in degrees.
        filter_name : `str`, optional
            The filter. The setting with the most fitted nights when omitted.
        exclude_nights : `list` [`str`], optional
            Nights whose frames are left out of the model, for a
            leave-one-out check.

        Returns
        -------
        prediction : `SkyRatePrediction`
            The rate in electrons per second per pixel and in ADU per second
            per pixel (through the camera profile's gain), the Moon-free
            rate, and the model's error. When there is no model, or the
            target is below the horizon, the rates are `None` and
            ``message`` says why. A declination outside -90 to 90 degrees
            raises `InvalidArgumentError`.
        """
        from wayfindinglib.tasks.control_tasks import sky_rate_tasks

        return sky_rate_tasks.get_sky_rate_prediction(
            self._context, epoch_seconds, ra_deg, dec_deg, filter_name, exclude_nights
        )

    def get_advisory(
        self,
        kind: AdvisoryKind,
        target: str | Target,
        *,
        object_id: str | None = None,
    ) -> TargetDatasetReport | list[TargetStatement]:
        """Judge a target's data or one result.

        This judges data and results that already exist. Tonight's ranking
        and calibration advice are `planning.get_tonight_advice` and
        `planning.get_calibration_advice`. The advice is worked out on
        demand and never stored.

        Parameters
        ----------
        kind : `str`
            ``"quality"``: the target dataset report. It summarizes each
            capture setting, lists warnings and recommendations in priority
            order, and gives a starting point when the target has no frames.
            ``"result"``: how far one per-star science result can be
            trusted, as statements. Needs `object_id`.
        target : `str` or `Target`
            The target, by id or as a `Target`.
        object_id : `str`, optional
            ``kind="result"`` only. The star whose result is judged.

        Returns
        -------
        advice : `TargetDatasetReport` or `list` [`TargetStatement`]
            The advice for the chosen kind. `InvalidArgumentError` is raised
            if `kind` is unknown, an argument the kind does not use is
            given, or a required argument is missing.
        """
        from wayfindinglib.tasks.planning_tasks.advisory_tasks import build_advisory

        return build_advisory(
            kind,
            target,
            object_id,
            astrometrics=self._context.astrometrics,
            butler=self._context.butler,
            config=self._context.config,
        )

    def _save_ekos_session_context(self, context: EkosSessionContext) -> None:
        """Save one Ekos session record, replacing an earlier read of it.

        Parameters
        ----------
        context : `EkosSessionContext`
            The session record, keyed by its analyze file's name.
        """
        self._context.save_ekos_session_context(context)
