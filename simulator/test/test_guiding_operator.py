"""Purpose: Test the guiding step the launcher plays for the real PHD2.

Description: A small fake PHD2 listens on a free loopback port, answers the
JSON-RPC commands the operator sends and sends the events a real PHD2 sends.
The mount-tracking check and the clock are fakes, so nothing real runs, no
program is started and no wait is real.
"""

import json
import logging
import os
import socketserver
import threading
from pathlib import Path
from typing import Any

import pytest

from simulator.guiding_operator import FAILED, GUIDING, LEFT_ALONE, offline_identity, run_guiding_step
from simulator.phd2_launch import Phd2Instance

PROFILE = "Astrometrics simulator 2"


class FakePhd2:
    """A fake PHD2 event and command server.

    Parameters
    ----------
    state : `str`, optional
        The state ``get_app_state`` reports at the start.
    profile : `str`, optional
        The profile name ``get_profile`` reports.
    star_found : `bool`, optional
        Whether ``find_star`` succeeds.
    settle_status : `int`, optional
        The ``Status`` of the ``SettleDone`` event (0 is success).

    Attributes
    ----------
    commands : `list` [`str`]
        Every command received, in order.
    port : `int`
        The port the server listens on.
    """

    def __init__(
        self,
        state: str = "Stopped",
        profile: str = PROFILE,
        star_found: bool = True,
        settle_status: int = 0,
    ) -> None:
        """Start the server on a free port."""
        self.state = state
        self.profile = profile
        self.star_found = star_found
        self.settle_status = settle_status
        self.connected = False
        self.exposure = 1000
        self.commands: list[str] = []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                """Answer each request line, then send the events it causes."""
                self.wfile.write((json.dumps({"Event": "Version", "PHDVersion": "2.6.14"}) + "\n").encode())
                for line in self.rfile:
                    request = json.loads(line)
                    outer.commands.append(request["method"])
                    body, events = outer.answer(request)
                    reply = {"jsonrpc": "2.0", "id": request["id"], **body}
                    self.wfile.write((json.dumps(reply) + "\r\n").encode())
                    for event in events:
                        self.wfile.write((json.dumps(event) + "\n").encode())

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, request: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Answer one request.

        Returns
        -------
        body : `dict`
            The ``result`` or ``error`` of the answer.
        events : `list` [`dict`]
            Events to send after the answer.
        """
        method = request["method"]
        if method == "get_profile":
            return {"result": {"id": 1, "name": self.profile}}, []
        if method == "get_app_state":
            return {"result": self.state}, []
        if method == "get_connected":
            return {"result": self.connected}, []
        if method == "set_connected":
            self.connected = True
            return {"result": 0}, []
        if method == "get_exposure":
            return {"result": self.exposure}, []
        if method == "set_exposure":
            self.exposure = request["params"][0]
            return {"result": 0}, []
        if method == "loop":
            self.state = "Looping"
            return {"result": 0}, [{"Event": "LoopingExposures", "Frame": 1}]
        if method == "find_star":
            if not self.star_found:
                return {"error": {"code": 1, "message": "no star found"}}, []
            return {"result": [100.0, 200.0]}, []
        if method == "guide":
            self.state = "Guiding"
            done: dict[str, Any] = {"Event": "SettleDone", "Status": self.settle_status}
            if self.settle_status:
                done["Error"] = "timed-out"
            return {"result": 0}, [{"Event": "StartCalibration", "Mount": "Mount"}, done]
        return {"result": 0}, []

    def close(self) -> None:
        """Stop the server."""
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_phd2() -> Any:
    """Give a factory for fake PHD2 servers and close them after the test.

    Yields
    ------
    make : `~collections.abc.Callable`
        Builds a `FakePhd2` from keyword arguments.
    """
    servers: list[FakePhd2] = []

    def make(**options: Any) -> FakePhd2:
        """Build one fake server.

        Returns
        -------
        server : `FakePhd2`
            The running server.
        """
        servers.append(FakePhd2(**options))
        return servers[-1]

    yield make
    for server in servers:
        server.close()


def instance_for(server: FakePhd2, exposure_milliseconds: int = 1000) -> Phd2Instance:
    """Describe the PHD2 the launcher would have started on `server`'s port.

    Returns
    -------
    instance : `Phd2Instance`
        The record the operator gets.
    """
    return Phd2Instance(
        instance=2,
        event_port=server.port,
        profile_name=PROFILE,
        indi_port=4500,
        home=Path("/world/standin/home"),
        exposure_milliseconds=exposure_milliseconds,
    )


def quick_step(server: FakePhd2, tracking: bool = True, **options: Any) -> Any:
    """Run the step with a fake mount check and no real waiting.

    Returns
    -------
    outcome : `GuidingOutcome`
        What the step reported.
    """
    clock_time = [0.0]

    def sleep(seconds: float) -> None:
        """Advance the fake clock instead of waiting."""
        clock_time[0] += seconds

    return run_guiding_step(
        instance_for(server, options.pop("exposure_milliseconds", 1000)),
        is_tracking=lambda indi_port: tracking,
        sleep=sleep,
        clock=lambda: clock_time[0],
        **options,
    )


def test_the_step_connects_loops_finds_a_star_and_guides(fake_phd2: Any) -> None:
    """The normal path ends with PHD2 guiding; commands come in order."""
    server = fake_phd2()
    outcome = quick_step(server)
    assert outcome.status == GUIDING
    assert server.commands == [
        "get_profile",
        "get_app_state",
        "get_connected",
        "set_connected",
        "get_exposure",
        "loop",
        "find_star",
        "guide",
    ]


def test_the_exposure_in_the_profile_is_checked(fake_phd2: Any) -> None:
    """A guide exposure unlike the profile's is set before looping."""
    server = fake_phd2()
    outcome = quick_step(server, exposure_milliseconds=2500)
    assert outcome.status == GUIDING
    assert server.exposure == 2500
    assert "set_exposure" in server.commands


@pytest.mark.parametrize("state", ["Guiding", "Looping", "Calibrating", "Paused"])
def test_a_phd2_that_is_not_idle_is_left_alone(fake_phd2: Any, state: str) -> None:
    """PHD2 already guiding (the app's job after a slew) gets no command."""
    server = fake_phd2(state=state)
    outcome = quick_step(server)
    assert outcome.status == LEFT_ALONE
    assert state in outcome.detail
    assert server.commands == ["get_profile", "get_app_state"]


def test_a_phd2_with_another_profile_is_not_touched(fake_phd2: Any) -> None:
    """The step checks that it talks to the PHD2 it started."""
    server = fake_phd2(profile="My real profile")
    outcome = quick_step(server)
    assert outcome.status == LEFT_ALONE
    assert "not the PHD2 started here" in outcome.detail
    assert server.commands == ["get_profile"]


def test_a_mount_that_does_not_track_is_a_failure_status(
    fake_phd2: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """No tracking in time is a warning and a status, never an exception."""
    server = fake_phd2()
    with caplog.at_level(logging.INFO, logger="simulator.guiding_operator"):
        outcome = quick_step(server, tracking=False)
    assert outcome.status == FAILED
    assert "not tracking" in outcome.detail
    assert "loop" not in server.commands
    assert [record.levelno for record in caplog.records if "guiding" in record.getMessage()][
        -1
    ] == logging.WARNING


def test_a_field_with_no_star_is_a_failure_status(fake_phd2: Any) -> None:
    """A refused find_star gives a status that names the star catalog."""
    server = fake_phd2(star_found=False)
    outcome = quick_step(server)
    assert outcome.status == FAILED
    assert "no guide star" in outcome.detail
    assert server.commands.count("find_star") == 3
    assert "guide" not in server.commands


def test_a_settle_that_fails_is_a_failure_status(fake_phd2: Any) -> None:
    """A SettleDone with a non-zero status is reported with PHD2's reason."""
    server = fake_phd2(settle_status=1)
    outcome = quick_step(server)
    assert outcome.status == FAILED
    assert "timed-out" in outcome.detail


def test_a_phd2_that_does_not_answer_is_a_failure_status() -> None:
    """With nothing listening the step reports a failure and raises nothing."""
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    instance = Phd2Instance(2, port, PROFILE, 4500, Path("/world/standin/home"))
    clock_time = [0.0]

    def sleep(seconds: float) -> None:
        """Advance the fake clock instead of waiting."""
        clock_time[0] += seconds

    outcome = run_guiding_step(instance, sleep=sleep, clock=lambda: clock_time[0])
    assert outcome.status == FAILED
    assert str(port) in outcome.detail


def test_a_stop_request_ends_the_wait_for_tracking(fake_phd2: Any) -> None:
    """When the session is ending the step gives up without guiding."""
    server = fake_phd2()
    asked: list[int] = []

    def stop_after_connecting() -> bool:
        """Say no while the connection opens, then yes.

        Returns
        -------
        stop : `bool`
            `True` from the second question on.
        """
        asked.append(1)
        return len(asked) > 1

    outcome = quick_step(server, tracking=False, should_stop=stop_after_connecting)
    assert outcome.status == FAILED
    assert "guide" not in server.commands


def test_the_identity_block_sets_and_restores_the_variables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Inside the block the process is offline with the world's settings.

    After the block, even an error, the variables are as before.

    Raises
    ------
    RuntimeError
        Raised on purpose inside the block.
    """
    monkeypatch.delenv("ASTROMETRICS_OFFLINE", raising=False)
    monkeypatch.setenv("ASTROMETRICS_CONFIG", "/real/settings.toml")
    monkeypatch.delenv("ASTROMETRICS_CONFIG_PATH", raising=False)
    world = tmp_path / "world.toml"
    with pytest.raises(RuntimeError), offline_identity(world):
        assert os.environ["ASTROMETRICS_OFFLINE"] == "1"
        assert os.environ["ASTROMETRICS_CONFIG"] == str(world)
        assert os.environ["ASTROMETRICS_CONFIG_PATH"] == str(world)
        raise RuntimeError("even an error restores them")
    assert "ASTROMETRICS_OFFLINE" not in os.environ
    assert os.environ["ASTROMETRICS_CONFIG"] == "/real/settings.toml"
    assert "ASTROMETRICS_CONFIG_PATH" not in os.environ


def test_the_guiding_step_never_reads_the_real_settings(
    fake_phd2: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The step is offline while it runs, so no settings file is read."""
    monkeypatch.delenv("ASTROMETRICS_OFFLINE", raising=False)
    seen: list[str | None] = []
    server = fake_phd2()

    def tracking(port: int) -> bool:
        """Note the flag as the step sees it.

        Returns
        -------
        tracking : `bool`
            Always `True`.
        """
        seen.append(os.environ.get("ASTROMETRICS_OFFLINE"))
        return True

    run_guiding_step(
        instance_for(server, 1000), is_tracking=tracking, sleep=lambda seconds: None, clock=lambda: 0.0
    )
    assert seen == ["1"]
    assert "ASTROMETRICS_OFFLINE" not in os.environ
