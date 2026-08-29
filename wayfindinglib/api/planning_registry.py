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

import uuid
from datetime import date, datetime
from typing import Any

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


class ObservationPlanning:
    """Synchronous observation-planning API: packages, advisories, sessions."""

    def __init__(self, butler: DiskButler | None = None, planning_config: PlanningConfig | None = None):  # ruff: ignore[missing-return-type-special-method]
        """Initialize with a storage layer and configuration."""
        self._butler = butler or DiskButler()
        self._planning_config = planning_config or PlanningConfig()
        self.__sky_engine = None
        self.__observation_engine = None

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
            from wayfindinglib.sky import Sky

            self.__sky_engine = Sky(config=self._butler.config)
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
            from wayfindinglib.observation import Observation

            self.__observation_engine = Observation(config=self._butler.config)
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

    def get_sources(
        self, ra_deg: float, dec_deg: float, radius_deg: float, include_catalog: bool = False
    ) -> list[Any]:
        """Return targets/stellar objects within a search radius of a point.

        Returns
        -------
        sources : `list`
            Targets and/or stellar objects within the search radius.
        """
        return self._sky_engine.get_sources(ra_deg, dec_deg, radius_deg, include_catalog)

    def get_online_catalog_sources(
        self, ra_deg: float, dec_deg: float, radius_deg: float, enabled_driver_names: list[str]
    ) -> list[tuple[str, Any]]:
        """Return matching stellar objects from enabled catalog drivers.

        Returns
        -------
        sources : `list` [`tuple` [`str`, `Any`]]
            Each match paired with the driver name that found it.
        """
        return self._sky_engine.get_online_catalog_sources(ra_deg, dec_deg, radius_deg, enabled_driver_names)

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

        Returns
        -------
        visibility : `list` [`dict`]
            Altitude/azimuth/rise/set/transit fields for each object.
        """
        return self._sky_engine.get_visibility(objects, time_input)

    # -- Mosaic & sequence planning (legacy dict-based) ---------------------

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
        astrometrics: Any,
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
        if not astrometrics.targets.get(target_id):
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

    def get_target_quality_advisory(self, astrometrics: Any, target_id: str) -> TargetQualityAdvisory:
        """Return the computed-on-demand quality advisory for a target.

        Returns
        -------
        advisory : `TargetQualityAdvisory`
            The computed quality advisory.
        """
        from wayfindinglib.tasks.planning_tasks.quality_advisory_tasks import build_target_quality_advisory

        return build_target_quality_advisory(astrometrics, target_id)

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
        astrometrics: Any,
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
            astrometrics, parent_target_id, grid_config, exposure_requests, equipment, dither_config
        )
        for package in packages:
            self._butler.put(package, "observation_package", {"id": package.id})
        return packages

    # -- Automated placement -------------------------------------------------

    def plan_observation_session(
        self,
        astrometrics: Any,
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
                quality_advisories[package.target_id] = self.get_target_quality_advisory(
                    astrometrics, package.target_id
                )

        session = plan_observation_session(
            astrometrics,
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
