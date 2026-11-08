"""Purpose: Play the observing night for the simulated TPM run.

Description: In the simulated profile the throwaway backend sees a telescope
that is "on". This script makes it busy, the way Ekos does on the telescope
computer:

1. It connects the INDI simulators (mount, camera, focuser, filter wheel,
   guide camera) of the local INDI server and points the mount at a target
   (M 13 by default, which is in the seeded library). It starts there and
   then follows the mount: when the world gives a backend port, each frame
   is filed under the library target nearest the mount's position (within a
   degree, the app's own rule), so a slew from the app changes the folder.
2. In ``driver`` capture mode (the default, used by the TPM harness) it takes
   one exposure after another on the simulated camera, and never while the
   mount is slewing. In ``app`` capture mode (the launcher's interactive
   session) it takes none: the app starts the exposures (Start Capture), and
   the driver only files each finished frame. The camera
   saves each FITS file straight into a folder that stands in for the
   telescope computer's picture folder (``<standin>/Pictures/<target>/Light``).
   The frames match the real telescope's geometry and capture labels, not its
   sky, noise or star brightness (INDI's defaults), so each frame is marked
   ``SYNTHETC = T``.
   When the world gives a backend port and the target has real light frames
   with the same camera, filter and capture setting, a real frame is
   delivered in place of the drawn one (`frame_source`), and that frame does
   not carry ``SYNTHETC``.
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
        [--guide-log simulator/guide_log_sample.txt] \
        [--backend-port N] [--fitted-settings <fit_settings.json>] \
        [--exposure-seconds 60] [--target "M 13"] [--capture driver|app] \
        [--ra-hours 16.695] [--dec-degrees 36.46] [--config <settings.toml>]

The script stops on SIGTERM or SIGINT.
"""

import argparse
import json
import math
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from astropy.io import fits

from astrometricslib import Target
from astrometricslib.exceptions import AstrometricsError
from simulator.device_settings import (
    DeviceSetting,
    device_settings_from_world,
    load_world_settings,
    matches,
    merge_device_settings,
)
from simulator.fit.results import fitted_device_settings
from simulator.frame_source import FrameSource
from simulator.guide_log_replay import ReplayItem, load_replay_items, render_line, replay
from simulator.library_copy import nearest_target_id
from simulator.rpc_client import call_rpc

#: The devices of the INDI simulators and the property that connects each.
DEVICES = ("Telescope Simulator", "CCD Simulator", "Focuser Simulator", "Filter Simulator", "Guide Simulator")
CAMERA = "CCD Simulator"
FOCUSER = "Focuser Simulator"
#: The camera simulator's property that holds the seeing and the best focus.
FOCUS_SIMULATION_PROPERTY = "SIM_FOCUSING"
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
COOLING_WAIT_SECONDS = 60.0
COOLING_TOLERANCE_C = 1.0
#: Tries to set one property before giving up.
SETTING_TRIES = 3
#: Who starts the exposures. The ``driver`` takes them itself (the TPM
#: harness). The ``app`` does (Start Capture in the app), and the driver only
#: files the frames that appear.
CAPTURE_DRIVER = "driver"
CAPTURE_APP = "app"
CAPTURE_MODES = (CAPTURE_DRIVER, CAPTURE_APP)
#: Seconds between two looks at an idle camera in ``app`` mode.
WATCH_POLL_SECONDS = 0.5
#: Seconds between two checks of the mount's position while idle in ``app``
#: mode, so the file name prefix is right before the app's next exposure.
IDLE_FOLLOW_SECONDS = 2.0
#: The longest wait (seconds) for a slew to end before an exposure.
SLEW_WAIT_SECONDS = 300.0
#: The pseudo-element ``indi_getprop`` gives for a property's state.
STATE_ELEMENT = "_STATE"


#: How far (degrees) the mount must move before the target is looked up again.
MOUNT_MOVE_THRESHOLD_DEGREES = 0.05
#: How long (seconds) the list of library targets is reused. A lookup that
#: finds nothing asks again sooner (`TARGET_RETRY_SECONDS`), so a target the
#: user just added is found.
TARGET_LIST_SECONDS = 300.0
TARGET_RETRY_SECONDS = 30.0


def position_name(ra_hours: float, dec_degrees: float) -> str:
    """Name a sky position in text that is safe as a folder name.

    The name is used when no library target is near the mount, for example
    ``RA 16h41m Dec +36d28m``. It is rounded to the minute.

    Parameters
    ----------
    ra_hours : `float`
        Right ascension in hours.
    dec_degrees : `float`
        Declination in degrees.

    Returns
    -------
    name : `str`
        The position as text.
    """
    ra_minutes = round((ra_hours % 24.0) * 60.0) % 1440
    dec_minutes = round(abs(dec_degrees) * 60.0)
    sign = "-" if dec_degrees < 0 else "+"
    return (
        f"RA {ra_minutes // 60:02d}h{ra_minutes % 60:02d}m "
        f"Dec {sign}{dec_minutes // 60:02d}d{dec_minutes % 60:02d}m"
    )


def upload_prefix(target: str) -> str:
    """Build the camera's file name prefix for a target.

    Parameters
    ----------
    target : `str`
        The target's folder name.

    Returns
    -------
    prefix : `str`
        The prefix, with letters and digits only (other runs become ``_``).
    """
    return re.sub(r"[^A-Za-z0-9]+", "_", target) + "_Light_XXX"


class BackendTargetFinder:
    """Finds the library target the app would pick for a mount position.

    It asks the running backend for the target list (the driver must not open
    the library database while the backend runs) and keeps the list for a
    while, so a frame does not cost a full listing.

    Parameters
    ----------
    fetch_targets : `~collections.abc.Callable`
        Returns the backend's targets as ``target:get_targets`` gives them
        (the position is text, such as ``16h 41m 42s``; each entry is read
        through the library's `Target` model, as `library_copy` does). It
        may raise `RuntimeError` or `OSError`.
    clock : `~collections.abc.Callable`, optional
        Returns the time in seconds (for tests).
    """

    def __init__(
        self,
        fetch_targets: Callable[[], object],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Keep the fetch function; nothing is fetched yet."""
        self._fetch_targets = fetch_targets
        self._clock = clock
        self._targets: list[tuple[str, float, float]] = []
        self._fetched_at: float | None = None

    def _refresh(self) -> None:
        """Fetch the target list again.

        An unreachable backend raises `OSError` (from the fetch function).

        Raises
        ------
        RuntimeError
            If the reply is not a list.
        """
        reply = self._fetch_targets()
        if not isinstance(reply, list):
            raise RuntimeError("target:get_targets did not return a list.")
        targets: list[tuple[str, float, float]] = []
        for entry in reply:
            try:
                target = Target.model_validate(entry)
                targets.append((target.id, float(target.ra_deg), float(target.dec_deg)))
            except ValueError, TypeError, AstrometricsError:
                continue
        self._targets = targets
        self._fetched_at = self._clock()

    def __call__(self, ra_hours: float, dec_degrees: float) -> str | None:
        """Find the target nearest a position, within a degree.

        Parameters
        ----------
        ra_hours : `float`
            Right ascension in hours.
        dec_degrees : `float`
            Declination in degrees.

        Returns
        -------
        target_id : `str` or `None`
            The id, or `None` when no target is within a degree.

        The call raises `RuntimeError` or `OSError` when the backend's list
        cannot be had (see `_refresh`).
        """
        if self._fetched_at is None or self._clock() - self._fetched_at > TARGET_LIST_SECONDS:
            self._refresh()
        found = nearest_target_id(self._targets, ra_hours, dec_degrees)
        if found is None and self._clock() - (self._fetched_at or 0.0) > TARGET_RETRY_SECONDS:
            self._refresh()
            found = nearest_target_id(self._targets, ra_hours, dec_degrees)
        return found


#: The header key the camera simulator does not write.
OFFSET_KEYWORD = "OFFSET"
#: The general header flag for a frame that was drawn, not taken from the sky.
SYNTHETIC_KEYWORD = "SYNTHETC"


def unused_frame_name(name: str, directory: Path) -> str:
    """Give a frame a file name that the picture folder does not have yet.

    The camera numbers its files by looking at its own upload folder
    (``..._Light_001.fits``). Frames leave that folder at once, so the camera
    starts at 001 every time. The number is counted again here, on the
    picture folder, so each frame keeps a name of its own.

    Parameters
    ----------
    name : `str`
        The name the camera gave, ending in ``_<number>.fits``.
    directory : `pathlib.Path`
        The picture folder.

    Returns
    -------
    name : `str`
        The same name with the lowest number that is free in `directory`.
    """
    stem = re.sub(r"_\d+\.fits$", "", name)
    number = 1
    while (directory / f"{stem}_{number:03d}.fits").exists():
        number += 1
    return f"{stem}_{number:03d}.fits"


def stamp_offset(source: Path, destination: Path, offset: float | None, mark_synthetic: bool = False) -> None:
    """Move a frame into its final folder with the camera offset in its header.

    The CCD simulator writes a ``GAIN`` header but no ``OFFSET``. The app
    reads both to find the capture setting, so the offset the operator set
    (``CCD_OFFSET``) is added. With ``mark_synthetic``, the general
    ``SYNTHETC`` flag is added too, so the app can tell a drawn frame from a
    real one. Only the header changes: the stored pixel values are copied as
    they are, without scaling. The new file is written next to the source
    (same folder, so the same file system), then renamed over the destination
    in one step, so a transfer never sees a half-written file. A keyword that
    is already in the header is left as it is.

    Parameters
    ----------
    source : `pathlib.Path`
        The finished frame the camera wrote (a staging folder).
    destination : `pathlib.Path`
        Where the app will find it. Replaced if it exists.
    offset : `float` or `None`
        The offset to write. `None` writes no offset.
    mark_synthetic : `bool`, optional
        `True` to add ``SYNTHETC = T``. Leave `False` for a real frame.
    """
    if offset is not None or mark_synthetic:
        with fits.open(source, memmap=False, do_not_scale_image_data=True) as hdus:
            header = hdus[0].header
            changed = False
            if offset is not None and OFFSET_KEYWORD not in header:
                value = int(offset) if float(offset).is_integer() else float(offset)
                header[OFFSET_KEYWORD] = (value, "Camera offset set by the simulator operator")
                changed = True
            if mark_synthetic and SYNTHETIC_KEYWORD not in header:
                header[SYNTHETIC_KEYWORD] = (True, "Drawn by a camera simulator, not a real sky")
                changed = True
            if changed:
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
    guide_log : `pathlib.Path` or `None`
        The real guide log to replay. `None` writes no guide log (the real
        PHD2 writes its own).
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
    frame_source : `~simulator.frame_source.FrameSource`, optional
        Delivers a real frame in place of the simulator's own when the
        mount points at a target the user has imaged. Default: none, so
        every frame is the simulator's.
    find_target : `~collections.abc.Callable`, optional
        Takes the mount's right ascension (hours) and declination (degrees)
        and returns the id of the library target the app would pick for it,
        or `None` when none is within a degree. It may raise `RuntimeError`
        or `OSError` (a status: the current target is kept). Default: none,
        so the target stays fixed.
    capture : `str`, optional
        ``driver`` (default): the driver starts every exposure. ``app``: the
        app starts them, and the driver only files the frames that appear.
    """

    def __init__(
        self,
        indi_port: int,
        standin: Path,
        guide_log: Path | None,
        stats_path: Path,
        exposure_seconds: float,
        target: str,
        ra_hours: float,
        dec_degrees: float,
        device_settings: list[DeviceSetting] | None = None,
        frame_source: FrameSource | None = None,
        find_target: Callable[[float, float], str | None] | None = None,
        capture: str = CAPTURE_DRIVER,
    ) -> None:
        """Keep the settings; nothing runs yet.

        Raises
        ------
        ValueError
            If `capture` is not one of `CAPTURE_MODES`.
        """
        if capture not in CAPTURE_MODES:
            raise ValueError(f"capture must be one of {CAPTURE_MODES}, not {capture!r}.")
        self.capture = capture
        self.indi_port = indi_port
        self.standin = standin
        self.guide_log = guide_log
        self.stats_path = stats_path
        self.exposure_seconds = exposure_seconds
        self.target = target
        self.ra_hours = ra_hours
        self.dec_degrees = dec_degrees
        self.device_settings = list(device_settings or [])
        self.frame_source = frame_source
        self.find_target = find_target
        self._resolved_position: tuple[float, float] | None = None
        self._lookup_failure_reported = False
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
                    if all(
                        matches(value, self._get(name), setting.read_back_decimals) for name, value in names
                    ):
                        break
                    self.stop_event.wait(0.25)
                else:
                    continue
                break
            else:
                failed.extend(
                    name
                    for name, value in names
                    if not matches(value, self._get(name), setting.read_back_decimals)
                )
        for name in failed:
            print(f"The simulator did not take the setting {name}.", file=sys.stderr)
        if any(setting.property_name == FOCUS_SIMULATION_PROPERTY for setting in self.device_settings):
            self.sync_focus_simulation()
        return failed

    def sync_focus_simulation(self) -> bool:
        """Make the camera simulator use its seeing setting at once.

        The camera simulator draws stars with a width it recomputes only when
        the focuser reports a new position (``ccd_simulator.cpp:1316-1337``:
        the width is the seeing plus a term that grows with the distance
        between the focuser position and ``SIM_FOCUS_POSITION``). Until then
        it keeps its starting width of 3.5 arcsec, whatever ``SIM_SEEING``
        says. So this sets ``SIM_FOCUS_POSITION`` to the focuser's current
        position (perfect focus) and moves the focuser one step, which makes
        the camera simulator read the new seeing.

        Returns
        -------
        synced : `bool`
            `True` when the focuser's position was read and the step sent.
            `False` (a status, logged) when the focuser did not answer.
        """
        name = f"{FOCUSER}.ABS_FOCUS_POSITION.FOCUS_ABSOLUTE_POSITION"
        try:
            position = round(float(self._get(name)))
        except ValueError:
            print(
                "The focuser did not report a position; the seeing setting waits for a focuser move.",
                file=sys.stderr,
            )
            return False
        self._set(f"{CAMERA}.{FOCUS_SIMULATION_PROPERTY}.SIM_FOCUS_POSITION={position + 1}")
        self._set(f"{name}={position + 1}")
        return True

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
        # The app lists this folder before the first frame arrives.
        self.pictures_directory.mkdir(parents=True, exist_ok=True)
        self._set(
            f"{CAMERA}.UPLOAD_MODE.UPLOAD_BOTH=On",
            f"{CAMERA}.UPLOAD_SETTINGS.UPLOAD_DIR={destination}",
            f"{CAMERA}.UPLOAD_SETTINGS.UPLOAD_PREFIX={upload_prefix(self.target)}",
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
            The camera offset setting (the ``header_value`` of the
            ``CCD_OFFSET`` setting, else its value), or `None` when the
            settings have none. The value sent to INDI may differ: it is the
            pedestal in ADU.
        """
        for setting in self.device_settings:
            if setting.device == CAMERA and setting.property_name == "CCD_OFFSET":
                if setting.header_value is not None:
                    return setting.header_value
                value = setting.elements[0][1]
                return None if isinstance(value, str) else value
        return None

    def _fits_files(self) -> set[str]:
        """List the FITS files in the camera's staging folder.

        Returns
        -------
        names : `set` [`str`]
            File names.
        """
        return {path.name for path in self.staging_directory.glob("*.fits")}

    def mount_position(self) -> tuple[float, float] | None:
        """Read where the simulated mount points.

        Returns
        -------
        position : `tuple` [`float`, `float`] or `None`
            Right ascension (hours) and declination (degrees), or `None` when
            the mount does not answer or gives text that is not a number.
        """
        try:
            ra_hours = float(self._get(f"{MOUNT}.EQUATORIAL_EOD_COORD.RA"))
            dec_degrees = float(self._get(f"{MOUNT}.EQUATORIAL_EOD_COORD.DEC"))
        except ValueError:
            return None
        if not (math.isfinite(ra_hours) and math.isfinite(dec_degrees)):
            return None
        return ra_hours, dec_degrees

    def follow_mount(self) -> bool:
        """Switch to the target the app would pick for the mount's position.

        Does nothing without `find_target`, when the mount cannot be read, or
        when the mount moved less than `MOUNT_MOVE_THRESHOLD_DEGREES` since
        the last lookup. When no library target is within a degree, the frame
        is filed under a name built from the position (`position_name`). A
        failed lookup is a status: one line to the error output, once, and
        the current target stays.

        Returns
        -------
        changed : `bool`
            `True` when the target changed.
        """
        if self.find_target is None:
            return False
        position = self.mount_position()
        if position is None:
            return False
        if self._resolved_position is not None:
            ra_hours, dec_degrees = position
            last_ra, last_dec = self._resolved_position
            # Small-angle separation is enough for a threshold this small.
            offset = math.hypot(
                (ra_hours - last_ra) * 15.0 * math.cos(math.radians(dec_degrees)), dec_degrees - last_dec
            )
            if offset < MOUNT_MOVE_THRESHOLD_DEGREES:
                return False
        try:
            found = self.find_target(*position)
        except (RuntimeError, OSError, ValueError) as error:
            if not self._lookup_failure_reported:
                self._lookup_failure_reported = True
                print(f"The target lookup failed ({error}); keeping {self.target}.", file=sys.stderr)
            return False
        self._lookup_failure_reported = False
        self._resolved_position = position
        name = found or position_name(*position)
        if name == self.target:
            return False
        self.switch_target(name)
        return True

    def switch_target(self, target: str) -> None:
        """File the next frames under another target.

        The camera keeps saving into its staging folder; only the picture
        folder and the file name prefix change.

        Parameters
        ----------
        target : `str`
            The new target's folder name.
        """
        self.target = target
        self.pictures_directory = self.standin / "Pictures" / target / "Light"
        self.pictures_directory.mkdir(parents=True, exist_ok=True)
        self._set(f"{CAMERA}.UPLOAD_SETTINGS.UPLOAD_PREFIX={upload_prefix(target)}")
        print(f"The mount moved: frames are now filed under {target}.", flush=True)

    def mount_is_slewing(self) -> bool:
        """Say whether the simulated mount is still moving to a position.

        The state of ``EQUATORIAL_EOD_COORD`` is ``Busy`` during a slew. If
        the state cannot be read, the position is read twice, a second
        apart: a mount that moved is slewing. A mount that does not answer
        counts as not slewing, so the driver never waits for it forever.

        Returns
        -------
        slewing : `bool`
            `True` while the mount moves.
        """
        state = self._get(f"{MOUNT}.EQUATORIAL_EOD_COORD.{STATE_ELEMENT}")
        if state:
            return state == "Busy"
        first = self.mount_position()
        if first is None:
            return False
        self.stop_event.wait(1.0)
        return self.mount_position() != first

    def wait_for_slew_to_end(self, timeout: float = SLEW_WAIT_SECONDS) -> bool:
        """Wait until the mount has stopped slewing.

        A frame taken during a slew shows no fixed place in the sky and was
        once filed under a position name.

        Parameters
        ----------
        timeout : `float`, optional
            The longest wait, in seconds.

        Returns
        -------
        settled : `bool`
            `True` when the mount is not slewing. `False` on a timeout or a
            stop (a status, logged on a timeout).
        """
        deadline = time.time() + timeout
        while not self.stop_event.is_set():
            if not self.mount_is_slewing():
                return True
            if time.time() >= deadline:
                print(f"The mount was still slewing after {timeout:.0f} s; exposing anyway.", file=sys.stderr)
                return False
            self.stop_event.wait(1.0)
        return False

    def file_frame(self, name: str, started: float | None) -> None:
        """File one finished frame from the staging folder and record it.

        Both capture modes use this. It waits until the file stops growing,
        gives it a name the picture folder does not have, delivers a real
        frame in its place where one matches (else stamps the simulator's
        frame), and adds the frame to the statistics.

        Parameters
        ----------
        name : `str`
            The file's name in the staging folder.
        started : `float` or `None`
            When the exposure began (seconds since the epoch). `None` (the
            app started it): the file's time minus its ``EXPTIME`` header
            value, else minus the driver's exposure time.
        """
        path = self.staging_directory / name
        # The file is complete once its size stops changing.
        size = -1
        while size != path.stat().st_size and not self.stop_event.is_set():
            size = path.stat().st_size
            self.stop_event.wait(0.3)
        if started is None:
            started = path.stat().st_mtime - self._exposure_time(path)
        self.pictures_directory.mkdir(parents=True, exist_ok=True)
        new_name = unused_frame_name(name, self.pictures_directory)
        path_in_pictures = self.pictures_directory / new_name
        if self.deliver_real_frame(path, path_in_pictures):
            # The real frame took the simulator frame's place.
            path.unlink(missing_ok=True)
        else:
            stamp_offset(path, path_in_pictures, self.camera_offset(), mark_synthetic=True)
        with self._lock:
            self.frames.append({
                "name": new_name,
                "appeared_at": path_in_pictures.stat().st_mtime,
                "exposure_started_at": started,
                "size_bytes": path_in_pictures.stat().st_size,
            })
        self.write_stats()

    def _exposure_time(self, path: Path) -> float:
        """Read a frame's exposure time.

        Parameters
        ----------
        path : `pathlib.Path`
            A finished FITS file.

        Returns
        -------
        seconds : `float`
            The ``EXPTIME`` header value, else the driver's exposure time.
        """
        try:
            return float(fits.getheader(path, memmap=False)["EXPTIME"])
        except OSError, KeyError, ValueError, TypeError:
            return self.exposure_seconds

    def take_exposures(self) -> None:
        """Take exposures one after another until stopped (``driver`` mode).

        An exposure starts only when the mount is not slewing.
        """
        while not self.stop_event.is_set():
            self.wait_for_slew_to_end()
            self.follow_mount()
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
            self.file_frame(new_name, started)

    def watch_for_frames(self) -> None:
        """File the frames the app takes, until stopped (``app`` mode).

        The driver sets no exposure. Each finished FITS file in the staging
        folder is filed after `follow_mount`, so its folder matches where the
        mount points. While idle, `follow_mount` runs every
        `IDLE_FOLLOW_SECONDS`, so the next frame's name prefix is right
        before the app starts its exposure. `follow_mount` skips small moves,
        so this is cheap.
        """
        last_follow = time.monotonic()
        while not self.stop_event.is_set():
            names = sorted(self._fits_files())
            if names:
                self.follow_mount()
                self.file_frame(names[0], None)
                last_follow = time.monotonic()
                continue
            if time.monotonic() - last_follow >= IDLE_FOLLOW_SECONDS:
                self.follow_mount()
                last_follow = time.monotonic()
            self.stop_event.wait(WATCH_POLL_SECONDS)

    def current_filter_name(self) -> str | None:
        """Read the name of the filter in the simulated wheel's current slot.

        Returns
        -------
        name : `str` or `None`
            The name, or `None` when the wheel does not answer.
        """
        slot = self._get("Filter Simulator.FILTER_SLOT.FILTER_SLOT_VALUE")
        try:
            number = int(float(slot))
        except ValueError:
            return None
        return self._get(f"Filter Simulator.FILTER_NAME.FILTER_SLOT_NAME_{number}") or None

    def deliver_real_frame(self, simulated: Path, destination: Path) -> bool:
        """Put a real frame at `destination` if the target has a match.

        The mount points at `target` (the library target the world chose, or
        the one nearest the mount after it moved), so that is the target whose
        real frames are used.

        Parameters
        ----------
        simulated : `pathlib.Path`
            The finished frame the camera simulator drew.
        destination : `pathlib.Path`
            Where the app will find the frame.

        Returns
        -------
        delivered : `bool`
            `True` when a real frame was written to `destination`. `False`
            leaves the simulator's frame to be used as it is.
        """
        if self.frame_source is None:
            return False
        return self.frame_source.deliver(
            self.target, simulated, destination, self.camera_offset(), self.current_filter_name()
        )

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
        """Connect, then take or file exposures until stopped.

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
        if self.guide_log is not None:
            log_thread = threading.Thread(target=self.write_guide_log, daemon=True, name="GuideLogWriter")
            log_thread.start()
        stats_thread = threading.Thread(target=self._stats_loop, daemon=True, name="StatsWriter")
        stats_thread.start()
        self.wait_for_cooling()
        if self.capture == CAPTURE_APP:
            self.watch_for_frames()
        else:
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
    parser.add_argument(
        "--guide-log",
        type=Path,
        default=None,
        help="A real guide log to replay. Leave it out with the real PHD2, which writes its own log.",
    )
    parser.add_argument("--stats", type=Path, required=True)
    parser.add_argument("--exposure-seconds", type=float, default=60.0)
    parser.add_argument("--target", default="M 13")
    parser.add_argument(
        "--capture",
        choices=CAPTURE_MODES,
        default=CAPTURE_DRIVER,
        help="Who takes the exposures: the driver (default, TPM harness) or the app (interactive).",
    )
    parser.add_argument("--ra-hours", type=float, default=16.695)
    parser.add_argument("--dec-degrees", type=float, default=36.46)
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="The world's settings file: the simulators take the real equipment's sensor and optics from it.",
    )
    parser.add_argument(
        "--fitted-settings",
        type=Path,
        default=None,
        help="The fit's results file (fit_results.json): the simulators are set to its values.",
    )
    parser.add_argument(
        "--backend-port",
        type=int,
        default=None,
        help="The world's backend port. With it, a target the user has imaged gets real frames.",
    )
    arguments = parser.parse_args()
    # No check on the backend port: the launcher passes its own child's port,
    # which is 5000 in interactive simulation mode (only one backend runs at a
    # time, and in a world it is the world's backend).
    if arguments.indi_port in (5000, 7624):
        print(
            f"Refusing INDI port {arguments.indi_port}: it is the real backend's or a real INDI port.",
            file=sys.stderr,
        )
        return 1
    world = load_world_settings(arguments.config) if arguments.config else None
    frame_source = None
    find_target = None
    if arguments.backend_port is not None:
        backend_port = arguments.backend_port
        find_target = BackendTargetFinder(
            lambda: call_rpc(backend_port, "target:get_targets", {"detail": "summary"})
        )
        frame_source = FrameSource(
            lambda method, params: call_rpc(backend_port, method, params),
            arguments.standin / "frame_source_cache",
            camera=(
                str(world.get("Observatory.Camera", {}).get("default_primary_camera", "")).strip() or None
                if world
                else None
            ),
        )
    driver = ExposureDriver(
        arguments.indi_port,
        arguments.standin,
        arguments.guide_log,
        arguments.stats,
        arguments.exposure_seconds,
        arguments.target,
        arguments.ra_hours,
        arguments.dec_degrees,
        merge_device_settings(
            device_settings_from_world(world) if world else [],
            fitted_device_settings(arguments.fitted_settings) if arguments.fitted_settings else [],
        )
        or None,
        frame_source,
        find_target,
        arguments.capture,
    )
    signal.signal(signal.SIGTERM, lambda *_: driver.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: driver.stop_event.set())
    return driver.run()


if __name__ == "__main__":
    sys.exit(main())
