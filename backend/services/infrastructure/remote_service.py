"""Remote telescope (StellarMate) access via system SSH/SCP commands."""

import logging
import os
import subprocess

logger = logging.getLogger(__name__)


class RemoteService:
    """Handle interactions with the remote telescope via SSH/SCP.

    Relies on ~/.ssh/config having a 'stellarmate' host alias
    configured.
    """

    def __init__(self, config_service=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.config = config_service

        # Load From Config
        if self.config:
            self.host_alias = self.config.get_telescope_hostname() or "stellarmate"
            self.remote_pictures_path = self.config.get_remote_pictures_path() or "/home/stellarmate/Pictures"
            self.frames_path = self.config.get_frames_path()
        else:
            self.host_alias = "stellarmate"
            self.remote_pictures_path = "/home/stellarmate/Pictures"
            self.frames_path = None

        self._last_connection_status = None  # None=Unknown, True=Online, False=Offline

    def _update_connection_status(self, is_online: bool):  # ruff: ignore[missing-return-type-private-function]
        """Update internal state and logs only on transition."""
        if self._last_connection_status != is_online:
            if is_online:
                logger.info(f"Remote Host '{self.host_alias}' is now ONLINE.")
            else:
                logger.debug(f"Remote Host '{self.host_alias}' is now OFFLINE.")
            self._last_connection_status = is_online

    def _run_command(self, cmd_list):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Run a subprocess command and returns output or raises error.

        Returns
        -------
        stdout : `str`
            The command's stripped standard output.

        Raises
        ------
        Exception
            If the remote host is known offline (cooldown active) or
            the underlying command fails.
        """
        # 1. Enforce quick-fail 2-second timeout option for SSH commands
        if cmd_list and cmd_list[0] == "ssh":
            cmd_list = cmd_list.copy()
            cmd_list.insert(1, "-o")
            cmd_list.insert(2, "ConnectTimeout=2")

        # 2. Debouncer check: if host is known offline, fail-fast to
        # prevent socket hang
        import time

        now = time.time()
        if self._last_connection_status is False:
            if hasattr(self, "_last_probe_time") and (now - self._last_probe_time) < 10.0:
                raise Exception("SSH/Command Failed: Remote host is known offline (cooldown active).")

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
                else:
                    logger.debug(f"SSH Probe failed (still offline): {e.stderr.strip()}")

                self._update_connection_status(False)
            else:
                logger.error(f"Command failed: {cmd_list}")
                logger.error(f"Stderr: {e.stderr}")
                self._update_connection_status(True)

            raise Exception(f"SSH/Command Failed: {e.stderr}") from e

    def check_connection(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Check if we can connect to the remote host.

        Returns
        -------
        connected : `bool`
            `True` if the remote host responded successfully.
        """
        try:
            self._run_command(["ssh", self.host_alias, "echo", "connected"])
            return True
        except Exception:
            return False

    def list_remote_targets(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """List directories in ~/Pictures on the remote host.

        Returns
        -------
        dirs : `list` of `str`
            The target directory names, or an empty list on failure.
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

    def list_remote_files(self, folder_name):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """List individual FITS file paths in a remote target folder.

        Returns
        -------
        files : `list` of `str`
            Filenames relative to the folder, or an empty list on
            failure.
        """
        try:
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

    def download_target_folder(  # ruff: ignore[missing-return-type-undocumented-public-function]
        self,
        remote_target_name,  # ruff: ignore[missing-type-function-argument]
        local_dest_path,  # ruff: ignore[missing-type-function-argument]
        log_callback=None,  # ruff: ignore[missing-type-function-argument]
        selected_files=None,  # ruff: ignore[missing-type-function-argument]
    ):
        """Download a target folder using rsync for robustness.

        Falls back to scp if rsync fails. If selected_files is
        provided (list of filenames), only those files are
        downloaded.

        Returns
        -------
        success : `bool`
            `True` once the folder has been downloaded successfully.

        Raises
        ------
        ValueError
            If no host alias is configured for the remote service.
        subprocess.CalledProcessError
            If both the rsync and the scp fallback fail.
        """
        if not self.host_alias:
            raise ValueError("No host alias configured for remote service.")

        # Normalize paths
        remote_path = f"{self.remote_pictures_path}/{remote_target_name}"
        if not remote_path.endswith("/"):
            remote_path += "/"

        # We ensure the local directory exists
        os.makedirs(local_dest_path, exist_ok=True)
        # We want the folder to be created inside local_dest_path
        local_target_path = os.path.join(local_dest_path, remote_target_name)
        os.makedirs(local_target_path, exist_ok=True)

        # 1. Attempt RSYNC (Superior for NTFS and large transfers)
        # --no-p --no-g --no-o: Skip permission/group/owner
        # preservation (crucial for NTFS)
        # -v: Verbose for log_callback parsing
        # -z: Compress
        # -a: Archive mode (recursive + keep times)
        # Build rsync command, optionally filtering to selected files
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
                f'{self.host_alias}:"{remote_path}"',
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

                # Simple progress heuristic for rsync output
                if line.endswith((".fits", ".fit", ".jpg", ".png")):
                    if log_callback:
                        log_callback(f"Downloading: {line}")

                logger.debug(f"rsync: {line}")

            return_code = process.wait()
            # Clean up temp file if used
            if files_from_path:
                try:
                    os.remove(files_from_path)
                except OSError:
                    pass
            if return_code == 0:
                logger.info(f"rsync completed successfully for {remote_target_name}")
                return True
            else:
                logger.warning(f"rsync failed with code {return_code}. Falling back to scp...")
        except Exception as e:
            if files_from_path:
                try:
                    os.remove(files_from_path)
                except OSError:
                    pass
            logger.warning(f"rsync execution error: {e}. Falling back to scp...")

        # 2. Fallback to SCP if rsync failed or is unavailable
        # Note: scp needs the remote path to be double-quoted if it
        # contains spaces, but here we'll just pass it as-is and let
        # the host:path handle it.
        remote_scp_path = f"{self.host_alias}:{self.remote_pictures_path}/{remote_target_name}"
        scp_cmd = ["scp", "-r", "-v", remote_scp_path, local_dest_path]

        logger.info(f"Starting fallback SCP download: {remote_scp_path} -> {local_dest_path}")

        try:
            process = subprocess.Popen(
                scp_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            scp_output = []
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue

                scp_output.append(line)
                if "Fetching" in line and "debug1" in line:
                    if log_callback:
                        log_callback(f"Downloading: {line.split('Fetching')[1].strip()}")

                logger.debug(f"scp: {line}")

            return_code = process.wait()
            if return_code != 0:
                last_err = "\n".join(scp_output[-10:])
                logger.error(f"SCP failed with code {return_code}. Last output:\n{last_err}")
                raise subprocess.CalledProcessError(return_code, scp_cmd)

            return True
        except Exception as e:
            logger.error(f"Remote download completely failed: {e}")
            raise

    def get_remote_folder_count(self, folder_name):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Recursively counts FITS files in a remote folder.

        REQ: BKD-5.3

        Returns
        -------
        count : `int`
            The number of FITS files found, or 0 on failure.
        """
        try:
            remote_path = f"{self.remote_pictures_path}/{folder_name}"
            cmd = f"find {remote_path} -type f \\( -name '*.fits' -o -name '*.fit' \\) | wc -l"
            output = self._run_command(["ssh", self.host_alias, cmd])
            return int(output)
        except Exception as e:
            if self._last_connection_status is not False:
                logger.error(f"Failed to count files in {folder_name}: {e}")
            return 0
