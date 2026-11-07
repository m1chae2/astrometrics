"""Purpose: Play the observing night for the simulated TPM run.

Description: In the simulated profile the throwaway backend sees a telescope
that is "on". This script makes it busy, the way Ekos does on the telescope
computer:

1. It connects the INDI simulators (mount, camera, focuser, filter wheel,
   guide camera) of the local INDI server and points the mount at a target
   (M 13 by default, which is in the seeded library).
2. It takes one exposure after another on the simulated camera. The camera
   saves each FITS file straight into a folder that stands in for the
   telescope computer's picture folder (``<standin>/Pictures/<target>/Light``).
3. It writes a guide log into the stand-in's guide log folder, one line at a
   time, at the pace of a real log (`guide_log_replay.py`). The app pulls
   both folders through its one rsync runner and a local stand-in for ssh.

It records when each frame appeared and how many log lines were written in a
statistics file, which `observatory_metrics.py` turns into TPMs.

Safety: it talks only to 127.0.0.1 on the INDI port given on the command line,
and writes only inside the stand-in folder it is given.

Usage::

    python -m simulator.exposure_driver --indi-port N \
        --standin <folder> --stats <out.json> \
        --guide-log simulator/guide_log_sample.txt \
        [--exposure-seconds 60] [--target "M 13"] \
        [--ra-hours 16.695] [--dec-degrees 36.46]

The script stops on SIGTERM or SIGINT.
"""

import argparse
import json
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from simulator.guide_log_replay import ReplayItem, load_replay_items, render_line, replay

#: The devices of the INDI simulators and the property that connects each.
DEVICES = ("Telescope Simulator", "CCD Simulator", "Focuser Simulator", "Filter Simulator", "Guide Simulator")
CAMERA = "CCD Simulator"
MOUNT = "Telescope Simulator"
#: The INDI server is always on this computer.
INDI_HOST = "127.0.0.1"
#: Seconds one indi_setprop or indi_getprop call may take.
COMMAND_TIMEOUT_SECONDS = 20
#: Seconds to wait for the simulators to appear after the server starts.
DEVICE_WAIT_SECONDS = 60
#: Seconds past the exposure time to wait for the file before giving up on
#: a frame.
FRAME_GRACE_SECONDS = 60


#: File written next to the statistics file when the simulators are connected.
READY_FILE_NAME = "driver_ready"


class ExposureDriver:
    """Drives the INDI simulators and the guide log writer.

    Parameters
    ----------
    indi_port : `int`
        Port of the local INDI server.
    standin : `pathlib.Path`
        Home folder of the stand-in telescope computer.
    guide_log : `pathlib.Path`
        The real guide log to replay.
    stats_path : `pathlib.Path`
        Where to write the statistics.
    exposure_seconds : `float`
        Length of each exposure.
    target : `str`
        The target's folder name under the picture folder.
    ra_hours, dec_degrees : `float`
        Where to point the mount.
    """

    def __init__(
        self,
        indi_port: int,
        standin: Path,
        guide_log: Path,
        stats_path: Path,
        exposure_seconds: float,
        target: str,
        ra_hours: float,
        dec_degrees: float,
    ) -> None:
        """Keep the settings; nothing runs yet."""
        self.indi_port = indi_port
        self.standin = standin
        self.guide_log = guide_log
        self.stats_path = stats_path
        self.exposure_seconds = exposure_seconds
        self.target = target
        self.ra_hours = ra_hours
        self.dec_degrees = dec_degrees
        self.stop_event = threading.Event()
        self.pictures_directory = standin / "Pictures" / target / "Light"
        self.guide_log_directory = standin / ".local" / "share" / "kstars" / "guidelogs"
        self.frames: list[dict] = []
        self.guide_log_lines = 0
        self.started_at = time.time()
        self._lock = threading.Lock()

    def _indi(self, tool: str, *arguments: str) -> subprocess.CompletedProcess:
        """Run indi_setprop or indi_getprop against the local server.

        Parameters
        ----------
        tool : `str`
            ``indi_setprop`` or ``indi_getprop``.
        *arguments : `str`
            The tool's arguments after the host and port.

        Returns
        -------
        result : `subprocess.CompletedProcess`
            The finished call, with its output as text.
        """
        command = [tool, "-h", INDI_HOST, "-p", str(self.indi_port), *arguments]
        return subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=COMMAND_TIMEOUT_SECONDS
        )

    def _set(self, *settings: str) -> None:
        """Set INDI properties.

        An example setting is ``CCD Simulator.CONNECTION.CONNECT=On``.

        Parameters
        ----------
        *settings : `str`
            One or more ``device.property.element=value`` texts.
        """
        result = self._indi("indi_setprop", *settings)
        if result.returncode != 0:
            print(f"indi_setprop failed for {settings}: {result.stderr.strip()}", file=sys.stderr)

    def _get(self, name: str) -> str:
        """Read one INDI property value, or an empty text if it is not there.

        Parameters
        ----------
        name : `str`
            ``device.property.element``.

        Returns
        -------
        value : `str`
            The value.
        """
        result = self._indi("indi_getprop", "-t", "2", name)
        line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        return line.partition("=")[2]

    def write_stats(self) -> None:
        """Write the statistics file."""
        with self._lock:
            stats = {
                "started_at": self.started_at,
                "target": self.target,
                "exposure_seconds": self.exposure_seconds,
                "frames": list(self.frames),
                "guide_log_lines_written": self.guide_log_lines,
            }
        temporary = self.stats_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        temporary.replace(self.stats_path)

    def connect_devices(self, upload_directory: Path | None = None) -> bool:
        """Connect every simulator and point the mount.

        Parameters
        ----------
        upload_directory : `pathlib.Path`, optional
            Where the simulated camera saves its files. By default it is the
            stand-in picture folder of the target, where the app finds them.
            The replay gives a scratch folder, because its frames are real
            files copied in by the replay driver.

        Returns
        -------
        connected : `bool`
            `False` if the camera or the mount did not appear in time.
        """
        deadline = time.time() + DEVICE_WAIT_SECONDS
        while time.time() < deadline and not self.stop_event.is_set():
            if all(self._get(f"{device}.CONNECTION.CONNECT") for device in (CAMERA, MOUNT)):
                break
            time.sleep(1)
        else:
            return False
        for device in DEVICES:
            self._set(f"{device}.CONNECTION.CONNECT=On")
        # The mount's properties appear after it connects.
        for _ in range(20):
            if self._get(f"{MOUNT}.EQUATORIAL_EOD_COORD.RA"):
                break
            time.sleep(0.5)
        self._set(f"{MOUNT}.GEOGRAPHIC_COORD.LAT;LONG;ELEV=45.76;249.26;1500")
        # The simulated mount starts parked. A parked mount ignores slews.
        self._set(f"{MOUNT}.TELESCOPE_PARK.UNPARK=On")
        for _ in range(20):
            if self._get(f"{MOUNT}.TELESCOPE_PARK.UNPARK") == "On":
                break
            time.sleep(0.5)
        self.point_mount(self.ra_hours, self.dec_degrees)
        destination = upload_directory or self.pictures_directory
        destination.mkdir(parents=True, exist_ok=True)
        prefix = re.sub(r"[^A-Za-z0-9]+", "_", self.target) + "_Light_XXX"
        self._set(
            f"{CAMERA}.UPLOAD_MODE.UPLOAD_LOCAL=On",
            f"{CAMERA}.UPLOAD_SETTINGS.UPLOAD_DIR={destination}",
            f"{CAMERA}.UPLOAD_SETTINGS.UPLOAD_PREFIX={prefix}",
        )
        return True

    def point_mount(self, ra_hours: float, dec_degrees: float) -> None:
        """Track at the given coordinates (the simulated mount slews there).

        Parameters
        ----------
        ra_hours : `float`
            Right ascension in hours.
        dec_degrees : `float`
            Declination in degrees.
        """
        self._set(f"{MOUNT}.ON_COORD_SET.TRACK=On")
        self._set(f"{MOUNT}.EQUATORIAL_EOD_COORD.RA;DEC={ra_hours};{dec_degrees}")

    def _fits_files(self) -> set[str]:
        """List the FITS files in the picture folder.

        Returns
        -------
        names : `set` [`str`]
            File names.
        """
        return {path.name for path in self.pictures_directory.glob("*.fits")}

    def take_exposures(self) -> None:
        """Take exposures one after another until stopped."""
        while not self.stop_event.is_set():
            before = self._fits_files()
            started = time.time()
            self._set(f"{CAMERA}.CCD_EXPOSURE.CCD_EXPOSURE_VALUE={self.exposure_seconds}")
            deadline = started + self.exposure_seconds + FRAME_GRACE_SECONDS
            new_name = None
            while time.time() < deadline and not self.stop_event.is_set():
                added = self._fits_files() - before
                if added:
                    new_name = sorted(added)[0]
                    break
                self.stop_event.wait(0.5)
            if new_name is None:
                if not self.stop_event.is_set():
                    print("No frame appeared in time; trying the next exposure.", file=sys.stderr)
                continue
            # The file is complete once its size stops changing.
            path = self.pictures_directory / new_name
            size = -1
            while size != path.stat().st_size and not self.stop_event.is_set():
                size = path.stat().st_size
                self.stop_event.wait(0.3)
            with self._lock:
                self.frames.append({
                    "name": new_name,
                    "appeared_at": path.stat().st_mtime,
                    "exposure_started_at": started,
                    "size_bytes": path.stat().st_size,
                })
            self.write_stats()

    def write_guide_log(self) -> None:
        """Append the replayed guide log, line by line, to a growing file."""
        self.guide_log_directory.mkdir(parents=True, exist_ok=True)
        log_path = self.guide_log_directory / f"guide_log-{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}.txt"
        items = load_replay_items(self.guide_log)
        with log_path.open("a", encoding="utf-8") as handle:

            def write(item: ReplayItem) -> None:
                """Write one line and flush it, so a copy sees it at once."""
                handle.write(render_line(item, datetime.now()) + "\n")
                handle.flush()
                with self._lock:
                    self.guide_log_lines += 1

            replay(items, write, self.stop_event.is_set, self.stop_event.wait, loop=False)

    def run(self) -> int:
        """Connect, then take exposures and write the guide log until stopped.

        Returns
        -------
        status : `int`
            1 if the simulators could not be reached, else 0.
        """
        if not self.connect_devices():
            print("ERROR: the INDI simulators did not appear.", file=sys.stderr)
            return 1
        # Tells the program that started this one that the simulators are
        # connected and the mount is pointing, so the app can open.
        self.stats_path.with_name(READY_FILE_NAME).write_text(str(time.time()), encoding="utf-8")
        log_thread = threading.Thread(target=self.write_guide_log, daemon=True, name="GuideLogWriter")
        log_thread.start()
        stats_thread = threading.Thread(target=self._stats_loop, daemon=True, name="StatsWriter")
        stats_thread.start()
        self.take_exposures()
        self.write_stats()
        return 0

    def _stats_loop(self) -> None:
        """Write the statistics every few seconds until stopped."""
        while not self.stop_event.wait(2.0):
            self.write_stats()


def main() -> int:
    """Run the driver from the command line.

    Returns
    -------
    status : `int`
        The process exit status.
    """
    parser = argparse.ArgumentParser(description="Exposure and guide log driver for the simulated TPM run.")
    parser.add_argument("--indi-port", type=int, required=True)
    parser.add_argument("--standin", type=Path, required=True)
    parser.add_argument("--guide-log", type=Path, required=True)
    parser.add_argument("--stats", type=Path, required=True)
    parser.add_argument("--exposure-seconds", type=float, default=60.0)
    parser.add_argument("--target", default="M 13")
    parser.add_argument("--ra-hours", type=float, default=16.695)
    parser.add_argument("--dec-degrees", type=float, default=36.46)
    arguments = parser.parse_args()
    if arguments.indi_port in (5000, 7624):
        print(
            f"Refusing INDI port {arguments.indi_port}: it is the real backend's or a real INDI port.",
            file=sys.stderr,
        )
        return 1
    driver = ExposureDriver(
        arguments.indi_port,
        arguments.standin,
        arguments.guide_log,
        arguments.stats,
        arguments.exposure_seconds,
        arguments.target,
        arguments.ra_hours,
        arguments.dec_degrees,
    )
    signal.signal(signal.SIGTERM, lambda *_: driver.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: driver.stop_event.set())
    return driver.run()


if __name__ == "__main__":
    sys.exit(main())
