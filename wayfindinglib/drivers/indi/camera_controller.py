"""Camera and guiding controller for INDI devices."""

import time
from typing import TYPE_CHECKING, Any

from wayfindinglib.drivers.indi.pyindi_compatibility import PyIndi

if TYPE_CHECKING:
    from wayfindinglib.drivers.indi_interface import IndiInterface

_IMAGE_POLL_SECONDS = 0.1
"""How often to check whether a new image has arrived, in seconds."""


class CameraController:
    """Manages camera exposure and mount pulse-guiding operations via INDI."""

    def __init__(self, client: IndiInterface) -> None:
        self.client = client
        self._arrivals_at_enable = 0

    def pulse_guide(self, telescope: PyIndi.BaseDevice, direction: str, duration_ms: float) -> bool:
        """Send a pulse guide command to the telescope/mount.

        Parameters
        ----------
        telescope
            INDI device handle for the mount.
        direction : str
            One of 'N', 'S', 'W', 'E'.
        duration_ms
            Pulse duration in milliseconds.

        Returns
        -------
        bool
            True if the pulse guide command was sent, False otherwise.
        """
        if not telescope:
            return False

        if direction in ("N", "S"):
            axis = telescope.getNumber("TELESCOPE_TIMED_GUIDE_NS")
            fallback_index = 0 if direction == "N" else 1
        elif direction in ("W", "E"):
            axis = telescope.getNumber("TELESCOPE_TIMED_GUIDE_WE")
            fallback_index = 0 if direction == "W" else 1
        else:
            return False

        if not axis:
            return False

        for i in range(len(axis)):
            if direction in axis[i].getName():
                axis[i].value = duration_ms
                self.client.sendNewNumber(axis)
                return True

        # Fallback: assume a conventional element order.
        axis[fallback_index].value = duration_ms
        self.client.sendNewNumber(axis)
        return True

    def expose(
        self, camera_device: PyIndi.BaseDevice, exposure_seconds: float, gain: float | None = None
    ) -> bool:
        """Start an exposure on the given camera device.

        Optionally sets gain first. Used for both the main imaging camera
        (capture_image) and the guide camera (guide_expose) -- gain is
        typically only set for guide exposures.

        Parameters
        ----------
        camera_device
            INDI device handle for the camera.
        exposure_seconds
            Exposure duration in seconds.
        gain
            Optional gain value to set on CCD_GAIN before exposing.

        Returns
        -------
        bool
            True if the exposure command was sent, False otherwise.
        """
        if not camera_device:
            return False

        if gain is not None:
            gain_property = camera_device.getNumber("CCD_GAIN")
            if gain_property:
                gain_property[0].value = gain
                self.client.sendNewNumber(gain_property)

        ccd_exposure = camera_device.getNumber("CCD_EXPOSURE")
        if ccd_exposure:
            ccd_exposure[0].value = exposure_seconds
            self.client.sendNewNumber(ccd_exposure)
            return True
        return False

    def abort(self, camera_device: PyIndi.BaseDevice) -> bool:
        """Cancel the exposure a camera is taking, if any.

        Turns on the camera's ``CCD_ABORT_EXPOSURE`` switch, the standard
        INDI way to stop an exposure early. The camera drops the frame.
        Sending it while the camera is idle does nothing.

        Parameters
        ----------
        camera_device
            INDI device handle for the camera.

        Returns
        -------
        bool
            True if the abort command was sent, False if there is no
            camera or it has no abort switch.
        """
        if not camera_device:
            return False
        abort_switch = camera_device.getSwitch("CCD_ABORT_EXPOSURE")
        if not abort_switch:
            return False
        abort_switch[0].s = PyIndi.ISS_ON
        self.client.sendNewSwitch(abort_switch)
        return True

    def set_image_reception(self, camera_device: PyIndi.BaseDevice | None, enabled: bool) -> bool:
        """Turn the receiving of a camera's images on or off.

        An INDI client receives no image data until it asks for it with
        ``setBLOBMode``. Turning it on records how many images have
        arrived, so `wait_for_new_image` can tell a new frame from an old
        one. The mode belongs to this client connection, so other clients
        are not affected.

        Parameters
        ----------
        camera_device
            INDI device handle for the camera.
        enabled
            `True` to receive images (``B_ALSO``), `False` to stop
            (``B_NEVER``).

        Returns
        -------
        bool
            `True` if the request was sent, `False` if there is no camera.
        """
        if not camera_device:
            return False
        if enabled:
            self._arrivals_at_enable = getattr(self.client, "image_arrivals", 0)
        mode = PyIndi.B_ALSO if enabled else PyIndi.B_NEVER
        self.client.setBLOBMode(mode, camera_device.getDeviceName(), "CCD1")
        return True

    def wait_for_new_image(
        self, camera_device: PyIndi.BaseDevice | None, timeout_seconds: float
    ) -> bytes | None:
        """Wait for an image that arrived after reception was turned on.

        Parameters
        ----------
        camera_device
            INDI device handle for the camera.
        timeout_seconds
            How long to wait, in seconds.

        Returns
        -------
        bytes or None
            The new frame's raw data, or `None` if no image arrived in time.
        """
        deadline = time.monotonic() + timeout_seconds
        while True:
            if getattr(self.client, "image_arrivals", 0) > self._arrivals_at_enable:
                return self.get_last_image(camera_device)
            if time.monotonic() >= deadline:
                return None
            time.sleep(_IMAGE_POLL_SECONDS)

    def get_last_image(self, camera_device: Any) -> bytes | None:
        """Retrieve the last image frame a camera sent, as raw bytes.

        Parameters
        ----------
        camera_device : `PyIndi.BaseDevice` or `None`
            INDI device handle for the main or the guide camera.

        Returns
        -------
        data : `bytes` or `None`
            The CCD1 or CCD2 frame's raw data (usually a FITS file in
            memory), or `None` if the device or frame is missing. Returned
            as plain `bytes` -- not the INDI BLOB property object itself --
            so the caller never has to know this came from INDI, and so the
            value can cross a process boundary (e.g. to the caller running
            in a different process than this driver).
        """
        if not camera_device:
            return None
        # BaseDevice has no getBlobs() (plural) -- BLOB properties are
        # looked up by name like every other property type
        # (getSwitch/getNumber/getText).
        blob_vector = camera_device.getBLOB("CCD1") or camera_device.getBLOB("CCD2")
        if not blob_vector:
            return None
        return bytes(blob_vector[0].getblobdata())
