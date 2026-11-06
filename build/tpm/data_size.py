"""Purpose: Count how much data a TPM run's library holds.

Description: A TPM only means something next to the size of the data it was
measured on. This script reads the throwaway copies (never the real files),
counts targets, light frames, stars, observing nights and guiding samples,
and adds up the database file sizes and the size of the frames folder (in the
real-snapshot mode that is the overlay, so it is the size of the real library
as the backend sees it). ``compile_tpm.py`` writes the result into
``tpm.json`` as the ``data_size`` info block.

Usage::

    .venv/bin/python build/tpm/data_size.py --library-dir <dir> \
        --wayfinding-db <file> --frames-root <dir> \
        [--catalog-db <file>] --out <json file>
"""

import argparse
import json
import sqlite3
from pathlib import Path

#: File types counted as frames.
FRAME_SUFFIXES = {".fits", ".fit", ".fts", ".cr2", ".nef", ".arw", ".dng"}


def count_rows(database: Path, query: str) -> int | None:
    """Count rows in one table, reading the file read only.

    Parameters
    ----------
    database : `pathlib.Path`
        The SQLite file.
    query : `str`
        A fixed ``SELECT`` that returns one number.

    Returns
    -------
    count : `int` or `None`
        The number, or `None` if the file or table is missing.
    """
    if not database.exists():
        return None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            return int(connection.execute(query).fetchone()[0])
        finally:
            connection.close()
    except sqlite3.Error:
        return None


def walk_frames(frames_root: Path) -> tuple[int, int]:
    """Count light frames and add up the size of the frames folder.

    Parameters
    ----------
    frames_root : `pathlib.Path`
        The frames folder (the overlay mount in real-snapshot mode).

    Returns
    -------
    light_frames, total_bytes : `int`
        Frame files under ``lights`` that are not stacks or previews, and
        the size of all files under the folder.
    """
    light_frames = 0
    total_bytes = 0
    lights = frames_root / "lights"
    for item in frames_root.rglob("*"):
        if not item.is_file():
            continue
        total_bytes += item.stat().st_size
        if (
            lights in item.parents
            and item.suffix.lower() in FRAME_SUFFIXES
            and "stack" not in item.name.lower()
        ):
            light_frames += 1
    return light_frames, total_bytes


def file_size(path: Path) -> int | None:
    """Give a file's size.

    Parameters
    ----------
    path : `pathlib.Path`
        The file.

    Returns
    -------
    size : `int` or `None`
        Bytes, or `None` if the file does not exist.
    """
    return path.stat().st_size if path.exists() else None


def collect(library_dir: Path, wayfinding_db: Path, frames_root: Path, catalog_db: Path | None) -> dict:
    """Collect the data-size numbers.

    Parameters
    ----------
    library_dir : `pathlib.Path`
        The folder with ``astrometrics.db`` and ``astrometrics_log.db``.
    wayfinding_db : `pathlib.Path`
        The wayfinding database.
    frames_root : `pathlib.Path`
        The frames folder.
    catalog_db : `pathlib.Path` or `None`
        The Gaia star cache, if present.

    Returns
    -------
    sizes : `dict`
        The counts and sizes, `None` where a source is missing.
    """
    main_db = library_dir / "astrometrics.db"
    light_frames, library_bytes = walk_frames(frames_root) if frames_root.exists() else (None, None)
    return {
        "targets": count_rows(main_db, "SELECT count(*) FROM targets"),
        "light_frames": light_frames,
        "stars": count_rows(main_db, "SELECT count(*) FROM stellar_objects"),
        "gaia_cache_stars": count_rows(catalog_db, "SELECT count(*) FROM gaia_sources")
        if catalog_db
        else None,
        "observing_nights": count_rows(
            wayfinding_db, "SELECT count(DISTINCT session_id) FROM guiding_samples"
        ),
        "guiding_samples": count_rows(wayfinding_db, "SELECT count(*) FROM guiding_samples"),
        "quality_measurements": count_rows(main_db, "SELECT count(*) FROM quality_measurements"),
        "database_bytes": {
            "astrometrics.db": file_size(main_db),
            "astrometrics_log.db": file_size(library_dir / "astrometrics_log.db"),
            "wayfinding.db": file_size(wayfinding_db),
        },
        "library_bytes": library_bytes,
    }


def main() -> int:
    """Collect and write the numbers.

    Returns
    -------
    status : `int`
        Always 0.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--library-dir", required=True)
    parser.add_argument("--wayfinding-db", required=True)
    parser.add_argument("--frames-root", required=True)
    parser.add_argument("--catalog-db")
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    sizes = collect(
        Path(arguments.library_dir),
        Path(arguments.wayfinding_db),
        Path(arguments.frames_root),
        Path(arguments.catalog_db) if arguments.catalog_db else None,
    )
    Path(arguments.out).write_text(json.dumps(sizes, indent=2))
    print("Data size:", {key: value for key, value in sizes.items() if key != "database_bytes"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
