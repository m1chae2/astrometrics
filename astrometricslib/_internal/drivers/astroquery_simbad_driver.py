"""Purpose: The SIMBAD driver, which owns the process's one SIMBAD client.

Description: `AstroquerySimbadDriver` implements the `SimbadDriver`
interface (`drivers/interfaces/simbad_driver.py`) with astroquery.

astroquery ships a module-level `Simbad` object and expects callers to
mutate it in place before each query, setting the row limit and the
columns to return. That makes it shared mutable state on a library
global: anything else in the process that queries SIMBAD -- our own
pipelines, or astroquery's internals -- reads back whatever configuration
the last caller left behind.

This module therefore builds and owns its own `SimbadClass` instance
rather than borrowing that global, and is the only place in the codebase
permitted to import from `astroquery.simbad`. One object, one owner, one
lock. `test_astroquery_simbad_driver.py` enforces the import rule.

Callers use the driver's `query_region` and `query_object`. Every
driver instance shares the one client, and both methods take
`SIMBAD_LOCK`, configure the client and query it without releasing it in
between, so a second caller can never observe a half-applied
configuration. A failed query (no network, a server error, or a reply
that cannot be read) is raised as `ExternalServiceError`, so callers
catch one error category.

When SIMBAD cannot be reached at all (a refused connection or a request
that times out), the driver stops asking for `SIMBAD_COOLDOWN_SECONDS`.
Every call in that time raises `ExternalServiceError` at once, instead of
each waiting out its own timeout. The pause is short and temporary: after
it the next call tries SIMBAD again, and one good reply clears it early.
"""

import logging
import threading
import time
from typing import Any

import requests
from astroquery.simbad import SimbadClass

from astrometricslib._internal.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.utilities.exceptions import ONLINE_QUERY_ERRORS

logger = logging.getLogger(__name__)


# How long to wait on a single SIMBAD HTTP request before giving up.
#
# requests applies no timeout of its own, so without this a stalled
# connection blocks the calling analysis run indefinitely, with no
# progress and nothing logged. 30s is generous for one region query
# against a responsive server, and fails fast enough that the caller can
# fall back to Gaia while the run is still worth finishing.
#
# This is enforced on the HTTP request itself rather than through
# `SimbadClass.timeout`. That property sets the execution duration of TAP
# *async* jobs, and astroquery discards it entirely on the synchronous
# path (`_cached_query_tap` forwards it to `run_async` only), which is the
# path every query here takes. Setting it would look like a timeout
# without being one -- and its setter fetches the service's advertised
# limits over the network, so merely assigning it can itself hang.
SIMBAD_QUERY_TIMEOUT_SECONDS = 30

# How long to stop asking SIMBAD after it could not be reached.
#
# A run that looks up many stars while SIMBAD is down would otherwise wait
# out the 30 s timeout above on every call (2 to 4 times in one M 13
# spectroscopy stage, about two minutes). After one failed connection the
# driver fails the next calls at once for this long. It is a pause, never a
# permanent mark: when it ends the next call goes to SIMBAD again. 120 s is
# a design estimate: long enough to cover the burst of lookups in one stage,
# short enough that a server that comes back is used within the same night's
# run.
SIMBAD_COOLDOWN_SECONDS = 120.0

# The time (from `time.monotonic`) before which calls fail at once, or 0.0
# when SIMBAD is not paused. Only read or written with `SIMBAD_LOCK` held.
_paused_until: float = 0.0

# Guards both the client's creation and every use of it. Held across
# configuration and the query together, because the columns one caller
# requests are read back by whoever queries next.
SIMBAD_LOCK = threading.Lock()

# The one client. Built on first use rather than at import, so importing
# this module -- which every test and the app itself does -- stays free of
# side effects.
_client: SimbadClass | None = None


def _is_connection_failure(error: BaseException) -> bool:
    """Say whether an error means SIMBAD could not be reached at all.

    A refused connection, a lost network and a timeout all do. A reply
    that arrives but is an error page, or cannot be read, does not: the
    server is up, so asking again soon may work. The error is followed
    through its causes, because pyvo can wrap the `requests` error.

    Parameters
    ----------
    error : `BaseException`
        The error a query raised.

    Returns
    -------
    is_connection_failure : `bool`
        `True` when the error or one of its causes is a connection error or
        a timeout.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(
            current,
            (requests.exceptions.ConnectionError, requests.exceptions.Timeout, ConnectionError, TimeoutError),
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def _raise_if_paused(message: str, details: dict[str, Any]) -> None:
    """Fail at once while SIMBAD is paused after a connection failure.

    Only call with `SIMBAD_LOCK` held.

    Parameters
    ----------
    message : `str`
        The error text of the call that is being refused.
    details : `dict`
        The details of that call's error.

    Raises
    ------
    ExternalServiceError
        If SIMBAD is paused.
    """
    remaining = _paused_until - time.monotonic()
    if remaining > 0:
        raise ExternalServiceError(
            message, details={**details, "skipped_for_seconds_more": round(remaining, 1)}
        )


def _note_outcome(error: BaseException | None) -> None:
    """Start or end the pause, from how the last call went.

    Only call with `SIMBAD_LOCK` held.

    Parameters
    ----------
    error : `BaseException` or `None`
        The error the call raised, or `None` when it got an answer.
    """
    global _paused_until
    if error is None:
        _paused_until = 0.0
    elif _is_connection_failure(error):
        _paused_until = time.monotonic() + SIMBAD_COOLDOWN_SECONDS
        logger.warning(
            "SIMBAD could not be reached; skipping its calls for %.0f s: %s", SIMBAD_COOLDOWN_SECONDS, error
        )


def _install_request_timeout(client: SimbadClass) -> None:
    """Give `client`'s HTTP session a default request timeout.

    astroquery builds its `pyvo` TAP service around a `requests.Session`
    it never passes a timeout to. Wrapping `request` supplies one for
    every call made through that session, including the ones pyvo makes
    internally, without reaching into pyvo's own call sites.

    Parameters
    ----------
    client : `SimbadClass`
        The client whose session should carry the timeout.
    """
    session = client._session
    original_request = session.request

    def request_with_timeout(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", SIMBAD_QUERY_TIMEOUT_SECONDS)
        return original_request(*args, **kwargs)

    session.request = request_with_timeout


def _get_client() -> SimbadClass:
    """Return the one SIMBAD client, building it on first use.

    Only call with `SIMBAD_LOCK` held: the lock is what makes "build it
    on first use" produce a single client rather than one per racing
    thread.

    Returns
    -------
    client : `SimbadClass`
        This process's SIMBAD client. The same object on every call.
    """
    global _client
    if _client is None:
        client = SimbadClass()
        _install_request_timeout(client)
        _client = client
    return _client


def _configure(client: SimbadClass, votable_fields: tuple[str, ...], row_limit: int | None) -> None:
    """Reset `client` and request `votable_fields` from it.

    Only call with `SIMBAD_LOCK` held.

    Parameters
    ----------
    client : `SimbadClass`
        The client to configure.
    votable_fields : `tuple` [`str`]
        Columns to request beyond SIMBAD's defaults.
    row_limit : `int` or `None`
        Maximum rows to return, or `None` to leave the limit alone.
    """
    # Reset first: astroquery accumulates requested columns, so without
    # this each query would also return the previous caller's.
    client.reset_votable_fields()
    if row_limit is not None:
        client.ROW_LIMIT = row_limit
    if votable_fields:
        client.add_votable_fields(*votable_fields)


class AstroquerySimbadDriver(SimbadDriver):
    """Queries SIMBAD through the process's one astroquery client."""

    def query_region(
        self,
        coordinates: Any,
        radius: str,
        *,
        votable_fields: tuple[str, ...] = (),
        row_limit: int | None = None,
    ) -> Any:
        """Query SIMBAD for every catalog object within `radius` of a point.

        Parameters
        ----------
        coordinates : `astropy.coordinates.SkyCoord`
            Centre of the search cone.
        radius : `str`
            Cone radius in a form astroquery accepts, e.g. ``"0.5d"``.
        votable_fields : `tuple` [`str`], optional
            Extra columns to request beyond SIMBAD's defaults.
        row_limit : `int`, optional
            Maximum rows to return. If `None` (default), the client's
            existing limit applies.

        Returns
        -------
        result_table : `astropy.table.Table` or `None`
            The matching rows, or `None` if SIMBAD returned nothing.

        Raises
        ------
        ExternalServiceError
            If SIMBAD could not be reached or its reply could not be read.
        """
        with SIMBAD_LOCK:
            _raise_if_paused("The SIMBAD region search was skipped.", {"radius": radius})
            client = _get_client()
            try:
                _configure(client, votable_fields, row_limit)
                result = client.query_region(coordinates, radius=radius)
                _note_outcome(None)
                return result
            except ONLINE_QUERY_ERRORS as query_error:
                _note_outcome(query_error)
                raise ExternalServiceError(
                    "The SIMBAD region search failed.", details={"radius": radius}
                ) from query_error

    def query_object(self, object_name: str, *, votable_fields: tuple[str, ...] = ()) -> Any:
        """Look one named object up in SIMBAD.

        Parameters
        ----------
        object_name : `str`
            Catalog identifier to resolve, e.g. ``"M 13"``.
        votable_fields : `tuple` [`str`], optional
            Extra columns to request beyond SIMBAD's defaults.

        Returns
        -------
        result_table : `astropy.table.Table` or `None`
            The matching row, or `None` if the name did not resolve.

        Raises
        ------
        ExternalServiceError
            If SIMBAD could not be reached or its reply could not be read.
        """
        with SIMBAD_LOCK:
            _raise_if_paused("The SIMBAD name lookup was skipped.", {"object_name": object_name})
            client = _get_client()
            try:
                # Inside the `try`: adding a column can itself ask the
                # server which columns exist, and that can fail offline.
                _configure(client, votable_fields, None)
                result = client.query_object(object_name)
                _note_outcome(None)
                return result
            except ONLINE_QUERY_ERRORS as query_error:
                _note_outcome(query_error)
                raise ExternalServiceError(
                    "The SIMBAD name lookup failed.", details={"object_name": object_name}
                ) from query_error

    def query_tap(self, adql_query: str) -> Any:
        """Run an ADQL query against SIMBAD's TAP service.

        Parameters
        ----------
        adql_query : `str`
            The query to run.

        Returns
        -------
        result_table : `astropy.table.Table` or `None`
            The rows, or `None` if SIMBAD returned nothing.

        Raises
        ------
        ExternalServiceError
            If SIMBAD could not be reached or its reply could not be read.
        """
        with SIMBAD_LOCK:
            _raise_if_paused("The SIMBAD TAP query was skipped.", {"query_length": len(adql_query)})
            client = _get_client()
            try:
                result = client.query_tap(adql_query)
                _note_outcome(None)
                return result
            except ONLINE_QUERY_ERRORS as query_error:
                _note_outcome(query_error)
                raise ExternalServiceError(
                    "The SIMBAD TAP query failed.", details={"query_length": len(adql_query)}
                ) from query_error
