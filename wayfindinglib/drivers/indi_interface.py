"""Description: INDI Driver Interface for Astrometrics.

Provides connectivity to INDI server and handles mounts, focusers, filter
wheels, and weather/environmental telemetry.
"""

# Note to use this you must first install PyIndi
# https://github.com/indilib/pyindi-client/tree/master
#   sudo apt-add-repository ppa:mutlaqja/ppa
#   sudo apt-get -y install python3-indi-client

import logging
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

logger = logging.getLogger(__name__)


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
    camera_temperature: str = Field("-", alias="cameraTemperature")
    camera_status: str = Field("Idle", alias="cameraStatus")
    target_name: str | None = Field(default=None, alias="targetName")


# Maps an INDI property type constant to the name of the per-type
# callback `IndiClient.updateProperty` should hand it to, and the wrapper
# class that casts the generic property into the specific one that
# callback expects. Built once at import time, and empty when PyIndi
# isn't installed (the stub in pyindi_compatibility carries none of these
# names), in which case updateProperty simply has nothing to dispatch.
_INDI_PROPERTY_TYPE_DISPATCH: list[tuple[Any, str, Any]] = [
    (getattr(PyIndi, type_constant_name, None), callback_name, getattr(PyIndi, wrapper_name, None))
    for type_constant_name, callback_name, wrapper_name in (
        ("INDI_NUMBER", "newNumber", "PropertyNumber"),
        ("INDI_SWITCH", "newSwitch", "PropertySwitch"),
        ("INDI_TEXT", "newText", "PropertyText"),
        ("INDI_LIGHT", "newLight", "PropertyLight"),
    )
    if getattr(PyIndi, type_constant_name, None) is not None
    and getattr(PyIndi, wrapper_name, None) is not None
]


class IndiClient(PyIndi.BaseClient):
    """Define properties and methods for an INDI server connection.

    Defines the per-property-type callbacks (`newNumber`, `newSwitch`,
    `newText`, `newLight`) as no-op hooks for subclasses to override,
    and dispatches to them from `updateProperty`.

    INDI Core 2.0 removed those four callbacks from `BaseClient` and
    replaced them with a single `updateProperty`, so on a 2.x client
    library nothing calls them unless `updateProperty` does. Keeping
    them as the extension point (rather than making subclasses override
    `updateProperty` and re-derive the property type themselves) means a
    subclass reads the same either way, and works on a 1.x client
    library too, where PyIndi calls them directly and `updateProperty`
    is never invoked.
    """

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the INDI client."""
        super().__init__()

    def newDevice(self, device):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle a new INDI device.

        No-op hook; override in a subclass to react to a device
        appearing on the server.
        """
        pass

    def removeDevice(self, device):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle a removed INDI device.

        No-op hook; override in a subclass to react to a device leaving
        the server.
        """
        pass

    def newProperty(self, property):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle an INDI property being created.

        No-op hook; override in a subclass to react to a property
        appearing for the first time. Value *changes* to an existing
        property arrive through `updateProperty` instead.
        """
        pass

    def removeProperty(self, property):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle a removed INDI property.

        No-op hook; override in a subclass to react to a property being
        withdrawn.
        """
        pass

    def updateProperty(self, property):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Route an updated INDI property to its per-type callback.

        Casts the generic property into the specific type its callback
        expects and calls that callback, so a subclass can override
        `newNumber` (or the switch/text/light equivalents) and have it
        fire on a 2.x client library. This is the compatibility
        dispatcher the pyindi-client migration guide describes.

        Parameters
        ----------
        property : `PyIndi.Property`
            The property whose value the server just changed.
        """
        property_type = property.getType()
        for dispatch_type, callback_name, wrapper_class in _INDI_PROPERTY_TYPE_DISPATCH:
            if property_type == dispatch_type:
                getattr(self, callback_name)(wrapper_class(property))
                return

    def newMessage(self, device, id):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle a new INDI message.

        No-op hook; override in a subclass to react to a device's log
        messages.
        """
        pass

    def newLight(self, property):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle an updated INDI light property.

        No-op hook; override in a subclass. Reached via
        `updateProperty` on a 2.x client library, and called directly by
        PyIndi on a 1.x one.
        """
        pass

    def newNumber(self, property):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle an updated INDI number property.

        No-op hook; override in a subclass. Reached via
        `updateProperty` on a 2.x client library, and called directly by
        PyIndi on a 1.x one.
        """
        pass

    def newSwitch(self, property):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle an updated INDI switch property.

        No-op hook; override in a subclass. Reached via
        `updateProperty` on a 2.x client library, and called directly by
        PyIndi on a 1.x one.
        """
        pass

    def newText(self, property):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Handle an updated INDI text property.

        No-op hook; override in a subclass. Reached via
        `updateProperty` on a 2.x client library, and called directly by
        PyIndi on a 1.x one.
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
        self._last_pulse_ns: float = 0.0
        self._last_pulse_we: float = 0.0

        # Thread-safe external alignment sync event queue
        self._external_syncs_lock = threading.Lock()
        self._external_syncs: list[dict[str, Any]] = []
        self._sync_mode_active: bool = False
        self._sync_mode_start_time: float | None = None
        self._pre_sync_ra: float | None = None
        self._pre_sync_dec: float | None = None
        self._last_mount_ra: float | None = None
        self._last_mount_dec: float | None = None
        # Polar alignment tracking state
        self._polar_alignment_lock = threading.Lock()
        self._polar_alignment_status: dict[str, Any] = {
            "status": "idle",
            "total_error_arcsec": None,
            "alt_error_arcsec": None,
            "az_error_arcsec": None,
            "pole_ra": None,
            "pole_dec": None,
            "paa_points": [],
            "timestamp": None,
        }
        self._pending_polar_alignment_record: dict[str, Any] | None = None

        # Camera exposure tracking state for countdown clock
        self._exposure_in_progress: bool = False
        self._active_exposure_duration: float | None = None
        self._exposure_start_time: float | None = None
        self._last_exposure_val: float | None = None
        self._exposure_counts_up: bool | None = None

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

    def newDevice(self, device: Any) -> None:
        """Handle a newly announced INDI device.

        Dynamically updates the device map if the device has a valid name.

        Parameters
        ----------
        device : `Any`
            The new INDI device object from the server.
        """
        if not hasattr(self, "deviceMap") or self.deviceMap is None:
            self.deviceMap = {}
        if device is not None:
            try:
                name = device.getDeviceName()
                if name and name.strip():
                    self.deviceMap[name] = device
            except Exception as e:
                logger.debug(f"Failed to query device name on newDevice: {e}")

    def removeDevice(self, device: Any) -> None:
        """Handle a removed INDI device.

        Evicts the removed device from the internal device map.

        Parameters
        ----------
        device : `Any`
            The removed INDI device object.
        """
        if hasattr(self, "deviceMap") and self.deviceMap and device is not None:
            try:
                name = device.getDeviceName()
                if name in self.deviceMap:
                    self.deviceMap.pop(name, None)
            except Exception as e:
                logger.debug(f"Failed to query device name on removeDevice: {e}")

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
            try:
                name = device.getDeviceName()
                if name and name.strip():
                    self.deviceMap[name] = self.getDevice(name)
            except Exception as e:
                logger.debug(f"Failed to resolve device name in connect_to_server: {e}")

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
        has_valid_devices = (
            hasattr(self, "deviceMap")
            and bool(self.deviceMap)
            and any(bool(k and str(k).strip()) for k in self.deviceMap)
        )
        if self.isServerConnected() and not has_valid_devices:
            self.device_discovery.refresh_device_map()
            has_valid_devices = (
                hasattr(self, "deviceMap")
                and bool(self.deviceMap)
                and any(bool(k and str(k).strip()) for k in self.deviceMap)
            )
            if not has_valid_devices and cooldown_elapsed:
                self._reconnect(now)

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

    def _reset_status(self) -> None:
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
            "CAMERA_TEMPERATURE": "-",
            "CAMERA_STATUS": "Idle",
            "TARGET_NAME": None,
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
                elif telescope_track_state and any(
                    (s.getName() in ("TRACK_ON", "ON") or "ON" in s.getName().upper())
                    and s.getState() == PyIndi.ISS_ON
                    for s in telescope_track_state
                ):
                    tracking_status = "Tracking"
                else:
                    # Check SkyWatcher / Star Adventurer GTi RASTATUS
                    # light property
                    ra_status = device_telescope.getLight("RASTATUS")
                    is_ra_running = False
                    if ra_status:
                        for light in ra_status:
                            light_name = light.getName()
                            if light_name == "RARunning" and light.getState() in (
                                PyIndi.IPS_BUSY,
                                PyIndi.IPS_OK,
                            ):
                                is_ra_running = True
                                break

                    # Also check TELESCOPE_MOTION_RATE or TELESCOPE_TRACK_RATE
                    # if present
                    track_rate = device_telescope.getSwitch("TELESCOPE_TRACK_RATE")
                    rate_active = False
                    if (
                        track_rate
                        and telescope_track_state
                        and not any(
                            (s.getName() in ("TRACK_OFF", "OFF", "IDLE")) and s.getState() == PyIndi.ISS_ON
                            for s in telescope_track_state
                        )
                    ):
                        rate_active = any(s.getState() == PyIndi.ISS_ON for s in track_rate)

                    if is_ra_running or rate_active:
                        tracking_status = "Tracking"
                    else:
                        tracking_status = "Idle"
            else:
                connection_status = (
                    "Disconnecting..."
                    if telescope_connect[0].getState() == PyIndi.ISS_OFF
                    else "Connecting..."
                )

            # Mark defaults initialized without forcibly altering
            # hardware tracking state
            if connection_status == "Connected" and not self._has_initialized_defaults:
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

    def _refresh_camera_status(self) -> None:
        """Poll the main camera for temperature, exposure, and target."""
        if self.status.get("CONNECTION_STATUS") != "Connected":
            return

        camera = self._find_main_camera_device()
        if not camera:
            return

        # Read camera sensor temperature
        ccd_temp = camera.getNumber("CCD_TEMPERATURE")
        if ccd_temp and len(ccd_temp) > 0:
            self.status["CAMERA_TEMPERATURE"] = f"{ccd_temp[0].value:.1f}°C"

        # Read camera exposure countdown or status
        ccd_exposure = camera.getNumber("CCD_EXPOSURE")
        if ccd_exposure and len(ccd_exposure) > 0:
            val = ccd_exposure[0].value
            state = getattr(ccd_exposure, "s", None)
            is_busy = (state == PyIndi.IPS_BUSY) if PyIndi else False
            fits_exptime = self._extract_exptime_from_camera(camera)
            self._handle_exposure_update(val, is_busy, fits_exptime)
        else:
            self.status["CAMERA_STATUS"] = "Idle"

        # Inspect camera FITS header for target name if available
        self._extract_target_from_camera(camera)

    def _extract_exptime_from_camera(self, camera: Any) -> float | None:
        """Extract commanded exposure duration from camera FITS_HEADER.

        Parameters
        ----------
        camera : `Any`
            INDI camera device handle.

        Returns
        -------
        exptime : `float` | `None`
            Target exposure seconds if found in header.
        """
        try:
            fits_header = camera.getText("FITS_HEADER")
            if not fits_header:
                return None
            import re

            for i in range(len(fits_header)):
                elem = fits_header[i]
                raw_text = elem.getText() if hasattr(elem, "getText") else getattr(elem, "text", "") or ""
                match = re.search(r"EXPTIME\s*=\s*([\d\.]+)", raw_text, re.IGNORECASE)
                if match:
                    return float(match.group(1))
        except Exception as exptime_err:
            logger.debug(f"Failed to read EXPTIME from FITS_HEADER: {exptime_err}")
        return None

    def _handle_exposure_update(self, val: float, is_busy: bool, fits_exptime: float | None = None) -> None:
        """Process camera exposure progress and maintain a countdown clock.

        Normalizes camera drivers that report elapsed seconds vs drivers that
        report remaining seconds into a monotonically decreasing countdown.

        Parameters
        ----------
        val : `float`
            Current exposure value reported by the camera driver.
        is_busy : `bool`
            Whether the camera exposure property state is IPS_BUSY.
        fits_exptime : `float` | `None`, optional
            Target exposure duration extracted from FITS_HEADER.
        """
        import time

        if not is_busy:
            self._exposure_in_progress = False
            self._active_exposure_duration = None
            self._exposure_start_time = None
            self._last_exposure_val = None
            self._exposure_counts_up = None
            self.status["CAMERA_STATUS"] = "Idle"
            return

        now = time.time()
        if not self._exposure_in_progress:
            self._exposure_in_progress = True
            self._exposure_start_time = now
            self._last_exposure_val = val
            self._exposure_counts_up = None

        if fits_exptime is not None and fits_exptime > 0:
            self._active_exposure_duration = fits_exptime
        elif self._active_exposure_duration is None and val > 0:
            self._active_exposure_duration = val

        # Detect direction of counter if changing
        if self._last_exposure_val is not None and abs(val - self._last_exposure_val) > 0.02:
            if val > self._last_exposure_val:
                self._exposure_counts_up = True
            elif val < self._last_exposure_val:
                self._exposure_counts_up = False
        self._last_exposure_val = val

        # Calculate remaining countdown seconds
        if self._exposure_counts_up is True:
            # val is elapsed time, so remaining is duration - val
            total = self._active_exposure_duration or val
            remaining = max(0.0, total - val)
        elif self._exposure_counts_up is False:
            # val is already remaining time
            remaining = val
        else:
            # Initial sample before direction is established:
            # If we know total duration and val is a small fraction (< 50%),
            # val is elapsed time (e.g. 0.4s into a 30s exposure).
            if (
                self._active_exposure_duration
                and self._active_exposure_duration > 1.0
                and val < self._active_exposure_duration * 0.5
            ):
                self._exposure_counts_up = True
                remaining = max(0.0, self._active_exposure_duration - val)
            else:
                remaining = val

        if remaining > 0:
            self.status["CAMERA_STATUS"] = f"Exposing ({remaining:.1f}s)"
        else:
            self.status["CAMERA_STATUS"] = "Downloading"

    def _extract_target_from_camera(self, camera: Any) -> None:
        """Check camera device properties for celestial target metadata.

        Parameters
        ----------
        camera : `Any`
            INDI camera device handle.
        """
        try:
            fits_header = camera.getText("FITS_HEADER")
            if fits_header:
                self._extract_target_from_text_property(fits_header)
        except Exception as header_error:
            logger.debug(f"Failed to read camera FITS_HEADER: {header_error}")

    def _extract_target_from_text_property(self, text_vector: Any) -> None:
        """Extract celestial target name from FITS_HEADER or target text.

        Parameters
        ----------
        text_vector : `Any`
            INDI text property containing target keywords or headers.
        """
        try:
            import re

            for i in range(len(text_vector)):
                elem = text_vector[i]
                name = elem.getName() if hasattr(elem, "getName") else getattr(elem, "name", "")
                raw_text = elem.getText() if hasattr(elem, "getText") else getattr(elem, "text", "") or ""
                text = raw_text.strip()
                if not text:
                    continue

                # Direct OBJECT element match
                if name.upper() in ("OBJECT", "FITS_OBJECT", "TARGET", "TARGET_NAME", "OBJECT_NAME"):
                    clean_name = text.strip("'\" \t\r\n")
                    if clean_name and clean_name.upper() not in ("UNKNOWN", "-", "NONE"):
                        self.status["TARGET_NAME"] = clean_name
                        return

                # FITS header card text match: OBJECT  = 'M31' / Target name
                card_match = re.search(r"OBJECT\s*=\s*['\"]?([^'\"/]+)", text, re.IGNORECASE)
                if card_match:
                    clean_name = card_match.group(1).strip()
                    if clean_name and clean_name.upper() not in ("UNKNOWN", "-", "NONE"):
                        self.status["TARGET_NAME"] = clean_name
                        return
        except Exception as extract_error:
            logger.debug(f"Failed to extract target from text property: {extract_error}")

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
        self._refresh_camera_status()

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
            camera_temperature=self.status.get("CAMERA_TEMPERATURE", "-"),
            camera_status=self.status.get("CAMERA_STATUS", "Idle"),
            target_name=self.status.get("TARGET_NAME"),
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

    def drain_external_syncs(self) -> list[dict[str, Any]]:
        """Drain and return queued external alignment sync events.

        Returns
        -------
        sync_records : `list` [`dict` [`str`, `Any`]]
            List of detected plate-solve sync events containing
            pointing error offsets and target coordinates.
        """
        with self._external_syncs_lock:
            sync_records = list(self._external_syncs)
            self._external_syncs.clear()
        return sync_records

    def drain_polar_alignment(self) -> dict[str, Any] | None:
        """Drain and return newly detected polar alignment record, if any.

        Returns
        -------
        record : `dict` [`str`, `Any`] | `None`
            Latest polar alignment telemetry record for persistence, or
            `None` if no new measurement occurred.
        """
        with self._polar_alignment_lock:
            record = self._pending_polar_alignment_record
            self._pending_polar_alignment_record = None
        return record

    def get_polar_alignment_status(self) -> dict[str, Any]:
        """Return the current polar alignment status and metrics.

        Returns
        -------
        status : `dict` [`str`, `Any`]
            Dictionary containing polar error, alt/az offsets, and pole coords.
        """
        with self._polar_alignment_lock:
            data = dict(self._polar_alignment_status)
        return {
            "status": data.get("status", "idle"),
            "totalErrorArcsec": data.get("total_error_arcsec"),
            "altErrorArcsec": data.get("alt_error_arcsec"),
            "azErrorArcsec": data.get("az_error_arcsec"),
            "poleRa": data.get("pole_ra"),
            "poleDec": data.get("pole_dec"),
            "paaPoints": data.get("paa_points", []),
            "timestamp": data.get("timestamp"),
        }

    def newSwitch(self, switch_vector: Any) -> None:
        """Handle a switch property update from the INDI server.

        Passively observes mount coordinate sync mode changes commanded by
        external programs such as KStars or Ekos.

        Parameters
        ----------
        switch_vector : `Any`
            Updated INDI switch property from the server.
        """
        try:
            property_name = switch_vector.getName()
            if property_name == "ON_COORD_SET":
                import time

                for switch_item in switch_vector:
                    name = switch_item.getName()
                    state = switch_item.getState()
                    if name == "SYNC":
                        is_sync = state == PyIndi.ISS_ON
                        self._sync_mode_active = is_sync
                        if is_sync:
                            self._sync_mode_start_time = time.time()
                            self._pre_sync_ra = self._last_mount_ra
                            self._pre_sync_dec = self._last_mount_dec
                    elif name in ("TRACK", "SLEW") and state == PyIndi.ISS_ON:
                        self._sync_mode_active = False
                        self._pre_sync_ra = None
                        self._pre_sync_dec = None
        except Exception as switch_error:
            logger.debug(f"Error checking external switch property: {switch_error}")

    def newNumber(self, number_vector: Any) -> None:
        """Handle a number property update from the INDI server.

        Passively intercepts timed guide pulse commands and mount coordinate
        syncs sent by external clients like KStars/Ekos/PHD2.

        Parameters
        ----------
        number_vector : `Any`
            Updated INDI number property from the server.
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

                # Filter driver countdown decrements: only record new
                # commanding pulses; ignore decrements until 0.
                if property_name == "TELESCOPE_TIMED_GUIDE_NS":
                    current_ns = (
                        pulse_north if pulse_north > 0 else (-pulse_south if pulse_south > 0 else 0.0)
                    )
                    last_ns = getattr(self, "_last_pulse_ns", 0.0)
                    is_new_pulse = abs(current_ns) > 0 and (
                        abs(current_ns) > abs(last_ns) or abs(last_ns) < 1e-3
                    )
                    self._last_pulse_ns = current_ns
                    if not is_new_pulse:
                        return
                elif property_name == "TELESCOPE_TIMED_GUIDE_WE":
                    current_we = pulse_west if pulse_west > 0 else (-pulse_east if pulse_east > 0 else 0.0)
                    last_we = getattr(self, "_last_pulse_we", 0.0)
                    is_new_pulse = abs(current_we) > 0 and (
                        abs(current_we) > abs(last_we) or abs(last_we) < 1e-3
                    )
                    self._last_pulse_we = current_we
                    if not is_new_pulse:
                        return

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

            elif property_name in ("EQUATORIAL_EOD_COORD", "EQUATORIAL_COORD"):
                import math
                import time

                ra_val = None
                dec_val = None
                for coord_element in number_vector:
                    coord_name = coord_element.getName()
                    if coord_name == "RA":
                        ra_val = coord_element.getValue()
                    elif coord_name == "DEC":
                        dec_val = coord_element.getValue()

                if ra_val is not None and dec_val is not None:
                    # Expire stale sync mode if no plate-solve sync arrived
                    # within 15 seconds
                    if (
                        self._sync_mode_active
                        and self._sync_mode_start_time is not None
                        and time.time() - self._sync_mode_start_time > 15.0
                    ):
                        self._sync_mode_active = False
                        self._pre_sync_ra = None
                        self._pre_sync_dec = None

                    if self._sync_mode_active:
                        base_ra = self._pre_sync_ra if self._pre_sync_ra is not None else self._last_mount_ra
                        base_dec = (
                            self._pre_sync_dec if self._pre_sync_dec is not None else self._last_mount_dec
                        )

                        if base_ra is not None and base_dec is not None:
                            d_ra_hours = (ra_val - base_ra + 12.0) % 24.0 - 12.0
                            dec_rad = math.radians(dec_val)
                            delta_ra_arcsec = d_ra_hours * 15.0 * 3600.0 * math.cos(dec_rad)
                            delta_dec_arcsec = (dec_val - base_dec) * 3600.0

                            pointing_error = math.hypot(delta_ra_arcsec, delta_dec_arcsec)

                            # Ignore mount tracking loop heartbeat echoes and
                            # minor jitter (error < 2.0 arcsec or stationary
                            # Dec with negligible RA). Legitimate plate solves
                            # correct slewing errors (> 3-5 arcsec). Keep
                            # _sync_mode_active = True so we wait for actual
                            # plate-solved coordinates rather than consuming a
                            # sub-tick tracking update.
                            is_tracking_jitter = abs(delta_dec_arcsec) < 1e-4 and abs(delta_ra_arcsec) < 2.0
                            if pointing_error < 2.0 or is_tracking_jitter:
                                return

                            if pointing_error < 36000.0:
                                status = "aligned" if pointing_error <= 120.0 else "warning"
                                with self._external_syncs_lock:
                                    self._external_syncs.append({
                                        "time": time.time(),
                                        "status": status,
                                        "delta_ra_arcsec": round(delta_ra_arcsec, 2),
                                        "delta_dec_arcsec": round(delta_dec_arcsec, 2),
                                        "pointing_error_arcsec": round(pointing_error, 2),
                                        "ra": ra_val,
                                        "dec": dec_val,
                                    })
                                if abs(dec_val) > 65.0:
                                    with self._polar_alignment_lock:
                                        pts = self._polar_alignment_status.get("paa_points", [])
                                        if not any(
                                            abs(p.get("ra", 0) - ra_val) < 0.01
                                            and abs(p.get("dec", 0) - dec_val) < 0.01
                                            for p in pts
                                        ):
                                            pts.append({"ra": ra_val, "dec": dec_val, "time": time.time()})
                                            self._polar_alignment_status["paa_points"] = pts[-5:]
                            self._sync_mode_active = False
                            self._pre_sync_ra = None
                            self._pre_sync_dec = None

                    self._last_mount_ra = ra_val
                    self._last_mount_dec = dec_val

            elif property_name in ("ALIGNPOINT", "POLAR_ALIGNMENT_POINT"):
                import time

                ra_val = None
                dec_val = None
                for elem in number_vector:
                    ename = elem.getName()
                    if ename in ("ALIGNPOINT_CELESTIAL_RA", "RA", "CELESTIAL_RA"):
                        ra_val = elem.getValue()
                    elif ename in ("ALIGNPOINT_CELESTIAL_DE", "DEC", "DE", "CELESTIAL_DEC"):
                        dec_val = elem.getValue()

                if ra_val is not None and dec_val is not None:
                    # In INDI, RA is typically in hours (0..24); convert to
                    # degrees if <= 24.
                    ra_deg = (ra_val * 15.0) if ra_val <= 24.0 else ra_val
                    with self._polar_alignment_lock:
                        pts = self._polar_alignment_status.get("paa_points", [])
                        if not any(
                            abs(p.get("ra", 0) - ra_deg) < 0.01 and abs(p.get("dec", 0) - dec_val) < 0.01
                            for p in pts
                        ):
                            pts.append({
                                "ra": round(ra_deg, 4),
                                "dec": round(dec_val, 4),
                                "time": time.time(),
                            })
                            self._polar_alignment_status["paa_points"] = pts[-5:]

            elif property_name == "SYNCPOLARALIGN":
                import math
                import time

                alt_err = None
                az_err = None
                for elem in number_vector:
                    ename = elem.getName()
                    if ename in ("SYNCPOLARALIGN_ALT", "ALT", "ALTITUDE"):
                        alt_err = elem.getValue()
                    elif ename in ("SYNCPOLARALIGN_AZ", "AZ", "AZIMUTH"):
                        az_err = elem.getValue()

                if alt_err is not None or az_err is not None:
                    alt_val = alt_err or 0.0
                    az_val = az_err or 0.0
                    total_err = math.hypot(alt_val, az_val)
                    status = "aligned" if total_err <= 60.0 else "warning"
                    current_dec = self._last_mount_dec if self._last_mount_dec is not None else 90.0
                    pole_dec = (
                        90.0 - (total_err / 3600.0) if current_dec >= 0 else -90.0 + (total_err / 3600.0)
                    )
                    pole_record = {
                        "status": status,
                        "total_error_arcsec": round(total_err, 2),
                        "alt_error_arcsec": round(alt_val, 2),
                        "az_error_arcsec": round(az_val, 2),
                        "pole_ra": self._last_mount_ra,
                        "pole_dec": round(pole_dec, 5),
                        "paa_points": list(self._polar_alignment_status.get("paa_points", [])),
                        "timestamp": time.time(),
                    }
                    with self._polar_alignment_lock:
                        self._polar_alignment_status = dict(pole_record)
                        self._pending_polar_alignment_record = dict(pole_record)

            elif property_name == "CCD_EXPOSURE":
                if len(number_vector) > 0:
                    val = number_vector[0].getValue()
                    state = getattr(number_vector, "s", None)
                    is_busy = (state == PyIndi.IPS_BUSY) if PyIndi else False
                    camera = self._find_main_camera_device()
                    fits_exptime = self._extract_exptime_from_camera(camera) if camera else None
                    self._handle_exposure_update(val, is_busy, fits_exptime)

        except Exception as pulse_error:
            logger.debug(f"Error handling property update in newNumber: {pulse_error}")

    def newText(self, text_vector: Any) -> None:
        """Handle a text property update from the INDI server.

        Passively intercepts FITS header keywords (such as OBJECT) and
        target info sent by external capture software (e.g. KStars/Ekos).

        Parameters
        ----------
        text_vector : `Any`
            Updated INDI text property from the server.
        """
        try:
            property_name = text_vector.getName()
            if property_name in ("FITS_HEADER", "OBJECT_INFO", "TARGET_NAME", "OBJECT_NAME"):
                self._extract_target_from_text_property(text_vector)
        except Exception as text_error:
            logger.debug(f"Error checking external text property: {text_error}")

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
