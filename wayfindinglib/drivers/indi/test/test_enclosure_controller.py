"""Purpose: Unit tests for EnclosureController's DOME_SHUTTER handling.

Description: Verifies `get_state`/`open`/`close` read and write the
standard INDI Dome Interface's `DOME_SHUTTER` switch correctly --
motion state (`IPS_BUSY`/`IPS_ALERT`), settled open/closed state, and
the `Unknown Is Unsafe` fallback when the device or property is
unavailable -- against a fake INDI device, matching this codebase's
established fake-device testing discipline (`test_mount_controller.py`).
"""

from wayfindinglib.drivers.indi.enclosure_controller import EnclosureController
from wayfindinglib.drivers.indi.pyindi_compatibility import PyIndi
from wayfindinglib.models.equipment_and_site.enclosure import EnclosureState


class _FakeSwitchElement:
    """A fake INDI switch element with a settable name and on/off state."""

    def __init__(self, name: str, state: int) -> None:
        self._name = name
        self.s = state

    def getName(self) -> str:
        """Return this element's property name.

        Returns
        -------
        name : `str`
            This element's fixed name.
        """
        return self._name

    def getState(self) -> int:
        """Return this element's on/off state.

        Returns
        -------
        state : `int`
            `PyIndi.ISS_ON` or `PyIndi.ISS_OFF`.
        """
        return self.s


class _FakeSwitchVector(list):
    """A fake INDI switch property vector: a list of elements plus `.s`."""

    def __init__(self, elements: list, vector_state: int) -> None:
        super().__init__(elements)
        self.s = vector_state


class _FakeEnclosureDevice:
    """A fake INDI device exposing a single `DOME_SHUTTER` switch."""

    def __init__(self, shutter_switch: _FakeSwitchVector | None) -> None:
        self._shutter_switch = shutter_switch

    def getSwitch(self, name: str) -> _FakeSwitchVector | None:
        """Return the fixed `DOME_SHUTTER` switch, or `None`.

        Returns
        -------
        switch : `_FakeSwitchVector` or `None`
            The fixed switch vector if `name` matches, else `None`.
        """
        return self._shutter_switch if name == "DOME_SHUTTER" else None


class _FakeClient:
    """A fake INDI client recording sent switch vectors."""

    def __init__(self) -> None:
        """Initialize with no switch vector sent yet."""
        self.sent_switch = None

    def sendNewSwitch(self, switch_vector) -> None:
        """Record the switch vector that was sent."""
        self.sent_switch = switch_vector


def _shutter(open_state: int, close_state: int, vector_state: int = PyIndi.IPS_OK) -> _FakeSwitchVector:
    """Build a `DOME_SHUTTER` switch vector with the given element states.

    Returns
    -------
    switch : `_FakeSwitchVector`
        A switch vector with `SHUTTER_OPEN`/`SHUTTER_CLOSE` elements.
    """
    return _FakeSwitchVector(
        [
            _FakeSwitchElement("SHUTTER_OPEN", open_state),
            _FakeSwitchElement("SHUTTER_CLOSE", close_state),
        ],
        vector_state,
    )


def test_get_state_returns_unknown_with_no_device() -> None:
    """Verify get_state fails closed to UNKNOWN when no device is found."""
    controller = EnclosureController(_FakeClient())
    assert controller.get_state(None) == EnclosureState.UNKNOWN


def test_get_state_returns_unknown_with_no_shutter_property() -> None:
    """Verify get_state fails closed to UNKNOWN with no DOME_SHUTTER."""
    controller = EnclosureController(_FakeClient())
    assert controller.get_state(_FakeEnclosureDevice(None)) == EnclosureState.UNKNOWN


def test_get_state_returns_open_when_shutter_open_element_is_on() -> None:
    """Verify get_state reports OPEN from a settled SHUTTER_OPEN element."""
    controller = EnclosureController(_FakeClient())
    device = _FakeEnclosureDevice(_shutter(PyIndi.ISS_ON, PyIndi.ISS_OFF))
    assert controller.get_state(device) == EnclosureState.OPEN


def test_get_state_returns_closed_when_shutter_close_element_is_on() -> None:
    """Verify get_state reports CLOSED from a settled SHUTTER_CLOSE element."""
    controller = EnclosureController(_FakeClient())
    device = _FakeEnclosureDevice(_shutter(PyIndi.ISS_OFF, PyIndi.ISS_ON))
    assert controller.get_state(device) == EnclosureState.CLOSED


def test_get_state_returns_opening_while_busy_with_open_requested() -> None:
    """Verify get_state reports OPENING while the vector is IPS_BUSY."""
    controller = EnclosureController(_FakeClient())
    device = _FakeEnclosureDevice(_shutter(PyIndi.ISS_ON, PyIndi.ISS_OFF, vector_state=PyIndi.IPS_BUSY))
    assert controller.get_state(device) == EnclosureState.OPENING


def test_get_state_returns_closing_while_busy_with_close_requested() -> None:
    """Verify get_state reports CLOSING while the vector is IPS_BUSY."""
    controller = EnclosureController(_FakeClient())
    device = _FakeEnclosureDevice(_shutter(PyIndi.ISS_OFF, PyIndi.ISS_ON, vector_state=PyIndi.IPS_BUSY))
    assert controller.get_state(device) == EnclosureState.CLOSING


def test_get_state_returns_fault_on_alert() -> None:
    """Verify get_state reports FAULT when the vector is IPS_ALERT."""
    controller = EnclosureController(_FakeClient())
    device = _FakeEnclosureDevice(_shutter(PyIndi.ISS_OFF, PyIndi.ISS_OFF, vector_state=PyIndi.IPS_ALERT))
    assert controller.get_state(device) == EnclosureState.FAULT


def test_open_sends_shutter_open_and_confirms() -> None:
    """Verify open() sends SHUTTER_OPEN=ON and confirms against the device.

    `EnclosureController._set_shutter` mutates the element states
    in-place before calling `sendNewSwitch` -- a real INDI driver would
    apply the change asynchronously, but `_FakeClient` (like the real
    property vector) already reflects the sent state immediately, which
    is enough to exercise the confirmation poll.
    """
    client = _FakeClient()
    controller = EnclosureController(client)
    switch = _shutter(PyIndi.ISS_OFF, PyIndi.ISS_ON)
    device = _FakeEnclosureDevice(switch)

    assert controller.open(device, timeout=1.0) is True
    assert client.sent_switch is switch
    assert switch[0].getState() == PyIndi.ISS_ON
    assert switch[1].getState() == PyIndi.ISS_OFF


def test_close_sends_shutter_close_and_confirms() -> None:
    """Verify close() sends SHUTTER_CLOSE=ON and confirms the device."""
    client = _FakeClient()
    controller = EnclosureController(client)
    switch = _shutter(PyIndi.ISS_ON, PyIndi.ISS_OFF)
    device = _FakeEnclosureDevice(switch)

    assert controller.close(device, timeout=1.0) is True
    assert client.sent_switch is switch
    assert switch[0].getState() == PyIndi.ISS_OFF
    assert switch[1].getState() == PyIndi.ISS_ON


def test_open_returns_false_with_no_device() -> None:
    """Verify open() fails safely rather than raising with no device."""
    controller = EnclosureController(_FakeClient())
    assert controller.open(None) is False


def test_close_returns_false_with_no_shutter_property() -> None:
    """Verify close() fails safely rather than raising with no DOME_SHUTTER."""
    controller = EnclosureController(_FakeClient())
    assert controller.close(_FakeEnclosureDevice(None)) is False
