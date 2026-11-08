"""Purpose: Copy the user's star-catalog cache into the TPM throwaway folder.

Description: The seeded library has few stars, but the real Planetarium draws
the stars from the Gaia cache (about a million rows), the Gaia spectra cache
and the deep-star catalog. With ``--sky-catalog-copy`` this script copies those
files, so the throwaway backend serves the same star density as the real app.

Safety: the real files are only read. SQLite files are read through a
read-only connection (the SQLite backup call), other files with a plain copy.
Everything is written inside the throwaway folder, where the backend looks
because ``run_tpm.sh`` points ``XDG_CACHE_HOME`` and the library path
there. The script refuses to copy more than 5 GB, and prints the size first.

Usage::

    .venv/bin/python build/tpm/copy_sky_catalog.py <sandbox>

Run it before the script changes ``XDG_CACHE_HOME`` and ``XDG_DATA_HOME``,
because it finds the real folders through them.
"""

import os
import shutil
import sqlite3
import sys
from pathlib import Path

#: The largest total the script copies, in bytes.
MAXIMUM_COPY_BYTES = 5 * 1024**3
#: Items under the cache folder ``catalogs`` that the Planetarium reads.
CACHE_ITEMS = ("catalog_cache.db", "gaia_xp")
#: Items under the data folder ``catalogs`` (the deep-star catalog).
DATA_ITEMS = ("deep_star_catalog.db",)


def real_catalog_folders() -> tuple[Path, Path]:
    """Find the user's real catalog folders the way the app does.

    Returns
    -------
    cache_folder, data_folder : `pathlib.Path`
        ``<cache root>/astrometrics/catalogs`` and
        ``<data root>/astrometrics/catalogs``.
    """
    cache_root = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    data_root = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(cache_root) / "astrometrics" / "catalogs", Path(data_root) / "astrometrics" / "catalogs"


def size_of(path: Path) -> int:
    """Give the size of a file, or of all the files in a folder.

    Parameters
    ----------
    path : `pathlib.Path`
        A file or folder.

    Returns
    -------
    size : `int`
        Bytes. Zero if it does not exist.
    """
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    return 0


def copy_item(source: Path, destination: Path) -> None:
    """Copy one file or folder without ever writing to the source.

    Parameters
    ----------
    source : `pathlib.Path`
        The real file or folder (read only).
    destination : `pathlib.Path`
        The copy, inside the throwaway folder.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
        return
    if source.suffix == ".db":
        try:
            origin = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
            target = sqlite3.connect(destination)
            try:
                origin.backup(target)
            finally:
                target.close()
                origin.close()
            return
        except sqlite3.Error:
            destination.unlink(missing_ok=True)
    shutil.copyfile(source, destination)


def copy_sky_catalog(sandbox: Path) -> int:
    """Copy the catalog files into the throwaway folder.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder for this run.

    Returns
    -------
    status : `int`
        0 on success, 1 if the copy would be too big or the sources are
        missing.
    """
    cache_folder, data_folder = real_catalog_folders()
    plan = [
        (cache_folder / name, sandbox / "xdg_cache" / "astrometrics" / "catalogs" / name)
        for name in CACHE_ITEMS
    ]
    plan += [(data_folder / name, sandbox / "library" / "catalogs" / name) for name in DATA_ITEMS]
    plan = [(source, destination) for source, destination in plan if source.exists()]
    for source, _ in plan:
        if sandbox.resolve() in source.resolve().parents:
            print(f"ERROR: {source} is inside the throwaway folder; refusing.", file=sys.stderr)
            return 1
    if not plan:
        print(f"ERROR: no star-catalog files found in {cache_folder} or {data_folder}.", file=sys.stderr)
        return 1
    total = sum(size_of(source) for source, _ in plan)
    limit_gb = MAXIMUM_COPY_BYTES / 1024**3
    print(f"Sky catalog copy: {total / 1024**2:.0f} MB from {len(plan)} item(s); limit {limit_gb:.0f} GB.")
    if total > MAXIMUM_COPY_BYTES:
        print("ERROR: the copy is over the limit; refusing.", file=sys.stderr)
        return 1
    for source, destination in plan:
        print(f"  {source} -> {destination}")
        copy_item(source, destination)
    return 0


if __name__ == "__main__":
    sys.exit(copy_sky_catalog(Path(sys.argv[1]).resolve()))
