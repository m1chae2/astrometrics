"""Data structures for comparing two stacks of the same target.

A restack changes the picture, and it is not obvious by eye whether it changed
for the better. These structures hold the numbers that say how the sky level,
the noise in empty sky, the large-scale brightness pattern and the star width
of two stacks differ.

- `StackMeasurements` holds what was measured on one stack, or the reason a
  number could not be measured.
- `StackComparison` holds two of them and a plain sentence for each change.
"""

from pydantic import BaseModel, ConfigDict, Field


class StackMeasurements(BaseModel):
    """What was measured on one stacked image.

    A number that could not be measured is `None`, and ``not_measured`` says
    why. No number is ever replaced by a default.
    """

    model_config = ConfigDict(populate_by_name=True)

    path: str
    # The median of the stack's usable pixels (not zero, not NaN), in the
    # stack's own units. This is the sky level the quality rows use
    # (`sky_level_of_plane`).
    sky_level: float | None = Field(default=None, alias="skyLevel")
    # The noise in empty sky as a fraction of the reference stars' brightness:
    # the same number as the target report's
    # `stacking.empty_aperture_noise_normalized`. Both stacks are measured in
    # the apertures the library stack chose. Lower is cleaner.
    normalized_noise: float | None = Field(default=None, alias="normalizedNoise")
    # Its 1-sigma uncertainty, from the same measurement.
    normalized_noise_uncertainty: float | None = Field(default=None, alias="normalizedNoiseUncertainty")
    # Fine-grain noise: the pixel-to-pixel spread as a fraction of the sky
    # level, from the difference of neighbouring pixels that are not stars. It
    # sees only the finest scale and is not the noise number of the target
    # report. Lower is cleaner.
    fine_grain_noise_fraction: float | None = Field(default=None, alias="fineGrainNoiseFraction")
    # How much the sky brightness varies across the frame on scales of about
    # 64 pixels, as a fraction of the sky level: the root mean square of the
    # block medians left after blocks with stars or nebulae are dropped. Lower
    # is flatter, which means less leftover vignetting and fewer dust shadows.
    flatness_rms: float | None = Field(default=None, alias="flatnessRms")
    # The same blocks' brightest minus faintest, as a fraction of the sky.
    flatness_peak_to_peak: float | None = Field(default=None, alias="flatnessPeakToPeak")
    # Median star width (FWHM) in pixels, from a Gaussian fit. `None` if too
    # few stars could be fitted.
    fwhm_px: float | None = Field(default=None, alias="fwhmPx")
    # Share of pixels at exactly zero, a sign of a blank or over-subtracted
    # stack (the empty edge of a registered stack counts).
    zero_fraction: float | None = Field(default=None, alias="zeroFraction")
    # Why each number that is `None` was not measured. Keys are the names of
    # the fields above.
    not_measured: dict[str, str] = Field(default_factory=dict, alias="notMeasured")
    # How the noise measurement found its reference frame and trimmed edges,
    # and anything that limits it.
    notes: list[str] = Field(default_factory=list)


class StackComparison(BaseModel):
    """Two stacks of the same target, measured and compared."""

    model_config = ConfigDict(populate_by_name=True)

    before: StackMeasurements
    after: StackMeasurements
    # Relative change of each measurement, after against before, as a fraction
    # (-0.5 is half as large). Keys are the measurement names. `None` when
    # either stack has no such number.
    changes: dict[str, float | None] = Field(default_factory=dict)
    # One plain sentence for each measurement, saying what moved or why it was
    # not measured. The comparison does not say which stack is better.
    summary: list[str] = Field(default_factory=list)
