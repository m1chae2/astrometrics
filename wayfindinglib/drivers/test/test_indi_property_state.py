"""Purpose: Unit tests for reading INDI property states.

Description: The status bar showed the main camera as "Idle" while it was
exposing, because the code read `.s` from a PyIndi 2.x property object,
which has only `getState()`. These tests use fake property objects that
have `getState()` and no `s` attribute, like the real ones, and check the
camera status and the property listing.
"""

import threading
import time

from wayfindinglib.drivers.indi.property_state import get_property_state, property_state_name
from wayfindinglib.drivers.indi.pyindi_compatibility import PyIndi
from wayfindinglib.drivers.indi_interface import IndiInterface


class _FakeElement:
    """A fake number element with a value."""

    def __init__(self, value: float, name: str = "CCD_EXPOSURE_VALUE") -> None:
        self.value = value
        self.name = name

    def getValue(self) -> float:
        """Return the element value.

        Returns
        -------
        value : `float`
            The value this fake was built with.
        """
        return self.value


class _FakeNumberProperty(list):
    """A fake PyIndi number property: elements plus `getState()`, no `s`."""

    def __init__(self, value: float, state: int) -> None:
        super().__init__([_FakeElement(value)])
        self._state = state

    def getState(self) -> int:
        """Return the property state.

        Returns
        -------
        state : `int`
            The `IPS_*` value this fake was built with.
        """
        return self._state


class _FakeCamera:
    """A fake camera device returning a fixed CCD_EXPOSURE property."""

    def __init__(self, exposure: object) -> None:
        self._exposure = exposure

    def getNumber(self, name: str) -> object | None:
        """Return the exposure property, or `None` for other names.

        Returns
        -------
        property : `object` | `None`
            The fixed exposure property for CCD_EXPOSURE.
        """
        return self._exposure if name == "CCD_EXPOSURE" else None

    def getText(self, name: str) -> None:
        """Report no text properties (so no FITS header).

        Returns
        -------
        text : `None`
            Always `None`.
        """
        return None


def _make_interface(exposure: object) -> IndiInterface:
    """Build an IndiInterface without a server, wired to a fake camera.

    Returns
    -------
    interface : `IndiInterface`
        The interface with only the fields the camera poll reads.
    """
    interface = IndiInterface.__new__(IndiInterface)
    interface.status = {"CONNECTION_STATUS": "Connected", "CAMERA_STATUS": "Idle"}
    interface._exposure_in_progress = False
    interface._active_exposure_duration = None
    interface._exposure_start_time = None
    interface._last_exposure_val = None
    interface._exposure_lock = threading.Lock()
    interface._monotonic_clock = time.monotonic
    interface._find_main_camera_device = lambda: _FakeCamera(exposure)
    interface._extract_target_from_camera = lambda camera: None
    return interface


def test_busy_exposure_is_not_reported_idle() -> None:
    """A BUSY CCD_EXPOSURE read with getState() sets a non-Idle status."""
    interface = _make_interface(_FakeNumberProperty(2.0, PyIndi.IPS_BUSY))
    interface._refresh_camera_status()
    assert interface.status["CAMERA_STATUS"] != "Idle"


def test_ok_and_idle_exposure_are_reported_idle() -> None:
    """OK and IDLE CCD_EXPOSURE states both give an Idle camera status."""
    for state in (PyIndi.IPS_OK, PyIndi.IPS_IDLE):
        interface = _make_interface(_FakeNumberProperty(0.0, state))
        interface.status["CAMERA_STATUS"] = "Exposing"
        interface._refresh_camera_status()
        assert interface.status["CAMERA_STATUS"] == "Idle"


def test_missing_get_state_is_visible_as_unknown() -> None:
    """A property with no getState() gives "Unknown", not a silent "Idle"."""
    exposure = [_FakeElement(1.0)]
    interface = _make_interface(exposure)
    interface._refresh_camera_status()
    assert interface.status["CAMERA_STATUS"] == "Unknown"
    assert get_property_state(exposure) is None


def test_new_number_callback_reads_get_state() -> None:
    """The newNumber callback also sees BUSY through getState()."""
    interface = _make_interface(None)

    class _NamedProperty(_FakeNumberProperty):
        """A fake property that also reports its INDI name."""

        def getName(self) -> str:
            """Return the INDI property name.

            Returns
            -------
            name : `str`
                Always "CCD_EXPOSURE".
            """
            return "CCD_EXPOSURE"

    interface._polar_alignment_lock = threading.Lock()
    interface.newNumber(_NamedProperty(2.0, PyIndi.IPS_BUSY))
    assert interface.status["CAMERA_STATUS"] != "Idle"


def test_state_names() -> None:
    """The four INDI states map to readable words; anything else is Unknown."""
    assert property_state_name(PyIndi.IPS_IDLE) == "Idle"
    assert property_state_name(PyIndi.IPS_OK) == "Ok"
    assert property_state_name(PyIndi.IPS_BUSY) == "Busy"
    assert property_state_name(PyIndi.IPS_ALERT) == "Alert"
    assert property_state_name(None) == "Unknown"


def test_property_listing_reports_real_states() -> None:
    """get_device_properties reports each property's own state."""
    states = {
        "P_BUSY": PyIndi.IPS_BUSY,
        "P_OK": PyIndi.IPS_OK,
        "P_IDLE": PyIndi.IPS_IDLE,
        "P_ALERT": PyIndi.IPS_ALERT,
    }

    class _FakeListedProperty:
        """A fake generic property with a name, type and getState()."""

        def __init__(self, name: str) -> None:
            self._name = name

        def getName(self) -> str:
            """Return the property name.

            Returns
            -------
            name : `str`
                The fixed name.
            """
            return self._name

        def getType(self) -> int:
            """Return an unknown type so no elements are read.

            Returns
            -------
            property_type : `int`
                `PyIndi.INDI_UNKNOWN`.
            """
            return PyIndi.INDI_UNKNOWN

        def getState(self) -> int:
            """Return the state for this property name.

            Returns
            -------
            state : `int`
                The `IPS_*` value from the table above.
            """
            return states[self._name]

    class _FakeDevice:
        """A fake device that lists the fake properties."""

        def getProperties(self) -> list:
            """List the fake properties.

            Returns
            -------
            properties : `list`
                One fake property per state.
            """
            return [_FakeListedProperty(name) for name in states]

    interface = IndiInterface.__new__(IndiInterface)
    interface.isServerConnected = lambda: True
    interface.deviceMap = {"Cam": _FakeDevice()}

    result = interface.get_device_properties("Cam")

    assert {name: info["state"] for name, info in result.items()} == {
        "P_BUSY": "Busy",
        "P_OK": "Ok",
        "P_IDLE": "Idle",
        "P_ALERT": "Alert",
    }


def test_disconnect_reset_reports_camera_status_unknown_not_idle() -> None:
    """A dropped link must not look like a finished frame (Idle)."""
    interface = IndiInterface.__new__(IndiInterface)
    interface.status = {"CAMERA_STATUS": "Exposing (20s)"}

    interface._reset_status()

    assert interface.status["CAMERA_STATUS"] == "Unknown"
