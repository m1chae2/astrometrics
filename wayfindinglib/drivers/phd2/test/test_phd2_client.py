"""Purpose: End-to-end tests for the PHD2 event-stream transport.

Description: Verifies PHD2Client.events() against a real local TCP server
(not just a mock socket), confirming correctly parsed line-delimited JSON
and reconnection after a dropped connection. Also verifies PHD2Session, the
command connection, against a small fake PHD2 that answers JSON-RPC requests
and sends events.
"""

import json
import socketserver
import threading
from collections.abc import Callable
from typing import Any

import pytest

from astrometricslib.exceptions import ExternalServiceError
from wayfindinglib.drivers import PHD2Client as PublicPHD2Client
from wayfindinglib.drivers.phd2.phd2_client import PHD2Client


def _start_test_server(
    handler_class: type[socketserver.BaseRequestHandler],
) -> tuple[socketserver.TCPServer, int]:
    """Start a local TCPServer with an ephemeral port for one test.

    Parameters
    ----------
    handler_class : `type`
        A `socketserver.BaseRequestHandler` subclass implementing the
        connection behavior under test.

    Returns
    -------
    server : `socketserver.TCPServer`
        The running server; caller is responsible for `server_close()`.
    port : `int`
        The ephemeral port the server bound to.
    """
    server = socketserver.TCPServer(("localhost", 0), handler_class)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def test_events_yields_correctly_parsed_json_lines() -> None:
    """Verify events() parses each JSON line from a real server."""
    sent_events = [
        {"Event": "Version", "PHDVersion": "2.6.11"},
        {"Event": "GuideStep", "Timestamp": 1000.0, "RADuration": 50, "RADirection": "East"},
    ]

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            for event in sent_events:
                self.request.sendall((json.dumps(event) + "\n").encode())

    server, port = _start_test_server(Handler)
    try:
        client = PHD2Client(host="localhost", port=port)
        collected = []
        for event in client.events():
            collected.append(event)
            if len(collected) == len(sent_events):
                break
        assert collected == sent_events
    finally:
        server.shutdown()
        server.server_close()


def test_events_reconnects_after_dropped_connection() -> None:
    """Verify events() reconnects and keeps yielding after socket closes."""
    connection_count = {"count": 0}

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            connection_count["count"] += 1
            event = {"Event": "GuideStep", "Timestamp": float(connection_count["count"]), "RADuration": 0}
            self.request.sendall((json.dumps(event) + "\n").encode())
            # Connection closes here (handle() returning drops it),
            # simulating PHD2 restarting or a network blip.

    server, port = _start_test_server(Handler)
    try:
        client = PHD2Client(host="localhost", port=port, reconnect_cooldown_seconds=0.01)
        collected = []
        for event in client.events():
            collected.append(event)
            if len(collected) == 2:
                break
        assert len(collected) == 2
        assert collected[0]["Timestamp"] != collected[1]["Timestamp"]
        assert connection_count["count"] >= 2
    finally:
        server.shutdown()
        server.server_close()


def test_events_notices_a_stop_on_a_connection_that_sends_nothing() -> None:
    """A stop is noticed even while PHD2 is connected but silent.

    PHD2 sends nothing while idle. Before reads had a timeout, the reader
    waited on such a connection forever and never saw the stop.
    """
    import time

    release = threading.Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            release.wait(timeout=10)

    server, port = _start_test_server(Handler)
    stop_event = threading.Event()
    finished = threading.Event()
    client = PHD2Client(host="localhost", port=port, socket_timeout_seconds=0.2)

    def read() -> None:
        """Read events until the stream ends."""
        for _ in client.events(stop_event=stop_event):
            pass
        finished.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        time.sleep(0.3)
        stop_event.set()
        assert finished.wait(timeout=1.5)
    finally:
        release.set()
        server.shutdown()
        server.server_close()


def test_events_splits_lines_that_arrive_in_pieces() -> None:
    """An event split across reads, or two in one read, still parses."""
    first = {"Event": "GuideStep", "Timestamp": 1.0}
    second = {"Event": "GuideStep", "Timestamp": 2.0}

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            text = (json.dumps(first) + "\n" + json.dumps(second) + "\n").encode()
            self.request.sendall(text[:10])
            self.request.sendall(text[10:])

    server, port = _start_test_server(Handler)
    try:
        client = PHD2Client(host="localhost", port=port)
        collected = []
        for event in client.events():
            collected.append(event)
            if len(collected) == 2:
                break
        assert collected == [first, second]
    finally:
        server.shutdown()
        server.server_close()


def test_a_phd2_that_is_not_running_is_warned_about_once(caplog: pytest.LogCaptureFixture) -> None:
    """Repeated failed connects log one warning, and the repeats stay at debug.

    A PHD2 that is not running used to write a warning on every retry, and
    those lines filled the log of any job that happened to be running.
    """
    import logging
    import socket
    import time

    # Find a port nothing listens on: bind one, note it, and release it.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("localhost", 0))
    unused_port = probe.getsockname()[1]
    probe.close()

    stop_event = threading.Event()
    client = PHD2Client(
        host="localhost", port=unused_port, reconnect_cooldown_seconds=0.02, socket_timeout_seconds=0.05
    )

    def read() -> None:
        """Read events until the stream ends."""
        for _ in client.events(stop_event=stop_event):
            pass

    with caplog.at_level(logging.DEBUG, logger="wayfindinglib.drivers.phd2.phd2_client"):
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        time.sleep(0.4)
        stop_event.set()
        reader.join(timeout=2)

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    repeats = [record for record in caplog.records if record.levelno == logging.DEBUG]
    assert len(warnings) == 1
    assert len(repeats) >= 2


def _fake_phd2(
    answers: Callable[[dict[str, Any]], dict[str, Any] | None],
    greeting: list[dict[str, Any]] | None = None,
    after_answer: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
) -> tuple[socketserver.TCPServer, int, list[dict[str, Any]]]:
    """Start a fake PHD2 that answers each request line it receives.

    Parameters
    ----------
    answers : `~collections.abc.Callable`
        Takes the decoded request and gives the body of the answer (``result``
        or ``error``), or `None` to send no answer.
    greeting : `list` [`dict`], optional
        Events sent as soon as a client connects.
    after_answer : `~collections.abc.Callable`, optional
        Takes the request and gives events to send after its answer.

    Returns
    -------
    server : `socketserver.TCPServer`
        The running server.
    port : `int`
        Its port.
    requests : `list` [`dict`]
        Every request received, in order.
    """
    requests: list[dict[str, Any]] = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            for event in greeting or []:
                self.wfile.write((json.dumps(event) + "\n").encode())
            for line in self.rfile:
                request = json.loads(line)
                requests.append(request)
                body = answers(request)
                if body is not None:
                    reply = {"jsonrpc": "2.0", "id": request["id"], **body}
                    self.wfile.write((json.dumps(reply) + "\r\n").encode())
                for event in (after_answer or (lambda _: []))(request):
                    self.wfile.write((json.dumps(event) + "\n").encode())

    server, port = _start_test_server(Handler)
    return server, port, requests


def test_the_public_module_offers_the_same_client() -> None:
    """`wayfindinglib.drivers` hands out the client the app uses."""
    assert PublicPHD2Client is PHD2Client


def test_a_session_sends_a_command_and_returns_its_result() -> None:
    """A command is sent as a JSON-RPC line and its result comes back."""
    server, port, requests = _fake_phd2(lambda request: {"result": "Guiding"})
    try:
        with PHD2Client(host="localhost", port=port).open_session() as session:
            assert session.get_app_state() == "Guiding"
            session.set_exposure(1500)
        assert requests[0]["method"] == "get_app_state"
        assert requests[1] == {"method": "set_exposure", "id": 2, "params": [1500]}
    finally:
        server.shutdown()
        server.server_close()


def test_a_session_raises_when_phd2_refuses_a_command() -> None:
    """An error answer becomes an `ExternalServiceError` naming the command."""
    server, port, _ = _fake_phd2(lambda request: {"error": {"code": 1, "message": "equipment not connected"}})
    try:
        with PHD2Client(host="localhost", port=port).open_session() as session:
            with pytest.raises(ExternalServiceError, match="refused loop: equipment not connected"):
                session.loop()
    finally:
        server.shutdown()
        server.server_close()


def test_a_session_gives_up_when_phd2_does_not_answer() -> None:
    """A command with no answer raises after its time limit."""
    server, port, _ = _fake_phd2(lambda request: None)
    try:
        with PHD2Client(host="localhost", port=port).open_session() as session:
            with pytest.raises(ExternalServiceError, match="did not answer get_connected"):
                session.call("get_connected", timeout_seconds=0.2)
    finally:
        server.shutdown()
        server.server_close()


def test_a_session_waits_for_an_event_that_arrives_after_a_command() -> None:
    """`guide` returns at once; the settle event is waited for apart."""
    settled = {"Event": "SettleDone", "Status": 0}

    def after(request: dict[str, Any]) -> list[dict[str, Any]]:
        """Send the settle event after the guide answer.

        Returns
        -------
        events : `list` [`dict`]
            The settle event for a guide request, else none.
        """
        return [{"Event": "AppState", "State": "Guiding"}, settled] if request["method"] == "guide" else []

    server, port, requests = _fake_phd2(lambda request: {"result": 0}, after_answer=after)
    try:
        with PHD2Client(host="localhost", port=port).open_session() as session:
            mark = session.event_count
            session.guide(settle_pixels=2.0, settle_seconds=5, settle_timeout_seconds=90, recalibrate=True)
            assert session.wait_for_event("SettleDone", mark, 5, Status=0) == settled
            assert session.wait_for_event("SettleDone", session.event_count, 0.2) is None
        assert requests[0]["params"] == [{"pixels": 2.0, "time": 5, "timeout": 90}, True]
    finally:
        server.shutdown()
        server.server_close()


def test_a_session_keeps_the_greeting_and_reads_the_version() -> None:
    """The events sent on connect are kept; the version is read from them."""
    greeting = [{"Event": "Version", "PHDVersion": "2.6.14"}, {"Event": "AppState", "State": "Stopped"}]
    server, port, _ = _fake_phd2(lambda request: {"result": 0}, greeting=greeting)
    try:
        with PHD2Client(host="localhost", port=port).open_session() as session:
            assert session.wait_for_event("AppState", 0, 5, State="Stopped") is not None
            assert session.version() == "2.6.14"
            assert [event["Event"] for event in session.events_since(0)] == ["Version", "AppState"]
    finally:
        server.shutdown()
        server.server_close()


def test_the_state_questions_convert_their_answers() -> None:
    """The questions give plain Python values."""
    answers = {
        "get_connected": True,
        "get_calibrated": False,
        "get_exposure": 2000,
        "get_profile": {"id": 1, "name": "Simulator 4400"},
        "get_current_equipment": {"camera": {"name": "Guide Simulator", "connected": True}},
        "find_star": [101.5, 202.25],
    }
    server, port, _ = _fake_phd2(lambda request: {"result": answers[request["method"]]})
    try:
        with PHD2Client(host="localhost", port=port).open_session() as session:
            assert session.get_connected() is True
            assert session.get_calibrated() is False
            assert session.get_exposure() == 2000
            assert session.get_profile()["name"] == "Simulator 4400"
            assert session.get_current_equipment()["camera"]["connected"] is True
            assert session.find_star() == [101.5, 202.25]
    finally:
        server.shutdown()
        server.server_close()


def test_a_session_to_another_computer_is_refused_when_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The offline check applies to a command session as to the stream."""
    from astrometricslib.exceptions import OfflineModeError

    monkeypatch.setenv("ASTROMETRICS_OFFLINE", "1")
    with pytest.raises(OfflineModeError):
        PHD2Client(host="192.0.2.7", port=4400).open_session()


def test_opening_a_session_with_no_phd2_raises_an_os_error() -> None:
    """A PHD2 that is not running is an `OSError` for the caller to report."""
    import socket

    probe = socket.socket()
    probe.bind(("localhost", 0))
    unused_port = probe.getsockname()[1]
    probe.close()
    with pytest.raises(OSError):
        PHD2Client(host="localhost", port=unused_port).open_session(timeout_seconds=0.5)
