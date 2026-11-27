"""Tests for the owners of the night reads of `review`.

`night_history` holds the owners of `review.nights.get_capture`,
`get_sky_coverage`, `get_ekos_session`, `review.guiding.get_night`,
`query_runs` and `review.alignment.get_pointing_model`. These tests replace
the night analyses with a fake, so they check the choice of analysis, the
argument checks and the paging.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from astrometricslib.exceptions import ConfigurationError, ConflictError, InvalidArgumentError, NotFoundError
from wayfindinglib.tasks.control_tasks import night_analysis, night_history, pointing_log_ingestion
from wayfindinglib.tasks.control_tasks.night_history import sample_evenly, to_plain


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
    "recurring_issues",
    "sky_coverage_analysis",
    "guiding_runs",
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


class _EkosRecord(BaseModel):
    """A small stand-in for a stored Ekos session record."""

    id: str
    session_id: str
    started_at: float
    ended_at: float
    equipment: Any = None
    captures: list[Any] = []
    aborted_captures: list[Any] = []
    autofocus_runs: list[Any] = []
    align_events: list[Any] = []
    guide_state_events: list[Any] = []
    mount_state_events: list[Any] = []
    temperatures: list[Any] = []
    mount_positions: list[Any] = []


def test_to_plain_turns_models_and_nested_data_into_plain_data() -> None:
    """Models, tuples and nested dicts become plain JSON-safe data."""
    plain = to_plain({"a": (_Analysis(flagged=True, recommendations=["x"]),)})
    assert plain == {"a": [{"flagged": True, "recommendations": ["x"]}]}


def test_sample_evenly_keeps_the_ends() -> None:
    """A sample keeps the first and last items and spreads the rest."""
    assert sample_evenly(list(range(10)), 4) == [0, 3, 6, 9]
    assert sample_evenly([1, 2], 5) == [1, 2]
    assert sample_evenly([1, 2, 3], 1) == [1]


def test_a_night_gets_its_capture_or_guiding_analysis(observatory: _Observatory) -> None:
    """Each owner calls its own analysis for the night."""
    assert night_history.get_capture_analysis(observatory, "2026-10-02").flagged is True
    night_history.get_guiding_analysis(observatory, "2026-10-02")
    assert ("capture_night_analysis", "2026-10-02") in observatory.calls
    assert ("guiding_night_analysis", "2026-10-02") in observatory.calls


def test_night_with_nothing_recorded_is_an_error(observatory: _Observatory) -> None:
    """A night with no data raises `NotFoundError`."""
    with pytest.raises(NotFoundError, match="Nothing is recorded"):
        night_history.get_capture_analysis(observatory, "none")


def test_guiding_runs_are_paged_oldest_first(observatory: _Observatory) -> None:
    """Every run comes back by default; `limit` and `offset` cut a page."""
    assert night_history.query_guiding_runs(observatory).total == 60
    page = night_history.query_guiding_runs(observatory, limit=10, offset=50)
    assert [run["id"] for run in page] == list(range(50, 60))
    with pytest.raises(InvalidArgumentError):
        night_history.query_guiding_runs(observatory, offset=-1)


def test_ekos_session_keeps_only_the_sections_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sections not in `include` come back empty; an unknown one is refused."""
    record = _EkosRecord(
        id="known", session_id="2026-09-23", started_at=1.0, ended_at=2.0, captures=[1, 2], equipment="kit"
    )
    monkeypatch.setattr(
        night_analysis, "ekos_session_record", lambda context, ekos_id: record if ekos_id == "known" else None
    )
    assert night_history.get_ekos_session(None, "x") is None
    bare = night_history.get_ekos_session(None, "known")
    assert (bare.captures, bare.equipment) == ([], None)
    assert night_history.get_ekos_session(None, "known", include=["captures"]).captures == [1, 2]
    with pytest.raises(InvalidArgumentError, match="nope"):
        night_history.get_ekos_session(None, "known", include=["nope"])


def test_ekos_sessions_are_listed_by_night(monkeypatch: pytest.MonkeyPatch) -> None:
    """The session list is typed, oldest first, and filters by night."""
    records = [
        _EkosRecord(id=f"s{day}", session_id=f"2026-09-{day:02d}", started_at=float(day), ended_at=day + 0.5)
        for day in (3, 1, 2)
    ]
    context = SimpleNamespace(butler=SimpleNamespace(get_all=lambda name: records))
    page = night_history.query_ekos_sessions(context)
    assert [summary.id for summary in page] == ["s1", "s2", "s3"]
    assert [summary.id for summary in night_history.query_ekos_sessions(context, night="2026-09-02")] == [
        "s2"
    ]


def test_pointing_model_needs_a_location(observatory: _Observatory) -> None:
    """The fit refuses to guess the latitude."""
    observatory.location = None
    with pytest.raises(ConfigurationError, match="observer location"):
        night_history.get_pointing_model(observatory, "2026-10-02")
    observatory.location = {"latitude": 45.0}
    assert night_history.get_pointing_model(observatory, "2026-10-02").flagged is False


def test_sky_coverage_without_an_active_profile_is_a_conflict(observatory: _Observatory) -> None:
    """With no telescope and camera active, sky coverage is a conflict."""
    observatory.location = None
    with pytest.raises(ConflictError, match="No telescope and camera"):
        night_history.get_sky_coverage(observatory)
