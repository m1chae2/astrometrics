"""In-memory simulator for PyIndi hardware drivers used in testing.

Implements mock connection and telescope/camera/filter-wheel control
simulation.
"""

import logging

from wayfindinglib.drivers.indi_interface import IndiInterface

# Filter names the simulated filter wheel reports -- matches the real filter
# wheel's expected name set (see astrometricslib.utilities.enums.FilterType)
# closely enough for resolve_filter_name's fuzzy matching to exercise
# realistic paths in tests.
_SIMULATED_FILTER_NAMES = ["Luminance", "Red", "Green", "Blue", "H_Alpha", "OIII", "SII", "SPEC"]


class _SimulatedDeviceDiscovery:
    """Stand-in for indi.device_discovery.DeviceDiscovery.

    The simulator has no real INDI device objects to search for, so every
    find_* method returns None (device handles are opaque to the simulated
    controllers below, which read simulator state directly instead).
    """

    def find_telescope(self):  # ruff: ignore[missing-return-type-private-function]
        """Return None: the simulator has no real INDI device object."""
        return None

    def find_focuser(self):  # ruff: ignore[missing-return-type-private-function]
        """Return None: the simulator has no real INDI device object."""
        return None

    def find_filterwheel(self):  # ruff: ignore[missing-return-type-private-function]
        """Return None: the simulator has no real INDI device object."""
        return None

    def find_guide_camera(self):  # ruff: ignore[missing-return-type-private-function]
        """Return None: the simulator has no real INDI device object."""
        return None

    def find_main_camera(self):  # ruff: ignore[missing-return-type-private-function]
        """Return None: the simulator has no real INDI device object."""
        return None

    def find_powerbox(self):  # ruff: ignore[missing-return-type-private-function]
        """Return None: the simulator has no real INDI device object."""
        return None

    def find_device_with_property(self, property_name):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Return None: the simulator has no real INDI device object."""
        return None

    def refresh_device_map(self):  # ruff: ignore[missing-return-type-private-function]
        """No-op: the simulator has no live device map to refresh."""


class _SimulatedMountController:
    """Stand-in for indi.mount_controller.MountController.

    Delegates to the owning SimulatorIndiInterface's own simulated state
    rather than a real INDI device, since IndiInterface's non-overridden
    methods (sync_coordinates, validate_altitude_limits, etc.) expect a
    mount_controller to exist even when SimulatorIndiInterface overrides
    the higher-level slew/park/unpark/set_tracking/move methods directly.
    """

    def __init__(self, simulator: SimulatorIndiInterface):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the stand-in with a reference to the owning simulator."""
        self._simulator = simulator

    def validate_altitude_limits(self, telescope, ra, dec):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Permit the move: the simulator has no altitude-limit config.

        Returns
        -------
        allowed : `bool`
            Always `True`.
        """
        return True

    def slew(self, telescope, ra, dec):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Delegate to the simulator's own slew() implementation.

        Returns
        -------
        success : `bool`
            The result of the simulator's `slew` call.
        """
        return self._simulator.slew(ra, dec)

    def park(self, telescope):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Delegate to the simulator's own park() implementation.

        Returns
        -------
        success : `bool`
            The result of the simulator's `park` call.
        """
        return self._simulator.park()

    def unpark(self, telescope):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Delegate to the simulator's own unpark() implementation.

        Returns
        -------
        success : `bool`
            The result of the simulator's `unpark` call.
        """
        return self._simulator.unpark()

    def set_tracking(self, telescope, enabled):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Delegate to the simulator's own set_tracking() implementation.

        Returns
        -------
        success : `bool`
            The result of the simulator's `set_tracking` call.
        """
        return self._simulator.set_tracking(enabled)

    def sync_coordinates(self, telescope, ra, dec):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Report success: the simulator has no real sync state.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        return True

    def move(self, device, direction, start=True):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Delegate to the simulator's own move() implementation.

        Returns
        -------
        success : `bool`
            The result of the simulator's `move` call.
        """
        return self._simulator.move(direction, start)

    def _set_coord_mode(self, telescope_device, mode):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Report success: the simulator has no real coordinate mode.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        return True


class _SimulatedFocuserController:
    """Stand-in for indi.focuser_controller.FocuserController."""

    def __init__(self, simulator: SimulatorIndiInterface):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the stand-in with a reference to the owning simulator."""
        self._simulator = simulator

    def get_position(self, device):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Delegate to the simulator's own get_focuser_position().

        Returns
        -------
        position : `int`
            The result of the simulator's `get_focuser_position` call.
        """
        return self._simulator.get_focuser_position()

    def move_relative(self, device, steps):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Delegate to the simulator's own focus_move() implementation.

        Returns
        -------
        success : `bool`
            The result of the simulator's `focus_move` call.
        """
        return self._simulator.focus_move(steps)


class _SimulatedFilterWheelController:
    """Stand-in for indi.filter_wheel_controller.FilterWheelController."""

    def __init__(self, simulator: SimulatorIndiInterface):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the stand-in with a reference to the owning simulator."""
        self._simulator = simulator

    def get_names(self, device):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Return the simulator's fixed simulated filter name list.

        Returns
        -------
        names : `list` [`str`]
            The simulated filter names.
        """
        return self._simulator.filter_names

    def get_current_filter(self, device):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Return the simulator's current filter name from its status dict.

        Returns
        -------
        filter_name : `str` or `None`
            The current simulated filter name.
        """
        return self._simulator.status.get("FILTER")

    def resolve_name(self, device, filter_name):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Case-insensitive exact match against the simulated filter names.

        Returns
        -------
        matched_name : `str` or `None`
            The matching simulated filter name, or `None` if no
            case-insensitive match was found.
        """
        for candidate_name in self._simulator.filter_names:
            if candidate_name.lower() == str(filter_name).lower():
                return candidate_name
        return None

    def set_position(self, device, filter_name):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Delegate to the simulator's own set_filterwheel_position().

        Returns
        -------
        success : `bool`
            The result of the simulator's `set_filterwheel_position`
            call.
        """
        return self._simulator.set_filterwheel_position(filter_name)


class _SimulatedCameraController:
    """Stand-in for indi.camera_controller.CameraController."""

    def __init__(self, simulator: SimulatorIndiInterface):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the stand-in with a reference to the owning simulator."""
        self._simulator = simulator

    def pulse_guide(self, device, direction, duration_ms):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Report success: the simulator has no real pulse-guide hardware.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        return True

    def expose(self, device, exposure_seconds, gain=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Report success: the simulator has no real camera hardware.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        return True

    def get_guide_image(self, device):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Return None: the simulator has no real guide-camera image data.

        Returns
        -------
        image : `None`
            Always `None`.
        """
        return None


class _SimulatedConnectionManager:
    """Stand-in for indi.connection_manager.ConnectionManager."""

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        """Initialize minimal connection-health state IndiInterface reads."""
        self.hostname = None
        self.last_connection_attempt = None

    def is_server_responsive(self):  # ruff: ignore[missing-return-type-private-function]
        """Report responsive: the simulator has no real socket to check.

        Returns
        -------
        responsive : `bool`
            Always `True`.
        """
        return True


class SimulatorIndiInterface(IndiInterface):
    """In-memory simulator for INDI hardware.

    Mimics the behavior of a real telescope and camera without external
    connections.
    """

    def __init__(self, config=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        # We generally do NOT call super().__init__() because we don't want
        # the real socket connection logic. But if IndiInterface has other
        # setup, we might need to mimic it. For now, we assume we are
        # replacing the connection logic entirely.
        self.config = config
        get_allow_commands = getattr(config, "get_allow_commands", None)
        self.allow_commands = get_allow_commands() if callable(get_allow_commands) else False

        self.connected = False
        self.telescope_coords = {"ra": "00:00:00", "dec": "00:00:00"}
        self.camera_status = "IDLE"
        self.filter_wheel_position = 1
        self.filter_names = list(_SIMULATED_FILTER_NAMES)
        self.exposure_time = 0

        # State required by consumers
        self.device_map = {}
        self.deviceMap = {}

        # Controller stand-ins so IndiInterface's non-overridden methods
        # (which delegate to these) don't AttributeError --
        # SimulatorIndiInterface overrides the higher-level
        # slew/park/focus_move/etc. methods directly, but a few methods
        # (get_filter_names, resolve_filter_name, sync_coordinates, ...)
        # are inherited unchanged and reach into these directly.
        self.device_discovery = _SimulatedDeviceDiscovery()
        self.mount_controller = _SimulatedMountController(self)
        self.focuser_controller = _SimulatedFocuserController(self)
        self.filter_wheel_controller = _SimulatedFilterWheelController(self)
        self.camera_controller = _SimulatedCameraController(self)
        self.connection_manager = _SimulatedConnectionManager()

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

        import threading

        self._external_pulses_lock = threading.Lock()
        self._external_pulses = []

        logging.getLogger(__name__).info("SimulatorIndiInterface initialized.")

    def _ensure_connection(self):  # ruff: ignore[missing-return-type-private-function]
        """Mark the simulator connected without touching any real socket.

        Prevents AttributeError from missing C++ backend connection state
        in testing.
        """
        self.connected = True
        self.status["CONNECTION_STATUS"] = "Connected"

    def connect(self, host="localhost", port=7624):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Mock connecting to establish a simulated INDI server connection.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        self.connected = True
        self.status["CONNECTION_STATUS"] = "Connected"
        return True
        self.connected = True
        self.status["CONNECTION_STATUS"] = "Connected"
        return True

    def connect_to_server(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Mock connecting to the server, ensuring connected status is set.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        self.connect()
        return True

    def connect_to_telescope(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Mock connecting to the telescope, always reporting success.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        self.connect()
        return True

    def disconnect(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Mark the simulator as disconnected."""
        self.connected = False

    def is_connected(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return the simulator's current connection state.

        Returns
        -------
        connected : `bool`
            `True` if the simulator is connected.
        """
        return self.connected

    def isServerConnected(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return the connection state directly.

        Bypasses PyIndi's uninitialized C++ SWIG bindings.

        Returns
        -------
        connected : `bool`
            `True` if the simulator is connected.
        """
        return self.connected

    def getDevices(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return no devices: `deviceMap` is always empty in simulation.

        Bypasses PyIndi's `AbstractBaseClient.getDevices` C++ overload,
        which requires a real server connection this simulator never
        establishes.

        Returns
        -------
        devices : `list`
            Always empty.
        """
        return []

    # --- Telescope Control ---

    def slew(self, ra, dec):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Simulate slewing to the given coordinates.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        self.telescope_coords["ra"] = str(
            # Stored as a string here to match self.status's string fields,
            # even though IndiInterface passes a float for the ra argument.
            ra
        )
        # Actually TelescopeService returns driver.slew result.
        # Let's just return True.
        # And update status.
        self.status["RA"] = str(ra)  # simplified
        self.status["DEC"] = str(dec)
        self.status["TRACKING_STATUS"] = "Tracking"
        return True

    def park(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Simulate parking the telescope.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        self.status["TRACKING_STATUS"] = "Parked"
        return True

    def abort_motion(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Simulate aborting telescope motion.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        self.status["TRACKING_STATUS"] = "Idle"
        return True

    def unpark(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Simulate unparking the telescope.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        self.status["TRACKING_STATUS"] = "Idle"
        return True

    def set_tracking(self, enabled):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Simulate toggling tracking on or off.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        self.status["TRACKING_STATUS"] = "Tracking" if enabled else "Idle"
        return True

    def move(self, direction, start=True):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Simulate a directional move command, always reporting success.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        return True

    def set_slew_rate(self, rate_index):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Simulate setting the slew rate, always reporting success.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        return True

    # --- Focuser ---
    def focus_move(self, steps):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Simulate a relative focuser move, always reporting success.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        # Simulator logic
        return True

    def get_focuser_position(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return a fixed focuser position of zero.

        Returns
        -------
        position : `int`
            Always ``0``.
        """
        return 0

    # --- Filter Wheel ---
    def set_filterwheel_position(self, filter_name):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Simulate moving the filter wheel to the given filter.

        Returns
        -------
        success : `bool`
            Always `True`.
        """
        self.filter_wheel_position = filter_name
        self.status["FILTER"] = filter_name
        return True

    # --- Status ---
    def get_status(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Build a TelescopeStatus object from the simulator's status dict.

        Returns
        -------
        status : `TelescopeStatus`
            The simulated telescope status.
        """
        # TelescopeService.get_status calls self._driver.get_status(),
        # which returns a Pydantic model in IndiInterface (TelescopeStatus),
        # so we return a TelescopeStatus object here to match.
        from wayfindinglib.drivers.indi_interface import TelescopeStatus

        return TelescopeStatus(
            ra=self.status.get("RA", "00:00:00"),
            dec=self.status.get("DEC", "00:00:00"),
            altitude=self.status.get("ALTITUDE", "00:00:00"),
            azimuth=self.status.get("AZIMUTH", "00:00:00"),
            temperature=self.status.get("TEMPERATURE", "0"),
            humidity=self.status.get("HUMIDITY", "0"),
            connection_status=self.status.get("CONNECTION_STATUS", "Connected"),
            tracking_status=self.status.get("TRACKING_STATUS", "Idle"),
            focuser_position=self.get_focuser_position(),
            filter=str(self.filter_wheel_position),
            guiding_history=[],
        )
