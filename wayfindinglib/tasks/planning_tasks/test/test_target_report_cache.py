"""Tests for the cache in front of `build_target_dataset_report`.

The report takes seconds to build and the depth goal display asks for it
twice. These tests check that a second request is answered from memory, and
that a change to the science library (a new frame, a quality measurement, a
frame set aside, a star), to the wayfinding records, or to the active
equipment makes the next request build it again. The build itself is replaced
by a counter; the report's content is tested elsewhere.
"""

import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import AppConfiguration, FrameRecord, Target
from astrometricslib.foundation.storage.data_versions import bump_data_version
from wayfindinglib.drivers.control_record_store import ControlRecordStore
from wayfindinglib.tasks.planning_tasks import target_report_tasks
from wayfindinglib.tasks.planning_tasks.target_report_tasks import build_target_dataset_report


class _BrokenRecords:
    """Stands in for a record store whose versions cannot be read."""

    def data_versions(self, tables: Any = ()) -> None:
        """Say that the versions cannot be read."""
        return None


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Give a made-up library, a counting builder and the arguments of a call.

    Returns
    -------
    setup : `SimpleNamespace`
        ``config``, ``butler``, ``database`` (the path of the science
        database), ``records``, ``builds`` (the number of real builds) and
        ``ask`` (a function that asks for the report).
    """
    library = tmp_path / "library"
    wayfinding = tmp_path / "wayfinding"
    library.mkdir()
    wayfinding.mkdir()
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library)}})
    database_file = wayfinding / "wayfinding.db"
    records = ControlRecordStore(str(database_file))
    butler = SimpleNamespace(control_records=records, library_path=wayfinding)
    state = SimpleNamespace(builds=0, delay=0.0)

    def counted_build(target: Target, **kwargs: Any) -> dict[str, Any]:
        """Count the build and make a new report.

        Returns
        -------
        report : `dict`
            A new report each time.
        """
        state.builds += 1
        time.sleep(state.delay)
        return {"target": target.id, "ratio": kwargs["target_noise_ratio"], "statements": [1, 2]}

    monkeypatch.setattr(target_report_tasks, "_build_target_dataset_report", counted_build)
    monkeypatch.setattr(target_report_tasks, "_TARGET_REPORT_CACHE", target_report_tasks.VersionedCache())
    monkeypatch.setattr(
        "wayfindinglib.analytics.data_versions.equipment_signs", lambda config: ("equipment",)
    )

    def ask(target: Target | None = None, **kwargs: Any) -> Any:
        """Ask for the report of a target.

        Returns
        -------
        report : `Any`
            The report.
        """
        return build_target_dataset_report(
            target or Target(id="M 81"), astrometrics=None, butler=butler, config=config, **kwargs
        )

    return SimpleNamespace(
        config=config,
        butler=butler,
        database=library / "astrometrics.db",
        records=records,
        database_file=database_file,
        state=state,
        ask=ask,
    )


def _count_write(database: Path, kind: str) -> None:
    """Count a write of one kind in the science database, as a writer does."""
    connection = sqlite3.connect(database)
    bump_data_version(connection, kind)
    connection.commit()
    connection.close()


def test_a_second_request_is_answered_from_memory(setup: SimpleNamespace) -> None:
    """The report is built once and each caller gets its own copy."""
    first = setup.ask()
    second = setup.ask()

    assert setup.state.builds == 1
    assert first == second
    assert first is not second
    second["statements"].append(3)
    assert setup.ask()["statements"] == [1, 2]


@pytest.mark.parametrize("kind", ["targets", "quality", "stars"])
def test_a_library_write_makes_the_next_request_build_again(setup: SimpleNamespace, kind: str) -> None:
    """Each kind of library write is seen: frames, measurements and stars."""
    setup.ask()
    _count_write(setup.database, kind)
    setup.ask()

    assert setup.state.builds == 2


_ROW_SCRIPT = """
CREATE TABLE IF NOT EXISTS TABLE_NAME (id TEXT PRIMARY KEY, data_json TEXT);
INSERT INTO TABLE_NAME (id, data_json) VALUES (hex(randomblob(8)), '{}');
"""
_ROW_SCRIPTS = {
    name: _ROW_SCRIPT.replace("TABLE_NAME", name)
    for name in (
        "wayfinding_observation_sessions",
        "ekos_session_contexts",
        "site_profiles",
        "guiding_runs",
        "observation_packages",
        "target_depth_goals",
    )
}
"""A script for each table that makes it and adds one row."""


def _write_row(database_file: Path, table: str) -> None:
    """Add one row to a plain table of ``wayfinding.db``, making the table."""
    connection = sqlite3.connect(database_file)
    connection.executescript(_ROW_SCRIPTS[table])
    bump_data_version(connection, table)
    connection.commit()
    connection.close()


@pytest.mark.parametrize("table", ["ekos_session_contexts", "site_profiles", "guiding_runs"])
def test_a_write_to_a_table_the_report_reads_builds_again(setup: SimpleNamespace, table: str) -> None:
    """A new plan, Ekos record, site or guiding run changes the version."""
    setup.ask()
    _write_row(setup.database_file, table)
    setup.ask()

    assert setup.state.builds == 2


def test_a_guiding_sample_does_not_build_again(setup: SimpleNamespace) -> None:
    """Live guiding writes a sample every few seconds; the report is kept."""
    setup.ask()
    for second in range(5):
        setup.records.record_guiding_samples([
            {"timestamp": 1.0e9 + second, "dra": 0.1, "ddec": 0.1, "source": "phd2_live"}
        ])
    setup.ask()

    assert setup.state.builds == 1


def test_a_bulk_load_of_guiding_samples_builds_again(setup: SimpleNamespace) -> None:
    """A bulk load of one coarse step of samples changes the version."""
    from wayfindinglib.analytics.data_versions import REPORT_COARSE_STEP

    setup.ask()
    setup.records.record_guiding_samples([
        {"timestamp": 1.0e9 + second, "dra": 0.1, "ddec": 0.1, "source": "phd2_guide_log"}
        for second in range(REPORT_COARSE_STEP)
    ])
    setup.ask()

    assert setup.state.builds == 2


def test_a_write_to_a_table_the_report_does_not_read_does_not_build_again(setup: SimpleNamespace) -> None:
    """A package or depth goal in the same file leaves the report alone."""
    setup.ask()
    _write_row(setup.database_file, "observation_packages")
    _write_row(setup.database_file, "target_depth_goals")
    _write_row(setup.database_file, "wayfinding_observation_sessions")
    setup.ask()

    assert setup.state.builds == 1


def test_an_update_in_place_of_a_report_table_builds_again(setup: SimpleNamespace) -> None:
    """A row changed in place, with the same count and row number, is seen."""
    _write_row(setup.database_file, "site_profiles")
    setup.ask()
    connection = sqlite3.connect(setup.database_file)
    connection.execute("UPDATE site_profiles SET data_json = '{\"changed\": 1}'")
    bump_data_version(connection, "site_profiles")
    connection.commit()
    connection.close()
    setup.ask()

    assert setup.state.builds == 2


def test_a_row_deleted_and_added_again_builds_again(setup: SimpleNamespace) -> None:
    """A row deleted and added again reuses its row number but still counts."""
    _write_row(setup.database_file, "guiding_runs")
    setup.ask()
    connection = sqlite3.connect(setup.database_file)
    connection.execute("DELETE FROM guiding_runs")
    connection.commit()
    connection.close()
    _write_row(setup.database_file, "guiding_runs")
    setup.ask()

    assert setup.state.builds == 2


def test_a_guide_log_sync_builds_again_even_below_one_coarse_step(setup: SimpleNamespace) -> None:
    """A sync of a few past samples is seen through its own counter."""
    setup.ask()
    setup.records.replace_guiding_samples([
        {"timestamp": 1.0e9 + second, "dra": 0.1, "ddec": 0.1, "source": "phd2_guide_log"}
        for second in range(3)
    ])
    setup.ask()

    assert setup.state.builds == 2


def test_a_camera_profile_change_builds_again(setup: SimpleNamespace) -> None:
    """Changing the gain of a camera profile changes the version.

    The depth forecast reads the gain from the ``[Observatory.Camera.*]``
    sections, so an edit there must not be answered from memory.
    """
    section = "Observatory.Camera.Test Camera"
    setup.config.update_config({section: {"gain": 100}})
    setup.ask()
    setup.ask()
    assert setup.state.builds == 1

    setup.config.update_config({section: {"gain": 120}})
    setup.ask()

    assert setup.state.builds == 2


def test_each_target_and_noise_ratio_has_its_own_report(setup: SimpleNamespace) -> None:
    """Two targets, or two ratios of one target, are two reports."""
    setup.ask(Target(id="M 81"))
    setup.ask(Target(id="M 13"))
    setup.ask(Target(id="M 81"), target_noise_ratio=0.5)
    setup.ask(Target(id="M 81"), target_noise_ratio=0.5)

    assert setup.state.builds == 3


def test_frames_the_held_target_gained_build_again(setup: SimpleNamespace) -> None:
    """Frames added in memory and not yet saved change the key."""
    target = Target(id="M 81")
    setup.ask(target)
    target.frames.append(FrameRecord(path="/new.fits"))
    setup.ask(target)

    assert setup.state.builds == 2


def test_a_report_for_a_given_time_is_never_kept(setup: SimpleNamespace) -> None:
    """A caller that names the time gets a fresh report each time."""
    when = datetime(2026, 10, 1, tzinfo=UTC)
    setup.ask(at=when)
    setup.ask(at=when)

    assert setup.state.builds == 2


def test_an_unknown_version_keeps_nothing(setup: SimpleNamespace) -> None:
    """When the version cannot be worked out, every request builds."""
    setup.butler.control_records = _BrokenRecords()
    setup.ask()
    setup.ask()

    assert setup.state.builds == 2


def test_threads_asking_together_build_once(setup: SimpleNamespace) -> None:
    """Two requests at the same moment share one build."""
    setup.state.delay = 0.2
    answers: list[Any] = []
    threads = [threading.Thread(target=lambda: answers.append(setup.ask())) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(answers) == 4
    assert setup.state.builds == 1
