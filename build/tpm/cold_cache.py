"""Purpose: Drop the Linux page cache for the files a TPM run reads.

Description: A "cold" start is a start where the files the app needs are not
yet in the computer's memory and must come from the disk. The truest cold
start happens right after a reboot. This script makes a close copy of it
without root rights and without any system setting: for each file the app
reads, it asks the kernel to forget the cached copy
(``os.posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED)``). The kernel honors this
for clean pages of any file the user can open. Pages that are in use by a
running program (a mapped library) and files on a memory-backed file system
(tmpfs) are not dropped; the report shows this, because it measures how much
of a sample of the files is still in memory before and after.

Three commands::

    cold_cache.py drop --sandbox S --repo R --out report.json \
        [--real-stacks D] [--browser P] [--after-reboot]
    cold_cache.py boot-age
    cold_cache.py filesystem PATH

``drop`` selects the files (see `file_groups`), measures the resident share of
a sample, drops the cache, measures the sample again and writes the report.
It never writes to any file it reads. Files under the throwaway folder are
flushed first (``fsync``), because pages that are not yet on disk cannot be
dropped; the user's real files are only opened for reading.

``boot-age`` prints the seconds since boot (``/proc/uptime``). ``filesystem``
prints the type of the file system that holds a path.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import time
from collections.abc import Callable, Iterable
from pathlib import Path

#: Files per group that the residency check samples (a mix of the largest and
#: evenly spaced files).
SAMPLE_FILES_PER_GROUP = 40
#: The file systems where the page cache is the storage, so a drop does
#: nothing.
MEMORY_FILE_SYSTEMS = ("tmpfs", "ramfs", "overlay")
#: Preview and stack files of the real library are picked by these suffixes
#: (raw frames are left out).
REAL_LIBRARY_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
#: The most files the real-library walk visits, so a huge library cannot make
#: it slow.
REAL_LIBRARY_VISIT_LIMIT = 200_000
#: The most real-library files and bytes that are dropped.
REAL_LIBRARY_FILE_LIMIT = 20_000
REAL_LIBRARY_BYTE_LIMIT = 4 * 1024**3
#: Folders of the throwaway folder that must never be walked (the overlay mount
#: shows the real library).
SKIPPED_SANDBOX_FOLDERS = ("overlay",)


def boot_age_seconds() -> float | None:
    """Read how long the computer has been running.

    Returns
    -------
    seconds : `float` or `None`
        Seconds since boot from ``/proc/uptime``, or `None` if unreadable.
    """
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except OSError, ValueError, IndexError:
        return None


def filesystem_type(path: Path) -> str | None:
    """Find the type of the file system that holds a path.

    Parameters
    ----------
    path : `pathlib.Path`
        Any existing path.

    Returns
    -------
    kind : `str` or `None`
        For example ``ext4`` or ``tmpfs``: the type of the mount with the
        longest mount point that is a parent of the path, from
        ``/proc/mounts``.
    """
    try:
        lines = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        return None
    target = str(path.resolve())
    best_point, best_kind = "", None
    for line in lines:
        fields = line.split()
        if len(fields) < 3:
            continue
        point = fields[1].replace("\\040", " ")
        inside = target == point or target.startswith(point.rstrip("/") + "/")
        if inside and len(point) >= len(best_point):
            best_point, best_kind = point, fields[2]
    return best_kind


def walk_files(root: Path, suffixes: tuple[str, ...] | None = None, skip: Iterable[Path] = ()) -> list[Path]:
    """List the regular files below a folder, without following links.

    Parameters
    ----------
    root : `pathlib.Path`
        The folder (or a single file).
    suffixes : `tuple` [`str`], optional
        Keep only files whose lower-case name ends with one of these.
    skip : iterable of `pathlib.Path`, optional
        Folders to leave out.

    Returns
    -------
    files : `list` [`pathlib.Path`]
        The files, sorted. Empty if the folder does not exist.
    """
    if root.is_file() and not root.is_symlink():
        return [root]
    skipped = {str(item) for item in skip}
    found = []
    for folder, names, files in os.walk(root, followlinks=False):
        names[:] = [name for name in names if os.path.join(folder, name) not in skipped]
        for name in files:
            if suffixes is None or name.lower().endswith(suffixes):
                path = Path(folder) / name
                if path.is_file() and not path.is_symlink():
                    found.append(path)
    return sorted(found)


def real_library_files(stacks_folder: Path) -> list[Path]:
    """Pick the preview and stack files of the real library a run may read.

    The real library can hold hundreds of gigabytes, so this does not take
    everything: only picture files (previews) and FITS files with ``stack`` in
    the name, and it stops at fixed visit, file and byte limits.

    Parameters
    ----------
    stacks_folder : `pathlib.Path`
        The real folder with the stacks (the overlay's lower folder).

    Returns
    -------
    files : `list` [`pathlib.Path`]
        The picked files, at most `REAL_LIBRARY_FILE_LIMIT` and
        `REAL_LIBRARY_BYTE_LIMIT` bytes in all.
    """
    picked, total, visited = [], 0, 0
    for folder, _names, files in os.walk(stacks_folder, followlinks=False):
        for name in files:
            visited += 1
            lower = name.lower()
            wanted = lower.endswith(REAL_LIBRARY_SUFFIXES) or (
                lower.endswith((".fits", ".fit")) and "stack" in lower
            )
            if not wanted:
                continue
            path = Path(folder) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if len(picked) >= REAL_LIBRARY_FILE_LIMIT or total + size > REAL_LIBRARY_BYTE_LIMIT:
                return picked
            picked.append(path)
            total += size
        if visited >= REAL_LIBRARY_VISIT_LIMIT:
            break
    return picked


def file_groups(
    sandbox: Path,
    repo: Path,
    real_stacks: Path | None = None,
    browser: Path | None = None,
) -> dict[str, list[Path]]:
    """Select the files the app reads, in named groups.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder of the run.
    repo : `pathlib.Path`
        The repository root.
    real_stacks : `pathlib.Path`, optional
        With a real snapshot: the real stacks folder (the overlay's lower
        folder). Only previews and stack files are taken
        (`real_library_files`).
    browser : `pathlib.Path`, optional
        The browser program. A file in a shared folder such as ``/usr/bin``
        is taken alone; a program in its own folder brings the whole folder.

    Returns
    -------
    groups : `dict` [`str`, `list` [`pathlib.Path`]]
        ``sandbox_databases`` (the databases, with their ``-wal`` and ``-shm``
        files), ``sandbox_library`` (the other library files),
        ``catalog_caches``
        (the star-catalog caches), ``ui_build`` (the built UI that the harness
        serves), ``electron`` (Electron's program and resources),
        ``python_site_packages`` (every installed package of the virtual
        environment), ``python_stdlib`` (the standard library),
        ``repo_packages`` (the repository's own Python packages),
        ``browser`` and ``real_library`` where they apply. Groups with no
        files are left out. A file appears in one group only.
    """
    skip = [sandbox / folder for folder in SKIPPED_SANDBOX_FOLDERS]
    sandbox_files = walk_files(sandbox, skip=skip)
    catalog_root = sandbox / "xdg_cache" / "astrometrics" / "catalogs"
    build_root = sandbox / "dist"
    database_pattern = re.compile(r"\.(db|sqlite3?)(-wal|-shm)?$")
    groups: dict[str, list[Path]] = {
        "sandbox_databases": [],
        "sandbox_library": [],
        "catalog_caches": [],
        "ui_build": [],
    }
    for path in sandbox_files:
        if catalog_root in path.parents:
            groups["catalog_caches"].append(path)
        elif build_root in path.parents:
            groups["ui_build"].append(path)
        elif database_pattern.search(path.name):
            groups["sandbox_databases"].append(path)
        elif (sandbox / "library") in path.parents:
            groups["sandbox_library"].append(path)
    groups["electron"] = walk_files(repo / "node_modules" / "electron" / "dist")
    site_packages = sorted((repo / ".venv" / "lib").glob("python*/site-packages"))
    groups["python_site_packages"] = [file for folder in site_packages for file in walk_files(folder)]
    standard_library = Path(sysconfig.get_path("stdlib"))
    base = Path(sys.base_prefix) / "lib" / standard_library.name
    groups["python_stdlib"] = walk_files(base if base.exists() else standard_library)
    groups["repo_packages"] = [
        file
        for package in ("astrometricslib", "wayfindinglib", "backend")
        for file in walk_files(repo / package)
    ]
    if browser is not None:
        real = browser.resolve()
        shared = {"/usr/bin", "/bin", "/usr/local/bin", "/usr/sbin", "/sbin"}
        groups["browser"] = [real] if str(real.parent) in shared else walk_files(real.parent)
    if real_stacks is not None and real_stacks.is_dir():
        groups["real_library"] = real_library_files(real_stacks)
    seen: set[tuple[int, int]] = set()
    for name, files in groups.items():
        unique = []
        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            key = (stat.st_dev, stat.st_ino)
            if key not in seen:
                seen.add(key)
                unique.append(path)
        groups[name] = unique
    return {name: files for name, files in groups.items() if files}


def drop_file(path: Path, flush: bool) -> int:
    """Ask the kernel to forget the cached pages of one file.

    Parameters
    ----------
    path : `pathlib.Path`
        The file. It is opened for reading only.
    flush : `bool`
        Flush the file's unwritten pages to disk first (``fsync``). Only the
        throwaway folder's files are flushed; flushing a read-only descriptor
        writes nothing new, it only waits for pages that are already queued.

    Returns
    -------
    size : `int`
        The file size in bytes.

    """
    descriptor = os.open(path, os.O_RDONLY)
    try:
        size = os.fstat(descriptor).st_size
        if flush:
            try:
                os.fsync(descriptor)
            except OSError:
                pass
        os.posix_fadvise(descriptor, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(descriptor)
    return size


def drop_files(paths: Iterable[Path], flush_below: Path | None) -> dict[str, float]:
    """Drop the cached pages of many files.

    Parameters
    ----------
    paths : iterable of `pathlib.Path`
        The files.
    flush_below : `pathlib.Path` or `None`
        Files under this folder are flushed before the drop.

    Returns
    -------
    counts : `dict`
        ``files`` and ``bytes`` dropped, ``errors`` (files that could not be
        opened) and ``seconds`` taken.
    """
    started = time.monotonic()
    files = size = errors = 0
    for path in paths:
        try:
            size += drop_file(path, flush_below is not None and flush_below in path.parents)
            files += 1
        except OSError:
            errors += 1
    return {"files": files, "bytes": size, "errors": errors, "seconds": round(time.monotonic() - started, 3)}


def sample_paths(paths: list[Path], count: int = SAMPLE_FILES_PER_GROUP) -> list[Path]:
    """Pick the files whose residency is checked.

    Parameters
    ----------
    paths : `list` [`pathlib.Path`]
        All files of a group.
    count : `int`, optional
        How many to pick.

    Returns
    -------
    sample : `list` [`pathlib.Path`]
        Half the count are the largest files (they matter most for time); the
        rest are evenly spaced through the sorted list. Always the same pick
        for the same input.
    """
    if len(paths) <= count:
        return list(paths)

    def size_of(path: Path) -> int:
        """Give a file's size, 0 if it cannot be read.

        Returns
        -------
        size : `int`
            Bytes.
        """
        try:
            return path.stat().st_size
        except OSError:
            return 0

    largest = sorted(paths, key=size_of, reverse=True)[: count // 2]
    step = max(1, len(paths) // (count - len(largest)))
    spaced = [path for path in paths[::step] if path not in largest][: count - len(largest)]
    return largest + spaced


def parse_fincore(text: str) -> tuple[int, int]:
    """Read the output of ``fincore --json --bytes``.

    Parameters
    ----------
    text : `str`
        The JSON text.

    Returns
    -------
    resident, total : `tuple` [`int`, `int`]
        Bytes in memory and file bytes, summed over the files listed. Files
        that fincore may not look at (the kernel refuses for files that are
        not the user's, such as the system's Python files) are not listed and
        so do not count.
    """
    rows = json.loads(text).get("fincore", [])
    # ``res`` counts whole pages, so it can exceed the size of a file that ends
    # inside a page.
    resident = sum(min(int(row.get("res", 0)), int(row.get("size", 0))) for row in rows)
    return resident, sum(int(row.get("size", 0)) for row in rows)


def parse_vmtouch(text: str) -> tuple[int, int]:
    """Read the output of ``vmtouch`` for a set of files.

    Parameters
    ----------
    text : `str`
        The program's text. The line used is
        ``Resident Pages: 12/34  48K/136K  35.3%``.

    Returns
    -------
    resident, total : `tuple` [`int`, `int`]
        Pages in memory and pages in all, times 4096 (the page size).
    """
    found = re.search(r"Resident Pages:\s*(\d+)/(\d+)", text)
    if not found:
        return 0, 0
    return int(found.group(1)) * 4096, int(found.group(2)) * 4096


def residency_tool() -> str | None:
    """Find the program that can tell how much of a file is in memory.

    Returns
    -------
    name : `str` or `None`
        ``vmtouch`` if installed, else ``fincore`` (from util-linux), else
        `None`.
    """
    for name in ("vmtouch", "fincore"):
        if shutil.which(name):
            return name
    return None


def resident_share(
    paths: list[Path], tool: str | None, run: Callable[..., subprocess.CompletedProcess] = subprocess.run
) -> dict:
    """Measure how much of some files is in memory now.

    Parameters
    ----------
    paths : `list` [`pathlib.Path`]
        The files to look at.
    tool : `str` or `None`
        ``vmtouch``, ``fincore`` or `None` (nothing is measured).
    run : callable, optional
        The function that runs a program, ``subprocess.run`` by default (a
        test passes a stand-in).

    Returns
    -------
    share : `dict`
        ``resident_bytes``, ``total_bytes``, ``percent`` (`None` when nothing
        could be measured) and ``files``.
    """
    empty = {"resident_bytes": None, "total_bytes": None, "percent": None, "files": len(paths)}
    if tool is None or not paths:
        return empty
    arguments = ["fincore", "--json", "--bytes"] if tool == "fincore" else ["vmtouch"]
    resident = total = 0
    for start in range(0, len(paths), 200):
        batch = [str(path) for path in paths[start : start + 200]]
        result = run([*arguments, *batch], capture_output=True, text=True, check=False)
        if not result.stdout:
            continue
        try:
            part = parse_fincore(result.stdout) if tool == "fincore" else parse_vmtouch(result.stdout)
        except ValueError, KeyError:
            continue
        resident += part[0]
        total += part[1]
    if total == 0:
        return empty
    return {
        "resident_bytes": resident,
        "total_bytes": total,
        "percent": round(100.0 * resident / total, 1),
        "files": len(paths),
    }


def drop_report(
    groups: dict[str, list[Path]],
    sandbox: Path,
    tool: str | None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict:
    """Drop the cache of every group and measure the effect.

    Parameters
    ----------
    groups : `dict` [`str`, `list` [`pathlib.Path`]]
        The result of `file_groups`.
    sandbox : `pathlib.Path`
        The throwaway folder (its files are flushed before the drop).
    tool : `str` or `None`
        The residency program (see `residency_tool`).
    run : callable, optional
        The function that runs a program.

    Returns
    -------
    report : `dict`
        ``groups`` (per group: ``files``, ``bytes``, ``errors``, ``seconds``,
        ``sample``, ``resident_before`` and ``resident_after``), and the totals
        ``files``, ``bytes``, ``seconds``.
    """
    report: dict = {"groups": {}, "files": 0, "bytes": 0, "seconds": 0.0}
    for name, files in groups.items():
        sample = sample_paths(files)
        before = resident_share(sample, tool, run)
        counts = drop_files(files, sandbox)
        after = resident_share(sample, tool, run)
        report["groups"][name] = {
            **counts,
            "filesystem": filesystem_type(files[0]),
            "sample_files": len(sample),
            "resident_before": before,
            "resident_after": after,
        }
        report["files"] += counts["files"]
        report["bytes"] += counts["bytes"]
        report["seconds"] = round(report["seconds"] + counts["seconds"], 3)
    return report


def overall_residency(report: dict) -> dict[str, float | None]:
    """Combine the per-group samples into one before and one after percentage.

    Parameters
    ----------
    report : `dict`
        The result of `drop_report`.

    Returns
    -------
    percent : `dict`
        ``before`` and ``after``: the share of the sampled bytes in memory,
        or `None` when nothing was measured.
    """
    result: dict[str, float | None] = {}
    for key in ("before", "after"):
        resident = total = 0
        for group in report["groups"].values():
            share = group[f"resident_{key}"]
            if share["total_bytes"]:
                resident += share["resident_bytes"]
                total += share["total_bytes"]
        result[key] = round(100.0 * resident / total, 1) if total else None
    return result


def format_report(report: dict) -> str:
    """Write the report as a short table for the terminal.

    Parameters
    ----------
    report : `dict`
        The result of `drop_report` with the extra keys `main` adds.

    Returns
    -------
    text : `str`
        One line per group (files, megabytes, seconds, resident percent of
        the sample before and after) and a total line.
    """

    def show(value: float | None) -> str:
        """Write a percentage, or a dash when it was not measured.

        Returns
        -------
        text : `str`
            The text.
        """
        return "-" if value is None else f"{value:.1f}%"

    lines = [
        f"{'group':<22} {'files':>7} {'MB':>9} {'sec':>6}  {'resident before':>15} {'after':>7}  file system"
    ]
    for name, group in report["groups"].items():
        lines.append(
            f"{name:<22} {group['files']:>7} {group['bytes'] / 1048576:>9.1f} {group['seconds']:>6.2f}"
            f"  {show(group['resident_before']['percent']):>15} {show(group['resident_after']['percent']):>7}"
            f"  {group['filesystem']}"
        )
    overall = report["overall_resident_percent"]
    lines.append(
        f"{'TOTAL':<22} {report['files']:>7} {report['bytes'] / 1048576:>9.1f} {report['seconds']:>6.2f}"
        f"  {show(overall['before']):>15} {show(overall['after']):>7}"
    )
    return "\n".join(lines)


def main() -> int:
    """Run one of the three commands.

    Returns
    -------
    status : `int`
        The exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    drop = commands.add_parser("drop")
    drop.add_argument("--sandbox", required=True)
    drop.add_argument("--repo", required=True)
    drop.add_argument("--out", required=True)
    drop.add_argument("--real-stacks")
    drop.add_argument("--browser")
    drop.add_argument("--after-reboot", action="store_true")
    commands.add_parser("boot-age")
    kind = commands.add_parser("filesystem")
    kind.add_argument("path")
    arguments = parser.parse_args()
    if arguments.command == "boot-age":
        print(boot_age_seconds())
        return 0
    if arguments.command == "filesystem":
        print(filesystem_type(Path(arguments.path)) or "unknown")
        return 0
    sandbox = Path(arguments.sandbox).resolve()
    started = time.monotonic()
    groups = file_groups(
        sandbox,
        Path(arguments.repo).resolve(),
        Path(arguments.real_stacks) if arguments.real_stacks else None,
        Path(arguments.browser) if arguments.browser else None,
    )
    selected_seconds = round(time.monotonic() - started, 2)
    tool = residency_tool()
    report = drop_report(groups, sandbox, tool)
    report.update({
        "cold": True,
        "cold_source": "reboot" if arguments.after_reboot else "fadvise",
        "boot_age_seconds": boot_age_seconds(),
        "residency_tool": tool,
        "selection_seconds": selected_seconds,
        "sandbox_filesystem": filesystem_type(sandbox),
        "overall_resident_percent": overall_residency(report),
        "ineffective_groups": [
            name for name, group in report["groups"].items() if group["filesystem"] in MEMORY_FILE_SYSTEMS
        ],
    })
    Path(arguments.out).write_text(json.dumps(report, indent=2) + "\n")
    print(format_report(report))
    print(
        f"cold_source: {report['cold_source']}   boot age: {report['boot_age_seconds']} s   "
        f"residency tool: {tool}   selection took {selected_seconds} s"
    )
    if report["ineffective_groups"]:
        print(
            "WARNING: these groups sit on a memory file system, where a drop cannot work: "
            + ", ".join(report["ineffective_groups"]),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
