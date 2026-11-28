"""Purpose: Test the slow reads the night analyses keep between calls.

Description: `night_analysis` keeps three reads in the context's review
cache: the stored measurements of every frame (`kept_frame_measurements`),
the Ekos session records and guiding runs (`recorded_sessions`), and each
night's guiding baseline (`_night_baseline_provider`). These tests use a
real quality store and a real record store on temporary databases, change
the data the way a live night does, and check that each kept answer equals
a fresh full read, that only the changed part is read again, and that a
deleted or replaced record makes it read everything again. The frame
measurements are kept with the science library's opaque data version; the
science library itself reads only the rows written since.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn

import pytest

from astrometricslib._internal.drivers import quality_store
from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib._internal.quality import MeasurementOrigin, QualitySubject
from astrometricslib.api.quality import QualityMeasurements
from astrometricslib.arguments import observing_night_id
from astrometricslib.drivers import VersionedCache, bump_data_version
from astrometricslib.exceptions import StorageError
from astrometricslib.reports import MeasurementStatus, QualityMeasurement, SubjectLevel
from wayfindinglib.analytics import capture_history, envelope_assembly
from wayfindinglib.analytics.capture_history import STAR_ELONGATION_METRIC, STAR_WIDTH_METRIC
from wayfindinglib.drivers.control_record_store import ControlRecordStore
from wayfindinglib.models.session.ekos_session import EkosSessionContext, SessionEquipmentAttribution
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.tasks.control_tasks import night_analysis

START = datetime(2026, 11, 1, 6, tzinfo=UTC)
"""When the first made-up frame was measured."""

NIGHT_START = 1790217108.0
"""2026-09-23 20:31:48 MDT, the start of the first made-up guiding night."""


def _row(metric: str, subject: str, value: float, minutes: int) -> QualityMeasurement:
    """Build one stored frame measurement.

    Returns
    -------
    measurement : `QualityMeasurement`
        A measured row of target M 13.
    """
    return QualityMeasurement(
        metric=metric,
        definition_version=1,
        subject=QualitySubject(level=SubjectLevel.FRAME, subject_id=subject, target_id="M 13"),
        status=MeasurementStatus.MEASURED,
        value=value,
        origin=MeasurementOrigin.MEASURED,
        run_id=f"run-{minutes}",
        measured_at=START + timedelta(minutes=minutes),
    )


def _measured_frame(subject: str, minutes: int) -> list[QualityMeasurement]:
    """Build the width and elongation rows of one measured frame.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The two rows.
    """
    return [
        _row(STAR_WIDTH_METRIC, subject, 2.4, minutes),
        _row(STAR_ELONGATION_METRIC, subject, 1.2, minutes),
    ]


@pytest.fixture
def measured_library(tmp_path: Path) -> tuple[Any, QualityStore]:
    """Write a made-up quality store and a context that reads it.

    Returns
    -------
    context, store : `tuple`
        A stand-in control context (configuration, science library and
        review cache) and the store to add rows to.
    """
    library = tmp_path / "library"
    library.mkdir()
    store = QualityStore(str(library / "astrometrics.db"))
    store.write_measurements([
        row for index in range(3) for row in _measured_frame(f"M 13/{index}.fits", index)
    ])
    config = SimpleNamespace(get_library_path=lambda: str(library))
    context = SimpleNamespace(
        config=config,
        astrometrics=SimpleNamespace(quality=SimpleNamespace(records=QualityMeasurements(config))),
        review_cache=VersionedCache(),
    )
    return context, store


def _count_full_reads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the reads of every frame's stored measurements.

    Returns
    -------
    reads : `list` [`int`]
        Gets one entry per read.
    """
    reads: list[int] = []
    real = capture_history.read_frame_measurements

    def counted(*arguments: Any) -> Any:
        """Count, then read.

        Returns
        -------
        measurements : `dict`
            The read.
        """
        reads.append(1)
        return real(*arguments)

    monkeypatch.setattr(capture_history, "read_frame_measurements", counted)
    return reads


def _fresh(context: Any) -> dict[str, Any]:
    """Read every frame's numbers with nothing kept by the science library.

    Returns
    -------
    measurements : `dict`
        A full read.
    """
    quality_store._LATEST_READS.clear()
    return capture_history.read_frame_measurements(context.astrometrics)


def test_kept_frame_measurements_are_read_again_only_when_their_rows_change(
    measured_library: tuple[Any, QualityStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unchanged library reads nothing; a measured frame is read again.

    The answer after the new frame equals a full read with nothing kept.
    """
    context, store = measured_library
    reads = _count_full_reads(monkeypatch)

    first = night_analysis.kept_frame_measurements(context)
    assert night_analysis.kept_frame_measurements(context) is first
    assert len(reads) == 1
    store.write_measurements(_measured_frame("M 13/new.fits", 60))
    updated = night_analysis.kept_frame_measurements(context)

    assert len(reads) == 2
    assert updated == _fresh(context)
    assert "M 13/new.fits" in updated


def test_kept_frame_measurements_follow_a_deleted_frame(measured_library: tuple[Any, QualityStore]) -> None:
    """Rows removed with a deleted frame are gone from the next answer."""
    context, store = measured_library
    night_analysis.kept_frame_measurements(context)

    store.delete_frame_measurements(["M 13/1.fits"])
    store.write_measurements(_measured_frame("M 13/new.fits", 60))
    kept = night_analysis.kept_frame_measurements(context)

    assert kept == _fresh(context)
    assert "M 13/1.fits" not in kept


def test_kept_frame_measurements_are_not_kept_without_a_version(
    measured_library: tuple[Any, QualityStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the science library cannot give a version, nothing is kept."""
    context, _ = measured_library
    reads = _count_full_reads(monkeypatch)

    def unreadable(**_arguments: Any) -> NoReturn:
        """Fail as an unreadable library does.

        Raises
        ------
        StorageError
            Always.
        """
        raise StorageError("The quality database could not be read.")

    monkeypatch.setattr(context.astrometrics.quality.records, "get_data_version", unreadable)
    assert night_analysis.frame_measurement_version(context) is None
    night_analysis.kept_frame_measurements(context)
    night_analysis.kept_frame_measurements(context)

    assert len(reads) == 2


def test_the_capture_library_uses_the_kept_measurements(
    measured_library: tuple[Any, QualityStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The capture frames get the kept numbers, without a second full read."""
    context, _ = measured_library
    seen: list[Any] = []
    monkeypatch.setattr(
        night_analysis,
        "read_capture_frames",
        lambda astrometrics, telescope, camera, targets, read_measurements: (
            seen.append(read_measurements()) or []
        ),
    )

    night_analysis.capture_library(context, "Scope", "Camera", {})

    assert seen == [night_analysis.kept_frame_measurements(context)]


class _Butler:
    """A stand-in butler that counts how often it reads every record."""

    def __init__(self) -> None:
        """Start with one session record and one guiding run."""
        self.reads = 0
        self.records = {
            "ekos_session_context": [_session(NIGHT_START)],
            "guiding_run": [_run(NIGHT_START, 200, 4)],
        }

    def get_all(self, dataset_type: str) -> list[Any]:
        """Count a read and give every record of a kind.

        Returns
        -------
        records : `list`
            The records.
        """
        self.reads += 1
        return list(self.records[dataset_type])


def _session(started_at: float, ended_after: float = 7200.0) -> EkosSessionContext:
    """Build a session record of equipment ``fp-a`` for a night.

    Returns
    -------
    context : `EkosSessionContext`
        The record.
    """
    night = observing_night_id(started_at)
    return EkosSessionContext(
        id=f"session-{night}",
        session_id=night,
        started_at=started_at,
        ended_at=started_at + ended_after,
        equipment=SessionEquipmentAttribution(equipment_fingerprint="fp-a"),
    )


def _run(started_at: float, frames_total: int, frames_lost: int) -> GuidingRunSummary:
    """Build a guiding run a minute into the night of `started_at`.

    Returns
    -------
    run : `GuidingRunSummary`
        The run.
    """
    return GuidingRunSummary(
        id=f"run-{started_at}-{frames_total}",
        session_id=observing_night_id(started_at),
        source_file_name="guide_log.txt",
        written_by_ekos=True,
        started_at=started_at + 60.0,
        frames_total=frames_total,
        frames_lost=frames_lost,
    )


def _bump(records: ControlRecordStore, table: str) -> None:
    """Count one write of a table, as the butler does when it saves one."""
    connection = sqlite3.connect(records.db_path)
    try:
        bump_data_version(connection, table)
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def recorded(tmp_path: Path) -> Any:
    """Build a context with a real record store and a counting butler.

    Returns
    -------
    context : `types.SimpleNamespace`
        Has ``records``, ``butler``, ``config`` and ``review_cache``.
    """
    return SimpleNamespace(
        records=ControlRecordStore(db_path=str(tmp_path / "wayfinding.db")),
        butler=_Butler(),
        config=SimpleNamespace(sections=lambda: []),
        review_cache=VersionedCache(),
    )


def test_recorded_sessions_are_read_again_only_after_a_write(recorded: Any) -> None:
    """The records are read again only after a session or a run is saved."""
    first = night_analysis.recorded_sessions(recorded)
    night_analysis.recorded_sessions(recorded)
    assert recorded.butler.reads == 2

    recorded.butler.records["guiding_run"].append(_run(NIGHT_START, 100, 1))
    _bump(recorded.records, "guiding_runs")
    contexts, runs = night_analysis.recorded_sessions(recorded)
    assert recorded.butler.reads == 4
    assert len(runs) == 2

    recorded.butler.records["ekos_session_context"] = []
    _bump(recorded.records, "ekos_session_contexts")
    contexts, _ = night_analysis.recorded_sessions(recorded)
    assert contexts == []
    assert first[0] != contexts


def test_recorded_sessions_give_each_caller_its_own_lists(recorded: Any) -> None:
    """A caller that changes its lists does not change what others get."""
    contexts, runs = night_analysis.recorded_sessions(recorded)
    contexts.clear()
    runs.append("not a run")

    again = night_analysis.recorded_sessions(recorded)
    assert len(again[0]) == 1
    assert len(again[1]) == 1


def _guide_night(records: ControlRecordStore, started_at: float) -> None:
    """Store 200 measured samples of one night, as a guide log sync does."""
    records.replace_guiding_samples([
        {
            "timestamp": started_at + 60.0 + 3.0 * index,
            "dra": 0.8 * (1 if index % 2 else -1),
            "ddec": 0.6 * (1 if index % 3 else -1),
            "snr": 300.0,
            "source": "ekos_guide_log",
        }
        for index in range(200)
    ])


def _live(records: ControlRecordStore, started_at: float, first: int, count: int) -> None:
    """Store live samples, 2 seconds apart, as the guiding poll does."""
    records.record_guiding_samples([
        {
            "timestamp": started_at + 100.0 + 2.0 * index,
            "dra": 0.3 + 0.01 * index,
            "ddec": -0.2,
            "snr": 250.0,
            "source": "phd2_live",
        }
        for index in range(first, first + count)
    ])


def _count_nights(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Count which nights' guiding baselines are worked out.

    Returns
    -------
    nights : `list` [`str`]
        Gets each night worked out.
    """
    worked_out: list[str] = []
    real = envelope_assembly.night_guiding_baseline

    def counted(records: Any, night: str, *arguments: Any) -> Any:
        """Note the night, then work it out.

        Returns
        -------
        baseline : `NightGuidingBaseline` or `None`
            The night's baseline.
        """
        worked_out.append(night)
        return real(records, night, *arguments)

    monkeypatch.setattr(envelope_assembly, "night_guiding_baseline", counted)
    return worked_out


def _baseline_numbers(baselines: Any) -> list[tuple[Any, ...]]:
    """Reduce baselines to comparable numbers.

    Returns
    -------
    numbers : `list` [`tuple`]
        Each night's name, SNR, error, cadence, lost share and guide errors.
    """
    return [
        (night.night, night.snr, night.rms, night.cadence_s, night.lost_fraction, tuple(night.total_errors))
        for night in baselines
    ]


def test_the_kept_baselines_follow_live_samples_night_by_night(
    recorded: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live samples work out only tonight again; the answer equals a full read.

    A new frame (a library write) is not watched at all, a new guiding run
    works out only its own night, and a guide log sync works out every
    night again.
    """
    records = recorded.records
    starts = [NIGHT_START + day * 86400.0 for day in range(3)]
    for started_at in starts[:2]:
        _guide_night(records, started_at)
    _live(records, starts[2], 0, 120)
    contexts = [_session(started_at) for started_at in starts]
    runs = [_run(starts[0], 200, 4)]
    worked_out = _count_nights(monkeypatch)
    tonight = observing_night_id(starts[2])

    def provide(runs: list[GuidingRunSummary]) -> Any:
        """Ask for the kept baselines with these runs.

        Returns
        -------
        baselines : `Sequence` [`NightGuidingBaseline`]
            The kept baselines.
        """
        return night_analysis._night_baseline_provider(recorded, contexts, runs)("fp-a")

    def full(runs: list[GuidingRunSummary]) -> list[tuple[Any, ...]]:
        """Work every night out from scratch.

        Returns
        -------
        numbers : `list` [`tuple`]
            The full answer's numbers.
        """
        return _baseline_numbers(envelope_assembly.collect_night_baselines(records, contexts, "fp-a", runs))

    assert len(provide(runs)) == 3
    assert len(worked_out) == 3
    worked_out.clear()

    provide(runs)
    assert worked_out == []

    _live(records, starts[2], 120, 40)
    kept = _baseline_numbers(provide(runs))
    assert worked_out == [tonight]
    assert kept == full(runs)
    worked_out.clear()

    runs = [*runs, _run(starts[1], 150, 15)]
    _bump(records, "guiding_runs")
    kept = _baseline_numbers(provide(runs))
    assert worked_out == [observing_night_id(starts[1])]
    assert kept == full(runs)
    worked_out.clear()

    _guide_night(records, starts[0])
    kept = _baseline_numbers(provide(runs))
    assert len(worked_out) == 3
    assert kept == full(runs)


def test_the_median_guide_cadence_equals_a_full_recompute(recorded: Any) -> None:
    """After live samples, the median cadence over all nights is exact."""
    records = recorded.records
    starts = [NIGHT_START + day * 86400.0 for day in range(4)]
    for started_at in starts[:3]:
        _guide_night(records, started_at)
    contexts = [_session(started_at) for started_at in starts]
    provide = night_analysis._night_baseline_provider(recorded, contexts, [])
    provide("fp-a")

    for batch in range(5):
        _live(records, starts[3], 60 * batch, 60)
        kept = envelope_assembly.collect_baseline_values(
            records, contexts, "fp-a", night_baselines=provide("fp-a")
        )
        fresh = envelope_assembly.collect_baseline_values(records, contexts, "fp-a")
        assert kept["guide_cadence_seconds"] == fresh["guide_cadence_seconds"]
        assert kept["guiding_rms"] == fresh["guiding_rms"]
