"""Tests for the dark defect check: particle hits, tracks and bad darks.

The frames are small synthetic darks: Gaussian noise (4 ADU) around a level,
with bright hits, tracks and lines drawn in. The sets live in a library that
saves inside the test's temporary folder, so nothing touches the real library.
They check each rule of `judge_dark_defects`, that a frame cut off at zero
does not set it off, that a dark left out is not chosen for a master (the
choice stacking and the stack check share), and that the warning reaches the
stack prediction.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers.batch_calibration import choose_batch_calibration
from astrometricslib._internal.drivers.calibration_library import CalibrationLibrary, CalibrationSelection
from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.quality.dark_defect_measurement import (
    build_set_reference,
    choose_reference_paths,
    measure_dark_defects,
)
from astrometricslib.pipelines.shared.quality.metrics import (
    DARK_DEFECT_METRIC_NAMES,
    FRAME_DARK_EDGE_STREAK,
    FRAME_DARK_HIT_COUNT,
    FRAME_DARK_LONGEST_STREAK,
    FRAME_DARK_OUTLIER_COUNT,
)
from astrometricslib.pipelines.stacking import stack_prediction
from astrometricslib.pipelines.stacking.dark_defect_run import (
    DarkDefectSummary,
    measure_dark_defects_in_library,
)
from astrometricslib.pipelines.stacking.pre_processing.dark_defect_judgement import (
    DefectVerdictKind,
    judge_dark_defects,
)

MEASURED_AT = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
CAMERA = "ZWO CCD ASI533MM Pro"
SHAPE = (120, 160)
PIXEL_COUNT = SHAPE[0] * SHAPE[1]


def noise_frame(seed: int, level: float = 400.0) -> np.ndarray:
    """Make a dark: Gaussian noise of 4 ADU around a level, in whole ADU.

    Returns
    -------
    pixels : `numpy.ndarray`
        The frame as unsigned 16-bit integers, cut off at zero.
    """
    generator = np.random.default_rng(seed)
    return np.round(generator.normal(level, 4.0, SHAPE)).clip(0, 65535).astype(np.uint16)


def write_dark(path: Path, pixels: np.ndarray) -> str:
    """Write a 30 s dark frame and give its path.

    Returns
    -------
    path : `str`
        The file's path.
    """
    header = fits.Header()
    header["INSTRUME"] = CAMERA
    header["GAIN"] = 0
    header["OFFSET"] = 10
    header["EXPTIME"] = 30.0
    header["CCD-TEMP"] = -10.0
    path.parent.mkdir(parents=True, exist_ok=True)
    fits.PrimaryHDU(pixels, header=header).writeto(path, overwrite=True)
    return str(path)


def measure_against_set(frame: np.ndarray, level: float = 400.0) -> dict[str, float]:
    """Measure one frame against a reference built from clean frames.

    Returns
    -------
    values : `dict` [`str`, `float`]
        The four stored numbers of the frame.
    """
    reference = np.median(np.stack([noise_frame(seed, level) for seed in range(7)]), axis=0).astype(
        np.float32
    )
    facts = measure_dark_defects(frame, reference, 4.0)
    return {
        FRAME_DARK_HIT_COUNT: float(facts.hit_count),
        FRAME_DARK_LONGEST_STREAK: float(facts.longest_streak_px),
        FRAME_DARK_EDGE_STREAK: 1.0 if facts.edge_to_edge else 0.0,
        FRAME_DARK_OUTLIER_COUNT: float(facts.outlier_pixel_count),
    }


def judge_one(frame_values: dict[str, float], level: float = 400.0) -> DefectVerdictKind:
    """Judge one frame among seven ordinary frames of its set.

    Returns
    -------
    kind : `DefectVerdictKind`
        The verdict for the frame.
    """
    values = {
        f"ordinary_{seed}": measure_against_set(noise_frame(100 + seed, level), level) for seed in range(7)
    }
    values["frame"] = frame_values
    return judge_dark_defects(values, PIXEL_COUNT)["frame"].kind


def test_ordinary_hits_are_counted_and_kept() -> None:
    """A few small hits give a count and a keep, with no warning."""
    frame = noise_frame(50).astype(np.int32)
    for row, column in ((10, 10), (40, 90), (80, 30)):
        frame[row : row + 2, column : column + 2] += 300
    values = measure_against_set(frame.clip(0, 65535).astype(np.uint16))
    assert values[FRAME_DARK_HIT_COUNT] == 3
    assert values[FRAME_DARK_LONGEST_STREAK] == 2
    assert values[FRAME_DARK_EDGE_STREAK] == 0
    assert values[FRAME_DARK_OUTLIER_COUNT] == 12
    assert judge_one(values) is DefectVerdictKind.KEEP


def test_a_long_track_inside_the_frame_is_kept_with_a_warning() -> None:
    """A 60 pixel track that touches no edge warns and keeps the frame."""
    frame = noise_frame(51).astype(np.int32)
    frame[60, 20:80] += 300
    values = measure_against_set(frame.clip(0, 65535).astype(np.uint16))
    assert values[FRAME_DARK_LONGEST_STREAK] == 60
    assert values[FRAME_DARK_EDGE_STREAK] == 0
    values_by_path = {f"ordinary_{seed}": measure_against_set(noise_frame(100 + seed)) for seed in range(7)}
    values_by_path["frame"] = values
    verdict = judge_dark_defects(values_by_path, PIXEL_COUNT)["frame"]
    assert verdict.kind is DefectVerdictKind.WARN
    assert "median master ignores it" in verdict.reason


def test_a_gap_in_a_track_does_not_split_it() -> None:
    """A gap of two pixels still leaves one track of the full length."""
    frame = noise_frame(52).astype(np.int32)
    frame[60, 20:80] += 300
    frame[60, 50:52] -= 300
    values = measure_against_set(frame.clip(0, 65535).astype(np.uint16))
    assert values[FRAME_DARK_LONGEST_STREAK] == 60


def test_a_straight_line_from_edge_to_edge_is_left_out() -> None:
    """A line across the whole frame (a satellite) leaves the dark out."""
    frame = noise_frame(53).astype(np.int32)
    frame[50, :] += 300
    values = measure_against_set(frame.clip(0, 65535).astype(np.uint16))
    assert values[FRAME_DARK_EDGE_STREAK] == 1
    assert judge_one(values) is DefectVerdictKind.OMIT


def test_a_hit_near_a_corner_is_not_edge_to_edge() -> None:
    """A short hit that touches two edges at a corner is an ordinary hit."""
    frame = noise_frame(54).astype(np.int32)
    frame[:3, :3] += 300
    values = measure_against_set(frame.clip(0, 65535).astype(np.uint16))
    assert values[FRAME_DARK_EDGE_STREAK] == 0
    assert judge_one(values) is DefectVerdictKind.KEEP


def test_a_light_struck_frame_is_left_out_for_its_outlier_count() -> None:
    """A frame lifted by 120 ADU has most pixels far above the set's median."""
    values = measure_against_set(noise_frame(55, level=520.0))
    assert values[FRAME_DARK_OUTLIER_COUNT] > 0.9 * PIXEL_COUNT
    assert values[FRAME_DARK_HIT_COUNT] == 0
    assert values[FRAME_DARK_EDGE_STREAK] == 0
    assert judge_one(values) is DefectVerdictKind.OMIT


def test_a_set_that_is_all_warm_leaves_nothing_out() -> None:
    """When every frame is far from the reference, none stands apart."""
    values_by_path = {
        f"frame_{index}": {
            FRAME_DARK_HIT_COUNT: 0.0,
            FRAME_DARK_LONGEST_STREAK: 0.0,
            FRAME_DARK_EDGE_STREAK: 0.0,
            FRAME_DARK_OUTLIER_COUNT: 0.9 * PIXEL_COUNT + index,
        }
        for index in range(8)
    }
    verdicts = judge_dark_defects(values_by_path, PIXEL_COUNT)
    assert {verdict.kind for verdict in verdicts.values()} == {DefectVerdictKind.KEEP}


def test_a_set_with_too_few_frames_keeps_every_frame() -> None:
    """The count rule needs a set; a set of three keeps all, even a bad one."""
    bad = {
        FRAME_DARK_HIT_COUNT: 0.0,
        FRAME_DARK_LONGEST_STREAK: 0.0,
        FRAME_DARK_EDGE_STREAK: 0.0,
        FRAME_DARK_OUTLIER_COUNT: float(PIXEL_COUNT),
    }
    verdicts = judge_dark_defects({"a": bad, "b": bad, "c": bad}, PIXEL_COUNT)
    assert {verdict.kind for verdict in verdicts.values()} == {DefectVerdictKind.KEEP}


def test_frames_cut_off_at_zero_do_not_set_off_a_rule() -> None:
    """A set that is mostly zeros has no outliers."""
    generator = np.random.default_rng(7)
    frames = [np.round(generator.normal(-8.0, 4.0, SHAPE)).clip(0, 65535).astype(np.uint16) for _ in range(9)]
    assert (frames[0] == 0).mean() > 0.9
    reference = np.median(np.stack(frames[:7]), axis=0).astype(np.float32)
    for frame in frames[7:]:
        facts = measure_dark_defects(frame, reference, None)
        assert facts.outlier_pixel_count == 0
        assert not facts.edge_to_edge


def test_the_reference_comes_from_the_frames_nearest_the_median_level() -> None:
    """A minority at another level is not used for the reference."""
    paths = [f"dark_{index:02d}.fits" for index in range(10)]
    levels = [400.0] * 7 + [900.0] * 3
    chosen = choose_reference_paths(paths, levels)
    assert len(chosen) == 7
    assert not set(chosen) & {"dark_07.fits", "dark_08.fits", "dark_09.fits"}


@pytest.fixture
def library_with_darks(tmp_path: Path) -> tuple[CalibrationLibrary, QualityStore, dict[str, str]]:
    """Build a set of ten darks: seven ordinary, a track, a line and a leak.

    Returns
    -------
    fixture : `tuple`
        The library, an empty store and the paths by role.
    """
    app_config = SimpleNamespace(get_library_file_path=lambda name: str(tmp_path / name))
    library = CalibrationLibrary(app_config=app_config)
    roles: dict[str, str] = {}
    for index in range(7):
        roles[f"ordinary_{index}"] = write_dark(
            tmp_path / "darks" / f"Dark_{index:02d}.fits", noise_frame(index)
        )
    track = noise_frame(20).astype(np.int32)
    track[60, 20:80] += 300
    roles["track"] = write_dark(tmp_path / "darks" / "Dark_20.fits", track.astype(np.uint16))
    line = noise_frame(21).astype(np.int32)
    line[50, :] += 300
    roles["line"] = write_dark(tmp_path / "darks" / "Dark_21.fits", line.astype(np.uint16))
    roles["leak"] = write_dark(tmp_path / "darks" / "Dark_22.fits", noise_frame(22, level=520.0))
    for path in roles.values():
        library.add_dark_frame(path)
    return library, QualityStore(str(tmp_path / "quality.db")), roles


def run_defects(library: CalibrationLibrary, store: QualityStore, **options: object) -> DarkDefectSummary:
    """Run the dark defect job with fixed run facts.

    Returns
    -------
    summary : `DarkDefectSummary`
        What the run did.
    """
    return measure_dark_defects_in_library(
        library, store, None, run_id="run", job_id=None, measured_at=MEASURED_AT, **options
    )


def test_the_job_stores_numbers_and_marks_the_bad_darks(library_with_darks: tuple) -> None:
    """The track warns, the line and the leak are left out, the rest pass."""
    library, store, roles = library_with_darks
    summary = run_defects(library, store)
    assert summary.measured_count == 10
    assert summary.rows_stored == 10 * len(DARK_DEFECT_METRIC_NAMES)
    assert summary.warned_count == 1
    assert summary.omitted_count == 2
    marks = library.dark_defect_verdicts
    assert marks[roles["track"]]["verdict"] == "warn"
    assert marks[roles["line"]]["verdict"] == "omit"
    assert marks[roles["leak"]]["verdict"] == "omit"
    assert not [
        path for path in marks if path.endswith(tuple(f"Dark_{index:02d}.fits" for index in range(7)))
    ]


def test_a_second_run_reads_no_pixels(library_with_darks: tuple) -> None:
    """The stamps make a second run skip every frame and keep the marks."""
    library, store, _ = library_with_darks
    run_defects(library, store)
    marks = dict(library.dark_defect_verdicts)
    again = run_defects(library, store)
    assert again.measured_count == 0
    assert again.unchanged_count == 10
    assert library.dark_defect_verdicts == marks
    assert run_defects(library, store, force=True).measured_count == 10


def test_a_dark_left_out_is_not_chosen_and_the_flags_say_why(library_with_darks: tuple) -> None:
    """The stack's own choice drops omitted darks and flags every mark."""
    library, store, roles = library_with_darks
    run_defects(library, store)
    light = FrameRecord(
        path="light.fits",
        role="LIGHT",
        filter="Luminance",
        exposure_s=30.0,
        timestamp=1000.0,
        camera=CAMERA,
        telescope="T",
        gain=0,
        offset=10,
        sensor_temperature_c=-10.0,
    )
    chosen = choose_batch_calibration(library, [light], [])
    assert roles["line"] not in chosen.dark.frames
    assert roles["leak"] not in chosen.dark.frames
    assert roles["track"] in chosen.dark.frames
    assert len(chosen.dark.frames) == 8
    flags = " ".join(chosen.dark.flags)
    assert "Dark frame Dark_21.fits was left out of the master" in flags
    assert "Dark frame Dark_22.fits was left out of the master" in flags
    assert "Dark frame Dark_20.fits is used with a warning: a long particle track" in flags


def test_clearing_the_verdicts_puts_the_darks_back(library_with_darks: tuple) -> None:
    """Nothing is moved, so removing the marks restores the darks."""
    library, store, roles = library_with_darks
    run_defects(library, store)
    assert library.without_omitted_darks(roles.values()) == [
        path for role, path in roles.items() if role not in ("line", "leak")
    ]
    assert library.clear_dark_defect_verdicts() == 3
    assert library.without_omitted_darks(roles.values()) == list(roles.values())
    selection = CalibrationSelection(frames=list(roles.values()))
    assert library.apply_dark_defect_verdicts(selection) is selection


def test_the_warning_reaches_the_stack_prediction(library_with_darks: tuple) -> None:
    """A group's prediction lists the left-out and warned darks as warnings."""
    library, store, _ = library_with_darks
    run_defects(library, store)
    light = FrameRecord(
        path="light.fits",
        role="LIGHT",
        filter="Luminance",
        exposure_s=30.0,
        timestamp=1000.0,
        camera=CAMERA,
        telescope="T",
        gain=0,
        offset=10,
        sensor_temperature_c=-10.0,
    )
    group = SimpleNamespace(frames=[light], exposure_s=30.0, night="2026-11-22")
    predicted = stack_prediction._predicted_group(group, library)
    warnings = " ".join(predicted.warnings)
    assert "dark: Dark frame Dark_21.fits was left out of the master" in warnings
    assert "dark: Dark frame Dark_20.fits is used with a warning: a long particle track" in warnings
    assert predicted.dark.frame_count == 8


def test_the_reference_is_the_per_pixel_median(tmp_path: Path) -> None:
    """The median of three frames takes the middle value of every pixel."""
    paths = [
        write_dark(tmp_path / f"d{index}.fits", np.full(SHAPE, value, dtype=np.uint16))
        for index, value in enumerate((10, 30, 20))
    ]
    reference = build_set_reference(paths)
    assert reference.shape == SHAPE
    assert np.allclose(reference, 20.0)
