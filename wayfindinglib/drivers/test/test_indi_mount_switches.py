"""Purpose: Tests for reading the mount's pier side, park and rate switches.

Description: `IndiInterface._read_mount_switches` turns the INDI switch
properties of a mount into plain values, and `control.mount.status`
reports them as ``pierSide``, ``parked`` and ``trackMode``. The tests use
a fake INDI device, so no INDI server is needed.
"""

from types import SimpleNamespace

from wayfindinglib.drivers.indi.pyindi_compatibility import PyIndi
from wayfindinglib.drivers.indi_interface import IndiInterface


def _fake_mount(switches: dict[str, dict[str, bool]]) -> SimpleNamespace:
    """Build a fake INDI mount device with fixed switch properties.

    Parameters
    ----------
    switches : `dict` [`str`, `dict` [`str`, `bool`]]
        Property name to its elements, each mapped to whether it is on.

    Returns
    -------
    mount : `types.SimpleNamespace`
        An object with INDI's ``getSwitch`` lookup.
    """
    properties = {
        name: [
            SimpleNamespace(
                getName=lambda element=element: element,
                getState=lambda on=on: PyIndi.ISS_ON if on else PyIndi.ISS_OFF,
            )
            for element, on in elements.items()
        ]
        for name, elements in switches.items()
    }
    return SimpleNamespace(getSwitch=properties.get)


def test_switches_become_plain_values() -> None:
    """The pier side, park switch and tracking rate read as plain values."""
    mount = _fake_mount({
        "TELESCOPE_PIER_SIDE": {"PIER_WEST": True, "PIER_EAST": False},
        "TELESCOPE_PARK": {"PARK": True, "UNPARK": False},
        "TELESCOPE_TRACK_MODE": {"TRACK_SIDEREAL": True, "TRACK_SOLAR": False},
    })
    switches = IndiInterface._read_mount_switches(IndiInterface.__new__(IndiInterface), mount)
    assert switches == {"PIER_SIDE": "WEST", "PARKED": True, "TRACK_MODE": "SIDEREAL"}


def test_missing_switches_read_as_none() -> None:
    """A mount that reports none of the switches gives `None` for each."""
    switches = IndiInterface._read_mount_switches(IndiInterface.__new__(IndiInterface), _fake_mount({}))
    assert switches == {"PIER_SIDE": None, "PARKED": None, "TRACK_MODE": None}


def test_unparked_mount_reads_as_not_parked() -> None:
    """A park property with UNPARK on reads as not parked."""
    mount = _fake_mount({"TELESCOPE_PARK": {"PARK": False, "UNPARK": True}})
    switches = IndiInterface._read_mount_switches(IndiInterface.__new__(IndiInterface), mount)
    assert switches["PARKED"] is False
