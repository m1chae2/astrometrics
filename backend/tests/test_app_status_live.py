"""Purpose: Tests for the live telescope, guiding and INDI parts of app_status.

Description: The backend holds the hardware connection, so these parts of
`app_status` ask it through the RPC call. The tests replace that call with
canned answers and check the layers are unwrapped, the mount's pier side is
read from its INDI properties, guide samples are trimmed, and an unknown
device is reported as an error.
"""

import asyncio

import pytest

from backend.mcp import tool_registry
from backend.mcp.tool_registry import _describe_switches, _unwrap, tool_app_status

MOUNT_PROPERTIES = {
    "TELESCOPE_PIER_SIDE": {"state": "Idle", "elements": {"PIER_WEST": "On", "PIER_EAST": "Off"}},
    "TELESCOPE_TRACK_STATE": {"elements": {"TRACK_ON": "Off", "TRACK_OFF": "On"}},
    "TELESCOPE_PARK": {"elements": {"PARK": "On", "UNPARK": "Off"}},
    "TELESCOPE_TRACK_MODE": {"elements": {"TRACK_SIDEREAL": "On", "TRACK_SOLAR": "Off"}},
    "EQUATORIAL_EOD_COORD": {"elements": {"RA": 12.4, "DEC": 84.9}},
}


def wrapped(payload: object) -> dict:
    """Wrap a payload the way the HTTP proxy and the router do.

    Returns
    -------
    response : `dict`
        Three layers of success and data around the payload.
    """
    return {
        "status": "success",
        "data": {"status": "success", "data": {"status": "success", "data": payload}},
    }


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    """Replace the backend call with canned answers.

    Returns
    -------
    calls : `list` [`tuple` [`str`, `dict`]]
        Every method and its parameters, in order.
    """
    calls: list[tuple[str, dict]] = []
    answers = {
        "telescope:status": {
            "ra": "12 00 00",
            "trackingStatus": "Parked",
            "connectionStatus": "Connected",
            "focuserPosition": 29863,
            "guidingHistory": [{"time": 1}],
        },
        "telescope:indi_devices": ["GPSD", "Star Adventurer GTi", "ZWO EFW"],
        "telescope:indi_properties": MOUNT_PROPERTIES,
        "guiding:status": {
            "is_guiding": False,
            "stats": {"rms_total": 1.9},
            "history": [
                {"time": index, "dra": 0.1, "ddec": 0.2, "pulseRa": 5.0, "rms_ra": 1.0} for index in range(50)
            ],
        },
        "system:health": {"resources": {"system_ram_usage_percent": 40}, "indi": {"status": "Disconnected"}},
    }

    async def fake_execute_rpc(method: str, params: dict | None = None) -> dict:
        """Answer a backend call from the canned table.

        Returns
        -------
        response : `dict`
            The canned answer, wrapped.
        """
        await asyncio.sleep(0)
        calls.append((method, params or {}))
        return wrapped(answers[method])

    monkeypatch.setattr(tool_registry, "execute_rpc", fake_execute_rpc)
    return calls


def test_the_layers_around_an_answer_are_removed() -> None:
    """Three layers of success and data come off, and errors stay errors."""
    assert _unwrap(wrapped({"a": 1})) == {"a": 1}
    assert _unwrap({"status": "error", "message": "boom"}) == {"error": "boom"}


def test_mount_switches_are_read_as_plain_values() -> None:
    """The pier side, tracking and park switches become readable fields."""
    summary = _describe_switches(MOUNT_PROPERTIES)
    assert summary["pier_side"] == "WEST"
    assert summary["tracking"] is False
    assert summary["parked"] is True
    assert summary["track_mode"] == "SIDEREAL"
    assert summary["equatorial_eod_coord"] == {"RA": 12.4, "DEC": 84.9}


def test_telescope_status_includes_the_pier_side_and_drops_the_history(fake_backend: list) -> None:
    """The header values come back with the mount's own pier side."""
    answer = asyncio.run(tool_app_status(["telescope"]))["telescope"]
    assert answer["trackingStatus"] == "Parked"
    assert answer["focuserPosition"] == 29863
    assert "guidingHistory" not in answer
    assert answer["mount_indi"]["device"] == "Star Adventurer GTi"
    assert answer["mount_indi"]["pier_side"] == "WEST"


def test_guiding_lists_only_the_newest_samples_with_plain_keys(fake_backend: list) -> None:
    """Twenty samples are kept and the camelCase copies are dropped."""
    answer = asyncio.run(tool_app_status(["guiding"]))["guiding"]
    assert answer["samples_total"] == 50
    assert len(answer["recent_samples"]) == 20
    assert "pulseRa" not in answer["recent_samples"][0]
    assert answer["recent_samples"][-1]["time"] == 49


def test_connections_and_system_are_read_from_the_health_answer(fake_backend: list) -> None:
    """The nested health answer is no longer read as null."""
    answer = asyncio.run(tool_app_status(["connections", "system"]))
    assert answer["connections"]["indi"] == {"status": "Disconnected"}
    assert answer["system"] == {"system_ram_usage_percent": 40}


def test_indi_properties_need_a_device_and_can_be_filtered(fake_backend: list) -> None:
    """A missing device is an error; a filter keeps the named ones."""
    assert "error" in asyncio.run(tool_app_status(["indi_properties"]))["indi_properties"]
    answer = asyncio.run(
        tool_app_status(["indi_properties"], device="Star Adventurer GTi", property_names=["TELESCOPE_PARK"])
    )["indi_properties"]
    assert list(answer["properties"]) == ["TELESCOPE_PARK"]
    assert answer["properties_total"] == 5
