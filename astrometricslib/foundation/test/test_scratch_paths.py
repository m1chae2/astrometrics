"""Tests for the working-file folders in `foundation.paths`."""

import os
from pathlib import Path

import pytest

from astrometricslib.foundation.paths import (
    catalog_cache_directory,
    data_directory,
    locks_directory,
    scratch_directory,
    siril_work_directory,
)


def test_scratch_directory_under_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scratch folder is `<cache>/astrometrics` and is made on demand."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    scratch_path = scratch_directory()
    assert scratch_path == os.path.join(str(tmp_path), "astrometrics")
    assert os.path.isdir(scratch_path)


def test_siril_directory_under_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Siril's work folder is `<cache>/astrometrics/siril`."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    work_path = siril_work_directory()
    assert work_path == os.path.join(str(tmp_path), "astrometrics", "siril")
    assert os.path.isdir(work_path)


def test_default_cache_root_is_home_dot_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without `XDG_CACHE_HOME`, the folders sit under `~/.cache`."""
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(os, "makedirs", lambda *args, **kwargs: None)
    expected_root = os.path.join(os.path.expanduser("~"), ".cache")
    assert scratch_directory() == os.path.join(expected_root, "astrometrics")
    assert siril_work_directory() == os.path.join(expected_root, "astrometrics", "siril")


def test_locks_and_catalog_folders_sit_inside_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The locks and catalog folders are inside the scratch folder."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert locks_directory() == os.path.join(str(tmp_path), "astrometrics", "locks")
    assert catalog_cache_directory() == os.path.join(str(tmp_path), "astrometrics", "catalogs")
    assert os.path.isdir(locks_directory())
    assert os.path.isdir(catalog_cache_directory())


def test_data_directory_uses_xdg_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The durable folder is `<data home>/astrometrics`, made on demand."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    data_path = data_directory()
    assert data_path == os.path.join(str(tmp_path), "astrometrics")
    assert os.path.isdir(data_path)


def test_data_directory_default_is_local_share(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without `XDG_DATA_HOME`, the folder is under `~/.local/share`."""
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(os, "makedirs", lambda *args, **kwargs: None)
    expected = os.path.join(os.path.expanduser("~"), ".local", "share", "astrometrics")
    assert data_directory() == expected
