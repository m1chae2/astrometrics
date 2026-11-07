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
        [--ra-hours 16.695] [--dec-degrees 36.46] [--config <settings.toml>]

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

from astropy.io import fits

from simulator.device_settings import (
    DeviceSetting,
    device_settings_from_world,
    load_world_settings,
    matches,
)
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
#: Seconds to wait for a simulator property to show the value just set.
SETTING_WAIT_SECONDS = 5.0
#: The longest wait for the simulated cooler to reach its setpoint, and how
#: close (degrees C) counts as there.
COOLING_WAIT_SECONDS = 180.0
COOLING_TOLERANCE_C = 1.0
#: Tries to set one property before giving up.
SETTING_TRIES = 3


#: The header key the camera simulator does not write.
OFFSET_KEYWORD = "OFFSET"


def stamp_offset(source: Path, destination: Path, offset: float | None) -> None:
    """Move a frame into its final folder with the camera offset in its header.

    The CCD simulator writes a ``GAIN`` header but no ``OFFSET``. The app
    reads both to find the capture setting, so the offset the operator set
    (``CCD_OFFSET``) is added. Only the header changes: the stored pixel
    values are copied as they are, without scaling. The new file is written
    next to the source (same folder, so the same file system), then renamed
    over the destination in one step, so a transfer never sees a half-written
    file. A frame that already has an ``OFFSET`` is moved as it is.

    Parameters
    ----------
    source : `pathlib.Path`
        The finished frame the camera wrote (a staging folder).
    destination : `pathlib.Path`
        Where the app will find it. Replaced if it exists.
    offset : `float` or `None`
        The offset to write. `None` moves the frame unchanged.
    """
    if offset is not None:
        with fits.open(source, memmap=False, do_not_scale_image_data=True) as hdus:
            if OFFSET_KEYWORD not in hdus[0].header:
                value = int(offset) if float(offset).is_integer() else float(offset)
                hdus[0].header[OFFSET_KEYWORD] = (value, "Camera offset set by the simulator operator")
                temporary = source.with_name(f".{source.name}.stamping")
                hdus.writeto(temporary, overwrite=True)
                temporary.replace(source)
    source.replace(destination)


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
    device_settings : `list` [`DeviceSetting`], optional
        INDI properties to set on the simulators after they connect (the
        real equipment's sensor, focal length and capture setting, see
        `simulator.device_settings`). Default: none.
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
        device_settings: list[DeviceSetting] | None = None,
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
        self.device_settings = list(device_settings or [])
        self.cooling_seconds: float | None = None
        self.stop_event = threading.Event()
        self.pictures_directory = standin / "Pictures" / target / "Light"
        # The camera saves here first; `stamp_offset` moves each frame on.
        self.staging_directory = standin / "camera_staging" / target / "Light"
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

    def apply_device_settings(self) -> list[str]:
        """Set the real equipment's properties on the connected simulators.

        Each property is set and then read back. A resolution change makes
        the camera simulator redefine its properties, which can undo a value
        set at the same moment, so a value that did not take is set again.

        Returns
        -------
        failed : `list` [`str`]
            The ``device.property.element`` names that did not show the value
            after the tries. Empty when all took. A failure is a status, not
            an error: the simulators still run with their own values.
        """
        failed: list[str] = []
        for setting in self.device_settings:
            names = setting.read_back_names()
            for _ in range(SETTING_TRIES):
                self._set(setting.specification())
                deadline = time.time() + SETTING_WAIT_SECONDS
                while time.time() < deadline and not self.stop_event.is_set():
                    if all(matches(value, self._get(name)) for name, value in names):
                        break
                    self.stop_event.wait(0.25)
                else:
                    continue
                break
            else:
                failed.extend(name for name, value in names if not matches(value, self._get(name)))
        for name in failed:
            print(f"The simulator did not take the setting {name}.", file=sys.stderr)
        return failed

    def write_stats(self) -> None:
        """Write the statistics file."""
        with self._lock:
            stats = {
                "started_at": self.started_at,
                "target": self.target,
                "exposure_seconds": self.exposure_seconds,
                "frames": list(self.frames),
                "guide_log_lines_written": self.guide_log_lines,
                "cooling_seconds": self.cooling_seconds,
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
        self.apply_device_settings()
        self._set(f"{MOUNT}.GEOGRAPHIC_COORD.LAT;LONG;ELEV=45.76;249.26;1500")
        # The simulated mount starts parked. A parked mount ignores slews.
        self._set(f"{MOUNT}.TELESCOPE_PARK.UNPARK=On")
        for _ in range(20):
            if self._get(f"{MOUNT}.TELESCOPE_PARK.UNPARK") == "On":
                break
            time.sleep(0.5)
        self.point_mount(self.ra_hours, self.dec_degrees)
        destination = upload_directory or self.staging_directory
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

    def camera_setpoint(self) -> float | None:
        """Give the cooler setpoint the operator set on the camera simulator.

        Returns
        -------
        temperature : `float` or `None`
            The ``CCD_TEMPERATURE`` target in degrees C, or `None` when the
            settings give none.
        """
        for setting in self.device_settings:
            if setting.device == CAMERA and setting.property_name == "CCD_TEMPERATURE":
                value = setting.elements[0][1]
                return None if isinstance(value, str) else value
        return None

    def wait_for_cooling(self, timeout: float = COOLING_WAIT_SECONDS) -> float | None:
        """Wait until the simulated sensor is near its setpoint.

        The simulator cools about half a degree per second, so a frame taken
        at once would carry the starting temperature in ``CCD-TEMP``.

        Parameters
        ----------
        timeout : `float`, optional
            The longest wait, in seconds.

        Returns
        -------
        seconds : `float` or `None`
            Seconds it took, or `None` when there is no setpoint or the sensor
            did not get there in time (a status, logged to the error output).
        """
        target = self.camera_setpoint()
        if target is None:
            return None
        started = time.time()
        while time.time() - started < timeout and not self.stop_event.is_set():
            current = self._get(f"{CAMERA}.CCD_TEMPERATURE.CCD_TEMPERATURE_VALUE")
            try:
                if abs(float(current) - target) <= COOLING_TOLERANCE_C:
                    self.cooling_seconds = time.time() - started
                    return self.cooling_seconds
            except ValueError:
                pass
            self.stop_event.wait(1.0)
        print(f"The simulated sensor did not reach {target} C in {timeout:.0f} s.", file=sys.stderr)
        return None

    def camera_offset(self) -> float | None:
        """Give the offset the operator set on the camera simulator.

        Returns
        -------
        offset : `float` or `None`
            The value of the ``CCD_OFFSET`` setting, or `None` when the
            settings have none.
        """
        for setting in self.device_settings:
            if setting.device == CAMERA and setting.property_name == "CCD_OFFSET":
                return setting.elements[0][1]
        return None

    def _fits_files(self) -> set[str]:
        """List the FITS files in the camera's staging folder.

        Returns
        -------
        names : `set` [`str`]
            File names.
        """
        return {path.name for path in self.staging_directory.glob("*.fits")}

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
            path = self.staging_directory / new_name
            size = -1
            while size != path.stat().st_size and not self.stop_event.is_set():
                size = path.stat().st_size
                self.stop_event.wait(0.3)
            self.pictures_directory.mkdir(parents=True, exist_ok=True)
            path_in_pictures = self.pictures_directory / new_name
            stamp_offset(path, path_in_pictures, self.camera_offset())
            path = path_in_pictures
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
        self.wait_for_cooling()
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
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="The world's settings file: the simulators take the real equipment's sensor and optics from it.",
    )
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
        device_settings_from_world(load_world_settings(arguments.config)) if arguments.config else None,
    )
    signal.signal(signal.SIGTERM, lambda *_: driver.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: driver.stop_event.set())
    return driver.run()


if __name__ == "__main__":
    sys.exit(main())
