"""Data structures for judging one stack: its inputs and its result.

`quality_summary.py` records how a whole stacking run went. This module
splits that judgement into two parts, the way the spectroscopy pipeline
does for a single star:

- `StackingInputQuality` says whether the frames and calibration data that
  went into the stack were good enough. It uses only what was known before
  the stacking engine ran.
- `StackingOutputQuality` says whether the stacked image itself came out
  well. It uses only measurements of the finished stack.

Each object carries its own `flag_reasons`, so a reader can tell a bad
input from a bad result without re-deriving either. Both are attached to
`StackQualitySummary` as optional fields.
"""

from pydantic import BaseModel, ConfigDict, Field


class StackingInputQuality(BaseModel):
    """Whether the frames and calibration data going into a stack were sound.

    Answers "was this stack worth running?" using only facts known before
    the stacking engine started: how many frames survived the pre-checks,
    whether the sky background changed during the session, and whether the
    flat frames could be trusted.
    """

    model_config = ConfigDict(populate_by_name=True)

    # How many frames were handed to the stage, before any were set aside.
    frames_submitted: int = Field(alias="framesSubmitted")
    # How many were left after the gain and background checks, so how many
    # the stacking engine was given.
    frames_accepted: int = Field(alias="framesAccepted")
    # Frames set aside because their camera gain differed from the rest.
    frames_excluded_for_gain: int = Field(default=0, alias="framesExcludedForGain")
    # Frames set aside because their sky background was at a different
    # level from the rest (clouds, twilight, a lamp).
    frames_excluded_for_background: int = Field(default=0, alias="framesExcludedForBackground")
    # Whether the frames fell into two sky-background groups.
    background_split_detected: bool = Field(default=False, alias="backgroundSplitDetected")
    background_split_detail: str | None = Field(default=None, alias="backgroundSplitDetail")

    # The master flat's quality. `None` when the stack used no flats. The
    # noise is the relative noise of the flat, so 0.005 is 0.5%.
    flat_frame_count: int | None = Field(default=None, alias="flatFrameCount")
    flat_noise_fraction: float | None = Field(default=None, alias="flatNoiseFraction")
    flat_smoothing_sigma_px: float | None = Field(default=None, alias="flatSmoothingSigmaPx")
    flat_calibration_issues: list[str] = Field(default_factory=list, alias="flatCalibrationIssues")
    # Metadata differences between the lights and the calibration frames
    # (for example a different gain), one sentence each.
    calibration_mismatch_flags: list[str] = Field(default_factory=list, alias="calibrationMismatchFlags")

    # True when any check above found a problem.
    is_flagged: bool = Field(default=False, alias="isFlagged")
    # One sentence per problem, in plain words.
    flag_reasons: list[str] = Field(default_factory=list, alias="flagReasons")


class StackingOutputQuality(BaseModel):
    """Whether a finished stack came out well.

    Answers "can this stacked image be trusted?" using only measurements of
    the stacked file and of the per-frame results the engine reported.
    Each measurement is `None` when it does not apply or could not be made
    (for example the sharpness comparison is only made for images, not for
    spectra).
    """

    model_config = ConfigDict(populate_by_name=True)

    # The share of pixel values the stacking engine threw out, 0 to 1.
    rejected_pixel_fraction: float | None = Field(default=None, alias="rejectedPixelFraction")
    # The share of pixels at the camera's saturation level, 0 to 1.
    saturated_pixel_fraction: float | None = Field(default=None, alias="saturatedPixelFraction")
    # The share of pixels that are exactly zero, 0 to 1 (for a colour stack,
    # in its worst channel). A mostly-zero stack has been over-subtracted and
    # is blank.
    zero_pixel_fraction: float | None = Field(default=None, alias="zeroPixelFraction")
    # The worst "many negative pixels" percentage the engine reported after
    # subtracting the dark, or `None` if it did not warn.
    negative_pixel_max_percent: int | None = Field(default=None, alias="negativePixelMaxPercent")
    # Star width (full width at half maximum, in pixels) in the stack and
    # the median of the input frames. Images only.
    stacked_fwhm_px: float | None = Field(default=None, alias="stackedFwhmPx")
    median_input_fwhm_px: float | None = Field(default=None, alias="medianInputFwhmPx")
    # How many spectral frames the registration check questioned. Spectra
    # only.
    spectral_registration_concern_count: int = Field(default=0, alias="spectralRegistrationConcernCount")

    # True when any check above found a problem.
    is_flagged: bool = Field(default=False, alias="isFlagged")
    # One sentence per problem, in plain words.
    flag_reasons: list[str] = Field(default_factory=list, alias="flagReasons")
