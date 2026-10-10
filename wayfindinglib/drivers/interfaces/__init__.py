"""Purpose: Driver interfaces, one abstract base class per device type.

Description: One interface per device type (mount, camera, focuser,
filter wheel, enclosure), not one per hardware-control protocol -- the
same shape ASCOM Alpaca uses. INDI is the first conformant implementation
(`wayfindinglib/drivers/indi/*_driver.py`); a second protocol implements
the same interfaces so devices in one equipment set can mix protocols.
"""

from wayfindinglib.drivers.interfaces.base_protocol_driver import ProtocolDriver
from wayfindinglib.drivers.interfaces.camera_driver import CameraDriver
from wayfindinglib.drivers.interfaces.enclosure_driver import EnclosureDriver, EnclosureState
from wayfindinglib.drivers.interfaces.filter_wheel_driver import FilterWheelDriver
from wayfindinglib.drivers.interfaces.focuser_driver import FocuserDriver
from wayfindinglib.drivers.interfaces.mount_driver import MountDriver, MountStatus

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
