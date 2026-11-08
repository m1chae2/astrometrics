"""Purpose: Play the operator for guiding in the simulated observatory.

Description: The operator step: until the app can command
PHD2 itself, the launcher plays the operator for one job, bringing the real
PHD2 to guiding. This module does that job once per session, after the app is
ready and the guide controller settings were written into PHD2's profile
(`simulator.phd2_launch`).

The steps, in order:

1. Open a command session on the PHD2 port the launcher started.
2. Check that this is the PHD2 the launcher started: its profile has the
   name the launcher wrote. A PHD2 with another profile is left alone.
3. Check PHD2 is idle (``Stopped``). If PHD2 is looping, calibrating or
   guiding, someone else (the app) already runs it, and the operator does
   nothing. This is also why the operator never guides again after the app
   slews the mount: it runs only once, and the app owns guiding afterwards.
4. Wait until the simulated mount is tracking and has stopped slewing.
5. Connect PHD2's equipment, loop, choose the guide star, and guide. PHD2
   calibrates first because it has no calibration yet. Wait for the settle.

Every outcome is a status in the log, never an exception: a PHD2 that is
missing, a mount that does not track, a field with no star and a calibration
that fails all end with a ``WARNING`` line and a `GuidingOutcome`. Each log
line starts with ``OPERATOR STEP``, as the launcher's other operator
lines do.

Safety: the module connects only to ``127.0.0.1`` (the PHD2 client refuses
other hosts when the launch is offline) and runs the INDI command line tools
against the world's own INDI port.
"""

import logging
import os
import subprocess
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from astrometricslib.drivers import OFFLINE_VARIABLE
from astrometricslib.exceptions import ExternalServiceError
from simulator.phd2_launch import MOUNT_DEVICE, Phd2Instance
from wayfindinglib.drivers import PHD2Client, PHD2Session

__all__ = [
    "CONFIG_VARIABLES",
    "GuidingOutcome",
    "mount_is_tracking",
    "offline_identity",
    "run_guiding_step",
]

logger = logging.getLogger("simulator.guiding_operator")

#: The environment variables that name the settings file in use.
CONFIG_VARIABLES = ("ASTROMETRICS_CONFIG", "ASTROMETRICS_CONFIG_PATH")

#: Seconds the operator waits for PHD2's port to accept a connection.
CONNECT_WAIT_SECONDS = 60.0
#: Seconds the operator waits for the mount to track and stop slewing.
TRACKING_WAIT_SECONDS = 180.0
#: Seconds PHD2 may take to connect its equipment.
EQUIPMENT_WAIT_SECONDS = 120.0
#: Seconds to wait for the first looping exposure.
LOOPING_WAIT_SECONDS = 30.0
#: Seconds of looping before the star is chosen, so the first frames are in.
LOOP_SETTLE_SECONDS = 3.0
#: Times the operator tries to find a guide star, and the seconds between.
FIND_STAR_ATTEMPTS = 3
FIND_STAR_RETRY_SECONDS = 5.0
#: Seconds the calibration and the first settle may take together.
GUIDE_WAIT_SECONDS = 900.0
#: The settle limits handed to PHD2: stay within this many pixels for this
#: many seconds, and give up after the timeout.
SETTLE_PIXELS = 1.5
SETTLE_SECONDS = 6.0
SETTLE_TIMEOUT_SECONDS = 300.0
#: Seconds between two looks at a state that is expected to change.
POLL_SECONDS = 1.0

#: The outcomes of the guiding step.
GUIDING = "guiding"
LEFT_ALONE = "left alone"
FAILED = "failed"


@dataclass(frozen=True)
class GuidingOutcome:
    """What the guiding step did.

    Attributes
    ----------
    status : `str`
        ``guiding`` (PHD2 settled and guides), ``left alone`` (PHD2 is not
        the one the launcher started, or is already busy) or ``failed``.
    detail : `str`
        One sentence that says why, for the log and the launch status.
    """

    status: str
    detail: str


@contextmanager
def offline_identity(config_path: Path | None = None) -> Generator[None]:
    """Run the enclosed code with the simulated world's offline identity.

    The PHD2 client checks that its host is this computer. That check asks
    whether the launch is offline, and if the environment does not say so it
    reads the user's settings file. The launcher process is not the backend
    child, so it does not get the child's environment. Inside this block the
    process says it is offline (``ASTROMETRICS_OFFLINE=1``) and, when
    `config_path` is given, that the world's settings are the settings in use,
    so the user's real settings are never read. The old values come back when
    the block ends, even after an error.

    Parameters
    ----------
    config_path : `pathlib.Path`, optional
        The world's settings file. When `None`, the settings variables are
        left as they are.

    Yields
    ------
    None
        Nothing; the environment is changed for the block.
    """
    changes = {OFFLINE_VARIABLE: "1"}
    if config_path is not None:
        changes.update(dict.fromkeys(CONFIG_VARIABLES, str(config_path)))
    saved = {name: os.environ.get(name) for name in changes}
    os.environ.update(changes)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def mount_is_tracking(indi_port: int, timeout_seconds: float = 20.0) -> bool:
    """Say whether the simulated mount tracks and is not slewing.

    Reads the world's INDI server with ``indi_getprop``.

    Parameters
    ----------
    indi_port : `int`
        The port of the world's INDI server.
    timeout_seconds : `float`, optional
        Longest the tool may take.

    Returns
    -------
    tracking : `bool`
        `True` when tracking is on and the coordinates property is not busy.
    """
    try:
        completed = subprocess.run(
            [
                "indi_getprop",
                "-p",
                str(indi_port),
                f"{MOUNT_DEVICE}.TELESCOPE_TRACK_STATE.TRACK_ON",
                f"{MOUNT_DEVICE}.EQUATORIAL_EOD_COORD._STATE",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except OSError, subprocess.SubprocessError:
        return False
    values = {}
    for line in completed.stdout.splitlines():
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip()
    return (
        values.get(f"{MOUNT_DEVICE}.TELESCOPE_TRACK_STATE.TRACK_ON") == "On"
        and values.get(f"{MOUNT_DEVICE}.EQUATORIAL_EOD_COORD._STATE") == "Ok"
    )


def _open_session(
    phd2: Phd2Instance,
    should_stop: Callable[[], bool],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> PHD2Session | None:
    """Open a command session, waiting for PHD2 to start listening.

    Parameters
    ----------
    phd2 : `Phd2Instance`
        The PHD2 the launcher started.
    should_stop : `~collections.abc.Callable`
        Says when the session is ending.
    sleep, clock : `~collections.abc.Callable`
        The pause and the time source (tests pass fakes).

    Returns
    -------
    session : `PHD2Session` or `None`
        The open session, or `None` when PHD2 never accepted a connection.
    """
    deadline = clock() + CONNECT_WAIT_SECONDS
    while not should_stop():
        try:
            return PHD2Client(host="127.0.0.1", port=phd2.event_port).open_session()
        except OSError:
            if clock() >= deadline:
                return None
            sleep(POLL_SECONDS)
    return None


def _wait_for_tracking(
    phd2: Phd2Instance,
    is_tracking: Callable[[int], bool],
    should_stop: Callable[[], bool],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> bool:
    """Wait until the simulated mount tracks and has stopped slewing.

    Parameters
    ----------
    phd2 : `Phd2Instance`
        The PHD2 the launcher started (its INDI port names the mount).
    is_tracking : `~collections.abc.Callable`
        ``is_tracking(indi_port)`` says whether the mount tracks.
    should_stop : `~collections.abc.Callable`
        Says when the session is ending.
    sleep, clock : `~collections.abc.Callable`
        The pause and the time source.

    Returns
    -------
    tracking : `bool`
        `True` when the mount tracks, `False` on a timeout or a stop.
    """
    deadline = clock() + TRACKING_WAIT_SECONDS
    while not should_stop():
        if is_tracking(phd2.indi_port):
            return True
        if clock() >= deadline:
            return False
        sleep(POLL_SECONDS)
    return False


def _bring_to_guiding(
    session: PHD2Session,
    phd2: Phd2Instance,
    should_stop: Callable[[], bool],
    sleep: Callable[[float], None],
) -> GuidingOutcome:
    """Connect, loop, choose a star and guide, on an open session.

    Parameters
    ----------
    session : `PHD2Session`
        The open command session.
    phd2 : `Phd2Instance`
        The PHD2 the launcher started.
    should_stop : `~collections.abc.Callable`
        Says when the session is ending.
    sleep : `~collections.abc.Callable`
        The pause.

    Returns
    -------
    outcome : `GuidingOutcome`
        ``guiding`` when PHD2 settled, else ``failed`` with the reason.
    """
    if not session.get_connected():
        session.set_connected(True, EQUIPMENT_WAIT_SECONDS)
    if session.get_exposure() != phd2.exposure_milliseconds:
        session.set_exposure(phd2.exposure_milliseconds)
    mark = session.event_count
    session.loop()
    if session.wait_for_event("LoopingExposures", mark, LOOPING_WAIT_SECONDS) is None:
        return GuidingOutcome(FAILED, "PHD2 did not start looping exposures.")
    sleep(LOOP_SETTLE_SECONDS)
    for attempt in range(FIND_STAR_ATTEMPTS):
        try:
            session.find_star()
            break
        except ExternalServiceError as error:
            if attempt == FIND_STAR_ATTEMPTS - 1:
                return GuidingOutcome(
                    FAILED,
                    f"PHD2 found no guide star ({error}). Does the simulated field have stars? "
                    "The star catalog (gsc) draws them.",
                )
            if should_stop():
                return GuidingOutcome(LEFT_ALONE, "The session ended before PHD2 found a star.")
            sleep(FIND_STAR_RETRY_SECONDS)
    mark = session.event_count
    session.guide(SETTLE_PIXELS, SETTLE_SECONDS, SETTLE_TIMEOUT_SECONDS)
    settled = session.wait_for_event("SettleDone", mark, GUIDE_WAIT_SECONDS)
    if settled is None:
        return GuidingOutcome(FAILED, "PHD2 did not finish calibrating and settling in time.")
    if settled.get("Status", 0) != 0:
        return GuidingOutcome(FAILED, f"PHD2 could not settle: {settled.get('Error', 'no reason given')}.")
    return GuidingOutcome(GUIDING, "PHD2 calibrated and settled; it guides.")


def run_guiding_step(
    phd2: Phd2Instance,
    should_stop: Callable[[], bool] = lambda: False,
    is_tracking: Callable[[int], bool] = mount_is_tracking,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> GuidingOutcome:
    """Bring the PHD2 the launcher started to guiding, once.

    Parameters
    ----------
    phd2 : `Phd2Instance`
        The PHD2 the launcher started.
    should_stop : `~collections.abc.Callable`, optional
        Says when the session is ending, so the step gives up at once.
    is_tracking : `~collections.abc.Callable`, optional
        ``is_tracking(indi_port)`` says whether the mount tracks. Tests pass
        a fake.
    sleep, clock : `~collections.abc.Callable`, optional
        The pause and the time source. Tests pass fakes.

    Returns
    -------
    outcome : `GuidingOutcome`
        What happened. Every failure is a status with a warning in the log;
        nothing is raised.
    """
    with offline_identity():
        return _run_guiding_step(phd2, should_stop, is_tracking, sleep, clock)


def _run_guiding_step(
    phd2: Phd2Instance,
    should_stop: Callable[[], bool],
    is_tracking: Callable[[int], bool],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> GuidingOutcome:
    """Do the work of `run_guiding_step` inside the offline identity.

    Parameters
    ----------
    phd2 : `Phd2Instance`
        The PHD2 the launcher started.
    should_stop, is_tracking, sleep, clock : `~collections.abc.Callable`
        As for `run_guiding_step`.

    Returns
    -------
    outcome : `GuidingOutcome`
        What happened.
    """
    logger.info(
        "OPERATOR STEP: the launcher plays the operator for guiding (PHD2 instance %d, port %d).",
        phd2.instance,
        phd2.event_port,
    )
    session = _open_session(phd2, should_stop, sleep, clock)
    if session is None:
        outcome = GuidingOutcome(FAILED, f"PHD2 did not answer on port {phd2.event_port}.")
        logger.warning("OPERATOR STEP: guiding not started. %s", outcome.detail)
        return outcome
    try:
        with session:
            profile = session.get_profile()
            if profile.get("name") != phd2.profile_name:
                outcome = GuidingOutcome(
                    LEFT_ALONE,
                    f"The program on port {phd2.event_port} is not the PHD2 started here "
                    f"(its profile is {profile.get('name')!r}, not {phd2.profile_name!r}).",
                )
            elif (state := session.get_app_state()) != "Stopped":
                outcome = GuidingOutcome(
                    LEFT_ALONE,
                    f"PHD2 is already {state}; the app owns guiding, so the operator does nothing.",
                )
            elif not _wait_for_tracking(phd2, is_tracking, should_stop, sleep, clock):
                outcome = GuidingOutcome(FAILED, "The simulated mount is not tracking.")
            else:
                outcome = _bring_to_guiding(session, phd2, should_stop, sleep)
    except (ExternalServiceError, OSError, ValueError) as error:
        outcome = GuidingOutcome(FAILED, f"{type(error).__name__}: {error}")
    if outcome.status == GUIDING:
        logger.info("OPERATOR STEP: %s", outcome.detail)
    else:
        logger.warning("OPERATOR STEP: guiding %s. %s", outcome.status, outcome.detail)
    return outcome
