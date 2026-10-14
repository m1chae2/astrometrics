"""Tests for the optional frame pre-fetch step and its hook in stacking."""

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from astrometricslib.drivers import frame_prefetch, siril_interface
from astrometricslib.foundation.config import AppConfiguration


def test_prefetch_reads_all_files_and_skips_missing(tmp_path: Path) -> None:
    """Every existing file is read in full; a missing path is only counted."""
    paths = []
    for index in range(5):
        path = tmp_path / f"f{index}.fits"
        path.write_bytes(b"x" * 1000)
        paths.append(str(path))
    summary = frame_prefetch.prefetch_frames([*paths, str(tmp_path / "gone.fits")], 2, chunk_bytes=300)
    assert (summary.file_count, summary.byte_count) == (5, 5000)
    assert summary.failure_count == 0
    assert summary.skipped_missing_count == 1


def test_prefetch_counts_failures_without_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed read is counted and logged; other files still finish."""
    good = tmp_path / "good.fits"
    good.write_bytes(b"abc")
    bad = tmp_path / "bad.fits"
    bad.write_bytes(b"abc")
    real_read = frame_prefetch._read_whole_file

    def flaky(path: str, chunk_bytes: int) -> int:
        """Fail for the bad file only.

        Returns
        -------
        byte_count : `int`
            Bytes read for any file but the bad one.

        Raises
        ------
        OSError
            For the bad file.
        """
        if path.endswith("bad.fits"):
            raise OSError("boom")
        return real_read(path, chunk_bytes)

    monkeypatch.setattr(frame_prefetch, "_read_whole_file", flaky)
    summary = frame_prefetch.prefetch_frames([str(good), str(bad)], 2)
    assert (summary.file_count, summary.failure_count) == (1, 1)


def test_prefetch_uses_no_more_workers_than_asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """At most worker_count reads run at once."""
    for index in range(12):
        (tmp_path / f"f{index}").write_bytes(b"x")
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}
    real_read = frame_prefetch._read_whole_file

    def counting(path: str, chunk_bytes: int) -> int:
        """Track how many reads overlap.

        Returns
        -------
        byte_count : `int`
            Bytes read.
        """
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        try:
            return real_read(path, chunk_bytes)
        finally:
            with lock:
                state["now"] -= 1

    monkeypatch.setattr(frame_prefetch, "_read_whole_file", counting)
    frame_prefetch.prefetch_frames([str(tmp_path / f"f{i}") for i in range(12)], 3)
    assert state["peak"] <= 3


def test_prefetch_settings_default_off_and_four_workers() -> None:
    """Missing settings mean off and four workers."""
    configuration = AppConfiguration.__new__(AppConfiguration)
    configuration.get_value = lambda section, key, fallback=None: fallback
    assert configuration.get_prefetch_frames() is False
    assert configuration.get_prefetch_workers() == 4


def _staged_folder(tmp_path: Path) -> Path:
    """Make a work folder with one staged light symlink.

    Returns
    -------
    folder : `pathlib.Path`
        The work folder.
    """
    real = tmp_path / "real.fits"
    real.write_bytes(b"x")
    (tmp_path / "work" / "lights").mkdir(parents=True)
    (tmp_path / "work" / "lights" / "light_00001.fits").symlink_to(real)
    return tmp_path / "work"


def test_stacking_hook_runs_only_when_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The driver pre-fetches the resolved staged files only when allowed."""
    calls = []
    monkeypatch.setattr(
        siril_interface,
        "prefetch_frames",
        lambda paths, workers: calls.append((paths, workers)) or MagicMock(),
    )
    work = _staged_folder(tmp_path)
    driver = siril_interface.ImageProcessing.__new__(siril_interface.ImageProcessing)
    driver.config = MagicMock()
    driver.config.get_prefetch_workers.return_value = 2

    driver.config.get_prefetch_frames.return_value = False
    driver._prefetch_staged_frames(str(work), MagicMock())
    assert calls == []

    driver.config.get_prefetch_frames.return_value = True
    driver._prefetch_staged_frames(str(work), MagicMock())
    assert calls == [([str((tmp_path / "real.fits").resolve())], 2)]
