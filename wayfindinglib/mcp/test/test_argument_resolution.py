"""Purpose: Unit tests for converting MCP client values into Wayfinder objects.

Description: Covers the converters in `wayfindinglib.mcp.argument_resolution`
(target ids to library targets) and the Wayfinder tools that take names,
coordinate dictionaries and ISO times, which used to fail with
`'str' object has no attribute 'id'`.
"""

import types
from collections.abc import Callable
from typing import Any

import pytest

from wayfindinglib.mcp.argument_resolution import build_argument_hooks
from wayfindinglib.mcp.tool_registry import registry as wayfinding_registry

pytestmark = pytest.mark.anyio

Hooks = tuple[dict[str, Callable[[Any], Any]], dict[str, Callable[[], Any]]]


@pytest.fixture
def anyio_backend() -> str:
    """Restrict anyio-marked tests in this module to the asyncio backend.

    Returns
    -------
    backend : `str`
        The anyio backend name to run these tests under.
    """
    return "asyncio"


class _FakeTargets:
    """A target catalog holding a few named targets."""

    def __init__(self) -> None:
        """Create the catalog with one target."""
        self._by_id = {"M 52": types.SimpleNamespace(id="M 52")}
        self.reload_count = 0

    def list(self) -> list[object]:
        """Re-read the catalog, as the real one does from disk.

        Returns
        -------
        targets : `list` [`object`]
            Every stored target.
        """
        self.reload_count += 1
        return list(self._by_id.values())

    def get(self, target_id: str, refresh: bool = False) -> object | None:
        """Return the target with this id, or `None`.

        Parameters
        ----------
        target_id : `str`
            The id to look up.
        refresh : `bool`, optional
            Re-read the catalog first, as the real one does from disk.

        Returns
        -------
        target : `object` or `None`
            The stored target.
        """
        if refresh:
            self.list()
        return self._by_id.get(target_id)


@pytest.fixture
def fake_astrometrics() -> types.SimpleNamespace:
    """Make a fake `Astrometrics` holding one target.

    Returns
    -------
    astrometrics : `types.SimpleNamespace`
        An object with a `targets` catalog.
    """
    return types.SimpleNamespace(targets=_FakeTargets())


@pytest.fixture
def hooks(fake_astrometrics: types.SimpleNamespace) -> Hooks:
    """Build hooks over a fake Wayfinder that holds a fake `Astrometrics`.

    Parameters
    ----------
    fake_astrometrics : `types.SimpleNamespace`
        The fake science library handle.

    Returns
    -------
    hooks : `tuple`
        The `(argument_resolvers, injected_arguments)` pair.
    """

    def resolve_name(name: str) -> object:
        """Resolve one known name and reject the rest.

        Returns
        -------
        resolved : `object`
            A stand-in sky object.

        Raises
        ------
        RuntimeError
            For any name but the one known.
        """
        if name == "NGC 7635":
            return types.SimpleNamespace(id="NGC 7635")
        raise RuntimeError("not found")

    wayfinder = types.SimpleNamespace(
        config=object(),
        astrometrics=fake_astrometrics,
        planning=types.SimpleNamespace(resolve_target_coordinates=resolve_name),
    )
    return build_argument_hooks(wayfinder)


def test_target_id_resolves_to_the_library_target(hooks: Hooks) -> None:
    """A known id returns the library's own target object."""
    resolvers, _ = hooks
    assert resolvers["target"]("M 52").id == "M 52"


def test_an_unknown_target_id_gives_a_clear_error(hooks: Hooks) -> None:
    """An unknown id raises `ValueError` that names the id."""
    resolvers, _ = hooks
    with pytest.raises(ValueError, match="No target 'Nope'"):
        resolvers["target"]("Nope")


def test_an_object_passes_through_unchanged(hooks: Hooks) -> None:
    """A value that is already an object is not converted."""
    resolvers, _ = hooks
    already_object = types.SimpleNamespace(id="x")
    assert resolvers["target"](already_object) is already_object


def test_no_parameter_is_injected(hooks: Hooks) -> None:
    """The server supplies no parameters; the library holds the handle."""
    _, injected = hooks
    assert injected == {}


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


def test_a_target_id_reads_the_catalog_fresh_each_time(
    hooks: Hooks, fake_astrometrics: types.SimpleNamespace
) -> None:
    """Resolving a target re-reads the catalog, so a frame sync is seen."""
    resolvers, _ = hooks
    catalog = fake_astrometrics.targets
    resolvers["target"]("M 52")
    resolvers["target"]("M 52")
    assert catalog.reload_count == 2
