"""Purpose: Test that stale stored paths move inside the sandbox.

Description: A database copy can hold a path to a folder that no longer
exists (an old stacks folder). The rewrite to the overlay misses it, and the
guard refuses the run. ``real_snapshot.repoint_outside_paths`` moves such a
path to ``<sandbox>/outside/...``. These tests use a small temporary SQLite
database and never touch the real library.

Run with ``.venv/bin/pytest build/tpm/test -q``.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

TPM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TPM_DIR))

import real_snapshot  # ruff: ignore[module-import-not-at-top-of-file]

STALE = "/old/Astrometrics/stacks/lights/M 1/M1_Stacked.fits"
OVERLAY = "/mnt/overlay"


def _make_sandbox(tmp_path: Path) -> Path:
    """Build a sandbox with a stale path and an overlay path.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The temporary folder.

    Returns
    -------
    sandbox : `pathlib.Path`
        The sandbox folder.
    """
    sandbox = tmp_path / "sandbox"
    (sandbox / "library").mkdir(parents=True)
    (sandbox / "wayfinding").mkdir()
    record = {"stackedImage": STALE, "kept": f"{OVERLAY}/lights/M1/a.fits", "list": [STALE]}
    connection = sqlite3.connect(sandbox / "library" / "astrometrics.db")
    connection.execute("CREATE TABLE targets (id TEXT PRIMARY KEY, data_json TEXT)")
    connection.execute("INSERT INTO targets VALUES ('m1', ?)", (json.dumps(record),))
    connection.commit()
    connection.close()
    (sandbox / "astrometrics.config.toml").write_text(
        f'["Image Library"]\npath = "{sandbox / "library"}"\nframes_path = "{OVERLAY}"\n'
        f'stacks_path = "{OVERLAY}/stacks"\n\n["Wayfinding Library"]\npath = "{sandbox / "wayfinding"}"\n'
    )
    (sandbox / "real_snapshot.json").write_text(json.dumps({"real_library": "/real/library"}))
    return sandbox


def test_stale_path_is_repointed_and_guard_passes(tmp_path: Path) -> None:
    """The guard refuses a stale path; after repointing it passes."""
    sandbox = _make_sandbox(tmp_path)
    assert real_snapshot.guard(sandbox) == 1
    assert real_snapshot.repoint_outside_paths(sandbox) == 2
    assert real_snapshot.guard(sandbox) == 0
    stored = json.loads(
        sqlite3
        .connect(sandbox / "library" / "astrometrics.db")
        .execute("SELECT data_json FROM targets")
        .fetchone()[0]
    )
    assert stored["stackedImage"] == str(sandbox / "outside" / STALE.lstrip("/"))
    assert stored["kept"] == f"{OVERLAY}/lights/M1/a.fits"


def test_toml_string_keeps_a_hostile_path_inside_its_string() -> None:
    """A quote or backslash in a path cannot end the TOML string early."""
    import tomllib

    path = 'C:\\data "x"\nline'
    parsed = tomllib.loads(f"key = {real_snapshot.toml_string(path)}\n")
    assert parsed["key"] == path


def test_prepare_writes_an_offline_launch_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The throwaway settings say offline, so no client leaves the computer."""
    import tomllib

    config_home = tmp_path / "config"
    library = tmp_path / "real_library"
    (library / "wayfinding").mkdir(parents=True)
    for name in ("astrometrics.db", "astrometrics_log.db"):
        sqlite3.connect(library / name).close()
    sqlite3.connect(library / "wayfinding" / "wayfinding.db").close()
    config = config_home / "astrometrics" / "astrometrics.config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        f'["Image Library"]\npath = "{library}"\nframes_path = "{library}"\nstacks_path = "{library}"\n'
        f'["Wayfinding Library"]\npath = "{library / "wayfinding"}"\n'
    )
    monkeypatch.setenv("ASTROMETRICS_REAL_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("ASTROMETRICS_REAL_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("ASTROMETRICS_REAL_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("ASTROMETRICS_REAL_CONFIG_PATH", str(config))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    (tmp_path / "cache" / "astrometrics" / "catalogs").mkdir(parents=True)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    assert real_snapshot.prepare(sandbox, tmp_path / "mount") == 0

    parsed = tomllib.loads((sandbox / "astrometrics.config.toml").read_text())
    assert parsed["Launch"]["offline"] is True
    assert parsed["Launch"]["live_hardware"] is False
    assert parsed["Image Library"]["frames_path"] == str(tmp_path / "mount")
