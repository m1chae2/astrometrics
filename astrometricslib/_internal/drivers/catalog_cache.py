"""Ask every outside catalog the same way: stored answer first, live second.

Gaia DR3 star positions were already kept in sky tiles (``catalog_store``).
This module gives every other catalog the same treatment. A question to
SIMBAD, to the AAVSO Variable Star Index (VSX) or to Gaia's variability
classes is asked once for a piece of sky, and the answer is stored with the
time it was fetched (``catalog_answer_store``). The rule for one question is:

1. A stored answer that is younger than the catalog's lifetime is used. Nothing
   goes over the network.
2. Otherwise the catalog is asked live (the driver tries the preferred server,
   then the other one) and the new answer is stored.
3. If the live question fails, the stored answer is used even though it is
   old, and a warning names the catalog and the date of the answer.
4. If nothing is stored and the live question fails, `CatalogUnavailableError`
   is raised, with a warning-level message. The caller then carries on with
   what it has: stars stay "unverified" and the run is not stopped.

An unreachable service is a status, not an error, so every message here is
a warning.

Sky questions are cut into the same HEALPix tiles the Gaia cache uses
(``sky_tiles``). A tile of the right size is asked for once, whatever circle
the caller wanted, so two images of the same field share their answers even
when their centers differ by a few arcseconds. `CatalogUseLog` records each
answer's origin (stored, live, stale or unavailable) so a run can list the
warnings it hit and a test can count live calls.
"""

import contextlib
import contextvars
import logging
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import numpy as np
from astropy.table import Table, vstack

from astrometricslib._internal.drivers.catalog_answer_store import (
    StoredAnswer,
    read_stored_answer,
    write_stored_answer,
)
from astrometricslib._internal.drivers.gaia_cone_driver import tile_circle
from astrometricslib._internal.drivers.outside_services import (
    ServiceEvent,
    merge_identical_requests,
    record_service_events,
)
from astrometricslib._internal.drivers.sky_tiles import tiles_for_cone
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.utilities.exceptions import ONLINE_QUERY_ERRORS

logger = logging.getLogger(__name__)

# How long a stored answer is used before the catalog is asked again, in
# days (design estimate AST-D24, see ``validation/astrometry.md``). SIMBAD's
# names and object types change rarely: a star's name and main type are
# fixed, and a new variable-star type appears for a few stars a year. Half a
# year keeps the types current enough for the comparison-star check, and an
# old answer still stands in during an outage.
SIMBAD_ANSWER_LIFETIME_DAYS = 180

# VSX gains new variables every week, and a new listing changes whether a star
# may serve as a comparison star, so its answers are renewed more often.
VSX_ANSWER_LIFETIME_DAYS = 90

# Gaia DR3 is a frozen release: its variability classes never change, so the
# answer is kept for ten years.
GAIA_VARIABILITY_ANSWER_LIFETIME_DAYS = 3650

# The HEALPix level of a stored SIMBAD sky answer (design estimate AST-D25).
# Level 6 tiles are about 0.9 degrees across. A typical imaged field then
# needs one to four tiles, and a tile's answer stays below SIMBAD's row limit
# outside the densest parts of the Milky Way.
SIMBAD_ANSWER_TILE_LEVEL = 6

# The HEALPix level of a stored VSX or Gaia variability sky answer (design
# estimate AST-D25). Level 5 tiles are about 1.8 degrees across, close to the
# 1.5 degree field one VizieR request used to cover, so a field needs one to
# four requests.
VARIABLE_CATALOG_TILE_LEVEL = 5

CatalogOutcome = Literal["stored", "live", "stale", "unavailable"]


class CatalogUnavailableError(ExternalServiceError):
    """A catalog could not be reached and nothing is stored for the question.

    It is a status, not a fault: callers treat the stars as unverified and
    log the message at warning level.
    """

    report = "status"
    log_level = "WARNING"


@dataclass(frozen=True)
class CatalogUse:
    """What happened to one catalog question.

    Attributes
    ----------
    catalog : `str`
        The catalog's name.
    key : `str`
        The question.
    outcome : `str`
        ``"stored"`` (a fresh stored answer was used), ``"live"`` (the
        catalog was asked), ``"stale"`` (the catalog could not be reached and
        an old stored answer was used) or ``"unavailable"`` (no answer at
        all).
    fetched_at : `datetime.datetime` or `None`
        When the answer used was fetched; `None` for ``"unavailable"``.
    """

    catalog: str
    key: str
    outcome: CatalogOutcome
    fetched_at: datetime | None = None


@dataclass
class CatalogUseLog:
    """The list of catalog questions answered while a scope was open.

    Attributes
    ----------
    uses : `list` [`CatalogUse`]
        One entry per question, in order.
    service_events : `list` [`ServiceEvent`]
        What the outside-service gateway did while the scope was open: the
        requests sent, the failures, the requests refused because a service
        was paused or the launch is offline, the pauses that started and the
        waits the rate limit caused.
    """

    uses: list[CatalogUse] = field(default_factory=list)
    service_events: list[ServiceEvent] = field(default_factory=list)

    def requests_sent(self, service: str | None = None) -> int:
        """Count the requests sent over the network.

        Parameters
        ----------
        service : `str`, optional
            Count only this service's requests.

        Returns
        -------
        count : `int`
            The number of requests, including repeats and failed ones.
        """
        return sum(1 for e in self.service_events if e.kind == "live" and service in (None, e.service))

    def requests_skipped(self, service: str | None = None) -> int:
        """Count the requests refused with no network use.

        Parameters
        ----------
        service : `str`, optional
            Count only this service's requests.

        Returns
        -------
        count : `int`
            The refusals: a service paused, or a launch that is offline.
        """
        return sum(1 for e in self.service_events if e.kind == "skipped" and service in (None, e.service))

    def pause_seconds(self, service: str) -> float:
        """Give the longest pause seen for a service while the scope was open.

        Parameters
        ----------
        service : `str`
            The service's name.

        Returns
        -------
        seconds : `float`
            The longest pause started, or the longest left when a request was
            refused; 0 when the service was never paused.
        """
        return max(
            (
                e.seconds
                for e in self.service_events
                if e.service == service and e.kind in ("paused", "skipped")
            ),
            default=0.0,
        )

    def summary(self) -> str:
        """Say in one line what the questions did.

        Returns
        -------
        text : `str`
            For example ``"12 live, 340 stored; SIMBAD paused 4 min"``.
        """
        parts = [
            f"{self.count('live')} live",
            f"{self.count('stored')} stored",
            f"{self.count('stale')} stale",
        ]
        if self.count("unavailable"):
            parts.append(f"{self.count('unavailable')} unavailable")
        text = ", ".join(parts)
        paused = [
            f"{service} paused {self.pause_seconds(service) / 60:.0f} min"
            for service in dict.fromkeys(e.service for e in self.service_events)
            if self.pause_seconds(service) > 0
        ]
        skipped = self.requests_skipped()
        if paused:
            text += "; " + ", ".join(paused)
        if skipped:
            text += f" ({skipped} requests skipped)"
        return text

    def count(self, outcome: CatalogOutcome, catalog: str | None = None) -> int:
        """Count the questions with one outcome.

        Parameters
        ----------
        outcome : `str`
            One of the `CatalogOutcome` values.
        catalog : `str`, optional
            Count only this catalog's questions.

        Returns
        -------
        count : `int`
            The number of matching questions.
        """
        return sum(1 for use in self.uses if use.outcome == outcome and catalog in (None, use.catalog))

    @property
    def live_calls(self) -> int:
        """Count the questions that went to a live service.

        Returns
        -------
        count : `int`
            The number of ``"live"`` answers.
        """
        return self.count("live")

    def warnings(self) -> list[str]:
        """List one plain sentence for each question that fell short.

        Returns
        -------
        warnings : `list` [`str`]
            For each catalog, one sentence for its stale answers and one for
            its unavailable ones, with the oldest date among the stale ones.
        """
        sentences = []
        for catalog in dict.fromkeys(use.catalog for use in self.uses):
            stale = [use for use in self.uses if use.catalog == catalog and use.outcome == "stale"]
            if stale:
                oldest = min(use.fetched_at for use in stale if use.fetched_at is not None)
                sentences.append(
                    f"{catalog} could not be reached; used stored results from {oldest:%Y-%m-%d}"
                )
            if self.count("unavailable", catalog):
                sentences.append(f"{catalog} could not be reached; no stored results for part of the field")
        for service in dict.fromkeys(e.service for e in self.service_events):
            paused = [
                e for e in self.service_events if e.service == service and e.kind in ("paused", "skipped")
            ]
            if any(e.seconds > 0 for e in paused):
                sentences.append(
                    f"{service} was paused after trouble (up to {self.pause_seconds(service) / 60:.0f} min); "
                    f"{self.requests_skipped(service)} requests were not sent"
                )
        return sentences


_current_log: contextvars.ContextVar[CatalogUseLog | None] = contextvars.ContextVar(
    "catalog_use_log", default=None
)
_refresh_requested: contextvars.ContextVar[bool] = contextvars.ContextVar("catalog_refresh", default=False)


@contextlib.contextmanager
def catalog_use_scope(*, refresh: bool = False) -> Generator[CatalogUseLog]:
    """Record the catalog questions answered inside a ``with`` block.

    Parameters
    ----------
    refresh : `bool`, optional
        Ask the catalogs again even when a fresh stored answer exists. The
        old answer still stands in when the live question fails.

    Yields
    ------
    log : `CatalogUseLog`
        The log, filled as questions are answered. Scopes nest: the inner
        scope's questions are also added to the outer log.
    """
    outer = _current_log.get()
    log = CatalogUseLog()
    log_token = _current_log.set(log)
    refresh_token = _refresh_requested.set(refresh or _refresh_requested.get())
    try:
        with record_service_events(log.service_events):
            yield log
    finally:
        _current_log.reset(log_token)
        _refresh_requested.reset(refresh_token)
        if outer is not None:
            outer.uses.extend(log.uses)
            outer.service_events.extend(log.service_events)


def _note(use: CatalogUse) -> None:
    """Add one entry to the open log, if there is one.

    Parameters
    ----------
    use : `CatalogUse`
        What happened.
    """
    log = _current_log.get()
    if log is not None:
        log.uses.append(use)


def _age_days(fetched_at: datetime, now: datetime) -> float:
    """Give the age of an answer.

    Returns
    -------
    age_days : `float`
        Days between `fetched_at` and `now`.
    """
    return (now - fetched_at).total_seconds() / 86400.0


def read_fresh_answer(catalog: str, key: str, lifetime_days: float) -> StoredAnswer | None:
    """Read a stored answer only if it is still within its lifetime.

    Parameters
    ----------
    catalog, key : `str`
        The question.
    lifetime_days : `float`
        The catalog's lifetime.

    Returns
    -------
    answer : `StoredAnswer` or `None`
        The answer, or `None` when it is missing or too old. No network is
        used.
    """
    stored = read_stored_answer(catalog, key)
    if stored is None or _age_days(stored.fetched_at, datetime.now(UTC)) > lifetime_days:
        return None
    return stored


def answer_through_cache(
    catalog: str,
    key: str,
    lifetime_days: float,
    fetch: Callable[[], Table | None],
    *,
    service: str | None = None,
    allow_live: bool = True,
) -> Table | None:
    """Answer one catalog question from the store, or live when needed.

    Parameters
    ----------
    catalog : `str`
        The catalog's name, used in messages and as the store's group.
    key : `str`
        The question.
    lifetime_days : `float`
        How long a stored answer counts as fresh.
    fetch : `Callable`
        Asks the catalog live. It returns the rows, or `None` for no rows. It
        raises `ExternalServiceError` (or an online query error) when the
        catalog cannot be reached.
    service : `str`, optional
        The name stored as the answering service. Defaults to `catalog`.
    allow_live : `bool`, optional
        When `False` only a stored answer is used and no network call is
        made; a missing answer then raises `CatalogUnavailableError`.

    Returns
    -------
    table : `astropy.table.Table` or `None`
        The rows; `None` when the catalog has nothing for the question.

    Raises
    ------
    CatalogUnavailableError
        If the catalog cannot be reached and nothing is stored.
    """
    now = datetime.now(UTC)
    stored = read_stored_answer(catalog, key)
    if (
        stored is not None
        and _age_days(stored.fetched_at, now) <= lifetime_days
        and not _refresh_requested.get()
    ):
        _note(CatalogUse(catalog, key, "stored", stored.fetched_at))
        return stored.table
    failure: BaseException | None = None
    if allow_live:
        try:
            table = merge_identical_requests(f"{catalog}|{key}", fetch)
        except (ExternalServiceError, *ONLINE_QUERY_ERRORS) as live_error:
            failure = live_error
        else:
            write_stored_answer(catalog, key, table, service or catalog)
            _note(CatalogUse(catalog, key, "live", now))
            return table
    if stored is not None:
        logger.warning(
            "%s could not be reached; used stored results from %s (%.0f days old).",
            catalog,
            f"{stored.fetched_at:%Y-%m-%d}",
            _age_days(stored.fetched_at, now),
        )
        _note(CatalogUse(catalog, key, "stale", stored.fetched_at))
        return stored.table
    _note(CatalogUse(catalog, key, "unavailable"))
    reason = f": {failure}" if failure is not None else ""
    raise CatalogUnavailableError(
        f"{catalog} could not be reached; no stored results for this field{reason}"
        if allow_live
        else f"{catalog} has no stored results for this field"
    ) from failure


def _row_identity(table: Table, row_index: int, key_columns: tuple[str, ...]) -> tuple:
    """Make a hashable identity of one row from some of its columns.

    Returns
    -------
    identity : `tuple`
        The text of each key column's cell.
    """
    return tuple(str(table[column][row_index]) for column in key_columns)


def answer_for_cone(
    catalog: str,
    variant: str,
    ra_degrees: float,
    dec_degrees: float,
    radius_degrees: float,
    *,
    healpix_level: int,
    lifetime_days: float,
    fetch_circle: Callable[[float, float, float], Table | None],
    key_columns: tuple[str, ...],
    allow_live: bool = True,
) -> Table | None:
    """Answer a circle on the sky by asking for, and storing, whole tiles.

    Every tile that touches the circle is answered through
    `answer_through_cache`. The tile is asked for as the circle that holds it
    (see ``tile_circle``), so the stored answer does not depend on the circle
    the caller wanted. The tiles' rows are joined and rows seen twice (a row
    near a tile edge sits in two tile circles) are kept once. The result
    holds the rows of every touching tile, a little more than the circle.

    Parameters
    ----------
    catalog : `str`
        The catalog's name.
    variant : `str`
        What distinguishes one kind of question to this catalog from another
        (the columns asked for and the row limit), part of the stored key.
    ra_degrees, dec_degrees, radius_degrees : `float`
        The circle wanted.
    healpix_level : `int`
        The tile level.
    lifetime_days : `float`
        The catalog's lifetime.
    fetch_circle : `Callable`
        Asks the catalog live for a circle: ``(ra, dec, radius)`` in degrees.
    key_columns : `tuple` [`str`]
        The columns that identify a row, used to drop rows seen twice.
    allow_live : `bool`, optional
        When `False` only stored tiles are used.

    Returns
    -------
    table : `astropy.table.Table` or `None`
        The joined rows; `None` when no tile has any row.

    """
    pieces: list[Table] = []
    for tile in tiles_for_cone(ra_degrees, dec_degrees, radius_degrees, healpix_level):
        tile_ra, tile_dec, tile_radius = tile_circle(healpix_level, tile)

        def fetch(ra: float = tile_ra, dec: float = tile_dec, radius: float = tile_radius) -> Table | None:
            """Ask the catalog live for this tile's circle.

            Returns
            -------
            table : `astropy.table.Table` or `None`
                The rows.
            """
            return fetch_circle(ra, dec, radius)

        piece = answer_through_cache(
            catalog,
            f"{variant}|L{healpix_level}|{tile}",
            lifetime_days,
            fetch,
            allow_live=allow_live,
        )
        if piece is not None and len(piece):
            pieces.append(piece)
    if not pieces:
        return None
    joined = vstack(pieces, metadata_conflicts="silent") if len(pieces) > 1 else pieces[0]
    if len(pieces) > 1 and all(column in joined.colnames for column in key_columns):
        seen: set[tuple] = set()
        keep = np.zeros(len(joined), dtype=bool)
        for index in range(len(joined)):
            identity = _row_identity(joined, index, key_columns)
            if identity not in seen:
                seen.add(identity)
                keep[index] = True
        joined = joined[keep]
    return joined
