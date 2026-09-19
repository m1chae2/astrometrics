"""Generic worker-count reconciliation for concurrent batch processing.

Resolves configured or automatic worker counts for any named batch
workload against both available CPU count and available system memory,
so batch jobs dynamically scale down to safe limits and never exhaust
system resources.
"""

import logging
import os
from typing import NamedTuple

import psutil

logger = logging.getLogger(__name__)


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
    available_ram_bytes: int | None = None,
    total_ram_bytes: int | None = None,
    estimated_mb_per_worker: int = 1536,
) -> WorkerCounts:
    """Reconcile worker counts against CPU and memory capacity.

    Accepts either an explicit integer (as a string, matching how
    configparser values are read) or the string "auto" for each of
    outer_worker_setting and inner_worker_setting. When both are
    "auto", reserves roughly one core of headroom for the OS/desktop
    and splits the remainder three ways between outer and inner
    workers.

    Crucially, this also calculates a memory-based concurrency ceiling
    from real-time available physical memory. Even when CPU core count
    is high, concurrency is constrained so that total worker memory
    consumption leaves a protective headroom buffer for the host OS
    and user interface, preventing out-of-memory crashes.

    Parameters
    ----------
    outer_worker_setting : `str`
        Configured outer worker count, or ``"auto"``.
    inner_worker_setting : `str`
        Configured inner worker count, or ``"auto"``.
    cpu_count : `int`, optional
        Override for `os.cpu_count`, primarily for testing, by
        default `None` (uses `os.cpu_count`, falling back to 4).
    available_ram_bytes : `int`, optional
        Override for available system memory in bytes, primarily for testing,
        by default `None` (queries `psutil.virtual_memory().available`).
    total_ram_bytes : `int`, optional
        Override for total system memory in bytes, primarily for testing,
        by default `None` (queries `psutil.virtual_memory().total`).
    estimated_mb_per_worker : `int`, optional
        Estimated worst-case resident memory (in megabytes) consumed by
        a single worker process, by default 1536 (1.5 GB).

    Returns
    -------
    resolved_worker_counts : `WorkerCounts`
        The resolved (outer_worker_count, inner_worker_count) pair.
    """
    resolved_cpu_count = cpu_count if cpu_count is not None else (os.cpu_count() or 4)
    usable_cpu_count = max(1, resolved_cpu_count - 1)

    # 1. Resolve CPU-based candidate worker counts
    if str(outer_worker_setting).strip().lower() == "auto":
        outer_worker_count = max(1, min(4, usable_cpu_count // 3))
    else:
        outer_worker_count = max(1, int(outer_worker_setting))

    if str(inner_worker_setting).strip().lower() == "auto":
        cpu_inner_workers = max(1, usable_cpu_count // outer_worker_count)
    else:
        cpu_inner_workers = max(1, int(inner_worker_setting))

    # 2. Resolve memory-based concurrency ceiling
    try:
        if available_ram_bytes is None or total_ram_bytes is None:
            virtual_memory_info = psutil.virtual_memory()
            if available_ram_bytes is None:
                available_ram_bytes = virtual_memory_info.available
            if total_ram_bytes is None:
                total_ram_bytes = virtual_memory_info.total
    except Exception as memory_query_error:
        logger.debug("Failed to query system memory via psutil: %s", memory_query_error)
        # Conservative fallback assumption if memory stats cannot be queried
        available_ram_bytes = available_ram_bytes or (8 * 1024 * 1024 * 1024)
        total_ram_bytes = total_ram_bytes or (16 * 1024 * 1024 * 1024)

    available_ram_mb = available_ram_bytes // (1024 * 1024)
    total_ram_mb = total_ram_bytes // (1024 * 1024)

    # Reserve a safety buffer for the host OS, desktop environment, and UI:
    # at least 2048 MB (2 GB) or 20% of total system RAM, whichever is larger.
    os_headroom_safety_buffer_mb = max(2048, int(total_ram_mb * 0.20))
    usable_ram_mb = max(0, available_ram_mb - os_headroom_safety_buffer_mb)
    safe_worker_ram_cap = max(1, usable_ram_mb // max(1, estimated_mb_per_worker))

    # Total concurrent processes = outer_worker_count * inner_worker_count.
    # Restrict inner_worker_count so total processes do not exceed safe limit.
    memory_inner_workers_cap = max(1, safe_worker_ram_cap // outer_worker_count)

    if str(inner_worker_setting).strip().lower() == "auto":
        inner_worker_count = min(cpu_inner_workers, memory_inner_workers_cap)
    else:
        configured_inner = int(inner_worker_setting)
        if configured_inner > memory_inner_workers_cap:
            logger.warning(
                "Configured inner worker count (%d) exceeds safe system memory capacity "
                "(%d workers at %d MB each). Capping to %d to prevent system memory exhaustion.",
                configured_inner,
                memory_inner_workers_cap,
                estimated_mb_per_worker,
                memory_inner_workers_cap,
            )
            inner_worker_count = memory_inner_workers_cap
        else:
            inner_worker_count = configured_inner

    return WorkerCounts(outer_worker_count=outer_worker_count, inner_worker_count=inner_worker_count)
