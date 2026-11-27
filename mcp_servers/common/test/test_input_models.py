"""Purpose: Tests for building a method's input models from a client's JSON.

Description: Checks which hints count as an input model, that the tool's
schema is the model's own JSON schema with its nested definitions at the
top, that valid JSON reaches the method as the model, and that JSON that
does not fit is refused with `InvalidArgumentError`.
"""

import json
from typing import Any

import jsonschema
import pytest
from pydantic import BaseModel

from astrometricslib.arguments import Page
from astrometricslib.exceptions import InvalidArgumentError
from mcp_servers.common.input_models import build_model_argument, model_parameter_of
from mcp_servers.common.reflection import register_reflected_tools
from mcp_servers.common.tool_registry import ToolRegistry

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


class _Corner(BaseModel):
    """One corner of a zone."""

    azimuth_deg: float
    altitude_deg: float


class _Zone(BaseModel):
    """A zone with nested corners, so its schema has ``$defs``."""

    name: str
    corners: list[_Corner]


class _FakeApi:
    """A stand-in API with one method that takes models."""

    def draw(self, zone: _Zone | dict[str, Any], extra: list[_Zone] | None = None) -> dict:
        """Report what the method received.

        Parameters
        ----------
        zone : `_Zone`
            The zone.
        extra : `list` [`_Zone`], optional
            More zones.

        Returns
        -------
        result : `dict`
            The received types.
        """
        return {"zone": type(zone).__name__, "extra": [type(item).__name__ for item in extra or []]}


def test_model_hints_are_recognized() -> None:
    """A model, a model or a dict, and lists of them are input models."""
    assert model_parameter_of(_Zone).model is _Zone
    assert model_parameter_of(_Zone | dict[str, Any] | None).model is _Zone
    listed = model_parameter_of(list[_Zone | dict[str, Any]] | None)
    assert listed is not None and listed.many
    assert model_parameter_of(bool | _Zone) is None
    assert model_parameter_of(Page[_Zone]) is None
    assert model_parameter_of(dict[str, list[_Zone]]) is None


def test_json_that_does_not_fit_is_refused() -> None:
    """A wrong field type is an `InvalidArgumentError` that names it."""
    parameter = model_parameter_of(_Zone)
    built = build_model_argument(parameter, "zone", {"name": "tree", "corners": []})
    assert isinstance(built, _Zone)
    with pytest.raises(InvalidArgumentError) as raised:
        build_model_argument(parameter, "zone", {"name": "tree", "corners": [{"azimuth_deg": "east"}]})
    assert "corners.0.azimuth_deg" in str(raised.value)


async def test_a_reflected_tool_offers_the_model_schema_and_builds_the_model() -> None:
    """The nested definitions sit at the top, and the method gets models."""
    registry = ToolRegistry()
    register_reflected_tools(registry, _FakeApi(), {"": "fake"})
    schema = registry.tools["fake_draw"]["tool_def"].input_schema
    jsonschema.Draft202012Validator.check_schema(schema)
    assert "_Corner" in schema["$defs"]
    assert schema["properties"]["zone"]["properties"]["name"]["type"] == "string"
    assert schema["properties"]["extra"]["type"] == "array"

    zone = {"name": "roof", "corners": [{"azimuth_deg": 10, "altitude_deg": 20}]}
    reply = await registry.execute("fake_draw", {"zone": zone, "extra": [zone]})
    assert json.loads(reply[0].text) == {"zone": "_Zone", "extra": ["_Zone"]}

    refusal = await registry.execute(
        "fake_draw", {"zone": {"name": "roof", "corners": [{"azimuth_deg": "x"}]}}
    )
    assert refusal[0].text.startswith("invalid_argument:")
