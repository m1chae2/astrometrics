"""Purpose: The SIMBAD driver: SIMBAD questions, asked through the gateway.

Description: `AstroquerySimbadDriver` implements the `SimbadDriver`
interface (`drivers/interfaces/simbad_driver.py`). It holds no client and no
retry logic of its own. Each question goes to the outside-service gateway
(``outside_services``), which owns the process's one astroquery client and
applies the rate limit (4 requests per second per host), the server choice
(`[Catalogs] simbad_preferred_server` first, then the other server), the
"too busy" repeats, and the pause after repeated trouble or a 429 or 503 reply.

A failed question (no network, a server error, a reply that cannot be read, or
a service that is paused) is raised as `ExternalServiceError`, so callers catch
one error category. A paused SIMBAD raises at once with no network use; the
stored-answer layer (``cached_simbad_driver``) then uses an old stored answer
and logs a warning.
"""

import logging
from typing import Any

from astrometricslib._internal.drivers import outside_services
from astrometricslib._internal.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.foundation.offline import OfflineModeError
from astrometricslib.utilities.exceptions import ONLINE_QUERY_ERRORS

logger = logging.getLogger(__name__)


def _failure_details(details: dict[str, Any], error: BaseException) -> dict[str, Any]:
    """Add to a failed question's details why nothing came back.

    Parameters
    ----------
    details : `dict`
        The question's details.
    error : `BaseException`
        What went wrong.

    Returns
    -------
    details : `dict`
        The details; a paused service or an offline launch is marked in them.
    """
    if isinstance(error, OfflineModeError):
        return {**details, "offline": True}
    if isinstance(error, outside_services.ServicePausedError):
        return {**details, "paused": True}
    return details


class AstroquerySimbadDriver(SimbadDriver):
    """Queries SIMBAD through the gateway's one astroquery client."""

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
            Center of the search cone.
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
        try:
            return outside_services.simbad_query_region(
                coordinates, radius, votable_fields=votable_fields, row_limit=row_limit
            )
        except (*ONLINE_QUERY_ERRORS, ExternalServiceError) as query_error:
            raise ExternalServiceError(
                "The SIMBAD region search failed.", details=_failure_details({"radius": radius}, query_error)
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
        try:
            return outside_services.simbad_query_object(object_name, votable_fields=votable_fields)
        except (*ONLINE_QUERY_ERRORS, ExternalServiceError) as query_error:
            raise ExternalServiceError(
                "The SIMBAD name lookup failed.",
                details=_failure_details({"object_name": object_name}, query_error),
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
        try:
            return outside_services.simbad_query_tap(adql_query)
        except (*ONLINE_QUERY_ERRORS, ExternalServiceError) as query_error:
            raise ExternalServiceError(
                "The SIMBAD TAP query failed.",
                details=_failure_details({"query_length": len(adql_query)}, query_error),
            ) from query_error
