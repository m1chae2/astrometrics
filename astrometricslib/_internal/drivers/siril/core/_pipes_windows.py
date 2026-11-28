r"""Purpose: The Windows way to give Siril its command and output pipes.

Description: Imported by `pipes` only on Windows. Windows named pipes are not
files in the work folder: they live under ``\\.\pipe\`` and Siril creates
them by name when it starts. So this program only chooses unique names, and
opening a pipe fails until Siril has made it, so opening is tried again. Each
function has the same name and inputs as its Linux version in `_pipes_posix`.
"""

import time
import uuid
from typing import Any

PIPE_PREFIX = r"\\.\pipe" + "\\"
"""Where Windows keeps named pipes; Siril creates them there by name."""

PIPE_RETRY_SECONDS = 0.05
"""Wait between tries to open a pipe Siril has not created yet."""

PIPE_CONNECT_TIMEOUT_SECONDS = 30
"""How long to keep trying when the caller gives no other limit."""


def create_pipes(base_path: str) -> tuple[str, str]:
    """Choose the names of the pipes Siril will create.

    A unique name keeps two Siril runs apart. Nothing is made on disk.

    Parameters
    ----------
    base_path : `str`
        Not used on Windows; kept so both versions take the same inputs.

    Returns
    -------
    command_pipe : `str`
        Name of the pipe Siril reads commands from.
    output_pipe : `str`
        Name of the pipe Siril writes status output to.
    """
    del base_path
    name = f"astrometrics_siril_{uuid.uuid4().hex}"
    return f"{PIPE_PREFIX}{name}.in", f"{PIPE_PREFIX}{name}.out"


def open_pipe(path: str, mode: str, keep_trying: Any = None) -> Any:
    """Open one end of a Siril pipe, trying again until Siril has made it.

    Parameters
    ----------
    path : `str`
        The pipe.
    mode : `str`
        ``"r"`` or ``"w"``.
    keep_trying : `~collections.abc.Callable`, optional
        Called between tries; return `False` to stop. Default: try for
        `PIPE_CONNECT_TIMEOUT_SECONDS`.

    Returns
    -------
    pipe : file object
        The open pipe.

    Raises
    ------
    OSError
        If the pipe cannot be opened once the tries end.
    """
    if keep_trying is None:
        deadline = time.monotonic() + PIPE_CONNECT_TIMEOUT_SECONDS

        def keep_trying() -> bool:
            """Say whether the connect time is not used up yet.

            Returns
            -------
            more : `bool`
                `True` until `PIPE_CONNECT_TIMEOUT_SECONDS` have passed.
            """
            return time.monotonic() < deadline

    while True:
        try:
            return open(path, mode)
        except OSError:
            if not keep_trying():
                raise
            time.sleep(PIPE_RETRY_SECONDS)


def stop_pipe_holders(work_directory: str) -> None:
    """Do nothing: Windows stops Siril's whole process tree with one call.

    Parameters
    ----------
    work_directory : `str`
        Not used on Windows; kept so both versions take the same inputs.
    """
    del work_directory
