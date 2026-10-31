"""Imaging stacker: the run that calibrates, registers and stacks one target.

`SirilImagingStacker.process_target` takes one `SirilStackRequest` and runs
short named phases in a fixed order: begin the run, stage the inputs, launch
Siril, write the script, stack, finish, and clean up. It is the top layer of
the driver class `ImageProcessing`.
"""

import contextlib
import contextvars
import glob
import logging
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from typing import Any

from astrometricslib.drivers.fits_access import FITS_READ_ERRORS, update_first_header
from astrometricslib.drivers.interfaces.stacking_driver import PipelineSteps
from astrometricslib.drivers.siril_interface.run_models import (
    RejectionChoice,
    SirilLaunch,
    SirilScript,
    SirilStackRequest,
    SirilStackRun,
    StagedInputs,
    empty_run_diagnostics,
)
from astrometricslib.drivers.siril_interface.script_builders import (
    CALIBRATION_BLOCKING_FLAGS_KEY,
    SIRIL_SCRIPT_PREAMBLE,
    LightScriptSettings,
    StagedFrameCounts,
    build_light_script,
    build_master_calibration_commands,
    calibration_count_flags,
)
from astrometricslib.drivers.siril_interface.session import siril_process_lock
from astrometricslib.drivers.siril_interface.spectral_stacker import SirilSpectralStacker
from astrometricslib.drivers.siril_interface.staging import (
    _frames_use_color_filter_array,
    work_directory_name,
)
from astrometricslib.foundation.errors import ConfigurationError
from astrometricslib.foundation.jobs.runner import capture_job_logs

logger = logging.getLogger(__name__)


class SirilImagingStacker(SirilSpectralStacker):
    """Calibrate, register and stack one target's frames with Siril."""

    def process_target(self, request: SirilStackRequest) -> str | None:
        """Calibrate, register, and stack a target's frames via Siril.

        Builds the working directory, drives Siril through a
        generated headless script (master calibration generation,
        registration, and rejection-stacking), and copies the
        resulting stacked file out to the target library. The steps run
        in this order: begin the run, stage the inputs, launch Siril,
        write the script, stack, finish. Whatever happens, the run is
        cleaned up last.

        Parameters
        ----------
        request : `SirilStackRequest`
            What to stack and how; see its fields.

        Returns
        -------
        result : `str` or `None`
            Path to the final stacked file, or `None` if the job
            failed.
        """
        run = self._begin_run(request)
        try:
            staged = self._stage_inputs(run)
            launch = self._launch_siril(run, staged)
            script = self._write_script(run, staged)
            run.result = self._stack(run, staged, launch, script)
            self._finish_run(run, staged, launch, script)
            return run.result
        except Exception:
            run.job_logger.exception("FATAL: Siril processing stopped with an unexpected error")
            return None
        finally:
            self._clean_up_run(run)

    def _begin_run(self, request: SirilStackRequest) -> SirilStackRun:
        """Reset the run records, fill in defaults and open the run's log.

        Parameters
        ----------
        request : `SirilStackRequest`
            What the run was asked to do.

        Returns
        -------
        run : `SirilStackRun`
            The settings and handles of the run.
        """
        steps = request.steps or PipelineSteps()
        self.reset_gui_flag()
        # Populated as process_target runs, read by
        # stacking_operations.process_target immediately afterward
        # to assemble a StackQualitySummary -- corrupt-frame skips
        # and calibration-mismatch soft-flags aren't otherwise
        # derivable from the returned stacked path, unlike the
        # rejmap/registration data which are recoverable from files
        # copied out alongside it.
        self.last_run_diagnostics = empty_run_diagnostics()
        filter_wfwhm = request.filter_wfwhm
        filter_round = request.filter_round
        stack_weight = request.stack_weight
        generate_rejmap = request.generate_rejmap
        if filter_wfwhm is None:
            filter_wfwhm = self.config.get_stack_filter_wfwhm_percentile()
        if filter_round is None:
            filter_round = self.config.get_stack_filter_round_percentile()
        if stack_weight is None:
            stack_weight = self.config.get_stack_weight()
        if generate_rejmap is None:
            generate_rejmap = self.config.get_stack_generate_rejmap()
        log_file = request.log_file
        if not log_file:
            try:
                logs_path = self.config.get_logs_path()
                log_file = os.path.join(
                    str(logs_path), f"stack_{work_directory_name(request.id, request.output_file)}.log"
                )
            except ConfigurationError, OSError:
                log_file = "siril.log"
        # Each run starts its log file afresh.
        with contextlib.suppress(OSError), open(log_file, "w", encoding="utf-8"):
            pass

        # This run's own log file. The job log router writes every message
        # the run logs into it, and into the job's rows when the run is not
        # already inside its job's own capture.
        run_log = contextlib.ExitStack()
        run_log.enter_context(
            capture_job_logs(
                job_id=request.job_id or f"siril:{request.id}",
                log_file_path=log_file,
                job_store=self.job_repository if request.job_id else None,
            )
        )
        job_logger = logger

        job_logger.info("JOB START: %s", request.id)

        try:
            # Stacks and the files made with them go to the stacks path, which
            # is the frames path unless the configuration sets another disk.
            stacks_path = self.config.get_stacks_path()
            library_dest = os.path.join(stacks_path, "lights", request.id)
        except ConfigurationError, OSError:
            library_dest = None

        camera_filter = request.camera_filter
        image_files = request.image_files
        if not camera_filter and isinstance(image_files, list) and len(image_files) > 0:
            first_frame = image_files[0]
            camera_filter = (
                first_frame.get("camera")
                if isinstance(first_frame, dict)
                else getattr(first_frame, "camera", None)
            )

        return SirilStackRun(
            request=request,
            steps=steps,
            job_logger=job_logger,
            log_file=log_file,
            run_log=run_log,
            siril_lock=contextlib.ExitStack(),
            filter_wfwhm=filter_wfwhm,
            filter_round=filter_round,
            stack_weight=stack_weight,
            generate_rejmap=generate_rejmap,
            library_dest=library_dest,
            camera_filter=camera_filter,
        )

    def _stage_inputs(self, run: SirilStackRun) -> StagedInputs:
        """Build the work folder and pipes, and choose the stack's settings.

        Parameters
        ----------
        run : `SirilStackRun`
            The run; its ``target_folder`` is set as soon as the folder exists.

        Returns
        -------
        staged : `StagedInputs`
            The work folder, pipes, frame counts and the choices made from
            them.
        """
        request = run.request
        job_logger = run.job_logger
        target_folder = self.build_directories(
            work_directory_name(request.id, request.output_file),
            request.image_files,
            camera_filter=run.camera_filter,
            job_logger=job_logger,
        )
        run.target_folder = target_folder
        command_pipe, output_pipe = self.create_named_pipes(target_folder)
        # Lets send_commands wait for each command's own completion
        # line (read by read_output, on a different thread) instead
        # of a blind sleep -- see send_commands for why that races
        # on any non-trivial image. It is a local, handed to both
        # halves explicitly, rather than instance state:
        # max_concurrent_jobs allows several process_target calls to
        # share one ImageProcessing instance, and each needs its own.
        status_queue: queue.Queue[str] = queue.Queue()

        # Get actual counts to handle 1-frame sequence issue in
        # Siril. Siril's 'convert' does not create a .seq file
        # for a single input frame, which causes subsequent
        # 'calibrate' or 'stack' commands to fail.
        counts = StagedFrameCounts(
            biases=len(os.listdir(os.path.join(target_folder, "biases"))),
            darks=len(os.listdir(os.path.join(target_folder, "darks"))),
            flats=len(os.listdir(os.path.join(target_folder, "flats"))),
            lights=len(os.listdir(os.path.join(target_folder, "lights"))),
        )

        # Decided from the lights, then applied to the flats too: both
        # sets come from the same camera for a given stack (frames are
        # filtered by camera_filter above), and mixing modes between
        # them would calibrate a debayered light against a non-debayered
        # flat. See `_frames_use_color_filter_array` for why guessing a
        # Bayer pattern (Siril's own fallback) is the bug being fixed.
        uses_color_filter_array = _frames_use_color_filter_array(os.path.join(target_folder, "lights"))
        # Surfaced so a quality summary can state whether this stack
        # demosaiced. A monochrome sensor being debayered was a real
        # defect found only by reading Siril's own logs.
        self.last_run_diagnostics["debayer_applied"] = uses_color_filter_array
        job_logger.info(
            "Sensor type detected as %s; %s CFA/debayer calibration flags.",
            "color (CFA)" if uses_color_filter_array else "monochrome",
            "applying" if uses_color_filter_array else "skipping",
        )

        rejection = self._choose_rejection_sigma(counts.lights, request.rejection_sigma)

        # Applies the minimum-surviving-frames floor to a
        # percentage-based filter_wfwhm before it's ever sent to
        # Siril: -filter-wfwhm=X% keeps a deterministic X% of
        # num_lights, so the survivor count (and whether it dips
        # below the floor) is knowable in advance without
        # needing Siril to run first. See
        # utilities/stack_filter_floor.py for the
        # loosening-ladder logic.
        from astrometricslib.utilities.stack_filter_floor import (
            resolve_filter_wfwhm_with_floor,
        )

        requested_filter_wfwhm = run.filter_wfwhm
        filter_wfwhm, filter_wfwhm_loosened = resolve_filter_wfwhm_with_floor(
            counts.lights, requested_filter_wfwhm
        )
        if filter_wfwhm_loosened:
            job_logger.info(
                "filter_wfwhm loosened from %r to %r to keep at least the "
                "minimum-surviving-frames floor with %s input frames.",
                requested_filter_wfwhm,
                filter_wfwhm,
                counts.lights,
            )

        self.last_run_diagnostics.update({
            "num_lights": counts.lights,
            "rejection_sigma_low": rejection.low,
            "rejection_sigma_high": rejection.high,
            "rejection_sigma_mode": rejection.mode,
            "rejection_sigma_floor": rejection.floor,
            "rejection_sigma_low_extra": rejection.low_extra,
            "rejection_sigma_floor_applied": rejection.floor_applied,
            "filter_wfwhm_requested": requested_filter_wfwhm,
            "filter_wfwhm_effective": filter_wfwhm,
            "filter_wfwhm_loosened": filter_wfwhm_loosened,
        })

        self._prefetch_staged_frames(target_folder, job_logger)

        return StagedInputs(
            target_folder=target_folder,
            command_pipe=command_pipe,
            output_pipe=output_pipe,
            status_queue=status_queue,
            counts=counts,
            uses_color_filter_array=uses_color_filter_array,
            rejection=rejection,
            filter_wfwhm=filter_wfwhm,
        )

    def _choose_rejection_sigma(
        self, num_lights: int, override: tuple[float, float] | None
    ) -> RejectionChoice:
        """Choose the sigma-clipping bounds of the stack.

        A bound pair passed by the caller is an explicit override and
        always wins. Otherwise, "adaptive" mode (the default) derives sigma
        from the light count via Chauvenet's criterion rather than using
        one fixed constant for every stack -- see
        utilities/rejection_thresholds.py for why. Adaptive mode keeps the
        high bound at or above a configured floor and sets the low bound a
        configured amount above the high bound, so a stack of 5 frames does
        not throw out about 7% of its good samples. "fixed" mode uses the
        configured constant unconditionally.

        Parameters
        ----------
        num_lights : `int`
            How many light frames were staged.
        override : `tuple` [`float`, `float`] or `None`
            The caller's ``(low, high)`` bounds, if any.

        Returns
        -------
        choice : `RejectionChoice`
            The bounds and how they were chosen.
        """
        if override is not None:
            low, high = override
            return RejectionChoice(low, high, "override", None, None, False)
        if self.config.get_stack_rejection_sigma_mode() == "fixed":
            low, high = self.config.get_stack_rejection_sigma()
            return RejectionChoice(low, high, "fixed", None, None, False)
        from astrometricslib.utilities.rejection_thresholds import rejection_bounds

        floor = self.config.get_stack_rejection_sigma_floor()
        low_extra = self.config.get_stack_rejection_low_extra_sigma()
        adaptive_bounds = rejection_bounds(max(num_lights, 1), floor=floor, low_extra=low_extra)
        return RejectionChoice(
            adaptive_bounds.low,
            adaptive_bounds.high,
            "adaptive",
            floor,
            low_extra,
            adaptive_bounds.floor_applied,
        )

    def _launch_siril(self, run: SirilStackRun, staged: StagedInputs) -> SirilLaunch:
        """Take the Siril slot, start Siril and start logging its output.

        The slot is held from launch until the output reader has drained
        Siril's results, and released by the clean-up on every exit path.
        It is entered through an `ExitStack` so the hold spans several
        phases.

        Parameters
        ----------
        run : `SirilStackRun`
            The run; its ``siril_lock`` receives the slot.
        staged : `StagedInputs`
            The staged work folder and pipes.

        Returns
        -------
        launch : `SirilLaunch`
            The running process, its output thread and the launch time.
        """
        siril_debug_log = os.path.join(staged.target_folder, "siril_debug.log")
        run.siril_lock.enter_context(siril_process_lock(job_logger=run.job_logger, config=self.config))

        # Measured from launch rather than from the start of
        # process_target, so waiting on the Siril lock is not counted
        # as time this stack spent working.
        started_at = time.monotonic()

        process = self.run_siril_headless(
            staged.command_pipe,
            staged.output_pipe,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            job_logger=run.job_logger,
        )

        # The reader runs in the run's log context, so its lines reach
        # this run's log file.
        log_reader_thread = threading.Thread(
            target=contextvars.copy_context().run,
            args=(self._read_siril_stdout, process, siril_debug_log, run.request.job_id, run.job_logger),
            daemon=True,
        )
        log_reader_thread.start()
        return SirilLaunch(process=process, reader_thread=log_reader_thread, started_at=started_at)

    def _read_siril_stdout(
        self,
        process: subprocess.Popen,
        siril_debug_log: str,
        job_id: str | None,
        job_logger: logging.Logger,
    ) -> None:
        """Copy Siril's output to the debug log and report its progress.

        Parameters
        ----------
        process : `subprocess.Popen`
            The Siril process, started with its output piped.
        siril_debug_log : `str`
            The file the output is copied to.
        job_id : `str` or `None`
            The job to report progress to, if any.
        job_logger : `logging.Logger`
            Logger for a failure of this reader.
        """
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
                        except (ValueError, sqlite3.Error) as exc:
                            logger.debug("Failed to parse/record Siril progress line: %s", exc)
        except OSError:
            if job_logger:
                job_logger.exception("Error in Siril stdout reader thread")

    def _prepare_calibration(self, run: SirilStackRun, staged: StagedInputs) -> tuple[set[str], float | None]:
        """Restore cached masters, assess the flats and record the count flags.

        Masters already built for this exact set of calibration
        frames are copied straight in, and their build steps are
        skipped -- the frames still had to be staged, since their
        fingerprint is what identifies the master.

        Only the *build* steps are skipped. The frame counts
        deliberately keep their real values, because the lights'
        `calibrate` command reads them to decide whether to
        pass -bias=/-dark=/-flat=; zeroing them would silently drop
        the very masters just restored.

        Parameters
        ----------
        run : `SirilStackRun`
            The run.
        staged : `StagedInputs`
            The staged work folder and counts.

        Returns
        -------
        restored_master_kinds : `set` [`str`]
            The masters restored from the cache.
        flat_smoothing_sigma : `float` or `None`
            The blur width for a master flat built now, or `None`.
        """
        job_logger = run.job_logger
        counts = staged.counts
        restored_master_kinds = self.restore_cached_calibration_masters(
            staged.target_folder, job_logger=job_logger
        )
        flat_smoothing_sigma = None
        if counts.flats > 0:
            flat_assessment = self.assess_staged_flats(
                staged.target_folder,
                staged.uses_color_filter_array,
                job_logger=job_logger,
                camera=run.camera_filter,
                assess_flats=run.steps.assess_flats,
            )
            if flat_assessment is not None:
                self.last_run_diagnostics["flat_calibration"] = flat_assessment.as_diagnostics()
                # A master restored from the cache was built, smoothed or
                # not, under the same recipe, so only a master built now
                # is blurred.
                if "flat" not in restored_master_kinds:
                    flat_smoothing_sigma = flat_assessment.smoothing_sigma_pixels

        # A master built from fewer frames than the minimum is still
        # built (the library can legitimately hold one flat), but the run
        # records a blocking flag that the stack quality summary reports.
        # Masters restored from the cache count too: the flag describes
        # the frames behind the master, not whether it was rebuilt now.
        minimum_calibration_frames = self.config.get_minimum_calibration_frames()
        count_flags = calibration_count_flags(
            counts.biases, counts.darks, counts.flats, minimum_calibration_frames
        )
        self.last_run_diagnostics[CALIBRATION_BLOCKING_FLAGS_KEY] = count_flags
        for count_flag in count_flags:
            job_logger.warning("Calibration frame count too low (blocking flag): %s", count_flag)
        return restored_master_kinds, flat_smoothing_sigma

    def _write_script(self, run: SirilStackRun, staged: StagedInputs) -> SirilScript:
        """Write the whole Siril script: preamble, masters and lights.

        Parameters
        ----------
        run : `SirilStackRun`
            The run.
        staged : `StagedInputs`
            The staged work folder, counts and choices.

        Returns
        -------
        script : `SirilScript`
            The commands and what they imply for the stacking phase.
        """
        request = run.request
        script = list(SIRIL_SCRIPT_PREAMBLE)
        restored_master_kinds, flat_smoothing_sigma = self._prepare_calibration(run, staged)

        # Automated Master Calibration Generation
        script += build_master_calibration_commands(
            staged.target_folder,
            staged.counts,
            restored_master_kinds,
            staged.uses_color_filter_array,
            flat_smoothing_sigma,
        )

        light_script = build_light_script(
            staged.target_folder,
            LightScriptSettings(
                counts=staged.counts,
                uses_color_filter_array=staged.uses_color_filter_array,
                is_spectral=request.is_spectral,
                spectral_star_detection=request.spectral_star_detection,
                rejection_sigma_low=staged.rejection.low,
                rejection_sigma_high=staged.rejection.high,
                filter_wfwhm=staged.filter_wfwhm,
                filter_round=run.filter_round,
                stack_weight=run.stack_weight,
                generate_rejmap=run.generate_rejmap,
                reference_frame_path=request.reference_frame_path,
                light_paths=self.last_run_diagnostics.get("symlinked_light_paths", []),
            ),
        )
        self.last_run_diagnostics["calibration_applied"] = light_script.calibration_applied
        script += light_script.commands
        return SirilScript(
            commands=script,
            sequence_name=light_script.sequence_name,
            uses_phase_correlation=light_script.uses_phase_correlation,
        )

    def _stack(
        self, run: SirilStackRun, staged: StagedInputs, launch: SirilLaunch, script: SirilScript
    ) -> str | None:
        """Send the script to Siril and collect the stacked file.

        Parameters
        ----------
        run : `SirilStackRun`
            The run.
        staged : `StagedInputs`
            The staged work folder, pipes and choices.
        launch : `SirilLaunch`
            The running Siril process.
        script : `SirilScript`
            The commands to send.

        Returns
        -------
        result : `str` or `None`
            The path of the stacked file, or `None` if nothing was stacked.
        """
        if script.uses_phase_correlation:
            return self._stack_phase_correlation_aligned_spectral_frames(run, staged, launch, script)

        job_logger = run.job_logger

        def write_commands() -> None:
            self.send_commands(
                staged.command_pipe,
                script.commands,
                job_logger=job_logger,
                status_queue=staged.status_queue,
                process=launch.process,
            )

        writer = threading.Thread(target=write_commands)
        writer.daemon = True
        writer.start()

        result = self.read_output(
            staged.output_pipe,
            staged.target_folder,
            run.request.id,
            output_file=run.request.output_file,
            library_dest=run.library_dest,
            job_logger=job_logger,
            generate_rejmap=run.generate_rejmap,
            registered_seq_name=script.sequence_name,
            status_queue=staged.status_queue,
            process=launch.process,
        )

        writer.join(timeout=5)
        self._kill_process_tree(launch.process, job_logger=job_logger, workdir=staged.target_folder)
        return result

    def _finish_run(
        self, run: SirilStackRun, staged: StagedInputs, launch: SirilLaunch, script: SirilScript
    ) -> None:
        """Record the run's timings and extras once the stack is made.

        Parameters
        ----------
        run : `SirilStackRun`
            The run; ``result`` holds the stacked file's path, if any.
        staged : `StagedInputs`
            The staged work folder.
        launch : `SirilLaunch`
            The Siril process, for its start time and output thread.
        script : `SirilScript`
            The script that was run.
        """
        res = run.result
        target_folder = staged.target_folder
        job_logger = run.job_logger
        sequence_name = script.sequence_name
        self.last_run_diagnostics["stacking_duration_seconds"] = round(
            time.monotonic() - launch.started_at, 1
        )

        # Cached here rather than in the finally block: the masters
        # live inside target_folder, which the finally block deletes
        # on success, and caching a master from a run that failed
        # partway risks storing a half-built one.
        if res:
            self.store_calibration_masters_in_cache(target_folder, job_logger=job_logger)

        # Per-frame zero-order star tracking for spectral
        # stacks: parsed here (while the scratch directory still
        # exists, before the finally block's cleanup) rather
        # than preserving the raw .lst files out, since
        # stacking_operations.py only needs the parsed values,
        # not the files themselves. Not available for the
        # phase-correlation path: those .lst files are Siril's own
        # star-detection registration output, and that path never
        # runs Siril's register command.
        if run.request.is_spectral and sequence_name and res and not script.uses_phase_correlation:
            from astrometricslib.drivers.siril_output_parsing import parse_zero_order_star

            lst_paths = sorted(
                glob.glob(os.path.join(target_folder, "process", "cache", f"{sequence_name}*.lst")),
                key=lambda p: int(os.path.splitext(os.path.basename(p))[0].replace(sequence_name, "")),
            )
            self.last_run_diagnostics["zero_order_stars"] = [parse_zero_order_star(p) for p in lst_paths]

        # Update stacked FITS header with total
        # exposure time
        if res and os.path.exists(res):
            self._record_total_exposure(res, run.request.image_files, job_logger)

        # Wait for log reader thread to finish reading all output
        launch.reader_thread.join(timeout=10)

    def _record_total_exposure(self, stacked_path: str, image_files: Any, job_logger: logging.Logger) -> None:
        """Write the summed input exposure time into the stack header.

        Parameters
        ----------
        stacked_path : `str`
            The stacked file.
        image_files : `Any`
            The input frames; a list of records or dictionaries with an
            ``exposure`` value. Anything else adds up to nothing.
        job_logger : `logging.Logger`
            Logger for the outcome.
        """
        try:
            from astropy.io import fits

            total_exp = 0.0
            if isinstance(image_files, list):
                for f in image_files:
                    exp = f.get("exposure") if isinstance(f, dict) else getattr(f, "exposure", "0")
                    try:
                        total_exp += float(exp)
                    except (ValueError, TypeError) as exc:
                        logger.debug("Skipping unparsable exposure value '%s': %s", exp, exc)

            if total_exp > 0:

                def _record_total_exposure(stack_header: fits.Header) -> bool:
                    """Record the summed exposure time in the header.

                    Parameters
                    ----------
                    stack_header : `astropy.io.fits.Header`
                        The header of the stacked image.

                    Returns
                    -------
                    changed : `bool`
                        Always `True`, because the header is changed.
                    """
                    stack_header["EXPTIME"] = total_exp
                    stack_header["EXPOSURE"] = total_exp
                    stack_header.add_comment(f"Total exposure time summed from {len(image_files)} frames.")
                    return True

                update_first_header(stacked_path, _record_total_exposure, output_verify="exception")
                job_logger.info("Updated stacked header: EXPTIME=%ss", total_exp)
        except FITS_READ_ERRORS:
            job_logger.exception("Failed to update stacked header EXPTIME")

    def _clean_up_run(self, run: SirilStackRun) -> None:
        """Stop Siril, free the slot and tidy the work folder.

        Runs on every exit path, after a failure too.

        Parameters
        ----------
        run : `SirilStackRun`
            The run.
        """
        job_logger = run.job_logger
        target_folder = run.target_folder
        self.cleanup_subprocesses()
        # Released after cleanup_subprocesses so the next waiting
        # Siril run never starts while this one's process tree is
        # still being torn down.
        run.siril_lock.close()
        # Only remove the scratch work directory once the final stack has
        # been copied out to library_dest. If library_dest wasn't
        # resolvable, the returned file lives inside target_folder itself,
        # so it must be left in place. On failure, target_folder is left
        # in place too, since its logs/intermediate files are often needed
        # to diagnose what went wrong.
        if run.result and run.library_dest and target_folder and os.path.exists(target_folder):
            try:
                shutil.rmtree(target_folder)
                job_logger.info("Removed temporary work directory: %s", target_folder)
            except OSError as cleanup_error:
                job_logger.warning(
                    "Failed to remove temporary work directory %s: %s", target_folder, cleanup_error
                )
        elif target_folder and os.path.exists(target_folder):
            # Keeping the whole directory to preserve diagnostics was
            # the right intent at the wrong granularity: measured on
            # 2026-08-24, a failed M 106 run held 65GB, of which
            # process/ was 65GB and everything actually read when
            # diagnosing -- siril_debug.log plus the staged symlink
            # trees recording exactly which frames were used -- came
            # to 1.1MB. Dropping only the intermediates keeps every
            # artifact that has ever been useful.
            self._discard_stacking_intermediates(target_folder, job_logger)
        run.run_log.close()
