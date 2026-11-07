"""Purpose: Make and remove the library copy of the TPM simulation mode.

Description: ``run_tpm.sh`` measures the app by default on a read-only copy of
the user's real library plus the simulated observatory, the same world the
app's SIMULATED button makes. This program does the library part with the
app's own code (`simulator`):

* ``prepare`` builds the world: the database copies, the copy-on-write
  overlay of the real frames and stacks, the settings file and the safety
  checks. It also writes ``library_record.json``, which says how big the copied
  library is (targets, measured frames, database sizes, deep-star count), for
  the setup record. It runs OUTSIDE the network seal: the overlay is a FUSE
  mount, and the system lets only an ordinary process (not one inside the
  seal) make it. The sealed run then reaches the mount through a bind.
* ``teardown`` unmounts the overlay and deletes the copies. It refuses to
  report success while anything is still mounted.
* ``start`` runs the stand-in observatory (INDI simulators, stand-in PHD2,
  exposure driver) in a world that ``prepare`` already built, with the app's
  ``observatory start --existing-world``. Without that flag the command
  would prepare the world again, unmount the overlay and rewrite the
  settings.

Safety: the real library is only read. Every write lands in the overlay's
upper folder or in the database copies, all inside the throwaway folder.

Usage::

    .venv/bin/python build/tpm/simulation_library.py prepare <sandbox> \
        --indi-port N --phd2-port N
    .venv/bin/python build/tpm/simulation_library.py teardown <sandbox>
    .venv/bin/python build/tpm/simulation_library.py start --kind simulated \
        --root <sandbox> --indi-port N --phd2-port N --ports-file F ...
"""

import json
import sqlite3
import sys
import time
from pathlib import Path

from astrometricslib.drivers import real_folders
from simulator import SimulatedObservatory, SimulationKind, library_copy
from simulator import observatory as observatory_module
from wayfindinglib.exceptions import SimulationUnavailableError

#: The deep-star catalog's place inside a library folder.
DEEP_CATALOG_PATH = Path("catalogs") / "deep_star_catalog.db"
#: The record ``prepare`` writes into the throwaway folder.
RECORD_FILE_NAME = "library_record.json"
#: The file ``copy_deep_catalog.py`` writes; ``setup_fingerprint.py`` reads it.
DEEP_CATALOG_RECORD_NAME = "deep_catalog.json"


def count_rows(database: Path, query: str) -> int | None:
    """Count rows in a copy of a database.

    Parameters
    ----------
    database : `pathlib.Path`
        A database copy inside the throwaway folder (never a real file).
    query : `str`
        A fixed ``SELECT`` that returns one number.

    Returns
    -------
    count : `int` or `None`
        The number, or `None` if the file or the table is missing.
    """
    if not database.is_file():
        return None
    try:
        connection = sqlite3.connect(database)
        try:
            return int(connection.execute(query).fetchone()[0])
        finally:
            connection.close()
    except sqlite3.Error:
        return None


def describe_library(sandbox: Path, report: dict) -> dict:
    """Describe the size of the copied library.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder after ``prepare``.
    report : `dict`
        The library copy report (`library_copy.prepare_library_copy`).

    Returns
    -------
    record : `dict`
        ``library_mode``, ``copy_seconds``, ``database_bytes``,
        ``paths_rewritten``, ``targets``, ``measured_frames`` (rows of the
        quality measurements), ``stellar_objects``, ``deep_catalog_stars``
        and ``real_frames``. Values are `None` where a source is missing.
    """
    main_database = sandbox / "library" / "astrometrics.db"
    deep_database = sandbox / "library" / DEEP_CATALOG_PATH
    return {
        "library_mode": "simulation",
        "real_frames": report.get("real_frames"),
        "copy_seconds": report.get("seconds"),
        "database_bytes": report.get("bytes"),
        "paths_rewritten": report.get("paths_rewritten"),
        "targets": count_rows(main_database, "SELECT count(*) FROM targets"),
        "measured_frames": count_rows(main_database, "SELECT count(*) FROM quality_measurements"),
        "stellar_objects": count_rows(main_database, "SELECT count(*) FROM stellar_objects"),
        "deep_catalog_stars": count_rows(deep_database, "SELECT count(*) FROM deep_stars"),
    }


def copy_deep_catalog_safely(
    source: Path, destination: Path, attempts: int = 5, wait_seconds: float = 2.0
) -> int:
    """Copy the deep-star catalog with the SQLite backup call only.

    The download (`build_deep_star_catalog`) can write the real file for
    hours. The app's library copy takes a plain file copy of a database that
    no program has open, which can catch the file half written. So the
    harness replaces that copy with a backup-call copy: the real file is
    opened read only and is never copied as a file.

    Parameters
    ----------
    source : `pathlib.Path`
        The real catalog file (never written).
    destination : `pathlib.Path`
        The copy inside the throwaway folder; replaced.
    attempts : `int`, optional
        How many times to try (the read-only open can fail while the
        download holds a lock).
    wait_seconds : `float`, optional
        Seconds between tries.

    Returns
    -------
    stars : `int`
        The number of stars in the copy.

    Raises
    ------
    sqlite3.OperationalError
        If every attempt failed.
    """
    for suffix in ("", "-wal", "-shm"):
        destination.with_name(destination.name + suffix).unlink(missing_ok=True)
    last_error = "no attempt was made"
    for _ in range(attempts):
        origin = target = None
        try:
            origin = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
            target = sqlite3.connect(destination)
            origin.backup(target)
            return int(target.execute("SELECT COUNT(*) FROM deep_stars").fetchone()[0])
        except sqlite3.Error as error:
            last_error = str(error)
            time.sleep(wait_seconds)
        finally:
            for connection in (target, origin):
                if connection is not None:
                    connection.close()
    raise sqlite3.OperationalError(last_error)


def prepare(sandbox: Path, indi_port: int, phd2_port: int) -> int:
    """Build the simulated world with the copy of the real library.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder, which is the simulator's root.
    indi_port, phd2_port : `int`
        The ports the stand-ins will use.

    Returns
    -------
    status : `int`
        0 on success, 1 if the app's code refuses or cannot build the world
        (the reason is printed on standard error).
    """
    observatory = SimulatedObservatory(
        sandbox,
        SimulationKind.SIMULATED_SKY,
        indi_port,
        phd2_port,
        copy_library=True,
        real_library=real_folders(),
    )
    try:
        observatory.prepare()
    except SimulationUnavailableError as error:
        print(f"ERROR: the simulated library could not be prepared: {error}", file=sys.stderr)
        return 1
    report = observatory.copy_report
    real_catalog = real_folders().library / DEEP_CATALOG_PATH
    if real_catalog.is_file():
        try:
            copy_deep_catalog_safely(real_catalog, sandbox / "library" / DEEP_CATALOG_PATH)
        except sqlite3.Error as error:
            print(
                f"ERROR: the deep-star catalog could not be copied with the backup call: {error}",
                file=sys.stderr,
            )
            return 1
    record = describe_library(sandbox, report)
    (sandbox / RECORD_FILE_NAME).write_text(json.dumps(record, indent=2), encoding="utf-8")
    deep_stars = record["deep_catalog_stars"]
    if deep_stars is not None:
        deep_file = sandbox / "library" / DEEP_CATALOG_PATH
        (sandbox / DEEP_CATALOG_RECORD_NAME).write_text(
            json.dumps({
                "path": str(deep_file),
                "size_bytes": deep_file.stat().st_size,
                "star_count": deep_stars,
            }),
            encoding="utf-8",
        )
    megabytes = sum((record["database_bytes"] or {}).values()) / 1024**2
    print(
        f"Simulation library: copied {megabytes:.0f} MB of databases and mounted the overlay in "
        f"{record['copy_seconds']} s ({record['targets']} targets, {record['measured_frames']} measured "
        f"frames, {deep_stars} deep-catalog stars)."
    )
    return 0


def teardown(sandbox: Path) -> int:
    """Unmount the overlay and delete the copies.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder.

    Returns
    -------
    status : `int`
        0 when nothing is mounted any more, 1 otherwise.
    """
    clean = library_copy.teardown(sandbox)
    for mountpoint in library_copy.overlay_folders(sandbox).values():
        if library_copy.is_mounted(mountpoint):
            clean = False
    print("Overlay unmounted and copies removed." if clean else "ERROR: the overlay is still mounted.")
    return 0 if clean else 1


def start(arguments: list[str]) -> int:
    """Run the app's ``start`` command on the world ``prepare`` built.

    The command gets ``--existing-world``, so it only runs the safety checks
    and does not copy the library, unmount the overlay or write the settings
    again.

    Parameters
    ----------
    arguments : `list` [`str`]
        The arguments of the app's ``observatory start`` command.

    Returns
    -------
    status : `int`
        What the app's command returns.
    """
    sys.argv = ["observatory", "start", "--existing-world", *arguments]
    return observatory_module.main()


def main() -> int:
    """Run the command named on the command line.

    Returns
    -------
    status : `int`
        The command's exit status.
    """
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "start":
        return start(sys.argv[2:])
    if command == "teardown" and len(sys.argv) == 3:
        return teardown(Path(sys.argv[2]).resolve())
    if (
        command == "prepare"
        and len(sys.argv) == 7
        and sys.argv[3] == "--indi-port"
        and sys.argv[5] == "--phd2-port"
    ):
        return prepare(Path(sys.argv[2]).resolve(), int(sys.argv[4]), int(sys.argv[6]))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
