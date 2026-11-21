"""Purpose: Tests for the proper motion columns of the Gaia cache.

Description: The cache stores each star's proper motion (how far it moves
across the sky each year) next to its epoch 2016.0 position. These tests
check that proper motion and its absence round-trip through the cache.
"""

from pathlib import Path

from astrometricslib._internal.drivers import catalog_store


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


_TILE = 42


def _fetch(config: _Config, *, include_proper_motion: bool) -> list[tuple]:
    """Read every cached star of the test tile.

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
    rows = catalog_store.stars_in_tiles(config, [_TILE], 99.0, include_proper_motion=include_proper_motion)
    return sorted(tuple(row) for row in rows)


def test_proper_motion_round_trips_through_the_cache(tmp_path: Path) -> None:
    """A stored proper motion is returned; a five-value row stores `None`."""
    config = _Config(tmp_path)

    first = str((_TILE << 45) + 101)
    second = str((_TILE << 45) + 102)
    catalog_store.record_tile_fetch(
        config,
        _TILE,
        18.0,
        [(first, 10.0, 20.0, 11.0, "x", 1000.0, -250.5), (second, 10.1, 20.1, 12.0, "y")],
        "test",
    )

    assert _fetch(config, include_proper_motion=True) == [
        (first, 10.0, 20.0, 11.0, f"Gaia DR3 {first}", 1000.0, -250.5),
        (second, 10.1, 20.1, 12.0, f"Gaia DR3 {second}", None, None),
    ]
