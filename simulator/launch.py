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
4. Operator step (see simulator/README.md). Until the app can
   command PHD2 and the camera by itself, the launcher plays the operator for
   one thing: once the backend answers ``/api/ready`` it records the
   calibrations, then lets the app take command of the simulated devices,
   through the backend's public RPC methods. The log says so in lines that
   start with ``OPERATOR STEP``.
5. Connection guard. Every few seconds the launcher lists the backend's TCP
   and UDP sockets. One to an address beyond this computer ends the session,
   and so do three failed checks in a row. The guard acts after the fact; the
   offline settings are what prevent traffic. The backend also gets
   ``ASTROMETRICS_OFFLINE=1`` as a backstop.
6. When the backend ends, the launcher stops the stand-ins, removes the
   library copy and releases the lock. Exit code 75 (`RESTART_EXIT_CODE`)
   means "the user changed the source": the launcher reads the choice again
   and starts the next session. Any other code is a stop, and the launcher
   exits with it. SIGTERM or SIGINT stops the backend first, then cleans up.

The backend has no world code of its own: without this launcher it runs on
the real telescope and cannot change its source.

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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from astrometricslib.drivers import OFFLINE_VARIABLE
from simulator import phd2_launch
from simulator.connection_guard import non_loopback_connections
from simulator.fit.results import RESULTS_FILE_NAME, fitted_controller_settings
from simulator.guiding_operator import GuidingOutcome, offline_identity, run_guiding_step
from simulator.library_copy import teardown as remove_library_copy
from simulator.observatory import (
    GuiderKind,
    SimulatedObservatory,
    SimulationKind,
    choose_free_port,
    launch_notes,
    missing_programs,
    offline_environment,
    prepare_offline_world,
)
from simulator.real_folders import real_folders
from simulator.rpc_client import backend_is_ready, call_rpc
from simulator.source_choice import (
    ACTIVE_ROOT_VARIABLE,
    ACTIVE_SOURCE_VARIABLE,
    LAUNCH_ERROR_VARIABLE,
    LAUNCH_NOTES_VARIABLE,
    LAUNCHER_VARIABLE,
    MISSING_PROGRAMS_VARIABLE,
    NO_TELESCOPE,
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
    "GUARD_FAILURE_LIMIT",
    "GUIDER_VARIABLE",
    "LOCKED_EXIT_CODE",
    "OFFLINE_VARIABLE",
    "Launcher",
    "World",
    "acquire_world_lock",
    "build_world",
    "choose_guider",
    "install_signal_handlers",
    "main",
    "run_operator_step",
]

logger = logging.getLogger("simulator.launch")

#: Environment variable that chooses who plays PHD2: ``mock`` (the stand-in)
#: or ``phd2`` (the real program). Unset: the real PHD2 when it can run.
GUIDER_VARIABLE = "ASTROMETRICS_SIMULATOR_GUIDER"
#: Name of the simulator's folder, beside the real data folder.
SIMULATOR_FOLDER_NAME = "astrometrics-simulator"
#: Name of the lock file, inside the simulator folder.
LOCK_FILE_NAME = "launcher.lock"
#: Exit code when another launcher holds the lock (``EX_CANTCREAT``).
LOCKED_EXIT_CODE = 73
#: Exit code when the connection guard ended the session (``EX_SOFTWARE``).
GUARD_EXIT_CODE = 70
#: This many connection checks in a row may fail before the guard ends the
#: session (a guard that cannot look is not a guard).
GUARD_FAILURE_LIMIT = 3
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
#: Seconds the fit step may take. It opens the library copy, so the first
#: load of the star catalog dominates.
FIT_TIMEOUT_SECONDS = 600.0
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
    guider : `str`
        ``phd2`` when the real PHD2 plays PHD2, else ``mock``.
    phd2 : `~simulator.phd2_launch.Phd2Instance` or `None`
        The real PHD2 that was started (the guiding step talks to it).
    notes : `list` [`str`]
        Statuses about parts that are missing or replaced (not errors). They
        reach the settings page through the launch notes.
    """

    root: Path
    source: str
    config_path: Path
    variables: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    pointed_targets: Callable[[], list[tuple[str, float, float]]] = list
    stop_standins: Callable[[], None] = lambda: None
    remove_copy: Callable[[], bool] = lambda: True
    guider: str = GuiderKind.MOCK.value
    phd2: phd2_launch.Phd2Instance | None = None
    notes: list[str] = field(default_factory=list)


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
    return World(root=folder, source=NO_TELESCOPE, config_path=config_path, variables=variables, error=error)


def choose_guider(environ: dict[str, str]) -> tuple[GuiderKind, int | None, list[str]]:
    """Decide who plays PHD2 in the world, and on which port.

    The real PHD2 plays when it is installed, has a display and a free
    instance, unless ``ASTROMETRICS_SIMULATOR_GUIDER=mock`` asks for the
    stand-in. When it cannot play, the stand-in does, and the reason is a
    note (a status, not an error).

    Parameters
    ----------
    environ : `dict` [`str`, `str`]
        The launcher's environment.

    Returns
    -------
    guider : `GuiderKind`
        Who plays PHD2.
    port : `int` or `None`
        The real PHD2's event port, or `None` for the stand-in (any free
        port will do).
    notes : `list` [`str`]
        Statuses to report.
    """
    wanted = environ.get(GUIDER_VARIABLE, "").strip().lower()
    if wanted == GuiderKind.MOCK.value:
        return GuiderKind.MOCK, None, []
    note = phd2_launch.phd2_missing_note(environ)
    if note is not None:
        return GuiderKind.MOCK, None, [note]
    instance = phd2_launch.choose_instance()
    if instance is None:
        return GuiderKind.MOCK, None, [f"{phd2_launch.PHD2_MISSING_NOTE} (no free PHD2 instance number)"]
    visible = phd2_launch.display_note(environ)
    return GuiderKind.PHD2, phd2_launch.event_port(instance), [visible] if visible else []


def run_fit_process(
    root: Path, config_path: Path, variables: dict[str, str], environ: dict[str, str]
) -> str | None:
    """Fit the simulators to the recorded nights, in a short-lived process.

    Runs ``python -m simulator.fit`` with the world's settings and folders in
    its environment, so it reads the world's library copy and never the real
    folders (the same way as `work_out_calibrations`). It runs after the copy
    is built and before the stand-ins start, so the copy is only read while
    nothing else uses it. A failure is a status: the simulators keep their own
    values and the launch note says why.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator folder; ``fit_results.json`` is written here.
    config_path : `pathlib.Path`
        The world's settings file.
    variables : `dict` [`str`, `str`]
        The variables the world adds to the backend's environment.
    environ : `dict` [`str`, `str`]
        The launcher's environment (it names the real folders).

    Returns
    -------
    note : `str` or `None`
        `None` when the results file was written. Otherwise a short status
        for the launch notes.
    """
    stub = World(root=root, source=SIMULATED, config_path=config_path, variables=variables)
    environment = world_environment(environ, stub, environ)
    environment["PYTHONPATH"] = f"{REPOSITORY}{os.pathsep}{environment.get('PYTHONPATH', '')}"
    command = [sys.executable, "-m", "simulator.fit", "--root", str(root), "--config", str(config_path)]
    try:
        completed = subprocess.run(
            command,
            env=environment,
            cwd=REPOSITORY,
            capture_output=True,
            text=True,
            check=False,
            timeout=FIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        logger.warning("Fit step: the fit did not run: %s", error)
        return f"Fitted settings: not applied ({type(error).__name__})"
    if completed.returncode != 0:
        reason = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "exit code"
        logger.warning("Fit step: the fit failed: %s", completed.stderr.strip() or completed.returncode)
        return f"Fitted settings: not applied ({reason})"
    logger.info(
        "Fit step: %s", completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else "done"
    )
    return None


def build_world(
    root: Path,
    environ: dict[str, str],
    controller_settings: Mapping[str, float] | None = None,
    backend_port: int | None = None,
    fitter: Callable[[Path, Path, dict[str, str], dict[str, str]], str | None] = run_fit_process,
) -> World:
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
    controller_settings : `~collections.abc.Mapping` [`str`, `float`], optional
        Guide controller settings for the real PHD2's profile (see
        `simulator.phd2_launch.CONTROLLER_SETTING_KEYS`). Default: the fit's
        (from the newest PHD2 run in the library copy), else PHD2's own.
    backend_port : `int`, optional
        The port the backend will listen on. The exposure driver uses it to
        deliver real frames. Default: none, so every frame is the
        simulator's.
    fitter : `~collections.abc.Callable`, optional
        ``fitter(root, config_path, variables, environ)`` runs the fit and
        gives a status note, or `None` when ``fit_results.json`` was written.
        Default: `run_fit_process`.

    Returns
    -------
    world : `World`
        ``simulated`` when everything started, otherwise ``offline`` with
        the reason in ``error``.
    """
    folders = real_folders(environ)
    guider, guider_port, notes = choose_guider(environ)
    indi_port, phd2_port = choose_free_port(), guider_port or choose_free_port()
    runner: SimulatedObservatory | None = None
    try:
        builder = SimulatedObservatory(
            root,
            SimulationKind.SIMULATED_SKY,
            indi_port,
            phd2_port,
            copy_library=True,
            real_library=folders,
            guider=guider,
            controller_settings=controller_settings,
        )
        builder.prepare()
        # Fit the simulators to the recorded nights on the library copy, while
        # nothing else uses it. A failure is a note; the defaults stay.
        results_path = root / RESULTS_FILE_NAME
        results_path.unlink(missing_ok=True)
        fit_note = fitter(root, builder.config_path, builder.environment(), environ)
        if fit_note is not None:
            notes.append(fit_note)
        fitted = results_path.is_file()
        # The world is built; the runner only starts the stand-ins in it.
        runner = SimulatedObservatory(
            root,
            SimulationKind.SIMULATED_SKY,
            indi_port,
            phd2_port,
            existing_world=True,
            guider=guider,
            controller_settings=controller_settings
            or (fitted_controller_settings(results_path) if fitted else None),
            backend_port=backend_port,
            fitted_settings_path=results_path if fitted else None,
            capture_by_app=True,
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
        guider=GuiderKind.PHD2.value if running.phd2 is not None else GuiderKind.MOCK.value,
        phd2=running.phd2,
        notes=[*notes, *running.notes],
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
    # Backstop: the library itself refuses online lookups and downloads.
    child[OFFLINE_VARIABLE] = "1"
    child[MISSING_PROGRAMS_VARIABLE] = ",".join(missing_programs())
    child[LAUNCH_NOTES_VARIABLE] = "\n".join([*launch_notes(), *world.notes])
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
    """Play the operator once the backend is ready.

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
    logger.info("OPERATOR STEP: the launcher plays the operator for the simulated devices.")
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
                logger.info("OPERATOR STEP: added the target %s.", name)
        except RuntimeError, OSError, ValueError:
            logger.warning("OPERATOR STEP: could not add the target %s.", name, exc_info=True)
    for method, parameters in calibration_calls:
        try:
            call(port, method, parameters)
            logger.info("OPERATOR STEP: %s recorded.", method)
        except RuntimeError, OSError, ValueError:
            logger.warning("OPERATOR STEP: %s failed.", method, exc_info=True)
    try:
        result = call(
            port,
            "observatory:enter_controller_mode",
            {"evidence_note": OPERATOR_EVIDENCE_NOTE, "shadow_first": True},
        )
    except (RuntimeError, OSError, ValueError) as error:
        logger.warning("OPERATOR STEP: the app could not take command of the simulator.", exc_info=True)
        return {"allowed": [], "off": {}, "error": f"{type(error).__name__}: {error}"}
    result = result if isinstance(result, dict) else {}
    allowed = sorted(result.get("applied", {}))
    off = {str(name): str(reason) for name, reason in result.get("rejected", {}).items()}
    logger.info("OPERATOR STEP: simulator commands are on for: %s.", ", ".join(allowed) or "nothing")
    if off:
        logger.warning(
            "OPERATOR STEP: simulator commands stay off for: %s.",
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
    results_path = world.root / RESULTS_FILE_NAME
    if results_path.is_file():
        command += ["--fit-results", str(results_path)]
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
                "OPERATOR STEP: the calibration values could not be worked out: %s",
                completed.stderr.strip() or f"exit code {completed.returncode}",
            )
            return []
        report = json.loads(completed.stdout.strip().splitlines()[-1])
    except OSError, ValueError, IndexError, subprocess.TimeoutExpired:
        logger.warning("OPERATOR STEP: the calibration values could not be worked out.", exc_info=True)
        return []
    logger.info(
        "OPERATOR STEP: guider calibration from %s; focus model from %s.",
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
    guiding_operator : `~collections.abc.Callable`, optional
        The guiding step for the real PHD2: ``guiding_operator(phd2,
        should_stop)`` gives a `GuidingOutcome`. Default: `run_guiding_step`.
    controller_settings : `~collections.abc.Mapping` [`str`, `float`], optional
        Guide controller settings for the real PHD2's profile (see
        `simulator.phd2_launch.CONTROLLER_SETTING_KEYS`). Default: PHD2's own.
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
        guiding_operator: Callable[..., GuidingOutcome] = run_guiding_step,
        controller_settings: Mapping[str, float] | None = None,
        ready_check: Callable[[int], bool] = backend_is_ready,
        connection_check: Callable[[int], list[str]] = non_loopback_connections,
        guard_interval: float = GUARD_INTERVAL_SECONDS,
    ) -> None:
        """Keep the settings; nothing starts yet."""
        self.environ = dict(os.environ if environ is None else environ)
        self.root = root or real_folders(self.environ).data_home / SIMULATOR_FOLDER_NAME
        self.port = port or int(self.environ.get("ASTROMETRICS_PORT", "5000"))
        self.backend_command = backend_command or [sys.executable, "-u", "-m", "backend.main_backend"]
        if world_builder is build_world:
            world_builder = partial(
                build_world, controller_settings=controller_settings, backend_port=self.port
            )
        self.world_builder = world_builder
        self.calibration_runner = calibration_runner
        self.operator = operator
        self.guiding_operator = guiding_operator
        self.guiding_outcome: GuidingOutcome | None = None
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
            if world.source == NO_TELESCOPE:
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
        failed_checks = 0
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
                    failed_checks = 0
                except OSError, subprocess.SubprocessError:
                    failed_checks += 1
                    logger.warning(
                        "The connection guard could not look at the connections (%d in a row).",
                        failed_checks,
                        exc_info=True,
                    )
                    if failed_checks >= GUARD_FAILURE_LIMIT:
                        logger.critical(
                            "CONNECTION GUARD: the check failed %d times in a row, so the guard is blind; "
                            "ending the simulator session.",
                            failed_checks,
                        )
                        self._end_backend(child)
                        return GUARD_EXIT_CODE
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
            logger.warning("OPERATOR STEP: the backend was not ready in time; no operator step.")
            return
        # These steps run in the launcher's own process, not in the backend
        # child, so they get the world's offline identity here: nothing below
        # may read the user's real settings.
        try:
            with offline_identity(world.config_path):
                self.operator_outcome = self.operator(self.port, calls, world.pointed_targets())
        except Exception:  # the app stays usable for watching; the log says what failed
            logger.exception("OPERATOR STEP failed")
        if world.phd2 is None:
            return
        # Guiding comes last: the settings are written, the app has taken
        # command and the mount tracks. It runs once; the app owns guiding
        # after that (for example after a slew).
        try:
            with offline_identity(world.config_path):
                self.guiding_outcome = self.guiding_operator(
                    world.phd2, lambda: child.poll() is not None or self.stop_requested.is_set()
                )
        except Exception:  # a status, not a crash: the app runs without guiding
            logger.warning("OPERATOR STEP: guiding failed.", exc_info=True)

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
