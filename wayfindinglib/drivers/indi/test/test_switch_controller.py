"""Purpose: Unit tests for SwitchController's power/dew-heater handling.

Description: Verifies `get_switch_states`/`set_switch_state` against a
fake `POWER_CONTROL` switch vector and `get_variable_values`/
`set_variable_value` against fake `DEW_PWM`/`POWER_SENSORS` number
vectors, matching this codebase's established fake-device testing
discipline (`test_enclosure_controller.py`).
"""

import pytest

from wayfindinglib.drivers.indi.pyindi_compatibility import PyIndi
from wayfindinglib.drivers.indi.switch_controller import SwitchController


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


class _FakeNumberElement:
    """A fake INDI number element with a settable name and value."""

    def __init__(self, name: str, value: float) -> None:
        self._name = name
        self.value = value

    def getName(self) -> str:
        """Return this element's property name.

        Returns
        -------
        name : `str`
            This element's fixed name.
        """
        return self._name


class _FakeVector(list):
    """A fake INDI property vector: a plain list of elements."""


class _FakeDevice:
    """A fake INDI device exposing switch and number vectors by name."""

    def __init__(self, switches: dict | None = None, numbers: dict | None = None) -> None:
        self._switches = switches or {}
        self._numbers = numbers or {}

    def getSwitch(self, name: str) -> _FakeVector | None:
        """Return the named fake switch vector, or `None`.

        Returns
        -------
        switch : `_FakeVector` or `None`
            The matching switch vector, or `None`.
        """
        return self._switches.get(name)

    def getNumber(self, name: str) -> _FakeVector | None:
        """Return the named fake number vector, or `None`.

        Returns
        -------
        number : `_FakeVector` or `None`
            The matching number vector, or `None`.
        """
        return self._numbers.get(name)


class _FakeClient:
    """A fake INDI client recording sent property vectors."""

    def __init__(self) -> None:
        """Initialize with nothing sent yet."""
        self.sent_switch = None
        self.sent_number = None

    def sendNewSwitch(self, switch_vector) -> None:
        """Record the switch vector that was sent."""
        self.sent_switch = switch_vector

    def sendNewNumber(self, number_vector) -> None:
        """Record the number vector that was sent."""
        self.sent_number = number_vector


def test_get_switch_states_returns_empty_with_no_device() -> None:
    """Verify get_switch_states fails closed to empty with no device."""
    controller = SwitchController(_FakeClient())
    assert controller.get_switch_states(None) == {}


def test_get_switch_states_returns_empty_with_no_power_control_property() -> None:
    """Verify get_switch_states returns empty with no POWER_CONTROL."""
    controller = SwitchController(_FakeClient())
    assert controller.get_switch_states(_FakeDevice()) == {}


def test_get_switch_states_reports_every_outlet() -> None:
    """Verify get_switch_states reports each outlet's on/off state."""
    controller = SwitchController(_FakeClient())
    power_control = _FakeVector([
        _FakeSwitchElement("POWER_CONTROL_1", PyIndi.ISS_ON),
        _FakeSwitchElement("POWER_CONTROL_2", PyIndi.ISS_OFF),
    ])
    device = _FakeDevice(switches={"POWER_CONTROL": power_control})
    assert controller.get_switch_states(device) == {
        "POWER_CONTROL_1": True,
        "POWER_CONTROL_2": False,
    }


def test_set_switch_state_sends_only_the_named_outlet_and_preserves_others() -> None:
    """Verify set_switch_state toggles one outlet without touching others."""
    client = _FakeClient()
    controller = SwitchController(client)
    power_control = _FakeVector([
        _FakeSwitchElement("POWER_CONTROL_1", PyIndi.ISS_ON),
        _FakeSwitchElement("POWER_CONTROL_2", PyIndi.ISS_OFF),
    ])
    device = _FakeDevice(switches={"POWER_CONTROL": power_control})

    assert controller.set_switch_state(device, "POWER_CONTROL_2", True) is True
    assert client.sent_switch is power_control
    assert power_control[0].getState() == PyIndi.ISS_ON
    assert power_control[1].getState() == PyIndi.ISS_ON


def test_set_switch_state_returns_false_for_unknown_outlet() -> None:
    """Verify set_switch_state fails safely for an unrecognized outlet name."""
    controller = SwitchController(_FakeClient())
    power_control = _FakeVector([_FakeSwitchElement("POWER_CONTROL_1", PyIndi.ISS_ON)])
    device = _FakeDevice(switches={"POWER_CONTROL": power_control})
    assert controller.set_switch_state(device, "NONEXISTENT", True) is False


def test_get_variable_values_merges_dew_pwm_and_power_sensors() -> None:
    """Verify get_variable_values merges both number vectors' elements."""
    controller = SwitchController(_FakeClient())
    device = _FakeDevice(
        numbers={
            "DEW_PWM": _FakeVector([_FakeNumberElement("DEW_A", 40.0)]),
            "POWER_SENSORS": _FakeVector([_FakeNumberElement("VOLTAGE", 12.1)]),
        }
    )
    assert controller.get_variable_values(device) == {"DEW_A": 40.0, "VOLTAGE": 12.1}


def test_get_variable_values_returns_empty_with_neither_property() -> None:
    """Verify get_variable_values returns empty with no matching property."""
    controller = SwitchController(_FakeClient())
    assert controller.get_variable_values(_FakeDevice()) == {}


def test_set_variable_value_sends_dew_pwm() -> None:
    """Verify set_variable_value commands the named DEW_PWM element."""
    client = _FakeClient()
    controller = SwitchController(client)
    dew_pwm = _FakeVector([_FakeNumberElement("DEW_A", 0.0)])
    device = _FakeDevice(numbers={"DEW_PWM": dew_pwm})

    assert controller.set_variable_value(device, "DEW_A", 75.0) is True
    assert client.sent_number is dew_pwm
    assert dew_pwm[0].value == pytest.approx(75.0)


def test_set_variable_value_returns_false_for_read_only_power_sensors() -> None:
    """Verify set_variable_value refuses a read-only POWER_SENSORS element.

    POWER_SENSORS is telemetry-only -- there is no corresponding
    command, so this honestly reports failure rather than silently
    doing nothing.
    """
    controller = SwitchController(_FakeClient())
    device = _FakeDevice(numbers={"POWER_SENSORS": _FakeVector([_FakeNumberElement("VOLTAGE", 12.1)])})
    assert controller.set_variable_value(device, "VOLTAGE", 13.0) is False
