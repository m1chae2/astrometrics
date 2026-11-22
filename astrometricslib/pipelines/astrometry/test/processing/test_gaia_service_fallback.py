"""Purpose: Tests for the order of Gaia services and the shallower retries.

Description: On 2026-10-10 the ESA Gaia archive timed out on the pipeline's
G < 18 query for M 13 while VizieR returned the same stars in 10 s, and every
remote lookup of 22 of 23 targets had failed. Each Gaia sky tile is now asked
of the ESA archive first, VizieR second, and VizieR again at brighter limits
(`star_identifier.make_gaia_tile_fetcher`). These tests check that order,
that the depth actually fetched is the one reported, how the circuit breaker
counts, and that VizieR's columns are mapped to the archive's. They use fake
services, so no network is needed.
"""

import socket
import sys
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import requests
from astropy.table import Table

from astrometricslib._internal.drivers import gaia_cone_driver
from astrometricslib._internal.drivers.gaia_cone_driver import (
    query_gaia_cone_vizier as real_query_gaia_cone_vizier,
)
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.pipelines.astrometry.processing import star_identifier as star_identifier_module

CENTRE_RA_DEG = 250.445
CENTRE_DEC_DEG = 36.157
TILE = 12345


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
        "query_gaia_tile_vizier",
        lambda level, tile, have, magnitude, row_limit, timeout: answer("vizier", magnitude),
    )
    monkeypatch.setattr(
        gaia_cone_driver,
        "query_gaia_tile_esa",
        lambda level, tile, have, magnitude, row_limit: answer("esa", magnitude),
    )
    star_identifier_module.reset_gaia_circuit_breaker()
    star_identifier_module.reset_gaia_query_statistics()
    yield state
    star_identifier_module.reset_gaia_circuit_breaker()
    star_identifier_module.reset_gaia_query_statistics()


def _fetch() -> gaia_cone_driver.TileFetch | None:
    """Ask for the test tile at G < 18, as the star identifier does.

    Returns
    -------
    answer : `TileFetch` or `None`
        The answer, or `None` when every service failed or no fetcher was
        given.
    """
    fetch, _ = star_identifier_module.make_gaia_tile_fetcher()
    if fetch is None:
        return None
    try:
        return fetch(7, TILE, 0.0, 18.0)
    except ExternalServiceError:
        return None


def test_the_archive_answers_first_and_vizier_is_not_asked(services: dict[str, Any]) -> None:
    """An ESA answer at full depth ends the search."""
    services["answers"]["esa", 18.0] = _stars()

    table = _fetch()

    assert table is not None
    assert services["calls"] == [("esa", 18.0)]
    assert table.service == "ESA Gaia archive"
    assert table.complete_to_g == pytest.approx(18.0)


def test_vizier_answers_when_the_archive_fails_and_no_failure_counts(services: dict[str, Any]) -> None:
    """VizieR is second choice; an ESA failure alone does not count."""
    services["answers"]["vizier", 18.0] = _stars()

    table = _fetch()

    assert table is not None
    assert services["calls"] == [("esa", 18.0), ("vizier", 18.0)]
    assert table.service == "VizieR"
    assert table.complete_to_g == pytest.approx(18.0)
    statistics = star_identifier_module.get_gaia_query_statistics()
    assert statistics["failed"] == 0
    assert not statistics["circuit_breaker_tripped"]


def test_a_connection_failure_rules_out_the_brighter_limits(services: dict[str, Any]) -> None:
    """A connection failure at G < 18 rules out G < 17 and G < 16."""
    services["answers"]["vizier", 17.0] = _stars()

    table = _fetch()

    assert table is None
    assert services["calls"] == [("esa", 18.0), ("vizier", 18.0)]


def test_a_read_timeout_keeps_the_brighter_limits(
    services: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow VizieR (reached, no answer in time) is asked again at G < 17."""
    services["answers"]["vizier", 17.0] = _stars()
    real_runner = star_identifier_module._run_with_daemon_thread_timeout
    slow_calls: list[float] = []

    def slow_at_full_depth(query: Any, timeout_seconds: float) -> Any:
        """Time out the first VizieR call, run the rest normally.

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
        if timeout_seconds > star_identifier_module.GAIA_ESA_TIMEOUT_SECONDS and not slow_calls:
            slow_calls.append(timeout_seconds)
            services["calls"].append(("vizier", 18.0))
            raise ExternalServiceError("Timed out")
        return real_runner(query, timeout_seconds)

    monkeypatch.setattr(star_identifier_module, "_run_with_daemon_thread_timeout", slow_at_full_depth)

    table = _fetch()

    assert table is not None
    assert services["calls"][0] == ("esa", 18.0)
    assert ("vizier", 18.0) in services["calls"]
    assert ("vizier", 17.0) in services["calls"]
    assert table.complete_to_g == pytest.approx(17.0)
    assert table.service == "VizieR"


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
    assert services["calls"] == [("esa", 18.0), ("vizier", 18.0)]
    statistics = star_identifier_module.get_gaia_query_statistics()
    assert statistics["attempted"] == 1
    assert statistics["failed"] == 1


def test_later_fetches_in_the_same_run_skip_the_failed_services(
    services: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """A second fetch makes no remote call, and each service is logged once."""
    with caplog.at_level("INFO"):
        _fetch()
        services["calls"].clear()
        assert _fetch() is None

    assert services["calls"] == []
    failure_lines = [record for record in caplog.records if "Gaia tile download from" in record.getMessage()]
    assert len(failure_lines) == 2


def test_a_new_run_tries_the_services_again(services: dict[str, Any]) -> None:
    """Resetting the run's statistics clears the unreachable services."""
    _fetch()
    services["calls"].clear()
    services["answers"]["vizier", 18.0] = _stars()

    star_identifier_module.reset_gaia_query_statistics()
    table = _fetch()

    assert table is not None
    assert services["calls"] == [("esa", 18.0), ("vizier", 18.0)]


def test_a_working_network_asks_the_same_questions_as_before(services: dict[str, Any]) -> None:
    """With the archive up, a new run asks it first again and nothing else."""
    services["answers"]["vizier", 18.0] = _stars()
    _fetch()
    services["calls"].clear()
    star_identifier_module.reset_gaia_query_statistics()
    services["answers"]["esa", 18.0] = _stars()
    _fetch()

    assert services["calls"] == [("esa", 18.0)]


def test_an_open_breaker_refuses_every_later_tile(services: dict[str, Any]) -> None:
    """After the breaker opens, no fetcher is given and nothing is sent."""
    for _ in range(star_identifier_module.GAIA_CONSECUTIVE_FAILURE_LIMIT):
        star_identifier_module._record_gaia_failure("test")

    assert _fetch() is None
    assert services["calls"] == []


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
    table = real_query_gaia_cone_vizier(CENTRE_RA_DEG, CENTRE_DEC_DEG, 0.3, 18.0, 10, 30)

    assert list(table.colnames) == list(gaia_cone_driver.GAIA_CONE_COLUMNS)
    assert table["source_id"][0] == "1327870404636047872"
    assert table["designation"][0] == "Gaia DR3 1327870404636047872"
    assert table["ra"][0] == pytest.approx(249.13192832082)
    assert table["phot_g_mean_mag"][0] == pytest.approx(9.5)
    assert np.isnan(table["pmra"][0])
    assert table["pmdec"][0] == pytest.approx(-27.855)
