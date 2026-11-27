"""Tests for the step that moves clouded and trailed frames aside.

They use stand-in frame records and a stand-in measuring function, so no
FITS data is read. The limits themselves were checked on real frames (see the
numbers in `frame_quarantine.py`).
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from astrometricslib.models.quality.quality_summary import StackingPipelineQualityMetrics, StackQualitySummary
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.shared.quarantine_path import QUARANTINE_FOLDER_NAME, is_quarantined_path
from astrometricslib.pipelines.stacking.post_processing.record_quality import stacking_notice_measurements
from astrometricslib.pipelines.stacking.pre_processing import frame_quarantine
from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import (
    QUARANTINE_EXCLUSION_REASON_PREFIX,
    record_input_counts,
)
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
    MANIFEST_FILE_NAME,
    drop_frames_already_set_aside,
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
    exposure: float = 120.0,
    role: str = "LIGHT",
) -> SimpleNamespace:
    """Build a stand-in frame record with the fields the step reads.

    Returns
    -------
    frame : `SimpleNamespace`
        A frame with ``path``, ``timestamp``, ``camera``, ``filter``,
        ``exposure_s``, ``role``, ``gain`` (100) and ``focal_length_mm``.
    """
    return SimpleNamespace(
        path=str(path),
        timestamp=timestamp,
        camera=camera,
        filter=SimpleNamespace(value=filter_name),
        exposure_s=exposure,
        role=role,
        gain=100,
        focal_length_mm=405.0,
    )


def make_measurement(
    path: Path | str, stars: int = 3400, elongation: float | None = 1.05, sky: float = 1400.0
) -> dict[str, Any]:
    """Build the measurement dictionary of one frame.

    Returns
    -------
    measurement : `dict`
        ``path``, ``star_count``, ``star_elongation`` and ``sky_median_adu``.
    """
    return {"path": str(path), "star_count": stars, "star_elongation": elongation, "sky_median_adu": sky}


def clean_batch(count: int = 12) -> list[dict[str, Any]]:
    """Build measurements for a batch of sound frames.

    Returns
    -------
    measurements : `list` [`dict`]
        One entry per frame, with star counts falling slowly.
    """
    return [make_measurement(f"frame_{index:03d}.fits", stars=3400 - 10 * index) for index in range(count)]


def test_split_into_batches_separates_setups_and_sessions() -> None:
    """Frames split by capture setting (gain too) and by gaps over 4 hours."""
    other_gain = make_frame("e1", 0.0)
    other_gain.gain = 200
    same_gain_written_otherwise = make_frame("a4", 60.0)
    same_gain_written_otherwise.gain = 100.0
    other_optic = make_frame("f1", 0.0)
    other_optic.focal_length_mm = 300.0
    frames = [
        make_frame("a1", 0.0),
        make_frame("a2", 120.0),
        make_frame("b1", 0.0, exposure=30.0),
        make_frame("c1", 0.0, filter_name="Red"),
        make_frame("d1", 0.0, camera="Camera B"),
        make_frame("a3", 120.0 + frame_quarantine.SESSION_GAP_SECONDS.default + 1),
        other_gain,
        same_gain_written_otherwise,
        other_optic,
    ]
    batches = split_into_batches(frames)
    assert sorted(len(batch) for batch in batches) == [1, 1, 1, 1, 1, 1, 3]


def test_judge_batch_flags_cloud_and_trailing_and_keeps_the_rest() -> None:
    """Few stars under bright sky read as cloud; long stars as trailing."""
    measurements = clean_batch()
    measurements[3] = make_measurement("cloud.fits", stars=900, sky=3500.0)
    measurements[5] = make_measurement("trail.fits", elongation=1.9)
    measurements[7] = make_measurement("mild.fits", elongation=1.25)
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


def colour_session(elongation_values: list[float], colour_camera: bool) -> list[dict[str, Any]]:
    """Build a batch of twelve frames with the given elongation values.

    Parameters
    ----------
    elongation_values : `list` [`float`]
        Elongation of the frames at the end of the batch; the rest are 1.10.
    colour_camera : `bool`
        Whether the measurements say a colour camera took them.

    Returns
    -------
    measurements : `list` [`dict`]
        The batch, each entry with a ``colour_camera`` key.
    """
    measurements = clean_batch()
    for measurement in measurements:
        measurement["star_elongation"] = 1.10
        measurement["colour_camera"] = colour_camera
    for offset, elongation in enumerate(elongation_values):
        measurements[-1 - offset]["star_elongation"] = elongation
    return measurements


def test_judge_batch_reads_the_colour_cameras_own_elongation_rise(monkeypatch: pytest.MonkeyPatch) -> None:
    """A colour camera is judged with its own rise, mono with the mono one."""
    wider = frame_quarantine.COLOUR_CAMERA_ELONGATION_RISE_ABOVE_MEDIAN.model_copy(update={"default": 0.40})
    monkeypatch.setattr(frame_quarantine, "COLOUR_CAMERA_ELONGATION_RISE_ABOVE_MEDIAN", wider)

    mono = judge_batch(colour_session([1.36], colour_camera=False))
    colour = judge_batch(colour_session([1.36], colour_camera=True))

    assert [d.kind for d in mono] == ["trailed"]
    assert colour == []


def test_judge_batch_still_catches_clearly_trailed_colour_frames() -> None:
    """Frames 0.7 over the median move for a colour camera too."""
    decisions = judge_batch(colour_session([1.9, 1.8], colour_camera=True))

    assert len(decisions) == 2
    assert {d.kind for d in decisions} == {"trailed"}


def test_judge_batch_treats_a_measurement_without_the_colour_key_as_mono() -> None:
    """Older measurements, with no ``colour_camera`` key, use the mono rise."""
    measurements = colour_session([1.36], colour_camera=True)
    for measurement in measurements:
        del measurement["colour_camera"]

    assert [d.kind for d in judge_batch(measurements)] == ["trailed"]


def test_judge_batch_scales_the_elongation_limit_to_the_session() -> None:
    """In a session with a median of 1.28, only frames 0.12 higher move."""
    measurements = [make_measurement(f"f{index}.fits", elongation=1.28) for index in range(12)]
    measurements[0] = make_measurement("worst.fits", elongation=2.0)
    measurements[1] = make_measurement("bad.fits", elongation=1.41)
    measurements[2] = make_measurement("mild.fits", elongation=1.38)

    moved = {decision.path for decision in judge_batch(measurements)}

    assert moved == {"worst.fits", "bad.fits"}


def test_find_frames_moves_every_bad_frame_and_warns_when_many_look_bad(tmp_path: Path) -> None:
    """With 30% of a batch bad, all of them move and the report warns."""
    frames = [make_frame(tmp_path / f"f{index}.fits", float(index)) for index in range(10)]
    measurements = {
        frame.path: make_measurement(frame.path, elongation=2.5 if index < 3 else 1.05)
        for index, frame in enumerate(frames)
    }

    report = find_frames_to_quarantine(frames, measure=lambda path: dict(measurements[path]))

    assert len(report.moved) == 3
    assert report.batches_with_many_set_aside == 1
    assert report.warnings == ["3 of 10 frames set aside: 3 trailed"]
    assert set(report.warning_by_path) == {decision.path for decision in report.moved}


def test_find_frames_does_not_warn_at_a_quarter_or_less(tmp_path: Path) -> None:
    """Two bad frames of 10 are moved without a warning."""
    frames = [make_frame(tmp_path / f"f{index}.fits", float(index)) for index in range(10)]
    measurements = {
        frame.path: make_measurement(frame.path, elongation=2.5 if index < 2 else 1.05)
        for index, frame in enumerate(frames)
    }

    report = find_frames_to_quarantine(frames, measure=lambda path: dict(measurements[path]))

    assert len(report.moved) == 2
    assert report.warnings == []


def test_find_frames_notes_small_batches_and_reports_unreadable_frames(tmp_path: Path) -> None:
    """Small batches are left alone, and an unreadable frame is reported."""
    small = [make_frame(tmp_path / f"s{index}.fits", float(index), exposure=30.0) for index in range(3)]
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
        return make_measurement(path, elongation=2.5 if path == bad_path else 1.05)

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


def test_frames_set_aside_earlier_are_dropped_even_if_the_target_still_lists_them(
    tmp_path: Path,
) -> None:
    """A moved frame the target still lists counts as set aside, not kept."""
    directory = tmp_path / "lights" / "M 1" / "Scope" / "Camera"
    directory.mkdir(parents=True)
    frames = []
    for index in range(10):
        path = directory / f"M1_{index:03d}.fits"
        path.write_bytes(b"frame")
        frames.append(make_frame(path, float(index) * 120.0))
    bad_path = frames[4].path
    first_run_target = SimpleNamespace(id="M 1", frames=list(frames), recalculate_total_exposure=lambda: None)
    quarantine_bad_frames(
        first_run_target,
        frames,
        measure=lambda path: make_measurement(path, elongation=2.5 if path == bad_path else 1.05),
    )
    # The first run's save of the target did not last: it still lists all ten.
    recalculations: list[int] = []
    stale_target = SimpleNamespace(
        id="M 1", frames=list(frames), recalculate_total_exposure=lambda: recalculations.append(1)
    )

    kept, set_aside = drop_frames_already_set_aside(stale_target, frames, str(tmp_path))

    assert [frame.path for frame in set_aside] == [bad_path]
    assert bad_path not in [frame.path for frame in kept]
    assert len(kept) == 9
    assert bad_path not in [frame.path for frame in stale_target.frames]
    assert recalculations == [1]


def test_a_restored_frame_is_not_dropped(tmp_path: Path) -> None:
    """A frame back in its folder stays, though the manifest lists it."""
    directory = tmp_path / "lights" / "M 1" / "Scope" / "Camera"
    directory.mkdir(parents=True)
    frames = []
    for index in range(10):
        path = directory / f"M1_{index:03d}.fits"
        path.write_bytes(b"frame")
        frames.append(make_frame(path, float(index) * 120.0))
    bad_path = frames[4].path
    target = SimpleNamespace(id="M 1", frames=list(frames), recalculate_total_exposure=lambda: None)
    quarantine_bad_frames(
        target,
        frames,
        measure=lambda path: make_measurement(path, elongation=2.5 if path == bad_path else 1.05),
    )
    # Put the moved file back without clearing the manifest.
    Path(bad_path).write_bytes(b"frame")

    kept, set_aside = drop_frames_already_set_aside(target, frames, str(tmp_path))

    assert set_aside == []
    assert len(kept) == 10


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
        return make_measurement(path, elongation=2.5 if path.endswith("f2.fits") else 1.05)

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
        decision = frame_quarantine.QuarantineDecision(str(path), "trailed", "test", 100, 3000.0, 2.5, 1400.0)
        frame_quarantine._move_to_quarantine(decision)

    names = sorted(os.listdir(directory / QUARANTINE_FOLDER_NAME))
    assert names == ["excluded_frames.json", "f.fits", "f_1.fits"]


def test_is_quarantined_path_checks_whole_folder_names() -> None:
    """Only a folder named _excluded counts, not a name that contains it."""
    assert is_quarantined_path(os.path.join("lights", "M 1", QUARANTINE_FOLDER_NAME, "f.fits"))
    assert not is_quarantined_path(os.path.join("lights", "M 1", "not_excluded_frames", "f.fits"))


def test_input_counts_include_quarantined_frames_and_a_notice_says_so() -> None:
    """The metrics count moved frames and the stack gets a notice row."""
    excluded = [
        SimpleNamespace(path="a.fits", reason=f"{QUARANTINE_EXCLUSION_REASON_PREFIX}: clouded (203 stars)"),
        SimpleNamespace(path="b.fits", reason=f"{QUARANTINE_EXCLUSION_REASON_PREFIX}: trailed (round 0.44)"),
        SimpleNamespace(path="c.fits", reason="minority gain setting"),
    ]

    summary = StackQualitySummary(
        target_id="M 1",
        stacking_metrics=StackingPipelineQualityMetrics(
            is_spectral=False, frames_submitted=56, frames_stacked=53
        ),
    )
    record_input_counts(summary.stacking_metrics, 53, excluded, {})
    context = PipelineRunContext(
        "M 1", "stacking", "1.3.0", "job-1", "job-1", datetime(2026, 11, 1, tzinfo=UTC)
    )

    rows = stacking_notice_measurements(summary, context, "M1_Stacked.fits")

    assert summary.stacking_metrics.frames_quarantined == 2
    assert summary.stacking_metrics.frames_excluded_for_gain == 1
    assert [row.reason for row in rows] == [
        "2 frame(s) with clouds or trailed stars moved to the _excluded folder"
    ]


def test_the_report_keeps_the_measurements_of_kept_frames_as_well_as_moved_ones(tmp_path: Path) -> None:
    """Every readable frame of a judged batch is in `report.measurements`."""
    frames = [make_frame(tmp_path / f"k{index}.fits", float(index)) for index in range(12)]
    bad_path = frames[3].path

    def measure(path: str) -> dict[str, Any]:
        if path.endswith("k7.fits"):
            raise OSError("truncated file")
        return make_measurement(path, elongation=2.5 if path == bad_path else 1.05)

    report = find_frames_to_quarantine(frames, measure=measure)

    assert [decision.path for decision in report.moved] == [bad_path]
    assert len(report.measurements) == 11
    assert {entry["path"] for entry in report.measurements} == {
        frame.path for frame in frames if not frame.path.endswith("k7.fits")
    }
    elongation_by_path = {entry["path"]: entry["star_elongation"] for entry in report.measurements}
    assert elongation_by_path[bad_path] == pytest.approx(2.5)


def sky_grid(pattern_shift: float = 0.0) -> list[list[float]]:
    """Build a 4 x 4 sky grid of a level near 1200 ADU with a gentle gradient.

    Parameters
    ----------
    pattern_shift : `float`
        Fraction of the sky level taken from the left half and added to the
        right half, as a stand-in for thin cloud over one side.

    Returns
    -------
    grid : `list` [`list` [`float`]]
        Four rows of four sky levels.
    """
    return [
        [
            1200.0 * (1.0 + 0.002 * column + (pattern_shift if column >= 2 else -pattern_shift))
            for column in range(4)
        ]
        for _ in range(4)
    ]


def uneven_batch() -> list[dict[str, Any]]:
    """Build a clean batch whose frame 4 has an uneven sky.

    Returns
    -------
    measurements : `list` [`dict`]
        Twelve frames with a bright-star flux of 1000; frame 4 lit unevenly.
    """
    measurements = clean_batch()
    for index, measurement in enumerate(measurements):
        measurement["sky_grid_adu"] = sky_grid(0.0005 * (index % 3))
        measurement["bright_star_flux_adu"] = 1000.0
    measurements[4]["sky_grid_adu"] = sky_grid(0.06)
    return measurements


def test_judge_batch_keeps_and_flags_an_uneven_frame_whose_stars_are_normal() -> None:
    """A gradient or a tree with normal stars is flagged, not set aside."""
    decisions, flagged, _ = frame_quarantine.judge_batch_in_full(uneven_batch())

    assert decisions == []
    assert [frame.path for frame in flagged] == ["frame_004.fits"]
    assert flagged[0].findings[0].rule == "uneven_sky_kept"
    assert flagged[0].reason.startswith("Uneven sky (gradient or obstruction): ")


def test_judge_batch_sets_aside_a_frame_with_an_uneven_sky_and_says_why() -> None:
    """An uneven frame whose bright stars are dimmed is called cloud."""
    measurements = uneven_batch()
    measurements[4]["bright_star_flux_adu"] = 600.0

    decisions, notes = frame_quarantine.judge_batch_with_notes(measurements)

    assert [decision.path for decision in decisions] == ["frame_004.fits"]
    assert decisions[0].kind == "clouded"
    assert decisions[0].reason.startswith("Uneven sky, likely cloud: ")
    assert ", limit " in decisions[0].reason
    assert decisions[0].sky_unevenness > 0.05
    assert all(m["sky_unevenness"] is not None for m in measurements)
    assert not any("sky was not checked" in note for note in notes)


def stars_at(rows: list[tuple[float, float]]) -> np.ndarray:
    """Build the star array `detect_stars` returns from ``(y, x)`` pairs.

    Returns
    -------
    stars : `numpy.ndarray`
        Rows of ``[y, x, sigma_x, sigma_y, flux]``.
    """
    return np.array([[y, x, 2.0, 2.0, 1000.0] for y, x in rows])


def test_judge_batch_sets_aside_a_frame_pointing_off_the_main_field() -> None:
    """A frame 700 pixels off the field is set aside; a flipped one is not."""
    generator = np.random.default_rng(3)
    sky_rows = [(float(y), float(x)) for y, x in generator.uniform(100, 2900, size=(80, 2))]
    shape = (3000, 3000)

    def frame_with(rows: list[tuple[float, float]], name: str) -> dict[str, Any]:
        measurement = make_measurement(name)
        measurement.update(_stars=stars_at(rows), width_px=shape[1], height_px=shape[0])
        return measurement

    measurements = [frame_with(sky_rows, f"f{index:02d}.fits") for index in range(12)]
    measurements[2] = frame_with([(y, x + 700.0) for y, x in sky_rows], "off.fits")
    # Turned half a revolution, as after a meridian flip, same field.
    measurements[6] = frame_with([(shape[0] - 1 - y, shape[1] - 1 - x) for y, x in sky_rows], "flipped.fits")

    decisions, _ = frame_quarantine.judge_batch_with_notes(measurements)

    assert [decision.path for decision in decisions] == ["off.fits"]
    assert decisions[0].kind == "off_field"
    assert decisions[0].pointing_offset_fraction == pytest.approx(700 / 3000, abs=0.01)
    assert decisions[0].reason.startswith("Pointing off the main field: 0.23 of the field width, limit 0.10")
    flipped = next(m for m in measurements if m["path"] == "flipped.fits")
    assert flipped["pointing_offset_fraction"] < 0.01


def test_a_frame_that_breaks_two_rules_lists_both_reasons() -> None:
    """The reason names every rule broken, with its value and limit."""
    measurements = clean_batch()
    measurements[5] = make_measurement("both.fits", stars=900, sky=3500.0, elongation=1.9)

    decisions = judge_batch(measurements)

    assert [decision.path for decision in decisions] == ["both.fits"]
    assert [finding.rule for finding in decisions[0].findings] == ["trailed", "few_stars"]
    assert decisions[0].reason.startswith("Stars trailed: elongation 1.90, limit 1.32; Few stars: 900")


def test_the_warning_counts_each_rule_in_words() -> None:
    """The warning says how many frames each rule caught."""
    measurements = clean_batch()
    for index in range(5):
        measurements[index] = make_measurement(f"trail_{index}.fits", elongation=2.5)
    measurements[8] = make_measurement("cloud.fits", stars=900, sky=3500.0)
    decisions = judge_batch(measurements)

    warning = frame_quarantine.describe_many_set_aside(decisions, len(measurements))

    assert len(decisions) == 6
    assert warning == "6 of 12 frames set aside: 5 trailed, 1 few stars"


def test_frames_with_no_elongation_are_noted_not_judged_for_trailing() -> None:
    """A missing elongation is said out loud, and never counts as trailed."""
    measurements = clean_batch()
    measurements[1]["star_elongation"] = None

    decisions, notes = frame_quarantine.judge_batch_with_notes(measurements)

    assert decisions == []
    assert any("1 of 12 frames have no star elongation" in note for note in notes)


def test_the_manifest_records_the_rule_value_and_limit(tmp_path: Path) -> None:
    """The manifest lists the plain reason and the measured values."""
    directory = tmp_path / "Camera"
    directory.mkdir()
    frames = []
    for index in range(10):
        path = directory / f"f{index}.fits"
        path.write_bytes(b"x")
        frames.append(make_frame(path, float(index)))
    target = SimpleNamespace(id="T", frames=list(frames), recalculate_total_exposure=lambda: None)

    quarantine_bad_frames(
        target,
        frames,
        measure=lambda path: make_measurement(path, elongation=1.64 if path.endswith("f2.fits") else 1.05),
    )

    manifest = json.loads((directory / QUARANTINE_FOLDER_NAME / MANIFEST_FILE_NAME).read_text())
    assert manifest[0]["reason"] == "Stars trailed: elongation 1.64, limit 1.32"
    assert manifest[0]["star_elongation"] == pytest.approx(1.64)
    assert manifest[0]["rules"] == ["trailed"]


def test_the_manifest_carries_the_warning_when_many_frames_are_set_aside(tmp_path: Path) -> None:
    """With 30% bad, every moved frame is listed with the warning."""
    directory = tmp_path / "Camera"
    directory.mkdir()
    frames = []
    for index in range(10):
        path = directory / f"f{index}.fits"
        path.write_bytes(b"x")
        frames.append(make_frame(path, float(index)))
    target = SimpleNamespace(id="T", frames=list(frames), recalculate_total_exposure=lambda: None)

    kept, report = quarantine_bad_frames(
        target,
        frames,
        measure=lambda path: make_measurement(
            path, elongation=1.64 if "f0" in path or "f1" in path or "f2" in path else 1.05
        ),
    )

    manifest = json.loads((directory / QUARANTINE_FOLDER_NAME / MANIFEST_FILE_NAME).read_text())
    assert len(manifest) == 3
    assert len(kept) == 7
    assert {entry["warning"] for entry in manifest} == {"3 of 10 frames set aside: 3 trailed"}
    assert report.warnings == ["3 of 10 frames set aside: 3 trailed"]


def frames_on_fields(
    field_sizes: list[int], shape: tuple[int, int] = (3000, 3000), shift: float = 700.0
) -> list[dict[str, Any]]:
    """Build frame measurements shot at several pointings of one star field.

    Parameters
    ----------
    field_sizes : `list` [`int`]
        Frames at each pointing. The first pointing is the main field; each
        later one is shifted a further ``shift`` pixels in x.
    shape : `tuple` [`int`, `int`]
        Frame height and width in pixels.
    shift : `float`
        Pointing step in pixels.

    Returns
    -------
    measurements : `list` [`dict`]
        One entry per frame: the frames of each pointing in a row.
    """
    generator = np.random.default_rng(3)
    sky_rows = [(float(y), float(x)) for y, x in generator.uniform(100, 2900, size=(80, 2))]
    measurements = []
    for field_index, size in enumerate(field_sizes):
        for frame_index in range(size):
            measurement = make_measurement(f"field{field_index}_{frame_index:02d}.fits")
            rows = [(y, x + shift * field_index) for y, x in sky_rows]
            measurement.update(_stars=stars_at(rows), width_px=shape[1], height_px=shape[0])
            measurements.append(measurement)
    return measurements


def test_a_second_field_is_set_aside_as_another_field() -> None:
    """A second pointing held by enough frames is an "Other field"."""
    measurements = frames_on_fields([14, 4])

    decisions, _ = frame_quarantine.judge_batch_with_notes(measurements)

    assert [decision.path for decision in decisions] == [f"field1_{index:02d}.fits" for index in range(4)]
    assert all(decision.kind == "off_field" for decision in decisions)
    assert decisions[0].reason == "Other field (4 frames): not in this stack"


def test_a_short_run_at_another_pointing_is_a_stray_not_a_field() -> None:
    """A small run or a lone frame at another pointing is a stray."""
    measurements = frames_on_fields([20, 2])

    decisions, _ = frame_quarantine.judge_batch_with_notes(measurements)

    assert [decision.path for decision in decisions] == ["field1_00.fits", "field1_01.fits"]
    assert all(decision.reason.startswith("Pointing off the main field: ") for decision in decisions)

    lone = frames_on_fields([11, 1])
    lone_decisions, _ = frame_quarantine.judge_batch_with_notes(lone)
    assert [decision.path for decision in lone_decisions] == ["field1_00.fits"]
    assert lone_decisions[0].reason.startswith("Pointing off the main field: ")
