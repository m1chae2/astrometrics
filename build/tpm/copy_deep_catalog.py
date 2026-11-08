"""Purpose: Copy the user's deep-star (Gaia) catalog into the TPM sandbox.

Description: Without a deep-star catalog the Planetarium has only a few
library stars to draw, so the sky-map frame-rate and frame-time TPMs would
measure an empty sky. This script copies the real catalog file into the
throwaway library's ``catalogs`` folder, so the throwaway backend loads the
same stars the real app does. It writes ``deep_catalog.json`` (the file size
and the star count) into the throwaway folder; ``setup_fingerprint.py collect``
puts that record into the setup record, so a baseline says what load it ran
with.

Safety: the real file is only read, through a read-only SQLite connection and
the SQLite backup call (a write-ahead log file cannot be half copied). Nothing
is symlinked or opened in place. The real library folder comes from the real
settings file, read only (``simulator.real_folders.real_folders``). If the
file is missing the script prints one line and exits with status 0, so the
run carries on without it.

In the simulation mode the app's own library copy already copies the catalog
with the other databases (`simulation_library.py prepare`), so that mode does
not use this script.

Usage::

    .venv/bin/python build/tpm/copy_deep_catalog.py <sandbox>

Run it before ``run_tpm.sh`` points the XDG folders at the throwaway folder.
"""

import json
import sqlite3
import sys
from pathlib import Path

from simulator.real_folders import real_folders

#: The deep-star catalog's file name (``deep_star_store.py`` in wayfindinglib).
DEEP_CATALOG_FILENAME = "deep_star_catalog.db"


def copy_deep_catalog(sandbox: Path, real_library: Path | None = None) -> dict | None:
    """Copy the deep-star catalog into the throwaway library and describe it.

    Parameters
    ----------
    sandbox : `pathlib.Path`
        The throwaway folder for this run.
    real_library : `pathlib.Path`, optional
        The real library folder. By default it is read from the real settings.

    Returns
    -------
    record : `dict` or `None`
        ``path``, ``size_bytes`` and ``star_count`` of the copy, or `None`
        if the real file does not exist.

    Raises
    ------
    ValueError
        If the real file is inside the throwaway folder.
    """
    library = real_library if real_library is not None else real_folders().library
    source = library / "catalogs" / DEEP_CATALOG_FILENAME
    if not source.is_file():
        print(f"Deep-star catalog: none at {source}; the Planetarium runs without it.")
        return None
    destination = sandbox / "library" / "catalogs" / DEEP_CATALOG_FILENAME
    if sandbox.resolve() in source.resolve().parents:
        raise ValueError(f"{source} is inside the throwaway folder; refusing.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    origin = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    target = sqlite3.connect(destination)
    try:
        origin.backup(target)
        star_count = target.execute("SELECT COUNT(*) FROM deep_stars").fetchone()[0]
    finally:
        target.close()
        origin.close()
    record = {
        "path": str(destination),
        "size_bytes": destination.stat().st_size,
        "star_count": int(star_count),
    }
    (sandbox / "deep_catalog.json").write_text(json.dumps(record), encoding="utf-8")
    print(f"Deep-star catalog: copied {record['star_count']} stars ({record['size_bytes'] / 1024:.0f} KB).")
    return record


def main(arguments: list[str]) -> int:
    """Copy the catalog for the sandbox named on the command line.

    Parameters
    ----------
    arguments : `list` [`str`]
        The command-line arguments after the program name: the sandbox.

    Returns
    -------
    status : `int`
        0 (also when the real file is missing), 2 if the usage is wrong.
    """
    if len(arguments) != 1:
        print(__doc__)
        return 2
    copy_deep_catalog(Path(arguments[0]).resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
