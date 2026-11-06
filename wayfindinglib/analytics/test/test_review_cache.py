"""Purpose: Tests for `ReviewCache`.

Description: Checks that a result is built once while its data version stays
the same, built again when the version changes, forgotten on request, never
kept when the version is unknown, kept in a bounded number, handed out as a
copy, and built only once when several threads ask for it together.
"""

import threading
import time
from typing import Any

import pytest

from wayfindinglib.analytics.review_cache import ReviewCache


class _Builder:
    """A builder that counts its calls."""

    def __init__(self, delay_seconds: float = 0.0) -> None:
        """Start with no calls."""
        self.calls = 0
        self.delay_seconds = delay_seconds
        self._guard = threading.Lock()

    def __call__(self) -> dict[str, Any]:
        """Count the call, wait a little, and build a result.

        Returns
        -------
        result : `dict`
            A new dictionary each call.
        """
        with self._guard:
            self.calls += 1
        time.sleep(self.delay_seconds)
        return {"value": [1, 2, 3]}


def test_an_unchanged_version_builds_once() -> None:
    """A second request with the same version does not build again."""
    cache, builder = ReviewCache(), _Builder()

    cache.get_or_build("night", 1, builder)
    cache.get_or_build("night", 1, builder)

    assert builder.calls == 1


def test_a_new_version_builds_again() -> None:
    """A changed version replaces the kept result."""
    cache, builder = ReviewCache(), _Builder()

    cache.get_or_build("night", 1, builder)
    cache.get_or_build("night", 2, builder)
    cache.get_or_build("night", 2, builder)

    assert builder.calls == 2


def test_keys_are_kept_apart() -> None:
    """Two nights are two results."""
    cache, builder = ReviewCache(), _Builder()

    cache.get_or_build("a", 1, builder)
    cache.get_or_build("b", 1, builder)

    assert builder.calls == 2
    assert len(cache) == 2


def test_an_unknown_version_is_never_kept() -> None:
    """With no version, every request builds and nothing is stored."""
    cache, builder = ReviewCache(), _Builder()

    cache.get_or_build("night", None, builder)
    cache.get_or_build("night", None, builder)

    assert builder.calls == 2
    assert len(cache) == 0


def test_an_error_is_not_kept() -> None:
    """A failed build leaves nothing, so the next request tries again."""
    cache = ReviewCache()

    def fail() -> None:
        """Fail with a value error.

        Raises
        ------
        ValueError
            Always.
        """
        raise ValueError("no data")

    with pytest.raises(ValueError, match="no data"):
        cache.get_or_build("night", 1, fail)
    builder = _Builder()
    cache.get_or_build("night", 1, builder)

    assert builder.calls == 1


def test_invalidate_forgets_chosen_results_or_all() -> None:
    """A predicate picks what to forget; none forgets everything."""
    cache, builder = ReviewCache(), _Builder()
    for key in ("a", "b", "c"):
        cache.get_or_build(key, 1, builder)

    assert cache.invalidate(lambda key: key == "a") == 1
    cache.get_or_build("a", 1, builder)
    assert builder.calls == 4
    assert cache.invalidate() == 3
    assert len(cache) == 0


def test_the_cache_is_bounded_and_drops_the_oldest_use() -> None:
    """The result used longest ago goes first."""
    cache, builder = ReviewCache(maximum_entries=2), _Builder()
    cache.get_or_build("a", 1, builder)
    cache.get_or_build("b", 1, builder)
    cache.get_or_build("a", 1, builder)  # a is now newer than b
    cache.get_or_build("c", 1, builder)  # drops b

    assert len(cache) == 2
    calls = builder.calls
    cache.get_or_build("a", 1, builder)
    assert builder.calls == calls
    cache.get_or_build("b", 1, builder)
    assert builder.calls == calls + 1


def test_each_caller_gets_its_own_copy() -> None:
    """Changing a returned result does not change the kept one."""
    cache = ReviewCache()

    first = cache.get_or_build("night", 1, _Builder())
    first["value"].append(99)
    second = cache.get_or_build("night", 1, _Builder())

    assert second == {"value": [1, 2, 3]}


def test_threads_asking_together_build_once() -> None:
    """Eight threads asking for the same missing result share one build."""
    cache, builder = ReviewCache(), _Builder(delay_seconds=0.2)
    results: list[Any] = []

    def ask() -> None:
        """Ask for the result and keep the answer."""
        results.append(cache.get_or_build("night", 1, builder))

    threads = [threading.Thread(target=ask) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert builder.calls == 1
    assert len(results) == 8
    assert all(result == {"value": [1, 2, 3]} for result in results)
