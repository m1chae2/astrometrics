"""Advisory File-Lock Management for Observatory Hardware.

Provides process-safe locking for telescope mount, camera, and focuser
operations backed by on-disk lock files.
"""

import os

from datastore.disk_interface import file_lock


class HardwareLock:
    """Manages advisory file-locks for process-safe hardware operations.

    Covers telescope mount, camera, and focuser operations.
    """

    def __init__(self, config):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize the HardwareLock manager.

        Configured with the application configuration object.
        """
        self._config = config

    def get_lock_path(self, device_name: str) -> str:
        """Resolve the absolute path for a device lock file.

        Returns
        -------
        path : `str`
            Absolute path to the device's lock file, creating the
            containing ``locks`` directory if needed.
        """
        lock_dir = os.path.join(str(self._config.get_library_path()), "locks")
        os.makedirs(lock_dir, exist_ok=True)
        return os.path.join(lock_dir, f"{device_name}.lock")

    def acquire(self, device_name: str, blocking: bool = False):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Context manager helper to acquire a file lock for a device.

        The device is identified by its designated name (e.g. 'mount'). Pass
        blocking=True for calls expected to briefly overlap themselves (see
        file_lock's docstring); left False (fail-fast) by default so
        genuinely conflicting operations (e.g. slewing while parking) still
        raise immediately.

        Returns
        -------
        lock_context
            The context manager returned by `file_lock` for the
            device's lock file.
        """
        lock_path = self.get_lock_path(device_name)
        return file_lock(lock_path, blocking=blocking)
