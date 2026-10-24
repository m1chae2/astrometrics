"""Purpose: `control.history`, past and current observing sessions.

Description: Answers questions about recorded observing nights (the
capture, guiding and sky-coverage analyses, findings that recur, Ekos
session records, guiding runs and the pointing model), reports how the
session in progress is going, lists the plate-solve alignment attempts
of each night, matches each light frame to the guide error
during its exposure, and works out the performance limits of the active
equipment. Only `save_ekos_session_context` writes. The analyses live in
`tasks.control_tasks.night_analysis` and `night_history`.
"""

from typing import Any, Literal

from astrometricslib import (
    InvalidArgumentError,
    NotFoundError,
    Target,
    background_job,
    get_current_job,
    registered_job,
)
from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
from wayfindinglib.models.session.ekos_session import EkosSessionContext
from wayfindinglib.models.session.live_session_status import LiveSessionStatus
from wayfindinglib.models.session.night_list import NightListEntry, TargetChange
from wayfindinglib.models.session.night_quality import NightQuality

__all__ = ["HistoryControl"]

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
"""The kinds of question `HistoryControl.query` answers."""

TYPED_KINDS = ("nights", "target_changes", "night_quality")
"""The kinds that answer with a typed list of models, not a dictionary.

`HistoryControl._answer_typed` serves them. A new typed night read (for
example ``"night_quality"``) is added in two places: here, and as a new
``case`` in `_answer_typed`, with its arguments in
`night_history.TYPED_KIND_ARGUMENTS`."""

FRAME_GUIDING_SECTIONS = ("quality",)
"""Optional sections `HistoryControl.frame_guiding` can add."""


class HistoryControl(ControlChild):
    """Read and analyse past and current observing sessions."""

    @background_job("diagnostics", grace_period_seconds=20.0)
    def query(
        self,
        kind: HistoryKind,
        session_id: str | None = None,
        ekos_file_id: str | None = None,
        include: list[str] | None = None,
        limit: int = 10,
        register_job: bool = True,
    ) -> dict[str, Any] | list[NightListEntry] | list[TargetChange] | NightQuality:
        """Analyse or list past observing nights, in replies of bounded size.

        One front door for the observatory's history. It only reads. A
        dictionary reply is measured and shrunk if needed, so it never
        exceeds 40,000 characters of JSON. The kinds in `TYPED_KINDS`
        answer with a list of typed models, each bounded by `limit`.

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
        reply : `dict` [`str`, `Any`] or `list`
            The answer: a dictionary, or for ``nights`` a list of
            `NightListEntry`, for ``target_changes`` a list of
            `TargetChange` and for ``night_quality`` a `NightQuality`. If a
            dictionary had to be cut to fit, ``truncated`` is true. An
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
                return self._answer_typed(kind, session_id, ekos_file_id, include, limit)
            return night_history.build_night_history(
                self._context, kind, session_id, ekos_file_id, include, limit
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
    ) -> dict[str, Any]:
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
        report : `dict`
            ``frames`` (one row per frame) and ``group`` (the median error
            and the frames well above it). An unknown target raises
            `NotFoundError`.

        Raises
        ------
        InvalidArgumentError
            If `since` or `until` is not an ISO 8601 time, or `include`
            names an unknown section.
        """
        from astrometricslib import FrameSelection, parse_iso_time
        from wayfindinglib.tasks.control_tasks import frame_guiding, hardware_operations

        sections = hardware_operations.check_sections(include or [], FRAME_GUIDING_SECTIONS)
        try:
            selection = FrameSelection(
                filter_name=filter_name,
                first_file=first_file,
                last_file=last_file,
                since=parse_iso_time(since),
                until=parse_iso_time(until),
            )
        except ValueError as error:
            raise InvalidArgumentError(f"since and until must be ISO 8601 times: {error}") from error
        target_id = target if isinstance(target, str) else target.id
        return frame_guiding.link_frames_to_guiding(
            self._context, target_id, selection, limit, "quality" in sections
        )

    def get_performance_envelope(
        self, blur_tolerance_fraction: float | None = None, before_night: str | None = None
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
            self._context, blur_tolerance_fraction, before_night, contexts, runs, {}
        )
        if envelope is None:
            return None
        risk = performance_envelope_tasks.tracking_risk_map(self._context)
        return envelope.model_copy(update={"tracking_risk": risk})

    def save_ekos_session_context(self, context: EkosSessionContext) -> None:
        """Save one Ekos session record, replacing an earlier read of it.

        Parameters
        ----------
        context : `EkosSessionContext`
            The session record, keyed by its analyze file's name.
        """
        self._context.save_ekos_session_context(context)
