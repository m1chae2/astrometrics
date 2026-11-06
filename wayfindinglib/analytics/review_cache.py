"""Purpose: Keep slow review results until the data behind them change.

Description: Reviewing a night reads many records and fits several models,
which takes seconds. `ReviewCache` remembers each result together with a
cheap "data version" (a small value that changes whenever any input of the
result changes). A later request with the same version gets the remembered
result. A request with a different version builds the result again and
replaces the old one. Nothing is stored on disk and nothing survives a
restart.

The cache is safe to use from several threads. When two threads ask for the
same missing result at the same moment, one builds it and the other waits
and uses it, so the work is done once. The cache holds a limited number of
results and drops the one used longest ago when it is full.

The cache never reads data itself. The caller decides what the version is.
A caller that cannot work out a version passes `None`, and the result is
built and not kept.
"""

from __future__ import annotations

import copy
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any

REVIEW_CACHE_MAXIMUM_ENTRIES = 64
"""How many results a `ReviewCache` keeps by default.

Design estimate WAY-D101 (see ``validation/wayfinding.md``): the 30 most recent
nights, a few lists of nights, and the shared per-night guiding baselines.
Each night's result is a few tens of kilobytes, so 64 results stay well
under a few megabytes.
"""


class ReviewCache:
    """Remember built results, each tied to the version of its data.

    Parameters
    ----------
    maximum_entries : `int`, optional
        The most results to keep. The one used longest ago is dropped first.
        Defaults to `REVIEW_CACHE_MAXIMUM_ENTRIES`.
    """

    def __init__(self, maximum_entries: int = REVIEW_CACHE_MAXIMUM_ENTRIES) -> None:
        """Start with no results."""
        self._maximum_entries = max(1, maximum_entries)
        self._entries: OrderedDict[Hashable, tuple[Hashable, Any]] = OrderedDict()
        self._guard = threading.Lock()
        self._build_locks: dict[Hashable, threading.Lock] = {}

    def get_or_build(
        self,
        key: Hashable,
        version: Hashable | Callable[[], Hashable | None] | None,
        builder: Callable[[], Any],
        *,
        copy_result: bool = True,
    ) -> Any:
        """Give the remembered result for `key`, building it when it is stale.

        Parameters
        ----------
        key : `~collections.abc.Hashable`
            What the result is about, such as
            ``("night_quality", "2026-10-03")``.
        version : `Hashable`, `Callable` or `None`
            The version of the data the result is built from. A remembered
            result is used only when its version equals this one. `None`
            means the version could not be worked out: the result is built
            and not kept. A function that gives the version is called once
            outside the lock; if that gives `None` it is called once more
            after the lock for `key` is held, so threads that all hit a
            passing problem (a busy database) take turns instead of each
            building the result without keeping it.
        builder : `~collections.abc.Callable`
            Builds the result. An error it raises goes to the caller and
            nothing is kept.
        copy_result : `bool`, optional
            Give each caller its own deep copy, so no caller can change what
            the cache holds. Defaults to true.

        Returns
        -------
        result : `Any`
            The remembered or newly built result.
        """
        resolve = version if callable(version) else (lambda: version)
        current = resolve()
        if current is not None:
            found, remembered = self._lookup(key, current)
            if found:
                return copy.deepcopy(remembered) if copy_result else remembered
        with self._lock_for(key):
            if current is None:
                # One more try, one thread at a time: a passing problem in
                # the first try has often cleared by the time the lock is ours.
                current = resolve()
            if current is None:
                return builder()
            # A thread that waited here finds the result the first one built.
            found, remembered = self._lookup(key, current)
            if not found:
                remembered = builder()
                self._store(key, current, remembered)
        return copy.deepcopy(remembered) if copy_result else remembered

    def invalidate(self, predicate: Callable[[Hashable], bool] | None = None) -> int:
        """Forget results.

        Parameters
        ----------
        predicate : `~collections.abc.Callable`, optional
            Called with each key. The results whose key gives true are
            forgotten. All results are forgotten when omitted.

        Returns
        -------
        count : `int`
            How many results were forgotten.
        """
        with self._guard:
            doomed = [key for key in self._entries if predicate is None or predicate(key)]
            for key in doomed:
                del self._entries[key]
            return len(doomed)

    def __len__(self) -> int:
        """Count the remembered results.

        Returns
        -------
        count : `int`
            The number of results held.
        """
        with self._guard:
            return len(self._entries)

    def _lookup(self, key: Hashable, version: Hashable) -> tuple[bool, Any]:
        """Find a result whose version is still the current one.

        Returns
        -------
        found, result : `tuple`
            Whether a current result exists, and the result.
        """
        with self._guard:
            entry = self._entries.get(key)
            if entry is None or entry[0] != version:
                return False, None
            self._entries.move_to_end(key)
            return True, entry[1]

    def _store(self, key: Hashable, version: Hashable, result: Any) -> None:
        """Keep a result and drop the one used longest ago when full."""
        with self._guard:
            self._entries[key] = (version, result)
            self._entries.move_to_end(key)
            while len(self._entries) > self._maximum_entries:
                dropped, _ = self._entries.popitem(last=False)
                self._build_locks.pop(dropped, None)

    def _lock_for(self, key: Hashable) -> threading.Lock:
        """Give the lock that lets one thread at a time build `key`.

        Returns
        -------
        lock : `threading.Lock`
            The lock of `key`, made on first use.
        """
        with self._guard:
            return self._build_locks.setdefault(key, threading.Lock())
