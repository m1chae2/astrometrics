r"""Check that raw frames on Google Drive have not vanished or changed.

Raw frames must never change after they are taken. This script asks Google
Drive (through ``rclone``) for the size, MD5 checksum and modification time of
every file under the library folder. It saves that list, called a manifest, as
a gzipped JSON file in the scratch folder, then compares it with the previous
manifest.

Results are sorted into three groups:

* MISSING: the path was in the old manifest and is gone now.
* CHANGED: the path is still there, but its MD5 checksum or size differs.
* ADDED: new paths. Only the count is reported.

A MISSING or CHANGED file under ``lights/``, ``darks/``, ``biases/`` or
``flats/`` is serious, unless it is derived output (stacks, rejection maps,
processed pictures, JPEGs, ``.seq`` files, or anything in a ``stacks/``
folder). Derived files are expected to be rebuilt, so they are only listed as
information. The ``_backup`` folder is skipped because another job writes it.

Exit codes: 0 means no serious problem, 2 means at least one, and 1 means
``rclone`` failed. The first run has nothing to compare with, so it only
records a baseline.

Run it with::

    python -m astrometricslib.scripts.check_drive_integrity
    python -m astrometricslib.scripts.check_drive_integrity --json --no-notify
"""

import argparse
import fnmatch
import gzip
import json
import logging
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from astrometricslib.foundation.paths import scratch_directory

logger = logging.getLogger(__name__)

RAW_TOP_FOLDERS = ("lights", "darks", "biases", "flats")
SKIPPED_TOP_FOLDERS = ("_backup",)
DERIVED_NAME_PATTERNS = ("*_Stacked*.fits", "*_RejMap.fits", "*_processed.*", "*.jpg", "*.seq")
MANIFESTS_TO_KEEP = 10
MANIFEST_PREFIX = "manifest_"
MANIFEST_SUFFIX = ".json.gz"

Manifest = dict[str, dict]


def is_derived_path(path: str) -> bool:
    """Say whether a path is derived output rather than a raw frame.

    Parameters
    ----------
    path : `str`
        Path relative to the library folder, with ``/`` separators.

    Returns
    -------
    is_derived : `bool`
        True for stacks, rejection maps, processed pictures, JPEGs, ``.seq``
        files and anything inside a ``stacks`` folder.
    """
    parts = path.split("/")
    if "stacks" in parts[:-1]:
        return True
    return any(fnmatch.fnmatch(parts[-1], pattern) for pattern in DERIVED_NAME_PATTERNS)


def is_raw_area_path(path: str) -> bool:
    """Say whether a path sits under one of the raw-frame folders.

    Parameters
    ----------
    path : `str`
        Path relative to the library folder, with ``/`` separators.

    Returns
    -------
    in_raw_area : `bool`
        True when the first folder is lights, darks, biases or flats.
    """
    return path.split("/")[0] in RAW_TOP_FOLDERS


def fetch_manifest(rclone_executable: str, remote: str, root: str) -> Manifest:
    """List every file on the remote and build a manifest from it.

    Parameters
    ----------
    rclone_executable : `str`
        Name or path of the ``rclone`` program.
    remote : `str`
        The rclone remote name, such as ``drive``.
    root : `str`
        The folder on the remote, such as ``Library``.

    Returns
    -------
    manifest : `dict` [`str`, `dict`]
        Maps each path to ``{"size", "md5", "mod_time"}``. ``md5`` is None when
        Drive gave no checksum.

    Raises
    ------
    RuntimeError
        If ``rclone`` cannot run, exits with an error, or prints bad JSON.
    """
    command = [rclone_executable, "lsjson", "-R", "--hash", "--files-only", f"{remote}:{root}"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        raise RuntimeError(f"could not run rclone: {error}") from error
    if completed.returncode != 0:
        raise RuntimeError(f"rclone exited with {completed.returncode}: {completed.stderr.strip()}")
    try:
        entries = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"rclone printed invalid JSON: {error}") from error
    manifest: Manifest = {}
    for entry in entries:
        path = entry["Path"]
        if path.split("/")[0] in SKIPPED_TOP_FOLDERS:
            continue
        manifest[path] = {
            "size": entry.get("Size"),
            "md5": (entry.get("Hashes") or {}).get("md5"),
            "mod_time": entry.get("ModTime"),
        }
    return manifest


def list_manifest_files(manifest_directory: Path) -> list[Path]:
    """List stored manifests, oldest first.

    Parameters
    ----------
    manifest_directory : `pathlib.Path`
        The folder holding manifests.

    Returns
    -------
    manifest_files : `list` [`pathlib.Path`]
        Manifest files sorted by name, which sorts them by time.
    """
    return sorted(manifest_directory.glob(f"{MANIFEST_PREFIX}*{MANIFEST_SUFFIX}"))


def save_manifest(manifest: Manifest, manifest_directory: Path, now: datetime) -> Path:
    """Write a manifest as gzipped JSON, then delete all but the newest few.

    Parameters
    ----------
    manifest : `dict` [`str`, `dict`]
        The manifest to store.
    manifest_directory : `pathlib.Path`
        The folder to write into, created if needed.
    now : `datetime.datetime`
        Time used in the file name.

    Returns
    -------
    manifest_path : `pathlib.Path`
        The file written.
    """
    manifest_directory.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_directory / f"{MANIFEST_PREFIX}{now:%Y%m%d_%H%M%S}{MANIFEST_SUFFIX}"
    with gzip.open(manifest_path, "wt", encoding="utf-8") as handle:
        json.dump(manifest, handle)
    for old_file in list_manifest_files(manifest_directory)[:-MANIFESTS_TO_KEEP]:
        old_file.unlink()
    return manifest_path


def load_manifest(manifest_path: Path) -> Manifest:
    """Read a stored manifest.

    Parameters
    ----------
    manifest_path : `pathlib.Path`
        A gzipped JSON manifest.

    Returns
    -------
    manifest : `dict` [`str`, `dict`]
        The stored manifest.
    """
    with gzip.open(manifest_path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def compare_manifests(old_manifest: Manifest, new_manifest: Manifest) -> dict:
    """Compare two manifests and sort the differences by seriousness.

    Parameters
    ----------
    old_manifest : `dict` [`str`, `dict`]
        The earlier manifest.
    new_manifest : `dict` [`str`, `dict`]
        The current manifest.

    Returns
    -------
    comparison : `dict`
        Keys ``serious_missing`` and ``serious_changed`` (raw frames),
        ``derived_missing`` and ``derived_changed`` (informational),
        ``moved`` (a list of ``[old_path, new_path]`` pairs, informational)
        and ``added_count``. A missing path whose checksum and size match a
        newly added path counts as moved, not missing. Each list is sorted.
    """
    serious_missing, serious_changed = [], []
    derived_missing, derived_changed = [], []
    added_paths = sorted(set(new_manifest) - set(old_manifest))
    unclaimed_by_identity: dict[tuple, list[str]] = {}
    for added_path in added_paths:
        added_entry = new_manifest[added_path]
        if added_entry.get("md5") is not None:
            identity = (added_entry["md5"], added_entry.get("size"))
            unclaimed_by_identity.setdefault(identity, []).append(added_path)
    moved = []
    for path, old_entry in old_manifest.items():
        new_entry = new_manifest.get(path)
        if new_entry is None:
            identity = (old_entry.get("md5"), old_entry.get("size"))
            if old_entry.get("md5") is not None and unclaimed_by_identity.get(identity):
                moved.append([path, unclaimed_by_identity[identity].pop(0)])
                continue
            problem = "missing"
        elif old_entry.get("md5") != new_entry.get("md5") or old_entry.get("size") != new_entry.get("size"):
            problem = "changed"
        else:
            continue
        is_serious = is_raw_area_path(path) and not is_derived_path(path)
        target_list = {
            ("missing", True): serious_missing,
            ("missing", False): derived_missing,
            ("changed", True): serious_changed,
            ("changed", False): derived_changed,
        }[problem, is_serious]
        target_list.append(path)
    return {
        "serious_missing": sorted(serious_missing),
        "serious_changed": sorted(serious_changed),
        "derived_missing": sorted(derived_missing),
        "derived_changed": sorted(derived_changed),
        "moved": sorted(moved),
        "added_count": len(added_paths) - len(moved),
    }


def send_notification(message: str) -> None:
    """Try to show a desktop notification, ignoring any failure.

    Parameters
    ----------
    message : `str`
        The notification text.
    """
    executable = shutil.which("notify-send")
    if executable is None:
        return
    try:
        subprocess.run(
            [executable, "-u", "critical", "Drive integrity check", message], check=False, timeout=10
        )
    except OSError, subprocess.SubprocessError:
        pass


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        The parser with all flags.
    """
    parser = argparse.ArgumentParser(description="Check that raw frames on Google Drive are unchanged.")
    parser.add_argument("--rclone", default="rclone", help="rclone program (default: rclone)")
    parser.add_argument("--remote", default="drive", help="rclone remote name (default: drive)")
    parser.add_argument("--root", default="Library", help="folder on the remote (default: Library)")
    parser.add_argument("--manifest-directory", default=None, help="where manifests are kept")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    parser.add_argument("--no-notify", action="store_true", help="do not send a desktop notification")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the check.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments. Defaults to ``sys.argv[1:]``.

    Returns
    -------
    exit_code : `int`
        0 when no serious problem, 2 when there is one, 1 if rclone failed.
    """
    arguments = build_argument_parser().parse_args(argv)
    if arguments.manifest_directory:
        manifest_directory = Path(arguments.manifest_directory)
    else:
        manifest_directory = Path(scratch_directory()) / "integrity"
    try:
        new_manifest = fetch_manifest(arguments.rclone, arguments.remote, arguments.root)
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    previous_files = list_manifest_files(manifest_directory) if manifest_directory.is_dir() else []
    old_manifest = load_manifest(previous_files[-1]) if previous_files else None
    save_manifest(new_manifest, manifest_directory, datetime.now())

    summary: dict = {"file_count": len(new_manifest), "baseline": old_manifest is None}
    if old_manifest is not None:
        summary.update(compare_manifests(old_manifest, new_manifest))
    serious_count = len(summary.get("serious_missing", [])) + len(summary.get("serious_changed", []))
    summary["serious_problem_count"] = serious_count

    if arguments.json:
        print(json.dumps(summary, indent=2))
    elif old_manifest is None:
        print(f"Baseline recorded: {len(new_manifest)} files.")
    else:
        print(f"Checked {len(new_manifest)} files; {summary['added_count']} added.")
        for label, key in (
            ("MISSING raw frame", "serious_missing"),
            ("CHANGED raw frame", "serious_changed"),
            ("missing derived file (info)", "derived_missing"),
            ("changed derived file (info)", "derived_changed"),
        ):
            for path in summary[key]:
                print(f"{label}: {path}")
        for old_path, new_path in summary["moved"]:
            print(f"MOVED (info): {old_path} -> {new_path}")
        if serious_count == 0:
            print("No serious problems.")
    if serious_count and not arguments.no_notify:
        send_notification(f"{serious_count} raw frame(s) on Drive are missing or changed.")
    return 2 if serious_count else 0


if __name__ == "__main__":
    sys.exit(main())
