"""Device discovery heuristics for INDI devices.

INDI devices are discovered generically (a client only learns a device's name
and properties, never its role), so finding "the telescope" or "the focuser"
among connected devices requires inspecting each device's properties/name for
type-specific signals. This module centralizes those heuristics.
"""


class DeviceDiscovery:
    """Finds and caches role-specific devices on an INDI client.

    Roles include telescope, focuser, camera, and filter wheel.
    """

    def __init__(self, client):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.client = client
        self._cached_filterwheel = None

    def refresh_device_map(self) -> None:
        """Refresh the client's internal device map.

        Rebuilds the map from the client's current device list.
        """
        client = self.client
        if not client.isServerConnected():
            return
        for device in client.getDevices():
            name = device.getDeviceName()
            if name not in client.deviceMap:
                client.deviceMap[name] = client.getDevice(name)

    def find_device_with_property(self, property_name: str):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Search connected devices for one with the specified property.

        Returns
        -------
        device
            The device object with the given property, or `None` if
            none is found.
        """
        client = self.client
        if client.isServerConnected() and not client.deviceMap:
            self.refresh_device_map()
        if not client.isServerConnected() or not client.deviceMap:
            return None

        def has_property(device):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
            return (
                device.getNumber(property_name)
                or device.getText(property_name)
                or device.getSwitch(property_name)
                or device.getLight(property_name)
            )

        for device in client.deviceMap.values():
            if has_property(device):
                return device

        # Not found? Refresh and try again.
        self.refresh_device_map()
        for device in client.deviceMap.values():
            if has_property(device):
                return device
        return None

    def find_telescope(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Heuristic to find the telescope device.

        1. Look for device with EQUATORIAL_EOD_COORD (definitive). 2.
        Look for device with 'Telescope' or 'Mount' in DRIVER_INFO. 3.
        Look for device with 'Telescope' or 'Mount' in name.

        Returns
        -------
        device
            The telescope device, or `None` if none is found.
        """
        client = self.client
        if not client.isServerConnected() or not client.deviceMap:
            return None

        device = self.find_device_with_property("EQUATORIAL_EOD_COORD")
        if device:
            return device
        device = self.find_device_with_property("HORIZONTAL_COORD")
        if device:
            return device

        for device_name, device in client.deviceMap.items():
            driver_info = device.getText("DRIVER_INFO")
            if driver_info:
                for driver_info_element in driver_info:
                    driver_info_text = driver_info_element.text.lower()
                    is_telescope_like = (
                        "telescope" in driver_info_text
                        or "mount" in driver_info_text
                        or "starhopper" in driver_info_text
                    )
                    if is_telescope_like:
                        return device
            if "telescope" in device_name.lower() or "mount" in device_name.lower():
                return device
        return None

    def find_powerbox(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Heuristic to find powerbox.

        1. Look for WEATHER_PARAMETERS. 2. Look for 'Powerbox' or 'Pegasus' in
        DRIVER_INFO/Name.

        Returns
        -------
        device
            The powerbox device, or `None` if none is found.
        """
        client = self.client
        device = self.find_device_with_property("WEATHER_PARAMETERS")
        if device:
            return device
        if not client.isServerConnected() or not client.deviceMap:
            return None
        for device_name, device in client.deviceMap.items():
            if "powerbox" in device_name.lower() or "pegasus" in device_name.lower():
                return device
        return None

    def find_focuser(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Heuristic to find focuser.

        1. Look for ABS_FOCUS_POSITION or REL_FOCUS_POSITION. 2. Prefer
        device with 'Focuser' in name.

        Returns
        -------
        device
            The focuser device, or `None` if none is found.
        """
        client = self.client
        candidates = []
        if client.isServerConnected() and client.deviceMap:
            for device in client.deviceMap.values():
                if device.getNumber("ABS_FOCUS_POSITION") or device.getNumber("REL_FOCUS_POSITION"):
                    candidates.append(device)

        for candidate_device in candidates:
            if "focuser" in candidate_device.getDeviceName().lower():
                return candidate_device
        if candidates:
            return candidates[0]
        return None

    def find_filterwheel(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Heuristic to find filter wheel.

        1. Look for FILTER_SLOT. 2. Prefer 'Filter' or 'Wheel' in name/info.

        Returns
        -------
        device
            The filter wheel device, or `None` if none is found.
        """
        client = self.client
        if self._cached_filterwheel and self._cached_filterwheel.getDeviceName() in client.deviceMap:
            return self._cached_filterwheel

        candidates = []
        if client.isServerConnected() and client.deviceMap:
            for device in client.deviceMap.values():
                if device.getNumber("FILTER_SLOT") or device.getText("FILTER_SLOT"):
                    candidates.append(device)

        for candidate_device in candidates:
            is_filter_wheel_like = (
                "filter" in candidate_device.getDeviceName().lower()
                or "wheel" in candidate_device.getDeviceName().lower()
            )
            if is_filter_wheel_like:
                self._cached_filterwheel = candidate_device
                return candidate_device
        if candidates:
            self._cached_filterwheel = candidates[0]
            return candidates[0]
        return None

    def find_guide_camera(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Heuristic to find guide camera.

        1. Look for device with CCD_EXPOSURE. 2. Prefer device with
        'Guide' in name.

        Returns
        -------
        device
            The guide camera device, or `None` if none is found.
        """
        client = self.client
        if not client.isServerConnected() or not client.deviceMap:
            return None

        candidates = [
            candidate_device
            for candidate_device in client.deviceMap.values()
            if candidate_device.getNumber("CCD_EXPOSURE")
        ]
        if not candidates:
            return None
        for candidate_device in candidates:
            if "guide" in candidate_device.getDeviceName().lower():
                return candidate_device
        return candidates[0]

    def find_main_camera(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Heuristic to find the main imaging camera.

        1. Look for device with CCD_EXPOSURE. 2. Prefer device WITHOUT
        'Guide' in name.

        Returns
        -------
        device
            The main imaging camera device, or `None` if none is
            found.
        """
        client = self.client
        if not client.isServerConnected() or not client.deviceMap:
            return None

        candidates = [
            candidate_device
            for candidate_device in client.deviceMap.values()
            if candidate_device.getNumber("CCD_EXPOSURE")
        ]
        if not candidates:
            return None

        main_candidates = [
            candidate_device
            for candidate_device in candidates
            if "guide" not in candidate_device.getDeviceName().lower()
        ]
        if main_candidates:
            return main_candidates[0]
        return candidates[0]
