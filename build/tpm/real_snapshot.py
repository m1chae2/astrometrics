"""Purpose: Build a throwaway copy of the user's real data for the TPM mode.

Description: ``run_tpm.sh --real-snapshot`` measures the app on the user's own
data, to see how it scales, without ever touching that data. This script does
the copying part:

* ``prepare`` makes consistent copies of the databases with SQLite's online
  backup call (safe while the real backend runs), copies the catalog caches,
  and writes the throwaway settings file. The settings file reuses the user's
  equipment sections, and drops every section that names a host, a key or a
  folder, replacing them with the offline settings from
  ``write_sandbox_config.py``.
* ``guard`` refuses the run if a database copy or the settings still hold a
  path outside the throwaway folder and the overlay (it uses the same check as
  ``build/agent_tools/realrun.sh``).
* ``fingerprint`` and ``verify`` prove the real library was not touched. The
  first walks the real image library folder and the real logs folder and
  writes the size and change time of every file (and of the three real
  databases) to a file. The second walks it again after the run and lists
  every file that was added, removed or changed. The databases are listed
  apart, because the user's own app may change them while the run goes on.
* ``get`` prints one value from ``real_snapshot.json`` (for the shell script).
* ``overlay-report`` lists what the backend wrote into the overlay's upper
  folder (the only place a write can land) and its size.

Safety: every real file is opened read only (``file:...?mode=ro``) or copied
with a plain read. All output goes inside the throwaway folder. The real
library (hundreds of GB) is not copied here: ``run_tpm.sh`` mounts a
copy-on-write overlay on it.

Usage::

    .venv/bin/python build/tpm/real_snapshot.py prepare <sandbox> \
        [--mountpoint DIR]
    .venv/bin/python build/tpm/real_snapshot.py guard <sandbox>
    .venv/bin/python build/tpm/real_snapshot.py fingerprint <sandbox> <out>
    .venv/bin/python build/tpm/real_snapshot.py verify <before> <after> <out>
    .venv/bin/python build/tpm/real_snapshot.py get <sandbox> <key>
    .venv/bin/python build/tpm/real_snapshot.py overlay-report \
        <upper folder> <out file>
"""

import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from write_sandbox_config import OFFLINE_TELESCOPE_SETTINGS

from simulator import library_copy
from simulator.real_folders import real_folders
from simulator.settings import launch_section_text

#: Do not copy more than this many bytes of databases and caches.
MAXIMUM_COPY_BYTES = 5 * 1024**3


def toml_string(value: object) -> str:
    """Write a value as a quoted TOML string, with escapes where needed.

    Parameters
    ----------
    value : `object`
        A path or other value; it is converted with `str`.

    Returns
    -------
    text : `str`
        The value in double quotes. Quotes, backslashes and control
        characters are escaped, so the value cannot end the string early.
    """
    return json.dumps(str(value), ensure_ascii=False)


def repoint_outside_paths(sandbox: Path) -> int:
    """Move stored paths that are still outside the throwaway folder inside it.

    Uses the shared helper of ``build/agent_tools/copy_path_guard.py``, on the
    database copies only. Paths under the settings' frames folder (the
    overlay) are left alone.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder, with the settings file and database copies.

    Returns
    -------
    count : `int`
        How many stored paths were repointed to ``<sandbox>/outside/...``.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent_tools"))
    import copy_path_guard

    return copy_path_guard.repoint(sandbox / "astrometrics.config.toml", str(sandbox), mirror_outside=True)


def prepare(sandbox: Path, mountpoint: Path | None = None) -> int:
    """Make the copies and the settings file.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder.
    mountpoint : `pathlib.Path`, optional
        Where the overlay of the real library will be mounted. When given
        (the processing scenario), every path in the database copies and in
        ``calibration_frames.json`` that names the real library is rewritten
        to the mount point, and the settings name the mount point as the
        frames and stacks folders. The backend then never sees a real path
        (the bwrap lock cannot be used with Flatpak's Siril; see
        ``guard``).
        Without it the paths stay real and ``run_tpm.sh`` binds the overlay
        over the real path (``bwrap``).

    Returns
    -------
    status : `int`
        0 on success, 1 if a source is missing or the copy is too big.
    """
    real = real_folders()
    text = real.config_file.read_text(encoding="utf-8")
    frames_path = str(real.frames)
    stacks_path = str(real.stacks)
    cache_root = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    catalog_source = Path(cache_root) / "astrometrics" / "catalogs"

    sources = [real.library / name for name in library_copy.LIBRARY_DATABASES[:2]]
    sources.append(real.wayfinding / library_copy.WAYFINDING_DATABASE)
    missing = [str(path) for path in sources if not path.exists()]
    if missing:
        print(f"ERROR: missing real files: {missing}", file=sys.stderr)
        return 1
    total = sum(path.stat().st_size for path in sources)
    total += sum(item.stat().st_size for item in catalog_source.rglob("*") if item.is_file())
    limit_gb = MAXIMUM_COPY_BYTES / 1024**3
    print(
        f"Real snapshot: copying {total / 1024**2:.0f} MB of databases and "
        f"catalogs (limit {limit_gb:.0f} GB)."
    )
    if total > MAXIMUM_COPY_BYTES:
        print("ERROR: the copy is over the limit; refusing.", file=sys.stderr)
        return 1

    # The one shared copy code. The paths inside the copies stay as they are:
    # `run_tpm.sh` binds the overlay over the real library path.
    replacements = [(frames_path, str(mountpoint))] if mountpoint is not None else []
    report = library_copy.copy_databases(sandbox, real.library, real.wayfinding, replacements)
    if mountpoint is not None:
        plain = sandbox / "library" / "calibration_frames.json"
        if plain.is_file():
            plain.write_text(
                plain.read_text(encoding="utf-8").replace(frames_path, str(mountpoint)), encoding="utf-8"
            )
        try:
            stacks_path = str(mountpoint / Path(stacks_path).relative_to(frames_path))
        except ValueError:
            print(
                "ERROR: the stacks folder is outside the frames folder; the overlay would miss it.",
                file=sys.stderr,
            )
            return 1
        frames_path = str(mountpoint)
    sizes = report["bytes"]
    for name, size in sizes.items():
        print(f"  {name} ({size / 1024**2:.0f} MB)")
    destination = sandbox / "xdg_cache" / "astrometrics" / "catalogs"
    shutil.copytree(catalog_source, destination, dirs_exist_ok=True)
    print(f"  {catalog_source} -> {destination}")

    (sandbox / "wayfinding").mkdir(parents=True, exist_ok=True)
    library = sandbox / "library"
    library.mkdir(parents=True, exist_ok=True)
    config_path = sandbox / "astrometrics.config.toml"
    # The [Launch] section makes the run offline: every outgoing client in
    # the library refuses (SIMBAD, online solvers, downloads), so the
    # unsealed processing scenario cannot reach the internet. Paths are
    # written as TOML strings with `toml_string`, never pasted in bare.
    config_path.write_text(
        launch_section_text("TPM SNAPSHOT", live_hardware=False, disposable_library=True, offline=True)
        + f'["Image Library"]\npath = {toml_string(library)}\nframes_path = {toml_string(frames_path)}\n'
        f"stacks_path = {toml_string(stacks_path)}\n\n"
        f'["Wayfinding Library"]\npath = {toml_string(sandbox / "wayfinding")}\n'
        + OFFLINE_TELESCOPE_SETTINGS
        + "\n"
        + library_copy.keep_sections(text, library_copy.DROPPED_SECTIONS + library_copy.TELESCOPE_SECTIONS),
        encoding="utf-8",
    )
    repointed = 0
    if mountpoint is not None:
        # Stale paths (for example an old stacks folder that no longer exists)
        # are not under the real library, so the rewrite above missed them.
        # Move each to a mirror inside the throwaway folder; the guard still
        # refuses anything this misses.
        repointed = repoint_outside_paths(sandbox)
        print(f"  repointed {repointed} stale stored path(s) into {sandbox / 'outside'}")
    (sandbox / "real_snapshot.json").write_text(
        json.dumps(
            {
                "real_library": str(real.frames),
                "real_stacks": str(real.stacks),
                "real_database_folder": str(real.library),
                "real_wayfinding_folder": str(real.wayfinding),
                "real_logs_folder": str(real.logs),
                "database_bytes": sizes,
                "paths_rewritten": report["paths_rewritten"],
                "paths_repointed_outside": repointed,
            },
            indent=2,
        )
    )
    return 0


def overlay_report(upper: Path, out: Path) -> int:
    """List everything written into the overlay's upper folder.

    Parameters
    ----------
    upper : `pathlib.Path`
        The overlay's upper folder.
    out : `pathlib.Path`
        The JSON file to write: ``files`` (path and size), ``total_bytes``.

    Returns
    -------
    status : `int`
        Always 0.
    """
    files = []
    for item in sorted(upper.rglob("*")):
        if item.is_file():
            files.append({"path": str(item.relative_to(upper)), "bytes": item.stat().st_size})
        elif not item.is_dir():
            files.append({
                "path": str(item.relative_to(upper)),
                "bytes": 0,
                "note": "deleted or replaced in the overlay",
            })
    total = sum(entry["bytes"] for entry in files)
    out.write_text(json.dumps({"total_bytes": total, "files": files}, indent=2))
    print(f"Overlay writes: {len(files)} item(s), {total / 1024**2:.1f} MB")
    for entry in files[:25]:
        print(f"  {entry['bytes']:>12}  {entry['path']}")
    if len(files) > 25:
        print(f"  ... and {len(files) - 25} more (see {out})")
    return 0


def guard(sandbox: Path) -> int:
    """Refuse a run whose copies point at the real library.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder, with the settings file and the database copies
        that ``prepare --mountpoint`` made. The settings' frames folder is the
        overlay mountpoint, which the check allows.

    Returns
    -------
    status : `int`
        0 if every absolute path stored in the copies is inside the throwaway
        folder or the overlay mountpoint, 1 otherwise (the paths are printed).
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent_tools"))
    import copy_path_guard

    config = sandbox / "astrometrics.config.toml"
    offenders = copy_path_guard.check(config, str(sandbox))
    # The settings: the frames folder is the overlay, never the real folder.
    frames = copy_path_guard.read_config_value(config, "frames_path")
    stacks = copy_path_guard.read_config_value(config, "stacks_path")
    real_library = json.loads((sandbox / "real_snapshot.json").read_text())["real_library"]
    if not frames or copy_path_guard.is_under(frames, real_library):
        offenders.append((config.name, "settings", "frames_path", "frames_path", frames))
    if not copy_path_guard.is_under(stacks, frames):
        offenders.append((config.name, "settings", "stacks_path", "stacks_path", stacks))
    for database, table, label, trail, path in offenders[:50]:
        print(f"  OUTSIDE: {database} {table} {label} {trail}: {path}", file=sys.stderr)
    print(f"Path guard: {len(offenders)} stored path(s) outside the throwaway folder and the overlay.")
    return 1 if offenders else 0


def walk_folder(folder: Path) -> dict[str, list[int]]:
    """Record the size and change time of every file under a folder.

    Only directory entries and file status are read.

    Parameters
    ----------
    folder : `pathlib.Path`
        The folder to walk. A missing folder gives an empty record.

    Returns
    -------
    files : `dict` [`str`, `list` [`int`]]
        Each file's path to ``[size, change time in nanoseconds]``.
    """
    files = {}
    for directory, _directories, names in os.walk(folder):
        for name in names:
            path = os.path.join(directory, name)
            try:
                status = os.stat(path, follow_symlinks=False)
            except OSError:
                continue
            files[path] = [status.st_size, status.st_mtime_ns]
    return files


def fingerprint(sandbox: Path, out: Path) -> int:
    """Record the size and change time of the real library's files.

    The walk reads directory entries and file status only. It covers the
    real frames folder (which holds the stacks) and the three real databases.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder (``real_snapshot.json`` names the real folders).
    out : `pathlib.Path`
        The JSON file to write: ``files`` (path to ``[size, change time in
        nanoseconds]``) and ``databases`` (the same for the databases).

    Returns
    -------
    status : `int`
        0.
    """
    # Use the real folders that prepare recorded, when it has run. By the
    # time a processing run takes its fingerprints, the settings already name
    # the copies, so asking the settings again would fingerprint the copies.
    # The simulation run fingerprints first, while the settings are still the
    # real ones, so it reads them from the settings.
    record = sandbox / "real_snapshot.json"
    if record.exists():
        recorded = json.loads(record.read_text())
        real_frames = Path(recorded["real_library"])
        real_database_folder = Path(recorded["real_database_folder"])
        real_wayfinding_folder = Path(recorded["real_wayfinding_folder"])
        real_logs_folder = Path(recorded["real_logs_folder"]) if recorded.get("real_logs_folder") else None
    else:
        real = real_folders()
        real_frames, real_database_folder, real_wayfinding_folder = real.frames, real.library, real.wayfinding
        real_logs_folder = real.logs
    files = walk_folder(real_frames)
    logs = walk_folder(real_logs_folder) if real_logs_folder is not None else {}
    databases = {}
    candidates = [real_database_folder / name for name in library_copy.LIBRARY_DATABASES[:2]]
    candidates.append(real_wayfinding_folder / library_copy.WAYFINDING_DATABASE)
    for path in candidates:
        for suffix in ("", "-wal", "-shm"):
            try:
                status = os.stat(f"{path}{suffix}")
            except OSError:
                continue
            databases[f"{path}{suffix}"] = [status.st_size, status.st_mtime_ns]
    out.write_text(json.dumps({"files": files, "databases": databases, "logs": logs}))
    print(
        f"Real library fingerprint: {len(files)} files, {len(databases)} database files, "
        f"{len(logs)} log files."
    )
    return 0


def verify(before: Path, after: Path, out: Path) -> int:
    """Compare two fingerprints and report what changed in the real library.

    Parameters
    ----------
    before, after : `pathlib.Path`
        Fingerprint files from `fingerprint`.
    out : `pathlib.Path`
        The JSON report: ``library_changes`` (``added``, ``removed``,
        ``changed``), ``log_changes`` (the same for the real logs folder),
        ``database_changes`` and ``ok``.

    Returns
    -------
    status : `int`
        0 if nothing in the real library changed, 1 otherwise. Any changed
        file or database file fails the check. The one exception is a
        ``-shm`` file (the user's own app touches it just by being open):
        it is listed as information only.
    """
    first, second = json.loads(before.read_text()), json.loads(after.read_text())

    def compare(old: dict, new: dict) -> dict[str, list[str]]:
        """List what differs between two path-to-status maps.

        Returns
        -------
        changes : `dict` [`str`, `list` [`str`]]
            ``added``, ``removed`` and ``changed`` paths.
        """
        return {
            "added": sorted(set(new) - set(old)),
            "removed": sorted(set(old) - set(new)),
            "changed": sorted(path for path in set(old) & set(new) if old[path] != new[path]),
        }

    library = compare(first["files"], second["files"])
    databases = compare(first["databases"], second["databases"])
    # The real logs folder. A fingerprint from before this check has no "logs".
    logs = compare(first.get("logs", {}), second.get("logs", {}))
    # Database changes other than the shared-memory file are real changes.
    database_failures = {
        kind: [path for path in paths if not path.endswith("-shm")] for kind, paths in databases.items()
    }
    ok = not any(library.values()) and not any(database_failures.values()) and not any(logs.values())
    out.write_text(
        json.dumps(
            {
                "ok": ok,
                "library_changes": library,
                "log_changes": logs,
                "database_changes": databases,
                "database_failures": database_failures,
            },
            indent=2,
        )
    )
    differences = (
        sum(len(paths) for paths in library.values())
        + sum(len(paths) for paths in database_failures.values())
        + sum(len(paths) for paths in logs.values())
    )
    print(f"Real library check: {len(second['files'])} files compared, {differences} difference(s).")
    for kind, paths in library.items():
        for path in paths[:20]:
            print(f"  REAL LIBRARY {kind}: {path}")
    for kind, paths in logs.items():
        for path in paths[:20]:
            print(f"  REAL LOGS {kind}: {path}")
    for kind, paths in database_failures.items():
        for path in paths:
            print(f"  REAL DATABASE {kind}: {path}")
    informational = sorted({path for paths in databases.values() for path in paths if path.endswith("-shm")})
    if informational:
        print(
            "  Real database shared-memory file changed (information only; "
            "the user's own app touches it): " + ", ".join(informational)
        )
    return 0 if ok else 1


def main() -> int:
    """Run the subcommand named on the command line.

    Returns
    -------
    status : `int`
        The command's exit status.
    """
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "prepare" and len(sys.argv) == 3:
        return prepare(Path(sys.argv[2]).resolve())
    if command == "prepare" and len(sys.argv) == 5 and sys.argv[3] == "--mountpoint":
        return prepare(Path(sys.argv[2]).resolve(), Path(sys.argv[4]))
    if command == "guard" and len(sys.argv) == 3:
        return guard(Path(sys.argv[2]).resolve())
    if command == "fingerprint" and len(sys.argv) == 4:
        return fingerprint(Path(sys.argv[2]), Path(sys.argv[3]))
    if command == "verify" and len(sys.argv) == 5:
        return verify(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
    if command == "get" and len(sys.argv) == 4:
        print(json.loads((Path(sys.argv[2]) / "real_snapshot.json").read_text())[sys.argv[3]])
        return 0
    if command == "overlay-report" and len(sys.argv) == 4:
        return overlay_report(Path(sys.argv[2]), Path(sys.argv[3]))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
