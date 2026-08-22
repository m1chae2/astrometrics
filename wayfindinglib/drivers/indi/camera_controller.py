"""Camera and guiding controller for INDI devices."""


class CameraController:
    """Manages camera exposure and mount pulse-guiding operations via INDI."""

    def __init__(self, client):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.client = client

    def pulse_guide(self, telescope, direction: str, duration_ms) -> bool:  # ruff: ignore[missing-type-function-argument]
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

    def expose(self, camera_device, exposure_seconds, gain=None) -> bool:  # ruff: ignore[missing-type-function-argument]
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

    def get_guide_image(self, guide_camera_device):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Retrieve the last image blob from the guide camera.

        Parameters
        ----------
        guide_camera_device
            INDI device handle for the guide camera.

        Returns
        -------
        The CCD1 or CCD2 BLOB property, or None if the device is missing.
        """
        if not guide_camera_device:
            return None
        # BaseDevice has no getBlobs() (plural) -- BLOB properties are
        # looked up by name like every other property type
        # (getSwitch/getNumber/getText).
        return guide_camera_device.getBLOB("CCD1") or guide_camera_device.getBLOB("CCD2")
