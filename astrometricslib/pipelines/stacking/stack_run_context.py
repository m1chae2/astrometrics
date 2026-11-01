"""Purpose: Hold everything one stack run knows, so its steps can share it.

Description: A stack run is a fixed list of steps (see `stage.py`). Each step
reads what the earlier steps found and adds its own results. `StackRun` is
the one object they pass along. Its first fields are what the caller asked
for. The later fields start empty and are filled in by the steps, in the order
the steps run, so a reader can see which step sets which value.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib.models.quality.gate_result import GateResult
from astrometricslib.models.target import Target

if TYPE_CHECKING:
    from astrometricslib._internal.drivers.interfaces.stacking_driver import StackingDriver
    from astrometricslib.models.quality.quality_summary import ExcludedFrame
    from astrometricslib.pipelines.stacking.frame_measurement_run import FrameQualityRecording


@dataclass
class StackRun:
    """The request and the running state of one stack of one target.

    Parameters
    ----------
    target : `Target`
        The target to stack.
    log_file : `str` or `None`
        Where to save the stacking program's output text.
    frames_to_stack : `list` or `None`
        A specific list of frames. `None` means all the target's frames.
    rejection_sigma : `tuple` [`float`, `float`] or `None`
        The rejection limits (lower, upper).
    filter_wfwhm : `str` or `None`
        The rule that throws out blurry frames.
    filter_round : `str` or `None`
        The rule that throws out frames with round-less stars.
    stack_weight : `str` or `None`
        How to weight the frames. The weight step replaces this with the
        weight the stack will use.
    generate_rejmap : `bool` or `None`
        Whether to save a picture of the rejected pixels.
    output_file : `str` or `None`
        Where to save the stack. The naming step fills it in when `None`.
    job_id : `str` or `None`
        The tracked job this stack runs under, if any.
    force : `bool`
        Rebuild the stack even when nothing has changed.
    preview_settings : `Any` or `None`
        Choices for this run's preview picture.
    drivers : `Drivers` or `None`
        The stacking program to use. `None` means Siril.
    measured_at : `datetime` or `None`
        When the stack started, with a time zone.
    quality_recording : `FrameQualityRecording` or `None`
        The configuration and calibration library for the quality rows.
    """

    # What the caller asked for.
    target: Target
    log_file: str | None = None
    frames_to_stack: list[Any] | None = None
    rejection_sigma: tuple[float, float] | None = None
    filter_wfwhm: str | None = None
    filter_round: str | None = None
    stack_weight: str | None = None
    generate_rejmap: bool | None = None
    output_file: str | None = None
    job_id: str | None = None
    force: bool = False
    preview_settings: Any | None = None
    drivers: Drivers | None = None
    measured_at: datetime | None = None
    quality_recording: FrameQualityRecording | None = None

    # Set by the frame-choosing and frame-type steps.
    target_frames: list[Any] = field(default_factory=list)
    has_spectral: bool = False

    # Set by the naming step: the file name the stack will have.
    stack_file: str = ""

    # Set by the screening step.
    frames_submitted: int = 0
    excluded_frames: list[ExcludedFrame] = field(default_factory=list)
    gate_results: list[GateResult] = field(default_factory=list)
    background_split: dict | list[dict] | None = None

    # Set by the preparation step.
    engine: StackingDriver | None = None
    expected_path: str = ""
    inputs_record: Any | None = None

    # Set by the engine step and the steps after it.
    staging: str | None = None
    stacked_path: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    crop_offset: tuple[int, int] = (0, 0)
    preview_path: str | None = None
