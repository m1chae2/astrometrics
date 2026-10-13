"""Purpose: The Gaia driver that looks up a star's interstellar reddening.

Description: `AstroqueryGaiaReddeningDriver` implements the `ReddeningDriver`
interface (`drivers/interfaces/reddening_driver.py`) with Gaia Data Release 3
(DR3), reached through astroquery.

Gaia DR3 estimates the dust in front of many stars from their colours and
parallaxes (the GSP-Phot method). The table
``gaiadr3.astrophysical_parameters`` holds two of its results for each star:

* ``ebpminrp_gspphot``: the colour excess E(BP-RP), in magnitudes, where BP
  and RP are Gaia's blue and red bands.
* ``ag_gspphot``: the dimming A(G) in Gaia's broad G band, in magnitudes.

The pipeline works in E(B-V), the colour excess of the Johnson B and V bands.
This driver converts with two ratios from Casagrande and VandenBerg (2018,
MNRAS 479, L102), who give the dimming of each Gaia band per unit E(B-V) for
a typical star: R(BP) = 3.374 and R(RP) = 2.035, so E(BP-RP) is about
1.339 E(B-V), and R(G) = 2.740, so A(G) is about 2.740 E(B-V). The driver uses
E(BP-RP) when the catalog has it and falls back to A(G).

The ratios depend a little on the star's temperature and on how much dust
there is, so a converted E(B-V) is an estimate. GSP-Phot values for a single
star can also be wrong when the star is a binary or has a poorly measured
parallax.

Caching. The local catalog store (`drivers/catalog_store.py`) keeps Gaia
positions, magnitudes and proper motions. It has no column for either
extinction value, so it cannot hold this result. The driver therefore keeps
the answers in memory for the life of the driver object, including the answer
"Gaia has no reddening for this star".

A failed query (no network, a server error, a reply that cannot be read, or
no reply within `GAIA_QUERY_TIMEOUT_SECONDS`) is raised as
`ExternalServiceError`. After `CONSECUTIVE_FAILURE_LIMIT` failures in a row
the driver stops asking and raises at once, so a run without a connection
does not wait for a timeout on every star.
"""

import logging
import math
import threading
from collections.abc import Callable
from typing import Any

import numpy as np

from astrometricslib.drivers.interfaces.reddening_driver import ReddeningDriver, ReddeningEstimate
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.utilities.exceptions import ONLINE_QUERY_ERRORS

logger = logging.getLogger(__name__)

__all__ = [
    "A_G_PER_E_B_V",
    "E_BP_RP_PER_E_B_V",
    "AstroqueryGaiaReddeningDriver",
    "build_reddening_query",
    "estimate_from_gaia_values",
]

E_BP_RP_PER_E_B_V = 1.339
"""The ratio E(BP-RP) / E(B-V) for a typical star: R(BP) - R(RP) =
3.374 - 2.035 (Casagrande and VandenBerg 2018, MNRAS 479, L102). Divide Gaia's
E(BP-RP) by it to get E(B-V)."""

A_G_PER_E_B_V = 2.740
"""The ratio A(G) / E(B-V) for a typical star: R(G) (Casagrande and
VandenBerg 2018, MNRAS 479, L102). Divide Gaia's A(G) by it to get E(B-V)."""

GAIA_REDDENING_TABLE = "gaiadr3.astrophysical_parameters"
"""The Gaia archive table with the GSP-Phot extinction values. The release is
pinned (DR3) so the ids and values always come from the same release as the
star identifier's Gaia queries."""

GAIA_QUERY_TIMEOUT_SECONDS = 60.0
"""How long to wait for one Gaia reply, in seconds. Gaia's client has no
timeout of its own, so a stalled connection would otherwise wait for ever.
The same 60 seconds as the star identifier's Gaia queries."""

CONSECUTIVE_FAILURE_LIMIT = 3
"""How many failed queries in a row make the driver stop asking."""

_SOURCE_NOTE_BP_RP = f"Gaia DR3 GSP-Phot E(BP-RP) / {E_BP_RP_PER_E_B_V}"
_SOURCE_NOTE_A_G = f"Gaia DR3 GSP-Phot A(G) / {A_G_PER_E_B_V}"


def build_reddening_query(gaia_source_id: int) -> str:
    """Write the ADQL query that asks Gaia for one star's extinction values.

    Parameters
    ----------
    gaia_source_id : `int`
        The Gaia DR3 source id.

    Returns
    -------
    query : `str`
        An ADQL ``SELECT`` for the star's ``source_id``, ``ebpminrp_gspphot``
        and ``ag_gspphot`` from `GAIA_REDDENING_TABLE`.

    Raises
    ------
    ValueError
        If the id is not a positive whole number.
    """
    if isinstance(gaia_source_id, bool) or not isinstance(gaia_source_id, int | np.integer):
        raise ValueError(f"A Gaia source id must be a whole number, got {gaia_source_id!r}.")
    if gaia_source_id <= 0:
        raise ValueError(f"A Gaia source id must be positive, got {gaia_source_id}.")
    # The id was checked above to be a positive whole number, so it carries
    # no query text. The parts are joined, not formatted into one string.
    return " ".join([
        "SELECT source_id, ebpminrp_gspphot, ag_gspphot",
        "FROM",
        GAIA_REDDENING_TABLE,
        "WHERE source_id =",
        str(int(gaia_source_id)),
    ])


def _finite_non_negative(value: object) -> float | None:
    """Read a catalog cell as a number that is zero or more.

    Parameters
    ----------
    value : `object`
        A table cell: a number, a masked value or `None`.

    Returns
    -------
    number : `float` or `None`
        The value as a `float`, or `None` when it is missing, masked, not a
        number, infinite or negative.
    """
    if value is None or np.ma.is_masked(value):
        return None
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) and number >= 0.0 else None


def estimate_from_gaia_values(gaia_source_id: int, e_bp_rp: object, a_g: object) -> ReddeningEstimate | None:
    """Turn Gaia's two extinction values into an E(B-V) estimate.

    Parameters
    ----------
    gaia_source_id : `int`
        The Gaia DR3 source id the values belong to.
    e_bp_rp : `object`
        Gaia's ``ebpminrp_gspphot`` cell, in magnitudes.
    a_g : `object`
        Gaia's ``ag_gspphot`` cell, in magnitudes.

    Returns
    -------
    estimate : `ReddeningEstimate` or `None`
        E(B-V) from E(BP-RP) divided by `E_BP_RP_PER_E_B_V` when that is
        usable, otherwise from A(G) divided by `A_G_PER_E_B_V`. `None` when
        neither is usable.
    """
    colour_excess = _finite_non_negative(e_bp_rp)
    if colour_excess is not None:
        return ReddeningEstimate(
            ebv=colour_excess / E_BP_RP_PER_E_B_V,
            source=_SOURCE_NOTE_BP_RP,
            gaia_source_id=int(gaia_source_id),
        )
    dimming = _finite_non_negative(a_g)
    if dimming is not None:
        return ReddeningEstimate(
            ebv=dimming / A_G_PER_E_B_V, source=_SOURCE_NOTE_A_G, gaia_source_id=int(gaia_source_id)
        )
    return None


def _run_gaia_query(adql_query: str) -> Any:
    """Send one ADQL query to the Gaia archive and wait for the rows.

    Parameters
    ----------
    adql_query : `str`
        The query.

    Returns
    -------
    result_table : `astropy.table.Table` or `None`
        The rows Gaia returned.
    """
    from astroquery.gaia import Gaia

    return Gaia.launch_job(adql_query, dump_to_file=False).get_results()


def _run_with_timeout(run_query: Callable[[str], Any], adql_query: str, timeout_seconds: float) -> Any:
    """Run a query in a worker thread and give up after a deadline.

    The thread is abandoned, not stopped, when the deadline passes.

    Parameters
    ----------
    run_query : `Callable`
        The function that sends the query and returns the rows.
    adql_query : `str`
        The query.
    timeout_seconds : `float`
        How long to wait, in seconds.

    Returns
    -------
    result_table : `object`
        Whatever `run_query` returned.

    Raises
    ------
    ExternalServiceError
        If the query failed, or did not answer in time.
    """
    outcome: dict[str, Any] = {}

    def work() -> None:
        """Run the query and store its rows or its error."""
        try:
            outcome["table"] = run_query(adql_query)
        except ONLINE_QUERY_ERRORS as query_error:
            outcome["error"] = query_error

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        raise ExternalServiceError(
            "The Gaia reddening query timed out.", details={"timeout_seconds": timeout_seconds}
        )
    if "error" in outcome:
        raise ExternalServiceError("The Gaia reddening query failed.") from outcome["error"]
    if "table" not in outcome:
        raise ExternalServiceError("The Gaia reddening query ended without a reply.")
    return outcome["table"]


def _first_row_values(result_table: Any) -> tuple[object, object] | None:
    """Pick the two extinction cells out of a Gaia reply.

    Parameters
    ----------
    result_table : `astropy.table.Table` or `None`
        The rows Gaia returned.

    Returns
    -------
    values : `tuple` [`object`, `object`] or `None`
        The ``ebpminrp_gspphot`` and ``ag_gspphot`` cells of the first row,
        or `None` when the reply has no rows. Column names are matched
        without regard to case.

    Raises
    ------
    ExternalServiceError
        If the reply has rows but lacks the expected columns.
    """
    if result_table is None or len(result_table) == 0:
        return None
    columns = {str(name).lower(): name for name in result_table.colnames}
    if "ebpminrp_gspphot" not in columns or "ag_gspphot" not in columns:
        raise ExternalServiceError(
            "The Gaia reddening reply lacks the extinction columns.", details={"columns": sorted(columns)}
        )
    row = result_table[0]
    return row[columns["ebpminrp_gspphot"]], row[columns["ag_gspphot"]]


class AstroqueryGaiaReddeningDriver(ReddeningDriver):
    """Looks up a star's reddening in Gaia DR3 through astroquery.

    Parameters
    ----------
    run_query : `Callable`, optional
        A function that takes an ADQL query string and returns the rows (an
        `astropy.table.Table`). Defaults to the Gaia archive through
        astroquery. A test passes its own function so no network is used.
    timeout_seconds : `float`, optional
        How long to wait for one reply. Defaults to
        `GAIA_QUERY_TIMEOUT_SECONDS`.
    """

    def __init__(
        self,
        run_query: Callable[[str], Any] | None = None,
        timeout_seconds: float = GAIA_QUERY_TIMEOUT_SECONDS,
    ) -> None:
        """Set up an empty cache."""
        self._run_query = run_query or _run_gaia_query
        self._timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._cache: dict[int, ReddeningEstimate | None] = {}
        self._consecutive_failures = 0

    def get_reddening(self, gaia_source_id: int) -> ReddeningEstimate | None:
        """Look up the colour excess E(B-V) of one star in Gaia DR3.

        Parameters
        ----------
        gaia_source_id : `int`
            The star's Gaia DR3 source id. A value that is not a positive
            whole number is refused before any query is sent.

        Returns
        -------
        estimate : `ReddeningEstimate` or `None`
            E(B-V) with a note on its source, or `None` when Gaia has no
            usable extinction for the star. The answer is kept in memory, so
            asking again does not query Gaia.

        Raises
        ------
        ExternalServiceError
            If Gaia could not be reached, did not answer in time, or the
            driver has stopped asking after repeated failures.
        """
        adql_query = build_reddening_query(gaia_source_id)
        source_id = int(gaia_source_id)
        with self._lock:
            if source_id in self._cache:
                return self._cache[source_id]
            if self._consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                raise ExternalServiceError(
                    "Gaia reddening queries are switched off after repeated failures.",
                    details={"failures": self._consecutive_failures},
                )
            try:
                result_table = _run_with_timeout(self._run_query, adql_query, self._timeout_seconds)
                values = _first_row_values(result_table)
            except ExternalServiceError:
                self._consecutive_failures += 1
                logger.warning("Gaia reddening lookup failed for source %s.", source_id)
                raise
            self._consecutive_failures = 0
            estimate = None if values is None else estimate_from_gaia_values(source_id, *values)
            self._cache[source_id] = estimate
            return estimate
