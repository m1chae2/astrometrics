"""StellarMate remote telescope driver for Wayfinding.

Handles connection probes, target listing, recursive FITS file
discovery, and rsync-based downloads from StellarMate or SSH-enabled
telescopes.

Relocated here from astrometricslib per the cross-library litmus test
(`Wayfinding_Library_Architecture.md` Design Invariant 4): pulling files
off a telescope host requires a telescope to be present, so it belongs
in the observatory-control library rather than the science library.

The first implementation of `RemoteTransferDriver`
(`wayfindinglib/drivers/protocols/remote_transfer_driver.py`) -- a
separate, pluggable abstraction from the hardware-control protocol
drivers, since retrieving files from a telescope host is not part of
INDI or ASCOM.
"""

import logging
import os
import subprocess
import threading
import time
from typing import Any

from astrometricslib import ConfigurationError, ExternalServiceError, require_mounted_storage
from wayfindinglib.drivers.protocols.remote_transfer_driver import RemoteTransferDriver

logger = logging.getLogger(__name__)

_RSYNC_BASE_OPTIONS = ("-avz", "--no-p", "--no-g", "--no-o", "-s")
"""Options every rsync transfer from the telescope computer uses."""

_RSYNC_IDLE_TIMEOUT_SECONDS = 60
"""Give up a log transfer that moves no data for this long."""

_OFFLINE_REPROBE_SECONDS = 10.0
"""While the host is offline, recheck in the background this often."""


class StellarMateInterface(RemoteTransferDriver):
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
        self._first_probe_lock = threading.Lock()
        self._background_probe_running = False

    @property
    def driver_name(self) -> str:
        """Registry key for this driver."""
        return "stellarmate"

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

        Injects a ConnectTimeout for SSH commands. While the host is known
        to be offline, a call fails at once instead of waiting: looking up
        a ``.local`` name that does not answer takes about 5 seconds, and
        every screen that asks about the telescope used to wait that long.
        A background check finds out when the host comes back. The very
        first call, when nothing is known yet, does the check itself; other
        calls made at the same moment wait for it and share the answer.

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
        ExternalServiceError
            Raised if the host is known offline, or if the command itself
            fails.
        """
        if self._last_connection_status is None:
            with self._first_probe_lock:
                if self._last_connection_status is None:
                    return self._execute_command(cmd_list)

        if self._last_connection_status is False:
            if (time.time() - self._last_probe_time) >= _OFFLINE_REPROBE_SECONDS:
                self._start_background_probe()
            raise ExternalServiceError("SSH/Command Failed: Remote host is known offline (cooldown active).")

        return self._execute_command(cmd_list)

    def _start_background_probe(self) -> None:
        """Check in a background thread whether the offline host is back.

        Only one check runs at a time. The result updates the connection
        status, so later calls go through (or keep failing fast).
        """
        if self._background_probe_running:
            return
        self._background_probe_running = True
        self._last_probe_time = time.time()

        def probe() -> None:
            """Run one connection check and record the answer."""
            try:
                self._execute_command(["ssh", self.host_alias, "echo", "connected"])
            except ExternalServiceError:
                pass  # _execute_command already recorded the host as offline
            finally:
                self._background_probe_running = False

        threading.Thread(target=probe, daemon=True, name="StellarMateProbe").start()

    def _execute_command(self, cmd_list: list[str]) -> str:
        """Run the command now and record whether the host answered.

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
        ExternalServiceError
            Raised if the command fails.
        """
        if cmd_list and cmd_list[0] == "ssh":
            cmd_list = cmd_list.copy()
            cmd_list.insert(1, "-o")
            cmd_list.insert(2, "ConnectTimeout=2")

        self._last_probe_time = time.time()

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
                or "no route to host" in err_msg
            )

            if is_conn_error:
                if self._last_connection_status is not False:
                    logger.debug(f"SSH Connection Failed: {e.stderr.strip()}")
                self._update_connection_status(False)
            else:
                logger.error(f"Command failed: {cmd_list}")
                logger.error(f"Stderr: {e.stderr}")
                self._update_connection_status(True)

            raise ExternalServiceError(f"SSH/Command Failed: {e.stderr}") from e

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
        ConfigurationError
            Raised if no host alias is configured.
        """
        if not self.host_alias:
            raise ConfigurationError("No host alias configured for remote service.")

        remote_target_name = self._resolve_remote_folder_name(remote_target_name)
        remote_path = f"{self.remote_pictures_path}/{remote_target_name}"
        if not remote_path.endswith("/"):
            remote_path += "/"

        # Refuse before creating folders: with the frames drive missing, they
        # would be made on the computer's own disk.
        require_mounted_storage(local_dest_path)
        os.makedirs(local_dest_path, exist_ok=True)
        local_target_path = os.path.join(local_dest_path, remote_target_name)
        os.makedirs(local_target_path, exist_ok=True)

        files_from_path = None
        extra_options: list[str] = []
        if selected_files:
            import tempfile

            files_from_fd = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
            for fname in selected_files:
                files_from_fd.write(fname + "\n")
            files_from_fd.close()
            files_from_path = files_from_fd.name
            extra_options = ["--files-from", files_from_path]

        if log_callback:
            log_callback(f"Starting download for {remote_target_name}...")

        try:
            return self._run_rsync(
                remote_path,
                local_target_path,
                extra_options,
                remote_target_name,
                log_callback,
            )
        finally:
            if files_from_path:
                try:
                    os.remove(files_from_path)
                except OSError:
                    pass

    def _run_rsync(
        self,
        remote_path: str,
        local_path: str,
        extra_options: list[str],
        description: str,
        log_callback: Any | None = None,
    ) -> bool:
        """Run one rsync transfer from the telescope computer.

        The one place rsync is invoked, shared by the image downloads and
        the log downloads so they cannot drift apart. The options every
        transfer needs are fixed here: archive mode with compression, no
        permission/group/owner copying (the two computers have different
        users), and ``-s`` (protect-args) so a remote path containing a
        space, like ``NGC 7023``, is not split into two words.

        Parameters
        ----------
        remote_path : `str`
            Absolute path on the telescope computer. End it with ``/`` to
            copy a folder's contents.
        local_path : `str`
            Local destination.
        extra_options : `list` [`str`]
            Options specific to this transfer, such as ``--files-from`` or
            ``--include`` filters.
        description : `str`
            What is being transferred, for log messages.
        log_callback : callable, optional
            Called with a message for each image file transferred.

        Returns
        -------
        succeeded : `bool`
            `True` if rsync exited with status 0. A failure is logged and
            reported as `False`; it is never retried with another tool
            (see `download_target_folder`).
        """
        rsync_cmd = [
            "rsync",
            *_RSYNC_BASE_OPTIONS,
            *extra_options,
            f"{self.host_alias}:{remote_path}",
            local_path,
        ]
        logger.info(f"Starting rsync download: {self.host_alias}:{remote_path} -> {local_path}")
        try:
            process = subprocess.Popen(
                rsync_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            if process.stdout:
                for line in iter(process.stdout.readline, ""):
                    line = line.strip()
                    if not line:
                        continue

                    if line.endswith((".fits", ".fit", ".jpg", ".png")):
                        if log_callback:
                            log_callback(f"Downloading: {line}")

                    logger.debug(f"rsync: {line}")

            return_code = process.wait()
            if return_code == 0:
                logger.info(f"rsync completed successfully for {description}")
                return True
            logger.error(f"rsync failed with code {return_code} for {description}.")
            return False
        except Exception as e:
            logger.error(f"rsync execution error for {description}: {e}")
            return False

    def _list_remote_files_with_sizes(self, find_command: str) -> dict[str, int]:
        """Run a ``find`` on the telescope computer and return file sizes.

        Parameters
        ----------
        find_command : `str`
            A ``find`` command whose ``-printf`` prints, on each line, the
            size in bytes, a tab, and the absolute path. It should end with
            ``|| true``: ``find`` exits non-zero when any folder it was given
            is missing (for example no PHD2 folder on an Ekos-only setup), and
            the folders that do exist still produce output.

        Returns
        -------
        sizes_by_path : `dict` [`str`, `int`]
            Size of each file found, keyed by its absolute remote path, in
            path order. Empty if the host cannot be reached.
        """
        try:
            output = self._run_command(["ssh", self.host_alias, find_command])
        except Exception as e:
            if self._last_connection_status is not False:
                logger.error(f"Failed to list remote files: {e}")
            return {}
        sizes_by_path: dict[str, int] = {}
        for line in output.split("\n"):
            size_text, _, remote_path = line.partition("\t")
            if remote_path.strip() and size_text.strip().isdigit():
                sizes_by_path[remote_path.strip()] = int(size_text)
        return dict(sorted(sizes_by_path.items()))

    def _download_remote_files(
        self,
        sizes_by_path: dict[str, int],
        destination_dir: str,
        name_patterns: tuple[str, ...],
        description: str,
    ) -> list[str]:
        """Sync remote log files into a local directory with rsync.

        The same mechanism the image downloads use: rsync compares each
        file's size and modification time and copies only what changed, so
        a log still being written is fetched again and a finished one is
        not. One rsync run per remote folder keeps it to one connection per
        folder, which matters because the telescope computer is slow at
        answering many small file requests.

        If every file is already present with the remote size, rsync is not
        started at all. As with the image downloads, there is deliberately
        no fallback to another tool: a failed run is logged and the files
        that are already current are still returned.

        Parameters
        ----------
        sizes_by_path : `dict` [`str`, `int`]
            Remote absolute path and size of each file wanted.
        destination_dir : `str`
            Local directory to sync into.
        name_patterns : `tuple` [`str`, ...]
            Shell patterns of the file names to take from each folder.
        description : `str`
            What the files are, for log messages.

        Returns
        -------
        local_paths : `list` [`str`]
            Local path of every wanted file that is present with its remote
            size after the sync.
        """
        os.makedirs(destination_dir, exist_ok=True)
        folders_needing_sync: set[str] = set()
        for remote_file, remote_size in sizes_by_path.items():
            local_path = os.path.join(destination_dir, os.path.basename(remote_file))
            if not (os.path.isfile(local_path) and os.path.getsize(local_path) == remote_size):
                folders_needing_sync.add(os.path.dirname(remote_file))

        for remote_folder in sorted(folders_needing_sync):
            self._run_rsync(
                f"{remote_folder}/",
                destination_dir + os.sep,
                [
                    *(f"--include={pattern}" for pattern in name_patterns),
                    "--exclude=*",
                    f"--timeout={_RSYNC_IDLE_TIMEOUT_SECONDS}",
                ],
                f"{description}s in {remote_folder}",
            )

        local_paths = []
        for remote_file, remote_size in sizes_by_path.items():
            local_path = os.path.join(destination_dir, os.path.basename(remote_file))
            if os.path.isfile(local_path) and os.path.getsize(local_path) == remote_size:
                local_paths.append(local_path)
            else:
                logger.warning(f"{description} {remote_file} is not present and current after the sync")
        return local_paths

    def _remote_guide_log_sizes(self) -> dict[str, int]:
        """List every remote guide log with its size.

        Looks where PHD2 stores its logs (``~/PHD2`` and ``~/.phd2/logs``)
        and where the Ekos internal guider stores its own
        (``~/.local/share/kstars/guidelogs``). Both use the PHD2 log format.

        Returns
        -------
        sizes_by_path : `dict` [`str`, `int`]
            Size in bytes of each guide log, keyed by remote path.
        """
        command = (
            'find "$HOME/PHD2/" "$HOME/.phd2/logs/" "$HOME/.local/share/kstars/guidelogs/" '
            "-maxdepth 2 -type f \\( -name 'PHD2_GuideLog_*.txt' -o -name 'guide_log*.txt' \\) "
            "-printf '%s\\t%p\\n' 2>/dev/null || true"
        )
        return self._list_remote_files_with_sizes(command)

    def list_remote_guide_logs(self) -> list[str]:
        """List all remote guide log filenames found on StellarMate.

        Searches the folders where PHD2 stores its logs and the folder
        where the Ekos internal guider stores its own.

        Returns
        -------
        log_files : `list` [`str`]
            List of absolute paths to remote guide log files.
        """
        return list(self._remote_guide_log_sizes())

    def download_guide_logs(self, destination_dir: str) -> list[str]:
        """Download remote guide logs to a local directory.

        Parameters
        ----------
        destination_dir : `str`
            Local directory to write downloaded log files into.

        Returns
        -------
        downloaded_paths : `list` [`str`]
            Local file paths of the guide logs, downloaded now or already
            present and unchanged.
        """
        return self._download_remote_files(
            self._remote_guide_log_sizes(),
            destination_dir,
            ("PHD2_GuideLog_*.txt", "guide_log*.txt"),
            "guide log",
        )

    def _remote_ekos_analyze_log_sizes(self) -> dict[str, int]:
        """List every remote Ekos analyze log with its size.

        Returns
        -------
        sizes_by_path : `dict` [`str`, `int`]
            Size in bytes of each analyze log, keyed by remote path.
        """
        command = (
            "find \"$HOME/.local/share/kstars/analyze/\" -maxdepth 1 -type f -name 'ekos-*.analyze' "
            "-printf '%s\\t%p\\n' 2>/dev/null || true"
        )
        return self._list_remote_files_with_sizes(command)

    def list_remote_ekos_analyze_logs(self) -> list[str]:
        """List all Ekos analyze logs found on StellarMate.

        Returns
        -------
        log_files : `list` [`str`]
            Absolute remote paths of the ``ekos-*.analyze`` files.
        """
        return list(self._remote_ekos_analyze_log_sizes())

    def download_ekos_analyze_logs(self, destination_dir: str) -> list[str]:
        """Download remote Ekos analyze logs to a local directory.

        Parameters
        ----------
        destination_dir : `str`
            Local directory to write downloaded log files into.

        Returns
        -------
        downloaded_paths : `list` [`str`]
            Local file paths of the analyze logs, downloaded now or already
            present and unchanged.
        """
        return self._download_remote_files(
            self._remote_ekos_analyze_log_sizes(), destination_dir, ("ekos-*.analyze",), "Ekos analyze log"
        )

    def _remote_kstars_log_sizes(self, days: int = 3) -> dict[str, int]:
        """List the recent KStars text logs with their sizes.

        KStars writes these when logging is switched on in Ekos (Setup tab,
        Logs, output to File), one ``log_<time>.txt`` per run inside a folder
        for each date. They record what the alignment, focus, guide and mount
        modules did, including why a plate solve failed.

        Parameters
        ----------
        days : `int`, optional
            Only files changed within this many days are listed. Verbose logs
            are large, so older ones are left on the telescope computer.

        Returns
        -------
        sizes_by_path : `dict` [`str`, `int`]
            Size in bytes of each log, keyed by remote path.
        """
        command = (
            "find \"$HOME/.local/share/kstars/logs/\" -maxdepth 2 -type f -name 'log_*.txt' "
            f"-mtime -{int(days)} -printf '%s\\t%p\\n' 2>/dev/null || true"
        )
        return self._list_remote_files_with_sizes(command)

    def download_kstars_logs(self, destination_dir: str) -> list[str]:
        """Download the recent KStars text logs to a local directory.

        The logs go into a ``kstars_logs`` folder inside `destination_dir`,
        so they stay apart from the analyze and guide logs that are read
        and stored.

        Parameters
        ----------
        destination_dir : `str`
            Local directory that holds the other downloaded logs.

        Returns
        -------
        downloaded_paths : `list` [`str`]
            Local file paths of the logs, downloaded now or already present
            and unchanged.
        """
        return self._download_remote_files(
            self._remote_kstars_log_sizes(),
            os.path.join(destination_dir, "kstars_logs"),
            ("log_*.txt",),
            "KStars log",
        )
