"""Purpose: Unit tests for converting MCP client values into Wayfinder objects.

Description: Covers the converters in `wayfindinglib.mcp.argument_resolution`
(names to targets and sky objects, ISO strings to astropy times, the
injected `Astrometrics` handle) and the Wayfinder tools that depend on
them, which used to fail with `'str' object has no attribute 'id'`.
"""

import types
from collections.abc import Callable
from typing import Any

import pytest
from astropy.time import Time

from wayfindinglib.mcp import argument_resolution
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

    def get(self, target_id: str) -> object | None:
        """Return the target with this id, or `None`.

        Returns
        -------
        target : `object` or `None`
            The stored target.
        """
        return self._by_id.get(target_id)


@pytest.fixture
def hooks(monkeypatch: pytest.MonkeyPatch) -> Hooks:
    """Build hooks over a fake Wayfinder and a fake `Astrometrics`.

    Returns
    -------
    hooks : `tuple`
        The `(argument_resolvers, injected_arguments)` pair.
    """
    fake_astrometrics = types.SimpleNamespace(targets=_FakeTargets())
    monkeypatch.setattr(argument_resolution, "Astrometrics", lambda config: fake_astrometrics)

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
        config=object(), planning=types.SimpleNamespace(resolve_target_coordinates=resolve_name)
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


def test_sky_objects_resolve_names_and_coordinates(hooks: Hooks) -> None:
    """Names resolve; coordinate dictionaries become stellar objects."""
    resolvers, _ = hooks
    resolved = resolvers["objects"](["NGC 7635", {"id": "P1", "ra_deg": 350.2, "dec_deg": 61.2}])
    assert resolved[0].id == "NGC 7635"
    assert resolved[1].id == "P1"
    assert float(resolved[1].right_ascension) == pytest.approx(350.2)


def test_a_name_that_cannot_resolve_is_named_in_the_error(hooks: Hooks) -> None:
    """The failing name appears in the message."""
    resolvers, _ = hooks
    with pytest.raises(ValueError, match="Could not resolve 'Nowhere'"):
        resolvers["objects"](["Nowhere"])


def test_a_coordinate_dictionary_missing_a_key_is_explained(hooks: Hooks) -> None:
    """A dictionary without coordinates explains the expected keys."""
    resolvers, _ = hooks
    with pytest.raises(ValueError, match="ra_deg"):
        resolvers["objects"]([{"id": "P1"}])


def test_time_with_an_offset_converts_to_utc(hooks: Hooks) -> None:
    """22:00 at UTC-6 is 04:00 UTC the next day."""
    resolvers, _ = hooks
    parsed = resolvers["time_input"]("2026-10-02T22:00:00-06:00")
    assert isinstance(parsed, Time)
    assert parsed.isot == "2026-10-03T04:00:00.000"


def test_time_without_an_offset_is_taken_as_utc(hooks: Hooks) -> None:
    """A naive ISO time is read as UTC."""
    resolvers, _ = hooks
    assert resolvers["time_input"]("2026-10-03T04:00:00").isot == "2026-10-03T04:00:00.000"


def test_a_bad_time_string_gives_a_clear_error(hooks: Hooks) -> None:
    """An unreadable time raises `ValueError` with an example."""
    resolvers, _ = hooks
    with pytest.raises(ValueError, match="ISO 8601"):
        resolvers["time_input"]("tomorrow evening")


def test_now_is_accepted_as_a_time(hooks: Hooks) -> None:
    """The word ``now`` gives the current time."""
    resolvers, _ = hooks
    assert isinstance(resolvers["time_input"]("now"), Time)


def test_astrometrics_is_injected_once(hooks: Hooks) -> None:
    """The factory returns one shared handle."""
    _, injected = hooks
    assert injected["astrometrics"]() is injected["astrometrics"]()


def test_injected_parameters_are_hidden_from_registered_tool_schemas() -> None:
    """Clients are not asked for `astrometrics`; the server supplies it."""
    definitions = {t.name: t for t in wayfinding_registry.get_tool_definitions()}
    schema = definitions["planning_get_target_quality_advisory"].inputSchema
    assert "astrometrics" not in schema["properties"]
    assert "astrometrics" not in schema["required"]
    assert "target_id" in schema["required"]


async def test_visibility_accepts_coordinates_and_an_offset_time() -> None:
    """Visibility runs with a coordinate dictionary and a local ISO time."""
    result = await wayfinding_registry.execute(
        "planning_get_visibility",
        {
            "objects": [{"id": "M 52", "ra_deg": 351.2, "dec_deg": 61.59}],
            "time_input": "2026-10-02T22:00:00-06:00",
        },
    )
    assert "M 52" in result[0].text
    assert "altitude" in result[0].text.lower() or "alt" in result[0].text.lower()
