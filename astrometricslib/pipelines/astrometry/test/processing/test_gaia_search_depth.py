"""Purpose: Tests for the depth and size limits of the Gaia cone search.

Description: The old search asked for every Gaia star in the circle, ranked
them by distance from the centre and stopped at 10,000 rows. In a dense field
the stars at the edge were cut, so detected stars there found no match. The
search now asks only for stars brighter than a magnitude limit, returns the
brightest first, allows more rows, and records a flag when it still hits the
row limit. The limit comes from how deep the frame's own detections go. These
tests use a fake Gaia client, so no network is needed.
"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.table import Table

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.astrometry.processing import star_identifier as star_identifier_module
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier

CENTRE_RA_DEG = 250.42
CENTRE_DEC_DEG = 36.46
ZERO_POINT = 24.0


def fake_gaia(monkeypatch: pytest.MonkeyPatch, row_count: int) -> list[str]:
    """Replace Gaia's job launcher with one that returns a made-up table.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace `Gaia.launch_job_async`.
    row_count : `int`
        How many rows the fake query returns.

    Returns
    -------
    queries : `list` [`str`]
        Collects the ADQL text of each query sent.
    """
    import astroquery.gaia as gaia_module

    queries: list[str] = []
    table = Table({
        "source_id": np.arange(row_count).astype(str),
        "ra": CENTRE_RA_DEG + np.linspace(0.0, 0.05, row_count),
        "dec": np.full(row_count, CENTRE_DEC_DEG),
        "phot_g_mean_mag": np.linspace(8.0, 17.9, row_count),
        "designation": [f"Gaia DR3 {index}" for index in range(row_count)],
        "pmra": np.zeros(row_count),
        "pmdec": np.zeros(row_count),
    })

    def launch_job_async(query: str, **_keywords: object) -> MagicMock:
        """Record the query and return a finished job holding the table.

        Parameters
        ----------
        query : `str`
            The ADQL query text.
        **_keywords
            Ignored job options.

        Returns
        -------
        job : `unittest.mock.MagicMock`
            A job whose ``get_results()`` is the made-up table.
        """
        queries.append(query)
        job = MagicMock()
        job.get_results.return_value = table.copy()
        return job

    monkeypatch.setattr(gaia_module.Gaia, "launch_job_async", launch_job_async)
    return queries


@pytest.fixture(autouse=True)
def isolated_gaia_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give each test an empty Gaia cache and a closed circuit breaker.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The test's own folder, used as the library path.
    monkeypatch : `pytest.MonkeyPatch`
        Used to point the configuration at that folder.

    Yields
    ------
    None
        Runs the test between setup and clean-up.
    """
    import astrometricslib.foundation.config as config_loader_module
    from astrometricslib.foundation.config import AppConfiguration

    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(tmp_path)}})
    monkeypatch.setattr(config_loader_module, "get_configuration", lambda: config)
    star_identifier_module.reset_gaia_circuit_breaker()
    yield
    star_identifier_module.reset_gaia_circuit_breaker()


def test_a_result_of_exactly_the_row_limit_is_marked_as_cut_short(monkeypatch: pytest.MonkeyPatch) -> None:
    """A query that returns exactly the limit sets ``row_limit_reached``."""
    row_limit = star_identifier_module.GAIA_ROW_LIMIT
    fake_gaia(monkeypatch, row_count=row_limit)

    table, _ = StarIdentifier._query_gaia_region(CENTRE_RA_DEG, CENTRE_DEC_DEG, 0.5)

    assert table is not None
    assert len(table) == row_limit
    assert table.meta["row_limit_reached"] is True


def test_a_result_under_the_row_limit_is_not_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    """One row fewer than the limit is a complete answer."""
    fake_gaia(monkeypatch, row_count=star_identifier_module.GAIA_ROW_LIMIT - 1)

    table, _ = StarIdentifier._query_gaia_region(CENTRE_RA_DEG, CENTRE_DEC_DEG, 0.5)

    assert table is not None
    assert not table.meta.get("row_limit_reached")


def test_the_row_limit_is_raised_above_the_old_ten_thousand() -> None:
    """The old limit of 10,000 rows truncated dense fields."""
    assert star_identifier_module.GAIA_ROW_LIMIT > 10_000


def test_the_query_has_a_magnitude_limit_and_returns_the_brightest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ADQL names the limit, the row cap and a brightness ordering."""
    queries = fake_gaia(monkeypatch, row_count=20)

    StarIdentifier._query_gaia_region(CENTRE_RA_DEG, CENTRE_DEC_DEG, 0.5, max_magnitude=16.5)

    assert len(queries) == 1
    assert "phot_g_mean_mag < 16.5" in queries[0]
    assert f"TOP {star_identifier_module.GAIA_ROW_LIMIT}" in queries[0]
    assert "ORDER BY phot_g_mean_mag ASC" in queries[0]


def test_a_truncated_result_puts_a_flag_on_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The matching step copies the truncation onto the identifier's flags."""
    fake_gaia(monkeypatch, row_count=star_identifier_module.GAIA_ROW_LIMIT)
    config = MagicMock()
    config.get_value.return_value = None
    identifier = StarIdentifier(config=config)
    detection = StellarObject(id="Star_1", name="Star_1")
    sky_positions = {id(detection): (CENTRE_RA_DEG, CENTRE_DEC_DEG)}

    identifier._match_stars_against_gaia(
        [detection], sky_positions, CENTRE_RA_DEG, CENTRE_DEC_DEG, None, 100, 100, 0.5
    )

    assert star_identifier_module.FLAG_GAIA_ROW_LIMIT_REACHED in identifier.astrometry_flags


def test_the_frame_derived_limit_reaches_the_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """A limit passed to the matching step is the one in the query."""
    queries = fake_gaia(monkeypatch, row_count=20)
    config = MagicMock()
    config.get_value.return_value = None
    identifier = StarIdentifier(config=config)
    detection = StellarObject(id="Star_1", name="Star_1")
    sky_positions = {id(detection): (CENTRE_RA_DEG, CENTRE_DEC_DEG)}

    identifier._match_stars_against_gaia(
        [detection], sky_positions, CENTRE_RA_DEG, CENTRE_DEC_DEG, None, 100, 100, 0.5, magnitude_limit=15.5
    )

    assert "phot_g_mean_mag < 15.5" in queries[0]


def test_cached_stars_fainter_than_the_limit_are_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cache has no depth of its own, so the same limit applies to it."""
    from astrometricslib.drivers import catalog_store
    from astrometricslib.foundation.config import get_configuration

    config = get_configuration()
    (config.get_library_path() / "catalogs").mkdir(parents=True, exist_ok=True)
    catalog_store.insert_gaia_sources(
        config,
        [
            (f"{index}", CENTRE_RA_DEG + index * 1e-4, CENTRE_DEC_DEG, mag, f"Gaia DR3 {index}")
            for index, mag in enumerate([10.0, 11.0, 12.0, 13.0, 14.0, 17.0, 19.5, 0.0])
        ],
    )

    cached = StarIdentifier._query_gaia_region_from_cache(config, CENTRE_RA_DEG, CENTRE_DEC_DEG, 0.5, 18.0)

    assert cached is not None
    magnitudes = sorted(float(value) for value in cached[0]["phot_g_mean_mag"])
    # The 19.5 star is dropped. The star stored as magnitude 0 is kept.
    assert magnitudes == [0.0, 10.0, 11.0, 12.0, 13.0, 14.0, 17.0]


def make_star(flux: float, magnitude: float | None) -> StellarObject:
    """Build a detected star with a flux and maybe a catalog magnitude.

    Parameters
    ----------
    flux : `float`
        The measured flux.
    magnitude : `float` or `None`
        The catalog magnitude. `None` leaves the star unidentified.

    Returns
    -------
    star : `StellarObject`
        The star.
    """
    star = StellarObject(id="s", name="s", flux=flux)
    if magnitude is not None:
        star.magnitude = magnitude
        star.is_catalog_identified = True
    return star


def stars_with_depth(faintest_magnitude: float, matched_count: int = 6) -> list[StellarObject]:
    """Build detections whose faintest star has a given magnitude.

    The matched stars (magnitudes 5.0, 5.5, ...) follow ``magnitude = 24 -
    2.5 log10(flux)``. One extra unmatched star is the faintest.

    Parameters
    ----------
    faintest_magnitude : `float`
        The magnitude of the faintest detection under that zero point.
    matched_count : `int`, optional
        How many catalog-matched stars to include.

    Returns
    -------
    stars : `list` [`StellarObject`]
        The detections.
    """

    def flux_of(magnitude: float) -> float:
        """Invert the magnitude relation.

        Parameters
        ----------
        magnitude : `float`
            A magnitude under the test zero point.

        Returns
        -------
        flux : `float`
            The flux that gives the magnitude.
        """
        return float(10 ** ((ZERO_POINT - magnitude) / 2.5))

    matched = [make_star(flux_of(5.0 + 0.5 * index), 5.0 + 0.5 * index) for index in range(matched_count)]
    return [*matched, make_star(flux_of(faintest_magnitude), None)]


@pytest.mark.parametrize(
    ("faintest", "expected"),
    [
        (16.0, 17.0),
        (18.5, 19.5),
        (11.0, 14.0),
        (22.0, 20.0),
    ],
)
def test_the_limit_is_the_faintest_detection_plus_one_magnitude_within_clamps(
    faintest: float, expected: float
) -> None:
    """Depth plus 1 mag, kept between the minimum and maximum limits."""
    limit = star_identifier_module.estimate_gaia_magnitude_limit(stars_with_depth(faintest))

    assert limit == pytest.approx(expected, abs=1e-6)


def test_too_few_matched_stars_gives_no_estimate() -> None:
    """With fewer than five identified stars, the caller uses the default."""
    stars = stars_with_depth(16.0, matched_count=star_identifier_module.MINIMUM_STARS_FOR_DEPTH_ESTIMATE - 1)

    assert star_identifier_module.estimate_gaia_magnitude_limit(stars) is None
    assert star_identifier_module.GAIA_DEFAULT_MAGNITUDE_LIMIT == pytest.approx(18.0)


def test_a_star_without_a_flux_is_ignored_by_the_estimate() -> None:
    """A zero or missing flux cannot give a magnitude."""
    stars = [*stars_with_depth(16.0), make_star(0.0, None), StellarObject(id="x", name="x")]

    assert star_identifier_module.estimate_gaia_magnitude_limit(stars) == pytest.approx(17.0, abs=1e-6)
