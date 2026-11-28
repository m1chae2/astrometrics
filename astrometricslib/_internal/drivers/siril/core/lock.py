"""Purpose: The one machine-wide lock that every Siril run takes.

Description: Siril is very demanding on the processor, so the computer runs
only a limited number of Siril programs at once. `siril_process_lock` holds
one of those slots. Every Siril run the driver starts takes it, the long pipe
session of a stack (`session`) and the short script runs
(`script_run.run_siril_script`) alike, and GraXpert's preview run takes it
too. A short one-off run that must never wait behind a stack (mount centering,
later) asks `script_run` not to wait for a slot; nothing else skips it.

`bind_lock_wait` lets a stacking run count the seconds it spent waiting, so
its timeout is not used up by the queue.
"""

import contextlib
import contextvars
import logging
import os
import threading
import time
from collections.abc import Generator

from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import ConfigurationError
from astrometricslib.foundation.paths import scratch_directory
from astrometricslib.foundation.run_abandonment import raise_if_abandoned
from astrometricslib.foundation.run_tally import RunTally, add_to_bound_tally, bind_tally

logger = logging.getLogger(__name__)

# A file lock is used to make sure only one Siril process runs at a time
# across the whole computer. Siril uses a lot of CPU power, so running
# multiple at once would overload the machine and cause tasks to time out.
# A file lock is used in the temporary directory because different parts
# of the program (like background workers and scripts) need to share it,
# and they don't share memory.
# Note: Only Siril itself is locked; other preparation steps still run
# in parallel to save time.
SIRIL_PROCESS_LOCK_PATH = os.path.join(scratch_directory(), "astrometricslib-siril.lock")

# The seconds the current stacking run has spent waiting for the Siril lock.
# They extend the run's timeout, so targets don't fail just because they had to
# wait in line for their turn. The run (`tasks.stack_frames_with_timeout`)
# makes a `RunTally`, binds it here with `bind_lock_wait` in the thread that
# stacks, and reads it back; `siril_process_lock` adds each wait to the tally
# that is bound where it runs, and to none when no run is tallying.
_bound_lock_wait: contextvars.ContextVar[RunTally | None] = contextvars.ContextVar(
    "siril_lock_wait", default=None
)


def bind_lock_wait(tally: RunTally) -> contextlib.AbstractContextManager[RunTally]:
    """Count Siril lock waits in ``tally`` inside a ``with`` block.

    Parameters
    ----------
    tally : `RunTally`
        The run's tally of waiting seconds.

    Returns
    -------
    scope : `contextlib.AbstractContextManager`
        Use it in a ``with`` statement, in the thread that stacks: a thread
        started inside the block does not see the binding.
    """
    return bind_tally(_bound_lock_wait, tally)


def _record_siril_lock_wait(wait_seconds: float) -> None:
    """Add one blocked interval to the bound run's accumulated wait.

    Parameters
    ----------
    wait_seconds : `float`
        Seconds spent blocked on this acquisition.
    """
    add_to_bound_tally(_bound_lock_wait, wait_seconds)


# Seconds a run waits for a Siril slot before it logs that it is queued.
_SIRIL_WAIT_ANNOUNCE_SECONDS = 1.0


@contextlib.contextmanager
def siril_process_lock(
    job_logger: logging.Logger | None = None,
    max_concurrent_runs: int | None = None,
    config: AppConfiguration | None = None,
    work_name: str = "",
) -> Generator[None]:
    """Hold one of a limited number of machine-wide Siril slots.

    Siril is very demanding on the CPU. If too many instances are run at
    once, the computer will slow down and tasks will fail. This function
    limits how many Siril tasks can run concurrently based on the
    `max_concurrent_jobs` setting in the configuration file. That slot
    pool is shared with photometry/spectroscopy analysis jobs (see
    `AppConfiguration.get_max_concurrent_jobs`), so a running stack and
    a running analysis compete for the same limit.

    It uses a file lock system so that all different parts of the program
    respect the same limit, and the lock is automatically released even
    if the program crashes.

    Parameters
    ----------
    job_logger : `logging.Logger`, optional
        Logger used to record that a run is waiting on another Siril
        run, so a stalled-looking job is explainable from its log.
    max_concurrent_runs : `int`, optional
        Slot count override, for benchmarking. Defaults to the
        configured `max_concurrent_jobs`.
    config : `AppConfiguration`, optional
        The configuration that sets the slot count. A caller that holds
        one passes it. Defaults to the shared configuration.
    work_name : `str`, optional
        Name of the target or job taking the slot. It is recorded next to
        the lock, and a long wait reports it with the holders it is behind
        (to ``job_logger`` too). Defaults to a name for the calling thread.

    Yields
    ------
    `None`
        Control returns to the caller holding a slot.

    Notes
    -----
    `RunAbandonedError` is raised, before or while waiting, if the run this
    thread belongs to was given up on.
    """
    from astrometricslib.foundation.storage.process_locks import acquire_resource_slot

    # A run given up on must not queue for a slot it will not use.
    raise_if_abandoned()
    slot_count = max_concurrent_runs
    configuration = None
    if slot_count is None:
        try:
            from astrometricslib.foundation.config import get_configuration

            configuration = config or get_configuration()
            slot_count = configuration.get_max_concurrent_jobs()
        except (ConfigurationError, OSError, ValueError) as configuration_error:
            # A missing configuration must not make Siril unrunnable;
            # one slot is the safe reading, matching the old behaviour.
            logger.debug("Could not read max_concurrent_jobs, using 1 slot: %s", configuration_error)
            slot_count = 1
    slot_count = max(1, int(slot_count))

    waiting_message = (
        f"Waiting for a free Siril slot ({slot_count} allowed concurrently) before starting this run..."
    )

    def _announce_wait() -> None:
        """Log that the run is queued, after the wait lasted long enough."""
        logger.info(waiting_message)
        if job_logger:
            job_logger.info(waiting_message)

    # A free slot is taken at once, so the message only appears when the
    # wait lasts past a second. Otherwise every run would read as queued.
    wait_announcer = threading.Timer(_SIRIL_WAIT_ANNOUNCE_SECONDS, _announce_wait)
    wait_announcer.daemon = True
    wait_announcer.start()

    wait_started_at = time.monotonic()
    # A stack's timeout reads this tally from another thread while this one
    # waits, so the wait must show before the slot is free. Otherwise a long
    # queue uses up the budget and the stack is abandoned while still waiting.
    bound_tally = _bound_lock_wait.get()
    stop_open_wait = bound_tally.start_open_wait() if bound_tally is not None else None
    try:
        with acquire_resource_slot(
            configuration, "job", slot_count, work_name=work_name, wait_logger=job_logger
        ):
            # Recorded whether or not the wait was long: the stacking
            # timeout adds this back to its budget, and a queue that exists
            # to protect the CPU must not convert into a cascade of
            # timeouts for the targets waiting their turn.
            # A run given up on while it waited gives the slot back at once.
            wait_announcer.cancel()
            raise_if_abandoned()
            waited_seconds = time.monotonic() - wait_started_at
            _record_siril_lock_wait(waited_seconds)
            if stop_open_wait is not None:
                stop_open_wait()
            if waited_seconds >= 1.0:
                acquired_message = f"Waited {waited_seconds:.1f}s for a Siril slot."
                logger.info(acquired_message)
                if job_logger:
                    job_logger.info(acquired_message)
            yield
    finally:
        wait_announcer.cancel()
        if stop_open_wait is not None:
            stop_open_wait()
