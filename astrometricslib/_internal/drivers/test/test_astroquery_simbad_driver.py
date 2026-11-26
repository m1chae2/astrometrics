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
import sys
import threading
import types
from typing import Any

import pytest
import requests
from pyvo.dal.exceptions import DALQueryError, DALServiceError

from astrometricslib._internal.drivers.astroquery_simbad_driver import AstroquerySimbadDriver
from astrometricslib._internal.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib._internal.drivers.outside_services import SIMBAD, classify_failure, gateway, host_state
from astrometricslib._internal.drivers.outside_services import clients as simbad_module
from astrometricslib.foundation.errors import ExternalServiceError


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

    def query_tap(self, adql_query: str) -> Any:
        self.events.append(("query_tap", adql_query))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


CDS_SERVER = "simbad.cds.unistra.fr"
HARVARD_SERVER = "simbad.harvard.edu"


class _FakeConfiguration:
    """A stand-in settings object that names the preferred SIMBAD server."""

    def __init__(self, preferred_server: str) -> None:
        """Store the preferred server.

        Parameters
        ----------
        preferred_server : `str`
            The server `get_simbad_preferred_server` returns.
        """
        self.preferred_server = preferred_server

    def get_simbad_preferred_server(self) -> str:
        """Return the stored preference.

        Returns
        -------
        server : `str`
            The preferred SIMBAD server.
        """
        return self.preferred_server


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with no remembered server and the default setting."""
    monkeypatch.setattr(gateway, "_last_answered", {})
    monkeypatch.setattr(gateway, "_preference_when_remembered", {})
    monkeypatch.setattr(gateway, "get_configuration", lambda: _FakeConfiguration(HARVARD_SERVER))


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeSimbad:
    """Install a stand-in as the driver's one client.

    Returns
    -------
    client : `_FakeSimbad`
        The stand-in the driver will hand out from `_get_client`.
    """
    client = _FakeSimbad()
    monkeypatch.setattr(simbad_module, "_client", client)
    return client


@pytest.fixture
def unbuilt_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the cached client so a test can watch it being built."""
    monkeypatch.setattr(simbad_module, "_client", None)


# --- One client, one owner --------------------------------------------------


def test_the_driver_owns_the_only_client(unbuilt_client: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The client is built once here and reused, never rebuilt per query."""
    built: list[_FakeSimbad] = []

    def build() -> _FakeSimbad:
        client = _FakeSimbad()
        built.append(client)
        return client

    monkeypatch.setitem(sys.modules, "astroquery.simbad", types.SimpleNamespace(SimbadClass=build))

    AstroquerySimbadDriver().query_object("M 13")
    AstroquerySimbadDriver().query_object("M 81")
    AstroquerySimbadDriver().query_region("coord", radius="0.5d")

    assert len(built) == 1
    assert simbad_module._client is built[0]


def test_the_client_is_not_astroquerys_module_level_singleton(unbuilt_client: None) -> None:
    """Our client must be our own, so nothing else can reconfigure it.

    astroquery's module-level `Simbad` is shared with every other user of
    the library in the process; configuring it would leak our row limit
    and columns to them, and theirs to us.
    """
    with simbad_module.SIMBAD_LOCK:
        client = simbad_module._get_simbad_client()

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

    monkeypatch.setitem(sys.modules, "astroquery.simbad", types.SimpleNamespace(SimbadClass=build))

    def query() -> None:
        start.wait(timeout=5)
        AstroquerySimbadDriver().query_object("M 13")

    threads = [threading.Thread(target=query) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(built) == 1


def test_no_other_module_imports_the_simbad_client() -> None:
    """Only the gateway package may import from `astroquery.simbad`.

    The one-client rule is only worth anything if nothing else reaches
    past the gateway to astroquery directly. The enforcement test of the
    gateway checks every network client in both libraries.
    """
    package_root = pathlib.Path(simbad_module.__file__).parent.parent
    gateway_folder = pathlib.Path(simbad_module.__file__).resolve().parent

    offenders = []
    for path in package_root.rglob("*.py"):
        if gateway_folder in path.resolve().parents or "__pycache__" in path.parts or "test" in path.parts:
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
        "these modules bypass the outside-service gateway and would give the "
        "process a second SIMBAD client:\n  " + "\n  ".join(offenders)
    )


# --- Request timeout --------------------------------------------------------


def test_the_client_session_carries_a_timeout(unbuilt_client: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The client is built with a request timeout already installed.

    `requests` applies no timeout of its own, so without this a stalled
    SIMBAD connection blocks the calling analysis run indefinitely.
    """
    monkeypatch.setitem(sys.modules, "astroquery.simbad", types.SimpleNamespace(SimbadClass=_FakeSimbad))

    with simbad_module.SIMBAD_LOCK:
        client = simbad_module._get_simbad_client()
    client._session.request("GET", "https://simbad.invalid/tap")

    assert client._session.calls[0]["timeout"] == SIMBAD.timeout_seconds


def test_an_explicit_timeout_is_not_overridden() -> None:
    """The default only fills in; a caller's own timeout still wins."""
    client = _FakeSimbad()
    simbad_module._install_request_timeout(client)

    client._session.request("GET", "https://simbad.invalid/tap", timeout=5)

    assert client._session.calls[0]["timeout"] == 5


# --- Configuration and locking ----------------------------------------------


def test_query_region_resets_before_requesting_its_columns(fake_client: _FakeSimbad) -> None:
    """Each query clears the previous caller's columns before adding its own.

    astroquery accumulates requested columns, so skipping the reset would
    hand one pipeline the columns another asked for.
    """
    result = AstroquerySimbadDriver().query_region(
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
    result = AstroquerySimbadDriver().query_object("M 13", votable_fields=("otype", "ra", "dec"))

    assert result == "table"
    assert fake_client.events == [
        ("reset",),
        ("add", ("otype", "ra", "dec")),
        ("query_object", "M 13"),
    ]


def test_no_columns_requested_still_resets(fake_client: _FakeSimbad) -> None:
    """An empty field list must not leave the previous caller's columns."""
    AstroquerySimbadDriver().query_object("M 13")

    assert fake_client.events == [("reset",), ("query_object", "M 13")]
    assert fake_client.ROW_LIMIT is None


def test_queries_hold_the_lock_while_configuring(monkeypatch: pytest.MonkeyPatch) -> None:
    """The client is never reconfigured by two callers at once."""
    observed: list[bool] = []

    class _LockObservingSimbad(_FakeSimbad):
        def query_object(self, object_name: str) -> Any:
            observed.append(simbad_module.SIMBAD_LOCK.locked())
            return "table"

    monkeypatch.setattr(simbad_module, "_client", _LockObservingSimbad())

    AstroquerySimbadDriver().query_object("M 13")

    assert observed == [True]
    # Released afterwards, so the next caller is not blocked.
    assert not simbad_module.SIMBAD_LOCK.locked()


def test_the_lock_is_released_when_a_query_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A SIMBAD outage must not deadlock every later lookup."""
    monkeypatch.setattr(simbad_module, "_client", _FakeSimbad(result=ConnectionError("SIMBAD unreachable")))

    with pytest.raises(ExternalServiceError):
        AstroquerySimbadDriver().query_object("M 13")

    assert not simbad_module.SIMBAD_LOCK.locked()


def test_a_service_error_is_reported_as_an_external_service_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A SIMBAD server error reaches the caller as `ExternalServiceError`.

    The original error stays attached as the cause, so the log still shows
    what the server said.
    """
    service_error = DALServiceError("SIMBAD returned HTTP 503", code=503)
    monkeypatch.setattr(simbad_module, "_client", _FakeSimbad(result=service_error))

    with pytest.raises(ExternalServiceError) as raised:
        AstroquerySimbadDriver().query_region("coord", radius="0.5d")

    assert raised.value.__cause__ is service_error


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
    monkeypatch.setattr(simbad_module, "_client", client)

    threads = [
        threading.Thread(
            target=AstroquerySimbadDriver().query_object, args=("M 13",), kwargs={"votable_fields": (name,)}
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


def test_the_driver_implements_the_simbad_interface() -> None:
    """`AstroquerySimbadDriver` is a `SimbadDriver`."""
    assert isinstance(AstroquerySimbadDriver(), SimbadDriver)


def test_query_tap_sends_the_query_without_touching_the_column_selection(fake_client: _FakeSimbad) -> None:
    """A TAP query names its own columns, so no votable fields are added."""
    result = AstroquerySimbadDriver().query_tap("SELECT main_id FROM basic")

    assert result == "table"
    assert fake_client.events == [("query_tap", "SELECT main_id FROM basic")]


def test_a_tap_failure_is_reported_as_an_external_service_error_and_frees_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable TAP service raises our error and frees the lock."""
    monkeypatch.setattr(simbad_module, "_client", _FakeSimbad(result=ConnectionError("SIMBAD unreachable")))

    with pytest.raises(ExternalServiceError):
        AstroquerySimbadDriver().query_tap("SELECT main_id FROM basic")

    assert not simbad_module.SIMBAD_LOCK.locked()


# --- Pause after repeated trouble ------------------------------------------


def _count_queries(client: _FakeSimbad) -> int:
    """Count the queries a stand-in client was sent.

    Returns
    -------
    count : `int`
        The number of query events.
    """
    return len([event for event in client.events if event[0].startswith("query")])


def test_repeated_connection_failures_pause_simbad_without_asking_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After two rounds of unreachable servers, later calls fail at once.

    The stand-in client records every call, so the test can see that the
    third call never reached it. The servers refuse the connection: a refused
    connection needs two failures before the pause, while a timeout pauses
    the host at once (see the gateway tests).
    """
    client = _FakeSimbad(result=requests.exceptions.ConnectionError("refused"))
    monkeypatch.setattr(simbad_module, "_client", client)
    driver = AstroquerySimbadDriver()

    for _ in range(SIMBAD.failures_before_backoff):
        with pytest.raises(ExternalServiceError):
            driver.query_object("M 13")
    asked_before = _count_queries(client)
    with pytest.raises(ExternalServiceError) as paused:
        driver.query_region("coord", radius="0.5d")
    with pytest.raises(ExternalServiceError):
        driver.query_tap("SELECT 1")

    assert asked_before == SIMBAD.failures_before_backoff * len(SIMBAD.hosts)
    assert _count_queries(client) == asked_before
    assert paused.value.details["paused"] is True


def test_the_pause_ends_with_time_and_a_good_reply_clears_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pause is temporary: the next call after it asks SIMBAD again."""
    client = _FakeSimbad(result=ConnectionError("SIMBAD unreachable"))
    monkeypatch.setattr(simbad_module, "_client", client)
    driver = AstroquerySimbadDriver()
    for _ in range(SIMBAD.failures_before_backoff):
        with pytest.raises(ExternalServiceError):
            driver.query_object("M 13")

    real_clock = host_state._clock
    monkeypatch.setattr(host_state, "_clock", lambda: real_clock() + SIMBAD.backoff_cap_seconds + 1.0)
    client._result = "table"

    assert driver.query_object("M 13") == "table"
    assert host_state.read_host_state(HARVARD_SERVER).failures == 0
    assert driver.query_object("M 81") == "table"


def test_a_bad_reply_does_not_start_a_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reply that arrived but cannot be used means the server is up."""
    client = _FakeSimbad(result=ValueError("unreadable reply"))
    monkeypatch.setattr(simbad_module, "_client", client)
    driver = AstroquerySimbadDriver()

    for _ in range(3):
        with pytest.raises(ExternalServiceError):
            driver.query_object("M 13")

    assert len([event for event in client.events if event[0] == "query_object"]) == 3


def test_a_503_reply_that_repeats_pauses_the_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 503 means "be quiet": repeat after a wait, then pause the host."""
    client = _FakeSimbad(result=DALServiceError("SIMBAD returned HTTP 503", code=503))
    monkeypatch.setattr(simbad_module, "_client", client)
    driver = AstroquerySimbadDriver()

    with pytest.raises(ExternalServiceError):
        driver.query_object("M 13")
    asked = _count_queries(client)
    with pytest.raises(ExternalServiceError):
        driver.query_object("M 81")

    assert asked == len(SIMBAD.hosts) * (SIMBAD.busy_retries + 1)
    assert _count_queries(client) == asked


def test_a_connection_error_wrapped_by_another_error_is_still_a_connection_failure() -> None:
    """The cause chain is followed, because pyvo wraps `requests` errors."""
    wrapped = DALServiceError("could not connect")
    wrapped.__cause__ = requests.exceptions.ConnectionError("refused")

    assert classify_failure(wrapped).kind.value == "connection"
    assert classify_failure(DALServiceError("HTTP 503", code=503)).kind.value == "busy"


class _TwoServerSimbad(_FakeSimbad):
    """A stand-in whose answer depends on which server it is pointed at."""

    def __init__(self, outcomes: dict[str, object]) -> None:
        """Store the outcome of each server.

        Parameters
        ----------
        outcomes : `dict` [`str`, `object`]
            Each server's reply, or the exception it raises.
        """
        super().__init__()
        self.outcomes = outcomes
        self.server = ""
        self.asked: list[str] = []

    def query_tap(self, _query: str) -> object:
        """Answer as the current server would.

        Returns
        -------
        reply : `object`
            The current server's reply. Its exception, when it has one, is
            raised instead.
        """
        self.asked.append(self.server)
        outcome = self.outcomes[self.server]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


MAIN_SERVER, MIRROR_SERVER = HARVARD_SERVER, CDS_SERVER


def test_an_unreachable_server_falls_back_to_the_other(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other server answers when the preferred one is down."""
    client = _TwoServerSimbad({MAIN_SERVER: ConnectionError("down"), MIRROR_SERVER: "rows"})
    monkeypatch.setattr(simbad_module, "_client", client)
    driver = AstroquerySimbadDriver()
    assert driver.query_tap("SELECT 1") == "rows"
    assert driver.query_tap("SELECT 2") == "rows"
    assert client.asked == [MAIN_SERVER, MIRROR_SERVER, MIRROR_SERVER]
    assert host_state.pause_remaining(MAIN_SERVER) == 0


def test_a_pyvo_error_with_no_http_status_counts_as_unreachable() -> None:
    """A capabilities failure from pyvo has no status and no network error."""
    capabilities = DALServiceError("Unable to access the capabilities endpoint")
    assert classify_failure(capabilities).kind.value == "connection"
    assert classify_failure(DALServiceError("server error", code=500)).kind.value == "server error"


def test_a_server_that_answers_is_not_left(monkeypatch: pytest.MonkeyPatch) -> None:
    """The main server is the only one asked while it answers."""
    client = _TwoServerSimbad({MAIN_SERVER: "rows", MIRROR_SERVER: "other"})
    monkeypatch.setattr(simbad_module, "_client", client)
    assert AstroquerySimbadDriver().query_tap("SELECT 1") == "rows"
    assert client.asked == [MAIN_SERVER]


def test_a_bad_reply_does_not_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reply that cannot be read is an error, not a reason to switch."""
    client = _TwoServerSimbad({MAIN_SERVER: ValueError("bad reply"), MIRROR_SERVER: "rows"})
    monkeypatch.setattr(simbad_module, "_client", client)
    with pytest.raises(ExternalServiceError):
        AstroquerySimbadDriver().query_tap("SELECT 1")
    assert client.asked == [MAIN_SERVER]


def test_both_servers_unreachable_twice_starts_the_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pause starts only after both servers have refused twice in a row.

    Both servers refuse the connection; a timeout would pause its server at
    once instead (see the gateway tests).
    """
    client = _TwoServerSimbad({MAIN_SERVER: ConnectionError("down"), MIRROR_SERVER: ConnectionError("down")})
    monkeypatch.setattr(simbad_module, "_client", client)
    driver = AstroquerySimbadDriver()
    for query in ("SELECT 1", "SELECT 2"):
        with pytest.raises(ExternalServiceError):
            driver.query_tap(query)
    assert len(client.asked) == 4
    with pytest.raises(ExternalServiceError):
        driver.query_tap("SELECT 3")
    assert len(client.asked) == 4


def test_harvard_is_asked_first_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the default setting, Harvard is asked before Strasbourg."""
    client = _TwoServerSimbad({CDS_SERVER: "cds", HARVARD_SERVER: "harvard"})
    monkeypatch.setattr(simbad_module, "_client", client)
    assert AstroquerySimbadDriver().query_tap("SELECT 1") == "harvard"
    assert client.asked == [HARVARD_SERVER]


def test_a_strasbourg_preference_asks_strasbourg_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CDS preference asks it first and falls back to Harvard."""
    monkeypatch.setattr(gateway, "get_configuration", lambda: _FakeConfiguration(CDS_SERVER))
    client = _TwoServerSimbad({CDS_SERVER: ConnectionError("down"), HARVARD_SERVER: "harvard"})
    monkeypatch.setattr(simbad_module, "_client", client)
    assert AstroquerySimbadDriver().query_tap("SELECT 1") == "harvard"
    assert client.asked == [CDS_SERVER, HARVARD_SERVER]


def test_a_changed_preference_replaces_the_remembered_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """After the setting changes, the new preferred server goes first."""
    client = _TwoServerSimbad({CDS_SERVER: "cds", HARVARD_SERVER: ConnectionError("down")})
    monkeypatch.setattr(simbad_module, "_client", client)
    monkeypatch.setattr(gateway, "get_configuration", lambda: _FakeConfiguration(HARVARD_SERVER))
    assert AstroquerySimbadDriver().query_tap("SELECT 1") == "cds"
    monkeypatch.setattr(gateway, "get_configuration", lambda: _FakeConfiguration(CDS_SERVER))
    client.asked.clear()
    assert AstroquerySimbadDriver().query_tap("SELECT 2") == "cds"
    assert client.asked == [CDS_SERVER]


def test_the_two_servers_are_the_ones_astroquery_accepts() -> None:
    """The driver knows exactly the two SIMBAD servers."""
    assert set(SIMBAD.hosts) == {CDS_SERVER, HARVARD_SERVER}


def test_a_busy_server_is_asked_again_and_then_the_other_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server that stays busy is repeated, then the other answers."""
    busy = DALQueryError("TAP service too busy! No connection available.")
    client = _TwoServerSimbad({MAIN_SERVER: busy, MIRROR_SERVER: "rows"})
    monkeypatch.setattr(simbad_module, "_client", client)
    assert AstroquerySimbadDriver().query_tap("SELECT 1") == "rows"
    assert client.asked == [MAIN_SERVER] * (SIMBAD.busy_retries + 1) + [MIRROR_SERVER]
    assert host_state.pause_remaining(MAIN_SERVER) > 0
    assert host_state.pause_remaining(MIRROR_SERVER) == 0


def test_a_server_that_is_busy_once_answers_on_the_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    """One "too busy" reply is followed by a good one from the same server."""
    client = _TwoServerSimbad({MAIN_SERVER: "rows", MIRROR_SERVER: "other"})
    replies = iter([DALQueryError("TAP service too busy!"), "rows"])

    def answer(_query: str) -> object:
        """Give the next reply, raising it when it is an error.

        Returns
        -------
        reply : `object`
            The next good reply.
        """
        client.asked.append(client.server)
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(client, "query_tap", answer)
    monkeypatch.setattr(simbad_module, "_client", client)
    assert AstroquerySimbadDriver().query_tap("SELECT 1") == "rows"
    assert client.asked == [MAIN_SERVER, MAIN_SERVER]
