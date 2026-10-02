"""Per-device-type hardware-control protocol driver ABCs.

One interface per device type (mount, camera, focuser, filter wheel,
enclosure), not one per protocol -- the same shape ASCOM Alpaca uses.
INDI is the first conformant implementation
(`wayfindinglib/drivers/indi/*_driver.py`); a second protocol implements
the same interfaces so devices in one equipment set can mix protocols.
"""

from wayfindinglib.drivers.protocols.base_protocol_driver import ProtocolDriver
from wayfindinglib.drivers.protocols.camera_driver import CameraDriver
from wayfindinglib.drivers.protocols.enclosure_driver import EnclosureDriver, EnclosureState
from wayfindinglib.drivers.protocols.filter_wheel_driver import FilterWheelDriver
from wayfindinglib.drivers.protocols.focuser_driver import FocuserDriver
from wayfindinglib.drivers.protocols.mount_driver import MountDriver, MountStatus

__all__ = [
    "CameraDriver",
    "EnclosureDriver",
    "EnclosureState",
    "FilterWheelDriver",
    "FocuserDriver",
    "MountDriver",
    "MountStatus",
    "ProtocolDriver",
]
