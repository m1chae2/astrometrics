"""Protocol-driver class registries, keyed by protocol name.

Mirrors `wayfindinglib/tasks/planning_tasks/catalog_operations.py`'s
`build_catalog_driver_registry()`, but maps a protocol name to a
**class**, not a stateless singleton instance -- hardware drivers are
stateful, connection-owning objects, one per device, constructed once
per active rig by `ObservatoryControl`.

Concrete-class imports stay inside each function body so importing
this module never pulls in `PyIndi`
(`Wayfinding_Library_Architecture.md` §2.3.4, "Planning Is
Hardware-Free").
"""

from wayfindinglib.drivers.protocols.camera_driver import CameraDriver
from wayfindinglib.drivers.protocols.enclosure_driver import EnclosureDriver
from wayfindinglib.drivers.protocols.filter_wheel_driver import FilterWheelDriver
from wayfindinglib.drivers.protocols.focuser_driver import FocuserDriver
from wayfindinglib.drivers.protocols.mount_driver import MountDriver
from wayfindinglib.drivers.protocols.remote_transfer_driver import RemoteTransferDriver


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


def build_remote_transfer_driver_registry() -> dict[str, type[RemoteTransferDriver]]:
    """Return the driver-name -> `RemoteTransferDriver` class registry.

    A separate registry from the six hardware-control ones above --
    remote file transfer is an independent, pluggable concern (§6),
    keyed by `driver_name`, not `protocol_name`.

    Returns
    -------
    registry : `dict` [`str`, `type` [`RemoteTransferDriver`]]
        Maps a driver name (e.g. ``"stellarmate"``) to its
        `RemoteTransferDriver` subclass.
    """
    from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

    return {"stellarmate": StellarMateInterface}
