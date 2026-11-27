"""Purpose: Tests for the subject resolver of the MCP tools.

Description: Checks which type hints count as a subject, that an id becomes
the object through the API's own lookup, that an unknown id is a
`NotFoundError` reported as an error, and that a reflected tool offers a
subject as a string id and calls the method with the object.
"""

import json
from typing import Any

import pytest

from astrometricslib import StellarObject, Target
from astrometricslib.exceptions import NotFoundError
from mcp_servers.common.reflection import plan_parameters, register_reflected_tools
from mcp_servers.common.resolvers import SUBJECT_KINDS, resolve_subject, subject_kind_of
from mcp_servers.common.tool_registry import ToolRegistry
from wayfindinglib import ObservationPackage, ObservationSession

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    """Run the async tests of this module on asyncio.

    Returns
    -------
    backend : `str`
        The anyio backend name.
    """
    return "asyncio"


class _FakeTargets:
    """A stand-in for ``Astrometrics.targets`` that holds one target."""

    def __init__(self) -> None:
        """Hold the target ``M 31`` and remember the lookups."""
        self.held = Target(id="M 31")
        self.lookups: list[tuple[str, Any]] = []

    def get(self, target: str, refresh: bool = False) -> Target | None:
        """Find the one target by id.

        Returns
        -------
        target : `Target` or `None`
            The target when the id is ``M 31``.
        """
        self.lookups.append((target, refresh))
        return self.held if target == "M 31" else None


class _FakeApi:
    """A stand-in `Astrometrics` with methods that take a target."""

    def __init__(self) -> None:
        """Build the stand-in target catalog."""
        self.targets = _FakeTargets()

    def measure(self, target: str | Target, path_or_target: str | Target = "") -> dict:
        """Report the type of what the method received.

        Parameters
        ----------
        target : `str` or `Target`
            The target.
        path_or_target : `str` or `Target`, optional
            A path or a target; never looked up.

        Returns
        -------
        result : `dict`
            The received types and the target's id.
        """
        return {
            "type": type(target).__name__,
            "id": target.id if isinstance(target, Target) else target,
            "other": type(path_or_target).__name__,
        }

    def get(self, target: str | Target) -> dict:
        """Stand in for the lookup method, whose id is not resolved first.

        Parameters
        ----------
        target : `str` or `Target`
            The id.

        Returns
        -------
        result : `dict`
            The received type.
        """
        return {"type": type(target).__name__}


def test_subject_hints_are_recognized() -> None:
    """The object alone, beside `str` or `None`, or in a list, is a subject."""
    assert subject_kind_of(Target).kind.noun == "target"
    assert subject_kind_of(str | Target | None).kind.noun == "target"
    assert subject_kind_of(str | StellarObject).kind.noun == "star"
    assert subject_kind_of(str | ObservationSession).kind.noun == "observing session"
    assert subject_kind_of(ObservationPackage | None).kind.noun == "observation package"
    many = subject_kind_of(list[Target] | None)
    assert many is not None and many.many


def test_other_hints_are_not_subjects() -> None:
    """Plain text, mixed unions and unrelated models are not subjects."""
    assert subject_kind_of(str) is None
    assert subject_kind_of(str | Target | dict) is None
    assert subject_kind_of(Target | StellarObject) is None
    assert subject_kind_of(int | None) is None


def test_the_table_names_each_library_type() -> None:
    """Every row names a class that the library root exports."""
    import astrometricslib
    import wayfindinglib

    roots = {"astrometricslib": astrometricslib, "wayfindinglib": wayfindinglib}
    for (library, class_name), kind in SUBJECT_KINDS.items():
        assert getattr(roots[library], class_name).__name__ == class_name, kind.noun


def test_an_id_becomes_the_object_and_an_unknown_id_is_an_error() -> None:
    """The lookup reads storage again; an unknown id is a reported error."""
    api = _FakeApi()
    parameter = subject_kind_of(str | Target)
    assert resolve_subject(api, parameter, "target", "M 31") is api.targets.held
    assert api.targets.lookups == [("M 31", True)]
    target = Target(id="Given")
    assert resolve_subject(api, parameter, "target", target) is target
    with pytest.raises(NotFoundError) as raised:
        resolve_subject(api, parameter, "target", "Nowhere")
    assert raised.value.report == "error"
    assert "Nowhere" in str(raised.value)


def test_a_list_of_ids_becomes_a_list_of_objects() -> None:
    """Each id of a list is looked up."""
    api = _FakeApi()
    parameter = subject_kind_of(list[Target])
    assert resolve_subject(api, parameter, "targets", ["M 31", "M 31"]) == [api.targets.held] * 2


def test_the_plan_skips_lookup_methods_and_path_parameters() -> None:
    """``get`` takes the id itself, and a ``path_or_target`` may be a path."""
    api = _FakeApi()
    assert set(plan_parameters(api.measure).subjects) == {"target"}
    assert plan_parameters(api.get).subjects == {}


async def test_a_reflected_tool_offers_an_id_and_calls_with_the_object() -> None:
    """The schema shows a string; the method gets the `Target`."""
    registry = ToolRegistry()
    register_reflected_tools(registry, _FakeApi(), {"": "fake"})
    schema = registry.tools["fake_measure"]["tool_def"].input_schema
    assert schema["properties"]["target"]["type"] == "string"
    assert "target's id" in schema["properties"]["target"]["description"]

    reply = json.loads(
        (await registry.execute("fake_measure", {"target": "M 31", "path_or_target": "a"}))[0].text
    )
    assert reply == {"type": "Target", "id": "M 31", "other": "str"}

    refusal = await registry.execute("fake_measure", {"target": "Nowhere"})
    assert refusal[0].text.startswith("not_found:")

    looked_up = json.loads((await registry.execute("fake_get", {"target": "M 31"}))[0].text)
    assert looked_up == {"type": "str"}
