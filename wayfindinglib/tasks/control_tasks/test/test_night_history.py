"""Tests for the past-night history front door.

`build_night_history` answers `control.history.query`. These tests replace
the night analyses with a fake, so they check the choice of analysis, the
argument checks, and above all that every reply fits under the size limit.
"""

import inspect
import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from astrometricslib import ConfigurationError, ConflictError, InvalidArgumentError, NotFoundError
from wayfindinglib.api.control.history import HistoryControl
from wayfindinglib.tasks.control_tasks import night_analysis, night_history, pointing_log_ingestion
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


class _AlignmentLogs:
    """A fake log database with one plate solve on one night."""

    def get_session_alignment_attempts(self, session_id: str) -> list[dict[str, Any]]:
        """Return one attempt.

        Returns
        -------
        attempts : `list` [`dict` [`str`, `Any`]]
            One aligned solve.
        """
        return [{"status": "aligned", "mount_ra": 10.0, "mount_dec": 20.0, "session_id": session_id}]

    def get_polar_alignments(self, session_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """Return no polar alignment runs.

        Returns
        -------
        logs : `list`
            Always empty.
        """
        return []


class _Observatory:
    """A fake context whose night analyses record how they were called."""

    def __init__(self) -> None:
        """Start with no calls and a known location."""
        self.calls: list[tuple[str, Any]] = []
        self.location: dict[str, float] | None = {"latitude": 45.0}
        self.records = _AlignmentLogs()

    def capture_night_analysis(self, context: Any, session_id: str) -> Any:
        """Record the call; return an analysis unless the night is "none".

        Returns
        -------
        result : `Any`
            The canned answer.
        """
        self.calls.append(("capture_night_analysis", session_id))
        return None if session_id == "none" else _Analysis(flagged=True, recommendations=["a"])

    def guiding_night_analysis(self, context: Any, session_id: str) -> Any:
        """Record the call and return an analysis.

        Returns
        -------
        result : `Any`
            The canned answer.
        """
        self.calls.append(("guiding_night_analysis", session_id))
        return _Analysis(flagged=False, recommendations=[])

    def capture_night_summaries(self, context: Any, latest_nights: int | None = None) -> list[dict]:
        """Record the call and return one summary row.

        Returns
        -------
        result : `Any`
            The canned answer.
        """
        self.calls.append(("capture_night_summaries", latest_nights))
        return [{"sessionId": "2026-10-02"}]

    def guiding_night_summaries(self, context: Any, latest_nights: int | None = None) -> list[dict]:
        """Record the call and return one summary row.

        Returns
        -------
        result : `Any`
            The canned answer.
        """
        self.calls.append(("guiding_night_summaries", latest_nights))
        return [{"sessionId": "2026-10-02"}]

    def recurring_issues(self, context: Any, latest_nights: int | None = None) -> list[dict]:
        """Record the call and return one issue.

        Returns
        -------
        result : `Any`
            The canned answer.
        """
        self.calls.append(("recurring_issues", latest_nights))
        return [{"issue": "x"}]

    def sky_coverage_analysis(self, context: Any) -> Any:
        """Return a small analysis.

        Returns
        -------
        result : `Any`
            The canned answer, or `None` when no profile is active.
        """
        if self.location is None:
            return None
        return _Analysis(flagged=False, recommendations=["sky"])

    def guiding_runs(self, context: Any, session_id: str | None = None) -> list[dict]:
        """Return sixty large runs.

        Returns
        -------
        result : `Any`
            The canned answer.
        """
        return [{"id": number, "pad": "z" * 900} for number in range(60)]

    def ekos_session_summaries(self, context: Any) -> list[dict]:
        """Return thirty session summaries, one per night.

        Returns
        -------
        result : `Any`
            The canned answer.
        """
        return [{"id": f"s{number}", "sessionId": f"2026-09-{number:02d}"} for number in range(1, 31)]

    def ekos_session_record(self, context: Any, session_file_id: str) -> Any:
        """Return the large session for "known", otherwise nothing.

        Returns
        -------
        result : `Any`
            The canned answer.
        """
        return _context() if session_file_id == "known" else None

    def observer_location(self) -> dict[str, float] | None:
        """Return the location the test set.

        Returns
        -------
        result : `Any`
            The canned answer.
        """
        return self.location

    def compute_pointing_model(self, context: Any, records: Any, session_id: str) -> Any:
        """Record the call and return a small model.

        Returns
        -------
        result : `Any`
            The canned answer.
        """
        self.calls.append(("compute_pointing_model", session_id))
        return _Analysis(flagged=False, recommendations=[])


ANALYSES = (
    "capture_night_analysis",
    "guiding_night_analysis",
    "capture_night_summaries",
    "guiding_night_summaries",
    "recurring_issues",
    "sky_coverage_analysis",
    "guiding_runs",
    "ekos_session_summaries",
    "ekos_session_record",
)
"""The `night_analysis` functions the fake stands in for."""


@pytest.fixture
def observatory(monkeypatch: pytest.MonkeyPatch) -> _Observatory:
    """Route the night analyses and the pointing fit to a fresh fake.

    Returns
    -------
    observatory : `_Observatory`
        The fake, which is also passed as the context.
    """
    fake = _Observatory()
    for name in ANALYSES:
        monkeypatch.setattr(night_analysis, name, getattr(fake, name))
    monkeypatch.setattr(pointing_log_ingestion, "compute_pointing_model", fake.compute_pointing_model)
    return fake


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


def test_unknown_kind_lists_the_kinds(observatory: _Observatory) -> None:
    """A bad kind is refused with the list of kinds."""
    with pytest.raises(InvalidArgumentError, match="kind must be one of"):
        build_night_history(observatory, "everything")


@pytest.mark.parametrize(
    ("kind", "arguments"),
    [
        ("sky_coverage", {"session_id": "2026-10-02"}),
        ("recurring_issues", {"ekos_file_id": "x"}),
        ("capture", {"include": ["captures"]}),
        ("guiding_runs", {"ekos_file_id": "x"}),
    ],
)
def test_arguments_a_kind_does_not_use_are_refused(
    observatory: _Observatory, kind: str, arguments: dict[str, Any]
) -> None:
    """An argument that does not apply to the kind is an error, not ignored."""
    with pytest.raises(InvalidArgumentError, match="does not use"):
        build_night_history(observatory, kind, **arguments)


def test_night_analysis_and_summary_choose_the_right_method(observatory: _Observatory) -> None:
    """A night gets its analysis; no night gets recent summaries."""
    assert build_night_history(observatory, "capture", session_id="2026-10-02")["analysis"]["flagged"] is True
    build_night_history(observatory, "guiding", session_id="2026-10-02")
    build_night_history(observatory, "capture", limit=7)
    build_night_history(observatory, "guiding", limit=1000)
    build_night_history(observatory, "recurring_issues", limit=0)
    assert ("capture_night_analysis", "2026-10-02") in observatory.calls
    assert ("guiding_night_analysis", "2026-10-02") in observatory.calls
    assert ("capture_night_summaries", 7) in observatory.calls
    assert ("guiding_night_summaries", 50) in observatory.calls
    assert ("recurring_issues", 1) in observatory.calls


def test_night_with_nothing_recorded_is_an_error(observatory: _Observatory) -> None:
    """A night with no data raises `NotFoundError`."""
    with pytest.raises(NotFoundError, match="Nothing is recorded"):
        build_night_history(observatory, "capture", session_id="none")


def test_guiding_runs_are_the_newest_and_fit_the_budget(observatory: _Observatory) -> None:
    """Sixty large runs come back as the newest rows that fit."""
    reply = build_night_history(observatory, "guiding_runs", limit=50)
    assert reply["total"] == 60
    assert reply_size(reply) <= MAXIMUM_REPLY_CHARACTERS
    assert reply["runs"][-1]["id"] == 59


def test_ekos_list_filters_by_night_and_takes_the_newest(observatory: _Observatory) -> None:
    """The session list can be narrowed to one night and is limited."""
    assert build_night_history(observatory, "ekos_sessions", limit=5)["shown"] == 5
    only = build_night_history(observatory, "ekos_sessions", session_id="2026-09-07")
    assert [row["id"] for row in only["sessions"]] == ["s7"]


def test_ekos_session_overview_and_sections(observatory: _Observatory) -> None:
    """One session gives an overview, or the chosen sections, sampled."""
    overview = build_night_history(observatory, "ekos_sessions", ekos_file_id="known")
    assert overview["session"]["overview"]["section_counts"]["mount_positions"] == 600
    detail = build_night_history(
        observatory, "ekos_sessions", ekos_file_id="known", include=["mount_positions", "captures"], limit=10
    )
    positions = detail["session"]["mount_positions"]
    assert (positions["total"], positions["shown"]) == (600, 10)
    assert reply_size(detail) <= MAXIMUM_REPLY_CHARACTERS


def test_ekos_errors_name_the_problem(observatory: _Observatory) -> None:
    """An unknown session or section raises the matching error."""
    with pytest.raises(NotFoundError, match="No Ekos session"):
        build_night_history(observatory, "ekos_sessions", ekos_file_id="x")
    with pytest.raises(InvalidArgumentError, match="Unknown section"):
        build_night_history(observatory, "ekos_sessions", ekos_file_id="known", include=["nope"])


def test_pointing_model_needs_a_night_and_a_location(observatory: _Observatory) -> None:
    """The fit refuses to mix nights or to guess the latitude."""
    with pytest.raises(InvalidArgumentError, match="needs a session_id"):
        build_night_history(observatory, "pointing_model")
    observatory.location = None
    with pytest.raises(ConfigurationError, match="observer location"):
        build_night_history(observatory, "pointing_model", session_id="2026-10-02")
    observatory.location = {"latitude": 45.0}
    reply = build_night_history(observatory, "pointing_model", session_id="2026-10-02")
    assert reply["model"]["flagged"] is False


def test_every_reply_is_json_and_within_the_limit(observatory: _Observatory) -> None:
    """Every kind produces JSON text under the limit."""
    for kind in night_history.KINDS:
        night = {"session_id": "2026-10-02"} if "session_id" in night_history.KIND_ARGUMENTS[kind] else {}
        reply = build_night_history(observatory, kind, limit=50, **night)
        assert len(json.dumps(reply, indent=2, default=str)) <= MAXIMUM_REPLY_CHARACTERS, kind


def test_control_history_has_the_front_door_and_limits() -> None:
    """`control.history` has `query`; the summaries take a limit."""
    assert "query" in dir(HistoryControl)
    for name in ("capture_night_summaries", "guiding_night_summaries", "recurring_issues"):
        assert "latest_nights" in inspect.signature(getattr(night_analysis, name)).parameters


def test_sky_coverage_without_an_active_profile_is_a_conflict(observatory: _Observatory) -> None:
    """With no telescope and camera active, sky coverage is a conflict."""
    observatory.location = None
    with pytest.raises(ConflictError, match="No telescope and camera"):
        build_night_history(observatory, "sky_coverage")
