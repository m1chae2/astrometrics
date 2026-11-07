"""Purpose: Record what a real PHD2 sends, for the PHD2 contract test.

Description: The simulated observatory plays PHD2 with a stand-in
(`mock_phd2.py`). The risk is that the stand-in drifts away from what real
PHD2 sends. This script
runs a real PHD2 (the user's own build, ``~/.local/bin/phd2``) against the
INDI simulators, drives it through connect, loop, calibrate, guide, one dither
and stop, and keeps two files:

- ``phd2_contract_events.jsonl``: every event line PHD2's event server sent.
  The first line is a comment object (``"Recording"``) with the PHD2
  version, the date and the commit when known.
- ``phd2_contract_guide_log.txt``: the guide log PHD2 wrote.

Both files go in ``wayfindinglib/simulation/test/fixtures/``. Run it again
after a PHD2 upgrade, then run
``.venv/bin/pytest wayfindinglib/simulation/test/test_phd2_contract.py``.
The run needs `xvfb-run`, `bwrap`, `indiserver` and the INDI simulators.

Safety: the whole run happens in a sealed network (``bwrap --unshare-net``),
so nothing leaves this computer and a PHD2 that is already running on the
real ports (4400) is not touched. PHD2 gets a throwaway ``HOME`` folder with
its own settings, deleted at the end. The user's ``~/.PHD2`` or
``~/.PHDGuidingV2`` is never read or written. Every child process (indiserver,
Xvfb, PHD2) is stopped at the end; ``--unshare-pid`` also makes them end if
this script dies.

Usage::

    .venv/bin/python build/tpm/record_phd2_contract.py \
        [--phd2 ~/.local/bin/phd2] [--guide-seconds 120] [--keep-home]
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

TPM_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TPM_DIR.parents[1] / "wayfindinglib" / "simulation" / "test" / "fixtures"
EVENTS_FIXTURE = FIXTURES_DIR / "phd2_contract_events.jsonl"
GUIDE_LOG_FIXTURE = FIXTURES_DIR / "phd2_contract_guide_log.txt"
#: PHD2's event server port for instance 1 (4400 + instance - 1).
#: Safe inside the seal.
EVENT_PORT = 4400
#: Marks the process that already runs inside the sealed network.
SEALED_MARKER = "PHD2_CONTRACT_SEALED"
#: The INDI simulator devices PHD2 is pointed at.
CAMERA_DEVICE = "Guide Simulator"
MOUNT_DEVICE = "Telescope Simulator"
#: Guide camera exposure in milliseconds.
EXPOSURE_MS = 1000

#: A stand-in for the `gsc` star catalog program the INDI camera simulators
#: call for the stars of a field. Without it the simulated guide camera
#: shows only noise and PHD2 finds no star. It prints a fixed lattice of
#: made-up stars, so
#: the same sky position always has the same stars. Arguments (as the INDI
#: simulator passes them):
#: ``-c RA_degrees Dec_degrees -r radius_arcmin -m 0 mag -n count``.
FAKE_GSC = """#!/usr/bin/env python3
import math, sys
arguments = sys.argv[1:]
ra, dec = float(arguments[arguments.index("-c") + 1]), float(arguments[arguments.index("-c") + 2])
radius = float(arguments[arguments.index("-r") + 1]) / 60.0
cell = 0.02
count = 0
for i in range(int((dec - radius) / cell) - 1, int((dec + radius) / cell) + 2):
    for j in range(int((ra - radius * 2) / cell) - 1, int((ra + radius * 2) / cell) + 2):
        hashed = (i * 73856093) ^ (j * 19349663)
        if hashed % 7:
            continue
        star_dec = (i + ((hashed >> 3) % 100) / 100.0) * cell
        star_ra = (j + ((hashed >> 11) % 100) / 100.0) * cell
        if abs(star_dec - dec) > radius or abs(star_ra - ra) * math.cos(math.radians(dec)) > radius:
            continue
        magnitude = 8.0 + ((hashed >> 17) % 60) / 10.0
        identifier = abs(hashed) % 10**10
        print("%010d %.6f %+.6f 0.2 %.2f 0.1 3 0 0001 N 0.0 0" % (identifier, star_ra, star_dec, magnitude))
        count += 1
        if count >= 3000:
            sys.exit(0)
"""


def profile_settings(indi_port: int) -> str:
    """Return the text of a PHD2 settings file with an INDI camera and mount.

    Parameters
    ----------
    indi_port : `int`
        The port of the local indiserver.

    Returns
    -------
    text : `str`
        The content of ``.PHDGuidingV2`` for the throwaway home folder.
    """
    return f"""ConfigVersion=2001
LoggingMode=1
currentProfile=1
ServerMode=1
[profile]
[profile/1]
name=TPM contract
ExposureDurationMs={EXPOSURE_MS}
[profile/1/frame]
focalLength=400
[profile/1/camera]
LastMenuChoice=INDI Camera
pixelsize=3.75
[profile/1/scope]
LastMenuChoice=INDI Mount
LastAuxMenuChoice=None
[profile/1/stepguider]
LastMenuChoice=None
[profile/1/rotator]
LastMenuChoice=None
[profile/1/indi]
INDIhost=127.0.0.1
INDIport={indi_port}
INDIcam={CAMERA_DEVICE}
INDIcam_ccd=CCD1
INDImount={MOUNT_DEVICE}
"""


class Phd2Session:
    """One connection to PHD2's event server: records events, sends requests.

    Parameters
    ----------
    port : `int`
        The event server port.
    """

    def __init__(self, port: int) -> None:
        """Connect to PHD2 and start the reader thread."""
        self._socket = socket.create_connection(("127.0.0.1", port), timeout=10)
        self._socket.settimeout(None)
        self.events: list[dict] = []
        self._responses: dict[int, dict] = {}
        self._condition = threading.Condition()
        self._next_id = 1
        threading.Thread(target=self._read_loop, daemon=True, name="Phd2Reader").start()

    def _read_loop(self) -> None:
        """Split the stream into lines; file each as an event or an answer."""
        buffer = b""
        while True:
            try:
                chunk = self._socket.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                message = json.loads(line)
                with self._condition:
                    if "Event" in message:
                        self.events.append(message)
                    elif "id" in message:
                        self._responses[message["id"]] = message
                    self._condition.notify_all()

    def call(self, method: str, params: object = None, timeout: float = 60.0) -> dict:
        """Send one JSON-RPC request and wait for its answer.

        Parameters
        ----------
        method : `str`
            The PHD2 method name.
        params : `object`, optional
            The ``params`` value.
        timeout : `float`, optional
            Seconds to wait for the answer.

        Returns
        -------
        response : `dict`
            The answer. Its ``error`` key is set when PHD2 refused.
        """
        request_id = self._next_id
        self._next_id += 1
        request: dict = {"method": method, "id": request_id}
        if params is not None:
            request["params"] = params
        self._socket.sendall((json.dumps(request) + "\r\n").encode())
        deadline = time.time() + timeout
        with self._condition:
            while request_id not in self._responses:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return {"error": {"message": f"no answer to {method} in {timeout} s"}}
                self._condition.wait(remaining)
            return self._responses.pop(request_id)

    def wait_for(self, event_name: str, after: int, timeout: float, **match: object) -> dict | None:
        """Wait for an event that arrives after the first `after` events.

        Parameters
        ----------
        event_name : `str`
            The ``Event`` value to wait for.
        after : `int`
            Only events at this index or later count.
        timeout : `float`
            Seconds to wait.
        **match : `object`
            Extra fields that must equal these values.

        Returns
        -------
        event : `dict` or `None`
            The event, or `None` on timeout.
        """
        deadline = time.time() + timeout
        with self._condition:
            while True:
                for event in self.events[after:]:
                    if event["Event"] == event_name and all(event.get(k) == v for k, v in match.items()):
                        return event
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._condition.wait(min(remaining, 1.0))


def free_port() -> int:
    """Return a free local port.

    Returns
    -------
    port : `int`
        A port nothing listens on at the moment.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for_port(port: int, seconds: float) -> bool:
    """Wait until something accepts connections on a local port.

    Parameters
    ----------
    port : `int`
        The port.
    seconds : `float`
        How long to wait.

    Returns
    -------
    ready : `bool`
        Whether the port answered in time.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return True
        except OSError:
            time.sleep(0.5)
    return False


def prepare_simulators(indi_port: int) -> None:
    """Connect the simulators, set a focal length and point the mount.

    The INDI camera simulator takes its field of view from the focal length in
    its ``SCOPE_INFO`` property (PHD2 does not set it), and draws the stars of
    the field around where the mount points. The mount starts at the pole,
    so it is sent to a field away from it and waited for.

    Parameters
    ----------
    indi_port : `int`
        The port of the local indiserver.

    Raises
    ------
    RuntimeError
        If the mount does not arrive in time.
    """

    def indi(*arguments: str) -> str:
        """Run one INDI command line tool.

        Returns
        -------
        output : `str`
            What the tool printed.
        """
        tool = "indi_getprop" if arguments[0] == "get" else "indi_setprop"
        return subprocess.run(
            [tool, "-p", str(indi_port), *arguments[1:]], capture_output=True, text=True, timeout=30
        ).stdout

    indi("set", f"{MOUNT_DEVICE}.CONNECTION.CONNECT=On")
    indi("set", f"{CAMERA_DEVICE}.CONNECTION.CONNECT=On")
    time.sleep(3)
    indi("set", f"{CAMERA_DEVICE}.SCOPE_INFO.FOCAL_LENGTH;APERTURE=400;80")
    indi("set", f"{MOUNT_DEVICE}.TELESCOPE_TRACK_STATE.TRACK_ON=On")
    indi("set", f"{MOUNT_DEVICE}.EQUATORIAL_EOD_COORD.RA;DEC=12;40")
    for _ in range(120):
        if indi("get", f"{MOUNT_DEVICE}.EQUATORIAL_EOD_COORD._STATE").strip().endswith("=Ok"):
            return
        time.sleep(1)
    raise RuntimeError("The simulated mount did not arrive at the field.")


def drive(session: Phd2Session, guide_seconds: float) -> list[str]:
    """Drive PHD2 through one guiding session and return notes.

    Parameters
    ----------
    session : `Phd2Session`
        The connection to PHD2.
    guide_seconds : `float`
        Seconds to guide before the dither.

    Returns
    -------
    notes : `list` [`str`]
        One line per step, for the console.
    """
    notes: list[str] = []

    def step(name: str, response: dict) -> dict:
        """Log one answer and stop the run on an error.

        Returns
        -------
        response : `dict`
            The answer, unchanged.

        Raises
        ------
        RuntimeError
            If PHD2 answered with an error.
        """
        notes.append(f"{name}: {json.dumps(response)[:200]}")
        print(notes[-1], flush=True)
        if "error" in response:
            raise RuntimeError(notes[-1])
        return response

    step("set_exposure", session.call("set_exposure", [EXPOSURE_MS]))
    step("set_connected", session.call("set_connected", [True], timeout=120))
    step("get_current_equipment", session.call("get_current_equipment"))
    mark = len(session.events)
    step("loop", session.call("loop"))
    session.wait_for("LoopingExposures", mark, 30)
    time.sleep(3)
    step("find_star", session.call("find_star", timeout=60))
    mark = len(session.events)
    step(
        "guide",
        session.call("guide", [{"pixels": 1.5, "time": 6, "timeout": 120}, True], timeout=30),
    )
    finished = session.wait_for("SettleDone", mark, 400)
    notes.append(f"first settle: {finished}")
    print(notes[-1], flush=True)
    time.sleep(guide_seconds)
    mark = len(session.events)
    step("dither", session.call("dither", [3, False, {"pixels": 1.5, "time": 6, "timeout": 60}], timeout=30))
    finished = session.wait_for("SettleDone", mark, 90)
    notes.append(f"dither settle: {finished}")
    print(notes[-1], flush=True)
    time.sleep(15)
    step("stop_capture", session.call("stop_capture", timeout=30))
    session.wait_for("AppState", mark, 20, State="Stopped")
    return notes


def phd2_version(phd2: str) -> str:
    """Ask PHD2 for its version text.

    Parameters
    ----------
    phd2 : `str`
        The PHD2 program path.

    Returns
    -------
    version : `str`
        The output of ``phd2 --version``, or ``unknown``.
    """
    try:
        return (
            subprocess.run([phd2, "-v"], capture_output=True, text=True, timeout=30).stdout.strip()
            or "unknown"
        )
    except OSError, subprocess.SubprocessError:
        return "unknown"


def record(arguments: argparse.Namespace) -> int:
    """Run the recording inside the sealed network.

    Parameters
    ----------
    arguments : `argparse.Namespace`
        The command line.

    Returns
    -------
    status : `int`
        0 when both fixtures were written.
    """
    home = Path(tempfile.mkdtemp(prefix="phd2_contract_home_"))
    processes: list[subprocess.Popen] = []
    try:
        indi_port = free_port()
        fake_bin = home / "bin"
        fake_bin.mkdir()
        (fake_bin / "gsc").write_text(FAKE_GSC, encoding="utf-8")
        (fake_bin / "gsc").chmod(0o755)
        processes.append(
            subprocess.Popen(
                ["indiserver", "-p", str(indi_port), "indi_simulator_telescope", "indi_simulator_guide"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
                start_new_session=True,
            )
        )
        if not wait_for_port(indi_port, 15):
            print("ERROR: indiserver did not start.", file=sys.stderr)
            return 1
        prepare_simulators(indi_port)
        (home / ".PHDGuidingV2").write_text(profile_settings(indi_port), encoding="utf-8")
        environment = {**os.environ, "HOME": str(home)}
        processes.append(
            subprocess.Popen(
                ["xvfb-run", "-a", arguments.phd2, "-i", "1"],
                env=environment,
                stdout=(home / "phd2.out").open("w"),
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        )
        if not wait_for_port(EVENT_PORT, 60):
            print("ERROR: PHD2 did not start.", file=sys.stderr)
            return 1
        session = Phd2Session(EVENT_PORT)
        try:
            drive(session, arguments.guide_seconds)
        finally:
            session.call("shutdown", timeout=10)
            time.sleep(3)
        write_fixtures(arguments, session.events, home)
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        time.sleep(1)
        for process in processes:
            try:
                os.killpg(process.pid, 9)
            except OSError:
                pass
        if not arguments.keep_home:
            shutil.rmtree(home, ignore_errors=True)
        else:
            print(f"Kept {home}")


def trim_events(events: list[dict], keep_steps: int = 120) -> list[dict]:
    """Keep the events a contract test needs and drop the bulk.

    Long runs repeat ``GuideStep`` and ``StarSelected``-style events; the test
    needs the shape of each, the first steps, and everything around the dither.

    Parameters
    ----------
    events : `list` [`dict`]
        All recorded events in order.
    keep_steps : `int`, optional
        How many ``GuideStep`` events to keep outside the dither window.

    Returns
    -------
    kept : `list` [`dict`]
        The trimmed events, still in order.
    """
    kept: list[dict] = []
    steps_seen = 0
    for index, event in enumerate(events):
        if event["Event"] == "GuideStep":
            steps_seen += 1
            near_dither = any(
                other["Event"] in ("GuidingDithered", "SettleBegin", "SettleDone", "Settling")
                for other in events[max(0, index - 6) : index + 6]
            )
            if steps_seen > keep_steps and not near_dither:
                continue
        elif (
            event["Event"] in ("LoopingExposures", "Settling")
            and kept
            and kept[-1]["Event"] == event["Event"]
        ):
            continue
        kept.append(event)
    return kept


def write_fixtures(arguments: argparse.Namespace, events: list[dict], home: Path) -> None:
    """Write the two fixture files.

    Parameters
    ----------
    arguments : `argparse.Namespace`
        The command line.
    events : `list` [`dict`]
        Every event PHD2 sent.
    home : `pathlib.Path`
        The throwaway home folder (the guide log is under ``PHD2/``).

    Raises
    ------
    RuntimeError
        If PHD2 wrote no guide log.
    """
    version = phd2_version(arguments.phd2)
    header = {
        "Recording": "PHD2 contract",
        "PHD2": version,
        "PHD2Source": str(arguments.phd2_commit),
        "Recorded": datetime.now().strftime("%Y-%m-%d"),
        "Script": "build/tpm/record_phd2_contract.py",
        "AllEvents": len(events),
    }
    kept = trim_events(events)
    FIXTURES_DIR.mkdir(exist_ok=True)
    with EVENTS_FIXTURE.open("w", encoding="utf-8") as handle:
        for line in [header, *kept]:
            handle.write(json.dumps(line) + "\n")
    logs = sorted((home / "PHD2").glob("PHD2_GuideLog_*.txt"))
    if not logs:
        raise RuntimeError("PHD2 wrote no guide log.")
    lines = logs[-1].read_text(encoding="utf-8", errors="replace").splitlines()
    GUIDE_LOG_FIXTURE.write_text("\n".join(lines[:600]) + "\n", encoding="utf-8")
    print(f"Wrote {EVENTS_FIXTURE} ({len(kept)} of {len(events)} events) and {GUIDE_LOG_FIXTURE}")


def main() -> int:
    """Seal the network if needed, then record.

    Returns
    -------
    status : `int`
        The process exit status.
    """
    parser = argparse.ArgumentParser(description="Record a real PHD2 for the contract test.")
    parser.add_argument("--phd2", default=str(Path.home() / ".local/bin/phd2"))
    parser.add_argument("--phd2-commit", default="built from source in ~/.local (commit 264a64d)")
    parser.add_argument("--guide-seconds", type=float, default=120.0)
    parser.add_argument("--keep-home", action="store_true")
    arguments = parser.parse_args()
    if os.environ.get(SEALED_MARKER) != "1":
        command = [
            "bwrap", "--bind", "/", "/", "--dev-bind", "/dev", "/dev", "--proc", "/proc",
            "--unshare-net", "--unshare-pid", "--die-with-parent",
            sys.executable, str(Path(__file__).resolve()), *sys.argv[1:],
        ]  # fmt: skip
        return subprocess.call(command, env={**os.environ, SEALED_MARKER: "1"})
    return record(arguments)


if __name__ == "__main__":
    sys.exit(main())
