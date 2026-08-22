"""Siril driver for Astrometrics.

Wraps the Siril headless CLI (via named pipes) to build working
directories, run calibration/registration/stacking scripts, and
retrieve the resulting stacked image.
"""

import atexit
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from typing import Any

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "ImageProcessing",
]

logger = logging.getLogger(__name__)

_active_image_processing_instances: weakref.WeakSet = weakref.WeakSet()


def _cleanup_all_active_image_processing_instances() -> None:
    """Clean up any ImageProcessing instances still alive at exit.

    Safety net registered with `atexit` so subprocesses started by
    an ImageProcessing instance are not leaked if the interpreter
    exits without an explicit `cleanup_subprocesses()` call.
    """
    for instance in list(_active_image_processing_instances):
        try:
            instance.cleanup_subprocesses()
        except Exception:
            logger.debug("Failed to clean up ImageProcessing subprocesses at exit", exc_info=True)


atexit.register(_cleanup_all_active_image_processing_instances)


class ImageProcessing:
    """Drive Siril to calibrate, register, and stack target frames.

    Builds the working directory Siril expects, launches Siril in
    headless mode over named pipes, and copies the resulting stacked
    image (and optional diagnostics) back out to the target library.
    """

    def __init__(self, config=None, calibration_library=None, job_repository=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize the ImageProcessing class with optional dependencies."""
        from astrometricslib.utilities.calibration_library import CalibrationLibrary
        from astrometricslib.utilities.config_loader import get_configuration

        self.config = config or get_configuration()
        if calibration_library is None:
            self.calibration_library = CalibrationLibrary(app_config=self.config)
            self.calibration_library.load_library()
        else:
            self.calibration_library = calibration_library
        self.siril_executable = self.config.get_siril_executable()
        # Work directory should ideally be in config or a safe temp
        # location in workspace
        self.workdir = os.path.join(os.path.expanduser("~"), "Siril", "Work")
        self.subprocesses = []
        self.gui_launched = False
        self.job_repository = job_repository
        # Reset at the start of every process_target call; initialized
        # here too so build_directories can also be called standalone
        # (as existing tests do) without requiring a prior
        # process_target call.
        self.last_run_diagnostics: dict[str, Any] = {
            "corrupt_frames_skipped": [],
            "calibration_mismatch_flags": [],
        }
        _active_image_processing_instances.add(self)

    def reset_gui_flag(self) -> None:
        """Clear the flag tracking whether the Siril GUI was launched."""
        self.gui_launched = False

    def cleanup_subprocesses(self) -> None:
        """Terminate every Siril subprocess this instance has started."""
        for process in self.subprocesses:
            if process.poll() is None:
                self._kill_process_tree(process)

    def _kill_process_tree(
        self, process: subprocess.Popen, job_logger: logging.Logger | None = None, workdir: str | None = None
    ) -> None:
        """Terminate a Siril subprocess and its entire process group.

        Siril is launched via flatpak/bwrap, which sandboxes the
        actual `siril` binary as a descendant process. Signaling
        process.pid alone can miss that descendant entirely, leaving it
        running indefinitely as an orphan. We send SIGTERM and SIGKILL to
        the process group and clean up lingering child processes associated
        with the work directory pipes.
        """
        log = job_logger.info if job_logger else logger.info
        try:
            pgid = os.getpgid(process.pid)
            try:
                os.killpg(pgid, signal.SIGTERM)
            except Exception as exc:
                logger.debug("SIGTERM to process group %s failed (likely already exited): %s", pgid, exc)
            time.sleep(0.2)
            try:
                os.killpg(pgid, signal.SIGKILL)
            except Exception as exc:
                logger.debug("SIGKILL to process group %s failed (likely already exited): %s", pgid, exc)
            process.wait(timeout=2)
        except Exception:
            log("Process group termination completed.")

        if workdir and os.path.exists(workdir):
            try:
                import glob

                pipes = glob.glob(os.path.join(workdir, "siril_command.*"))
                for pipe in pipes:
                    try:
                        subprocess.run(
                            ["fuser", "-k", "-9", pipe],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    except Exception as exc:
                        logger.debug("fuser cleanup failed for pipe '%s': %s", pipe, exc)
                # Safety net for sandboxed processes linked to workdir pipes
                subprocess.run(
                    ["pkill", "-9", "-f", workdir],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as exc:
                logger.debug("Failed to force-kill lingering Siril processes for '%s': %s", workdir, exc)

    def _is_fits_file_readable(self, path: str) -> bool:
        """Read a FITS file's primary data to confirm it isn't corrupted.

        A single corrupted calibration or light frame handed to
        Siril can stall its ordered write queue indefinitely (the
        writer can never advance past the slot for a frame whose
        data failed to read), hanging the entire stacking job. This
        check lets callers filter such files out before they ever
        reach Siril.

        Returns
        -------
        is_readable : `bool`
            `True` if `path`'s primary data could be read; `False`
            if the file is missing data or fails to open.
        """
        try:
            from astropy.io import fits

            with fits.open(path, memmap=False) as hdul:
                hdu = hdul[1] if hdul[0].data is None and len(hdul) > 1 else hdul[0]
                if hdu.data is None:
                    return False
            return True
        except Exception:
            return False

    def build_directories(
        self,
        id: str,
        image_files: Any,
        camera_filter: str | None = None,
        job_logger: logging.Logger | None = None,
    ) -> str:
        """Create working directories for processing and symlink frames in.

        Parameters
        ----------
        id : `str`
            Target identifier used to name the working folder.
        image_files : `Any`
            Either a list of frame records (dict or FrameRecord-like
            objects with ``path``/``camera``/``telescope``/``iso``/
            ``exposure``/``filter`` attributes) or a legacy nested
            dict keyed by telescope/camera/iso/exposure/filter.
        camera_filter : `str`, optional
            Camera name to restrict light frames to. If `None`
            (default), it is inferred from the first frame, falling
            back to ``"ZWO ASI 533MM Pro"``.
        job_logger : `logging.Logger`, optional
            Logger to record progress to. If `None` (default), the
            module logger is used.

        Returns
        -------
        target_folder : `str`
            Path to the populated working directory for this target.
        """
        log = job_logger.info if job_logger else logger.info

        if not camera_filter:
            if isinstance(image_files, list) and len(image_files) > 0:
                f = image_files[0]
                camera_filter = f.get("camera") if isinstance(f, dict) else getattr(f, "camera", None)

            if not camera_filter:
                camera_filter = "ZWO ASI 533MM Pro"

        target_folder = os.path.join(self.workdir, id)
        for folder in ["biases", "darks", "flats", "lights", "process"]:
            folder_path = os.path.join(target_folder, folder)
            if os.path.exists(folder_path):
                shutil.rmtree(folder_path)
            os.makedirs(folder_path, exist_ok=True)

        library = self.calibration_library

        light_idx = 0
        dark_idx = 0
        bias_idx = 0
        flat_idx = 0

        def find_readable_paths(candidate_paths: list[str]) -> set:
            """Check FITS readability for many candidate paths concurrently.

            Each check opens a file with astropy and reads its
            header, which releases the GIL during the actual disk
            read, so a small thread pool overlaps I/O across
            candidates instead of checking them one at a time.

            Returns
            -------
            readable_paths : `set`
                Subset of `candidate_paths` whose FITS data read
                successfully.
            """
            if not candidate_paths:
                return set()
            with ThreadPoolExecutor(max_workers=8) as pool:
                is_readable_by_path = dict(
                    zip(candidate_paths, pool.map(self._is_fits_file_readable, candidate_paths), strict=False)
                )
            return {path for path, is_readable in is_readable_by_path.items() if is_readable}

        # Handle FrameRecord list (Modern)
        if isinstance(image_files, list) and len(image_files) > 0:
            log(f"Processing frame list for {id}")
            # Group by categories to find calibrations
            candidate_light_paths = [
                frame.get("path") if isinstance(frame, dict) else getattr(frame, "path", "")
                for frame in image_files
                if (frame.get("camera") if isinstance(frame, dict) else getattr(frame, "camera", "Unknown"))
                == camera_filter
            ]
            readable_light_paths = find_readable_paths(candidate_light_paths)

            for frame in image_files:
                # Expecting dict if serialized, or FrameRecord if internal
                path = frame.get("path") if isinstance(frame, dict) else getattr(frame, "path", "")
                cam = frame.get("camera") if isinstance(frame, dict) else getattr(frame, "camera", "Unknown")

                if cam != camera_filter:
                    continue

                if path not in readable_light_paths:
                    log(f"Skipping corrupted/unreadable light frame: {path}")
                    self.last_run_diagnostics.setdefault("corrupt_frames_skipped", []).append(path)
                    continue

                dst_name = f"light_source_{light_idx:05d}.fits"
                try:
                    os.symlink(path, os.path.join(target_folder, "lights", dst_name))
                    # Tracked so the per-frame .lst star lists
                    # (0-indexed in the same symlink order) can be
                    # matched back to their original submitted paths
                    # for the spectral registration-quality check.
                    self.last_run_diagnostics.setdefault("symlinked_light_paths", []).append(path)
                    light_idx += 1
                except Exception as e:
                    log(f"Error symlinking light {path}: {e}")

            # Populate calibrations from the first matching frame
            matching_frames = [
                f
                for f in image_files
                if (f.get("camera") if isinstance(f, dict) else getattr(f, "camera", "")) == camera_filter
            ]
            if matching_frames:
                f = matching_frames[0]
                tel = f.get("telescope") if isinstance(f, dict) else getattr(f, "telescope", "Apertura 75Q")
                cam = f.get("camera") if isinstance(f, dict) else getattr(f, "camera", camera_filter)
                iso = f.get("iso") if isinstance(f, dict) else getattr(f, "iso", "800")
                exp = f.get("exposure") if isinstance(f, dict) else getattr(f, "exposure", "0")
                filt = f.get("filter") if isinstance(f, dict) else getattr(f, "filter", "None")

                def soft_flag_calibration_mismatch(
                    master_paths: list[str], master_kind: str, check_exposure: bool
                ) -> None:
                    """Log, without blocking, a relaxed-match gain mismatch.

                    Checks whether a relaxed-matched calibration
                    master's own gain (and, for darks only,
                    exposure) looks incompatible with the light
                    frames it'll be applied to.

                    calibration_library.py's get_dark_frames/
                    get_bias_frames/get_flat_frames are documented
                    as "REQ: BKD-1.1 - Relaxed matching (ignore
                    ISO...)" -- they deliberately accept a
                    mismatched-gain master over having none at all.
                    This check doesn't override that: it only
                    surfaces the mismatch as a soft flag, checked
                    against the first matched master file as a
                    low-cost approximation rather than reading every
                    matched file's header. check_exposure must be
                    False for bias/flat masters -- their exposure
                    times are unrelated to the light frames' by
                    design (bias is near-zero, flats are set by the
                    flat panel's brightness), so comparing them
                    against light exposure would flag normal,
                    correct calibration setups as mismatched.
                    """
                    if not master_paths:
                        return
                    from astropy.io import fits

                    from astrometricslib.tasks.target_tasks.frame_homogeneity import (
                        is_calibration_gain_compatible,
                        is_dark_calibration_metadata_compatible,
                    )

                    try:
                        with fits.open(master_paths[0], memmap=False) as hdul:
                            header = hdul[0].header
                        master_iso = str(header.get("ISOSPEED", header.get("GAIN", iso)))
                        master_exp = float(header.get("EXPTIME", exp))
                    except Exception:
                        return

                    if check_exposure:
                        compatible = is_dark_calibration_metadata_compatible(
                            light_exposure=float(exp),
                            light_gain=str(iso),
                            master_exposure=master_exp,
                            master_gain=master_iso,
                        )
                    else:
                        compatible = is_calibration_gain_compatible(
                            light_gain=str(iso), master_gain=master_iso
                        )

                    if not compatible:
                        exposure_note = (
                            f" exposure={master_exp}s vs light frames'... exposure={exp}s"
                            if check_exposure
                            else ""
                        )
                        message = (
                            f"{master_kind} master '{master_paths[0]}' has gain={master_iso} vs "
                            f"light frames' gain={iso}.{exposure_note}"
                        )
                        log(f"Calibration metadata mismatch (soft flag, master still applied): {message}")
                        self.last_run_diagnostics.setdefault("calibration_mismatch_flags", []).append(message)

                dark_frame_paths = library.get_dark_frames(camera=cam, iso=iso, exposure=exp)
                soft_flag_calibration_mismatch(dark_frame_paths, "dark", check_exposure=True)
                readable_dark_paths = find_readable_paths(dark_frame_paths)
                for item in dark_frame_paths:
                    if item not in readable_dark_paths:
                        log(f"Skipping corrupted/unreadable dark frame: {item}")
                        continue
                    try:
                        os.symlink(item, os.path.join(target_folder, "darks", f"dark_{dark_idx:05d}.fits"))
                        dark_idx += 1
                    except Exception as e:
                        log(f"Error symlinking dark {item}: {e}")

                bias_frame_paths = library.get_bias_frames(camera=cam, iso=iso)
                soft_flag_calibration_mismatch(bias_frame_paths, "bias", check_exposure=False)
                readable_bias_paths = find_readable_paths(bias_frame_paths)
                for item in bias_frame_paths:
                    if item not in readable_bias_paths:
                        log(f"Skipping corrupted/unreadable bias frame: {item}")
                        continue
                    try:
                        os.symlink(item, os.path.join(target_folder, "biases", f"bias_{bias_idx:05d}.fits"))
                        bias_idx += 1
                    except Exception as e:
                        log(f"Error symlinking bias {item}: {e}")

                flat_frame_paths = library.get_flat_frames(
                    telescope=tel, camera=cam, filter_type=filt, iso=iso
                )
                soft_flag_calibration_mismatch(flat_frame_paths, "flat", check_exposure=False)
                readable_flat_paths = find_readable_paths(flat_frame_paths)
                for item in flat_frame_paths:
                    if item not in readable_flat_paths:
                        log(f"Skipping corrupted/unreadable flat frame: {item}")
                        continue
                    try:
                        os.symlink(item, os.path.join(target_folder, "flats", f"flat_{flat_idx:05d}.fits"))
                        flat_idx += 1
                    except Exception as e:
                        log(f"Error symlinking flat {item}: {e}")

            return target_folder

        # Handle Legacy 5-Level Structure
        if isinstance(image_files, dict):
            for telescope, tel_val in image_files.items():
                if not isinstance(tel_val, dict):
                    continue
                for camera, cam_val in tel_val.items():
                    if camera != camera_filter:
                        continue
                    for iso, iso_val in cam_val.items():
                        if not isinstance(iso_val, dict):
                            continue
                        for exptime, exp_val in iso_val.items():
                            if not isinstance(exp_val, dict):
                                continue
                            for filt, filter_val in exp_val.items():
                                file_list = []
                                if isinstance(filter_val, list):
                                    file_list = filter_val
                                elif isinstance(filter_val, dict):
                                    for date_files in filter_val.values():
                                        if isinstance(date_files, list):
                                            file_list.extend(date_files)

                                for item in file_list:
                                    path = item.get("path") if isinstance(item, dict) else item
                                    try:
                                        os.symlink(
                                            path,
                                            os.path.join(
                                                target_folder, "lights", f"light_source_{light_idx:05d}.fits"
                                            ),
                                        )
                                        light_idx += 1
                                    except Exception as exc:
                                        logger.debug("Failed to symlink light frame '%s': %s", path, exc)

                                # Calibrations per group
                                for item in library.get_dark_frames(camera=camera, iso=iso, exposure=exptime):
                                    try:
                                        os.symlink(
                                            item,
                                            os.path.join(target_folder, "darks", f"dark_{dark_idx:05d}.fits"),
                                        )
                                        dark_idx += 1
                                    except Exception as exc:
                                        logger.debug("Failed to symlink dark frame '%s': %s", item, exc)
                                for item in library.get_bias_frames(camera=camera, iso=iso):
                                    try:
                                        os.symlink(
                                            item,
                                            os.path.join(
                                                target_folder, "biases", f"bias_{bias_idx:05d}.fits"
                                            ),
                                        )
                                        bias_idx += 1
                                    except Exception as exc:
                                        logger.debug("Failed to symlink bias frame '%s': %s", item, exc)
                                for item in library.get_flat_frames(
                                    telescope=telescope, camera=camera, filter_type=filt, iso=iso
                                ):
                                    try:
                                        os.symlink(
                                            item,
                                            os.path.join(target_folder, "flats", f"flat_{flat_idx:05d}.fits"),
                                        )
                                        flat_idx += 1
                                    except Exception as exc:
                                        logger.debug("Failed to symlink flat frame '%s': %s", item, exc)

        return target_folder

    def create_named_pipes(self, base_path: str) -> tuple[str, str]:
        """Create the FIFOs used to send commands to/read output from Siril.

        Parameters
        ----------
        base_path : `str`
            Directory in which to create the named pipes.

        Returns
        -------
        command_pipe : `str`
            Path to the pipe Siril reads commands from.
        output_pipe : `str`
            Path to the pipe Siril writes status output to.
        """
        command_pipe = os.path.join(base_path, "siril_command.in")
        output_pipe = os.path.join(base_path, "siril_command.out")
        for pipe in [command_pipe, output_pipe]:
            if os.path.exists(pipe):
                os.remove(pipe)
            os.mkfifo(pipe)
            # Owner-only: both ends (this process and the spawned Siril
            # subprocess) run as the current user, so wider access isn't
            # needed.
            os.chmod(pipe, 0o600)
        return command_pipe, output_pipe

    def run_siril_headless(
        self,
        command_pipe: str,
        output_pipe: str,
        stdout=subprocess.DEVNULL,  # ruff: ignore[missing-type-function-argument]
        stderr=subprocess.DEVNULL,  # ruff: ignore[missing-type-function-argument]
        job_logger: logging.Logger | None = None,
    ) -> subprocess.Popen:
        """Launch Siril in headless mode, reading/writing over named pipes.

        Parameters
        ----------
        command_pipe : `str`
            Path to the FIFO Siril should read commands from.
        output_pipe : `str`
            Path to the FIFO Siril should write status output to.
        stdout : optional
            Destination for the subprocess's stdout stream.
        stderr : optional
            Destination for the subprocess's stderr stream.
        job_logger : `logging.Logger`, optional
            Logger to record the launch command to. If `None`
            (default), nothing is logged.

        Returns
        -------
        process : `subprocess.Popen`
            The running Siril subprocess, also appended to
            ``self.subprocesses`` for later cleanup.
        """
        import shlex

        parts = shlex.split(self.siril_executable)
        if "flatpak" in parts[0]:
            if "run" in parts:
                idx = parts.index("run")
                parts.insert(idx + 1, "--filesystem=host")
            parts.insert(-1, "--command=sh")
            args = [*parts, "-c", f"siril -p -r {command_pipe} -w {output_pipe}"]
        else:
            args = [*parts, "-p", "-r", command_pipe, "-w", output_pipe]

        if job_logger:
            job_logger.info(f"Executing: {' '.join(args)}")
        # start_new_session=True makes this process the leader of its
        # own process group, so the whole sandboxed tree
        # (flatpak/bwrap/siril) can be killed together via os.killpg
        # later -- signaling process.pid alone can miss the actual
        # siril binary running as a descendant inside the sandbox.
        process = subprocess.Popen(args, stdout=stdout, stderr=stderr, start_new_session=True)
        self.subprocesses.append(process)
        time.sleep(2)
        return process

    def send_commands(
        self, command_pipe: str, commands: list[str], job_logger: logging.Logger | None = None
    ) -> None:
        """Write a sequence of commands to the Siril command pipe.

        Parameters
        ----------
        command_pipe : `str`
            Path to the FIFO to write commands to.
        commands : `list` [`str`]
            Siril script commands to send, in order. An ``"exit"``
            command is always appended after them.
        job_logger : `logging.Logger`, optional
            Logger to record write errors to. If `None` (default),
            errors are silently swallowed.
        """
        try:
            with open(command_pipe, "w") as pipe:
                for cmd in commands:
                    pipe.write(cmd + "\n")
                    pipe.flush()
                    time.sleep(0.05)
                pipe.write("exit\n")
                pipe.flush()
        except Exception as e:
            if job_logger:
                job_logger.error(f"Pipe write error: {e}")

    def read_output(
        self,
        output_pipe: str,
        target_folder: str,
        target_name: str,
        output_file: str | None = None,
        library_dest: str | None = None,
        job_logger: logging.Logger | None = None,
        generate_rejmap: bool = False,
        registered_seq_name: str | None = None,
    ) -> str | None:
        """Read Siril's status output and copy out the finished stack.

        Blocks reading ``output_pipe`` line by line until Siril
        reports success or error, then copies the resulting stacked
        file (and, if requested, the rejection map and registration
        sequence) to its final destination.

        Parameters
        ----------
        output_pipe : `str`
            Path to the FIFO Siril writes status output to.
        target_folder : `str`
            Working directory containing the ``process`` scratch
            subdirectory Siril wrote its output into.
        target_name : `str`
            Target name used to build the output filename.
        output_file : `str`, optional
            Explicit output filename to use when ``library_dest`` is
            not given. If `None` (default), a name derived from
            ``target_name`` is used.
        library_dest : `str`, optional
            Directory to copy the final stacked file into. If `None`
            (default), the file is written inside ``target_folder``.
        job_logger : `logging.Logger`, optional
            Logger to record progress and errors to. If `None`
            (default), the module logger is used.
        generate_rejmap : `bool`, optional
            Whether to also copy out the ``-rejmap`` output.
            Default is `False`.
        registered_seq_name : `str`, optional
            Base name of the registered ``.seq`` file to copy out.
            If `None` (default), no registration sequence is copied.

        Returns
        -------
        final_file_path : `str` or `None`
            Path to the copied stacked file, or `None` if Siril
            reported an error or no result was found.
        """
        log = job_logger.info if job_logger else logger.info
        try:
            with open(output_pipe) as pipe:
                for line in pipe:
                    log(line.strip())
                    if "status: success stack" in line:
                        stacked_file = os.path.join(target_folder, "process", "result_stacked.fits")
                        if not os.path.exists(stacked_file):
                            stacked_file = os.path.join(target_folder, "process", "result_stacked.fit")

                        if not os.path.exists(stacked_file):
                            continue

                        safe_name = target_name.replace(" ", "_")
                        final_filename = output_file if output_file else f"{safe_name}_Stacked.fits"

                        if library_dest:
                            os.makedirs(library_dest, exist_ok=True)
                            final_file_path = os.path.join(library_dest, final_filename)
                        else:
                            final_file_path = os.path.join(target_folder, final_filename)

                        shutil.copy(stacked_file, final_file_path)

                        # The -rejmap option writes its output next to
                        # result_stacked in the same scratch "process"
                        # directory, which is removed once
                        # process_target returns. Copy it out
                        # alongside the stacked result (derivable
                        # from the returned path by appending
                        # "_RejMap" to its stem) so callers can still
                        # inspect it after the scratch directory is
                        # gone.
                        if generate_rejmap:
                            stacked_stem = os.path.splitext(os.path.basename(stacked_file))[0]
                            rejmap_src = None
                            for ext in (".fits", ".fit"):
                                candidate = os.path.join(
                                    target_folder, "process", f"{stacked_stem}_low+high_rejmap{ext}"
                                )
                                if os.path.exists(candidate):
                                    rejmap_src = candidate
                                    break
                            if rejmap_src:
                                rejmap_dest = os.path.splitext(final_file_path)[0] + "_RejMap.fits"
                                shutil.copy(rejmap_src, rejmap_dest)
                            else:
                                log("Rejection map was requested but no rejmap output was found.")

                        # The registered .seq file's R0 lines hold
                        # each input frame's own FWHM as measured by
                        # Siril's findstar pass during registration
                        # -- exactly the "median input FWHM" a
                        # post-stack FWHM-degradation check needs,
                        # computed at no extra cost. Same
                        # preservation pattern as the rejmap above:
                        # copy it out before the scratch directory is
                        # removed.
                        if registered_seq_name:
                            seq_src = os.path.join(target_folder, "process", f"r_{registered_seq_name}.seq")
                            if os.path.exists(seq_src):
                                seq_dest = os.path.splitext(final_file_path)[0] + "_Registration.seq"
                                shutil.copy(seq_src, seq_dest)
                            else:
                                log("Registration sequence file was expected but not found.")

                        should_open_gui = (
                            not self.gui_launched
                            and os.environ.get("HEADLESS") != "1"
                            and self.config.get_auto_open_siril_gui()
                        )
                        if should_open_gui:
                            self.launch_siril_gui(final_file_path)
                            self.gui_launched = True
                        return final_file_path
                    elif "status: error" in line:
                        return None
                return None
        except Exception as e:
            if job_logger:
                job_logger.error(f"Pipe read error: {e}")
            return None

    def process_target(
        self,
        id: str,
        image_files: Any,
        output_file: str | None = None,
        camera_filter: str | None = None,
        log_file: str | None = None,
        is_spectral: bool = False,
        job_id: str | None = None,
        rejection_sigma: tuple[float, float] | None = None,
        filter_wfwhm: str | None = None,
        filter_round: str | None = None,
        stack_weight: str | None = None,
        generate_rejmap: bool | None = None,
    ) -> str | None:
        """Calibrate, register, and stack a target's frames via Siril.

        Builds the working directory, drives Siril through a
        generated headless script (master calibration generation,
        registration, and rejection-stacking), and copies the
        resulting stacked file out to the target library.

        Parameters
        ----------
        id : `str`
            Target identifier used to name the working folder and
            log file.
        image_files : `Any`
            Frame list or legacy nested dict, as accepted by
            `build_directories`.
        output_file : `str`, optional
            Explicit output filename. If `None` (default), a name
            derived from ``id`` is used.
        camera_filter : `str`, optional
            Camera name to restrict light frames to. If `None`
            (default), it is inferred from the first frame.
        log_file : `str`, optional
            Path to write the job's Siril log to. If `None`
            (default), a path under the configured logs directory is
            used.
        is_spectral : `bool`, optional
            Whether these are spectroscopy frames, which use a
            shift-only registration transform. Default is `False`.
        job_id : `str`, optional
            Job identifier used to report progress to the job
            repository. If `None` (default), progress isn't
            reported.
        rejection_sigma : `tuple` [`float`, `float`], optional
            Explicit ``(low, high)`` rejection sigma override. If
            `None` (default), the sigma is derived from the
            configured rejection mode.
        filter_wfwhm : `str`, optional
            Siril ``-filter-wfwhm`` value. If `None` (default), the
            configured default is used.
        filter_round : `str`, optional
            Siril ``-filter-round`` value. If `None` (default), the
            configured default is used.
        stack_weight : `str`, optional
            Siril ``-weight`` value. If `None` (default), the
            configured default is used.
        generate_rejmap : `bool`, optional
            Whether to generate a rejection map. If `None` (default),
            the configured default is used.

        Returns
        -------
        result : `str` or `None`
            Path to the final stacked file, or `None` if the job
            failed.
        """
        self.reset_gui_flag()
        # Populated as process_target runs, read by
        # stacking_operations.process_target immediately afterward
        # to assemble a StackQualitySummary -- corrupt-frame skips
        # and calibration-mismatch soft-flags aren't otherwise
        # derivable from the returned stacked path, unlike the
        # rejmap/registration data which are recoverable from files
        # copied out alongside it.
        self.last_run_diagnostics: dict[str, Any] = {
            "corrupt_frames_skipped": [],
            "calibration_mismatch_flags": [],
        }
        if filter_wfwhm is None:
            filter_wfwhm = self.config.get_stack_filter_wfwhm_percentile()
        if filter_round is None:
            filter_round = self.config.get_stack_filter_round_percentile()
        if stack_weight is None:
            stack_weight = self.config.get_stack_weight()
        if generate_rejmap is None:
            generate_rejmap = self.config.get_stack_generate_rejmap()
        job_logger = logging.getLogger(f"siril_{id}")
        job_logger.setLevel(logging.INFO)
        # Isolate this job's log to its own dedicated file (below)
        # instead of bubbling up to the root logger, which may be
        # shared with unrelated activity in the calling process
        # (e.g. the backend/planetarium server).
        job_logger.propagate = False
        if job_logger.handlers:
            job_logger.handlers.clear()
        if not log_file:
            try:
                logs_path = self.config.get_logs_path()
                safe_id = id.replace(" ", "_")
                log_file = os.path.join(str(logs_path), f"stack_{safe_id}.log")
            except Exception:
                log_file = "siril.log"

        handler = logging.FileHandler(log_file, mode="w")
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        job_logger.addHandler(handler)

        db_handler = None
        if self.job_repository is not None:
            from astrometricslib.drivers.logger_interface import DbLogHandler

            db_handler = DbLogHandler(self.job_repository, job_id=job_id)
            job_logger.addHandler(db_handler)

        job_logger.info(f"JOB START: {id}")

        try:
            frames_path = self.config.get_frames_path()
            library_dest = os.path.join(frames_path, "lights", id)
        except Exception:
            library_dest = None

        if not camera_filter:
            if isinstance(image_files, list) and len(image_files) > 0:
                f = image_files[0]
                camera_filter = f.get("camera") if isinstance(f, dict) else getattr(f, "camera", None)

            if not camera_filter:
                camera_filter = "ZWO ASI 533MM Pro"

        target_folder = None
        res = None
        try:
            safe_id = id.replace(" ", "_")
            target_folder = self.build_directories(
                safe_id, image_files, camera_filter=camera_filter, job_logger=job_logger
            )
            command_pipe, output_pipe = self.create_named_pipes(target_folder)

            # Get actual counts to handle 1-frame sequence issue in
            # Siril. Siril's 'convert' does not create a .seq file
            # for a single input frame, which causes subsequent
            # 'calibrate' or 'stack' commands to fail.
            num_biases = len(os.listdir(os.path.join(target_folder, "biases")))
            num_darks = len(os.listdir(os.path.join(target_folder, "darks")))
            num_flats = len(os.listdir(os.path.join(target_folder, "flats")))
            num_lights = len(os.listdir(os.path.join(target_folder, "lights")))

            # rejection_sigma passed by the caller is an explicit
            # override and always wins. Otherwise, "adaptive" mode
            # (the default) derives sigma from num_lights via
            # Chauvenet's criterion rather than using one fixed
            # constant for every stack -- see
            # tasks/target_tasks/rejection_thresholds.py for why. "fixed"
            # mode uses the configured constant unconditionally.
            if rejection_sigma is not None:
                rejection_sigma_low, rejection_sigma_high = rejection_sigma
                rejection_sigma_mode_used = "override"
            elif self.config.get_stack_rejection_sigma_mode() == "fixed":
                rejection_sigma_low, rejection_sigma_high = self.config.get_stack_rejection_sigma()
                rejection_sigma_mode_used = "fixed"
            else:
                from astrometricslib.tasks.target_tasks.rejection_thresholds import chauvenet_sigma

                adaptive_sigma = chauvenet_sigma(max(num_lights, 1))
                rejection_sigma_low = rejection_sigma_high = adaptive_sigma
                rejection_sigma_mode_used = "adaptive"

            # Applies the minimum-surviving-frames floor to a
            # percentage-based filter_wfwhm before it's ever sent to
            # Siril: -filter-wfwhm=X% keeps a deterministic X% of
            # num_lights, so the survivor count (and whether it dips
            # below the floor) is knowable in advance without
            # needing Siril to run first. See
            # tasks/target_tasks/stack_quality_tasks.py for the
            # loosening-ladder logic.
            from astrometricslib.tasks.target_tasks.stack_quality_tasks import (
                resolve_filter_wfwhm_with_floor,
            )

            requested_filter_wfwhm = filter_wfwhm
            filter_wfwhm, filter_wfwhm_loosened = resolve_filter_wfwhm_with_floor(num_lights, filter_wfwhm)
            if filter_wfwhm_loosened:
                job_logger.info(
                    f"filter_wfwhm loosened from {requested_filter_wfwhm!r} to {filter_wfwhm!r} "
                    f"to keep at least the minimum-surviving-frames floor with {num_lights} input frames."
                )

            self.last_run_diagnostics.update({
                "num_lights": num_lights,
                "rejection_sigma_low": rejection_sigma_low,
                "rejection_sigma_high": rejection_sigma_high,
                "rejection_sigma_mode": rejection_sigma_mode_used,
                "filter_wfwhm_requested": requested_filter_wfwhm,
                "filter_wfwhm_effective": filter_wfwhm,
                "filter_wfwhm_loosened": filter_wfwhm_loosened,
            })

            siril_debug_log = os.path.join(target_folder, "siril_debug.log")

            import re
            import subprocess

            process = self.run_siril_headless(
                command_pipe,
                output_pipe,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                job_logger=job_logger,
            )

            def read_siril_stdout():  # ruff: ignore[missing-return-type-private-function]
                progress_regex = re.compile(r"progress:.*?([0-9.]+)\s*%")
                try:
                    with open(siril_debug_log, "w") as debug_out:
                        for line in iter(process.stdout.readline, b""):
                            decoded_line = line.decode("utf-8", errors="ignore")
                            debug_out.write(decoded_line)
                            debug_out.flush()

                            # Parse and update progress
                            match = progress_regex.search(decoded_line)
                            if match and self.job_repository is not None and job_id:
                                try:
                                    val = float(match.group(1))
                                    job = self.job_repository.get_job(job_id)
                                    if job:
                                        job.progress_current = int(val)
                                        self.job_repository.upsert_job(job)
                                except Exception as exc:
                                    logger.debug("Failed to parse/persist Siril progress line: %s", exc)
                except Exception as e:
                    if job_logger:
                        job_logger.error(f"Error in Siril stdout reader thread: {e}")

            log_reader_thread = threading.Thread(target=read_siril_stdout, daemon=True)
            log_reader_thread.start()

            script = ["setext fits"]

            # REQ: IMG-4.2 - Automated Master Calibration Generation
            if num_biases > 0:
                script += [f"cd {os.path.join(target_folder, 'biases')}"]
                if num_biases == 1:
                    # Single bias: convert and use directly as master
                    script += [
                        "convert bias -out=../process",
                        "cd ../process",
                        "load bias_00001.fits",
                        "save bias_stacked",
                    ]
                else:
                    script += [
                        "convert bias -out=../process -fitseq",
                        "cd ../process",
                        "stack bias rej 3 3 -nonorm -out=bias_stacked",
                    ]

            if num_darks > 0:
                script += [f"cd {os.path.join(target_folder, 'darks')}"]
                if num_darks == 1:
                    # Single dark: convert and use directly as master
                    script += [
                        "convert dark -out=../process",
                        "cd ../process",
                        "load dark_00001.fits",
                        "save dark_stacked",
                    ]
                else:
                    script += [
                        "convert dark -out=../process -fitseq",
                        "cd ../process",
                        "stack dark rej 3 3 -nonorm -out=dark_stacked",
                    ]

            if num_flats > 0:
                script += [f"cd {os.path.join(target_folder, 'flats')}"]
                if num_flats == 1:
                    script += [
                        "convert flat -out=../process",
                        "cd ../process",
                        "load flat_00001.fits",
                        "save flat_stacked",
                    ]
                else:
                    script += ["convert flat -out=../process -fitseq", "cd ../process"]
                    # Calibrate flat with bias if available
                    script += [
                        f"calibrate flat {'-bias=bias_stacked' if num_biases > 0 else ''} -cfa -equalize_cfa",
                        "stack pp_flat rej 3 3 -norm=mul -out=flat_stacked",
                    ]

            # Process Lights
            script += [f"cd {os.path.join(target_folder, 'lights')}"]
            # only set in the multi-frame branch below; single-frame
            # stacks skip registration entirely
            seq = None
            if num_lights == 1:
                # Siril's 'convert' does not create a .seq file for
                # a single input frame, so sequence-based
                # calibrate/register/stack commands can't be used
                # here. Load and save the single converted frame
                # directly; calibration is skipped in this case.
                script += [
                    "convert light_source -out=../process",
                    "cd ../process",
                    "load light_source_00001.fits",
                    "save result_stacked",
                ]
            else:
                script += ["convert light_source -out=../process -fitseq", "cd ../process"]

                has_cal = num_darks > 0 or num_biases > 0 or num_flats > 0
                if has_cal:
                    dark_flag = "-dark=dark_stacked" if num_darks > 0 else ""
                    flat_flag = "-flat=flat_stacked" if num_flats > 0 else ""
                    bias_flag = "-bias=bias_stacked" if num_biases > 0 else ""
                    script.append(
                        f"calibrate light_source {dark_flag} {flat_flag} {bias_flag} "
                        "-cfa -equalize_cfa -debayer"
                    )
                    seq = "pp_light_source"
                else:
                    seq = "light_source"

                # REQ: IMG-4.4 - Multi-frame Stacking
                # Spectroscopy frames need a shift-only transform: a
                # diffraction grating disperses every star's light,
                # not just the target's, so field stars are usually
                # still plentiful (confirmed empirically -- 99-125
                # stars/frame in a real SA200 session is typical,
                # not "too few" for a homography fit). The actual
                # constraint is that any rotation or scale
                # introduced by registration would rotate the
                # dispersion axis and smear the spectral trace
                # between frames, breaking wavelength calibration
                # consistency across the stack.
                register_command = f"register {seq} -transf=shift" if is_spectral else f"register {seq}"

                # Field-star population is typically rich enough in
                # spectral frames too (see above), so
                # FWHM/roundness-based selection and weighting apply
                # to both frame types -- they're a proxy for overall
                # seeing/focus quality, not something specific to
                # standard imaging.
                stack_options = [f"rej {rejection_sigma_low:.4f} {rejection_sigma_high:.4f}"]
                if filter_wfwhm:
                    stack_options.append(f"-filter-wfwhm={filter_wfwhm}")
                if filter_round:
                    stack_options.append(f"-filter-round={filter_round}")
                if stack_weight:
                    stack_options.append(f"-weight={stack_weight}")
                if generate_rejmap:
                    stack_options.append("-rejmap")
                stack_options += ["-norm=addscale", "-out=result_stacked"]

                script += [register_command, f"stack r_{seq} " + " ".join(stack_options)]

            def write_commands():  # ruff: ignore[missing-return-type-private-function]
                self.send_commands(command_pipe, script, job_logger=job_logger)

            writer = threading.Thread(target=write_commands)
            writer.daemon = True
            writer.start()

            res = self.read_output(
                output_pipe,
                target_folder,
                id,
                output_file=output_file,
                library_dest=library_dest,
                job_logger=job_logger,
                generate_rejmap=generate_rejmap,
                registered_seq_name=seq,
            )

            # Per-frame zero-order star tracking for spectral
            # stacks: parsed here (while the scratch directory still
            # exists, before the finally block's cleanup) rather
            # than preserving the raw .lst files out, since
            # stacking_operations.py only needs the parsed values,
            # not the files themselves.
            if is_spectral and seq and res:
                import glob as _glob

                from astrometricslib.data_access.image_quality_metrics import parse_zero_order_star

                lst_paths = sorted(
                    _glob.glob(os.path.join(target_folder, "process", "cache", f"{seq}*.lst")),
                    key=lambda p: int(os.path.splitext(os.path.basename(p))[0].replace(seq, "")),
                )
                self.last_run_diagnostics["zero_order_stars"] = [parse_zero_order_star(p) for p in lst_paths]

            # REQ: HDR-4.1 - Update stacked FITS header with total
            # exposure time
            if res and os.path.exists(res):
                try:
                    from astropy.io import fits

                    total_exp = 0.0
                    if isinstance(image_files, list):
                        for f in image_files:
                            exp = f.get("exposure") if isinstance(f, dict) else getattr(f, "exposure", "0")
                            try:
                                total_exp += float(exp)
                            except Exception as exc:
                                logger.debug("Skipping unparsable exposure value '%s': %s", exp, exc)

                    if total_exp > 0:
                        with fits.open(res, mode="update") as hdul:
                            hdul[0].header["EXPTIME"] = total_exp
                            hdul[0].header["EXPOSURE"] = total_exp
                            hdul[0].header.add_comment(
                                f"Total exposure time summed from {len(image_files)} frames."
                            )
                        job_logger.info(f"Updated stacked header: EXPTIME={total_exp}s")
                except Exception as e:
                    job_logger.error(f"Failed to update stacked header EXPTIME: {e}")

            writer.join(timeout=5)
            self._kill_process_tree(process, job_logger=job_logger, workdir=target_folder)

            # Wait for log reader thread to finish reading all output
            log_reader_thread.join(timeout=10)
            return res
        except Exception as fatal:
            job_logger.error(f"FATAL: {fatal}", exc_info=True)
            return None
        finally:
            self.cleanup_subprocesses()
            job_logger.removeHandler(handler)
            handler.close()
            if db_handler is not None:
                job_logger.removeHandler(db_handler)
            # Only remove the scratch work directory once the final stack has
            # been copied out to library_dest. If library_dest wasn't
            # resolvable, the returned file lives inside target_folder itself,
            # so it must be left in place. On failure, target_folder is left
            # in place too, since its logs/intermediate files are often needed
            # to diagnose what went wrong.
            if res and library_dest and target_folder and os.path.exists(target_folder):
                try:
                    shutil.rmtree(target_folder)
                    job_logger.info(f"Removed temporary work directory: {target_folder}")
                except Exception as cleanup_error:
                    job_logger.warning(
                        f"Failed to remove temporary work directory {target_folder}: {cleanup_error}"
                    )

    def launch_siril_gui(self, file_path: str) -> None:
        """Open the stacked result in the Siril GUI, if available.

        Parameters
        ----------
        file_path : `str`
            Path to the stacked FITS file to open in Siril.
        """
        import shlex

        args = [*shlex.split(self.siril_executable), file_path]
        try:
            # Explicitly redirected, not inherited: an unredirected
            # stdout/stderr would hand this GUI process a copy of the
            # caller's pipe write-end, so any caller reading this
            # process's own output via subprocess.run(capture_output=
            # True) (e.g. the script test harness) would block until
            # this GUI window is closed, since the pipe never reports
            # EOF while any process still holds it open.
            subprocess.Popen(
                args,
                cwd=os.path.dirname(file_path),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            logger.debug("Failed to launch Siril GUI for '%s': %s", file_path, exc)
