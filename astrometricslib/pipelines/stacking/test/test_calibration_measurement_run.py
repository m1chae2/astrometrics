"""Tests for the dark and bias measurement job and its stamps.

The frames are tiny FITS files filed in a library that saves inside the test's
temporary folder. Nothing touches the real calibration index.
"""

import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.drivers.calibration_library import CalibrationLibrary
from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.pipelines.shared.quality.calibration_frame_measurement import (
    calibration_metric_set_fingerprint,
    calibration_subject_id,
)
from astrometricslib.pipelines.shared.quality.metrics import (
    CALIBRATION_FRAME_METRIC_NAMES,
    FRAME_CLIPPED_MEAN_LEVEL,
)
from astrometricslib.pipelines.stacking.calibration_measurement_run import (
    CalibrationMeasurementSummary,
    measure_calibration_frames,
)

MEASURED_AT = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
CAMERA = "ZWO CCD ASI533MM Pro"
FRAME_SHAPE = (120, 160)


def _library(tmp_path: Path) -> CalibrationLibrary:
    """Build an empty library that saves and loads inside `tmp_path`.

    Returns
    -------
    library : `CalibrationLibrary`
        A library with no frames.
    """
    app_config = SimpleNamespace(get_library_file_path=lambda name: str(tmp_path / name))
    return CalibrationLibrary(app_config=app_config)


def _write(path: Path, level: float, seed: int, **cards: object) -> str:
    """Write a small frame with noise around a level.

    Returns
    -------
    path : `str`
        The file's path.
    """
    generator = np.random.default_rng(seed)
    pixels = np.round(generator.normal(level, 4.0, FRAME_SHAPE)).clip(0, 65535).astype(np.uint16)
    header = fits.Header()
    header["INSTRUME"] = CAMERA
    header["GAIN"] = 0
    header["OFFSET"] = 10
    for key, value in cards.items():
        header[key.replace("_", "-")] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    fits.PrimaryHDU(pixels, header=header).writeto(path, overwrite=True)
    return str(path)


def _run(
    library: CalibrationLibrary, store: QualityStore, **options: object
) -> CalibrationMeasurementSummary:
    """Run the job with fixed run facts.

    Returns
    -------
    summary : `CalibrationMeasurementSummary`
        What the run did.
    """
    return measure_calibration_frames(
        library, store, None, run_id="run", job_id=None, measured_at=MEASURED_AT, **options
    )


@pytest.fixture
def filled(tmp_path: Path) -> tuple[CalibrationLibrary, QualityStore, list[str]]:
    """Build a library with three darks and two biases, and an empty store.

    Returns
    -------
    fixture : `tuple`
        The library, the store and the frame paths.
    """
    library = _library(tmp_path)
    paths = []
    for index in range(3):
        path = _write(
            tmp_path / "frames" / "darks" / f"Dark_{index}.fits", 400.0, index, EXPTIME=30.0, CCD_TEMP=-10.0
        )
        library.add_dark_frame(path)
        paths.append(path)
    for index in range(2):
        path = _write(tmp_path / "frames" / "biases" / f"Bias_{index}.fits", 400.0, 10 + index, EXPTIME=0.0)
        library.add_bias_frame(path)
        paths.append(path)
    return library, QualityStore(str(tmp_path / "quality.db")), paths


def test_sets_are_listed_by_kind_with_their_settings(filled: tuple) -> None:
    """Darks are filed by exposure and temperature slot, biases by setting."""
    library, _, _ = filled
    (dark_set,) = library.list_calibration_sets("dark")
    (bias_set,) = library.list_calibration_sets("bias")
    assert len(dark_set.paths) == 3
    assert dark_set.exposure_seconds == pytest.approx(30.0)
    assert dark_set.temperature_c is not None
    assert dark_set.offset == pytest.approx(10.0)
    assert len(bias_set.paths) == 2
    assert bias_set.exposure_seconds is None
    assert bias_set.temperature_c is None


def test_the_first_run_measures_every_frame_and_stores_one_row_per_metric(filled: tuple) -> None:
    """Five frames give five times the metric count of rows, with no target."""
    library, store, _ = filled
    summary = _run(library, store)
    assert summary.frame_count == 5
    assert summary.measured_count == 5
    assert summary.rows_stored == 5 * len(CALIBRATION_FRAME_METRIC_NAMES)
    rows = store.query(metric=FRAME_CLIPPED_MEAN_LEVEL)
    assert len(rows) == 5
    assert all(row.subject.target_id is None for row in rows)


def test_a_second_run_skips_unchanged_files(filled: tuple) -> None:
    """The stamps make a second run read nothing and store nothing."""
    library, store, _ = filled
    _run(library, store)
    again = _run(library, store)
    assert again.unchanged_count == 5
    assert again.measured_count == 0
    assert again.rows_stored == 0


def test_stamps_survive_a_save_and_a_reload(filled: tuple, tmp_path: Path) -> None:
    """A new library object loaded from the index still skips the frames."""
    library, store, paths = filled
    _run(library, store)
    reloaded = _library(tmp_path)
    assert reloaded.measurement_stamp(calibration_subject_id(paths[0], "dark")) is not None
    assert _run(reloaded, store).unchanged_count == 5


def test_a_changed_file_is_measured_again(filled: tuple) -> None:
    """Touching a file changes its time, so it is measured again."""
    library, store, paths = filled
    _run(library, store)
    stat = os.stat(paths[0])
    os.utime(paths[0], ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    summary = _run(library, store)
    assert summary.measured_count == 1
    assert summary.unchanged_count == 4


def test_a_stamp_from_an_older_metric_set_is_measured_again(filled: tuple) -> None:
    """A frame stamped with another fingerprint counts as not yet measured."""
    library, store, paths = filled
    _run(library, store)
    subject_id = calibration_subject_id(paths[0], "dark")
    stamp = library.measurement_stamp(subject_id)
    library.set_measurement_stamp(subject_id, stamp["size"], stamp["modified_ns"], "old-set")
    assert _run(library, store).measured_count == 1
    assert library.measurement_stamp(subject_id)["metric_set"] == calibration_metric_set_fingerprint()


def test_force_measures_every_frame_again(filled: tuple) -> None:
    """With force the stamps are ignored."""
    library, store, _ = filled
    _run(library, store)
    assert _run(library, store, force=True).measured_count == 5


def test_an_unreadable_frame_is_counted_and_not_stamped(filled: tuple) -> None:
    """A truncated file is reported, stores nothing and is tried again."""
    library, store, paths = filled
    Path(paths[0]).write_bytes(b"not a fits file")
    summary = _run(library, store)
    assert summary.unreadable_count == 1
    assert summary.measured_count == 4
    assert library.measurement_stamp(calibration_subject_id(paths[0], "dark")) is None
    assert summary.notes


def test_the_job_can_be_limited_to_one_kind(filled: tuple) -> None:
    """Only the asked kind is looked at."""
    library, store, _ = filled
    summary = _run(library, store, kinds=("bias",))
    assert summary.frame_count == 2
    assert summary.measured_count == 2


def test_the_store_reads_rows_by_subject_prefix(filled: tuple) -> None:
    """The prefix picks the dark rows and leaves the bias rows out."""
    library, store, _ = filled
    _run(library, store)
    darks = store.query(metric=FRAME_CLIPPED_MEAN_LEVEL, subject_id_prefix="calibration/dark/")
    assert len(darks) == 3
    assert not store.query(metric=FRAME_CLIPPED_MEAN_LEVEL, subject_id_prefix="calibration/flat/")
