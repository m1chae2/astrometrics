"""Purpose: Run one short Siril script to the end, as every short run does.

Description: The preview steps (stretch and save, colour calibration,
StarNet) and the library scripts each give Siril a short list of commands.
`run_siril_script` writes them to a script file in the folder Siril works in,
starts Siril with the private settings file (`settings`), waits for it to
finish and removes the script. It keeps what Siril printed, so a step can read
a warning that Siril logs while still exiting with success
(`last_siril_output`, read with the parsers in `output_parsing`).

The lock policy is the same for every Siril run: the run first waits for a
free Siril slot (`lock.siril_process_lock`), and the wait is not counted in
its timeout. Only a short run that must never wait behind a stack (mount
centering, later) passes ``wait_for_slot=False``.

The run is registered with the run it belongs to
(`astrometricslib.foundation.run_abandonment.run_registered`), so giving up on
a stack stops its Siril too, and a run that takes longer than its timeout is
stopped.
"""

import contextlib
import contextvars
import logging
import os

from astrometricslib._internal.drivers.siril.core.lock import siril_process_lock
from astrometricslib._internal.drivers.siril.core.program import script_arguments
from astrometricslib._internal.drivers.siril.core.settings import prepare_private_settings
from astrometricslib.foundation.run_abandonment import run_registered

logger = logging.getLogger(__name__)


# The longest a single short Siril script may run, in seconds. The M 13
# preview script took 0.7 seconds once Siril had started, so two minutes
# leaves room for Siril's start-up and for a much larger stack, and still stops
# a hung process. The wait for a free Siril slot is not counted: the lock is
# taken before the clock starts.
DEFAULT_SCRIPT_TIMEOUT_SECONDS = 120


# The text Siril printed in the last script this thread ran: the standard
# output, and the standard output and error together.
_last_siril_output: contextvars.ContextVar[str] = contextvars.ContextVar("last_siril_output", default="")
_last_siril_messages: contextvars.ContextVar[str] = contextvars.ContextVar("last_siril_messages", default="")


def run_siril_script(
    directory: str,
    commands: list[str],
    siril_executable: str,
    timeout_seconds: float = DEFAULT_SCRIPT_TIMEOUT_SECONDS,
    wait_for_slot: bool = True,
    offline: bool = False,
    script_name: str = "stack_preview",
) -> bool:
    """Run a short Siril script in a folder.

    Parameters
    ----------
    directory : `str`
        Folder to start Siril in (its ``-d`` option). The script file is
        written here and removed afterwards.
    commands : `list` [`str`]
        The Siril commands to run, in order, as one of the driver's script
        builders writes them.
    siril_executable : `str`
        The command that starts Siril, as in the configuration (see
        `program`).
    timeout_seconds : `float`, optional
        The longest the script may run once Siril has started. Default
        `DEFAULT_SCRIPT_TIMEOUT_SECONDS`.
    wait_for_slot : `bool`, optional
        Whether to wait for a free Siril slot first. Default `True`. Only a
        short run that must never wait behind a stack passes `False`.
    offline : `bool`, optional
        Whether Siril starts in offline mode (``-o``). Default `False`.
    script_name : `str`, optional
        The start of the script file's name; the process number is added.
        Default ``"stack_preview"``.

    Returns
    -------
    succeeded : `bool`
        `True` if Siril exited without an error code.

    Notes
    -----
    `OSError` comes back to the caller if Siril cannot be started, and
    `subprocess.TimeoutExpired` if the script ran longer than
    ``timeout_seconds``.
    """
    script_path = os.path.join(directory, f"{script_name}{os.getpid()}.ssf")
    with open(script_path, "w", encoding="utf-8") as script_file:
        script_file.write("\n".join(commands) + "\n")
    try:
        settings_file = prepare_private_settings(siril_executable)
        arguments = script_arguments(siril_executable, directory, script_path, settings_file, offline)
        slot = siril_process_lock() if wait_for_slot else contextlib.nullcontext()
        with slot:
            # Stoppable: giving up on the run stops this Siril too.
            completed = run_registered(arguments, timeout_seconds)
    finally:
        os.remove(script_path)
    output = getattr(completed, "stdout", None) or ""
    errors = getattr(completed, "stderr", None) or ""
    _last_siril_output.set(output)
    _last_siril_messages.set(output + errors)
    if completed.returncode != 0:
        logger.debug(
            "Siril exited with code %s; the end of its output: %s",
            completed.returncode,
            (output + errors)[-2000:],
        )
    return completed.returncode == 0


def last_siril_output(include_errors: bool = False) -> str:
    """Give the text Siril printed in the last script this thread ran.

    Parameters
    ----------
    include_errors : `bool`, optional
        Whether to add what Siril printed on its error stream after its
        normal output. Default `False`.

    Returns
    -------
    output : `str`
        The output of the last `run_siril_script` call in this thread, or an
        empty string if there was none.
    """
    return _last_siril_messages.get() if include_errors else _last_siril_output.get()
