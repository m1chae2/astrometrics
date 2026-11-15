"""Back up the library databases to the backup folder while the app runs.

When the settings name a backup folder (``backup_path`` in
``[Image Library]``, for example a Google Drive folder), this service runs the
library's backup script a few minutes after start, then every few hours, and
once more when the backend shuts down normally. The script makes a safe copy
of each database on the local disk and then moves it into the backup folder;
it skips the run when nothing changed. The databases only change while the
app runs, so no system timer is needed. Without ``backup_path`` the service
does nothing (on Linux a systemd timer runs the same script instead).

The script runs as its own process, the same way the Linux timer runs it, so
a failed backup cannot disturb the backend. A failed backup is logged as a
warning: the library itself is unharmed and the next run tries again.
"""

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from backend.services.infrastructure.background_logging import background_log_context

logger = logging.getLogger(__name__)

BACKUP_MODULE = "astrometricslib.scripts.backup_library_databases"
"""The script that makes one backup."""

FIRST_BACKUP_DELAY_SECONDS = 10 * 60
"""Wait after start before the first backup, so start-up work goes first.

Chosen by reasoning, not measured: short enough that a session of an hour
or two still gets a backup, long enough not to slow down start-up.
"""

BACKUP_INTERVAL_SECONDS = 4 * 60 * 60
"""Time between backups; the same as the Linux timer (OnUnitActiveSec=4h)."""

BACKUP_TIMEOUT_SECONDS = 15 * 60
"""Longest one backup may take before it is stopped.

Chosen by reasoning, not measured: the script notes a snapshot of a large
library is about 1.5 GB, which a local disk copies well within this time.
"""

SHUTDOWN_BACKUP_TIMEOUT_SECONDS = 120
"""Longest the backup at shutdown may delay the backend's exit."""


class LibraryBackupService:
    """Run the library backup script on a schedule while the backend runs.

    Parameters
    ----------
    config_service : `Any`
        The application configuration. It is read before each run, so a
        change to ``backup_path`` takes effect without a restart.
    """

    def __init__(self, config_service: Any) -> None:
        self._config_service = config_service
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._run_lock = threading.Lock()

    def start(self) -> None:
        """Start the background thread that runs the backups."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="LibraryBackupThread")
        self._thread.start()

    def stop(self) -> None:
        """Stop the thread, then make one last backup if one is set up."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.run_backup(timeout_seconds=SHUTDOWN_BACKUP_TIMEOUT_SECONDS)

    def _run_loop(self) -> None:
        """Wait, back up, and repeat until stopped."""
        if self._stop_event.wait(FIRST_BACKUP_DELAY_SECONDS):
            return
        while not self._stop_event.is_set():
            try:
                with background_log_context("library-backup"):
                    self.run_backup()
            except Exception:
                # This loop runs for the life of the app and must not die.
                logger.exception("Error while running the library backup")
            self._stop_event.wait(BACKUP_INTERVAL_SECONDS)

    def _backup_folder(self) -> Path | None:
        """Read the backup folder from the settings.

        Returns
        -------
        backup_path : `pathlib.Path` or `None`
            The folder, or `None` when no backup folder is set.
        """
        try:
            return self._config_service.get_backup_path()
        except Exception:  # a broken setting must not stop the backend
            logger.warning("Could not read backup_path from the settings", exc_info=True)
            return None

    def run_backup(self, timeout_seconds: float = BACKUP_TIMEOUT_SECONDS) -> bool:
        """Run the backup script once, if a backup folder is set.

        Parameters
        ----------
        timeout_seconds : `float`, optional
            Stop the script if it runs longer than this.

        Returns
        -------
        ran : `bool`
            `True` if the script ran and succeeded (including "nothing
            changed"). `False` if no backup folder is set, the app cannot run
            the script, or the backup failed.
        """
        backup_folder = self._backup_folder()
        if backup_folder is None:
            return False
        if getattr(sys, "frozen", False):
            # A packaged backend has no Python to run the script with.
            logger.info("Scheduled backups are not available in the packaged app; backup_path is ignored")
            return False
        if not self._run_lock.acquire(blocking=False):
            return False
        try:
            project_root = str(self._config_service.get_project_root())
            environment = {**os.environ, "PYTHONPATH": project_root}
            completed = subprocess.run(
                [sys.executable, "-m", BACKUP_MODULE],
                cwd=project_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning("The library backup to %s took too long and was stopped", backup_folder)
            return False
        except OSError as error:
            logger.warning("Could not start the library backup: %s", error)
            return False
        finally:
            self._run_lock.release()
        output = (completed.stderr or completed.stdout or "").strip()
        if completed.returncode != 0:
            # The library is unharmed and the next run tries again: a warning.
            logger.warning("The library backup to %s failed: %s", backup_folder, output[-2000:])
            return False
        logger.info(
            "Library backup checked (%s): %s", backup_folder, output.splitlines()[-1] if output else "done"
        )
        return True
