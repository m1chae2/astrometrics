"""Tests for the SIMBAD driver's client configuration and timeout.

The point of this module is that nothing else touches astroquery's
process-global `Simbad` object. These tests pin the two things that are
easy to get wrong and impossible to watch failing in production: that a
request timeout really is applied to the HTTP session, and that the
shared client is reset and reconfigured under the lock on every query.

They drive the driver against a stand-in client rather than astroquery's
own. The root `conftest.py` replaces `astroquery` with a `MagicMock`
whenever it has not already been imported, so a test that asserted
against the real object would pass or fail depending on import order.
"""

import queue
import threading
from typing import Any

import pytest

from astrometricslib.drivers import simbad_interface


class _FakeSession:
    """Stands in for the `requests.Session` astroquery queries through."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> str:
        self.calls.append({"method": method, "url": url, **kwargs})
        return "response"


class _FakeSimbad:
    """Stands in for astroquery's shared `Simbad` client object."""

    def __init__(self, result: Any = "table") -> None:
        self._session = _FakeSession()
        self.ROW_LIMIT: int | None = None
        self.events: list[tuple] = []
        self._result = result

    def reset_votable_fields(self) -> None:
        self.events.append(("reset",))

    def add_votable_fields(self, *fields: str) -> None:
        self.events.append(("add", fields))

    def query_region(self, coordinates: Any, radius: str | None = None) -> Any:
        self.events.append(("query_region", coordinates, radius))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    def query_object(self, object_name: str) -> Any:
        self.events.append(("query_object", object_name))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.fixture
def fake_simbad(monkeypatch: pytest.MonkeyPatch) -> _FakeSimbad:
    """Swap in a stand-in client and re-arm the one-time timeout install.

    Returns
    -------
    client : `_FakeSimbad`
        The stand-in installed as the driver's module-level client.
    """
    client = _FakeSimbad()
    monkeypatch.setattr(simbad_interface, "Simbad", client)
    monkeypatch.setattr(simbad_interface, "_timeout_installed", False)
    return client


def test_query_requests_carry_a_timeout(fake_simbad: _FakeSimbad) -> None:
    """The session gains a default timeout, so a stall cannot hang a run.

    `requests` applies no timeout of its own, so without this a stalled
    SIMBAD connection blocks the calling analysis run indefinitely.
    """
    simbad_interface.query_object("M 13")

    fake_simbad._session.request("GET", "https://simbad.invalid/tap")

    assert fake_simbad._session.calls[0]["timeout"] == (simbad_interface.SIMBAD_QUERY_TIMEOUT_SECONDS)


def test_an_explicit_timeout_is_not_overridden(fake_simbad: _FakeSimbad) -> None:
    """The default only fills in; a caller's own timeout still wins."""
    simbad_interface._install_request_timeout()

    fake_simbad._session.request("GET", "https://simbad.invalid/tap", timeout=5)

    assert fake_simbad._session.calls[0]["timeout"] == 5


def test_installing_the_timeout_twice_does_not_stack_wrappers(fake_simbad: _FakeSimbad) -> None:
    """Repeated installs must not nest one wrapper per query."""
    simbad_interface._install_request_timeout()
    once = fake_simbad._session.request
    simbad_interface._install_request_timeout()

    assert fake_simbad._session.request is once


def test_query_region_resets_before_requesting_its_columns(fake_simbad: _FakeSimbad) -> None:
    """Each query clears the previous caller's columns before adding its own.

    astroquery accumulates requested columns, so skipping the reset would
    hand one pipeline the columns another asked for.
    """
    result = simbad_interface.query_region(
        "coord", radius="0.5d", votable_fields=("otype", "ids"), row_limit=100
    )

    assert result == "table"
    assert fake_simbad.events == [
        ("reset",),
        ("add", ("otype", "ids")),
        ("query_region", "coord", "0.5d"),
    ]
    assert fake_simbad.ROW_LIMIT == 100


def test_query_object_passes_its_columns_through(fake_simbad: _FakeSimbad) -> None:
    """Named lookups configure the same shared client the same way."""
    result = simbad_interface.query_object("M 13", votable_fields=("otype", "ra", "dec"))

    assert result == "table"
    assert fake_simbad.events == [
        ("reset",),
        ("add", ("otype", "ra", "dec")),
        ("query_object", "M 13"),
    ]


def test_no_columns_requested_still_resets(fake_simbad: _FakeSimbad) -> None:
    """An empty field list must not leave the previous caller's columns."""
    simbad_interface.query_object("M 13")

    assert fake_simbad.events == [("reset",), ("query_object", "M 13")]
    assert fake_simbad.ROW_LIMIT is None


def test_queries_hold_the_lock_while_configuring(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared client is never reconfigured by two callers at once."""
    observed: list[bool] = []

    class _LockObservingSimbad(_FakeSimbad):
        def query_object(self, object_name: str) -> Any:
            observed.append(simbad_interface.SIMBAD_LOCK.locked())
            return "table"

    monkeypatch.setattr(simbad_interface, "Simbad", _LockObservingSimbad())
    monkeypatch.setattr(simbad_interface, "_timeout_installed", False)

    simbad_interface.query_object("M 13")

    assert observed == [True]
    # Released afterwards, so the next caller is not blocked.
    assert not simbad_interface.SIMBAD_LOCK.locked()


def test_the_lock_is_released_when_a_query_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A SIMBAD outage must not deadlock every later lookup."""
    monkeypatch.setattr(simbad_interface, "Simbad", _FakeSimbad(result=ConnectionError("SIMBAD unreachable")))
    monkeypatch.setattr(simbad_interface, "_timeout_installed", False)

    with pytest.raises(ConnectionError):
        simbad_interface.query_object("M 13")

    assert not simbad_interface.SIMBAD_LOCK.locked()


def test_concurrent_queries_do_not_interleave_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two threads must not see each other's half-applied column set.

    This is the failure the lock exists to prevent: without it, one
    caller's `reset_votable_fields` lands between another's reset and its
    own `add_votable_fields`, and the second query returns the wrong
    columns.
    """
    barrier = queue.Queue()

    class _SlowSimbad(_FakeSimbad):
        def add_votable_fields(self, *fields: str) -> None:
            # Give the other thread every chance to interleave here.
            barrier.put(fields)
            self.events.append(("add", fields))

    client = _SlowSimbad()
    monkeypatch.setattr(simbad_interface, "Simbad", client)
    monkeypatch.setattr(simbad_interface, "_timeout_installed", False)

    threads = [
        threading.Thread(
            target=simbad_interface.query_object, args=("M 13",), kwargs={"votable_fields": (name,)}
        )
        for name in ("otype", "ids")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    # Each query's three steps must appear as an uninterrupted run.
    assert len(client.events) == 6
    for start in (0, 3):
        window = client.events[start : start + 3]
        assert window[0] == ("reset",)
        assert window[1][0] == "add"
        assert window[2][0] == "query_object"
