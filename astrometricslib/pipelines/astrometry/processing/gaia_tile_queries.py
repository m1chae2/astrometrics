"""Fetches Gaia DR3 tiles from the online archives and tallies each run.

The star naming and the Gaia refit of the plate solution (see
`star_identifier`) need the Gaia stars under each image.
`make_gaia_tile_fetcher` builds the function that downloads one sky tile:
VizieR first, then the ESA Gaia archive, then VizieR again at brighter
limits. A service that keeps failing is paused by the outside-service
gateway. Each run keeps its own tallies of successes and failures, in a
context variable, and `get_gaia_query_statistics` reports them.
"""

import contextvars
import logging
import threading
from dataclasses import dataclass, field

from astrometricslib._internal.drivers import outside_services
from astrometricslib._internal.drivers.gaia_cone_driver import TileFetch
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.foundation.offline import network_is_offline
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.astrometry.processing.gaia_field_stars import (
    TileFetcher,
)

logger = logging.getLogger(__name__)


# --- Gaia Search Depth and Epoch --------------------------------------------
#
# The Gaia stars of an image are read from the tile cache down to a depth,
# the query depth. The depth is predicted from the equipment and the
# exposure (see `gaia_depth.predict_gaia_depth_from_header`). When the
# header does not give what the prediction needs, the depth is
# `GAIA_DEFAULT_MAGNITUDE_LIMIT`. After the matching, the faintest detected
# star's magnitude is estimated from the catalog-matched stars and compared
# with the depth; a fainter star records `FLAG_GAIA_DEPTH_EXCEEDED`.
GAIA_DEFAULT_MAGNITUDE_LIMIT = TunedParameter(
    name="astrometry.gaia_default_magnitude_limit",
    default=18.0,
    unit="magnitude",
    description="Gaia G magnitude limit used when the frame gives no depth prediction.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-D11",
)


# The most rows one Gaia tile download may return. A result with this many
# rows is treated as cut short, and the tile is fetched again as its four
# children (see `gaia_field_stars`).
GAIA_ROW_LIMIT = 50_000


# Gaia DR3 is downloaded from the ESA Gaia archive first (a synchronous
# query) and from VizieR second (see `drivers/gaia_cone_driver.py`). On
# 2026-11-22 a level-7 tile of the M 52 field (G < 19.6) came back from ESA
# with 8,129 stars in 2.8 s; VizieR's Harvard server needed 39.7 s for
# 24,551 stars (its circle is about three times the tile) and the CDS server
# timed out. So ESA gets a short 20 s deadline, long enough for a slow day
# and short enough that a failure falls through to VizieR quickly. An ESA
# failure alone never trips the circuit breaker; only a tile that fails on
# both services counts. The ESA archive has announced downtime from
# 2026-11-25 12:00 CET until the Gaia DR4 release on 2026-12-02. In that
# window every tile is served by VizieR, which gets 45 s per server. If both
# fail at the requested depth, VizieR is asked again up to
# GAIA_DEPTH_STEPS_DOWN times, each GAIA_DEPTH_STEP_MAGNITUDES brighter: a
# shallower answer still names the brighter stars, and the depth reached is
# recorded, so the cache never claims stars it was not given. These are
# design choices, not limits validated over many nights.
GAIA_VIZIER_TIMEOUT_SECONDS = 45


GAIA_ESA_TIMEOUT_SECONDS = 20


GAIA_DEPTH_STEP_MAGNITUDES = TunedParameter(
    name="astrometry.gaia_depth_step_magnitudes",
    default=1.0,
    unit="magnitudes",
    description="How much brighter each repeat Gaia query is than the one before it.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-08",
)


GAIA_DEPTH_STEPS_DOWN = TunedParameter(
    name="astrometry.gaia_depth_steps_down",
    default=2,
    unit="steps",
    description="Most brighter repeat Gaia queries made after the requested depth fails.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-08",
)


@dataclass
class _GaiaRunState:
    """The Gaia connection health of one run.

    Each run keeps its own copy, so two runs in one process (the backend runs
    jobs on threads) do not mix their query counts or their pause flags.

    Attributes
    ----------
    lock : `threading.Lock`
        Guards every other field, because a run's worker threads share it.
    circuit_open : `bool`
        True once a tile could not be asked because the gateway had paused
        the Gaia services after repeated trouble. The name is kept because
        the quality metrics already record it as ``circuit_breaker_tripped``.
    queries_attempted, queries_failed : `int`
        Tallies for the whole run. They let a quality summary say whether the
        catalog service was healthy: without them, a run where every query
        failed looks the same as one where the fields held no catalog stars.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    circuit_open: bool = False
    queries_attempted: int = 0
    queries_failed: int = 0


# The state used outside any run (scripts, tests), so existing callers work.
_process_default_gaia_state = _GaiaRunState()


# The current run's state. `reset_gaia_query_statistics` installs a fresh one.
# A new thread does not inherit a context variable, so a thread that works
# for a run must start inside `contextvars.copy_context()`.
_current_gaia_state: contextvars.ContextVar[_GaiaRunState | None] = contextvars.ContextVar(
    "current_gaia_state", default=None
)


def _gaia_state() -> _GaiaRunState:
    """Get the Gaia state of the current run.

    Returns
    -------
    state : `_GaiaRunState`
        The state installed for this run, or the process default when no run
        has installed one.
    """
    return _current_gaia_state.get() or _process_default_gaia_state


def _record_gaia_failure(error: BaseException) -> None:
    """Count a failed tile request, and note when the gateway had paused Gaia.

    Parameters
    ----------
    error : `BaseException`
        What the tile request raised.
    """
    state = _gaia_state()
    with state.lock:
        state.queries_attempted += 1
        state.queries_failed += 1
        if not state.circuit_open and _paused_by_gateway(error):
            state.circuit_open = True
            logger.warning(
                "Gaia remote queries are paused by the outside-service gateway (last: %s). "
                "Locally cached Gaia data is still used; SIMBAD identification is unaffected.",
                error,
            )


def _paused_by_gateway(error: BaseException) -> bool:
    """Say whether an error, or one of its causes, is a gateway pause.

    Returns
    -------
    paused : `bool`
        `True` when a `ServicePausedError` is in the chain.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, outside_services.ServicePausedError):
            return True
        current = current.__cause__ or current.__context__
    return False


def _record_gaia_success() -> None:
    """Count a tile request that was answered."""
    state = _gaia_state()
    with state.lock:
        state.queries_attempted += 1


def make_gaia_tile_fetcher() -> tuple[TileFetcher | None, str]:
    """Build the function that downloads one Gaia sky tile for this run.

    Each tile tries VizieR, then the ESA Gaia archive, both at the depth
    wanted; then VizieR again at up to `GAIA_DEPTH_STEPS_DOWN` brighter limits
    (see `gaia_cone_driver.fetch_gaia_tile`). A service that keeps failing is
    paused by the outside-service gateway, so later tiles skip it with no
    network use. Each tile counts as one success or one failure in the run's
    tallies, and the run notes when the gateway had paused Gaia.

    Returns
    -------
    fetch : `TileFetcher` or `None`
        The fetcher, or `None` when this run may not go online.
    reason : `str`
        Why there is no fetcher; empty when there is one.
    """
    from astrometricslib._internal.drivers import gaia_cone_driver

    if network_is_offline():
        return None, "this launch sends nothing off the computer"
    if _gaia_remote_queries_disabled():
        logger.warning("Gaia downloads are skipped: the gateway paused them earlier in this run.")
        return None, "Gaia downloads paused after repeated failures earlier in this run"

    def on_error(service: str, error: BaseException) -> None:
        """Log one failed attempt.

        Parameters
        ----------
        service : `str`
            The service that failed.
        error : `BaseException`
            What went wrong.
        """
        logger.info("A Gaia tile download from %s failed: %s", service, error)

    def fetch(level: int, tile: int, have_magnitude: float, want_magnitude: float) -> TileFetch:
        """Download the missing depth of one tile.

        Parameters
        ----------
        level, tile : `int`
            The tile.
        have_magnitude, want_magnitude : `float`
            The depth already cached and the depth wanted.

        Returns
        -------
        answer : `TileFetch`
            The tile's stars and how deep they are complete.

        Raises
        ------
        ExternalServiceError
            When every service failed or is paused.
        """
        try:
            answer = gaia_cone_driver.fetch_gaia_tile(
                level,
                tile,
                have_magnitude,
                want_magnitude,
                GAIA_ROW_LIMIT,
                vizier_timeout_seconds=GAIA_VIZIER_TIMEOUT_SECONDS,
                esa_timeout_seconds=GAIA_ESA_TIMEOUT_SECONDS,
                depth_step_magnitudes=float(resolve(GAIA_DEPTH_STEP_MAGNITUDES).value),
                depth_steps_down=int(resolve(GAIA_DEPTH_STEPS_DOWN).value),
                run_with_deadline=outside_services.run_with_deadline,
                on_error=on_error,
                vizier_query=gaia_cone_driver.query_gaia_tile_vizier,
                esa_query=gaia_cone_driver.query_gaia_tile_esa,
            )
        except ExternalServiceError as tile_error:
            _record_gaia_failure(tile_error)
            raise
        _record_gaia_success()
        return answer

    return fetch, ""


def get_gaia_query_statistics() -> dict[str, int | bool]:
    """Check how reliable the Gaia connection has been so far.

    Returns
    -------
    statistics : `dict` [`str`, `int` or `bool`]
        How many times the connection was attempted, how many times it failed,
        and whether the safety switch has flipped.
    """
    state = _gaia_state()
    with state.lock:
        return {
            "attempted": state.queries_attempted,
            "failed": state.queries_failed,
            "circuit_breaker_tripped": state.circuit_open,
        }


def _gaia_remote_queries_disabled() -> bool:
    """Check if the Gaia safety switch has tripped.

    Returns
    -------
    disabled : `bool`
        True if the connection failed too many times in a row.
    """
    state = _gaia_state()
    with state.lock:
        return state.circuit_open


def reset_gaia_circuit_breaker() -> None:
    """Reset the safety switch to allow trying to connect to Gaia again.

    Only the current run's state is cleared (the process default outside a
    run), so another run's breaker is not touched.
    """
    state = _gaia_state()
    with state.lock:
        state.circuit_open = False
        state.queries_attempted = 0
        state.queries_failed = 0


def reset_gaia_query_statistics() -> None:
    """Start a new run's Gaia state: fresh tallies and a closed safety switch.

    Installs a new state for the current run (the current thread's context),
    so it never changes another run's switch or counts. A run therefore
    begins with each service tried once more.
    """
    _current_gaia_state.set(_GaiaRunState())
