"""Driver owning the process's one SIMBAD client.

astroquery ships a module-level `Simbad` object and expects callers to
mutate it in place before each query, setting the row limit and the
columns to return. That makes it shared mutable state on a library
global: anything else in the process that queries SIMBAD -- our own
pipelines, or astroquery's internals -- reads back whatever configuration
the last caller left behind.

This module therefore builds and owns its own `SimbadClass` instance
rather than borrowing that global, and is the only place in the codebase
permitted to import from `astroquery.simbad`. One object, one owner, one
lock. `test_simbad_interface.py` enforces the import rule.

Callers use `query_region` and `query_object`; both take `SIMBAD_LOCK`,
configure the client and query it without releasing it in between, so a
second caller can never observe a half-applied configuration.
"""

import logging
import threading
from typing import Any

from astroquery.simbad import SimbadClass

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

# Guards both the client's creation and every use of it. Held across
# configuration and the query together, because the columns one caller
# requests are read back by whoever queries next.
SIMBAD_LOCK = threading.Lock()

# The one client. Built on first use rather than at import, so importing
# this module -- which every test and the app itself does -- stays free of
# side effects.
_client: SimbadClass | None = None


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


def query_region(
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
    """
    with SIMBAD_LOCK:
        client = _get_client()
        _configure(client, votable_fields, row_limit)
        return client.query_region(coordinates, radius=radius)


def query_object(object_name: str, *, votable_fields: tuple[str, ...] = ()) -> Any:
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
    """
    with SIMBAD_LOCK:
        client = _get_client()
        _configure(client, votable_fields, None)
        return client.query_object(object_name)
