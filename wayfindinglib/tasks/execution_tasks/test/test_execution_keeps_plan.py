"""Purpose: Test that Execution's session saves keep what Planning wrote.

Description: Execution holds its own copy of an observing session while it
runs, and saves it again and again. Planning can change the stored plan in
the meantime (tick a checklist item, add or reorder queue entries, set the
framing). These tests use a real `DiskButler` on a temporary folder, with
no stand-in on the write path, and check that every one of Execution's four
writers (the recorder, the imaging queue's checkpoint, post-session
reconciliation and abort) leaves the Planning-owned fields alone, that the
revision never goes backwards, and that a plan edit which changes nothing
does not bump the revision.
"""

import threading
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import AppConfiguration
from wayfindinglib.api.execution import ObservationExecution
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.planning.observation_package import ExposureRequest, FrameType
from wayfindinglib.models.session.observation_session import (
    ChecklistItem,
    Framing,
    ObservationSession,
    PlanChange,
    PlanChangeItem,
    PlanRevisionConflictError,
    QueuedObservationPackage,
    QueueEntryStatus,
    SessionStatus,
    StartTimeMode,
)
from wayfindinglib.models.session.telemetry import GuidingSample
from wayfindinglib.tasks.execution_tasks import imaging_queue
from wayfindinglib.tasks.execution_tasks.post_session_reconciliation import reconcile_session
from wayfindinglib.tasks.execution_tasks.session_recorder import ObservationSessionRecorder
from wayfindinglib.tasks.planning_tasks.night_plan_tasks import (
    add_checklist_items,
    find_checklist_item,
    store_session_change,
    tick_item,
)


@pytest.fixture
def butler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DiskButler:
    """Build a DiskButler over a temporary database.

    Returns
    -------
    butler : `DiskButler`
        A fresh butler that writes only under `tmp_path`.
    """
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: tmp_path / "config.toml")
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


def _entry(entry_id: str, count: int = 5) -> QueuedObservationPackage:
    """Build a pending queue entry.

    Returns
    -------
    entry : `QueuedObservationPackage`
        An entry for target M 81.
    """
    return QueuedObservationPackage(
        id=entry_id,
        observation_package_id=f"package-{entry_id}",
        target_id="M 81",
        exposure_requests=[ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=60.0, count=count)],
        start_time_mode=StartTimeMode.SOONEST,
        computed_start_time=datetime(2026, 10, 24, 3, 0, tzinfo=UTC),
    )


def _stored_plan(butler: DiskButler, session_id: str = "night-1") -> ObservationSession:
    """Store a running session with one entry and one checklist item.

    Returns
    -------
    session : `ObservationSession`
        The stored session, as Execution would load it.
    """
    session = ObservationSession(
        id=session_id,
        night_date=date(2026, 10, 24),
        status=SessionStatus.RUNNING,
        site_profile_id="site",
        telescope_id="scope",
        camera_id="camera",
        queue=[_entry("a")],
        checklist=[ChecklistItem(item_id="fix-1", rule="focus")],
    )
    butler.put(session, "observation_session", {"session_id": session.id})
    return butler.get("observation_session", {"session_id": session.id})


def _planning_edit(butler: DiskButler, session_id: str, edit: Any) -> PlanChange:
    """Make one Planning write the way `ObservationPlanning` does.

    Parameters
    ----------
    butler : `DiskButler`
        The storage.
    session_id : `str`
        The session to change.
    edit : `callable`
        Changes the loaded session and returns the list of changes.

    Returns
    -------
    change : `PlanChange`
        The stored change.
    """
    session = butler.get("observation_session", {"session_id": session_id})
    loaded_revision = session.revision
    changes = edit(session)
    result = store_session_change(butler, session, loaded_revision, None, changes, [])
    assert not isinstance(result, PlanRevisionConflictError)
    return result


def _tick(butler: DiskButler, session_id: str, item_id: str = "fix-1") -> PlanChange:
    """Tick one checklist item through the Planning write path.

    Returns
    -------
    change : `PlanChange`
        The stored change.
    """
    return _planning_edit(
        butler,
        session_id,
        lambda session: [tick_item(find_checklist_item(session, item_id), None, "did it")],
    )


def test_recorder_checkpoints_keep_a_tick_the_framing_and_new_entries(butler: DiskButler) -> None:
    """A running recorder keeps the tick, framing and entry from Planning."""
    _stored_plan(butler)
    sample = GuidingSample(time=1000.0, dra=0.1, ddec=0.1, pulse_ra=10.0, pulse_dec=10.0, snr=25.0)

    class Guiding:
        """A guiding service that runs the Planning edits during the loop."""

        def __init__(self) -> None:
            """Start with no polls and one sample to hand out."""
            self.polls = 0
            self.samples = [sample]
            self.stop = threading.Event()

        def poll_external_telemetry(self) -> None:
            """Count the poll; edit on the second, stop on the fourth."""
            self.polls += 1
            if self.polls == 2:
                _tick(butler, "night-1")
                _planning_edit(
                    butler,
                    "night-1",
                    _add_entry_and_framing,
                )
            if self.polls >= 4:
                self.stop.set()

        def drain_guiding_samples(self) -> list[GuidingSample]:
            """Hand out the sample once.

            Returns
            -------
            samples : `list` [`GuidingSample`]
                The sample, then nothing.
            """
            drained, self.samples = self.samples, []
            return drained

    def _add_entry_and_framing(session: ObservationSession) -> list[PlanChangeItem]:
        """Append entry b and set the framing.

        Returns
        -------
        changes : `list` [`PlanChangeItem`]
            The two changes.
        """
        session.queue.append(_entry("b", count=9))
        session.framing = Framing(center_ra_deg=148.9, center_dec_deg=69.1)
        return [PlanChangeItem(kind="queue_added"), PlanChangeItem(kind="framing_set")]

    guiding = Guiding()
    driver = SimpleNamespace(get_status=lambda: SimpleNamespace(temperature="10 C", humidity="40 %"))
    recorder = ObservationSessionRecorder(guiding, driver, butler=butler, snapshot_interval_seconds=0)

    recorder.run("night-1", stop_event=guiding.stop)

    stored = butler.get("observation_session", {"session_id": "night-1"})
    item = find_checklist_item(stored, "fix-1")
    assert item.ticked_at is not None
    assert len(item.tick_history) == 1
    assert item.note == "did it"
    assert stored.framing is not None
    assert [entry.id for entry in stored.queue] == ["a", "b"]
    assert stored.queue[1].exposure_requests[0].count == 9
    assert stored.revision == 2  # the tick and the queue edit; Execution added none
    assert len(stored.guiding_samples) == 1  # Execution's own data is also kept
    assert len(stored.weather_samples) >= 1


def test_execution_fields_win_and_planning_fields_come_from_the_store(butler: DiskButler) -> None:
    """A save keeps Execution's status and Planning's queue edits."""
    stored = _stored_plan(butler)
    stored.queue.extend([_entry("b"), _entry("c")])
    butler.put(stored, "observation_session", {"session_id": "night-1"})
    local = butler.get("observation_session", {"session_id": "night-1"})
    local.queue[0].status = QueueEntryStatus.COMPLETED
    local.queue[0].frames_captured = 5
    local.status_detail = "working"

    def edit(session: ObservationSession) -> list:
        """Reorder the queue and change the exposure of b.

        Returns
        -------
        changes : `list` [`PlanChangeItem`]
            The reorder.
        """
        by_id = {entry.id: entry for entry in session.queue}
        by_id["b"].exposure_requests[0].count = 11
        session.queue = [by_id["b"], by_id["a"]]
        return [PlanChangeItem(kind="queue_reordered")]

    _planning_edit(butler, "night-1", edit)
    queue_before = list(local.queue)

    butler.put_session_from_execution(local)

    saved = butler.get("observation_session", {"session_id": "night-1"})
    assert [entry.id for entry in saved.queue] == ["b", "a"]
    assert saved.queue[0].exposure_requests[0].count == 11
    assert saved.queue[1].status == QueueEntryStatus.COMPLETED
    assert saved.queue[1].frames_captured == 5
    assert saved.status_detail == "working"
    # The caller's object is merged in place; its entry objects are kept.
    assert local.queue[1] is queue_before[0]
    assert [entry.id for entry in local.queue] == ["b", "a"]


def test_a_started_entry_that_planning_dropped_is_kept(butler: DiskButler) -> None:
    """An entry Execution has started or finished stays in the saved queue."""
    _stored_plan(butler)
    local = butler.get("observation_session", {"session_id": "night-1"})
    local.queue[0].status = QueueEntryStatus.COMPLETED

    def clear_queue(session: ObservationSession) -> list[PlanChangeItem]:
        """Take every entry out of the queue.

        Returns
        -------
        changes : `list` [`PlanChangeItem`]
            The removal.
        """
        session.queue.clear()
        return [PlanChangeItem(kind="queue_removed")]

    _planning_edit(butler, "night-1", clear_queue)

    butler.put_session_from_execution(local)

    saved = butler.get("observation_session", {"session_id": "night-1"})
    assert [entry.id for entry in saved.queue] == ["a"]


def test_the_revision_never_goes_backwards_and_is_not_bumped(butler: DiskButler) -> None:
    """Execution saves leave the revision at the stored value."""
    _stored_plan(butler)
    local = butler.get("observation_session", {"session_id": "night-1"})
    assert local.revision == 0
    _tick(butler, "night-1")
    _tick(butler, "night-1")
    assert butler.get("observation_session", {"session_id": "night-1"}).revision == 2

    butler.put_session_from_execution(local)  # the old copy, at revision 0

    assert local.revision == 2
    assert butler.get("observation_session", {"session_id": "night-1"}).revision == 2


def test_a_first_save_stores_the_session_as_it_is(butler: DiskButler) -> None:
    """With nothing stored yet, the session is stored unchanged."""
    session = ObservationSession(
        id="new", night_date=date(2026, 10, 24), site_profile_id="s", telescope_id="t", camera_id="c"
    )
    butler.put_session_from_execution(session)
    assert butler.get("observation_session", {"session_id": "new"}).id == "new"


def test_the_imaging_queue_checkpoint_keeps_the_tick(butler: DiskButler) -> None:
    """The imaging queue checkpoint keeps a tick made after the load."""
    local = _stored_plan(butler)
    control = SimpleNamespace(
        safety=SimpleNamespace(
            status=lambda include: SimpleNamespace(delegation_policy=None, assessment=None)
        )
    )
    dependencies = imaging_queue.imaging_dependencies(control, butler)
    _tick(butler, "night-1")

    local.status_detail = "cycle done"
    dependencies.checkpoint(local)

    saved = butler.get("observation_session", {"session_id": "night-1"})
    assert find_checklist_item(saved, "fix-1").ticked_at is not None
    assert saved.status_detail == "cycle done"
    assert saved.revision == 1


def test_reconciliation_keeps_the_tick(butler: DiskButler) -> None:
    """Post-session reconciliation saves without erasing a tick."""
    local = _stored_plan(butler)
    local.status = SessionStatus.COMPLETED
    _tick(butler, "night-1")

    reconcile_session(butler, local, SimpleNamespace(targets=SimpleNamespace(get=lambda target_id: None)))

    saved = butler.get("observation_session", {"session_id": "night-1"})
    assert saved.status == SessionStatus.COMPLETED
    assert find_checklist_item(saved, "fix-1").ticked_at is not None


def test_abort_keeps_the_tick_and_the_framing(butler: DiskButler) -> None:
    """Aborting a session skips pending entries and keeps Planning's fields."""
    _stored_plan(butler)
    execution = ObservationExecution(butler=butler)
    loaded = butler.get("observation_session", {"session_id": "night-1"})
    _tick(butler, "night-1")

    # Abort the old copy, as a caller holding a session object would.
    execution.abort_session(loaded, "stop")

    saved = butler.get("observation_session", {"session_id": "night-1"})
    assert saved.status == SessionStatus.ABORTED
    assert saved.queue[0].status == QueueEntryStatus.SKIPPED
    assert find_checklist_item(saved, "fix-1").ticked_at is not None
    assert saved.revision == 1


def test_ticks_made_while_execution_saves_are_not_lost(butler: DiskButler) -> None:
    """Many Planning ticks and many Execution saves at once lose no tick."""
    _stored_plan(butler)
    local = butler.get("observation_session", {"session_id": "night-1"})
    ticks = 15
    finished = threading.Event()

    def planning() -> None:
        """Tick the item repeatedly, retrying on a revision conflict."""
        done = 0
        while done < ticks:
            session = butler.get("observation_session", {"session_id": "night-1"})
            revision = session.revision
            change = tick_item(find_checklist_item(session, "fix-1"), None, None)
            result = store_session_change(butler, session, revision, None, [change], [])
            if not isinstance(result, PlanRevisionConflictError):
                done += 1
        finished.set()

    thread = threading.Thread(target=planning)
    thread.start()
    while not finished.is_set():
        local.status_detail = "busy"
        butler.put_session_from_execution(local)
    thread.join()

    saved = butler.get("observation_session", {"session_id": "night-1"})
    assert len(find_checklist_item(saved, "fix-1").tick_history) == ticks
    assert saved.revision == ticks


def test_an_edit_that_changes_nothing_does_not_bump_the_revision(butler: DiskButler) -> None:
    """Adding a checklist item that is already there stores nothing."""
    _stored_plan(butler)

    def add_again(session: ObservationSession) -> list:
        """Add the same rule and setting again.

        Returns
        -------
        changes : `list` [`PlanChangeItem`]
            The skip records.
        """
        return add_checklist_items(session, [ChecklistItem(rule="focus")])

    change = _planning_edit(butler, "night-1", add_again)

    assert change.revision == change.previous_revision == 0
    assert [item.kind for item in change.changes] == ["checklist_already_present"]
    assert butler.get("observation_session", {"session_id": "night-1"}).revision == 0


def test_a_database_error_other_than_a_missing_table_is_not_hidden(
    butler: DiskButler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A locked database raises; it is not reported as an ordinary refusal."""
    import sqlite3

    session = _stored_plan(butler)

    class _Locked:
        """A connection whose every statement fails as if locked."""

        def execute(self, *args: Any) -> None:
            """Fail.

            Raises
            ------
            sqlite3.OperationalError
                Always.
            """
            raise sqlite3.OperationalError("database is locked")

        def close(self) -> None:
            """Do nothing."""

    monkeypatch.setattr("wayfindinglib.drivers.butler.connect_db", lambda path: _Locked())
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        butler.put_session_if_revision(session, 0)
