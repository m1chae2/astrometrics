"""Tests for the kept inputs of the target report.

The pooled noise history reads the stack-level rows of every target, and the
Ekos session records are decoded for every report. Neither depends on a new
frame of the target, so both are kept until their own data change. These
tests check that a new frame does not read them again, that a stack run, a
write to the session records, or an unreadable version does, and that the
kept result equals a fresh one.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import AppConfiguration
from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib._internal.quality import MeasurementOrigin, QualitySubject
from astrometricslib.reports import CaptureSetting, MeasurementStatus, QualityMeasurement, SubjectLevel
from wayfindinglib.tasks.planning_tasks import target_report_inputs, target_report_tasks
from wayfindinglib.tasks.planning_tasks.target_report_inputs import read_noise_history
from wayfindinglib.tasks.planning_tasks.target_report_tasks import _read_ekos_contexts


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfiguration:
    """Give a configuration whose library is a temporary folder.

    Returns
    -------
    config : `AppConfiguration`
        The configuration. The kept inputs start empty.
    """
    library = tmp_path / "library"
    library.mkdir()
    configuration = AppConfiguration()
    configuration.update_config({"Image Library": {"path": str(library)}})
    monkeypatch.setattr(target_report_inputs, "_NOISE_HISTORY_CACHE", target_report_inputs.VersionedCache())
    monkeypatch.setattr(target_report_tasks, "_EKOS_CONTEXT_CACHE", target_report_tasks.VersionedCache())
    return configuration


class _Quality:
    """Counts the reads of the stack rows and gives no rows."""

    def __init__(self) -> None:
        """Start with no reads."""
        self.reads = 0

    def query(self, **_filters: Any) -> SimpleNamespace:
        """Count a read and give no rows.

        Returns
        -------
        answer : `SimpleNamespace`
            An answer with no rows.
        """
        self.reads += 1
        return SimpleNamespace(rows=[])

    def capture_settings(self, ids: list[int]) -> dict[int, CaptureSetting]:
        """Give no settings.

        Returns
        -------
        settings : `dict`
            Empty.
        """
        return {}

    def calibration_identities(self) -> list[Any]:
        """Give no calibration records.

        Returns
        -------
        identities : `list`
            Empty.
        """
        return []


def _write(config: AppConfiguration, level: SubjectLevel, run_id: str) -> None:
    """Store one row of a level.

    Parameters
    ----------
    config : `AppConfiguration`
        The configuration.
    level : `SubjectLevel`
        The level of the row's subject.
    run_id : `str`
        The run that wrote it.
    """
    store = QualityStore.from_configuration(config)
    setting_id = store.get_or_create_capture_setting(CaptureSetting(camera="CamA"))
    store.write_measurements([
        QualityMeasurement(
            metric="frame.sky_noise_e" if level == SubjectLevel.FRAME else "stack.noise",
            definition_version=1,
            subject=QualitySubject(
                level=level,
                subject_id=f"M 31/{run_id}.fits",
                target_id="M 31",
                capture_setting_id=setting_id,
            ),
            status=MeasurementStatus.MEASURED,
            value=1.0,
            origin=MeasurementOrigin.MEASURED,
            run_id=run_id,
            measured_at=datetime.now(UTC),
        )
    ])


def test_a_new_frame_does_not_read_the_noise_history_again(config: AppConfiguration) -> None:
    """Frame rows leave the kept history; a stack row replaces it."""
    quality = _Quality()
    astrometrics = SimpleNamespace(quality=quality)
    _write(config, SubjectLevel.FRAME, "frame-0")
    first = read_noise_history(astrometrics, config)
    reads = quality.reads
    assert reads > 0

    _write(config, SubjectLevel.FRAME, "frame-1")
    second = read_noise_history(astrometrics, config)
    assert quality.reads == reads
    assert second == first

    _write(config, SubjectLevel.STACK, "stack-1")
    read_noise_history(astrometrics, config)
    assert quality.reads > reads


def test_without_a_configuration_the_noise_history_is_read_every_time() -> None:
    """A caller that gives no configuration keeps nothing."""
    quality = _Quality()
    astrometrics = SimpleNamespace(quality=quality)
    read_noise_history(astrometrics)
    first = quality.reads
    read_noise_history(astrometrics)
    assert quality.reads == 2 * first


def test_the_kept_noise_history_equals_a_fresh_one(config: AppConfiguration) -> None:
    """The kept history and the history read without keeping are equal."""
    astrometrics = SimpleNamespace(quality=_Quality())
    kept = read_noise_history(astrometrics, config)
    assert read_noise_history(astrometrics, config) == kept
    assert read_noise_history(astrometrics) == kept


class _Records:
    """Stands in for the control record store: one counter."""

    def __init__(self) -> None:
        """Start the counter at zero."""
        self.counter: dict[str, int] | None = {"ekos_session_contexts": 0}

    def data_versions(self, tables: Any = ()) -> dict[str, int] | None:
        """Give the counter.

        Returns
        -------
        versions : `dict` or `None`
            The counter, or `None` when it cannot be read.
        """
        return self.counter


def test_the_ekos_records_are_read_again_only_after_a_write(config: AppConfiguration) -> None:
    """The session records are kept until their write counter changes."""
    records = _Records()
    reads = []

    def get_all(dataset_type: str) -> list[str]:
        """Count a read and give the records.

        Returns
        -------
        contexts : `list`
            The records.
        """
        reads.append(dataset_type)
        return [f"context-{len(reads)}"]

    butler = SimpleNamespace(control_records=records, get_all=get_all)
    assert _read_ekos_contexts(butler, config) == ["context-1"]
    assert _read_ekos_contexts(butler, config) == ["context-1"]
    records.counter = {"ekos_session_contexts": 1}
    assert _read_ekos_contexts(butler, config) == ["context-2"]
    records.counter = None
    assert _read_ekos_contexts(butler, config) == ["context-3"]
    assert _read_ekos_contexts(butler, config) == ["context-4"]
