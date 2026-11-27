"""Purpose: `ReviewNights`, the recorded observing nights (``review.nights``).

Description: Lists the recorded nights and reads what each holds: one
night's quality review, its capture analysis and the slews that changed
target, the sky coverage over every night, the findings that recur, the
Ekos session records, and the session in progress. The slow reads are
marked `background_job`, so a server runs them as a job. Only `get_live_status`
with ``force=True`` copies files (the newest logs, into a local folder);
nothing here writes to the library. Each method is a thin wrapper of one
owner function.
"""

from __future__ import annotations

from astrometricslib.arguments import Page
from astrometricslib.jobs import background_job
from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.session.capture_quality import CaptureSessionAnalysis
from wayfindinglib.models.session.ekos_session import EkosSessionContext, EkosSessionSummary
from wayfindinglib.models.session.live_session_status import LiveSessionStatus
from wayfindinglib.models.session.night_list import NightListEntry, TargetChange
from wayfindinglib.models.session.night_quality import NightQuality
from wayfindinglib.models.session.recurring_issue import RecurringIssue
from wayfindinglib.models.session.sky_quality import SkyAnalysis

__all__ = ["ReviewNights"]


class ReviewNights(ControlChild):
    """The recorded observing nights, reached as ``wayfinder.review.nights``.

    A night is named for the local date on which it began, such as
    ``"2026-09-24"``.
    """

    def query(self, *, limit: int | None = None, offset: int = 0) -> Page[NightListEntry]:
        """List the recorded observing nights and what data each has.

        A night is listed if it has light frames from the active equipment,
        an Ekos session record or a guiding run.

        Owner:
            wayfindinglib.tasks.control_tasks.night_history.query_nights

        Parameters
        ----------
        limit : `int`, optional
            The most nights to return. `None` (the default) returns every
            night.
        offset : `int`, optional
            How many nights to skip first. Defaults to 0.

        Returns
        -------
        nights : `Page` [`NightListEntry`]
            The nights, oldest first.

        Notes
        -----
        Raises `InvalidArgumentError` if `limit` is below 1 or `offset` is
        below 0, and `StorageError` if the recorded sessions cannot be read.
        """
        from wayfindinglib.tasks.control_tasks.night_history import query_nights

        return query_nights(self._context, limit=limit, offset=offset)

    @background_job("diagnostics", grace_period_seconds=20.0)
    def get(self, night: str, *, required: bool = False) -> NightQuality | None:
        """Read the quality review of one night.

        It says where each target's time went, its warnings and a guiding
        summary. The review is kept until the data behind it change.

        Owner:
            wayfindinglib.tasks.control_tasks.night_quality_task.get_night_quality

        Parameters
        ----------
        night : `str`
            The observing night, such as ``"2026-09-24"``.
        required : `bool`, optional
            Raise `NotFoundError` instead of returning `None` when nothing
            is recorded for the night.

        Returns
        -------
        review : `NightQuality` or `None`
            The night's review, or `None` when nothing is recorded for it.
        """
        from wayfindinglib.tasks.control_tasks.night_quality_task import get_night_quality

        return get_night_quality(self._context, night, required=required)

    def get_target_changes(self, night: str | None = None) -> list[TargetChange]:
        """List the slews that moved the mount to a new place in the sky.

        Each change is matched to the nearest library target.

        Owner:
            wayfindinglib.tasks.control_tasks.target_changes.find_target_changes

        Parameters
        ----------
        night : `str`, optional
            Only this observing night. Every recorded night when omitted.

        Returns
        -------
        changes : `list` [`TargetChange`]
            The changes, oldest first. Empty when no recorded session has a
            slew that ended in tracking.
        """
        from wayfindinglib.tasks.control_tasks.target_changes import find_target_changes

        return find_target_changes(self._context, night)

    @background_job("diagnostics", grace_period_seconds=20.0)
    def get_capture(self, night: str) -> CaptureSessionAnalysis:
        """Analyse one night's captured frames.

        Whether every exposure Ekos finished reached the frame library,
        whether frames carry the measurements later steps need, clipped
        stars, star sharpness and roundness, how much of the night was spent
        exposing, and the recommendations these lead to. The night is judged
        only against the equipment's earlier nights.

        Owner:
            wayfindinglib.tasks.control_tasks.night_history.get_capture_analysis

        Parameters
        ----------
        night : `str`
            The observing night, such as ``"2026-09-24"``.

        Returns
        -------
        analysis : `CaptureSessionAnalysis`
            The night's capture analysis.

        Notes
        -----
        Raises `NotFoundError` if nothing is recorded for the night or no
        telescope and camera are active.
        """
        from wayfindinglib.tasks.control_tasks.night_history import get_capture_analysis

        return get_capture_analysis(self._context, night)

    @background_job("diagnostics", grace_period_seconds=20.0)
    def get_sky_coverage(self) -> SkyAnalysis:
        """Analyse where on the sky the active equipment has imaged.

        Slow: it reads every night.

        Owner:
            wayfindinglib.tasks.control_tasks.night_history.get_sky_coverage

        Returns
        -------
        analysis : `SkyAnalysis`
            The sky-coverage analysis.

        Notes
        -----
        Raises `ConflictError` if no telescope and camera are active.
        """
        from wayfindinglib.tasks.control_tasks.night_history import get_sky_coverage

        return get_sky_coverage(self._context)

    @background_job("diagnostics", grace_period_seconds=20.0)
    def get_recurring_issues(self, *, latest_nights: int | None = None) -> list[RecurringIssue]:
        """List the findings that recur across nights.

        Owner:
            wayfindinglib.tasks.control_tasks.night_analysis.recurring_issues

        Parameters
        ----------
        latest_nights : `int`, optional
            Look only at this many of the most recent nights. Every night
            when omitted.

        Returns
        -------
        issues : `list` [`RecurringIssue`]
            One entry per finding seen on more than one night.
        """
        from wayfindinglib.tasks.control_tasks.night_analysis import recurring_issues

        return recurring_issues(self._context, latest_nights=latest_nights)

    def query_ekos_sessions(
        self, *, night: str | None = None, limit: int | None = None, offset: int = 0
    ) -> Page[EkosSessionSummary]:
        """List the recorded Ekos sessions, without their detail.

        Owner:
            wayfindinglib.tasks.control_tasks.night_history.query_ekos_sessions

        Parameters
        ----------
        night : `str`, optional
            Only the sessions of this observing night.
        limit : `int`, optional
            The most sessions to return. `None` (the default) returns every
            session.
        offset : `int`, optional
            How many sessions to skip first. Defaults to 0.

        Returns
        -------
        sessions : `Page` [`EkosSessionSummary`]
            One summary per session, oldest first: its id, night, times,
            equipment and how many items each section holds.
        """
        from wayfindinglib.tasks.control_tasks.night_history import query_ekos_sessions

        return query_ekos_sessions(self._context, night=night, limit=limit, offset=offset)

    def get_ekos_session(
        self, ekos_id: str, *, include: list[str] | None = None
    ) -> EkosSessionContext | None:
        """Read one recorded Ekos session.

        Owner:
            wayfindinglib.tasks.control_tasks.night_history.get_ekos_session

        Parameters
        ----------
        ekos_id : `str`
            The session's id, the analyze file's time stamp, such as
            ``"2026-09-23T20-31-48"`` (from `query_ekos_sessions`).
        include : `list` [`str`], optional
            The sections to fill in: ``captures``, ``aborted_captures``,
            ``autofocus_runs``, ``align_events``, ``guide_state_events``,
            ``mount_state_events``, ``temperatures``, ``mount_positions``
            and ``equipment``. The others come back empty. Without it, only
            the id, night and times are filled.

        Returns
        -------
        session : `EkosSessionContext` or `None`
            The session, or `None` if none has that id.

        Notes
        -----
        Raises `InvalidArgumentError` if `include` names an unknown section.
        """
        from wayfindinglib.tasks.control_tasks.night_history import get_ekos_session

        return get_ekos_session(self._context, ekos_id, include=include)

    def get_live_status(
        self,
        *,
        window_min: float | None = None,
        force: bool = False,
        include: list[str] | None = None,
    ) -> LiveSessionStatus:
        """Report how the current observing session is going, right now.

        Reads the newest Ekos analyze log and KStars text log, and reports
        the guiding accuracy over the last few minutes, the latest exposures
        with any that look ruined, each dither and whether it worked,
        guiding excursions, the pier side, the camera temperature, and
        exposure counts. Nothing is stored in the library.

        Owner:
            wayfindinglib.tasks.control_tasks.live_session_status_task.get_live_status

        Parameters
        ----------
        window_min : `float`, optional
            Length of the recent-guiding window, in minutes. `None` uses the
            live status's own window (ten minutes).
        force : `bool`, optional
            Copy the newest logs from the observatory computer first (into
            ``ekos_logs`` in the wayfinding library's data folder). `False`
            reads the logs already copied, for example by
            ``control.remote.sync_logs``.
        include : `list` [`str`], optional
            Sections of tonight's Ekos record to add under ``details``:
            ``captures``, ``aborted_captures``, ``autofocus_runs``,
            ``align_events``, ``guide_state_events``,
            ``mount_state_events``, ``temperatures`` and
            ``mount_positions``. Each keeps 10 items, spread over the night.
            Times are Unix seconds.

        Returns
        -------
        status : `LiveSessionStatus`
            Kept under 30,000 characters of JSON: if the exposures,
            excursions, dithers or detail sections would be longer, the
            oldest items are dropped and a line in ``unavailable`` says so.

        Notes
        -----
        Raises `NotFoundError` if no readable Ekos analyze log exists, and
        `InvalidArgumentError` if `include` names an unknown section.
        """
        from wayfindinglib.tasks.control_tasks.live_session_status_task import get_live_status

        return get_live_status(self._context, window_min=window_min, force=force, include=include)
