"""Put back the stacks that interrupted restacks left parked.

A restack parks the old stack in a staging folder until the new one exists.
When the program running it is killed or crashes, the job framework
(`astrometricslib.foundation.jobs`) closes the job, and the cleanup here puts
the parked stack back. The package root registers `restore_interrupted_stacks`
with `register_interrupted_job_cleanup`, so it runs whenever
`close_interrupted_jobs` closes a stacking job.
"""

import logging
import os
from typing import Any

from astrometricslib.foundation.jobs import JobStore, ProcessingJob
from astrometricslib.pipelines.stacking.post_processing.previous_stack import (
    rollback_archive,
    staging_folder_stack_path,
)

logger = logging.getLogger(__name__)

__all__ = ["restore_interrupted_stacks", "restore_orphaned_stack_staging"]


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


def restore_interrupted_stacks(
    interrupted: list[ProcessingJob], job_store: JobStore, configuration: Any
) -> None:
    """Restore the stacks that closed stacking jobs left parked.

    A target's staging folder is restored only if no stacking job for that
    target is still open, so a restack that another program is running right
    now keeps its staging folder.

    Parameters
    ----------
    interrupted : `list` [`ProcessingJob`]
        The jobs that were just closed.
    job_store : `JobStore`
        The job list, used to find stacking jobs that are still open.
    configuration : `AppConfiguration`
        Where the library's stacks folder is found.
    """
    stacking_targets = sorted({job.target_id for job in interrupted if job.job_type == "stacking"})
    if not stacking_targets:
        return
    stacks_path = str(configuration.get_stacks_path())
    for target_id in stacking_targets:
        still_open = [
            other
            for other in job_store.get_jobs_by_target(target_id, job_type="stacking")
            if other.status in ("started", "running")
        ]
        if still_open:
            continue
        target_folder = os.path.join(stacks_path, "lights", target_id)
        for staging in restore_orphaned_stack_staging(target_folder):
            logger.warning("Put back the old stack of '%s' from %s.", target_id, staging)
