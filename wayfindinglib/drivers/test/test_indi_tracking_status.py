"""Purpose: Tests for how the mount's tracking status is decided.

Description: `IndiInterface._read_tracking_status` reads the park switch,
the track-state switch and, as a last resort, the SkyWatcher ``RASTATUS``
lights. The tests use a fake INDI device, so no INDI server is needed.
"""

from types import SimpleNamespace

from wayfindinglib.drivers.indi.pyindi_compatibility import PyIndi
from wayfindinglib.drivers.indi_interface import IndiInterface


def _fake_mount(
    switches: dict[str, list[tuple[str, bool]]],
    lights: dict[str, list[tuple[str, int]]] | None = None,
) -> SimpleNamespace:
    """Build a fake INDI mount with fixed switch and light properties.

    Parameters
    ----------
    switches : `dict` [`str`, `list` [`tuple` [`str`, `bool`]]]
        Property name to its (element name, is on) pairs, in order.
    lights : `dict` [`str`, `list` [`tuple` [`str`, `int`]]], optional
        Property name to its (element name, ``IPS_*`` state) pairs.

    Returns
    -------
    mount : `types.SimpleNamespace`
        An object with INDI's ``getSwitch`` and ``getLight`` lookups.
    """
    switch_properties = {
        name: [
            SimpleNamespace(
                getName=lambda element=element: element,
                getState=lambda on=on: PyIndi.ISS_ON if on else PyIndi.ISS_OFF,
            )
            for element, on in elements
        ]
        for name, elements in switches.items()
    }
    light_properties = {
        name: [
            SimpleNamespace(getName=lambda element=element: element, getState=lambda state=state: state)
            for element, state in elements
        ]
        for name, elements in (lights or {}).items()
    }
    return SimpleNamespace(getSwitch=switch_properties.get, getLight=light_properties.get)


_STOPPED_LIGHTS = {"RASTATUS": [("RAInitialized", PyIndi.IPS_OK), ("RARunning", PyIndi.IPS_BUSY)]}
_RUNNING_LIGHTS = {"RASTATUS": [("RAInitialized", PyIndi.IPS_OK), ("RARunning", PyIndi.IPS_OK)]}


def test_track_off_beats_ra_status_lights() -> None:
    """TRACK_OFF on reads as Idle even when the lights say running."""
    mount = _fake_mount(
        {"TELESCOPE_TRACK_STATE": [("TRACK_ON", False), ("TRACK_OFF", True)]},
        _RUNNING_LIGHTS,
    )
    assert IndiInterface._read_tracking_status(mount) == "Idle"


def test_track_off_with_stopped_lights_is_idle() -> None:
    """The live GTi case: TRACK_OFF on and RARunning Busy reads as Idle."""
    mount = _fake_mount(
        {
            "TELESCOPE_TRACK_STATE": [("TRACK_ON", False), ("TRACK_OFF", True)],
            "TELESCOPE_PARK": [("PARK", False), ("UNPARK", True)],
        },
        _STOPPED_LIGHTS,
    )
    assert IndiInterface._read_tracking_status(mount) == "Idle"


def test_track_on_reads_as_tracking() -> None:
    """TRACK_ON on reads as Tracking."""
    mount = _fake_mount({"TELESCOPE_TRACK_STATE": [("TRACK_ON", True), ("TRACK_OFF", False)]})
    assert IndiInterface._read_tracking_status(mount) == "Tracking"


def test_park_wins_whatever_the_element_order() -> None:
    """PARK on reads as Parked with PARK first or last."""
    track = {"TELESCOPE_TRACK_STATE": [("TRACK_ON", True), ("TRACK_OFF", False)]}
    for order in ([("PARK", True), ("UNPARK", False)], [("UNPARK", False), ("PARK", True)]):
        assert (
            IndiInterface._read_tracking_status(_fake_mount({**track, "TELESCOPE_PARK": order})) == "Parked"
        )
    unparked = [("UNPARK", True), ("PARK", False)]
    assert (
        IndiInterface._read_tracking_status(_fake_mount({**track, "TELESCOPE_PARK": unparked})) == "Tracking"
    )


def test_no_track_state_uses_running_light() -> None:
    """With no track-state switch, RARunning Ok reads as Tracking."""
    assert IndiInterface._read_tracking_status(_fake_mount({}, _RUNNING_LIGHTS)) == "Tracking"


def test_no_track_state_busy_light_is_idle() -> None:
    """With no track-state switch, RARunning Busy reads as Idle."""
    assert IndiInterface._read_tracking_status(_fake_mount({}, _STOPPED_LIGHTS)) == "Idle"


def test_no_track_state_and_no_lights_is_idle() -> None:
    """A mount that reports neither property reads as Idle."""
    assert IndiInterface._read_tracking_status(_fake_mount({})) == "Idle"
