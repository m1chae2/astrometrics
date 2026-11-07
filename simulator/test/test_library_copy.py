"""Purpose: Test the temporary copy of the real library for the simulator.

Description: Every test builds a made-up "real" library in a temporary
folder and names it with ``ASTROMETRICS_REAL_*`` variables. The user's own
library is never read. The overlay tests need ``fuse-overlayfs`` and are
skipped where it cannot mount. They check that the frames and the stacks are
visible through the mounts, that a write goes to the temporary layer and the
real files stay the same (hash and modification time), and that leaving the
mode unmounts and deletes only what the simulator made.
"""

import hashlib
import shutil
import sqlite3
import tomllib
from pathlib import Path

import pytest

from astrometricslib.drivers import real_folders
from simulator import library_copy
from wayfindinglib.exceptions import SimulationUnavailableError


def _overlay_works(tmp_path: Path) -> bool:
    """Say whether this computer can mount a ``fuse-overlayfs`` overlay.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        A temporary folder to try in.

    Returns
    -------
    works : `bool`
        `True` if a trial mount succeeded.
    """
    if shutil.which("fuse-overlayfs") is None or shutil.which("fusermount3") is None:
        return False
    trial = tmp_path / "trial"
    lower = trial / "lower"
    lower.mkdir(parents=True)
    try:
        library_copy.mount_overlay([lower], trial / "u", trial / "w", trial / "m")
    except SimulationUnavailableError:
        return False
    library_copy.unmount_overlay(trial / "m")
    return True


def _fingerprint(folder: Path) -> dict[str, tuple[str, int]]:
    """Describe every file under a folder by hash and modification time.

    Parameters
    ----------
    folder : `pathlib.Path`
        The folder.

    Returns
    -------
    fingerprint : `dict` [`str`, `tuple`]
        ``{relative path: (sha256, mtime in ns)}``.
    """
    return {
        str(path.relative_to(folder)): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def _make_real(tmp_path: Path, nested_stacks: bool) -> tuple[dict[str, str], Path, Path]:
    """Build a made-up real library, with databases, frames and stacks.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        A temporary folder.
    nested_stacks : `bool`
        `True` puts the stacks folder inside the library (as on a common
        install); `False` puts it in a separate folder.

    Returns
    -------
    environment : `dict` [`str`, `str`]
        ``ASTROMETRICS_REAL_*`` variables that name the made-up library.
    library, stacks : `pathlib.Path`
        The made-up library and stacks folders.
    """
    config_home = tmp_path / "real_config"
    data_home = tmp_path / "real_data"
    library = tmp_path / "real_library"
    stacks = library / "stacks" if nested_stacks else tmp_path / "real_stacks"
    (library / "lights" / "M 13" / "scope").mkdir(parents=True)
    (library / "lights" / "M 13" / "scope" / "frame_001.fits").write_bytes(b"raw frame")
    (stacks / "lights" / "M 13").mkdir(parents=True)
    (stacks / "lights" / "M 13" / "stack.fits").write_bytes(b"stacked")
    (library / "wayfinding").mkdir()
    for path, rows in (
        (
            library / "astrometrics.db",
            [("M 13", str(library / "lights" / "M 13" / "scope" / "frame_001.fits"))],
        ),
        (library / "astrometrics_log.db", [("job", f"{stacks}/lights/M 13/stack.fits")]),
        (library / "wayfinding" / "wayfinding.db", [("w", "x")]),
    ):
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE things (name TEXT, path TEXT)")
            connection.executemany("INSERT INTO things VALUES (?, ?)", rows)
            connection.execute("CREATE TABLE processing_jobs (id TEXT)")
    config = config_home / "astrometrics" / "astrometrics.config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        f'["Image Library"]\npath = "{library}"\nframes_path = "{library}"\nstacks_path = "{stacks}"\n'
        f'["Wayfinding Library"]\npath = "{library / "wayfinding"}"\n'
        '["Observatory.Telescope"]\nhostname = "stellarmate.local"\n'
        '["Observatory.Camera"]\nmodels = "My camera"\n'
    )
    environment = {
        "ASTROMETRICS_REAL_CONFIG_HOME": str(config_home),
        "ASTROMETRICS_REAL_DATA_HOME": str(data_home),
        "ASTROMETRICS_REAL_CONFIG_PATH": str(config),
    }
    return environment, library, stacks


def test_plan_puts_nested_folders_in_one_overlay(tmp_path: Path) -> None:
    """A stacks folder inside the library shares the library's overlay."""
    plan = library_copy.plan_mounts(tmp_path / "lib", tmp_path / "lib" / "stacks", tmp_path / "root")
    assert len(plan["mounts"]) == 1
    assert plan["mounts"][0]["lower"] == tmp_path / "lib"
    assert plan["stacks_path"] == plan["frames_path"] / "stacks"


def test_plan_gives_a_separate_stacks_folder_its_own_overlay(tmp_path: Path) -> None:
    """Disjoint folders get two overlays and two mount points."""
    plan = library_copy.plan_mounts(tmp_path / "lib", tmp_path / "stacks", tmp_path / "root")
    assert [mount["lower"] for mount in plan["mounts"]] == [tmp_path / "lib", tmp_path / "stacks"]
    assert plan["stacks_path"] == tmp_path / "root" / "library" / "stacks_view"


def test_settings_keep_the_real_equipment_and_the_telescope_sections() -> None:
    """The world keeps the real telescope and guiding sections."""
    text = (
        '["Observatory.Telescope"]\nfocal_length_mm = "405"\n'
        '["Observatory.Guiding"]\nphd2_port = "4400"\n'
        '["Image Library"]\npath = "/real"\n'
        '["Observatory.Camera"]\nmodels = "x"\n'
    )
    kept = library_copy.keep_sections(text)
    assert "focal_length_mm" in kept
    assert "Observatory.Guiding" in kept
    assert "/real" not in kept
    assert "Observatory.Camera" in kept


def test_a_harness_with_its_own_telescope_drops_the_telescope_sections() -> None:
    """The real-snapshot harness drops the telescope sections as well."""
    text = '["Observatory.Telescope"]\nhostname = "stellarmate.local"\n["Observatory.Camera"]\nmodels = "x"\n'
    kept = library_copy.keep_sections(text, library_copy.DROPPED_SECTIONS + library_copy.TELESCOPE_SECTIONS)
    assert "stellarmate" not in kept
    assert "Observatory.Camera" in kept


def test_paths_are_rewritten_only_at_folder_boundaries(tmp_path: Path) -> None:
    """``/lib`` changes ``/lib/a`` but not ``/library_old/a``."""
    database = tmp_path / "x.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE t (p TEXT, n INTEGER)")
        connection.executemany("INSERT INTO t VALUES (?, 1)", [("/lib/a",), ("/library_old/a",), ("/lib",)])
    library_copy.rewrite_paths(database, [("/lib", "/new")])
    with sqlite3.connect(database) as connection:
        rows = sorted(row[0] for row in connection.execute("SELECT p FROM t"))
    assert rows == ["/library_old/a", "/new", "/new/a"]


def test_paths_are_rewritten_in_every_text_column_and_other_values_stay(tmp_path: Path) -> None:
    """One pass fixes every text column; numbers, blobs and NULL stay."""
    database = tmp_path / "x.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE t (a TEXT, b TEXT, n INTEGER, raw BLOB, g TEXT GENERATED ALWAYS AS (a || '!'))"
        )
        connection.execute("INSERT INTO t (a, b, n, raw) VALUES ('/lib/x', '/lib', 7, X'2F6C6962')")
        connection.execute("INSERT INTO t (a, b, n, raw) VALUES ('other', NULL, 8, NULL)")
    changed = library_copy.rewrite_paths(database, [("/lib", "/new")])
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT a, b, n, raw FROM t ORDER BY n").fetchall()
    assert rows == [("/new/x", "/new", 7, b"/lib"), ("other", None, 8, None)]
    assert changed >= 1


def test_paths_are_rewritten_when_a_table_cannot_be_updated_in_one_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A table the one-pass update refuses is rewritten by column."""
    database = tmp_path / "x.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE t (a TEXT, b TEXT)")
        connection.execute("INSERT INTO t VALUES ('/lib/a', '/lib/b')")

    def refuse(*_arguments: object) -> int:
        """Fail the way SQLite does for an update it cannot run.

        Raises
        ------
        sqlite3.OperationalError
            Always.
        """
        raise sqlite3.OperationalError("refused")

    monkeypatch.setattr(library_copy, "_rewrite_table_in_one_pass", refuse)
    library_copy.rewrite_paths(database, [("/lib", "/new")])
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT a, b FROM t").fetchall() == [("/new/a", "/new/b")]


@pytest.mark.parametrize("nested", [True, False])
def test_copy_mounts_the_library_and_leaves_the_real_one_alone(tmp_path: Path, nested: bool) -> None:
    """Frames and stacks show through, writes stay out of the real one."""
    if not _overlay_works(tmp_path):
        pytest.skip("fuse-overlayfs cannot mount here")
    environment, _library, stacks = _make_real(tmp_path, nested)
    before = _fingerprint(tmp_path / "real_library")
    if not nested:
        before.update({f"stacks/{k}": v for k, v in _fingerprint(stacks).items()})
    root = tmp_path / "simulator"
    root.mkdir()
    real = real_folders(environment)
    report = library_copy.prepare_library_copy(root, real=real)
    try:
        frames_path, stacks_path = Path(report["frames_path"]), Path(report["stacks_path"])
        assert (frames_path / "lights" / "M 13" / "scope" / "frame_001.fits").read_bytes() == b"raw frame"
        assert (stacks_path / "lights" / "M 13" / "stack.fits").read_bytes() == b"stacked"
        with sqlite3.connect(root / "library" / "astrometrics.db") as connection:
            copied_path = connection.execute("SELECT path FROM things").fetchone()[0]
        assert copied_path.startswith(str(frames_path))
        with sqlite3.connect(root / "library" / "astrometrics_log.db") as connection:
            assert connection.execute("SELECT path FROM things").fetchone()[0].startswith(str(stacks_path))
        # A write, an overwrite and a delete through the mount.
        (frames_path / "lights" / "M 13" / "new.fits").write_bytes(b"new")
        (frames_path / "lights" / "M 13" / "scope" / "frame_001.fits").write_bytes(b"changed")
        (stacks_path / "lights" / "M 13" / "stack.fits").unlink()
        assert report["seconds"] >= 0
    finally:
        assert library_copy.teardown(root)
    after = _fingerprint(tmp_path / "real_library")
    if not nested:
        after.update({f"stacks/{k}": v for k, v in _fingerprint(stacks).items()})
    assert after == before
    assert not (root / "overlay").exists()
    assert not (root / "library" / "astrometrics.db").exists()
    assert not library_copy.is_mounted(root / "library" / "frames")


def test_teardown_deletes_only_inside_the_root(tmp_path: Path) -> None:
    """Nothing outside the simulator root is touched by a teardown."""
    root = tmp_path / "simulator"
    (root / "library").mkdir(parents=True)
    (root / "library" / "astrometrics.db").write_text("copy")
    outside = tmp_path / "library" / "astrometrics.db"
    outside.parent.mkdir()
    outside.write_text("real")
    assert library_copy.teardown(root)
    assert not (root / "library" / "astrometrics.db").exists()
    assert outside.read_text() == "real"


def test_missing_overlay_program_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without fuse-overlayfs the copy says so (offline fallback applies)."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(SimulationUnavailableError, match="fuse-overlayfs"):
        library_copy.mount_overlay([tmp_path], tmp_path / "u", tmp_path / "w", tmp_path / "m")


def test_backup_of_a_database_nobody_has_open_writes_nothing_beside_it(tmp_path: Path) -> None:
    """The copy of a WAL database adds no file to the real folder."""
    real = tmp_path / "real"
    real.mkdir()
    database = real / "a.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE t (x TEXT)")
    connection.execute("INSERT INTO t VALUES ('in the wal')")
    connection.commit()
    # Leave the -wal file behind with the data in it, but no -shm file.
    shutil.copyfile(database, tmp_path / "a.db")
    wal = Path(f"{database}-wal")
    shutil.copyfile(wal, tmp_path / "a.db-wal")
    connection.close()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    shutil.copyfile(tmp_path / "a.db", snapshot / "a.db")
    shutil.copyfile(tmp_path / "a.db-wal", snapshot / "a.db-wal")
    before = sorted(path.name for path in snapshot.iterdir())
    library_copy.backup_database(snapshot / "a.db", tmp_path / "copy" / "a.db")
    assert sorted(path.name for path in snapshot.iterdir()) == before
    with sqlite3.connect(tmp_path / "copy" / "a.db") as copied:
        assert copied.execute("SELECT x FROM t").fetchone()[0] == "in the wal"


def test_copy_report_sizes_match_the_files_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The report's byte counts are the copies' final sizes, not early ones."""
    library = tmp_path / "real_library"
    wayfinding = tmp_path / "real_wayfinding"
    library.mkdir()
    wayfinding.mkdir()
    connection = sqlite3.connect(library / "astrometrics.db")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE t (x TEXT)")
    connection.executemany("INSERT INTO t VALUES (?)", [("x" * 1000,)] * 2000)
    connection.commit()
    try:
        # Make the size taken inside the backup stale (the report said 4096).
        original = library_copy.backup_database

        def stale_size(source: Path, destination: Path) -> int:
            """Run the real backup but report the size of an empty page.

            Parameters
            ----------
            source, destination : `pathlib.Path`
                The database and its copy.

            Returns
            -------
            size : `int`
                A stale size of 4096 bytes.
            """
            original(source, destination)
            return 4096

        monkeypatch.setattr(library_copy, "backup_database", stale_size)
        root = tmp_path / "simulator"
        report = library_copy.copy_databases(root, library, wayfinding, [])
    finally:
        connection.close()
    copy = root / "library" / "astrometrics.db"
    assert report["bytes"]["astrometrics.db"] == library_copy.database_size(copy)
    assert report["bytes"]["astrometrics.db"] > 1_000_000
    assert report["bytes"]["astrometrics.db"] >= copy.stat().st_size


def test_a_real_launch_section_is_dropped_so_the_simulators_own_stands_alone() -> None:
    """A real ``[Launch]`` section does not double the simulator's."""
    real = '[Launch]\nlabel = "REAL NIGHT"\n\n["Observatory.Camera"]\nmodels = "x"\n'
    mine = '[Launch]\nlabel = "SIMULATOR"\n\n["Image Library"]\npath = "y"\n'

    text = library_copy.world_settings_text(real, mine)

    parsed = tomllib.loads(text)
    assert parsed["Launch"]["label"] == "SIMULATOR"
    assert parsed["Observatory.Camera"]["models"] == "x"
