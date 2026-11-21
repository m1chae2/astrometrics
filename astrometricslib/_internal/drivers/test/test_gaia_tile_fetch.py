"""Purpose: Tests for the per-tile Gaia download functions.

Description: The tile functions in ``gaia_cone_driver`` ask the ESA archive
by source-ID range or VizieR by the circle around a tile. These tests use
fake services, so nothing touches the network. They check the tile geometry
against the library's own pixel finder, the queries sent, the service order
and the depth recorded when an answer hits the row limit.
"""

import sys
import types

import numpy as np
import pytest
from astropy.table import Table

from astrometricslib._internal.drivers import gaia_cone_driver
from astrometricslib._internal.drivers.gaia_cone_driver import (
    GAIA_CONE_COLUMNS,
    TileFetch,
    fetch_gaia_tile,
    separations_degrees,
    tile_circle,
    tile_outline_points,
)
from astrometricslib._internal.drivers.sky_tiles import healpix_pixels_of_points, pixel_source_id_range
from astrometricslib.foundation.errors import ExternalServiceError

LEVEL = 7


def _table(tile: int, magnitudes: list[float]) -> Table:
    """Build a result table of stars inside a tile.

    Parameters
    ----------
    tile : `int`
        The level-7 tile.
    magnitudes : `list` [`float`]
        One G magnitude per star.

    Returns
    -------
    table : `astropy.table.Table`
        The stars, in the column layout of `GAIA_CONE_COLUMNS`.
    """
    low, _ = pixel_source_id_range(LEVEL, tile)
    ids = [str(low + index) for index in range(len(magnitudes))]
    count = len(ids)
    return Table(
        [
            ids,
            [1.0] * count,
            [2.0] * count,
            magnitudes,
            [f"Gaia DR3 {i}" for i in ids],
            [0.0] * count,
            [0.0] * count,
        ],
        names=GAIA_CONE_COLUMNS,
    )


@pytest.mark.parametrize("tile", [0, 5, 63, 100_000, 120_000, 196_607, 90_000])
def test_tile_outline_points_match_the_pixel_finder(tile: int) -> None:
    """The center is in its tile and the circle holds the outline."""
    ra, dec = tile_outline_points(LEVEL, tile)
    assert int(healpix_pixels_of_points(LEVEL, ra[0], dec[0])) == tile
    # Corners lie on the border, so only the center is exact.
    center_ra, center_dec, radius = tile_circle(LEVEL, tile)
    assert 0.1 < radius < 0.45
    assert np.max(separations_degrees(center_ra, center_dec, ra, dec)) < radius


def test_every_tile_center_at_a_low_level_is_found() -> None:
    """Centers round-trip through the pixel finder at level 2."""
    for tile in range(12 * 4**2):
        ra, dec = tile_outline_points(2, tile)
        assert int(healpix_pixels_of_points(2, ra[0], dec[0])) == tile


def test_the_esa_query_asks_for_the_missing_depth_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The query holds the ID range, both depths, TOP and ORDER BY."""
    sent: list[str] = []

    class _Job:
        """A finished fake job."""

        def get_results(self) -> Table:
            """Return an empty result.

            Returns
            -------
            table : `astropy.table.Table`
                A table with the archive's column names and no rows.
            """
            return Table(
                names=["source_id", "ra", "dec", "phot_g_mean_mag", "designation", "pmra", "pmdec"],
                dtype=[str, float, float, float, str, float, float],
            )

    class _Gaia:
        """A fake archive client."""

        @staticmethod
        def launch_job_async(query: str, dump_to_file: bool = False) -> _Job:
            """Remember the query.

            Parameters
            ----------
            query : `str`
                The ADQL text.
            dump_to_file : `bool`
                Ignored.

            Returns
            -------
            job : `_Job`
                A finished job.
            """
            sent.append(query)
            return _Job()

    module = types.ModuleType("astroquery.gaia")
    module.Gaia = _Gaia  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "astroquery.gaia", module)
    monkeypatch.setattr(gaia_cone_driver, "require_online", lambda service: None)

    gaia_cone_driver.query_gaia_tile_esa(LEVEL, 42, 18.0, 20.0, 5000)

    low, high = pixel_source_id_range(LEVEL, 42)
    assert len(sent) == 1
    assert sent[0].startswith("SELECT TOP 5000 source_id")
    assert f"source_id >= {low} AND source_id < {high}" in sent[0]
    assert "phot_g_mean_mag < 20.0" in sent[0]
    assert "phot_g_mean_mag >= 18.0" in sent[0]
    assert sent[0].endswith("ORDER BY phot_g_mean_mag ASC")


def test_the_vizier_path_keeps_only_rows_in_the_tile(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cone over the tile's circle is cut to the tile's source-ID range."""
    inside = _table(77, [10.0, 12.0])
    outside = _table(78, [11.0])
    combined = Table(rows=[*inside, *outside], names=GAIA_CONE_COLUMNS)
    seen: dict[str, object] = {}

    def fake_cone(
        ra: float, dec: float, radius: float, magnitude: float, row_limit: int, timeout: float, **kw: object
    ) -> Table:
        """Return the combined table and remember the request.

        Parameters
        ----------
        ra, dec, radius, magnitude : `float`
            The circle and depth asked for.
        row_limit : `int`
            The row limit.
        timeout : `float`
            The timeout.
        **kw : `object`
            Extra options, such as the magnitude filter.

        Returns
        -------
        table : `astropy.table.Table`
            Stars from tile 77 and tile 78.
        """
        seen.update(radius=radius, filter=kw.get("magnitude_filter"))
        return combined

    monkeypatch.setattr(gaia_cone_driver, "query_gaia_cone_vizier", fake_cone)
    result = gaia_cone_driver.query_gaia_tile_vizier(LEVEL, 77, 0.0, 18.0, 50_000, 45.0)

    assert len(result) == 2
    assert result.meta["circle_rows"] == 3
    assert result.meta["circle_faintest_g"] == pytest.approx(12.0)
    assert seen["filter"] == "<18.0"
    assert 0.1 < float(seen["radius"]) < 0.45


def _fetch(**overrides: object) -> TileFetch:
    """Run `fetch_gaia_tile` with small settings and the given fakes.

    Parameters
    ----------
    **overrides : `object`
        Arguments that replace the defaults.

    Returns
    -------
    fetch : `TileFetch`
        The answer.
    """
    arguments: dict[str, object] = {
        "vizier_timeout_seconds": 45,
        "esa_timeout_seconds": 60,
        "depth_step_magnitudes": 1.0,
        "depth_steps_down": 2,
        "run_with_deadline": lambda query, deadline: query(),
    }
    arguments.update(overrides)
    return fetch_gaia_tile(LEVEL, 10, 0.0, 18.0, 4, **arguments)  # type: ignore[arg-type]


def test_vizier_is_tried_first_and_a_full_answer_is_complete_to_the_depth() -> None:
    """The first service that answers wins; the depth is the one asked for."""
    calls: list[str] = []

    def vizier(*arguments: object) -> Table:
        """Answer from fake VizieR.

        Parameters
        ----------
        *arguments : `object`
            The query arguments.

        Returns
        -------
        table : `astropy.table.Table`
            Two stars.
        """
        calls.append("vizier")
        return _table(10, [10.0, 11.0])

    result = _fetch(vizier_query=vizier)

    assert calls == ["vizier"]
    assert (result.service, result.complete_to_g, result.row_limit_reached) == ("VizieR", 18.0, False)


def test_a_row_limit_hit_records_the_faintest_g_reached() -> None:
    """A full table is complete only to its faintest G."""
    result = _fetch(vizier_query=lambda *a: _table(10, [10.0, 11.0, 12.0, 13.5]))

    assert result.row_limit_reached
    assert result.complete_to_g == pytest.approx(13.5)


def test_esa_follows_vizier_then_vizier_goes_shallower() -> None:
    """VizieR, ESA at full depth, then VizieR at G < 17 and G < 16."""
    calls: list[tuple[str, float]] = []

    def vizier(level: int, tile: int, have: float, want: float, limit: int, timeout: float) -> Table:
        """Fail every VizieR call except the one at G < 16.

        Parameters
        ----------
        level, tile : `int`
            The tile.
        have, want : `float`
            The depths.
        limit : `int`
            The row limit.
        timeout : `float`
            The timeout.

        Returns
        -------
        table : `astropy.table.Table`
            One star, at the third VizieR depth.

        Raises
        ------
        OSError
            At any depth fainter than G = 16.
        """
        calls.append(("vizier", want))
        if want > 16.0:
            raise OSError("down")
        return _table(10, [10.0])

    def esa(level: int, tile: int, have: float, want: float, limit: int) -> Table:
        """Fail like a busy archive.

        Parameters
        ----------
        level, tile : `int`
            The tile.
        have, want : `float`
            The depths.
        limit : `int`
            The row limit.

        Raises
        ------
        OSError
            Always.
        """
        calls.append(("esa", want))
        raise OSError("busy")

    errors: list[str] = []
    result = _fetch(
        vizier_query=vizier, esa_query=esa, on_error=lambda service, error: errors.append(service)
    )

    assert calls == [("vizier", 18.0), ("esa", 18.0), ("vizier", 17.0), ("vizier", 16.0)]
    assert result.complete_to_g == pytest.approx(16.0)
    assert errors == ["VizieR", "ESA Gaia archive", "VizieR"]


def test_every_failure_raises_and_skipped_services_are_not_called() -> None:
    """All attempts failing raises; skipped services are not called."""
    calls: list[str] = []

    def esa(*arguments: object) -> Table:
        """Fail like a busy archive.

        Parameters
        ----------
        *arguments : `object`
            The query arguments.

        Raises
        ------
        OSError
            Always.
        """
        calls.append("esa")
        raise OSError("busy")

    with pytest.raises(ExternalServiceError):
        _fetch(esa_query=esa, skip_service=lambda service: service == "VizieR")

    assert calls == ["esa"]
