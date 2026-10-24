"""Purpose: Tests for the guard that protects the real library from agent runs.

Description: ``build/agent_tools/copy_path_guard.py`` refuses a real-data run
when a copy database stores absolute paths outside the copy. These tests
build a small copy database with a ``targets`` table shaped like the real
one and check that a clean copy passes, a real stacks path is refused, the
frames path is allowed, a wayfinding database is scanned, and repointing
fixes the copy.
"""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "build" / "agent_tools" / "copy_path_guard.py"
spec = importlib.util.spec_from_file_location("copy_path_guard", SCRIPT_PATH)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

REAL_STACKS = "/real/stacks"
REAL_FRAMES = "/real/frames"


def _make_copy(tmp_path: Path, record: dict, frames_in_config: bool = True) -> Path:
    """Build a copy library with one target record.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The scratch folder.
    record : `dict`
        The target's JSON record.
    frames_in_config : `bool`, optional
        Whether the config names the real frames folder.

    Returns
    -------
    config_file : `pathlib.Path`
        The copy config.
    """
    library = tmp_path / "library"
    library.mkdir()
    connection = sqlite3.connect(library / "astrometrics.db")
    connection.execute(
        "CREATE TABLE targets (id TEXT PRIMARY KEY, name TEXT, ra TEXT, dec TEXT, data_json TEXT)"
    )
    connection.execute("INSERT INTO targets VALUES ('m13', 'M 13', '1', '2', ?)", (json.dumps(record),))
    connection.commit()
    connection.close()
    config_file = tmp_path / "copy.config"
    lines = ["[Image Library]", f'path = "{library}"', f'stacks_path = "{tmp_path / "stacks"}"']
    lines += ["[Wayfinding Library]", f'path = "{tmp_path / "wayfinding"}"', "[Image Library]"]
    if frames_in_config:
        lines.append(f'frames_path = "{REAL_FRAMES}"')
    config_file.write_text("\n".join(lines) + "\n")
    return config_file


def test_clean_copy_passes(tmp_path: Path) -> None:
    """A copy whose stored paths are all inside the copy has no offenders."""
    record = {
        "stacking": {"stacked_image": str(tmp_path / "stacks/lights/M13/M13_Stacked.fits"), "note": "/"}
    }
    assert guard.check(_make_copy(tmp_path, record), tmp_path) == []


def test_real_stacks_path_is_refused(tmp_path: Path) -> None:
    """A real stacks path is listed with its target and key."""
    record = {"stacking": {"stacked_image": f"{REAL_STACKS}/lights/M13/M13_Stacked.fits"}}
    offenders = guard.check(_make_copy(tmp_path, record), tmp_path)
    assert len(offenders) == 1
    assert offenders[0][2] == "m13"
    assert offenders[0][3].endswith("stacking.stacked_image")


def test_frames_path_is_allowed(tmp_path: Path) -> None:
    """Paths under the config's frames folder may stay at their real place."""
    record = {"inputs": [f"{REAL_FRAMES}/lights/M13/frame1.fits"]}
    assert guard.check(_make_copy(tmp_path, record), tmp_path) == []


def test_repoint_fixes_the_copy(tmp_path: Path) -> None:
    """Repointing moves stacks paths into the copy, not frames paths."""
    record = {"a": f"{REAL_STACKS}/lights/M13/inputs.json", "b": f"{REAL_FRAMES}/lights/M13/f.fits"}
    config_file = _make_copy(tmp_path, record)
    assert guard.repoint(config_file, tmp_path, real_data_folder="/real/data") == 1
    assert guard.check(config_file, tmp_path) == []
    stored = json.loads(
        sqlite3
        .connect(tmp_path / "library" / "astrometrics.db")
        .execute("SELECT data_json FROM targets")
        .fetchone()[0]
    )
    assert stored["a"] == str(tmp_path / "stacks" / "lights" / "M13" / "inputs.json")
    assert stored["b"] == f"{REAL_FRAMES}/lights/M13/f.fits"


def test_repoint_refuses_database_outside_scratch(tmp_path: Path) -> None:
    """Repointing refuses a database outside the scratch folder."""
    config_file = _make_copy(tmp_path, {"a": "/real/stacks/lights/x"})
    other = tmp_path / "elsewhere"
    other.mkdir()
    with pytest.raises(ValueError):
        guard.repoint(config_file, other)


def test_wayfinding_database_outside_path_is_refused(tmp_path: Path) -> None:
    """A wayfinding.db that stores a real path is found in its own folder."""
    config_file = _make_copy(tmp_path, {})
    wayfinding = tmp_path / "wayfinding"
    wayfinding.mkdir()
    connection = sqlite3.connect(wayfinding / "wayfinding.db")
    connection.execute("CREATE TABLE sessions (id TEXT, payload TEXT)")
    connection.execute(
        "INSERT INTO sessions VALUES ('s1', ?)", (json.dumps({"log": "/real/data/wayfinding/ekos.log"}),)
    )
    connection.commit()
    connection.close()
    offenders = guard.check(config_file, tmp_path)
    assert [(row[0], row[1], row[2]) for row in offenders] == [("wayfinding.db", "sessions", "s1")]
    assert guard.repoint(config_file, tmp_path, real_data_folder="/real/data") == 1
    assert guard.check(config_file, tmp_path) == []
