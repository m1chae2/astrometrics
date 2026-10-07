"""Purpose: Unit tests for how Wayfinder tools take a client's arguments.

Description: The MCP tools pass a client's JSON arguments to the `Wayfinder`
methods as they are. These tests check that the methods accept what a client
can send: a coordinate dictionary for a sky position, an ISO time with an
offset, and a target id. They also check that the server, not the client,
decides `register_job`.
"""

import types

import pytest

from astrometricslib import InvalidArgumentError, NotFoundError
from wayfindinglib import SkyPosition
from wayfindinglib.mcp.tool_registry import registry as wayfinding_registry
from wayfindinglib.tasks.control_tasks import hardware_operations

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    """Restrict anyio-marked tests in this module to the asyncio backend.

    Returns
    -------
    backend : `str`
        The anyio backend name to run these tests under.
    """
    return "asyncio"


def test_server_only_parameters_are_hidden_from_registered_tool_schemas() -> None:
    """Clients are not asked for `register_job`; the server decides it."""
    definitions = {t.name: t for t in wayfinding_registry.get_tool_definitions()}
    schema = definitions["planning_deep_catalog_status"].inputSchema
    assert "register_job" not in schema["properties"]
    assert "include" in schema["properties"]
    advisory = definitions["planning_get_advisory"].inputSchema
    assert advisory["required"] == ["kind"]


async def test_visibility_accepts_coordinates_and_an_offset_time() -> None:
    """Visibility runs with a coordinate dictionary and a local ISO time."""
    result = await wayfinding_registry.execute(
        "planning_get_visibility",
        {
            "objects": [{"id": "M 52", "ra_deg": 351.2, "dec_deg": 61.59}],
            "time": "2026-10-02T22:00:00-06:00",
        },
    )
    assert "M 52" in result[0].text
    assert "altitude_deg" in result[0].text
    assert "2026-10-03T04:00:00Z" in result[0].text


def test_a_position_dictionary_becomes_a_sky_position() -> None:
    """The mount methods take the dictionary a client sends."""
    position = hardware_operations.sky_position_from({"ra_deg": 10.5, "dec_deg": -5.25})

    assert position == SkyPosition(ra_deg=10.5, dec_deg=-5.25)


def test_a_bad_position_dictionary_is_an_invalid_argument() -> None:
    """A dictionary that is not a position names the expected keys."""
    with pytest.raises(InvalidArgumentError, match="ra_deg"):
        hardware_operations.sky_position_from({"ra": 10.5})


def test_a_slew_target_id_is_read_fresh_from_the_library() -> None:
    """A target id is looked up after re-reading the catalog."""
    calls = []

    def get(target_id: str, refresh: bool = False) -> None:
        """Record the lookup; no target has the id."""
        calls.append((target_id, refresh))

    context = types.SimpleNamespace(
        astrometrics=types.SimpleNamespace(targets=types.SimpleNamespace(get=get))
    )

    with pytest.raises(NotFoundError, match="Nope"):
        hardware_operations.resolve_destination(context, "Nope")
    assert calls == [("Nope", True)]
