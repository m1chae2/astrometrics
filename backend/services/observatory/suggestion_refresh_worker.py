"""Purpose: Run the package-suggestion refresh one run at a time.

Description: The planning library's `refresh_suggestions` creates, updates and
deletes suggested observation packages, adds calibration items to tonight's
checklist and marks finished packages complete. It reads the quality numbers,
so it should run when they change: after a quality measurement run and after a
frame sync.

Those triggers can arrive in bursts (a sync measures several targets), so this
worker keeps one set of targets that changed and one background thread:

* `request` adds targets to the set and starts the thread if none is running.
  It never waits and never raises.
* The thread takes the whole set, runs the refresh for those targets, and
  looks again. Targets that arrived during the run are handled by one more run.
  So at most one run is going and one is waiting, however many triggers come.
* A request with no target names means "refresh everything". It wins over any
  named targets in the same batch.
* A refresh that fails is logged and the worker goes on. A failure never
  reaches the caller of `request`, so it cannot break an announcement.

After a run that changed something, the worker sends ``packages:changed`` with
the counts (kind ``refreshed``) and the ids of the removed
(`deletedPackageIds`) and completed (`completedPackageIds`) packages. If the
run added checklist items, it also sends ``plan:changed`` for tonight.
"""

import logging
import threading
import time
from collections.abc import Iterable
from typing import Any

from astrometricslib import observing_night_id

logger = logging.getLogger(__name__)


class SuggestionRefreshWorker:
    """Run `refresh_suggestions` in the background, coalescing triggers."""

    def __init__(self, wayfinder: Any, socket_manager: Any = None) -> None:
        """Keep the Wayfinder that does the work and the event sender.

        Parameters
        ----------
        wayfinder : `wayfindinglib.Wayfinder`
            The shared Wayfinder.
        socket_manager : `SocketManager`, optional
            Sends events to the app windows. With none, nothing is announced.
        """
        self._wayfinder = wayfinder
        self._socket_manager = socket_manager
        self._lock = threading.Lock()
        self._pending_targets: set[str] = set()
        self._refresh_everything = False
        self._has_pending = False
        self._thread: threading.Thread | None = None

    def request(self, target_ids: Iterable[str] | None = None) -> None:
        """Ask for a refresh. Returns at once and never raises.

        Parameters
        ----------
        target_ids : `Iterable` [`str`], optional
            The targets whose data changed. `None` or empty means refresh
            everything.
        """
        try:
            names = [] if target_ids is None else list(target_ids)
            with self._lock:
                if names:
                    self._pending_targets.update(names)
                else:
                    self._refresh_everything = True
                self._has_pending = True
                if self._thread is not None and self._thread.is_alive():
                    return
                self._thread = threading.Thread(target=self._run, name="suggestion-refresh", daemon=True)
                self._thread.start()
        except Exception:  # a trigger must never fail the announcement that sent it
            logger.exception("Could not start the suggestion refresh")

    def wait_until_idle(self, timeout: float = 10.0) -> bool:
        """Wait for the running thread to finish (used by tests).

        Parameters
        ----------
        timeout : `float`, optional
            The most seconds to wait.

        Returns
        -------
        idle : `bool`
            `True` if no run was going when the wait ended.
        """
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return thread is None or not thread.is_alive()

    def _take_batch(self) -> tuple[bool, list[str] | None]:
        """Take the pending set, or say there is none.

        Returns
        -------
        found : `bool`
            `False` when nothing is waiting (the thread then ends).
        target_ids : `list` [`str`] or `None`
            The targets to refresh, or `None` for everything.
        """
        with self._lock:
            if not self._has_pending:
                self._thread = None
                return False, None
            everything = self._refresh_everything
            targets = sorted(self._pending_targets)
            self._pending_targets = set()
            self._refresh_everything = False
            self._has_pending = False
        return True, None if everything else targets

    def _run(self) -> None:
        """Run batches until none is waiting; the top of the thread."""
        while True:
            found, target_ids = self._take_batch()
            if not found:
                return
            try:
                change = self._wayfinder.planning.refresh_suggestions(target_ids)
            except Exception:  # the top of a background thread: log it, keep the worker alive
                logger.exception("The suggestion refresh failed")
                continue
            try:
                self._announce(change)
            except Exception:  # an announcement must never stop the worker
                logger.exception("Could not announce the suggestion refresh")

    def _announce(self, change: Any) -> None:
        """Send the events for one finished refresh.

        Parameters
        ----------
        change : `PackageChange`
            What the library changed.
        """
        if self._socket_manager is None:
            return
        counts = {
            "added": len(change.added),
            "updated": len(change.updated),
            "deleted": len(change.deleted),
            "completed": len(change.completed),
            "reverted": len(change.reverted),
            "checklistItemsAdded": len(change.checklist_item_ids),
        }
        if not any(counts.values()):
            return
        self._socket_manager.broadcast_ui_event_sync(
            "packages:changed",
            {
                "packageId": None,
                "targetId": None,
                "kind": "refreshed",
                **counts,
                "deletedPackageIds": list(change.deleted),
                "completedPackageIds": list(change.completed),
            },
        )
        if change.checklist_item_ids:
            session = self._wayfinder.planning.get_plan(night_id=observing_night_id(time.time()))
            if session is not None:
                self._socket_manager.broadcast_ui_event_sync(
                    "plan:changed",
                    {
                        "sessionId": session.id,
                        "revision": session.revision,
                        "changed": ["checklist_added"],
                    },
                )
