"""Purpose: `PlanningSessions`, the night plans of planning.

Reached as ``wayfinder.planning.sessions``.

Description: A session is the plan of one observing night: a queue of entries
(each a frozen copy of an observation package), a checklist of fixes the user
means to try, and the framing of the field. There is one session per night.
`get` reads a night's session and `query` lists them; `create` records one,
`add` and `remove` change the queue and `update` changes the order, the
exposures of an entry, the framing and the checklist. Every change checks the
revision the session object carries and returns the new session. Each method
is a thin wrapper of one owner function in
`wayfindinglib.tasks.planning_tasks.session_records`.
"""

from datetime import date

from astrometricslib import ObservatoryConfiguration
from astrometricslib.arguments import Page, TimeInput
from wayfindinglib._internal.renames import MovedNames
from wayfindinglib.models.planning.observation_package import ExposureRequest, ObservationPackage
from wayfindinglib.models.session.observation_session import (
    ChecklistItem,
    Framing,
    ObservationSession,
    QueuedObservationPackage,
    QueueRequest,
    SessionStatus,
)
from wayfindinglib.tasks.planning_tasks.planning_context import PlanningContext

__all__ = ["PlanningSessions"]


class PlanningSessions(MovedNames):
    """The stored night plans, one per observing night.

    Parameters
    ----------
    context : `PlanningContext`
        The settings and storage the planning objects share.
    """

    def __init__(self, context: PlanningContext) -> None:
        self._context = context

    def get(
        self, night: str | date, include: list[str] | None = None, required: bool = False
    ) -> ObservationSession | None:
        """Read the session of one observing night.

        Owner:
            wayfindinglib.tasks.planning_tasks.session_records.get_session

        Parameters
        ----------
        night : `str` or `datetime.date`
            The observing night, the local date it began, such as
            ``"2026-09-24"``.
        include : `list` [`str`], optional
            Parts to add. ``"progress"`` counts the frames that arrived and
            sets the session's ``progress`` (for each entry the frames
            requested and captured, flagged and not judged, and a run state).
            ``"guiding_samples"``, ``"weather_samples"`` and
            ``"divergence_records"`` fill those lists, which are empty
            otherwise.
        required : `bool`, optional
            Raise `NotFoundError` instead of returning `None` when the night
            has no session.

        Returns
        -------
        session : `ObservationSession` or `None`
            The night's session, or `None` when it has none.

        Notes
        -----
        Raises `InvalidArgumentError` if the night is not a date or a part is
        unknown.
        """
        from wayfindinglib.tasks.planning_tasks.session_records import get_session

        return get_session(self._context, night, include or (), required)

    def query(
        self,
        *,
        status: SessionStatus | str | None = None,
        since: TimeInput | None = None,
        until: TimeInput | None = None,
        include: list[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Page[ObservationSession]:
        """List the stored sessions, newest night first.

        Owner:
            wayfindinglib.tasks.planning_tasks.session_records.query_sessions

        Parameters
        ----------
        status : `SessionStatus` or `str`, optional
            Only sessions with this status, such as ``"PLANNED"``.
        since, until : `str`, `datetime` or `Time`, optional
            Only nights from the date of ``since`` to the date of ``until``
            (UTC).
        include : `list` [`str`], optional
            Parts to add to each session, as for `get`.
        limit : `int`, optional
            Most sessions to return. `None` (the default) returns every match.
        offset : `int`, optional
            How many matches to skip first. Defaults to 0.

        Returns
        -------
        sessions : `Page` [`ObservationSession`]
            The matching sessions.
        """
        from wayfindinglib.tasks.planning_tasks.session_records import query_sessions

        return query_sessions(self._context, status, since, until, include or (), limit, offset)

    def create(
        self,
        night: str | date,
        configuration: ObservatoryConfiguration | None = None,
        *,
        packages: list[ObservationPackage | QueueRequest] | None = None,
    ) -> ObservationSession:
        """Record the session of one observing night.

        With no ``packages`` the session starts with an empty queue, to fill
        with `add`; a night that already has a session gets that session
        back. With ``packages`` the library places each one into the night
        automatically and lists those it cannot place, with the reason; a
        night that already has a session gets the new placement. No device is
        contacted.

        Owner:
            wayfindinglib.tasks.planning_tasks.session_records.create_session

        Parameters
        ----------
        night : `str` or `datetime.date`
            The observing night, such as ``"2026-09-24"``.
        configuration : `ObservatoryConfiguration`, optional
            The telescope and camera. The active ones when omitted.
        packages : `list`, optional
            Stored packages (placed as soon as possible) or `QueueRequest`
            objects (which also say when) to place automatically.

        Returns
        -------
        session : `ObservationSession`
            The stored session.

        Notes
        -----
        Raises `InvalidArgumentError` if the night is not a date, and
        `ConflictError` if an entry of the night's session already started.
        """
        from wayfindinglib.tasks.planning_tasks.session_records import create_session

        return create_session(self._context, night, configuration, packages)

    def add(
        self, session: ObservationSession, packages: list[ObservationPackage | QueueRequest]
    ) -> ObservationSession:
        """Append packages to the end of a session's queue.

        Each new entry freezes a copy of its package (exposures, dithering,
        altitude limit and priority). A planned package that was partly
        captured asks only for the frames still missing. Every exposure
        request is checked against the camera: a gain, offset or binning the
        camera profile did not measure is refused, and exact duplicates
        merge. A suggested package that is added becomes planned.

        Owner:
            wayfindinglib.tasks.planning_tasks.session_records.add_to_session

        Parameters
        ----------
        session : `ObservationSession`
            The session as last read; its revision must be the stored one.
        packages : `list`
            Stored packages, or `QueueRequest` objects that also give a start
            mode or their own exposure requests.

        Returns
        -------
        session : `ObservationSession`
            The stored session.

        Notes
        -----
        Raises `PlanRevisionConflictError` if the session changed since it
        was read; its details hold the current revision.
        """
        from wayfindinglib.tasks.planning_tasks.session_records import add_to_session

        return add_to_session(self._context, session, packages)

    def remove(
        self, session: ObservationSession, entries: list[QueuedObservationPackage]
    ) -> ObservationSession:
        """Take entries out of a session's queue.

        Owner:
            wayfindinglib.tasks.planning_tasks.session_records.remove_from_session

        Parameters
        ----------
        session : `ObservationSession`
            The session as last read.
        entries : `list` [`QueuedObservationPackage`]
            The entries to remove, from ``session.queue``.

        Returns
        -------
        session : `ObservationSession`
            The stored session.

        Notes
        -----
        Raises `NotFoundError` for an entry that is not in the queue and
        `ConflictError` for one that is running.
        """
        from wayfindinglib.tasks.planning_tasks.session_records import remove_from_session

        return remove_from_session(self._context, session, entries)

    def update(
        self,
        session: ObservationSession,
        *,
        order: list[QueuedObservationPackage] | None = None,
        exposures: dict[str, list[ExposureRequest]] | None = None,
        framing: Framing | None = None,
        checklist: list[ChecklistItem] | None = None,
    ) -> ObservationSession:
        """Change a session's queue order, exposures, framing or checklist.

        The checklist is the whole new list: a new item is added (one with
        the same rule and capture setting as an item already there is not
        added again), an item left out is removed, and an item whose
        ``ticked_at`` changed is ticked, the time joining its
        ``tick_history``. To tick an item, set its ``ticked_at`` (and maybe
        its ``note``) and pass the list back.

        Owner:
            wayfindinglib.tasks.planning_tasks.session_records.update_session

        Parameters
        ----------
        session : `ObservationSession`
            The session as last read.
        order : `list` [`QueuedObservationPackage`], optional
            Every entry of the queue, in the new order.
        exposures : `dict` [`str`, `list` [`ExposureRequest`]], optional
            New exposure requests for entries that have not started, by
            entry id.
        framing : `Framing`, optional
            The field center, the camera rotation and any mosaic panels.
        checklist : `list` [`ChecklistItem`], optional
            The whole new checklist.

        Returns
        -------
        session : `ObservationSession`
            The stored session. A change that changes nothing keeps the
            revision.

        Notes
        -----
        Raises `InvalidArgumentError` if nothing is given, ``order`` does not
        name exactly the entries, or an exposure request is refused, and
        `PlanRevisionConflictError` if the session changed since it was read.
        """
        from wayfindinglib.tasks.planning_tasks.session_records import update_session

        return update_session(self._context, session, order, exposures, framing, checklist)
