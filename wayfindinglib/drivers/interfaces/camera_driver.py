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
    async def abort_exposure(self) -> bool:
        """Cancel the exposure this camera is taking, if any.

        Returns
        -------
        success : `bool`
            `True` if the abort command was sent. Sending it to an idle
            camera is harmless.
        """

    @abc.abstractmethod
    async def set_image_reception(self, enabled: bool) -> bool:
        """Turn the receiving of this camera's images on or off.

        Images are received only when a caller needs them, such as a
        centering frame. Otherwise the app never receives a frame, which
        keeps a remote camera's large images off the network.

        Parameters
        ----------
        enabled : `bool`
            `True` to receive images, `False` to stop.

        Returns
        -------
        success : `bool`
            `True` if the request was sent.
        """

    @abc.abstractmethod
    async def wait_for_new_image(self, timeout_seconds: float) -> Any:
        """Wait for a frame that arrives after reception was turned on.

        Parameters
        ----------
        timeout_seconds : `float`
            How long to wait, in seconds.

        Returns
        -------
        image : `Any`
            The new frame's raw data, or `None` if none arrived in time.
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
