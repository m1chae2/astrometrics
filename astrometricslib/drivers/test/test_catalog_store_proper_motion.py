"""Purpose: Tests for the proper motion columns of the Gaia cache.

Description: The cache stores each star's proper motion (how far it moves
across the sky each year) next to its epoch 2016.0 position. A cache file
written before those columns existed must keep working: the store adds the
columns, and the stars already in the file read back with an unknown
(`None`) proper motion. These tests build such an old file by hand.
"""

import sqlite3
from pathlib import Path

from astrometricslib.drivers import catalog_store


class _Config:
    """A stand-in for `AppConfiguration` that names a library folder."""

    def __init__(self, library_path: Path) -> None:
        """Remember the folder.

        Parameters
        ----------
        library_path : `pathlib.Path`
            The folder that holds the ``catalogs`` directory.
        """
        self.library_path = library_path

    def get_library_path(self) -> Path:
        """Return the library folder.

        Returns
        -------
        path : `pathlib.Path`
            The folder given at construction.
        """
        return self.library_path


def _write_old_cache(library_path: Path) -> None:
    """Write a cache file with the original five-column ``gaia_sources`` table.

    Parameters
    ----------
    library_path : `pathlib.Path`
        The folder in which to create ``catalogs/catalog_cache.db``.
    """
    catalogs = library_path / "catalogs"
    catalogs.mkdir()
    connection = sqlite3.connect(catalogs / "catalog_cache.db")
    connection.execute(
        "CREATE TABLE gaia_sources "
        "(source_id TEXT PRIMARY KEY, ra REAL, dec REAL, phot_g_mean_mag REAL, designation TEXT)"
    )
    connection.execute("INSERT INTO gaia_sources VALUES ('1', 10.0, 20.0, 12.0, 'Gaia DR3 1')")
    connection.commit()
    connection.close()


def _fetch(config: _Config, *, include_proper_motion: bool) -> list[tuple]:
    """Read every cached star in a box around (10, 20) degrees.

    Parameters
    ----------
    config : `_Config`
        Names the library folder.
    include_proper_motion : `bool`
        Whether to ask for the proper motion columns.

    Returns
    -------
    rows : `list` [`tuple`]
        One plain tuple per star, sorted.
    """
    rows = catalog_store.query_gaia_sources_in_bounds(
        config, 9.0, 11.0, 19.0, 21.0, include_proper_motion=include_proper_motion
    )
    return sorted(tuple(row) for row in rows)


def test_an_old_cache_gains_the_columns_and_reads_back_unknown_proper_motion(tmp_path: Path) -> None:
    """A star stored before proper motions existed has `None` for both."""
    _write_old_cache(tmp_path)

    rows = _fetch(_Config(tmp_path), include_proper_motion=True)

    assert rows == [("1", 10.0, 20.0, 12.0, "Gaia DR3 1", None, None)]


def test_the_default_query_still_returns_five_values_per_star(tmp_path: Path) -> None:
    """Callers that do not ask for proper motion see the old row shape."""
    _write_old_cache(tmp_path)

    rows = _fetch(_Config(tmp_path), include_proper_motion=False)

    assert rows == [("1", 10.0, 20.0, 12.0, "Gaia DR3 1")]


def test_proper_motion_round_trips_through_the_cache(tmp_path: Path) -> None:
    """A stored proper motion is returned; a five-value row stores `None`."""
    (tmp_path / "catalogs").mkdir()
    config = _Config(tmp_path)

    catalog_store.insert_gaia_sources(
        config,
        [
            ("a", 10.0, 20.0, 11.0, "Gaia DR3 a", 1000.0, -250.5),
            ("b", 10.1, 20.1, 12.0, "Gaia DR3 b"),
        ],
    )

    assert _fetch(config, include_proper_motion=True) == [
        ("a", 10.0, 20.0, 11.0, "Gaia DR3 a", 1000.0, -250.5),
        ("b", 10.1, 20.1, 12.0, "Gaia DR3 b", None, None),
    ]


def test_opening_a_migrated_cache_again_changes_nothing(tmp_path: Path) -> None:
    """The second open finds the columns already there."""
    _write_old_cache(tmp_path)
    config = _Config(tmp_path)

    first = _fetch(config, include_proper_motion=True)
    second = _fetch(config, include_proper_motion=True)

    assert first == second
