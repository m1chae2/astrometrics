"""Purpose: The one gate every request to an outside service passes through.

Description: `call_service` runs one question to a named service
(`services.SERVICES`) under the rules that protect other people's servers and
this program's own run time:

1. A launch that sends nothing off the computer refuses at once.
2. Hosts that are paused after trouble are skipped. When every host of the
   service is paused, `ServicePausedError` is raised with no network use, and
   the caller falls back to a stored answer (see ``catalog_cache``).
3. The host's rate limit is applied, across all processes (``host_state``).
4. The question is asked of the hosts in order: the preferred one (the
   ``[Catalogs]`` setting for SIMBAD and VizieR, or the one that answered last)
   first. A host that cannot be reached, is too busy, or has a server error
   hands the question to the next host. A host that answers "too busy" is first
   asked again after a short wait.
5. Trouble is recorded for the host: a 429 or 503 pauses it (for the
   ``Retry-After`` time when the host gives one, otherwise for a growing time),
   as do repeated timeouts or refusals. One good answer clears the pause.

Everything is also counted as a `ServiceEvent`. A job that opens
`catalog_use_scope` can then say "12 live, 340 stored, SIMBAD paused 4 min".
`merge_identical_requests` makes two threads that ask the same question at the
same time share one request.
"""

import contextlib
import contextvars
import logging
import queue
import threading
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any, TypeVar

from astrometricslib._internal.drivers.outside_services import host_state
from astrometricslib._internal.drivers.outside_services.failures import (
    Failure,
    FailureKind,
    ServicePausedError,
    ServiceTimeoutError,
    classify_failure,
)
from astrometricslib._internal.drivers.outside_services.services import (
    RETRY_AFTER_CAP_SECONDS,
    SERVICES,
    OutsideService,
)
from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.foundation.offline import OfflineModeError, require_online
from astrometricslib.utilities.exceptions import ONLINE_QUERY_ERRORS

logger = logging.getLogger(__name__)

T = TypeVar("T")

# The errors a request may raise that the gateway records and sorts.
_REQUEST_ERRORS = (ExternalServiceError, RuntimeError, ValueError, KeyError, *ONLINE_QUERY_ERRORS)

# The wait function. Tests replace it so no test sleeps.
_sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class ServiceEvent:
    """One thing the gateway did for a run's counters.

    Attributes
    ----------
    service, host : `str`
        Where it happened.
    kind : `str`
        ``"live"`` (a request was sent), ``"failed"`` (it failed),
        ``"skipped"`` (a host or service was not asked because it was paused or
        the launch is offline), ``"paused"`` (a pause started) or ``"waited"``
        (the rate limit made the caller wait).
    seconds : `float`
        The pause started, the pause still left (for ``"skipped"``) or the
        wait; 0 otherwise.
    """

    service: str
    host: str
    kind: str
    seconds: float = 0.0


_event_sink: contextvars.ContextVar[list[ServiceEvent] | None] = contextvars.ContextVar(
    "outside_service_events", default=None
)


@contextlib.contextmanager
def record_service_events(events: list[ServiceEvent]) -> Generator[None]:
    """Collect the gateway's events inside a ``with`` block.

    Parameters
    ----------
    events : `list` [`ServiceEvent`]
        The list the events are appended to. Threads started from inside the
        block with a copied context append to it too.

    Yields
    ------
    None
    """
    token = _event_sink.set(events)
    try:
        yield
    finally:
        _event_sink.reset(token)


def _note(service: OutsideService, host: str, kind: str, seconds: float = 0.0) -> None:
    """Add one event to the open list, if there is one.

    Parameters
    ----------
    service : `OutsideService`
        The service.
    host, kind : `str`
        As in `ServiceEvent`.
    seconds : `float`, optional
        As in `ServiceEvent`.
    """
    sink = _event_sink.get()
    if sink is not None:
        sink.append(ServiceEvent(service.name, host, kind, seconds))


# The host that last answered, for each service, and the preference set
# then. A changed setting drops the memory so the new preference takes effect.
_last_answered: dict[str, str] = {}
_preference_when_remembered: dict[str, str] = {}
_memory_lock = threading.Lock()


def _preferred_host(service: OutsideService) -> str:
    """Name the host the settings ask first.

    Returns
    -------
    host : `str`
        The ``[Catalogs]`` choice for SIMBAD and VizieR; the first host
        otherwise.
    """
    configuration = get_configuration()
    if service.name == "SIMBAD":
        return configuration.get_simbad_preferred_server()
    if service.name == "VizieR":
        return configuration.get_vizier_preferred_server()
    return service.hosts[0]


def hosts_in_order(service: OutsideService | str) -> list[str]:
    """List a service's hosts in the order to ask them.

    Parameters
    ----------
    service : `OutsideService` or `str`
        The service or its name.

    Returns
    -------
    hosts : `list` [`str`]
        The host that last answered first, unless the setting changed;
        then the preferred host; then the rest.
    """
    service = SERVICES[service] if isinstance(service, str) else service
    preferred = _preferred_host(service)
    with _memory_lock:
        if _preference_when_remembered.get(service.name) != preferred:
            _last_answered.pop(service.name, None)
        hosts = [preferred, *(host for host in service.hosts if host != preferred)]
        remembered = _last_answered.get(service.name)
    if remembered in hosts:
        hosts.remove(remembered)
        hosts.insert(0, remembered)
    return hosts


def _remember(service: OutsideService, host: str) -> None:
    """Remember the host that answered, so the next call asks it first.

    Parameters
    ----------
    service : `OutsideService`
        The service.
    host : `str`
        The host that answered.
    """
    preferred = _preferred_host(service)
    with _memory_lock:
        _last_answered[service.name] = host
        _preference_when_remembered[service.name] = preferred


def _ask_host[T](service: OutsideService, host: str, ask: Callable[[str], T]) -> T:
    """Send one question to one host: rate limit, request, repeat when busy.

    Parameters
    ----------
    service : `OutsideService`
        The service.
    host : `str`
        The host to ask.
    ask : `Callable`
        Sends the question to the named host and returns the answer.

    Returns
    -------
    answer : `object`
        What `ask` returned.
    """
    attempt = 0
    while True:
        wait = host_state.reserve_slot(host, service.requests_per_second)
        if wait > 0:
            _note(service, host, "waited", wait)
            _sleep(wait)
        _note(service, host, "live")
        try:
            return ask(host)
        except _REQUEST_ERRORS as error:
            failure = classify_failure(error)
            if (
                failure.kind is FailureKind.BUSY
                and failure.retry_after_seconds is None
                and attempt < service.busy_retries
            ):
                attempt += 1
                logger.info("%s server %s is too busy; asking again shortly.", service.name, host)
                _sleep(service.busy_wait_seconds)
                continue
            raise


def _record_failure(service: OutsideService, host: str, error: BaseException, failure: Failure) -> None:
    """Count a failure against a host and log a pause if one starts.

    Parameters
    ----------
    service : `OutsideService`
        The service.
    host : `str`
        The host that failed.
    error : `BaseException`
        What it raised.
    failure : `Failure`
        The error's classification.
    """
    _note(service, host, "failed")
    if not failure.host_trouble:
        return
    pause = host_state.record_failure(
        host,
        error_text=str(error),
        immediate=failure.kind in (FailureKind.RATE_LIMITED, FailureKind.BUSY),
        retry_after_seconds=(
            None
            if failure.retry_after_seconds is None
            else min(failure.retry_after_seconds, RETRY_AFTER_CAP_SECONDS)
        ),
        failures_before_backoff=service.failures_before_backoff,
        backoff_base_seconds=service.backoff_base_seconds,
        backoff_cap_seconds=service.backoff_cap_seconds,
    )
    if pause > 0:
        _note(service, host, "paused", pause)
        logger.warning(
            "%s host %s is paused for %.0f s (%s): %s", service.name, host, pause, failure.kind.value, error
        )


def call_service[T](service: OutsideService | str, ask: Callable[[str], T]) -> T:
    """Ask an outside service one question under the gateway's rules.

    Parameters
    ----------
    service : `OutsideService` or `str`
        The service or its name.
    ask : `Callable` [[`str`], `object`]
        Sends the question to the host whose name it is given and returns the
        answer. It may be called once per host, and again after a busy reply.

    Returns
    -------
    answer : `object`
        What `ask` returned for the host that answered.

    Raises
    ------
    OfflineModeError
        If this launch sends nothing off the computer.
    ServicePausedError
        If every host of the service is paused. Nothing was sent.
    ExternalServiceError
        If the service lists no host. When no host answers, what `ask` raised
        for the last host is raised instead.
    """
    service = SERVICES[service] if isinstance(service, str) else service
    try:
        require_online(service.name)
    except OfflineModeError:
        _note(service, service.hosts[0], "skipped")
        raise
    candidates: list[str] = []
    shortest_pause = 0.0
    for host in hosts_in_order(service):
        remaining = host_state.pause_remaining(host)
        if remaining > 0:
            _note(service, host, "skipped", remaining)
            shortest_pause = remaining if shortest_pause == 0 else min(shortest_pause, remaining)
        else:
            candidates.append(host)
    if not candidates:
        raise ServicePausedError(
            f"{service.name} is paused for {shortest_pause:.0f} s more after trouble; nothing was sent."
        )
    for position, host in enumerate(candidates):
        try:
            answer = _ask_host(service, host, ask)
        except _REQUEST_ERRORS as error:
            failure = classify_failure(error)
            _record_failure(service, host, error, failure)
            if position == len(candidates) - 1 or not failure.host_trouble:
                raise
            logger.warning(
                "%s server %s could not be used (%s); trying the other one: %s",
                service.name,
                host,
                failure.kind.value,
                error,
            )
            continue
        host_state.record_success(host)
        _remember(service, host)
        return answer
    raise ExternalServiceError(
        f"No {service.name} host is configured."
    )  # unreachable: the list is never empty


_in_flight: dict[str, tuple[threading.Event, list[Any]]] = {}
_in_flight_lock = threading.Lock()


def merge_identical_requests[T](key: str, run: Callable[[], T]) -> T:
    """Let threads that ask the same question at once share one request.

    The first caller runs `run`; the others wait for it and get its answer, or
    its error. A question asked after the first has finished runs again.

    Parameters
    ----------
    key : `str`
        Names the question. Two callers with the same key must want the same
        answer.
    run : `Callable`
        Asks the question.

    Returns
    -------
    answer : `object`
        The shared answer.
    """
    with _in_flight_lock:
        entry = _in_flight.get(key)
        leader = entry is None
        if leader:
            entry = (threading.Event(), [])
            _in_flight[key] = entry
    done, outcome = entry  # type: ignore[misc]
    if not leader:
        done.wait()
        kind, value = outcome[0]
        if kind == "error":
            raise value
        return value
    try:
        outcome.append(("ok", run()))
    except BaseException as error:
        outcome.append(("error", error))
        raise
    finally:
        with _in_flight_lock:
            _in_flight.pop(key, None)
        done.set()
    return outcome[0][1]


def run_with_deadline[T](function: Callable[[], T], timeout_seconds: float) -> T:
    """Run a function on a helper thread and stop waiting after a deadline.

    A stalled connection cannot block the caller for ever. The thread is
    abandoned, not stopped, when the deadline passes. It runs in a copy of the
    caller's context, so it sees the caller's run state and event list.

    Parameters
    ----------
    function : `Callable`
        The work.
    timeout_seconds : `float`
        How long to wait.

    Returns
    -------
    result : `object`
        What the function returned.

    Raises
    ------
    ServiceTimeoutError
        If the function has not finished in time.
    """
    results: queue.Queue = queue.Queue(maxsize=1)

    def work() -> None:
        """Run the function and hand its result or error to the caller."""
        try:
            results.put((True, function()))
        except BaseException as error:
            logger.debug("The background request raised an error.", exc_info=True)
            results.put((False, error))

    threading.Thread(target=contextvars.copy_context().run, args=(work,), daemon=True).start()
    try:
        succeeded, payload = results.get(timeout=timeout_seconds)
    except queue.Empty:
        raise ServiceTimeoutError(f"Timed out after {timeout_seconds}s") from None
    if succeeded:
        return payload
    raise payload
