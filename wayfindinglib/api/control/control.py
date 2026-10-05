"""Purpose: `ObservatoryControl`, the entry point for the observatory.

Description: `Wayfinder.control` is an `ObservatoryControl`. It holds no
operations of its own. Its seven children, built once with it, do the
work, each on one topic:

- `mount`: pointing and tracking;
- `imaging`: the main camera, filter wheel and focuser;
- `guiding`: the guide camera, guide pulses and guider models;
- `remote`: the observatory computer's files and logs;
- `history`: past and current observing sessions;
- `safety`: weather, the enclosure, and who is allowed to act;
- `equipment`: the equipment profile and device connections.

The children share one `ControlContext` (configuration, storage and
drivers) and hold no reference back to `control`. The driver properties
stay here, as the place where tests and the backend put in their own
drivers. Setting one updates the shared context, so every child sees it.
Callers never import `wayfindinglib.tasks` directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from wayfindinglib.api.control.context import ControlContext
from wayfindinglib.api.control.equipment import EquipmentControl
from wayfindinglib.api.control.guiding import GuidingControl
from wayfindinglib.api.control.history import HistoryControl
from wayfindinglib.api.control.imaging import ImagingControl
from wayfindinglib.api.control.mount import MountControl
from wayfindinglib.api.control.remote import RemoteControl
from wayfindinglib.api.control.safety import SafetyControl

if TYPE_CHECKING:
    from astrometricslib import AppConfiguration, Astrometrics
    from wayfindinglib.drivers.butler import DiskButler
    from wayfindinglib.drivers.protocols.camera_driver import CameraDriver
    from wayfindinglib.drivers.protocols.enclosure_driver import EnclosureDriver
    from wayfindinglib.drivers.protocols.filter_wheel_driver import FilterWheelDriver
    from wayfindinglib.drivers.protocols.focuser_driver import FocuserDriver
    from wayfindinglib.drivers.protocols.mount_driver import MountDriver
    from wayfindinglib.drivers.protocols.remote_transfer_driver import RemoteTransferDriver
    from wayfindinglib.drivers.protocols.switch_driver import SwitchDriver
    from wayfindinglib.drivers.protocols.weather_driver import WeatherDriver
    from wayfindinglib.models.session.correction_config import CorrectionConfig

__all__ = ["ObservatoryControl"]


class ObservatoryControl:
    """Operate the observatory through seven topic children.

    Parameters
    ----------
    config : `AppConfiguration`, optional
        The application configuration. Loaded when omitted.
    driver : `Any`, optional
        The INDI session. Built on first use when omitted.
    butler : `DiskButler`, optional
        Stores the wayfinding records. Built over `config` when omitted.
    correction_config : `CorrectionConfig`, optional
        Limits and gains for the computed corrections.
    astrometrics : `Astrometrics`, optional
        The science library handle shared with the rest of the
        `Wayfinder`. Built over `config` on first use when omitted.

    Attributes
    ----------
    mount : `MountControl`
        Pointing and tracking.
    imaging : `ImagingControl`
        The main camera, filter wheel and focuser.
    guiding : `GuidingControl`
        The guide camera, guide pulses and guider models.
    remote : `RemoteControl`
        The observatory computer's files and logs.
    history : `HistoryControl`
        Past and current observing sessions.
    safety : `SafetyControl`
        Weather, the enclosure, and who is allowed to act.
    equipment : `EquipmentControl`
        The equipment profile and device connections.
    """

    mount: MountControl
    imaging: ImagingControl
    guiding: GuidingControl
    remote: RemoteControl
    history: HistoryControl
    safety: SafetyControl
    equipment: EquipmentControl

    def __init__(
        self,
        config: AppConfiguration | None = None,
        driver: Any | None = None,
        butler: DiskButler | None = None,
        correction_config: CorrectionConfig | None = None,
        astrometrics: Astrometrics | None = None,
    ) -> None:
        """Build the shared context and the seven children."""
        if config is None:
            from astrometricslib import get_configuration

            config = get_configuration()
        self._context = ControlContext(
            config,
            driver=driver,
            butler=butler,
            correction_config=correction_config,
            astrometrics=astrometrics,
        )
        self.mount = MountControl(self._context)
        self.imaging = ImagingControl(self._context)
        self.guiding = GuidingControl(self._context)
        self.remote = RemoteControl(self._context)
        self.history = HistoryControl(self._context)
        self.safety = SafetyControl(self._context)
        self.equipment = EquipmentControl(self._context)

    @property
    def driver(self) -> Any:
        """The INDI session every INDI device driver wraps.

        Returns
        -------
        driver : `IndiInterface` or `SimulatorIndiInterface`
            Built on first use: the simulator when ``ASTROMETRICS_TESTING``
            is set, otherwise the real INDI client.
        """
        return self._context.driver

    @driver.setter
    def driver(self, driver: Any) -> None:
        """Set the INDI session."""
        self._context.driver = driver

    @property
    def mount_driver(self) -> MountDriver:
        """The mount driver, built for the active telescope's protocol.

        Returns
        -------
        mount_driver : `MountDriver`
            The active mount driver.
        """
        return self._context.mount_driver

    @mount_driver.setter
    def mount_driver(self, mount_driver: MountDriver) -> None:
        """Set the mount driver."""
        self._context.mount_driver = mount_driver

    @property
    def focuser_driver(self) -> FocuserDriver:
        """The focuser driver, built for the active telescope's protocol.

        Returns
        -------
        focuser_driver : `FocuserDriver`
            The active focuser driver.
        """
        return self._context.focuser_driver

    @focuser_driver.setter
    def focuser_driver(self, focuser_driver: FocuserDriver) -> None:
        """Set the focuser driver."""
        self._context.focuser_driver = focuser_driver

    @property
    def filter_wheel_driver(self) -> FilterWheelDriver:
        """The filter wheel driver, built for the active telescope's protocol.

        Returns
        -------
        filter_wheel_driver : `FilterWheelDriver`
            The active filter wheel driver.
        """
        return self._context.filter_wheel_driver

    @filter_wheel_driver.setter
    def filter_wheel_driver(self, filter_wheel_driver: FilterWheelDriver) -> None:
        """Set the filter wheel driver."""
        self._context.filter_wheel_driver = filter_wheel_driver

    @property
    def camera_driver(self) -> CameraDriver:
        """The main camera driver, built for the active camera's protocol.

        Returns
        -------
        camera_driver : `CameraDriver`
            The active main camera driver.
        """
        return self._context.camera_driver

    @camera_driver.setter
    def camera_driver(self, camera_driver: CameraDriver) -> None:
        """Set the main camera driver."""
        self._context.camera_driver = camera_driver

    @property
    def guide_camera_driver(self) -> CameraDriver:
        """The guide camera driver.

        Returns
        -------
        guide_camera_driver : `CameraDriver`
            The active guide camera driver.
        """
        return self._context.guide_camera_driver

    @guide_camera_driver.setter
    def guide_camera_driver(self, guide_camera_driver: CameraDriver) -> None:
        """Set the guide camera driver."""
        self._context.guide_camera_driver = guide_camera_driver

    @property
    def enclosure_driver(self) -> EnclosureDriver:
        """The enclosure driver, built for the configured enclosure's protocol.

        Returns
        -------
        enclosure_driver : `EnclosureDriver`
            The active enclosure driver.
        """
        return self._context.enclosure_driver

    @enclosure_driver.setter
    def enclosure_driver(self, enclosure_driver: EnclosureDriver) -> None:
        """Set the enclosure driver."""
        self._context.enclosure_driver = enclosure_driver

    @property
    def switch_driver(self) -> SwitchDriver:
        """The power switch driver.

        Returns
        -------
        switch_driver : `SwitchDriver`
            The active power switch driver.
        """
        return self._context.switch_driver

    @switch_driver.setter
    def switch_driver(self, switch_driver: SwitchDriver) -> None:
        """Set the power switch driver."""
        self._context.switch_driver = switch_driver

    @property
    def weather_driver(self) -> WeatherDriver:
        """The weather sensor driver.

        Returns
        -------
        weather_driver : `WeatherDriver`
            The active weather sensor driver.
        """
        return self._context.weather_driver

    @weather_driver.setter
    def weather_driver(self, weather_driver: WeatherDriver) -> None:
        """Set the weather sensor driver."""
        self._context.weather_driver = weather_driver

    @property
    def remote_transfer_driver(self) -> RemoteTransferDriver:
        """The driver that copies files from the observatory computer.

        Returns
        -------
        remote_transfer_driver : `RemoteTransferDriver`
            The active remote transfer driver.
        """
        return self._context.remote_transfer_driver

    @remote_transfer_driver.setter
    def remote_transfer_driver(self, remote_transfer_driver: RemoteTransferDriver) -> None:
        """Set the remote transfer driver."""
        self._context.remote_transfer_driver = remote_transfer_driver
