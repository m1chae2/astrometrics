"""Tests for the step that moves clouded and trailed frames aside.

They use stand-in frame records and a stand-in measuring function, so no
FITS data is read. The limits themselves were checked on real frames (see the
numbers in `frame_quarantine.py`).
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from astrometricslib.pipelines.shared.quarantine_path import QUARANTINE_FOLDER_NAME, is_quarantined_path
from astrometricslib.pipelines.stacking.pre_processing import frame_quarantine
from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import (
    QUARANTINE_EXCLUSION_REASON_PREFIX,
    assess_input_quality,
)
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
    MANIFEST_FILE_NAME,
    find_frames_to_quarantine,
    judge_batch,
    quarantine_bad_frames,
    restore_quarantined_frames,
    split_into_batches,
)


def make_frame(
    path: Path | str,
    timestamp: float = 0.0,
    camera: str = "Camera A",
    filter_name: str = "Luminance",
    exposure: str = "120.0",
    role: str = "LIGHT",
) -> SimpleNamespace:
    """Build a stand-in frame record with the fields the step reads.

    Returns
    -------
    frame : `SimpleNamespace`
        A frame with ``path``, ``timestamp``, ``camera``, ``filter``,
        ``exposure`` and ``role``.
    """
    return SimpleNamespace(
        path=str(path),
        timestamp=timestamp,
        camera=camera,
        filter=SimpleNamespace(value=filter_name),
        exposure=exposure,
        role=role,
    )


def make_measurement(
    path: Path | str, stars: int = 3400, roundness: float | None = 0.97, sky: float = 1400.0
) -> dict[str, Any]:
    """Build the measurement dictionary `raw_frame_check` returns for a frame.

    Returns
    -------
    measurement : `dict`
        ``path``, ``star_count``, ``roundness`` and ``sky_median_adu``.
    """
    return {"path": str(path), "star_count": stars, "roundness": roundness, "sky_median_adu": sky}


def clean_batch(count: int = 12) -> list[dict[str, Any]]:
    """Build measurements for a batch of sound frames.

    Returns
    -------
    measurements : `list` [`dict`]
        One entry per frame, with star counts falling slowly.
    """
    return [make_measurement(f"frame_{index:03d}.fits", stars=3400 - 10 * index) for index in range(count)]


def test_split_into_batches_separates_setups_and_sessions() -> None:
    """Frames split by camera, filter, exposure and by gaps over 4 hours."""
    frames = [
        make_frame("a1", 0.0),
        make_frame("a2", 120.0),
        make_frame("b1", 0.0, exposure="30.0"),
        make_frame("c1", 0.0, filter_name="Red"),
        make_frame("d1", 0.0, camera="Camera B"),
        make_frame("a3", 120.0 + frame_quarantine.SESSION_GAP_SECONDS + 1),
    ]
    batches = split_into_batches(frames)
    assert sorted(len(batch) for batch in batches) == [1, 1, 1, 1, 2]


def test_judge_batch_flags_cloud_and_trailing_and_keeps_the_rest() -> None:
    """Thin stars under bright sky read as cloud; low roundness as trailing."""
    measurements = clean_batch()
    measurements[3] = make_measurement("cloud.fits", stars=900, sky=3500.0)
    measurements[5] = make_measurement("trail.fits", roundness=0.55)
    measurements[7] = make_measurement("mild.fits", roundness=0.86)
    measurements[9] = make_measurement("thin.fits", stars=2900, sky=1650.0)

    decisions = {decision.path: decision for decision in judge_batch(measurements)}

    assert set(decisions) == {"cloud.fits", "trail.fits"}
    assert decisions["cloud.fits"].kind == "clouded"
    assert "sky 2.5 times normal" in decisions["cloud.fits"].reason
    assert decisions["trail.fits"].kind == "trailed"


def test_judge_batch_calls_a_low_star_frame_with_normal_sky_trailed() -> None:
    """Few stars with a normal sky level means heavy trailing, not cloud."""
    measurements = clean_batch()
    measurements[2] = make_measurement("smear.fits", stars=700)

    decisions = judge_batch(measurements)

    assert [d.path for d in decisions] == ["smear.fits"]
    assert decisions[0].kind == "trailed"


def test_judge_batch_keeps_every_frame_of_a_clean_batch() -> None:
    """Round stars and normal counts mean nothing moves, satellite or not."""
    assert judge_batch(clean_batch()) == []


def test_judge_batch_needs_enough_frames() -> None:
    """Seven frames are too few for the median to be trusted."""
    measurements = clean_batch(7)
    measurements[0] = make_measurement("cloud.fits", stars=100, sky=9000.0)
    assert judge_batch(measurements) == []


def test_judge_batch_scales_the_roundness_limit_to_the_session() -> None:
    """In a session of 0.90-round stars, only frames 0.15 lower move."""
    measurements = [make_measurement(f"f{index}.fits", roundness=0.90) for index in range(12)]
    measurements[0] = make_measurement("worst.fits", roundness=0.50)
    measurements[1] = make_measurement("bad.fits", roundness=0.72)
    measurements[2] = make_measurement("mild.fits", roundness=0.78)

    moved = {decision.path for decision in judge_batch(measurements)}

    assert moved == {"worst.fits", "bad.fits"}


def test_find_frames_refuses_a_batch_where_too_many_look_bad(tmp_path: Path) -> None:
    """More than a quarter flagged means the batch is poor; nothing moves."""
    frames = [make_frame(tmp_path / f"f{index}.fits", float(index)) for index in range(12)]
    measurements = {
        frame.path: make_measurement(frame.path, roundness=0.4 if index < 5 else 0.97)
        for index, frame in enumerate(frames)
    }

    report = find_frames_to_quarantine(frames, measure=lambda path: dict(measurements[path]))

    assert report.moved == []
    assert any("none moved" in note for note in report.notes)


def test_find_frames_notes_small_batches_and_reports_unreadable_frames(tmp_path: Path) -> None:
    """Small batches are left alone, and an unreadable frame is reported."""
    small = [make_frame(tmp_path / f"s{index}.fits", float(index), exposure="30.0") for index in range(3)]
    big = [make_frame(tmp_path / f"b{index}.fits", float(index)) for index in range(10)]

    def measure(path: str) -> dict[str, Any]:
        if path.endswith("b4.fits"):
            raise OSError("truncated file")
        return make_measurement(path)

    report = find_frames_to_quarantine([*small, *big], measure=measure)

    assert report.moved == []
    assert report.unreadable == [str(tmp_path / "b4.fits")]
    assert any("too few to judge" in note for note in report.notes)


def test_quarantine_moves_files_records_them_and_updates_the_target(tmp_path: Path) -> None:
    """A bad frame moves to _excluded, is recorded, and leaves the target."""
    directory = tmp_path / "lights" / "M 1" / "Scope" / "Camera"
    directory.mkdir(parents=True)
    frames = []
    for index in range(10):
        path = directory / f"M1_{index:03d}.fits"
        path.write_bytes(b"frame")
        frames.append(make_frame(path, float(index) * 120.0))
    flat = make_frame(directory / "flat.fits", 5.0, role="FLAT")
    recalculations: list[int] = []
    target = SimpleNamespace(
        id="M 1", frames=[*frames, flat], recalculate_total_exposure=lambda: recalculations.append(1)
    )
    bad_path = frames[4].path

    def measure(path: str) -> dict[str, Any]:
        return make_measurement(path, roundness=0.4 if path == bad_path else 0.97)

    kept, report = quarantine_bad_frames(target, frames, measure=measure)

    assert [frame.path for frame in kept] == [f.path for f in frames if f.path != bad_path]
    assert not os.path.exists(bad_path)
    assert os.path.exists(directory / QUARANTINE_FOLDER_NAME / "M1_004.fits")
    assert bad_path not in [frame.path for frame in target.frames]
    assert flat in target.frames
    assert recalculations == [1]
    manifest = json.loads((directory / QUARANTINE_FOLDER_NAME / MANIFEST_FILE_NAME).read_text())
    assert manifest[0]["original_path"] == bad_path
    assert manifest[0]["kind"] == "trailed"
    assert report.reasons_by_path()[bad_path].startswith(QUARANTINE_EXCLUSION_REASON_PREFIX)


def test_quarantine_ignores_calibration_frames(tmp_path: Path) -> None:
    """Only light frames are measured; a flat is never judged or moved."""
    flats = []
    for index in range(10):
        path = tmp_path / f"flat_{index}.fits"
        path.write_bytes(b"x")
        flats.append(make_frame(path, float(index), role="FLAT"))
    target = SimpleNamespace(id="T", frames=list(flats), recalculate_total_exposure=lambda: None)

    def measure(path: str) -> dict[str, Any]:
        raise AssertionError("a flat frame was measured")

    kept, report = quarantine_bad_frames(target, flats, measure=measure)

    assert len(kept) == 10
    assert report.moved == []


def test_restore_moves_frames_back_and_cleans_up(tmp_path: Path) -> None:
    """Restoring returns the frames, then removes the manifest and folder."""
    directory = tmp_path / "Camera"
    directory.mkdir()
    frames = []
    for index in range(10):
        path = directory / f"f{index}.fits"
        path.write_bytes(b"x")
        frames.append(make_frame(path, float(index)))
    target = SimpleNamespace(id="T", frames=list(frames), recalculate_total_exposure=lambda: None)

    def measure(path: str) -> dict[str, Any]:
        return make_measurement(path, roundness=0.3 if path.endswith("f2.fits") else 0.97)

    quarantine_bad_frames(target, frames, measure=measure)
    assert not (directory / "f2.fits").exists()

    restored = restore_quarantined_frames(str(directory))

    assert restored == [str(directory / "f2.fits")]
    assert (directory / "f2.fits").exists()
    assert not (directory / QUARANTINE_FOLDER_NAME).exists()


def test_restore_leaves_a_frame_whose_original_place_is_taken(tmp_path: Path) -> None:
    """A frame is never restored over a file that now holds its old name."""
    quarantine = tmp_path / "Camera" / QUARANTINE_FOLDER_NAME
    quarantine.mkdir(parents=True)
    (quarantine / "f.fits").write_bytes(b"old")
    (tmp_path / "Camera" / "f.fits").write_bytes(b"new")
    manifest = [{"file": "f.fits", "original_path": str(tmp_path / "Camera" / "f.fits")}]
    (quarantine / MANIFEST_FILE_NAME).write_text(json.dumps(manifest))

    restored = restore_quarantined_frames(str(quarantine))

    assert restored == []
    assert (tmp_path / "Camera" / "f.fits").read_bytes() == b"new"
    assert (quarantine / "f.fits").read_bytes() == b"old"


def test_a_second_move_with_the_same_name_does_not_overwrite(tmp_path: Path) -> None:
    """Two frames with one file name keep both copies in _excluded."""
    directory = tmp_path / "Camera"
    directory.mkdir()
    for content in (b"first", b"second"):
        path = directory / "f.fits"
        path.write_bytes(content)
        decision = frame_quarantine.QuarantineDecision(str(path), "trailed", "test", 100, 3000.0, 0.4, 1400.0)
        frame_quarantine._move_to_quarantine(decision)

    names = sorted(os.listdir(directory / QUARANTINE_FOLDER_NAME))
    assert names == ["excluded_frames.json", "f.fits", "f_1.fits"]


def test_is_quarantined_path_checks_whole_folder_names() -> None:
    """Only a folder named _excluded counts, not a name that contains it."""
    assert is_quarantined_path(os.path.join("lights", "M 1", QUARANTINE_FOLDER_NAME, "f.fits"))
    assert not is_quarantined_path(os.path.join("lights", "M 1", "not_excluded_frames", "f.fits"))


def test_input_quality_counts_quarantined_frames_and_says_so() -> None:
    """The input judgement counts moved frames and flags the stack."""
    excluded = [
        SimpleNamespace(path="a.fits", reason=f"{QUARANTINE_EXCLUSION_REASON_PREFIX}: clouded (203 stars)"),
        SimpleNamespace(path="b.fits", reason=f"{QUARANTINE_EXCLUSION_REASON_PREFIX}: trailed (round 0.44)"),
        SimpleNamespace(path="c.fits", reason="minority gain setting"),
    ]

    quality = assess_input_quality(56, 53, excluded, None, {})

    assert quality.frames_quarantined == 2
    assert quality.frames_excluded_for_gain == 1
    assert quality.is_flagged
    assert any("2 frame(s) with clouds or trailed stars" in reason for reason in quality.flag_reasons)
