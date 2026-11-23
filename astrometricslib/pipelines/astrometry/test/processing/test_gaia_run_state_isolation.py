"""Purpose: Tests that each run keeps its own Gaia safety switch and tallies.

Description: The backend runs jobs on threads, so two analyses can run in one
process. These tests start two runs in two threads and check that one run's
failures do not open the other's safety switch, that one run's reset does not
reopen the other's, that the tallies stay separate, and that a thread started
inside a copied context sees its run's state.
"""

import contextvars
import threading
from collections.abc import Callable

from astrometricslib._internal.drivers.outside_services import ServicePausedError
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.pipelines.astrometry.processing import star_identifier as star_identifier_module


def _run_in_thread(work: Callable[[], None]) -> None:
    """Run work in a new thread and wait for it.

    Parameters
    ----------
    work : `Callable`
        The function to run on the thread.
    """
    thread = threading.Thread(target=work)
    thread.start()
    thread.join()


def test_two_runs_have_independent_breakers_and_statistics() -> None:
    """One run's failures and reset must not change the other run's state."""
    paused = ExternalServiceError("Gaia tile 1 failed on every service")
    paused.__cause__ = ServicePausedError("paused")
    first_started = threading.Event()
    first_may_finish = threading.Event()
    results: dict[str, object] = {}

    def first_run() -> None:
        """Trip the breaker, then wait for the second run."""
        star_identifier_module.reset_gaia_query_statistics()
        star_identifier_module._record_gaia_failure(paused)
        first_started.set()
        first_may_finish.wait(timeout=10)
        results["first_disabled"] = star_identifier_module._gaia_remote_queries_disabled()
        results["first_statistics"] = star_identifier_module.get_gaia_query_statistics()

    def second_run() -> None:
        """Reset after the first run tripped, then record a success."""
        first_started.wait(timeout=10)
        star_identifier_module.reset_gaia_query_statistics()
        results["second_disabled_at_start"] = star_identifier_module._gaia_remote_queries_disabled()
        star_identifier_module._record_gaia_success()
        results["second_statistics"] = star_identifier_module.get_gaia_query_statistics()
        first_may_finish.set()

    threads = [threading.Thread(target=first_run), threading.Thread(target=second_run)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results["second_disabled_at_start"] is False
    assert results["second_statistics"] == {"attempted": 1, "failed": 0, "circuit_breaker_tripped": False}
    assert results["first_disabled"] is True
    assert results["first_statistics"] == {
        "attempted": 1,
        "failed": 1,
        "circuit_breaker_tripped": True,
    }


def test_thread_in_copied_context_sees_the_run_state() -> None:
    """A thread in a copied context uses its run's state."""
    star_identifier_module.reset_gaia_query_statistics()
    run_context = contextvars.copy_context()
    thread = threading.Thread(
        target=run_context.run, args=(star_identifier_module._record_gaia_failure, ExternalServiceError("x"))
    )
    thread.start()
    thread.join()
    assert star_identifier_module.get_gaia_query_statistics()["failed"] == 1
