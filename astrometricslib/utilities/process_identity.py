"""Tell one running program from another, and tell whether one is still alive.

Every job the app records names the program that is running it, so that a job
left open by a program that crashed or was stopped can be recognised and
closed. A program's number (its process ID) alone is not enough: the
operating system reuses numbers, so a new, unrelated program can later get
the number of one that ended. The time the program started is kept with the
number, and only a program with both the same number and the same start time
counts as the same program.
"""

import logging

import psutil

logger = logging.getLogger(__name__)

__all__ = ["current_process_identity", "process_is_alive"]


def _start_time_text(process: psutil.Process) -> str:
    """Write down when a program started, in a form that can be compared.

    Returns
    -------
    started_at : `str`
        The start time, in seconds since 1970, to two decimal places.
    """
    return f"{process.create_time():.2f}"


def current_process_identity() -> tuple[int, str]:
    """Name the program that is running this code.

    Returns
    -------
    process_id : `int`
        The program's number.
    started_at : `str`
        When the program started, as written by `_start_time_text`.
    """
    process = psutil.Process()
    return process.pid, _start_time_text(process)


def process_is_alive(process_id: int, started_at: str) -> bool:
    """Say whether the program with this number and start time still runs.

    Parameters
    ----------
    process_id : `int`
        The program's number.
    started_at : `str`
        The start time that was recorded with the number.

    Returns
    -------
    alive : `bool`
        `True` if a program with this number exists and started at this time.
        A program that cannot be inspected counts as alive, so a job is never
        closed by mistake because of a permission problem.
    """
    try:
        process = psutil.Process(process_id)
        if process.status() == psutil.STATUS_ZOMBIE:
            return False
        return _start_time_text(process) == started_at
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True
    except psutil.Error as error:
        logger.debug("Could not inspect process %s: %s", process_id, error)
        return True
