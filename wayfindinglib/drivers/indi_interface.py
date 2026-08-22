"""Description: INDI Driver Interface for Astrometrics.

Provides connectivity to INDI server and handles mounts, focusers, filter
wheels, and weather/environmental telemetry.
"""

# Note to use this you must first install PyIndi
# https://github.com/indilib/pyindi-client/tree/master
#   sudo apt-add-repository ppa:mutlaqja/ppa
#   sudo apt-get -y install python3-indi-client

import threading
import time
from typing import Any

import astropy.units as u
from astropy.coordinates import EarthLocation
from astropy.time import Time
from pydantic import BaseModel, ConfigDict, Field

from .indi import coordinate_utils
from .indi.camera_controller import CameraController
from .indi.connection_manager import ConnectionManager
from .indi.device_discovery import DeviceDiscovery
from .indi.filter_wheel_controller import FilterWheelController
from .indi.focuser_controller import FocuserController
from .indi.mount_controller import MountController
from .indi.pyindi_compatibility import PyIndi

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "IndiClient",
    "IndiInterface",
    "TelescopeStatus",
]


class TelescopeStatus(BaseModel):
    """Represents the current status and telemetry of the telescope.

    Uses aliases to provide camelCase names for the frontend.
    """

    model_config = ConfigDict(populate_by_name=True)

    ra: str = Field(..., alias="ra")
    dec: str = Field(..., alias="dec")
    altitude: str = Field(..., alias="altitude")
    azimuth: str = Field(..., alias="azimuth")
    temperature: str = Field(..., alias="temperature")
    humidity: str = Field(..., alias="humidity")
    tracking_status: str = Field(..., alias="trackingStatus")
    connection_status: str = Field("Disconnected", alias="connectionStatus")
    focuser_position: int = Field(0, alias="focuserPosition")
    filter: str = Field("L", alias="filter")
    guiding_history: list = Field([], alias="guidingHistory")


class IndiClient(PyIndi.BaseClient):
    """Define properties and methods for an INDI server connection.

    Overrides `PyIndi.BaseClient` callbacks with no-op implementations
    to avoid a C++ proxy type mismatch (see individual methods).
    """

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the INDI client."""
        super().__init__()

    def newDevice(self, device):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle a new INDI device.

        Overridden to avoid C++ proxy type mismatch.
        """
        pass

    def removeDevice(self, device):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle a removed INDI device.

        Overridden to avoid C++ proxy type mismatch.
        """
        pass

    def newProperty(self, property):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle a new INDI property.

        Overridden to avoid C++ proxy type mismatch.
        """
        pass

    def removeProperty(self, property):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle a removed INDI property.

        Overridden to avoid C++ proxy type mismatch.
        """
        pass

    def updateProperty(self, property):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle an updated INDI property.

        Overridden to avoid C++ proxy type mismatch.
        """
        pass

    def newMessage(self, device, id):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle a new INDI message.

        Overridden to avoid C++ proxy type mismatch.
        """
        pass

    def newLight(self, property):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle a new INDI light property.

        Overridden to avoid C++ proxy type mismatch.
        """
        pass

    def newNumber(self, property):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle a new INDI number property.

        Overridden to avoid C++ proxy type mismatch.
        """
        pass

    def newSwitch(self, property):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle a new INDI switch property.

        Overridden to avoid C++ proxy type mismatch.
        """
        pass

    def newText(self, property):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle a new INDI text property.

        Overridden to avoid C++ proxy type mismatch.
        """
        pass


class IndiInterface(IndiClient):
    """Interface Astrometrics with an INDI server."""

    def __init__(self, config):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize the interface to an INDI server.

        Parameters
        ----------
        config
            Configuration object providing telescope hostname, allowed
            commands, and INDI host/port, injected by the caller.

        """
        super().__init__()
        self.config = config
        self.allow_commands = False  # Default to Safe Mode
        self._sync_config()
        self.device_map = {}
        self._has_initialized_defaults = False

        # Thread-safe external pulse event queue

        self._external_pulses_lock = threading.Lock()
        self._external_pulses = []

        # Modular Controllers
        self.mount_controller = MountController(self)
        self.focuser_controller = FocuserController(self)
        self.filter_wheel_controller = FilterWheelController(self)
        self.camera_controller = CameraController(self)
        self.connection_manager = ConnectionManager(config.get_indi_host(), config.get_indi_port())
        self.device_discovery = DeviceDiscovery(self)

        self._reset_status()
        # self._ensure_connection() is removed to prevent blocking startup.
        # Connection will be established lazily on first use.

    def _sync_config(self):  # ruff: ignore[missing-return-type-private-function]
        """Sync local state (hostname, allow_commands) from config."""
        self.hostname = self.config.get_telescope_hostname()
        self.allow_commands = self.config.get_allow_commands()
        self.setServer(self.hostname, 7624)
        if hasattr(self, "connection_manager"):
            self.connection_manager.hostname = self.hostname

    def reload_connection(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Force a reload of the configuration and reconnection.

        Useful when hostname changes.
        """
        print("Reloading INDI connection...")
        # Force refresh config in the injected object (it might have
        # been updated via API). But usually the config object stays
        # the same, its state changes.
        self._sync_config()

        # If we are already connected, we might need to disconnect
        # first if host changed
        if self.isServerConnected():
            current_host = self.getHost()
            if current_host != self.hostname:
                print(f"Hostname changed from {current_host} to {self.hostname}. Reconnecting...")
                self.disconnectServer()
                # self.setServer is called in _load_config

                self._reset_status()
                # Allow slight pause?
                time.sleep(0.5)
                self.connect_to_server()
            else:
                print("Hostname unchanged.")
                # Optional: Verify connection anyway?
                pass
        else:
            # Not connected, try connecting with new config
            self.connect_to_server()

    def connect_to_server(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Connect to an INDI server.

        Returns
        -------
        client : `IndiInterface` or `None`
            This instance if the connection succeeded, `None` if the
            server could not be reached.
        """
        # If we are not connected, try to sync config in case
        # hostname changed
        if not self.isServerConnected():
            self._sync_config()

        if not self.connectServer():
            # Only print if we haven't already logged this recently
            print(f"No indiserver running on {self.getHost()}:{self.getPort()}")
            self._reset_status()
            return None

        # Waiting for devices to be discovered
        # Loop for up to 2 seconds to allow devices to show up
        for _ in range(5):  # Reduced from 10 to 5
            if self.getDevices():
                break
            time.sleep(0.2)

        self.deviceMap = {}
        device_list = self.getDevices()
        for device in device_list:
            self.deviceMap[device.getDeviceName()] = self.getDevice(device.getDeviceName())

        return self

    def _ensure_connection(self):  # ruff: ignore[missing-return-type-private-function]
        """Lazily ensures that we are connected to the INDI server."""
        now = time.time()
        connection_manager = self.connection_manager
        cooldown_elapsed = (
            now - connection_manager.last_connection_attempt
        ) >= connection_manager.connection_cooldown

        if not self.isServerConnected():
            if cooldown_elapsed:
                self._reconnect(now)
        else:
            # Check physical connection to port AND application
            # responsiveness. Simple port check gave false positives.
            # We must attempt to talk to the server. isServerConnected()
            # is re-checked here too: the INDI client runs a background
            # thread that can flip it False concurrently with the
            # responsiveness probe above.
            server_ok = self._is_server_responsive() and self.isServerConnected()
            if not server_ok and cooldown_elapsed:
                self._reconnect(now)

        # Re-check device map population logic
        if self.isServerConnected() and (not hasattr(self, "deviceMap") or not self.deviceMap):
            self.connect_to_server()

    def _reconnect(self, now: float) -> None:
        """Tear down (if needed) and re-establish the connection.

        Only reconnect() should update
        connection_manager.last_connection_attempt, so that both the
        "not yet connected" and "connected but unresponsive" paths in
        _ensure_connection share one debounce timer.
        """
        self.connection_manager.last_connection_attempt = now
        if self.isServerConnected():
            self.disconnectServer()
        self.deviceMap = {}
        self._reset_status()
        self.connect_to_server()

    def _is_server_responsive(self):  # ruff: ignore[missing-return-type-private-function]
        """Check if the INDI server is reachable and responsive.

        Sends a handshake and delegates to ConnectionManager
        (drivers/indi/connection_manager.py), which caches the result
        for 5 seconds to prevent blocking frequent status polls.

        Returns
        -------
        responsive : `bool`
            `True` if the server responded to the handshake.
        """
        return self.connection_manager.is_server_responsive()

    def serverDisconnected(self, code):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle the server-disconnected callback."""
        print(f"INDI Server disconnected (code {code})")
        self._reset_status()

    def _reset_status(self):  # ruff: ignore[missing-return-type-private-function]
        """Reset the status dictionary to default/disconnected state."""
        self.status = {
            "CONNECTION_STATUS": "Disconnected",
            "TRACKING_STATUS": "Unknown",
            "RA": "Unknown",
            "DEC": "Unknown",
            "ALTITUDE": "Unknown",
            "AZIMUTH": "Unknown",
            "TEMPERATURE": "-",
            "HUMIDITY": "-",
            "FILTER": "Unknown",
        }

    def connect_to_telescope(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Connect to a telescope and grab key parameters.

        Returns
        -------
        device : `PyIndi.BaseDevice` or `None`
            The telescope device if found, `None` if the server is
            unreachable.
        """
        self._ensure_connection()
        if not self.isServerConnected():
            self.status["CONNECTION_STATUS"] = "Disconnected"
            return None

        if not self.isServerConnected():
            self.status["CONNECTION_STATUS"] = "Disconnected"
            return None

        device_telescope = self._find_telescope_device()
        # Note: We iterate all devices to ensure everything is
        # connected (Filter wheels, Focusers, etc.)
        if self.deviceMap:
            for device_name, device in self.deviceMap.items():
                try:
                    connect_switch = device.getSwitch("CONNECTION")
                    if connect_switch and connect_switch[0].getState() != PyIndi.ISS_ON:
                        connect_switch[0].setState(PyIndi.ISS_ON)
                        self.sendNewSwitch(connect_switch)
                except Exception as connect_error:
                    # Ignore connection errors for individual devices,
                    # keep trying others
                    print(f"Error connecting device '{device_name}': {connect_error}")

        connection_status = "Disconnected"
        tracking_status = "Not Tracking"

        # Handle Telescope Status
        if device_telescope:
            telescope_connect = device_telescope.getSwitch("CONNECTION")

            # Re-check state
            if telescope_connect and telescope_connect[0].getState() == PyIndi.ISS_ON:
                connection_status = "Connected"

                # Double check capability after connection
                if not (
                    device_telescope.getNumber("EQUATORIAL_EOD_COORD")
                    or device_telescope.getNumber("HORIZONTAL_COORD")
                ):
                    # Could allow pass if we are sure it is the
                    # telescope but just doesn't have coords yet?
                    # But user requested stricter check.
                    # However, if we just connected, properties might
                    # lag slightly.
                    # For now, let's strictly require it for
                    # "Connected" status.
                    connection_status = "Disconnected"

                # Get telescope status
                telescope_track_state = device_telescope.getSwitch("TELESCOPE_TRACK_STATE")
                parking_status = device_telescope.getSwitch("TELESCOPE_PARK")

                if parking_status and parking_status[0].getState() == PyIndi.ISS_ON:
                    tracking_status = "Parked"
                elif telescope_track_state and telescope_track_state[0].getState() == PyIndi.ISS_ON:
                    tracking_status = "Tracking"
                else:
                    tracking_status = "Idle"
            else:
                connection_status = (
                    "Disconnecting..."
                    if telescope_connect[0].getState() == PyIndi.ISS_OFF
                    else "Connecting..."
                )

            # Enforce default tracking off on first connection
            if connection_status == "Connected" and not self._has_initialized_defaults:
                self._enforce_default_tracking_off(device_telescope)
                self._has_initialized_defaults = True

        # Save statuses to status dictionary
        self.status["TRACKING_STATUS"] = tracking_status
        self.status["CONNECTION_STATUS"] = connection_status

        return device_telescope

    def _find_telescope_device(self):  # ruff: ignore[missing-return-type-private-function]
        """Heuristic to find the telescope device.

        Returns
        -------
        device : `PyIndi.BaseDevice` or `None`
            The telescope device, or `None` if none is found.
        """
        return self.device_discovery.find_telescope()

    def _find_powerbox_device(self):  # ruff: ignore[missing-return-type-private-function]
        """Heuristic to find powerbox.

        Returns
        -------
        device : `PyIndi.BaseDevice` or `None`
            The powerbox device, or `None` if none is found.
        """
        return self.device_discovery.find_powerbox()

    def _find_focuser_device(self):  # ruff: ignore[missing-return-type-private-function]
        """Heuristic to find focuser.

        Returns
        -------
        device : `PyIndi.BaseDevice` or `None`
            The focuser device, or `None` if none is found.
        """
        return self.device_discovery.find_focuser()

    def _find_filterwheel_device(self):  # ruff: ignore[missing-return-type-private-function]
        """Heuristic to find filter wheel.

        Returns
        -------
        device : `PyIndi.BaseDevice` or `None`
            The filter wheel device, or `None` if none is found.
        """
        return self.device_discovery.find_filterwheel()

    def get_filter_names(self) -> list[str]:
        """Return the available filter names from the filter wheel.

        Returns
        -------
        names : `list` [`str`]
            The configured filter names, or an empty list if the
            filter wheel is unavailable.
        """
        return self.filter_wheel_controller.get_names(self._find_filterwheel_device())

    def resolve_filter_name(self, filter_name: str) -> str | None:
        """Resolve a target filter name to the device's actual name.

        Uses exact and prefix/fuzzy matches to bridge telescope
        control requests.

        Returns
        -------
        resolved_name : `str` or `None`
            The matching device filter name, or `None` if no match
            was found.
        """
        return self.filter_wheel_controller.resolve_name(self._find_filterwheel_device(), filter_name)

    def _find_guide_camera_device(self):  # ruff: ignore[missing-return-type-private-function]
        """Heuristic to find guide camera.

        Returns
        -------
        device : `PyIndi.BaseDevice` or `None`
            The guide camera device, or `None` if none is found.
        """
        return self.device_discovery.find_guide_camera()

    def _refresh_device_map(self):  # ruff: ignore[missing-return-type-private-function]
        """Refresh the internal device map from the client's list."""
        self.device_discovery.refresh_device_map()

    def _find_device_with_property(self, property_name):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Search connected devices for one with the given property.

        Returns
        -------
        device : `PyIndi.BaseDevice` or `None`
            The matching device object, or `None` if none is found.
        """
        return self.device_discovery.find_device_with_property(property_name)

    def coordinate_dms_to_decimal(self, coordinate):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Convert a sexagesimal (D:M:S) coordinate to decimal.

        Returns
        -------
        decimal_value : `float`
            The coordinate expressed in decimal form.
        """
        return coordinate_utils.coordinate_dms_to_decimal(coordinate)

    def coordinate_decimal_to_dms(self, coordinate):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Convert a decimal coordinate to sexagesimal (D:M:S).

        Returns
        -------
        dms_value : `str`
            The coordinate expressed in D:M:S form.
        """
        return coordinate_utils.coordinate_decimal_to_dms(coordinate)

    def get_environmentals(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Retrieve environmental telemetry from the powerbox/weather.

        Fetches temperature and humidity and updates the status
        dictionary for observatory display.
        """
        if self.status.get("CONNECTION_STATUS") == "Connected":
            powerbox = self._find_powerbox_device()
            if powerbox:
                weather_parameters = powerbox.getNumber("WEATHER_PARAMETERS")
                if weather_parameters and len(weather_parameters) >= 2:
                    self.status["TEMPERATURE"] = f"{weather_parameters[0].value:.1f}°C"
                    self.status["HUMIDITY"] = f"{weather_parameters[1].value:.1f}%"
                    return

        if "TEMPERATURE" not in self.status or self.status["TEMPERATURE"] == "Unknown":
            self.status["TEMPERATURE"] = "-"
        if "HUMIDITY" not in self.status or self.status["HUMIDITY"] == "Unknown":
            self.status["HUMIDITY"] = "-"

    def _refresh_filter_status(self):  # ruff: ignore[missing-return-type-private-function]
        """Poll the filter wheel for the current slot and update status."""
        if self.status.get("CONNECTION_STATUS") != "Connected":
            return

        device = self._find_filterwheel_device()
        if not device:
            return

        current = self.filter_wheel_controller.get_current_filter(device)
        if current is not None:
            self.status["FILTER"] = current

    def get_coordinates(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Get the coordinates the telescope is pointing at."""
        # Connect to telescope device
        telescope = self.connect_to_telescope()

        if self.status.get("CONNECTION_STATUS") == "Connected":
            # Get coordinates and save to status dictionary
            telescope_coordinates = telescope.getNumber("EQUATORIAL_EOD_COORD")
            horizontal_coordinates = telescope.getNumber("HORIZONTAL_COORD")

            if telescope_coordinates:
                self.status["RA"] = self.coordinate_decimal_to_dms(telescope_coordinates[0].value)
                self.status["DEC"] = self.coordinate_decimal_to_dms(telescope_coordinates[1].value)

            if horizontal_coordinates:
                self.status["ALTITUDE"] = self.coordinate_decimal_to_dms(horizontal_coordinates[0].value)
                self.status["AZIMUTH"] = self.coordinate_decimal_to_dms(horizontal_coordinates[1].value)
            else:
                # If we don't have horizontal coords (e.g. some
                # simulators), calculate them if possible
                try:
                    self._calculate_horizontal_coordinates(telescope, telescope_coordinates)
                except Exception as coord_error:
                    print(f"Error calculating horizontal coordinates: {coord_error}")

        if "RA" not in self.status:
            self.status["RA"] = "Unknown"
        if "DEC" not in self.status:
            self.status["DEC"] = "Unknown"
        if "ALTITUDE" not in self.status:
            self.status["ALTITUDE"] = "Unknown"
        if "AZIMUTH" not in self.status:
            self.status["AZIMUTH"] = "Unknown"

    def _calculate_horizontal_coordinates(self, telescope_device, equatorial_coords):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Calculate Alt/Az from RA/Dec, Location, and Time if valid.

        Updates self.status.
        """
        if not equatorial_coords:
            return

        # 1. Get Location
        geographic_coordinates = telescope_device.getNumber("GEOGRAPHIC_COORD")
        if not geographic_coordinates:
            return

        latitude = geographic_coordinates[0].value
        longitude = geographic_coordinates[1].value
        elevation = geographic_coordinates[2].value if len(geographic_coordinates) > 2 else 0

        # 2. Get Time
        # For live status updates, system time is more reliable for
        # coordinate projection than the potentially stale TIME_UTC
        # property from the mount.
        observation_time = Time.now()

        # 3. Calculate
        location = EarthLocation(lat=latitude * u.deg, lon=longitude * u.deg, height=elevation * u.m)
        ra = equatorial_coords[0].value  # Hours
        dec = equatorial_coords[1].value  # Degrees

        from wayfindinglib.skylib.coordinate_operations import compute_altaz

        # RA in INDI is typically Hours; compute_altaz's contract is degrees.
        alt_deg, az_deg = compute_altaz(ra * 15.0, dec, location, observation_time)

        self.status["ALTITUDE"] = self.coordinate_decimal_to_dms(alt_deg)
        self.status["AZIMUTH"] = self.coordinate_decimal_to_dms(az_deg)

    def _validate_altitude_limits(self, telescope, ra: float, dec: float):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return self.mount_controller.validate_altitude_limits(telescope, ra, dec)

    def get_observer_location(self) -> tuple[float, float, float] | None:
        """Retrieve observer geographic coordinates from the telescope.

        Returns
        -------
        Optional[Tuple[float, float, float]]
            (latitude, longitude, elevation) if connected and
            available, else None.
        """
        telescope = self._find_telescope_device()
        if not telescope or self.status.get("CONNECTION_STATUS") != "Connected":
            return None

        geographic_coordinates = telescope.getNumber("GEOGRAPHIC_COORD")
        if not geographic_coordinates:
            return None

        latitude = geographic_coordinates[0].value
        longitude = geographic_coordinates[1].value
        elevation = geographic_coordinates[2].value if len(geographic_coordinates) > 2 else 0.0
        return latitude, longitude, elevation

    def get_status(self) -> TelescopeStatus:
        """Get current telescope status.

        Returns
        -------
        status : `TelescopeStatus`
            The current telescope coordinates, environmentals,
            tracking/connection state, and filter/focuser position.
        """
        # Refresh Data from INDI
        self.get_coordinates()
        self.get_environmentals()
        self._refresh_filter_status()

        # Check for disconnection signal from get_coordinates (which
        # calls connect_to_telescope -> _ensure_connection). If
        # get_coordinates failed to populate 'RA' properly or
        # connection_status indicates disconnect:
        if self.status.get("CONNECTION_STATUS") == "Disconnected":
            self._reset_status()  # Force full reset to ensure defaults

        return TelescopeStatus(
            ra=self.status.get("RA", "-"),
            dec=self.status.get("DEC", "-"),
            altitude=self.status.get("ALTITUDE", "-"),
            azimuth=self.status.get("AZIMUTH", "-"),
            temperature=self.status.get("TEMPERATURE", "-"),
            humidity=self.status.get("HUMIDITY", "-"),
            connection_status=self.status.get("CONNECTION_STATUS", "Disconnected"),
            tracking_status=self.status.get("TRACKING_STATUS", "Unknown"),
            focuser_position=self.get_focuser_position(),
            filter=self.status.get("FILTER", "L"),
            guiding_history=[],  # Handled by GuidingService
        )

    def set_filterwheel_position(self, filter_name):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Set the active filter by slot number or text name.

        Handles fuzzy matching for standard filters.

        Returns
        -------
        success : `bool`
            `True` if the filter position was set successfully.
        """
        device = self._find_filterwheel_device()
        if not device:
            return False

        success = self.filter_wheel_controller.set_position(device, filter_name)
        if success:
            self.status["FILTER"] = filter_name
        return success

    def slew(self, ra, dec):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Slew the telescope to the given RA/Dec coordinates.

        Returns
        -------
        success : `bool`
            `True` if the slew command was accepted.
        """
        return self.mount_controller.slew(self.connect_to_telescope(), ra, dec)

    def park(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Park the telescope mount.

        Returns
        -------
        success : `bool`
            `True` if the park command was accepted.
        """
        return self.mount_controller.park(self.connect_to_telescope())

    def abort_motion(self) -> bool:
        """Abort all telescope mount motion immediately.

        Returns
        -------
        success : `bool`
            `True` if the abort command was accepted.
        """
        return self.mount_controller.abort(self.connect_to_telescope())

    def _should_block_command(self, property_item: Any) -> bool:
        """Check if commands should be blocked based on Safe Mode.

        REQ: SR-1.5: Safe Mode implementation.

        Returns
        -------
        should_block : `bool`
            `False` if the command is a connection command or Safe
            Mode is disabled (commands allowed).

        Raises
        ------
        AstrometryHardwareError
            If Safe Mode is enabled and the command is not a
            connection-related command.
        """
        if self.allow_commands:
            return False

        # Always allow connection-related commands
        property_name = ""
        if hasattr(property_item, "getName"):
            property_name = property_item.getName()
        elif hasattr(property_item, "name"):
            property_name = property_item.name

        if property_name in ["CONNECTION", "CONNECT", "DISCONNECT"]:
            return False

        from wayfindinglib import AstrometryHardwareError

        error_message = (
            f"Command to {property_name} blocked by Safe Mode. Please enable hardware control in Settings."
        )
        print(f"BLOCKED: {error_message}")
        raise AstrometryHardwareError(error_message)

    def sendNewText(self, property_item: Any) -> None:
        """REQ: SR-1.

        2, SR-1.5.
        """
        if self._should_block_command(property_item):
            return
        super().sendNewText(property_item)

    def sendNewNumber(self, property_item: Any) -> None:
        """REQ: SR-1.

        2, SR-1.5.
        """
        if self._should_block_command(property_item):
            return
        super().sendNewNumber(property_item)

    def newNumber(self, number_vector: Any) -> None:
        """Handle a number property update from the INDI server.

        Passively intercepts timed guide pulse commands sent by
        external guiders like KStars/Ekos/PHD2.
        """
        try:
            property_name = number_vector.getName()
            if property_name in ("TELESCOPE_TIMED_GUIDE_NS", "TELESCOPE_TIMED_GUIDE_WE"):
                pulse_north = 0.0
                pulse_south = 0.0
                pulse_west = 0.0
                pulse_east = 0.0

                for guide_element in number_vector:
                    element_name = guide_element.getName()
                    pulse_value = guide_element.getValue()
                    if element_name == "TIMED_GUIDE_N":
                        pulse_north = pulse_value
                    elif element_name == "TIMED_GUIDE_S":
                        pulse_south = pulse_value
                    elif element_name == "TIMED_GUIDE_W":
                        pulse_west = pulse_value
                    elif element_name == "TIMED_GUIDE_E":
                        pulse_east = pulse_value

                # Timed guide commands send positive duration, which
                # counts down to 0. We only record the initial
                # commanding values (> 0) to avoid countdown duplicates
                if pulse_north > 0 or pulse_south > 0 or pulse_west > 0 or pulse_east > 0:
                    import time

                    # Dict keys below are a data contract with
                    # backend/services/observatory/guiding_service.py
                    # -- do not rename them.
                    with self._external_pulses_lock:
                        self._external_pulses.append({
                            "time": time.time(),
                            "pulse_n": pulse_north,
                            "pulse_s": pulse_south,
                            "pulse_w": pulse_west,
                            "pulse_e": pulse_east,
                        })
        except Exception as pulse_error:
            print(f"Error recording external guide pulse: {pulse_error}")

    def sendNewSwitch(self, property_item: Any) -> None:
        """REQ: SR-1.

        2, SR-1.5.
        """
        if self._should_block_command(property_item):
            return
        super().sendNewSwitch(property_item)

    def get_device_properties(self, device_name):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Retrieve all properties for a device in a structured format.

        Returns
        -------
        properties : `dict`
            Mapping of property name to a dict describing its label,
            group, permission, type, state, and elements. Empty if
            the server is disconnected or the device is unknown.
        """
        if not self.isServerConnected():
            return {}

        device = self.deviceMap.get(device_name)
        if not device:
            return {}

        result = {}
        # getProperties() might return a list of property objects
        for property_item in device.getProperties():
            try:
                property_name = property_item.getName()
                property_type = property_item.getType()

                property_info = {
                    "name": property_name,
                    "label": (
                        property_item.getLabel() if hasattr(property_item, "getLabel") else property_name
                    ),
                    "group": (
                        property_item.getGroupName() if hasattr(property_item, "getGroupName") else "Main"
                    ),
                    "perm": (
                        "ro"
                        if (
                            hasattr(property_item, "getPermission")
                            and property_item.getPermission() == PyIndi.IP_RO
                        )
                        else "rw"
                    ),
                    "state": "Idle",
                }

                # Extract elements based on type
                elements = {}
                try:
                    if property_type == PyIndi.INDI_NUMBER:
                        property_info["type"] = "Number"
                        vector = device.getNumber(property_name)
                        if vector:
                            for i in range(len(vector)):
                                elements[vector[i].name] = vector[i].value
                    elif property_type == PyIndi.INDI_SWITCH:
                        property_info["type"] = "Switch"
                        vector = device.getSwitch(property_name)
                        if vector:
                            for i in range(len(vector)):
                                elements[vector[i].name] = "On" if vector[i].s == PyIndi.ISS_ON else "Off"
                    elif property_type == PyIndi.INDI_TEXT:
                        property_info["type"] = "Text"
                        vector = device.getText(property_name)
                        if vector:
                            for i in range(len(vector)):
                                elements[vector[i].name] = vector[i].text
                    elif property_type == PyIndi.INDI_LIGHT:
                        property_info["type"] = "Light"
                        vector = device.getLight(property_name)
                        if vector:
                            for i in range(len(vector)):
                                state = vector[i].s
                                state_str = "Idle"
                                if state == PyIndi.IPS_OK:
                                    state_str = "Ok"
                                elif state == PyIndi.IPS_BUSY:
                                    state_str = "Busy"
                                elif state == PyIndi.IPS_ALERT:
                                    state_str = "Alert"
                                elements[vector[i].name] = state_str
                except Exception as element_error:
                    property_info["error"] = str(element_error)

                property_info["elements"] = elements
                result[property_name] = property_info
            except Exception as property_error:
                print(f"Error processing property: {property_error}")

        return result

    def set_property(self, device_name, property_name, element_name, value):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Set a specific element of an INDI property.

        Returns
        -------
        success : `bool`
            `True` if the element was found and the update was sent.
        """
        device = self.deviceMap.get(device_name)
        if not device:
            return False

        target_property = device.getProperty(property_name)
        if not target_property:
            return False

        property_type = target_property.getType()
        try:
            if property_type == PyIndi.INDI_NUMBER:
                property_vector = device.getNumber(property_name)
                for i in range(len(property_vector)):
                    if property_vector[i].name == element_name:
                        property_vector[i].value = float(value)
                        self.sendNewNumber(property_vector)
                        return True
            elif property_type == PyIndi.INDI_TEXT:
                property_vector = device.getText(property_name)
                for i in range(len(property_vector)):
                    if property_vector[i].name == element_name:
                        property_vector[i].text = value
                        self.sendNewText(property_vector)
                        return True
            elif property_type == PyIndi.INDI_SWITCH:
                property_vector = device.getSwitch(property_name)
                found = False
                for i in range(len(property_vector)):
                    if property_vector[i].name == element_name:
                        is_on = value.lower() in ["on", "true", "1"]
                        property_vector[i].s = PyIndi.ISS_ON if is_on else PyIndi.ISS_OFF
                        found = True
                    # If it's a "One of Many" switch, we might need to
                    # turn others off, but usually sendNewSwitch
                    # handles the vector state we send.
                if found:
                    self.sendNewSwitch(property_vector)
                    return True
        except Exception as property_error:
            print(f"Error setting property {property_name}: {property_error}")

        return False

    def _enforce_default_tracking_off(self, telescope_device):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Force tracking OFF.

        Does not call connect_to_telescope to avoid recursion.
        Best-effort: this runs automatically on first connection, so
        a blocked command (e.g. Safe Mode) or any other failure here
        must not break the connection/status flow that called it.
        """
        try:
            self.mount_controller.set_tracking(telescope_device, False)
        except Exception as tracking_error:
            print(f"Error enforcing default tracking: {tracking_error}")

    def unpark(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Unparks the telescope.

        Returns
        -------
        success : `bool`
            `True` if the unpark command was accepted.
        """
        telescope = self.connect_to_telescope()
        if not telescope or self.status.get("CONNECTION_STATUS") != "Connected":
            return False
        return self.mount_controller.unpark(telescope)

    def set_tracking(self, enabled):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Set the tracking state.

        Returns
        -------
        success : `bool`
            `True` if the tracking command was accepted.
        """
        telescope = self.connect_to_telescope()
        if not telescope or self.status.get("CONNECTION_STATUS") != "Connected":
            return False
        return self.mount_controller.set_tracking(telescope, enabled)

    def _set_coord_mode(self, telescope_device, mode="TRACK"):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Set the ON_COORD_SET switch (TRACK / SLEW / SYNC).

        mode: 'TRACK', 'SLEW', 'SYNC'

        Returns
        -------
        success : `bool`
            `True` if the coordinate mode switch was set.
        """
        return self.mount_controller._set_coord_mode(telescope_device, mode)

    def sync_coordinates(self, ra, dec):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Sync the telescope to the specified coordinates.

        Uses ON_COORD_SET = SYNC.

        Returns
        -------
        success : `bool`
            `True` if the sync command was accepted.
        """
        telescope = self.connect_to_telescope()
        if not telescope:
            return False
        return self.mount_controller.sync_coordinates(telescope, ra, dec)

    def get_focuser_position(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Get the current focuser absolute position.

        Returns
        -------
        position : `int`
            The absolute focuser position, or ``0`` if no focuser
            device is found.
        """
        device = self._find_focuser_device()
        if not device:
            return 0
        return self.focuser_controller.get_position(device)

    def focus_move(self, steps):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Move the focuser.

        Positive or Negative steps. If device supports
        REL_FOCUS_POSITION, use that. Else if ABS_FOCUS_POSITION,
        read + add. Else if FOCUS_MOTION (in/out) + FOCUS_TIMER?
        (Simulators often use REL or ABS).

        Returns
        -------
        success : `bool`
            `True` if the focuser move command was accepted.
        """
        device = self._find_focuser_device()
        if not device:
            return False
        return self.focuser_controller.move_relative(device, steps)

    def pulse_guide(self, direction, duration_ms):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Send a pulse guide command to the telescope/mount.

        direction: 'N', 'S', 'W', 'E' duration_ms: Duration in milliseconds

        Returns
        -------
        success : `bool`
            `True` if the pulse guide command was accepted.
        """
        device = self.connect_to_telescope()
        if not device:
            return False
        return self.camera_controller.pulse_guide(device, direction, duration_ms)

    def guide_expose(self, exposure_seconds, gain=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Take an exposure with the guide camera.

        REQ: BKD-1.3: The backend SHALL provide a generic interface
        for camera control (Exposure, Gain, Cooling). Returns the
        blob or data if available, or just initiates it. For now,
        initiates.

        Returns
        -------
        result
            The exposure result (blob/data) from the guide camera
            controller, whatever form it returns for the current
            exposure state.
        """
        device = self._find_guide_camera_device()
        return self.camera_controller.expose(device, exposure_seconds, gain=gain)

    def get_guide_image(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Retrieve the last image blob from the guide camera.

        Returns
        -------
        image
            The last guide camera image blob, as returned by the
            camera controller.
        """
        device = self._find_guide_camera_device()
        return self.camera_controller.get_guide_image(device)

    def _find_main_camera_device(self):  # ruff: ignore[missing-return-type-private-function]
        """Heuristic to find the main imaging camera.

        Returns
        -------
        device : `PyIndi.BaseDevice` or `None`
            The main camera device, or `None` if none is found.
        """
        return self.device_discovery.find_main_camera()

    def capture_image(self, exposure_seconds):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Take an exposure with the main camera.

        Returns
        -------
        result
            The exposure result from the camera controller.
        """
        device = self._find_main_camera_device()
        return self.camera_controller.expose(device, exposure_seconds)

    def move(self, direction, start=True):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Start or stops manual movement in a direction.

        direction: 'N', 'S', 'E', 'W', 'NW', 'NE', 'SW', 'SE' or 'STOP'

        Returns
        -------
        success : `bool`
            `True` if the move command was accepted.
        """
        device = self.connect_to_telescope()
        if not device:
            return False
        return self.mount_controller.move(device, direction, start)
