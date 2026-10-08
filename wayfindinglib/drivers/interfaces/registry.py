"""Protocol-driver class registries, keyed by protocol name.

Mirrors `wayfindinglib/tasks/planning_tasks/catalog_operations.py`'s
`build_catalog_driver_registry()`, but maps a protocol name to a
**class**, not a stateless singleton instance -- hardware drivers are
stateful, connection-owning objects, one per device, constructed once
per active rig by `ObservatoryControl`.

Concrete-class imports stay inside each function body so importing
this module never pulls in `PyIndi`
(`Wayfinding_Library_Architecture.md`, "Planning Is
Hardware-Free").
"""

from wayfindinglib.drivers.interfaces.camera_driver import CameraDriver
from wayfindinglib.drivers.interfaces.enclosure_driver import EnclosureDriver
from wayfindinglib.drivers.interfaces.filter_wheel_driver import FilterWheelDriver
from wayfindinglib.drivers.interfaces.focuser_driver import FocuserDriver
from wayfindinglib.drivers.interfaces.guiding_driver import GuidingDriver
from wayfindinglib.drivers.interfaces.mount_driver import MountDriver
from wayfindinglib.drivers.interfaces.remote_transfer_driver import RemoteTransferDriver
from wayfindinglib.drivers.interfaces.switch_driver import SwitchDriver
from wayfindinglib.drivers.interfaces.weather_driver import WeatherDriver


def build_mount_driver_registry() -> dict[str, type[MountDriver]]:
    """Return the protocol-name -> `MountDriver` class registry.

    Returns
    -------
    registry : `dict` [`str`, `type` [`MountDriver`]]
        Maps a protocol name (e.g. ``"indi"``) to its `MountDriver` subclass.
    """
    from wayfindinglib.drivers.indi.mount_driver import IndiMountDriver

    return {"indi": IndiMountDriver}


def build_camera_driver_registry() -> dict[str, type[CameraDriver]]:
    """Return the protocol-name -> `CameraDriver` class registry.

    Returns
    -------
    registry : `dict` [`str`, `type` [`CameraDriver`]]
        Maps a protocol name (e.g. ``"indi"``) to its `CameraDriver` subclass.
    """
    from wayfindinglib.drivers.indi.camera_driver import IndiCameraDriver

    return {"indi": IndiCameraDriver}


def build_focuser_driver_registry() -> dict[str, type[FocuserDriver]]:
    """Return the protocol-name -> `FocuserDriver` class registry.

    Returns
    -------
    registry : `dict` [`str`, `type` [`FocuserDriver`]]
        Maps a protocol name (e.g. ``"indi"``) to its `FocuserDriver` subclass.
    """
    from wayfindinglib.drivers.indi.focuser_driver import IndiFocuserDriver

    return {"indi": IndiFocuserDriver}


def build_filter_wheel_driver_registry() -> dict[str, type[FilterWheelDriver]]:
    """Return the protocol-name -> `FilterWheelDriver` class registry.

    Returns
    -------
    registry : `dict` [`str`, `type` [`FilterWheelDriver`]]
        Maps a protocol name (e.g. ``"indi"``) to its `FilterWheelDriver`
        subclass.
    """
    from wayfindinglib.drivers.indi.filter_wheel_driver import IndiFilterWheelDriver

    return {"indi": IndiFilterWheelDriver}


def build_enclosure_driver_registry() -> dict[str, type[EnclosureDriver]]:
    """Return the protocol-name -> `EnclosureDriver` class registry.

    Returns
    -------
    registry : `dict` [`str`, `type` [`EnclosureDriver`]]
        Maps a protocol name (e.g. ``"indi"``) to its `EnclosureDriver`
        subclass.
    """
    from wayfindinglib.drivers.indi.enclosure_driver import IndiEnclosureDriver

    return {"indi": IndiEnclosureDriver}


def build_switch_driver_registry() -> dict[str, type[SwitchDriver]]:
    """Return the protocol-name -> `SwitchDriver` class registry.

    Returns
    -------
    registry : `dict` [`str`, `type` [`SwitchDriver`]]
        Maps a protocol name (e.g. ``"indi"``) to its `SwitchDriver` subclass.
    """
    from wayfindinglib.drivers.indi.switch_driver import IndiSwitchDriver

    return {"indi": IndiSwitchDriver}


def build_weather_driver_registry() -> dict[str, type[WeatherDriver]]:
    """Return the protocol-name -> `WeatherDriver` class registry.

    Returns
    -------
    registry : `dict` [`str`, `type` [`WeatherDriver`]]
        Maps a protocol name (e.g. ``"indi"``) to its `WeatherDriver` subclass.
    """
    from wayfindinglib.drivers.indi.weather_driver import IndiWeatherDriver

    return {"indi": IndiWeatherDriver}


def build_guiding_driver_registry() -> dict[str, type[GuidingDriver]]:
    """Return the protocol-name -> `GuidingDriver` class registry.

    Keyed by the active telescope's ``guiding_protocol``: ``"phd2"``
    (PHD2 guides), ``"internal"`` (KStars/Ekos guides the mount itself)
    or ``"simulator"`` (a stand-in guide loop).

    Returns
    -------
    registry : `dict` [`str`, `type` [`GuidingDriver`]]
        Maps a protocol name to its `GuidingDriver` subclass.
    """
    from wayfindinglib.drivers.indi.guiding_driver import IndiGuidingDriver
    from wayfindinglib.drivers.phd2.guiding_driver import Phd2GuidingDriver
    from wayfindinglib.drivers.simulators.guiding_simulator import SimulatedGuidingDriver

    return {"phd2": Phd2GuidingDriver, "internal": IndiGuidingDriver, "simulator": SimulatedGuidingDriver}


def build_remote_transfer_driver_registry() -> dict[str, type[RemoteTransferDriver]]:
    """Return the driver-name -> `RemoteTransferDriver` class registry.

    A separate registry from the six hardware-control ones above --
    remote file transfer is an independent, pluggable concern,
    keyed by `driver_name`, not `protocol_name`.

    Returns
    -------
    registry : `dict` [`str`, `type` [`RemoteTransferDriver`]]
        Maps a driver name (e.g. ``"stellarmate"``) to its
        `RemoteTransferDriver` subclass.
    """
    from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

    return {"stellarmate": StellarMateInterface}
