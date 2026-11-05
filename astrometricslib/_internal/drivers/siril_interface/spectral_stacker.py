"""Spectral stacker: the phase-correlation way to stack spectroscopy frames.

Spectroscopy frames whose stars Siril cannot register are calibrated by
Siril, lined up in Python by phase correlation, and stacked by a second
Siril run. `SirilSpectralStacker` is the layer of the driver class
`ImageProcessing` that does this.
"""

import contextvars
import glob
import logging
import os
import queue
import threading
from typing import cast

from astrometricslib._internal.drivers.siril_interface.run_models import (
    SirilLaunch,
    SirilScript,
    SirilStackRun,
    StagedInputs,
)
from astrometricslib._internal.drivers.siril_interface.script_builders import SIRIL_SCRIPT_PREAMBLE
from astrometricslib._internal.drivers.siril_interface.staging import SirilStaging

logger = logging.getLogger(__name__)


# Siril's own two per-frame `stack -weight=` modes ("nbstars" and "wfwhm")
# both read numbers that only exist because Siril's own registration step
# measured them for every frame. The phase-correlation path never runs
# that step (see `_stack_phase_correlation_aligned_spectral_frames`), so
# asking for either one aborts the stack outright with "Sequence does not
# have registration info" -- confirmed against a real Albireo run, where
# the configured default of "wfwhm" did exactly that. "noise" is measured
# straight from each frame's own pixels and needs no registration data.
WEIGHT_MODES_NEEDING_REGISTRATION_DATA = frozenset({"nbstars", "wfwhm"})


PHASE_CORRELATION_FALLBACK_WEIGHT_MODE = "noise"


# The name of the Siril sequence built by running `convert` over the frames
# the phase-correlation alignment step wrote. It must differ from the
# basename those frames are written under (`ALIGNED_FRAME_BASENAME` in
# `pipelines/stacking/processing/spectral_frame_alignment.py`): Siril's
# `convert` picks up every image file already in its target directory and
# relinks it under its own numbering, and when the sequence name and the
# files share one basename, its output paths collide with its own input
# paths mid-conversion and corrupt the sequence it is building.
ALIGNED_SIRIL_SEQUENCE_NAME = "aligned_light"


def find_calibrated_frame_paths(process_directory: str, sequence_name: str) -> list[str]:
    """List Siril's own converted/calibrated frames for one sequence.

    Parameters
    ----------
    process_directory : `str`
        Siril's scratch ``process`` folder for this stacking run.
    sequence_name : `str`
        The sequence's exact name as Siril wrote it, e.g.
        ``"pp_light_source"`` for calibrated frames named
        ``pp_light_source_00001.fits``, or plain ``"light_source"`` when
        there were no calibration masters to apply.

    Returns
    -------
    paths : `list` [`str`]
        The frames, sorted by their frame number.
    """
    pattern = os.path.join(process_directory, f"{sequence_name}_*.fits")
    return sorted(glob.glob(pattern))


class SirilSpectralStacker(SirilStaging):
    """Stack spectroscopy frames lined up by phase correlation."""

    def _stack_phase_correlation_aligned_spectral_frames(
        self,
        run: SirilStackRun,
        staged: StagedInputs,
        launch: SirilLaunch,
        script: SirilScript,
    ) -> str | None:
        """Calibrate with Siril, align in Python, then stack with Siril again.

        Used in place of Siril's own star-detection registration for a
        spectral batch whose frames don't give Siril's star finder enough
        real point sources to register reliably -- a close visual double
        star's field, for instance, where every star (not just the
        target's) is smeared into a trail rather than a compact dot. See
        `spectral_frame_alignment` for the alignment itself.

        `script` (everything `process_target` built up through the
        ``calibrate`` command, with no ``register``/``stack`` appended) is
        sent through the Siril process already running on the command
        pipe. Once it finishes, the calibrated frames it wrote are read
        back, aligned in Python, and handed to a second, separate Siril
        process for a plain rejection stack with no registration step at
        all -- Siril's own ``stack`` command does not require one; the
        bias, dark and flat master steps earlier in `process_target`
        already rely on exactly that.

        Parameters
        ----------
        run : `SirilStackRun`
            The run: its request (output file name), the library folder to
            copy the stack to, the stack weight and rejection map setting,
            the pipeline steps (``steps.align_frames`` does the alignment)
            and the logger.
        staged : `StagedInputs`
            The work folder, the command and output pipes of the Siril
            process already running, the queue its output reader publishes
            command status lines to (reused so `send_commands` can pace the
            calibration script the same way `process_target` always does)
            and the stack's rejection bounds.
        launch : `SirilLaunch`
            That already-running Siril process.
        script : `SirilScript`
            The commands to run before alignment: the
            `SIRIL_SCRIPT_PREAMBLE` (``setext`` and ``set32bits``), master
            calibration generation, `convert`, and `calibrate`; and the
            calibrated sequence's name (``"pp_light_source"``, or plain
            ``"light_source"`` when there were no calibration masters to
            apply), used to find the frames `calibrate` wrote.

        Returns
        -------
        stacked_path : `str` or `None`
            The path to the copied-out stacked file, or `None` if either
            Siril run, or the alignment step in between, produced nothing
            to stack.
        """
        command_pipe = staged.command_pipe
        output_pipe = staged.output_pipe
        process = launch.process
        status_queue = staged.status_queue
        calibration_script = script.commands
        # Several lights are staged in this mode, so the sequence has a name.
        seq = cast(str, script.sequence_name)
        target_folder = staged.target_folder
        output_file = cast(str, run.request.output_file)
        library_dest = run.library_dest
        generate_rejmap = run.generate_rejmap
        rejection_sigma_low = staged.rejection.low
        rejection_sigma_high = staged.rejection.high
        stack_weight = run.stack_weight
        job_logger = run.job_logger
        align_frames = run.steps.align_frames

        if align_frames is None:
            job_logger.error(
                "Phase-correlation registration needs the stacking pipeline's alignment step, "
                "and none was given; nothing to stack."
            )
            self._kill_process_tree(process, job_logger=job_logger, workdir=target_folder)
            return None

        def write_calibration_commands() -> None:
            self.send_commands(
                command_pipe,
                calibration_script,
                job_logger=job_logger,
                status_queue=status_queue,
                process=process,
            )

        calibration_writer = threading.Thread(
            target=contextvars.copy_context().run, args=(write_calibration_commands,), daemon=True
        )
        calibration_writer.start()
        # No stack command was sent, so this only ever reaches Siril's
        # own exit and returns None; it is read for its side effect of
        # keeping status_queue fed while calibration_writer paces itself
        # against it, and for blocking until Siril has actually written
        # every calibrated frame to disk.
        self.read_output(
            output_pipe,
            target_folder,
            os.path.basename(target_folder),
            job_logger=job_logger,
            status_queue=status_queue,
            process=process,
        )
        calibration_writer.join(timeout=5)
        self._kill_process_tree(process, job_logger=job_logger, workdir=target_folder)

        process_directory = os.path.join(target_folder, "process")
        calibrated_paths = find_calibrated_frame_paths(process_directory, seq)
        if not calibrated_paths:
            job_logger.error(
                "No calibrated frames were found in %s after Siril's calibration run; cannot align.",
                process_directory,
            )
            return None

        aligned_directory = os.path.join(process_directory, "aligned")
        aligned_paths, counts = align_frames(calibrated_paths, aligned_directory)
        self.last_run_diagnostics["registered_frames"] = counts["registered"]
        self.last_run_diagnostics["registration_failed_frames"] = counts["failed"]
        if not aligned_paths:
            job_logger.error("No frames could be aligned by phase correlation; nothing to stack.")
            return None

        if stack_weight in WEIGHT_MODES_NEEDING_REGISTRATION_DATA:
            job_logger.warning(
                "Configured stack weight %r needs registration data this run never produces; "
                "using %r instead.",
                stack_weight,
                PHASE_CORRELATION_FALLBACK_WEIGHT_MODE,
            )
            stack_weight = PHASE_CORRELATION_FALLBACK_WEIGHT_MODE

        stack_options = [f"rej {rejection_sigma_low:.4f} {rejection_sigma_high:.4f}"]
        if stack_weight:
            stack_options.append(f"-weight={stack_weight}")
        if generate_rejmap:
            stack_options.append("-rejmap")
        # `-out=../result_stacked`, not the bare name every other stack in
        # this file uses: this run's `cd` below puts Siril's working
        # directory in the "aligned" subfolder (kept separate from
        # `process/` itself so `convert` below only picks up this run's
        # own aligned frames, not the calibrated frames or masters already
        # sitting in `process/`), but `read_output` always looks for
        # `result_stacked.fits` directly in `process/`. The extra `../`
        # writes it where `read_output` expects it without moving
        # `read_output` itself.
        stack_options += ["-norm=addscale", "-out=../result_stacked"]

        stack_command_pipe, stack_output_pipe = self.create_named_pipes(target_folder)
        stack_process = self.run_siril_headless(stack_command_pipe, stack_output_pipe, job_logger=job_logger)
        stack_status_queue: queue.Queue[str] = queue.Queue()
        stack_script = [
            *SIRIL_SCRIPT_PREAMBLE,
            f"cd {aligned_directory}",
            f"convert {ALIGNED_SIRIL_SEQUENCE_NAME} -out=.",
            f"stack {ALIGNED_SIRIL_SEQUENCE_NAME} " + " ".join(stack_options),
        ]

        def write_stack_commands() -> None:
            self.send_commands(
                stack_command_pipe,
                stack_script,
                job_logger=job_logger,
                status_queue=stack_status_queue,
                process=stack_process,
            )

        stack_writer = threading.Thread(
            target=contextvars.copy_context().run, args=(write_stack_commands,), daemon=True
        )
        stack_writer.start()
        res = self.read_output(
            stack_output_pipe,
            target_folder,
            os.path.basename(target_folder),
            output_file=output_file,
            library_dest=library_dest,
            job_logger=job_logger,
            generate_rejmap=generate_rejmap,
            # No Siril registration ran, so there is no .seq file of
            # per-frame registration data to preserve.
            registered_seq_name=None,
            status_queue=stack_status_queue,
            process=stack_process,
        )
        stack_writer.join(timeout=5)
        self._kill_process_tree(stack_process, job_logger=job_logger, workdir=target_folder)
        return res
