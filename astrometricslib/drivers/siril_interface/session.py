"""Siril session: the machine-wide lock, the named pipes and Siril's output.

Holds what any Siril run needs, whatever it stacks: the slot lock that limits
how many Siril processes run at once, the command and output pipes, the
launch and tear-down of the Siril process, and the parsing of what Siril
prints. `SirilSession` is the base of the driver class `ImageProcessing`.
"""

import atexit
import contextlib
import contextvars
import logging
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
import weakref
from collections.abc import Generator
from typing import Any

from astrometricslib.drivers.siril_output_parsing import (
    parse_negative_pixel_percentage,
    parse_registration_totals,
    parse_stacked_image_count,
)
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import ConfigurationError, ExternalServiceError
from astrometricslib.foundation.paths import scratch_directory
from astrometricslib.foundation.run_tally import RunTally, add_to_bound_tally, bind_tally

logger = logging.getLogger(__name__)


_active_image_processing_instances: weakref.WeakSet = weakref.WeakSet()


# A file lock is used to make sure only one Siril process runs at a time
# across the whole computer. Siril uses a lot of CPU power, so running
# multiple at once would overload the machine and cause tasks to time out.
# A file lock is used in the temporary directory because different parts
# of the program (like background workers and scripts) need to share it,
# and they don't share memory.
# Note: Only Siril itself is locked; other preparation steps still run
# in parallel to save time.
SIRIL_PROCESS_LOCK_PATH = os.path.join(scratch_directory(), "astrometricslib-siril.lock")


# How long send_commands waits for one Siril command to report its own
# completion before giving up on the whole script. This is a deadlock
# guard, not a per-command budget: it has to exceed the slowest single
# command a real run issues, and `stack` over a few hundred frames is
# comfortably minutes. Five minutes is far above that while still
# bounding a Siril that has died without closing the output pipe.
SIRIL_COMMAND_TIMEOUT_SECONDS = 300


# How long to wait for Siril to open its end of a command/output FIFO
# before giving up. open() on a FIFO blocks until the other end is
# opened too, and Siril opens both of ours (-r command_pipe -w
# output_pipe) moments after launch -- but if it dies before reaching
# that point (a crash inside a flatpak sandbox, a version that doesn't
# understand -p/-r/-w, ...), nothing will ever open the other end and
# that open() blocks forever. This bounds only that initial handshake,
# not the run itself: SIRIL_COMMAND_TIMEOUT_SECONDS above covers a
# Siril that connects but then hangs mid-command.
SIRIL_PIPE_CONNECT_TIMEOUT_SECONDS = 30


# The seconds the current stacking run has spent waiting for the Siril lock.
# They extend the run's timeout, so targets don't fail just because they had to
# wait in line for their turn. The run (`tasks.stack_frames_with_timeout`)
# makes a `RunTally`, binds it here with `bind_lock_wait` in the thread that
# stacks, and reads it back; `siril_process_lock` adds each wait to the tally
# that is bound where it runs, and to none when no run is tallying.
_bound_lock_wait: contextvars.ContextVar[RunTally | None] = contextvars.ContextVar(
    "siril_lock_wait", default=None
)


def bind_lock_wait(tally: RunTally) -> contextlib.AbstractContextManager[RunTally]:
    """Count Siril lock waits in ``tally`` inside a ``with`` block.

    Parameters
    ----------
    tally : `RunTally`
        The run's tally of waiting seconds.

    Returns
    -------
    scope : `contextlib.AbstractContextManager`
        Use it in a ``with`` statement, in the thread that stacks: a thread
        started inside the block does not see the binding.
    """
    return bind_tally(_bound_lock_wait, tally)


def _record_siril_lock_wait(wait_seconds: float) -> None:
    """Add one blocked interval to the bound run's accumulated wait.

    Parameters
    ----------
    wait_seconds : `float`
        Seconds spent blocked on this acquisition.
    """
    add_to_bound_tally(_bound_lock_wait, wait_seconds)


@contextlib.contextmanager
def siril_process_lock(
    job_logger: logging.Logger | None = None,
    max_concurrent_runs: int | None = None,
    config: AppConfiguration | None = None,
) -> Generator[None]:
    """Hold one of a limited number of machine-wide Siril slots.

    Siril is very demanding on the CPU. If too many instances are run at
    once, the computer will slow down and tasks will fail. This function
    limits how many Siril tasks can run concurrently based on the
    `max_concurrent_jobs` setting in the configuration file. That slot
    pool is shared with photometry/spectroscopy analysis jobs (see
    `AppConfiguration.get_max_concurrent_jobs`), so a running stack and
    a running analysis compete for the same limit.

    It uses a file lock system so that all different parts of the program
    respect the same limit, and the lock is automatically released even
    if the program crashes.

    Parameters
    ----------
    job_logger : `logging.Logger`, optional
        Logger used to record that a run is waiting on another Siril
        run, so a stalled-looking job is explainable from its log.
    max_concurrent_runs : `int`, optional
        Slot count override, for benchmarking. Defaults to the
        configured `max_concurrent_jobs`.
    config : `AppConfiguration`, optional
        The configuration that sets the slot count. A caller that holds
        one passes it. Defaults to the shared configuration.

    Yields
    ------
    `None`
        Control returns to the caller holding a slot.
    """
    from astrometricslib.foundation.storage.process_locks import acquire_resource_slot

    slot_count = max_concurrent_runs
    configuration = None
    if slot_count is None:
        try:
            from astrometricslib.foundation.config import get_configuration

            configuration = config or get_configuration()
            slot_count = configuration.get_max_concurrent_jobs()
        except (ConfigurationError, OSError, ValueError) as configuration_error:
            # A missing configuration must not make Siril unrunnable;
            # one slot is the safe reading, matching the old behaviour.
            logger.debug("Could not read max_concurrent_jobs, using 1 slot: %s", configuration_error)
            slot_count = 1
    slot_count = max(1, int(slot_count))

    waiting_message = (
        f"Waiting for a free Siril slot ({slot_count} allowed concurrently) before starting this run..."
    )
    logger.info(waiting_message)
    if job_logger:
        job_logger.info(waiting_message)

    wait_started_at = time.monotonic()
    with acquire_resource_slot(configuration, "job", slot_count):
        # Recorded whether or not the wait was long: the stacking
        # timeout adds this back to its budget, and a queue that exists
        # to protect the CPU must not convert into a cascade of
        # timeouts for the targets waiting their turn.
        waited_seconds = time.monotonic() - wait_started_at
        _record_siril_lock_wait(waited_seconds)
        if waited_seconds >= 1.0:
            acquired_message = f"Waited {waited_seconds:.1f}s for a Siril slot."
            logger.info(acquired_message)
            if job_logger:
                job_logger.info(acquired_message)
        yield


def _open_pipe_or_die(
    path: str,
    mode: str,
    process: subprocess.Popen,
    timeout: float = SIRIL_PIPE_CONNECT_TIMEOUT_SECONDS,
) -> Any:
    """Open a named pipe, bailing out if Siril never connects to it.

    A plain `open()` on a FIFO blocks the calling thread until a peer
    opens the other end -- which is exactly what we want while Siril is
    alive and simply hasn't gotten to it yet, but not if Siril has
    already died and nothing ever will. Doing the open in a background
    thread lets this function bound the wait against `process` and give
    up instead of hanging forever.

    Parameters
    ----------
    path : `str`
        Path to the FIFO to open.
    mode : `str`
        Mode to open it with, e.g. ``"r"`` or ``"w"``.
    process : `subprocess.Popen`
        The Siril process expected to open the other end. Polled while
        waiting so a Siril that has already exited is reported instead
        of waited on forever.
    timeout : `float`, optional
        Seconds to wait for the open to complete after `process` is
        confirmed still running. Default is
        `SIRIL_PIPE_CONNECT_TIMEOUT_SECONDS`.

    Returns
    -------
    pipe : file object
        The opened pipe, as returned by the built-in `open`.

    Raises
    ------
    ExternalServiceError
        If `process` exits before the open completes, or if it is still
        running but the open does not complete within `timeout` seconds.
    """
    outcome: dict[str, Any] = {}

    def attempt_open() -> None:
        try:
            outcome["pipe"] = open(path, mode)
        except OSError as os_error:
            outcome["error"] = os_error

    opener = threading.Thread(target=attempt_open, daemon=True)
    opener.start()

    deadline = time.monotonic() + timeout
    while opener.is_alive():
        if process.poll() is not None:
            # Give the thread one last short grace window in case Siril's
            # exit and its pipe connection landed at nearly the same
            # moment, then treat it as a lost cause: nothing else is
            # going to open the other end of `path`.
            opener.join(timeout=0.5)
            if opener.is_alive():
                raise ExternalServiceError(
                    f"Siril exited (return code {process.returncode}) before opening {path!r}."
                )
            break
        if time.monotonic() > deadline:
            raise ExternalServiceError(f"Timed out waiting for Siril to open {path!r}.")
        opener.join(timeout=0.2)

    if "error" in outcome:
        raise outcome["error"]
    return outcome["pipe"]


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


class SirilSession:
    """Run Siril over named pipes and read what it reports.

    Base of `ImageProcessing`. It declares the settings and state the layers
    above share, and holds the process, pipe and output handling.
    """

    config: AppConfiguration
    calibration_library: Any
    siril_executable: str
    workdir: str
    subprocesses: list[Any]
    gui_launched: bool
    job_repository: Any
    last_run_diagnostics: dict[str, Any]

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
        running indefinitely as an orphan. SIGTERM and SIGKILL are sent to
        the process group and clean up lingering child processes associated
        with the work directory pipes.
        """
        log = job_logger.info if job_logger else logger.info
        try:
            pgid = os.getpgid(process.pid)
            try:
                os.killpg(pgid, signal.SIGTERM)
            except OSError as exc:
                logger.debug("SIGTERM to process group %s failed (likely already exited): %s", pgid, exc)
            time.sleep(0.2)
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError as exc:
                logger.debug("SIGKILL to process group %s failed (likely already exited): %s", pgid, exc)
            process.wait(timeout=2)
        except OSError, subprocess.TimeoutExpired:
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
                    except (OSError, subprocess.SubprocessError) as exc:
                        logger.debug("fuser cleanup failed for pipe '%s': %s", pipe, exc)
                # Safety net for sandboxed processes linked to workdir pipes
                subprocess.run(
                    ["pkill", "-9", "-f", workdir],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                logger.debug("Failed to force-kill lingering Siril processes for '%s': %s", workdir, exc)

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
            job_logger.info("Executing: %s", " ".join(args))
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
        self,
        command_pipe: str,
        commands: list[str],
        job_logger: logging.Logger | None = None,
        status_queue: queue.Queue[str] | None = None,
        process: subprocess.Popen | None = None,
    ) -> None:
        """Write a sequence of commands to the Siril command pipe.

        Siril's pipe protocol runs one command at a time: sending the
        next command while it's still executing the current one aborts
        it ("status: error command interrupted"), rather than queuing.
        A fixed delay between writes (this function's old behavior)
        only happened to work on trivially small/fast commands -- any
        real FITS frame's `convert`/`register`/`stack` step routinely
        takes longer than that, so every real stacking run raced and
        lost. Pass `status_queue` (fed by `read_output` on the other,
        output pipe as it observes each command's own "status:
        success"/"status: error" line) to make each command wait for
        that signal before the next is sent.

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
        status_queue : `queue.Queue` [`str`], optional
            Queue that the paired `read_output` call publishes each
            command's Siril status line to. If `None` (default), falls
            back to a blind fixed delay between commands, which is only
            safe when every command is known to complete faster than
            it -- pass a queue for any real frame.
        process : `subprocess.Popen`, optional
            The Siril process expected to open the other end of
            `command_pipe`. If given, opening the pipe is bounded by
            `SIRIL_PIPE_CONNECT_TIMEOUT_SECONDS` and fails loudly instead
            of hanging forever should Siril die before connecting. If
            `None` (default), opening falls back to a plain blocking
            `open()`.
        """
        try:
            if process is None:
                pipe_context = open(command_pipe, "w")
            else:
                pipe_context = _open_pipe_or_die(command_pipe, "w", process)
            with pipe_context as pipe:
                for cmd in commands:
                    pipe.write(cmd + "\n")
                    pipe.flush()

                    if status_queue is None:
                        time.sleep(0.05)
                        continue

                    try:
                        result_line = status_queue.get(timeout=SIRIL_COMMAND_TIMEOUT_SECONDS)
                    except queue.Empty:
                        result_line = None
                    if result_line is None:
                        if job_logger:
                            job_logger.error("Timed out waiting for Siril to finish command: %r", cmd)
                        break

                    if "status: error" in result_line:
                        if job_logger:
                            job_logger.error(
                                "Siril reported an error after command %r: %s", cmd, result_line.strip()
                            )
                        break
                pipe.write("exit\n")
                pipe.flush()
        except ExternalServiceError, OSError:
            if job_logger:
                job_logger.exception("Pipe write error")

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
        status_queue: queue.Queue[str] | None = None,
        process: subprocess.Popen | None = None,
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
        status_queue : `queue.Queue` [`str`], optional
            Queue to publish each Siril status line to, so that the
            paired `send_commands` call can send one command at a time.
            If `None` (default), status lines are only logged.
        process : `subprocess.Popen`, optional
            The Siril process expected to open the other end of
            `output_pipe`. If given, opening the pipe is bounded by
            `SIRIL_PIPE_CONNECT_TIMEOUT_SECONDS` and fails loudly instead
            of hanging forever should Siril die before connecting. If
            `None` (default), opening falls back to a plain blocking
            `open()`.

        Returns
        -------
        final_file_path : `str` or `None`
            Path to the copied stacked file, or `None` if Siril
            reported an error or no result was found.
        """
        log = job_logger.info if job_logger else logger.info
        try:
            if process is None:
                pipe_context = open(output_pipe)
            else:
                pipe_context = _open_pipe_or_die(output_pipe, "r", process)
            with pipe_context as pipe:
                for line in pipe:
                    log(line.strip())
                    registration_totals = parse_registration_totals(line)
                    if registration_totals is not None:
                        self.last_run_diagnostics["registration_failed_frames"] = registration_totals[0]
                        self.last_run_diagnostics["registered_frames"] = registration_totals[1]
                    stacked_count = parse_stacked_image_count(line)
                    if stacked_count is not None:
                        self.last_run_diagnostics["images_stacked"] = stacked_count
                    negative_percentage = parse_negative_pixel_percentage(line)
                    if negative_percentage is not None:
                        # One warning per frame: keep how many frames were
                        # warned about and the worst share of negative pixels.
                        diagnostics = self.last_run_diagnostics
                        diagnostics["negative_pixel_frames"] = diagnostics.get("negative_pixel_frames", 0) + 1
                        diagnostics["negative_pixel_max_percent"] = max(
                            diagnostics.get("negative_pixel_max_percent", 0), negative_percentage
                        )
                    if status_queue is not None and ("status: success" in line or "status: error" in line):
                        status_queue.put(line)
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

                        # The .seq file's R<n> lines hold each input
                        # frame's own FWHM as measured by Siril's
                        # findstar pass during registration -- exactly
                        # the "median input FWHM" a post-stack
                        # FWHM-degradation check needs, computed at no
                        # extra cost. Same preservation pattern as the
                        # rejmap above: copy it out before the scratch
                        # directory is removed.
                        #
                        # The *input* sequence is preserved, not the
                        # registered "r_" output. Both carry identical
                        # findstar columns, but registration writes its
                        # computed transforms back into the input
                        # sequence, while the r_ frames are already
                        # physically aligned and so record an identity
                        # matrix for every frame. Preserving r_ therefore
                        # silently discarded every frame's dx/dy shift:
                        # all 238 frames measured before this fix stored
                        # dx=0, dy=0, which is precisely the per-frame
                        # drift series needed to see tracking error,
                        # polar misalignment, or a mount's periodic
                        # error. Confirmed on a real M 106 run, same
                        # frames in both files:
                        #   pp_light_source.seq  -> H ... 6.31985 ... 5.55176
                        #   r_pp_light_source.seq -> H 1 0 0 0 1 0 0 0 1
                        if registered_seq_name:
                            seq_src = os.path.join(target_folder, "process", f"{registered_seq_name}.seq")
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
        except ExternalServiceError, OSError, ValueError:
            if job_logger:
                job_logger.exception("Pipe read error")
            return None

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
        except (OSError, ValueError) as exc:
            logger.debug("Failed to launch Siril GUI for '%s': %s", file_path, exc)
