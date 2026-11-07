"""Purpose: Choose the observatory source before anything else loads.

The source is the real telescope or a simulator.

Description: The "Observatory source" setting has two values: the real
telescope, or a simulated sky. The choice is kept
in a small file next to the settings file (``observatory_source.json`` in the
real settings folder). The real settings file itself is never changed.

For a simulator source the backend needs a whole second world: its own data,
cache, state and settings folders, so simulated frames, nights and databases
never touch the real library. The world holds a copy of the real databases and
an overlay that shows the real frames and stacks read only (every write goes to
a temporary folder, see `simulator.library_copy`). It is removed
when the simulator stops, and a start in real mode removes what a crash
left. `apply_launch_environment` builds that world by pointing the
``XDG_*`` variables and ``ASTROMETRICS_CONFIG`` at a folder named
``astrometrics-simulator`` beside the real data folder. It must run before the
settings are read, so `backend.main_backend` calls it first. Changing the
source later writes the choice file and starts the backend again (see
`restart_backend`), which makes the next start use the other world.

Restart contract. When a launcher runs the backend as its child, it sets
``ASTROMETRICS_LAUNCHER`` (`LAUNCHER_VARIABLE`) in the backend's environment.
Then `restart_backend` does not start the backend again by itself. After it
has stopped the backend's services and child processes, it exits with the
code `RESTART_EXIT_CODE` (75, ``EX_TEMPFAIL``: "try again"), and the launcher
reads the saved choice and starts the world again. Any other exit code is a
stop. Without the variable the backend starts itself again in place, as before.

The real folders are remembered in ``ASTROMETRICS_REAL_*`` variables. One
helper (`astrometricslib.drivers.real_folders`) finds them, before and after
the move.

If the simulator cannot start for any reason (a program is missing, the
packaged app, a settings problem, any error at all), the launch does not fall
back to the real telescope. It builds an offline world in the same folder: no
telescope, no PHD2, no downloads, a refusing ``ssh``. The reason is kept in
`LAUNCH_ERROR_VARIABLE`, and the source in force is `OFFLINE`, so the app can
show "SIMULATOR FAILED".

The launch is skipped when a program has already chosen its own world:
tests, and any run that sets ``ASTROMETRICS_TESTING`` or a settings path.
"""

import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psutil

from astrometricslib.drivers import SIMULATOR_FOLDER_NAME, real_folders
from simulator import (
    SimulatedObservatory,
    SimulationKind,
    choose_free_port,
    offline_environment,
    prepare_offline_world,
    remove_library_copy,
)

logger = logging.getLogger(__name__)

#: The two values of the setting.
REAL = "real"
SIMULATED = SimulationKind.SIMULATED_SKY.value
SOURCES = (REAL, SIMULATED)
#: The source in force when the simulator failed to start: no telescope at all.
#: It is never a choice, only a state.
OFFLINE = "offline"

#: Name of the choice file, inside the real settings folder.
CHOICE_FILE_NAME = "observatory_source.json"

#: Variables the launch sets, so the running backend knows its world.
ACTIVE_SOURCE_VARIABLE = "ASTROMETRICS_OBSERVATORY_SOURCE"
ACTIVE_ROOT_VARIABLE = "ASTROMETRICS_SIMULATOR_ROOT"
ACTIVE_PORTS_VARIABLE = "ASTROMETRICS_SIMULATOR_PORTS"
LAUNCH_ERROR_VARIABLE = "ASTROMETRICS_OBSERVATORY_SOURCE_ERROR"
REAL_CONFIG_HOME_VARIABLE = "ASTROMETRICS_REAL_CONFIG_HOME"
REAL_DATA_HOME_VARIABLE = "ASTROMETRICS_REAL_DATA_HOME"
REAL_CONFIG_PATH_VARIABLE = "ASTROMETRICS_REAL_CONFIG_PATH"

#: Holds the values the launch replaced, so a restart can put them back.
ORIGINALS_VARIABLE = "ASTROMETRICS_LAUNCH_ORIGINALS"
#: The variables the launch changes or adds for a simulator world.
CHANGED_VARIABLES = (
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_STATE_HOME",
    "XDG_CONFIG_HOME",
    "ASTROMETRICS_CONFIG",
    "ASTROMETRICS_CONFIG_PATH",
    "PATH",
    "ASTROMETRICS_STANDIN_HOME",
    "ASTROMETRICS_STANDIN_SSH_MODE",
)
#: The variables the launch only adds, which a restart removes.
ADDED_VARIABLES = (
    ACTIVE_SOURCE_VARIABLE,
    ACTIVE_ROOT_VARIABLE,
    ACTIVE_PORTS_VARIABLE,
    LAUNCH_ERROR_VARIABLE,
    REAL_CONFIG_HOME_VARIABLE,
    REAL_DATA_HOME_VARIABLE,
    REAL_CONFIG_PATH_VARIABLE,
    ORIGINALS_VARIABLE,
)

#: Seconds the backend waits after answering before it starts again.
RESTART_DELAY_SECONDS = 0.7

#: Set by a launcher that runs the backend as its child. The backend then
#: ends with `RESTART_EXIT_CODE` instead of starting itself again.
LAUNCHER_VARIABLE = "ASTROMETRICS_LAUNCHER"
#: The exit code that means "start me again with the saved choice" (75 is
#: ``EX_TEMPFAIL`` in ``sysexits.h``). A launcher relaunches on this code only.
RESTART_EXIT_CODE = 75


@dataclass(frozen=True)
class SourceChoice:
    """The saved choice.

    Attributes
    ----------
    source : `str`
        ``real`` or ``simulated``.
    """

    source: str = REAL


def choice_file_path(environ: dict[str, str] | None = None) -> Path:
    """Give the path of the choice file.

    Parameters
    ----------
    environ : `dict` [`str`, `str`], optional
        The environment. Default: the process environment.

    Returns
    -------
    path : `pathlib.Path`
        ``<real settings folder>/astrometrics/observatory_source.json``.
    """
    return (
        real_folders(os.environ if environ is None else environ).config_home
        / "astrometrics"
        / CHOICE_FILE_NAME
    )


def read_choice(environ: dict[str, str] | None = None) -> SourceChoice:
    """Read the saved choice.

    A missing or damaged file means the real telescope.

    Parameters
    ----------
    environ : `dict` [`str`, `str`], optional
        The environment. Default: the process environment.

    Returns
    -------
    choice : `SourceChoice`
        The saved choice.
    """
    try:
        data = json.loads(choice_file_path(environ).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return SourceChoice()
    source = str(data.get("source", REAL)) if isinstance(data, dict) else REAL
    if source not in SOURCES:
        return SourceChoice()
    return SourceChoice(source)


def write_choice(choice: SourceChoice, environ: dict[str, str] | None = None) -> None:
    """Save the choice.

    Parameters
    ----------
    choice : `SourceChoice`
        The choice to save.
    environ : `dict` [`str`, `str`], optional
        The environment. Default: the process environment.
    """
    path = choice_file_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"source": choice.source}, indent=2), encoding="utf-8")
    temporary.replace(path)


def world_is_pinned(environ: dict[str, str]) -> bool:
    """Say whether a program has already chosen its own world.

    Parameters
    ----------
    environ : `dict` [`str`, `str`]
        The environment.

    Returns
    -------
    pinned : `bool`
        `True` with ``ASTROMETRICS_TESTING``, or with a settings
        path set (the TPM harness and the UI test backend do this).
    """
    return (
        bool(environ.get("ASTROMETRICS_TESTING"))
        or bool(environ.get("ASTROMETRICS_CONFIG_PATH") or environ.get("ASTROMETRICS_CONFIG"))
        or ACTIVE_SOURCE_VARIABLE in environ
    )


def apply_launch_environment(environ: dict[str, str] | None = None) -> str:
    """Point the process at the world the saved choice names.

    For the real telescope this changes nothing. For a simulator source it
    sets the ``XDG_*`` variables and the settings path to the simulator's
    folder, puts the ssh stand-in first on ``PATH``, picks the simulator's
    ports and checks the simulator's settings. If any step fails, for any
    reason, the launch builds an offline world instead (no telescope of any
    kind), never the real telescope, and leaves the reason in
    `LAUNCH_ERROR_VARIABLE`.

    Parameters
    ----------
    environ : `dict` [`str`, `str`], optional
        The environment to read and change. Default: the process environment.

    Returns
    -------
    source : `str`
        The source in force: ``real``, ``simulated`` or
        ``offline`` (the simulator failed).
    """
    environment = os.environ if environ is None else environ
    if (environ is None and "pytest" in sys.modules) or world_is_pinned(environment):
        return environment.get(ACTIVE_SOURCE_VARIABLE, REAL)
    choice = read_choice(environment)
    folders = real_folders(environment)
    root = folders.data_home / SIMULATOR_FOLDER_NAME
    if choice.source == REAL:
        remove_leftovers(root)
        return REAL
    observatory: SimulatedObservatory | None = None
    try:
        observatory = SimulatedObservatory(
            root,
            SimulationKind(choice.source),
            choose_free_port(),
            choose_free_port(),
            copy_library=True,
            real_library=folders,
        )
        observatory.prepare()
        config_path = observatory.config_path
        extra = observatory.environment()
        source = choice.source
    except Exception as error:  # any failure means offline, never the real telescope
        logger.exception("The simulator source cannot start; using the offline world")
        environment[LAUNCH_ERROR_VARIABLE] = f"{type(error).__name__}: {error}"
        observatory = None
        config_path, extra = _prepare_offline(root)
        source = OFFLINE
    environment[ORIGINALS_VARIABLE] = json.dumps({name: environment.get(name) for name in CHANGED_VARIABLES})
    environment[REAL_CONFIG_HOME_VARIABLE] = str(folders.config_home)
    environment[REAL_DATA_HOME_VARIABLE] = str(folders.data_home)
    environment[REAL_CONFIG_PATH_VARIABLE] = str(folders.config_file)
    environment["XDG_DATA_HOME"] = str(root / "xdg_data")
    environment["XDG_CACHE_HOME"] = str(root / "xdg_cache")
    environment["XDG_STATE_HOME"] = str(root / "xdg_state")
    environment["XDG_CONFIG_HOME"] = str(root / "xdg_config")
    environment["ASTROMETRICS_CONFIG"] = str(config_path)
    environment["ASTROMETRICS_CONFIG_PATH"] = str(config_path)
    environment.update(extra)
    environment[ACTIVE_SOURCE_VARIABLE] = source
    environment[ACTIVE_ROOT_VARIABLE] = str(root)
    if observatory is not None:
        environment[ACTIVE_PORTS_VARIABLE] = f"{observatory.indi_port},{observatory.phd2_port}"
    for name in ("xdg_data", "xdg_cache", "xdg_state", "xdg_config"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return source


def remove_leftovers(root: Path) -> None:
    """Remove what a crashed simulator session left behind, in real mode.

    Unmounts the overlay and deletes the temporary upper layer and database
    copies, only inside `root`. Does nothing when the folder does not exist.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's folder.
    """
    if not (root / "overlay").exists() and not (root / "library").exists():
        return
    try:
        if not remove_library_copy(root):
            logger.error("Leftovers of a simulator session are still mounted in %s", root)
    except Exception:  # cleaning up must never stop the real backend from starting
        logger.exception("Could not clean up the simulator folder %s", root)


def _prepare_offline(root: Path) -> tuple[Path, dict[str, str]]:
    """Build the offline world, in a throwaway folder if `root` will not do.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's folder.

    Returns
    -------
    config_path : `pathlib.Path`
        The offline settings file.
    environment : `dict` [`str`, `str`]
        The variables the offline world adds.
    """
    try:
        return prepare_offline_world(root), offline_environment(root)
    except Exception:  # the folder is unusable; a throwaway one still keeps us off the telescope
        logger.exception("The offline world could not be built in %s; using a temporary folder", root)
        import tempfile

        fallback = Path(tempfile.mkdtemp(prefix="astrometrics-offline-"))
        return prepare_offline_world(fallback), offline_environment(fallback)


def active_observatory(environ: dict[str, str] | None = None) -> SimulatedObservatory | None:
    """Rebuild the simulator object for the world this process was launched in.

    Parameters
    ----------
    environ : `dict` [`str`, `str`], optional
        The environment. Default: the process environment.

    Returns
    -------
    observatory : `SimulatedObservatory` or `None`
        `None` when the process uses the real telescope.
    """
    environment = os.environ if environ is None else environ
    source = environment.get(ACTIVE_SOURCE_VARIABLE, REAL)
    if source != SIMULATED or ACTIVE_PORTS_VARIABLE not in environment:
        return None
    indi_port, phd2_port = (int(part) for part in environment[ACTIVE_PORTS_VARIABLE].split(","))
    return SimulatedObservatory(
        Path(environment[ACTIVE_ROOT_VARIABLE]),
        SimulationKind(source),
        indi_port,
        phd2_port,
        copy_library=True,
    )


def original_environment(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Give the environment the process had before the launch changed it.

    A restart starts from this, so the next launch reads the saved choice
    again instead of staying in the world of this one.

    Parameters
    ----------
    environ : `dict` [`str`, `str`], optional
        The environment. Default: the process environment.

    Returns
    -------
    restored : `dict` [`str`, `str`]
        A copy with the launch's changes undone.
    """
    restored = dict(os.environ if environ is None else environ)
    try:
        originals = json.loads(restored.get(ORIGINALS_VARIABLE, "{}"))
    except ValueError:
        originals = {}
    for name, value in originals.items():
        if value is None:
            restored.pop(name, None)
        else:
            restored[name] = value
    for name in ADDED_VARIABLES:
        restored.pop(name, None)
    return restored


def end_child_processes(timeout: float = 5.0) -> int:
    """End every process this process started, and wait for them.

    The backend starts a Siril in a session of its own, the INDI worker and
    process pools. None of them may survive a restart in place, because the
    process id does not change. They get SIGTERM, then SIGKILL after
    `timeout` seconds.

    Parameters
    ----------
    timeout : `float`, optional
        Seconds to wait after SIGTERM.

    Returns
    -------
    count : `int`
        How many child processes there were.
    """
    children = psutil.Process().children(recursive=True)
    for child in children:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(children, timeout=timeout)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=2.0)
    return len(children)


def close_inherited_descriptors() -> None:
    """Mark every open descriptor above 2 as closed-on-exec.

    The listening socket, pipes and files must not reach the new program
    that ``execve`` starts, or they would stay open (and the port would stay
    taken) in a program that does not know about them.
    """
    for entry in os.listdir("/proc/self/fd"):
        descriptor = int(entry)
        if descriptor > 2:
            try:
                os.set_inheritable(descriptor, False)
            except OSError:
                pass


def restart_backend(
    shutdown: Callable[[], None] | None = None,
    execve: Callable[[str, list[str], dict[str, str]], None] = os.execve,
    delay_seconds: float = RESTART_DELAY_SECONDS,
    exit_process: Callable[[int], None] = os._exit,
    environ: dict[str, str] | None = None,
) -> threading.Thread:
    """Start this backend again, in place, soon after the reply is sent.

    When a launcher runs the backend (``ASTROMETRICS_LAUNCHER`` is set), the
    backend does not start itself again. It stops everything in the same
    way, then ends with `RESTART_EXIT_CODE` and leaves the new start to the
    launcher.

    The process keeps its id, its output and its environment (so Electron's
    manager and the run scripts do not notice), and the next start reads the
    new choice. Because the id does not change, nothing may be left running
    that watches it. So, before the process image is replaced, this

    1. calls `shutdown` (the container's `shutdown_resources`: the
       simulator, the maintenance and download threads, Siril, the INDI
       worker);
    2. ends and waits for every child process that is left; and
    3. marks every open descriptor (the listening socket, pools) as
       closed-on-exec.

    Parameters
    ----------
    shutdown : `~collections.abc.Callable`, optional
        Stops the backend's services.
    execve : `~collections.abc.Callable`, optional
        Replaces the process image. Tests pass a recorder.
    delay_seconds : `float`, optional
        Seconds to wait first, so the reply goes out.
    exit_process : `~collections.abc.Callable`, optional
        Ends the process with an exit code, when a launcher is running the
        backend. Tests pass a recorder.
    environ : `dict` [`str`, `str`], optional
        The environment that says whether a launcher is running the
        backend. Default: the process environment.

    Returns
    -------
    thread : `threading.Thread`
        The thread that does the restart.
    """
    launched_by_launcher = bool((os.environ if environ is None else environ).get(LAUNCHER_VARIABLE))

    def restart() -> None:
        """Wait for the reply to go out, stop everything, replace the image."""
        time.sleep(delay_seconds)
        if shutdown is not None:
            try:
                shutdown()
            except Exception:  # the restart must go on to stop the rest
                logger.exception("Shutdown before the restart failed")
        ended = end_child_processes()
        logger.warning(
            "%s the backend to change the observatory source (%d child processes ended).",
            "Stopping for the launcher to start" if launched_by_launcher else "Restarting",
            ended,
        )
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except OSError:
                pass
        if launched_by_launcher:
            exit_process(RESTART_EXIT_CODE)
            return
        close_inherited_descriptors()
        execve(sys.executable, [sys.executable, *sys.orig_argv[1:]], original_environment())

    thread = threading.Thread(target=restart, name="observatory-source-restart", daemon=True)
    thread.start()
    return thread
