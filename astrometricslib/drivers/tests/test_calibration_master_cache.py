"""Tests for reuse of master bias/dark/flat calibration frames.

Master frames were rebuilt from scratch for every target even though the
source calibration frames come from a shared library, so the same master
was re-stacked once per target: 168-474s each across 35 targets on the
2026-08-23 DSLR run, for only 9 distinct calibration combinations.
"""

import os
from unittest.mock import MagicMock

from astrometricslib.drivers import siril_interface
from astrometricslib.drivers.siril_interface import (
    CALIBRATION_MASTER_CACHE_DIRECTORY_NAME,
    _calibration_source_fingerprint,
)


def _make_processor(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Build an ImageProcessing with its work directory inside tmp_path.

    Returns
    -------
    processor : `ImageProcessing`
        Instance whose `workdir` (and therefore master cache) is
        sandboxed inside the test's temporary directory.
    """
    processor = object.__new__(siril_interface.ImageProcessing)
    processor.workdir = str(tmp_path / "Work")
    os.makedirs(processor.workdir, exist_ok=True)
    return processor


def _stage_frames(target_folder, subdirectory, library_directory, count):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Symlink `count` library frames into a staging subdirectory.

    Returns
    -------
    staging_directory : `str`
        The staging directory the frames were linked into.
    """
    staging = os.path.join(target_folder, subdirectory)
    os.makedirs(staging, exist_ok=True)
    os.makedirs(library_directory, exist_ok=True)
    for index in range(count):
        library_frame = os.path.join(library_directory, f"frame_{index}.fits")
        with open(library_frame, "w") as handle:
            handle.write(f"library frame {index}")
        os.symlink(library_frame, os.path.join(staging, f"staged_{index:05d}.fits"))
    return staging


def test_fingerprint_is_stable_for_the_same_frames(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The same library frames must fingerprint identically across runs."""
    library = str(tmp_path / "library")
    first = _stage_frames(str(tmp_path / "run_a"), "biases", library, 3)
    second = _stage_frames(str(tmp_path / "run_b"), "biases", library, 3)

    assert _calibration_source_fingerprint(first) == _calibration_source_fingerprint(second)


def test_fingerprint_changes_when_a_frame_is_added(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A different frame set must not reuse the previous master."""
    library = str(tmp_path / "library")
    three_frames = _stage_frames(str(tmp_path / "run_a"), "biases", library, 3)
    fingerprint_before = _calibration_source_fingerprint(three_frames)

    four_frames = _stage_frames(str(tmp_path / "run_b"), "biases", str(tmp_path / "library2"), 4)

    assert _calibration_source_fingerprint(four_frames) != fingerprint_before


def test_fingerprint_changes_when_a_source_frame_is_modified(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Editing a library frame must invalidate the cached master."""
    library = str(tmp_path / "library")
    staging = _stage_frames(str(tmp_path / "run"), "biases", library, 2)
    fingerprint_before = _calibration_source_fingerprint(staging)

    edited = os.path.join(library, "frame_0.fits")
    with open(edited, "w") as handle:
        handle.write("materially different content of a different length")
    os.utime(edited, (1_800_000_000, 1_800_000_000))

    assert _calibration_source_fingerprint(staging) != fingerprint_before


def test_empty_directory_has_no_fingerprint(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Nothing staged means nothing to cache."""
    empty = tmp_path / "biases"
    empty.mkdir()

    assert _calibration_source_fingerprint(str(empty)) is None


def test_store_then_restore_round_trips_a_master(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A master built by one run is reused by the next identical run."""
    processor = _make_processor(tmp_path)
    library = str(tmp_path / "library")

    first_run = str(tmp_path / "run_a")
    _stage_frames(first_run, "biases", library, 3)
    os.makedirs(os.path.join(first_run, "process"), exist_ok=True)
    with open(os.path.join(first_run, "process", "bias_stacked.fits"), "w") as handle:
        handle.write("MASTER BIAS PIXELS")
    processor.store_calibration_masters_in_cache(first_run)

    second_run = str(tmp_path / "run_b")
    _stage_frames(second_run, "biases", library, 3)
    restored = processor.restore_cached_calibration_masters(second_run)

    assert "bias" in restored
    with open(os.path.join(second_run, "process", "bias_stacked.fits")) as handle:
        assert handle.read() == "MASTER BIAS PIXELS"


def test_restore_misses_when_frames_differ(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A different calibration set must rebuild rather than reuse."""
    processor = _make_processor(tmp_path)

    first_run = str(tmp_path / "run_a")
    _stage_frames(first_run, "biases", str(tmp_path / "library_a"), 3)
    os.makedirs(os.path.join(first_run, "process"), exist_ok=True)
    with open(os.path.join(first_run, "process", "bias_stacked.fits"), "w") as handle:
        handle.write("MASTER A")
    processor.store_calibration_masters_in_cache(first_run)

    second_run = str(tmp_path / "run_b")
    _stage_frames(second_run, "biases", str(tmp_path / "library_b"), 3)

    assert processor.restore_cached_calibration_masters(second_run) == set()
    assert not os.path.exists(os.path.join(second_run, "process", "bias_stacked.fits"))


def test_each_master_kind_is_cached_independently(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A cached flat must not be served when only the bias matches."""
    processor = _make_processor(tmp_path)
    shared_bias_library = str(tmp_path / "bias_library")

    first_run = str(tmp_path / "run_a")
    _stage_frames(first_run, "biases", shared_bias_library, 2)
    _stage_frames(first_run, "flats", str(tmp_path / "flat_library_a"), 2)
    os.makedirs(os.path.join(first_run, "process"), exist_ok=True)
    for filename in ("bias_stacked.fits", "flat_stacked.fits"):
        with open(os.path.join(first_run, "process", filename), "w") as handle:
            handle.write(filename)
    processor.store_calibration_masters_in_cache(first_run)

    # Same biases, different flats.
    second_run = str(tmp_path / "run_b")
    _stage_frames(second_run, "biases", shared_bias_library, 2)
    _stage_frames(second_run, "flats", str(tmp_path / "flat_library_b"), 2)
    restored = processor.restore_cached_calibration_masters(second_run)

    assert restored == {"bias"}


def test_no_partial_files_are_left_in_the_cache(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Atomic writes must not leave .partial files a later run could read."""
    processor = _make_processor(tmp_path)
    run = str(tmp_path / "run")
    _stage_frames(run, "darks", str(tmp_path / "library"), 2)
    os.makedirs(os.path.join(run, "process"), exist_ok=True)
    with open(os.path.join(run, "process", "dark_stacked.fits"), "w") as handle:
        handle.write("MASTER DARK")

    processor.store_calibration_masters_in_cache(run)

    cache_directory = os.path.join(processor.workdir, CALIBRATION_MASTER_CACHE_DIRECTORY_NAME)
    assert not [name for name in os.listdir(cache_directory) if name.endswith(".partial")]


def test_cache_hit_is_reported_to_the_job_log(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A reused master is visible in the job log, not silent."""
    processor = _make_processor(tmp_path)
    library = str(tmp_path / "library")

    first_run = str(tmp_path / "run_a")
    _stage_frames(first_run, "biases", library, 2)
    os.makedirs(os.path.join(first_run, "process"), exist_ok=True)
    with open(os.path.join(first_run, "process", "bias_stacked.fits"), "w") as handle:
        handle.write("MASTER")
    processor.store_calibration_masters_in_cache(first_run)

    second_run = str(tmp_path / "run_b")
    _stage_frames(second_run, "biases", library, 2)
    job_logger = MagicMock()
    processor.restore_cached_calibration_masters(second_run, job_logger=job_logger)

    assert any("Reusing cached master bias" in str(call) for call in job_logger.info.call_args_list)
