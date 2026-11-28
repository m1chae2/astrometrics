"""Stand-ins for the Gaia stars of an image's tiles, for the astrometry tests.

The star identifier reads the Gaia stars of an image through
`gaia_field_stars.load_field_gaia_stars` and lists the tiles with
`gaia_field_stars.tiles_under_image`. `use_field_stars` replaces them inside
the star identifier, so a test can hand it a fixed table without a cache or a
network.
"""

from typing import Any

import pytest

from astrometricslib.pipelines.astrometry.processing import gaia_field_matching, plate_solution_steps
from astrometricslib.pipelines.astrometry.processing.gaia_field_stars import FieldGaiaStars


def use_field_stars(
    monkeypatch: pytest.MonkeyPatch, table: Any = None, coords: Any = None, missing: bool = False
) -> list[tuple]:
    """Make the star identifier see a fixed set of Gaia stars for every image.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace the two functions.
    table : `astropy.table.Table`, optional
        The Gaia stars. `None` means no tile is complete.
    coords : `astropy.coordinates.SkyCoord`, optional
        Their positions, in the order of `table`.
    missing : `bool`, optional
        Report the one tile as missing (not complete), as a failed download
        would.

    Returns
    -------
    calls : `list` [`tuple`]
        One ``(depth, fetch)`` entry per read, so a test can see the depth
        asked for and whether a download was allowed.
    """
    calls: list[tuple] = []

    def fake_load(config: Any, tiles: Any, depth: float, fetch: Any, **_keywords: Any) -> FieldGaiaStars:
        """Give the fixed stars.

        Parameters
        ----------
        config, tiles : `Any`
            Ignored.
        depth : `float`
            The depth asked for, recorded.
        fetch : `Any`
            The fetcher, recorded.
        **_keywords : `Any`
            Ignored.

        Returns
        -------
        field : `FieldGaiaStars`
            The fixed stars.
        """
        calls.append((depth, fetch))
        return FieldGaiaStars(
            table=None if missing else table,
            coords=None if missing else coords,
            depth=depth,
            tiles=[1],
            complete_tiles=[] if missing or table is None else [1],
            missing_tiles=[1] if missing or table is None else [],
        )

    monkeypatch.setattr(gaia_field_matching, "load_field_gaia_stars", fake_load)

    monkeypatch.setattr(plate_solution_steps, "load_field_gaia_stars", fake_load)
    monkeypatch.setattr(gaia_field_matching, "tiles_under_image", lambda *_arguments, **_keywords: [1])
    return calls
