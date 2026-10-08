"""Abstract base class for camera hardware-control protocol drivers.

Instantiated twice per rig by `ObservatoryControl` -- once with
``role="main"`` for the imaging camera, once with ``role="guide"`` for
the guide camera -- mirroring how
`wayfindinglib/drivers/indi/camera_controller.py` already distinguishes
the two internally. No cooling-setpoint command:
nothing in the codebase commands `CCD_TEMPERATURE` today
(`wayfindinglib/tasks/control_tasks/cooling_control.py`'s ramp math is
pure and unwired), so this is left as a documented future extension
point rather than guessed at.
"""

import abc
from typing import Any, Literal

from wayfindinglib.drivers.interfaces.base_protocol_driver import ProtocolDriver

CameraRole = Literal["main", "guide"]


class CameraDriver(ProtocolDriver):
    """Abstract base for camera hardware-control protocol drivers."""

    @property
    @abc.abstractmethod
    def role(self) -> CameraRole:
        """Which camera on the rig this driver instance controls."""

    @abc.abstractmethod
    async def expose(self, exposure_seconds: float, gain: float | None = None) -> Any:
        """Start an exposure on this camera.

        Returns
        -------
        result : `Any`
            The driver's raw exposure result.
        """

    @abc.abstractmethod
    async def get_last_image(self) -> Any:
        """Retrieve the last image blob captured by this camera.

        Returns
        -------
        image : `Any`
            The driver's raw last-image blob.
        """

    @abc.abstractmethod
    async def get_sensor_temperature_c(self) -> float | None:
        """Return the camera sensor temperature in degrees Celsius.

        Returns
        -------
        temperature_c : `float` | `None`
            Sensor temperature, or `None` if unavailable.
        """
