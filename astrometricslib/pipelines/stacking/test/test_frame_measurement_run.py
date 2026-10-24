"""Tests for the frame measurement job in `frame_measurement_run.py`.

The frames are small synthetic FITS files with random noise and no stars, so
each measurement takes a fraction of a second. The star metrics come back
``not_measured`` on them, which is a valid result; the tests are about what
the job does around the measurement: which frames it measures, what it stores,
what it stamps, and what it does when one frame fails.
"""

import os
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.drivers.calibration_library import CalibrationSelection
from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.errors import InvalidArgumentError, StorageError
from astrometricslib.models.camera_profile import (
    CameraProfile,
    ProvenancedValue,
    ProvenanceKind,
    SensorSettingNoise,
    ValueProvenance,
)
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.quality.frame_measurement import frame_metric_set_fingerprint
from astrometricslib.pipelines.shared.quality.frame_measurement import measure_frame as real_measure_frame
from astrometricslib.pipelines.shared.quality.metrics import (
    FRAME_BACKGROUND_LEVEL,
    FRAME_READ_NOISE_E,
    FRAME_SKY_RATE_E_PER_S_PER_PX,
    FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
)
from astrometricslib.pipelines.stacking import frame_measurement_run
from astrometricslib.pipelines.stacking.frame_measurement_run import (
    BIAS_FRAMES_FOR_LEVEL,
    BiasLevelFinder,
    FrameMeasurementSession,
    FrameQualityRecording,
    build_capture_setting,
    frame_needs_measuring,
    frame_was_cut_short,
    measurements_to_store,
    quarantine_measurements_for_frame,
    record_frame_measurements_for_target,
    record_stage_frame_quality,
)
from astrometricslib.pipelines.stacking.metrics import FRAME_QUARANTINE_ROUNDNESS, FRAME_QUARANTINE_SKY_MEDIAN

GAIN_SETTING = "100"
BIAS_LEVEL_ADU = 100.0
SKY_ADU = 500.0
FRAME_SIZE = 96


def _write_fits(path: Path, level: float = SKY_ADU, seed: int = 1) -> None:
    """Write a small noisy frame with a flat level.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write it.
    level : `float`, optional
        The mean pixel value in ADU.
    seed : `int`, optional
        The random seed.
    """
    generator = np.random.default_rng(seed)
    data = generator.normal(level, 5.0, (FRAME_SIZE, FRAME_SIZE)).clip(0, 65535).astype(np.uint16)
    fits.PrimaryHDU(data).writeto(path, overwrite=True)


def _profile() -> CameraProfile:
    """Build a camera profile that knows the frames' setting.

    Returns
    -------
    profile : `CameraProfile`
        A profile with a measured gain and read noise.
    """
    provenance = ValueProvenance(kind=ProvenanceKind.MEASURED, source="test")
    datasheet = ValueProvenance(kind=ProvenanceKind.DATASHEET, source="test")
    return CameraProfile(
        camera_name="Test Camera",
        clip_ceiling_adu=ProvenancedValue(value=65535.0, provenance=datasheet),
        saturation_threshold_adu=ProvenancedValue(value=60000.0, provenance=datasheet),
        sensor_noise=(
            SensorSettingNoise(
                gain_setting=GAIN_SETTING,
                offset="0",
                binning="1x1",
                gain_e_per_adu=ProvenancedValue(value=0.5, provenance=provenance),
                read_noise_e=ProvenancedValue(value=2.0, provenance=provenance),
            ),
        ),
    )


class _Library:
    """A stand-in calibration library that lists given bias frames."""

    def __init__(self, bias_paths: list[str], flags: list[str] | None = None) -> None:
        """Remember the bias frames to offer.

        Parameters
        ----------
        bias_paths : `list` [`str`]
            The bias frame paths to offer.
        flags : `list` [`str`], optional
            Flags to put on the selection (a flag means a nearby setting).
        """
        self.bias_paths = bias_paths
        self.flags = flags or []
        self.calls = 0

    def select_bias_frames(self, camera: str, **settings: object) -> CalibrationSelection:
        """Give the bias frames and count the call.

        Returns
        -------
        selection : `CalibrationSelection`
            The frames and flags set in the constructor.
        """
        self.calls += 1
        return CalibrationSelection(list(self.bias_paths), list(self.flags), [])


@pytest.fixture
def biases(tmp_path: Path) -> list[str]:
    """Write bias frames at a known level.

    Returns
    -------
    paths : `list` [`str`]
        Six bias frame paths.
    """
    folder = tmp_path / "bias"
    folder.mkdir()
    paths = []
    for index in range(BIAS_FRAMES_FOR_LEVEL + 1):
        path = folder / f"bias_{index}.fits"
        _write_fits(path, level=BIAS_LEVEL_ADU, seed=10 + index)
        paths.append(str(path))
    return paths


@pytest.fixture
def target(tmp_path: Path) -> Target:
    """Build a target with three light frames and one dark.

    Returns
    -------
    target : `Target`
        A target whose frames exist on disk.
    """
    folder = tmp_path / "lights" / "M 99" / "Scope" / "Cam"
    folder.mkdir(parents=True)
    frames = []
    for index in range(3):
        path = folder / f"light_{index}.fits"
        _write_fits(path, seed=index)
        frames.append(
            FrameRecord(
                path=str(path),
                camera="Test Camera",
                telescope="Scope",
                iso=GAIN_SETTING,
                offset="0",
                binning=1,
                exposure="10.0",
                timestamp=1_792_000_000.0 + 60.0 * index,
                pixelScaleArcsec=2.0,
                sensorTemperatureC=-10.0,
            )
        )
    dark = folder / "dark_0.fits"
    _write_fits(dark)
    frames.append(FrameRecord(path=str(dark), role="DARK", camera="Test Camera"))
    return Target(id="M 99", frames=frames)


@pytest.fixture
def store(tmp_path: Path) -> QualityStore:
    """Open a quality store in a temporary file.

    Returns
    -------
    store : `QualityStore`
        An empty store.
    """
    return QualityStore(str(tmp_path / "astrometrics.db"))


@pytest.fixture(autouse=True)
def known_camera_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every camera name resolve to the test profile."""
    monkeypatch.setattr(frame_measurement_run, "resolve_camera_profile", lambda name: _profile())


def _session(store: QualityStore, biases: list[str]) -> FrameMeasurementSession:
    """Build a session over a store and a library holding the biases.

    Returns
    -------
    session : `FrameMeasurementSession`
        A session with a fixed run id.
    """
    return FrameMeasurementSession(
        store,
        _Library(biases),
        run_id="run-test",
        measured_at=datetime(2026, 10, 22, 6, 0, tzinfo=UTC),
        use_worker_processes=False,
    )


def test_every_light_frame_is_measured_once_and_a_second_run_measures_nothing(
    target: Target, store: QualityStore, biases: list[str]
) -> None:
    """A repeat run skips frames whose files are unchanged."""
    saved = MagicMock()
    first = record_frame_measurements_for_target(target, _session(store, biases), save_target=saved)

    assert first.frames_considered == 3
    assert first.frames_measured == 3
    assert first.frames_skipped_unchanged == 0
    assert first.frames_failed == 0
    assert first.rows_written > 0
    assert saved.call_count == 1
    rows = store.query(target_id="M 99", latest_only=False)
    assert len(rows) == first.rows_written
    assert len({row.subject.subject_id for row in rows}) == 3
    assert all(frame.quality_measured_file_size is not None for frame in target.frames[:3])
    # The dark frame is not a light frame and is not touched.
    assert target.frames[3].quality_measured_file_size is None

    saved.reset_mock()
    second = record_frame_measurements_for_target(target, _session(store, biases), save_target=saved)
    assert second.frames_measured == 0
    assert second.frames_skipped_unchanged == 3
    assert second.rows_written == 0
    assert saved.call_count == 0
    assert len(store.query(target_id="M 99", latest_only=False)) == first.rows_written


def test_a_changed_file_is_measured_again(target: Target, store: QualityStore, biases: list[str]) -> None:
    """A frame whose file changed after its measurement is measured again."""
    record_frame_measurements_for_target(target, _session(store, biases))
    touched = target.frames[1]
    stat = os.stat(touched.path)
    os.utime(touched.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    summary = record_frame_measurements_for_target(target, _session(store, biases))

    assert summary.frames_measured == 1
    assert summary.frames_skipped_unchanged == 2
    assert frame_needs_measuring(touched, os.stat(touched.path)) is False


def test_a_frame_measured_with_an_older_metric_set_is_measured_again(
    target: Target, store: QualityStore, biases: list[str]
) -> None:
    """A frame with an older metric set is measured again."""
    record_frame_measurements_for_target(target, _session(store, biases))
    unchanged = target.frames[0]
    older = target.frames[1]
    assert unchanged.quality_measured_metric_set == frame_metric_set_fingerprint()
    older.quality_measured_metric_set = "000000000000"

    assert frame_needs_measuring(unchanged, os.stat(unchanged.path)) is False
    assert frame_needs_measuring(older, os.stat(older.path)) is True
    summary = record_frame_measurements_for_target(target, _session(store, biases))

    assert summary.frames_measured == 1
    assert summary.frames_skipped_unchanged == 2
    assert older.quality_measured_metric_set == frame_metric_set_fingerprint()


def test_a_frame_measured_before_the_metric_set_was_recorded_is_measured_again(
    target: Target, store: QualityStore, biases: list[str]
) -> None:
    """A stamp with no metric set counts as older."""
    record_frame_measurements_for_target(target, _session(store, biases))
    legacy = target.frames[0]
    legacy.quality_measured_metric_set = None

    assert frame_needs_measuring(legacy, os.stat(legacy.path)) is True


def test_the_metric_set_fingerprint_follows_the_registry() -> None:
    """The fingerprint follows definition versions, not declaration order."""
    from astrometricslib.pipelines.shared.quality import frame_measurement as module

    first = frame_metric_set_fingerprint()
    assert first == frame_metric_set_fingerprint()
    assert len(first) == 12
    specs = module.FRAME_METRICS
    redefined = (
        specs[0].model_copy(update={"definition_version": specs[0].definition_version + 1}),
        *specs[1:],
    )
    original_specs = module.FRAME_METRICS
    module.frame_metric_set_fingerprint.cache_clear()
    try:
        module.FRAME_METRICS = tuple(reversed(specs))
        assert module.frame_metric_set_fingerprint() == first
        module.frame_metric_set_fingerprint.cache_clear()
        module.FRAME_METRICS = redefined
        assert module.frame_metric_set_fingerprint() != first
    finally:
        module.FRAME_METRICS = original_specs
        module.frame_metric_set_fingerprint.cache_clear()


def test_the_newest_frames_come_first_under_a_limit(
    target: Target, store: QualityStore, biases: list[str]
) -> None:
    """With a limit of one, the newest frame is measured and the rest wait."""
    summary = record_frame_measurements_for_target(target, _session(store, biases), limit=1)

    assert summary.frames_measured == 1
    assert any("2 frame(s) over the limit" in note for note in summary.notes)
    newest = max(target.frames[:3], key=lambda frame: frame.timestamp)
    assert newest.quality_measured_file_size is not None
    assert sum(frame.quality_measured_file_size is not None for frame in target.frames) == 1


def test_a_write_failure_on_one_frame_does_not_stop_the_others(
    target: Target, store: QualityStore, biases: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed frame is counted, not stamped, and measured next run."""
    real_write = store.write_measurements
    failing_name = os.path.basename(target.frames[1].path)

    def write_or_fail(measurements: list) -> int:
        """Fail for the one frame, store the rest.

        Returns
        -------
        new_count : `int`
            Rows stored.

        Raises
        ------
        StorageError
            For the one frame chosen to fail.
        """
        if failing_name in measurements[0].subject.subject_id:
            raise StorageError("database is locked")
        return real_write(measurements)

    monkeypatch.setattr(store, "write_measurements", write_or_fail)
    first = record_frame_measurements_for_target(target, _session(store, biases))

    assert first.frames_measured == 2
    assert first.frames_failed == 1
    assert "database is locked" in first.notes[0]
    assert target.frames[1].quality_measured_file_size is None

    monkeypatch.setattr(store, "write_measurements", real_write)
    second = record_frame_measurements_for_target(target, _session(store, biases))
    assert second.frames_measured == 1
    assert second.frames_skipped_unchanged == 2


def test_an_unreadable_or_missing_frame_is_counted_and_retried(
    target: Target, store: QualityStore, biases: list[str]
) -> None:
    """A garbage file and a missing file fail with no rows and no stamp."""
    Path(target.frames[0].path).write_bytes(b"this is not a FITS file")
    os.remove(target.frames[2].path)

    summary = record_frame_measurements_for_target(target, _session(store, biases))

    assert summary.frames_measured == 1
    assert summary.frames_failed == 2
    assert target.frames[0].quality_measured_file_size is None
    assert target.frames[2].quality_measured_file_size is None
    assert not [
        row
        for row in store.query(target_id="M 99", latest_only=False)
        if os.path.basename(target.frames[0].path) in row.subject.subject_id
    ]


def test_the_sky_in_electrons_uses_the_bias_of_the_frames_own_setting(
    target: Target, store: QualityStore, biases: list[str]
) -> None:
    """With biases at the setting, the sky rate is measured."""
    record_frame_measurements_for_target(target, _session(store, biases))

    sky = store.query(metric=FRAME_SKY_RATE_E_PER_S_PER_PX)
    assert len(sky) == 3
    assert all(row.status == MeasurementStatus.MEASURED for row in sky)
    # (500 - 100) ADU x 0.5 e-/ADU / 10 s = 20 e-/s/px
    assert sky[0].value == pytest.approx(20.0, rel=0.02)
    read_noise = store.query(metric=FRAME_READ_NOISE_E)
    assert read_noise[0].value == pytest.approx(2.0)
    assert store.query(metric=FRAME_BACKGROUND_LEVEL)[0].value == pytest.approx(SKY_ADU, rel=0.01)


def test_a_setting_with_no_bias_frames_gets_not_measured_sky_with_the_reason(
    target: Target, store: QualityStore
) -> None:
    """No bias at the setting: the sky rate is not measured and says why."""
    record_frame_measurements_for_target(target, _session(store, []))

    sky = store.query(metric=FRAME_SKY_RATE_E_PER_S_PER_PX)
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in sky)
    assert "no bias frames" in sky[0].reason
    assert "exactly gain 100, offset 0, binning 1x1" in sky[0].reason


def test_a_flag_from_the_library_means_a_nearby_setting_and_no_level(
    biases: list[str], target: Target
) -> None:
    """The finder never uses the bias frames of another setting."""
    library = _Library(biases, flags=["bias frames of another gain were used"])
    level, reason = BiasLevelFinder(library).find(target.frames[0])

    assert level is None
    assert "no bias frames" in reason


def test_the_bias_level_is_found_once_per_setting(biases: list[str], target: Target) -> None:
    """Three frames at one setting ask the library once."""
    library = _Library(biases)
    finder = BiasLevelFinder(library)

    levels = [finder.find(frame)[0] for frame in target.frames[:3]]

    assert levels == [pytest.approx(BIAS_LEVEL_ADU, abs=1.0)] * 3
    assert library.calls == 1


def test_a_failure_to_read_every_bias_frame_gives_no_level(tmp_path: Path, target: Target) -> None:
    """Unreadable bias files give no level and a reason."""
    broken = tmp_path / "broken.fits"
    broken.write_bytes(b"not fits")

    level, reason = BiasLevelFinder(_Library([str(broken)])).find(target.frames[0])

    assert level is None
    assert "could be read" in reason


def test_the_capture_setting_describes_the_frame(target: Target) -> None:
    """The setting row takes its fields from the frame."""
    setting = build_capture_setting(target.frames[0])

    assert setting.camera == "Test Camera"
    assert setting.telescope == "Scope"
    assert setting.exposure_seconds == pytest.approx(10.0)
    assert setting.gain_setting == 100
    assert setting.offset == 0
    assert setting.binning == 1
    assert setting.filter_name is None


def test_quarantine_numbers_become_two_rows_and_a_missing_roundness_is_not_measured(target: Target) -> None:
    """The roundness row says why when no star could be measured."""
    common = {
        "run_id": "run-test",
        "job_id": None,
        "measured_at": datetime(2026, 10, 22, tzinfo=UTC),
        "capture_setting_id": 7,
    }
    rows = quarantine_measurements_for_frame(
        {"roundness": 0.91, "sky_median_adu": 512.0}, target.frames[0], "M 99", **common
    )
    by_metric = {row.metric: row for row in rows}
    assert by_metric[FRAME_QUARANTINE_ROUNDNESS].value == pytest.approx(0.91)
    assert by_metric[FRAME_QUARANTINE_SKY_MEDIAN].value == pytest.approx(512.0)

    rows = quarantine_measurements_for_frame(
        {"roundness": None, "sky_median_adu": 512.0}, target.frames[0], "M 99", **common
    )
    roundness = next(row for row in rows if row.metric == FRAME_QUARANTINE_ROUNDNESS)
    assert roundness.status == MeasurementStatus.NOT_MEASURED
    assert roundness.reason


def test_the_stage_records_quarantine_rows_for_kept_frames(
    target: Target, tmp_path: Path, biases: list[str]
) -> None:
    """Every measured frame gets quarantine rows; new frames are measured."""
    config = SimpleNamespace(get_library_path=lambda: tmp_path)
    recording = FrameQualityRecording(configuration=config, calibration_library=_Library(biases))
    lights = target.frames[:3]
    measurements = [
        {"path": frame.path, "roundness": 0.9, "sky_median_adu": 500.0 + index}
        for index, frame in enumerate(lights)
    ]

    summary = record_stage_frame_quality(
        target, lights[:2], measurements, lights, recording, datetime(2026, 10, 22, 6, 0, tzinfo=UTC)
    )

    store = QualityStore.from_configuration(config)
    quarantine_rows = store.query(metric=FRAME_QUARANTINE_ROUNDNESS)
    assert len(quarantine_rows) == 3
    assert summary.frames_measured == 2
    assert summary.rows_written > 6
    assert len(store.query(metric=FRAME_BACKGROUND_LEVEL)) == 2
    assert lights[2].quality_measured_file_size is None


def _pipelines(tmp_path: Path, biases: list[str]) -> tuple[ProcessingPipelines, MagicMock]:
    """Build the processing API over a fake catalog access.

    Returns
    -------
    pipelines : `ProcessingPipelines`
        The API, with a stand-in calibration library holding the biases.
    catalog_access : `MagicMock`
        The stand-in for the stored catalog, whose merge call shows what was
        saved.
    """
    catalog_access = MagicMock()
    targets = SimpleNamespace(save=MagicMock(), catalog_access=catalog_access)
    pipelines = ProcessingPipelines(
        SimpleNamespace(get_library_path=lambda: tmp_path),
        MagicMock(),
        targets=targets,
        calibration=SimpleNamespace(library=_Library(biases)),
    )
    return pipelines, catalog_access


@pytest.fixture
def mountain_time() -> Iterator[None]:
    """Pin the process time zone to the observatory's, then restore it.

    The observing night is named in local time, so a test that expects a
    night name needs the same zone on every machine.

    Yields
    ------
    None
        Control to the test, with the zone set to Mountain time.
    """
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "America/Denver"
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


@pytest.mark.usefixtures("mountain_time")
def test_the_processing_api_runs_the_job_and_checks_the_limit(
    target: Target, tmp_path: Path, biases: list[str]
) -> None:
    """The public method stores rows, saves only stamps, checks the limit."""
    pipelines, catalog_access = _pipelines(tmp_path, biases)

    summary = pipelines.record_frame_measurements(target, register_job=False)

    assert summary.target_id == "M 99"
    assert summary.frames_measured == 3
    assert summary.observing_night_ids == ["2026-10-13"]
    assert catalog_access.merge_and_record.call_count == 1
    assert catalog_access.merge_and_record.call_args.args[0] == "target_catalog"
    assert QualityStore(str(tmp_path / "astrometrics.db")).query(target_id="M 99")
    again = pipelines.record_frame_measurements(target, register_job=False)
    assert again.frames_measured == 0
    with pytest.raises(InvalidArgumentError, match="limit"):
        pipelines.record_frame_measurements(target, limit=0, register_job=False)


def test_the_stamps_are_saved_every_few_frames_and_at_the_end(
    target: Target, store: QualityStore, biases: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long run keeps its progress: the save is called as the run goes."""
    monkeypatch.setattr(frame_measurement_run, "STAMP_SAVE_INTERVAL_FRAMES", 2)
    stamps_seen_at_save: list[int] = []

    def save() -> None:
        """Count how many frames carry a stamp when the save is called."""
        stamps_seen_at_save.append(
            sum(frame.quality_measured_file_size is not None for frame in target.frames)
        )

    record_frame_measurements_for_target(target, _session(store, biases), save_target=save)

    # Three frames with an interval of two: one save after the second frame,
    # one at the end for the third.
    assert stamps_seen_at_save == [2, 3]


def test_an_unexpected_error_on_one_frame_does_not_stop_the_run(
    target: Target, store: QualityStore, biases: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A RuntimeError from the image libraries costs one frame, not the run."""
    session = _session(store, biases)
    real_measure = session.measure
    failing_name = os.path.basename(target.frames[1].path)

    def measure_or_fail(frame: FrameRecord, target_id: str, capture_setting_id: int) -> list:
        """Raise for one frame and measure the others.

        Returns
        -------
        measurements : `list`
            The rows of a frame that does not fail.

        Raises
        ------
        RuntimeError
            For the one frame chosen to fail.
        """
        if os.path.basename(frame.path) == failing_name:
            raise RuntimeError("photutils broke")
        return real_measure(frame, target_id, capture_setting_id)

    monkeypatch.setattr(session, "measure", measure_or_fail)
    saved = MagicMock()

    summary = record_frame_measurements_for_target(target, session, save_target=saved)

    assert summary.frames_measured == 2
    assert summary.frames_failed == 1
    assert "RuntimeError: photutils broke" in summary.notes[0]
    assert target.frames[1].quality_measured_file_size is None
    assert saved.call_count == 1


def test_a_second_run_for_the_same_target_returns_at_once_and_says_so(
    target: Target, store: QualityStore, biases: list[str]
) -> None:
    """While a target is being measured, another caller measures nothing."""
    started = threading.Event()
    release = threading.Event()
    session = _session(store, biases)
    real_measure = session.measure

    def slow_measure(frame: FrameRecord, target_id: str, capture_setting_id: int) -> list:
        """Hold the first run until the test has made its second call.

        Returns
        -------
        measurements : `list`
            The frame's rows.
        """
        started.set()
        release.wait(30)
        return real_measure(frame, target_id, capture_setting_id)

    session.measure = slow_measure
    results: list = []
    worker = threading.Thread(
        target=lambda: results.append(record_frame_measurements_for_target(target, session))
    )
    worker.start()
    try:
        assert started.wait(30)
        second = record_frame_measurements_for_target(target, _session(store, biases))
    finally:
        release.set()
        worker.join(60)

    assert second.already_running is True
    assert second.frames_measured == 0
    assert "already going" in second.notes[0]
    assert results[0].frames_measured == 3
    assert results[0].already_running is False
    # The guard is released: a later run goes ahead (and finds nothing to do).
    later = record_frame_measurements_for_target(target, _session(store, biases))
    assert later.already_running is False
    assert later.frames_skipped_unchanged == 3


def test_a_run_for_another_target_waits_its_turn_and_the_stage_never_waits(
    target: Target, store: QualityStore, biases: list[str]
) -> None:
    """Runs take turns; the stacking stage skips instead of waiting."""
    other = Target(id="M 100", frames=[])
    started = threading.Event()
    release = threading.Event()
    session = _session(store, biases)
    real_measure = session.measure

    def slow_measure(frame: FrameRecord, target_id: str, capture_setting_id: int) -> list:
        """Hold the first run until the test is done.

        Returns
        -------
        measurements : `list`
            The frame's rows.
        """
        started.set()
        release.wait(30)
        return real_measure(frame, target_id, capture_setting_id)

    session.measure = slow_measure
    worker = threading.Thread(target=lambda: record_frame_measurements_for_target(target, session))
    worker.start()
    try:
        assert started.wait(30)
        waited = time.monotonic()
        skipped = record_frame_measurements_for_target(other, _session(store, biases), wait_for_turn=False)
        assert time.monotonic() - waited < 5.0
    finally:
        release.set()
        worker.join(60)

    assert skipped.already_running is True
    assert "another measurement run" in skipped.notes[0]
    # With the turn free, the same call for the other target runs.
    assert record_frame_measurements_for_target(other, _session(store, biases)).already_running is False


def test_a_run_nested_in_a_parent_job_keeps_its_own_run_id_and_the_parents_progress(
    target: Target, store: QualityStore, biases: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside a parent job, the run keeps its own run id and no progress."""
    parent = MagicMock()
    parent.job_id = "stacking-job-1"
    monkeypatch.setattr(frame_measurement_run, "get_current_job", lambda: parent)
    session = FrameMeasurementSession(
        store,
        _Library(biases),
        measured_at=datetime(2026, 10, 22, 6, 0, tzinfo=UTC),
        use_worker_processes=False,
    )

    record_frame_measurements_for_target(target, session)

    assert session.run_id != "stacking-job-1"
    assert session.run_id.startswith("frame-measurement-")
    assert session.job_id == "stacking-job-1"
    parent.stage.assert_not_called()
    rows = store.query(target_id="M 99", latest_only=False)
    assert {row.run_id for row in rows} == {session.run_id}


def test_a_run_with_its_own_job_reports_progress_and_uses_the_job_id_as_run_id(
    target: Target, store: QualityStore, biases: list[str]
) -> None:
    """The run's own job gets the progress and names the run."""
    own_job = MagicMock()
    own_job.job_id = "quality-job-7"
    session = FrameMeasurementSession(
        store,
        _Library(biases),
        progress_job=own_job,
        measured_at=datetime(2026, 10, 22, 6, 0, tzinfo=UTC),
        use_worker_processes=False,
    )

    record_frame_measurements_for_target(target, session)

    assert session.run_id == "quality-job-7"
    assert own_job.stage.call_count == 3


def test_an_uncooled_camera_gets_an_assumed_sky(
    target: Target, store: QualityStore, biases: list[str]
) -> None:
    """A frame with no CCD-TEMP is taken as uncooled and its sky is assumed."""
    for frame in target.frames[:3]:
        frame.sensor_temperature_c = None

    record_frame_measurements_for_target(target, _session(store, biases))

    sky = store.query(metric=FRAME_SKY_RATE_E_PER_S_PER_PX)
    assert sky
    assert all(row.status == MeasurementStatus.ASSUMED for row in sky)
    assert "uncooled" in sky[0].reason


def test_the_bias_level_ignores_hot_pixels(tmp_path: Path, target: Target) -> None:
    """The level is a clipped mean, so hot pixels do not pull it."""
    folder = tmp_path / "hot"
    folder.mkdir()
    paths = []
    for index in range(3):
        path = folder / f"bias_{index}.fits"
        generator = np.random.default_rng(index)
        data = generator.normal(BIAS_LEVEL_ADU, 3.0, (FRAME_SIZE, FRAME_SIZE))
        data[:5, :5] = 60000.0
        fits.PrimaryHDU(data.clip(0, 65535).astype(np.uint16)).writeto(path)
        paths.append(str(path))

    level, reason = BiasLevelFinder(_Library(paths)).find(target.frames[0])

    assert reason == ""
    assert level == pytest.approx(BIAS_LEVEL_ADU, abs=0.5)


def _spend_budget_of_one_frame(monkeypatch: pytest.MonkeyPatch, frame_name: str) -> None:
    """Give one named frame a CPU time budget of zero.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        The fixture that undoes the change after the test.
    frame_name : `str`
        The file name of the frame whose budget runs out at once.
    """

    def measure_with_tiny_budget(path: str, *args: object, **kwargs: object) -> list:
        """Measure, with no CPU time to spare for the chosen frame.

        Returns
        -------
        measurements : `list` [`QualityMeasurement`]
            The frame's rows.
        """
        if os.path.basename(path) == frame_name:
            kwargs["time_budget_seconds"] = 0.0
        return real_measure_frame(path, *args, **kwargs)

    monkeypatch.setattr(frame_measurement_run, "measure_frame", measure_with_tiny_budget)


def test_a_frame_cut_short_by_the_cpu_budget_is_not_stamped_and_the_next_run_measures_it(
    target: Target, store: QualityStore, biases: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spent budget leaves the frame unstamped; a second run retries it."""
    slow = target.frames[1]
    _spend_budget_of_one_frame(monkeypatch, os.path.basename(slow.path))

    first = record_frame_measurements_for_target(target, _session(store, biases))

    assert first.frames_measured == 2
    assert first.frames_cut_short == 1
    assert first.frames_failed == 0
    assert slow.quality_measured_file_size is None
    assert any("ran out of CPU time" in note for note in first.notes)
    slow_rows = [
        row
        for row in store.query(target_id="M 99", latest_only=False)
        if os.path.basename(slow.path) in row.subject.subject_id
    ]
    # The quick metrics are stored; the skipped metrics leave no row.
    assert slow_rows
    assert all(not row.reason.startswith("CPU time budget") for row in slow_rows)
    assert FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX not in {row.metric for row in slow_rows}

    monkeypatch.setattr(frame_measurement_run, "measure_frame", real_measure_frame)
    second = record_frame_measurements_for_target(target, _session(store, biases))

    assert second.frames_measured == 1
    assert second.frames_skipped_unchanged == 2
    assert second.frames_cut_short == 0
    assert slow.quality_measured_file_size is not None


def test_a_retry_adds_a_second_row_for_each_quick_metric_under_a_new_run_id(
    target: Target, store: QualityStore, biases: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry writes the measured metrics again; a latest read sees one."""
    slow = target.frames[0]
    _spend_budget_of_one_frame(monkeypatch, os.path.basename(slow.path))
    record_frame_measurements_for_target(target, _session(store, biases))
    monkeypatch.setattr(frame_measurement_run, "measure_frame", real_measure_frame)
    retry_session = FrameMeasurementSession(
        store,
        _Library(biases),
        run_id="run-retry",
        measured_at=datetime(2026, 10, 22, 7, 0, tzinfo=UTC),
        use_worker_processes=False,
    )

    record_frame_measurements_for_target(target, retry_session)

    every_row = [
        row
        for row in store.query(target_id="M 99", latest_only=False)
        if os.path.basename(slow.path) in row.subject.subject_id
    ]
    background = [row for row in every_row if row.metric == FRAME_BACKGROUND_LEVEL]
    assert {row.run_id for row in background} == {"run-test", "run-retry"}
    latest = [
        row for row in store.query(target_id="M 99") if os.path.basename(slow.path) in row.subject.subject_id
    ]
    assert len([row for row in latest if row.metric == FRAME_BACKGROUND_LEVEL]) == 1
    assert all(
        row.run_id == "run-retry" for row in latest if row.metric == FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX
    )


def test_a_frame_not_measured_for_a_permanent_reason_is_stamped(target: Target, store: QualityStore) -> None:
    """No bias at the setting is permanent: the frame is stamped."""
    summary = record_frame_measurements_for_target(target, _session(store, []))

    assert summary.frames_measured == 3
    assert summary.frames_cut_short == 0
    assert all(frame.quality_measured_file_size is not None for frame in target.frames[:3])
    sky = store.query(metric=FRAME_SKY_RATE_E_PER_S_PER_PX)
    assert sky
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in sky)


def test_only_a_budget_reason_counts_as_cut_short() -> None:
    """The helpers tell a budget skip from any other not-measured reason."""
    base = {
        "metric": FRAME_BACKGROUND_LEVEL,
        "definition_version": 1,
        "subject": QualitySubject(level=SubjectLevel.FRAME, subject_id="a", target_id="t"),
        "origin": MeasurementOrigin.MEASURED,
        "run_id": "r",
        "measured_at": datetime(2026, 10, 22, tzinfo=UTC),
        "status": MeasurementStatus.NOT_MEASURED,
    }
    budget = QualityMeasurement(reason="CPU time budget of 60 s exceeded after 61.0 s", **base)
    permanent = QualityMeasurement(reason="no gain for this setting", **base)

    assert frame_was_cut_short([budget, permanent])
    assert not frame_was_cut_short([permanent])
    assert measurements_to_store([budget, permanent]) == [permanent]
    assert measurements_to_store([permanent]) == [permanent]


def test_worker_processes_give_the_same_rows_as_worker_threads(
    target: Target, store: QualityStore, biases: list[str], tmp_path: Path
) -> None:
    """Worker processes store the same metrics as worker threads."""
    thread_target = Target(id="M 99", frames=[frame.model_copy(deep=True) for frame in target.frames])
    session = FrameMeasurementSession(
        store,
        _Library(biases),
        run_id="run-processes",
        measured_at=datetime(2026, 10, 22, 6, 0, tzinfo=UTC),
        use_worker_processes=True,
    )

    summary = record_frame_measurements_for_target(target, session)

    assert summary.frames_measured == 3
    assert summary.frames_failed == 0
    assert all(frame.quality_measured_file_size is not None for frame in target.frames[:3])
    thread_store = QualityStore(str(tmp_path / "threads.db"))
    record_frame_measurements_for_target(
        thread_target,
        _session(thread_store, biases),
    )
    process_metrics = sorted((row.metric, row.status.value) for row in store.query(target_id="M 99"))
    thread_metrics = sorted((row.metric, row.status.value) for row in thread_store.query(target_id="M 99"))
    assert process_metrics == thread_metrics
