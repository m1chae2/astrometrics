"""Tests for frame-geometry filtering and work-directory housekeeping.

Both behaviours come from the 2026-08-24 batch run. Siril refuses to
build a sequence from frames of differing geometry and fails the whole
conversion rather than the offending frame, so Sun lost all 450 frames
to 4 strays and M 27 all 126 to 45. Separately, failed runs kept their
entire work directory for diagnosis -- 65GB for M 106, of which the
artifacts anyone actually reads came to about 1MB.
"""

import os
import time

import numpy as np
from astropy.io import fits

from astrometricslib.data_access.image_type import select_dominant_frame_dimensions
from astrometricslib.drivers import siril_interface
from astrometricslib.drivers.siril_interface import purge_stale_work_directories


def _write_frame(path, width, height):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Write a FITS frame of the given geometry.

    Returns
    -------
    path : `str`
        The path just written, as a string.
    """
    fits.PrimaryHDU(np.zeros((height, width), dtype=np.float32)).writeto(path, overwrite=True)
    return str(path)


def test_uniform_frames_are_all_kept(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A clean target must not lose a single frame to this filter."""
    paths = [_write_frame(tmp_path / f"f{i}.fits", 60, 40) for i in range(4)]

    kept, dimensions = select_dominant_frame_dimensions(paths)

    assert kept == set(paths)
    assert dimensions == (60, 40)


def test_a_few_stray_frames_do_not_cost_the_whole_target(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Sun's shape: 4 odd frames among hundreds must not fail the stack."""
    majority = [_write_frame(tmp_path / f"good{i}.fits", 60, 40) for i in range(10)]
    strays = [
        _write_frame(tmp_path / "odd1.fits", 24, 17),
        _write_frame(tmp_path / "odd2.fits", 22, 21),
    ]

    kept, dimensions = select_dominant_frame_dimensions(majority + strays)

    assert kept == set(majority)
    assert dimensions == (60, 40)


def test_a_large_minority_is_still_excluded(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """M 27's shape: 45 of 126 frames differ, and the majority still wins."""
    majority = [_write_frame(tmp_path / f"a{i}.fits", 6000, 4000) for i in range(8)]
    minority = [_write_frame(tmp_path / f"b{i}.fits", 6016, 4016) for i in range(5)]

    kept, dimensions = select_dominant_frame_dimensions(majority + minority)

    assert kept == set(majority)
    assert dimensions == (6000, 4000)


def test_an_even_split_prefers_the_larger_geometry(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A tie must resolve deterministically, not by read order."""
    small = [_write_frame(tmp_path / f"s{i}.fits", 40, 30) for i in range(3)]
    large = [_write_frame(tmp_path / f"l{i}.fits", 60, 40) for i in range(3)]

    kept, dimensions = select_dominant_frame_dimensions(small + large)

    assert dimensions == (60, 40)
    assert kept == set(large)


def test_an_unreadable_frame_is_never_dropped_by_this_filter(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Readability is a separate concern with its own reporting.

    Dropping a frame here on a failed header read would hide it from
    the corrupt-frame diagnostics that exist to surface it.
    """
    good = [_write_frame(tmp_path / f"g{i}.fits", 60, 40) for i in range(3)]
    broken = tmp_path / "broken.fits"
    broken.write_bytes(b"not FITS")

    kept, _ = select_dominant_frame_dimensions([*good, str(broken)])

    assert str(broken) in kept


def test_no_frames_yields_no_dimensions():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """An empty candidate list is not an error."""
    assert select_dominant_frame_dimensions([]) == (set(), None)


def test_intermediates_are_discarded_but_evidence_is_kept(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A failed run keeps its logs and staged frame lists, not its bulk."""
    processor = object.__new__(siril_interface.ImageProcessing)
    target_folder = tmp_path / "FailedTarget"
    (target_folder / "process").mkdir(parents=True)
    (target_folder / "lights").mkdir()
    (target_folder / "process" / "light_source.fits").write_bytes(b"x" * 5000)
    (target_folder / "lights" / "light_00001.fits").write_bytes(b"y")
    (target_folder / "siril_debug.log").write_text("what went wrong")

    reclaimed = processor._discard_stacking_intermediates(str(target_folder))

    assert reclaimed >= 5000
    assert not (target_folder / "process").exists()
    assert (target_folder / "siril_debug.log").read_text() == "what went wrong"
    assert (target_folder / "lights" / "light_00001.fits").exists()


def test_discarding_intermediates_twice_is_harmless(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Cleanup must be safe to reach on any failure path."""
    processor = object.__new__(siril_interface.ImageProcessing)
    target_folder = tmp_path / "T"
    target_folder.mkdir()

    assert processor._discard_stacking_intermediates(str(target_folder)) == 0


def test_stale_directories_are_purged(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Debris from a cancelled run is reclaimed by a later one."""
    stale = tmp_path / "OldTarget"
    (stale / "process").mkdir(parents=True)
    (stale / "process" / "big.fits").write_bytes(b"z" * 4096)
    ancient = time.time() - 30 * 86400
    os.utime(stale, (ancient, ancient))

    removed, reclaimed = purge_stale_work_directories(str(tmp_path), retention_days=7)

    assert removed == 1
    assert reclaimed >= 4096
    assert not stale.exists()


def test_recent_directories_survive(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A run in progress must keep its scratch."""
    active = tmp_path / "ActiveTarget"
    (active / "process").mkdir(parents=True)

    removed, _ = purge_stale_work_directories(str(tmp_path), retention_days=7)

    assert removed == 0
    assert active.exists()


def test_the_master_cache_is_never_purged(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The cache is the optimisation; sweeping it would undo the win."""
    cache = tmp_path / siril_interface.CALIBRATION_MASTER_CACHE_DIRECTORY_NAME
    cache.mkdir()
    (cache / "bias_abc.fits").write_bytes(b"master")
    ancient = time.time() - 365 * 86400
    os.utime(cache, (ancient, ancient))

    removed, _ = purge_stale_work_directories(str(tmp_path), retention_days=7)

    assert removed == 0
    assert (cache / "bias_abc.fits").exists()


def test_purging_a_missing_workdir_is_not_an_error(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A fresh install has no work root yet."""
    assert purge_stale_work_directories(str(tmp_path / "nope")) == (0, 0)
