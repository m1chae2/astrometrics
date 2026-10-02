"""Purpose: Unit tests for WeatherController's WEATHER_PARAMETERS handling.

Description: Verifies `get_readings` generalizes every element of a
fake `WEATHER_PARAMETERS` number vector into the `SensorReadings`
shape `safety_monitor.assess_safety` expects, and fails closed to
empty when the device or property is unavailable.
"""

from datetime import UTC, datetime

import pytest

from wayfindinglib.drivers.indi.weather_controller import WeatherController


class _FakeNumberElement:
    """A fake INDI number element with a settable name and value."""

    def __init__(self, name: str, value: float):  # ruff: ignore[missing-return-type-special-method]
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
    """A fake INDI device exposing a single `WEATHER_PARAMETERS` vector."""

    def __init__(self, weather_parameters: _FakeVector | None):  # ruff: ignore[missing-return-type-special-method]
        self._weather_parameters = weather_parameters

    def getNumber(self, name: str):  # ruff: ignore[missing-return-type-private-function]
        """Return the fixed `WEATHER_PARAMETERS` vector, or `None`.

        Returns
        -------
        number : `_FakeVector` or `None`
            The fixed vector if `name` matches, else `None`.
        """
        return self._weather_parameters if name == "WEATHER_PARAMETERS" else None


def test_get_readings_returns_empty_with_no_device() -> None:
    """Verify get_readings fails closed to empty with no device."""
    controller = WeatherController()
    assert controller.get_readings(None) == {}


def test_get_readings_returns_empty_with_no_weather_parameters_property() -> None:
    """Verify get_readings returns empty with no WEATHER_PARAMETERS."""
    controller = WeatherController()
    assert controller.get_readings(_FakeDevice(None)) == {}


def test_get_readings_reports_every_element_with_a_current_timestamp() -> None:
    """Verify get_readings generalizes to every element, not just two.

    `IndiInterface.get_environmentals` only ever reads the first two
    elements (temperature, humidity) into fixed status-dict fields --
    this generalizes to whatever `WEATHER_PARAMETERS` actually
    publishes, the M12 extension over that fixed two-element read.
    """
    before = datetime.now(UTC)
    weather_parameters = _FakeVector([
        _FakeNumberElement("WEATHER_TEMPERATURE", 12.5),
        _FakeNumberElement("WEATHER_HUMIDITY", 55.0),
        _FakeNumberElement("WEATHER_DEWPOINT", 3.2),
    ])
    controller = WeatherController()
    readings = controller.get_readings(_FakeDevice(weather_parameters))
    after = datetime.now(UTC)

    assert set(readings) == {"WEATHER_TEMPERATURE", "WEATHER_HUMIDITY", "WEATHER_DEWPOINT"}
    temperature_value, temperature_at = readings["WEATHER_TEMPERATURE"]
    assert temperature_value == pytest.approx(12.5)
    assert before <= temperature_at <= after
    assert readings["WEATHER_HUMIDITY"][0] == pytest.approx(55.0)
    assert readings["WEATHER_DEWPOINT"][0] == pytest.approx(3.2)
