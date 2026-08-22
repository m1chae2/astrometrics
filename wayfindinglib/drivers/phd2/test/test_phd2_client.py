"""Purpose: End-to-end tests for the PHD2 event-stream transport.

Description: Verifies PHD2Client.events() against a real local TCP server
(not just a mock socket), confirming correctly parsed line-delimited JSON
and reconnection after a dropped connection.
"""

import json
import socketserver
import threading

from wayfindinglib.drivers.phd2.phd2_client import PHD2Client


def _start_test_server(handler_class):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
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


def test_events_yields_correctly_parsed_json_lines():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify events() parses each JSON line from a real server."""
    sent_events = [
        {"Event": "Version", "PHDVersion": "2.6.11"},
        {"Event": "GuideStep", "Timestamp": 1000.0, "RADuration": 50, "RADirection": "East"},
    ]

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):  # ruff: ignore[missing-return-type-private-function]
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


def test_events_reconnects_after_dropped_connection():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify events() reconnects and keeps yielding after socket closes."""
    connection_count = {"count": 0}

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):  # ruff: ignore[missing-return-type-private-function]
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
