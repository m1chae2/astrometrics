"""Purpose: Test the Gaia reddening driver without a network.

Description: `AstroqueryGaiaReddeningDriver` asks the Gaia archive for a
star's GSP-Phot extinction and converts it to E(B-V). These tests give the
driver a function in place of the archive, so nothing leaves the machine. They
check the query text that would be sent, the two conversions and their
documented factors, the preference for E(BP-RP) over A(G), masked and negative
values, the in-memory cache (including "no value"), the handling of failures
and timeouts, the batched prefetch (one query for many stars, chunks, one
bounded wait on failure, the same values as one-star queries), and that
`Drivers` builds this driver by default and returns a caller's own driver
when one is given.
"""

import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.table import MaskedColumn, Table

from astrometricslib._internal.drivers.astroquery_gaia_reddening_driver import (
    A_G_PER_E_B_V,
    CONSECUTIVE_FAILURE_LIMIT,
    E_BP_RP_PER_E_B_V,
    GAIA_BATCH_SIZE,
    GAIA_REDDENING_TABLE,
    AstroqueryGaiaReddeningDriver,
    build_reddening_batch_query,
    build_reddening_query,
    estimate_from_gaia_values,
)
from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib._internal.drivers.interfaces.reddening_driver import ReddeningDriver, ReddeningEstimate
from astrometricslib.foundation.errors import ExternalServiceError

SOURCE_ID = 1328045433153485824


class FakeReddening(ReddeningDriver):
    """A reddening source with fixed answers, for tests that need no Gaia.

    Attributes
    ----------
    calls : `list` [`int`]
        The source ids asked for, in order.
    """

    def __init__(self, answers: dict[int, ReddeningEstimate | None]) -> None:
        """Store the answers by Gaia source id."""
        self._answers = answers
        self.calls: list[int] = []

    def get_reddening(self, gaia_source_id: int) -> ReddeningEstimate | None:
        """Record the call and give the stored answer.

        Returns
        -------
        estimate : `ReddeningEstimate` or `None`
            The stored answer, or `None` for an id that was not stored.
        """
        self.calls.append(gaia_source_id)
        return self._answers.get(gaia_source_id)


def gaia_table(e_bp_rp: object, a_g: object, source_id: int = SOURCE_ID) -> Table:
    """Build a one-row table shaped like the Gaia archive's reply.

    Parameters
    ----------
    e_bp_rp : `object`
        The ``ebpminrp_gspphot`` value, or `None` for a masked cell.
    a_g : `object`
        The ``ag_gspphot`` value, or `None` for a masked cell.
    source_id : `int`, optional
        The ``source_id`` value.

    Returns
    -------
    table : `astropy.table.Table`
        The reply.
    """
    return Table({
        "source_id": [source_id],
        "ebpminrp_gspphot": MaskedColumn([e_bp_rp if e_bp_rp is not None else 0.0], mask=[e_bp_rp is None]),
        "ag_gspphot": MaskedColumn([a_g if a_g is not None else 0.0], mask=[a_g is None]),
    })


# ------------------------------------------------------------ the query


def test_the_query_asks_the_astrophysical_parameters_table_for_one_source() -> None:
    """Verify the ADQL names the table, the three columns and the source id."""
    query = build_reddening_query(SOURCE_ID)

    assert query.split() == [
        "SELECT",
        "source_id,",
        "ebpminrp_gspphot,",
        "ag_gspphot",
        "FROM",
        "gaiadr3.astrophysical_parameters",
        "WHERE",
        "source_id",
        "=",
        str(SOURCE_ID),
    ]
    assert GAIA_REDDENING_TABLE == "gaiadr3.astrophysical_parameters"


def test_the_query_accepts_a_numpy_integer() -> None:
    """Verify an id read from a NumPy array builds the same query."""
    assert build_reddening_query(np.int64(SOURCE_ID)) == build_reddening_query(SOURCE_ID)


@pytest.mark.parametrize("bad_id", [0, -5, 1.5, "1328045433153485824", "1 OR 1=1", True, None])
def test_the_query_refuses_anything_but_a_positive_whole_number(bad_id: object) -> None:
    """Verify text, floats, booleans and non-positive ids build no query."""
    with pytest.raises(ValueError, match="Gaia source id"):
        build_reddening_query(bad_id)  # type: ignore[arg-type]


# ----------------------------------------------------------- conversions


def test_the_factors_are_the_documented_casagrande_and_vandenberg_ratios() -> None:
    """Verify E(BP-RP)/E(B-V) is 3.374 - 2.035 and A(G)/E(B-V) is 2.740."""
    assert pytest.approx(3.374 - 2.035) == E_BP_RP_PER_E_B_V
    assert pytest.approx(1.339) == E_BP_RP_PER_E_B_V
    assert pytest.approx(2.740) == A_G_PER_E_B_V


def test_e_bp_rp_is_divided_by_1_339() -> None:
    """Verify E(BP-RP) = 0.2678 gives E(B-V) = 0.2 and names the conversion."""
    estimate = estimate_from_gaia_values(SOURCE_ID, 0.2678, 0.5)

    assert estimate is not None
    assert estimate.ebv == pytest.approx(0.2)
    assert estimate.gaia_source_id == SOURCE_ID
    assert "E(BP-RP)" in estimate.source
    assert "1.339" in estimate.source


def test_a_g_is_the_fallback_when_e_bp_rp_is_missing() -> None:
    """Verify A(G) = 0.548 gives E(B-V) = 0.2 when E(BP-RP) is masked."""
    estimate = estimate_from_gaia_values(SOURCE_ID, np.ma.masked, 0.548)

    assert estimate is not None
    assert estimate.ebv == pytest.approx(0.2)
    assert "A(G)" in estimate.source
    assert "2.74" in estimate.source


def test_e_bp_rp_wins_when_both_are_present() -> None:
    """Verify the colour excess is preferred over the G-band dimming."""
    estimate = estimate_from_gaia_values(SOURCE_ID, 0.1339, 5.0)

    assert estimate is not None
    assert estimate.ebv == pytest.approx(0.1)


@pytest.mark.parametrize(
    ("e_bp_rp", "a_g"),
    [(None, None), (np.nan, np.nan), (-0.01, -0.01), (np.inf, None), ("bad", None)],
)
def test_no_usable_value_gives_no_estimate(e_bp_rp: object, a_g: object) -> None:
    """Verify missing, NaN, negative and non-numeric values give `None`."""
    assert estimate_from_gaia_values(SOURCE_ID, e_bp_rp, a_g) is None


def test_a_negative_colour_excess_falls_back_to_a_g() -> None:
    """Verify an unusable E(BP-RP) does not hide a usable A(G)."""
    estimate = estimate_from_gaia_values(SOURCE_ID, -0.02, 0.274)

    assert estimate is not None
    assert estimate.ebv == pytest.approx(0.1)


def test_zero_reddening_is_a_value_not_a_missing_value() -> None:
    """Verify E(BP-RP) = 0 gives E(B-V) = 0, not `None`."""
    estimate = estimate_from_gaia_values(SOURCE_ID, 0.0, 0.0)

    assert estimate is not None
    assert estimate.ebv == pytest.approx(0.0)


# ------------------------------------------------------------ the driver


def test_the_driver_sends_the_built_query_and_converts_the_reply() -> None:
    """Verify the stand-in for Gaia receives the exact query text."""
    sent: list[str] = []

    def fake_archive(query: str) -> Table:
        """Record the query and answer with one row.

        Returns
        -------
        reply : `astropy.table.Table`
            A reply with E(BP-RP) = 0.2678.
        """
        sent.append(query)
        return gaia_table(0.2678, None)

    estimate = AstroqueryGaiaReddeningDriver(run_query=fake_archive).get_reddening(SOURCE_ID)

    assert sent == [build_reddening_query(SOURCE_ID)]
    assert estimate is not None
    assert estimate.ebv == pytest.approx(0.2)
    assert estimate.gaia_source_id == SOURCE_ID


def test_the_driver_matches_column_names_without_regard_to_case() -> None:
    """Verify a reply with upper-case column names is read."""
    reply = Table({"SOURCE_ID": [SOURCE_ID], "EBPMINRP_GSPPHOT": [0.1339], "AG_GSPPHOT": [0.3]})

    estimate = AstroqueryGaiaReddeningDriver(run_query=lambda _query: reply).get_reddening(SOURCE_ID)

    assert estimate is not None
    assert estimate.ebv == pytest.approx(0.1)


def test_the_driver_remembers_answers_including_no_value() -> None:
    """Verify a repeated question does not query again, even for `None`."""
    calls: list[str] = []

    def fake_archive(query: str) -> Table:
        """Count the calls and answer with a masked row.

        Returns
        -------
        reply : `astropy.table.Table`
            A reply whose extinction cells are masked.
        """
        calls.append(query)
        return gaia_table(None, None)

    driver = AstroqueryGaiaReddeningDriver(run_query=fake_archive)

    assert driver.get_reddening(SOURCE_ID) is None
    assert driver.get_reddening(SOURCE_ID) is None
    assert len(calls) == 1


def test_an_empty_reply_means_the_star_has_no_astrophysical_parameters() -> None:
    """Verify zero rows gives `None`, not an error."""
    empty = Table({"source_id": [], "ebpminrp_gspphot": [], "ag_gspphot": []})

    assert AstroqueryGaiaReddeningDriver(run_query=lambda _query: empty).get_reddening(SOURCE_ID) is None
    assert AstroqueryGaiaReddeningDriver(run_query=lambda _query: None).get_reddening(SOURCE_ID) is None


def test_a_reply_without_the_extinction_columns_is_an_error() -> None:
    """Verify a malformed reply raises `ExternalServiceError`."""
    reply = Table({"source_id": [SOURCE_ID], "teff_gspphot": [5800.0]})

    with pytest.raises(ExternalServiceError, match="extinction columns"):
        AstroqueryGaiaReddeningDriver(run_query=lambda _query: reply).get_reddening(SOURCE_ID)


def test_a_failed_query_is_raised_as_an_external_service_error_and_not_remembered() -> None:
    """Verify a network error becomes `ExternalServiceError`, then retries."""
    attempts: list[str] = []

    def flaky_archive(query: str) -> Table:
        """Fail on the first call and succeed on the second.

        Returns
        -------
        reply : `astropy.table.Table`
            A reply with E(BP-RP) = 0.1339, on the second call.

        Raises
        ------
        OSError
            On the first call.
        """
        attempts.append(query)
        if len(attempts) == 1:
            raise OSError("connection reset")
        return gaia_table(0.1339, None)

    driver = AstroqueryGaiaReddeningDriver(run_query=flaky_archive)

    with pytest.raises(ExternalServiceError, match="failed"):
        driver.get_reddening(SOURCE_ID)
    estimate = driver.get_reddening(SOURCE_ID)

    assert estimate is not None
    assert len(attempts) == 2


def test_the_driver_stops_asking_after_repeated_failures() -> None:
    """Verify the failure limit makes the next call raise without a query."""
    attempts: list[str] = []

    def broken_archive(query: str) -> Table:
        """Fail every time.

        Raises
        ------
        OSError
            Always.
        """
        attempts.append(query)
        raise OSError("no route to host")

    driver = AstroqueryGaiaReddeningDriver(run_query=broken_archive)
    for offset in range(CONSECUTIVE_FAILURE_LIMIT):
        with pytest.raises(ExternalServiceError):
            driver.get_reddening(SOURCE_ID + offset)

    with pytest.raises(ExternalServiceError, match="switched off"):
        driver.get_reddening(SOURCE_ID + 100)
    assert len(attempts) == CONSECUTIVE_FAILURE_LIMIT


def test_a_query_that_does_not_answer_in_time_is_an_error() -> None:
    """Verify a stalled archive raises after the timeout instead of waiting."""
    release = threading.Event()

    def stalled_archive(_query: str) -> Table:
        """Wait until the test lets go.

        Returns
        -------
        reply : `astropy.table.Table`
            A reply, once released.
        """
        release.wait(timeout=5.0)
        return gaia_table(0.1, None)

    driver = AstroqueryGaiaReddeningDriver(run_query=stalled_archive, timeout_seconds=0.05)
    try:
        with pytest.raises(ExternalServiceError, match="timed out"):
            driver.get_reddening(SOURCE_ID)
    finally:
        release.set()


def test_the_default_query_function_uses_the_gaia_client(gaia_module: MagicMock) -> None:
    """Verify the built-in path calls ``Gaia.launch_job`` with the query.

    The Gaia client is replaced, so nothing is sent over the network.
    """
    fake_gaia = MagicMock()
    fake_gaia.launch_job.return_value.get_results.return_value = gaia_table(0.1339, None)
    gaia_module.Gaia = fake_gaia

    estimate = AstroqueryGaiaReddeningDriver().get_reddening(SOURCE_ID)

    fake_gaia.launch_job.assert_called_once_with(build_reddening_query(SOURCE_ID), dump_to_file=False)
    assert estimate is not None
    assert estimate.ebv == pytest.approx(0.1)


# ------------------------------------------------------------ Drivers


def test_drivers_builds_the_gaia_driver_by_default() -> None:
    """Verify an empty `Drivers` gives the built-in Gaia reddening driver."""
    assert isinstance(Drivers().reddening_or_default(), AstroqueryGaiaReddeningDriver)
    assert not Drivers().any_chosen


def test_drivers_gives_back_the_fake_a_caller_chose() -> None:
    """Verify a chosen reddening driver is returned and counts as chosen."""
    fake = FakeReddening({SOURCE_ID: ReddeningEstimate(ebv=0.3, source="fake", gaia_source_id=SOURCE_ID)})
    drivers = Drivers(reddening=fake)

    assert drivers.reddening_or_default() is fake
    assert drivers.any_chosen
    estimate = drivers.reddening_or_default().get_reddening(SOURCE_ID)
    assert estimate is not None
    assert estimate.ebv == pytest.approx(0.3)
    assert fake.calls == [SOURCE_ID]


# ------------------------------------------------------- the batched prefetch


def batch_table(rows: dict[int, tuple[float | None, float | None]]) -> Table:
    """Build a table shaped like the archive's reply to a batch query.

    Parameters
    ----------
    rows : `dict` [`int`, `tuple` [`float` or `None`, `float` or `None`]]
        For each source id, its E(BP-RP) and A(G); `None` is a masked cell.

    Returns
    -------
    table : `astropy.table.Table`
        The reply, with one row for each id.
    """
    ids = list(rows)
    return Table({
        "source_id": ids,
        "ebpminrp_gspphot": MaskedColumn(
            [rows[i][0] if rows[i][0] is not None else 0.0 for i in ids],
            mask=[rows[i][0] is None for i in ids],
        ),
        "ag_gspphot": MaskedColumn(
            [rows[i][1] if rows[i][1] is not None else 0.0 for i in ids],
            mask=[rows[i][1] is None for i in ids],
        ),
    })


def test_the_batch_query_asks_for_every_id_in_one_select() -> None:
    """Verify the batch ADQL has one IN list and refuses a bad id or no id."""
    query = build_reddening_batch_query([SOURCE_ID, SOURCE_ID + 1])

    assert query.endswith(f"WHERE source_id IN ({SOURCE_ID}, {SOURCE_ID + 1})")
    assert GAIA_REDDENING_TABLE in query
    with pytest.raises(ValueError, match="at least one"):
        build_reddening_batch_query([])
    with pytest.raises(ValueError, match="whole number"):
        build_reddening_batch_query([SOURCE_ID, "1; DROP TABLE x"])


def test_a_prefetch_asks_once_and_later_lookups_send_no_query() -> None:
    """Verify many stars cost one query; answers then come from the cache."""
    ids = [SOURCE_ID + offset for offset in range(5)]
    reply = {ids[0]: (0.134, None), ids[1]: (None, 0.274), ids[2]: (None, None)}
    queries: list[str] = []

    def archive(query: str) -> Table:
        """Record the query and answer for the stars Gaia holds.

        Returns
        -------
        reply : `astropy.table.Table`
            The rows for ids 0 to 2; ids 3 and 4 have no row.
        """
        queries.append(query)
        return batch_table(reply)

    driver = AstroqueryGaiaReddeningDriver(run_query=archive)
    driver.prefetch_reddening(ids)

    assert len(queries) == 1
    first, second, empty, absent = (driver.get_reddening(i) for i in ids[:4])
    assert first is not None and first.ebv == pytest.approx(0.134 / E_BP_RP_PER_E_B_V)
    assert second is not None and second.ebv == pytest.approx(0.274 / A_G_PER_E_B_V)
    assert empty is None
    assert absent is None
    assert len(queries) == 1


def test_a_prefetch_gives_the_values_of_one_star_queries() -> None:
    """Verify batch and one-star queries give identical estimates."""
    values = {SOURCE_ID: (0.2, 0.5), SOURCE_ID + 1: (None, 0.3), SOURCE_ID + 2: (0.0, None)}

    def archive(query: str) -> Table:
        """Answer a one-star or batch query from the same values.

        Returns
        -------
        reply : `astropy.table.Table`
            The rows for the ids named in the query.
        """
        wanted = [i for i in values if str(i) in query]
        return batch_table({i: values[i] for i in wanted})

    batched = AstroqueryGaiaReddeningDriver(run_query=archive)
    batched.prefetch_reddening(values)
    single = AstroqueryGaiaReddeningDriver(run_query=archive)

    for source_id in values:
        assert batched.get_reddening(source_id) == single.get_reddening(source_id)


def test_a_long_list_is_asked_for_in_chunks() -> None:
    """Verify more than `GAIA_BATCH_SIZE` ids make one query per chunk."""
    ids = list(range(1, 2 * GAIA_BATCH_SIZE + 2))
    queries: list[str] = []

    def archive(query: str) -> Table:
        """Record the query and answer with no rows.

        Returns
        -------
        reply : `astropy.table.Table`
            An empty reply.
        """
        queries.append(query)
        return Table()

    driver = AstroqueryGaiaReddeningDriver(run_query=archive)
    driver.prefetch_reddening(ids)

    assert len(queries) == 3
    assert max(len(query.split("(")[1].split(",")) for query in queries) <= GAIA_BATCH_SIZE
    assert driver.get_reddening(ids[-1]) is None
    assert len(queries) == 3


def test_ids_already_cached_are_not_asked_for_again() -> None:
    """Verify a second prefetch asks only for the ids it has not seen."""
    queries: list[str] = []

    def archive(query: str) -> Table:
        """Record the query and answer with no rows.

        Returns
        -------
        reply : `astropy.table.Table`
            An empty reply.
        """
        queries.append(query)
        return Table()

    driver = AstroqueryGaiaReddeningDriver(run_query=archive)
    driver.prefetch_reddening([SOURCE_ID])
    driver.prefetch_reddening([SOURCE_ID])
    driver.prefetch_reddening([SOURCE_ID, SOURCE_ID + 1])

    assert len(queries) == 2
    assert str(SOURCE_ID + 1) in queries[1]
    assert str(SOURCE_ID) not in queries[1].replace(str(SOURCE_ID + 1), "")


def test_a_failed_batch_costs_one_wait_and_later_lookups_fail_at_once() -> None:
    """Verify one failed chunk stops the prefetch and no star sends a query."""
    ids = list(range(1, 3 * GAIA_BATCH_SIZE + 1))
    attempts: list[str] = []

    def overloaded_archive(query: str) -> Table:
        """Fail like an overloaded server.

        Raises
        ------
        OSError
            Always.
        """
        attempts.append(query)
        raise OSError("HTTP 503 Server overloaded")

    driver = AstroqueryGaiaReddeningDriver(run_query=overloaded_archive)
    with pytest.raises(ExternalServiceError):
        driver.prefetch_reddening(ids)

    assert len(attempts) == 1
    for source_id in (ids[0], ids[-1], 10 * len(ids)):
        if source_id in ids:
            with pytest.raises(ExternalServiceError, match="batch query"):
                driver.get_reddening(source_id)
    assert len(attempts) == 1


def test_a_batch_that_does_not_answer_in_time_is_one_bounded_wait() -> None:
    """Verify a stalled archive raises after one timeout, not one per chunk."""
    release = threading.Event()
    attempts: list[str] = []

    def stalled_archive(query: str) -> Table:
        """Wait until the test lets go.

        Returns
        -------
        reply : `astropy.table.Table`
            An empty reply, once released.
        """
        attempts.append(query)
        release.wait(timeout=5.0)
        return Table()

    driver = AstroqueryGaiaReddeningDriver(run_query=stalled_archive, batch_timeout_seconds=0.05)
    try:
        with pytest.raises(ExternalServiceError, match="timed out"):
            driver.prefetch_reddening(range(1, 2 * GAIA_BATCH_SIZE + 1))
        assert len(attempts) == 1
    finally:
        release.set()


def test_a_prefetch_after_failures_still_succeeds_and_clears_the_verdict() -> None:
    """Verify a success does not depend on earlier failures."""
    state = {"fail": True}

    def archive(query: str) -> Table:
        """Fail until told to answer.

        Returns
        -------
        reply : `astropy.table.Table`
            One row for `SOURCE_ID`.

        Raises
        ------
        OSError
            While the test says to fail.
        """
        if state["fail"]:
            raise OSError("down")
        return batch_table({SOURCE_ID: (0.134, None)})

    driver = AstroqueryGaiaReddeningDriver(run_query=archive)
    for _ in range(CONSECUTIVE_FAILURE_LIMIT + 1):
        with pytest.raises(ExternalServiceError):
            driver.prefetch_reddening([SOURCE_ID])
    state["fail"] = False
    driver.prefetch_reddening([SOURCE_ID])

    estimate = driver.get_reddening(SOURCE_ID)
    assert estimate is not None and estimate.ebv == pytest.approx(0.134 / E_BP_RP_PER_E_B_V)


def test_a_failed_second_chunk_keeps_the_first_chunks_answers() -> None:
    """Verify ids answered before a failure stay usable."""
    ids = list(range(1, GAIA_BATCH_SIZE + 3))
    calls = {"count": 0}

    def archive(query: str) -> Table:
        """Answer the first chunk and fail the second.

        Returns
        -------
        reply : `astropy.table.Table`
            One row for id 1 on the first call.

        Raises
        ------
        OSError
            On the second call.
        """
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("down")
        return batch_table({1: (0.134, None)})

    driver = AstroqueryGaiaReddeningDriver(run_query=archive)
    with pytest.raises(ExternalServiceError):
        driver.prefetch_reddening(ids)

    assert driver.get_reddening(1) is not None
    with pytest.raises(ExternalServiceError):
        driver.get_reddening(ids[-1])
    assert calls["count"] == 2


def test_a_batch_reply_without_the_source_id_column_is_an_error() -> None:
    """Verify a reply that cannot be matched to stars is a failure."""
    reply = Table({"ebpminrp_gspphot": [0.1], "ag_gspphot": [0.2]})
    driver = AstroqueryGaiaReddeningDriver(run_query=lambda _query: reply)

    with pytest.raises(ExternalServiceError, match="lacks"):
        driver.prefetch_reddening([SOURCE_ID])
