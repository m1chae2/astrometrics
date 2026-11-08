"""Purpose: Start the backend in the world the observatory source names.

Description: The launcher is the program that starts the backend when the
simulator layer is used (``python -m simulator.launch``). It owns the
simulated world for the whole time the backend runs, so the backend itself
runs unaware of simulation. One session goes like this:

1. Read the saved observatory source choice (`simulator.source_choice`).
2. Real telescope: run the backend as a child with the environment as it
   is, plus ``ASTROMETRICS_LAUNCHER``. No world is built.
3. Simulated sky: take an exclusive lock on
   ``<simulator folder>/launcher.lock`` and hold it until the world is
   gone. If another launcher holds it, touch
   nothing and stop with a clear message. Then build the world (a copy of the
   databases and a read-only overlay of the real frames), start the stand-in
   telescope computer, work out the calibration records, and run the backend
   as a child with the world's settings and folders. If the world cannot be
   built or started, an offline world (no telescope) is built in its place and
   the reason is passed to the backend, which shows "SIMULATOR FAILED".
4. Operator step (decision D11 of the simulator plan). Until the app can
   command PHD2 and the camera by itself, the launcher plays the operator for
   one thing: once the backend answers ``/api/ready`` it records the
   calibrations, then lets the app take command of the simulated devices,
   through the backend's public RPC methods. The log says so in lines that
   start with ``OPERATOR STEP (D11)``.
5. Connection guard. Every few seconds the launcher lists the backend's TCP
   connections. One to an address beyond this computer ends the session.
6. When the backend ends, the launcher stops the stand-ins, removes the
   library copy and releases the lock. Exit code 75 (`RESTART_EXIT_CODE`)
   means "the user changed the source": the launcher reads the choice again
   and starts the next session. Any other code is a stop, and the launcher
   exits with it. SIGTERM or SIGINT stops the backend first, then cleans up.

The backend has no world code of its own: without this launcher it runs on
the real telescope and cannot change its source (phase L3).

Usage::

    python -m simulator.launch
    python -m simulator.launch --root /tmp/simulator-test --port 5100
"""

import argparse
import fcntl
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from astrometricslib.drivers import LAUNCH_NOTES_VARIABLE, MISSING_PROGRAMS_VARIABLE, real_folders
from simulator.connection_guard import non_loopback_connections
from simulator.library_copy import teardown as remove_library_copy
from simulator.observatory import (
    SimulatedObservatory,
    SimulationKind,
    choose_free_port,
    launch_notes,
    missing_programs,
    offline_environment,
    prepare_offline_world,
)
from simulator.rpc_client import backend_is_ready, call_rpc
from simulator.source_choice import (
    ACTIVE_ROOT_VARIABLE,
    ACTIVE_SOURCE_VARIABLE,
    LAUNCH_ERROR_VARIABLE,
    LAUNCHER_VARIABLE,
    OFFLINE,
    REAL,
    REAL_CONFIG_HOME_VARIABLE,
    REAL_CONFIG_PATH_VARIABLE,
    REAL_DATA_HOME_VARIABLE,
    REAL_STATE_HOME_VARIABLE,
    RESTART_EXIT_CODE,
    SIMULATED,
    read_choice,
)

__all__ = [
    "GUARD_EXIT_CODE",
    "LOCKED_EXIT_CODE",
    "Launcher",
    "World",
    "acquire_world_lock",
    "build_world",
    "install_signal_handlers",
    "main",
    "run_operator_step",
]

logger = logging.getLogger("simulator.launch")

#: Name of the simulator's folder, beside the real data folder.
SIMULATOR_FOLDER_NAME = "astrometrics-simulator"
#: Name of the lock file, inside the simulator folder.
LOCK_FILE_NAME = "launcher.lock"
#: Exit code when another launcher holds the lock (``EX_CANTCREAT``).
LOCKED_EXIT_CODE = 73
#: Exit code when the connection guard ended the session (``EX_SOFTWARE``).
GUARD_EXIT_CODE = 70
#: Seconds between looks at the backend's connections.
GUARD_INTERVAL_SECONDS = 5.0
#: Seconds the backend gets to end after SIGTERM before it is killed.
BACKEND_STOP_SECONDS = 30.0
#: Seconds between looks at the backend child.
POLL_SECONDS = 0.25
#: Seconds the backend may take to answer ``/api/ready`` before the operator
#: step gives up (the first load of the star catalog is slow).
READY_TIMEOUT_SECONDS = 600.0
#: Seconds the calibration step may take.
CALIBRATION_TIMEOUT_SECONDS = 300.0
#: The note the operator step keeps with the change of control mode.
OPERATOR_EVIDENCE_NOTE = "Simulator: commands reach only simulated devices on this computer."

#: The repository folder (the backend child runs from here).
REPOSITORY = Path(__file__).resolve().parents[1]


@dataclass
class World:
    """A world the backend runs in, and how to take it down.

    Attributes
    ----------
    root : `pathlib.Path`
        The simulator folder.
    source : `str`
        ``simulated`` or ``offline`` (the simulator failed).
    config_path : `pathlib.Path`
        The world's settings file.
    variables : `dict` [`str`, `str`]
        Variables the world adds to the backend's environment (``PATH`` with
        the ssh stand-in, the stand-in home).
    error : `str` or `None`
        Why the simulator failed (offline world only).
    pointed_targets : `~collections.abc.Callable`
        Gives ``(name, ra_hours, dec_degrees)`` of the targets the simulated
        mount points at.
    stop_standins : `~collections.abc.Callable`
        Ends the stand-in processes.
    remove_copy : `~collections.abc.Callable`
        Unmounts the overlay and deletes the copies; gives `True` when
        nothing is left.
    """

    root: Path
    source: str
    config_path: Path
    variables: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    pointed_targets: Callable[[], list[tuple[str, float, float]]] = list
    stop_standins: Callable[[], None] = lambda: None
    remove_copy: Callable[[], bool] = lambda: True


def acquire_world_lock(root: Path) -> int | None:
    """Take the exclusive lock of a simulator folder, without waiting.

    The lock is an ``flock`` on ``<root>/launcher.lock``. The system drops it
    when the process ends, even after a crash, so it never goes stale. The
    descriptor is not inherited by child processes.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator folder. It is made if it does not exist.

    Returns
    -------
    descriptor : `int` or `None`
        The open descriptor that holds the lock (close it to release), or
        `None` when another process holds the lock.
    """
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root / LOCK_FILE_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    os.ftruncate(descriptor, 0)
    os.write(descriptor, f"{os.getpid()}\n".encode())
    return descriptor


def release_world_lock(descriptor: int) -> None:
    """Release a lock taken by `acquire_world_lock`.

    Parameters
    ----------
    descriptor : `int`
        The descriptor it returned.
    """
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def remove_leftovers(root: Path) -> None:
    """Remove what a crashed simulator session left behind.

    Only called while this launcher holds the folder's lock. Does nothing
    when the folder has no overlay or library copy.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator folder.
    """
    if not (root / "overlay").exists() and not (root / "library").exists():
        return
    try:
        if not remove_library_copy(root):
            logger.error("Leftovers of a simulator session are still mounted in %s", root)
    except Exception:  # cleaning up must never stop the real backend from starting
        logger.exception("Could not clean up the simulator folder %s", root)


def _offline_world(root: Path, error: str) -> World:
    """Build the offline world, in a throwaway folder if `root` will not do.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator folder.
    error : `str`
        Why the simulator failed.

    Returns
    -------
    world : `World`
        A world with no telescope, no PHD2, no downloads and a refusing ssh.
    """
    try:
        config_path = prepare_offline_world(root)
        variables = offline_environment(root)
        folder = root
    except Exception:  # the folder is unusable; a throwaway one still keeps us off the telescope
        logger.exception("The offline world could not be built in %s; using a temporary folder", root)
        folder = Path(tempfile.mkdtemp(prefix="astrometrics-offline-"))
        config_path = prepare_offline_world(folder)
        variables = offline_environment(folder)
    return World(root=folder, source=OFFLINE, config_path=config_path, variables=variables, error=error)


def build_world(root: Path, environ: dict[str, str]) -> World:
    """Build the simulated world and start its stand-ins.

    Builds the databases copy and the overlay with
    `SimulatedObservatory.prepare`, starts the stand-in processes, and waits
    until the simulated telescope is up. Any failure, for any reason, gives
    the offline world instead, never the real telescope.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator folder (locked by the caller).
    environ : `dict` [`str`, `str`]
        The environment, which names the real folders.

    Returns
    -------
    world : `World`
        ``simulated`` when everything started, otherwise ``offline`` with
        the reason in ``error``.
    """
    folders = real_folders(environ)
    indi_port, phd2_port = choose_free_port(), choose_free_port()
    runner: SimulatedObservatory | None = None
    try:
        builder = SimulatedObservatory(
            root,
            SimulationKind.SIMULATED_SKY,
            indi_port,
            phd2_port,
            copy_library=True,
            real_library=folders,
        )
        builder.prepare()
        # The world is built; the runner only starts the stand-ins in it.
        runner = SimulatedObservatory(
            root, SimulationKind.SIMULATED_SKY, indi_port, phd2_port, existing_world=True
        )
        runner.start()
        runner.wait_ready()
    except Exception as error:  # any failure means offline, never the real telescope
        logger.exception("The simulator cannot start; using the offline world")
        if runner is not None:
            runner.stop()
        remove_leftovers(root)
        return _offline_world(root, f"{type(error).__name__}: {error}")
    running = runner
    return World(
        root=root,
        source=SIMULATED,
        config_path=running.config_path,
        variables=running.environment(),
        pointed_targets=running.pointed_targets,
        stop_standins=running.stop,
        remove_copy=lambda: remove_library_copy(root),
    )


def world_environment(base: dict[str, str], world: World, environ: dict[str, str]) -> dict[str, str]:
    """Give the backend's environment inside a world.

    The backend finds its settings and folders through this environment, and
    still finds the real settings folder (for the source choice) through the
    ``ASTROMETRICS_REAL_*`` variables. It also learns which programs the
    simulator is missing, so the settings page can say so.

    Parameters
    ----------
    base : `dict` [`str`, `str`]
        The launcher's environment.
    world : `World`
        The world.
    environ : `dict` [`str`, `str`]
        The environment that names the real folders.

    Returns
    -------
    variables : `dict` [`str`, `str`]
        A new environment for the backend child.
    """
    folders = real_folders(environ)
    root = world.root
    child = dict(base)
    child[REAL_CONFIG_HOME_VARIABLE] = str(folders.config_home)
    child[REAL_DATA_HOME_VARIABLE] = str(folders.data_home)
    child[REAL_STATE_HOME_VARIABLE] = str(folders.state_home)
    child[REAL_CONFIG_PATH_VARIABLE] = str(folders.config_file)
    for name in ("data", "cache", "state", "config"):
        (root / f"xdg_{name}").mkdir(parents=True, exist_ok=True)
        child[f"XDG_{name.upper()}_HOME"] = str(root / f"xdg_{name}")
    child["ASTROMETRICS_CONFIG"] = str(world.config_path)
    child["ASTROMETRICS_CONFIG_PATH"] = str(world.config_path)
    child.update(world.variables)
    child[ACTIVE_SOURCE_VARIABLE] = world.source
    child[ACTIVE_ROOT_VARIABLE] = str(root)
    if world.error:
        child[LAUNCH_ERROR_VARIABLE] = world.error
    child[LAUNCHER_VARIABLE] = "1"
    child[MISSING_PROGRAMS_VARIABLE] = ",".join(missing_programs())
    child[LAUNCH_NOTES_VARIABLE] = "\n".join(launch_notes())
    return child


def _sexagesimal(value: float, is_right_ascension: bool) -> str:
    """Write a position as the library's ``HH:MM:SS`` or ``+DD:MM:SS`` text.

    Parameters
    ----------
    value : `float`
        Hours (right ascension) or degrees (declination).
    is_right_ascension : `bool`
        Whether `value` is hours.

    Returns
    -------
    text : `str`
        The position as sexagesimal text.
    """
    sign = "-" if value < 0 else ("" if is_right_ascension else "+")
    total = round(abs(value) * 3600)
    return f"{sign}{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def run_operator_step(
    port: int,
    calibration_calls: list[tuple[str, dict]],
    targets: list[tuple[str, float, float]],
    call: Callable[..., object] = call_rpc,
) -> dict:
    """Play the operator (decision D11) once the backend is ready.

    In order: add the targets the simulated mount points at (a frame is
    downloaded only for a target the library knows), record the guider
    calibration and the focus model, and let the app take command of the
    simulated devices with ``shadow_first``. Each failure is logged and the
    step goes on; the rules of the library decide what is allowed.

    Parameters
    ----------
    port : `int`
        The backend's port.
    calibration_calls : `list` [`tuple` [`str`, `dict`]]
        ``(method, parameters)`` of the two record methods, from
        `simulator.simulated_calibration.rpc_calls`.
    targets : `list` [`tuple`]
        ``(name, ra_hours, dec_degrees)`` of the pointed targets.
    call : `~collections.abc.Callable`, optional
        Calls one RPC method: ``call(port, method, params)``. Tests pass a
        recorder.

    Returns
    -------
    outcome : `dict`
        ``allowed`` and ``off`` (capabilities and the reason each stays
        off) from the change of control mode, and ``error`` (`str` or
        `None`).
    """
    logger.info("OPERATOR STEP (D11): the launcher plays the operator for the simulated devices.")
    for name, right_ascension, declination in targets:
        try:
            if call(port, "target:get", {"target_id": name}) is None:
                call(
                    port,
                    "target:create",
                    {
                        "target_id": name,
                        "ra": _sexagesimal(right_ascension, True),
                        "dec": _sexagesimal(declination, False),
                    },
                )
                logger.info("OPERATOR STEP (D11): added the target %s.", name)
        except RuntimeError, OSError, ValueError:
            logger.warning("OPERATOR STEP (D11): could not add the target %s.", name, exc_info=True)
    for method, parameters in calibration_calls:
        try:
            call(port, method, parameters)
            logger.info("OPERATOR STEP (D11): %s recorded.", method)
        except RuntimeError, OSError, ValueError:
            logger.warning("OPERATOR STEP (D11): %s failed.", method, exc_info=True)
    try:
        result = call(
            port,
            "observatory:enter_controller_mode",
            {"evidence_note": OPERATOR_EVIDENCE_NOTE, "shadow_first": True},
        )
    except (RuntimeError, OSError, ValueError) as error:
        logger.warning("OPERATOR STEP (D11): the app could not take command of the simulator.", exc_info=True)
        return {"allowed": [], "off": {}, "error": f"{type(error).__name__}: {error}"}
    result = result if isinstance(result, dict) else {}
    allowed = sorted(result.get("applied", {}))
    off = {str(name): str(reason) for name, reason in result.get("rejected", {}).items()}
    logger.info("OPERATOR STEP (D11): simulator commands are on for: %s.", ", ".join(allowed) or "nothing")
    if off:
        logger.warning(
            "OPERATOR STEP (D11): simulator commands stay off for: %s.",
            "; ".join(f"{name} ({reason})" for name, reason in off.items()),
        )
    return {"allowed": allowed, "off": off, "error": None}


def work_out_calibrations(world: World, environment: dict[str, str]) -> list[tuple[str, dict]]:
    """Work out the calibration records in a short-lived process.

    Runs ``python -m simulator.simulated_calibration`` with the world's
    settings, before the backend starts, so the library copy is read while
    nothing else uses it. A failure is logged and gives no records; the
    session goes on.

    Parameters
    ----------
    world : `World`
        The simulated world.
    environment : `dict` [`str`, `str`]
        The backend's environment for this world.

    Returns
    -------
    calls : `list` [`tuple` [`str`, `dict`]]
        ``(method, parameters)`` of the two record methods; empty on failure.
    """
    command = [sys.executable, "-m", "simulator.simulated_calibration", "--root", str(world.root)]
    try:
        completed = subprocess.run(
            command,
            env={**environment, "PYTHONPATH": f"{REPOSITORY}{os.pathsep}{environment.get('PYTHONPATH', '')}"},
            cwd=REPOSITORY,
            capture_output=True,
            text=True,
            check=False,
            timeout=CALIBRATION_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            logger.warning(
                "OPERATOR STEP (D11): the calibration values could not be worked out: %s",
                completed.stderr.strip() or f"exit code {completed.returncode}",
            )
            return []
        report = json.loads(completed.stdout.strip().splitlines()[-1])
    except OSError, ValueError, IndexError, subprocess.TimeoutExpired:
        logger.warning("OPERATOR STEP (D11): the calibration values could not be worked out.", exc_info=True)
        return []
    logger.info(
        "OPERATOR STEP (D11): guider calibration from %s; focus model from %s.",
        report.get("guider_source"),
        report.get("focus_source"),
    )
    return [(str(method), dict(parameters)) for method, parameters in report["calls"]]


class Launcher:
    """Run the backend in the world the saved choice names.

    Parameters
    ----------
    environ : `dict` [`str`, `str`], optional
        The environment. It names the real folders and is the base of the
        backend's environment. Default: a copy of the process environment.
    root : `pathlib.Path`, optional
        The simulator folder. Default: ``astrometrics-simulator`` beside the
        real data folder.
    port : `int`, optional
        The backend's port. Default: ``ASTROMETRICS_PORT`` or 5000.
    backend_command : `list` [`str`], optional
        The backend program. Default: ``python -u -m backend.main_backend``.
    world_builder : `~collections.abc.Callable`, optional
        ``world_builder(root, environ)`` gives a `World`. Default:
        `build_world`.
    calibration_runner : `~collections.abc.Callable`, optional
        ``calibration_runner(world, environment)`` gives the calibration
        calls. Default: `work_out_calibrations`.
    operator : `~collections.abc.Callable`, optional
        The operator step: ``operator(port, calls, targets)``. Default:
        `run_operator_step`.
    ready_check : `~collections.abc.Callable`, optional
        ``ready_check(port)`` says whether the backend answers ``/api/ready``.
    connection_check : `~collections.abc.Callable`, optional
        ``connection_check(pid)`` lists connections beyond this computer.
    guard_interval : `float`, optional
        Seconds between connection checks.
    """

    def __init__(
        self,
        environ: dict[str, str] | None = None,
        root: Path | None = None,
        port: int | None = None,
        backend_command: list[str] | None = None,
        world_builder: Callable[[Path, dict[str, str]], World] = build_world,
        calibration_runner: Callable[[World, dict[str, str]], list[tuple[str, dict]]] = work_out_calibrations,
        operator: Callable[..., dict] = run_operator_step,
        ready_check: Callable[[int], bool] = backend_is_ready,
        connection_check: Callable[[int], list[str]] = non_loopback_connections,
        guard_interval: float = GUARD_INTERVAL_SECONDS,
    ) -> None:
        """Keep the settings; nothing starts yet."""
        self.environ = dict(os.environ if environ is None else environ)
        self.root = root or real_folders(self.environ).data_home / SIMULATOR_FOLDER_NAME
        self.port = port or int(self.environ.get("ASTROMETRICS_PORT", "5000"))
        self.backend_command = backend_command or [sys.executable, "-u", "-m", "backend.main_backend"]
        self.world_builder = world_builder
        self.calibration_runner = calibration_runner
        self.operator = operator
        self.ready_check = ready_check
        self.connection_check = connection_check
        self.guard_interval = guard_interval
        self.stop_requested = threading.Event()
        self.operator_outcome: dict | None = None
        self.sessions: list[str] = []
        self._child: subprocess.Popen | None = None

    def request_stop(self) -> None:
        """Ask the launcher to stop the backend, clean up and exit."""
        self.stop_requested.set()

    def run(self) -> int:
        """Run sessions until the backend stops for a reason but a restart.

        Returns
        -------
        status : `int`
            The backend's exit code; 0 after a requested stop;
            `LOCKED_EXIT_CODE` when another launcher holds the world;
            `GUARD_EXIT_CODE` when the connection guard ended the session.
        """
        while True:
            choice = read_choice(self.environ).source
            self.sessions.append(choice)
            logger.info("Observatory source: %s.", choice)
            status = self._run_real() if choice == REAL else self._run_simulated()
            if status == RESTART_EXIT_CODE and not self.stop_requested.is_set():
                logger.info("The backend asked for a restart; reading the choice again.")
                continue
            return 0 if self.stop_requested.is_set() and status == RESTART_EXIT_CODE else status

    def _run_real(self) -> int:
        """Run one session with the real telescope: no world.

        Returns
        -------
        status : `int`
            The backend's exit code.
        """
        descriptor = acquire_world_lock(self.root)
        if descriptor is not None:
            # Nobody runs a world here, so what a crash left can go.
            try:
                remove_leftovers(self.root)
            finally:
                release_world_lock(descriptor)
        child_environment = {
            **self.environ,
            LAUNCHER_VARIABLE: "1",
            MISSING_PROGRAMS_VARIABLE: ",".join(missing_programs()),
            LAUNCH_NOTES_VARIABLE: "\n".join(launch_notes()),
        }
        child = self._start_backend(child_environment)
        return self._supervise(child, guard=False)

    def _run_simulated(self) -> int:
        """Run one session in a simulated world, and take the world down after.

        Returns
        -------
        status : `int`
            The backend's exit code, or `LOCKED_EXIT_CODE`.
        """
        descriptor = acquire_world_lock(self.root)
        if descriptor is None:
            logger.error(
                "Another launcher holds the simulator folder %s (see %s). "
                "Nothing was changed. Stop that launcher first.",
                self.root,
                self.root / LOCK_FILE_NAME,
            )
            return LOCKED_EXIT_CODE
        world: World | None = None
        try:
            world = self.world_builder(self.root, self.environ)
            if world.source == OFFLINE:
                logger.error("SIMULATOR FAILED (%s); the backend runs in the offline world.", world.error)
            if self.stop_requested.is_set():
                return 0
            child_environment = world_environment(self.environ, world, self.environ)
            calls = self.calibration_runner(world, child_environment) if world.source == SIMULATED else []
            if self.stop_requested.is_set():
                return 0
            child = self._start_backend(child_environment)
            operator_thread = None
            if world.source == SIMULATED:
                operator_thread = threading.Thread(
                    target=self._operate, args=(child, world, calls), name="launcher-operator", daemon=True
                )
                operator_thread.start()
            try:
                return self._supervise(child, guard=True)
            finally:
                if operator_thread is not None:
                    operator_thread.join(timeout=5.0)
        finally:
            if world is not None:
                self._tear_down(world)
            release_world_lock(descriptor)

    def _start_backend(self, environment: dict[str, str]) -> subprocess.Popen:
        """Start the backend as a child.

        Parameters
        ----------
        environment : `dict` [`str`, `str`]
            The child's environment.

        Returns
        -------
        child : `subprocess.Popen`
            The running backend.
        """
        environment = {
            **environment,
            "PYTHONPATH": f"{REPOSITORY}{os.pathsep}{environment.get('PYTHONPATH', '')}",
        }
        logger.info("Starting the backend: %s", " ".join(self.backend_command))
        self._child = subprocess.Popen(self.backend_command, env=environment, cwd=REPOSITORY)
        return self._child

    def _supervise(self, child: subprocess.Popen, guard: bool) -> int:
        """Wait for the backend; watch the stop request and the connections.

        Parameters
        ----------
        child : `subprocess.Popen`
            The backend.
        guard : `bool`
            Whether to look at its connections.

        Returns
        -------
        status : `int`
            The backend's exit code, or `GUARD_EXIT_CODE`.
        """
        next_check = time.monotonic() + self.guard_interval
        while True:
            try:
                return child.wait(timeout=POLL_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            if self.stop_requested.is_set():
                logger.info("Stopping the backend.")
                self._end_backend(child)
                return 0
            if guard and time.monotonic() >= next_check:
                next_check = time.monotonic() + self.guard_interval
                try:
                    offending = self.connection_check(child.pid)
                except OSError, subprocess.SubprocessError:
                    logger.warning("The connection guard could not look at the connections.", exc_info=True)
                    continue
                if offending:
                    logger.critical(
                        "CONNECTION GUARD: the backend has connections beyond this computer; "
                        "ending the simulator session:\n  %s",
                        "\n  ".join(offending),
                    )
                    self._end_backend(child)
                    return GUARD_EXIT_CODE

    def _end_backend(self, child: subprocess.Popen) -> None:
        """Stop the backend: SIGTERM, then SIGKILL after a wait.

        Parameters
        ----------
        child : `subprocess.Popen`
            The backend.
        """
        if child.poll() is not None:
            return
        child.terminate()
        try:
            child.wait(timeout=BACKEND_STOP_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning("The backend did not end in %.0f s; killing it.", BACKEND_STOP_SECONDS)
            child.kill()
            child.wait()

    def _operate(self, child: subprocess.Popen, world: World, calls: list[tuple[str, dict]]) -> None:
        """Wait for ``/api/ready``, then play the operator.

        Parameters
        ----------
        child : `subprocess.Popen`
            The backend.
        world : `World`
            The simulated world.
        calls : `list` [`tuple` [`str`, `dict`]]
            The calibration record calls.
        """
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if child.poll() is not None or self.stop_requested.is_set():
                return
            if self.ready_check(self.port):
                break
            time.sleep(POLL_SECONDS)
        else:
            logger.warning("OPERATOR STEP (D11): the backend was not ready in time; no operator step.")
            return
        try:
            self.operator_outcome = self.operator(self.port, calls, world.pointed_targets())
        except Exception:  # the app stays usable for watching; the log says what failed
            logger.exception("OPERATOR STEP (D11) failed")

    def _tear_down(self, world: World) -> None:
        """Stop the stand-ins and remove the library copy.

        Parameters
        ----------
        world : `World`
            The world to take down.
        """
        try:
            world.stop_standins()
        except Exception:  # go on to remove the copy
            logger.exception("Could not stop the stand-ins")
        try:
            if not world.remove_copy():
                logger.error("The library copy in %s is not completely removed.", world.root)
        except Exception:  # the lock is released either way
            logger.exception("Could not remove the library copy in %s", world.root)


def install_signal_handlers(launcher: Launcher) -> None:
    """Make SIGTERM and SIGINT stop the launcher cleanly.

    The backend is stopped first, then the world is taken down, then the
    launcher exits. Call it from the main thread.

    Parameters
    ----------
    launcher : `Launcher`
        The launcher to stop.
    """
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: launcher.request_stop())


def main(argv: list[str] | None = None) -> int:
    """Run the launcher from the command line.

    Parameters
    ----------
    argv : `list` [`str`], optional
        The command line. Default: ``sys.argv[1:]``.

    Returns
    -------
    status : `int`
        See `Launcher.run`.
    """
    parser = argparse.ArgumentParser(
        description="Start the backend in the world the observatory source names."
    )
    parser.add_argument(
        "--root", type=Path, default=None, help="The simulator folder (default: the usual one)."
    )
    parser.add_argument(
        "--port", type=int, default=None, help="The backend's port (default: ASTROMETRICS_PORT)."
    )
    arguments = parser.parse_args(argv)
    logging.basicConfig(  # ruff: ignore[banned-api]
        level=logging.INFO, format="%(asctime)s [launcher] %(levelname)s %(message)s"
    )
    launcher = Launcher(root=arguments.root, port=arguments.port)
    install_signal_handlers(launcher)
    return launcher.run()


if __name__ == "__main__":
    sys.exit(main())
