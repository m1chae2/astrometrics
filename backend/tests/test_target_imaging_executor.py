"""Purpose: Unit tests for the backend's thin sequencer service.

Description: The sequencer's queue is an observing session's queue in
the wayfinding library. These tests use a real `ObservationPlanning` over
a temporary store and a stand-in execution, and check the RPC methods
keep the shape the app shows: added sequences appear as queued items,
removing, reordering and changing work on the session's queue, and
starting runs `advance_session` on a thread.
"""

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import AppConfiguration, Target
from backend.services.observatory.target_imaging_executor import TargetImagingExecutor
from wayfindinglib import ObservationPlanning, Telescope


@pytest.fixture
def executor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TargetImagingExecutor:
    """Build a sequencer over a temporary store, one target and a rig.

    Returns
    -------
    executor : `TargetImagingExecutor`
        The service under test.
    """
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: tmp_path / "config.toml")
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    targets = {"M 81": Target(id="M 81", ra="09:55:33", dec="+69:03:55")}
    astrometrics = SimpleNamespace(
        targets=SimpleNamespace(get=lambda target_id, refresh=False: targets.get(target_id))
    )
    telescope = Telescope(id="t1", name="Scope", focal_length_mm=450.0, focal_ratio=6.0)
    catalog = SimpleNamespace(
        active_telescope=lambda: telescope, active_camera=lambda: SimpleNamespace(id="c1")
    )
    monkeypatch.setattr(
        "wayfindinglib.data_access.equipment_catalog_reader.get_equipment_catalog", lambda config: catalog
    )
    planning = ObservationPlanning(config, astrometrics=astrometrics)
    runs: list[Any] = []

    def advance_session(session_id: str, stop: threading.Event) -> None:
        """Record the run.

        Parameters
        ----------
        session_id : `str`
            The session.
        stop : `threading.Event`
            The stop signal.
        """
        runs.append(session_id)

    service = TargetImagingExecutor(
        wayfinder=SimpleNamespace(
            planning=planning, execution=SimpleNamespace(advance_session=advance_session)
        )
    )
    service.runs = runs
    return service


def _sequence(count: int = 3) -> dict[str, Any]:
    """Build a sequence as the app sends it.

    Returns
    -------
    sequence : `dict` [`str`, `Any`]
        One target with one item.
    """
    return {
        "target_name": "M 81",
        "items": [{"count": count, "exposure": 60.0, "filter": "Ha", "duration": 60.0 * count}],
    }


def test_added_sequences_show_as_queued_items(executor: TargetImagingExecutor) -> None:
    """A sequence comes back with its items, total and timing."""
    assert executor.get_queue() == []
    assert executor.enqueue_sequence(_sequence(), {"mode": "soonest"}) == {"status": "queued"}

    (item,) = executor.get_queue()
    assert item["target_name"] == "M 81"
    assert item["items"] == [{"count": 3, "exposure": 60.0, "filter": "Ha", "duration": 180.0}]
    assert item["total_duration"] == pytest.approx(180.0)
    assert item["timing"] == {"mode": "soonest"}
    assert item["status"] == "queued"


def test_remove_reorder_and_modify_edit_the_queue(executor: TargetImagingExecutor) -> None:
    """The queue can be cut, reordered and changed in place."""
    executor.enqueue_sequence(_sequence(1), {"mode": "soonest"})
    executor.enqueue_sequence(_sequence(2), {"mode": "at", "time": "2030-01-01T22:00:00Z"})
    first, second = (item["id"] for item in executor.get_queue())

    assert executor.reorder([second]) is True
    assert [item["id"] for item in executor.get_queue()] == [second, first]
    assert executor.get_queue()[0]["timing"]["mode"] == "at"

    assert executor.modify_queue_item(second, _sequence(5)) is True
    changed = executor.get_queue()[0]
    assert changed["items"][0]["count"] == 5
    assert changed["timing"]["mode"] == "at"

    assert executor.remove_from_queue(first) is True
    assert executor.remove_from_queue(first) is False
    assert len(executor.get_queue()) == 1


def test_begin_runs_the_session_queue_on_a_thread(executor: TargetImagingExecutor) -> None:
    """Starting the sequencer runs advance_session for its session."""
    executor.begin_imaging()
    assert executor.runs == []

    executor.enqueue_sequence(_sequence(), {"mode": "soonest"})
    executor.begin_imaging()
    executor._thread.join(timeout=5.0)

    assert executor.runs == [executor._session_id]
