"""Tests for the past-night history front door.

`build_night_history` answers questions about recorded nights for an AI
client. These tests use a fake observatory, so they check the choice of
method, the argument checks, and above all that every reply fits under the
size limit.
"""

import inspect
import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from wayfindinglib.api.control_registry import ObservatoryControl
from wayfindinglib.tasks.control_tasks import night_history
from wayfindinglib.tasks.control_tasks.night_history import (
    MAXIMUM_REPLY_CHARACTERS,
    build_night_history,
    fit_to_budget,
    reply_size,
    sample_evenly,
    shrink,
    to_plain,
)


class _Analysis(BaseModel):
    """A small stand-in for a nightly analysis."""

    flagged: bool
    recommendations: list[str]


def _context(positions: int = 600) -> SimpleNamespace:
    """Build a fake Ekos session with many mount positions.

    Returns
    -------
    context : `types.SimpleNamespace`
        The session, with the sections `ekos_overview` reads.
    """
    return SimpleNamespace(
        id="2026-09-23T20-31-48",
        session_id="2026-09-23",
        started_at=1.0,
        ended_at=2.0,
        equipment=SimpleNamespace(equipment_fingerprint="fp", model_dump=lambda mode="json": {"fp": "x"}),
        captures=[{"n": number} for number in range(40)],
        aborted_captures=[],
        autofocus_runs=[],
        align_events=[],
        guide_state_events=[],
        mount_state_events=[],
        temperatures=[],
        mount_positions=[{"ra": number, "text": "y" * 80} for number in range(positions)],
    )


class _Observatory:
    """A fake observatory that records how it was called."""

    def __init__(self) -> None:
        """Start with no calls and a known location."""
        self.calls: list[tuple[str, Any]] = []
        self.location: dict[str, float] | None = {"latitude": 45.0}

    def analyze_capture_session(self, session_id: str) -> Any:
        self.calls.append(("analyze_capture_session", session_id))
        return None if session_id == "none" else _Analysis(flagged=True, recommendations=["a"])

    def analyze_guiding_session(self, session_id: str) -> Any:
        self.calls.append(("analyze_guiding_session", session_id))
        return _Analysis(flagged=False, recommendations=[])

    def summarize_capture_sessions(self, latest_nights: int | None = None) -> list[dict]:
        self.calls.append(("summarize_capture_sessions", latest_nights))
        return [{"sessionId": "2026-10-02"}]

    def summarize_guiding_sessions(self, latest_nights: int | None = None) -> list[dict]:
        self.calls.append(("summarize_guiding_sessions", latest_nights))
        return [{"sessionId": "2026-10-02"}]

    def summarize_recurring_issues(self, latest_nights: int | None = None) -> list[dict]:
        self.calls.append(("summarize_recurring_issues", latest_nights))
        return [{"issue": "x"}]

    def analyze_sky_coverage(self) -> Any:
        return _Analysis(flagged=False, recommendations=["sky"])

    def list_guiding_runs(self, session_id: str | None = None) -> list[dict]:
        return [{"id": number, "pad": "z" * 900} for number in range(60)]

    def list_ekos_session_summaries(self) -> list[dict]:
        return [{"id": f"s{number}", "sessionId": f"2026-09-{number:02d}"} for number in range(1, 31)]

    def get_ekos_session_context(self, session_file_id: str) -> Any:
        return _context() if session_file_id == "known" else None

    def get_observer_location(self) -> dict[str, float] | None:
        return self.location

    def get_pointing_model(self, session_id: str) -> Any:
        self.calls.append(("get_pointing_model", session_id))
        return _Analysis(flagged=False, recommendations=[])


def test_to_plain_turns_models_and_nested_data_into_plain_data() -> None:
    """Models, lists and dicts become plain JSON-safe data."""
    assert to_plain({"a": [_Analysis(flagged=True, recommendations=["x"])]}) == {
        "a": [{"flagged": True, "recommendations": ["x"]}]
    }


def test_shrink_cuts_lists_text_and_depth() -> None:
    """Each limit is applied, and a note says what was left out."""
    shrunk = shrink({"items": list(range(10)), "text": "x" * 100, "deep": {"a": {"b": 1}}}, 3, 20, 2)
    assert shrunk["items"] == [0, 1, 2, "<7 more items left out>"]
    assert len(shrunk["text"]) == 20
    assert shrunk["deep"]["a"] == "<1 fields left out>"


def test_sample_evenly_keeps_the_ends() -> None:
    """A long list is sampled across its whole length."""
    sample = sample_evenly(list(range(100)), 5)
    assert sample[0] == 0
    assert sample[-1] == 99
    assert len(sample) == 5
    assert sample_evenly([1, 2], 5) == [1, 2]
    assert sample_evenly([1, 2, 3], 1) == [1]


def test_small_reply_is_unchanged() -> None:
    """A reply within the budget comes back as it was."""
    payload = {"kind": "x", "rows": [1, 2, 3]}
    assert fit_to_budget(payload) is payload


def test_long_list_is_cut_to_its_newest_rows() -> None:
    """Dropping old rows keeps the newest ones complete and says so."""
    rows = [{"night": number, "pad": "p" * 200} for number in range(200)]
    reply = fit_to_budget({"kind": "x", "shown": 200, "rows": rows}, budget=5000)
    assert reply["truncated"] is True
    assert reply["rows"][-1] == rows[-1]
    assert reply["shown"] == len(reply["rows"])
    assert reply_size(reply) <= 5000


def test_deep_reply_is_shrunk_and_flagged() -> None:
    """When rows cannot be cut, nested data is shrunk until it fits."""
    payload = {"kind": "x", "analysis": {"sections": {name: ["w" * 400] * 30 for name in "abcdefgh"}}}
    reply = fit_to_budget(payload, budget=4000)
    assert reply["truncated"] is True
    assert reply_size(reply) <= 4000


def test_hopeless_reply_falls_back_to_the_field_names() -> None:
    """If nothing fits, the reply names its fields and says it was cut."""
    reply = fit_to_budget({"kind": "x", "blob": "q" * 100_000, "other": 1}, budget=300)
    assert reply["truncated"] is True
    assert reply["fields"] == ["blob", "kind", "other"]


def test_unknown_kind_lists_the_kinds() -> None:
    """A bad kind gets a plain error."""
    assert "kind must be one of" in build_night_history(_Observatory(), "everything")["error"]


def test_night_analysis_and_summary_choose_the_right_method() -> None:
    """A night gets its analysis; no night gets recent summaries."""
    observatory = _Observatory()
    assert build_night_history(observatory, "capture", session_id="2026-10-02")["analysis"]["flagged"] is True
    build_night_history(observatory, "guiding", session_id="2026-10-02")
    build_night_history(observatory, "capture", limit=7)
    build_night_history(observatory, "guiding", limit=1000)
    build_night_history(observatory, "recurring_issues", limit=0)
    assert ("analyze_capture_session", "2026-10-02") in observatory.calls
    assert ("analyze_guiding_session", "2026-10-02") in observatory.calls
    assert ("summarize_capture_sessions", 7) in observatory.calls
    assert ("summarize_guiding_sessions", 50) in observatory.calls
    assert ("summarize_recurring_issues", 1) in observatory.calls


def test_night_with_nothing_recorded_is_an_error() -> None:
    """A night with no data says so."""
    assert "Nothing is recorded" in build_night_history(_Observatory(), "capture", session_id="none")["error"]


def test_guiding_runs_are_the_newest_and_fit_the_budget() -> None:
    """Sixty large runs come back as the newest rows that fit."""
    reply = build_night_history(_Observatory(), "guiding_runs", limit=50)
    assert reply["total"] == 60
    assert reply_size(reply) <= MAXIMUM_REPLY_CHARACTERS
    assert reply["runs"][-1]["id"] == 59


def test_ekos_list_filters_by_night_and_takes_the_newest() -> None:
    """The session list can be narrowed to one night and is limited."""
    observatory = _Observatory()
    assert build_night_history(observatory, "ekos_sessions", limit=5)["shown"] == 5
    only = build_night_history(observatory, "ekos_sessions", session_id="2026-09-07")
    assert [row["id"] for row in only["sessions"]] == ["s7"]


def test_ekos_session_overview_and_sections() -> None:
    """One session gives an overview, or the chosen sections, sampled."""
    observatory = _Observatory()
    overview = build_night_history(observatory, "ekos_sessions", ekos_file_id="known")
    assert overview["session"]["overview"]["section_counts"]["mount_positions"] == 600
    detail = build_night_history(
        observatory, "ekos_sessions", ekos_file_id="known", include=["mount_positions", "captures"], limit=10
    )
    positions = detail["session"]["mount_positions"]
    assert (positions["total"], positions["shown"]) == (600, 10)
    assert reply_size(detail) <= MAXIMUM_REPLY_CHARACTERS


def test_ekos_errors_name_the_problem() -> None:
    """An unknown session or section is reported."""
    observatory = _Observatory()
    assert "No Ekos session" in build_night_history(observatory, "ekos_sessions", ekos_file_id="x")["error"]
    unknown = build_night_history(observatory, "ekos_sessions", ekos_file_id="known", include=["nope"])
    assert "Unknown section" in unknown["error"]


def test_pointing_model_needs_a_night_and_a_location() -> None:
    """The fit refuses to mix nights or to guess the latitude."""
    observatory = _Observatory()
    assert "needs a session_id" in build_night_history(observatory, "pointing_model")["error"]
    observatory.location = None
    assert (
        "observer location"
        in build_night_history(observatory, "pointing_model", session_id="2026-10-02")["error"]
    )
    observatory.location = {"latitude": 45.0}
    reply = build_night_history(observatory, "pointing_model", session_id="2026-10-02")
    assert reply["model"]["flagged"] is False


def test_every_reply_is_json_and_within_the_limit() -> None:
    """Every kind produces JSON text under the limit."""
    observatory = _Observatory()
    for kind in night_history.KINDS:
        reply = build_night_history(observatory, kind, session_id="2026-10-02", limit=50)
        assert len(json.dumps(reply, indent=2, default=str)) <= MAXIMUM_REPLY_CHARACTERS, kind


def test_observatory_control_has_the_new_front_door_and_limits() -> None:
    """`ObservatoryControl` has `night_history`; summaries take a limit."""
    assert "night_history" in dir(ObservatoryControl)
    for name in ("summarize_capture_sessions", "summarize_guiding_sessions", "summarize_recurring_issues"):
        assert "latest_nights" in inspect.signature(getattr(ObservatoryControl, name)).parameters


@pytest.mark.parametrize("name", ["observatory_night_history"])
def test_the_mcp_server_offers_the_tool(name: str) -> None:
    """The reflected MCP tool has the documented arguments."""
    from wayfindinglib.mcp.tool_registry import registry

    schema = registry.tools[name]["tool_def"].inputSchema
    assert schema["required"] == ["kind"]
    assert set(schema["properties"]) == {"kind", "session_id", "ekos_file_id", "include", "limit"}
