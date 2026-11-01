"""Run models: the request and the state of one Siril stacking run.

`SirilStackRequest` is the one input to `ImageProcessing.process_target`. The
other classes carry what each phase of a run hands to the next, so a phase
takes the values it needs by name instead of through loose local variables.
"""

import contextlib
import logging
import queue
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any

from astrometricslib._internal.drivers.calibration_library import CALIBRATION_MATCH_BLOCKING_FLAGS_KEY
from astrometricslib._internal.drivers.interfaces.stacking_driver import PipelineSteps
from astrometricslib._internal.drivers.siril_interface.script_builders import (
    CALIBRATION_BLOCKING_FLAGS_KEY,
    StagedFrameCounts,
)


def empty_run_diagnostics() -> dict[str, Any]:
    """Make the diagnostics record of a run that has not started.

    Returns
    -------
    diagnostics : `dict`
        The record with its lists empty. Later phases add their entries.
    """
    return {
        "corrupt_frames_skipped": [],
        "calibration_mismatch_flags": [],
        CALIBRATION_BLOCKING_FLAGS_KEY: [],
        CALIBRATION_MATCH_BLOCKING_FLAGS_KEY: [],
    }


@dataclass(frozen=True)
class SirilStackRequest:
    """Everything one Siril stacking run is asked to do.

    Parameters
    ----------
    id : `str`
        Target identifier used to name the working folder and log file.
    image_files : `Any`
        Frame list, as accepted by `build_directories`.
    output_file : `str`, optional
        Explicit output filename. If `None` (default), a name derived from
        ``id`` is used.
    camera_filter : `str`, optional
        Camera name to restrict light frames to. If `None` (default), it is
        inferred from the first frame.
    log_file : `str`, optional
        Path to write the job's Siril log to. If `None` (default), a path
        under the configured logs directory is used.
    is_spectral : `bool`, optional
        Whether these are spectroscopy frames, which use a shift-only
        registration transform. Default is `False`.
    job_id : `str`, optional
        Job identifier used to report progress to the job repository. If
        `None` (default), progress isn't reported.
    rejection_sigma : `tuple` [`float`, `float`], optional
        Explicit ``(low, high)`` rejection sigma override. If `None`
        (default), the sigma is derived from the configured rejection mode.
    filter_wfwhm : `str`, optional
        Siril ``-filter-wfwhm`` value. If `None` (default), the configured
        default is used.
    filter_round : `str`, optional
        Siril ``-filter-round`` value. If `None` (default), the configured
        default is used.
    stack_weight : `str`, optional
        Siril ``-weight`` value. If `None` (default), the configured default
        is used.
    generate_rejmap : `bool`, optional
        Whether to generate a rejection map. If `None` (default), the
        configured default is used.
    spectral_star_detection : `str`, optional
        How spectral frames are lined up: a key of
        `SPECTRAL_STAR_DETECTION_COMMANDS`, or ``"phase_correlation"`` to
        skip Siril's registration and use ``steps.align_frames``. Default is
        ``"standard"``.
    steps : `PipelineSteps`, optional
        The stacking pipeline's own steps this run calls: the flat
        assessment and the phase-correlation alignment. If `None` (default),
        the flats are not checked or smoothed, and a phase-correlation run
        fails.
    reference_frame_path : `str`, optional
        The frame to line the others up with. When it is among the frames
        stacked (and the frames are not spectral), the script sets it as the
        sequence's reference (``setref``) before the two-pass registration,
        so Siril does not choose its own. If it is not among them, nothing
        is set and Siril chooses. Default is `None` (Siril chooses).
    """

    id: str
    image_files: Any
    output_file: str | None = None
    camera_filter: str | None = None
    log_file: str | None = None
    is_spectral: bool = False
    job_id: str | None = None
    rejection_sigma: tuple[float, float] | None = None
    filter_wfwhm: str | None = None
    filter_round: str | None = None
    stack_weight: str | None = None
    generate_rejmap: bool | None = None
    spectral_star_detection: str = "standard"
    steps: PipelineSteps | None = None
    reference_frame_path: str | None = None


@dataclass
class SirilStackRun:
    """The settings and handles of one run, from its start to its clean-up.

    Parameters
    ----------
    request : `SirilStackRequest`
        What the run was asked to do.
    steps : `PipelineSteps`
        The pipeline steps of the request (an empty set when none were given).
    job_logger : `logging.Logger`
        The logger for this run.
    log_file : `str`
        The run's log file.
    run_log : `contextlib.ExitStack`
        Holds the job log capture; closed at the end of the run.
    siril_lock : `contextlib.ExitStack`
        Holds the machine-wide Siril slot once the run has one; closed after
        the Siril process is gone.
    filter_wfwhm, filter_round, stack_weight : `str` or `None`
        The frame filters and weight, with configured defaults filled in.
    generate_rejmap : `bool`
        Whether to make a rejection map, with the configured default filled in.
    library_dest : `str` or `None`
        The library folder the stack is copied to, or `None` when the
        configuration could not give one.
    camera_filter : `str` or `None`
        The camera whose lights are used.
    target_folder : `str` or `None`
        The work folder, once it is built.
    result : `str` or `None`
        The path of the stacked file, once there is one.
    """

    request: SirilStackRequest
    steps: PipelineSteps
    job_logger: logging.Logger
    log_file: str
    run_log: contextlib.ExitStack
    siril_lock: contextlib.ExitStack
    filter_wfwhm: str | None
    filter_round: str | None
    stack_weight: str | None
    generate_rejmap: bool
    library_dest: str | None
    camera_filter: str | None
    target_folder: str | None = None
    result: str | None = None


@dataclass(frozen=True)
class RejectionChoice:
    """The sigma-clipping bounds of a stack and how they were chosen.

    Parameters
    ----------
    low, high : `float`
        The lower and upper rejection bounds, in standard deviations.
    mode : `str`
        ``"override"`` (the request named them), ``"fixed"`` (the
        configured constant) or ``"adaptive"`` (derived from the frame count).
    floor, low_extra : `float` or `None`
        The adaptive mode's configured floor and low-bound extra, or `None`
        in the other modes.
    floor_applied : `bool`
        Whether the adaptive floor raised the high bound.
    """

    low: float
    high: float
    mode: str
    floor: float | None
    low_extra: float | None
    floor_applied: bool


@dataclass(frozen=True)
class StagedInputs:
    """What staging produced: the work folder, the pipes and the choices.

    Parameters
    ----------
    target_folder : `str`
        The run's work folder.
    command_pipe, output_pipe : `str`
        The named pipes Siril reads commands from and writes output to.
    status_queue : `queue.Queue`
        Command-completion lines, filled by the output reader and read by the
        command writer. Each run has its own.
    counts : `StagedFrameCounts`
        How many frames were staged.
    uses_color_filter_array : `bool`
        Whether the lights come from a color sensor.
    rejection : `RejectionChoice`
        The sigma-clipping bounds.
    filter_wfwhm : `str` or `None`
        The weighted-FWHM filter after the minimum-frames floor was applied.
    """

    target_folder: str
    command_pipe: str
    output_pipe: str
    status_queue: queue.Queue[str]
    counts: StagedFrameCounts
    uses_color_filter_array: bool
    rejection: RejectionChoice
    filter_wfwhm: str | None


@dataclass(frozen=True)
class SirilLaunch:
    """A running Siril process and the thread that logs its output.

    Parameters
    ----------
    process : `subprocess.Popen`
        The Siril process.
    reader_thread : `threading.Thread`
        The thread copying the process's output to the debug log.
    started_at : `float`
        The `time.monotonic` value when the process was launched.
    """

    process: subprocess.Popen
    reader_thread: threading.Thread
    started_at: float


@dataclass(frozen=True)
class SirilScript:
    """The whole script of a run.

    Parameters
    ----------
    commands : `list` [`str`]
        The preamble, the master calibration commands and the commands for
        the lights, in order.
    sequence_name : `str` or `None`
        The calibrated light sequence's name, or `None` for a single light.
    uses_phase_correlation : `bool`
        Whether the frames are lined up in Python instead of by Siril.
    """

    commands: list[str] = field(default_factory=list)
    sequence_name: str | None = None
    uses_phase_correlation: bool = False
