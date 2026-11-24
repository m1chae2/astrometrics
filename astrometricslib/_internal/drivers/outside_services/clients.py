"""Purpose: Build the network clients, and the only place that may.

Description: Every client that sends a request to an outside computer is
imported and built here: astroquery's SIMBAD, VizieR, Gaia archive, SkyBoT and
Astrometry.net classes, and the URL opener for downloads. Each function below
asks one question through `call_service`, so the rate limit, the back-off, the
server choice and the counters apply to it. No other module of
`astrometricslib` or `wayfindinglib` may import a network client; a test
(``test_outside_services_enforcement.py``) and the import-linter contract
check that.

SIMBAD needs special care. astroquery ships a module-level `Simbad` object
that callers change in place before each query (the row limit and the columns).
That makes it shared state on a library global. This module therefore builds
and owns its own `SimbadClass`, and holds `SIMBAD_LOCK` while it configures
the client and queries it, so a second caller never sees a half-set client.
"""

import socket
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.table import Table

from astrometricslib._internal.drivers.outside_services.gateway import call_service
from astrometricslib._internal.drivers.outside_services.services import (
    ASTROMETRY_NET,
    DOWNLOADS,
    ESA_GAIA,
    GAIA_XP,
    SIMBAD,
    SIRIL_GAIA_CATALOG,
    SKYBOT,
    VIZIER,
)
from astrometricslib.foundation.offline import require_online

# Guards both the SIMBAD client's creation and every use of it. Held across
# configuration and the query together, because the columns one caller asks for
# are read back by whoever queries next.
SIMBAD_LOCK = threading.Lock()

# The one SIMBAD client, built on first use so importing this module has no
# side effects.
_client: Any = None


def _install_request_timeout(client: Any) -> None:
    """Give a client's HTTP session a default request timeout.

    astroquery builds a `requests.Session` it never passes a timeout to.
    Wrapping `request` supplies one for every call made through that session,
    including the ones pyvo makes inside. (`SimbadClass.timeout` is no use: it
    only sets the run time of async jobs and is dropped on the synchronous
    path every query here takes.)

    Parameters
    ----------
    client : `object`
        A client with a ``_session`` attribute.
    """
    session = client._session
    original_request = session.request

    def request_with_timeout(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", SIMBAD.timeout_seconds)
        return original_request(*args, **kwargs)

    session.request = request_with_timeout


def _get_simbad_client() -> Any:
    """Return the one SIMBAD client, building it on first use.

    Only call with `SIMBAD_LOCK` held.

    Returns
    -------
    client : `astroquery.simbad.SimbadClass`
        This process's SIMBAD client.
    """
    global _client
    if _client is None:
        from astroquery.simbad import SimbadClass

        client = SimbadClass()
        _install_request_timeout(client)
        _client = client
    return _client


def _configure_simbad(client: Any, votable_fields: tuple[str, ...], row_limit: int | None) -> None:
    """Reset a SIMBAD client and ask it for some columns.

    Only call with `SIMBAD_LOCK` held.

    Parameters
    ----------
    client : `astroquery.simbad.SimbadClass`
        The client.
    votable_fields : `tuple` [`str`]
        Columns beyond SIMBAD's defaults.
    row_limit : `int` or `None`
        The most rows to return; `None` leaves the limit alone.
    """
    # Reset first: astroquery adds up the columns asked for, so without this
    # each query would also return the previous caller's.
    client.reset_votable_fields()
    if row_limit is not None:
        client.ROW_LIMIT = row_limit
    if votable_fields:
        client.add_votable_fields(*votable_fields)


def _ask_simbad(query: Callable[[Any], Any], votable_fields: tuple[str, ...], row_limit: int | None) -> Any:
    """Run one query on SIMBAD through the gateway.

    Parameters
    ----------
    query : `Callable`
        Runs the query on the configured client.
    votable_fields : `tuple` [`str`]
        Columns beyond SIMBAD's defaults.
    row_limit : `int` or `None`
        The most rows to return.

    Returns
    -------
    result : `astropy.table.Table` or `None`
        The rows.
    """

    def ask(host: str) -> Any:
        """Query one SIMBAD host.

        Returns
        -------
        result : `astropy.table.Table` or `None`
            The rows.
        """
        with SIMBAD_LOCK:
            client = _get_simbad_client()
            client.server = host
            _configure_simbad(client, votable_fields, row_limit)
            return query(client)

    return call_service(SIMBAD, ask)


def simbad_query_region(
    coordinates: Any, radius: str, *, votable_fields: tuple[str, ...] = (), row_limit: int | None = None
) -> Any:
    """Ask SIMBAD for every object within a radius of a point.

    Parameters
    ----------
    coordinates : `astropy.coordinates.SkyCoord`
        The center.
    radius : `str`
        The radius in a form astroquery accepts, such as ``"0.5d"``.
    votable_fields : `tuple` [`str`], optional
        Columns beyond SIMBAD's defaults.
    row_limit : `int`, optional
        The most rows to return.

    Returns
    -------
    result : `astropy.table.Table` or `None`
        The rows.
    """
    return _ask_simbad(
        lambda client: client.query_region(coordinates, radius=radius), votable_fields, row_limit
    )


def simbad_query_object(object_name: str, *, votable_fields: tuple[str, ...] = ()) -> Any:
    """Look one named object up in SIMBAD.

    Parameters
    ----------
    object_name : `str`
        The identifier, such as ``"M 13"``.
    votable_fields : `tuple` [`str`], optional
        Columns beyond SIMBAD's defaults.

    Returns
    -------
    result : `astropy.table.Table` or `None`
        The row, or `None` when the name did not resolve.
    """
    return _ask_simbad(lambda client: client.query_object(object_name), votable_fields, None)


def simbad_query_tap(adql_query: str) -> Any:
    """Run an ADQL query against SIMBAD's TAP service.

    Parameters
    ----------
    adql_query : `str`
        The query.

    Returns
    -------
    result : `astropy.table.Table` or `None`
        The rows.
    """

    def ask(host: str) -> Any:
        """Run the query on one SIMBAD host.

        Returns
        -------
        result : `astropy.table.Table` or `None`
            The rows.
        """
        with SIMBAD_LOCK:
            client = _get_simbad_client()
            client.server = host
            return client.query_tap(adql_query)

    return call_service(SIMBAD, ask)


def vizier_query_region(
    center: SkyCoord,
    radius_degrees: float,
    *,
    catalog: str,
    columns: list[str],
    column_filters: dict[str, str] | None,
    row_limit: int,
    timeout_seconds: float,
) -> Any:
    """Ask VizieR for the rows of a catalog inside a circle.

    Parameters
    ----------
    center : `astropy.coordinates.SkyCoord`
        The circle's center.
    radius_degrees : `float`
        The radius.
    catalog : `str`
        The VizieR catalog name.
    columns : `list` [`str`]
        The columns to return.
    column_filters : `dict` [`str`, `str`] or `None`
        VizieR column filters.
    row_limit : `int`
        The most rows to return.
    timeout_seconds : `float`
        How long one server may take.

    Returns
    -------
    results : `astroquery.utils.TableList`
        The answer: one table for each catalog asked.
    """
    from astroquery.vizier import Vizier

    def ask(host: str) -> Any:
        """Run the query on one VizieR host.

        Returns
        -------
        results : `astroquery.utils.TableList`
            The answer.
        """
        vizier = Vizier(
            columns=columns,
            column_filters=column_filters or {},
            row_limit=int(row_limit),
            timeout=timeout_seconds,
            vizier_server=host,
        )
        return vizier.query_region(center, radius=radius_degrees * u.deg, catalog=catalog)

    return call_service(VIZIER, ask)


def gaia_archive_query(adql_query: str, *, asynchronous: bool = False) -> Table:
    """Run one ADQL query on the ESA Gaia archive and wait for the rows.

    Parameters
    ----------
    adql_query : `str`
        The query.
    asynchronous : `bool`, optional
        Run it as an asynchronous job, which may return more than 2000 rows.
        By default it is a synchronous job.

    Returns
    -------
    table : `astropy.table.Table`
        The rows.
    """

    def ask(host: str) -> Table:
        """Send the query to the archive.

        Returns
        -------
        table : `astropy.table.Table`
            The rows.
        """
        from astroquery.gaia import Gaia

        launch = Gaia.launch_job_async if asynchronous else Gaia.launch_job
        return launch(adql_query, dump_to_file=False).get_results()

    return call_service(ESA_GAIA, ask)


class _FinishedJob:
    """A finished archive query, with the interface of astroquery's job."""

    def __init__(self, table: Table) -> None:
        """Keep the rows.

        Parameters
        ----------
        table : `astropy.table.Table`
            The rows.
        """
        self._table = table

    def get_results(self) -> Table:
        """Give the rows.

        Returns
        -------
        table : `astropy.table.Table`
            The rows.
        """
        return self._table


class GaiaArchive:
    """The ESA Gaia archive as the rest of the program sees it.

    It has the one method callers used on astroquery's ``Gaia`` object,
    ``launch_job``, and every call goes through the gateway.
    """

    def launch_job(self, query: str, dump_to_file: bool = False) -> _FinishedJob:
        """Run a query and give the finished job.

        Parameters
        ----------
        query : `str`
            The ADQL query.
        dump_to_file : `bool`, optional
            Ignored; the rows always come back in memory.

        Returns
        -------
        job : `_FinishedJob`
            The job; ``get_results()`` gives the rows.
        """
        del dump_to_file
        return _FinishedJob(gaia_archive_query(query))

    def launch_job_async(self, query: str, dump_to_file: bool = False) -> _FinishedJob:
        """Run a query as an asynchronous job and give the finished job.

        Parameters
        ----------
        query : `str`
            The ADQL query.
        dump_to_file : `bool`, optional
            Ignored; the rows always come back in memory.

        Returns
        -------
        job : `_FinishedJob`
            The job; ``get_results()`` gives the rows.
        """
        del dump_to_file
        return _FinishedJob(gaia_archive_query(query, asynchronous=True))


def get_gaia_archive() -> GaiaArchive:
    """Give the connection to the ESA Gaia archive.

    Every query passes through the gateway, so the archive's rate limit and
    its pause after repeated trouble apply.

    Returns
    -------
    archive : `GaiaArchive`
        An object with ``launch_job(query)`` and ``launch_job_async(query)``;
        ``get_results()`` on the result gives the rows.
    """
    return GaiaArchive()


def gaia_archive_load_data(**arguments: Any) -> Any:
    """Download Gaia data products (such as XP spectra) from the archive.

    Parameters
    ----------
    **arguments
        Passed to ``Gaia.load_data``.

    Returns
    -------
    files : `dict`
        The reply of ``Gaia.load_data``.
    """

    def ask(host: str) -> Any:
        """Send the download request to the archive.

        Returns
        -------
        files : `dict`
            The reply.
        """
        from astroquery.gaia import Gaia

        return Gaia.load_data(**arguments)

    # A 404 here means this source has no such product.
    return call_service(GAIA_XP, ask, not_found_is_answer=True)


def skybot_cone_search(
    coordinate: SkyCoord, radius: Any, epoch: Any, *, location: str, position_error: Any, client: Any = None
) -> Any:
    """Ask SkyBoT for the known asteroids in a cone at a time.

    Parameters
    ----------
    coordinate : `astropy.coordinates.SkyCoord`
        The center.
    radius : `astropy.units.Quantity`
        The radius.
    epoch : `astropy.time.Time`
        The time.
    location : `str`
        The observatory code.
    position_error : `astropy.units.Quantity`
        The position error to allow.
    client : `object`, optional
        A stand-in for astroquery's ``Skybot``, for a test.

    Returns
    -------
    table : `astropy.table.Table` or `None`
        The asteroids.
    """
    require_online("SkyBoT")
    if client is None:
        from astroquery.imcce import Skybot

        client = Skybot
    return call_service(
        SKYBOT,
        lambda host: client.cone_search(
            coordinate, radius, epoch, location=location, position_error=position_error
        ),
    )


class AstrometryNetSolver:
    """The online Astrometry.net solver, asked through the gateway.

    Parameters
    ----------
    api_key : `str`, optional
        The service's API key.
    """

    def __init__(self, api_key: str | None = None) -> None:
        """Build the astroquery client.

        Parameters
        ----------
        api_key : `str`, optional
            The service's API key.
        """
        from astroquery.astrometry_net import AstrometryNet

        self._client = AstrometryNet()
        if api_key:
            self._client.api_key = api_key

    def solve_from_source_list(self, x: list, y: list, width: int, height: int, **kwargs: Any) -> Any:
        """Solve from the star positions.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The solution.
        """
        return call_service(
            ASTROMETRY_NET,
            lambda host: self._client.solve_from_source_list(x, y, width, height, **kwargs),
            not_found_is_answer=True,
        )

    def solve_from_image(self, image_path: str, **kwargs: Any) -> Any:
        """Solve from an uploaded image.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The solution.
        """
        return call_service(
            ASTROMETRY_NET,
            lambda host: self._client.solve_from_image(image_path, **kwargs),
            not_found_is_answer=True,
        )


def open_download(url: str, *, timeout_seconds: float | None = None) -> Any:
    """Open a file download, as one request to the file's host.

    Parameters
    ----------
    url : `str`
        An ``https://`` address.
    timeout_seconds : `float`, optional
        How long to wait for the host; the downloads' default otherwise.

    Returns
    -------
    response : `http.client.HTTPResponse`
        The open reply, to read in chunks and close.
    """
    host = urllib.parse.urlparse(url).hostname or "downloads"
    service = replace(DOWNLOADS, hosts=(host,))
    timeout = DOWNLOADS.timeout_seconds if timeout_seconds is None else timeout_seconds
    return call_service(
        service,
        lambda _host: urllib.request.urlopen(url, timeout=timeout),  # ruff: ignore[suspicious-url-open-usage] -- the caller checks https
        not_found_is_answer=True,
    )


def check_siril_gaia_catalog_server() -> None:
    """Prove that the server Siril asks for Gaia stars answers.

    Opens a TCP connection to the server and closes it at once. A timeout
    pauses the host for every process, so later calls raise
    `ServicePausedError` without sending anything. An offline launch raises
    `OfflineModeError`, and a failed or timed-out connection raises `OSError`.
    """

    def ask(host: str) -> None:
        """Connect to one host and close the connection."""
        with socket.create_connection((host, 80), timeout=SIRIL_GAIA_CATALOG.timeout_seconds):
            pass

    call_service(SIRIL_GAIA_CATALOG, ask)
