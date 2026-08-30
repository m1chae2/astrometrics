"""Driver for the SIMBAD astronomical database, via astroquery.

astroquery exposes SIMBAD as a single process-global `Simbad` object
whose query settings (row limit, requested columns) are mutated in place
before each call. That makes it shared mutable state, and it is not
thread safe: two callers configuring it at once will read each other's
columns back. This module is the one place allowed to touch that object,
so the configuration lives next to the lock that protects it instead of
being repeated in each pipeline that wants a catalog lookup.

Callers use `query_region` and `query_object`; both serialise on
`SIMBAD_LOCK` and request their columns inside it.
"""

import logging
import threading
from typing import Any

from astroquery.simbad import Simbad

logger = logging.getLogger(__name__)

# How long to wait on a single SIMBAD HTTP request before giving up.
#
# requests has no default timeout at all, so without this a stalled
# connection blocks the calling analysis run indefinitely, with no
# progress and nothing logged. 30s is generous for one region query
# against a responsive server, and fails fast enough that the caller can
# fall back to Gaia while the run is still worth finishing.
#
# This is enforced on the HTTP request itself rather than through
# `Simbad.timeout`. That property sets the execution duration of TAP
# *async* jobs, and astroquery discards it entirely on the synchronous
# path (`_cached_query_tap` passes it to `run_async` only), which is the
# path every query here takes. Setting it would look like a timeout
# without being one -- and its setter fetches the service's advertised
# limits over the network, so merely assigning it can itself hang.
SIMBAD_QUERY_TIMEOUT_SECONDS = 30

# SIMBAD class-level state is not thread-safe. Held across configuration
# and the query itself, since the columns requested by one caller are
# read back by whoever queries next.
SIMBAD_LOCK = threading.Lock()

_timeout_installed = False


def _install_request_timeout() -> None:
    """Give the shared SIMBAD HTTP session a default request timeout.

    astroquery builds its `pyvo` TAP service around a `requests.Session`
    it never passes a timeout to. Wrapping `request` supplies one for
    every call made through that session, including the ones pyvo makes
    internally, without reaching into pyvo's own call sites.
    """
    global _timeout_installed
    if _timeout_installed:
        return

    session = Simbad._session
    original_request = session.request

    def request_with_timeout(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", SIMBAD_QUERY_TIMEOUT_SECONDS)
        return original_request(*args, **kwargs)

    session.request = request_with_timeout
    _timeout_installed = True


def _configure(votable_fields: tuple[str, ...], row_limit: int | None) -> None:
    """Reset the shared client and request `votable_fields`.

    Only call with `SIMBAD_LOCK` held.
    """
    _install_request_timeout()
    Simbad.reset_votable_fields()
    if row_limit is not None:
        Simbad.ROW_LIMIT = row_limit
    if votable_fields:
        Simbad.add_votable_fields(*votable_fields)


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
        Maximum rows to return. If `None` (default), astroquery's
        configured limit applies.

    Returns
    -------
    result_table : `astropy.table.Table` or `None`
        The matching rows, or `None` if SIMBAD returned nothing.
    """
    with SIMBAD_LOCK:
        _configure(votable_fields, row_limit)
        return Simbad.query_region(coordinates, radius=radius)


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
        _configure(votable_fields, None)
        return Simbad.query_object(object_name)
