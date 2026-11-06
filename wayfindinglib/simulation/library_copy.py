"""Purpose: Give a simulated observatory a temporary copy of the real library.

Description: With a copy of the user's library the simulator looks like the
real app: the same targets, frames, stacks and previews. The real library is
read and never written. This module makes the copy cheap and safe:

- The databases (``astrometrics.db``, ``astrometrics_log.db``,
  ``wayfinding.db``, the deep star catalog) are copied with SQLite's online
  backup call. The source is opened read only, so this is safe while the
  real app runs. Absolute paths inside the copies that name the real frames
  or stacks folder are rewritten to the mount point below, so a write that
  the app plans for an existing row lands in the temporary library, not in
  the real one.
- The frames, stacks and previews are not copied. A ``fuse-overlayfs``
  overlay shows them: the real folders are the read-only lower layer, a
  folder inside the simulator's root is the upper layer that takes every
  write, and the mount point is the library's frames folder.
- The settings are a copy of the real settings in which only the library
  paths and the telescope, guiding, remote-transfer and online-solver parts
  are replaced by the simulator's.

`teardown` unmounts the overlay and deletes the upper layer and the database
copies, and only those, only inside the simulator root. The TPM harness's
real-snapshot mode uses the same functions.

Usage (for the harness)::

    python -m wayfindinglib.simulation.library_copy mount \
        --lower DIR [--lower DIR] --upper DIR --work DIR --mountpoint DIR
"""

import argparse
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from astrometricslib.drivers import real_folders
from wayfindinglib.exceptions import SimulationUnavailableError

logger = logging.getLogger(__name__)

#: The programs the overlay needs.
OVERLAY_PROGRAMS = ("fuse-overlayfs", "fusermount3")
#: Databases in the real image library folder, copied to the same relative
#: place in the simulator's library folder.
LIBRARY_DATABASES = ("astrometrics.db", "astrometrics_log.db", "catalogs/deep_star_catalog.db")
#: The one database in the real wayfinding library folder.
WAYFINDING_DATABASE = "wayfinding.db"
#: Small plain files the app reads from the library folder.
PLAIN_FILES = ("calibration_frames.json",)
#: Sections of the real settings that the simulator replaces with its own.
DROPPED_SECTIONS = (
    "Image Library",
    "Wayfinding Library",
    "Observatory.Telescope",
    "Observatory.RemoteTransfer",
    "Observatory.Guiding",
    "Processing.Astrometry.Online Solver",
)
#: Name of the report file in the simulator root.
REPORT_FILE_NAME = "library_copy.json"
#: Do not copy more than this many bytes of databases.
MAXIMUM_COPY_BYTES = 5 * 1024**3
#: Seconds to wait for an unmount before it is made lazy.
UNMOUNT_WAIT_SECONDS = 10.0


def overlay_folders(root: Path) -> dict[str, Path]:
    """Name the folders of the overlays inside a simulator root.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder.

    Returns
    -------
    folders : `dict` [`str`, `pathlib.Path`]
        ``upper`` and ``work`` (under ``<root>/overlay``) and ``mountpoint``
        (``<root>/library/frames``, the library's frames folder). When the
        stacks folder is not part of the frames folder it gets an overlay of
        its own: ``stacks_upper``, ``stacks_work`` and ``stacks_mountpoint``
        (``<root>/library/stacks_view``).
    """
    return {
        "upper": root / "overlay" / "upper",
        "work": root / "overlay" / "work",
        "mountpoint": root / "library" / "frames",
        "stacks_upper": root / "overlay" / "stacks_upper",
        "stacks_work": root / "overlay" / "stacks_work",
        "stacks_mountpoint": root / "library" / "stacks_view",
    }


def plan_mounts(frames: Path, stacks: Path, root: Path) -> dict:
    """Decide which real folders are overlaid, and where they appear.

    Two lower layers that overlap (a stacks folder inside the library, both
    with a ``lights`` folder) hide each other, so overlapping folders share
    one overlay, made of the outer folder alone.

    Parameters
    ----------
    frames, stacks : `pathlib.Path`
        The real frames and stacks folders.
    root : `pathlib.Path`
        The simulator's root folder.

    Returns
    -------
    plan : `dict`
        ``mounts`` (a list of ``lower``, ``upper``, ``work``, ``mountpoint``),
        and the simulator's ``frames_path`` and ``stacks_path``.
    """
    folders = overlay_folders(root)
    mountpoint = folders["mountpoint"]
    main = {
        "lower": frames,
        "upper": folders["upper"],
        "work": folders["work"],
        "mountpoint": mountpoint,
    }
    if stacks == frames:
        return {"mounts": [main], "frames_path": mountpoint, "stacks_path": mountpoint}
    if stacks.is_relative_to(frames):
        return {
            "mounts": [main],
            "frames_path": mountpoint,
            "stacks_path": mountpoint / stacks.relative_to(frames),
        }
    if frames.is_relative_to(stacks):
        main["lower"] = stacks
        return {
            "mounts": [main],
            "frames_path": mountpoint / frames.relative_to(stacks),
            "stacks_path": mountpoint,
        }
    second = {
        "lower": stacks,
        "upper": folders["stacks_upper"],
        "work": folders["stacks_work"],
        "mountpoint": folders["stacks_mountpoint"],
    }
    return {"mounts": [main, second], "frames_path": mountpoint, "stacks_path": folders["stacks_mountpoint"]}


def keep_sections(text: str) -> str:
    """Remove the replaced sections from the real settings text.

    Parameters
    ----------
    text : `str`
        The real settings file.

    Returns
    -------
    kept : `str`
        The same text without the sections in `DROPPED_SECTIONS`. Arrays that
        span lines stay with their section, because only a line that starts
        with ``[`` and a name begins a section.
    """
    blocks = re.split(r'(?m)^(?=\["?[A-Za-z])', text)
    kept = []
    for block in blocks:
        header = re.match(r'\["?(.*?)"?\]\s*$', block.split("\n", 1)[0])
        if header and header.group(1) in DROPPED_SECTIONS:
            continue
        kept.append(block)
    return "".join(kept)


def backup_database(source: Path, destination: Path) -> int:
    """Copy a database with the online backup call, writing nothing next to it.

    A SQLite connection to a database in write-ahead-log mode creates the
    ``-shm`` and ``-wal`` files beside it, even when it is read only. So when
    no program has the database open (no ``-shm`` file), the database and
    its ``-wal`` file are first copied with plain reads into the destination
    folder, and the backup runs on that copy. When a program has it open, the
    ``-shm`` file exists already, and the source is opened read only.

    Parameters
    ----------
    source : `pathlib.Path`
        The real database (never written).
    destination : `pathlib.Path`
        The copy, inside the simulator's folder.

    Returns
    -------
    size : `int`
        Size of the copy in bytes.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    origin_path = source
    if not source.with_name(source.name + "-shm").exists():
        origin_path = destination.with_name(destination.name + ".source")
        staged.append(origin_path)
        shutil.copyfile(source, origin_path)
        wal = source.with_name(source.name + "-wal")
        if wal.exists():
            staged.append(origin_path.with_name(origin_path.name + "-wal"))
            shutil.copyfile(wal, staged[-1])
    try:
        origin = sqlite3.connect(
            f"file:{origin_path}?mode=ro" if staged == [] else str(origin_path), uri=staged == []
        )
        target = sqlite3.connect(destination)
        try:
            origin.backup(target)
        finally:
            target.close()
            origin.close()
    finally:
        for path in staged:
            path.unlink(missing_ok=True)
        if staged:
            origin_path.with_name(origin_path.name + "-shm").unlink(missing_ok=True)
    return destination.stat().st_size


def rewrite_paths(database: Path, replacements: Iterable[tuple[str, str]]) -> int:
    """Replace folder prefixes in every text column of a database copy.

    A prefix matches only as a whole folder name: it must be followed by a
    slash, a quote, a space, a comma or the end of the value.

    Parameters
    ----------
    database : `pathlib.Path`
        The copy (never the real database).
    replacements : `~collections.abc.Iterable` [`tuple` [`str`, `str`]]
        ``(old, new)`` pairs. Longer `old` texts are applied first.

    Returns
    -------
    rows : `int`
        How many values were changed.
    """
    pairs = sorted(set(replacements), key=lambda pair: len(pair[0]), reverse=True)
    if not pairs:
        return 0
    patterns = [(re.compile(re.escape(old) + r"""(?=/|$|["'\s,])"""), new) for old, new in pairs]

    def rewrite(value: str) -> str:
        """Apply every replacement to one text value.

        Returns
        -------
        changed : `str`
            The value with the folder prefixes replaced.
        """
        for pattern, new in patterns:
            value = pattern.sub(lambda _match, new=new: new, value)
        return value

    contains = " OR ".join('instr("{column}", ?) > 0' for _ in pairs)
    changed = 0
    connection = sqlite3.connect(database)
    connection.create_function("_rewrite_paths", 1, rewrite, deterministic=True)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            try:
                columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
                for column in columns:
                    cursor = connection.execute(
                        f'UPDATE "{table}" SET "{column}" = _rewrite_paths("{column}") '
                        f"WHERE typeof(\"{column}\") = 'text' AND ({contains.format(column=column)})",
                        [old for old, _ in pairs],
                    )
                    changed += max(cursor.rowcount, 0)
            except sqlite3.Error:
                logger.warning("Could not rewrite paths in the table %s of %s", table, database.name)
        connection.commit()
    finally:
        connection.close()
    return changed


def copy_databases(
    root: Path, library: Path, wayfinding: Path, replacements: Sequence[tuple[str, str]]
) -> dict:
    """Copy the real databases into a simulator root.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder.
    library, wayfinding : `pathlib.Path`
        The real image library and wayfinding library folders (read only).
    replacements : `~collections.abc.Sequence` [`tuple` [`str`, `str`]]
        Folder prefixes to rewrite in the copies, see `rewrite_paths`.

    Returns
    -------
    report : `dict`
        ``bytes`` by database name, ``paths_rewritten`` and ``seconds``.

    Raises
    ------
    SimulationUnavailableError
        If the databases are over `MAXIMUM_COPY_BYTES`.
    """
    started = time.monotonic()
    jobs = [(library / name, root / "library" / name) for name in LIBRARY_DATABASES]
    jobs.append((wayfinding / WAYFINDING_DATABASE, root / "wayfinding" / WAYFINDING_DATABASE))
    present = [(source, destination) for source, destination in jobs if source.is_file()]
    if sum(source.stat().st_size for source, _ in present) > MAXIMUM_COPY_BYTES:
        raise SimulationUnavailableError("The real databases are too big to copy for the simulator.")
    sizes: dict[str, int] = {}
    rewritten = 0
    for source, destination in present:
        sizes[source.name] = backup_database(source, destination)
        rewritten += rewrite_paths(destination, replacements)
    for name in PLAIN_FILES:
        if (library / name).is_file():
            shutil.copyfile(library / name, root / "library" / name)
    return {"bytes": sizes, "paths_rewritten": rewritten, "seconds": round(time.monotonic() - started, 2)}


def is_mounted(path: Path) -> bool:
    """Say whether something is mounted exactly at a path.

    Parameters
    ----------
    path : `pathlib.Path`
        The folder to look for in ``/proc/mounts``.

    Returns
    -------
    mounted : `bool`
        `True` if the path is a mount point.
    """
    target = str(path.resolve())
    try:
        lines = Path("/proc/mounts").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(
        len(fields := line.split()) > 1 and fields[1].replace("\\040", " ") == target for line in lines
    )


def mount_overlay(lowers: Sequence[Path], upper: Path, work: Path, mountpoint: Path) -> None:
    """Mount a copy-on-write overlay of real folders.

    Reads show the lower folders; every write lands in `upper`. The first
    lower folder wins when two have the same file.

    Parameters
    ----------
    lowers : `~collections.abc.Sequence` [`pathlib.Path`]
        The real folders (read only).
    upper, work : `pathlib.Path`
        Folders for the writes, inside the simulator's root.
    mountpoint : `pathlib.Path`
        An empty folder to mount on.

    Raises
    ------
    SimulationUnavailableError
        If the program is missing, a path cannot be used, or the mount fails.
    """
    if shutil.which("fuse-overlayfs") is None:
        raise SimulationUnavailableError("The library copy needs fuse-overlayfs, which is not installed.")
    if any(":" in str(path) or "," in str(path) for path in lowers):
        raise SimulationUnavailableError(
            "A real library folder name has a colon or a comma; cannot overlay it."
        )
    for folder in (upper, work, mountpoint):
        folder.mkdir(parents=True, exist_ok=True)
    options = f"lowerdir={':'.join(str(path) for path in lowers)},upperdir={upper},workdir={work}"
    result = subprocess.run(
        ["fuse-overlayfs", "-o", options, str(mountpoint)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0 or not is_mounted(mountpoint):
        raise SimulationUnavailableError(
            f"fuse-overlayfs could not mount the library: {result.stderr.strip()}"
        )


def unmount_overlay(mountpoint: Path) -> bool:
    """Unmount the overlay, making the unmount lazy if the folder is busy.

    Parameters
    ----------
    mountpoint : `pathlib.Path`
        The folder the overlay is mounted on.

    Returns
    -------
    unmounted : `bool`
        `True` when nothing is mounted there any more.
    """
    if not is_mounted(mountpoint):
        return True
    deadline = time.monotonic() + UNMOUNT_WAIT_SECONDS
    while is_mounted(mountpoint) and time.monotonic() < deadline:
        subprocess.run(["fusermount3", "-u", str(mountpoint)], capture_output=True, check=False, timeout=30)
        if is_mounted(mountpoint):
            time.sleep(0.5)
    if is_mounted(mountpoint):
        # Something still uses the folder: detach it now, close it later.
        subprocess.run(["fusermount3", "-uz", str(mountpoint)], capture_output=True, check=False, timeout=30)
    return not is_mounted(mountpoint)


def teardown(root: Path) -> bool:
    """End a library copy: unmount the overlay and delete the temporary files.

    Deletes only inside `root`: the overlay's upper and work folders, the
    database copies and the copy report. Nothing is deleted while the
    overlay is still mounted, so a write through the mount can never reach
    the real library.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder.

    Returns
    -------
    clean : `bool`
        `True` when everything is unmounted and deleted.
    """
    root = root.resolve()
    folders = overlay_folders(root)
    for name in ("mountpoint", "stacks_mountpoint"):
        if not unmount_overlay(folders[name]):
            logger.error("The overlay at %s is still mounted; nothing was deleted.", folders[name])
            return False
    shutil.rmtree(folders["stacks_mountpoint"], ignore_errors=True)
    shutil.rmtree(root / "overlay", ignore_errors=True)
    for name in (*LIBRARY_DATABASES, *PLAIN_FILES):
        for suffix in ("", "-wal", "-shm"):
            (root / "library" / f"{name}{suffix}").unlink(missing_ok=True)
    for suffix in ("", "-wal", "-shm"):
        (root / "wayfinding" / f"{WAYFINDING_DATABASE}{suffix}").unlink(missing_ok=True)
    (root / REPORT_FILE_NAME).unlink(missing_ok=True)
    return True


def world_settings_text(real_text: str, library_and_telescope_text: str) -> str:
    """Join the simulator's settings and the kept part of the real settings.

    Parameters
    ----------
    real_text : `str`
        The real settings file.
    library_and_telescope_text : `str`
        The simulator's library and telescope sections.

    Returns
    -------
    text : `str`
        The simulator's sections first, then the real settings without the
        sections the simulator replaced.
    """
    return library_and_telescope_text.rstrip("\n") + "\n\n" + keep_sections(real_text)


def prepare_library_copy(
    root: Path, real_environment: dict[str, str] | None = None, real: Any = None
) -> dict:
    """Copy the databases and mount the overlay of the real library.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder.
    real_environment : `dict` [`str`, `str`], optional
        The environment that names the real folders. Default: the process
        environment.
    real : `object`, optional
        The real folders (from `astrometricslib.drivers.real_folders`).
        Overrides `real_environment`.

    Returns
    -------
    report : `dict`
        ``seconds`` (the whole copy), ``bytes``, ``paths_rewritten``,
        ``mounted`` (`True` if there was a real frames folder to overlay),
        and the real folders used. Also written to
        ``<root>/library_copy.json``.

    Raises
    ------
    SimulationUnavailableError
        If the overlay program is missing or the mount fails.
    """
    started = time.monotonic()
    real = real or real_folders(real_environment)
    if not teardown(root):
        raise SimulationUnavailableError("An earlier library copy is still mounted.")
    plan = plan_mounts(real.frames, real.stacks, root)
    for mount in plan["mounts"]:
        mount["mountpoint"].mkdir(parents=True, exist_ok=True)
    replacements = [
        (str(real.frames), str(plan["frames_path"])),
        (str(real.stacks), str(plan["stacks_path"])),
    ]
    if real.library != real.frames:
        replacements.append((str(real.library), str(root / "library")))
    report = copy_databases(root, real.library, real.wayfinding, replacements)
    mounted = []
    try:
        for mount in plan["mounts"]:
            if not mount["lower"].is_dir():
                continue
            mount_overlay([mount["lower"]], mount["upper"], mount["work"], mount["mountpoint"])
            mounted.append(mount)
            if (mount["lower"] / "lights").is_dir() and not (mount["mountpoint"] / "lights").is_dir():
                raise SimulationUnavailableError(
                    f"The overlay of {mount['lower']} does not show its lights folder."
                )
    except SimulationUnavailableError:
        teardown(root)
        raise
    report["mounted"] = bool(mounted)
    report["seconds"] = round(time.monotonic() - started, 2)
    report["real_frames"] = str(real.frames)
    report["real_stacks"] = str(real.stacks)
    report["frames_path"] = str(plan["frames_path"])
    report["stacks_path"] = str(plan["stacks_path"])
    (root / REPORT_FILE_NAME).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def read_report(root: Path) -> dict:
    """Read the report of the last library copy.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder.

    Returns
    -------
    report : `dict`
        The report, or an empty dict if there is none.
    """
    try:
        return json.loads((root / REPORT_FILE_NAME).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}


def main() -> int:
    """Run one command from the command line (used by the TPM harness).

    Returns
    -------
    status : `int`
        0 on success, 1 if the mount failed.
    """
    parser = argparse.ArgumentParser(description="Overlay a real library for a throwaway run.")
    commands = parser.add_subparsers(dest="command", required=True)
    mount = commands.add_parser("mount")
    mount.add_argument("--lower", type=Path, action="append", required=True)
    mount.add_argument("--upper", type=Path, required=True)
    mount.add_argument("--work", type=Path, required=True)
    mount.add_argument("--mountpoint", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        mount_overlay(arguments.lower, arguments.upper, arguments.work, arguments.mountpoint)
    except SimulationUnavailableError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
