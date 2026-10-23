"""Purpose: Keep a subset-stack job and a library stack from running together.

Description: A subset-stack job (see `subset_stack.py`) reads the library
stack's folder, its stored noise apertures and its list of input frames, and
it uses a Siril work folder named after the target. A library stack of the
same target rewrites those same files. If both ran at once, the subset job
could measure a half-written stack or a half-written aperture file. Two
subset jobs of one target would also share their working files.

Two kinds of guard are kept as lock files in the locks folder (see
`locks_directory`), so they are respected by every program on the computer:

- `subset_job_guard` is held by a subset-stack job. Only one can run for a
  target, and none can run while a library stack of that target runs.
- `library_stack_guard` is held by a library stack. Library stacks of one
  target do not block each other (a luminance stack and a spectral stack of
  the same target may run together), so each takes a lock file of its own.
  It is refused while a subset job of the target runs.

Neither guard waits. A refused job raises a `ConflictError` that says what is
running, because a subset series can take hours and waiting would hide the
reason a job seems stuck.

The order that makes this safe with plain exclusive locks: a library stack
takes its own file first and then checks for a subset job; a subset job takes
its lock first and then tries every library file. If the two start together,
both may refuse, but they never both run.
"""

import contextlib
import contextvars
import glob
import hashlib
import os
import re
import uuid
from collections.abc import Generator

from astrometricslib.foundation.errors import ConflictError
from astrometricslib.foundation.paths import locks_directory
from astrometricslib.foundation.storage.process_locks import DeviceInUseError, file_lock

__all__ = [
    "library_stack_guard",
    "subset_job_guard",
]

# The target ids whose subset guard this work already holds, so a job that
# runs two subset steps one after the other (the growth series, then the night
# contributions) takes the lock once.
_held_subset_targets: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "held_subset_targets", default=()
)


def _lock_prefix(target_id: str) -> str:
    """Name the start of every lock file of one target.

    Parameters
    ----------
    target_id : `str`
        The target's id.

    Returns
    -------
    prefix : `str`
        The full path start, ``<locks folder>/stacking_<safe name>_<hash>``.
        The safe name keeps only letters, digits, dots, underscores and
        hyphens, so it is safe in a file name and in a glob pattern. The hash
        of the real id keeps two ids that differ only in the removed
        characters apart.
    """
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", target_id)
    digest = hashlib.sha1(target_id.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return os.path.join(locks_directory(), f"stacking_{safe_name}_{digest}")


def _remove_quietly(path: str) -> None:
    """Delete a file, ignoring a failure.

    Parameters
    ----------
    path : `str`
        The file to delete.
    """
    with contextlib.suppress(OSError):
        os.unlink(path)


@contextlib.contextmanager
def library_stack_guard(target_id: str) -> Generator[None]:
    """Hold a library stack's guard for the length of the stack.

    Parameters
    ----------
    target_id : `str`
        The target being stacked.

    Yields
    ------
    None
        The caller's stack runs while the guard is held.

    Raises
    ------
    ConflictError
        If a subset-stack job of this target is running.
    """
    prefix = _lock_prefix(target_id)
    own_lock = f"{prefix}__library_{os.getpid()}_{uuid.uuid4().hex[:8]}.lock"
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(file_lock(own_lock))
            try:
                with file_lock(f"{prefix}__subset.lock"):
                    pass  # only a test: the subset lock is free, so no subset job runs
            except DeviceInUseError as error:
                raise ConflictError(
                    f"A subset-stack job of {target_id!r} is running, so its library stack cannot be "
                    "made now. Wait for the job to finish, then stack again.",
                    details={"target_id": target_id},
                ) from error
            yield
    finally:
        with contextlib.suppress(OSError):
            os.unlink(own_lock)


@contextlib.contextmanager
def subset_job_guard(target_id: str) -> Generator[None]:
    """Hold a subset-stack job's guard for the length of the job.

    Parameters
    ----------
    target_id : `str`
        The target whose frames the job stacks.

    Yields
    ------
    None
        The job runs while the guard is held. A guard this work already holds
        is reused.

    Raises
    ------
    ConflictError
        If another subset job of this target is running, or a library stack of
        this target is running.
    """
    if target_id in _held_subset_targets.get():
        yield
        return
    prefix = _lock_prefix(target_id)
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(file_lock(f"{prefix}__subset.lock"))
        except DeviceInUseError as error:
            raise ConflictError(
                f"Another subset-stack job of {target_id!r} is already running. Wait for it to "
                "finish before starting another.",
                details={"target_id": target_id},
            ) from error
        for library_lock in sorted(glob.glob(f"{prefix}__library_*.lock")):
            try:
                stack.enter_context(file_lock(library_lock))
                # A free file was left by a library stack that did not stop
                # cleanly. This job holds it now, so removing it is safe.
                stack.callback(_remove_quietly, library_lock)
            except DeviceInUseError as error:
                raise ConflictError(
                    f"A library stack of {target_id!r} is running, so a subset-stack job cannot start. "
                    "Wait for the stack to finish, then start the job again.",
                    details={"target_id": target_id},
                ) from error
        token = _held_subset_targets.set((*_held_subset_targets.get(), target_id))
        try:
            yield
        finally:
            _held_subset_targets.reset(token)
