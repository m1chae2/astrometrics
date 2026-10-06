"""Clean up after jobs whose program ended before they finished.

A job is closed by the program that runs it. When that program is killed or
crashes, the job stays open in the job list, and a restack that was part way
through leaves the old stack parked in a staging folder. This module closes
such jobs and puts such stacks back.

It runs when the backend or the MCP server starts, and again before each new
job is recorded, so a stuck job does not stay in the list until someone
notices it.
"""

import logging
import os
import sqlite3
from collections.abc import Callable
from typing import Any

from astrometricslib.drivers.logger_interface import LoggerInterface
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.pipelines.stacking.post_processing.previous_stack import (
    rollback_archive,
    staging_folder_stack_path,
)
from astrometricslib.utilities.pipeline_models import ProcessingJob
from astrometricslib.utilities.process_identity import process_is_alive

logger = logging.getLogger(__name__)

__all__ = ["close_interrupted_jobs", "recover_interrupted_jobs", "restore_orphaned_stack_staging"]


def restore_orphaned_stack_staging(target_folder: str) -> list[str]:
    """Put back the old stacks that a restack left in staging folders.

    A restack parks the old stack in a `_previous.staging.<name>` folder until
    the new one exists. If the program ended in between, the target would be
    left with no stack. This moves the parked files back, as a failed restack
    does (see `rollback_archive`).

    Parameters
    ----------
    target_folder : `str`
        The folder that holds the target's stacks.

    Returns
    -------
    restored : `list` [`str`]
        The staging folders that were emptied back into the target's folder.
    """
    restored = []
    if not os.path.isdir(target_folder):
        return restored
    for name in sorted(os.listdir(target_folder)):
        staging = os.path.join(target_folder, name)
        stack_path = staging_folder_stack_path(staging)
        if stack_path is None or not os.path.isdir(staging):
            continue
        rollback_archive(stack_path, staging)
        restored.append(staging)
    return restored


def recover_interrupted_jobs(
    logs_database_path: str,
    stacks_path: str,
    is_process_alive: Callable[[int, str], bool] = process_is_alive,
) -> list[ProcessingJob]:
    """Close the jobs a program left open, and restore the stacks they parked.

    A target's staging folder is restored only if no job for that target is
    still open, so a restack that another program is running right now keeps
    its staging folder.

    Parameters
    ----------
    logs_database_path : `str`
        Path of the logs database that holds the job list.
    stacks_path : `str`
        The library's stacks folder.
    is_process_alive : `Callable`, optional
        Says whether a program, given its number and start time, still runs.
        Tests pass a stand-in.

    Returns
    -------
    interrupted : `list` [`ProcessingJob`]
        The jobs that were closed.
    """
    logger_interface = LoggerInterface(logs_database_path)
    interrupted = logger_interface.interrupt_orphaned_jobs(is_process_alive)
    for job in interrupted:
        logger.warning(
            "Closed %s job %s for '%s': the program running it ended before it finished.",
            job.job_type,
            job.id,
            job.target_id,
        )
    for target_id in sorted({job.target_id for job in interrupted if job.job_type == "stacking"}):
        still_open = [
            other
            for other in logger_interface.get_jobs_by_target(target_id, job_type="stacking")
            if other.status in ("started", "running")
        ]
        if still_open:
            continue
        target_folder = os.path.join(stacks_path, "lights", target_id)
        for staging in restore_orphaned_stack_staging(target_folder):
            logger.warning("Put back the old stack of '%s' from %s.", target_id, staging)
    return interrupted


def close_interrupted_jobs(configuration: Any | None = None) -> list[ProcessingJob]:
    """Close the jobs a program left open, using the app's own settings.

    This is what the backend and the MCP server call when they start. It
    never raises: tidying the job list must not stop a program from starting
    or new work from running.

    Parameters
    ----------
    configuration : `AppConfiguration`, optional
        Where the logs database and the stacks folder are found. Defaults to
        the app's configuration.

    Returns
    -------
    interrupted : `list` [`ProcessingJob`]
        The jobs that were closed. Empty if there were none or the job list
        could not be read.
    """
    try:
        if configuration is None:
            from astrometricslib.foundation.config import get_configuration

            configuration = get_configuration()
        return recover_interrupted_jobs(
            str(configuration.get_logs_db_path()), str(configuration.get_stacks_path())
        )
    except (AstrometricsError, sqlite3.Error, OSError) as recovery_error:
        logger.warning("Could not close interrupted jobs: %s", recovery_error)
        return []
