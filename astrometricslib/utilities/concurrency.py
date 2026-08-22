"""Generic worker-count reconciliation for concurrent batch processing.

Resolves configured or automatic worker counts for any named batch
workload against the available CPU count, so callers don't each need
to invent their own reconciliation math.
"""

import os
from typing import NamedTuple


class WorkerCounts(NamedTuple):
    """Resolved worker counts for a batch workload.

    Attributes
    ----------
    outer_worker_count : `int`
        Number of outer (top-level) concurrent worker processes.
    inner_worker_count : `int`
        Number of inner worker processes each outer worker may spawn.
    """

    outer_worker_count: int
    inner_worker_count: int


def resolve_worker_counts(
    outer_worker_setting: str,
    inner_worker_setting: str,
    cpu_count: int | None = None,
) -> WorkerCounts:
    """Reconcile an outer/inner worker-count pair against CPU count.

    Accepts either an explicit integer (as a string, matching how
    configparser values are read) or the string "auto" for each of
    outer_worker_setting and inner_worker_setting. When both are
    "auto", reserves roughly one core of headroom for the OS/desktop
    and splits the remainder three ways between outer and inner
    workers, since running outer_worker_count processes that each
    spawn inner_worker_count sub-processes multiplies out to roughly
    outer_worker_count * inner_worker_count concurrent processes.

    This is a generic reconciliation utility with no knowledge of what
    the workload actually is (targets, frames, or anything else) —
    any future batch workload can call this with its own config
    values.

    Parameters
    ----------
    outer_worker_setting : `str`
        Configured outer worker count, or ``"auto"``.
    inner_worker_setting : `str`
        Configured inner worker count, or ``"auto"``.
    cpu_count : `int`, optional
        Override for `os.cpu_count`, primarily for testing, by
        default `None` (uses `os.cpu_count`, falling back to 4).

    Returns
    -------
    resolved_worker_counts : `WorkerCounts`
        The resolved (outer_worker_count, inner_worker_count) pair.
    """
    resolved_cpu_count = cpu_count if cpu_count is not None else (os.cpu_count() or 4)
    usable_cpu_count = max(1, resolved_cpu_count - 1)

    if str(outer_worker_setting).strip().lower() == "auto":
        outer_worker_count = max(1, min(4, usable_cpu_count // 3))
    else:
        outer_worker_count = max(1, int(outer_worker_setting))

    if str(inner_worker_setting).strip().lower() == "auto":
        inner_worker_count = max(1, usable_cpu_count // outer_worker_count)
    else:
        inner_worker_count = max(1, int(inner_worker_setting))

    return WorkerCounts(outer_worker_count=outer_worker_count, inner_worker_count=inner_worker_count)
