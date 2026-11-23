"""Purpose: Tests for the gateway every outside request passes through.

Description: The gateway (``outside_services``) applies one rate limit per
host across processes, pauses a host after 429, 503 or repeated timeouts,
honors ``Retry-After``, chooses between a service's servers, serves stored
answers while a service is paused, and counts what happened for a run. No test
here uses the network: a fake function stands in for each request.
"""

import itertools
import multiprocessing
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import requests
from astropy.table import Table

from astrometricslib._internal.drivers import catalog_cache
from astrometricslib._internal.drivers.catalog_answer_store import write_stored_answer
from astrometricslib._internal.drivers.outside_services import (
    SIMBAD,
    VIZIER,
    FailureKind,
    ServicePausedError,
    call_service,
    classify_failure,
    gateway,
    host_state,
    merge_identical_requests,
)
from astrometricslib._internal.drivers.outside_services.failures import parse_retry_after
from astrometricslib.foundation.errors import ExternalServiceError

HARVARD = "simbad.harvard.edu"
CDS = "simbad.cds.unistra.fr"


class _Config:
    """A settings stand-in that names the preferred servers."""

    @staticmethod
    def get_simbad_preferred_server() -> str:
        """Give the preferred SIMBAD server.

        Returns
        -------
        server : `str`
            Harvard.
        """
        return HARVARD

    @staticmethod
    def get_vizier_preferred_server() -> str:
        """Give the preferred VizieR server.

        Returns
        -------
        server : `str`
            The first VizieR host.
        """
        return VIZIER.hosts[0]


@pytest.fixture(autouse=True)
def fresh_gateway(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Forget remembered servers, use fixed settings and record the waits.

    Returns
    -------
    sleeps : `list` [`float`]
        Each wait the gateway asked for, in seconds.
    """
    sleeps: list[float] = []
    monkeypatch.setattr(gateway, "_last_answered", {})
    monkeypatch.setattr(gateway, "_preference_when_remembered", {})
    monkeypatch.setattr(gateway, "get_configuration", lambda: _Config())
    monkeypatch.setattr(gateway, "_sleep", sleeps.append)
    return sleeps


def _http_error(status: int, retry_after: str | None = None) -> requests.exceptions.HTTPError:
    """Build the error `requests` raises for an HTTP status.

    Returns
    -------
    error : `requests.exceptions.HTTPError`
        An error with a response that carries the status and header.
    """
    response = requests.Response()
    response.status_code = status
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    return requests.exceptions.HTTPError(f"HTTP {status}", response=response)


def _advance(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    """Move the gateway's clock forward.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the clock.
    seconds : `float`
        How far to move it.
    """
    real_clock = host_state._clock
    monkeypatch.setattr(host_state, "_clock", lambda: real_clock() + seconds)


# --- Rate limit -------------------------------------------------------------


def test_the_rate_limit_spaces_requests_to_one_host(fresh_gateway: list[float]) -> None:
    """Five requests in a burst wait one interval each, after the first."""
    for _ in range(5):
        call_service(SIMBAD, lambda host: "ok")

    interval = 1.0 / SIMBAD.requests_per_second
    assert len(fresh_gateway) == 4
    assert fresh_gateway[-1] == pytest.approx(4 * interval, abs=0.1)


def test_each_host_has_its_own_rate_limit(fresh_gateway: list[float]) -> None:
    """A request to another host does not wait for the first host's slot."""
    host_state.reserve_slot("a.example", 1.0)

    assert host_state.reserve_slot("b.example", 1.0) == pytest.approx(0.0)


def _reserve_in_process(path: str, count: int, rate: float, queue: Any) -> None:
    """Take `count` slots in a separate process and report each one's time.

    Parameters
    ----------
    path : `str`
        The state file.
    count : `int`
        How many slots to take.
    rate : `float`
        Requests per second.
    queue : `multiprocessing.Queue`
        Receives the time each slot starts.
    """
    host_state.outside_state_path = lambda: Path(path)
    for _ in range(count):
        wait = host_state.reserve_slot("shared.example", rate)
        queue.put(time.time() + wait)


def test_the_rate_limit_holds_across_two_processes(tmp_path: Path) -> None:
    """Two processes taking slots together never start two in one interval."""
    path = str(tmp_path / "shared.db")
    # "spawn" is the one start method every platform offers (Windows has no
    # "fork"). A spawned child starts fresh, so it gets the state file path
    # as an argument and sets it itself in `_reserve_in_process`.
    context = multiprocessing.get_context("spawn")
    queue: Any = context.Queue()
    rate = 20.0
    processes = [context.Process(target=_reserve_in_process, args=(path, 6, rate, queue)) for _ in range(2)]
    for process in processes:
        process.start()
    # A spawned child must import this module and the library first, which
    # is slow on a busy runner, so allow a generous wait.
    starts = sorted(queue.get(timeout=120) for _ in range(12))
    for process in processes:
        process.join(timeout=30)

    gaps = [later - earlier for earlier, later in itertools.pairwise(starts)]
    assert min(gaps) >= 1.0 / rate - 0.005


# --- Pauses -----------------------------------------------------------------


def test_a_503_pauses_the_host_and_a_later_call_skips_the_network() -> None:
    """After a 503 that repeats, nothing more is sent to that service."""
    calls: list[str] = []

    def ask(host: str) -> str:
        calls.append(host)
        raise _http_error(503)

    with pytest.raises(requests.exceptions.HTTPError):
        call_service(SIMBAD, ask)
    sent = len(calls)
    with pytest.raises(ServicePausedError):
        call_service(SIMBAD, ask)

    assert sent == len(SIMBAD.hosts) * (SIMBAD.busy_retries + 1)
    assert len(calls) == sent


def test_a_429_with_retry_after_is_honored_and_not_repeated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pause lasts as long as the host asked, then the host is asked."""
    calls: list[str] = []

    def ask(host: str) -> str:
        calls.append(host)
        raise _http_error(429, "120")

    with pytest.raises(requests.exceptions.HTTPError):
        call_service(SIMBAD, ask)

    assert len(calls) == len(SIMBAD.hosts)  # no repeat on the same host
    assert host_state.pause_remaining(HARVARD) == pytest.approx(120.0, abs=1.0)
    _advance(monkeypatch, 119.0)
    with pytest.raises(ServicePausedError):
        call_service(SIMBAD, lambda host: "ok")
    _advance(monkeypatch, 122.0)
    assert call_service(SIMBAD, lambda host: "ok") == "ok"


def test_without_retry_after_the_pause_doubles_up_to_the_cap() -> None:
    """Each further 503 in a row doubles the pause, up to the cap."""
    pauses = [
        host_state.record_failure(
            "grow.example",
            error_text="503",
            immediate=True,
            retry_after_seconds=None,
            failures_before_backoff=2,
            backoff_base_seconds=SIMBAD.backoff_base_seconds,
            backoff_cap_seconds=SIMBAD.backoff_cap_seconds,
        )
        for _ in range(7)
    ]

    assert pauses[:4] == pytest.approx([60.0, 120.0, 240.0, 480.0])
    assert pauses[4:] == pytest.approx([SIMBAD.backoff_cap_seconds] * 3)


def test_one_timeout_starts_no_pause_but_two_do() -> None:
    """A single timeout may be a hiccup; repeated ones pause the host."""

    def ask(host: str) -> str:
        raise TimeoutError("slow")

    for _ in range(SIMBAD.failures_before_backoff - 1):
        with pytest.raises(TimeoutError):
            call_service(VIZIER, ask)
    assert all(host_state.pause_remaining(host) == 0 for host in VIZIER.hosts)
    with pytest.raises(TimeoutError):
        call_service(VIZIER, ask)
    assert all(host_state.pause_remaining(host) > 0 for host in VIZIER.hosts)


def test_a_bad_reply_never_pauses() -> None:
    """A reply that arrived and was unusable says nothing against the host."""
    for _ in range(5):
        with pytest.raises(ValueError, match="bad"):
            call_service(SIMBAD, lambda host: (_ for _ in ()).throw(ValueError("bad reply")))

    assert host_state.pause_remaining(HARVARD) == 0


def test_a_good_answer_clears_the_failures() -> None:
    """One answer resets the count of failures in a row."""
    with pytest.raises(TimeoutError):
        call_service(VIZIER, lambda host: (_ for _ in ()).throw(TimeoutError("slow")))
    call_service(VIZIER, lambda host: "ok")

    assert host_state.read_host_state(VIZIER.hosts[0]).failures == 0


# --- Server choice ----------------------------------------------------------


def test_the_other_server_is_asked_when_the_first_is_down() -> None:
    """A refused connection hands the question to the other host."""
    asked: list[str] = []

    def ask(host: str) -> str:
        asked.append(host)
        if host == HARVARD:
            raise ConnectionError("refused")
        return "rows"

    assert call_service(SIMBAD, ask) == "rows"
    assert call_service(SIMBAD, ask) == "rows"
    assert asked == [HARVARD, CDS, CDS]


def test_a_paused_host_is_skipped_and_the_other_is_used() -> None:
    """With Harvard paused, the question goes straight to Strasbourg."""
    host_state.record_failure(
        HARVARD,
        error_text="x",
        immediate=True,
        retry_after_seconds=300.0,
        failures_before_backoff=2,
        backoff_base_seconds=60.0,
        backoff_cap_seconds=900.0,
    )
    asked: list[str] = []

    call_service(SIMBAD, lambda host: asked.append(host) or "ok")

    assert asked == [CDS]


# --- Classification ---------------------------------------------------------


def test_failures_are_classified_from_the_error_and_its_causes() -> None:
    """Status codes, timeouts and refusals are told apart through causes."""
    assert classify_failure(_http_error(429, "30")).kind is FailureKind.RATE_LIMITED
    assert classify_failure(_http_error(429, "30")).retry_after_seconds == pytest.approx(30.0)
    assert classify_failure(_http_error(503)).kind is FailureKind.BUSY
    assert classify_failure(_http_error(502)).kind is FailureKind.SERVER_ERROR
    assert classify_failure(requests.exceptions.ReadTimeout("slow")).kind is FailureKind.TIMEOUT
    assert classify_failure(requests.exceptions.ConnectionError("refused")).kind is FailureKind.CONNECTION
    assert classify_failure(ValueError("bad")).kind is FailureKind.OTHER
    wrapped = OSError("wrapped")
    wrapped.__cause__ = requests.exceptions.ConnectTimeout("no connect")
    assert classify_failure(wrapped).kind is FailureKind.TIMEOUT
    assert classify_failure(requests.exceptions.HTTPError("Error 503: busy")).kind is FailureKind.BUSY


def test_retry_after_reads_seconds_and_dates() -> None:
    """The header may be a number of seconds or an HTTP date."""
    assert parse_retry_after("90") == pytest.approx(90.0)
    assert parse_retry_after(None) is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412480.0 - 30) == pytest.approx(30.0)
    assert parse_retry_after("soon") is None


# --- Stored answers and counters --------------------------------------------


def test_a_paused_service_gives_the_stored_answer_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    """During a pause, an old stored answer stands in and the log says so."""
    write_stored_answer("SIMBAD", "q", Table({"a": [1]}), "SIMBAD")
    host_state.record_failure(
        HARVARD,
        error_text="503",
        immediate=True,
        retry_after_seconds=600.0,
        failures_before_backoff=2,
        backoff_base_seconds=60.0,
        backoff_cap_seconds=900.0,
    )
    host_state.record_failure(
        CDS,
        error_text="503",
        immediate=True,
        retry_after_seconds=600.0,
        failures_before_backoff=2,
        backoff_base_seconds=60.0,
        backoff_cap_seconds=900.0,
    )

    with catalog_cache.catalog_use_scope(refresh=True) as log:
        table = catalog_cache.answer_through_cache(
            "SIMBAD", "q", 180, lambda: call_service(SIMBAD, lambda host: Table({"a": [2]}))
        )

    assert table is not None
    assert list(table["a"]) == [1]
    assert log.count("stale") == 1
    assert log.requests_sent() == 0
    assert log.requests_skipped("SIMBAD") == 2
    assert log.pause_seconds("SIMBAD") == pytest.approx(600.0, abs=2.0)
    assert any("SIMBAD was paused" in sentence for sentence in log.warnings())
    assert "SIMBAD paused 10 min" in log.summary()


def test_counters_count_live_stored_and_waits() -> None:
    """A scope lists the requests sent, the stored answers and the waits."""
    with catalog_cache.catalog_use_scope() as log:
        for index in range(3):
            catalog_cache.answer_through_cache(
                "SIMBAD", f"k{index}", 180, lambda: call_service(SIMBAD, lambda host: Table({"a": [1]}))
            )
        catalog_cache.answer_through_cache("SIMBAD", "k0", 180, lambda: Table({"a": [9]}))

    assert log.count("live") == 3
    assert log.count("stored") == 1
    assert log.requests_sent("SIMBAD") == 3
    assert "3 live, 1 stored" in log.summary()


def test_identical_requests_at_once_share_one_call() -> None:
    """Two threads asking the same question make one request."""
    calls: list[int] = []
    release = threading.Event()
    started = threading.Event()

    def slow() -> str:
        calls.append(1)
        started.set()
        release.wait(timeout=5)
        return "answer"

    results: list[str] = []
    first = threading.Thread(target=lambda: results.append(merge_identical_requests("k", slow)))
    first.start()
    started.wait(timeout=5)
    second = threading.Thread(target=lambda: results.append(merge_identical_requests("k", slow)))
    second.start()
    time.sleep(0.05)
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert results == ["answer", "answer"]
    assert len(calls) == 1


def test_an_error_reaches_every_waiting_thread() -> None:
    """The threads that waited get the leader's error."""
    release = threading.Event()
    started = threading.Event()

    def failing() -> str:
        started.set()
        release.wait(timeout=5)
        raise ExternalServiceError("down")

    errors: list[BaseException] = []

    def run() -> None:
        try:
            merge_identical_requests("e", failing)
        except ExternalServiceError as error:
            errors.append(error)

    first = threading.Thread(target=run)
    first.start()
    started.wait(timeout=5)
    second = threading.Thread(target=run)
    second.start()
    time.sleep(0.05)
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert len(errors) == 2
