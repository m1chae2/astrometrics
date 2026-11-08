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

Both files go in ``simulator/test/fixtures/``. Run it again
after a PHD2 upgrade, then run
``.venv/bin/pytest simulator/test/test_phd2_contract.py``.
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
import time
from datetime import datetime
from pathlib import Path

TPM_DIR = Path(__file__).resolve().parent
# Run as a script, this file's folder is on the path, not the repository's.
sys.path.insert(0, str(TPM_DIR.parents[1]))

from simulator import phd2_launch  # ruff: ignore[module-import-not-at-top-of-file]
from wayfindinglib.drivers import PHD2Client, PHD2Session  # ruff: ignore[module-import-not-at-top-of-file]

FIXTURES_DIR = TPM_DIR.parents[1] / "simulator" / "test" / "fixtures"
EVENTS_FIXTURE = FIXTURES_DIR / "phd2_contract_events.jsonl"
GUIDE_LOG_FIXTURE = FIXTURES_DIR / "phd2_contract_guide_log.txt"
#: PHD2's event server port for instance 1 (4400 + instance - 1).
#: Safe inside the seal.
EVENT_PORT = 4400
#: Marks the process that already runs inside the sealed network.
SEALED_MARKER = "PHD2_CONTRACT_SEALED"
#: The INDI simulator devices PHD2 is pointed at.
CAMERA_DEVICE = phd2_launch.GUIDE_CAMERA_DEVICE
MOUNT_DEVICE = phd2_launch.MOUNT_DEVICE

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


def drive(session: PHD2Session, guide_seconds: float) -> list[str]:
    """Drive PHD2 through one guiding session and return notes.

    Parameters
    ----------
    session : `PHD2Session`
        The connection to PHD2, from the library's one PHD2 client.
    guide_seconds : `float`
        Seconds to guide before the dither.

    Returns
    -------
    notes : `list` [`str`]
        One line per step, for the console.
    """
    notes: list[str] = []

    def note(text: str) -> None:
        """Keep one line and print it."""
        notes.append(text)
        print(text, flush=True)

    session.set_exposure(phd2_launch.DEFAULT_EXPOSURE_MS)
    note(f"set_exposure: {phd2_launch.DEFAULT_EXPOSURE_MS} ms")
    session.set_connected(True)
    note(f"get_current_equipment: {json.dumps(session.get_current_equipment())[:200]}")
    mark = session.event_count
    session.loop()
    note("loop")
    session.wait_for_event("LoopingExposures", mark, 30)
    time.sleep(3)
    note(f"find_star: {session.find_star()}")
    mark = session.event_count
    session.guide(settle_pixels=1.5, settle_seconds=6, settle_timeout_seconds=120)
    note("guide")
    note(f"first settle: {session.wait_for_event('SettleDone', mark, 400)}")
    time.sleep(guide_seconds)
    mark = session.event_count
    session.dither(3, False, settle_pixels=1.5, settle_seconds=6, settle_timeout_seconds=60)
    note("dither")
    note(f"dither settle: {session.wait_for_event('SettleDone', mark, 90)}")
    time.sleep(15)
    session.stop_capture()
    note("stop_capture")
    session.wait_for_event("AppState", mark, 20, State="Stopped")
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
        # The simulators save their settings here, never in the user's ~/.indi.
        indi_home = home / "indi_home"
        (indi_home / ".indi").mkdir(parents=True)
        processes.append(
            subprocess.Popen(
                [
                    "indiserver",
                    *("-p", str(indi_port)),
                    *("-u", str(home / "indiserver.sock")),
                    "indi_simulator_telescope",
                    "indi_simulator_guide",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "HOME": str(indi_home),
                },
                start_new_session=True,
            )
        )
        if not wait_for_port(indi_port, 15):
            print("ERROR: indiserver did not start.", file=sys.stderr)
            return 1
        prepare_simulators(indi_port)
        phd2_launch.write_profile(home, home, 1, indi_port)
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
        session = PHD2Client(host="127.0.0.1", port=EVENT_PORT).open_session(timeout_seconds=10)
        try:
            drive(session, arguments.guide_seconds)
        finally:
            session.shutdown()
            time.sleep(3)
        write_fixtures(arguments, session.events_since(0), home)
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
