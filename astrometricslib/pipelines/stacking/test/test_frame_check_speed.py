"""Tests for the speed fixes of the pre-stack frame check.

Four things are checked, each with small synthetic frames and stand-ins for the
slow star fits:

1. The set-aside step stores the star elongation it fitted, so the next
   stack reads it and does not fit the frame again.
2. The stacking stage measures every kept frame that has no stored numbers (no
   limit of 40) and waits for the library's turn instead of skipping.
3. The sky background of a raw color mosaic is measured on 2 x 2 super-pixels,
   and a level of an older method is measured again.
4. The sky background of all frames is measured in a pool of worker threads
   with the same results, in the same order, as one frame at a time.
"""

import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from astropy.io import fits
from astropy.stats import sigma_clipped_stats

from astrometricslib._internal.drivers.calibration_library import CalibrationSelection
from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib.foundation.run_abandonment import RunAbandonedError
from astrometricslib.models.camera_profile import (
    CameraProfile,
    ProvenancedValue,
    ProvenanceKind,
    SensorSettingNoise,
    ValueProvenance,
)
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.quality import background_measurement
from astrometricslib.pipelines.shared.quality.background_measurement import (
    BACKGROUND_LEVEL_METHOD,
    measure_background_of_pixels,
    measure_frame_background_level,
)
from astrometricslib.pipelines.shared.quality.metrics import (
    FRAME_STAR_ELONGATION,
    FRAME_STAR_ELONGATION_ANGLE_DEG,
    FRAME_STAR_WIDTH_MOFFAT_PX,
)
from astrometricslib.pipelines.shared.quality.star_profile_fitting import StarProfileResult
from astrometricslib.pipelines.stacking import frame_measurement_run, frame_screening
from astrometricslib.pipelines.stacking.frame_measurement_run import (
    FrameQualityRecording,
    record_frame_measurements_for_target,
    record_stage_frame_quality,
)
from astrometricslib.pipelines.stacking.pre_processing import frame_set_aside

GAIN_SETTING = "100"
FRAME_SIZE = 96
MEASURED_AT = datetime(2026, 11, 23, 6, 0, tzinfo=UTC)


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
                gain=GAIN_SETTING,
                offset="0",
                binning="1x1",
                gain_e_per_adu=ProvenancedValue(value=0.5, provenance=provenance),
                read_noise_e=ProvenancedValue(value=2.0, provenance=provenance),
            ),
        ),
    )


class _Library:
    """A stand-in calibration library that offers no bias frames."""

    def select_bias_frames(self, camera: str, **settings: object) -> CalibrationSelection:
        """Offer nothing.

        Returns
        -------
        selection : `CalibrationSelection`
            No frames and no flags.
        """
        return CalibrationSelection([], [], [])


def _write_frame(path: Path, seed: int, size: int = FRAME_SIZE) -> None:
    """Write a small noisy frame.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write it.
    seed : `int`
        The random seed.
    size : `int`, optional
        The side of the square frame in pixels.
    """
    data = np.random.default_rng(seed).normal(500.0, 5.0, (size, size)).clip(0, 65535).astype(np.uint16)
    fits.PrimaryHDU(data).writeto(path, overwrite=True)


def _make_target(tmp_path: Path, count: int) -> Target:
    """Build a target with light frames that exist on disk.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The test's folder.
    count : `int`
        How many light frames.

    Returns
    -------
    target : `Target`
        The target.
    """
    folder = tmp_path / "lights" / "M 99" / "Scope" / "Cam"
    folder.mkdir(parents=True)
    frames = []
    for index in range(count):
        path = folder / f"light_{index:03d}.fits"
        _write_frame(path, seed=index)
        frames.append(
            FrameRecord(
                path=str(path),
                camera="Test Camera",
                telescope="Scope",
                gain=GAIN_SETTING,
                offset=0,
                binning=1,
                exposure_s=10.0,
                timestamp=1_792_000_000.0 + 60.0 * index,
                pixelScaleArcsec=2.0,
                sensorTemperatureC=-10.0,
            )
        )
    return Target(id="M 99", frames=frames)


@pytest.fixture(autouse=True)
def known_camera_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every camera name resolve to the test profile."""
    monkeypatch.setattr(frame_measurement_run, "resolve_camera_profile", lambda name: _profile())


def _planes(elongation: float) -> list[tuple[str | None, StarProfileResult, str]]:
    """Build the one-plane result the star-shape step gives for a frame.

    Parameters
    ----------
    elongation : `float`
        The frame's elongation.

    Returns
    -------
    planes : `list` of `tuple`
        What `measure_star_profile_planes` returns.
    """
    result = StarProfileResult(
        width_px=3.2,
        width_px_uncertainty=0.1,
        elongation=elongation,
        elongation_uncertainty=0.02,
        angle_deg=40.0,
        angle_uncertainty_deg=5.0,
        angle_coherence=0.6,
        star_count=120,
        candidate_count=150,
        median_beta=2.8,
    )
    return [(None, result, "")]


def test_the_elongation_the_set_aside_step_fitted_is_stored_and_not_fitted_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The next stack reads the stored elongation and fits no star."""
    target = _make_target(tmp_path, 3)
    config = SimpleNamespace(get_library_path=lambda: tmp_path)
    monkeypatch.setattr(frame_set_aside, "get_configuration", lambda: config)
    monkeypatch.setattr(
        frame_set_aside.raw_frame_check,
        "measure_raw_frame",
        lambda path: {"camera": "Test Camera", "sky_median_adu": 500.0},
    )
    elongations = {frame.path: 1.1 + 0.05 * index for index, frame in enumerate(target.frames)}
    fits_made: list[str] = []

    def fake_fit(path: str, camera: str | None) -> list[tuple[str | None, StarProfileResult, str]]:
        """Stand in for the slow star fit and count its calls.

        Returns
        -------
        planes : `list` of `tuple`
            One plane with the frame's elongation.
        """
        fits_made.append(path)
        return _planes(elongations[path])

    monkeypatch.setattr(frame_set_aside, "measure_star_profile_planes", fake_fit)

    # First stack: nothing stored, so every frame is fitted.
    first = [frame_set_aside.make_set_aside_measurer("M 99")(frame.path) for frame in target.frames]
    assert len(fits_made) == 3
    assert {measurement["elongation_source"] for measurement in first} == {"measured"}
    for measurement, frame in zip(first, target.frames, strict=True):
        measurement["path"] = frame.path
    recording = FrameQualityRecording(configuration=config, calibration_library=_Library())
    record_stage_frame_quality(target, [], first, target.frames, recording, MEASURED_AT)

    store = QualityStore.from_configuration(config)
    rows = store.query(metric=FRAME_STAR_ELONGATION)
    assert len(rows) == 3
    assert sorted(row.value for row in rows) == pytest.approx(sorted(elongations.values()))
    assert store.query(metric=FRAME_STAR_ELONGATION_ANGLE_DEG)
    assert store.query(metric=FRAME_STAR_WIDTH_MOFFAT_PX)

    # Second stack: the stored values are read, and no star is fitted.
    fits_made.clear()
    measure = frame_set_aside.make_set_aside_measurer("M 99")
    second = [measure(frame.path) for frame in target.frames]
    assert fits_made == []
    assert {measurement["elongation_source"] for measurement in second} == {"stored"}
    assert [measurement["star_elongation"] for measurement in second] == pytest.approx([
        elongations[frame.path] for frame in target.frames
    ])
    assert all("_star_profiles" not in measurement for measurement in second)


def test_a_frame_with_no_stored_elongation_gives_the_same_median_as_before() -> None:
    """The median over colour planes is unchanged by the refactoring."""
    planes = [
        ("R", StarProfileResult(elongation=1.2), ""),
        ("G", StarProfileResult(elongation=1.4), ""),
        ("B", StarProfileResult(elongation=None), ""),
    ]
    assert frame_set_aside.median_star_elongation(planes) == pytest.approx(1.3)
    assert frame_set_aside.median_star_elongation([("R", StarProfileResult(), "")]) is None


def test_the_stage_measures_every_frame_with_no_limit_of_forty(tmp_path: Path) -> None:
    """Forty-five unmeasured frames are all measured in one stage call."""
    target = _make_target(tmp_path, 45)
    config = SimpleNamespace(get_library_path=lambda: tmp_path)
    recording = FrameQualityRecording(configuration=config, calibration_library=_Library())
    summary = record_stage_frame_quality(target, target.frames, [], target.frames, recording, MEASURED_AT)
    assert summary.frames_measured == 45
    assert summary.frames_considered == 45
    assert not hasattr(frame_measurement_run, "STACKING_STAGE_MEASUREMENT_LIMIT")


def test_the_stage_waits_for_the_turn_of_another_targets_run(tmp_path: Path) -> None:
    """A stage call blocks while another run has the turn, then measures."""
    target = _make_target(tmp_path, 2)
    config = SimpleNamespace(get_library_path=lambda: tmp_path)
    recording = FrameQualityRecording(configuration=config, calibration_library=_Library())
    results: list[Any] = []

    assert frame_measurement_run._RUN_TURN.acquire(blocking=False)
    try:
        worker = threading.Thread(
            target=lambda: results.append(
                record_stage_frame_quality(target, target.frames, [], target.frames, recording, MEASURED_AT)
            )
        )
        worker.start()
        time.sleep(1.0)
        # Still waiting: it neither skipped nor finished.
        assert worker.is_alive()
        assert results == []
    finally:
        frame_measurement_run._RUN_TURN.release()
    worker.join(60)
    assert not worker.is_alive()
    assert results[0].already_running is False
    assert results[0].frames_measured == 2


def test_a_cancel_ends_the_wait_for_the_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A job cancelled while it waits for the turn stops waiting."""
    target = _make_target(tmp_path, 1)
    session = SimpleNamespace()

    def cancelled() -> None:
        """Say the run was given up on.

        Raises
        ------
        RunAbandonedError
            Always.
        """
        raise RunAbandonedError("The run was given up on.")

    monkeypatch.setattr(frame_measurement_run, "raise_if_abandoned", cancelled)
    assert frame_measurement_run._RUN_TURN.acquire(blocking=False)
    try:
        with pytest.raises(RunAbandonedError):
            record_frame_measurements_for_target(target, session)  # type: ignore[arg-type]
    finally:
        frame_measurement_run._RUN_TURN.release()
    # The per-target guard was released with the failed wait.
    assert target.id not in frame_measurement_run._ACTIVE_TARGET_IDS


def _mosaic_data(size: int = 400, seed: int = 3) -> np.ndarray:
    """Make a raw color mosaic with a different level in each color.

    Parameters
    ----------
    size : `int`, optional
        The side in pixels (even).
    seed : `int`, optional
        The random seed.

    Returns
    -------
    data : `numpy.ndarray`
        An RGGB mosaic of unsigned 16-bit integers, with a few stars.
    """
    generator = np.random.default_rng(seed)
    levels = np.array([[900.0, 1200.0], [1200.0, 700.0]])
    base = np.tile(levels, (size // 2, size // 2))
    data = base + generator.normal(0.0, 12.0, (size, size))
    data[100:104, 100:104] += 20000.0
    return data.clip(0, 65535).astype(np.uint16)


def test_a_binned_color_mosaic_gives_the_clipped_median_of_the_cell_means() -> None:
    """The mosaic level is the clipped median of the 2 x 2 cell means."""
    data = _mosaic_data()
    cells = data.astype(float).reshape(200, 2, 200, 2).mean(axis=(1, 3))
    expected = float(sigma_clipped_stats(cells, sigma=3.0)[1])
    assert measure_background_of_pixels(data, True) == pytest.approx(expected, abs=1e-9)
    # A cell mean averages the four colors: (900 + 2 * 1200 + 700) / 4.
    assert expected == pytest.approx(1000.0, abs=3.0)
    # The old estimator sat on one color's pixel values, off the average.
    mixed = measure_background_of_pixels(data, False)
    assert abs(mixed - 1000.0) > 20.0


def test_a_binned_color_mosaic_is_within_the_stated_tolerance_of_the_full_frame_estimate() -> None:
    """On a flat gray mosaic the binned level matches the full-frame level."""
    generator = np.random.default_rng(5)
    data = generator.normal(800.0, 10.0, (400, 400)).astype(np.uint16)
    binned = measure_background_of_pixels(data, True)
    full = measure_background_of_pixels(data, False)
    # A flat sky seen through four equal colors: the two estimators agree
    # within the noise of the median of the cell means (sigma / 2 / sqrt(N)).
    assert binned == pytest.approx(full, rel=2e-3)


def test_an_odd_sized_mosaic_drops_the_last_row_and_column() -> None:
    """A mosaic with an odd side is measured on the whole cells."""
    data = _mosaic_data(size=402)[:401, :399]
    assert measure_background_of_pixels(data, True) == pytest.approx(
        measure_background_of_pixels(data[:400, :398], True)
    )


def test_a_file_with_a_bayer_card_is_binned_and_one_without_is_not(tmp_path: Path) -> None:
    """The header card decides whether a frame is measured as a mosaic."""
    data = _mosaic_data()
    with_card = tmp_path / "mosaic.fits"
    header = fits.Header()
    header["BAYERPAT"] = "RGGB"
    fits.PrimaryHDU(data, header=header).writeto(with_card)
    without_card = tmp_path / "mono.fits"
    fits.PrimaryHDU(data).writeto(without_card)
    assert measure_frame_background_level(str(with_card)) == pytest.approx(
        measure_background_of_pixels(data, True)
    )
    assert measure_frame_background_level(str(without_card)) == pytest.approx(
        measure_background_of_pixels(data, False)
    )
    assert measure_frame_background_level(str(without_card)) == pytest.approx(
        float(sigma_clipped_stats(data.astype(float), sigma=3.0)[1])
    )


def _screening_frames(tmp_path: Path, count: int) -> list[FrameRecord]:
    """Build frames of mosaic files at different sky levels.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The test's folder.
    count : `int`
        How many frames.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        Frames whose files exist.
    """
    frames = []
    for index in range(count):
        data = (_mosaic_data(size=64, seed=index).astype(float) + 10.0 * index).astype(np.uint16)
        path = tmp_path / f"mosaic_{index:02d}.fits"
        header = fits.Header()
        header["BAYERPAT"] = "RGGB"
        fits.PrimaryHDU(data, header=header).writeto(path)
        frames.append(FrameRecord(path=str(path), camera="Test Camera"))
    return frames


def test_the_backgrounds_are_measured_in_a_pool_with_the_same_results_as_one_by_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pool results equal the serial results, on several worker threads."""
    frames = _screening_frames(tmp_path, 12)
    expected = [measure_frame_background_level(frame.path) for frame in frames]
    threads_used: set[int] = set()
    real = background_measurement.measure_frame_background_level

    def spy(path: str) -> float | None:
        """Record the thread that measured, then measure.

        Returns
        -------
        level : `float` or `None`
            The real level.
        """
        threads_used.add(threading.get_ident())
        time.sleep(0.05)
        return real(path)

    monkeypatch.setattr(background_measurement, "measure_frame_background_level", spy)
    frame_screening._measure_frame_backgrounds(frames, dry_run=True)

    assert [frame.measurements.background_level for frame in frames] == expected
    assert {frame.measurements.background_level_method for frame in frames} == {BACKGROUND_LEVEL_METHOD}
    assert len(threads_used) > 1
    assert threading.get_ident() not in threads_used


def test_a_level_of_an_older_method_is_measured_again_and_a_current_one_is_kept(tmp_path: Path) -> None:
    """A level with no method (or an old one) is replaced, never mixed in."""
    frames = _screening_frames(tmp_path, 3)
    frames[0].measurements.background_level = 123.0  # old method: no version stored
    frames[1].measurements.background_level = 456.0
    frames[1].measurements.background_level_method = BACKGROUND_LEVEL_METHOD
    frame_screening._measure_frame_backgrounds(frames, dry_run=True)
    assert frames[0].measurements.background_level == pytest.approx(
        measure_frame_background_level(frames[0].path)
    )
    assert frames[0].measurements.background_level_method == BACKGROUND_LEVEL_METHOD
    assert frames[1].measurements.background_level == pytest.approx(456.0)
    assert frames[2].measurements.background_level == pytest.approx(
        measure_frame_background_level(frames[2].path)
    )


def test_an_old_level_that_cannot_be_replaced_is_cleared(tmp_path: Path) -> None:
    """A frame whose file cannot be read ends with no level, not an old one."""
    frame = FrameRecord(path=str(tmp_path / "missing.fits"), camera="Test Camera")
    frame.measurements.background_level = 321.0
    frame_screening._measure_frame_backgrounds([frame], dry_run=True)
    assert frame.measurements.background_level is None
    assert frame.measurements.background_level_method is None
