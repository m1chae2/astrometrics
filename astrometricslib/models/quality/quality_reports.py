"""Data structures for the quality checks on frames and stacks.

`astrometrics.quality` measures raw frames and finished stacks, and
`processing.stacking.get_summary` reads back what the stacking stage saved.
Each check returns one of these structures. A check never saves anything, so
these only describe; they never change a target.

- `RawFrameCheckReport` is the batch check of raw light
  frames: star count, width and roundness, trailing, sky level and jumps,
  with the frames that stand out flagged.
- `SpectralFrameCheckReport` describes raw spectrum frames and where they
  clip.
- `FrameQualityReport` is what `quality.frames.measure` returns: the
  check of imaging or spectrum frames, and the frame metric set if asked.
- `StackQualityReport` holds the measurements of one stack, and optionally
  its comparison with another stack.
- `StackSummary` is the short description of a target's current stack that
  the stacking stage saved.

Rows that describe one frame are plain dictionaries. Their keys are the
measurement names, and which keys a row has depends on what could be
measured.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib.models.quality.folder_measurement import FolderMeasurementReport
from astrometricslib.models.quality.stack_comparison import StackComparison

__all__ = [
    "FrameQualityReport",
    "RawFrameCheckReport",
    "RegistrationRow",
    "SpectralFrameCheckReport",
    "StackQualityReport",
    "StackSummary",
]


class RawFrameCheckReport(BaseModel):
    """The batch check of raw light frames.

    The star width of each row (``fwhm_px``) and of the batch
    (``median_fwhm_px``) is the check's own quick estimate,
    ``fwhm_estimator`` (``"raw check second moment"``): the second moment of
    the brightest stars' boxes, given as the FWHM of a Gaussian of that
    spread. It has no value on the
    library's one star-width scale, and ``fwhm_not_on_scale_reason`` says
    why; compare it only with other raw checks.
    """

    fwhm_estimator: str | None = None
    fwhm_not_on_scale_reason: str | None = None
    # How many frames were given, and how many of them were read.
    frames_matching: int = 0
    frames_checked: int = 0
    # One row per frame: its measurements and the ``flags`` it raised.
    frames: list[dict[str, Any]] = Field(default_factory=list)
    # The batch medians and how many frames were flagged.
    batch: dict[str, Any] = Field(default_factory=dict)
    # For star width, star count and sky level: how far each moved across
    # the newest frames, with a sentence in ``alerts`` for each worrying drift.
    trends: dict[str, Any] = Field(default_factory=dict)


class SpectralFrameCheckReport(BaseModel):
    """Measurements of raw spectrum frames and where they clip."""

    frames_matching: int = 0
    frames_measured: int = 0
    # The frames grouped by exposure length (how many clip the zero order or
    # the spectrum) and by pier side (the spread of the streak's tilt).
    summary: dict[str, Any] = Field(default_factory=dict)
    frames: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class FrameQualityReport(BaseModel):
    """What the frame check found in some raw frames.

    Imaging frames always get the batch check (``checks``); with
    ``include=["metrics"]`` they also get the stored frame metric set
    (``metrics``). Spectrum frames get the spectrum check (``spectral``).
    Nothing is saved.

    Parameters
    ----------
    kind : {"imaging", "spectral"}
        The kind of frames given, worked out from their filters.
    frames_measured : `int`
        How many frames were measured.
    checks : `RawFrameCheckReport` or `None`
        Imaging frames: star count, width and roundness, trailing, sky level
        and jumps, with the frames that stand out flagged.
    metrics : `FolderMeasurementReport` or `None`
        Imaging frames, with ``include=["metrics"]``: the value of each
        ``frame.*`` metric for each frame, and the medians.
    spectral : `SpectralFrameCheckReport` or `None`
        Spectrum frames: where each frame clips, by exposure and pier side.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["imaging", "spectral"]
    frames_measured: int = 0
    checks: RawFrameCheckReport | None = None
    metrics: FolderMeasurementReport | None = None
    spectral: SpectralFrameCheckReport | None = None


class RegistrationRow(BaseModel):
    """What Siril's registration found for one frame of a stack.

    The fields match the keys that `parse_seq_file` returns for each
    registration line of the ``_Registration.seq`` file.

    Attributes
    ----------
    fwhm_px : `float`
        Width of the stars in pixels (Siril's plain FWHM).
    weighted_fwhm_px : `float`
        Siril's weighted FWHM in pixels, which counts each star by how well
        its fit went.
    roundness : `float`
        Star roundness, 0 to 1 (1 is a perfect circle).
    quality : `float`
        Siril's registration quality score for the frame.
    background_level : `float`
        Sky background level, normalized to the 0 to 1 image range.
    star_count : `int`
        The number of stars Siril matched in the frame.
    homography : `list` [`float`]
        The 3 by 3 transform that moves the frame onto the reference, as nine
        numbers in row order.
    dx, dy : `float`
        The shift in pixels (the homography's ``h02`` and ``h12``).
    rotation_deg : `float`
        The rotation in degrees.
    scale : `float`
        The scale factor (1.0 means the same size as the reference).
    """

    model_config = ConfigDict(populate_by_name=True)

    fwhm_px: float = Field(alias="fwhmPx")
    weighted_fwhm_px: float = Field(alias="weightedFwhmPx")
    roundness: float
    quality: float
    background_level: float = Field(alias="backgroundLevel")
    star_count: int = Field(alias="starCount")
    homography: list[float]
    dx: float
    dy: float
    rotation_deg: float = Field(alias="rotationDeg")
    scale: float


class StackQualityReport(BaseModel):
    """Measurements of one stack, and optionally a comparison with another."""

    stack_path: str
    target_id: str | None = None
    # The sections that were asked for with ``include=``.
    included: list[str] = Field(default_factory=list)
    # Median star width (FWHM) of the brightest stars, in pixels.
    fwhm_px: float | None = None
    # The `StarWidthEstimator` of ``fwhm_px``: ``"stack Gaussian"``. `None`
    # with no width.
    fwhm_estimator: str | None = None
    # ``fwhm_px`` on the library's one star-width scale (the per-frame
    # Gaussian of the brightest stars), in pixels, with the stack Gaussian's
    # ratio. `None` with no width.
    fwhm_on_scale_px: float | None = None
    # Mean share of frames rejected per pixel, from the stack's rejection map.
    rejected_fraction: float | None = None
    # One row per registered frame, from the ``_Registration.seq`` file Siril
    # wrote beside the stack.
    registration: list[RegistrationRow] | None = None
    # The estimator of the rows' ``fwhm_px`` and ``weighted_fwhm_px``:
    # ``"Siril registration"``, with the rows. No ratio of Siril's width to the
    # one star-width scale has been measured, so the rows have no value on it.
    registration_fwhm_estimator: str | None = None
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
    # The id of the stacking job that made this stack. A reader lists that
    # job's warnings with `Jobs.query(job_id=..., detail="warnings")`. `None`
    # for a stack saved before jobs were recorded, or made with no job.
    job_id: str | None = None
    made_at: str
    frames_submitted: int
    frames_stacked: int
    frames_skipped: int
    # The first few frames left out of the stack, and why.
    skipped_reasons: list[dict[str, Any]] = Field(default_factory=list)
    frames_set_aside_before_stacking: int | None = None
    sessions: list[dict[str, Any]] = Field(default_factory=list)
    rejected_pixel_fraction: float | None = None
    # The stack's star width against what the input frames predict.
    star_width_px: dict[str, Any] = Field(default_factory=dict)
    saturated_pixel_fraction: float | None = None
    zero_pixel_fraction: float | None = None
    exposure_groups: list[dict[str, Any]] = Field(default_factory=list)
    calibration_mismatches: int = 0
