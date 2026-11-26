"""Tests for the set-aside frame record and the read-only listing.

Two things are checked:

1. The quarantine rows of a moved frame carry the action taken (a processing
   decision is stored as its measurement plus the action taken).
2. `QualityRecords.set_aside_frames` lists the frames in `_excluded` folders
   with a reason and says whether it came from a stored row, from the
   manifest, or only from the folder.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib.api.quality import QualityRecords
from astrometricslib.models.quality.capture_setting import CaptureSetting
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject_id
from astrometricslib.pipelines.shared.quarantine_path import QUARANTINE_FOLDER_NAME
from astrometricslib.pipelines.stacking.frame_measurement_run import quarantine_measurements_for_frame
from astrometricslib.pipelines.stacking.metrics import (
    FRAME_POINTING_OFFSET_FRACTION,
    FRAME_QUARANTINE_SKY_MEDIAN,
    FRAME_SKY_UNEVENNESS,
)
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
    MANIFEST_FILE_NAME,
    QuarantineDecision,
    QuarantineReport,
    parse_set_aside_action,
    set_aside_action_text,
)

MEASURED_AT = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
REASON = "203 stars against a typical 3348"


@pytest.fixture
def quality(tmp_path: Path) -> QualityRecords:
    """Make the quality API on a temporary library and frames folder.

    Returns
    -------
    quality : `QualityRecords`
        The quality API on a stub configuration.
    """
    configuration = SimpleNamespace(
        get_frames_path=lambda: tmp_path / "frames",
        get_library_path=lambda: tmp_path,
        get_stacks_path=lambda: tmp_path / "stacks",
    )
    return QualityRecords(configuration, None, targets=MagicMock())


def _camera_folder(tmp_path: Path) -> Path:
    """Make the folder a target's lights are stored in.

    Returns
    -------
    folder : `pathlib.Path`
        The folder, created.
    """
    folder = tmp_path / "frames" / "lights" / "M 1" / "Scope" / "Camera"
    folder.mkdir(parents=True)
    return folder


def _excluded_folder(folder: Path, names: list[str], manifest_names: list[str]) -> Path:
    """Make an `_excluded` folder with frames and a manifest for some of them.

    Returns
    -------
    quarantine : `pathlib.Path`
        The folder.
    """
    quarantine = folder / QUARANTINE_FOLDER_NAME
    quarantine.mkdir()
    for name in names:
        (quarantine / name).write_bytes(b"x")
    entries = [
        {
            "file": name,
            "original_path": str(folder / name),
            "kind": "clouded",
            "reason": REASON,
            "star_count": 203,
            "typical_star_count": 3348.0,
            "star_elongation": 1.2,
            "sky_median_adu": 5000.0,
            "moved_at": "2026-10-03T08:00:00+00:00",
        }
        for name in manifest_names
    ]
    if entries:
        (quarantine / MANIFEST_FILE_NAME).write_text(json.dumps(entries))
    return quarantine


def test_action_text_round_trips_through_the_parser() -> None:
    """The text written for a row is read back to the same kind and reason."""
    text = set_aside_action_text("trailed", "roundness 0.44 (stars stretched)")
    assert text.startswith("frame moved to _excluded/")
    assert parse_set_aside_action(text) == ("trailed", "roundness 0.44 (stars stretched)")
    assert parse_set_aside_action(None) is None
    assert parse_set_aside_action("something else") is None


def test_report_gives_an_action_for_each_moved_frame_only() -> None:
    """`actions_by_path` has one entry per moved frame."""
    decision = QuarantineDecision("/a/f.fits", "clouded", REASON, 203, 3348.0, 0.9, 7000.0)
    report = QuarantineReport(moved=[decision])
    assert report.actions_by_path() == {"/a/f.fits": set_aside_action_text("clouded", REASON)}


def test_both_quarantine_rows_of_a_moved_frame_carry_the_action(tmp_path: Path) -> None:
    """A moved frame's quarantine rows all store the action taken."""
    frame = FrameRecord(path=str(tmp_path / "lights" / "M 1" / "S" / "C" / "f.fits"), timestamp=1.0e9)
    action = set_aside_action_text("clouded", REASON)
    kwargs = {"run_id": "r", "job_id": None, "measured_at": MEASURED_AT, "capture_setting_id": None}
    moved = quarantine_measurements_for_frame(
        {"sky_unevenness": None, "pointing_offset_fraction": 0.3, "sky_median_adu": 7000.0},
        frame,
        "M 1",
        action_taken=action,
        **kwargs,
    )
    kept = quarantine_measurements_for_frame(
        {"sky_unevenness": 0.001, "pointing_offset_fraction": 0.01, "sky_median_adu": 5000.0},
        frame,
        "M 1",
        **kwargs,
    )
    assert {row.metric for row in moved} == {
        FRAME_QUARANTINE_SKY_MEDIAN,
        FRAME_SKY_UNEVENNESS,
        FRAME_POINTING_OFFSET_FRACTION,
    }
    assert next(row for row in moved if row.metric == FRAME_SKY_UNEVENNESS).status.value == "not_measured"
    assert all(row.action_taken == action for row in moved)
    assert all(row.action_taken is None for row in kept)


def test_a_folder_without_stored_rows_is_listed_with_its_source(
    quality: QualityRecords, tmp_path: Path
) -> None:
    """Manifest frames say "manifest", unrecorded frames say "folder_only"."""
    folder = _camera_folder(tmp_path)
    _excluded_folder(folder, ["a.fits", "b.fits", "notes.txt"], ["a.fits"])
    records = {record.file: record for record in quality.set_aside_frames(Target(id="M 1"))}

    assert set(records) == {"a.fits", "b.fits"}
    assert records["a.fits"].reason_source == "manifest"
    assert (records["a.fits"].kind, records["a.fits"].reason) == ("clouded", REASON)
    assert records["a.fits"].subject_id == "M 1/Scope/Camera/a.fits"
    assert records["b.fits"].reason_source == "folder_only"
    assert records["b.fits"].reason == ""
    assert records["b.fits"].kind is None
    assert not (tmp_path / "astrometrics.db").exists()


def test_a_frame_without_a_manifest_entry_is_listed_but_not_restored(
    quality: QualityRecords, tmp_path: Path
) -> None:
    """Restore moves manifest frames only; the listing flags the rest."""
    from astrometricslib._internal.processing_scripts import restore_excluded_frames
    from astrometricslib.api.processing import ProcessingPipelines
    from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import restore_quarantined_frames

    folder = _camera_folder(tmp_path)
    quarantine = _excluded_folder(folder, ["a.fits", "b.fits"], ["a.fits"])
    other = _camera_folder_sibling(folder, "Camera2")
    loose = _excluded_folder(other, ["c.fits"], [])

    records = {record.file: record for record in quality.set_aside_frames(Target(id="M 1"))}
    assert records["a.fits"].restorable is True
    assert records["a.fits"].restore_note == ""
    for name in ("b.fits", "c.fits"):
        assert records[name].restorable is False
        assert "cannot be restored automatically" in records[name].restore_note

    pipelines = ProcessingPipelines(
        SimpleNamespace(get_frames_path=lambda: tmp_path / "frames"), None, targets=MagicMock()
    )
    report = restore_excluded_frames(pipelines, Target(id="M 1"), apply=True)

    assert report.restored_count == 1
    assert (folder / "a.fits").exists()
    assert (quarantine / "b.fits").exists()
    assert (loose / "c.fits").exists()
    assert restore_quarantined_frames(str(other)) == []
    assert (loose / "c.fits").exists()


def _camera_folder_sibling(folder: Path, name: str) -> Path:
    """Make a second camera folder next to a first one.

    Returns
    -------
    sibling : `pathlib.Path`
        The new folder.
    """
    sibling = folder.parent / name
    sibling.mkdir()
    return sibling


def test_a_stored_row_gives_the_reason_exposure_and_setting(quality: QualityRecords, tmp_path: Path) -> None:
    """A stored action wins over the manifest and gives the exposure."""
    folder = _camera_folder(tmp_path)
    _excluded_folder(folder, ["a.fits", "b.fits"], ["a.fits", "b.fits"])
    store = QualityStore(str(tmp_path / "astrometrics.db"))
    setting_id = store.get_or_create_capture_setting(CaptureSetting(camera="Camera", exposure_seconds=120.0))
    action = set_aside_action_text("trailed", "stars stretched")
    store.write_measurements([
        QualityMeasurement(
            metric=FRAME_QUARANTINE_SKY_MEDIAN,
            definition_version=1,
            subject=QualitySubject(
                level=SubjectLevel.FRAME,
                subject_id=frame_subject_id(str(folder / "a.fits"), "M 1"),
                target_id="M 1",
                capture_setting_id=setting_id,
            ),
            status=MeasurementStatus.MEASURED,
            value=7000.0,
            action_taken=action,
            origin=MeasurementOrigin.MEASURED,
            run_id="r",
            measured_at=MEASURED_AT,
        )
    ])
    records = {record.file: record for record in quality.set_aside_frames(Target(id="M 1"))}

    assert records["a.fits"].reason_source == "stored_row"
    assert (records["a.fits"].kind, records["a.fits"].reason) == ("trailed", "stars stretched")
    assert records["a.fits"].action_taken == action
    assert records["a.fits"].exposure_seconds == pytest.approx(120.0)
    assert records["a.fits"].capture_setting_id == setting_id
    assert records["b.fits"].reason_source == "manifest"


def test_a_target_with_nothing_set_aside_gives_an_empty_list(quality: QualityRecords, tmp_path: Path) -> None:
    """No `_excluded` folder gives an empty list and moves nothing."""
    _camera_folder(tmp_path)
    assert quality.set_aside_frames(Target(id="M 1")) == []


def test_the_stacking_stage_stores_the_action_for_moved_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows written by the stage carry the action only for the moved frame."""
    from astrometricslib.foundation.config import get_configuration
    from astrometricslib.pipelines.stacking import frame_screening
    from astrometricslib.pipelines.stacking.frame_measurement_run import FrameQualityRecording

    monkeypatch.setattr(get_configuration(), "get_library_path", lambda: tmp_path)
    folder = tmp_path / "lights" / "M 99" / "Scope" / "Cam"
    folder.mkdir(parents=True)
    frames = []
    for name in ("moved.fits", "kept.fits"):
        (folder / name).write_bytes(b"x")
        frames.append(FrameRecord(path=str(folder / name), camera="Cam", timestamp=1_792_000_000.0))
    target = Target(id="M 99", frames=frames)
    report = QuarantineReport(
        moved=[QuarantineDecision(frames[0].path, "clouded", REASON, 203, 3348.0, 0.9, 7000.0)],
        measurements=[{"path": f.path, "sky_median_adu": 500.0} for f in frames],
    )
    library = SimpleNamespace(
        select_bias_frames=lambda *a, **k: SimpleNamespace(frames=[], flags=[], blocking_flags=[])
    )
    recording = FrameQualityRecording(configuration=get_configuration(), calibration_library=library)

    frame_screening.record_frame_quality(target, [frames[1]], report, frames, MEASURED_AT, recording)

    rows = QualityStore(str(tmp_path / "astrometrics.db")).query(metric=FRAME_QUARANTINE_SKY_MEDIAN)
    actions = {row.subject.subject_id.split("/")[-1]: row.action_taken for row in rows}
    assert actions["moved.fits"] == set_aside_action_text("clouded", REASON)
    assert actions["kept.fits"] is None


def test_frame_statuses_list_the_targets_frames_with_the_flags(
    quality: QualityRecords, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The status call reads stored elongations and flags a stretched frame."""
    target = Target(
        id="M 1",
        frames=[FrameRecord(path="/a.fits", timestamp=100.0), FrameRecord(path="/b.fits", timestamp=300.0)],
    )
    monkeypatch.setattr(quality, "set_aside_frames", lambda resolved: [])
    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.stored_star_elongations",
        lambda target_id: {"M 1/a.fits": 1.6, "M 1/b.fits": 1.7},
    )

    report = quality.frame_statuses(target, 200.0)

    assert [(status.name, status.state) for status in report.frames] == [
        ("a.fits", "flagged"),
        ("b.fits", "not_stacked"),
    ]
    assert report.frames[0].elongation == pytest.approx(1.6)
    assert "too small for the quarantine" in report.frames[0].reason


def test_frame_statuses_find_the_stack_time_themselves(
    quality: QualityRecords, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With no stack time given, the newest stack file's time is used."""
    import os

    stack = tmp_path / "stacks" / "M_1_Stacked.fits"
    stack.parent.mkdir(parents=True)
    stack.write_bytes(b"x")
    os.utime(stack, (200.0, 200.0))
    target = Target(
        id="M 1",
        frames=[FrameRecord(path="/a.fits", timestamp=100.0), FrameRecord(path="/b.fits", timestamp=300.0)],
    )
    target.stacking.stacked_image = str(stack)
    monkeypatch.setattr(quality, "set_aside_frames", lambda resolved: [])
    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.stored_star_elongations",
        lambda target_id: {},
    )

    report = quality.frame_statuses(target)

    assert report.stacked_at == pytest.approx(200.0)
    assert [status.state for status in report.frames] == ["included", "not_stacked"]
