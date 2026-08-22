"""Thread_management — lightweight background sync-task helpers.

This module provides a simple, module-level registry and helper
functions used by `sync_service.py` to track a target's background
light-frame sync thread.
"""

import logging

logger = logging.getLogger(__name__)

# Simple module-level registry to track running sync threads.
# Mirrors an attribute that previously lived on the high-level
# interface instance.
_syncing: dict[str, object] = {}


def start_sync(object_id: str, target):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Start `target`'s light-frame sync thread and track it.

    Parameters
    ----------
    object_id : `str`
        Identifier used to register the sync thread for later
        status checks.
    target : `Target`
        Domain object providing a `sync_light_frames()` method that
        starts and returns the sync thread.

    Returns
    -------
    result : `dict`
        Dict with ``"started"`` set to `True`.
    """
    thread = target.sync_light_frames()
    try:
        _syncing[object_id] = thread
    except Exception as exc:
        logger.debug("Failed to register sync thread for '%s': %s", object_id, exc)
    return {"started": True}


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
    try:
        thread = _syncing.get(object_id)
        if not thread:
            return False
        alive = thread.is_alive()
        if not alive:
            try:
                del _syncing[object_id]
            except Exception as exc:
                logger.debug("Failed to prune finished sync thread for '%s': %s", object_id, exc)
        return alive
    except Exception:
        return False
