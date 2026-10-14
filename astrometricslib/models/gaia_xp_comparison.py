"""Data structures for checking a spectrum against Gaia DR3's XP spectrum.

The Gaia satellite measured a low-resolution spectrum (the XP spectrum) for
about 220 million stars. Its resolving power, about 30 to 100, is close to
that of a slitless grating, so it is an independent reference for the
library's calibrated spectra. Comparing the two tests the instrument
response, the extinction correction and the wavelength scale together.

This file holds only the records. `GaiaXpComparison` is the result for one
spectrum, stored on `SpectroscopyResult.gaia_xp_comparison`. `GaiaXpRunSummary`
rolls the comparisons of a whole run into one record for the run summary. The
calculations are in
`pipelines/spectroscopy/post_processing/compare_to_gaia_xp.py`.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GaiaXpBandResidual(BaseModel):
    """How one wavelength band of a spectrum compares with the XP spectrum.

    Both spectra are scaled to match at 5400-5600 Angstroms first, so the
    numbers show the change of shape across the spectrum, not its brightness.
    """

    model_config = ConfigDict(populate_by_name=True)

    # The band's edges, in Angstroms.
    start_angstrom: float = Field(alias="startAngstrom")
    end_angstrom: float = Field(alias="endAngstrom")
    # The median of (observed / XP) over the band's samples. 1.0 means the
    # two agree. A value of 1.04 means the observed spectrum is 4 percent
    # brighter than the XP spectrum, relative to 5400-5600 Angstroms. `None`
    # when the band holds too few samples.
    median_ratio: float | None = Field(default=None, alias="medianRatio")
    # The root-mean-square of (observed / XP - 1) over the band, as a
    # fraction. It includes the median offset above. `None` when the band
    # holds too few samples.
    rms_fraction: float | None = Field(default=None, alias="rmsFraction")
    # How many samples fell in the band.
    sample_count: int = Field(default=0, alias="sampleCount")


class GaiaXpComparison(BaseModel):
    """The result of checking one spectrum against its Gaia XP spectrum.

    ``status`` is ``"compared"`` when the check ran, and ``"not_checked"``
    when it could not (no Gaia id, no XP spectrum, no response-corrected
    spectrum). A not-checked record keeps its reason and has no numbers.
    """

    model_config = ConfigDict(populate_by_name=True)

    status: Literal["compared", "not_checked"] = Field(alias="status")
    # Why the check did not run. Empty when ``status`` is ``"compared"``.
    not_checked_reason: str = Field(default="", alias="notCheckedReason")
    # The Gaia DR3 source id the spectrum was compared with, or `None` when
    # the star has no known id.
    gaia_source_id: int | None = Field(default=None, alias="gaiaSourceId")
    # How many samples of the observed spectrum were compared (between 4200
    # and 8000 Angstroms, outside the atmospheric bands).
    sample_count: int = Field(default=0, alias="sampleCount")
    # The root-mean-square of (observed / XP - 1) over all compared samples,
    # as a fraction. It includes any tilt.
    residual_rms_fraction: float | None = Field(default=None, alias="residualRmsFraction")
    # The slope of a straight line fitted to observed / XP against wavelength,
    # in percent of the flux at 5500 Angstroms per 1000 Angstroms. Positive
    # means the observed spectrum is too bright in the red compared with XP.
    # This is a tilt of the instrument response or of the extinction
    # correction.
    slope_percent_per_1000_angstrom: float | None = Field(default=None, alias="slopePercentPer1000Angstrom")
    # The one-sigma error of the slope, in the same unit.
    slope_error_percent_per_1000_angstrom: float | None = Field(
        default=None, alias="slopeErrorPercentPer1000Angstrom"
    )
    # How far the observed spectrum's features sit redward of XP's, in
    # Angstroms, from the cross-correlation of the two spectra's logarithmic
    # derivatives. Positive means the observed wavelengths are too long.
    # `None` when the correlation peak was too weak to trust.
    wavelength_shift_angstrom: float | None = Field(default=None, alias="wavelengthShiftAngstrom")
    # The correlation coefficient at the peak, between -1 and 1. A low value
    # means the two spectra share few features and the shift is not reliable.
    wavelength_shift_correlation: float | None = Field(default=None, alias="wavelengthShiftCorrelation")
    # The comparison in four wavelength bands, in increasing wavelength.
    bands: list[GaiaXpBandResidual] = Field(default_factory=list, alias="bands")
    # The two spectra were scaled to match over this wavelength window, in
    # Angstroms.
    normalization_window_angstrom: list[float] = Field(
        default_factory=list, alias="normalizationWindowAngstrom"
    )
    # The median observed value in that window (in the pipeline's relative
    # units) and the median XP flux in it (in W m^-2 nm^-1). The scale of
    # the observed spectrum is arbitrary, so only their ratio means anything.
    observed_normalization: float | None = Field(default=None, alias="observedNormalization")
    xp_normalization: float | None = Field(default=None, alias="xpNormalization")
    # Whether the XP spectrum was blurred to the instrument's line spread
    # (where the instrument is the broader of the two).
    xp_was_blurred: bool = Field(default=False, alias="xpWasBlurred")
    # Whether the observed spectrum was blurred to XP's resolution (where XP
    # is the broader of the two).
    observed_was_blurred: bool = Field(default=False, alias="observedWasBlurred")
    # Whether the spectrum's own errors were used, as well as XP's, to weight
    # the straight-line fit.
    used_observed_errors: bool = Field(default=False, alias="usedObservedErrors")


class GaiaXpBandSummary(BaseModel):
    """The run's comparison with XP in one wavelength band, over all stars.

    The median ratio of observed to XP is the measured residual instrument
    response in this band: dividing the response-corrected spectra by it would
    make them agree with Gaia.
    """

    model_config = ConfigDict(populate_by_name=True)

    start_angstrom: float = Field(alias="startAngstrom")
    end_angstrom: float = Field(alias="endAngstrom")
    # The median over the compared stars of each star's median ratio
    # (observed / XP). `None` when no star had a value in this band.
    median_ratio: float | None = Field(default=None, alias="medianRatio")
    # The spread of the stars' ratios about that median: 1.4826 times the
    # median absolute deviation, which equals the standard deviation for a
    # normal distribution. `None` with fewer than two stars.
    scatter: float | None = Field(default=None, alias="scatter")
    # How many stars had a value in this band.
    star_count: int = Field(default=0, alias="starCount")


class GaiaXpRunSummary(BaseModel):
    """How a run's spectra compare with Gaia XP, summed over its stars.

    Each star counts once, however many spectra (frames) it has: its numbers
    are the median over its own spectra.
    """

    model_config = ConfigDict(populate_by_name=True)

    # How many stars were compared with an XP spectrum.
    compared_star_count: int = Field(default=0, alias="comparedStarCount")
    # The four bands, in increasing wavelength.
    bands: list[GaiaXpBandSummary] = Field(default_factory=list, alias="bands")
    # The median over the compared stars of the signed slope, in percent of
    # the flux at 5500 Angstroms per 1000 Angstroms, and of its absolute
    # value. The gate `gaia_xp_agreement` judges the second.
    median_slope_percent_per_1000_angstrom: float | None = Field(
        default=None, alias="medianSlopePercentPer1000Angstrom"
    )
    median_absolute_slope_percent_per_1000_angstrom: float | None = Field(
        default=None, alias="medianAbsoluteSlopePercentPer1000Angstrom"
    )
