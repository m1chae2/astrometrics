"""Purpose: Run the INDI telescope-control client in its own OS process.

Description: `pyindi-client` (the SWIG-generated Python binding for libindi)
does not release Python's GIL during its blocking calls -- confirmed by
direct measurement: a background thread doing nothing but a tight sleep loop
froze for the exact duration of a `PyIndi.BaseClient.connectServer()` call
made from a different thread. Since the GIL is shared by every thread in a
process, any blocking PyIndi call freezes the whole backend, not just the
caller -- the asyncio event loop included, so `asyncio.to_thread()` (used
throughout the INDI driver layer) does not help. The only way to guarantee a
slow or stuck INDI call can never freeze the rest of the backend is to give
PyIndi a GIL of its own: a separate process.

`IndiWorkerClient` spawns that process and exposes a `call_sync()` that looks
like an ordinary (blocking) method call from the caller's side;
`IndiWorkerProxy` wraps a client so it can stand in for a real `IndiInterface`
session wherever one is expected (see `backend/container.py`), without any of
the `Indi*Driver` adapter files needing to change -- they already call
`asyncio.to_thread(self._session.<method>, ...)`, which now just blocks on a
response from the worker process instead of running PyIndi directly.

Always spawned with "spawn", never "fork" -- see
`astrometricslib/utilities/parallel_batch.py`'s module docstring for why a
forked child inheriting already-initialized native-extension state is unsafe;
PyIndi is exactly that kind of native extension.
"""

import logging
import multiprocessing
import os
import queue
import threading
import time
import traceback
import uuid
from concurrent.futures import Future
from multiprocessing import Queue
from multiprocessing.process import BaseProcess
from typing import Any

from astrometricslib import HardwareError

logger = logging.getLogger(__name__)

DEFAULT_CALL_TIMEOUT_SECONDS = 15.0
"""How long call_sync() waits before assuming the worker is wedged, not just
slow. The slowest real call measured so far (a connection attempt to an
unreachable mDNS hostname) takes ~5s, so this leaves generous margin."""

_PARENT_LIVENESS_CHECK_SECONDS = 5.0
"""How often the worker checks that its parent process is still alive."""

_STOP_SENTINEL = None
"""Placed on the command queue to tell the worker loop to exit cleanly."""


def _worker_main(testing: bool, command_queue: Queue, result_queue: Queue, parent_pid: int) -> None:
    """Build the real INDI session and serve requests for it, in-process.

    Runs entirely inside the spawned worker process. Builds one session
    (shared by every request, exactly as the in-process code this replaces
    did) and then loops: read a request, call the named method on the
    session, and push back the result or a description of the error. Never
    raises back into `multiprocessing`'s process bootstrap -- a failure to
    even build the session is reported as an error on the first request's
    response (or, if nothing ever asks, just leaves the worker idle and
    harmless) rather than crashing silently.

    Parameters
    ----------
    testing : `bool`
        Whether to build the pure-Python `SimulatorIndiInterface` instead of
        the real `IndiInterface`.
    command_queue : `multiprocessing.Queue`
        Receives `(request_id, method_name, args, kwargs)` tuples, or the
        stop sentinel.
    result_queue : `multiprocessing.Queue`
        Receives `(request_id, result, error)` tuples, where `error` is
        `None` on success or `(type_name, message, traceback_str)` on
        failure.
    parent_pid : `int`
        PID of the backend process that spawned this worker. Checked
        periodically so an orphaned worker (parent killed without a chance
        to signal shutdown) exits on its own instead of holding a telescope
        connection open forever.
    """
    session = None
    try:
        from astrometricslib import get_configuration

        config = get_configuration()
        if testing:
            from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface

            session = SimulatorIndiInterface(config=config)
        else:
            from wayfindinglib.drivers.indi_interface import IndiInterface

            session = IndiInterface(config=config)
    except Exception:
        # This is the top of the worker process, so any error has to stop
        # here. The traceback is logged, and the loop below still runs so
        # every request gets back a clear "no session" error.
        logger.exception("INDI worker failed to build its session")

    serve_requests(session, command_queue, result_queue, parent_pid)


def serve_requests(session: Any, command_queue: Queue, result_queue: Queue, parent_pid: int) -> None:
    """Read requests and call them on `session` until told to stop.

    Separated from `_worker_main` so the request-serving loop itself --
    the actual IPC plumbing -- can be tested against a trivial stand-in
    `session` without needing real (or simulated) INDI hardware.

    Parameters
    ----------
    session : `Any`
        The object to call each request's `method_name` on. `None` is
        allowed (e.g. if building the real session failed) -- every
        request then gets back an error instead of being attempted.
    command_queue, result_queue : `multiprocessing.Queue`
        As in `_worker_main`.
    parent_pid : `int`
        As in `_worker_main`.
    """
    last_liveness_check = time.monotonic()
    while True:
        now = time.monotonic()
        if now - last_liveness_check >= _PARENT_LIVENESS_CHECK_SECONDS:
            last_liveness_check = now
            if os.getppid() != parent_pid:
                logger.warning("INDI worker's parent (pid %s) is gone; exiting.", parent_pid)
                return

        try:
            request = command_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        if request is _STOP_SENTINEL:
            return

        request_id, method_name, args, kwargs = request
        if session is None:
            result_queue.put((request_id, None, ("RuntimeError", "INDI worker has no session to call.", "")))
            continue
        try:
            attribute = getattr(session, method_name)
            # IndiWorkerProxy's __getattr__ can't tell a method from a plain
            # data attribute (e.g. IndiInterface.status, a dict some callers
            # read directly via getattr(driver, "status", {}) rather than
            # through a Indi*Driver method) -- it has no real session to
            # check against, only this worker does. Call it only if it's
            # actually callable; otherwise this *is* the answer.
            result = attribute(*args, **kwargs) if callable(attribute) else attribute
            result_queue.put((request_id, result, None))
        except Exception as call_error:
            # The raw exception may not itself be picklable (PyIndi can
            # raise SWIG/C++-originated exception types) -- send plain
            # strings instead and let the client reconstruct a RuntimeError.
            # This loop must not die, so the error is logged here and sent
            # back to the caller, which raises it on its own side.
            logger.exception("INDI worker call %s failed", method_name)
            error_tuple = (type(call_error).__name__, str(call_error), traceback.format_exc())
            result_queue.put((request_id, None, error_tuple))


class IndiWorkerClient:
    """Owns the INDI worker process and forwards calls to it.

    Lives in the main backend process. Every call blocks the calling thread
    (intended to be called via `asyncio.to_thread`, same as the code it
    replaces) until the worker responds or `timeout` elapses.
    """

    def __init__(self, testing: bool = False, worker_target: Any = None) -> None:
        """Set up the client; the worker process itself starts on first use.

        Starting the worker process is deliberately deferred to the first
        `call_sync()`, so a backend that never talks to INDI never starts
        it. `multiprocessing`'s "spawn" method re-imports the main module in
        the child process. That import builds nothing, because the backend
        starts its services in the app's lifespan and not at import (see
        `backend/main_backend.py`), so the child stays small.

        Parameters
        ----------
        testing : `bool`, optional
            Forwarded to the worker: build `SimulatorIndiInterface` instead
            of the real `IndiInterface`.
        worker_target : `Callable`, optional
            Overrides the worker process's entry point. Must be a
            module-level function (picklable by name, since the worker is
            spawned fresh) taking `(testing, command_queue, result_queue,
            parent_pid)`, the same signature as the default, `_worker_main`.
            Exists so tests can run the real IPC plumbing against a trivial
            stand-in session instead of real INDI hardware.
        """
        self._testing = testing
        self._worker_target = worker_target or _worker_main
        self._context = multiprocessing.get_context("spawn")
        self._command_queue: Queue | None = None
        self._result_queue: Queue | None = None
        self._pending: dict[str, Future] = {}
        self._pending_lock = threading.Lock()
        self._restart_lock = threading.Lock()
        self._process: BaseProcess | None = None
        self._stopped = False

    def _start_process(self) -> None:
        """Spawn (or respawn) the worker process.

        Builds a *fresh* pair of queues for every spawn, including the
        first one -- confirmed by direct reproduction that reusing a queue
        across a respawn is unsafe: if the previous worker was `kill()`ed
        (as `call_sync()` does on a timeout) while it held the queue's
        internal read lock, that lock can stay held forever, permanently
        deadlocking every future reader of that same queue object even
        though the new process is alive and otherwise healthy. A new queue
        has a fresh, never-touched lock, so this can't happen. The old
        queues (and the dispatch thread reading the old result queue) are
        simply abandoned; nothing writes to them again, so the abandoned
        dispatch thread just blocks forever on a queue nothing will ever
        put to -- harmless for a daemon thread, and only happens on the
        rare restart path, not during normal operation. Any request sent
        to the just-killed process still resolves (with a timeout error)
        on its own via that caller's own `call_sync()` timeout -- restarting
        here doesn't need to actively cancel it.
        """
        self._command_queue = self._context.Queue()
        result_queue: Queue = self._context.Queue()
        self._result_queue = result_queue

        threading.Thread(
            target=self._dispatch_results, args=(result_queue,), daemon=True, name="IndiWorkerDispatch"
        ).start()

        self._process = self._context.Process(
            target=self._worker_target,
            args=(self._testing, self._command_queue, result_queue, os.getpid()),
            name="IndiWorker",
            daemon=True,
        )
        self._process.start()

    def _dispatch_results(self, result_queue: Queue) -> None:
        """Resolve pending futures as responses arrive on `result_queue`.

        Bound to the specific queue it was started for (not
        `self._result_queue`, which a respawn reassigns) -- see
        `_start_process`'s docstring for why.
        """
        while True:
            try:
                request_id, result, error = result_queue.get()
            except OSError, EOFError, ValueError:
                # Raised when the queue itself is torn down (e.g. during
                # interpreter shutdown or after stop() closes things out).
                if self._stopped:
                    return
                continue

            with self._pending_lock:
                future = self._pending.pop(request_id, None)
            if future is None:
                continue
            if error is None:
                future.set_result(result)
            else:
                type_name, message, tb_str = error
                future.set_exception(
                    HardwareError(
                        f"INDI worker call failed ({type_name}): {message}",
                        details={"error_type": type_name, "worker_traceback": tb_str},
                    )
                )

    def call_sync(
        self, method_name: str, *args: Any, timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS, **kwargs: Any
    ) -> Any:
        """Call `method_name` on the worker's session and block for the result.

        Self-healing: if the worker process has died, it is respawned
        before the call is sent. If the call itself times out, the worker
        is assumed wedged (not just slow -- `timeout` already allows
        generous margin) and is force-killed and respawned so the *next*
        call isn't queued behind a call that will never finish; this call
        still raises.

        Parameters
        ----------
        method_name : `str`
            Name of the method to call on the worker's session object.
        *args, **kwargs
            Forwarded to that method. Must be picklable.
        timeout : `float`, optional
            Seconds to wait for a response before treating the worker as
            wedged.

        Returns
        -------
        result : `Any`
            Whatever the worker's call to `method_name` returned.

        Raises
        ------
        HardwareError
            If the call fails on the worker side, or times out.
        """
        with self._restart_lock:
            if self._process is None:
                self._start_process()
            elif not self._process.is_alive():
                logger.warning("INDI worker is not running; restarting it before this call.")
                self._start_process()

        request_id = str(uuid.uuid4())
        future: Future = Future()
        with self._pending_lock:
            self._pending[request_id] = future
        self._command_queue.put((request_id, method_name, args, kwargs))

        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            logger.exception(
                "INDI worker call to %s() did not respond within %.1fs; assuming it is wedged and "
                "restarting it.",
                method_name,
                timeout,
            )
            with self._restart_lock:
                if self._process is not None and self._process.is_alive():
                    self._process.kill()
                    self._process.join(timeout=2.0)
                self._start_process()
            raise HardwareError(
                f"INDI worker call to {method_name}() timed out after {timeout:.1f}s."
            ) from None

    def stop(self) -> None:
        """Stop the worker process cleanly.

        Also explicitly closes the current command/result queues once the
        worker process is gone. Without this, each queue's underlying
        semaphores stay open until Python's `resource_tracker` notices they
        were never released and cleans them up itself, logging a "leaked
        semaphore objects" warning at interpreter shutdown -- harmless (the
        tracker does clean them up), but closing them here is the correct,
        quiet way to release them.
        """
        self._stopped = True
        if self._command_queue is not None:
            try:
                self._command_queue.put(_STOP_SENTINEL)
            except (OSError, ValueError) as error:
                logger.debug("Could not enqueue INDI worker stop sentinel: %s", error)
        if self._process is not None:
            self._process.join(timeout=3.0)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=2.0)
        if self._command_queue is not None:
            self._command_queue.close()
            self._command_queue.join_thread()
        if self._result_queue is not None:
            self._result_queue.close()
            self._result_queue.join_thread()
        with self._pending_lock:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(HardwareError("INDI worker stopped.", retryable=False))
            self._pending.clear()


class IndiWorkerProxy:
    """Stands in for a real `IndiInterface` session, forwarding every call.

    Forwards to the worker process behind `worker_client`. Generic
    (`__getattr__`-based) rather than enumerating `IndiInterface`'s method
    surface, so it never needs to be kept in sync as that surface changes.
    Every `Indi*Driver` adapter already calls
    `asyncio.to_thread(self._session.<method>, ...)` -- handing this proxy
    in place of the real session needs no change there: the thread-pool call
    now blocks on `IndiWorkerClient.call_sync()`, a normal GIL-releasing wait
    for a response from a separate process, instead of running PyIndi
    directly.

    `__getattr__` normally returns a callable rather than a value, since a
    method call like `session.connect_to_telescope()` works either way
    (call the callable this returns) but a plain data read like
    `getattr(driver, "status", {})` -- which `hardware_operations.py` and
    `alignment_service.py` both do, reaching past the `Indi*Driver` layer
    straight at `IndiInterface.status`, a dict -- does not: the caller
    never invokes what `getattr` gave it, so returning a callable there
    would hand back a `function` object the caller then tries to use as a
    dict. `_PLAIN_ATTRIBUTE_NAMES` lists exactly the attributes known to be
    read this way; for those, `__getattr__` makes the worker call
    immediately and returns the real value instead of a callable.
    `serve_requests` only calls the named attribute if it is actually
    callable (and returns it as-is otherwise), so an entry here is also
    safe against being read either way.
    """

    _PLAIN_ATTRIBUTE_NAMES = frozenset({"status"})

    def __init__(self, worker_client: IndiWorkerClient) -> None:
        """Wrap `worker_client`, the owner of the actual worker process."""
        self._worker_client = worker_client

    def __getattr__(self, name: str) -> Any:
        """Resolve `name` against the worker's session.

        Returns
        -------
        result : `Any`
            The attribute's value directly, for a name in
            `_PLAIN_ATTRIBUTE_NAMES`; otherwise a callable that forwards to
            the worker as `name(...)` when called.
        """
        if name in self._PLAIN_ATTRIBUTE_NAMES:
            return self._worker_client.call_sync(name)

        def _forward(*args: Any, **kwargs: Any) -> Any:
            return self._worker_client.call_sync(name, *args, **kwargs)

        return _forward
