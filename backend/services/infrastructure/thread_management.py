"""Thread_management — lightweight background sync-task helpers.

This module provides a simple, module-level registry and helper
functions used by `sync_service.py` to track a target's background
light-frame sync thread.
"""

import logging
import threading

logger = logging.getLogger(__name__)

# Simple module-level registry to track running sync threads.
_syncing: dict[str, threading.Thread] = {}


def is_syncing(object_id: str) -> bool:
    """Return whether `object_id` has a live sync thread.

    Prunes the registry entry for `object_id` if its thread has
    finished.

    Returns
    -------
    alive : `bool`
        `True` if a tracked sync thread for `object_id` exists and
        is still running, `False` otherwise.
    """
    thread = _syncing.get(object_id)
    if not thread:
        return False
    alive = thread.is_alive()
    if not alive:
        # pop() with a default does nothing if another caller already
        # removed the entry.
        _syncing.pop(object_id, None)
    return alive
