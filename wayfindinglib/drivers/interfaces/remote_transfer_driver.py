"""Abstract base class for remote telescope file-transfer drivers.

A parallel abstraction from `ProtocolDriver` (`base_protocol_driver.py`),
not a subclass of it: pulling files off a telescope host has no
`connect()`/device-state notion the hardware-control protocols do --
it's a separate, independently pluggable concern from INDI or ASCOM
(`Wayfinding_Library_Architecture.md` §2.5's remote-transfer
abstraction). `StellarMateInterface` is the first implementation.
"""

import abc
from typing import Any


class RemoteTransferDriver(abc.ABC):
    """Abstract base for remote telescope file-transfer drivers."""

    @property
    @abc.abstractmethod
    def driver_name(self) -> str:
        """Short unique identifier used as a registry key.

        For example, ``"stellarmate"``.
        """

    @abc.abstractmethod
    def check_connection(self) -> bool:
        """Probe the remote host connection.

        Returns
        -------
        is_online : `bool`
            `True` if the remote host is currently reachable.
        """

    @abc.abstractmethod
    def list_remote_targets(self) -> list[str]:
        """List target folder directories on the remote host.

        Returns
        -------
        target_folder_names : `list` [`str`]
            Names of the remote target directories found.
        """

    @abc.abstractmethod
    def resolve_remote_folder_name(self, folder_name: str) -> str:
        """Return the actual remote directory name for a target name.

        Returns
        -------
        resolved_folder_name : `str`
            The matching remote directory name, or `folder_name`
            unchanged if no variant matches.
        """

    @abc.abstractmethod
    def list_remote_files(self, folder_name: str) -> list[str]:
        """List file relative paths under a remote target directory.

        Returns
        -------
        relative_file_paths : `list` [`str`]
            File paths relative to the target directory.
        """

    @abc.abstractmethod
    def list_remote_files_with_sizes(self, folder_name: str) -> list[tuple[str, int]]:
        """List remote file relative paths paired with byte sizes.

        Returns
        -------
        files_with_sizes : `list` [`tuple` [`str`, `int`]]
            ``(relative_path, size_in_bytes)`` for each file.
        """

    @abc.abstractmethod
    def download_target_folder(
        self,
        remote_target_name: str,
        local_dest_path: str,
        log_callback: Any | None = None,
        selected_files: list[str] | None = None,
    ) -> bool:
        """Download a remote target folder to a local destination.

        Returns
        -------
        succeeded : `bool`
            `True` if the download completed successfully.
        """
