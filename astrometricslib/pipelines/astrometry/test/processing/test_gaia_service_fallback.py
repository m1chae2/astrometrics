"""Purpose: Tests for the order of Gaia services and the shallower retries.

Description: On 2026-10-10 the ESA Gaia archive timed out on the pipeline's
G < 18 query for M 13 while VizieR returned the same stars in 10 s, and every
remote lookup of 22 of 23 targets had failed. The pipeline now asks VizieR
first, the ESA archive second, and VizieR again at brighter limits. These
tests check that order, that the depth actually fetched is the one recorded
in the cache and flagged on the run, and that VizieR's columns are mapped to
the archive's. They use fake services, so no network is needed.
"""

import socket
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import requests
from astropy.table import Table

from astrometricslib._internal.drivers import catalog_store, gaia_cone_driver
from astrometricslib._internal.drivers.gaia_cone_driver import (
    query_gaia_cone_vizier as real_query_gaia_cone_vizier,
)
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.pipelines.astrometry.processing import star_identifier as star_identifier_module
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier

CENTRE_RA_DEG = 250.445
CENTRE_DEC_DEG = 36.157
RADIUS_DEG = 0.3


def _stars(count: int = 5) -> Table:
    """Build a small Gaia table in the driver's column layout.

    Parameters
    ----------
    count : `int`, optional
        How many stars.

    Returns
    -------
    table : `astropy.table.Table`
        Stars near the test field, brightest first.
    """
    return Table(
        [
            [str(index) for index in range(count)],
            CENTRE_RA_DEG + np.arange(count) * 1e-3,
            np.full(count, CENTRE_DEC_DEG),
            np.linspace(10.0, 14.0, count),
            [f"Gaia DR3 {index}" for index in range(count)],
            np.zeros(count),
            np.zeros(count),
        ],
        names=gaia_cone_driver.GAIA_CONE_COLUMNS,
    )


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Replace both Gaia services with fakes that a test can steer.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace the two driver functions.

    Yields
    ------
    state : `dict`
        ``calls`` collects ``(service, magnitude)`` for each request;
        ``answers`` maps ``(service, magnitude)`` to a table, and a request
        with no answer fails with a connection error.
    """
    state: dict[str, Any] = {"calls": [], "answers": {}}

    def answer(service: str, magnitude: float) -> Table:
        """Record the request and return its answer or fail.

        Parameters
        ----------
        service : `str`
            ``"vizier"`` or ``"esa"``.
        magnitude : `float`
            The magnitude limit asked for.

        Returns
        -------
        table : `astropy.table.Table`
            The steered answer.

        Raises
        ------
        ConnectionError
            When the test set no answer for this request.
        """
        state["calls"].append((service, magnitude))
        if (service, magnitude) not in state["answers"]:
            raise ConnectionError(f"{service} is down")
        return state["answers"][service, magnitude].copy()

    monkeypatch.setattr(
        gaia_cone_driver,
        "query_gaia_cone_vizier",
        lambda ra, dec, radius, magnitude, row_limit, timeout: answer("vizier", magnitude),
    )
    monkeypatch.setattr(
        gaia_cone_driver,
        "query_gaia_cone_esa",
        lambda ra, dec, radius, magnitude, row_limit: answer("esa", magnitude),
    )
    star_identifier_module.reset_gaia_circuit_breaker()
    star_identifier_module.reset_gaia_query_statistics()
    yield state
    star_identifier_module.reset_gaia_circuit_breaker()
    star_identifier_module.reset_gaia_query_statistics()


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Give the test an empty Gaia cache in its own folder.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The test's own folder, used as the library path.
    monkeypatch : `pytest.MonkeyPatch`
        Used to point the configuration at that folder.

    Returns
    -------
    config : `AppConfiguration`
        Settings whose library path is the test folder.
    """
    import astrometricslib.foundation.config as config_loader_module
    from astrometricslib.foundation.config import AppConfiguration

    app_config = AppConfiguration()
    app_config.update_config({"Image Library": {"path": str(tmp_path)}})
    monkeypatch.setattr(config_loader_module, "get_configuration", lambda: app_config)
    (app_config.get_library_path() / "catalogs").mkdir(parents=True, exist_ok=True)
    return app_config


def _fetch() -> Table | None:
    """Ask for the test field at G < 18 with a large row limit.

    Returns
    -------
    table : `astropy.table.Table` or `None`
        What `_fetch_gaia_cone` returned.
    """
    return star_identifier_module._fetch_gaia_cone(
        CENTRE_RA_DEG, CENTRE_DEC_DEG, RADIUS_DEG, 18.0, 1000, "test"
    )


def test_vizier_answers_first_and_the_archive_is_not_asked(services: dict[str, Any]) -> None:
    """A VizieR answer at full depth ends the search."""
    services["answers"]["vizier", 18.0] = _stars()

    table = _fetch()

    assert table is not None
    assert services["calls"] == [("vizier", 18.0)]
    assert table.meta["gaia_service"] == "VizieR"
    assert table.meta["magnitude_limit"] == pytest.approx(18.0)
    assert table.meta["depth_reduced"] is False


def test_the_archive_answers_when_vizier_fails(services: dict[str, Any]) -> None:
    """The ESA archive is the second choice at full depth."""
    services["answers"]["esa", 18.0] = _stars()

    table = _fetch()

    assert table is not None
    assert services["calls"] == [("vizier", 18.0), ("esa", 18.0)]
    assert table.meta["gaia_service"] == "ESA Gaia archive"
    assert table.meta["depth_reduced"] is False


def test_a_connection_failure_rules_out_the_brighter_limits(services: dict[str, Any]) -> None:
    """A connection failure at G < 18 rules out G < 17 and G < 16."""
    services["answers"]["vizier", 17.0] = _stars()

    table = _fetch()

    assert table is None
    assert services["calls"] == [("vizier", 18.0), ("esa", 18.0)]


def test_a_read_timeout_keeps_the_brighter_limits(
    services: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow VizieR (reached, no answer in time) is asked again at G < 17."""
    services["answers"]["vizier", 17.0] = _stars()
    real_runner = star_identifier_module._run_with_daemon_thread_timeout

    def slow_at_full_depth(query: Any, timeout_seconds: float) -> Any:
        """Time out the first VizieR call, run every other call normally.

        Parameters
        ----------
        query : `Callable`
            The query to run.
        timeout_seconds : `float`
            The deadline, ignored here.

        Returns
        -------
        table : `astropy.table.Table`
            The query's answer.

        Raises
        ------
        ExternalServiceError
            For the first VizieR call.
        """
        if not services["calls"]:
            services["calls"].append(("vizier", 18.0))
            raise ExternalServiceError("Timed out")
        return real_runner(query, timeout_seconds)

    monkeypatch.setattr(star_identifier_module, "_run_with_daemon_thread_timeout", slow_at_full_depth)

    table = _fetch()

    assert table is not None
    assert services["calls"][0] == ("vizier", 18.0)
    assert ("vizier", 17.0) in services["calls"]
    assert table.meta["depth_reduced"] is True
    assert table.meta["gaia_service"] == "VizieR"


def test_a_requests_read_timeout_is_not_a_connection_failure() -> None:
    """Read timeouts keep the service; connect and DNS failures do not."""
    is_failure = star_identifier_module._is_connection_failure

    assert not is_failure(requests.exceptions.ReadTimeout("slow"))
    assert not is_failure(TimeoutError("slow"))
    assert not is_failure(ValueError("bad reply"))
    assert is_failure(requests.exceptions.ConnectTimeout("no connect"))
    assert is_failure(requests.exceptions.ConnectionError("refused"))
    assert is_failure(ConnectionRefusedError("refused"))
    assert is_failure(socket.gaierror("no such name"))
    wrapped = OSError("wrapped")
    wrapped.__cause__ = socket.gaierror("no such name")
    assert is_failure(wrapped)


def test_every_failure_gives_none_and_counts_once(services: dict[str, Any]) -> None:
    """When every attempt fails, the answer is None and one failure counts."""
    table = _fetch()

    assert table is None
    assert services["calls"] == [("vizier", 18.0), ("esa", 18.0)]
    statistics = star_identifier_module.get_gaia_query_statistics()
    assert statistics["attempted"] == 1
    assert statistics["failed"] == 1


def test_later_fetches_in_the_same_run_skip_the_failed_services(
    services: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """A second fetch makes no remote call, and each service warned once."""
    with caplog.at_level("WARNING"):
        _fetch()
        services["calls"].clear()
        assert _fetch() is None

    assert services["calls"] == []
    failure_warnings = [record for record in caplog.records if "Gaia test from" in record.getMessage()]
    assert len(failure_warnings) == 2


def test_a_new_run_tries_the_services_again(services: dict[str, Any]) -> None:
    """Resetting the run's statistics clears the unreachable services."""
    _fetch()
    services["calls"].clear()
    services["answers"]["vizier", 18.0] = _stars()

    star_identifier_module.reset_gaia_query_statistics()
    table = _fetch()

    assert table is not None
    assert services["calls"] == [("vizier", 18.0)]


def test_a_working_network_asks_the_same_questions_as_before(services: dict[str, Any]) -> None:
    """With VizieR down and the archive up, the order is unchanged."""
    services["answers"]["esa", 18.0] = _stars()

    _fetch()
    services["calls"].clear()
    star_identifier_module.reset_gaia_query_statistics()
    services["answers"]["vizier", 18.0] = _stars()
    _fetch()

    assert services["calls"] == [("vizier", 18.0)]


def _record_cached_download(config: Any, radius_deg: float, complete: bool) -> None:
    """Put stars and a region record for the test field into the cache.

    Parameters
    ----------
    config : `AppConfiguration`
        Settings pointing at the test's cache folder.
    radius_deg : `float`
        The recorded download's radius.
    complete : `bool`
        If `False`, no region record is written, as for stars left by a
        download that was never recorded.
    """
    stars = _stars()
    catalog_store.insert_gaia_sources(
        config,
        [
            (
                str(row["source_id"]),
                float(row["ra"]),
                float(row["dec"]),
                float(row["phot_g_mean_mag"]),
                "x",
                0.0,
                0.0,
            )
            for row in stars
        ],
    )
    if complete:
        catalog_store.record_gaia_region(
            config, CENTRE_RA_DEG, CENTRE_DEC_DEG, radius_deg, 18.0, row_limit_hit=False
        )


def test_a_cached_field_covering_the_cone_makes_no_remote_call(services: dict[str, Any], config: Any) -> None:
    """A recorded download holding the whole cone is used; nothing is sent."""
    _record_cached_download(config, radius_deg=0.6, complete=True)

    table, coordinates = StarIdentifier._query_gaia_region(CENTRE_RA_DEG, CENTRE_DEC_DEG, RADIUS_DEG, 18.0)

    assert table is not None and coordinates is not None
    assert len(table) == 5
    assert services["calls"] == []


def test_stars_in_an_unrecorded_cache_are_not_used_when_every_service_fails(
    services: dict[str, Any], config: Any
) -> None:
    """A cache with no complete download never stands in for the network."""
    _record_cached_download(config, radius_deg=0.6, complete=False)

    table, coordinates = StarIdentifier._query_gaia_region(CENTRE_RA_DEG, CENTRE_DEC_DEG, RADIUS_DEG, 18.0)

    assert services["calls"] == [("vizier", 18.0), ("esa", 18.0)]
    assert table is None and coordinates is None


def test_the_cache_records_the_depth_fetched_not_the_depth_asked(
    services: dict[str, Any], config: Any
) -> None:
    """A G < 17 answer to a G < 18 request is not reused as complete to 18."""
    shallower = _stars()
    shallower.meta["magnitude_limit"] = 17.0
    shallower.meta["row_limit_reached"] = False

    StarIdentifier._cache_gaia_results(
        config, shallower, region=(CENTRE_RA_DEG, CENTRE_DEC_DEG, RADIUS_DEG, 17.0)
    )

    assert catalog_store.is_gaia_region_complete(config, CENTRE_RA_DEG, CENTRE_DEC_DEG, RADIUS_DEG, 17.0)
    assert not catalog_store.is_gaia_region_complete(config, CENTRE_RA_DEG, CENTRE_DEC_DEG, RADIUS_DEG, 18.0)


def test_vizier_columns_are_mapped_to_the_archive_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """VizieR's column names are mapped to the Gaia archive's."""
    vizier_table = Table({
        "Source": [1327870404636047872],
        "RA_ICRS": [249.13192832082],
        "DE_ICRS": [36.03980821366],
        "Gmag": [9.5],
        "pmRA": np.ma.masked_array([0.0], mask=[True]),
        "pmDE": [-27.855],
    })

    class FakeVizier:
        """Stands in for astroquery's Vizier and returns one row."""

        def __init__(self, **keywords: object) -> None:
            """Keep the options, as the real client does.

            Parameters
            ----------
            **keywords
                The client options.
            """
            self.keywords = keywords

        def query_region(self, *_arguments: object, **_keywords: object) -> list[Table]:
            """Return the one-row table.

            Returns
            -------
            tables : `list` [`astropy.table.Table`]
                One table per catalog.
            """
            return [vizier_table]

    # Another test may have replaced astroquery itself, so the fake is
    # registered as the module the driver imports.
    monkeypatch.setitem(sys.modules, "astroquery.vizier", SimpleNamespace(Vizier=FakeVizier))

    # The real function, imported before the test suite's fixtures replace it.
    table = real_query_gaia_cone_vizier(CENTRE_RA_DEG, CENTRE_DEC_DEG, RADIUS_DEG, 18.0, 10, 30)

    assert list(table.colnames) == list(gaia_cone_driver.GAIA_CONE_COLUMNS)
    assert table["source_id"][0] == "1327870404636047872"
    assert table["designation"][0] == "Gaia DR3 1327870404636047872"
    assert table["ra"][0] == pytest.approx(249.13192832082)
    assert table["phot_g_mean_mag"][0] == pytest.approx(9.5)
    assert np.isnan(table["pmra"][0])
    assert table["pmdec"][0] == pytest.approx(-27.855)
