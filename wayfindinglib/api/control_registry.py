"""Purpose: Observatory Control High-Level Interface.

Description: `ObservatoryControl` is the single entry point external
callers should use for direct hardware operation, envelope-respecting
corrections, calibration, safety, enclosure/safe-state, thermal, device
state, equipment selection, capability promotion, and remote transfer
(`Wayfinding_Library_Architecture.md` §2.5.1). Callers should never
import `tasks.control_tasks` directly.

Composes the driver layer the same way the deprecated
`ObservatoryManager` did (a lazily-initialized `.driver`), so
`tasks.control_tasks.hardware_operations` -- relocated verbatim from
`observatorylib` -- works unmodified against this high-level interface as its
"manager".

Per §2.5.9's "Corrections Are Pure": `compute_*_correction` methods here
resolve the calibration/config inputs and forward to the pure task
functions, but issue nothing themselves. Sending a computed correction
through the driver is a separate, delegation-gated step this
high-level interface does not perform -- that orchestration belongs
to Observation Execution (M5) and the capability's current
`DelegationState`.
"""

import os
from datetime import UTC, datetime
from typing import Any

from wayfindinglib.data_access.delegation_policy_reader import get_delegation_policy
from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog
from wayfindinglib.data_access.safety_policy_reader import (
    get_safety_rule_set,
    save_safety_rule_set,
)
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.equipment_and_site.enclosure import Enclosure
from wayfindinglib.models.equipment_and_site.equipment import Camera, CoolingPolicy, Telescope
from wayfindinglib.models.equipment_and_site.focus_model import FocusModel
from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.policy.commissioning import CommissioningRun
from wayfindinglib.models.policy.delegation import DelegationPolicy, DelegationState, ObservatoryCapability
from wayfindinglib.models.policy.device_state import DeviceRole, DeviceState
from wayfindinglib.models.policy.safety import SafetyAssessment, SafetyRuleSet
from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.models.session.correction_result import (
    FocusCorrection,
    FocusCurvePoint,
    GuidingCorrection,
    PointingCorrection,
)
from wayfindinglib.models.session.safe_state import SafeStateOutcome
from wayfindinglib.models.session.telemetry import GuidingSpectrumAnalysis, MountPointingModel
from wayfindinglib.tasks.control_tasks.calibration_routines import (
    BacklashCalibrationSteps,
    GuiderCalibrationSteps,
)
from wayfindinglib.tasks.control_tasks.capability_promotion import DivergenceEvidenceSummary
from wayfindinglib.tasks.control_tasks.safe_state import SafeStateSteps
from wayfindinglib.tasks.control_tasks.safety_monitor import SafetyMonitor, SensorReadings

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "ObservatoryControl",
]


class ObservatoryControl:
    """Synchronous observatory-control API: hardware, safety, policy."""

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        config: Any | None = None,
        driver: Any | None = None,
        butler: DiskButler | None = None,
        correction_config: CorrectionConfig | None = None,
    ):
        """Initialize the interface with config, a driver, and recording."""
        if config is None:
            from astrometricslib import get_configuration

            config = get_configuration()
        self._config = config
        self.__driver = driver
        self.__mount_driver = None
        self.__focuser_driver = None
        self.__filter_wheel_driver = None
        self.__camera_driver = None
        self.__guide_camera_driver = None
        self.__enclosure_driver = None
        self.__remote_transfer_driver = None
        self.__indi_diagnostics = None
        self._guiding_service = None
        self._sync_service = None
        self.__logger_interface = None
        self._butler = butler or DiskButler(app_config=config)
        self._correction_config = correction_config or CorrectionConfig()
        self._safety_monitor = SafetyMonitor()

    @property
    def driver(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Lazily initialize or return the INDI driver interface.

        Returns
        -------
        driver : `IndiInterface` or `SimulatorIndiInterface`
            The active hardware driver, real or simulated depending on
            `ASTROMETRICS_TESTING`.
        """
        if self.__driver is None:
            if os.getenv("ASTROMETRICS_TESTING"):
                from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface

                self.__driver = SimulatorIndiInterface(config=self._config)
            else:
                from wayfindinglib.drivers.indi_interface import IndiInterface

                self.__driver = IndiInterface(config=self._config)
        return self.__driver

    @driver.setter
    def driver(self, driver_interface) -> None:  # ruff: ignore[missing-type-function-argument]
        """Set the active INDI hardware driver interface."""
        self.__driver = driver_interface

    @property
    def _logger_interface(self):  # ruff: ignore[missing-return-type-private-function]
        """Lazily build or return the shared `LoggerInterface`.

        Cross-library dependency on `astrometricslib.LoggerInterface`
        (already established precedent -- `remote_transfer_tasks.py`
        imports it the same way), used by the guiding/alignment log
        ingestion pipelines (§6a) to read/record samples and attempts.
        Lazy, like `.driver`/`._butler`, so a dummy/sentinel `config`
        (as some tests use) doesn't eagerly resolve a real log database
        path.

        Returns
        -------
        logger_interface : `astrometricslib.LoggerInterface`
            The shared log-database interface.
        """
        if self.__logger_interface is None:
            from astrometricslib import LoggerInterface

            self.__logger_interface = LoggerInterface(self._config.get_logs_db_path())
        return self.__logger_interface

    @_logger_interface.setter
    def _logger_interface(self, logger_interface) -> None:  # ruff: ignore[missing-type-function-argument]
        """Set the shared `LoggerInterface` (test injection)."""
        self.__logger_interface = logger_interface

    @property
    def mount_driver(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Lazily build or return the active `MountDriver`.

        Resolved once per active telescope's `mount_protocol` (§2's
        registry), wrapping the shared session `.driver` already
        lazily builds -- one INDI client connection serves every
        device type (`Wayfinding_Library_Architecture.md` §2.5.1a).

        Returns
        -------
        mount_driver : `MountDriver`
            The active mount hardware-control driver.
        """
        if self.__mount_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_mount_driver_registry

            telescope = self.active_telescope()
            protocol = telescope.mount_protocol if telescope else "indi"
            self.__mount_driver = self._build_protocol_driver(build_mount_driver_registry(), protocol)
        return self.__mount_driver

    @mount_driver.setter
    def mount_driver(self, mount_driver) -> None:  # ruff: ignore[missing-type-function-argument]
        """Set the active `MountDriver` (test injection)."""
        self.__mount_driver = mount_driver

    @property
    def focuser_driver(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Lazily build or return the active `FocuserDriver`.

        Resolved once per active telescope's `focuser_protocol` (§2's
        registry), wrapping the same shared session `.driver` already
        lazily builds.

        Returns
        -------
        focuser_driver : `FocuserDriver`
            The active focuser hardware-control driver.
        """
        if self.__focuser_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_focuser_driver_registry

            telescope = self.active_telescope()
            protocol = telescope.focuser_protocol if telescope else "indi"
            self.__focuser_driver = self._build_protocol_driver(build_focuser_driver_registry(), protocol)
        return self.__focuser_driver

    @focuser_driver.setter
    def focuser_driver(self, focuser_driver) -> None:  # ruff: ignore[missing-type-function-argument]
        """Set the active `FocuserDriver` (test injection)."""
        self.__focuser_driver = focuser_driver

    @property
    def filter_wheel_driver(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Lazily build or return the active `FilterWheelDriver`.

        Resolved once per active telescope's `filter_wheel_protocol`
        (§2's registry), wrapping the same shared session `.driver`
        already lazily builds.

        Returns
        -------
        filter_wheel_driver : `FilterWheelDriver`
            The active filter wheel hardware-control driver.
        """
        if self.__filter_wheel_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_filter_wheel_driver_registry

            telescope = self.active_telescope()
            protocol = telescope.filter_wheel_protocol if telescope else "indi"
            self.__filter_wheel_driver = self._build_protocol_driver(
                build_filter_wheel_driver_registry(), protocol
            )
        return self.__filter_wheel_driver

    @filter_wheel_driver.setter
    def filter_wheel_driver(self, filter_wheel_driver) -> None:  # ruff: ignore[missing-type-function-argument]
        """Set the active `FilterWheelDriver` (test injection)."""
        self.__filter_wheel_driver = filter_wheel_driver

    @property
    def camera_driver(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Lazily build or return the active main-camera `CameraDriver`.

        Resolved once per active camera's `protocol` (§2's registry),
        wrapping the same shared session `.driver` already lazily builds.

        Returns
        -------
        camera_driver : `CameraDriver`
            The active main-camera hardware-control driver
            (``role="main"``).
        """
        if self.__camera_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_camera_driver_registry

            camera = self.active_camera()
            protocol = camera.protocol if camera else "indi"
            self.__camera_driver = self._build_protocol_driver(
                build_camera_driver_registry(), protocol, role="main"
            )
        return self.__camera_driver

    @camera_driver.setter
    def camera_driver(self, camera_driver) -> None:  # ruff: ignore[missing-type-function-argument]
        """Set the active main-camera `CameraDriver` (test injection)."""
        self.__camera_driver = camera_driver

    @property
    def guide_camera_driver(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Lazily build or return the active guide-camera `CameraDriver`.

        Always ``"indi"`` today -- the guide camera has no
        `EquipmentCatalog` entry of its own to hold a protocol
        selection (a known limitation, noted in §2), so there is
        nothing to resolve a protocol from yet.

        Returns
        -------
        guide_camera_driver : `CameraDriver`
            The active guide-camera hardware-control driver
            (``role="guide"``).
        """
        if self.__guide_camera_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_camera_driver_registry

            self.__guide_camera_driver = self._build_protocol_driver(
                build_camera_driver_registry(), "indi", role="guide"
            )
        return self.__guide_camera_driver

    @guide_camera_driver.setter
    def guide_camera_driver(self, guide_camera_driver) -> None:  # ruff: ignore[missing-type-function-argument]
        """Set the active guide-camera `CameraDriver` (test injection)."""
        self.__guide_camera_driver = guide_camera_driver

    @property
    def enclosure_driver(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Lazily build or return the active `EnclosureDriver`.

        Resolved once per configured `Enclosure`'s `protocol` (§2's
        registry), wrapping the same shared session `.driver` already
        lazily builds.

        Returns
        -------
        enclosure_driver : `EnclosureDriver`
            The active enclosure hardware-control driver.
        """
        if self.__enclosure_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_enclosure_driver_registry

            enclosure = self.active_enclosure()
            protocol = enclosure.protocol if enclosure else "indi"
            self.__enclosure_driver = self._build_protocol_driver(build_enclosure_driver_registry(), protocol)
        return self.__enclosure_driver

    @enclosure_driver.setter
    def enclosure_driver(self, enclosure_driver) -> None:  # ruff: ignore[missing-type-function-argument]
        """Set the active `EnclosureDriver` (test injection)."""
        self.__enclosure_driver = enclosure_driver

    @property
    def remote_transfer_driver(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Lazily build or return the active `RemoteTransferDriver`.

        Resolved once from `AppConfiguration.get_remote_transfer_driver_name`
        (default ``"stellarmate"``) -- a separate, independent choice
        from the six hardware-control protocol drivers above, since
        retrieving files from a telescope host is not part of INDI or
        ASCOM (§6). Cached rather than constructed fresh per call, per
        `remote_transfer_tasks.py`'s de-duplication cleanup.

        Returns
        -------
        remote_transfer_driver : `RemoteTransferDriver`
            The active remote-transfer driver.

        Raises
        ------
        NotImplementedError
            If the configured driver name has no constructor wiring
            yet (only ``"stellarmate"`` does today).
        """
        if self.__remote_transfer_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_remote_transfer_driver_registry

            driver_name = self._config.get_remote_transfer_driver_name()
            driver_class = build_remote_transfer_driver_registry()[driver_name]
            if driver_name == "stellarmate":
                self.__remote_transfer_driver = driver_class(
                    host_alias=self._config.get_telescope_hostname(),
                    remote_pictures_path=self._config.get_remote_pictures_path(),
                )
            else:
                raise NotImplementedError(
                    f"No constructor wiring yet for remote transfer driver '{driver_name}'"
                )
        return self.__remote_transfer_driver

    @remote_transfer_driver.setter
    def remote_transfer_driver(self, remote_transfer_driver) -> None:  # ruff: ignore[missing-type-function-argument]
        """Set the active `RemoteTransferDriver` (test injection)."""
        self.__remote_transfer_driver = remote_transfer_driver

    @property
    def indi_diagnostics(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Raw INDI device/property inspection, or `None` off-INDI.

        `None` when the active telescope's `mount_protocol` isn't
        ``"indi"`` -- this escape hatch is inherently INDI-specific,
        unlike the six `ProtocolDriver` properties above.

        Returns
        -------
        indi_diagnostics : `IndiDiagnostics` | `None`
            Raw INDI inspection for the active session, or `None` if
            the active mount protocol isn't ``"indi"``.
        """
        telescope = self.active_telescope()
        protocol = telescope.mount_protocol if telescope else "indi"
        if protocol != "indi":
            return None
        if self.__indi_diagnostics is None:
            from wayfindinglib.drivers.indi.diagnostics import IndiDiagnostics

            self.__indi_diagnostics = IndiDiagnostics(session=self.driver)
        return self.__indi_diagnostics

    def _build_protocol_driver(  # ruff: ignore[missing-return-type-private-function]
        self, registry: dict[str, type], protocol: str, **extra_kwargs: Any
    ):
        """Construct a protocol driver instance for `protocol` from `registry`.

        Only ``"indi"`` has real constructor wiring today -- it wraps
        the shared `.driver` session (§3). A future second protocol
        adds its own branch here rather than a generic `config=`
        constructor call, since different protocols need different
        construction arguments (a session vs. e.g. a network address).
        `extra_kwargs` passes through device-specific constructor
        arguments (e.g. `CameraDriver`'s `role`).

        Returns
        -------
        driver : `ProtocolDriver`
            The constructed driver instance.

        Raises
        ------
        NotImplementedError
            If `protocol` has no constructor wiring yet.
        """
        driver_class = registry[protocol]
        if protocol == "indi":
            return driver_class(session=self.driver, **extra_kwargs)
        raise NotImplementedError(f"No constructor wiring yet for protocol '{protocol}'")

    @property
    def guiding_service(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """The active guiding telemetry service."""
        return self._guiding_service

    @guiding_service.setter
    def guiding_service(self, guiding_service) -> None:  # ruff: ignore[missing-type-function-argument]
        """Set the active guiding telemetry service."""
        self._guiding_service = guiding_service

    @property
    def sync_service(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """The active plate-solve sync service."""
        return self._sync_service

    @sync_service.setter
    def sync_service(self, sync_service) -> None:  # ruff: ignore[missing-type-function-argument]
        """Set the active plate-solve sync service."""
        self._sync_service = sync_service

    # -- Hardware operations (relocated verbatim from observatorylib) ----

    def get_telescope_status(self) -> dict[str, Any]:
        """Return the current mount coordinates, tracking, and telemetry.

        Returns
        -------
        status : `dict`
            The mount's current coordinates, tracking state, and
            telemetry fields.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.get_telescope_status(self)

    def slew_to_target(self, target_name: str) -> bool:
        """Resolve target from library coordinates and drive mount to slew.

        Returns
        -------
        success : `bool`
            Whether the slew command was issued successfully.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.slew_to_target(self, target_name)

    def slew_to_coordinates(self, ra: float, dec: float) -> bool:
        """Command the telescope mount to slew to coordinates.

        Returns
        -------
        success : `bool`
            Whether the slew command was issued successfully.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.slew_to_coordinates(self, ra, dec)

    def sync_coordinates(self, ra: float, dec: float) -> bool:
        """Sync the mount's internal coordinates to a plate-solved position.

        Returns
        -------
        success : `bool`
            Whether the sync command was issued successfully.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.sync_coordinates(self, ra, dec)

    def park(self) -> bool:
        """Park the telescope mount.

        Returns
        -------
        success : `bool`
            Whether the park command was issued successfully.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.park(self)

    def unpark(self) -> bool:
        """Unpark the telescope mount.

        Returns
        -------
        success : `bool`
            Whether the unpark command was issued successfully.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.unpark(self)

    def set_tracking(self, enabled: bool) -> bool:
        """Enable or disable mount tracking.

        Returns
        -------
        success : `bool`
            Whether the tracking command was issued successfully.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.set_tracking(self, enabled)

    def set_filter(self, filter_name: str) -> bool:
        """Slew the filter wheel to a designated filter.

        Returns
        -------
        success : `bool`
            Whether the filter-wheel command was issued successfully.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.set_filter(self, filter_name)

    def manual_move(self, direction: str, start: bool = True) -> bool:
        """Drive manual motor movement in a specific direction.

        Returns
        -------
        success : `bool`
            Whether the move command was issued successfully.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.manual_move(self, direction, start)

    def abort_motion(self) -> bool:
        """Abort all telescope mount motion immediately.

        Returns
        -------
        success : `bool`
            Whether the abort command was issued successfully.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.abort_motion(self)

    def set_slew_rate(self, rate_index: int) -> bool:
        """Set mount slew rate index.

        Returns
        -------
        success : `bool`
            Whether the slew-rate command was issued successfully.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.set_slew_rate(self, rate_index)

    def focus_move(self, steps: int) -> bool:
        """Move the focuser motor by a designated number of steps.

        Returns
        -------
        success : `bool`
            Whether the focuser-move command was issued successfully.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.focus_move(self, steps)

    def get_focuser_position(self) -> int:
        """Get current focuser absolute position.

        Returns
        -------
        position : `int`
            The focuser's current absolute step position.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.get_focuser_position(self)

    def get_filter_names(self) -> list[str]:
        """List the configured filter wheel slot names.

        Returns
        -------
        filter_names : `list` [`str`]
            The configured name of each filter wheel slot, in order.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.get_filter_names(self)

    def pulse_guide(self, direction: str, duration_ms: float) -> bool:
        """Send a pulse guide command to the telescope mount.

        Returns
        -------
        success : `bool`
            Whether the pulse-guide command was issued successfully.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.pulse_guide(self, direction, duration_ms)

    def capture_image(self, exposure_seconds: float):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Take an exposure with the main camera.

        Returns
        -------
        result : `Any`
            The driver's raw exposure result.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.capture_image(self, exposure_seconds)

    def guide_expose(self, exposure_seconds: float, gain: float | None = None):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Take an exposure with the guide camera.

        Returns
        -------
        result : `Any`
            The driver's raw guide-exposure result.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.guide_expose(self, exposure_seconds, gain=gain)

    def get_guide_image(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Retrieve the last image blob from the guide camera.

        Returns
        -------
        image : `Any`
            The driver's raw last guide-camera image blob.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.get_guide_image(self)

    def drain_external_pulses(self) -> list[dict[str, Any]]:
        """Return and clear guide pulses issued by an external commander.

        Lets a caller passively observe guiding activity commanded by
        something other than this process (e.g. KStars/Ekos or PHD2
        driving the mount directly).

        Returns
        -------
        pulses : `list` [`dict`]
            Guide pulses detected since the last drain.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.drain_external_pulses(self)

    def connect(self) -> bool:
        """Command telescope hardware connection.

        Returns
        -------
        success : `bool`
            Whether the connect command was issued successfully.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.connect(self)

    def disconnect(self) -> bool:
        """Command a real, driver-level disconnection from the INDI server.

        Returns
        -------
        success : `bool`
            Whether the disconnect command was issued successfully.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.disconnect(self)

    def sync(self, target_name: str) -> dict[str, Any]:
        """Start a remote sync task for target frames.

        Returns
        -------
        result : `dict`
            The started sync task's status fields.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.sync(self, target_name)

    def is_syncing(self, target_name: str) -> bool:
        """Query active sync status for a target.

        Returns
        -------
        is_syncing : `bool`
            Whether a sync task for `target_name` is currently active.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.is_syncing(self, target_name)

    def get_observer_location(self) -> dict[str, float] | None:
        """Retrieve observer latitude, longitude, and elevation.

        Returns
        -------
        location : `dict` [`str`, `float`] or `None`
            The observer's latitude, longitude, and elevation, or
            `None` if unavailable.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.get_observer_location(self)

    # -- Equipment selection ----------------------------------------------

    def set_active_telescope(self, telescope_id: str) -> bool:
        """Record a new active telescope selection.

        Returns
        -------
        success : `bool`
            Whether the selection was recorded successfully.
        """
        from wayfindinglib.tasks.control_tasks.equipment_activation import set_active_telescope

        return set_active_telescope(self._config, telescope_id)

    def set_active_camera(self, camera_id: str) -> bool:
        """Record a new active camera selection.

        Returns
        -------
        success : `bool`
            Whether the selection was recorded successfully.
        """
        from wayfindinglib.tasks.control_tasks.equipment_activation import set_active_camera

        return set_active_camera(self._config, camera_id)

    def active_telescope(self) -> Telescope | None:
        """Return the currently active `Telescope`, or `None` if unset.

        Returns
        -------
        telescope : `Telescope` or `None`
            The currently active telescope, or `None` if unset.
        """
        return get_equipment_catalog(self._config).active_telescope()

    def active_camera(self) -> Camera | None:
        """Return the currently active `Camera`, or `None` if unset.

        Returns
        -------
        camera : `Camera` or `None`
            The currently active camera, or `None` if unset.
        """
        return get_equipment_catalog(self._config).active_camera()

    def list_camera_profiles(self) -> list[dict[str, Any]]:
        """Return all camera profiles defined in the config as dicts.

        Returns
        -------
        profiles : `list` [`dict`]
            Every configured camera profile.
        """
        from wayfindinglib.tasks.control_tasks.equipment_activation import list_camera_profiles

        return list_camera_profiles(self._config)

    def get_equipment_configuration(self) -> dict[str, Any] | None:
        """Return the active equipment configuration with FOV geometry.

        Returns
        -------
        configuration : `dict` or `None`
            The active telescope/camera pairing with derived
            field-of-view geometry, or `None` if unset.
        """
        from wayfindinglib.tasks.control_tasks.equipment_activation import get_equipment_configuration

        return get_equipment_configuration(self._config)

    # -- Corrections (pure: computed and returned, never issued) ---------

    def compute_pointing_correction(
        self,
        comparison_input_id: str,
        commanded_ra_deg: float,
        commanded_dec_deg: float,
        solved_ra_deg: float,
        solved_dec_deg: float,
        iteration: int,
        pointing_model: MountPointingModel | None = None,
    ) -> PointingCorrection:
        """Compute one iteration's pointing error and closing correction.

        Parameters
        ----------
        pointing_model : `MountPointingModel` | `None`, optional
            This session's fitted model (`get_pointing_model`), fed
            forward as `compute_pointing_correction`'s feedforward
            input. `None` (default) disables it -- deliberately not
            resolved automatically here, since it is session-scoped,
            not standing state this object holds (see
            `pointing_log_ingestion.py`).

        Returns
        -------
        correction : `PointingCorrection`
            The computed pointing error and closing correction.
        """
        from wayfindinglib.tasks.control_tasks.pointing_correction import compute_pointing_correction

        observer_location = self.get_observer_location()
        latitude_deg = observer_location["latitude"] if observer_location else 45.0
        return compute_pointing_correction(
            comparison_input_id,
            commanded_ra_deg,
            commanded_dec_deg,
            solved_ra_deg,
            solved_dec_deg,
            iteration,
            self._correction_config,
            pointing_model=pointing_model,
            latitude_deg=latitude_deg,
        )

    def compute_guiding_correction(
        self,
        comparison_input_id: str,
        drift_x_px: float,
        drift_y_px: float,
        elapsed_guiding_seconds: float | None = None,
        dec_direction_reversal: bool = False,
    ) -> GuidingCorrection:
        """Compute a signed per-axis guiding pulse from a measured pixel drift.

        Parameters
        ----------
        elapsed_guiding_seconds : `float` | `None`, optional
            Seconds since this guiding run began, forwarded to
            `compute_guiding_correction`'s periodic-error feedforward.
        dec_direction_reversal : `bool`, optional
            Whether this call's Dec correction reverses the previous
            pulse's direction, forwarded to the backlash feedforward.

        Returns
        -------
        correction : `GuidingCorrection`
            The computed signed per-axis guiding pulse, including any
            feedforward from `active_guiding_spectrum_analysis` (the
            active telescope's persisted, cross-night model --
            resolved automatically, since unlike `pointing_model`
            above this *is* standing state this object holds).

        Raises
        ------
        ValueError
            If no `GuiderCalibration` exists for the active
            telescope/camera pairing -- guiding correction never
            assumes a default calibration (§2.5.9, "Calibration
            Required").
        """
        from wayfindinglib.tasks.control_tasks.guiding_correction import compute_guiding_correction

        calibration = self.active_guider_calibration()
        if calibration is None:
            raise ValueError("No GuiderCalibration exists for the active telescope/camera pairing")
        return compute_guiding_correction(
            comparison_input_id,
            drift_x_px,
            drift_y_px,
            calibration,
            self._correction_config,
            mount_model=self.active_guiding_spectrum_analysis(),
            elapsed_guiding_seconds=elapsed_guiding_seconds,
            dec_direction_reversal=dec_direction_reversal,
        )

    def compute_focus_correction(
        self,
        comparison_input_id: str,
        curve: list[FocusCurvePoint],
        starting_position: int,
        trigger_reason: str,
    ) -> FocusCorrection:
        """Fit a parabola to a sampled focus curve and select its minimum.

        Returns
        -------
        correction : `FocusCorrection`
            The fitted curve's minimum and the resulting move.
        """
        from wayfindinglib.tasks.control_tasks.focus_correction import compute_focus_correction

        return compute_focus_correction(
            comparison_input_id, curve, starting_position, trigger_reason, self._correction_config
        )

    # -- Calibration -------------------------------------------------------

    def active_guider_calibration(self) -> GuiderCalibration | None:
        """Return the `GuiderCalibration` for the active equipment pairing.

        Returns
        -------
        calibration : `GuiderCalibration` or `None`
            The recorded calibration for the active telescope/camera
            pairing, or `None` if none exists.
        """
        telescope = self.active_telescope()
        camera = self.active_camera()
        if telescope is None or camera is None:
            return None
        for calibration in self._butler.get_all("guider_calibration"):
            if calibration.telescope_id == telescope.id and calibration.camera_id == camera.id:
                return calibration
        return None

    def save_guider_calibration(self, calibration: GuiderCalibration) -> None:
        """Record a measured `GuiderCalibration`."""
        self._butler.put(calibration, "guider_calibration", {"id": calibration.id})

    def run_guider_calibration(
        self,
        steps: GuiderCalibrationSteps,
        calibration_id: str,
        camera_id: str,
        telescope_id: str,
        arcsec_per_pixel: float,
        ra_pulse_duration_sec: float = 3.0,
        dec_pulse_duration_sec: float = 3.0,
    ) -> GuiderCalibration:
        """Command a known RA/Dec pulse pair and persist the calibration.

        `steps` supplies the real pulse/centroid-measurement operations
        -- this method does not construct a default wiring itself (the
        same precedent `execute_safe_state`'s `SafeStateSteps` sets: no
        production code builds one of those either). See
        `calibration_routines.run_guider_calibration` for the full
        sequencing and error contract.

        Returns
        -------
        calibration : `GuiderCalibration`
            The derived and persisted calibration.
        """
        from wayfindinglib.tasks.control_tasks.calibration_routines import (
            run_guider_calibration as _run_guider_calibration,
        )

        calibration = _run_guider_calibration(
            steps,
            calibration_id,
            camera_id,
            telescope_id,
            arcsec_per_pixel,
            ra_pulse_duration_sec,
            dec_pulse_duration_sec,
        )
        self.save_guider_calibration(calibration)
        return calibration

    def run_backlash_calibration(
        self,
        steps: BacklashCalibrationSteps,
        settle_direction: str = "north",
        reversed_direction: str = "south",
        settle_pulse_sec: float = 1.0,
        reversal_test_pulse_sec: float = 0.05,
        max_test_pulses: int = 40,
        motion_detection_threshold_px: float = 0.5,
    ) -> float:
        """Measure Dec backlash by reversing direction and probing gently.

        Does not persist the result itself -- the caller folds it into
        the active telescope's `GuidingSpectrumAnalysis`
        (`dec_backlash_estimate_ms`) via `save_guiding_spectrum_analysis`,
        since this measurement alone doesn't carry the periodic-error
        half of that model. See
        `calibration_routines.run_backlash_calibration` for the full
        probing algorithm.

        Returns
        -------
        backlash_estimate_ms : `float`
            The measured reversal delay, in milliseconds.
        """
        from wayfindinglib.tasks.control_tasks.calibration_routines import (
            run_backlash_calibration as _run_backlash_calibration,
        )

        return _run_backlash_calibration(
            steps,
            settle_direction,
            reversed_direction,
            settle_pulse_sec,
            reversal_test_pulse_sec,
            max_test_pulses,
            motion_detection_threshold_px,
        )

    def run_polar_alignment_assist(
        self, attempts: list[dict[str, Any]], latitude_deg: float | None = None
    ) -> MountPointingModel:
        """Fit ME/MA from this session's plate solves for real-time adjustment.

        Never persisted -- a live, repeatable adjust-and-recheck loop
        for tonight's own polar alignment, not standing state. See
        `calibration_routines.run_polar_alignment_assist` for the full
        workflow this is meant to support.

        Parameters
        ----------
        attempts : `list` [`dict` [`str`, `Any`]]
            This session's plate-solve records so far.
        latitude_deg : `float` | `None`, optional
            Observer latitude in decimal degrees; resolved from
            `get_observer_location` when not given.

        Returns
        -------
        model : `MountPointingModel`
            The freshly-fit model.
        """
        from wayfindinglib.tasks.control_tasks.calibration_routines import (
            run_polar_alignment_assist as _run_polar_alignment_assist,
        )

        if latitude_deg is None:
            observer_location = self.get_observer_location()
            latitude_deg = observer_location["latitude"] if observer_location else 45.0
        return _run_polar_alignment_assist(attempts, latitude_deg=latitude_deg)

    def active_focus_model(self) -> FocusModel | None:
        """Return the `FocusModel` for the active telescope/camera pairing.

        Returns
        -------
        focus_model : `FocusModel` or `None`
            The recorded focus model for the active telescope/camera
            pairing, or `None` if none exists.
        """
        telescope = self.active_telescope()
        camera = self.active_camera()
        if telescope is None or camera is None:
            return None
        for focus_model in self._butler.get_all("focus_model"):
            if focus_model.telescope_id == telescope.id and focus_model.camera_id == camera.id:
                return focus_model
        return None

    def save_focus_model(self, focus_model: FocusModel) -> None:
        """Record a measured `FocusModel`."""
        self._butler.put(focus_model, "focus_model", {"id": focus_model.id})

    # -- Guiding/alignment log ingestion (§6a) -------------------------------

    def active_guiding_spectrum_analysis(self) -> GuidingSpectrumAnalysis | None:
        """Return the standing `GuidingSpectrumAnalysis` for the active mount.

        Returns
        -------
        analysis : `GuidingSpectrumAnalysis` or `None`
            The most recently persisted spectrum analysis for the
            active telescope, or `None` if none has been recorded yet.
        """
        telescope = self.active_telescope()
        if telescope is None:
            return None
        return self._butler.get("guiding_spectrum_analysis", {"id": telescope.id})

    def save_guiding_spectrum_analysis(self, analysis: GuidingSpectrumAnalysis) -> None:
        """Record `analysis` as the active telescope's standing spectrum.

        Mount-mechanical periodic error/backlash is a property of the
        mount, not tonight's setup (unlike `MountPointingModel`'s
        session-scoped terms -- see `pointing_log_ingestion.py`), so
        this is keyed by the active telescope's id and overwritten on
        every refit rather than accumulated as separate rows.
        """
        telescope = self.active_telescope()
        analysis_id = telescope.id if telescope else "default"
        telescope_id = telescope.id if telescope else ""
        persisted = analysis.model_copy(update={"id": analysis_id, "telescope_id": telescope_id})
        self._butler.put(persisted, "guiding_spectrum_analysis", {"id": analysis_id})

    def ingest_guiding_log_file(
        self, file_path: str, target_name: str | None = None
    ) -> GuidingSpectrumAnalysis | None:
        """Parse one PHD2 guide log file, persist its samples, and refit.

        Returns
        -------
        analysis : `GuidingSpectrumAnalysis` | `None`
            The refit and persisted spectrum analysis, or `None` if the
            file contained no parseable samples -- see
            `guiding_log_ingestion.ingest_guide_log_file`.
        """
        from wayfindinglib.tasks.control_tasks import guiding_log_ingestion

        return guiding_log_ingestion.ingest_guide_log_file(
            self, self._logger_interface, file_path, target_name
        )

    def fetch_and_ingest_new_guide_logs(
        self, destination_dir: str, target_name: str | None = None
    ) -> GuidingSpectrumAnalysis | None:
        """Download every remote PHD2 guide log and ingest each one.

        The "new-artifact" trigger point this pipeline exists to give
        Control -- call when an exposure-complete/new-guide-data signal
        fires, or manually for catch-up/backfill -- see
        `guiding_log_ingestion.fetch_and_ingest_new_guide_logs`.

        Returns
        -------
        analysis : `GuidingSpectrumAnalysis` | `None`
            The refit and persisted spectrum analysis, or `None` if the
            configured remote-transfer driver doesn't support guide-log
            retrieval, or no new samples were found.
        """
        from wayfindinglib.tasks.control_tasks import guiding_log_ingestion

        return guiding_log_ingestion.fetch_and_ingest_new_guide_logs(
            self, self._logger_interface, destination_dir, target_name
        )

    def get_pointing_model(self, session_id: str | None = None) -> MountPointingModel:
        """Fit the geometric pointing model from locally recorded plate solves.

        Session-scoped, never persisted or fed forward across sessions
        -- see `pointing_log_ingestion.compute_pointing_model`.

        Returns
        -------
        model : `MountPointingModel`
            The freshly-fit model.
        """
        from wayfindinglib.tasks.control_tasks import pointing_log_ingestion

        return pointing_log_ingestion.compute_pointing_model(self, self._logger_interface, session_id)

    # -- Environmental safety -----------------------------------------------

    def assess_safety(self, readings: SensorReadings, now: datetime | None = None) -> SafetyAssessment:
        """Evaluate the recorded `SafetyRuleSet` against `readings`.

        Uses this high-level interface's own `SafetyMonitor` instance,
        so hysteresis state (the timestamp of the last non-safe
        reading) is carried across calls made through the same
        `ObservatoryControl`.

        Returns
        -------
        assessment : `SafetyAssessment`
            The current environmental verdict, which rule produced it,
            and its freshness.
        """
        rule_set = get_safety_rule_set(self._butler)
        return self._safety_monitor.evaluate(rule_set, readings, now or datetime.now(UTC))

    def get_safety_rule_set(self) -> SafetyRuleSet | None:
        """Return the recorded `SafetyRuleSet`, or `None` if unconfigured.

        Returns
        -------
        rule_set : `SafetyRuleSet` or `None`
            The recorded rule set, or `None` if unconfigured.
        """
        return get_safety_rule_set(self._butler)

    def save_safety_rule_set(self, rule_set: SafetyRuleSet) -> None:
        """Record `rule_set` as the active safety configuration."""
        save_safety_rule_set(self._butler, rule_set)

    # -- Enclosure and safe state -------------------------------------------

    def active_enclosure(self) -> Enclosure | None:
        """Return the configured `Enclosure`, or `None` if unrecorded.

        Returns
        -------
        enclosure : `Enclosure` or `None`
            The configured enclosure, or `None` if unrecorded.
        """
        enclosures = self._butler.get_all("enclosure")
        return enclosures[0] if enclosures else None

    def get_enclosure_state(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return the enclosure's current motion state.

        Returns
        -------
        state : `EnclosureState`
            The current enclosure state (`UNKNOWN` if unavailable).
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.get_enclosure_state(self)

    def open_enclosure(self) -> bool:
        """Open the enclosure.

        Returns
        -------
        success : `bool`
            Whether the open command was issued and confirmed.

        Requires `OBSERVATORY_SAFETY` to be currently `AUTHORITATIVE`.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.open_enclosure(self)

    def close_enclosure(self) -> bool:
        """Close the enclosure, refusing if the mount is not clear of it.

        Checks `enclosure_control.can_close_enclosure`'s geometric
        interlock, fed the current mount position, before dispatching
        to the driver -- the damage case this interlock exists to
        prevent.

        Returns
        -------
        success : `bool`
            Whether the close command was issued and confirmed.

        Requires `OBSERVATORY_SAFETY` to be currently `AUTHORITATIVE`.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.close_enclosure(self)

    def execute_safe_state(self, trigger: str, steps: SafeStateSteps) -> SafeStateOutcome:
        """Run the ordered, bounded safe-state sequence, recording it.

        Returns
        -------
        outcome : `SafeStateOutcome`
            The recorded outcome of the safe-state sequence.
        """
        from wayfindinglib.tasks.control_tasks.safe_state import execute

        return execute(trigger, steps)

    # -- Thermal -------------------------------------------------------------

    def cooling_ramp_rate(self, policy: CoolingPolicy) -> float:
        """Return the ramp rate for the active camera, capped at its max.

        Returns
        -------
        ramp_rate_c_per_min : `float`
            The effective cooling ramp rate, in degrees Celsius per
            minute.
        """
        from wayfindinglib.tasks.control_tasks.cooling_control import effective_ramp_rate_c_per_min

        return effective_ramp_rate_c_per_min(policy, self.active_camera())

    # -- Device state ----------------------------------------------------

    def summarize_device(
        self,
        device_id: str,
        device_role: DeviceRole,
        is_present: bool,
        is_connected: bool,
        allow_commands: bool,
        has_alert: bool,
        fault_detail: str | None = None,
    ) -> DeviceState:
        """Classify one device's raw signals into a `DeviceState`.

        Returns
        -------
        state : `DeviceState`
            The classified device state.
        """
        from wayfindinglib.tasks.control_tasks.device_state_tasks import summarize_device

        return summarize_device(
            device_id, device_role, is_present, is_connected, allow_commands, has_alert, fault_detail
        )

    # -- Commissioning evidence --------------------------------------------

    def save_commissioning_run(self, run: CommissioningRun) -> None:
        """Record a `CommissioningRun`.

        Append-only: re-saving under the same `id` overwrites, so
        callers must give each drill invocation a fresh `id` rather
        than reusing one across runs.
        """
        self._butler.put(run, "commissioning_run", {"id": run.id})

    def get_commissioning_runs(self) -> list[CommissioningRun]:
        """Return every recorded `CommissioningRun`.

        Returns
        -------
        runs : `list` [`CommissioningRun`]
            Every recorded commissioning run.
        """
        return self._butler.get_all("commissioning_run")

    # -- Capability promotion ----------------------------------------------

    def delegation_policy(self) -> DelegationPolicy:
        """Return the recorded `DelegationPolicy`.

        Returns
        -------
        policy : `DelegationPolicy`
            The recorded delegation policy.
        """
        return get_delegation_policy(self._butler)

    def apply_promotion_decision(
        self,
        capability: ObservatoryCapability,
        new_state: DelegationState,
        *,
        evidence_note: str = "",
    ) -> DelegationPolicy:
        """Apply an operator's promotion decision and record the result.

        Returns
        -------
        policy : `DelegationPolicy`
            The resulting, recorded delegation policy.
        """
        from wayfindinglib.tasks.control_tasks.capability_promotion import apply_promotion_decision

        return apply_promotion_decision(
            self._butler,
            capability,
            new_state,
            evidence_note=evidence_note,
            has_guider_calibration=self.active_guider_calibration() is not None,
            has_focus_model=self.active_focus_model() is not None,
        )

    def summarize_divergence_evidence(self, capability: ObservatoryCapability) -> DivergenceEvidenceSummary:
        """Summarize recorded divergence evidence for `capability`.

        Returns
        -------
        summary : `DivergenceEvidenceSummary`
            The aggregated divergence evidence for `capability`.
        """
        from wayfindinglib.tasks.control_tasks.capability_promotion import summarize_divergence_evidence

        divergence_records = self._butler.get_all("divergence_record")
        return summarize_divergence_evidence(capability, divergence_records)

    # -- Remote file transfer -----------------------------------------------

    def check_for_new_remote_images(self, target) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
        """Check for new FITS files on the telescope pictures path.

        Returns
        -------
        result : `dict`
            Whether new remote files are available, their count, and
            their names.
        """
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        return remote_transfer_tasks.check_for_new_remote_images(self, target)

    def download_remote_frames(
        self,
        target,  # ruff: ignore[missing-type-function-argument]
        selected_files: list[str] | None = None,
        remote_target_name: str | None = None,
        local_subfolder: str = "lights",
    ) -> bool:
        """Download remote frames from the telescope, then index them.

        Returns
        -------
        success : `bool`
            Whether the download and indexing succeeded.
        """
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        return remote_transfer_tasks.download_remote_frames(
            self, target, selected_files, remote_target_name, local_subfolder
        )

    def list_remote_targets(self) -> list[str]:
        """List astronomical target directories discovered on the telescope.

        Returns
        -------
        target_names : `list` [`str`]
            The names of every discovered remote target directory.
        """
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        return remote_transfer_tasks.list_remote_targets(self)

    def list_remote_target_folders(self) -> list[str]:
        """List remote folders that represent astronomical targets.

        Excludes Bias/Dark/Flat calibration folders from the full
        remote directory listing.

        Returns
        -------
        target_folder_names : `list` [`str`]
            Remote folder names that are not calibration folders.
        """
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        return remote_transfer_tasks.list_remote_target_folders(self)

    def list_remote_calibration_folders(self) -> list[str]:
        """List remote folders that hold Bias/Dark/Flat calibration frames.

        Returns
        -------
        calibration_folder_names : `list` [`str`]
            Remote folder names matching Bias, Dark, or Flat.
        """
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        return remote_transfer_tasks.list_remote_calibration_folders(self)

    def list_remote_files(self, folder_name: str) -> list[str]:
        """List FITS file relative paths in a remote target directory.

        Returns
        -------
        file_paths : `list` [`str`]
            Relative paths of every FITS file in the remote directory.
        """
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        return remote_transfer_tasks.list_remote_files(self, folder_name)

    def list_remote_files_with_sizes(self, folder_name: str) -> list[tuple[str, int]]:
        """List remote FITS relative paths paired with their byte sizes.

        Returns
        -------
        files_with_sizes : `list` [`tuple` [`str`, `int`]]
            ``(relative_path, size_in_bytes)`` for each FITS file in
            the remote directory.
        """
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        return remote_transfer_tasks.list_remote_files_with_sizes(self, folder_name)

    def check_remote_connection(self) -> bool:
        """Probe remote connection status.

        Returns
        -------
        connected : `bool`
            Whether the remote telescope host is reachable.
        """
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        return remote_transfer_tasks.check_remote_connection(self)

    def discover_unassociated_remote_targets(self) -> list[str]:
        """Discover remote folders that are not associated with any target.

        Returns
        -------
        folder_names : `list` [`str`]
            Remote folder names with no matching local target.
        """
        from astrometricslib import Astrometrics
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        astrometrics = Astrometrics(self._config)
        return remote_transfer_tasks.discover_unassociated_remote_targets(self, astrometrics.targets)

    def download_remote_targets(
        self,
        target_id: str,
        selected_files: list[str] | None = None,
        log_callback: Any | None = None,
        local_path: str | None = None,
        incremental: bool = True,
    ) -> bool:
        """Download target files into the light frames directory and reindex.

        If `local_path` is provided, skips download and ingests files
        from the local directory instead.

        Parameters
        ----------
        target_id : `str`
            The target id/name to resolve or create locally.
        selected_files : `list` [`str`], optional
            Specific remote file paths to download.
        log_callback : `Any`, optional
            Callback invoked with progress messages.
        local_path : `str`, optional
            If given, ingest from this local directory instead.
        incremental : `bool`, optional
            If `True` (default), transfer only remote files not
            already held locally -- see
            `remote_transfer_tasks.download_remote_targets`.

        Returns
        -------
        success : `bool`
            Whether the download (or local ingestion) and reindex
            succeeded.
        """
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        return remote_transfer_tasks.download_remote_targets(
            self, target_id, selected_files, log_callback, local_path, incremental
        )

    def sync_calibration_folder(self, remote_folder_name: str) -> dict[str, Any]:
        """Download a Bias/Dark/Flat folder into the calibration library.

        Never registers `remote_folder_name` as an astronomical
        target -- see `remote_transfer_tasks.sync_calibration_folder`
        for the full download/classify/reindex sequence, and for the
        `ValueError` raised on a non-calibration folder name.

        Returns
        -------
        summary : `dict` [`str`, `Any`]
            ``success``, ``remote_count``, ``already_held_count``,
            ``transferred_count`` and ``added_by_folder`` -- see
            `remote_transfer_tasks.sync_calibration_folder`.
        """
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        return remote_transfer_tasks.sync_calibration_folder(self, remote_folder_name)

    def sync_all_remote_folders(
        self, log_callback: Any | None = None, register_job: bool = True
    ) -> dict[str, Any]:
        """Download every remote folder: calibration frames and all targets.

        Splits the telescope's remote folders into calibration folders
        (routed into the calibration library) and target folders
        (every locally-catalogued target plus every remote folder with
        no matching local target). Never halts on a single folder's
        failure -- every folder is attempted, and failures are
        collected rather than raised.

        Parameters
        ----------
        log_callback : callable, optional
            Callable receiver for progress messages.
        register_job : `bool`, optional
            Whether to auto-register a `ProcessingJob` in
            astrometrics_log.db for this run (default `True`) -- see
            `remote_transfer_tasks.sync_all_remote_folders` for the
            full job-registration behavior.

        Returns
        -------
        result : `dict`
            A dict with ``"succeeded"``, ``"failed"``, and
            ``"job_id"`` keys. ``"succeeded"`` is a `list` of every
            folder name that downloaded successfully. ``"failed"`` is
            a `list` of ``(folder_name, error_message)`` tuples.
            ``"job_id"`` is the registered `ProcessingJob` id, or
            `None` if `register_job` was `False` or registration
            failed.
        """
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        return remote_transfer_tasks.sync_all_remote_folders(self, log_callback, register_job)
