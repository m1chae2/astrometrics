"""Purpose: Tests for the library mode of the TPM harness.

Description: The default run is a read-only copy of the real library plus the
simulated observatory (``--library simulation``); the made-up library is the
option ``--library synthetic``. These tests cover the option handling of
``run_tpm.sh`` (every refusal happens before anything starts), the setup key,
``copy_deep_catalog.py``, the helpers of ``simulation_library.py`` and the
mount-skipping walk of ``cold_cache.py``.

Run with ``.venv/bin/pytest build/tpm/test -q``.
"""

import json
import sqlite3
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path

import pytest

TPM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TPM_DIR))

import cold_cache  # ruff: ignore[module-import-not-at-top-of-file]
import copy_deep_catalog  # ruff: ignore[module-import-not-at-top-of-file]
import simulation_library  # ruff: ignore[module-import-not-at-top-of-file]
from setup_fingerprint import make_setup_key  # ruff: ignore[module-import-not-at-top-of-file]

RUN_TPM = TPM_DIR / "run_tpm.sh"
FINGERPRINT = {
    "cpu_model": "cpu",
    "displays": ["DP-1 1920x1080"],
    "ac_online": True,
    "power_profile": "performance",
}


def run_script(*arguments: str) -> subprocess.CompletedProcess:
    """Run ``run_tpm.sh`` with arguments that must be refused.

    Returns
    -------
    result : `subprocess.CompletedProcess`
        The finished process (standard error captured).
    """
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        ["/bin/bash", str(RUN_TPM), *arguments], capture_output=True, text=True, check=False, timeout=60
    )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--library", "bogus"], "--library must be simulation or synthetic"),
        (["--observatory", "offline"], "needs --library synthetic"),
        (["--observatory", "replay"], "needs --library synthetic"),
        (["--scale", "2"], "--scale needs --library synthetic"),
        (["--sky-catalog-copy"], "needs --library synthetic"),
        (["--real-snapshot", "--library", "synthetic"], "exclude each other"),
        (["--processing", "M 31", "--library", "simulation"], "not with --library simulation"),
    ],
)
def test_run_script_refuses_a_wrong_library_option(arguments: list[str], message: str) -> None:
    """A wrong mix of options ends with status 2 and says why."""
    result = run_script(*arguments)
    assert result.returncode == 2
    assert message in result.stderr


def test_synthetic_library_accepts_the_offline_options() -> None:
    """The synthetic library takes the options simulation refuses."""
    result = run_script("--library", "synthetic", "--scale", "2", "--observatory", "offline", "--preflight")
    assert "ERROR" not in result.stderr or "needs --library synthetic" not in result.stderr


def test_help_names_the_library_option() -> None:
    """The usage text lists --library and says which mode is the default."""
    result = run_script("--help")
    assert "--library simulation|synthetic" in result.stdout
    assert "simulation (the default)" in result.stdout


def test_library_mode_is_part_of_the_setup_key() -> None:
    """Each library mode has its own baseline key."""
    keys = {
        mode: make_setup_key(FINGERPRINT, "nvidia", mode, "simulated")
        for mode in ("simulation", "synthetic", "real-snapshot")
    }
    assert len(set(keys.values())) == 3


def test_only_the_simulation_mode_is_compared_with_a_baseline() -> None:
    """The compile step names the simulation mode as the one gated mode."""
    import compile_tpm

    assert compile_tpm.SIMULATION_MODE == "simulation"
    source = (TPM_DIR / "compile_tpm.py").read_text(encoding="utf-8")
    assert 'choices=(SIMULATION_MODE, "synthetic", "real-snapshot")' in source


def make_deep_catalog(path: Path, stars: int) -> None:
    """Make a small deep-star catalog database.

    Parameters
    ----------
    path : `pathlib.Path`
        The file to create.
    stars : `int`
        How many rows ``deep_stars`` gets.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE deep_stars (id INTEGER PRIMARY KEY)")
    connection.executemany("INSERT INTO deep_stars (id) VALUES (?)", [(index,) for index in range(stars)])
    connection.commit()
    connection.close()


def test_copy_deep_catalog_copies_and_counts(tmp_path: Path) -> None:
    """The copy lands in the sandbox and the record holds the count."""
    real = tmp_path / "real"
    make_deep_catalog(real / "catalogs" / copy_deep_catalog.DEEP_CATALOG_FILENAME, 7)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    record = copy_deep_catalog.copy_deep_catalog(sandbox, real)
    assert record is not None
    assert record["star_count"] == 7
    assert Path(record["path"]).is_file()
    assert json.loads((sandbox / "deep_catalog.json").read_text())["star_count"] == 7


def test_copy_deep_catalog_without_a_real_file_returns_none(tmp_path: Path) -> None:
    """A missing real catalog is not an error."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    assert copy_deep_catalog.copy_deep_catalog(sandbox, tmp_path / "nowhere") is None
    assert not (sandbox / "deep_catalog.json").exists()


def test_copy_deep_catalog_refuses_a_source_inside_the_sandbox(tmp_path: Path) -> None:
    """A real folder inside the throwaway folder is refused."""
    sandbox = tmp_path / "sandbox"
    make_deep_catalog(sandbox / "real" / "catalogs" / copy_deep_catalog.DEEP_CATALOG_FILENAME, 1)
    with pytest.raises(ValueError, match="inside the throwaway folder"):
        copy_deep_catalog.copy_deep_catalog(sandbox, sandbox / "real")


def test_copy_deep_catalog_main_needs_one_argument() -> None:
    """The command line takes exactly the sandbox."""
    assert copy_deep_catalog.main([]) == 2


def test_safe_copy_uses_the_backup_call_and_leaves_the_source_alone(tmp_path: Path) -> None:
    """The backup copy has every star and the source is unchanged."""
    source = tmp_path / "real.db"
    make_deep_catalog(source, 5)
    before = (source.stat().st_size, source.stat().st_mtime_ns)
    destination = tmp_path / "copy" / "deep.db"
    destination.parent.mkdir()
    destination.write_bytes(b"half written")
    assert simulation_library.copy_deep_catalog_safely(source, destination, attempts=1, wait_seconds=0) == 5
    assert (source.stat().st_size, source.stat().st_mtime_ns) == before


def test_describe_library_counts_the_copy(tmp_path: Path) -> None:
    """The record counts targets, frames and deep-catalog stars."""
    library = tmp_path / "library"
    library.mkdir()
    connection = sqlite3.connect(library / "astrometrics.db")
    for table in ("targets", "quality_measurements", "stellar_objects"):
        connection.execute(f"CREATE TABLE {table} (id INTEGER)")
    connection.executemany("INSERT INTO targets VALUES (?)", [(1,), (2,)])
    connection.execute("INSERT INTO quality_measurements VALUES (1)")
    connection.commit()
    connection.close()
    make_deep_catalog(library / simulation_library.DEEP_CATALOG_PATH, 3)
    record = simulation_library.describe_library(tmp_path, {"seconds": 1.5, "bytes": {"a.db": 10}})
    assert record["library_mode"] == "simulation"
    assert (record["targets"], record["measured_frames"], record["stellar_objects"]) == (2, 1, 0)
    assert record["deep_catalog_stars"] == 3
    assert record["copy_seconds"] == pytest.approx(1.5)


def test_walk_files_skips_mount_points(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A mount point (the overlay of the real library) is not walked."""
    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "real.fits").write_text("x")
    (tmp_path / "keep.txt").write_text("x")
    monkeypatch.setattr(cold_cache.os.path, "ismount", lambda path: path.endswith("frames"))
    names = [path.name for path in cold_cache.walk_files(tmp_path, skip_mounts=True)]
    assert names == ["keep.txt"]
    assert sorted(path.name for path in cold_cache.walk_files(tmp_path)) == ["keep.txt", "real.fits"]
