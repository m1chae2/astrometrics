"""Imaging stacker: the run that calibrates, registers and stacks one target.

`SirilImagingStacker.process_target` builds the work folder, writes the
Siril script, runs Siril and copies the stack out to the library. It is the
top layer of the driver class `ImageProcessing`.
"""

import contextlib
import contextvars
import logging
import os
import queue
import shutil
import sqlite3
import threading
import time
from typing import Any

from astrometricslib.drivers.calibration_library import (
    CALIBRATION_MATCH_BLOCKING_FLAGS_KEY,
)
from astrometricslib.drivers.fits_access import FITS_READ_ERRORS, update_first_header
from astrometricslib.drivers.interfaces.stacking_driver import PipelineSteps
from astrometricslib.drivers.siril_interface.script_builders import (
    CALIBRATION_BLOCKING_FLAGS_KEY,
    SIRIL_SCRIPT_PREAMBLE,
    SPECTRAL_STAR_DETECTION_COMMANDS,
    build_bias_master_commands,
    build_dark_master_commands,
    build_flat_master_commands,
    build_single_light_commands,
    calibration_count_flags,
    light_calibration_flags,
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
        spectral_star_detection: str = "standard",
        steps: PipelineSteps | None = None,
        reference_frame_path: str | None = None,
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
            Frame list, as accepted by `build_directories`.
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
        spectral_star_detection : `str`, optional
            How spectral frames are lined up: a key of
            `SPECTRAL_STAR_DETECTION_COMMANDS`, or ``"phase_correlation"``
            to skip Siril's registration and use ``steps.align_frames``.
            Default is ``"standard"``.
        steps : `PipelineSteps`, optional
            The stacking pipeline's own steps this run calls: the flat
            assessment and the phase-correlation alignment. If `None`
            (default), the flats are not checked or smoothed, and a
            phase-correlation run fails.
        reference_frame_path : `str`, optional
            The frame to line the others up with. When it is among the
            frames stacked (and the frames are not spectral), the script
            sets it as the sequence's reference (``setref``) before the
            two-pass registration, so Siril does not choose its own. If it
            is not among them, nothing is set and Siril chooses. Default
            is `None` (Siril chooses).

        Returns
        -------
        result : `str` or `None`
            Path to the final stacked file, or `None` if the job
            failed.
        """
        steps = steps or PipelineSteps()
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
            CALIBRATION_BLOCKING_FLAGS_KEY: [],
            CALIBRATION_MATCH_BLOCKING_FLAGS_KEY: [],
        }
        if filter_wfwhm is None:
            filter_wfwhm = self.config.get_stack_filter_wfwhm_percentile()
        if filter_round is None:
            filter_round = self.config.get_stack_filter_round_percentile()
        if stack_weight is None:
            stack_weight = self.config.get_stack_weight()
        if generate_rejmap is None:
            generate_rejmap = self.config.get_stack_generate_rejmap()
        if not log_file:
            try:
                logs_path = self.config.get_logs_path()
                log_file = os.path.join(str(logs_path), f"stack_{work_directory_name(id, output_file)}.log")
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
                job_id=job_id or f"siril:{id}",
                log_file_path=log_file,
                job_store=self.job_repository if job_id else None,
            )
        )
        job_logger = logger

        job_logger.info("JOB START: %s", id)

        try:
            # Stacks and the files made with them go to the stacks path, which
            # is the frames path unless the configuration sets another disk.
            stacks_path = self.config.get_stacks_path()
            library_dest = os.path.join(stacks_path, "lights", id)
        except ConfigurationError, OSError:
            library_dest = None

        if not camera_filter and isinstance(image_files, list) and len(image_files) > 0:
            f = image_files[0]
            camera_filter = f.get("camera") if isinstance(f, dict) else getattr(f, "camera", None)

        target_folder = None
        res = None
        siril_lock = contextlib.ExitStack()
        try:
            target_folder = self.build_directories(
                work_directory_name(id, output_file),
                image_files,
                camera_filter=camera_filter,
                job_logger=job_logger,
            )
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
            num_biases = len(os.listdir(os.path.join(target_folder, "biases")))
            num_darks = len(os.listdir(os.path.join(target_folder, "darks")))
            num_flats = len(os.listdir(os.path.join(target_folder, "flats")))
            num_lights = len(os.listdir(os.path.join(target_folder, "lights")))

            # Decided from the lights, then applied to the flats too: both
            # sets come from the same camera for a given stack (frames are
            # filtered by camera_filter above), and mixing modes between
            # them would calibrate a debayered light against a non-debayered
            # flat. See `_frames_use_color_filter_array` for why guessing a
            # Bayer pattern (Siril's own fallback) is the bug being fixed.
            uses_color_filter_array = _frames_use_color_filter_array(os.path.join(target_folder, "lights"))
            color_filter_array_flags = " -cfa -equalize_cfa" if uses_color_filter_array else ""
            # Surfaced so a quality summary can state whether this stack
            # demosaiced. A monochrome sensor being debayered was a real
            # defect found only by reading Siril's own logs.
            self.last_run_diagnostics["debayer_applied"] = uses_color_filter_array
            job_logger.info(
                "Sensor type detected as %s; %s CFA/debayer calibration flags.",
                "color (CFA)" if uses_color_filter_array else "monochrome",
                "applying" if uses_color_filter_array else "skipping",
            )

            # rejection_sigma passed by the caller is an explicit
            # override and always wins. Otherwise, "adaptive" mode
            # (the default) derives sigma from num_lights via
            # Chauvenet's criterion rather than using one fixed
            # constant for every stack -- see
            # utilities/rejection_thresholds.py for why. Adaptive mode
            # keeps the high bound at or above a configured floor and sets
            # the low bound a configured amount above the high bound, so a
            # stack of 5 frames does not throw out about 7% of its good
            # samples. "fixed" mode uses the configured constant
            # unconditionally.
            rejection_sigma_floor: float | None = None
            rejection_sigma_low_extra: float | None = None
            rejection_sigma_floor_applied = False
            if rejection_sigma is not None:
                rejection_sigma_low, rejection_sigma_high = rejection_sigma
                rejection_sigma_mode_used = "override"
            elif self.config.get_stack_rejection_sigma_mode() == "fixed":
                rejection_sigma_low, rejection_sigma_high = self.config.get_stack_rejection_sigma()
                rejection_sigma_mode_used = "fixed"
            else:
                from astrometricslib.utilities.rejection_thresholds import rejection_bounds

                rejection_sigma_floor = self.config.get_stack_rejection_sigma_floor()
                rejection_sigma_low_extra = self.config.get_stack_rejection_low_extra_sigma()
                adaptive_bounds = rejection_bounds(
                    max(num_lights, 1), floor=rejection_sigma_floor, low_extra=rejection_sigma_low_extra
                )
                rejection_sigma_low = adaptive_bounds.low
                rejection_sigma_high = adaptive_bounds.high
                rejection_sigma_floor_applied = adaptive_bounds.floor_applied
                rejection_sigma_mode_used = "adaptive"

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

            requested_filter_wfwhm = filter_wfwhm
            filter_wfwhm, filter_wfwhm_loosened = resolve_filter_wfwhm_with_floor(num_lights, filter_wfwhm)
            if filter_wfwhm_loosened:
                job_logger.info(
                    "filter_wfwhm loosened from %r to %r to keep at least the "
                    "minimum-surviving-frames floor with %s input frames.",
                    requested_filter_wfwhm,
                    filter_wfwhm,
                    num_lights,
                )

            self.last_run_diagnostics.update({
                "num_lights": num_lights,
                "rejection_sigma_low": rejection_sigma_low,
                "rejection_sigma_high": rejection_sigma_high,
                "rejection_sigma_mode": rejection_sigma_mode_used,
                "rejection_sigma_floor": rejection_sigma_floor,
                "rejection_sigma_low_extra": rejection_sigma_low_extra,
                "rejection_sigma_floor_applied": rejection_sigma_floor_applied,
                "filter_wfwhm_requested": requested_filter_wfwhm,
                "filter_wfwhm_effective": filter_wfwhm,
                "filter_wfwhm_loosened": filter_wfwhm_loosened,
            })

            self._prefetch_staged_frames(target_folder, job_logger)

            siril_debug_log = os.path.join(target_folder, "siril_debug.log")

            import re
            import subprocess

            # Held from launch until read_output has drained Siril's
            # results below, and released by the finally block's
            # siril_lock.close() on every exit path. Entered via an
            # ExitStack rather than a `with` block purely to avoid
            # re-indenting the ~180 lines this span covers; the release
            # guarantee is the same.
            siril_lock.enter_context(siril_process_lock(job_logger=job_logger, config=self.config))

            # Measured from launch rather than from the start of
            # process_target, so waiting on the Siril lock is not counted
            # as time this stack spent working.
            siril_started_at = time.monotonic()

            process = self.run_siril_headless(
                command_pipe,
                output_pipe,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                job_logger=job_logger,
            )

            def read_siril_stdout() -> None:
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

            # The reader runs in the run's log context, so its lines reach
            # this run's log file.
            log_reader_thread = threading.Thread(
                target=contextvars.copy_context().run, args=(read_siril_stdout,), daemon=True
            )
            log_reader_thread.start()

            script = list(SIRIL_SCRIPT_PREAMBLE)

            # Masters already built for this exact set of calibration
            # frames are copied straight in, and their build steps below
            # are skipped -- the frames still had to be staged above,
            # since their fingerprint is what identifies the master.
            #
            # Only the *build* steps are skipped. num_biases/num_darks/
            # num_flats deliberately keep their real values, because the
            # lights' `calibrate` command reads them to decide whether to
            # pass -bias=/-dark=/-flat=; zeroing them would silently drop
            # the very masters just restored.
            restored_master_kinds = self.restore_cached_calibration_masters(
                target_folder, job_logger=job_logger
            )
            flat_smoothing_sigma = None
            if num_flats > 0:
                flat_assessment = self.assess_staged_flats(
                    target_folder,
                    uses_color_filter_array,
                    job_logger=job_logger,
                    camera=camera_filter,
                    assess_flats=steps.assess_flats,
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
                num_biases, num_darks, num_flats, minimum_calibration_frames
            )
            self.last_run_diagnostics[CALIBRATION_BLOCKING_FLAGS_KEY] = count_flags
            for count_flag in count_flags:
                job_logger.warning("Calibration frame count too low (blocking flag): %s", count_flag)

            # Automated Master Calibration Generation
            if num_biases > 0 and "bias" not in restored_master_kinds:
                script += [f"cd {os.path.join(target_folder, 'biases')}"]
                script += build_bias_master_commands(num_biases)

            if num_darks > 0 and "dark" not in restored_master_kinds:
                script += [f"cd {os.path.join(target_folder, 'darks')}"]
                script += build_dark_master_commands(num_darks)

            if num_flats > 0 and "flat" not in restored_master_kinds:
                script += [f"cd {os.path.join(target_folder, 'flats')}"]
                script += build_flat_master_commands(
                    num_flats, num_biases > 0, color_filter_array_flags, flat_smoothing_sigma
                )

            # Process Lights
            script += [f"cd {os.path.join(target_folder, 'lights')}"]
            # only set in the multi-frame branch below; single-frame
            # stacks skip registration entirely
            seq = None
            # Read below and in the tail of this method, past the
            # num_lights branch, so it needs to exist either way -- a
            # single-frame "stack" never has more than one frame to align.
            use_phase_correlation_alignment = (
                num_lights > 1 and is_spectral and spectral_star_detection == "phase_correlation"
            )
            if num_lights == 1:
                # Siril's 'convert' does not create a .seq file for
                # a single input frame, so sequence-based
                # calibrate/register/stack commands can't be used
                # here. The frame is calibrated as a single file and
                # saved as the result: skipping the dark and flat would
                # record an uncalibrated frame as the target's stack.
                dark_flag, flat_flag, bias_flag = light_calibration_flags(num_darks, num_flats, num_biases)
                self.last_run_diagnostics["calibration_applied"] = {
                    "dark": bool(dark_flag),
                    "flat": bool(flat_flag),
                    "bias": bool(bias_flag),
                }
                script += build_single_light_commands(
                    dark_flag,
                    flat_flag,
                    bias_flag,
                    color_filter_array_flags,
                    " -debayer" if uses_color_filter_array else "",
                )
            else:
                # -fitseq stores the whole sequence as one file, which is
                # fine when Siril also does the registration -- but the
                # phase-correlation path (see
                # `_stack_phase_correlation_aligned_spectral_frames`)
                # reads each calibrated frame back out in Python, which a
                # loose per-frame FITS file (Siril's other convert format)
                # makes trivial and a packed fitseq file does not.
                convert_flags = "" if use_phase_correlation_alignment else " -fitseq"
                script += [f"convert light_source -out=../process{convert_flags}", "cd ../process"]

                has_cal = num_darks > 0 or num_biases > 0 or num_flats > 0
                if has_cal:
                    dark_flag, flat_flag, bias_flag = light_calibration_flags(
                        num_darks, num_flats, num_biases
                    )
                    self.last_run_diagnostics["calibration_applied"] = {
                        "dark": bool(dark_flag),
                        "flat": bool(flat_flag),
                        "bias": bool(bias_flag),
                    }
                    # -debayer belongs only to the lights: it is what turns
                    # a CFA mosaic into an RGB image, and is meaningless
                    # (and harmful) on monochrome data.
                    debayer_flag = " -debayer" if uses_color_filter_array else ""
                    script.append(
                        f"calibrate light_source {dark_flag} {flat_flag} {bias_flag}"
                        f"{color_filter_array_flags}{debayer_flag}"
                    )
                    seq = "pp_light_source"
                else:
                    seq = "light_source"
                    self.last_run_diagnostics["calibration_applied"] = {
                        "dark": False,
                        "flat": False,
                        "bias": False,
                    }

                # Multi-frame Stacking
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
                # Pin star detection instead of inheriting whatever the
                # Siril GUI last recorded to its own config: detection
                # settings are global user state, so without this the
                # pipeline's registration behaviour differs between
                # machines and silently changes if someone adjusts the
                # GUI. -relax=on is what M 42 needs -- its nebulosity
                # dominates the frame's background statistics, and with
                # the default relax=off Siril's findstar returned 9
                # candidates on a calibrated frame carrying 464
                # detectable stars (photutils DAOStarFinder, 5 sigma),
                # which aborted registration for the whole sequence.
                # With relax=on the same frame yields 472.
                register_commands = ["setfindstar -relax=on"]

                # Field-star population is typically rich enough in
                # spectral frames too (see below), so FWHM/roundness
                # selection applies to both frame types -- they're a
                # proxy for overall seeing/focus quality, not something
                # specific to standard imaging. Which command carries
                # them differs by path; see each branch.
                frame_filter_options = []
                if filter_wfwhm:
                    frame_filter_options.append(f"-filter-wfwhm={filter_wfwhm}")
                if filter_round:
                    frame_filter_options.append(f"-filter-round={filter_round}")

                if use_phase_correlation_alignment:
                    # No register/stack commands go into this script at
                    # all: `_stack_phase_correlation_aligned_spectral_frames`
                    # sends what has been built so far (calibration only)
                    # as its own complete run, then drives a second Siril
                    # run itself once the frames are aligned in Python.
                    pass
                elif is_spectral:
                    # Multi-frame Stacking
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
                    #
                    # Left on single-pass registration deliberately: the
                    # two-pass reference selection below is validated
                    # only against standard frames, and the spectral
                    # path's shift-only constraint is the more delicate
                    # of the two to disturb.
                    #
                    # Single-pass `register` applies its transforms as
                    # it goes and offers no separate filtering step, so
                    # this path's frame filters stay on `stack` below.
                    # Which stars register the frames is chosen by
                    # `spectral_star_detection`; see
                    # SPECTRAL_STAR_DETECTION_COMMANDS for the two
                    # settings and why the standard one is the default.
                    register_commands = [SPECTRAL_STAR_DETECTION_COMMANDS[spectral_star_detection]]
                    register_commands.append(f"register {seq} -transf=shift")
                    registered_seq = f"r_{seq}"
                    stack_filter_options = frame_filter_options
                else:
                    # Two-pass registration scores every frame before
                    # picking a reference, where single-pass just takes
                    # the first. M 42's first frame is also its worst
                    # for this purpose -- pointing sits ~9.6 arcmin off
                    # the other 21 -- and single-pass registration
                    # matched only 6-8 star pairs against it and
                    # registered nothing, even with detection fixed.
                    # Two-pass chose a different reference and stacked
                    # 21 of 22 frames.
                    if reference_frame_path is not None:
                        # The caller names the reference (the odd–even
                        # difference job needs both halves on the library
                        # stack's reference). Siril counts sequence images
                        # from 1, in the order of the symlinked light frames.
                        light_paths = self.last_run_diagnostics.get("symlinked_light_paths", [])
                        if reference_frame_path in light_paths:
                            register_commands.append(
                                f"setref {seq} {light_paths.index(reference_frame_path) + 1}"
                            )
                    register_commands.append(f"register {seq} -2pass")
                    # -2pass computes the transforms without applying
                    # them, so the registered sequence only exists once
                    # seqapplyreg has run.
                    #
                    # The frame filters ride on seqapplyreg rather than
                    # on `stack` below. Both commands accept the same
                    # -filter-* options and select on the same per-frame
                    # FWHM/roundness that -2pass just measured, so the
                    # surviving frame set is the same either way -- but
                    # filtering here means a rejected frame is never
                    # interpolated and never written to disk at all,
                    # where filtering at stack time pays for the
                    # full-resolution resample first and discards the
                    # result afterwards. On the 238-frame M 106 run
                    # whose intermediates measured 65GB, a 90% wfwhm
                    # setting is ~24 frames of resample-and-write
                    # avoided.
                    register_commands.append(" ".join([f"seqapplyreg {seq}", *frame_filter_options]))
                    registered_seq = f"r_{seq}"
                    # Deliberately empty: seqapplyreg already dropped
                    # those frames, and passing the same filters again
                    # would filter an already-filtered sequence,
                    # compounding to 81% of the input for a 90% setting.
                    stack_filter_options = []

                if not use_phase_correlation_alignment:
                    stack_options = [f"rej {rejection_sigma_low:.4f} {rejection_sigma_high:.4f}"]
                    stack_options += stack_filter_options
                    if stack_weight:
                        stack_options.append(f"-weight={stack_weight}")
                    if generate_rejmap:
                        stack_options.append("-rejmap")
                    stack_options += ["-norm=addscale", "-out=result_stacked"]

                    script += [
                        *register_commands,
                        f"stack {registered_seq} " + " ".join(stack_options),
                    ]

            if use_phase_correlation_alignment:
                res = self._stack_phase_correlation_aligned_spectral_frames(
                    command_pipe=command_pipe,
                    output_pipe=output_pipe,
                    process=process,
                    status_queue=status_queue,
                    calibration_script=script,
                    seq=seq,
                    target_folder=target_folder,
                    output_file=output_file,
                    library_dest=library_dest,
                    generate_rejmap=generate_rejmap,
                    rejection_sigma_low=rejection_sigma_low,
                    rejection_sigma_high=rejection_sigma_high,
                    stack_weight=stack_weight,
                    job_logger=job_logger,
                    align_frames=steps.align_frames,
                )
            else:

                def write_commands() -> None:
                    self.send_commands(
                        command_pipe,
                        script,
                        job_logger=job_logger,
                        status_queue=status_queue,
                        process=process,
                    )

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
                    status_queue=status_queue,
                    process=process,
                )

                writer.join(timeout=5)
                self._kill_process_tree(process, job_logger=job_logger, workdir=target_folder)

            self.last_run_diagnostics["stacking_duration_seconds"] = round(
                time.monotonic() - siril_started_at, 1
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
            if is_spectral and seq and res and not use_phase_correlation_alignment:
                import glob as _glob

                from astrometricslib.drivers.siril_output_parsing import parse_zero_order_star

                lst_paths = sorted(
                    _glob.glob(os.path.join(target_folder, "process", "cache", f"{seq}*.lst")),
                    key=lambda p: int(os.path.splitext(os.path.basename(p))[0].replace(seq, "")),
                )
                self.last_run_diagnostics["zero_order_stars"] = [parse_zero_order_star(p) for p in lst_paths]

            # Update stacked FITS header with total
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
                            stack_header.add_comment(
                                f"Total exposure time summed from {len(image_files)} frames."
                            )
                            return True

                        update_first_header(res, _record_total_exposure)
                        job_logger.info("Updated stacked header: EXPTIME=%ss", total_exp)
                except FITS_READ_ERRORS:
                    job_logger.exception("Failed to update stacked header EXPTIME")

            # Wait for log reader thread to finish reading all output
            log_reader_thread.join(timeout=10)
            return res
        except Exception:
            job_logger.exception("FATAL: Siril processing stopped with an unexpected error")
            return None
        finally:
            self.cleanup_subprocesses()
            # Released after cleanup_subprocesses so the next waiting
            # Siril run never starts while this one's process tree is
            # still being torn down.
            siril_lock.close()
            # Only remove the scratch work directory once the final stack has
            # been copied out to library_dest. If library_dest wasn't
            # resolvable, the returned file lives inside target_folder itself,
            # so it must be left in place. On failure, target_folder is left
            # in place too, since its logs/intermediate files are often needed
            # to diagnose what went wrong.
            if res and library_dest and target_folder and os.path.exists(target_folder):
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
            run_log.close()
