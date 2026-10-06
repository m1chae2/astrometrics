"""Purpose: `ObservationPlanning`, the entry point for deciding what to see.

Description: `Wayfinder.planning` is an `ObservationPlanning`. It answers
where objects are in the sky (`get_visibility`), advises on targets and
calibration frames (`get_advisory`), lays out mosaics
(`calculate_panels`, `create_mosaic`), writes plans (`create_plan`,
`edit_queue`) and reads them back (`get_plan`). It also browses the
library and online catalogs around a point in the sky and reports on the
downloaded deep-star catalog.

Planning never talks to hardware. A plan made by hand (`create_plan`
with ``kind="empty_session"``, then `edit_queue`) and a plan placed
automatically (``kind="scheduled_session"``) write the same queue
structure, so `ObservationExecution` runs either one.

Each method checks its arguments and hands the work to a function under
`wayfindinglib.tasks.planning_tasks`. Callers never import those tasks
directly.
"""

import threading
import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Literal

from astropy.time import Time
from pydantic import ValidationError

from astrometricslib import (
    AppConfiguration,
    Astrometrics,
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
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.equipment_and_site.calibration import CalibrationAdvisory
from wayfindinglib.models.equipment_and_site.equipment import EquipmentConfiguration, Telescope
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.planning.deep_catalog import DeepCatalogEstimate, DeepCatalogStatus
from wayfindinglib.models.planning.mosaic import MosaicPanel, MosaicPlan
from wayfindinglib.models.planning.observation_package import (
    DitherConfig,
    ExposureRequest,
    FrameType,
    ObservationPackage,
)
from wayfindinglib.models.planning.planning_config import PlanningConfig
from wayfindinglib.models.planning.quality_advisory import TargetQualityAdvisory
from wayfindinglib.models.planning.sequence_plan import SequencePlan
from wayfindinglib.models.planning.visibility import HorizonZone, VisibilityReport
from wayfindinglib.models.session.observation_session import (
    ObservationSession,
    ObservationSessionSummary,
    QueuedObservationPackage,
    QueueRequest,
    SessionStatus,
)

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "ObservationPlanning",
]


MAXIMUM_SOURCES = 300
"""Most stars one `ObservationPlanning.find_sources` answer lists."""

AdvisoryKind = Literal["quality", "calibration"]
"""The kinds of advice `ObservationPlanning.get_advisory` gives."""

ADVISORY_ARGUMENTS = {
    "quality": ("target",),
    "calibration": ("camera_id", "frame_type", "exposure_seconds", "filter"),
}
"""The arguments each advisory kind uses."""

PlanKind = Literal["sequence", "package", "empty_session", "scheduled_session"]
"""The kinds of plan `ObservationPlanning.create_plan` writes."""

PLAN_ARGUMENTS = {
    "sequence": ("target", "plan_items"),
    "package": (
        "target",
        "exposure_requests",
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
    "package": ("target", "exposure_requests"),
    "empty_session": ("site_profile", "telescope", "camera_id", "night_id"),
    "scheduled_session": ("requests", "site_profile", "telescope", "camera_id", "night_id"),
}
"""The arguments each plan kind cannot do without."""

DEEP_CATALOG_SECTIONS = ("estimate",)
"""Optional sections `ObservationPlanning.deep_catalog_status` can add."""


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
        # one apiece -- each `Sky` loads its own full copy of the star catalog.
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
        sky : `wayfindinglib.sky.Sky`
            The engine.
        """
        if self.__sky_engine is None:
            with self._engine_construction_lock:
                if self.__sky_engine is None:
                    from wayfindinglib.sky import Sky

                    self.__sky_engine = Sky(config=self._config, astrometrics=self._astrometrics)
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
                except TypeError, ValueError:
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
    ) -> list[Target | StellarObject]:
        """Return targets, and optionally stars, within a radius of a point.

        Loading every library star in full takes seconds on a large
        library; read them with `get_library_star_summaries` instead.

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
            SIMBAD's objects. Defaults to ``["stars"]``; pass ``[]`` for
            library targets only.

        Returns
        -------
        sources : `list` [`Target` or `StellarObject`]
            The objects within the radius.
        """
        sections = check_include(include, ("stars", "online"))
        return self._sky_engine.get_sources(
            ra_deg, dec_deg, radius_deg, "online" in sections, "stars" in sections
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
    ) -> list[tuple[str, Any]]:
        """Return matching stellar objects from enabled catalog drivers.

        Returns
        -------
        sources : `list` [`tuple` [`str`, `Any`]]
            Each match paired with the driver name that found it.
        """
        return self._sky_engine.get_online_catalog_sources(
            ra_deg, dec_deg, radius_deg, enabled_driver_names, magnitude_limit
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
        from astrometricslib import derive_field_centers

        return derive_field_centers(self._astrometrics.targets.list())

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
            Write times in this UTC offset, such as -6 for Montana in
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
    ) -> TargetQualityAdvisory | CalibrationAdvisory:
        """Give planning advice about a target or about calibration frames.

        The advice is worked out on demand and never stored.

        Parameters
        ----------
        kind : `str`
            ``"quality"``: the quality flags each pipeline raised for a
            target and its science results so far. Uses `target`.
            ``"calibration"``: how many calibration frames of one kind the
            library already holds for a camera. Uses `camera_id`,
            `frame_type`, `exposure_seconds` and `filter`.
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

        Returns
        -------
        advisory : `TargetQualityAdvisory` or `CalibrationAdvisory`
            The advice for the chosen kind.

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
        }
        reject_unused_arguments(kind, ADVISORY_ARGUMENTS, given)
        if kind == "quality":
            if target is None:
                raise InvalidArgumentError('kind="quality" needs a target.')
            from wayfindinglib.tasks.planning_tasks.quality_advisory_tasks import (
                build_target_quality_advisory,
            )

            return build_target_quality_advisory(self._target(target))
        if camera_id is None or frame_type is None:
            raise InvalidArgumentError('kind="calibration" needs camera_id and frame_type.')
        from wayfindinglib.tasks.planning_tasks.calibration_advisory_tasks import build_calibration_advisory

        return build_calibration_advisory(
            self._butler, camera_id, FrameType(frame_type), exposure_seconds, filter
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

        Returns
        -------
        panels : `list` [`MosaicPanel`]
            One panel per grid cell, row by row.
        """
        from wayfindinglib.tasks.planning_tasks.mosaic_tasks import calculate_panels

        return calculate_panels(self._config, center_ra, center_dec, rows, cols, overlap_percent, equipment)

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
            `exposure_requests` and the optional `dither_config`,
            `minimum_altitude_deg`, `priority`,
            `quality_weighting_enabled` and `notes`. Recorded.
            ``"empty_session"``: an observing session with an empty queue
            for the night `night_id`, to fill by hand with `edit_queue`.
            Uses `site_profile`, `telescope` and `camera_id`. Recorded.
            ``"scheduled_session"``: an observing session for the night
            `night_id` with every package in `requests` placed
            automatically, using `site_profile`, `telescope` and
            `camera_id`. Packages that cannot be placed are listed with
            the reason. No device is contacted. Recorded.
        target : `str` or `Target`, optional
            The target, by id or as a `Target`.
        plan_items : `list` [`dict`], optional
            Sequence items, each with ``count``, ``exposure`` (seconds)
            and ``filter``.
        exposure_requests : `list` [`ExposureRequest`], optional
            The exposures a package asks for.
        dither_config : `DitherConfig`, optional
            How a package dithers.
        minimum_altitude_deg : `float`, optional
            The lowest altitude a package may be imaged at.
        priority : `int`, optional
            A package's priority; higher is placed first. Defaults to 0.
        quality_weighting_enabled : `bool`, optional
            Let the target's quality advisory raise the package's
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
            resolved = self._target(target)
            package = ObservationPackage(
                id=str(uuid.uuid4()),
                name=resolved.id,
                target_id=resolved.id,
                exposure_requests=exposure_requests,
                dither_config=dither_config,
                minimum_altitude_deg=minimum_altitude_deg,
                priority=priority or 0,
                quality_weighting_enabled=bool(quality_weighting_enabled),
                notes=notes or "",
            )
            self._butler.put(package, "observation_package", {"id": package.id})
            return package
        if kind == "empty_session":
            session = ObservationSession(
                id=str(uuid.uuid4()),
                night_date=_night_date(night_id),
                status=SessionStatus.PLANNED,
                site_profile_id=site_profile.id,
                telescope_id=telescope.id,
                camera_id=camera_id,
            )
        else:
            session = self._schedule_session(
                requests, site_profile, telescope, camera_id, _night_date(night_id)
            )
        self._butler.put(session, "observation_session", {"session_id": session.id})
        return session

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
        from wayfindinglib.tasks.planning_tasks.quality_advisory_tasks import build_target_quality_advisory
        from wayfindinglib.tasks.planning_tasks.scheduling import schedule_session

        placements = []
        quality_advisories = {}
        for item in requests:
            request = QueueRequest.model_validate(item)
            package = self._package(request.package_id)
            placements.append((package, request.start_time_mode, request.requested_start_time))
            if package.quality_weighting_enabled:
                quality_advisories[package.target_id] = build_target_quality_advisory(
                    self._target(package.target_id)
                )
        return schedule_session(
            self._astrometrics,
            placements,
            site_profile,
            telescope,
            camera_id,
            night,
            quality_advisories=quality_advisories,
            planning_config=self._planning_config,
        )

    def edit_queue(
        self,
        session_id: str,
        add: list[QueueRequest | dict[str, Any]] | None = None,
        order: list[str] | None = None,
    ) -> ObservationSession:
        """Change an observing session's queue by hand.

        Added entries freeze a copy of their package (exposures,
        dithering, altitude limit and priority), the same way automatic
        placement does, so a session built by hand and one placed
        automatically run the same way. When both arguments are given,
        `order` is applied first and the new entries go at the end.

        Parameters
        ----------
        session_id : `str`
            The session to change.
        add : `list` [`QueueRequest`], optional
            Recorded packages to append, each with its start mode and any
            start and end worked out by hand.
        order : `list` [`str`], optional
            Every current entry id, in the new order.

        Returns
        -------
        session : `ObservationSession`
            The changed, recorded session.

        Raises
        ------
        InvalidArgumentError
            If neither `add` nor `order` is given, a request is malformed,
            or `order` does not name exactly the entries in the queue.
        """
        if add is None and order is None:
            raise InvalidArgumentError("Give add, order, or both.")
        session = self._session(session_id)
        if order is not None:
            entries_by_id = {entry.id: entry for entry in session.queue}
            if sorted(order) != sorted(entries_by_id):
                raise InvalidArgumentError(
                    "order must name exactly the entries currently in the queue.",
                    details={"order": list(order), "entries": sorted(entries_by_id)},
                )
            session.queue = [entries_by_id[entry_id] for entry_id in order]
        for item in add or []:
            try:
                request = QueueRequest.model_validate(item)
            except ValidationError as error:
                raise InvalidArgumentError(
                    "Each entry to add needs a package_id.", details={"error": str(error)}
                ) from error
            package = self._package(request.package_id)
            session.queue.append(
                QueuedObservationPackage(
                    id=str(uuid.uuid4()),
                    observation_package_id=package.id,
                    target_id=package.target_id,
                    exposure_requests=list(package.exposure_requests),
                    dither_config=package.dither_config,
                    minimum_altitude_deg=package.minimum_altitude_deg,
                    priority=package.priority,
                    start_time_mode=request.start_time_mode,
                    requested_start_time=request.requested_start_time,
                    computed_start_time=request.computed_start_time,
                    computed_end_time=request.computed_end_time,
                )
            )
        self._butler.put(session, "observation_session", {"session_id": session.id})
        return session

    def get_plan(self, session_id: str | None = None) -> ObservationSession | list[ObservationSessionSummary]:
        """Read one recorded observing session, or list them all.

        Parameters
        ----------
        session_id : `str`, optional
            The session to read. When omitted, every session is listed.

        Returns
        -------
        plan : `ObservationSession` or `list` [`ObservationSessionSummary`]
            The whole session, or one line per session, newest night
            first.
        """
        if session_id is not None:
            return self._session(session_id)
        summaries = [
            ObservationSessionSummary(
                id=session.id,
                status=session.status,
                night_date=session.night_date,
                entry_count=len(session.queue),
            )
            for session in self._butler.get_all("observation_session")
        ]
        summaries.sort(key=lambda summary: summary.night_date, reverse=True)
        return summaries

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

            # Under a job already (the MCP server runs this as one), join it
            # rather than listing the same work twice.
            with registered_job(
                enabled=register_job and get_current_job() is None,
                job_type="planning",
                target_id="deep_catalog",
                package_logger_name="wayfindinglib",
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
