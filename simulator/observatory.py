"""Purpose: Start and stop the simulated observatory's stand-in processes.

Description: `SimulatedObservatory` gives the app a telescope that is "on"
without a telescope. It owns these pieces, all on this computer:

* an INDI server (``indiserver``) with the INDI simulators, on a free port;
* the guider on a free port: the real PHD2 (`phd2_launch`) for the interactive
  simulation mode, or the stand-in PHD2 (`mock_phd2`) for the TPM runs and the
  replay. If the real PHD2 is not installed, or does not start,
  the stand-in plays it and the world reports a note (a status, not an error);
* a folder that plays the telescope computer (``<root>/standin/home``) and an
  ``ssh`` stand-in (`standin_ssh.sh`) that the app's transfer code reaches by
  PATH;
* the exposure driver (`exposure_driver`) for the "simulated sky" kind, or the
  replay driver (`replay_driver`) for the "replay" kind.

Every process runs under `supervisor`, so none outlives its owner. The class
never opens a connection beyond this computer, and `prepare` refuses a
settings file that points anywhere else, or that places a library outside the
simulator's folder or on one of the user's real folders (`observatory_guard`).

Nothing in this file reads the user's real settings except the replay kind,
which reads one night of the real library and logs, read only, through
`night_replay`.

`prepare_offline_world` builds the same kind of folder with no telescope at
all. The app uses it when the simulator cannot start, so a failed simulator
never leaves the app talking to the real telescope.

The module is also a program, for the TPM test harness and for anyone who
wants the stand-ins without the app::

    python -m simulator.observatory start --kind simulated \
        --root <folder> --indi-port N --phd2-port N --ports-file <file>
    python -m simulator.observatory stop --root <folder>

``start`` stays in the foreground until it gets SIGTERM, or the program that
started it ends. It writes the ports file once the stand-ins listen.
"""

import argparse
import enum
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from simulator import library_copy, observatory_guard, phd2_launch
from simulator.errors import SimulationUnavailableError
from simulator.settings import (
    FALLBACK_TELESCOPE_KEYS,
    SIMULATED_LAUNCH_SETTINGS,
    SIMULATOR_INSTRUMENT_NAME,
    endpoint_keys,
    library_settings_text,
    offline_config_text,
    simulator_config_text,
    standin_home,
    with_launch_section,
)

logger = logging.getLogger(__name__)

#: The programs the INDI side needs.
REQUIRED_PROGRAMS = (
    "indiserver",
    "indi_simulator_telescope",
    "indi_simulator_ccd",
    "indi_simulator_focus",
    "indi_simulator_wheel",
    "indi_simulator_guide",
    "indi_setprop",
    "indi_getprop",
    "rsync",
)
#: The INDI simulator drivers the server loads.
INDI_DRIVERS = (
    "indi_simulator_telescope",
    "indi_simulator_ccd",
    "indi_simulator_focus",
    "indi_simulator_wheel",
    "indi_simulator_guide",
)
#: The star catalog program the INDI camera simulators call to draw stars,
#: and the environment variable that names its data folder.
STAR_CATALOG_PROGRAM = "gsc"
STAR_CATALOG_DATA_VARIABLE = "GSCDAT"
#: Where the catalog's data may be installed, tried in this order when
#: ``GSCDAT`` is not already set.
STAR_CATALOG_DATA_FOLDERS = (
    Path("/usr/share/gsc"),
    Path("/usr/share/GSC"),
    Path("/usr/local/share/gsc"),
    Path("/usr/share/gsc-data"),
)
#: The text the launcher reports when the star catalog is missing. It is a
#: status: the world starts, and the simulators draw no stars.
STAR_CATALOG_MISSING_NOTE = "gsc (star catalog): stars are not drawn"
#: The command that installs the star catalog (about 240 MB to download).
STAR_CATALOG_INSTALL_COMMAND = "sudo apt install gsc"
#: The most bytes of a Unix-socket name the INDI server can bind. Longer
#: names get a short name made from the folder's path.
INDI_SOCKET_NAME_LIMIT = 100
#: The guide log the stand-in PHD2 plays in the simulated-sky kind.
GUIDE_LOG_FIXTURE = Path(__file__).resolve().parent / "guide_log_sample.txt"
#: The supervisor, run by path: it needs only the standard library.
SUPERVISOR_SCRIPT = Path(__file__).resolve().parent / "supervisor.py"
#: The ssh stand-in script.
STANDIN_SSH_SCRIPT = Path(__file__).resolve().parent / "standin_ssh.sh"
#: The target the simulated sky points at (the exposure driver default).
SIMULATED_TARGET_NAME = "M 13"
SIMULATED_TARGET_RA_HOURS = 16.695
SIMULATED_TARGET_DEC_DEGREES = 36.46
#: Name of the file in the root folder that holds the process id of a running
#: ``start`` command, for ``stop``.
PROCESS_ID_FILE = "observatory.pid"
#: Seconds the ``stop`` command waits for the ``start`` command to end.
STOP_WAIT_SECONDS = 20.0
#: Seconds the whole simulated telescope may take to be ready.
READY_WAIT_SECONDS = 120.0
#: Seconds to wait for the stand-ins to listen.
LISTEN_WAIT_SECONDS = 30.0
#: Seconds the replay driver may take to connect the simulators.
REPLAY_READY_WAIT_SECONDS = 90.0
#: Seconds between the replay start signal and the first replayed event.
REPLAY_LEAD_SECONDS = 3.0
#: Seconds the real PHD2 may take to open its port (it draws a window first).
PHD2_LISTEN_WAIT_SECONDS = 90.0
#: The note the world keeps when the stand-in PHD2 plays in place of the real
#: one. The reason follows it.
PHD2_FALLBACK_NOTE = "PHD2 (guiding program): the stand-in guider plays PHD2"


class GuiderKind(enum.StrEnum):
    """Which program plays PHD2."""

    MOCK = "mock"
    """The stand-in (`mock_phd2`): it replays a guide log, the same way."""

    PHD2 = "phd2"
    """The real PHD2, run against the INDI guide and telescope simulators."""


class SimulationKind(enum.StrEnum):
    """What the simulated telescope plays."""

    SIMULATED_SKY = "simulated"
    """Fresh frames from the INDI simulators; PHD2 plays a sample guide log."""

    REPLAY = "replay"
    """A past night: its real frames, guiding and Ekos log play back."""


def choose_free_port() -> int:
    """Pick a free loopback port that is not a real service's port.

    Returns
    -------
    port : `int`
        A port nothing listens on, never one in
        `observatory_guard.FORBIDDEN_PORTS`.

    Raises
    ------
    SimulationUnavailableError
        If no suitable port turns up after many tries.
    """
    for _ in range(50):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port not in observatory_guard.FORBIDDEN_PORTS:
            return port
    raise SimulationUnavailableError("Could not find a free local port.")


def is_listening(port: int) -> bool:
    """Say whether a local TCP port has a listener.

    Reads the kernel's table, so no connection is made (a connection to the
    stand-in PHD2 would start its replay early).

    Parameters
    ----------
    port : `int`
        The port.

    Returns
    -------
    listening : `bool`
        `True` when something listens on the port.
    """
    suffix = f":{port:04X}"
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) > 3 and fields[1].endswith(suffix) and fields[3] == "0A":
                return True
    return False


def missing_programs(include_overlay: bool = True) -> list[str]:
    """List the programs the simulator needs that are not installed.

    Parameters
    ----------
    include_overlay : `bool`, optional
        `True` (default) also asks for the programs that mount a copy of the
        real library (`library_copy.OVERLAY_PROGRAMS`).

    Returns
    -------
    missing : `list` [`str`]
        Program names, empty when all are on the PATH.
    """
    # ss is the connection guard's only eyes; without it the guard cannot work.
    names = (*REQUIRED_PROGRAMS, "ss", *(library_copy.OVERLAY_PROGRAMS if include_overlay else ()))
    return [name for name in names if shutil.which(name) is None]


def unavailable_reason(include_overlay: bool = True) -> str | None:
    """Say why the simulator cannot run here, if it cannot.

    Parameters
    ----------
    include_overlay : `bool`, optional
        Whether the library copy's programs are needed too.

    Returns
    -------
    reason : `str` or `None`
        `None` when it can run. In the packaged app the text says it is not
        available there (the packaged app does not carry the INDI
        simulators). Otherwise it names the programs that are missing.
    """
    if getattr(sys, "frozen", False):
        return "The simulator is not available in the packaged app."
    missing = missing_programs(include_overlay)
    if missing:
        return f"The simulator needs these programs: {', '.join(missing)}."
    return None


def offline_environment(root: Path) -> dict[str, str]:
    """Give the environment variables of an offline world.

    The refusing ``ssh`` stand-in comes first on the path, so nothing in the
    app can start the real ``ssh`` and reach a telescope computer.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder.

    Returns
    -------
    variables : `dict` [`str`, `str`]
        ``PATH`` with the stand-in first, and ``ASTROMETRICS_STANDIN_SSH_MODE``
        set to ``refuse``.
    """
    return {
        "PATH": f"{root / 'standin' / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        "ASTROMETRICS_STANDIN_SSH_MODE": "refuse",
    }


def indi_socket_name(root: Path) -> str:
    """Give the name of the world's own INDI server socket.

    ``indiserver -u NAME`` listens on a local (abstract) socket called NAME,
    by default ``/tmp/indiserver``, so two servers on one computer clash. Each
    world uses ``<root>/indiserver.sock`` instead. The name is not a file, and
    the system allows only about 100 bytes, so a long folder name gets a
    short name made from a hash of the folder.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder.

    Returns
    -------
    name : `str`
        The socket name for ``indiserver -u``.
    """
    name = str(root / "indiserver.sock")
    if len(name.encode("utf-8")) <= INDI_SOCKET_NAME_LIMIT:
        return name
    return f"astrometrics-indi-{hashlib.sha256(str(root).encode('utf-8')).hexdigest()[:16]}"


def indi_home(root: Path) -> Path:
    """Give the home folder the INDI server and its drivers run with.

    The drivers load and save their settings in ``<home>/.indi``. A folder
    inside the world keeps them away from the user's own ``~/.indi``.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder.

    Returns
    -------
    home : `pathlib.Path`
        ``<root>/indi_home``.
    """
    return root / "indi_home"


def star_catalog_data_folder(environ: Mapping[str, str] | None = None) -> Path | None:
    """Find the star catalog's data folder.

    Parameters
    ----------
    environ : `~collections.abc.Mapping`, optional
        The environment. Default: the process environment.

    Returns
    -------
    folder : `pathlib.Path` or `None`
        The folder ``GSCDAT`` names, if it exists, else the first of
        `STAR_CATALOG_DATA_FOLDERS` that exists, else `None`.
    """
    environment = os.environ if environ is None else environ
    named = environment.get(STAR_CATALOG_DATA_VARIABLE)
    if named and Path(named).is_dir():
        return Path(named)
    return next((folder for folder in STAR_CATALOG_DATA_FOLDERS if folder.is_dir()), None)


def star_catalog_installed() -> bool:
    """Say whether the star catalog program is on the PATH.

    Returns
    -------
    installed : `bool`
        `True` when ``gsc`` is found. Its package pulls in the data, so the
        program is the test.
    """
    return shutil.which(STAR_CATALOG_PROGRAM) is not None


def launch_notes() -> list[str]:
    """List what the simulator lacks that does not stop it.

    Returns
    -------
    notes : `list` [`str`]
        One text per missing optional part, empty when all is present. Today
        that is the star catalog: without it the simulated frames hold no
        stars.
    """
    return [] if star_catalog_installed() else [STAR_CATALOG_MISSING_NOTE]


def indiserver_environment(root: Path, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Give the environment of the world's INDI server and its drivers.

    ``HOME`` is the world's own folder, so the simulators never read or write
    the user's ``~/.indi``. ``GSCDAT`` names the star catalog's data when it
    is installed.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder.
    environ : `~collections.abc.Mapping`, optional
        The base environment. Default: the process environment.

    Returns
    -------
    variables : `dict` [`str`, `str`]
        Only the variables to add or replace.
    """
    variables = {"HOME": str(indi_home(root))}
    data = star_catalog_data_folder(environ)
    if data is not None and star_catalog_installed():
        variables[STAR_CATALOG_DATA_VARIABLE] = str(data)
    return variables


def indiserver_command(root: Path, port: int) -> list[str]:
    """Give the command that starts the world's INDI server.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder.
    port : `int`
        The TCP port the server listens on.

    Returns
    -------
    command : `list` [`str`]
        ``indiserver`` with the port, the world's own socket and the drivers.
    """
    return ["indiserver", "-p", str(port), "-u", indi_socket_name(root), *INDI_DRIVERS]


def _make_folders(root: Path) -> None:
    """Make the folders every world needs and link the ``ssh`` stand-in.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder.
    """
    (standin_home(root) / "Pictures").mkdir(parents=True, exist_ok=True)
    (indi_home(root) / ".indi").mkdir(parents=True, exist_ok=True)
    bin_directory = root / "standin" / "bin"
    bin_directory.mkdir(parents=True, exist_ok=True)
    link = bin_directory / "ssh"
    link.unlink(missing_ok=True)
    link.symlink_to(STANDIN_SSH_SCRIPT)
    (root / "library" / "frames").mkdir(parents=True, exist_ok=True)
    (root / "wayfinding").mkdir(parents=True, exist_ok=True)


def prepare_offline_world(root: Path) -> Path:
    """Make a world with no telescope, and check its settings.

    The settings name this computer as the telescope host with a port that
    nothing listens on, turn automatic downloads off and place both
    libraries inside `root`. The file is always written again from scratch.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder.

    Returns
    -------
    config_path : `pathlib.Path`
        The settings file.

    Raises
    ------
    SimulationUnavailableError
        If the guard refuses the settings (for example, `root` is inside a
        real library folder).
    """
    try:
        observatory_guard.check_root(root)
    except observatory_guard.GuardError as error:
        raise SimulationUnavailableError(f"Refusing to use the offline world: {error}") from error
    _make_folders(root)
    config_path = root / "astrometrics.config.toml"
    config_path.write_text(offline_config_text(root), encoding="utf-8")
    try:
        observatory_guard.check_offline_settings_text(config_path.read_text(encoding="utf-8"), root)
    except observatory_guard.GuardError as error:
        raise SimulationUnavailableError(f"Refusing to use the offline world: {error}") from error
    return config_path


def _set_key(text: str, section: str, key: str, value: str) -> str:
    """Set one key of one section in settings text, adding either if missing.

    Parameters
    ----------
    text : `str`
        The settings in TOML.
    section : `str`
        The section name, written in quotes in the file.
    key : `str`
        The key.
    value : `str`
        The value as TOML text, including its quotes.

    Returns
    -------
    changed : `str`
        The settings with the key set. Other lines are untouched.
    """
    line = f"{key} = {value}"
    header = re.search(rf'^\[\s*"{re.escape(section)}"\s*\][ \t]*$', text, re.MULTILINE)
    if header is None:
        return text.rstrip("\n") + f'\n\n["{section}"]\n{line}\n'
    following = re.search(r"^\[", text[header.end() :], re.MULTILINE)
    end = header.end() + following.start() if following else len(text)
    block = text[header.end() : end]
    existing = re.search(rf"^{re.escape(key)}[ \t]*=.*$", block, re.MULTILINE)
    if existing:
        block = block[: existing.start()] + line + block[existing.end() :]
    else:
        block = f"\n{line}{block}"
    return text[: header.end()] + block + text[end:]


def with_camera_alias(text: str, alias: str) -> str:
    """Add a name to the primary camera profile's aliases, in settings text.

    The app files a frame under the camera whose profile name or alias equals
    the frame's ``INSTRUME`` header. The INDI CCD simulator writes its own
    device name there, so the world's copy of the primary camera's profile
    lists that name as one more alias. The real settings are not touched.

    Parameters
    ----------
    text : `str`
        The world's settings in TOML.
    alias : `str`
        The name to add.

    Returns
    -------
    changed : `str`
        The settings with the alias added, or unchanged when the settings
        name no primary camera, the camera has no profile section or the
        alias is already there.
    """
    try:
        settings = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return text
    cameras = settings.get("Observatory.Camera", {})
    primary = str(cameras.get("default_primary_camera", "")).strip()
    section = f"Observatory.Camera.{primary}"
    if not primary or section not in settings:
        return text
    aliases = [
        name.strip() for name in str(settings[section].get("name_aliases", "")).split(",") if name.strip()
    ]
    if alias in aliases:
        return text
    return _set_key(text, section, "name_aliases", json.dumps(", ".join([*aliases, alias])))


def with_stand_in_endpoints(text: str, indi_port: int, phd2_port: int, pictures: Path) -> str:
    """Aim settings text at the stand-ins and keep every other key.

    Parameters
    ----------
    text : `str`
        The world's settings in TOML. A missing section or key is added.
    indi_port, phd2_port : `int`
        The ports of the stand-in INDI server and PHD2.
    pictures : `pathlib.Path`
        The stand-in telescope computer's picture folder.

    Returns
    -------
    changed : `str`
        The settings with only the endpoint keys set (see
        `simulator.settings.endpoint_keys`), and the made-up focal length and
        ratio added when the settings have none.
    """
    try:
        telescope = tomllib.loads(text).get("Observatory.Telescope", {})
    except tomllib.TOMLDecodeError:
        telescope = {}
    for key, value in FALLBACK_TELESCOPE_KEYS:
        if key not in telescope:
            text = _set_key(text, "Observatory.Telescope", key, json.dumps(value))
    for section, key, value in endpoint_keys(indi_port, phd2_port, pictures):
        text = _set_key(text, section, key, json.dumps(value))
    return text


class SimulatedObservatory:
    """The stand-in observatory: processes, folders and settings.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder. The settings file, the stand-in
        telescope computer and the replay files are inside it.
    kind : `SimulationKind`
        What the simulator plays.
    indi_port, phd2_port : `int`
        The local ports for the INDI server and the stand-in PHD2.
    replay_night : `str`, optional
        The night to play (``YYYY-MM-DD``) for the replay kind.
    exposure_seconds : `float`, optional
        Seconds per simulated exposure for the simulated-sky kind.
    replay_options : `~collections.abc.Mapping`, optional
        Keyword arguments for `night_replay.build_plan` (``speed``,
        ``window_seconds``, ``preload_frames``) in the replay kind.
    replay_staged : `bool`, optional
        `True` when the night was already staged in ``<root>/replay``, so
        `start` uses it as it is.
    auto_start_replay : `bool`, optional
        `True` (default) to start the replay a few seconds after the
        simulators connect. `False` leaves the start to whoever writes the
        ``start`` file in ``<root>/replay``.
    copy_library : `bool`, optional
        `True` for the app: `prepare` copies the real databases and mounts
        an overlay of the real frames and stacks (see `library_copy`), and
        `stop` removes them. The real library is only read.
    existing_world : `bool`, optional
        `True` when the root folder was already built, for example by
        `library_copy.prepare_library_copy` in another program. `start` then
        only runs the safety checks: it does not copy the library, unmount
        the overlay or write the settings file again.
    real_library : `object`, optional
        The real folders for the copy (from
        `simulator.real_folders.real_folders`). Default: found when
        `prepare` runs.
    guider : `GuiderKind`, optional
        Which program plays PHD2 (default: the stand-in, which keeps the TPM
        runs repeatable). The real PHD2 is used only in the simulated-sky
        kind. With it, `phd2_port` must be the event port of a PHD2 instance
        (`phd2_launch.event_port`); the replay is not played, because PHD2
        writes its own genuine guide log.
    controller_settings : `~collections.abc.Mapping` [`str`, `float`], optional
        Guide controller settings for the real PHD2's profile (see
        `phd2_launch.CONTROLLER_SETTING_KEYS`). Default: PHD2's own defaults.
    capture_by_app : `bool`, optional
        `True` for an interactive session (the launcher): the app starts the
        exposures (Start Capture), the exposure driver only files the frames
        (``--capture app``), and `start` empties the stand-in picture and
        staging folders first, so the session starts like a new night.
        `False` (default, the TPM harness) keeps the driver's own loop.
    """

    def __init__(
        self,
        root: Path,
        kind: SimulationKind,
        indi_port: int,
        phd2_port: int,
        replay_night: str | None = None,
        exposure_seconds: float = 60.0,
        replay_options: Mapping | None = None,
        replay_staged: bool = False,
        auto_start_replay: bool = True,
        copy_library: bool = False,
        real_library: Any = None,
        existing_world: bool = False,
        guider: GuiderKind = GuiderKind.MOCK,
        controller_settings: Mapping[str, float] | None = None,
        backend_port: int | None = None,
        fitted_settings_path: Path | None = None,
        capture_by_app: bool = False,
    ) -> None:
        """Keep the settings; nothing runs yet."""
        self.root = root
        self.capture_by_app = capture_by_app
        self.kind = kind
        self.indi_port = indi_port
        self.phd2_port = phd2_port
        self.replay_night = replay_night
        self.exposure_seconds = exposure_seconds
        self.replay_options = dict(replay_options or {})
        self.replay_staged = replay_staged
        self.auto_start_replay = auto_start_replay
        self.copy_library = copy_library
        self.real_library = real_library
        self.existing_world = existing_world
        self.guider = GuiderKind(guider)
        self.controller_settings = dict(controller_settings) if controller_settings else None
        self.backend_port = backend_port
        self.fitted_settings_path = fitted_settings_path
        #: The PHD2 that runs, once started (`None` while the stand-in plays).
        self.phd2: phd2_launch.Phd2Instance | None = None
        #: Statuses about parts that are missing or replaced; not errors.
        self.notes: list[str] = []
        self.sky_target_name = SIMULATED_TARGET_NAME
        self.copy_report: dict = {}
        self.standin_home = standin_home(root)
        self.config_path = root / "astrometrics.config.toml"
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()
        self._start_error: str | None = None

    # ------------------------------------------------------------------
    # Folders and settings (no process starts here)
    # ------------------------------------------------------------------

    @property
    def bin_directory(self) -> Path:
        """The folder whose ``ssh`` is the stand-in; put it first on PATH."""
        return self.root / "standin" / "bin"

    def environment(self) -> dict[str, str]:
        """Give the environment variables the app's own process needs.

        Returns
        -------
        variables : `dict` [`str`, `str`]
            ``PATH`` with the ssh stand-in first, and
            ``ASTROMETRICS_STANDIN_HOME``.
        """
        return {
            "PATH": f"{self.bin_directory}{os.pathsep}{os.environ.get('PATH', '')}",
            "ASTROMETRICS_STANDIN_HOME": str(self.standin_home),
        }

    def prepare(self) -> None:
        """Make the folders and the settings file, and run the safety checks.

        The settings file is written whole if it is missing or unreadable.
        If it exists (the user may have changed other settings in it), the
        keys that name the stand-ins and place the libraries are set again,
        every time: the hosts, ports, picture folder, and the image library,
        frames, stacks and wayfinding library folders. The guard then
        refuses any setting that points beyond this computer.

        Raises
        ------
        SimulationUnavailableError
            If a needed program is missing, the platform cannot run the
            simulator, or the guard refuses the settings.
        """
        if self.existing_world:
            self._check_existing_world()
            return
        reason = unavailable_reason(self.copy_library)
        if reason:
            raise SimulationUnavailableError(reason)
        try:
            observatory_guard.check_root(self.root)
        except observatory_guard.GuardError as error:
            raise SimulationUnavailableError(f"Refusing to start the simulator: {error}") from error
        _make_folders(self.root)
        if self.copy_library:
            self.copy_report = library_copy.prepare_library_copy(self.root, real=self.real_library)
            self.config_path.write_text(self._copy_settings_text(), encoding="utf-8")
        else:
            self._write_settings()
        try:
            observatory_guard.check_settings(
                self.config_path, self.indi_port, self.phd2_port, self.standin_home, self.root
            )
        except observatory_guard.GuardError as error:
            raise SimulationUnavailableError(f"Refusing to start the simulator: {error}") from error

    def _check_existing_world(self) -> None:
        """Run the safety checks on a world that was built before.

        Raises
        ------
        SimulationUnavailableError
            If the root folder or the settings file fails the guard.
        """
        try:
            observatory_guard.check_root(self.root)
            observatory_guard.check_settings(
                self.config_path, self.indi_port, self.phd2_port, self.standin_home, self.root
            )
        except observatory_guard.GuardError as error:
            raise SimulationUnavailableError(f"Refusing to start the simulator: {error}") from error

    def _copy_settings_text(self) -> str:
        """Build the settings of a world with a copy of the real library.

        Returns
        -------
        text : `str`
            The simulator's launch and library sections, then the real
            settings without the sections the simulator replaced. The real
            ``Observatory.Telescope``, ``Observatory.RemoteTransfer`` and
            ``Observatory.Guiding`` sections stay: only their endpoint keys
            change (`with_stand_in_endpoints`). The primary camera's profile
            gains the INDI simulator's instrument name as an alias. Without a
            real settings file, the made-up equipment is used.
        """
        from simulator.real_folders import real_folders

        real = self.real_library or real_folders()
        mine = SIMULATED_LAUNCH_SETTINGS + library_settings_text(
            self.root,
            Path(self.copy_report.get("frames_path", self.root / "library" / "frames")),
            Path(self.copy_report.get("stacks_path", self.root / "library" / "frames")),
        )
        try:
            real_text = real.config_file.read_text(encoding="utf-8")
            tomllib.loads(real_text)
        except OSError, tomllib.TOMLDecodeError:
            return simulator_config_text(self.root, self.indi_port, self.phd2_port)
        text = library_copy.world_settings_text(real_text, mine)
        text = with_stand_in_endpoints(text, self.indi_port, self.phd2_port, self.standin_home / "Pictures")
        return with_camera_alias(text, SIMULATOR_INSTRUMENT_NAME)

    def _write_settings(self) -> None:
        """Write the settings file, or set the stand-in and library keys."""
        fresh = simulator_config_text(self.root, self.indi_port, self.phd2_port)
        if not self.config_path.exists():
            self.config_path.write_text(fresh, encoding="utf-8")
            return
        text = self.config_path.read_text(encoding="utf-8")
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            self.config_path.write_text(fresh, encoding="utf-8")
            return
        frames = self.root / "library" / "frames"
        wanted = (
            ("Image Library", "path", self.root / "library"),
            ("Image Library", "frames_path", frames),
            ("Image Library", "stacks_path", frames),
            ("Wayfinding Library", "path", self.root / "wayfinding"),
        )
        for section, key, value in wanted:
            text = _set_key(text, section, key, f'"{value}"')
        text = with_stand_in_endpoints(text, self.indi_port, self.phd2_port, self.standin_home / "Pictures")
        text = with_camera_alias(text, SIMULATOR_INSTRUMENT_NAME)
        self.config_path.write_text(with_launch_section(text, SIMULATED_LAUNCH_SETTINGS), encoding="utf-8")

    # ------------------------------------------------------------------
    # Processes
    # ------------------------------------------------------------------

    @property
    def ready_marker(self) -> Path:
        """The file that says the driver has connected everything."""
        if self.kind is SimulationKind.REPLAY:
            return self.root / "replay" / "ready"
        return self.root / "logs" / "driver_ready"

    def is_ready(self) -> bool:
        """Say whether the whole telescope is up.

        Returns
        -------
        ready : `bool`
            `True` when the INDI server and the stand-in PHD2 listen and the
            driver has connected the simulators.
        """
        return (
            self.is_running()
            and is_listening(self.indi_port)
            and is_listening(self.phd2_port)
            and self.ready_marker.exists()
        )

    def wait_ready(self, timeout: float = READY_WAIT_SECONDS) -> None:
        """Wait until the whole telescope is up.

        Parameters
        ----------
        timeout : `float`, optional
            Seconds to wait.

        Raises
        ------
        SimulationUnavailableError
            If a stand-in ends, or the telescope is not up in time.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_ready():
                return
            with self._lock:
                dead = [name for name, process in self._processes.items() if process.poll() is not None]
            if dead:
                raise SimulationUnavailableError(f"The simulator process {', '.join(dead)} ended early.")
            time.sleep(0.5)
        raise SimulationUnavailableError(f"The simulated telescope was not ready after {timeout:.0f} s.")

    def _launch(
        self,
        name: str,
        command: list[str],
        log_name: str | None = None,
        extra_environment: Mapping[str, str] | None = None,
        unset_environment: Iterable[str] = (),
    ) -> None:
        """Start one stand-in under the supervisor.

        Parameters
        ----------
        name : `str`
            A short name for the process table.
        command : `list` [`str`]
            The command and arguments.
        log_name : `str`, optional
            The file under ``<root>/logs`` that gets its output.
        extra_environment : `~collections.abc.Mapping`, optional
            Variables for this process only (the INDI server's own ``HOME``).
        unset_environment : `~collections.abc.Iterable` [`str`], optional
            Variables this process must not inherit.
        """
        logs = self.root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_file = (logs / (log_name or f"{name}.log")).open("ab")
        repository = Path(__file__).resolve().parents[1]
        environment = {
            **os.environ,
            "PYTHONPATH": f"{repository}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        }
        environment.update(self.environment())
        environment.update(extra_environment or {})
        for variable in unset_environment:
            environment.pop(variable, None)
        process = subprocess.Popen(
            [sys.executable, str(SUPERVISOR_SCRIPT), "--", *command],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=environment,
            cwd=repository,
        )
        log_file.close()
        self._processes[name] = process

    def _wait_listening(self, ports: tuple[int, ...]) -> None:
        """Wait until every port has a listener.

        Raises
        ------
        SimulationUnavailableError
            If a port stays silent past `LISTEN_WAIT_SECONDS`.
        """
        deadline = time.time() + LISTEN_WAIT_SECONDS
        while time.time() < deadline:
            if all(is_listening(port) for port in ports):
                return
            time.sleep(0.25)
        raise SimulationUnavailableError(f"The simulator did not start listening on ports {ports}.")

    def start(self) -> None:
        """Start the INDI server, the stand-in PHD2 and the driver.

        Never called in real-telescope mode. Safe to call once; call `stop`
        to end everything.

        A failure of `prepare` or of a start-up step is raised again after
        whatever started has been stopped.
        """
        with self._lock:
            if self._processes:
                return
            try:
                self.prepare()
                self._start_processes()
            except Exception as error:
                self._start_error = str(error)
                self._stop_locked()
                self.remove_library_copy()
                raise

    def _start_processes(self) -> None:
        """Start the processes for the kind."""
        python = sys.executable
        stats = self.root / "logs"
        stats.mkdir(parents=True, exist_ok=True)
        # The app lists the picture folder before the first frame arrives, and
        # the INDI server's home must exist (a world built by another program
        # does not have them).
        (self.standin_home / "Pictures").mkdir(parents=True, exist_ok=True)
        (indi_home(self.root) / ".indi").mkdir(parents=True, exist_ok=True)
        if self.kind is not SimulationKind.REPLAY:
            self.ready_marker.unlink(missing_ok=True)
        if self.capture_by_app:
            self.start_fresh_night()
        if self.kind is SimulationKind.SIMULATED_SKY:
            # The app files frames under the library target nearest the mount.
            self.sky_target_name = (
                library_copy.nearest_target_name(
                    self.root, SIMULATED_TARGET_RA_HOURS, SIMULATED_TARGET_DEC_DEGREES
                )
                or SIMULATED_TARGET_NAME
            )
        self._launch(
            "indiserver",
            indiserver_command(self.root, self.indi_port),
            "indiserver.log",
            indiserver_environment(self.root),
        )
        module = "simulator"
        replay_directory = self.root / "replay"
        if self.kind is SimulationKind.REPLAY:
            self._stage_replay()
        self._wait_listening((self.indi_port,))
        if self.kind is SimulationKind.SIMULATED_SKY and self.guider is GuiderKind.PHD2:
            reason = self._start_real_phd2()
            if reason is not None:
                self.notes.append(f"{PHD2_FALLBACK_NOTE} ({reason})")
                logger.warning("The real PHD2 is not used: %s. The stand-in guider plays PHD2.", reason)
        if self.phd2 is None:
            phd2_command = [python, "-m", f"{module}.mock_phd2", "--port", str(self.phd2_port)]
            if self.kind is SimulationKind.REPLAY:
                phd2_command += [
                    "--plan", str(replay_directory / "plan.json"),
                    "--start-file", str(replay_directory / "start"),
                    "--stats", str(stats / "phd2_stats.json"),
                ]  # fmt: skip
            else:
                phd2_command += ["--log", str(GUIDE_LOG_FIXTURE), "--stats", str(stats / "phd2_stats.json")]
            self._launch("phd2", phd2_command, "mock_phd2.log")
        self._wait_listening((self.indi_port, self.phd2_port))
        if self.kind is SimulationKind.REPLAY:
            self._launch(
                "driver",
                [
                    python, "-m", f"{module}.replay_driver",
                    "--indi-port", str(self.indi_port),
                    "--standin", str(self.standin_home),
                    "--replay", str(replay_directory),
                    "--stats", str(stats / "driver_stats.json"),
                ],
                "driver.log",
            )  # fmt: skip
            if self.auto_start_replay:
                threading.Thread(target=self._signal_replay_start, name="replay-start", daemon=True).start()
        else:
            self._launch(
                "driver",
                [
                    python, "-m", f"{module}.exposure_driver",
                    "--indi-port", str(self.indi_port),
                    "--standin", str(self.standin_home),
                    "--stats", str(stats / "driver_stats.json"),
                    "--exposure-seconds", str(self.exposure_seconds),
                    "--target", self.sky_target_name,
                    "--ra-hours", str(SIMULATED_TARGET_RA_HOURS),
                    "--dec-degrees", str(SIMULATED_TARGET_DEC_DEGREES),
                    "--config", str(self.config_path),
                    "--capture", "app" if self.capture_by_app else "driver",
                    *self._driver_optional_arguments(),
                ],
                "driver.log",
            )  # fmt: skip

    def start_fresh_night(self) -> None:
        """Empty the stand-in picture folder and the camera's staging folder.

        An interactive session starts like a new night. The library copy is
        rebuilt at every start, so frames left by an earlier session would
        be downloaded and measured again for nothing. Only the contents go;
        the folders stay. A folder that resolves outside the world is
        refused, and so is a link inside them that points outside (a link is
        removed, never followed). The TPM harness does not call this.

        Raises
        ------
        SimulationUnavailableError
            If a folder is not inside the world's root.
        """
        world = self.root.resolve()
        for folder in (self.standin_home / "Pictures", self.standin_home / "camera_staging"):
            if not folder.is_dir():
                continue
            if not folder.resolve().is_relative_to(world) or folder.resolve() == world:
                raise SimulationUnavailableError(
                    f"Refusing to empty {folder}: it is outside the world {world}."
                )
            for entry in folder.iterdir():
                if entry.is_symlink() or entry.is_file():
                    entry.unlink()
                else:
                    shutil.rmtree(entry)
        (self.standin_home / "Pictures").mkdir(parents=True, exist_ok=True)

    def _driver_optional_arguments(self) -> list[str]:
        """Give the exposure driver's arguments that are not always there.

        Returns
        -------
        arguments : `list` [`str`]
            ``--guide-log`` with the stand-in guider, ``--backend-port`` when
            the launcher gave the backend's port (real frames), and
            ``--fitted-settings`` when the fit wrote its file.
        """
        arguments: list[str] = []
        guide_log = self._driver_guide_log()
        if guide_log is not None:
            arguments += ["--guide-log", str(guide_log)]
        if self.backend_port is not None:
            arguments += ["--backend-port", str(self.backend_port)]
        if self.fitted_settings_path is not None:
            arguments += ["--fitted-settings", str(self.fitted_settings_path)]
        return arguments

    def _driver_guide_log(self) -> Path | None:
        """Give the guide log the exposure driver replays into the stand-in.

        Returns
        -------
        path : `pathlib.Path` or `None`
            The sample guide log with the stand-in guider. `None` with the
            real PHD2: PHD2 writes a genuine log, and a replay would count the
            guiding twice, so the driver writes no guide log at all.
        """
        return GUIDE_LOG_FIXTURE if self.phd2 is None else None

    def _guide_optics(self) -> tuple[float, float]:
        """Read the guide scope and guide camera from the world's settings.

        Returns
        -------
        focal_length_mm, pixel_size_um : `float`
            The active guide scope's focal length and the secondary camera's
            pixel size. A value the settings lack is PHD2 launch's default.
        """
        focal_length = phd2_launch.DEFAULT_GUIDE_FOCAL_LENGTH_MM
        pixel_size = phd2_launch.DEFAULT_GUIDE_PIXEL_SIZE_UM
        try:
            settings = tomllib.loads(self.config_path.read_text(encoding="utf-8"))
        except OSError, tomllib.TOMLDecodeError:
            return focal_length, pixel_size
        scopes = settings.get("Observatory.GuideScope", {})
        scope = settings.get(f"Observatory.GuideScope.{scopes.get('active_guide_scope', '')}", {})
        camera_name = settings.get("Observatory.Camera", {}).get("default_secondary_camera", "")
        camera = settings.get(f"Observatory.Camera.{camera_name}", {})
        try:
            focal_length = float(scope.get("focal_length_mm", focal_length))
            pixel_size = float(camera.get("pixel_size_\u03bcm", pixel_size))
        except TypeError, ValueError:
            pass
        return focal_length, pixel_size

    def _guide_rate_arcsec_per_sec(self) -> float:
        """Give the right ascension guide rate of the simulated mount.

        Returns
        -------
        rate : `float`
            The fitted ``GUIDE_RATE_WE`` (a fraction of 15 arcseconds per
            second) times 15 when the fit wrote its file, else the mount
            simulator's default of 7.5.
        """
        fraction = None
        if self.fitted_settings_path is not None:
            from simulator.fit.results import fitted_guide_rate_fraction

            fraction = fitted_guide_rate_fraction(self.fitted_settings_path)
        return float((fraction or {}).get("GUIDE_RATE_WE", 0.5)) * phd2_launch.SIDEREAL_ARCSEC_PER_SEC

    def _start_real_phd2(self) -> str | None:
        """Write PHD2's profile and start the real PHD2 in the world.

        PHD2 runs with the stand-in telescope computer's home as ``HOME``, an
        instance number that matches `phd2_port`, and a profile that connects
        it to the world's INDI server (see `phd2_launch`).

        Returns
        -------
        reason : `str` or `None`
            `None` when PHD2 listens on its port. Otherwise the reason it is
            not used, in a few words (the caller falls back to the stand-in).
        """
        missing = phd2_launch.phd2_missing_reason(os.environ)
        if missing is not None:
            return missing
        executable = phd2_launch.find_phd2(os.environ)
        instance = self.phd2_port - phd2_launch.EVENT_PORT_BASE
        if executable is None or instance < phd2_launch.FIRST_INSTANCE:
            return f"port {self.phd2_port} is not a PHD2 instance port"
        if not phd2_launch.choose_instance(candidates=[instance]) == instance:
            return f"PHD2's ports for instance {instance} are in use"
        focal_length, pixel_size = self._guide_optics()
        try:
            record = phd2_launch.write_profile(
                self.standin_home,
                self.root,
                instance,
                self.indi_port,
                self.controller_settings,
                focal_length,
                pixel_size,
                self._guide_rate_arcsec_per_sec(),
                SIMULATED_TARGET_DEC_DEGREES,
            )
        except ValueError as error:
            return str(error)
        self._launch(
            "phd2",
            phd2_launch.phd2_command(executable, instance, os.environ),
            "phd2.log",
            phd2_launch.phd2_environment(self.standin_home, os.environ),
            phd2_launch.UNSET_VARIABLES,
        )
        deadline = time.time() + PHD2_LISTEN_WAIT_SECONDS
        while time.time() < deadline:
            if is_listening(record.event_port):
                self.phd2 = record
                return None
            if self._processes["phd2"].poll() is not None:
                break
            time.sleep(0.5)
        process = self._processes.pop("phd2")
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8.0)
            except subprocess.TimeoutExpired:
                process.kill()
        return "PHD2 did not open its port (see logs/phd2.log)"

    def _stage_replay(self) -> None:
        """Copy the chosen night's files into the simulator root.

        The real library and logs are only read.

        Raises
        ------
        SimulationUnavailableError
            If no night is chosen or the night cannot be planned.
        """
        from simulator import night_replay

        replay_directory = self.root / "replay"
        if self.replay_staged:
            if not (replay_directory / "plan.json").exists():
                raise SimulationUnavailableError(f"No staged replay in {replay_directory}.")
            return
        if not self.replay_night:
            raise SimulationUnavailableError("Replay needs a night to play.")
        shutil.rmtree(replay_directory, ignore_errors=True)
        try:
            plan = night_replay.build_plan(self.replay_night, **self.replay_options)
            night_replay.stage(self.root, plan)
        except (FileNotFoundError, ValueError, RuntimeError) as error:
            raise SimulationUnavailableError(f"Cannot replay {self.replay_night}: {error}") from error

    def _signal_replay_start(self) -> None:
        """Write the replay's start time once the replay driver is ready."""
        replay_directory = self.root / "replay"
        deadline = time.time() + REPLAY_READY_WAIT_SECONDS
        while time.time() < deadline:
            if (replay_directory / "ready").exists():
                (replay_directory / "start").write_text(
                    str(time.time() + REPLAY_LEAD_SECONDS), encoding="utf-8"
                )
                return
            time.sleep(0.5)
        logger.error("The replay driver did not connect the simulators in time; the replay will not start.")

    def stop(self) -> None:
        """End every stand-in process. Safe to call more than once."""
        with self._lock:
            self._stop_locked()

    def remove_library_copy(self) -> bool:
        """Unmount the overlays and delete the copies (library-copy world).

        Call it after everything that uses the library has stopped: the
        databases are deleted.

        Returns
        -------
        clean : `bool`
            `True` when nothing is left (also when there was no copy).
        """
        return library_copy.teardown(self.root) if self.copy_library else True

    def _stop_locked(self) -> None:
        """End the processes (the caller holds the lock)."""
        for process in self._processes.values():
            if process.poll() is None:
                process.terminate()
        deadline = time.time() + 8.0
        for process in self._processes.values():
            try:
                process.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                process.kill()
        self._processes.clear()

    def is_running(self) -> bool:
        """Say whether the INDI server and the stand-in PHD2 are still alive.

        Returns
        -------
        running : `bool`
            `True` when both processes exist and have not ended.
        """
        with self._lock:
            return all(
                name in self._processes and self._processes[name].poll() is None
                for name in ("indiserver", "phd2")
            )

    def pointed_targets(self) -> list[tuple[str, float, float]]:
        """List the targets the simulated mount points at.

        Returns
        -------
        targets : `list` [`tuple`]
            ``(name, right ascension in hours, declination in degrees)``.
            The simulated sky points at the position of M 13, under the
            name of the library target nearest it (see
            `library_copy.nearest_target_name`; M 13 when the library has
            none). A replay lists the targets of its staged frames, so call
            this after `start`.
        """
        if self.kind is SimulationKind.SIMULATED_SKY:
            return [(self.sky_target_name, SIMULATED_TARGET_RA_HOURS, SIMULATED_TARGET_DEC_DEGREES)]
        try:
            plan = json.loads((self.root / "replay" / "plan.json").read_text(encoding="utf-8"))
        except OSError, ValueError:
            return []
        found: dict[str, tuple[float, float]] = {}
        for frame in plan.get("preload_frames", []) + plan.get("window_frames", []):
            found.setdefault(frame["target_id"], (frame["ra_hours"], frame["dec_degrees"]))
        return [(name, ra, dec) for name, (ra, dec) in found.items()]

    def status(self) -> dict:
        """Describe the simulator for the status display.

        Returns
        -------
        status : `dict`
            ``kind``, ``replay_night``, ``running``, the ports, ``guider``
            (``phd2`` or ``mock``), ``notes`` and ``error``.
        """
        return {
            "kind": self.kind.value,
            "replay_night": self.replay_night,
            "running": self.is_running(),
            "indi_port": self.indi_port,
            "phd2_port": self.phd2_port,
            "guider": GuiderKind.PHD2.value if self.phd2 is not None else GuiderKind.MOCK.value,
            "notes": list(self.notes),
            "error": self._start_error,
            "library_copy": library_copy.read_report(self.root) if self.copy_library else {},
        }


def _write_ports_file(path: Path, observatory: SimulatedObservatory) -> None:
    """Write the ports file that tells a waiting program the stand-ins listen.

    Parameters
    ----------
    path : `pathlib.Path`
        The file to write, replaced in one step.
    observatory : `SimulatedObservatory`
        The running observatory.
    """
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({
            "indi_port": observatory.indi_port,
            "phd2_port": observatory.phd2_port,
            "pid": os.getpid(),
        }),
        encoding="utf-8",
    )
    temporary.replace(path)


def run_start(arguments: argparse.Namespace) -> int:
    """Run the ``start`` command: start the stand-ins and wait to be stopped.

    Parameters
    ----------
    arguments : `argparse.Namespace`
        The parsed command line.

    Returns
    -------
    status : `int`
        0 after a requested stop, 1 if the simulator did not start or a
        stand-in ended on its own.
    """
    root = arguments.root.resolve()
    guider = GuiderKind(arguments.guider)
    phd2_port = arguments.phd2_port
    if not phd2_port:
        instance = phd2_launch.choose_instance() if guider is GuiderKind.PHD2 else None
        phd2_port = phd2_launch.event_port(instance) if instance is not None else choose_free_port()
    observatory = SimulatedObservatory(
        root,
        SimulationKind(arguments.kind),
        arguments.indi_port or choose_free_port(),
        phd2_port,
        arguments.replay_night,
        arguments.exposure_seconds,
        replay_staged=arguments.replay_staged,
        auto_start_replay=not arguments.manual_replay_start,
        existing_world=arguments.existing_world,
        guider=guider,
    )
    stopping = threading.Event()
    parent_pid = os.getppid()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    try:
        observatory.start()
    except (SimulationUnavailableError, OSError) as error:
        print(f"The simulated observatory did not start: {error}", file=sys.stderr)
        return 1
    pid_file = root / PROCESS_ID_FILE
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    if arguments.ports_file is not None:
        _write_ports_file(arguments.ports_file, observatory)
    status = 0
    try:
        while not stopping.wait(0.5):
            if os.getppid() != parent_pid:
                break
            if not observatory.is_running():
                print("A stand-in process ended on its own.", file=sys.stderr)
                status = 1
                break
    finally:
        observatory.stop()
        pid_file.unlink(missing_ok=True)
    return status


def run_stop(arguments: argparse.Namespace) -> int:
    """Run the ``stop`` command: end the ``start`` command of a root folder.

    Parameters
    ----------
    arguments : `argparse.Namespace`
        The parsed command line.

    Returns
    -------
    status : `int`
        0 when nothing runs or the command ended, 1 if it did not end in
        time.
    """
    pid_file = arguments.root.resolve() / PROCESS_ID_FILE
    try:
        pid = int(pid_file.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        return 0
    deadline = time.time() + STOP_WAIT_SECONDS
    while time.time() < deadline:
        if not pid_file.exists():
            return 0
        time.sleep(0.2)
    print(f"The start command (process {pid}) did not end in time.", file=sys.stderr)
    return 1


def main() -> int:
    """Run one command from the command line.

    Returns
    -------
    status : `int`
        See `run_start` and `run_stop`.
    """
    parser = argparse.ArgumentParser(description="Start or stop a simulated observatory.")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="Start the stand-ins and stay until stopped.")
    start.add_argument("--kind", choices=[kind.value for kind in SimulationKind], required=True)
    start.add_argument("--root", type=Path, required=True)
    start.add_argument("--indi-port", type=int, default=0, help="Default: a free port.")
    start.add_argument("--phd2-port", type=int, default=0, help="Default: a free port.")
    start.add_argument("--ports-file", type=Path, default=None, help="Written when the stand-ins listen.")
    start.add_argument("--replay-night", default=None)
    start.add_argument(
        "--replay-staged", action="store_true", help="Use the night already staged in the root."
    )
    start.add_argument(
        "--manual-replay-start", action="store_true", help="Wait for the start file instead of starting."
    )
    start.add_argument("--exposure-seconds", type=float, default=60.0)
    start.add_argument(
        "--guider",
        choices=[kind.value for kind in GuiderKind],
        default=GuiderKind.MOCK.value,
        help="Who plays PHD2: the stand-in (default, repeatable) or the real PHD2.",
    )
    start.add_argument(
        "--existing-world",
        action="store_true",
        help="The root was already built (library copy and settings): only run the safety checks.",
    )
    stop = commands.add_parser("stop", help="End the start command of a root folder.")
    stop.add_argument("--root", type=Path, required=True)
    arguments = parser.parse_args()
    return run_start(arguments) if arguments.command == "start" else run_stop(arguments)


if __name__ == "__main__":
    sys.exit(main())
