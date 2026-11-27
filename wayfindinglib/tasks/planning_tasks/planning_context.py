"""Purpose: What the planning owners share: settings, storage, sky engine.

Description: `PlanningContext` holds what every planning owner function needs:
the application settings, the `DiskButler` that stores packages and sessions,
the shared `Astrometrics` handle, the placement settings and the sky engine
(built once, on first use). The four objects of `Wayfinder.planning`
(`sessions`, `packages`, `sky` and `catalogs`) share one context and pass it
to the owners in this package.

The small helpers here load one stored package or session, find the session
of a night, give the names a session's equipment goes by in frame records,
and write a line of the suggestion log. `utc_now` is the one clock of the
planning owners; tests replace it to control the time.
"""

import threading
from datetime import UTC, date, datetime
from typing import Any

from astrometricslib import AppConfiguration, Astrometrics, Target
from astrometricslib.exceptions import AstrometricsError, InvalidArgumentError, NotFoundError
from wayfindinglib.analytics.frame_counting import EquipmentNames
from wayfindinglib.drivers.butler import DiskButler, pick_session_for_night
from wayfindinglib.models.planning.observation_package import ObservationPackage
from wayfindinglib.models.planning.planning_config import PlanningConfig
from wayfindinglib.models.planning.suggestion_log import SuggestionEvent, SuggestionLogEntry
from wayfindinglib.models.session.observation_session import ObservationSession

__all__ = [
    "PlanningContext",
    "equipment_names",
    "fold_target_id",
    "load_package",
    "load_session",
    "log_suggestion_event",
    "night_date",
    "session_for_night",
    "utc_now",
]


def utc_now() -> datetime:
    """Give the current time. Tests replace this to control the clock.

    Returns
    -------
    now : `datetime`
        The current time, with a UTC offset.
    """
    return datetime.now(UTC)


def fold_target_id(name: str) -> str:
    """Fold a target id so spelling differences do not matter.

    Parameters
    ----------
    name : `str`
        The target id.

    Returns
    -------
    folded : `str`
        The id with underscores as spaces, in lower case.
    """
    return name.replace("_", " ").strip().casefold()


def night_date(night: str | date) -> date:
    """Read an observing night as the date it names.

    Parameters
    ----------
    night : `str` or `datetime.date`
        The local date on which the night began, such as ``"2026-09-24"``.

    Returns
    -------
    night : `datetime.date`
        That date.

    Raises
    ------
    InvalidArgumentError
        If the text is not a ``YYYY-MM-DD`` date.
    """
    if isinstance(night, date):
        return night
    try:
        return date.fromisoformat(night)
    except (TypeError, ValueError) as error:
        raise InvalidArgumentError(
            f"night must be a date such as 2026-09-24, not {night!r}.", details={"night": night}
        ) from error


class PlanningContext:
    """The settings, storage and sky engine the planning owners share.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    butler : `DiskButler`
        Stores packages, sessions and the suggestion log.
    astrometrics : `Astrometrics`, optional
        The science library handle. Built over ``config`` on first use when
        omitted.
    planning_config : `PlanningConfig`, optional
        Settings for automatic placement.
    """

    def __init__(
        self,
        config: AppConfiguration,
        butler: DiskButler,
        astrometrics: Astrometrics | None = None,
        planning_config: PlanningConfig | None = None,
    ) -> None:
        self.config = config
        self.butler = butler
        self._astrometrics = astrometrics
        self.planning_config = planning_config or PlanningConfig()
        self._sky_engine: Any = None
        # A web backend calls planning from many threads at once; without the
        # lock the first requests after startup would each build an engine,
        # and each engine loads its own copy of the star catalog.
        self._engine_construction_lock = threading.Lock()

    @property
    def astrometrics(self) -> Astrometrics:
        """The shared `Astrometrics` handle, built on first use if not given.

        Returns
        -------
        astrometrics : `Astrometrics`
            The science library handle.
        """
        if self._astrometrics is None:
            self._astrometrics = Astrometrics(self.config)
        return self._astrometrics

    @property
    def sky_engine(self) -> Any:
        """The sky engine: site, coordinate sums, name lookups and catalogs.

        Built once, on first use, even when several threads ask at the same
        time. It holds no hardware code.

        Returns
        -------
        sky : `SkyEngine`
            The engine.
        """
        if self._sky_engine is None:
            with self._engine_construction_lock:
                if self._sky_engine is None:
                    from wayfindinglib.tasks.planning_tasks.sky_engine import SkyEngine

                    self._sky_engine = SkyEngine(config=self.config, astrometrics=self.astrometrics)
        return self._sky_engine


def load_package(context: PlanningContext, package_id: str) -> ObservationPackage:
    """Load one stored observation package.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    package_id : `str`
        The package id.

    Returns
    -------
    package : `ObservationPackage`
        The stored package.

    Raises
    ------
    NotFoundError
        If no package has that id.
    """
    package = context.butler.get("observation_package", {"id": package_id})
    if package is None:
        raise NotFoundError(f"No observation package {package_id!r}.", details={"package_id": package_id})
    return package


def load_session(context: PlanningContext, session_id: str) -> ObservationSession:
    """Load one stored observing session.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    session_id : `str`
        The session id.

    Returns
    -------
    session : `ObservationSession`
        The stored session.

    Raises
    ------
    NotFoundError
        If no session has that id.
    """
    session = context.butler.get("observation_session", {"session_id": session_id})
    if session is None:
        raise NotFoundError(f"No observing session {session_id!r}.", details={"session_id": session_id})
    return session


def session_for_night(context: PlanningContext, night: date) -> ObservationSession | None:
    """Find the one stored session that stands for a night.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    night : `datetime.date`
        The local date the night begins.

    Returns
    -------
    session : `ObservationSession` or `None`
        The session with the most queue entries, then the newest; `None`
        when the night has none.
    """
    return pick_session_for_night([
        session for session in context.butler.get_all("observation_session") if session.night_date == night
    ])


def equipment_names(context: PlanningContext, session: ObservationSession) -> EquipmentNames:
    """Give the names a session's camera and telescope go by in frames.

    A frame record holds the camera and telescope by name, a session by id.
    Both the id and the configured name are accepted.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    session : `ObservationSession`
        The session.

    Returns
    -------
    names : `EquipmentNames`
        The names to match frames against.
    """
    from wayfindinglib.data_access.equipment_catalog_reader import list_cameras, list_telescopes

    camera_names = [session.camera_id]
    telescope_names = [session.telescope_id]
    try:
        camera_names.extend(
            camera.name for camera in list_cameras(context.config) if camera.id == session.camera_id
        )
        telescope_names.extend(
            telescope.name
            for telescope in list_telescopes(context.config)
            if telescope.id == session.telescope_id
        )
    except AstrometricsError, OSError, KeyError, ValueError:
        pass
    return EquipmentNames.from_names(camera_names, telescope_names)


def log_suggestion_event(
    context: PlanningContext,
    event: SuggestionEvent,
    package: ObservationPackage,
    cause: str = "",
    source_key: str = "",
) -> None:
    """Write one line of the suggestion log for a user or promotion action.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    event : `SuggestionEvent`
        What happened.
    package : `ObservationPackage`
        The package it happened to.
    cause : `str`, optional
        Why.
    source_key : `str`, optional
        The row, when the event is about one row.
    """
    suggestion = package.suggestion
    entry = SuggestionLogEntry(
        time=utc_now(),
        event=event,
        package_id=package.id,
        target_id=package.target_id,
        rule_name="" if suggestion is None else suggestion.rule_name,
        source_key=source_key or ("" if suggestion is None else suggestion.source_key),
        cause=cause,
    )
    context.butler.put(entry, "package_suggestion_event", {"id": entry.id})


def target_of(context: PlanningContext, target_id: str) -> Target:
    """Read the library target a stored package or entry names.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    target_id : `str`
        The target id.

    Returns
    -------
    target : `Target`
        The library target.

    Notes
    -----
    Raises `NotFoundError` if the library has no such target.
    """
    return context.astrometrics.targets.get(target_id, required=True)
