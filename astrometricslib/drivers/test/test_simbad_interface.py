"""Tests for the driver that owns this process's one SIMBAD client.

Two invariants are pinned here. First, that there really is only one
client and this module makes it: `test_the_driver_owns_the_only_client`
and `test_no_other_module_imports_the_simbad_client` together say nobody
else builds or borrows one. Second, that the client is configured and
queried without ever exposing a half-applied configuration to a second
caller.

The behavioural tests drive the driver against a stand-in client rather
than astroquery's own. Both `conftest.py` files replace `astroquery` in
`sys.modules` with a `MagicMock` -- the root one with `setdefault`, the
`astrometricslib` one by assignment -- after the real package has already
been imported. Which object a module ends up holding therefore depends on
whether it was imported before or after that, so a test asserting against
"the real client" passes or fails on collection order.
"""

import pathlib
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
    """Stands in for an astroquery `SimbadClass` instance."""

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
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeSimbad:
    """Install a stand-in as the driver's one client.

    Returns
    -------
    client : `_FakeSimbad`
        The stand-in the driver will hand out from `_get_client`.
    """
    client = _FakeSimbad()
    monkeypatch.setattr(simbad_interface, "_client", client)
    return client


@pytest.fixture
def unbuilt_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the cached client so a test can watch it being built."""
    monkeypatch.setattr(simbad_interface, "_client", None)


# --- One client, one owner --------------------------------------------------


def test_the_driver_owns_the_only_client(unbuilt_client: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The client is built once here and reused, never rebuilt per query."""
    built: list[_FakeSimbad] = []

    def build() -> _FakeSimbad:
        client = _FakeSimbad()
        built.append(client)
        return client

    monkeypatch.setattr(simbad_interface, "SimbadClass", build)

    simbad_interface.query_object("M 13")
    simbad_interface.query_object("M 81")
    simbad_interface.query_region("coord", radius="0.5d")

    assert len(built) == 1
    assert simbad_interface._client is built[0]


def test_the_client_is_not_astroquerys_module_level_singleton(unbuilt_client: None) -> None:
    """Our client must be our own, so nothing else can reconfigure it.

    astroquery's module-level `Simbad` is shared with every other user of
    the library in the process; configuring it would leak our row limit
    and columns to them, and theirs to us.
    """
    with simbad_interface.SIMBAD_LOCK:
        client = simbad_interface._get_client()

    astroquery_simbad = pytest.importorskip("astroquery.simbad")
    module_singleton = getattr(astroquery_simbad, "Simbad", None)
    if module_singleton is not None:
        assert client is not module_singleton


def test_concurrent_first_use_still_builds_one_client(
    unbuilt_client: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Racing threads must not each build their own client."""
    built: list[_FakeSimbad] = []
    start = threading.Barrier(4)

    def build() -> _FakeSimbad:
        client = _FakeSimbad()
        built.append(client)
        return client

    monkeypatch.setattr(simbad_interface, "SimbadClass", build)

    def query() -> None:
        start.wait(timeout=5)
        simbad_interface.query_object("M 13")

    threads = [threading.Thread(target=query) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(built) == 1


def test_no_other_module_imports_the_simbad_client() -> None:
    """Only this driver may import from `astroquery.simbad`.

    The one-client rule is only worth anything if nothing else reaches
    past the driver to astroquery directly. `wayfindinglib` is a separate
    library with its own catalog drivers and is deliberately not covered.
    """
    package_root = pathlib.Path(simbad_interface.__file__).parent.parent
    driver = pathlib.Path(simbad_interface.__file__).resolve()

    offenders = []
    for path in package_root.rglob("*.py"):
        if path.resolve() == driver or "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "astroquery.simbad" in stripped and (
                stripped.startswith("import ") or stripped.startswith("from ")
            ):
                offenders.append(f"{path.relative_to(package_root)}:{number}: {stripped}")

    assert offenders == [], (
        "these modules bypass drivers/simbad_interface.py and would give the "
        "process a second SIMBAD client:\n  " + "\n  ".join(offenders)
    )


# --- Request timeout --------------------------------------------------------


def test_the_client_session_carries_a_timeout(unbuilt_client: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The client is built with a request timeout already installed.

    `requests` applies no timeout of its own, so without this a stalled
    SIMBAD connection blocks the calling analysis run indefinitely.
    """
    monkeypatch.setattr(simbad_interface, "SimbadClass", _FakeSimbad)

    with simbad_interface.SIMBAD_LOCK:
        client = simbad_interface._get_client()
    client._session.request("GET", "https://simbad.invalid/tap")

    assert client._session.calls[0]["timeout"] == simbad_interface.SIMBAD_QUERY_TIMEOUT_SECONDS


def test_an_explicit_timeout_is_not_overridden() -> None:
    """The default only fills in; a caller's own timeout still wins."""
    client = _FakeSimbad()
    simbad_interface._install_request_timeout(client)

    client._session.request("GET", "https://simbad.invalid/tap", timeout=5)

    assert client._session.calls[0]["timeout"] == 5


# --- Configuration and locking ----------------------------------------------


def test_query_region_resets_before_requesting_its_columns(fake_client: _FakeSimbad) -> None:
    """Each query clears the previous caller's columns before adding its own.

    astroquery accumulates requested columns, so skipping the reset would
    hand one pipeline the columns another asked for.
    """
    result = simbad_interface.query_region(
        "coord", radius="0.5d", votable_fields=("otype", "ids"), row_limit=100
    )

    assert result == "table"
    assert fake_client.events == [
        ("reset",),
        ("add", ("otype", "ids")),
        ("query_region", "coord", "0.5d"),
    ]
    assert fake_client.ROW_LIMIT == 100


def test_query_object_passes_its_columns_through(fake_client: _FakeSimbad) -> None:
    """Named lookups configure the same client the same way."""
    result = simbad_interface.query_object("M 13", votable_fields=("otype", "ra", "dec"))

    assert result == "table"
    assert fake_client.events == [
        ("reset",),
        ("add", ("otype", "ra", "dec")),
        ("query_object", "M 13"),
    ]


def test_no_columns_requested_still_resets(fake_client: _FakeSimbad) -> None:
    """An empty field list must not leave the previous caller's columns."""
    simbad_interface.query_object("M 13")

    assert fake_client.events == [("reset",), ("query_object", "M 13")]
    assert fake_client.ROW_LIMIT is None


def test_queries_hold_the_lock_while_configuring(monkeypatch: pytest.MonkeyPatch) -> None:
    """The client is never reconfigured by two callers at once."""
    observed: list[bool] = []

    class _LockObservingSimbad(_FakeSimbad):
        def query_object(self, object_name: str) -> Any:
            observed.append(simbad_interface.SIMBAD_LOCK.locked())
            return "table"

    monkeypatch.setattr(simbad_interface, "_client", _LockObservingSimbad())

    simbad_interface.query_object("M 13")

    assert observed == [True]
    # Released afterwards, so the next caller is not blocked.
    assert not simbad_interface.SIMBAD_LOCK.locked()


def test_the_lock_is_released_when_a_query_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A SIMBAD outage must not deadlock every later lookup."""
    monkeypatch.setattr(
        simbad_interface, "_client", _FakeSimbad(result=ConnectionError("SIMBAD unreachable"))
    )

    with pytest.raises(ConnectionError):
        simbad_interface.query_object("M 13")

    assert not simbad_interface.SIMBAD_LOCK.locked()


def test_concurrent_queries_do_not_interleave_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two threads must not see each other's half-applied column set.

    This is the failure the lock exists to prevent: without it, one
    caller's `reset_votable_fields` lands between another's reset and its
    own `add_votable_fields`, and the second query returns the wrong
    columns.
    """
    handoff: queue.Queue = queue.Queue()

    class _SlowSimbad(_FakeSimbad):
        def add_votable_fields(self, *fields: str) -> None:
            # Give the other thread every chance to interleave here.
            handoff.put(fields)
            self.events.append(("add", fields))

    client = _SlowSimbad()
    monkeypatch.setattr(simbad_interface, "_client", client)

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
