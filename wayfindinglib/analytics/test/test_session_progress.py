"""Purpose: Unit tests for the queue card progress and the run states.

Description: Checks the counts of captured, flagged and not-judged frames for
each entry, the entry being imaged now (a recent frame or a running entry),
the typed run states (upcoming, imaging, in progress, complete, partly done,
not run, unknown) and that a past night with nothing captured reads "not
run". Uses in-memory frames and a fixed clock, never real data.
"""

from datetime import UTC, datetime, timedelta

from astrometricslib import FilterType
from wayfindinglib.analytics.frame_counting import TargetFrameReader
from wayfindinglib.analytics.session_progress import (
    IMAGING_NOW_WINDOW_SECONDS,
    build_session_progress,
    current_night,
)
from wayfindinglib.analytics.test.frame_counting_helpers import (
    EQUIPMENT,
    FakeLibrary,
    judgment_row,
    make_frame,
)
from wayfindinglib.models.planning.observation_package import ExposureRequest, FrameType
from wayfindinglib.models.session.observation_session import (
    ObservationSession,
    QueuedObservationPackage,
    QueueEntryStatus,
    RunState,
    StartTimeMode,
)
from wayfindinglib.models.session.session_progress import SessionProgress

NOW = datetime(2026, 10, 25, 3, 0, tzinfo=UTC)
START = NOW - timedelta(hours=6)


def _entry(entry_id: str, target_id: str, count: int, **changes: object) -> QueuedObservationPackage:
    """Build a queue entry for one light row of 60 s frames.

    Parameters
    ----------
    entry_id : `str`
        The entry id.
    target_id : `str`
        The target.
    count : `int`
        How many frames the row asks for.
    **changes : `object`
        Entry fields to change.

    Returns
    -------
    entry : `QueuedObservationPackage`
        The entry.
    """
    row = ExposureRequest(
        frame_type=FrameType.LIGHT, filter=FilterType.L, exposure_sec=60.0, count=count, counted_from=START
    )
    return QueuedObservationPackage(
        id=entry_id,
        observation_package_id=f"p-{entry_id}",
        target_id=target_id,
        exposure_requests=[row],
        start_time_mode=StartTimeMode.SOONEST,
        **changes,
    )


def _session(entries: list[QueuedObservationPackage], night_offset_days: int = 0) -> ObservationSession:
    """Build a session for tonight, or an earlier or later night.

    Parameters
    ----------
    entries : `list` [`QueuedObservationPackage`]
        The queue.
    night_offset_days : `int`, optional
        Days from tonight (negative for a past night).

    Returns
    -------
    session : `ObservationSession`
        The session.
    """
    return ObservationSession(
        id="s1",
        night_date=current_night(NOW) + timedelta(days=night_offset_days),
        site_profile_id="site",
        telescope_id="scope1",
        camera_id="cam1",
        queue=entries,
        created_at=START,
    )


def _frames_at(target_id: str, ages_minutes: list[int]) -> list:
    """Build frames taken the given number of minutes before now.

    Parameters
    ----------
    target_id : `str`
        The target.
    ages_minutes : `list` [`int`]
        How long before now each frame was taken.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        The frames.
    """
    return [
        make_frame(index, target_id=target_id, taken=NOW - timedelta(minutes=age))
        for index, age in enumerate(ages_minutes)
    ]


def _progress(session: ObservationSession, library: FakeLibrary) -> SessionProgress:
    """Count a session's progress against a fake library at the fixed clock.

    Parameters
    ----------
    session : `ObservationSession`
        The session.
    library : `FakeLibrary`
        The fake library.

    Returns
    -------
    progress : `SessionProgress`
        The progress.
    """
    return build_session_progress(session, TargetFrameReader(library), EQUIPMENT, NOW)


def test_an_entry_counts_captured_flagged_and_not_judged_frames() -> None:
    """The card shows captured of requested, with flagged and unjudged."""
    frames = _frames_at("M 81", [200, 190, 180, 170])
    rows = [judgment_row(frames[0], failed=True, row_id=1), judgment_row(frames[1], row_id=2)]
    library = FakeLibrary({"M 81": frames}, rows)
    progress = _progress(_session([_entry("e1", "M 81", 10)]), library)
    entry = progress.entries[0]
    assert (entry.requested, entry.captured, entry.flagged, entry.not_judged) == (10, 4, 1, 2)
    assert entry.rows[0].judged == 2
    assert entry.state == RunState.IN_PROGRESS


def test_the_entry_with_a_recent_frame_is_imaging_now() -> None:
    """A frame newer than the window marks its entry as imaging now."""
    recent = IMAGING_NOW_WINDOW_SECONDS / 60 / 2
    library = FakeLibrary({"M 81": _frames_at("M 81", [int(recent)]), "M 13": _frames_at("M 13", [120])})
    session = _session([_entry("e1", "M 81", 10), _entry("e2", "M 13", 10)])
    progress = _progress(session, library)
    assert progress.imaging_now_entry_id == "e1"
    assert progress.entries[0].imaging_now and progress.entries[0].state == RunState.IMAGING
    assert not progress.entries[1].imaging_now
    assert progress.run_state == RunState.IMAGING


def test_a_running_entry_is_imaging_now_even_with_no_frame_yet() -> None:
    """An entry marked running counts as imaging now."""
    library = FakeLibrary({"M 81": []})
    progress = _progress(_session([_entry("e1", "M 81", 10, status=QueueEntryStatus.RUNNING)]), library)
    assert progress.imaging_now_entry_id == "e1"


def test_every_row_met_makes_the_entry_and_the_session_complete() -> None:
    """Judged frames that reach the requested count make the entry complete."""
    frames = _frames_at("M 81", [200, 190, 180])
    library = FakeLibrary(
        {"M 81": frames}, [judgment_row(frame, row_id=index) for index, frame in enumerate(frames)]
    )
    progress = _progress(_session([_entry("e1", "M 81", 3)]), library)
    assert progress.entries[0].state == RunState.COMPLETE
    assert progress.run_state == RunState.COMPLETE
    assert progress.imaging_now_entry_id is None


def test_frames_not_yet_judged_do_not_complete_an_entry() -> None:
    """Frames with no judgment are captured but the entry is not complete."""
    library = FakeLibrary({"M 81": _frames_at("M 81", [200, 190, 180])})
    progress = _progress(_session([_entry("e1", "M 81", 3)]), library)
    assert progress.entries[0].captured == 3
    assert progress.entries[0].state != RunState.COMPLETE


def test_a_past_night_with_nothing_captured_is_not_run() -> None:
    """A past night with no frames reads "not run"."""
    library = FakeLibrary({"M 81": []})
    progress = _progress(_session([_entry("e1", "M 81", 10)], night_offset_days=-1), library)
    assert progress.entries[0].state == RunState.NOT_RUN
    assert progress.run_state == RunState.NOT_RUN


def test_a_past_night_with_some_frames_is_partly_done() -> None:
    """A past night with some of the frames reads "partly done"."""
    library = FakeLibrary({"M 81": _frames_at("M 81", [200, 190])})
    progress = _progress(_session([_entry("e1", "M 81", 10)], night_offset_days=-1), library)
    assert progress.run_state == RunState.PARTLY_DONE
    assert progress.imaging_now_entry_id is None


def test_a_future_night_is_upcoming() -> None:
    """A night that has not begun reads "upcoming"."""
    library = FakeLibrary({"M 81": []})
    progress = _progress(_session([_entry("e1", "M 81", 10)], night_offset_days=2), library)
    assert progress.run_state == RunState.UPCOMING


def test_a_session_with_no_entries_is_not_run_after_its_night() -> None:
    """An empty past session is "not run"; one tonight is upcoming."""
    library = FakeLibrary({})
    assert _progress(_session([], night_offset_days=-1), library).run_state == RunState.NOT_RUN
    assert _progress(_session([]), library).run_state == RunState.UPCOMING


def test_frames_that_cannot_be_read_give_an_unknown_state() -> None:
    """Missing data is not "not run": the state is unknown."""
    library = FakeLibrary({"M 81": _frames_at("M 81", [200])})
    library.read_failures.add("M 81")
    progress = _progress(_session([_entry("e1", "M 81", 10)], night_offset_days=-1), library)
    assert progress.entries[0].state == RunState.UNKNOWN
    assert "could not be read" in progress.entries[0].note
    assert progress.run_state == RunState.UNKNOWN
