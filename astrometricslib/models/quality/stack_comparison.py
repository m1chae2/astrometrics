"""Data structures for comparing two stacks of the same target.

A restack changes the picture, and it is not obvious by eye whether it changed
for the better. These structures hold the numbers that say how the large-scale
brightness pattern, the pixel noise and the star width of two stacks differ.

- `StackMeasurements` holds what was measured on one stack.
- `StackComparison` holds two of them and a plain sentence for each change.
"""

from pydantic import BaseModel, ConfigDict, Field


class StackMeasurements(BaseModel):
    """What was measured on one stacked image.

    Every measurement uses the whole image and none of them needs the two
    stacks to be aligned, so a comparison works across a change of frames.
    """

    model_config = ConfigDict(populate_by_name=True)

    path: str
    # The median pixel value, in the stack's own units: the sky level.
    sky_level: float = Field(alias="skyLevel")
    # Noise from pixel to pixel, as a fraction of the sky level. It comes from
    # the difference of neighbouring pixels that are not stars, so a smooth
    # sky gradient does not count. Lower is cleaner.
    noise_fraction: float = Field(alias="noiseFraction")
    # How much the sky brightness varies across the frame on scales of about
    # 64 pixels, as a fraction of the sky level: the root mean square of the
    # block medians left after blocks with stars or nebulae are dropped. Lower
    # is flatter, which means less leftover vignetting and fewer dust shadows.
    flatness_rms: float = Field(alias="flatnessRms")
    # The same blocks' brightest minus faintest, as a fraction of the sky.
    flatness_peak_to_peak: float = Field(alias="flatnessPeakToPeak")
    # Median star width (FWHM) in pixels, from a Gaussian fit. `None` if too
    # few stars could be fitted.
    fwhm_px: float | None = Field(default=None, alias="fwhmPx")
    # Share of pixels at exactly zero, a sign of a blank or over-subtracted
    # stack.
    zero_fraction: float = Field(alias="zeroFraction")


class StackComparison(BaseModel):
    """Two stacks of the same target, measured and compared."""

    model_config = ConfigDict(populate_by_name=True)

    before: StackMeasurements
    after: StackMeasurements
    # Relative change of each measurement, after against before, as a fraction
    # (-0.5 is half as large). Keys are the measurement names.
    changes: dict[str, float | None] = Field(default_factory=dict)
    # One plain sentence for each measurement, saying what moved.
    summary: list[str] = Field(default_factory=list)
