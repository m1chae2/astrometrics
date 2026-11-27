"""Purpose: Tests for the frame filter a client sends instead of frames.

Description: Checks that a `FrameChoice` parameter is offered as a filter
object, that the filter is turned into frames through the public frame query
(``targets.frames.query``, with a configuration id looked up first), and that
paths pass through.
"""

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from astrometricslib import FrameRecord, Target
from astrometricslib.arguments import FrameChoice, observing_night_id
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from mcp_servers.common.frame_choice_form import build_frame_choice, is_frame_choice
from mcp_servers.common.reflection import register_reflected_tools
from mcp_servers.common.tool_registry import ToolRegistry

pytestmark = pytest.mark.anyio

_FIRST = datetime(2026, 10, 3, 4, 0, tzinfo=UTC).timestamp()
_SECOND = datetime(2026, 10, 9, 4, 0, tzinfo=UTC).timestamp()


@pytest.fixture
def anyio_backend() -> str:
    """Run the async tests of this module on asyncio.

    Returns
    -------
    backend : `str`
        The anyio backend name.
    """
    return "asyncio"


class _FakeFrames:
    """A stand-in for ``targets.frames`` that filters by night and gain."""

    def __init__(self) -> None:
        """Start with no recorded queries."""
        self.queries: list[dict[str, Any]] = []

    def query(self, target: Target, **filters: Any) -> list[FrameRecord]:
        """Keep the frames of the night and gain asked; log the filters.

        Returns
        -------
        frames : `list` [`FrameRecord`]
            The matching frames of the target.
        """
        self.queries.append(filters)
        night, gain = filters.get("night"), filters.get("gain")
        return [
            frame
            for frame in target.frames
            if (night is None or observing_night_id(frame.timestamp) == night)
            and (gain is None or frame.iso == str(gain))
        ]


class _FakeTargets:
    """A stand-in for ``Astrometrics.targets`` with three frames."""

    def __init__(self) -> None:
        """Hold one target with frames from two nights and two gains."""
        self.held = Target(
            id="M 31",
            frames=[
                FrameRecord(path="/f/a.fits", timestamp=_FIRST, iso="100"),
                FrameRecord(path="/f/b.fits", timestamp=_FIRST, iso="200"),
                FrameRecord(path="/f/c.fits", timestamp=_SECOND, iso="100"),
            ],
        )
        self.frames = _FakeFrames()

    def get(self, target: str) -> Target | None:
        """Find the one target by id.

        Returns
        -------
        target : `Target` or `None`
            The target when the id is ``M 31``.
        """
        return self.held if target == "M 31" else None


class _FakeConfigurations:
    """A stand-in for ``Astrometrics.observatories.configurations``."""

    def get(self, configuration_id: str) -> object | None:
        """Find a configuration by id.

        Returns
        -------
        configuration : `object` or `None`
            A marker for the id ``refractor``, else `None`.
        """
        return "the refractor" if configuration_id == "refractor" else None


class _FakeApi:
    """A stand-in `Astrometrics` with one method that takes frames."""

    def __init__(self) -> None:
        """Build the stand-in target catalog."""
        self.targets = _FakeTargets()
        self.observatories = type("Observatories", (), {"configurations": _FakeConfigurations()})()

    def predict(self, target: str | Target, frames: FrameChoice | None = None) -> dict:
        """Report what the method received.

        Parameters
        ----------
        target : `str` or `Target`
            The target.
        frames : `FrameChoice`, optional
            The frames.

        Returns
        -------
        result : `dict`
            The frame paths or the values received.
        """
        return {"frames": [getattr(frame, "path", frame) for frame in frames or []]}


def test_the_frame_choice_hint_is_recognized() -> None:
    """`FrameChoice` (or it or `None`) is a frame choice; a list is not."""
    assert is_frame_choice(FrameChoice)
    assert is_frame_choice(FrameChoice | None)
    assert not is_frame_choice(list[FrameRecord] | None)


def test_a_filter_goes_to_the_frame_query() -> None:
    """Every filter reaches ``targets.frames.query`` with the target."""
    api = _FakeApi()
    night = observing_night_id(_FIRST)
    chosen = build_frame_choice(
        api, "frames", {"night": night, "gain": 100, "filter_name": "L"}, {"target": "M 31"}
    )
    assert [frame.path for frame in chosen] == ["/f/a.fits"]
    assert api.targets.frames.queries == [{"night": night, "gain": 100, "filter_name": "L"}]


def test_a_configuration_id_becomes_the_configuration() -> None:
    """The configuration is looked up by id; an unknown id is refused."""
    api = _FakeApi()
    build_frame_choice(api, "frames", {"configuration": "refractor"}, {"target": "M 31"})
    assert api.targets.frames.queries == [{"configuration": "the refractor"}]
    with pytest.raises(NotFoundError):
        build_frame_choice(api, "frames", {"configuration": "nope"}, {"target": "M 31"})


def test_paths_pass_through_and_cannot_be_mixed_with_filters() -> None:
    """Paths are given to the method; paths with a filter are refused."""
    api = _FakeApi()
    assert build_frame_choice(api, "frames", {"paths": ["/f/a.fits"]}, {}) == ["/f/a.fits"]
    with pytest.raises(InvalidArgumentError):
        build_frame_choice(api, "frames", {"paths": ["/f/a.fits"], "night": "2026-10-02"}, {})
    with pytest.raises(InvalidArgumentError):
        build_frame_choice(api, "frames", {"night": "2026-10-02"}, {})


async def test_a_reflected_tool_offers_the_filter_and_calls_with_frames() -> None:
    """The schema is the filter object; the method gets frame records."""
    registry = ToolRegistry()
    register_reflected_tools(registry, _FakeApi(), {"": "fake"})
    schema = registry.tools["fake_predict"]["tool_def"].input_schema["properties"]["frames"]
    assert schema["type"] == "object"
    assert {"night", "since", "until", "configuration", "gain", "paths", "target"} <= set(
        schema["properties"]
    )

    reply = await registry.execute("fake_predict", {"target": "M 31", "frames": {"gain": 200}})
    assert json.loads(reply[0].text) == {"frames": ["/f/b.fits"]}

    refusal = await registry.execute("fake_predict", {"target": "M 31", "frames": {"telescope": "x"}})
    assert refusal[0].text.startswith("invalid_argument:")
