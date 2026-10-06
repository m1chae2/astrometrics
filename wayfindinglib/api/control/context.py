"""Purpose: The state that `ObservatoryControl` and its children share.

Description: `ControlContext` holds what every part of `control` needs:
the configuration, the `DiskButler` that stores records, the correction
settings, the safety monitor, the shared `Astrometrics` handle, the log
database, and the hardware drivers. The drivers are built on first use,
so building a context never touches hardware.

`ObservatoryControl` builds one context and hands it to each of its seven
children. The children and the task functions under
`wayfindinglib.tasks.control_tasks` read the context; none of them holds
a reference back to `control`. Setting a driver on `control` (as tests
and the backend do) sets it here, so every child sees the change.

The context also answers the small lookups several children need: the
active equipment, the saved guider and focus models, the delegation
policy, and the observer location.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING, Any

from wayfindinglib.data_access.delegation_policy_reader import get_delegation_policy
from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.tasks.control_tasks.safety_monitor import SafetyMonitor

if TYPE_CHECKING:
    from astrometricslib import AppConfiguration, Astrometrics, LoggerInterface
    from wayfindinglib.drivers.indi.diagnostics import IndiDiagnostics
    from wayfindinglib.drivers.protocols.base_protocol_driver import ProtocolDriver
    from wayfindinglib.drivers.protocols.camera_driver import CameraDriver
    from wayfindinglib.drivers.protocols.enclosure_driver import EnclosureDriver
    from wayfindinglib.drivers.protocols.filter_wheel_driver import FilterWheelDriver
    from wayfindinglib.drivers.protocols.focuser_driver import FocuserDriver
    from wayfindinglib.drivers.protocols.mount_driver import MountDriver
    from wayfindinglib.drivers.protocols.remote_transfer_driver import RemoteTransferDriver
    from wayfindinglib.drivers.protocols.switch_driver import SwitchDriver
    from wayfindinglib.drivers.protocols.weather_driver import WeatherDriver
    from wayfindinglib.models.equipment_and_site.enclosure import Enclosure
    from wayfindinglib.models.equipment_and_site.equipment import Camera, GuideScope, Telescope
    from wayfindinglib.models.equipment_and_site.focus_model import FocusModel
    from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
    from wayfindinglib.models.policy.delegation import DelegationPolicy
    from wayfindinglib.models.session.ekos_session import EkosSessionContext
    from wayfindinglib.models.session.guiding_run import GuidingRunSummary
    from wayfindinglib.models.session.telemetry import GuidingSpectrumAnalysis

__all__ = ["ControlChild", "ControlContext"]

logger = logging.getLogger(__name__)

DEFAULT_LATITUDE_DEG = 45.0
"""Latitude used by the pointing fits when no observer location is known."""


class ControlContext:
    """Configuration, storage and drivers shared by `control` and its children.

    Parameters
    ----------
    config : `AppConfiguration`
        The application configuration.
    driver : `Any`, optional
        The INDI session (`IndiInterface` or `SimulatorIndiInterface`).
        Built on first use when omitted.
    butler : `DiskButler`, optional
        Stores the wayfinding records. Built over `config` when omitted.
    correction_config : `CorrectionConfig`, optional
        Limits and gains for the computed corrections.
    astrometrics : `Astrometrics`, optional
        The science library handle shared with the rest of the
        `Wayfinder`. Built over `config` on first use when omitted.
    """

    def __init__(
        self,
        config: AppConfiguration,
        driver: Any | None = None,
        butler: DiskButler | None = None,
        correction_config: CorrectionConfig | None = None,
        astrometrics: Astrometrics | None = None,
    ) -> None:
        """Store the shared inputs. No driver is built yet."""
        self.config = config
        self.butler = butler or DiskButler(app_config=config)
        self.correction_config = correction_config or CorrectionConfig()
        self.safety_monitor = SafetyMonitor()
        self._astrometrics = astrometrics
        self._logger_interface: LoggerInterface | None = None
        self._driver = driver
        self._mount_driver: MountDriver | None = None
        self._focuser_driver: FocuserDriver | None = None
        self._filter_wheel_driver: FilterWheelDriver | None = None
        self._camera_driver: CameraDriver | None = None
        self._guide_camera_driver: CameraDriver | None = None
        self._enclosure_driver: EnclosureDriver | None = None
        self._switch_driver: SwitchDriver | None = None
        self._weather_driver: WeatherDriver | None = None
        self._remote_transfer_driver: RemoteTransferDriver | None = None
        self._indi_diagnostics: IndiDiagnostics | None = None
        self.motion_stop = threading.Event()
        """Set by `control.mount.abort_motion` to stop a centering loop."""

    # -- Shared handles --------------------------------------------------

    @property
    def astrometrics(self) -> Astrometrics:
        """The shared `Astrometrics` handle, built on first use if not given.

        Returns
        -------
        astrometrics : `astrometricslib.Astrometrics`
            The science library handle.
        """
        if self._astrometrics is None:
            from astrometricslib import Astrometrics

            self._astrometrics = Astrometrics(self.config)
        return self._astrometrics

    @property
    def logger_interface(self) -> LoggerInterface:
        """The log database that holds guiding samples and plate solves.

        Built on first use, so a test configuration that has no log
        database path is never asked for one unless a method needs it.

        Returns
        -------
        logger_interface : `astrometricslib.LoggerInterface`
            The shared log-database interface.
        """
        if self._logger_interface is None:
            from astrometricslib import LoggerInterface

            self._logger_interface = LoggerInterface(self.config.get_logs_db_path())
        return self._logger_interface

    @logger_interface.setter
    def logger_interface(self, logger_interface: LoggerInterface) -> None:
        """Set the log database (tests use this to inject one)."""
        self._logger_interface = logger_interface

    # -- Drivers ---------------------------------------------------------

    @property
    def driver(self) -> Any:
        """The INDI session that every INDI device driver wraps.

        Returns
        -------
        driver : `IndiInterface` or `SimulatorIndiInterface`
            The simulator when ``ASTROMETRICS_TESTING`` is set, otherwise
            the real INDI client.
        """
        if self._driver is None:
            if os.getenv("ASTROMETRICS_TESTING"):
                from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface

                self._driver = SimulatorIndiInterface(config=self.config)
            else:
                from wayfindinglib.drivers.indi_interface import IndiInterface

                self._driver = IndiInterface(config=self.config)
        return self._driver

    @driver.setter
    def driver(self, driver: Any) -> None:
        """Set the INDI session."""
        self._driver = driver

    @property
    def mount_driver(self) -> MountDriver:
        """The mount driver for the active telescope's `mount_protocol`.

        Returns
        -------
        mount_driver : `MountDriver`
            The active mount driver.
        """
        if self._mount_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_mount_driver_registry

            telescope = self.active_telescope()
            protocol = telescope.mount_protocol if telescope else "indi"
            self._mount_driver = self._build_protocol_driver(build_mount_driver_registry(), protocol)
        return self._mount_driver

    @mount_driver.setter
    def mount_driver(self, mount_driver: MountDriver) -> None:
        """Set the mount driver."""
        self._mount_driver = mount_driver

    @property
    def focuser_driver(self) -> FocuserDriver:
        """The focuser driver for the active telescope's `focuser_protocol`.

        Returns
        -------
        focuser_driver : `FocuserDriver`
            The active focuser driver.
        """
        if self._focuser_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_focuser_driver_registry

            telescope = self.active_telescope()
            protocol = telescope.focuser_protocol if telescope else "indi"
            self._focuser_driver = self._build_protocol_driver(build_focuser_driver_registry(), protocol)
        return self._focuser_driver

    @focuser_driver.setter
    def focuser_driver(self, focuser_driver: FocuserDriver) -> None:
        """Set the focuser driver."""
        self._focuser_driver = focuser_driver

    @property
    def filter_wheel_driver(self) -> FilterWheelDriver:
        """The filter wheel driver for the active telescope's protocol.

        Returns
        -------
        filter_wheel_driver : `FilterWheelDriver`
            The active filter wheel driver.
        """
        if self._filter_wheel_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_filter_wheel_driver_registry

            telescope = self.active_telescope()
            protocol = telescope.filter_wheel_protocol if telescope else "indi"
            self._filter_wheel_driver = self._build_protocol_driver(
                build_filter_wheel_driver_registry(), protocol
            )
        return self._filter_wheel_driver

    @filter_wheel_driver.setter
    def filter_wheel_driver(self, filter_wheel_driver: FilterWheelDriver) -> None:
        """Set the filter wheel driver."""
        self._filter_wheel_driver = filter_wheel_driver

    @property
    def camera_driver(self) -> CameraDriver:
        """The main camera driver for the active camera's `protocol`.

        Returns
        -------
        camera_driver : `CameraDriver`
            The active main camera driver (``role="main"``).
        """
        if self._camera_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_camera_driver_registry

            camera = self.active_camera()
            protocol = camera.protocol if camera else "indi"
            self._camera_driver = self._build_protocol_driver(
                build_camera_driver_registry(), protocol, role="main"
            )
        return self._camera_driver

    @camera_driver.setter
    def camera_driver(self, camera_driver: CameraDriver) -> None:
        """Set the main camera driver."""
        self._camera_driver = camera_driver

    @property
    def guide_camera_driver(self) -> CameraDriver:
        """The guide camera driver.

        Always INDI today: the guide camera has no catalog entry that
        could name another protocol.

        Returns
        -------
        guide_camera_driver : `CameraDriver`
            The active guide camera driver (``role="guide"``).
        """
        if self._guide_camera_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_camera_driver_registry

            self._guide_camera_driver = self._build_protocol_driver(
                build_camera_driver_registry(), "indi", role="guide"
            )
        return self._guide_camera_driver

    @guide_camera_driver.setter
    def guide_camera_driver(self, guide_camera_driver: CameraDriver) -> None:
        """Set the guide camera driver."""
        self._guide_camera_driver = guide_camera_driver

    @property
    def enclosure_driver(self) -> EnclosureDriver:
        """The enclosure driver for the configured enclosure's `protocol`.

        Returns
        -------
        enclosure_driver : `EnclosureDriver`
            The active enclosure driver.
        """
        if self._enclosure_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_enclosure_driver_registry

            enclosure = self.active_enclosure()
            protocol = enclosure.protocol if enclosure else "indi"
            self._enclosure_driver = self._build_protocol_driver(build_enclosure_driver_registry(), protocol)
        return self._enclosure_driver

    @enclosure_driver.setter
    def enclosure_driver(self, enclosure_driver: EnclosureDriver) -> None:
        """Set the enclosure driver."""
        self._enclosure_driver = enclosure_driver

    @property
    def switch_driver(self) -> SwitchDriver:
        """The power switch driver. Always INDI today.

        Returns
        -------
        switch_driver : `SwitchDriver`
            The active power switch driver.
        """
        if self._switch_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_switch_driver_registry

            self._switch_driver = self._build_protocol_driver(build_switch_driver_registry(), "indi")
        return self._switch_driver

    @switch_driver.setter
    def switch_driver(self, switch_driver: SwitchDriver) -> None:
        """Set the power switch driver."""
        self._switch_driver = switch_driver

    @property
    def weather_driver(self) -> WeatherDriver:
        """The weather sensor driver. Always INDI today.

        Returns
        -------
        weather_driver : `WeatherDriver`
            The active weather sensor driver.
        """
        if self._weather_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_weather_driver_registry

            self._weather_driver = self._build_protocol_driver(build_weather_driver_registry(), "indi")
        return self._weather_driver

    @weather_driver.setter
    def weather_driver(self, weather_driver: WeatherDriver) -> None:
        """Set the weather sensor driver."""
        self._weather_driver = weather_driver

    @property
    def remote_transfer_driver(self) -> RemoteTransferDriver:
        """The driver that copies files from the observatory computer.

        Chosen by `AppConfiguration.get_remote_transfer_driver_name`
        (``"stellarmate"`` by default). File transfer is not part of INDI,
        so this choice is separate from the device protocols.

        Returns
        -------
        remote_transfer_driver : `RemoteTransferDriver`
            The active remote transfer driver.

        Raises
        ------
        NotImplementedError
            If the configured driver has no construction code yet. Only
            ``"stellarmate"`` has it today.
        """
        if self._remote_transfer_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_remote_transfer_driver_registry

            driver_name = self.config.get_remote_transfer_driver_name()
            driver_class = build_remote_transfer_driver_registry()[driver_name]
            if driver_name != "stellarmate":
                raise NotImplementedError(
                    f"No constructor wiring yet for remote transfer driver '{driver_name}'"
                )
            self._remote_transfer_driver = driver_class(
                host_alias=self.config.get_telescope_hostname(),
                remote_pictures_path=self.config.get_remote_pictures_path(),
            )
        return self._remote_transfer_driver

    @remote_transfer_driver.setter
    def remote_transfer_driver(self, remote_transfer_driver: RemoteTransferDriver) -> None:
        """Set the remote transfer driver."""
        self._remote_transfer_driver = remote_transfer_driver

    @property
    def indi_diagnostics(self) -> IndiDiagnostics | None:
        """Raw INDI device and property inspection, or `None` off INDI.

        Returns
        -------
        indi_diagnostics : `IndiDiagnostics` or `None`
            Inspection over the shared session, or `None` when the active
            mount protocol is not ``"indi"``.
        """
        telescope = self.active_telescope()
        protocol = telescope.mount_protocol if telescope else "indi"
        if protocol != "indi":
            return None
        if self._indi_diagnostics is None:
            from wayfindinglib.drivers.indi.diagnostics import IndiDiagnostics

            self._indi_diagnostics = IndiDiagnostics(session=self.driver)
        return self._indi_diagnostics

    def _build_protocol_driver(
        self, registry: dict[str, type], protocol: str, **extra_kwargs: Any
    ) -> ProtocolDriver:
        """Build the driver for `protocol` from `registry`.

        Only ``"indi"`` has construction code today: it wraps the shared
        `driver` session. Another protocol adds its own branch here,
        because it needs different arguments (for example a network
        address instead of a session).

        Parameters
        ----------
        registry : `dict` [`str`, `type`]
            Protocol name to driver class.
        protocol : `str`
            The protocol to build.
        **extra_kwargs : `Any`
            Device-specific constructor arguments, such as a camera's
            ``role``.

        Returns
        -------
        driver : `ProtocolDriver`
            The new driver.

        Raises
        ------
        NotImplementedError
            If `protocol` has no construction code yet.
        """
        driver_class = registry[protocol]
        if protocol == "indi":
            return driver_class(session=self.driver, **extra_kwargs)
        raise NotImplementedError(f"No constructor wiring yet for protocol '{protocol}'")

    # -- Active equipment and saved models -------------------------------

    def active_telescope(self) -> Telescope | None:
        """Return the active telescope, or `None` if none is selected.

        Returns
        -------
        telescope : `Telescope` or `None`
            The active telescope.
        """
        return get_equipment_catalog(self.config).active_telescope()

    def active_camera(self) -> Camera | None:
        """Return the active main camera, or `None` if none is selected.

        Returns
        -------
        camera : `Camera` or `None`
            The active main camera.
        """
        return get_equipment_catalog(self.config).active_camera()

    def active_guide_scope(self) -> GuideScope | None:
        """Return the active guide scope, or `None` if none is configured.

        Returns
        -------
        guide_scope : `GuideScope` or `None`
            The active guide scope.
        """
        return get_equipment_catalog(self.config).active_guide_scope()

    def active_guide_camera(self) -> Camera | None:
        """Return the active guide camera, or `None` if the main camera guides.

        Returns
        -------
        guide_camera : `Camera` or `None`
            The camera that sees the guide star.
        """
        return get_equipment_catalog(self.config).active_guide_camera()

    def active_enclosure(self) -> Enclosure | None:
        """Return the configured enclosure, or `None` if none is recorded.

        Returns
        -------
        enclosure : `Enclosure` or `None`
            The first recorded enclosure.
        """
        enclosures = self.butler.get_all("enclosure")
        return enclosures[0] if enclosures else None

    def guider_plate_scale_arcsec_per_px(self) -> float | None:
        """Return the guide camera's plate scale.

        Uses the guide scope's focal length and the guide camera's pixel
        size when those are configured, and the main telescope and camera
        otherwise.

        Returns
        -------
        plate_scale : `float` or `None`
            Arcseconds per pixel, or `None` if no telescope and camera are
            both active.
        """
        telescope = self.active_telescope()
        camera = self.active_camera()
        if telescope is None or camera is None:
            return None
        from wayfindinglib.models.equipment_and_site.equipment import EquipmentConfiguration

        configuration = EquipmentConfiguration(telescope=telescope, camera=camera)
        return configuration.guider_plate_scale_arcsec_per_px(
            self.active_guide_scope(), self.active_guide_camera()
        )

    def active_guider_calibration(self) -> GuiderCalibration | None:
        """Return the saved guider calibration for the active equipment.

        Returns
        -------
        calibration : `GuiderCalibration` or `None`
            The calibration for the active telescope and camera, or `None`.
        """
        telescope = self.active_telescope()
        camera = self.active_camera()
        if telescope is None or camera is None:
            return None
        for calibration in self.butler.get_all("guider_calibration"):
            if calibration.telescope_id == telescope.id and calibration.camera_id == camera.id:
                return calibration
        return None

    def active_focus_model(self) -> FocusModel | None:
        """Return the saved focus model for the active equipment.

        Returns
        -------
        focus_model : `FocusModel` or `None`
            The focus model for the active telescope and camera, or `None`.
        """
        telescope = self.active_telescope()
        camera = self.active_camera()
        if telescope is None or camera is None:
            return None
        for focus_model in self.butler.get_all("focus_model"):
            if focus_model.telescope_id == telescope.id and focus_model.camera_id == camera.id:
                return focus_model
        return None

    def active_guiding_spectrum_analysis(self) -> GuidingSpectrumAnalysis | None:
        """Return the saved periodic error model of the active mount.

        Returns
        -------
        analysis : `GuidingSpectrumAnalysis` or `None`
            The latest saved analysis for the active telescope, or `None`.
        """
        telescope = self.active_telescope()
        if telescope is None:
            return None
        return self.butler.get("guiding_spectrum_analysis", {"id": telescope.id})

    def delegation_policy(self) -> DelegationPolicy:
        """Return the saved delegation policy.

        Returns
        -------
        policy : `DelegationPolicy`
            Which capabilities this app may command.
        """
        return get_delegation_policy(self.butler)

    def observer_location(self) -> dict[str, float] | None:
        """Return the observatory's latitude, longitude and elevation.

        Asks the mount first, then the configuration.

        Returns
        -------
        location : `dict` [`str`, `float`] or `None`
            ``latitude``, ``longitude`` and ``elevation``, or `None` if
            neither source gives one.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.observer_location(self)

    def observer_latitude_deg(self) -> float:
        """Return the observer latitude, or 45 degrees when it is unknown.

        Returns
        -------
        latitude_deg : `float`
            Latitude in degrees.
        """
        location = self.observer_location()
        return location["latitude"] if location else DEFAULT_LATITUDE_DEG

    def ekos_log_directory(self) -> str:
        """Return the local folder that holds downloaded Ekos logs.

        Returns
        -------
        directory : `str`
            ``ekos_logs`` inside the wayfinding library's data folder.
        """
        from wayfindinglib.drivers import local_database

        return str(local_database._wayfinding_library_path(self.config) / "ekos_logs")

    # -- Record writes shared by several children and tasks --------------

    def save_guiding_spectrum_analysis(self, analysis: GuidingSpectrumAnalysis) -> None:
        """Save `analysis` as the active mount's periodic error model.

        Periodic error and backlash belong to the mount, so the record is
        keyed by the active telescope and replaced on every refit.

        Parameters
        ----------
        analysis : `GuidingSpectrumAnalysis`
            The fitted model.
        """
        telescope = self.active_telescope()
        analysis_id = telescope.id if telescope else "default"
        telescope_id = telescope.id if telescope else ""
        saved = analysis.model_copy(update={"id": analysis_id, "telescope_id": telescope_id})
        self.butler.put(saved, "guiding_spectrum_analysis", {"id": analysis_id})

    def save_ekos_session_context(self, context: EkosSessionContext) -> None:
        """Save one Ekos session record, replacing an earlier read of it.

        Parameters
        ----------
        context : `EkosSessionContext`
            The session record, keyed by its analyze file's name.
        """
        self.butler.put(context, "ekos_session_context", {"id": context.id})

    def save_guiding_run(self, run: GuidingRunSummary) -> None:
        """Save one guiding run, replacing an earlier read of the same run.

        Parameters
        ----------
        run : `GuidingRunSummary`
            The run, keyed by its guide log's name and its place in it.
        """
        self.butler.put(run, "guiding_run", {"id": run.id})


class ControlChild:
    """Base of the `control` children: each holds the shared context.

    Parameters
    ----------
    context : `ControlContext`
        The state shared by `control` and all its children.
    """

    def __init__(self, context: ControlContext) -> None:
        """Keep the shared context. A child holds no reference to `control`."""
        self._context = context
