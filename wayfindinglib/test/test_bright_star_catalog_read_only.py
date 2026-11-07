"""Tests that the bundled bright-star catalog loads from a read-only folder.

The sky map got no stars at all when the catalog's folder could not be written
to (a read-only install): the driver opened the file in a way that creates
side files next to it, so the open failed and the driver returned no stars.
"""

import os
import shutil
import sqlite3
import stat
from pathlib import Path

import pytest

from wayfindinglib.drivers.catalog.local_bright_star_catalog_driver import LocalBrightStarCatalogDriver


def _write_small_catalog(path: Path) -> None:
    """Write a two-star catalog file in the bundled catalog's layout.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to create the SQLite file.
    """
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE bright_stars (hip_id INTEGER, ra REAL, dec REAL, magnitude REAL)")
    connection.executemany(
        "INSERT INTO bright_stars VALUES (?, ?, ?, ?)", [(1, 10.0, 0.0, 1.0), (2, 11.0, 0.0, 2.0)]
    )
    connection.commit()
    connection.close()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores folder permissions")
def test_catalog_loads_from_a_read_only_folder(tmp_path: Path) -> None:
    """Stars come back and no side files appear in a read-only folder."""
    folder = tmp_path / "installed"
    folder.mkdir()
    catalog_path = folder / "bright_star_catalog.sqlite"
    _write_small_catalog(catalog_path)
    catalog_path.chmod(stat.S_IRUSR)
    folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        stars = LocalBrightStarCatalogDriver(catalog_path).query_region(10.5, 0.0, 5.0)
        assert sorted(star.id for star in stars) == ["HIP_1", "HIP_2"]
        assert sorted(entry.name for entry in folder.iterdir()) == ["bright_star_catalog.sqlite"]
    finally:
        folder.chmod(stat.S_IRWXU)
        shutil.rmtree(folder, ignore_errors=True)
