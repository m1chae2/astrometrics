"""Data structures for the wavelength zero point of a spectrum and of a run.

A spectrum's wavelength scale is anchored on the centre of the star's
zero-order image (the undispersed image of the star). If that image is
saturated, crowded, or pulled by a neighbour, the anchor moves and every
wavelength of that spectrum shifts by the same amount. This is the zero-point
error.

`WavelengthZeroPointRecord` holds what the pipeline measured for one spectrum:
the shift, how sure it is, which known lines gave it, and whether the pipeline
removed it. `WavelengthScaleSummary` rolls the records of a whole run up into
the scatter the run-level `wavelength_scale` gate reads.
"""

from pydantic import BaseModel, ConfigDict, Field


class WavelengthZeroPointLine(BaseModel):
    """One absorption line used to measure a spectrum's zero-point offset.

    The pipeline looks for the dip a known line makes and compares where it
    found the dip with the line's known (rest) wavelength.
    """

    model_config = ConfigDict(populate_by_name=True)

    # The line's name, for example "H-alpha".
    name: str = Field(alias="name")
    # The line's known wavelength in air, in Angstroms.
    rest_wavelength_angstrom: float = Field(alias="restWavelengthAngstrom")
    # Where the dip's centre was measured, in Angstroms.
    measured_wavelength_angstrom: float = Field(alias="measuredWavelengthAngstrom")
    # Measured minus rest, in Angstroms. Positive means the spectrum's
    # wavelengths read too high.
    offset_angstrom: float = Field(alias="offsetAngstrom")
    # The error of `offset_angstrom`, in Angstroms: the fit error and the
    # uncertainty of the line's rest position added in quadrature.
    uncertainty_angstrom: float = Field(alias="uncertaintyAngstrom")
    # The depth of the dip as a fraction of the continuum (0.1 is 10% deep).
    depth: float = Field(alias="depth")
    # How many noise widths deep the dip is. The pipeline keeps a line only
    # when this reaches `MINIMUM_LINE_SIGNIFICANCE`.
    significance: float = Field(alias="significance")


class WavelengthZeroPointRecord(BaseModel):
    """The zero-point offset measured for one spectrum, and what was done.

    The offset is the error-weighted mean of measured minus rest wavelength
    over the lines found. The pipeline shifts the spectrum's wavelengths by it
    only when at least two lines agree with each other within their errors.
    """

    model_config = ConfigDict(populate_by_name=True)

    # The weighted mean offset, in Angstroms, before any correction. `None`
    # when no line passed the significance test.
    offset_angstrom: float | None = Field(default=None, alias="offsetAngstrom")
    # The error of the weighted mean, in Angstroms. `None` with no lines.
    uncertainty_angstrom: float | None = Field(default=None, alias="uncertaintyAngstrom")
    # How many lines passed the significance test.
    line_count: int = Field(default=0, alias="lineCount")
    # The chi-square (a sum of squared, error-scaled differences) of the
    # lines' offsets about their mean. `None` with fewer than two lines.
    chi_square: float | None = Field(default=None, alias="chiSquare")
    # The chance of a chi-square this large if the lines truly agree. Small
    # means the lines disagree. `None` with fewer than two lines.
    agreement_p_value: float | None = Field(default=None, alias="agreementPValue")
    # `True` when the pipeline shifted this spectrum's wavelengths by
    # `offset_angstrom`. `False` when the offset was only measured.
    is_applied: bool = Field(default=False, alias="isApplied")
    # The fraction of the zero-order image that was saturated, 0 to 1.
    # `None` when it was not measured.
    zero_order_saturated_pixel_fraction: float | None = Field(
        default=None, alias="zeroOrderSaturatedPixelFraction"
    )
    # Whether that fraction reached the run's saturation flag level. `None`
    # when it was not measured.
    is_zero_order_saturated: bool | None = Field(default=None, alias="isZeroOrderSaturated")
    # The lines behind the offset, in order of rest wavelength.
    lines: list[WavelengthZeroPointLine] = Field(default_factory=list, alias="lines")


class WavelengthScaleSummary(BaseModel):
    """How well the wavelength scale held across the spectra of a run.

    Built from the pre-processing checkpoint of each spectrum. It counts only
    spectra with a measured offset.
    """

    model_config = ConfigDict(populate_by_name=True)

    # How many spectra had a measured offset.
    spectrum_count: int = Field(alias="spectrumCount")
    # How many of those had the offset applied.
    applied_count: int = Field(alias="appliedCount")
    # The root mean square (RMS) of the offsets before any correction, in
    # Angstroms.
    rms_before_correction_angstrom: float = Field(alias="rmsBeforeCorrectionAngstrom")
    # The RMS of what is left after correction, in Angstroms. A spectrum with
    # the offset applied counts as its offset's uncertainty; any other counts
    # as its full offset.
    rms_after_correction_angstrom: float = Field(alias="rmsAfterCorrectionAngstrom")
    # The Pearson correlation (-1 to 1) between a spectrum's absolute offset
    # and its zero-order saturated fraction. `None` when fewer than three
    # spectra have both numbers or either one does not vary.
    offset_saturation_correlation: float | None = Field(default=None, alias="offsetSaturationCorrelation")
    # How many spectra went into that correlation.
    correlation_spectrum_count: int = Field(default=0, alias="correlationSpectrumCount")
