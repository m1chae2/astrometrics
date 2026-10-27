"""Purpose: `ObservationPlanning`, the entry point for deciding what to see.

Description: `Wayfinder.planning` is an `ObservationPlanning`. It answers
where objects are in the sky (`get_visibility`), advises on targets and
calibration frames (`get_advisory`), lays out mosaics
(`calculate_panels`, `create_mosaic`), writes plans (`create_plan`,
`edit_queue`, `edit_package`, `tick_checklist_item`) and reads them back
(`get_plan`). `refresh_suggestions` keeps the library's suggested
observation packages up to date and marks finished packages complete. It
also browses the library and online catalogs around a point in the sky and
reports on the downloaded deep-star catalog.

Planning never talks to hardware. A plan made by hand (`create_plan`
with ``kind="empty_session"``, then `edit_queue`) and a plan placed
automatically (``kind="scheduled_session"``) write the same queue
structure, so `ObservationExecution` runs either one.

Each method checks its arguments and hands the work to a function under
`wayfindinglib.tasks.planning_tasks`. Callers never import those tasks
directly.
"""

import logging
import threading
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from functools import partial
from typing import Any, Literal

from astropy.time import Time
from pydantic import ValidationError

from astrometricslib import (
    AppConfiguration,
    Astrometrics,
    AstrometricsError,
    FilterType,
    InvalidArgumentError,
    NotFoundError,
    StellarObject,
    Target,
    background_job,
    check_choice,
    check_include,
    get_current_job,
    registered_job,
    reject_unused_arguments,
    resolve_target,
)
from wayfindinglib.analytics.frame_counting import (
    EquipmentNames,
    TargetFrameReader,
    count_request_frames,
    missing_requests,
)
from wayfindinglib.drivers.butler import DiskButler, pick_session_for_night
from wayfindinglib.models.equipment_and_site.calibration import CalibrationAdvisory
from wayfindinglib.models.equipment_and_site.equipment import EquipmentConfiguration, Telescope
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.planning.deep_catalog import DeepCatalogEstimate, DeepCatalogStatus
from wayfindinglib.models.planning.depth_goal import DepthGoalStatus, DepthLevel
from wayfindinglib.models.planning.mosaic import MosaicPanel, MosaicPlan
from wayfindinglib.models.planning.observation_package import (
    DismissedRow,
    DitherConfig,
    ExposureRequest,
    FrameType,
    ObservationPackage,
    PackageStatus,
)
from wayfindinglib.models.planning.planning_config import PlanningConfig
from wayfindinglib.models.planning.sequence_plan import SequencePlan
from wayfindinglib.models.planning.sky_source import SkySource
from wayfindinglib.models.planning.suggestion_log import (
    PackageChange,
    SuggestionEvent,
    SuggestionLogEntry,
)
from wayfindinglib.models.planning.target_report import TargetDatasetReport
from wayfindinglib.models.planning.tonight_ranking import TonightRanking
from wayfindinglib.models.planning.visibility import HorizonZone, VisibilityReport
from wayfindinglib.models.quality.statements import TargetStatement
from wayfindinglib.models.session.observation_session import (
    ChecklistItem,
    Framing,
    ObservationSession,
    ObservationSessionSummary,
    PlanChange,
    PlanChangeItem,
    PlanRevisionConflictError,
    QueuedObservationPackage,
    QueueEntryStatus,
    QueueRequest,
    SessionStatus,
)
from wayfindinglib.models.session.session_progress import ObservationSessionWithProgress

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "ObservationPlanning",
]


logger = logging.getLogger(__name__)

MAXIMUM_SOURCES = 300
"""Most stars one `ObservationPlanning.find_sources` answer lists."""

AdvisoryKind = Literal["quality", "calibration", "tonight", "result"]
"""The kinds of advice `ObservationPlanning.get_advisory` gives."""

ADVISORY_ARGUMENTS = {
    "quality": ("target",),
    "calibration": (
        "camera_id",
        "frame_type",
        "exposure_seconds",
        "filter",
        "gain_setting",
        "offset",
        "binning",
        "sensor_temperature_c",
    ),
    "tonight": ("at",),
    "result": ("target", "object_id"),
}
"""The arguments each advisory kind uses."""

PlanKind = Literal["sequence", "package", "empty_session", "scheduled_session"]
"""The kinds of plan `ObservationPlanning.create_plan` writes."""

PLAN_ARGUMENTS = {
    "sequence": ("target", "plan_items"),
    "package": (
        "target",
        "exposure_requests",
        "plan_items",
        "dither_config",
        "minimum_altitude_deg",
        "priority",
        "quality_weighting_enabled",
        "notes",
    ),
    "empty_session": ("site_profile", "telescope", "camera_id", "night_id"),
    "scheduled_session": ("requests", "site_profile", "telescope", "camera_id", "night_id"),
}
"""The arguments each plan kind uses."""

PLAN_REQUIRED_ARGUMENTS = {
    "sequence": ("target", "plan_items"),
    "package": ("target",),
    "empty_session": ("night_id",),
    "scheduled_session": ("requests", "site_profile", "telescope", "camera_id", "night_id"),
}
"""The arguments each plan kind cannot do without."""

PlanListKind = Literal["sessions", "packages", "suggestion_log"]
"""What `ObservationPlanning.get_plan` lists."""

GET_PLAN_ARGUMENTS = {
    "sessions": ("session_id", "night_id", "include"),
    "packages": ("status", "target", "include_dismissed", "pick"),
    "suggestion_log": ("target",),
}
"""The arguments each `get_plan` kind uses."""

GET_PLAN_SECTIONS = ("progress",)
"""Optional sections `ObservationPlanning.get_plan` can add to a session."""

DEEP_CATALOG_SECTIONS = ("estimate",)
"""Optional sections `ObservationPlanning.deep_catalog_status` can add."""


def _utc_now() -> datetime:
    """Give the current time. Tests replace this to control the clock.

    Returns
    -------
    now : `datetime`
        The current time, with a UTC offset.
    """
    return datetime.now(UTC)


def _fold_target_id(name: str) -> str:
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


def _night_date(night_id: str) -> date:
    """Read an observing night id as the date it names.

    Parameters
    ----------
    night_id : `str`
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
    try:
        return date.fromisoformat(night_id)
    except (TypeError, ValueError) as error:
        raise InvalidArgumentError(
            f"night_id must be a date such as 2026-09-24, not {night_id!r}.", details={"night_id": night_id}
        ) from error


def _advisory_time(at: datetime | str | None) -> datetime:
    """Read the time an advisory is for.

    Parameters
    ----------
    at : `datetime`, `str` or `None`
        A time, ISO 8601 text, or ``None`` for now. A time with no offset
        counts as UTC.

    Returns
    -------
    moment : `datetime`
        The time, with an offset.

    Raises
    ------
    InvalidArgumentError
        If the text is not an ISO 8601 time.
    """
    if at is None:
        return datetime.now(UTC)
    if isinstance(at, str):
        try:
            at = datetime.fromisoformat(at)
        except ValueError as error:
            raise InvalidArgumentError(
                f"at must be an ISO 8601 time, not {at!r}.", details={"at": at}
            ) from error
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)


class ObservationPlanning:
    """Decide what to observe: visibility, advice, mosaics and plans.

    Parameters
    ----------
    config : `AppConfiguration`, optional
        The application configuration. Taken from `butler`, or loaded,
        when omitted.
    butler : `DiskButler`, optional
        Stores packages and sessions. Built over `config` when omitted.
    astrometrics : `Astrometrics`, optional
        The science library handle shared with the rest of the
        `Wayfinder`. Built over `config` on first use when omitted.
    planning_config : `PlanningConfig`, optional
        Settings for automatic placement, such as the twilight used to
        bound the night.
    """

    def __init__(
        self,
        config: AppConfiguration | None = None,
        butler: DiskButler | None = None,
        *,
        astrometrics: Astrometrics | None = None,
        planning_config: PlanningConfig | None = None,
    ) -> None:
        """Store the configuration, storage and shared science handle."""
        if config is None:
            if butler is not None:
                config = butler.config
            else:
                from astrometricslib import get_configuration

                config = get_configuration()
        self._config = config
        self._butler = butler or DiskButler(app_config=config)
        self._shared_astrometrics = astrometrics
        self._planning_config = planning_config or PlanningConfig()
        self.__sky_engine = None
        # Guards the lazy engine construction below. A web backend calls this
        # object from many threads at once, and the first request after startup
        # (e.g. the Planetarium mounting and firing several queries together)
        # would otherwise find the engine still None on every thread and build
        # one apiece -- each `SkyEngine` loads its own full copy of the star
        # catalog.
        self._engine_construction_lock = threading.Lock()

    @property
    def _astrometrics(self) -> Astrometrics:
        """The shared `Astrometrics` handle, built on first use if not given.

        Returns
        -------
        astrometrics : `astrometricslib.Astrometrics`
            The science library handle.
        """
        if self._shared_astrometrics is None:
            from astrometricslib import Astrometrics

            self._shared_astrometrics = Astrometrics(self._config)
        return self._shared_astrometrics

    @property
    def _sky_engine(self) -> Any:
        """The sky engine: site, coordinate sums, name lookups and catalogs.

        Built on first use, once, even when several threads ask at the
        same time. It holds no hardware code, so planning stays free of
        hardware.

        Returns
        -------
        sky : `SkyEngine`
            The engine.
        """
        if self.__sky_engine is None:
            with self._engine_construction_lock:
                if self.__sky_engine is None:
                    from wayfindinglib.tasks.planning_tasks.sky_engine import SkyEngine

                    self.__sky_engine = SkyEngine(config=self._config, astrometrics=self._astrometrics)
        return self.__sky_engine

    def _target(self, target: str | Target) -> Target:
        """Turn a target id into the library `Target` it names.

        Parameters
        ----------
        target : `str` or `Target`
            A target id, or a target, which is returned as it is.

        Returns
        -------
        target : `Target`
            The library target.
        """
        return resolve_target(self._astrometrics.targets, target)

    def _package(self, package_id: str) -> ObservationPackage:
        """Load one recorded observation package.

        Parameters
        ----------
        package_id : `str`
            The package id.

        Returns
        -------
        package : `ObservationPackage`
            The recorded package.

        Raises
        ------
        NotFoundError
            If no package has that id.
        """
        package = self._butler.get("observation_package", {"id": package_id})
        if package is None:
            raise NotFoundError(f"No observation package {package_id!r}.", details={"package_id": package_id})
        return package

    def _session(self, session_id: str) -> ObservationSession:
        """Load one recorded observation session.

        Parameters
        ----------
        session_id : `str`
            The session id.

        Returns
        -------
        session : `ObservationSession`
            The recorded session.

        Raises
        ------
        NotFoundError
            If no session has that id.
        """
        session = self._butler.get("observation_session", {"session_id": session_id})
        if session is None:
            raise NotFoundError(f"No observation session {session_id!r}.", details={"session_id": session_id})
        return session

    def _session_equipment(
        self, site_profile: SiteProfile | None, telescope: Telescope | None, camera_id: str | None
    ) -> tuple[SiteProfile, Telescope, str]:
        """Fill in a session's site, telescope and camera from the defaults.

        Parameters
        ----------
        site_profile : `SiteProfile` or `None`
            The site, or `None` for the recorded default site.
        telescope : `Telescope` or `None`
            The telescope, or `None` for the active one.
        camera_id : `str` or `None`
            The camera, or `None` for the active one.

        Returns
        -------
        site_profile, telescope, camera_id : `SiteProfile`, `Telescope`, `str`
            The equipment the session records.

        Raises
        ------
        ConfigurationError
            If no telescope or camera is given and none is active.
        """
        from astrometricslib import ConfigurationError
        from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog
        from wayfindinglib.data_access.site_profile_reader import get_or_seed_default_site_profile

        if site_profile is None:
            site_profile = get_or_seed_default_site_profile(self._butler, self._config)
        if telescope is None or camera_id is None:
            catalog = get_equipment_catalog(self._config)
            telescope = telescope or catalog.active_telescope()
            camera = catalog.active_camera()
            camera_id = camera_id or (camera.id if camera else None)
        if telescope is None or camera_id is None:
            raise ConfigurationError(
                "An observing session needs a telescope and a camera, and none is active."
            )
        return site_profile, telescope, camera_id

    def _equipment_names(self, session: ObservationSession) -> EquipmentNames:
        """Give the names a session's camera and telescope go by in frames.

        A frame record holds the camera and telescope by name, a session by
        id. Both the id and the configured name are accepted. An id with no
        configured equipment is matched by its id alone.

        Parameters
        ----------
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
                camera.name for camera in list_cameras(self._config) if camera.id == session.camera_id
            )
            telescope_names.extend(
                telescope.name
                for telescope in list_telescopes(self._config)
                if telescope.id == session.telescope_id
            )
        except AstrometricsError, OSError, KeyError, ValueError:
            pass
        return EquipmentNames.from_names(camera_names, telescope_names)

    def _log_suggestion_event(
        self, event: SuggestionEvent, package: ObservationPackage, cause: str = "", source_key: str = ""
    ) -> None:
        """Write one line of the suggestion log for a user or promotion action.

        Parameters
        ----------
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
            time=_utc_now(),
            event=event,
            package_id=package.id,
            target_id=package.target_id,
            rule_name="" if suggestion is None else suggestion.rule_name,
            source_key=source_key or ("" if suggestion is None else suggestion.source_key),
            cause=cause,
        )
        self._butler.put(entry, "package_suggestion_event", {"id": entry.id})

    # -- Sky browsing (name lookup and catalogs) ----------------------------

    def resolve_target_coordinates(self, name: str) -> Target | StellarObject:
        """Resolve a target or star by name, through the library or SIMBAD.

        Parameters
        ----------
        name : `str`
            A target id or common name, or a star name.

        Returns
        -------
        resolved : `Target` or `StellarObject`
            The whole record of the object found.
        """
        return self._sky_engine.resolve_target_coordinates(name)

    def lookup_coordinates(self, name: str) -> dict[str, Any]:
        """Find where a named object is, from the library or SIMBAD.

        Gives a short answer. `resolve_target_coordinates` returns the
        whole target record, frames and all, which is far more than a
        position needs.

        Parameters
        ----------
        name : `str`
            A target id, a common name, or a star name.

        Returns
        -------
        position : `dict` [`str`, `Any`]
            ``id``, ``name``, ``kind`` (``"target"`` or ``"star"``), and
            the position in degrees (``ra_deg``, ``dec_deg``) and as text.
        """
        from astrometricslib import parse_coordinate_string

        found = self._sky_engine.resolve_target_coordinates(name)
        if isinstance(found, StellarObject):
            return {
                "id": found.id,
                "name": found.name,
                "kind": "star",
                "ra_deg": round(float(found.right_ascension), 6),
                "dec_deg": round(float(found.declination), 6),
                "magnitude": found.magnitude,
                "spectral_type": found.spectral_type,
            }
        return {
            "id": found.id,
            "name": found.common_name or None,
            "kind": "target",
            "ra_deg": round(parse_coordinate_string(found.ra, True), 6),
            "dec_deg": round(parse_coordinate_string(found.dec, False), 6),
            "ra": found.ra,
            "dec": found.dec,
        }

    def find_sources(
        self,
        ra_deg: float,
        dec_deg: float,
        radius_deg: float,
        magnitude_min: float | None = None,
        magnitude_max: float | None = None,
        include: Sequence[str] = ("targets",),
        limit: int = 100,
    ) -> dict[str, Any]:
        """List the library's stars, and optionally targets, near a point.

        Stars come brightest first and the list is cut at ``limit``, with
        the total reported so the cut is never silent.

        Parameters
        ----------
        ra_deg : `float`
            Right ascension of the center, in degrees.
        dec_deg : `float`
            Declination of the center, in degrees.
        radius_deg : `float`
            Search radius, in degrees.
        magnitude_min : `float`, optional
            Keep stars at least this magnitude (numerically).
        magnitude_max : `float`, optional
            Keep stars no fainter than this magnitude.
        include : `list` [`str`], optional
            ``"targets"`` also lists the library targets in the circle.
            Defaults to ``["targets"]``; pass ``[]`` for stars only.
        limit : `int`, optional
            Most stars to list, from 1 to 300. Defaults to 100.

        Returns
        -------
        answer : `dict` [`str`, `Any`]
            ``stars`` (the summary rows), ``stars_total``, whether the star
            list was cut, and ``targets`` (id, name, position) when asked.
        """
        from astrometricslib import parse_coordinate_string

        sections = check_include(include, ("targets",))
        limit = max(1, min(int(limit), MAXIMUM_SOURCES))
        magnitude_range = None
        if magnitude_min is not None or magnitude_max is not None:
            magnitude_range = (
                magnitude_min if magnitude_min is not None else -30.0,
                magnitude_max if magnitude_max is not None else 60.0,
            )
        stars = list(
            self._sky_engine.get_library_star_summaries(ra_deg, dec_deg, radius_deg, magnitude_range)
        )
        stars.sort(key=lambda row: (row.get("magnitude") is None, row.get("magnitude") or 0.0))
        answer: dict[str, Any] = {
            "stars_total": len(stars),
            "stars_truncated": len(stars) > limit,
            "stars": stars[:limit],
        }
        if "targets" in sections:
            targets = []
            for target in self._sky_engine.get_sources(ra_deg, dec_deg, radius_deg, False, False):
                try:
                    ra = parse_coordinate_string(target.ra, True)
                    dec = parse_coordinate_string(target.dec, False)
                except InvalidArgumentError:
                    ra = dec = None
                targets.append({
                    "id": target.id,
                    "name": getattr(target, "common_name", None) or None,
                    "ra_deg": ra,
                    "dec_deg": dec,
                })
            answer["targets"] = targets
        return answer

    def get_sources(
        self,
        ra_deg: float,
        dec_deg: float,
        radius_deg: float,
        include: Sequence[str] = ("stars",),
        limiting_magnitude: float | None = None,
        include_stars_without_catalog_magnitude: bool = True,
    ) -> list[SkySource]:
        """List the targets, and optionally stars, to draw in a sky region.

        Targets show their stacked image, or their longest LIGHT frame when
        they have no stack. Stars are read from the library's indexed
        summaries, so a wide region stays fast. Single-frame photometry
        detections are never listed.

        Parameters
        ----------
        ra_deg : `float`
            Right ascension of the center, in degrees.
        dec_deg : `float`
            Declination of the center, in degrees.
        radius_deg : `float`
            Search radius, in degrees.
        include : `list` [`str`], optional
            ``"stars"`` adds the library's own stars; ``"online"`` adds
            SIMBAD's objects that the library does not hold. Defaults to
            ``["stars"]``; pass ``[]`` for library targets only.
        limiting_magnitude : `float`, optional
            Leave out stars whose catalog magnitude is fainter than this.
            Targets are never left out. Needs ``"stars"`` or ``"online"``.
        include_stars_without_catalog_magnitude : `bool`, optional
            Keep stars that have no real catalog magnitude (missing, zero,
            or an instrumental value from photometry). Defaults to `True`.
            A wide map view passes `False`, because most library stars are
            field detections that the magnitude limit cannot thin out.
            Needs ``"stars"`` or ``"online"``.

        Returns
        -------
        sources : `list` [`SkySource`]
            Library targets, then library stars, then online objects.

        Raises
        ------
        InvalidArgumentError
            If ``include`` names an unknown section, or a star limit is
            given with no stars asked for.
        """
        from wayfindinglib.tasks.planning_tasks import sky_sources

        sections = check_include(include, ("stars", "online"))
        if not sections and (limiting_magnitude is not None or not include_stars_without_catalog_magnitude):
            raise InvalidArgumentError(
                "limiting_magnitude and include_stars_without_catalog_magnitude only apply when "
                "include has 'stars' or 'online'."
            )
        return sky_sources.collect_sky_sources(
            self._sky_engine,
            ra_deg,
            dec_deg,
            radius_deg,
            include_stars="stars" in sections,
            include_online="online" in sections,
            limiting_magnitude=limiting_magnitude,
            include_stars_without_catalog_magnitude=include_stars_without_catalog_magnitude,
        )

    def get_library_star_summaries(
        self,
        ra_deg: float,
        dec_deg: float,
        radius_deg: float,
        magnitude_range: tuple[float, float] | None = None,
    ) -> list[dict[str, Any]]:
        """Return quick summaries of the user's own stars near a point.

        ``magnitude_range`` keeps only stars whose magnitude is between
        the two values, ends included; stars with no saved magnitude are
        left out. Every star is kept when it is omitted.

        Returns
        -------
        summaries : `list` [`dict`]
            One dict per library star inside the search radius, with keys
            ``id``, ``name``, ``ra``, ``dec``, ``targetIds``,
            ``hasSpectra``, ``hasPhotometry``, ``magnitude`` and
            ``spectralType``.
        """
        return self._sky_engine.get_library_star_summaries(ra_deg, dec_deg, radius_deg, magnitude_range)

    def get_online_catalog_sources(
        self,
        ra_deg: float,
        dec_deg: float,
        radius_deg: float,
        enabled_driver_names: list[str],
        magnitude_limit: float | None = None,
    ) -> list[SkySource]:
        """Ask the enabled online catalog drivers for the stars in a region.

        Only the online catalogs are asked; the library is neither read nor
        changed.

        Parameters
        ----------
        ra_deg : `float`
            Right ascension of the center, in degrees.
        dec_deg : `float`
            Declination of the center, in degrees.
        radius_deg : `float`
            Search radius, in degrees.
        enabled_driver_names : `list` [`str`]
            The drivers to ask, such as ``["deep_stars"]``.
        magnitude_limit : `float`, optional
            Faintest magnitude wanted. Drivers that can use it fetch fewer
            stars.

        Returns
        -------
        sources : `list` [`SkySource`]
            One source per star found, marked global, with
            ``catalog_source`` naming the driver.
        """
        from wayfindinglib.tasks.planning_tasks import sky_sources

        return sky_sources.online_catalog_sources(
            self._sky_engine, ra_deg, dec_deg, radius_deg, enabled_driver_names, magnitude_limit
        )

    def list_catalog_driver_metadata(self) -> list[dict[str, Any]]:
        """Return metadata describing each registered online catalog driver.

        Returns
        -------
        metadata : `list` [`dict`]
            Metadata for every registered online catalog driver.
        """
        return self._sky_engine.list_catalog_driver_metadata()

    def get_constellation_lines(self) -> list[dict[str, Any]]:
        """Return constellation stick-figure line segment definitions.

        Returns
        -------
        lines : `list` [`dict`]
            Every constellation's stick-figure line segment definition.
        """
        return self._sky_engine.get_constellation_lines()

    def get_imaged_field_centers(self) -> list[dict[str, Any]]:
        """List the sky positions of every target the library has imaged.

        Used to download the deep-star catalog only around the fields
        actually imaged, instead of the whole sky (see
        `build_deep_star_catalog`'s ``pixels`` argument).

        Returns
        -------
        field_centers : `list` [`dict`]
            One entry per unique imaged field, with keys
            ``right_ascension_deg``, ``declination_deg``, ``target_ids``
            and ``frames_examined``.
        """
        return self._astrometrics.targets.imaged_field_centers(register_job=False)

    # -- Visibility ---------------------------------------------------------

    def get_visibility(
        self,
        objects: list[str | Target | StellarObject | dict[str, Any]] | None = None,
        time: str | datetime | Time | None = None,
        end_time: str | datetime | Time | None = None,
        step_minutes: float | None = None,
        include: list[str] | None = None,
        minimum_altitude_deg: float = 0.0,
        horizon_zones: list[HorizonZone | dict[str, Any]] | None = None,
        timezone_offset_hours: float = 0.0,
        clear_only: bool = False,
    ) -> VisibilityReport:
        """Say where objects are in the sky, at one moment or over a span.

        At one moment (no `end_time`) each object gets its altitude,
        azimuth, whether it is above the horizon and clear of the horizon
        limit, and its rise, set and transit times. Any number of objects
        may be asked about at once.

        Over a span (`end_time` given), each object also gets its highest
        point, its meridian crossings and flip times, when it is clear of
        the horizon limit, and when it is *usable*: clear while the Sun is
        below -18 degrees. The report also describes the night: twilight,
        the fully dark span, and when the Moon is up and how bright it is.
        A span covers at most 30 objects and 150 rows.

        Nothing is stored. Names not in the library are looked up in
        SIMBAD over the network.

        Parameters
        ----------
        objects : `list`, optional
            Names or ids (the library first, then SIMBAD), `Target` or
            `StellarObject` objects, or dictionaries ``{"id", "ra_deg",
            "dec_deg"}`` for points with known coordinates. When omitted,
            every library target, highest first.
        time : `str`, `datetime` or `astropy.time.Time`, optional
            The moment, or the start of the span. ``"now"`` or omitted
            means now. A text time is ISO 8601; an offset such as
            ``-06:00`` is honored, and no offset means UTC.
        end_time : `str`, `datetime` or `astropy.time.Time`, optional
            The end of the span. Omit it for one moment.
        step_minutes : `float`, optional
            Time between rows of a span, at least 1. Defaults to 30. Used
            only with `end_time`.
        include : `list` [`str`], optional
            ``"meridian"`` adds each object's hour angle and flip status
            at `time`. ``"samples"`` adds the row-by-row table of a span
            (only with `end_time`).
        minimum_altitude_deg : `float`, optional
            The lowest altitude that counts as clear. Defaults to 0.
        horizon_zones : `list`, optional
            Blocked parts of the sky, such as trees. Each has
            ``azimuth_start_deg``, ``azimuth_end_deg`` (a range may wrap
            past north) and ``min_clear_altitude_deg``.
        timezone_offset_hours : `float`, optional
            Write times in this UTC offset, such as -6 for Denver in
            summer. Defaults to 0 (UTC).
        clear_only : `bool`, optional
            Keep only the objects that are clear of the horizon limit at
            `time`.

        Returns
        -------
        report : `VisibilityReport`
            The site, the horizon limit, one entry per object, and for a
            span the night itself.

        Raises
        ------
        InvalidArgumentError
            If a time cannot be read, a span-only argument comes without
            `end_time`, or the span is too long or too crowded.
        """
        from wayfindinglib.tasks.planning_tasks import visibility_report

        sections = check_include(include, visibility_report.INCLUDE_SECTIONS)
        try:
            zones = [HorizonZone.model_validate(zone) for zone in horizon_zones or []]
        except ValidationError as error:
            raise InvalidArgumentError(
                "Each horizon zone needs azimuth_start_deg, azimuth_end_deg and min_clear_altitude_deg.",
                details={"error": str(error)},
            ) from error
        start = visibility_report.to_astropy_time(time, "time")
        end = visibility_report.to_astropy_time(end_time, "end_time") if end_time is not None else None
        resolved = visibility_report.resolve_sky_objects(self._sky_engine, objects)
        report = visibility_report.build_visibility_report(
            self._sky_engine,
            resolved,
            start,
            end,
            step_minutes,
            sections,
            minimum_altitude_deg,
            zones,
            timezone_offset_hours,
        )
        if clear_only:
            report.objects = [entry for entry in report.objects if entry.clear]
        if objects is None:
            report.objects.sort(key=lambda entry: entry.altitude_deg, reverse=True)
        return report

    # -- Advice -------------------------------------------------------------

    def get_advisory(
        self,
        kind: AdvisoryKind,
        target: str | Target | None = None,
        camera_id: str | None = None,
        frame_type: FrameType | None = None,
        exposure_seconds: float | None = None,
        filter: FilterType | None = None,
        *,
        at: datetime | str | None = None,
        object_id: str | None = None,
        gain_setting: int | None = None,
        offset: int | None = None,
        binning: int | None = None,
        sensor_temperature_c: float | None = None,
    ) -> TargetDatasetReport | CalibrationAdvisory | TonightRanking | list[TargetStatement]:
        """Give planning advice about data, a result, tonight or calibration.

        The advice is worked out on demand and never stored.

        Parameters
        ----------
        kind : `str`
            ``"quality"``: the target dataset report. It summarizes each
            capture setting, lists warnings and recommendations in priority
            order, and gives a starting point when the target has no frames.
            Uses `target`.
            ``"calibration"``: how many calibration frames of one kind the
            library already holds for a camera. Uses `camera_id`,
            `frame_type`, `exposure_seconds`, `filter` and, to narrow the
            match, `gain_setting`, `offset`, `binning` and
            `sensor_temperature_c`.
            ``"tonight"``: the targets ranked by what the next hour of
            each would add. Uses `at`.
            ``"result"``: how far one per-star science result can be
            trusted, as statements. Uses `target` and `object_id`.
        target : `str` or `Target`, optional
            The target, by id or as a `Target`.
        camera_id : `str`, optional
            The camera whose calibration frames are counted.
        frame_type : `FrameType`, optional
            ``DARK``, ``FLAT`` or ``BIAS``.
        exposure_seconds : `float`, optional
            Count only frames of this exposure, when the frame type has one.
        filter : `FilterType`, optional
            Count only frames taken through this filter.
        at : `datetime` or `str`, optional
            The time to rank for (ISO 8601; no offset means UTC). Defaults
            to now.
        object_id : `str`, optional
            The star whose result is judged.
        gain_setting : `int`, optional
            Count only calibration frames taken at this gain setting.
        offset : `int`, optional
            Count only calibration frames taken at this offset.
        binning : `int`, optional
            Count only calibration frames taken at this binning.
        sensor_temperature_c : `float`, optional
            Count only darks within the dark temperature tolerance of this
            sensor temperature, in degrees Celsius.

        Returns
        -------
        advice : `TargetDatasetReport`, `CalibrationAdvisory`, `TonightRanking`
            or `list` [`TargetStatement`]. The advice for the chosen kind.

        Raises
        ------
        InvalidArgumentError
            If `kind` is unknown, an argument the kind does not use is
            given, or a required argument is missing.
        """
        check_choice("kind", kind, tuple(ADVISORY_ARGUMENTS))
        given = {
            "target": target is not None,
            "camera_id": camera_id is not None,
            "frame_type": frame_type is not None,
            "exposure_seconds": exposure_seconds is not None,
            "filter": filter is not None,
            "at": at is not None,
            "object_id": object_id is not None,
            "gain_setting": gain_setting is not None,
            "offset": offset is not None,
            "binning": binning is not None,
            "sensor_temperature_c": sensor_temperature_c is not None,
        }
        reject_unused_arguments(kind, ADVISORY_ARGUMENTS, given)
        if kind == "quality":
            if target is None:
                raise InvalidArgumentError('kind="quality" needs a target.')
            from wayfindinglib.tasks.planning_tasks.target_report_tasks import build_target_dataset_report

            return build_target_dataset_report(
                self._target(target),
                astrometrics=self._astrometrics,
                butler=self._butler,
                config=self._config,
            )
        if kind == "tonight":
            from wayfindinglib.tasks.planning_tasks.tonight_ranking_tasks import rank_tonight

            return rank_tonight(
                _advisory_time(at),
                astrometrics=self._astrometrics,
                butler=self._butler,
                config=self._config,
            )
        if kind == "result":
            if target is None or object_id is None:
                raise InvalidArgumentError('kind="result" needs a target and an object_id.')
            from wayfindinglib.tasks.planning_tasks.result_judgment_tasks import judge_star_result

            return judge_star_result(self._target(target).id, object_id, astrometrics=self._astrometrics)
        if camera_id is None or frame_type is None:
            raise InvalidArgumentError('kind="calibration" needs camera_id and frame_type.')
        from wayfindinglib.tasks.planning_tasks.calibration_advisory_tasks import build_calibration_advisory

        return build_calibration_advisory(
            self._butler,
            camera_id,
            FrameType(frame_type),
            exposure_seconds,
            filter,
            gain_setting=gain_setting,
            offset=offset,
            binning=binning,
            sensor_temperature_c=sensor_temperature_c,
        )

    # -- Mosaics -------------------------------------------------------------

    def calculate_panels(
        self,
        center_ra: str,
        center_dec: str,
        rows: int,
        cols: int,
        overlap_percent: float,
        equipment: EquipmentConfiguration | None = None,
        rotation_deg: float = 0.0,
    ) -> list[MosaicPanel]:
        """Work out the center of each panel of a mosaic grid.

        Nothing is stored. Pass the panels to `create_mosaic` to add them
        to the library.

        Parameters
        ----------
        center_ra : `str`
            Right ascension of the grid center, as text (hours) or degrees.
        center_dec : `str`
            Declination of the grid center, as text or degrees.
        rows : `int`
            Number of panel rows.
        cols : `int`
            Number of panel columns.
        overlap_percent : `float`
            How much neighbouring panels overlap, from 0 to under 100.
        equipment : `EquipmentConfiguration`, optional
            The telescope and camera whose field of view sets the spacing.
            The configured sensor and focal length are used when omitted.
        rotation_deg : `float`, optional
            How far the camera is turned, in degrees east of north, as
            Ekos and KStars give it. At 0 the grid rows run along
            declination; at 90 the two grid axes swap. Defaults to 0.

        Returns
        -------
        panels : `list` [`MosaicPanel`]
            One panel per grid cell, row by row. Each carries the rotation.
        """
        from wayfindinglib.tasks.planning_tasks.mosaic_tasks import calculate_panels

        return calculate_panels(
            self._config, center_ra, center_dec, rows, cols, overlap_percent, equipment, rotation_deg
        )

    def create_mosaic(
        self,
        target: str | Target,
        panels: list[MosaicPanel | dict[str, Any]],
        exposure_requests: list[ExposureRequest] | None = None,
        dither_config: DitherConfig | None = None,
        packages: bool = True,
    ) -> MosaicPlan:
        """Add one library target per mosaic panel, and one package each.

        Each panel target is named after the parent and the panel, such as
        ``"NGC 7000_P1_2"``, and copies the parent's camera, telescope and
        field of view. With `packages`, one observation package per panel
        is recorded too, all sharing the same exposure recipe; from then on
        each is an ordinary package.

        Parameters
        ----------
        target : `str` or `Target`
            The target the mosaic covers.
        panels : `list` [`MosaicPanel` or `dict`]
            The panels, as `calculate_panels` returns them.
        exposure_requests : `list` [`ExposureRequest`], optional
            The exposure recipe every panel shares. Needed with `packages`.
        dither_config : `DitherConfig`, optional
            The dithering every panel shares. Used only with `packages`.
        packages : `bool`, optional
            Also record one observation package per panel. Defaults to
            `True`.

        Returns
        -------
        plan : `MosaicPlan`
            The panel target ids and the recorded packages.

        Raises
        ------
        InvalidArgumentError
            If a panel is malformed, `packages` is asked for without
            `exposure_requests`, or package settings come without
            `packages`.
        """
        from wayfindinglib.tasks.planning_tasks.mosaic_tasks import create_mosaic

        if packages and not exposure_requests:
            raise InvalidArgumentError("packages=True needs exposure_requests for the panels' packages.")
        if not packages and (exposure_requests or dither_config is not None):
            raise InvalidArgumentError(
                "exposure_requests and dither_config are used only with packages=True."
            )
        try:
            checked_panels = [MosaicPanel.model_validate(panel) for panel in panels]
        except ValidationError as error:
            raise InvalidArgumentError(
                "Each panel needs row, col, ra_str, dec_str, ra_deg, dec_deg and panel_id, as "
                "calculate_panels returns them.",
                details={"error": str(error)},
            ) from error
        plan = create_mosaic(
            self._astrometrics,
            self._target(target),
            checked_panels,
            exposure_requests,
            dither_config,
            packages,
        )
        for package in plan.packages:
            self._butler.put(package, "observation_package", {"id": package.id})
        return plan

    # -- Plans and queues --------------------------------------------------

    def create_plan(
        self,
        kind: PlanKind,
        target: str | Target | None = None,
        plan_items: list[dict[str, Any]] | None = None,
        exposure_requests: list[ExposureRequest] | None = None,
        dither_config: DitherConfig | None = None,
        minimum_altitude_deg: float | None = None,
        priority: int | None = None,
        quality_weighting_enabled: bool | None = None,
        notes: str | None = None,
        requests: list[QueueRequest] | None = None,
        site_profile: SiteProfile | None = None,
        telescope: Telescope | None = None,
        camera_id: str | None = None,
        night_id: str | None = None,
    ) -> SequencePlan | ObservationPackage | ObservationSession:
        """Write a plan: a sequence, a package, or an observing session.

        Parameters
        ----------
        kind : `str`
            ``"sequence"``: a sequence plan for the app's sequencer queue,
            from `target` and `plan_items`. Nothing is stored.
            ``"package"``: a reusable imaging request for `target`, from
            `exposure_requests` (or the sequence items `plan_items`) and
            the optional `dither_config`, `minimum_altitude_deg`,
            `priority`, `quality_weighting_enabled` and `notes`. Recorded.
            ``"empty_session"``: an observing session with an empty queue
            for the night `night_id`, to fill by hand with `edit_queue`.
            Uses `site_profile`, `telescope` and `camera_id`, which
            default to the recorded site and the active telescope and
            camera. Recorded.
            ``"scheduled_session"``: an observing session for the night
            `night_id` with every package in `requests` placed
            automatically, using `site_profile`, `telescope` and
            `camera_id`. Packages that cannot be placed are listed with
            the reason. No device is contacted. Recorded.
        target : `str` or `Target`, optional
            The target, by id or as a `Target`.
        plan_items : `list` [`dict`], optional
            Sequence items, each with ``count``, ``exposure`` (seconds)
            and ``filter``, and for a package an optional frame ``type``
            (``LIGHT`` by default).
        exposure_requests : `list` [`ExposureRequest`], optional
            The exposures a package asks for.
        dither_config : `DitherConfig`, optional
            How a package dithers.
        minimum_altitude_deg : `float`, optional
            The lowest altitude a package may be imaged at.
        priority : `int`, optional
            A package's priority; higher is placed first. Defaults to 0.
        quality_weighting_enabled : `bool`, optional
            Let the target's dataset report raise the package's
            priority during automatic placement. Defaults to `False`.
        notes : `str`, optional
            Free text kept with a package.
        requests : `list` [`QueueRequest`], optional
            The recorded packages to place, each with its start mode.
        site_profile : `SiteProfile`, optional
            The observing site.
        telescope : `Telescope`, optional
            The telescope, whose pointing limits the placement respects.
        camera_id : `str`, optional
            The camera, recorded on the session.
        night_id : `str`, optional
            The observing night, named for the local date on which it
            began, such as ``"2026-09-24"``.

        Returns
        -------
        plan : `SequencePlan`, `ObservationPackage` or `ObservationSession`
            The plan for the chosen kind.

        Raises
        ------
        InvalidArgumentError
            If `kind` is unknown, an argument the kind does not use is
            given, or a required argument is missing.
        NotFoundError
            If a package's target is neither in the library nor known to
            the catalog. A catalog name that is not in the library yet is
            added to the library, with its position, before the package
            is recorded.
        """
        check_choice("kind", kind, tuple(PLAN_ARGUMENTS))
        arguments = {
            "target": target,
            "plan_items": plan_items,
            "exposure_requests": exposure_requests,
            "dither_config": dither_config,
            "minimum_altitude_deg": minimum_altitude_deg,
            "priority": priority,
            "quality_weighting_enabled": quality_weighting_enabled,
            "notes": notes,
            "requests": requests,
            "site_profile": site_profile,
            "telescope": telescope,
            "camera_id": camera_id,
            "night_id": night_id,
        }
        reject_unused_arguments(
            kind, PLAN_ARGUMENTS, {name: value is not None for name, value in arguments.items()}
        )
        missing = [name for name in PLAN_REQUIRED_ARGUMENTS[kind] if arguments[name] is None]
        if missing:
            raise InvalidArgumentError(
                f"kind={kind!r} needs: {', '.join(missing)}.", details={"missing": missing}
            )

        if kind == "sequence":
            from wayfindinglib.tasks.planning_tasks.planning_operations import build_sequence_plan

            return build_sequence_plan(self._target(target).id, plan_items)
        if kind == "package":
            from wayfindinglib.tasks.planning_tasks.planning_operations import exposure_requests_from_items

            if exposure_requests is not None and plan_items is not None:
                raise InvalidArgumentError("kind='package' takes exposure_requests or plan_items, not both.")
            try:
                resolved = self._target(target)
            except NotFoundError:
                # Not in the library: make it from the catalog, with its
                # position. `create` raises NotFoundError when the catalog
                # does not know the name.
                try:
                    resolved = self._astrometrics.targets.create(target, from_catalog=True)
                except NotFoundError as error:
                    raise NotFoundError(
                        f"No target '{target}' in the library or the catalog: {error}"
                    ) from error
            if plan_items is not None:
                requests_for_package = exposure_requests_from_items(plan_items)
            else:
                # No exposures is allowed: "New package" and the
                # Planetarium's "Add" start empty.
                requests_for_package = list(exposure_requests or [])
            # Frames count toward a row only from its last update, so a new
            # row starts counting now.
            moment = _utc_now()
            requests_for_package = [
                row if row.counted_from is not None else row.model_copy(update={"counted_from": moment})
                for row in requests_for_package
            ]
            package = ObservationPackage(
                id=str(uuid.uuid4()),
                name=resolved.id,
                target_id=resolved.id,
                exposure_requests=requests_for_package,
                dither_config=dither_config,
                minimum_altitude_deg=minimum_altitude_deg,
                priority=priority or 0,
                quality_weighting_enabled=bool(quality_weighting_enabled),
                notes=notes or "",
            )
            self._butler.put(package, "observation_package", {"id": package.id})
            return package
        if kind == "empty_session":
            night = _night_date(night_id)
            existing = self._session_for_night(night)
            if existing is not None:
                return existing
            site_profile, telescope, camera_id = self._session_equipment(site_profile, telescope, camera_id)
            session = ObservationSession(
                id=str(uuid.uuid4()),
                night_date=night,
                status=SessionStatus.PLANNED,
                site_profile_id=site_profile.id,
                telescope_id=telescope.id,
                camera_id=camera_id,
            )
            # Check and insert in one transaction: a second caller gets
            # this session back.
            return self._butler.insert_session_for_night(session)
        night = _night_date(night_id)
        placed = self._schedule_session(requests, site_profile, telescope, camera_id, night)
        existing = self._session_for_night(night)
        if existing is None:
            existing = self._butler.insert_session_for_night(placed)
            if existing is placed:
                return placed
        return self._replace_placement(existing, placed)

    def _session_for_night(self, night: date) -> ObservationSession | None:
        """Find the one session that stands for a night.

        Parameters
        ----------
        night : `datetime.date`
            The local date the night begins.

        Returns
        -------
        session : `ObservationSession` or `None`
            The session with the most queue entries, then the newest; `None`
            when the night has none.
        """
        return pick_session_for_night([
            session for session in self._butler.get_all("observation_session") if session.night_date == night
        ])

    def _replace_placement(
        self, existing: ObservationSession, placed: ObservationSession
    ) -> ObservationSession:
        """Put a new automatic placement into a night's existing session.

        The session keeps its id, checklist, framing and history. Its queue,
        the reasons packages were left out, and its site, telescope and
        camera come from `placed`.

        Parameters
        ----------
        existing : `ObservationSession`
            The night's recorded session.
        placed : `ObservationSession`
            The new placement, not stored.

        Returns
        -------
        session : `ObservationSession`
            The updated, stored session.

        Raises
        ------
        ConflictError
            If an entry of the existing queue has started, so re-placing
            would lose its record.
        PlanRevisionConflictError
            If the session changed while this was running.
        """
        from astrometricslib import ConflictError

        started = [entry.id for entry in existing.queue if entry.status != QueueEntryStatus.PENDING]
        if started:
            raise ConflictError(
                "This night's session has entries that already started, so it cannot be placed again.",
                details={"started": started},
            )
        loaded_revision = existing.revision
        existing.queue = placed.queue
        existing.unplaced_package_diagnostics = placed.unplaced_package_diagnostics
        existing.site_profile_id = placed.site_profile_id
        existing.telescope_id = placed.telescope_id
        existing.camera_id = placed.camera_id
        existing.revision = loaded_revision + 1
        if not self._butler.put_session_if_revision(existing, loaded_revision):
            stored = self._butler.get("observation_session", {"session_id": existing.id})
            raise PlanRevisionConflictError(
                "The plan changed while it was being placed. Read it again and redo the edit.",
                details={
                    "current_revision": stored.revision if stored is not None else None,
                    "expected_revision": loaded_revision,
                },
            )
        return existing

    def _schedule_session(
        self,
        requests: list[QueueRequest],
        site_profile: SiteProfile,
        telescope: Telescope,
        camera_id: str,
        night: date,
    ) -> ObservationSession:
        """Place recorded packages into a night, without storing the result.

        Parameters
        ----------
        requests : `list` [`QueueRequest`]
            The packages to place, each with its start mode.
        site_profile : `SiteProfile`
            The observing site.
        telescope : `Telescope`
            The telescope.
        camera_id : `str`
            The camera, recorded on the session.
        night : `datetime.date`
            The local date the night begins.

        Returns
        -------
        session : `ObservationSession`
            The placed queue and the reasons any package was left out.
        """
        from wayfindinglib.tasks.planning_tasks.scheduling import schedule_session
        from wayfindinglib.tasks.planning_tasks.target_report_tasks import build_target_dataset_report

        placements = []
        target_reports = {}
        for item in requests:
            request = QueueRequest.model_validate(item)
            package = self._package(request.package_id)
            placements.append((package, request.start_time_mode, request.requested_start_time))
            # One report for each target, however many packages name it.
            if package.quality_weighting_enabled and package.target_id not in target_reports:
                target_reports[package.target_id] = build_target_dataset_report(
                    self._target(package.target_id),
                    astrometrics=self._astrometrics,
                    butler=self._butler,
                    config=self._config,
                )
        return schedule_session(
            self._astrometrics,
            placements,
            site_profile,
            telescope,
            camera_id,
            night,
            target_reports=target_reports,
            planning_config=self._planning_config,
        )

    def _allowed_capture_values(self, session: ObservationSession) -> Any:
        """Work out what the session's camera may be asked to do.

        Parameters
        ----------
        session : `ObservationSession`
            The session; its camera id is the camera's name.

        Returns
        -------
        allowed : `AllowedCaptureValues`
            The measured settings from the camera profile and the filters
            seen on that camera's library frames.
        """
        from wayfindinglib.analytics.result_quality.allowed_capture_values import (
            allowed_capture_values_for_camera,
        )

        # A library with no quality records gives an empty dictionary.
        return allowed_capture_values_for_camera(
            session.camera_id, self._config, self._astrometrics.quality.capture_settings().values()
        )

    def edit_queue(
        self,
        session_id: str,
        add: list[QueueRequest | dict[str, Any]] | None = None,
        order: list[str] | None = None,
        remove: list[str] | None = None,
        *,
        set_exposures: dict[str, list[ExposureRequest | dict[str, Any]]] | None = None,
        add_checklist: list[ChecklistItem | dict[str, Any]] | None = None,
        framing: Framing | dict[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> PlanChange:
        """Change an observing session's night plan by hand.

        Added entries freeze a copy of their package (exposures,
        dithering, altitude limit and priority), the same way automatic
        placement does, so a session built by hand and one placed
        automatically run the same way. The queue changes apply in this
        order: `remove`, then `order`, then `add` (new entries go at the
        end), then `set_exposures`.

        Every exposure request is checked against the camera: a gain
        setting, offset or binning the camera profile did not measure is
        refused with the reason, and a camera with no measured setting
        accepts the request and notes "setting not measured". Exact
        duplicate requests in one entry merge (their counts add);
        requests that differ only in delay are refused.

        The write is one database step. When `expected_revision` is given,
        it must be the revision last read, or nothing is written.

        Parameters
        ----------
        session_id : `str`
            The session to change.
        add : `list` [`QueueRequest`], optional
            Recorded packages to append, each with its start mode, any
            start and end worked out by hand, and optionally its own
            exposure requests instead of the package's.
        order : `list` [`str`], optional
            Every entry id left after `remove`, in the new order.
        remove : `list` [`str`], optional
            Ids of entries to take out of the queue. An entry that is
            running cannot be removed.
        set_exposures : `dict` [`str`, `list` [`ExposureRequest`]], optional
            New exposure requests for existing entries, by entry id. An
            entry that is running or done cannot change.
        add_checklist : `list` [`ChecklistItem`], optional
            Checklist items to add. An item with the same rule and capture
            setting as one already on the list is not added again.
        framing : `Framing`, optional
            The field center, camera rotation and mosaic panels to store.
        expected_revision : `int`, optional
            The plan revision the caller last read. Leave out to write
            over whatever is stored.

        Returns
        -------
        change : `PlanChange`
            The new revision, what changed, notes on accepted settings and
            the stored session.

        Raises
        ------
        InvalidArgumentError
            If nothing to change is given, a request is malformed, `order`
            does not name exactly the entries in the queue, or an exposure
            request is refused.
        NotFoundError
            If `remove` or `set_exposures` names an entry that is not in
            the queue.
        ConflictError
            If `remove` names an entry that is running.
        PlanRevisionConflictError
            If the stored revision is not `expected_revision`. Its details
            hold the current revision.
        """
        from wayfindinglib.tasks.planning_tasks.night_plan_tasks import (
            add_checklist_items,
            check_exposure_requests,
            merge_exposure_requests,
            store_session_change,
        )

        if (
            add is None
            and order is None
            and remove is None
            and set_exposures is None
            and add_checklist is None
            and framing is None
        ):
            raise InvalidArgumentError("Give add, order, remove, set_exposures, add_checklist or framing.")
        session = self._session(session_id)
        loaded_revision = session.revision
        changes: list[PlanChangeItem] = []
        allowed = None
        notes: list[str] = []
        promoted_package_ids: list[str] = []

        def checked_requests(requests: list[ExposureRequest]) -> list[ExposureRequest]:
            """Merge duplicates and check the camera allows each request.

            Parameters
            ----------
            requests : `list` [`ExposureRequest`]
                The requests of one entry.

            Returns
            -------
            requests : `list` [`ExposureRequest`]
                The merged requests.
            """
            nonlocal allowed
            merged = merge_exposure_requests(requests)
            if allowed is None:
                allowed = self._allowed_capture_values(session)
            notes.extend(note for note in check_exposure_requests(merged, allowed) if note not in notes)
            return merged

        if remove:
            from astrometricslib import ConflictError

            entries_by_id = {entry.id: entry for entry in session.queue}
            missing = [entry_id for entry_id in remove if entry_id not in entries_by_id]
            if missing:
                raise NotFoundError("Those entries are not in the queue.", details={"remove": missing})
            running = [
                entry_id for entry_id in remove if entries_by_id[entry_id].status == QueueEntryStatus.RUNNING
            ]
            if running:
                raise ConflictError("A running entry cannot be removed.", details={"running": running})
            session.queue = [entry for entry in session.queue if entry.id not in set(remove)]
            changes.extend(PlanChangeItem(kind="queue_removed", entry_id=entry_id) for entry_id in remove)
        if order is not None:
            entries_by_id = {entry.id: entry for entry in session.queue}
            if sorted(order) != sorted(entries_by_id):
                raise InvalidArgumentError(
                    "order must name exactly the entries currently in the queue.",
                    details={"order": list(order), "entries": sorted(entries_by_id)},
                )
            session.queue = [entries_by_id[entry_id] for entry_id in order]
            changes.append(PlanChangeItem(kind="queue_reordered"))
        for item in add or []:
            try:
                request = QueueRequest.model_validate(item)
            except ValidationError as error:
                raise InvalidArgumentError(
                    "Each entry to add needs a package_id.", details={"error": str(error)}
                ) from error
            package = self._package(request.package_id)
            if request.exposure_requests is None:
                base_requests = self._requests_still_missing(package, session)
            else:
                base_requests = list(request.exposure_requests)
            requests = checked_requests(base_requests)
            entry = QueuedObservationPackage(
                id=str(uuid.uuid4()),
                observation_package_id=package.id,
                target_id=package.target_id,
                exposure_requests=requests,
                dither_config=package.dither_config,
                minimum_altitude_deg=package.minimum_altitude_deg,
                priority=package.priority,
                start_time_mode=request.start_time_mode,
                requested_start_time=request.requested_start_time,
                computed_start_time=request.computed_start_time,
                computed_end_time=request.computed_end_time,
            )
            session.queue.append(entry)
            changes.append(PlanChangeItem(kind="queue_added", detail=package.id, entry_id=entry.id))
            if package.status == PackageStatus.SUGGESTED and package.id not in promoted_package_ids:
                promoted_package_ids.append(package.id)
                changes.append(PlanChangeItem(kind="package_promoted", detail=package.id))
        for entry_id, raw_requests in (set_exposures or {}).items():
            entry = next((candidate for candidate in session.queue if candidate.id == entry_id), None)
            if entry is None:
                raise NotFoundError("That entry is not in the queue.", details={"set_exposures": entry_id})
            if entry.status != QueueEntryStatus.PENDING:
                from astrometricslib import ConflictError

                raise ConflictError(
                    "Only an entry that has not started can change its exposures.",
                    details={"entry_id": entry_id, "status": entry.status.value},
                )
            try:
                requests = [ExposureRequest.model_validate(raw) for raw in raw_requests]
            except ValidationError as error:
                raise InvalidArgumentError(
                    "Each exposure request needs frame_type, exposure_sec and count.",
                    details={"error": str(error)},
                ) from error
            entry.exposure_requests = checked_requests(requests)
            changes.append(PlanChangeItem(kind="exposures_set", entry_id=entry_id))
        if add_checklist is not None:
            try:
                items = [ChecklistItem.model_validate(item) for item in add_checklist]
            except ValidationError as error:
                raise InvalidArgumentError(
                    "Each checklist item needs a rule.", details={"error": str(error)}
                ) from error
            changes.extend(add_checklist_items(session, items))
        if framing is not None:
            try:
                session.framing = Framing.model_validate(framing)
            except ValidationError as error:
                raise InvalidArgumentError(
                    "Framing needs center_ra_deg and center_dec_deg.", details={"error": str(error)}
                ) from error
            changes.append(PlanChangeItem(kind="framing_set"))
        result = store_session_change(
            self._butler, session, loaded_revision, expected_revision, changes, notes
        )
        if isinstance(result, PlanRevisionConflictError):
            raise PlanRevisionConflictError(str(result), details=result.details) from result
        for package_id in promoted_package_ids:
            self._promote_suggested_package(package_id)
        return result

    def _requests_still_missing(
        self, package: ObservationPackage, session: ObservationSession
    ) -> list[ExposureRequest]:
        """List the rows a package still has to ask for in a session.

        A planned package that was partly captured in an earlier session asks
        a later one only for the missing frames. A light row that is met is
        dropped; a partly met row keeps the missing count. The counting is
        the one that decides completeness, with the session's camera and
        telescope. The package itself is not changed.

        When nothing was captured, when every row is already met (the package
        is about to turn complete, and the user is adding it on purpose), when
        the package is not planned, or when the frames cannot be read, the
        package's own rows are used.

        Parameters
        ----------
        package : `ObservationPackage`
            The package being added.
        session : `ObservationSession`
            The session it is added to. Its camera and telescope decide
            which frames count.

        Returns
        -------
        requests : `list` [`ExposureRequest`]
            The rows for the new queue entry.
        """
        full = list(package.exposure_requests)
        if package.status != PackageStatus.PLANNED or not any(
            row.frame_type == FrameType.LIGHT for row in full
        ):
            return full
        target_frames = TargetFrameReader(self._astrometrics).read(package.target_id)
        if target_frames.note and not target_frames.frames:
            return full
        remaining = missing_requests(full, target_frames, self._equipment_names(session), package.created_at)
        if not any(row.frame_type == FrameType.LIGHT for row in remaining):
            return full
        return remaining

    def _promote_suggested_package(self, package_id: str) -> None:
        """Make a suggested package planned, with a revision-checked write.

        A package that is no longer suggested (someone else already changed
        it) is left as it is. A write that loses a race is retried against
        the newly read package.

        Parameters
        ----------
        package_id : `str`
            The package that was added to a queue.
        """
        for _attempt in range(3):
            package = self._butler.get("observation_package", {"id": package_id})
            if package is None or package.status != PackageStatus.SUGGESTED:
                return
            loaded_revision = package.revision
            package.status = PackageStatus.PLANNED
            # Frames count toward the rows only from now on.
            moment = _utc_now()
            package.exposure_requests = [
                row.model_copy(update={"counted_from": moment}) for row in package.exposure_requests
            ]
            package.revision = loaded_revision + 1
            if self._butler.put_package_if_revision(package, loaded_revision):
                self._log_suggestion_event(SuggestionEvent.PROMOTED, package, "added to a session")
                return

    def edit_package(
        self,
        package_id: str,
        *,
        exposure_requests: list[ExposureRequest | dict[str, Any]] | None = None,
        dither_config: DitherConfig | dict[str, Any] | None = None,
        minimum_altitude_deg: float | None = None,
        priority: int | None = None,
        notes: str | None = None,
        name: str | None = None,
        framing: Framing | dict[str, Any] | None = None,
        reopen: bool = False,
        delete: bool = False,
        dismiss: bool = False,
        undismiss: bool = False,
        source_key: str | None = None,
        expected_revision: int | None = None,
    ) -> ObservationPackage:
        """Change, reopen, dismiss or delete one observation package.

        Values are checked the same way `create_plan` with ``kind="package"``
        checks them. A change counts as one write and adds one to the
        package's revision. A value left out (`None`) is not changed, so
        this call cannot clear the dither or the altitude limit.

        A new or changed exposure row starts counting frames from now. A row
        whose frame type, filter, length, gain, offset and binning are the
        same as a row already in the package keeps its counting time (a
        change of its count alone does not restart the count).

        Reopening a complete package stores, for each light row, how many
        matching frames are already captured, so those frames do not complete
        the package again. When the frames cannot be counted (the package is
        in no session, or the library cannot be read), each row starts
        counting from now instead.

        Dismissing a library suggestion hides it until its evidence changes
        (the refresh then shows it again). With `source_key` only that row
        is dismissed and taken out of the package. Undismissing reverses it.

        Parameters
        ----------
        package_id : `str`
            The package to change.
        exposure_requests : `list` [`ExposureRequest`], optional
            The new full list of exposures, replacing the old list. An empty
            list clears them.
        dither_config : `DitherConfig`, optional
            The new dithering.
        minimum_altitude_deg : `float`, optional
            The new lowest altitude the package may be imaged at.
        priority : `int`, optional
            The new priority.
        notes : `str`, optional
            The new free text.
        name : `str`, optional
            The new package name.
        framing : `Framing`, optional
            Where the camera points: the field center, the camera rotation
            and any mosaic panels. The Planetarium's "Send framing to
            package" sets it.
        reopen : `bool`, optional
            Set a complete package back to planned.
        delete : `bool`, optional
            Remove the package. Cannot be combined with any change.
        dismiss : `bool`, optional
            Dismiss a suggested package, or one row of it with `source_key`.
            Cannot be combined with any other change.
        undismiss : `bool`, optional
            Take a dismissal back. Cannot be combined with any other change.
        source_key : `str`, optional
            With `dismiss` or `undismiss`: the row to act on instead of the
            whole package.
        expected_revision : `int`, optional
            The package revision the caller last read. Leave out to write
            over whatever is stored.

        Returns
        -------
        package : `ObservationPackage`
            The stored package. For ``delete=True``, the package as it was
            when removed.

        Raises
        ------
        InvalidArgumentError
            If nothing to change is given, `delete`, `dismiss` or `undismiss`
            is mixed with a change, `source_key` is given without `dismiss`
            or `undismiss`, or a value is malformed.
        NotFoundError
            If no package has that id, or `source_key` names no row.
        ConflictError
            If `reopen` is asked for a package that is not complete, or a
            dismissal is asked for a package that is not a library
            suggestion.
        PlanRevisionConflictError
            If the stored revision is not `expected_revision`. Its details
            hold the current revision.
        """
        from astrometricslib import ConflictError

        changes = {
            "exposure_requests": exposure_requests,
            "dither_config": dither_config,
            "minimum_altitude_deg": minimum_altitude_deg,
            "priority": priority,
            "notes": notes,
            "name": name,
            "framing": framing,
        }
        has_change = any(value is not None for value in changes.values()) or reopen
        for flag, label in ((delete, "delete"), (dismiss, "dismiss"), (undismiss, "undismiss")):
            if flag and (has_change or sum([delete, dismiss, undismiss]) > 1):
                raise InvalidArgumentError(
                    f"{label} cannot be combined with a change or with delete, dismiss or undismiss."
                )
        if source_key is not None and not (dismiss or undismiss):
            raise InvalidArgumentError("source_key is used only with dismiss or undismiss.")
        if not (delete or dismiss or undismiss or has_change):
            raise InvalidArgumentError(
                "Give exposure_requests, dither_config, minimum_altitude_deg, priority, notes, name, "
                "framing, reopen, delete, dismiss or undismiss."
            )
        package = self._package(package_id)
        loaded_revision = package.revision
        wanted = loaded_revision if expected_revision is None else expected_revision
        if wanted != loaded_revision:
            raise PlanRevisionConflictError(
                f"The package is at revision {loaded_revision}, not {wanted}. "
                "Read it again and redo the edit.",
                details={"current_revision": loaded_revision, "expected_revision": wanted},
            )
        if delete:
            if not self._butler.delete_package(package_id):
                raise NotFoundError(
                    f"No observation package {package_id!r}.", details={"package_id": package_id}
                )
            return package
        if reopen and package.status != PackageStatus.COMPLETE:
            raise ConflictError(
                "Only a complete package can be reopened.",
                details={"package_id": package_id, "status": package.status.value},
            )
        if (dismiss or undismiss) and (
            package.status != PackageStatus.SUGGESTED or package.suggestion is None
        ):
            raise ConflictError(
                "Only a suggested package can be dismissed.",
                details={"package_id": package_id, "status": package.status.value},
            )
        if dismiss or undismiss:
            edited = self._dismissed(package, dismiss, source_key)
        else:
            edited = self._changed_package(package, changes, reopen)
        edited.revision = loaded_revision + 1
        if not self._butler.put_package_if_revision(edited, loaded_revision):
            stored = self._butler.get("observation_package", {"id": package_id})
            raise PlanRevisionConflictError(
                "The package changed while this edit was being made. Read it again and redo the edit.",
                details={
                    "current_revision": stored.revision if stored is not None else None,
                    "expected_revision": wanted,
                },
            )
        if dismiss or undismiss:
            self._log_suggestion_event(
                SuggestionEvent.DISMISSED if dismiss else SuggestionEvent.UNDISMISSED,
                edited,
                "the user asked",
                source_key or "",
            )
        elif reopen:
            self._log_suggestion_event(SuggestionEvent.REOPENED, edited, "the user reopened it")
        return edited

    def _changed_package(
        self, package: ObservationPackage, changes: dict[str, Any], reopen: bool
    ) -> ObservationPackage:
        """Apply the plain changes of `edit_package` to a copy of a package.

        Parameters
        ----------
        package : `ObservationPackage`
            The package as stored. It is not changed.
        changes : `dict`
            The values to set, `None` for one to leave alone.
        reopen : `bool`
            Whether to set a complete package back to planned.

        Returns
        -------
        edited : `ObservationPackage`
            The validated copy with the changes. Its revision is not yet
            bumped.

        Raises
        ------
        InvalidArgumentError
            If a value is not valid.
        """
        updates = {name_: value for name_, value in changes.items() if value is not None}
        if reopen:
            updates["status"] = PackageStatus.PLANNED
            updates["completed_at"] = None
        try:
            edited = ObservationPackage.model_validate({**package.model_dump(), **updates})
        except ValidationError as error:
            raise InvalidArgumentError(
                "A package value is not valid.", details={"error": str(error)}
            ) from error
        if changes.get("exposure_requests") is not None:
            edited.exposure_requests = self._stamped_rows(package.exposure_requests, edited.exposure_requests)
        if reopen:
            edited.exposure_requests = self._rows_with_baseline(edited)
        return edited

    @staticmethod
    def _stamped_rows(
        old_rows: list[ExposureRequest], new_rows: list[ExposureRequest]
    ) -> list[ExposureRequest]:
        """Give each edited row its counting time and baseline.

        A row that matches an old row in frame type, filter, length, gain,
        offset and binning keeps the old row's counting time and baseline. A
        new or changed row starts counting now. The source key, reason and
        fingerprint of a row the library suggested are kept.

        Parameters
        ----------
        old_rows : `list` [`ExposureRequest`]
            The package's rows before the edit.
        new_rows : `list` [`ExposureRequest`]
            The rows after the edit.

        Returns
        -------
        rows : `list` [`ExposureRequest`]
            The new rows with their counting fields set.
        """

        def match_key(row: ExposureRequest) -> tuple:
            """Name the fields that decide which frames a row counts.

            Parameters
            ----------
            row : `ExposureRequest`
                The row.

            Returns
            -------
            key : `tuple`
                Frame type, filter, length, gain, offset and binning.
            """
            return (row.frame_type, row.filter, row.exposure_sec, row.gain_setting, row.offset, row.binning)

        old_by_key = {match_key(row): row for row in old_rows}
        moment = _utc_now()
        stamped = []
        for row in new_rows:
            old = old_by_key.get(match_key(row))
            if old is None:
                stamped.append(row.model_copy(update={"counted_from": moment, "baseline_captured": 0}))
                continue
            stamped.append(
                row.model_copy(
                    update={
                        "counted_from": old.counted_from,
                        "baseline_captured": old.baseline_captured,
                        "source_key": row.source_key or old.source_key,
                        "reason": row.reason or old.reason,
                        "evidence_fingerprint": row.evidence_fingerprint or old.evidence_fingerprint,
                    }
                )
            )
        return stamped

    def _rows_with_baseline(self, package: ObservationPackage) -> list[ExposureRequest]:
        """Store how many frames are already captured for each light row.

        Used when a complete package is reopened. The matching frames
        already in the library become the row's baseline, so they do not
        complete it again. When the frames cannot be counted, the row
        counts from now instead.

        Parameters
        ----------
        package : `ObservationPackage`
            The package being reopened.

        Returns
        -------
        rows : `list` [`ExposureRequest`]
            The rows with their baselines (or new counting times) set.
        """
        from wayfindinglib.tasks.planning_tasks.suggestion_tasks import newest_session_with

        moment = _utc_now()
        session = newest_session_with(package.id, self._butler.get_all("observation_session"))
        target_frames = TargetFrameReader(self._astrometrics).read(package.target_id) if session else None
        rows = []
        for row in package.exposure_requests:
            if row.frame_type != FrameType.LIGHT:
                rows.append(row)
            elif (
                session is None or target_frames is None or (target_frames.note and not target_frames.frames)
            ):
                rows.append(row.model_copy(update={"counted_from": moment, "baseline_captured": 0}))
            else:
                count = count_request_frames(
                    row.model_copy(update={"baseline_captured": 0}),
                    target_frames,
                    self._equipment_names(session),
                    package.created_at,
                )
                rows.append(row.model_copy(update={"baseline_captured": count.captured}))
        return rows

    @staticmethod
    def _dismissed(package: ObservationPackage, dismiss: bool, source_key: str | None) -> ObservationPackage:
        """Dismiss or undismiss a suggested package, or one of its rows.

        Parameters
        ----------
        package : `ObservationPackage`
            The package as stored. It is not changed.
        dismiss : `bool`
            `True` to dismiss, `False` to take a dismissal back.
        source_key : `str` or `None`
            The row to act on, or `None` for the whole package.

        Returns
        -------
        edited : `ObservationPackage`
            A copy with the dismissal changed. Its revision is not yet bumped.

        Raises
        ------
        NotFoundError
            If `source_key` names no row (or no dismissed row).
        """
        edited = package.model_copy(deep=True)
        suggestion = edited.suggestion
        if suggestion is None:
            return edited
        if source_key is None:
            if dismiss and not suggestion.evidence_fingerprint:
                suggestion.evidence_fingerprint = package.id[:16]
            suggestion.dismissed_fingerprint = suggestion.evidence_fingerprint if dismiss else ""
            return edited
        if dismiss:
            row = next((row for row in edited.exposure_requests if row.source_key == source_key), None)
            if row is None:
                raise NotFoundError(
                    f"No row {source_key!r} in the package.",
                    details={"package_id": package.id, "source_key": source_key},
                )
            edited.exposure_requests = [item for item in edited.exposure_requests if item is not row]
            suggestion.dismissed_rows.append(
                DismissedRow(source_key=source_key, fingerprint=row.evidence_fingerprint, request=row)
            )
            return edited
        held = next((item for item in suggestion.dismissed_rows if item.source_key == source_key), None)
        if held is None:
            raise NotFoundError(
                f"No dismissed row {source_key!r} in the package.",
                details={"package_id": package.id, "source_key": source_key},
            )
        suggestion.dismissed_rows = [item for item in suggestion.dismissed_rows if item is not held]
        edited.exposure_requests.append(held.request)
        return edited

    def tick_checklist_item(
        self,
        session_id: str,
        item_id: str,
        *,
        expected_revision: int | None = None,
        note: str | None = None,
        ticked_at: datetime | None = None,
    ) -> PlanChange:
        """Record that the user did one item of the night plan's checklist.

        Whether the tick holds is not decided here. A later advisory read
        compares the tick time with the measurements taken since and says
        when an item comes back.

        Parameters
        ----------
        session_id : `str`
            The session whose checklist holds the item.
        item_id : `str`
            The item to tick.
        expected_revision : `int`, optional
            The plan revision the caller last read. Leave out to write
            over whatever is stored.
        note : `str`, optional
            A note to keep with the item.
        ticked_at : `datetime`, optional
            When the user did it. Now when omitted.

        Returns
        -------
        change : `PlanChange`
            The new revision and the tick.

        Raises
        ------
        NotFoundError
            If the session or the item does not exist.
        PlanRevisionConflictError
            If the stored revision is not `expected_revision`.
        """
        from wayfindinglib.tasks.planning_tasks.night_plan_tasks import (
            find_checklist_item,
            store_session_change,
            tick_item,
        )

        session = self._session(session_id)
        item = find_checklist_item(session, item_id)
        if item is None:
            raise NotFoundError(
                f"No checklist item {item_id!r} in session {session_id!r}.",
                details={"item_id": item_id, "session_id": session_id},
            )
        loaded_revision = session.revision
        change = tick_item(item, ticked_at, note)
        result = store_session_change(self._butler, session, loaded_revision, expected_revision, [change], [])
        if isinstance(result, PlanRevisionConflictError):
            raise PlanRevisionConflictError(str(result), details=result.details) from result
        return result

    def get_plan(
        self,
        session_id: str | None = None,
        *,
        kind: PlanListKind = "sessions",
        night_id: str | None = None,
        status: PackageStatus | str | None = None,
        target: str | Target | None = None,
        include: list[str] | None = None,
        include_dismissed: bool = False,
        pick: Literal["all", "one"] = "all",
    ) -> (
        ObservationSession
        | list[ObservationSessionSummary]
        | list[ObservationPackage]
        | list[SuggestionLogEntry]
        | ObservationPackage
        | None
    ):
        """Read recorded sessions, observation packages or the suggestion log.

        Parameters
        ----------
        session_id : `str`, optional
            ``kind="sessions"`` only. The session to read.
        kind : `str`, optional
            ``"sessions"`` (default): one session when `session_id` or
            `night_id` is given, otherwise one line per session.
            ``"packages"``: the package library, newest first, narrowed by
            `status` and `target`. ``"suggestion_log"``: the suggestion
            lifecycle log (created, updated, deleted with the cause,
            dismissed, completed), newest first, narrowed by `target`.
        night_id : `str`, optional
            ``kind="sessions"`` only. The observing night, such as
            ``"2026-09-24"``. Gives that night's session, or `None` when the
            night has none. A night with several old sessions gives the one
            with the most queue entries, then the newest.
        status : `PackageStatus` or `str`, optional
            ``kind="packages"`` only. Keep packages with this status.
        target : `str` or `Target`, optional
            ``kind="packages"`` or ``"suggestion_log"``. Keep only this
            target.
        include : `list` [`str`], optional
            ``kind="sessions"`` only. ``"progress"`` counts the frames that
            arrived. One session then comes back as an
            `ObservationSessionWithProgress` (its `progress` holds, for each
            entry, the frames requested and captured, flagged and not
            judged, the entry being imaged now and a typed run state,
            including "not run" for a night that passed with nothing
            captured). A list of sessions then has each line's `run_state`.
        include_dismissed : `bool`, optional
            ``kind="packages"`` only. Also list suggestions the user
            dismissed. They are left out by default.
        pick : `str`, optional
            ``kind="packages"`` only. ``"all"`` (default) lists the packages.
            ``"one"`` needs `target` and gives that target's package: the
            newest planned one, else the newest suggested one that is not
            dismissed, else `None`. Complete packages are never picked. The
            target is matched like the list does (ignoring case, with ``_``
            read as a space).

        Returns
        -------
        plan : `ObservationSession`, `list` or `None`
            A whole session (or `None` for a night with none), one line per
            session newest night first, the packages newest first, or the
            log lines newest first.

        Raises
        ------
        InvalidArgumentError
            If `kind`, `status` or `include` is unknown, an argument the kind
            does not use is given, or both `session_id` and `night_id` are
            given.
        """
        check_choice("kind", kind, tuple(GET_PLAN_ARGUMENTS))
        reject_unused_arguments(
            kind,
            GET_PLAN_ARGUMENTS,
            {
                "session_id": session_id is not None,
                "night_id": night_id is not None,
                "status": status is not None,
                "target": target is not None,
                "include": include is not None,
                "include_dismissed": include_dismissed,
                "pick": pick != "all",
            },
        )
        sections = check_include(include, GET_PLAN_SECTIONS)
        check_choice("pick", pick, ("all", "one"))
        if kind == "packages" and pick == "one":
            if target is None:
                raise InvalidArgumentError("pick='one' needs a target.")
            return self._package_for_target(target)
        if kind == "packages":
            return self._list_packages(status, target, include_dismissed)
        if kind == "suggestion_log":
            return self._suggestion_log(target)
        if session_id is not None and night_id is not None:
            raise InvalidArgumentError("Give session_id or night_id, not both.")
        session = None
        if session_id is not None:
            session = self._session(session_id)
        elif night_id is not None:
            session = self._session_for_night(_night_date(night_id))
        if session_id is not None or night_id is not None:
            if session is None:
                return None
            session = self._with_checklist_labels(session)
            if "progress" not in sections:
                return session
            return self._with_progress(session)
        reader = TargetFrameReader(self._astrometrics) if "progress" in sections else None
        summaries = []
        for recorded in self._butler.get_all("observation_session"):
            run_state = None
            if reader is not None:
                from wayfindinglib.analytics.session_progress import run_state_of_session

                run_state = run_state_of_session(
                    recorded, reader, self._equipment_names(recorded), _utc_now()
                )
            summaries.append(
                ObservationSessionSummary(
                    id=recorded.id,
                    status=recorded.status,
                    night_date=recorded.night_date,
                    entry_count=len(recorded.queue),
                    run_state=run_state,
                )
            )
        summaries.sort(key=lambda summary: summary.night_date, reverse=True)
        return summaries

    def _with_checklist_labels(self, session: ObservationSession) -> ObservationSession:
        """Give each checklist item a plain label if it has none.

        Parameters
        ----------
        session : `ObservationSession`
            The stored session. It is not changed.

        Returns
        -------
        session : `ObservationSession`
            The session, with an empty item label replaced by the purpose of
            the item's rule (the rule's one plain sentence), or by the rule
            name when no rule has that name. The session itself when no item
            needs a label.
        """
        if all(item.label for item in session.checklist):
            return session
        from wayfindinglib.analytics.result_quality.all_rules import ALL_RULES

        purposes = {rule.name: rule.purpose for rule in ALL_RULES}
        labelled = session.model_copy(deep=True)
        for item in labelled.checklist:
            if not item.label:
                item.label = purposes.get(item.rule, item.rule)
        return labelled

    def _with_progress(self, session: ObservationSession) -> ObservationSessionWithProgress:
        """Add the counted progress to a session read.

        Parameters
        ----------
        session : `ObservationSession`
            The stored session.

        Returns
        -------
        session : `ObservationSessionWithProgress`
            The session with its `progress` set.
        """
        from wayfindinglib.analytics.session_progress import build_session_progress

        progress = build_session_progress(
            session, TargetFrameReader(self._astrometrics), self._equipment_names(session), _utc_now()
        )
        return ObservationSessionWithProgress.model_validate({**session.model_dump(), "progress": progress})

    def _suggestion_log(self, target: str | Target | None) -> list[SuggestionLogEntry]:
        """List the suggestion lifecycle log, newest first.

        Parameters
        ----------
        target : `str` or `Target` or `None`
            Keep only this target's lines, or every line when `None`.

        Returns
        -------
        entries : `list` [`SuggestionLogEntry`]
            The log lines, newest first.
        """
        wanted = (
            None if target is None else _fold_target_id(target.id if isinstance(target, Target) else target)
        )
        entries = [
            entry
            for entry in self._butler.get_all("package_suggestion_event")
            if wanted is None or _fold_target_id(entry.target_id) == wanted
        ]
        entries.sort(key=lambda entry: entry.time, reverse=True)
        return entries

    def _package_for_target(self, target: str | Target) -> ObservationPackage | None:
        """Pick the one package that stands for a target.

        Parameters
        ----------
        target : `str` or `Target`
            The target, matched like `_list_packages` matches it.

        Returns
        -------
        package : `ObservationPackage` or `None`
            The newest planned package, else the newest suggested package
            that is not dismissed, else `None`.
        """
        for status in (PackageStatus.PLANNED, PackageStatus.SUGGESTED):
            found = self._list_packages(status, target, False)
            if found:
                return found[0]
        return None

    def _list_packages(
        self, status: PackageStatus | str | None, target: str | Target | None, include_dismissed: bool = False
    ) -> list[ObservationPackage]:
        """List the recorded packages, newest first.

        Parameters
        ----------
        status : `PackageStatus` or `str` or `None`
            Keep only this status, or every status when `None`.
        target : `str` or `Target` or `None`
            Keep only this target's packages, or every target when `None`.
        include_dismissed : `bool`, optional
            Also keep suggestions the user dismissed.

        Returns
        -------
        packages : `list` [`ObservationPackage`]
            The matching packages, newest first.

        Raises
        ------
        InvalidArgumentError
            If `status` is not a package status.
        """
        wanted_status = None
        if status is not None:
            try:
                wanted_status = PackageStatus(status)
            except ValueError as error:
                raise InvalidArgumentError(
                    f"status must be one of: {', '.join(item.value for item in PackageStatus)}.",
                    details={"status": str(status)},
                ) from error

        wanted_target = None
        if target is not None:
            wanted_target = _fold_target_id(target.id if isinstance(target, Target) else target)
        packages = [
            package
            for package in self._butler.get_all("observation_package")
            if (wanted_status is None or package.status == wanted_status)
            and (wanted_target is None or _fold_target_id(package.target_id) == wanted_target)
            and (include_dismissed or not package.is_dismissed)
        ]
        packages.sort(key=lambda package: package.created_at, reverse=True)
        return packages

    # -- Depth goal --------------------------------------------------------

    def get_depth_goal(self, target: str | Target) -> DepthGoalStatus:
        """Say how close a target's image is to its chosen depth level.

        The depth levels are Quick look, Good (the default) and Deep. Each is a
        depth in magnitudes that depends on the kind of object (register row
        WAY-D94). The status gives the chosen level, the newest library
        stack's depth, the level that depth reaches, and the time to reach the
        chosen level, forecast with the "take N more" machinery.

        Parameters
        ----------
        target : `str` or `Target`
            The target, by id or as a `Target`.

        Returns
        -------
        status : `DepthGoalStatus`
            The status, with a sentence for a novice in ``message``.
        """
        from wayfindinglib.tasks.planning_tasks.depth_goal_tasks import build_depth_goal_status
        from wayfindinglib.tasks.planning_tasks.target_report_tasks import build_target_dataset_report

        resolved = self._target(target)
        return build_depth_goal_status(
            resolved.id,
            astrometrics=self._astrometrics,
            butler=self._butler,
            report_for_noise_ratio=lambda ratio: build_target_dataset_report(
                resolved,
                astrometrics=self._astrometrics,
                butler=self._butler,
                config=self._config,
                target_noise_ratio=ratio,
            ),
        )

    def set_depth_goal(self, target: str | Target, level: DepthLevel | str) -> DepthGoalStatus:
        """Choose the depth level of a target.

        The level is kept in wayfindinglib's own database, not in the
        configuration file. A level that is not one of the three raises
        `InvalidArgumentError`.

        Parameters
        ----------
        target : `str` or `Target`
            The target, by id or as a `Target`.
        level : `DepthLevel` or `str`
            ``"quick_look"``, ``"good"`` or ``"deep"``.

        Returns
        -------
        status : `DepthGoalStatus`
            The status for the new level.

        """
        from wayfindinglib.tasks.planning_tasks.depth_goal_tasks import coerce_level, store_chosen_level

        chosen = coerce_level(level)
        resolved = self._target(target)
        store_chosen_level(self._butler, resolved.id, chosen)
        return self.get_depth_goal(resolved)

    # -- Suggestions -------------------------------------------------------

    @background_job("planning", grace_period_seconds=20.0)
    def refresh_suggestions(
        self, target_ids: list[str] | None = None, register_job: bool = True
    ) -> PackageChange:
        """Update suggested packages, the checklist and finished packages.

        One run does four things:

        * **Rule suggestions.** A target's quality report may ask for more
          frames. While the target's noise is above the library's one
          default noise goal, that becomes one suggested package of
          exposure rows for the target. Each row keeps its own reason and
          source key (rule, target, capture setting). The package is
          deleted only when the rules say frames are no longer needed (the
          noise is at or below the goal, or a stop rule spoke). A target
          whose noise or report is missing is left as it is: missing data
          never deletes a suggestion.
        * **Calibration suggestions.** A rule whose action is a checklist
          item (such as taking flats) adds it to tonight's session
          checklist, merged with the items already there. Nothing is added
          when tonight has no session.
        * **Ranking suggestions.** The top 3 targets of each use in
          tonight's ranking become suggested packages with no rows, tied to
          tonight, labeled a design estimate. They are deleted when the
          night ends or the target falls out of the top. The ranking is read
          only when `target_ids` is `None`.
        * **Completion.** A planned package whose light rows are all
          captured and judged becomes complete. A complete package that is
          no longer met (frames were set aside) becomes planned again.

        A source key held by a planned or complete package, or by a queue
        entry of tonight or later, is not suggested again. A dismissed
        suggestion stays hidden until its evidence changes. Every write
        checks the revision it read, so an edit made at the same time is
        never overwritten (a lost race is read again, up to three times).
        Every change is written to the suggestion log (see `get_plan` with
        ``kind="suggestion_log"``).

        Parameters
        ----------
        target_ids : `list` [`str`], optional
            Refresh only these targets (after new frames were measured).
            `None` refreshes every library target and the ranking.
        register_job : `bool`, optional
            Record the run as a job in the job history, with its log.
            Defaults to `True`.

        Returns
        -------
        change : `PackageChange`
            The ids of the packages added, updated, deleted, completed and
            reverted, the checklist items added, and notes on anything
            skipped.
        """
        with registered_job(
            enabled=register_job and get_current_job() is None,
            job_type="planning",
            target_id="package_suggestions",
        ):
            return self._refresh_suggestions(target_ids)

    @staticmethod
    def _refresh_rule_package_of(
        tasks: Any,
        target_id: str,
        reading: Any,
        noise: Any,
        depth_met: bool | None,
        held_keys: Callable[[], set[str]],
        writer: Any,
        packages: list[ObservationPackage],
    ) -> None:
        """Refresh one target's suggested package of rule rows.

        Parameters
        ----------
        tasks : `module`
            The suggestion tasks module.
        target_id : `str`
            The target.
        reading : `RuleReading`
            What the target's statements ask for.
        noise : `TargetNoise` or `None`
            The target's noise.
        depth_met : `bool` or `None`
            Whether the target reached its chosen depth level, or `None`
            when it has no depth goal.
        held_keys : `Callable`
            Gives the source keys no suggestion may take now.
        writer : `PackageWriter`
            Writes with revision checks and logs.
        packages : `list` [`ObservationPackage`]
            Every package, freshly read.
        """
        from wayfindinglib.analytics.package_suggestions import DEFAULT_NOISE_GOAL

        tasks.refresh_rule_package(
            writer,
            packages,
            target_id,
            reading,
            noise,
            DEFAULT_NOISE_GOAL,
            held_keys(),
            depth_goal_met=depth_met,
        )

    def _refresh_suggestions(self, target_ids: list[str] | None) -> PackageChange:
        """Run the suggestion refresh. See `refresh_suggestions`.

        Parameters
        ----------
        target_ids : `list` [`str`] or `None`
            The targets to refresh, or `None` for all.

        Returns
        -------
        change : `PackageChange`
            What changed.
        """
        import sqlite3

        from astrometricslib import observing_night_id
        from wayfindinglib.analytics.package_suggestions import (
            RANKING_SUGGESTIONS_PER_USE,
            read_target_noise,
        )
        from wayfindinglib.analytics.result_quality.all_rules import ALL_RULES
        from wayfindinglib.tasks.planning_tasks import suggestion_tasks as tasks
        from wayfindinglib.tasks.planning_tasks.depth_goal_tasks import depth_goal_met

        now = _utc_now()
        change = PackageChange(as_of=now)
        night_id = observing_night_id(now.timestamp())
        tonight_session = self._session_for_night(date.fromisoformat(night_id))
        rules = {rule.name: rule for rule in ALL_RULES}
        try:
            from wayfindinglib.analytics.result_quality.exposure_request import (
                exposure_request_from_statement,
            )
        except ImportError:
            exposure_request_from_statement = None
            change.notes.append(
                "Rule suggestions are not available: no function turns a statement into a row."
            )
        targets = (
            list(dict.fromkeys(target_ids))
            if target_ids is not None
            else [target.id for target in self._astrometrics.targets.list()]
        )
        reader = TargetFrameReader(self._astrometrics)
        noise_by_target: dict[str, Any] = {}

        def noise_for(target_id: str) -> Any:
            """Read a target's noise once per refresh.

            Parameters
            ----------
            target_id : `str`
                The target.

            Returns
            -------
            noise : `TargetNoise` or `None`
                The target's noise, or `None` when unknown.
            """
            if target_id not in noise_by_target:
                noise_by_target[target_id] = read_target_noise(self._astrometrics, target_id)
            return noise_by_target[target_id]

        def held_keys() -> set[str]:
            """Collect the source keys no suggestion may take now.

            Returns
            -------
            keys : `set` [`str`]
                The held keys, read fresh.
            """
            return tasks.held_source_keys(
                self._butler.get_all("observation_package"),
                self._butler.get_all("observation_session"),
                night_id,
                lambda target_id: getattr(noise_for(target_id), "measured_at", None),
            )

        def retrying(work: Any, label: str) -> None:
            """Run one write step, reading again when it loses a race.

            Parameters
            ----------
            work : `Callable`
                Takes a `PackageWriter` and the freshly read packages.
            label : `str`
                What the step is, for the note when it gives up.
            """
            for _attempt in range(3):
                writer = tasks.PackageWriter(self._butler, now, change)
                try:
                    work(writer, self._butler.get_all("observation_package"))
                    return
                except PlanRevisionConflictError:
                    continue
            change.notes.append(f"{label}: a package kept changing, so it was left for the next refresh.")

        checklist_statements: list[TargetStatement] = []
        for target_id in targets:
            if exposure_request_from_statement is None:
                break
            try:
                report = self.get_advisory(kind="quality", target=target_id)
            except (AstrometricsError, OSError, sqlite3.Error, ValueError) as error:
                logger.warning("Suggestions: no report for %s: %s", target_id, error)
                change.notes.append(
                    f"{target_id}: no report ({error}). Its suggestions were left as they are."
                )
                continue
            reading = tasks.read_rule_statements(
                target_id, report.statements, rules, exposure_request_from_statement
            )
            checklist_statements.extend(reading.checklist)
            noise = noise_for(target_id)
            depth_met = depth_goal_met(self._astrometrics, self._butler, target_id)
            retrying(
                partial(
                    self._refresh_rule_package_of,
                    tasks,
                    target_id,
                    reading,
                    noise,
                    depth_met,
                    held_keys,
                ),
                target_id,
            )
        self._add_calibration_items(tonight_session, checklist_statements, change)
        ranking = None
        if target_ids is None:
            try:
                ranking = self.get_advisory(kind="tonight", at=now)
            except (AstrometricsError, OSError, sqlite3.Error, ValueError) as error:
                logger.warning("Suggestions: no ranking: %s", error)
                change.notes.append(
                    f"No ranking for tonight ({error}). Ranking suggestions were left as they are."
                )
        queued_targets = {entry.target_id for entry in tonight_session.queue} if tonight_session else set()
        retrying(
            lambda writer, packages: tasks.refresh_ranking_packages(
                writer, packages, ranking, night_id, RANKING_SUGGESTIONS_PER_USE, held_keys(), queued_targets
            ),
            "ranking",
        )
        retrying(
            lambda writer, packages: tasks.refresh_completion(
                writer,
                packages,
                self._butler.get_all("observation_session"),
                reader,
                self._equipment_names,
                None if target_ids is None else set(targets),
            ),
            "completion",
        )
        for name in ("added", "updated", "deleted", "completed", "reverted"):
            setattr(change, name, list(dict.fromkeys(getattr(change, name))))
        return change

    def _add_calibration_items(
        self,
        session: ObservationSession | None,
        statements: list[TargetStatement],
        change: PackageChange,
    ) -> None:
        """Add the checklist items that calibration statements ask for.

        Parameters
        ----------
        session : `ObservationSession` or `None`
            Tonight's session. With `None`, nothing is added (a note says so).
        statements : `list` [`TargetStatement`]
            Statements whose rule makes a checklist item.
        change : `PackageChange`
            The refresh result. The new item ids and any note are added.
        """
        from wayfindinglib.tasks.planning_tasks.suggestion_tasks import checklist_items_from

        items = checklist_items_from(statements)
        if not items:
            return
        if session is None:
            change.notes.append("Tonight has no session, so calibration items were not added to a checklist.")
            return
        for _attempt in range(3):
            try:
                result = self.edit_queue(session.id, add_checklist=items)
            except PlanRevisionConflictError:
                continue
            change.checklist_item_ids.extend(
                item.entry_id for item in result.changes if item.kind == "checklist_added" and item.entry_id
            )
            return
        change.notes.append(
            "The checklist kept changing, so calibration items were left for the next refresh."
        )

    # -- Deep-star catalog -------------------------------------------------

    @background_job("planning", grace_period_seconds=20.0)
    def deep_catalog_status(
        self,
        include: list[str] | None = None,
        healpix_level: int | None = None,
        magnitude_limit: float | None = None,
        sample_count: int | None = None,
        query_timeout_seconds: float | None = None,
        request_delay_seconds: float | None = None,
        register_job: bool = True,
    ) -> DeepCatalogStatus:
        """Report the installed deep-star catalog, and maybe its full size.

        The Planetarium draws faint stars from a local copy of the Gaia
        catalog. This reads how much of it is on disk. With
        ``include=["estimate"]`` it also guesses the finished size by
        counting the stars in a sample of chunks, which sends about two
        dozen queries to the Gaia archive. Nothing is saved.

        Parameters
        ----------
        include : `list` [`str`], optional
            ``"estimate"`` adds the size estimate.
        healpix_level : `int`, optional
            How finely the estimate cuts the sky. Estimate only.
        magnitude_limit : `float`, optional
            Count only stars brighter than this Gaia G magnitude. Estimate
            only.
        sample_count : `int`, optional
            How many chunks to count. Estimate only.
        query_timeout_seconds : `float`, optional
            Seconds to wait for each count. Estimate only.
        request_delay_seconds : `float`, optional
            Seconds to pause between counts. Estimate only.
        register_job : `bool`, optional
            Record the estimate as a job in the job history, with its log.
            Defaults to `True`.

        Returns
        -------
        status : `DeepCatalogStatus`
            What is installed, and the estimate when asked for.

        Raises
        ------
        InvalidArgumentError
            If an estimate setting is given without ``"estimate"``.
        """
        from wayfindinglib.drivers.catalog import deep_star_store

        sections = check_include(include, DEEP_CATALOG_SECTIONS)
        settings = {
            "healpix_level": healpix_level,
            "magnitude_limit": magnitude_limit,
            "sample_count": sample_count,
            "query_timeout_seconds": query_timeout_seconds,
            "request_delay_seconds": request_delay_seconds,
        }
        given = sorted(name for name, value in settings.items() if value is not None)
        if given and "estimate" not in sections:
            raise InvalidArgumentError(
                f'{", ".join(given)} apply only with include=["estimate"].', details={"unused": given}
            )
        status = DeepCatalogStatus.model_validate(deep_star_store.read_catalog_status(self._config))
        if "estimate" in sections:
            from wayfindinglib.drivers.catalog.deep_star_catalog_builder import estimate_catalog_size

            # Under a job already (a caller may run this as one), join it
            # rather than listing the same work twice.
            with registered_job(
                enabled=register_job and get_current_job() is None,
                job_type="planning",
                target_id="deep_catalog",
            ):
                estimate = estimate_catalog_size(**{
                    name: value for name, value in settings.items() if value is not None
                })
            status.estimate = DeepCatalogEstimate.model_validate(estimate)
        return status

    def build_deep_star_catalog(self, **kwargs: Any) -> dict[str, Any]:
        """Download the deep-star catalog the Planetarium draws from.

        A thin pass-through to
        `wayfindinglib.drivers.catalog.deep_star_catalog_builder.build_deep_star_catalog`,
        binding this instance's own configuration. See that function for
        the accepted keyword arguments and the returned report's shape.

        Returns
        -------
        report : `dict`
            The download report. See
            `deep_star_catalog_builder.build_deep_star_catalog`.
        """
        from wayfindinglib.drivers.catalog.deep_star_catalog_builder import (
            build_deep_star_catalog as _build_deep_star_catalog,
        )

        return _build_deep_star_catalog(self._config, **kwargs)
