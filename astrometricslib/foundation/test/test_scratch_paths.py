"""Tests for the working-file folders in `foundation.paths`."""

import os
from pathlib import Path

import pytest

from astrometricslib.foundation.paths import scratch_directory, siril_work_directory


def test_scratch_directory_under_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scratch folder is `<cache>/astrometrics` and is made on demand."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    scratch_path = scratch_directory()
    assert scratch_path == os.path.join(str(tmp_path), "astrometrics")
    assert os.path.isdir(scratch_path)


def test_siril_directory_under_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Siril's work folder is `<cache>/siril` and is made on demand."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    work_path = siril_work_directory()
    assert work_path == os.path.join(str(tmp_path), "siril")
    assert os.path.isdir(work_path)


def test_default_cache_root_is_home_dot_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without `XDG_CACHE_HOME`, the folders sit under `~/.cache`."""
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(os, "makedirs", lambda *args, **kwargs: None)
    expected_root = os.path.join(os.path.expanduser("~"), ".cache")
    assert scratch_directory() == os.path.join(expected_root, "astrometrics")
    assert siril_work_directory() == os.path.join(expected_root, "siril")
