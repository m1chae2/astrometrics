"""Purpose: Test the launcher's operator step, RPC client and shared helpers.

Description: The backend is a small HTTP server on a free loopback port that
records the JSON-RPC calls it gets, so nothing real is called and port 5000
is never used.
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from simulator.connection_guard import parse_ss_connections
from simulator.launch import OPERATOR_EVIDENCE_NOTE, run_operator_step
from simulator.rpc_client import backend_is_ready, call_rpc
from simulator.source_choice import (
    REAL,
    SIMULATED,
    SourceChoice,
    choice_file_path,
    read_choice,
    write_choice,
)

CALIBRATION_CALLS = [
    ("observatory:record_guider_calibration", {"calibration_id": "simulator-a"}),
    ("observatory:record_focus_model", {"focus_model_id": "simulator-a"}),
]
TARGETS = [("M 13", 16.695, 36.46)]


class Recorder:
    """A fake RPC caller that records calls and answers from a table.

    Attributes
    ----------
    calls : `list` [`tuple` [`str`, `dict`]]
        Every call, in order.
    answers : `dict` [`str`, `object`]
        The answer for each method; an exception instance is raised.
    """

    def __init__(self, answers: dict[str, object] | None = None) -> None:
        """Keep the answers."""
        self.calls: list[tuple[str, dict]] = []
        self.answers = answers or {}

    def __call__(self, port: int, method: str, params: dict) -> object:
        """Record a call and answer it.

        Returns
        -------
        answer : `object`
            The table's answer, or `None`.

        """
        self.calls.append((method, params))
        answer = self.answers.get(method)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_the_operator_calls_the_three_rpcs_in_order_with_shadow_first() -> None:
    """Record the guider calibration, the focus model, then take command."""
    outcome_data = {
        "applied": {"MOUNT_CONTROL": "authoritative", "AUTOFOCUS": "authoritative"},
        "rejected": {},
    }
    recorder = Recorder({"observatory:enter_controller_mode": outcome_data})
    outcome = run_operator_step(5100, CALIBRATION_CALLS, TARGETS, recorder)
    methods = [method for method, _ in recorder.calls if method.startswith("observatory:")]
    assert methods == [
        "observatory:record_guider_calibration",
        "observatory:record_focus_model",
        "observatory:enter_controller_mode",
    ]
    assert recorder.calls[-1][1] == {"evidence_note": OPERATOR_EVIDENCE_NOTE, "shadow_first": True}
    assert outcome == {"allowed": ["AUTOFOCUS", "MOUNT_CONTROL"], "off": {}, "error": None}


def test_the_operator_adds_a_missing_target_before_the_records() -> None:
    """The pointed target is added first (frames need a known target)."""
    recorder = Recorder({"observatory:enter_controller_mode": {"applied": {}, "rejected": {}}})
    run_operator_step(5100, CALIBRATION_CALLS, TARGETS, recorder)
    assert recorder.calls[0] == ("target:get", {"target_id": "M 13"})
    assert recorder.calls[1] == ("target:create", {"target_id": "M 13", "ra": "16:41:42", "dec": "+36:27:36"})


def test_the_operator_leaves_an_existing_target_alone() -> None:
    """A target the library already has is not created again."""
    recorder = Recorder({
        "target:get": {"id": "M 13"},
        "observatory:enter_controller_mode": {"applied": {}, "rejected": {}},
    })
    run_operator_step(5100, CALIBRATION_CALLS, TARGETS, recorder)
    assert "target:create" not in [method for method, _ in recorder.calls]


def test_the_operator_logs_what_stays_off(caplog: pytest.LogCaptureFixture) -> None:
    """Rejected capabilities are logged with reasons, under the D11 label."""
    recorder = Recorder({
        "observatory:enter_controller_mode": {
            "applied": {"MOUNT_CONTROL": "authoritative"},
            "rejected": {"AUTOFOCUS": "no focus model"},
        }
    })
    with caplog.at_level(logging.INFO, logger="simulator.launch"):
        outcome = run_operator_step(5100, [], [], recorder)
    assert outcome["off"] == {"AUTOFOCUS": "no focus model"}
    text = caplog.text
    assert "OPERATOR STEP (D11)" in text
    assert "on for: MOUNT_CONTROL" in text
    assert "AUTOFOCUS (no focus model)" in text


def test_a_failed_record_does_not_stop_the_step() -> None:
    """A failure is logged and the step goes on to take command."""
    recorder = Recorder({
        "observatory:record_guider_calibration": RuntimeError("boom"),
        "observatory:enter_controller_mode": {"applied": {}, "rejected": {"X": "no record"}},
    })
    outcome = run_operator_step(5100, CALIBRATION_CALLS, [], recorder)
    assert [method for method, _ in recorder.calls][-2:] == [
        "observatory:record_focus_model",
        "observatory:enter_controller_mode",
    ]
    assert outcome["off"] == {"X": "no record"}


def test_a_failed_change_of_mode_is_reported_not_raised() -> None:
    """The outcome says why commands are off; nothing is raised."""
    recorder = Recorder({"observatory:enter_controller_mode": RuntimeError("no")})
    outcome = run_operator_step(5100, [], [], recorder)
    assert outcome["allowed"] == []
    assert outcome["error"] == "RuntimeError: no"


class _Handler(BaseHTTPRequestHandler):
    """A fake backend: records RPC posts and answers ``/api/ready``."""

    received: list[dict] = []  # ruff: ignore[mutable-class-default] (shared on purpose with the test)
    ready = True

    def do_GET(self) -> None:
        """Answer ``/api/ready`` with 200 or 503."""
        self.send_response(200 if self.ready else 503)
        self.end_headers()

    def do_POST(self) -> None:
        """Record the JSON-RPC body and answer with the backend's wrapper."""
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        self.received.append(body)
        reply = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"status": "success", "data": {"echo": body["method"]}},
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(reply.encode())

    def log_message(self, *args: object) -> None:
        """Keep the test output quiet."""


def test_the_rpc_client_posts_json_rpc_and_unwraps_the_reply() -> None:
    """``call_rpc`` posts to 127.0.0.1 and unwraps the reply."""
    _Handler.received = []
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        assert call_rpc(port, "observatory:enter_controller_mode", {"shadow_first": True}) == {
            "echo": "observatory:enter_controller_mode"
        }
        assert _Handler.received[0]["params"] == {"shadow_first": True}
        assert backend_is_ready(port) is True
        _Handler.ready = False
        assert backend_is_ready(port) is False
    finally:
        _Handler.ready = True
        server.shutdown()
        server.server_close()


def test_a_backend_that_is_not_listening_is_not_ready() -> None:
    """A refused connection is just "not ready"."""
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    server.server_close()
    assert backend_is_ready(port, timeout=0.5) is False


def test_the_connection_guard_picks_out_only_non_loopback_peers() -> None:
    """The parser keeps only connections that leave the computer."""
    output = (
        'ESTAB 0 0 127.0.0.1:5000 127.0.0.1:41000 users:(("python",pid=10,fd=3))\n'
        'ESTAB 0 0 192.168.1.5:40000 93.184.216.34:443 users:(("python",pid=11,fd=4))\n'
        'ESTAB 0 0 192.168.1.5:40001 93.184.216.34:443 users:(("other",pid=99,fd=4))\n'
    )
    offending = parse_ss_connections(output, {10, 11})
    assert len(offending) == 1
    assert "pid=11" in offending[0]


def test_the_choice_file_lives_in_the_real_settings_folder(tmp_path: Path) -> None:
    """The layer reads and writes the same file the backend uses."""
    environment = {"XDG_CONFIG_HOME": str(tmp_path / "config")}
    assert read_choice(environment).source == REAL
    write_choice(SourceChoice(SIMULATED), environment)
    assert choice_file_path(environment) == tmp_path / "config" / "astrometrics" / "observatory_source.json"
    assert read_choice(environment).source == SIMULATED
