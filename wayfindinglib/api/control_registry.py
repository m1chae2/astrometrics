"""Purpose: Observatory Control High-Level Interface.

Description: `ObservatoryControl` is the single entry point external
callers should use for direct hardware operation, envelope-respecting
corrections, calibration, safety, enclosure/safe-state, thermal, device
state, equipment selection, capability promotion, and remote transfer
(`Wayfinding_Library_Architecture.md` §2.5.1). Callers should never
import `tasks.control_tasks` directly.

Composes the driver layer through a lazily initialized `.driver`
attribute. `tasks.control_tasks.hardware_operations` uses this
high-level interface as its "manager" object.

Per §2.5.9's "Corrections Are Pure": `compute_*_correction` methods here
resolve the calibration/config inputs and forward to the pure task
functions, but issue nothing themselves. Sending a computed correction
through the driver is a separate, delegation-gated step this
high-level interface does not perform -- that orchestration belongs
to Observation Execution (M5) and the capability's current
`DelegationState`.
"""

import logging
import os
import statistics
from datetime import UTC, datetime
from typing import Any

from astrometricslib import background_job
from wayfindinglib.data_access.delegation_policy_reader import get_delegation_policy
from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog
from wayfindinglib.data_access.safety_policy_reader import (
    get_safety_rule_set,
    save_safety_rule_set,
)
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.equipment_and_site.enclosure import Enclosure
from wayfindinglib.models.equipment_and_site.equipment import Camera, CoolingPolicy, GuideScope, Telescope
from wayfindinglib.models.equipment_and_site.focus_model import FocusModel
from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
from wayfindinglib.models.policy.commissioning import CommissioningRun
from wayfindinglib.models.policy.delegation import DelegationPolicy, DelegationState, ObservatoryCapability
from wayfindinglib.models.policy.device_state import DeviceRole, DeviceState
from wayfindinglib.models.policy.safety import SafetyAssessment, SafetyRuleSet
from wayfindinglib.models.session.capture_quality import CaptureSessionAnalysis
from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.models.session.correction_result import (
    FocusCorrection,
    FocusCurvePoint,
    GuidingCorrection,
    PointingCorrection,
)
from wayfindinglib.models.session.ekos_session import EkosSessionContext
from wayfindinglib.models.session.guide_exposure_ladder import GuideExposureLadder
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.models.session.recurring_issue import RecurringIssue
from wayfindinglib.models.session.safe_state import SafeStateOutcome
from wayfindinglib.models.session.session_quality import GuidingSessionAnalysis
from wayfindinglib.models.session.sky_quality import SkyAnalysis
from wayfindinglib.models.session.telemetry import GuidingSpectrumAnalysis, MountPointingModel
from wayfindinglib.tasks.control_tasks.calibration_routines import (
    BacklashCalibrationSteps,
    GuiderCalibrationSteps,
)
from wayfindinglib.tasks.control_tasks.capability_promotion import (
    BulkDelegationOutcome,
    DivergenceEvidenceSummary,
)
from wayfindinglib.tasks.control_tasks.safe_state import SafeStateSteps
from wayfindinglib.tasks.control_tasks.safety_monitor import SafetyMonitor, SensorReadings

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "ObservatoryControl",
]

logger = logging.getLogger(__name__)


class ObservatoryControl:
    """Synchronous observatory-control API: hardware, safety, policy."""

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        config: Any | None = None,
        driver: Any | None = None,
        butler: DiskButler | None = None,
        correction_config: CorrectionConfig | None = None,
        astrometrics: Any | None = None,
    ):
        """Initialize the interface with config, a driver, and recording.

        `astrometrics` is the science library handle shared with the rest of
        the `Wayfinder`. When omitted, one is built over `config` on first use.
        """
        if config is None:
            from astrometricslib import get_configuration

            config = get_configuration()
        self._config = config
        self._astrometrics = astrometrics
        self.__driver = driver
        self.__mount_driver = None
        self.__focuser_driver = None
        self.__filter_wheel_driver = None
        self.__camera_driver = None
        self.__guide_camera_driver = None
        self.__enclosure_driver = None
        self.__switch_driver = None
        self.__weather_driver = None
        self.__remote_transfer_driver = None
        self.__indi_diagnostics = None
        self._guiding_service = None
        self._sync_service = None
        self.__logger_interface = None
        self._butler = butler or DiskButler(app_config=config)
        self._correction_config = correction_config or CorrectionConfig()
        self._safety_monitor = SafetyMonitor()

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

            self._astrometrics = Astrometrics(self._config)
        return self._astrometrics

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
    def switch_driver(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Lazily build or return the active `SwitchDriver`.

        No `EquipmentCatalog` entry names a protocol for this device --
        the powerbox is discovered heuristically, the same limitation
        `focuser_driver`/`filter_wheel_driver` have, so this always
        resolves to ``"indi"`` rather than reading a configured field.

        Returns
        -------
        switch_driver : `SwitchDriver`
            The active switch/power-distribution hardware-control driver.
        """
        if self.__switch_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_switch_driver_registry

            self.__switch_driver = self._build_protocol_driver(build_switch_driver_registry(), "indi")
        return self.__switch_driver

    @switch_driver.setter
    def switch_driver(self, switch_driver) -> None:  # ruff: ignore[missing-type-function-argument]
        """Set the active `SwitchDriver` (test injection)."""
        self.__switch_driver = switch_driver

    @property
    def weather_driver(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Lazily build or return the active `WeatherDriver`.

        Same heuristic-discovery limitation as `switch_driver` above:
        always resolves to ``"indi"``.

        Returns
        -------
        weather_driver : `WeatherDriver`
            The active weather/environmental-sensing hardware-control driver.
        """
        if self.__weather_driver is None:
            from wayfindinglib.drivers.protocols.registry import build_weather_driver_registry

            self.__weather_driver = self._build_protocol_driver(build_weather_driver_registry(), "indi")
        return self.__weather_driver

    @weather_driver.setter
    def weather_driver(self, weather_driver) -> None:  # ruff: ignore[missing-type-function-argument]
        """Set the active `WeatherDriver` (test injection)."""
        self.__weather_driver = weather_driver

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

    # -- Hardware operations ----

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

    def active_guide_scope(self) -> GuideScope | None:
        """Return the active `GuideScope`, or `None` if unconfigured.

        Returns
        -------
        guide_scope : `GuideScope` or `None`
            The active guide scope, or `None` if none is configured.
        """
        return get_equipment_catalog(self._config).active_guide_scope()

    def active_guide_camera(self) -> Camera | None:
        """Return the active guide `Camera`, or `None` if unconfigured.

        Returns
        -------
        guide_camera : `Camera` or `None`
            The camera that sees the guide star, or `None` when the
            main camera also does the guiding.
        """
        return get_equipment_catalog(self._config).active_guide_camera()

    def guider_plate_scale_arcsec_per_px(self) -> float | None:
        """Plate scale for `run_guider_calibration`'s `arcsec_per_pixel`.

        Uses the active `GuideScope`'s focal length in place of the
        active telescope's, and the active guide camera's pixel size in
        place of the main camera's, when those are configured
        (`EquipmentConfiguration.guider_plate_scale_arcsec_per_px`).

        Returns
        -------
        plate_scale : `float` or `None`
            Arcseconds per pixel, or `None` if no telescope and camera
            are both active.
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

    def refit_guiding_spectrum(
        self, session_id: str | None = None, limit: int = 2000
    ) -> GuidingSpectrumAnalysis:
        """Refit the cumulative guiding spectrum from already-recorded samples.

        Unlike `ingest_guiding_log_file`/`fetch_and_ingest_new_guide_logs`,
        this reads no new file -- it refits and persists from whatever
        samples are already on record, for an on-demand "what does the
        spectrum look like right now" query -- see
        `guiding_log_ingestion.refit_and_persist_guiding_spectrum`.

        Returns
        -------
        analysis : `GuidingSpectrumAnalysis`
            The refit and persisted spectrum analysis.
        """
        from wayfindinglib.tasks.control_tasks import guiding_log_ingestion

        return guiding_log_ingestion.refit_and_persist_guiding_spectrum(
            self, self._logger_interface, session_id, limit
        )

    # -- Ekos session log ingestion ------------------------------------------

    def save_ekos_session_context(self, context: EkosSessionContext) -> None:
        """Record one Ekos session's context, replacing an earlier read of it.

        Keyed by the analyze file's own name, so reading the same file
        again updates its record instead of adding a second one.
        """
        self._butler.put(context, "ekos_session_context", {"id": context.id})

    def save_guiding_run(self, run: GuidingRunSummary) -> None:
        """Record one guiding run, replacing an earlier read of the same run.

        Keyed by the guide log's file name and the run's position in it.
        """
        self._butler.put(run, "guiding_run", {"id": run.id})

    def list_guiding_runs(self, session_id: str | None = None) -> list[GuidingRunSummary]:
        """Return recorded guiding runs, oldest first.

        Parameters
        ----------
        session_id : `str` or `None`, optional
            Keep only the runs of this observing night.

        Returns
        -------
        runs : `list` [`GuidingRunSummary`]
            The matching runs.
        """
        runs = self._butler.get_all("guiding_run")
        return sorted(
            (run for run in runs if session_id is None or run.session_id == session_id),
            key=lambda run: run.started_at,
        )

    def get_ekos_session_context(self, session_file_id: str) -> EkosSessionContext | None:
        """Return one recorded Ekos session context.

        Parameters
        ----------
        session_file_id : `str`
            The analyze file's timestamp name, for example
            ``"2026-09-23T20-31-48"``.

        Returns
        -------
        context : `EkosSessionContext` or `None`
            The recorded context, or `None` if none has that id.
        """
        return self._butler.get("ekos_session_context", {"id": session_file_id})

    @background_job("diagnostics", grace_period_seconds=20.0)
    def night_history(
        self,
        kind: str,
        session_id: str | None = None,
        ekos_file_id: str | None = None,
        include: list[str] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Analyse or list past observing nights, in replies of bounded size.

        One front door for the history of the observatory: the capture and
        guiding analyses, the sky coverage, findings that recur across
        nights, Ekos session records, guiding runs, and the pointing model.
        It only reads. The reply is measured and shrunk if needed, so it
        never exceeds the MCP reply limit.

        Parameters
        ----------
        kind : `str`
            ``"capture"`` or ``"guiding"`` (a night's analysis with
            ``session_id``, otherwise one summary row per recent night),
            ``"sky_coverage"`` (all nights; slow), ``"recurring_issues"``,
            ``"ekos_sessions"`` (a list, or one session with
            ``ekos_file_id``), ``"guiding_runs"`` or ``"pointing_model"``
            (needs ``session_id``).
        session_id : `str`, optional
            An observing night, named for the local date on which it began,
            for example ``"2026-09-24"``.
        ekos_file_id : `str`, optional
            One Ekos session's id, for ``kind="ekos_sessions"``.
        include : `list` [`str`], optional
            Sections of that Ekos session to return: ``captures``,
            ``aborted_captures``, ``autofocus_runs``, ``align_events``,
            ``guide_state_events``, ``mount_state_events``,
            ``temperatures``, ``mount_positions``, ``equipment``. Without
            this, only an overview comes back.
        limit : `int`, optional
            How many of the most recent nights, runs or sessions to cover,
            and how many items of each Ekos section. From 1 to 50.
            Defaults to 10.

        Returns
        -------
        reply : `dict` [`str`, `Any`]
            The answer, or ``{"error": ...}``. If it had to be cut to fit,
            ``truncated`` is true.
        """
        from wayfindinglib.tasks.control_tasks import night_history

        return night_history.build_night_history(self, kind, session_id, ekos_file_id, include, limit)

    def frame_guiding(
        self,
        target_id: str,
        filter_name: str | None = None,
        first_file: str | None = None,
        last_file: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 60,
        include_quality: bool = False,
    ) -> dict[str, Any]:
        """Report the guide error during each light frame's exposure.

        Cuts the stored guide-log samples to the window of each frame, from
        its start time for its exposure length, and gives the sample count,
        how much of the window the samples cover, and the RMS and peak
        error in arcseconds. Use it to tell whether wind, a dither or a
        drift spoiled a particular frame. Frames that fall outside the
        ingested guide logs show no samples. Nothing is stored.

        Parameters
        ----------
        target_id : `str`
            The library target whose light frames to match.
        filter_name : `str`, optional
            Only frames of this filter, such as ``"L"`` or ``"Luminance"``.
            Spectroscopy frames are left out.
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
        include_quality : `bool`, optional
            Also put each frame's image measurements on its row: star
            count, star width (``fwhm_px``), roundness, longest trail, sky
            level, saturated pixels and quality flags. This gives one
            table of image quality and guide error per frame. Slow (about
            a second a frame), so at most 60 frames are measured.

        Returns
        -------
        report : `dict`
            ``frames`` (one row per frame) and ``group`` (the median error
            and the frames well above it), or ``{"error": ...}``.
        """
        from astrometricslib import FrameSelection, parse_iso_time
        from wayfindinglib.tasks.control_tasks import frame_guiding

        try:
            selection = FrameSelection(
                filter_name=filter_name,
                first_file=first_file,
                last_file=last_file,
                since=parse_iso_time(since),
                until=parse_iso_time(until),
            )
        except ValueError as error:
            return {"error": f"since and until must be ISO 8601 times: {error}"}
        return frame_guiding.link_frames_to_guiding(self, target_id, selection, limit, include_quality)

    def list_ekos_session_summaries(self) -> list[dict[str, Any]]:
        """Summarise every recorded Ekos session, without their full detail.

        Returns
        -------
        summaries : `list` [`dict`]
            One entry per session, oldest first: its id, observing night,
            start and end times, equipment fingerprint, and how many
            exposures, aborted exposures, autofocus runs and mount position
            samples it holds.
        """
        contexts = sorted(self._butler.get_all("ekos_session_context"), key=lambda c: c.started_at)
        return [
            {
                "id": context.id,
                "sessionId": context.session_id,
                "startedAt": context.started_at,
                "endedAt": context.ended_at,
                "equipmentFingerprint": context.equipment.equipment_fingerprint
                if context.equipment
                else None,
                "captures": len(context.captures),
                "abortedCaptures": len(context.aborted_captures),
                "autofocusRuns": len(context.autofocus_runs),
                "mountPositions": len(context.mount_positions),
            }
            for context in contexts
        ]

    def get_live_session_status(
        self,
        window_minutes: float = 10.0,
        refresh: bool = True,
        destination_dir: str | None = None,
        include: list[str] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Report how the current observing session is going, right now.

        Reads the telescope computer's latest Ekos analyze log and KStars
        text log, and reports the guiding accuracy over the last few
        minutes, the latest exposures with any that look ruined (too few
        stars, or a guiding excursion during them), each dither and whether
        it worked, and guiding excursions with their drift rate. It also
        gives the pier side, the camera temperature, how many exposures
        finished and were cancelled, and how the session has been
        dithering. The guide algorithm and its settings are not in the
        logs; the reply says so under ``unavailable``. Nothing is stored
        in the library; the answer is recomputed on every call, and
        ``refresh`` only copies the newest logs into a local folder.

        Parameters
        ----------
        window_minutes : `float`, optional
            Length of the recent-guiding window, in minutes.
        refresh : `bool`, optional
            Whether to download the latest logs first. `False` reads what
            is already in `destination_dir`.
        destination_dir : `str` or `None`, optional
            Local folder for the logs. Defaults to ``ekos_logs`` inside
            this library's own data folder.
        include : `list` [`str`], optional
            Sections of tonight's Ekos record to add under ``details``, so
            the reason behind a flag can be read: ``captures``,
            ``aborted_captures``, ``autofocus_runs`` (with each run's
            focus curve and whether it succeeded), ``align_events``,
            ``guide_state_events``, ``mount_state_events``,
            ``temperatures`` and ``mount_positions`` (declination,
            altitude, azimuth and pier side over time). Times are Unix
            seconds.
        limit : `int`, optional
            Most items per section, from 1 to 50. A longer list is sampled
            evenly, keeping the first and last. Defaults to 10.

        Returns
        -------
        status : `dict`
            See `LiveSessionStatus`. Empty with an ``"error"`` key if no
            readable analyze log exists.
        """
        from wayfindinglib.drivers import local_database
        from wayfindinglib.tasks.control_tasks import live_session_status_task

        if destination_dir is None:
            destination_dir = str(local_database._wayfinding_library_path(self._config) / "ekos_logs")
        status = live_session_status_task.get_live_session_status(
            self,
            destination_dir,
            window_minutes=window_minutes,
            refresh=refresh,
            include=include,
            limit=limit,
        )
        if status is None:
            return {"error": f"No readable Ekos analyze log found in {destination_dir}."}
        if isinstance(status, dict):
            return status
        from wayfindinglib.tasks.control_tasks.night_history import fit_to_budget

        return fit_to_budget(status.model_dump(mode="json"))

    def ingest_ekos_session_logs(
        self, destination_dir: str | None = None, download: bool = True
    ) -> dict[str, Any]:
        """Read Ekos's guide and session logs and store what they record.

        Downloads the guide logs and analyze logs from the telescope
        computer (skipping files already current), then stores the guiding
        samples and one session record per analyze file. Safe to repeat: a
        second run leaves the same data as the first. Each session is tied
        to the equipment it used, worked out from its own data.

        Parameters
        ----------
        destination_dir : `str` or `None`, optional
            Local folder for the downloaded logs. Defaults to ``ekos_logs``
            inside this library's own data folder.
        download : `bool`, optional
            If `False`, read whatever is already in `destination_dir` and
            do not contact the telescope computer.

        Returns
        -------
        summary : `dict`
            What was read and stored -- see `EkosLogIngestionSummary`.
        """
        from wayfindinglib.drivers import local_database
        from wayfindinglib.tasks.control_tasks import ekos_log_ingestion

        if destination_dir is None:
            destination_dir = str(local_database._wayfinding_library_path(self._config) / "ekos_logs")

        frame_lookup = None
        try:
            frame_lookup = ekos_log_ingestion.build_frame_lookup(self.astrometrics)
        except Exception as error:
            logger.warning("Could not read the frame library to name imaging equipment: %s", error)

        if download:
            summary = ekos_log_ingestion.fetch_and_ingest_ekos_session_logs(
                self, self._logger_interface, destination_dir, frame_lookup
            )
        else:
            summary = ekos_log_ingestion.ingest_ekos_session_logs_from_directory(
                self, self._logger_interface, destination_dir, frame_lookup
            )
        return summary.as_dict()

    # -- Performance envelope -----------------------------------------------

    def get_performance_envelope(
        self, blur_tolerance_fraction: float | None = None, before_night: str | None = None
    ) -> PerformanceEnvelope | None:
        """Work out the performance limits for the equipment in use now.

        Every limit is derived on the spot from the active equipment, the
        camera's stored profile, the star width this equipment's own frames
        show, and how this equipment behaved in earlier sessions. Nothing
        is stored, so changing the active equipment changes every limit,
        and the new equipment starts with no history of its own. A limit
        without enough data says so instead of giving a guess.

        Parameters
        ----------
        blur_tolerance_fraction : `float` or `None`, optional
            The most guiding error and trailing may widen a star image, as
            a fraction of its width. Defaults to 0.10.
        before_night : `str` or `None`, optional
            Use only this equipment's nights earlier than this one (an
            observing-night date such as ``"2026-09-24"``) for the history
            behind the baseline limits. Left out, the history is every
            recorded night, which is the right one for judging a new night.

        Returns
        -------
        envelope : `PerformanceEnvelope` or `None`
            The limits, or `None` if no telescope and camera are active.
        """
        return self._derive_performance_envelope(
            blur_tolerance_fraction,
            before_night,
            self._butler.get_all("ekos_session_context"),
            self._butler.get_all("guiding_run"),
            {},
        )

    def _capture_library(
        self, telescope_name: str, camera_name: str, library_cache: dict[tuple[str, str, str], Any]
    ) -> tuple[list[Any], list[Any]]:
        """Read the equipment's light frames and stack verdicts once.

        Parameters
        ----------
        telescope_name : `str`
            Name of the imaging telescope.
        camera_name : `str`
            Name of the imaging camera.
        library_cache : `dict`
            Holds earlier reads, so many nights share one read.

        Returns
        -------
        frames : `list` [`CaptureFrame`]
            The equipment's light frames, oldest first. Empty if the frame
            library could not be read.
        verdicts : `list` [`StackSaturationVerdict`]
            The science library's saturation verdicts for stacked exposures.
        """
        from wayfindinglib.tasks.control_tasks import capture_analysis_tasks

        key = ("library", telescope_name, camera_name)
        if key not in library_cache:
            try:
                astrometrics = self.astrometrics
                library_cache[key] = (
                    capture_analysis_tasks.collect_capture_frames(
                        astrometrics, telescope_name, camera_name, self._config
                    ),
                    capture_analysis_tasks.collect_stack_saturation_verdicts(astrometrics),
                )
            except Exception as error:
                logger.warning("Could not read this equipment's frames from the frame library: %s", error)
                library_cache[key] = ([], [])
        return library_cache[key]

    def _derive_performance_envelope(
        self,
        blur_tolerance_fraction: float | None,
        before_night: str | None,
        contexts: list[EkosSessionContext],
        runs: list[GuidingRunSummary],
        library_cache: dict[tuple[str, str, str], Any],
    ) -> PerformanceEnvelope | None:
        """Derive the envelope from data that is already loaded.

        `library_cache` holds what was read from the science library's frame
        records between calls, so analysing many nights reads them once, not
        once per night.

        Returns
        -------
        envelope : `PerformanceEnvelope` or `None`
            The limits, or `None` if no telescope and camera are active.
        """
        from wayfindinglib.analytics.performance_envelope import (
            DEFAULT_BLUR_TOLERANCE_FRACTION,
            MINIMUM_GUIDE_CYCLES_PER_EXPOSURE,
            derive_performance_envelope,
        )
        from wayfindinglib.models.equipment_and_site.equipment import EquipmentConfiguration
        from wayfindinglib.models.equipment_and_site.equipment_fingerprint import build_equipment_fingerprint
        from wayfindinglib.tasks.control_tasks import capture_analysis_tasks, performance_envelope_tasks

        catalog = get_equipment_catalog(self._config)
        telescope, camera = catalog.active_telescope(), catalog.active_camera()
        if telescope is None or camera is None:
            return None
        guide_scope, guide_camera = catalog.active_guide_scope(), catalog.active_guide_camera()
        equipment = EquipmentConfiguration(telescope=telescope, camera=camera)
        guide_scale = equipment.guider_plate_scale_arcsec_per_px(guide_scope, guide_camera)
        guide_focal_length = guide_scope.focal_length_mm if guide_scope else telescope.focal_length_mm
        fingerprint = build_equipment_fingerprint(
            telescope.name, camera.name, guide_focal_length, guide_scale
        )

        # The guide cycle is a property of the equipment, not of one night,
        # so it is read from every recorded night. The baseline limits use
        # only the nights before `before_night`.
        every_night = performance_envelope_tasks.collect_baseline_values(
            self._logger_interface, contexts, fingerprint, runs
        )
        baseline_values = (
            every_night
            if before_night is None
            else performance_envelope_tasks.collect_baseline_values(
                self._logger_interface, contexts, fingerprint, runs, before_night
            )
        )

        # Only frames long enough for guiding error to show in them say how
        # sharp this equipment's images are. "Long enough" is a few cycles
        # of the equipment's own guider, known only once it has guided.
        measured_image_quality = None
        cadences = every_night["guide_cadence_seconds"]
        guide_cadence = statistics.median(cadences) if cadences else None
        capture_frames, _ = self._capture_library(telescope.name, camera.name, library_cache)
        if guide_cadence is not None:
            measured_image_quality = performance_envelope_tasks.image_quality_from_frames(
                capture_frames, MINIMUM_GUIDE_CYCLES_PER_EXPOSURE * guide_cadence
            )
        baseline_values = {
            **baseline_values,
            **capture_analysis_tasks.collect_capture_baseline_values(
                capture_frames,
                contexts,
                None if guide_cadence is None else MINIMUM_GUIDE_CYCLES_PER_EXPOSURE * guide_cadence,
                before_night,
            ),
        }

        try:
            sensor_limits = performance_envelope_tasks.sensor_limits_for_camera(camera.name, self._config)
        except ValueError as error:
            logger.warning("No usable camera profiles, so no saturation limits: %s", error)
            sensor_limits = None

        def derive(values):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
            return derive_performance_envelope(
                equipment,
                guide_scope,
                guide_camera,
                fingerprint,
                sensor_limits=sensor_limits,
                measured_image_quality=measured_image_quality,
                baseline_values=values,
                guide_cadence_seconds=guide_cadence,
                blur_tolerance_fraction=(
                    DEFAULT_BLUR_TOLERANCE_FRACTION
                    if blur_tolerance_fraction is None
                    else blur_tolerance_fraction
                ),
            )

        # The excursion limit depends on the measured star width, and each
        # earlier night's share of excursions depends on that limit, so the
        # envelope is derived twice: once to learn the limit, once with the
        # nights' shares included.
        envelope = derive(baseline_values)
        excursion_limit = envelope.value("guide_excursion_limit")
        if excursion_limit is not None:
            baseline_values["guide_excursion_fraction"] = (
                performance_envelope_tasks.collect_excursion_fraction_baseline(
                    self._logger_interface, contexts, fingerprint, excursion_limit, before_night
                )
            )
            envelope = derive(baseline_values)
        return envelope

    # -- Session-quality analysis -------------------------------------------

    def _exposure_lengths_in_use(
        self, envelope: PerformanceEnvelope | None, library_cache: dict[tuple[str, str, str], Any]
    ) -> list[float]:
        """List the exposure lengths the active equipment is used with.

        Returns
        -------
        lengths : `list` [`float`]
            The lengths, shortest first. Empty if no equipment is active, the
            equipment has not guided yet, or no length has enough frames.
        """
        from wayfindinglib.tasks.control_tasks import capture_analysis_tasks

        minimum = envelope.value("minimum_star_measurement_exposure") if envelope else None
        catalog = get_equipment_catalog(self._config)
        telescope, camera = catalog.active_telescope(), catalog.active_camera()
        if minimum is None or telescope is None or camera is None:
            return []
        frames, _ = self._capture_library(telescope.name, camera.name, library_cache)
        return capture_analysis_tasks.exposure_lengths_in_use(frames, minimum)

    def _analyze_guiding_night(
        self,
        session_id: str,
        contexts: list[EkosSessionContext],
        runs: list[GuidingRunSummary],
        library_cache: dict[tuple[str, str, str], Any],
    ) -> GuidingSessionAnalysis | None:
        """Analyse one night's guiding from data that is already loaded.

        The night is judged against limits whose history contains only the
        equipment's earlier nights, never the night itself or later ones.

        Returns
        -------
        analysis : `GuidingSessionAnalysis` or `None`
            The analysis, or `None` if the night has no runs, samples or
            session records at all.
        """
        from wayfindinglib.session_analysis.guiding.pipeline import (
            GuidingAnalysisRequest,
            analyze_guiding_session_request,
        )
        from wayfindinglib.tasks.control_tasks import session_analysis_tasks as tasks

        night_contexts = [context for context in contexts if context.session_id == session_id]
        night_runs = [run for run in runs if run.session_id == session_id]
        samples = tasks.measured_night_samples(self._logger_interface, session_id)
        if not (night_contexts or night_runs or samples):
            return None
        envelope = self._derive_performance_envelope(None, session_id, contexts, runs, library_cache)
        exposure_lengths = self._exposure_lengths_in_use(envelope, library_cache)
        fingerprints = [c.equipment.equipment_fingerprint for c in night_contexts if c.equipment is not None]
        match = tasks.equipment_match_level(
            envelope.equipment_fingerprint if envelope else None, fingerprints, night_runs
        )
        request = GuidingAnalysisRequest(
            session_id=session_id,
            equipment_fingerprint=envelope.equipment_fingerprint if envelope else "unknown",
            envelope=envelope,
            limits_equipment_match=match,
            samples=samples,
            runs=night_runs,
            guide_scale_matches_configuration=tasks.guide_scale_agreement(night_contexts),
            logged_guide_scale=tasks.logged_guide_scale(night_runs),
            configured_guide_scale=self.guider_plate_scale_arcsec_per_px(),
            exposure_lengths_seconds=exposure_lengths,
        )
        return analyze_guiding_session_request(request)

    def analyze_guiding_session(self, session_id: str) -> GuidingSessionAnalysis | None:
        """Analyse one observing night's guiding in three stages.

        Pre-processing asks whether the guiding data is good: lost frames,
        a weak guide star, jumps to the wrong star, impossible calibrations.
        Processing measures the guiding error, what it does to star width,
        and the declination drift the guider had to correct. Post-processing
        turns those into recommendations, each with the evidence and the
        equipment-derived limit behind it. Nothing is stored: the result is
        computed from the stored samples and the equipment's current limits.

        Parameters
        ----------
        session_id : `str`
            The observing night, named for the local date on which it began,
            for example ``"2026-09-24"``.

        Returns
        -------
        analysis : `GuidingSessionAnalysis` or `None`
            The analysis, or `None` if nothing is recorded for that night.
        """
        return self._analyze_guiding_night(
            session_id,
            self._butler.get_all("ekos_session_context"),
            self._butler.get_all("guiding_run"),
            {},
        )

    def summarize_guiding_sessions(self, latest_nights: int | None = None) -> list[dict[str, Any]]:
        """Summarise the guiding analysis of every recorded night.

        Parameters
        ----------
        latest_nights : `int` or `None`, optional
            Cover only the most recent nights. Each night is analysed
            against the nights before it, so the cost grows with the
            number of nights; this bounds it.

        Returns
        -------
        summaries : `list` [`dict`]
            One row per night, oldest first: whether it was flagged and why,
            how many samples it has, the share of lost frames, the guide
            signal, the guiding error, and the kinds of its recommendations.
        """
        contexts = self._butler.get_all("ekos_session_context")
        runs = self._butler.get_all("guiding_run")
        library_cache: dict[tuple[str, str, str], Any] = {}
        rows = []
        for night in sorted({run.session_id for run in runs})[-latest_nights if latest_nights else None :]:
            analysis = self._analyze_guiding_night(night, contexts, runs, library_cache)
            if analysis is None:
                continue
            rows.append({
                "sessionId": night,
                "flagged": analysis.flagged,
                "flagReasons": analysis.flag_reasons,
                "limitsEquipmentMatch": analysis.input_quality.limits_equipment_match,
                "samples": analysis.input_quality.samples_analyzed,
                "lostFraction": analysis.input_quality.lost_fraction,
                "medianSnr": analysis.input_quality.median_snr,
                "rmsPerAxisArcsec": analysis.performance.rms_per_axis_arcsec,
                "recommendations": [r.kind.value for r in analysis.recommendations],
            })
        return rows

    def _analyze_capture_night(
        self,
        session_id: str,
        contexts: list[EkosSessionContext],
        runs: list[GuidingRunSummary],
        library_cache: dict[tuple[str, str, str], Any],
    ) -> CaptureSessionAnalysis | None:
        """Analyse one night's captured frames from already-loaded data.

        The night is judged against limits whose history contains only the
        equipment's earlier nights, never the night itself or later ones.

        Returns
        -------
        analysis : `CaptureSessionAnalysis` or `None`
            The analysis, or `None` if no equipment is active, or the night has
            neither frames from this equipment nor an Ekos record.
        """
        from wayfindinglib.session_analysis.capture.pipeline import (
            CaptureAnalysisRequest,
            analyze_capture_session_request,
        )
        from wayfindinglib.tasks.control_tasks import capture_analysis_tasks

        catalog = get_equipment_catalog(self._config)
        telescope, camera = catalog.active_telescope(), catalog.active_camera()
        if telescope is None or camera is None:
            return None
        frames, verdicts = self._capture_library(telescope.name, camera.name, library_cache)
        night_frames = capture_analysis_tasks.frames_by_night(frames).get(session_id, [])
        captures, aborted, has_record = capture_analysis_tasks.night_captures(contexts, session_id)
        if not (night_frames or has_record):
            return None
        envelope = self._derive_performance_envelope(None, session_id, contexts, runs, library_cache)
        request = CaptureAnalysisRequest(
            session_id=session_id,
            equipment_fingerprint=envelope.equipment_fingerprint if envelope else "unknown",
            envelope=envelope,
            limits_equipment_match="exact" if night_frames and envelope else "none",
            frames=night_frames,
            captures=captures,
            aborted_captures=aborted,
            has_ekos_record=has_record,
            stack_saturation=verdicts,
            sensor_pixel_count=camera.sensor_width_px * camera.sensor_height_px,
        )
        return analyze_capture_session_request(request)

    def analyze_capture_session(self, session_id: str) -> CaptureSessionAnalysis | None:
        """Analyse one observing night's captured frames in three stages.

        Pre-processing asks whether the capture data is good: whether every
        exposure Ekos finished reached the frame library, whether frames carry
        the measurements later steps need, and whether exposures were
        cancelled often. Processing measures which exposures clipped a star,
        how sharp and round the stars were, and how much of the night was
        spent exposing. Post-processing turns those into recommendations, each
        with the evidence and the equipment-derived limit behind it. The
        science library's own saturation verdicts are used wherever it has
        stacked the target. Nothing is stored: the result is computed from the
        recorded frames and the equipment's current limits.

        Parameters
        ----------
        session_id : `str`
            The observing night, named for the local date on which it began,
            for example ``"2026-09-24"``.

        Returns
        -------
        analysis : `CaptureSessionAnalysis` or `None`
            The analysis, or `None` if nothing is recorded for that night.
        """
        return self._analyze_capture_night(
            session_id,
            self._butler.get_all("ekos_session_context"),
            self._butler.get_all("guiding_run"),
            {},
        )

    def summarize_capture_sessions(self, latest_nights: int | None = None) -> list[dict[str, Any]]:
        """Summarise the capture analysis of every recorded night.

        Parameters
        ----------
        latest_nights : `int` or `None`, optional
            Cover only the most recent nights, to bound the cost.

        Returns
        -------
        summaries : `list` [`dict`]
            One row per night, oldest first: whether it was flagged and why,
            how many light frames it has, how many Ekos exposures have no
            frame, the median star width, and the kinds of its
            recommendations.
        """
        from wayfindinglib.tasks.control_tasks import capture_analysis_tasks

        contexts = self._butler.get_all("ekos_session_context")
        runs = self._butler.get_all("guiding_run")
        library_cache: dict[tuple[str, str, str], Any] = {}
        catalog = get_equipment_catalog(self._config)
        telescope, camera = catalog.active_telescope(), catalog.active_camera()
        if telescope is None or camera is None:
            return []
        frames, _ = self._capture_library(telescope.name, camera.name, library_cache)
        nights = set(capture_analysis_tasks.frames_by_night(frames)) | {
            context.session_id for context in contexts if context.captures
        }
        rows = []
        for night in sorted(nights)[-latest_nights if latest_nights else None :]:
            analysis = self._analyze_capture_night(night, contexts, runs, library_cache)
            if analysis is None:
                continue
            rows.append({
                "sessionId": night,
                "flagged": analysis.flagged,
                "flagReasons": analysis.flag_reasons,
                "lightFrames": analysis.input_quality.light_frames,
                "capturesWithoutFrame": analysis.input_quality.captures_without_frame,
                "medianStarWidthArcsec": analysis.performance.star_quality.median_star_width_arcsec,
                "dutyCycle": analysis.performance.efficiency.duty_cycle,
                "recommendations": [r.kind.value for r in analysis.recommendations],
            })
        return rows

    def analyze_sky_coverage(self) -> SkyAnalysis | None:
        """Compare how the equipment performs in different parts of the sky.

        Across every recorded night, asks whether stars were wider or less
        round, or the guiding error larger, at low altitude, in one azimuth
        direction, or on one side of the pier. Each measurement is compared
        with the typical value of the same night, which removes the night's
        seeing, and a part of the sky is judged only when several nights
        reached it. The result also maps which parts of the sky no night
        reached, so they are not mistaken for parts that performed well.
        Nothing is stored.

        Returns
        -------
        analysis : `SkyAnalysis` or `None`
            The analysis, or `None` if no telescope and camera are active.
        """
        from wayfindinglib.session_analysis.sky.pipeline import SkyAnalysisRequest, analyze_sky_request
        from wayfindinglib.tasks.control_tasks import sky_analysis_tasks

        catalog = get_equipment_catalog(self._config)
        telescope, camera = catalog.active_telescope(), catalog.active_camera()
        if telescope is None or camera is None:
            return None
        contexts = self._butler.get_all("ekos_session_context")
        runs = self._butler.get_all("guiding_run")
        library_cache: dict[tuple[str, str, str], Any] = {}
        envelope = self._derive_performance_envelope(None, None, contexts, runs, library_cache)
        frames, _ = self._capture_library(telescope.name, camera.name, library_cache)
        frame_samples, frames_without_position = sky_analysis_tasks.sky_samples_from_frames(
            frames, envelope.value("minimum_star_measurement_exposure") if envelope else None
        )
        guiding_analyses = [
            analysis
            for night in sorted({run.session_id for run in runs})
            if (analysis := self._analyze_guiding_night(night, contexts, runs, library_cache)) is not None
        ]
        run_samples, runs_without_position, nights_excluded = sky_analysis_tasks.sky_samples_from_guiding(
            guiding_analyses
        )
        samples = [*frame_samples, *run_samples]
        nights = sorted({sample.night for sample in samples})
        request = SkyAnalysisRequest(
            session_id=f"{nights[0]}..{nights[-1]}" if nights else "none",
            equipment_fingerprint=envelope.equipment_fingerprint if envelope else "unknown",
            envelope=envelope,
            limits_equipment_match="exact",
            samples=samples,
            samples_without_position=frames_without_position + runs_without_position,
            guiding_nights_excluded=nights_excluded,
            minimum_altitude_degrees=telescope.min_altitude_deg,
            maximum_altitude_degrees=telescope.max_altitude_deg,
            blur_tolerance_fraction=envelope.blur_tolerance_fraction if envelope else 0.10,
        )
        return analyze_sky_request(request)

    def summarize_recurring_issues(self, latest_nights: int | None = None) -> list[RecurringIssue]:
        """List the findings that repeat across nights.

        Parameters
        ----------
        latest_nights : `int` or `None`, optional
            Look only at the most recent nights of each kind (guiding and
            capture), to bound the cost.

        Runs the guiding and capture analyses on every night and reports each
        finding of advice or warning level that appears on at least two. A
        single bad night can be weather. The same finding on many nights points
        at the equipment or the routine. Nothing is stored.

        Returns
        -------
        issues : `list` [`RecurringIssue`]
            Each recurring finding with the nights it appeared on, most
            frequent first.
        """
        from wayfindinglib.session_analysis.recurring_issues import find_recurring_issues
        from wayfindinglib.tasks.control_tasks import capture_analysis_tasks

        contexts = self._butler.get_all("ekos_session_context")
        runs = self._butler.get_all("guiding_run")
        library_cache: dict[tuple[str, str, str], Any] = {}
        catalog = get_equipment_catalog(self._config)
        telescope, camera = catalog.active_telescope(), catalog.active_camera()
        capture_nights: set[str] = {context.session_id for context in contexts if context.captures}
        if telescope is not None and camera is not None:
            frames, _ = self._capture_library(telescope.name, camera.name, library_cache)
            capture_nights |= set(capture_analysis_tasks.frames_by_night(frames))
        per_night: list[tuple[str, str, list[Any]]] = []
        recent = slice(-latest_nights if latest_nights else None, None)
        for night in sorted({run.session_id for run in runs})[recent]:
            guiding = self._analyze_guiding_night(night, contexts, runs, library_cache)
            if guiding is not None:
                per_night.append(("guiding", night, guiding.recommendations))
        for night in sorted(capture_nights)[recent]:
            capture = self._analyze_capture_night(night, contexts, runs, library_cache)
            if capture is not None:
                per_night.append(("capture", night, capture.recommendations))
        return find_recurring_issues(per_night)

    def run_guide_exposure_test(
        self,
        exposure_seconds: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0),
        frames_per_exposure: int = 8,
        gain: float | None = None,
    ) -> GuideExposureLadder:
        """Find the shortest guide exposure this guide camera needs.

        Takes a short series of guide frames at each exposure length while
        the mount tracks without guiding. For each length it measures how
        bright the guide star is, whether it saturates, and how much its
        measured position jumps from frame to frame, in arcseconds. Seeing
        moves the star at every length, so a short exposure is judged by the
        noise it adds beyond the best length tried, against the guiding error
        this equipment can absorb. The answer is the shortest length that
        works, or a statement that none did and what to check.

        This commands the guide camera (exposures only). It does not move the
        mount and does not guide. Point the guide scope at a bright star
        first. The frame capture has not yet been run against the real guide
        camera.

        Parameters
        ----------
        exposure_seconds : `tuple` [`float`, ...], optional
            The exposure lengths to try.
        frames_per_exposure : `int`, optional
            Frames to take at each length. At least 5 are needed to work out
            the position noise.
        gain : `float` or `None`, optional
            Guide camera gain to set for the test, or `None` to leave it alone.

        Returns
        -------
        test : `GuideExposureLadder`
            One result per length and the recommended exposure.

        Raises
        ------
        RuntimeError
            If the telescope host is not reachable, no guide camera or limits
            are configured, or the camera does not deliver a frame.
        """
        from wayfindinglib.analytics.guide_exposure_ladder import analyze_guide_exposure_ladder
        from wayfindinglib.tasks.control_tasks import guide_exposure_ladder_tasks, performance_envelope_tasks

        if not self.check_remote_connection():
            raise RuntimeError("The telescope host is not reachable, so the guide camera cannot be used.")
        catalog = get_equipment_catalog(self._config)
        guide_camera = catalog.active_guide_camera()
        plate_scale = self.guider_plate_scale_arcsec_per_px()
        if guide_camera is None or plate_scale is None:
            raise RuntimeError("No guide camera and guide optics are configured.")
        envelope = self.get_performance_envelope()
        ceiling = performance_envelope_tasks.sensor_limits_for_camera(
            guide_camera.name, self._config
        ).clip_ceiling_adu
        frames = guide_exposure_ladder_tasks.capture_guide_ladder(
            lambda seconds, camera_gain: self.guide_expose(seconds, camera_gain),
            self.get_guide_image,
            exposure_seconds,
            frames_per_exposure,
            gain,
        )
        return analyze_guide_exposure_ladder(
            frames,
            plate_scale,
            ceiling,
            envelope.value("guiding_rms_limit") if envelope else None,
            envelope.blur_tolerance_fraction if envelope else 0.10,
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

    def refresh_safety_assessment(self) -> SafetyAssessment:
        """Read the active `weather_driver` and assess safety against it.

        The first live feed `assess_safety` has ever had (§1a) -- prior
        callers had to construct `SensorReadings` themselves with no
        driver to read them from.

        Returns
        -------
        assessment : `SafetyAssessment`
            The current environmental verdict against a fresh reading.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.refresh_safety_assessment(self)

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

    def enter_monitoring_mode(self, *, evidence_note: str = "") -> BulkDelegationOutcome:
        """Move every capability toward `DELEGATED` ("monitoring mode").

        This system's real backing for the "Safe Mode" toggle: watches
        and computes against every capability, issuing nothing. Always
        fully succeeds -- `DELEGATED` has no dependency ordering of its
        own to violate.

        Returns
        -------
        outcome : `BulkDelegationOutcome`
            Every capability's resulting state (always all `applied`,
            never `rejected`).
        """
        from wayfindinglib.tasks.control_tasks.capability_promotion import set_all_capabilities

        return set_all_capabilities(
            self._butler,
            DelegationState.DELEGATED,
            evidence_note=evidence_note,
            has_guider_calibration=self.active_guider_calibration() is not None,
            has_focus_model=self.active_focus_model() is not None,
        )

    def enter_controller_mode(self, *, evidence_note: str = "") -> BulkDelegationOutcome:
        """Move every capability toward `AUTHORITATIVE` ("controller mode").

        This system's real backing for the "Safe Mode" toggle's
        opposite state. A capability not yet eligible -- a correction
        capability not already `SHADOWED`, or capture before the three
        corrections above it are `AUTHORITATIVE` -- is reported in
        `BulkDelegationOutcome.rejected` rather than silently skipped or
        forced; a caller must promote it through the required
        intermediate states first (`apply_promotion_decision`).

        Returns
        -------
        outcome : `BulkDelegationOutcome`
            Which capabilities reached `AUTHORITATIVE`, and why any
            that didn't were rejected.
        """
        from wayfindinglib.tasks.control_tasks.capability_promotion import set_all_capabilities

        return set_all_capabilities(
            self._butler,
            DelegationState.AUTHORITATIVE,
            evidence_note=evidence_note,
            has_guider_calibration=self.active_guider_calibration() is not None,
            has_focus_model=self.active_focus_model() is not None,
        )

    # -- Remote file transfer -----------------------------------------------

    def check_for_new_remote_images(self, target) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
        """Check for new FITS files on the telescope pictures path.

        Parameters
        ----------
        target : `Target`
            The library target to check. Through the MCP server, give its
            id (for example ``"M 52"``).

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

        Parameters
        ----------
        target : `Target`
            The library target the frames belong to. Through the MCP
            server, give its id (for example ``"M 52"``).
        selected_files : `list` [`str`], optional
            Remote file paths to download; default is the whole folder.
        remote_target_name : `str`, optional
            The remote folder name when it differs from the target id.
        local_subfolder : `str`, optional
            The folder under the frames path to download into.

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
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        return remote_transfer_tasks.discover_unassociated_remote_targets(self, self.astrometrics.targets)

    def frame_status(self, target_id: str) -> dict[str, Any]:
        """Show where a target's frames are: telescope, drive, library.

        One call gives the count in each place and the frames that are in
        one place but not the next, so a missing frame can be traced to the
        step that lost it. It reads only. If the telescope computer cannot
        be reached, the drive and library parts are still given.

        Parameters
        ----------
        target_id : `str`
            The target, such as ``"M 57"``.

        Returns
        -------
        status : `dict` [`str`, `Any`]
            The counts and the differences, with a few file names each.
        """
        from wayfindinglib.tasks.control_tasks import frame_status

        return frame_status.build_frame_status(self, target_id)

    @background_job("remote_sync", grace_period_seconds=8.0)
    def sync_remote_frames(self, target_id: str, dry_run: bool = True) -> dict[str, Any]:
        """Bring one target's new frames into the library.

        With ``dry_run=True`` (the default) it only says what would transfer.
        A real run transfers the files not already held, sorts them into the
        library, adds the frame records and saves the target. It never
        deletes a file or a frame record. It refuses to run if the frames
        drive is not mounted, and the target must match a folder on the
        telescope computer. Through the MCP server a slow run returns a job
        id; follow it with ``jobs_query``.

        Parameters
        ----------
        target_id : `str`
            The target to sync, such as ``"M 13"``. It must match a target
            folder on the telescope computer.
        dry_run : `bool`, optional
            `True` (default) only reports the plan. `False` transfers.

        Returns
        -------
        result : `dict` [`str`, `Any`]
            The remote folder, how many files exist, are already held and
            would transfer, a few example names, and for a real run
            ``success`` and ``transferred``. A problem comes back under
            ``error``.
        """
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        return remote_transfer_tasks.sync_target_frames(self, target_id, dry_run)

    @background_job("remote_sync", grace_period_seconds=8.0)
    def sync_remote_logs(self, dry_run: bool = True) -> dict[str, Any]:
        """Bring the guide and Ekos logs into the library's database.

        With ``dry_run=True`` (the default) it only says which logs on the
        telescope computer are new. A real run downloads the new guide logs
        and Ekos analyze logs, then stores the guiding samples and one
        session record per analyze file. It is safe to repeat and never
        deletes. Through the MCP server a slow run returns a job id; follow
        it with ``jobs_query``.

        Parameters
        ----------
        dry_run : `bool`, optional
            `True` (default) only reports what is new. `False` ingests.

        Returns
        -------
        result : `dict` [`str`, `Any`]
            How many guide logs and Ekos analyze logs exist and are new,
            and for a real run what was stored. A problem comes back under
            ``error``.
        """
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        return remote_transfer_tasks.sync_remote_logs(self, dry_run)

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
