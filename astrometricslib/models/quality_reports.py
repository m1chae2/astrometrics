"""Data structures for the quality checks on frames and stacks.

`QualityDiagnostics` measures raw frames and finished stacks, and
`ProcessingPipelines.stack_summary` reads back what the stacking stage saved.
Each check returns one of these structures. A check never saves anything, so
these only describe; they never change a target.

- `RawFrameCheckReport` is the batch check of a folder or of a target's
  frames: star count, width and roundness, trailing, sky level and jumps,
  with the frames that stand out flagged.
- `InputQualityReport` holds the sky level, saturation and (if asked) star
  width of a target's newest frames, with a summary of each number.
- `SpectralFrameCheckReport` describes a target's raw spectrum frames and
  where they clip.
- `StackQualityReport` holds the measurements of one stack, and optionally
  its comparison with another stack.
- `StackSummary` is the short description of a target's current stack that
  the stacking stage saved.

Rows that describe one frame are plain dictionaries. Their keys are the
measurement names, and which keys a row has depends on what could be
measured.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from astrometricslib.models.excluded_frames import SetAsideFrame
from astrometricslib.models.stack_comparison import StackComparison

__all__ = [
    "InputQualityReport",
    "RawFrameCheckReport",
    "SpectralFrameCheckReport",
    "StackQualityReport",
    "StackSummary",
]


class RawFrameCheckReport(BaseModel):
    """The batch check of a folder of frames, or of a target's frames."""

    kind: Literal["raw_check"] = "raw_check"
    target_id: str | None = None
    folder_path: str | None = None
    # How many frames matched the selection, and how many of them were read.
    frames_matching: int = 0
    frames_checked: int = 0
    # One row per frame: its measurements and the ``flags`` it raised.
    frames: list[dict[str, Any]] = Field(default_factory=list)
    # The batch medians and how many frames were flagged.
    batch: dict[str, Any] = Field(default_factory=dict)
    # For star width, star count and sky level: how far each moved across
    # the newest frames, with a sentence in ``alerts`` for each worrying drift.
    trends: dict[str, Any] = Field(default_factory=dict)
    # The frames the stacker has set aside, when ``include=["excluded"]``.
    excluded: list[SetAsideFrame] | None = None


class InputQualityReport(BaseModel):
    """Sky level, saturation and star width of a target's newest frames.

    The frames are measured on a copy of the target, so the saved catalog
    never changes. Frames that were already measured keep their stored values.
    """

    kind: Literal["input_quality"] = "input_quality"
    target_id: str
    light_frames_in_target: int
    spectral_light_frames_in_target: int
    frames_matching: int
    frames_measured: int
    # How many frames were measured, skipped (already measured) or failed.
    counts: dict[str, int] = Field(default_factory=dict)
    # For each number: how many frames had it, and its minimum, median and
    # maximum.
    summary: dict[str, dict[str, Any]] = Field(default_factory=dict)
    trends: dict[str, Any] = Field(default_factory=dict)
    frames: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""
    excluded: list[SetAsideFrame] | None = None


class SpectralFrameCheckReport(BaseModel):
    """Measurements of a target's raw spectrum frames and where they clip."""

    target_id: str
    frames_matching: int = 0
    frames_measured: int = 0
    # The frames grouped by exposure length (how many clip the zero order or
    # the spectrum) and by pier side (the spread of the streak's tilt).
    summary: dict[str, Any] = Field(default_factory=dict)
    frames: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class StackQualityReport(BaseModel):
    """Measurements of one stack, and optionally a comparison with another."""

    stack_path: str
    target_id: str | None = None
    # The sections that were asked for with ``include=``.
    included: list[str] = Field(default_factory=list)
    # Median star width (FWHM) of the brightest stars, in pixels.
    fwhm_px: float | None = None
    # Mean share of frames rejected per pixel, from the stack's rejection map.
    rejected_fraction: float | None = None
    # One row per registered frame, from the ``_Registration.seq`` file Siril
    # wrote beside the stack.
    registration: list[dict[str, float]] | None = None
    # The comparison with the previous stack or another stack, when asked for.
    comparison: StackComparison | None = None
    # Sentences about sections that could not be measured, and why.
    notes: list[str] = Field(default_factory=list)


class StackSummary(BaseModel):
    """The short description of a target's current stack.

    It is read from the quality summary that the stacking stage saved with the
    stack, so nothing is measured again.
    """

    target_id: str
    kind: Literal["imaging", "spectral"] = "imaging"
    stack_path: str | None = None
    made_at: str
    frames_submitted: int
    frames_stacked: int
    frames_skipped: int
    # The first few frames left out of the stack, and why.
    skipped_reasons: list[dict[str, Any]] = Field(default_factory=list)
    frames_set_aside_before_stacking: int | None = None
    sessions: list[dict[str, Any]] = Field(default_factory=list)
    rejected_pixel_fraction: float | None = None
    rejected_fraction_flagged: bool | None = None
    # The stack's star width against what the input frames predict.
    star_width_px: dict[str, Any] = Field(default_factory=dict)
    saturated_pixel_fraction: float | None = None
    zero_pixel_fraction: float | None = None
    exposure_groups: list[dict[str, Any]] = Field(default_factory=list)
    flagged: bool = False
    flag_reasons: list[str] = Field(default_factory=list)
    calibration_mismatches: int = 0
