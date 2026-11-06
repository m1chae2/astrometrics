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
* ``get`` prints one value from ``real_snapshot.json`` (for the shell script).
* ``overlay-report`` lists what the backend wrote into the overlay's upper
  folder (the only place a write can land) and its size.

Safety: every real file is opened read only (``file:...?mode=ro``) or copied
with a plain read. All output goes inside the throwaway folder. The real
library (hundreds of GB) is not copied here: ``run_tpm.sh`` mounts a
copy-on-write overlay on it.

Usage::

    .venv/bin/python build/tpm/real_snapshot.py prepare <sandbox>
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

from astrometricslib.drivers import real_folders
from wayfindinglib.simulation import library_copy

#: Do not copy more than this many bytes of databases and caches.
MAXIMUM_COPY_BYTES = 5 * 1024**3


def prepare(sandbox: Path) -> int:
    """Make the copies and the settings file.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder.

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
    report = library_copy.copy_databases(sandbox, real.library, real.wayfinding, [])
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
    config_path.write_text(
        f'["Image Library"]\npath = "{library}"\nframes_path = "{frames_path}"\n'
        f'stacks_path = "{stacks_path}"\n\n'
        f'["Wayfinding Library"]\npath = "{sandbox / "wayfinding"}"\n'
        + OFFLINE_TELESCOPE_SETTINGS
        + "\n"
        + library_copy.keep_sections(text),
        encoding="utf-8",
    )
    (sandbox / "real_snapshot.json").write_text(
        json.dumps(
            {"real_library": frames_path, "real_stacks": stacks_path, "database_bytes": sizes}, indent=2
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
    if command == "get" and len(sys.argv) == 4:
        print(json.loads((Path(sys.argv[2]) / "real_snapshot.json").read_text())[sys.argv[3]])
        return 0
    if command == "overlay-report" and len(sys.argv) == 4:
        return overlay_report(Path(sys.argv[2]), Path(sys.argv[3]))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
