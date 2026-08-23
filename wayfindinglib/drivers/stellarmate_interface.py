"""StellarMate remote telescope driver for Wayfinding.

Handles connection probes, target listing, recursive FITS file
discovery, and rsync-based downloads from StellarMate or SSH-enabled
telescopes.

Relocated here from astrometricslib per the cross-library litmus test
(`Wayfinding_Library_Architecture.md` Design Invariant 4): pulling files
off a telescope host requires a telescope to be present, so it belongs
in the observatory-control library rather than the science library.
"""

import logging
import os
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)


class StellarMateInterface:
    """Interface driver for the remote StellarMate telescope controller.

    Communicates over SSH and Rsync. Configured with a host alias and
    the telescope's remote pictures path.
    """

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        host_alias: str = "stellarmate",
        remote_pictures_path: str = "/home/stellarmate/Pictures",
        frames_path: str | None = None,
    ):
        """Initialize the StellarMate interface.

        Parameters
        ----------
        host_alias : `str`, optional
            SSH hostname or config host alias (e.g. ``"stellarmate"``).
        remote_pictures_path : `str`, optional
            Base directory on the remote telescope host.
        frames_path : `str`, optional
            Local frames directory base destination path.
        """
        self.host_alias = host_alias
        self.remote_pictures_path = remote_pictures_path
        self.frames_path = frames_path
        self._last_connection_status = None  # None=Unknown, True=Online, False=Offline
        self._last_probe_time = 0.0

    def _update_connection_status(self, is_online: bool) -> None:
        """Update the internal connection status and log state changes.

        Parameters
        ----------
        is_online : `bool`
            Whether the remote host is currently reachable.
        """
        if self._last_connection_status != is_online:
            if is_online:
                logger.info(f"Remote Host '{self.host_alias}' is now ONLINE.")
            else:
                logger.debug(f"Remote Host '{self.host_alias}' is now OFFLINE.")
            self._last_connection_status = is_online

    def _run_command(self, cmd_list: list[str]) -> str:
        """Run a shell command as a subprocess.

        Injects a ConnectTimeout for SSH commands and implements
        cooldown fail-fast checks if the host is known offline.

        Parameters
        ----------
        cmd_list : `list` [`str`]
            List of shell command tokens.

        Returns
        -------
        stdout : `str`
            Stripped standard output produced by the command.

        Raises
        ------
        RuntimeError
            Raised if the host is known offline within the cooldown
            window, or if the command itself fails.
        """
        if cmd_list and cmd_list[0] == "ssh":
            cmd_list = cmd_list.copy()
            cmd_list.insert(1, "-o")
            cmd_list.insert(2, "ConnectTimeout=2")

        now = time.time()
        if self._last_connection_status is False:
            if (now - self._last_probe_time) < 10.0:
                raise RuntimeError("SSH/Command Failed: Remote host is known offline (cooldown active).")

        self._last_probe_time = now

        try:
            result = subprocess.run(cmd_list, capture_output=True, text=True, check=True)
            self._update_connection_status(True)
            return result.stdout.strip()

        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.lower() if e.stderr else ""
            is_conn_error = (
                "could not resolve hostname" in err_msg
                or "timed out" in err_msg
                or "connection refused" in err_msg
            )

            if is_conn_error:
                if self._last_connection_status is not False:
                    logger.debug(f"SSH Connection Failed: {e.stderr.strip()}")
                self._update_connection_status(False)
            else:
                logger.error(f"Command failed: {cmd_list}")
                logger.error(f"Stderr: {e.stderr}")
                self._update_connection_status(True)

            raise RuntimeError(f"SSH/Command Failed: {e.stderr}") from e

    def check_connection(self) -> bool:
        """Probe the host connection to check if the telescope is online.

        Returns
        -------
        is_online : `bool`
            `True` if the SSH probe succeeded; `False` otherwise.
        """
        try:
            self._run_command(["ssh", self.host_alias, "echo", "connected"])
            return True
        except Exception:
            return False

    def list_remote_targets(self) -> list[str]:
        """List target folder directories under the remote Pictures path.

        Returns
        -------
        target_folder_names : `list` [`str`]
            Names of target directories found, or an empty list if
            the listing command fails.
        """
        try:
            cmd = f"ls -F {self.remote_pictures_path} | grep '/$'"
            output = self._run_command(["ssh", self.host_alias, cmd])
            dirs = [d.rstrip("/") for d in output.split("\n") if d.strip()]
            return dirs
        except Exception as e:
            if self._last_connection_status is not False:
                logger.error(f"Failed to list remote targets: {e}")
            return []

    def resolve_remote_folder_name(self, folder_name: str) -> str:
        """Return the actual remote directory name for a target name.

        Public entry point for callers that must know where a download
        will actually land: `download_target_folder` resolves
        space/underscore naming mismatches internally, so a caller that
        derives its own post-download path from the *unresolved* name
        would look in the wrong directory.

        Returns
        -------
        resolved_folder_name : `str`
            The matching remote directory name, or `folder_name`
            unchanged if no space/underscore variant matches.
        """
        return self._resolve_remote_folder_name(folder_name)

    def _resolve_remote_folder_name(self, folder_name: str) -> str:
        """Resolve the actual remote folder name for a target.

        Handles space/underscore naming mismatches between the local
        target id and the remote directory name.

        Returns
        -------
        resolved_folder_name : `str`
            The matching remote directory name, or `folder_name`
            unchanged if no space/underscore variant matches.
        """
        remote_dirs = self.list_remote_targets()
        if folder_name not in remote_dirs:
            alt_name = folder_name.replace(" ", "_")
            if alt_name in remote_dirs:
                return alt_name
            alt_name2 = folder_name.replace("_", " ")
            if alt_name2 in remote_dirs:
                return alt_name2
        return folder_name

    def list_remote_files(self, folder_name: str) -> list[str]:
        """List all FITS file relative paths under a remote target directory.

        Parameters
        ----------
        folder_name : `str`
            Name of the remote directory corresponding to the target.

        Returns
        -------
        relative_file_paths : `list` [`str`]
            FITS file paths relative to the target directory, or an
            empty list if the listing command fails.
        """
        try:
            folder_name = self._resolve_remote_folder_name(folder_name)
            remote_path = f"{self.remote_pictures_path}/{folder_name}"
            cmd = (
                f"find '{remote_path}' -type f \\( -name '*.fits' -o -name '*.fit' \\) -printf '%P\\n' | sort"
            )
            output = self._run_command(["ssh", self.host_alias, cmd])
            files = [f.strip() for f in output.split("\n") if f.strip()]
            return files
        except Exception as e:
            if self._last_connection_status is not False:
                logger.error(f"Failed to list files in {folder_name}: {e}")
            return []

    def list_remote_files_with_sizes(self, folder_name: str) -> list[tuple[str, int]]:
        """List remote FITS file relative paths paired with byte sizes.

        Size is what lets a caller distinguish two genuinely different
        frames that happen to share a filename -- which does occur in
        practice, where separate sessions reuse a capture-order naming
        pattern. Matching on filename alone would treat the second one
        as already held and silently skip transferring it.

        Parameters
        ----------
        folder_name : `str`
            Name of the remote directory corresponding to the target.

        Returns
        -------
        files_with_sizes : `list` [`tuple` [`str`, `int`]]
            ``(relative_path, size_in_bytes)`` for each FITS file, or
            an empty list if the listing command fails.
        """
        try:
            folder_name = self._resolve_remote_folder_name(folder_name)
            remote_path = f"{self.remote_pictures_path}/{folder_name}"
            cmd = (
                f"find '{remote_path}' -type f \\( -name '*.fits' -o -name '*.fit' \\) "
                f"-printf '%s\\t%P\\n' | sort -k2"
            )
            output = self._run_command(["ssh", self.host_alias, cmd])
            files_with_sizes = []
            for line in output.split("\n"):
                if "\t" not in line:
                    continue
                size_text, _, relative_path = line.partition("\t")
                if relative_path.strip() and size_text.strip().isdigit():
                    files_with_sizes.append((relative_path.strip(), int(size_text.strip())))
            return files_with_sizes
        except Exception as e:
            if self._last_connection_status is not False:
                logger.error(f"Failed to list files with sizes in {folder_name}: {e}")
            return []

    def get_remote_folder_count(self, folder_name: str) -> int:
        """Count the FITS frames inside a remote target folder.

        Parameters
        ----------
        folder_name : `str`
            Name of the remote directory.

        Returns
        -------
        file_count : `int`
            Number of FITS frames found, or `0` if the count command
            fails.
        """
        try:
            folder_name = self._resolve_remote_folder_name(folder_name)
            remote_path = f"{self.remote_pictures_path}/{folder_name}"
            cmd = f"find '{remote_path}' -type f \\( -name '*.fits' -o -name '*.fit' \\) | wc -l"
            output = self._run_command(["ssh", self.host_alias, cmd])
            return int(output)
        except Exception as e:
            if self._last_connection_status is not False:
                logger.error(f"Failed to count files in {folder_name}: {e}")
            return 0

    def download_target_folder(
        self,
        remote_target_name: str,
        local_dest_path: str,
        log_callback: Any | None = None,
        selected_files: list[str] | None = None,
    ) -> bool:
        """Download files via rsync.

        Deliberately rsync-only, with no SCP (or other tool) fallback:
        a partial rsync run can leave its own temp/partial-file
        artifacts behind (e.g. rsync's dotfile-prefixed in-progress
        files), and a second transfer tool re-fetching into the same
        destination has no way to know about or reconcile those --
        risking leftover/duplicate files alongside the complete ones.
        A failed rsync run is reported as `False` so the caller can
        retry the same, single, well-understood transfer path.

        Parameters
        ----------
        remote_target_name : `str`
            Target directory name on StellarMate.
        local_dest_path : `str`
            Base directory where local light files are structured.
        log_callback : callable, optional
            Callable receiver for progress logging.
        selected_files : `list` [`str`], optional
            File filter list of specific filenames to fetch.

        Returns
        -------
        succeeded : `bool`
            `True` if the rsync download completed successfully,
            `False` otherwise.

        Raises
        ------
        ValueError
            Raised if no host alias is configured.
        """
        if not self.host_alias:
            raise ValueError("No host alias configured for remote service.")

        remote_target_name = self._resolve_remote_folder_name(remote_target_name)
        remote_path = f"{self.remote_pictures_path}/{remote_target_name}"
        if not remote_path.endswith("/"):
            remote_path += "/"

        os.makedirs(local_dest_path, exist_ok=True)
        local_target_path = os.path.join(local_dest_path, remote_target_name)
        os.makedirs(local_target_path, exist_ok=True)

        files_from_path = None
        if selected_files:
            import tempfile

            files_from_fd = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
            for fname in selected_files:
                files_from_fd.write(fname + "\n")
            files_from_fd.close()
            files_from_path = files_from_fd.name

            rsync_cmd = [
                "rsync",
                "-avz",
                "--no-p",
                "--no-g",
                "--no-o",
                "-s",
                "--files-from",
                files_from_path,
                f"{self.host_alias}:{remote_path}",
                local_target_path,
            ]
        else:
            rsync_cmd = [
                "rsync",
                "-avz",
                "--no-p",
                "--no-g",
                "--no-o",
                f"{self.host_alias}:{remote_path}",
                local_target_path,
            ]

        logger.info(f"Starting rsync download: {self.host_alias}:{remote_path} -> {local_target_path}")
        if log_callback:
            log_callback(f"Starting robust download for {remote_target_name}...")

        try:
            process = subprocess.Popen(
                rsync_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue

                if line.endswith((".fits", ".fit", ".jpg", ".png")):
                    if log_callback:
                        log_callback(f"Downloading: {line}")

                logger.debug(f"rsync: {line}")

            return_code = process.wait()
            if files_from_path:
                try:
                    os.remove(files_from_path)
                except OSError:
                    pass
            if return_code == 0:
                logger.info(f"rsync completed successfully for {remote_target_name}")
                return True
            else:
                logger.error(f"rsync failed with code {return_code} for {remote_target_name}.")
                return False
        except Exception as e:
            if files_from_path:
                try:
                    os.remove(files_from_path)
                except OSError:
                    pass
            logger.error(f"rsync execution error for {remote_target_name}: {e}")
            return False
