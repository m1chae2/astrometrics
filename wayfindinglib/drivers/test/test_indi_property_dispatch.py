"""Purpose: Unit tests for IndiClient's updateProperty type dispatcher.

Description: INDI Core 2.0 removed `newNumber`/`newSwitch`/`newText`/
`newLight` from `BaseClient` and replaced them with a single
`updateProperty`, so on a 2.x client library those four callbacks are
never invoked unless `updateProperty` dispatches to them. This repo runs
pyindi-client 2.2.0, where `IndiInterface.newNumber` -- which records
guide pulses issued by an external guider such as KStars/Ekos or PHD2 --
had no caller at all.

These tests build real `PyIndi.Property` objects rather than fakes.
That is deliberate: the dispatcher's whole job is to cast a generic
property into the specific type its callback expects
(`PyIndi.PropertyNumber(property)` and friends), and that cast is a SWIG
C++ constructor which rejects any Python stand-in. A duck-typed fake
would fail on the cast and prove nothing about the routing, so these
construct genuine properties, populate them, and upcast them to
`PyIndi.Property` exactly as the INDI server's own updates arrive.
"""

import threading

import PyIndi
import pytest

from wayfindinglib.drivers.indi_interface import IndiClient, IndiInterface


def _make_generic_property(property_class, name: str, elements=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Build a real INDI property and upcast it to a generic Property.

    Parameters
    ----------
    property_class : `type`
        The concrete PyIndi property class to build, e.g.
        `PyIndi.PropertyNumber`.
    name : `str`
        The INDI property name to assign.
    elements : `list` [`tuple` [`str`, `float`]], optional
        ``(element_name, value)`` pairs to populate. Number properties
        only; other types are built with a single unnamed element.

    Returns
    -------
    generic_property : `PyIndi.Property`
        The populated property, upcast to the generic type that
        `updateProperty` receives from the server.
    """
    elements = elements or []
    concrete_property = property_class(max(len(elements), 1))
    concrete_property.setDeviceName("FakeMount")
    concrete_property.setName(name)
    for element_index, (element_name, element_value) in enumerate(elements):
        concrete_property[element_index].setName(element_name)
        concrete_property[element_index].setValue(element_value)
    return PyIndi.Property(concrete_property)


class _RecordingClient(IndiClient):
    """An IndiClient that records which per-type callback was reached."""

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        super().__init__()
        self.received: list[tuple[str, str]] = []

    def newNumber(self, property) -> None:  # ruff: ignore[missing-type-function-argument]
        """Record that a number property arrived."""
        self.received.append(("newNumber", property.getName()))

    def newSwitch(self, property) -> None:  # ruff: ignore[missing-type-function-argument]
        """Record that a switch property arrived."""
        self.received.append(("newSwitch", property.getName()))

    def newText(self, property) -> None:  # ruff: ignore[missing-type-function-argument]
        """Record that a text property arrived."""
        self.received.append(("newText", property.getName()))

    def newLight(self, property) -> None:  # ruff: ignore[missing-type-function-argument]
        """Record that a light property arrived."""
        self.received.append(("newLight", property.getName()))


@pytest.mark.parametrize(
    ("property_class", "expected_callback"),
    [
        (PyIndi.PropertyNumber, "newNumber"),
        (PyIndi.PropertySwitch, "newSwitch"),
        (PyIndi.PropertyText, "newText"),
        (PyIndi.PropertyLight, "newLight"),
    ],
)
def test_update_property_routes_to_per_type_callback(property_class, expected_callback):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Each property type reaches the callback named for that type."""
    client = _RecordingClient()

    client.updateProperty(_make_generic_property(property_class, "SOME_PROPERTY"))

    assert client.received == [(expected_callback, "SOME_PROPERTY")]


def test_update_property_ignores_unknown_type():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A property of unknown type is dropped rather than raising.

    A default-constructed `PyIndi.Property` reports `INDI_UNKNOWN`,
    standing in for any type this dispatcher does not recognize. It must
    not take the client down: updateProperty is called by the C++
    library on its own thread, where an exception has nowhere to go.
    """
    client = _RecordingClient()

    client.updateProperty(PyIndi.Property())

    assert client.received == []


def test_external_guide_pulse_is_recorded_through_update_property():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A timed guide pulse reaches IndiInterface's external-pulse recorder.

    This is the end-to-end path that was silently dead on pyindi-client
    2.x: the server pushes TELESCOPE_TIMED_GUIDE_WE, PyIndi calls
    updateProperty, and the pulse must land in `_external_pulses` for
    backend/services/observatory/guiding_service.py to read.
    """
    interface = IndiInterface.__new__(IndiInterface)
    interface._external_pulses = []
    interface._external_pulses_lock = threading.Lock()

    guide_property = _make_generic_property(
        PyIndi.PropertyNumber,
        "TELESCOPE_TIMED_GUIDE_WE",
        elements=[("TIMED_GUIDE_W", 350.0), ("TIMED_GUIDE_E", 0.0)],
    )

    IndiClient.updateProperty(interface, guide_property)

    assert len(interface._external_pulses) == 1
    assert interface._external_pulses[0]["pulse_w"] == pytest.approx(350.0)
    assert interface._external_pulses[0]["pulse_e"] == pytest.approx(0.0)


def test_unrelated_number_property_records_no_pulse():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Only the timed-guide properties are recorded as external pulses.

    Coordinate updates arrive constantly while a mount slews, so the
    recorder must ignore everything that is not a guide pulse.
    """
    interface = IndiInterface.__new__(IndiInterface)
    interface._external_pulses = []
    interface._external_pulses_lock = threading.Lock()

    coordinate_property = _make_generic_property(
        PyIndi.PropertyNumber, "EQUATORIAL_EOD_COORD", elements=[("RA", 5.5), ("DEC", -3.2)]
    )

    IndiClient.updateProperty(interface, coordinate_property)

    assert interface._external_pulses == []
