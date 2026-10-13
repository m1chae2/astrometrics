"""INDI implementation of the `CameraDriver` protocol ABC.

Wraps the same shared `IndiInterface` session as `IndiMountDriver`,
instantiated once per role (main/guide), per
`Wayfinding_Library_Architecture.md`.
"""

import asyncio
from typing import Any

from wayfindinglib.drivers.interfaces.camera_driver import CameraDriver, CameraRole


class IndiCameraDriver(CameraDriver):
    """Adapts a shared `IndiInterface` session to the `CameraDriver` ABC."""

    def __init__(self, session: Any, role: CameraRole = "main") -> None:
        """Wrap an existing `IndiInterface`/`SimulatorIndiInterface`.

        Parameters
        ----------
        session : `IndiInterface`
            The shared INDI session other `Indi*Driver` adapters for
            this rig also wrap.
        role : `CameraRole`, optional
            Which camera on the rig this instance controls.
        """
        self._session = session
        self._role = role

    @property
    def protocol_name(self) -> str:
        """Registry key for this driver."""
        return "indi"

    @property
    def display_name(self) -> str:
        """Human-readable label for this driver."""
        return "INDI"

    @property
    def role(self) -> CameraRole:
        """Which camera on the rig this driver instance controls."""
        return self._role

    async def connect(self) -> bool:
        """INDI cameras connect as part of the shared session's discovery.

        Returns
        -------
        success : `bool`
            Whether the underlying INDI server connection is live.
        """
        return await asyncio.to_thread(self._session.isServerConnected)

    async def disconnect(self) -> bool:
        """INDI has no per-device disconnect; the shared session owns this.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        return True

    async def is_connected(self) -> bool:
        """Report whether the shared INDI session's server is connected.

        Returns
        -------
        connected : `bool`
            Whether the underlying INDI server connection is live.
        """
        return await asyncio.to_thread(self._session.isServerConnected)

    async def expose(self, exposure_seconds: float, gain: float | None = None) -> Any:
        """Start an exposure on this camera.

        For ``role="main"`` this triggers capture
        (`IndiInterface.capture_image`); `get_last_image` reads the frame
        once it has arrived.

        Returns
        -------
        result : `Any`
            The driver's raw exposure result.
        """
        if self._role == "guide":
            return await asyncio.to_thread(self._session.guide_expose, exposure_seconds, gain)
        return await asyncio.to_thread(self._session.capture_image, exposure_seconds)

    async def get_last_image(self) -> Any:
        """Retrieve the last image this camera sent.

        Returns
        -------
        image : `bytes` or `None`
            The frame's raw data (usually a FITS file in memory), or
            `None` if the camera has sent no frame yet.
        """
        if self._role == "guide":
            return await asyncio.to_thread(self._session.get_guide_image)
        return await asyncio.to_thread(self._session.get_main_image)

    async def get_sensor_temperature_c(self) -> float | None:
        """Return this camera's sensor temperature.

        Bug fixed from an earlier version of this method (M4): only the
        main camera's sensor temperature is tracked by `IndiInterface`
        today (`TelescopeStatus.camera_temperature`, read from
        `CCD_TEMPERATURE` on the main camera device, formatted like
        ``"21.3°C"``). `TelescopeStatus.temperature` is the *ambient*
        powerbox/weather reading, not a camera's own sensor -- using it
        here would have been wrong. No guide-camera temperature is
        tracked anywhere in `IndiInterface`, so ``role="guide"`` is
        honestly `None` rather than fabricated.

        Returns
        -------
        temperature_c : `float` | `None`
            Sensor temperature, or `None` if unavailable.
        """
        if self._role == "guide":
            return None
        status = await asyncio.to_thread(self._session.get_status)
        try:
            return float(status.camera_temperature.rstrip("°C"))
        except AttributeError, TypeError, ValueError:
            return None
