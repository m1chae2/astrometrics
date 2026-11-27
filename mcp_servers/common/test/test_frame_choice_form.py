"""Purpose: Tests for the frame filter a client sends instead of frames.

Description: Checks that a `FrameChoice` parameter is offered as a filter
object, that the filter is turned into frames through the public frame
selection (with the night and camera applied), that paths pass through,
and that a frame query is used when the library has one.
"""

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from astrometricslib import FrameRecord, Target
from astrometricslib.arguments import FrameChoice, observing_night_id
from astrometricslib.exceptions import InvalidArgumentError
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


class _FakeTargets:
    """A stand-in for ``Astrometrics.targets`` with three frames."""

    def __init__(self) -> None:
        """Hold one target with frames from two nights and two cameras."""
        self.held = Target(
            id="M 31",
            frames=[
                FrameRecord(path="/f/a.fits", timestamp=_FIRST, camera="ASI 533MM"),
                FrameRecord(path="/f/b.fits", timestamp=_FIRST, camera="ASI 2600MC"),
                FrameRecord(path="/f/c.fits", timestamp=_SECOND, camera="ASI 533MM"),
            ],
        )
        self.selections: list[dict[str, Any]] = []

    def get(self, target: str, refresh: bool = False) -> Target | None:
        """Find the one target by id.

        Returns
        -------
        target : `Target` or `None`
            The target when the id is ``M 31``.
        """
        return self.held if target == "M 31" else None

    def select_frames(self, target: Target, **filters: Any) -> list[FrameRecord]:
        """Return every frame, remembering the filters.

        Returns
        -------
        frames : `list` [`FrameRecord`]
            The target's frames.
        """
        self.selections.append(filters)
        return list(target.frames)


class _FakeApi:
    """A stand-in `Astrometrics` with one method that takes frames."""

    def __init__(self) -> None:
        """Build the stand-in target catalog."""
        self.targets = _FakeTargets()

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


def test_a_filter_chooses_frames_by_night_and_camera() -> None:
    """Night and camera filter the records; the rest go to the library."""
    api = _FakeApi()
    night = observing_night_id(_FIRST)
    chosen = build_frame_choice(
        api, "frames", {"night": night, "camera": "asi 533mm", "filter_name": "L"}, {"target": "M 31"}
    )
    assert [frame.path for frame in chosen] == ["/f/a.fits"]
    assert api.targets.selections == [{"filter_name": "L"}]


def test_paths_pass_through_and_cannot_be_mixed_with_filters() -> None:
    """Paths are given to the method; paths with a filter are refused."""
    api = _FakeApi()
    assert build_frame_choice(api, "frames", {"paths": ["/f/a.fits"]}, {}) == ["/f/a.fits"]
    with pytest.raises(InvalidArgumentError):
        build_frame_choice(api, "frames", {"paths": ["/f/a.fits"], "night": "2026-10-02"}, {})
    with pytest.raises(InvalidArgumentError):
        build_frame_choice(api, "frames", {"night": "2026-10-02"}, {})


def test_a_frame_query_is_used_when_the_library_has_one() -> None:
    """``targets.frames.query`` gets the target and every filter."""
    api = _FakeApi()
    calls: list[tuple[Target, dict[str, Any]]] = []

    class _Frames:
        """A stand-in frame query."""

        def query(self, target: Target, **filters: Any) -> list[str]:
            """Remember the call.

            Returns
            -------
            frames : `list` [`str`]
                A marker.
            """
            calls.append((target, filters))
            return ["queried"]

    api.targets.frames = _Frames()
    assert build_frame_choice(api, "frames", {"night": "2026-10-02"}, {"target": "M 31"}) == ["queried"]
    assert calls == [(api.targets.held, {"night": "2026-10-02"})]


async def test_a_reflected_tool_offers_the_filter_and_calls_with_frames() -> None:
    """The schema is the filter object; the method gets frame records."""
    registry = ToolRegistry()
    register_reflected_tools(registry, _FakeApi(), {"": "fake"})
    schema = registry.tools["fake_predict"]["tool_def"].input_schema["properties"]["frames"]
    assert schema["type"] == "object"
    assert {"night", "since", "until", "camera", "paths", "target"} <= set(schema["properties"])

    reply = await registry.execute("fake_predict", {"target": "M 31", "frames": {"camera": "ASI 2600MC"}})
    assert json.loads(reply[0].text) == {"frames": ["/f/b.fits"]}

    refusal = await registry.execute("fake_predict", {"target": "M 31", "frames": {"telescope": "x"}})
    assert refusal[0].text.startswith("invalid_argument:")
