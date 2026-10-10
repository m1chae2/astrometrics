"""Purpose: Test the Gaia reddening driver without a network.

Description: `AstroqueryGaiaReddeningDriver` asks the Gaia archive for a
star's GSP-Phot extinction and converts it to E(B-V). These tests give the
driver a function in place of the archive, so nothing leaves the machine. They
check the query text that would be sent, the two conversions and their
documented factors, the preference for E(BP-RP) over A(G), masked and negative
values, the in-memory cache (including "no value"), the handling of failures
and timeouts, and that `Drivers` builds this driver by default and returns a
caller's own driver when one is given.
"""

import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.table import MaskedColumn, Table

from astrometricslib.drivers.astroquery_gaia_reddening_driver import (
    A_G_PER_E_B_V,
    CONSECUTIVE_FAILURE_LIMIT,
    E_BP_RP_PER_E_B_V,
    GAIA_REDDENING_TABLE,
    AstroqueryGaiaReddeningDriver,
    build_reddening_query,
    estimate_from_gaia_values,
)
from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.drivers.interfaces.reddening_driver import ReddeningDriver, ReddeningEstimate
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


def test_the_default_query_function_uses_the_gaia_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the built-in path calls ``Gaia.launch_job`` with the query.

    The Gaia client is replaced, so nothing is sent over the network.
    """
    import astroquery.gaia as gaia_module

    fake_gaia = MagicMock()
    fake_gaia.launch_job.return_value.get_results.return_value = gaia_table(0.1339, None)
    monkeypatch.setattr(gaia_module, "Gaia", fake_gaia)

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
