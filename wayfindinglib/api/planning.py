"""Purpose: Observation Planning High-Level Interface.

Description: `ObservationPlanning` is the single entry point external
callers should use for package authoring, advisory computation, mosaic
generation, and session placement -- both the automated path
(`plan_observation_session`) and its peer manual path
(`create_empty_session`/`add_to_queue`/`reorder_queue`), which write the
identical queue structure (`Wayfinding_Library_Architecture.md` §2.3.2,
"Manual Parity"). Callers should never import `tasks.planning_tasks`
directly.
"""

import threading
import uuid
from datetime import UTC, date, datetime
from typing import Any

from astrometricslib import background_job
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.equipment_and_site.calibration import CalibrationAdvisory
from wayfindinglib.models.equipment_and_site.equipment import EquipmentConfiguration, Telescope
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.planning.mosaic import MosaicGridConfig
from wayfindinglib.models.planning.observation_package import (
    DitherConfig,
    ExposureRequest,
    FrameType,
    ObservationPackage,
)
from wayfindinglib.models.planning.planning_config import PlanningConfig
from wayfindinglib.models.planning.quality_advisory import TargetQualityAdvisory
from wayfindinglib.models.session.observation_session import (
    ObservationSession,
    QueuedObservationPackage,
    SessionStatus,
    StartTimeMode,
)

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "ObservationPlanning",
]


MAXIMUM_SOURCES = 300
"""Most stars one `ObservationPlanning.find_sources` answer lists."""


class ObservationPlanning:
    """Synchronous observation-planning API: packages, advisories, sessions."""

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        butler: DiskButler | None = None,
        planning_config: PlanningConfig | None = None,
        astrometrics: Any | None = None,
    ):
        """Initialize with a storage layer, configuration, and science handle.

        `astrometrics` is the science library handle shared with the rest of
        the `Wayfinder`. When omitted, one is built over the butler's
        configuration on first use.
        """
        self._butler = butler or DiskButler()
        self._astrometrics = astrometrics
        self._planning_config = planning_config or PlanningConfig()
        self.__sky_engine = None
        self.__observation_engine = None
        # Guards the lazy engine construction below. A web backend calls this
        # object from many threads at once, and the first request after startup
        # (e.g. the Planetarium mounting and firing several queries together)
        # would otherwise find an engine still None on every thread and build
        # one apiece -- each `Sky` loads its own full copy of the star catalog.
        self._engine_construction_lock = threading.Lock()

    @property
    def astrometrics(self) -> Any:
        """The shared `Astrometrics` handle, built on first use if not given.

        Returns
        -------
        astrometrics : `astrometricslib.Astrometrics`
            The science library handle.
        """
        if self._astrometrics is None:
            from astrometricslib import Astrometrics

            self._astrometrics = Astrometrics(self._butler.config)
        return self._astrometrics

    @property
    def _sky_engine(self) -> Any:
        """Lazily construct the sky-browsing engine this interface re-exposes.

        `Sky` (coordinate transforms, target resolution, catalog
        queries, and visibility) predates the three-function redesign
        and was never rebuilt on this high-level interface -- rather than
        duplicating its logic, this composes the existing engine and
        re-exposes it under `ObservationPlanning`, the layer it
        belongs to per the UI-surface mapping (`Wayfinding_Library
        _Architecture.md` Appendix, Planetarium Display -> Planning).
        `Sky` carries no hardware import, so this does not violate
        "Planning Is Hardware-Free".
        """
        if self.__sky_engine is None:
            with self._engine_construction_lock:
                if self.__sky_engine is None:
                    from wayfindinglib.sky import Sky

                    self.__sky_engine = Sky(config=self._butler.config, astrometrics=self.astrometrics)
        return self.__sky_engine

    @property
    def _observation_engine(self) -> Any:
        """Lazily construct the mosaic/sequence-planning engine, re-exposed.

        Same rationale as `_sky_engine`: `Observation`'s mosaic-panel
        and sequence-plan computation was never rebuilt on the new
        `ObservationPackage`/`ExposureRequest` model, so this composes
        the existing engine rather than duplicating it.
        """
        if self.__observation_engine is None:
            with self._engine_construction_lock:
                if self.__observation_engine is None:
                    from wayfindinglib.observation import Observation

                    self.__observation_engine = Observation(
                        config=self._butler.config, astrometrics=self.astrometrics
                    )
        return self.__observation_engine

    # -- Sky browsing (visibility, resolution, catalog) -------------------

    @property
    def site_latitude_deg(self) -> float:
        """The configured observatory latitude, in degrees."""
        return self._sky_engine.latitude

    @property
    def site_longitude_deg(self) -> float:
        """The configured observatory longitude, in degrees."""
        return self._sky_engine.longitude

    @property
    def site_elevation_m(self) -> float:
        """The configured observatory elevation, in meters."""
        return self._sky_engine.elevation

    @property
    def meridian_flip_delay_min(self) -> float:
        """The configured meridian-flip delay, in minutes."""
        return self._sky_engine.meridian_flip_delay_min

    def resolve_target_coordinates(self, target_name: str) -> Any:
        """Resolve a target/stellar object by name, via library or SIMBAD.

        Returns
        -------
        resolved : `Any`
            The resolved target or stellar object.
        """
        return self._sky_engine.resolve_target_coordinates(target_name)

    def lookup_coordinates(self, target_name: str) -> dict[str, Any]:
        """Find where a named object is, from the library or SIMBAD.

        Gives a short answer. `resolve_target_coordinates` returns the
        whole target record, frames and all, which is far more than a
        position needs.

        Parameters
        ----------
        target_name : `str`
            A target id, a common name, or a star name.

        Returns
        -------
        position : `dict` [`str`, `Any`]
            ``id``, ``name``, ``kind`` (``"target"`` or ``"star"``), and
            the position in degrees (``ra_deg``, ``dec_deg``) and as text.
            A name that cannot be resolved comes back under ``error``.
        """
        from astrometricslib import parse_coordinate_string

        try:
            found = self._sky_engine.resolve_target_coordinates(target_name)
        except Exception as error:
            return {"error": str(error)}
        if hasattr(found, "right_ascension"):
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
        include_targets: bool = True,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List the library's targets and stars near a point, capped.

        Replaces `get_sources` and `get_library_star_summaries` for a client
        that cannot take a long list. Stars come brightest first and the
        list is cut at ``limit``, with the total reported so the cut is
        never silent.

        Parameters
        ----------
        ra_deg : `float`
            Right ascension of the centre, in degrees.
        dec_deg : `float`
            Declination of the centre, in degrees.
        radius_deg : `float`
            Search radius, in degrees.
        magnitude_min : `float`, optional
            Keep stars at least this magnitude (numerically).
        magnitude_max : `float`, optional
            Keep stars no fainter than this magnitude.
        include_targets : `bool`, optional
            Also list the library targets in the circle.
        limit : `int`, optional
            Most stars to list, from 1 to 300. Defaults to 100.

        Returns
        -------
        answer : `dict` [`str`, `Any`]
            ``targets`` (id, name, position), ``stars`` (the summary rows),
            ``stars_total`` and whether the star list was cut.
        """
        from astrometricslib import parse_coordinate_string

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
        if include_targets:
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
        include_catalog: bool = False,
        include_stars: bool = True,
    ) -> list[Any]:
        """Return targets/stellar objects within a search radius of a point.

        Pass ``include_stars=False`` to get only targets. Loading every
        library star in full takes seconds on a large library; read them
        with `get_library_star_summaries` instead.

        Returns
        -------
        sources : `list`
            Targets and/or stellar objects within the search radius.
        """
        return self._sky_engine.get_sources(ra_deg, dec_deg, radius_deg, include_catalog, include_stars)

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

    # -- Deep-star catalog (Planetarium faint-star layer provisioning) ----

    def get_deep_catalog_status(self) -> dict[str, Any]:
        """Say how much of the downloaded deep-star catalog is installed.

        Returns
        -------
        status : `dict`
            ``installed``, ``complete``, ``star_count``, ``pixels_downloaded``,
            ``pixels_total``, ``healpix_level``, ``magnitude_limit`` and
            ``size_megabytes``. See
            `wayfindinglib.drivers.catalog.deep_star_store.get_deep_catalog_status`.
        """
        from wayfindinglib.drivers.catalog import deep_star_store

        return deep_star_store.get_deep_catalog_status(self._butler.config)

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

        return _build_deep_star_catalog(self._butler.config, **kwargs)

    @background_job("planning", grace_period_seconds=20.0)
    def estimate_deep_catalog_size(self, **kwargs: Any) -> dict[str, Any]:
        """Guess how big the finished deep-star catalog will be.

        A thin pass-through to
        `wayfindinglib.drivers.catalog.deep_star_catalog_builder.estimate_deep_catalog_size`.
        Nothing is saved by this call.

        Returns
        -------
        estimate : `dict`
            The size estimate. See
            `deep_star_catalog_builder.estimate_deep_catalog_size`.
        """
        from wayfindinglib.drivers.catalog.deep_star_catalog_builder import (
            estimate_deep_catalog_size as _estimate_deep_catalog_size,
        )

        return _estimate_deep_catalog_size(**kwargs)

    def get_imaged_field_centers(self) -> list[dict[str, Any]]:
        """List the sky positions of every target the library has imaged.

        Used to scope a deep-star catalog download to only the fields
        actually imaged, instead of the whole sky (see
        `build_deep_star_catalog`'s ``pixels`` argument, combined with
        `wayfindinglib.drivers.catalog.deep_star_catalog_builder.pixels_near_circles`).

        Returns
        -------
        field_centers : `list` [`dict`]
            One entry per unique imaged field, with keys
            ``right_ascension_deg``, ``declination_deg``, ``target_ids``
            and ``frames_examined``. See
            `astrometricslib`'s ``derive_field_centers`` for the exact shape.
        """
        from astrometricslib import derive_field_centers

        return derive_field_centers(self.astrometrics.targets.list())

    def get_meridian_status(self, ra_deg: float, dec_deg: float, time_input: Any) -> dict[str, Any]:
        """Return meridian proximity/flip status for one coordinate/time.

        Returns
        -------
        status : `dict`
            Meridian proximity and flip status fields.
        """
        return self._sky_engine.get_meridian_status(ra_deg, dec_deg, time_input)

    def get_visibility(self, objects: list[Any], time_input: Any = None) -> list[dict[str, Any]]:
        """Return altitude/azimuth/rise/set/transit for a list of objects.

        Parameters
        ----------
        objects : `list`
            Names or ids (looked up in the library, then SIMBAD), or
            dictionaries ``{"id", "ra_deg", "dec_deg"}``. Through the MCP
            server these are converted to sky objects automatically.
        time_input : `datetime`, `Time`, or `str`, optional
            When to compute for; default is now. Through the MCP server,
            an ISO 8601 string (an offset such as ``-06:00`` is honored;
            no offset means UTC).

        Returns
        -------
        visibility : `list` [`dict`]
            Altitude/azimuth/rise/set/transit fields for each object.
        """
        return self._sky_engine.get_visibility(objects, time_input)

    def get_visibility_over_time(
        self,
        objects: list[Any],
        start: str | None = None,
        end: str | None = None,
        step_minutes: float = 30.0,
        minimum_altitude_deg: float = 0.0,
        horizon_zones: list[dict[str, Any]] | None = None,
        timezone_offset_hours: float = 0.0,
        include_samples: bool = True,
    ) -> dict[str, Any]:
        """Plan a night: altitude, clearance, meridian, Sun and Moon by time.

        For each object gives its highest altitude, when it crosses the
        meridian and when a flip is due, when it is clear of the horizon
        limit, and when it is *usable* (clear while the Sun is below -18
        degrees). Also gives the span of astronomical night, when the Moon
        is up and how bright it is, and the Moon's distance from each
        object. Nothing is stored.

        For one moment, give only ``start`` and set ``end`` equal to it: one
        row per object. Every object also has ``at_start``: its altitude and
        azimuth, hour angle, whether a flip is due, and rise, set and
        transit as UTC times of day, all at ``start``.

        Parameters
        ----------
        objects : `list`
            Names or ids (library, then SIMBAD) or dictionaries
            ``{"id", "ra_deg", "dec_deg"}``. At most 30.
        start : `str`, optional
            Start of the span: ISO 8601 or ``"now"``. A time with no offset
            means UTC. Defaults to now.
        end : `str`, optional
            End of the span. Defaults to 12 hours after the start. Equal to
            ``start`` for a single moment.
        step_minutes : `float`, optional
            Time between rows, at least 1. A span may have at most 150
            rows. Defaults to 30.
        minimum_altitude_deg : `float`, optional
            The lowest altitude that counts as clear. Defaults to 0.
        horizon_zones : `list` [`dict`], optional
            Blocked parts of the sky, such as trees. Each has
            ``azimuth_start_deg``, ``azimuth_end_deg`` (a range may wrap
            past north) and ``min_clear_altitude_deg``.
        timezone_offset_hours : `float`, optional
            Write times in this UTC offset, such as -6 for Montana in
            summer. Defaults to 0 (UTC).
        include_samples : `bool`, optional
            Include the row-by-row table for each object. Defaults to `True`.

        Returns
        -------
        table : `dict`
            The site, window, Sun and Moon, and one entry per object, or
            ``{"error": ...}``.
        """
        from astrometricslib import parse_iso_time
        from wayfindinglib.tasks.planning_tasks import visibility_over_time

        try:
            start_epoch = (
                datetime.now(UTC).timestamp()
                if start is None or start.strip().lower() == "now"
                else parse_iso_time(start)
            )
            end_epoch = parse_iso_time(end)
        except ValueError as error:
            return {"error": f"start and end must be ISO 8601 times: {error}"}
        return visibility_over_time.build_visibility_over_time(
            self._sky_engine,
            objects,
            start_epoch,
            end_epoch,
            step_minutes,
            minimum_altitude_deg,
            horizon_zones,
            timezone_offset_hours,
            include_samples,
        )

    # -- Mosaic & sequence planning ---------------------

    def calculate_panels(
        self, center_ra: str, center_dec: str, rows: int, cols: int, overlap_percent: float
    ) -> list[dict[str, Any]]:
        """Calculate RA/DEC coordinate offsets for a multi-panel mosaic.

        Returns
        -------
        panels : `list` [`dict`]
            Each panel's computed RA/DEC coordinate offsets.
        """
        return self._observation_engine.calculate_panels(center_ra, center_dec, rows, cols, overlap_percent)

    def create_mosaic_targets(
        self,
        parent_target_id: str,
        grid_config: dict[str, Any],
        panels: list[dict[str, Any]],
        image_config: dict[str, Any],
        dither_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Generate target records for mosaic panels, linked to a parent.

        Returns
        -------
        targets : `list` [`dict`]
            The generated per-panel target records.
        """
        return self._observation_engine.create_mosaic_targets(
            parent_target_id, grid_config, panels, image_config, dither_config
        )

    def create_sequence_plan(self, target_name: str, plan_items: list[dict[str, Any]]) -> dict[str, Any]:
        """Generate a structured, dict-based sequence plan for a target.

        Returns
        -------
        plan : `dict`
            The generated sequence plan.
        """
        return self._observation_engine.create_sequence_plan(target_name, plan_items)

    # -- Package authoring ----------------------------------------------

    def create_observation_package(
        self,
        target_id: str,
        exposure_requests: list[ExposureRequest],
        dither_config: DitherConfig | None = None,
        minimum_altitude_deg: float | None = None,
        priority: int = 0,
        quality_weighting_enabled: bool = False,
        notes: str = "",
    ) -> ObservationPackage:
        """Create and record a reusable imaging request for a target.

        Validates that `target_id` resolves to an existing target before
        constructing the package (`Wayfinding_Library_Sequences.md` §1.1).

        Returns
        -------
        package : `ObservationPackage`
            The newly created and recorded package.

        Raises
        ------
        ValueError
            Raised if `target_id` does not resolve to an existing target.
        """
        if not self.astrometrics.targets.get(target_id):
            raise ValueError(f"Target {target_id} not found")

        package = ObservationPackage(
            id=str(uuid.uuid4()),
            name=target_id,
            target_id=target_id,
            exposure_requests=exposure_requests,
            dither_config=dither_config,
            minimum_altitude_deg=minimum_altitude_deg,
            priority=priority,
            quality_weighting_enabled=quality_weighting_enabled,
            notes=notes,
        )
        self._butler.put(package, "observation_package", {"id": package.id})
        return package

    # -- Advisory computation --------------------------------------------

    def get_target_quality_advisory(self, target_id: str) -> TargetQualityAdvisory:
        """Return the computed-on-demand quality advisory for a target.

        Returns
        -------
        advisory : `TargetQualityAdvisory`
            The computed quality advisory.
        """
        from wayfindinglib.tasks.planning_tasks.quality_advisory_tasks import build_target_quality_advisory

        return build_target_quality_advisory(self.astrometrics, target_id)

    def get_calibration_advisory(
        self, camera_id: str, frame_type: FrameType, exposure_sec: float | None = None, filter: Any = None
    ) -> CalibrationAdvisory:
        """Return the calibration inventory count for a requested entry.

        Returns
        -------
        advisory : `CalibrationAdvisory`
            The existing calibration inventory count for the entry.
        """
        from wayfindinglib.tasks.planning_tasks.calibration_advisory_tasks import build_calibration_advisory

        return build_calibration_advisory(self._butler, camera_id, frame_type, exposure_sec, filter)

    # -- Mosaic generation -------------------------------------------------

    def generate_mosaic_packages(
        self,
        parent_target_id: str,
        grid_config: MosaicGridConfig,
        exposure_requests: list[ExposureRequest],
        equipment: EquipmentConfiguration,
        dither_config: DitherConfig | None = None,
    ) -> list[ObservationPackage]:
        """Expand one multi-panel imaging request into sibling packages.

        Returns
        -------
        packages : `list` [`ObservationPackage`]
            The generated, recorded sibling packages, one per panel.
        """
        from wayfindinglib.tasks.planning_tasks.mosaic_tasks import generate_mosaic_packages

        packages = generate_mosaic_packages(
            self.astrometrics, parent_target_id, grid_config, exposure_requests, equipment, dither_config
        )
        for package in packages:
            self._butler.put(package, "observation_package", {"id": package.id})
        return packages

    # -- Automated placement -------------------------------------------------

    def plan_observation_session(
        self,
        requests: list[tuple[ObservationPackage, StartTimeMode, datetime | None]],
        site_profile: SiteProfile,
        telescope: Telescope,
        camera_id: str,
        night_date: date,
    ) -> ObservationSession:
        """Resolve the night window and place every requested package.

        Runs entirely against the arguments it is handed -- no device is
        contacted, so this is callable with nothing connected
        (`Wayfinding_Library_Architecture.md` §2.3.4, "Planning Is
        Hardware-Free").

        Returns
        -------
        session : `ObservationSession`
            The assembled, recorded session with its placed queue and
            unplaced-package diagnostics.
        """
        from wayfindinglib.tasks.planning_tasks.scheduling import plan_observation_session

        quality_advisories = {}
        for package, _mode, _requested in requests:
            if package.quality_weighting_enabled:
                quality_advisories[package.target_id] = self.get_target_quality_advisory(package.target_id)

        session = plan_observation_session(
            self.astrometrics,
            requests,
            site_profile,
            telescope,
            camera_id,
            night_date,
            quality_advisories=quality_advisories,
            planning_config=self._planning_config,
        )
        self._butler.put(session, "observation_session", {"session_id": session.id})
        return session

    # -- Manual queue authoring (peer path) -------------------------------

    def create_empty_session(
        self, site_profile: SiteProfile, telescope: Telescope, camera_id: str, night_date: date
    ) -> ObservationSession:
        """Create and record an empty session for hand-authored entries.

        Returns
        -------
        session : `ObservationSession`
            The newly created, recorded, empty session.
        """
        session = ObservationSession(
            id=str(uuid.uuid4()),
            night_date=night_date,
            status=SessionStatus.PLANNED,
            site_profile_id=site_profile.id,
            telescope_id=telescope.id,
            camera_id=camera_id,
        )
        self._butler.put(session, "observation_session", {"session_id": session.id})
        return session

    def add_to_queue(
        self,
        session_id: str,
        observation_package: ObservationPackage,
        start_time_mode: StartTimeMode,
        requested_start_time: datetime | None = None,
        computed_start_time: datetime | None = None,
        computed_end_time: datetime | None = None,
    ) -> ObservationSession:
        """Append one hand-authored entry to a session's queue.

        Writes the identical `QueuedObservationPackage` structure the
        automated placement path produces -- freezing a self-contained
        snapshot of the package -- so a hand-built and an automatically
        generated session are interchangeable inputs to Observation
        Execution (`Wayfinding_Library_Architecture.md` §2.3.4, "Manual
        Parity").

        Returns
        -------
        session : `ObservationSession`
            The session with the new entry appended and recorded.

        Raises
        ------
        ValueError
            Raised if `session_id` does not resolve to an existing session.
        """
        session = self._butler.get("observation_session", {"session_id": session_id})
        if session is None:
            raise ValueError(f"Session {session_id} not found")

        entry = QueuedObservationPackage(
            id=str(uuid.uuid4()),
            observation_package_id=observation_package.id,
            target_id=observation_package.target_id,
            exposure_requests=list(observation_package.exposure_requests),
            dither_config=observation_package.dither_config,
            minimum_altitude_deg=observation_package.minimum_altitude_deg,
            priority=observation_package.priority,
            start_time_mode=start_time_mode,
            requested_start_time=requested_start_time,
            computed_start_time=computed_start_time,
            computed_end_time=computed_end_time,
        )
        session.queue.append(entry)
        self._butler.put(session, "observation_session", {"session_id": session.id})
        return session

    def reorder_queue(self, session_id: str, entry_ids: list[str]) -> ObservationSession:
        """Reorder a session's queue to match `entry_ids`.

        Returns
        -------
        session : `ObservationSession`
            The session with its queue reordered and recorded.

        Raises
        ------
        ValueError
            Raised if `session_id` does not resolve, or if `entry_ids`
            does not name exactly the entries currently in the queue.
        """
        session = self._butler.get("observation_session", {"session_id": session_id})
        if session is None:
            raise ValueError(f"Session {session_id} not found")

        entries_by_id = {e.id: e for e in session.queue}
        if set(entry_ids) != set(entries_by_id):
            raise ValueError("entry_ids must name exactly the entries currently in the queue")

        session.queue = [entries_by_id[entry_id] for entry_id in entry_ids]
        self._butler.put(session, "observation_session", {"session_id": session.id})
        return session
