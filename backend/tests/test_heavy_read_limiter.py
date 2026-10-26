"""Purpose: Tests for the limiter that caps slow whole-catalog reads.

Description: Checks that at most a set number of heavy reads run at once,
that identical concurrent requests compute once, that a read refused after
the wait gets the retryable busy error, and that other calls are not held
up. Uses threads and fake work; no catalog is read.
"""

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrometricslib import ConflictError
from backend.services.data.stellar_service import StellarService
from backend.services.infrastructure.heavy_read_limiter import HeavyReadLimiter


def _run_threads(targets: list) -> None:
    """Start every target on its own thread and wait for all to finish."""
    threads = [threading.Thread(target=target) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)


def test_at_most_the_limit_run_at_once() -> None:
    """Different requests never compute more than the slot count together."""
    limiter = HeavyReadLimiter(maximum_concurrent=3, slot_wait_seconds=10)
    lock = threading.Lock()
    running = 0
    peak = 0

    def compute() -> int:
        """Record how many are running now, then work briefly.

        Returns
        -------
        value : `int`
            Always 1.
        """
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return 1

    _run_threads([lambda i=i: limiter.run(("k", i), compute) for i in range(12)])

    assert peak == 3


def test_identical_concurrent_requests_compute_once() -> None:
    """Requests with the same key share one computation and its answer."""
    limiter = HeavyReadLimiter(maximum_concurrent=3)
    calls = []
    answers = []
    release = threading.Event()

    def compute() -> str:
        """Wait until all requests have arrived, then answer.

        Returns
        -------
        answer : `str`
            The shared answer.
        """
        calls.append(1)
        release.wait(5)
        return "answer"

    def request() -> None:
        """Ask for the shared answer and keep it."""
        answers.append(limiter.run(("same", 1), compute))

    threads = [threading.Thread(target=request) for _ in range(8)]
    for thread in threads:
        thread.start()
    time.sleep(0.2)
    release.set()
    for thread in threads:
        thread.join(timeout=10)

    assert len(calls) == 1
    assert answers == ["answer"] * 8


def test_a_refused_read_gets_the_retryable_busy_error() -> None:
    """With every slot held, a new request is refused after the wait."""
    limiter = HeavyReadLimiter(maximum_concurrent=1, slot_wait_seconds=0.1)
    release = threading.Event()
    holder = threading.Thread(target=lambda: limiter.run("a", lambda: release.wait(5)))
    holder.start()
    time.sleep(0.1)

    with pytest.raises(ConflictError) as raised:
        limiter.run("b", lambda: 1)
    release.set()
    holder.join(timeout=10)

    assert raised.value.retryable is True
    assert raised.value.details == {"reason": "busy"}
    assert limiter.run("b", lambda: 2) == 2


def test_a_failure_is_shared_and_does_not_stick() -> None:
    """A shared error does not stick; the next call tries again."""
    limiter = HeavyReadLimiter()

    def failing() -> None:
        """Fail every time.

        Raises
        ------
        ValueError
            Always.
        """
        raise ValueError("boom")

    with pytest.raises(ValueError):
        limiter.run("x", failing)
    assert limiter.run("x", lambda: "ok") == "ok"


def test_other_calls_are_not_blocked_while_slots_are_full() -> None:
    """A call that does not use the limiter runs while all slots are held."""
    limiter = HeavyReadLimiter(maximum_concurrent=1, slot_wait_seconds=0.1)
    release = threading.Event()
    holder = threading.Thread(target=lambda: limiter.run("a", lambda: release.wait(5)))
    holder.start()
    time.sleep(0.1)

    started = time.monotonic()
    result = (lambda: "status")()
    release.set()
    holder.join(timeout=10)

    assert result == "status"
    assert time.monotonic() - started < 1


def test_stellar_service_listing_and_count_use_the_limiter() -> None:
    """Identical concurrent listing requests reach the library only once."""
    release = threading.Event()
    calls = []

    def query(**kwargs: object) -> SimpleNamespace:
        """Record the call and wait, standing in for a slow scan.

        Returns
        -------
        answer : `types.SimpleNamespace`
            One star and a total of one.
        """
        calls.append(kwargs)
        release.wait(5)
        return SimpleNamespace(stars=[{"id": "A"}], total_matching=1)

    astrometrics = SimpleNamespace(stars=SimpleNamespace(query=query))
    service = StellarService(config=MagicMock(), astrometrics=astrometrics, wayfinder=MagicMock())
    results = []

    threads = [
        threading.Thread(target=lambda: results.append(service.get_displayable_stellar_object_summaries()))
        for _ in range(5)
    ]
    threads += [
        threading.Thread(target=lambda: results.append(service.count_displayable_stellar_objects()))
        for _ in range(5)
    ]
    for thread in threads:
        thread.start()
    time.sleep(0.3)
    release.set()
    for thread in threads:
        thread.join(timeout=10)

    assert len(calls) == 2
    assert sorted(map(str, results)) == sorted(["[{'id': 'A'}]"] * 5 + ["1"] * 5)
