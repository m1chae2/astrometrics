"""Purpose: Tests for the INDI worker-process IPC plumbing.

Description: Exercises `IndiWorkerClient`/`IndiWorkerProxy`/`serve_requests`
against a trivial stand-in session (not real or simulated INDI hardware) run
in a real spawned child process, so these cover the actual process boundary
-- concurrent request/response correlation, a wedged call being detected and
the worker force-restarted, and a worker that died on its own being
transparently respawned on the next call. See
`backend/services/infrastructure/indi_worker.py`'s module docstring for why
this process boundary exists: `pyindi-client` does not release the GIL
during its blocking calls, so any blocking call on it freezes the whole
backend unless PyIndi runs in a process of its own.
"""

import time
from typing import Any

import pytest

from backend.services.infrastructure.indi_worker import (
    IndiWorkerClient,
    IndiWorkerProxy,
    serve_requests,
)


class _DummySession:
    """A trivial stand-in for `IndiInterface`, for exercising the IPC path."""

    def __init__(self) -> None:
        # Mirrors IndiInterface.status: a plain dict attribute some callers
        # read directly via getattr(driver, "status", {}), not a method.
        self.status = {"TEMPERATURE": "12.3"}

    def isServerConnected(self) -> bool:  # ruff: ignore[invalid-function-name] (PyIndi's own name)
        """Report a server that is down.

        Returns
        -------
        connected : `bool`
            Always `False`.
        """
        return False

    def connect_to_server_if_due(self) -> None:
        """Do nothing: there is no server to connect to."""

    def get_device_names(self) -> list[str]:
        """Report no devices.

        Returns
        -------
        names : `list` [`str`]
            An empty list.
        """
        return []

    def echo_after_delay(self, value, delay_seconds: float) -> Any:  # ruff: ignore[missing-type-function-argument]
        """Sleep `delay_seconds`, then return `value`.

        Returns
        -------
        value : `Any`
            Whatever was passed in as `value`.
        """
        time.sleep(delay_seconds)
        return value

    def sleep_forever(self) -> None:
        """Never return -- stands in for a wedged PyIndi call."""
        while True:
            time.sleep(1.0)

    def raise_an_error(self) -> None:
        """Raise a `ValueError`, to exercise the error round-trip.

        Raises
        ------
        ValueError
            Always.
        """
        raise ValueError("deliberate test failure")


def _dummy_worker_main(testing: bool, command_queue, result_queue, parent_pid: int) -> None:  # ruff: ignore[missing-type-function-argument]
    """Worker entry point used only by these tests.

    Builds a `_DummySession` instead of a real
    `IndiInterface`/`SimulatorIndiInterface`. Module-level (not a closure)
    because `multiprocessing`'s "spawn" context re-imports the target by
    name in the child process.
    """
    serve_requests(_DummySession(), command_queue, result_queue, parent_pid)


@pytest.fixture
def worker_client():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Build a real `IndiWorkerClient` backed by `_dummy_worker_main`.

    Yields
    ------
    client : `IndiWorkerClient`
        A running client, stopped automatically after the test.
    """
    client = IndiWorkerClient(worker_target=_dummy_worker_main)
    yield client
    client.stop()


def test_the_worker_process_does_not_start_until_the_first_call() -> None:
    """Constructing the client must not itself spawn the worker process.

    The worker starts on the first call, so a backend that never uses INDI
    never starts it. The spawned child re-imports the main module, which
    builds nothing (see `test_startup_lifecycle.py`).
    """
    client = IndiWorkerClient(worker_target=_dummy_worker_main)
    try:
        assert client._process is None
    finally:
        client.stop()


def test_concurrent_calls_correlate_to_the_right_response(worker_client) -> None:  # ruff: ignore[missing-type-function-argument]
    """Several in-flight calls each get back their own result, not another's.

    Dispatches calls whose delays finish in a different order than they were
    sent, so a naive FIFO (rather than request-id-keyed) implementation
    would hand back the wrong value to at least one caller.
    """
    import concurrent.futures

    calls = [("a", 0.3), ("b", 0.05), ("c", 0.15)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(worker_client.call_sync, "echo_after_delay", value, delay): value
            for value, delay in calls
        }
        for future, expected in futures.items():
            assert future.result(timeout=10) == expected


def test_a_raised_exception_round_trips_as_a_runtime_error(worker_client) -> None:  # ruff: ignore[missing-type-function-argument]
    """An exception the worker raises surfaces here as a RuntimeError."""
    with pytest.raises(RuntimeError, match="deliberate test failure"):
        worker_client.call_sync("raise_an_error")


def test_a_wedged_call_times_out_and_the_worker_is_restarted(worker_client) -> None:  # ruff: ignore[missing-type-function-argument]
    """A call that never returns is treated as wedged, not waited on forever.

    The call itself still raises, but a subsequent call must succeed
    quickly -- proving the worker was force-killed and respawned rather than
    left stuck, which would otherwise queue every future call behind it.
    """
    with pytest.raises(RuntimeError, match="timed out"):
        worker_client.call_sync("sleep_forever", timeout=1.0)

    assert worker_client.call_sync("echo_after_delay", "still alive", 0.0, timeout=10.0) == "still alive"


def test_a_dead_worker_is_respawned_transparently_on_the_next_call(worker_client) -> None:  # ruff: ignore[missing-type-function-argument]
    """If the worker process has already died, the next call just works.

    Simulates the worker crashing on its own (distinct from the
    call-times-out case above) by killing it directly, bypassing
    `call_sync`'s own timeout-driven restart path entirely.
    """
    worker_client.call_sync("echo_after_delay", "start it", 0.0, timeout=10.0)
    worker_client._process.kill()
    worker_client._process.join(timeout=5.0)
    assert not worker_client._process.is_alive()

    assert worker_client.call_sync("echo_after_delay", 42, 0.0, timeout=10.0) == 42


def test_indi_worker_proxy_forwards_any_method_name_by_attribute_access(worker_client) -> None:  # ruff: ignore[missing-type-function-argument]
    """IndiWorkerProxy stands in for a real session via plain attribute access.

    This is what `Indi*Driver` adapters call -- see
    `backend/container.py`, which hands this proxy to them in place of a
    real `IndiInterface` instance.
    """
    proxy = IndiWorkerProxy(worker_client)

    assert proxy.echo_after_delay("via proxy", 0.0) == "via proxy"


def test_a_plain_data_attribute_comes_back_as_its_value_not_a_callable(worker_client) -> None:  # ruff: ignore[missing-type-function-argument]
    """`getattr(driver, "status", {})`-style reads get the real value back.

    `hardware_operations.py` and `alignment_service.py` both read
    `IndiInterface.status` (a plain dict, not a method) directly via
    `getattr(driver, "status", {})`, bypassing the `Indi*Driver` layer --
    note no `()`, the caller never invokes what `getattr` hands back, so
    the straightforward "`__getattr__` always returns a forwarding
    callable" design (correct for an actual method call) silently handed
    back a `function` object here instead of the dict, which every caller
    of `getattr(driver, "status", {})` then tried to use as one. Reproduced
    live against the real backend before this was fixed: every telemetry
    poll failed with `'function' object has no attribute 'get'`, since
    `getattr(...)` doesn't call the thing it returns -- a unit test calling
    `call_sync("status")` directly (as this one originally did) could not
    have caught this, since that's not what the real caller does.
    """
    proxy = IndiWorkerProxy(worker_client)

    assert getattr(proxy, "status", {}) == {"TEMPERATURE": "12.3"}


def test_the_device_listing_works_through_the_worker_proxy(worker_client) -> None:  # ruff: ignore[missing-type-function-argument]
    """`IndiDiagnostics` uses only methods the proxy can forward.

    The proxy returns a forwarding function for any attribute name, so code
    that reads a plain attribute off the session (such as the connection
    manager) breaks with "'function' object has no attribute ...". This is
    the regression test for exactly that error.
    """
    from wayfindinglib.drivers.indi.diagnostics import IndiDiagnostics

    assert IndiDiagnostics(IndiWorkerProxy(worker_client)).get_devices() == []
