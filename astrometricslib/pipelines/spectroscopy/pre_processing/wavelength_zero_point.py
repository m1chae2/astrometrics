"""Measures and corrects the wavelength zero point of one spectrum.

The pipeline turns a position along the trail into a wavelength with the
grating equation, ``wavelength = d * sin(arctan(x / L))`` (see
`optics_physics`). One fitted number, the grating distance ``L``, sets the
scale. The position ``x`` is counted from the centre of the star's zero-order
image (the undispersed image of the star). If that image is saturated, crowded
or pulled by a neighbour, the centre is wrong by a few pixels. Every wavelength
of that spectrum then shifts by the same amount. This is the zero-point error.
Nothing else in the pipeline measures it for a single spectrum.

How the measurement works, for one spectrum:

1. For each line in `ZERO_POINT_LINES`, take the part of the spectrum around
   the line's known wavelength, leaving out the atmospheric bands (see
   `atmospheric_mask`) other than the line itself. Two side bands, one on each
   side of the search range, show the continuum (the spectrum without lines).
   A parabola fitted to them, divided out, gives the continuum-normalized
   spectrum: 1.0 where there is no line, less than 1.0 inside a dip.
2. Slide a Gaussian dip, as wide as the instrument blur at that wavelength
   (the line spread, from `load_line_spread_profile`), across plus or minus
   one line spread around the known wavelength. At each position, fit the
   parabola and the dip's depth together, so the depth's error includes the
   error of the continuum under the dip. Keep the position with the strongest
   dip.
3. Keep the line only if the dip is at least `MINIMUM_LINE_SIGNIFICANCE`
   noise widths deep and its centre is inside the searched range, not on the
   edge. Noise is the scatter of the two side bands, made larger when
   neighbouring samples are correlated. Pure noise does not pass this test.
4. The line's offset is its measured centre minus its known wavelength. Its
   error is the fit error and the uncertainty of the line's own position
   (blends, stellar motion) added in quadrature.

The type of the star is not known at this stage, so every line is tried and
only the lines that pass step 3 count. A cool star has no H-gamma, and the
test rejects it.

The spectrum's offset is the error-weighted mean of the lines' offsets. Two
tests then decide whether to remove it:

* At least `MINIMUM_LINES_TO_APPLY` lines must be found.
* The lines must agree with each other within their errors: a chi-square test
  (a sum of squared, error-scaled differences from the mean) with a p-value
  of at least `AGREEMENT_MINIMUM_P_VALUE`.

When both hold, the pipeline subtracts the offset from every wavelength. When
they do not, the offset is only recorded. A single line cannot tell a
zero-point error from a misidentified line, and lines that disagree point to a
wrong scale rather than a shifted one.

Order in the pipeline: this runs first in `_apply_result_to_stellar_object`,
before the quantum-efficiency, instrument-response and airmass-extinction
corrections. All three of those depend on wavelength, so each needs the
corrected one. The response is fitted at fixed wavelengths: a 25 A shift would
put the response curve's features in the wrong place.

Limits of the method:

* The correction is a constant shift. The zero-order error moves the anchor,
  and the physical model turns that into a nearly constant shift, so the
  model is a good fit. A wrong ``L`` instead stretches the scale; the lines
  then disagree and the offset is not applied.
* A line cannot be measured when the true offset is larger than about one
  line spread, because the search range ends there.
* A different feature close to a line can be mistaken for it. The G band
  (CH, 4304 A) of G and K stars lies 36 A from H-gamma. The chi-square test
  catches this only when other lines are present to disagree with it.
"""

import logging
import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import chi2

from astrometricslib.models.spectroscopy_quality import StageQualityMetric, metric
from astrometricslib.models.wavelength_scale import WavelengthZeroPointLine, WavelengthZeroPointRecord
from astrometricslib.pipelines.shared.quality.saturation import is_saturation_significant
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_mask import ATMOSPHERIC_BANDS_ANGSTROM
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    FWHM_PER_SIGMA,
    ResolutionProfile,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ZeroPointLine:
    """A known line that can anchor the wavelength scale.

    Attributes
    ----------
    name : `str`
        The line's name.
    rest_wavelength_angstrom : `float`
        The line's wavelength in air, in Angstroms.
    intrinsic_fwhm_angstrom : `float`
        How wide the line already is before the instrument blurs it, in
        Angstroms (the full width at half the depth). A single Balmer line is
        narrow compared with the blur, so it is 0. A doublet, a triplet or a
        molecular band is wider.
    rest_uncertainty_angstrom : `float`
        How well the position of the dip is known even with a perfect
        instrument, in Angstroms. A blend of close lines has no single
        wavelength, and a star's motion toward or away from us shifts the
        line. This is added to the fit error.
    atmospheric_band : `str` or `None`
        The name (in `ATMOSPHERIC_BANDS_ANGSTROM`) of the atmospheric band
        this line is, if it is one. The measurement leaves every other
        atmospheric band out of the spectrum, because they would bend the
        continuum next to the line.
    """

    name: str
    rest_wavelength_angstrom: float
    intrinsic_fwhm_angstrom: float
    rest_uncertainty_angstrom: float
    atmospheric_band: str | None = None


# The lines tried on every spectrum, in order of wavelength. All wavelengths
# are in air. The Balmer lines are strong in A, F and early G stars. Na D (a
# doublet at 5889.95 and 5895.92 A) and Mg b (a triplet at 5167.3, 5172.7 and
# 5183.6 A) are strong in G and K stars. The O2 A band is made by Earth's air,
# so every spectrum has it whatever the star is; its 7605 A is the middle of
# the band limits in `atmospheric_mask`. The widths and uncertainties are
# judgements, not measurements: 2 A for a Balmer line (blends with metal lines
# and stellar motion up to about 100 km/s), 3 A for Na D, 4 A for Mg b, and 8 A
# for the A band, whose centre in blurred Vega and Alcor spectra was 7609 to
# 7615 A (see `atmospheric_mask`).
ZERO_POINT_LINES: tuple[ZeroPointLine, ...] = (
    ZeroPointLine("H-gamma", 4340.5, 0.0, 2.0),
    ZeroPointLine("H-beta", 4861.3, 0.0, 2.0),
    ZeroPointLine("Mg b", 5175.0, 17.0, 4.0),
    ZeroPointLine("Na D", 5892.9, 6.0, 3.0),
    ZeroPointLine("H-alpha", 6562.8, 0.0, 2.0),
    ZeroPointLine("O2 A band", 7605.0, 80.0, 8.0, "oxygen A band"),
)

# How deep, in noise widths, a dip must be to count as a measured line. The
# noise is estimated from only 20 to 30 side-band samples, so the tail of the
# significance is heavier than a Gaussian's. Measured on synthetic noise-only
# spectra, with every line tried in each:
#
# * 1500 flat trails with Poisson and read noise, line spread 40 A (9000
#   trials): 4 reached 5.0 widths, 1 reached 5.5 (5.85), none reached 6.0.
# * 1000 sloped, curved continua with 2% white noise at 11.3 A per sample,
#   using the camera's stored line spread of 100 to 150 A (6000 trials): 1
#   reached 5.0, none reached 6.0.
#
# 6.0 keeps a margin. Real lines in a clean spectrum reach 20 or more. Not
# checked on real faint spectra, whose noise may be correlated.
MINIMUM_LINE_SIGNIFICANCE = 6.0

# The fewest independent lines needed before the offset is removed. One line
# cannot be told apart from a misidentified feature.
MINIMUM_LINES_TO_APPLY = 2

# The least p-value of the chi-square test for the lines to count as agreeing.
# A designed value: with it, truly agreeing lines are wrongly refused 1 time in
# 100. Not tuned on data.
AGREEMENT_MINIMUM_P_VALUE = 0.01

# The limit on the size of a spectrum's offset: half a resolution element at
# 5000 A. Designed, not measured. The resolution element is about 40 A there
# (Vega: 38 to 43 A, see `spectral_resolution`), so the limit is about 20 A.
# An offset this large moves a line by half its own blur.
ZERO_POINT_OFFSET_LIMIT_ANGSTROM = 20.0

# Step of the search for a line's centre, in Angstroms. Far below the fit
# errors (about 1 A or more) so that the grid does not matter.
SEARCH_STEP_ANGSTROM = 0.5

# The nearest edge of the side bands is this many effective line widths (FWHM)
# beyond the search range. That clears the Gaussian dip's wing (3.5 sigma is
# 1.5 widths) when the dip sits at the end of the search range.
SIDE_BAND_GAP_WIDTHS = 1.5

# How wide each side band is, in effective line widths, and the fewest samples
# it is made to hold. The noise is estimated from the side bands, so a band
# of only a few samples gives a poor estimate. Where the samples are spaced
# widely compared with the line (a narrow line spread), the band is made wide
# enough for `MINIMUM_NOISE_SAMPLES_PER_SIDE`. Where the line spread is broad
# (the stored profile gives 100 to 150 A), 1.5 widths already hold plenty, and
# a wider band would reach other lines.
SIDE_BAND_WIDTH_WIDTHS = 1.5
MINIMUM_NOISE_SAMPLES_PER_SIDE = 10

# The fewest samples in each side band, and in the line's own range. Below
# these the curve through the side bands or the dip itself is not constrained.
MINIMUM_SIDE_BAND_SAMPLES = 6
MINIMUM_CORE_SAMPLES = 6

# A side-band sample further than this many scatters from the fitted curve is
# a cosmic-ray hit or a neighbouring line, and is dropped once.
SIDE_BAND_CLIP_SIGMAS = 4.0

# The most the noise is enlarged for correlated neighbouring samples. A lag-1
# correlation of 0.8 enlarges it threefold.
MAXIMUM_NOISE_CORRELATION = 0.8

# Samples must be at least this close together, in units of the dip's sigma,
# for the dip to span several samples.
MAXIMUM_SAMPLE_SPACING_SIGMAS = 1.5


def _line_spread_angstrom(
    rest_wavelength_angstrom: float, profile: ResolutionProfile | None, fallback_angstrom: float
) -> float:
    """Give the instrument blur (FWHM) at one wavelength.

    Parameters
    ----------
    rest_wavelength_angstrom : `float`
        The wavelength, in Angstroms.
    profile : `ResolutionProfile` or `None`
        The stored line-spread profile of the camera, if there is one.
    fallback_angstrom : `float`
        The blur to use when there is no profile.

    Returns
    -------
    spread_angstrom : `float`
        The line spread at that wavelength, in Angstroms.
    """
    if profile is None:
        return float(fallback_angstrom)
    return float(profile.at(np.array([rest_wavelength_angstrom]))[0])


def _side_band_noise(side_residuals: list[np.ndarray]) -> float | None:
    """Estimate the noise of a normalized spectrum from its side bands.

    The scatter of the side-band residuals is the noise of one sample. Samples
    next to each other can share noise, and then a dip several samples wide is
    harder to tell from noise than the scatter suggests. The estimate is made
    larger by the lag-1 correlation of the residuals: for a first-order
    autoregressive series the noise of an average grows by
    ``sqrt((1 + rho) / (1 - rho))``.

    Parameters
    ----------
    side_residuals : `list` [`numpy.ndarray`]
        The normalized residuals of each side band, in wavelength order.

    Returns
    -------
    noise : `float` or `None`
        The noise of a dip's amplitude per sample, or `None` if it cannot be
        estimated.
    """
    pooled = np.concatenate(side_residuals)
    if pooled.size < 4:
        return None
    # Three numbers were fitted (the parabola), so three degrees of freedom
    # are lost.
    scatter = float(np.sqrt(np.sum(pooled**2) / max(pooled.size - 3, 1)))
    products = 0.0
    squares = 0.0
    for residual in side_residuals:
        if residual.size >= 2:
            products += float(np.sum(residual[:-1] * residual[1:]))
            squares += float(np.sum(residual[:-1] ** 2))
    correlation = min(max(products / squares, 0.0), MAXIMUM_NOISE_CORRELATION) if squares > 0 else 0.0
    noise = scatter * math.sqrt((1.0 + correlation) / (1.0 - correlation))
    return max(noise, 1e-9)


def measure_line(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    line: ZeroPointLine,
    spread_angstrom: float,
) -> WavelengthZeroPointLine | None:
    """Look for one known line and measure where its dip is centred.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The spectrum's wavelengths, in Angstroms, in increasing order. Every
        value is finite.
    intensity : `numpy.ndarray`
        The spectrum's brightness at each wavelength. Every value is finite.
    line : `ZeroPointLine`
        The line to look for.
    spread_angstrom : `float`
        The instrument blur (FWHM) at the line's wavelength, in Angstroms.
        The search covers plus or minus this much around the known
        wavelength.

    Returns
    -------
    measured : `WavelengthZeroPointLine` or `None`
        The measurement, or `None` when the spectrum does not cover the line,
        no dip reaches `MINIMUM_LINE_SIGNIFICANCE`, or the best position is on
        the edge of the search range.
    """
    rest = line.rest_wavelength_angstrom
    effective_fwhm = math.hypot(spread_angstrom, line.intrinsic_fwhm_angstrom)
    sigma = effective_fwhm / FWHM_PER_SIGMA
    inner = spread_angstrom + SIDE_BAND_GAP_WIDTHS * effective_fwhm
    if wavelength_angstrom.size < 2:
        return None
    sample_spacing = float(np.median(np.diff(wavelength_angstrom)))
    outer = inner + max(
        SIDE_BAND_WIDTH_WIDTHS * effective_fwhm, MINIMUM_NOISE_SAMPLES_PER_SIDE * sample_spacing
    )

    # Leave out the atmospheric bands that are not this line, so they do not
    # bend the continuum beside it.
    in_other_band = np.zeros(wavelength_angstrom.shape, dtype=bool)
    for band_name, band_start, band_end in ATMOSPHERIC_BANDS_ANGSTROM:
        if band_name != line.atmospheric_band:
            in_other_band |= (wavelength_angstrom >= band_start) & (wavelength_angstrom <= band_end)
    wavelength_angstrom = wavelength_angstrom[~in_other_band]
    intensity = intensity[~in_other_band]

    offsets = wavelength_angstrom - rest
    distance = np.abs(offsets)
    in_core = distance < inner
    in_side = (distance >= inner) & (distance <= outer)
    left = in_side & (offsets < 0)
    right = in_side & (offsets > 0)
    if (
        left.sum() < MINIMUM_SIDE_BAND_SAMPLES
        or right.sum() < MINIMUM_SIDE_BAND_SAMPLES
        or in_core.sum() < MINIMUM_CORE_SAMPLES
    ):
        return None
    if np.median(np.diff(wavelength_angstrom[in_core | in_side])) > MAXIMUM_SAMPLE_SPACING_SIGMAS * sigma:
        return None

    # Work with brightness in units of the side bands' median, so the numbers
    # are close to 1 whatever units the spectrum is in.
    reference = float(np.median(intensity[in_side]))
    if reference <= 0:
        return None
    scaled_brightness = intensity / reference
    scaled_offsets = offsets / outer

    # The continuum guess: a parabola through the side bands, dropping once any
    # sample far from it (a cosmic-ray hit or a neighbouring line).
    keep = in_side.copy()
    coefficients = np.polyfit(scaled_offsets[keep], scaled_brightness[keep], 2)
    residual = scaled_brightness - np.polyval(coefficients, scaled_offsets)
    scatter = float(np.std(residual[keep]))
    if scatter > 0:
        keep &= np.abs(residual) <= SIDE_BAND_CLIP_SIGMAS * scatter
        if keep.sum() < 2 * MINIMUM_SIDE_BAND_SAMPLES:
            return None
        coefficients = np.polyfit(scaled_offsets[keep], scaled_brightness[keep], 2)
        residual = scaled_brightness - np.polyval(coefficients, scaled_offsets)
    noise = _side_band_noise([residual[left & keep], residual[right & keep]])
    if noise is None:
        return None

    # Fit the continuum and a Gaussian dip together. The brightness is
    # modelled as ``parabola - depth * continuum * gaussian``. For a fixed
    # centre this is linear in the parabola's three numbers and the depth.
    # Fitting them together makes the depth's error include the error of the
    # continuum under the dip. A continuum fitted first and treated as exact
    # would make noise look like lines.
    region = in_core | (in_side & keep)
    region_offsets = offsets[region]
    region_brightness = scaled_brightness[region]
    region_continuum = np.polyval(coefficients, scaled_offsets[region])
    if np.any(region_continuum <= 0):
        return None
    parabola = np.vander(scaled_offsets[region], 3)
    # M removes whatever a parabola can describe from a vector.
    remover = np.eye(region_offsets.size) - parabola @ np.linalg.pinv(parabola)
    shifts = np.arange(-spread_angstrom, spread_angstrom + 1e-9, SEARCH_STEP_ANGSTROM)
    dip_shapes = -region_continuum * np.exp(
        -0.5 * ((region_offsets[np.newaxis, :] - shifts[:, np.newaxis]) / sigma) ** 2
    )
    cleaned_shapes = dip_shapes @ remover
    shape_power = np.sum(cleaned_shapes * dip_shapes, axis=1)
    depths = (cleaned_shapes @ region_brightness) / shape_power
    significance = depths * np.sqrt(shape_power) / noise
    best = int(np.argmax(significance))
    if best in (0, shifts.size - 1) or significance[best] < MINIMUM_LINE_SIGNIFICANCE:
        return None

    # The error of the centre, with the parabola and the depth both free: how
    # fast the model changes with the centre, after taking out what the
    # parabola and the depth can absorb.
    depth = float(depths[best])
    slope = depth * dip_shapes[best] * (region_offsets - shifts[best]) / sigma**2
    free_terms = np.column_stack([parabola, dip_shapes[best]])
    absorber = np.eye(region_offsets.size) - free_terms @ np.linalg.pinv(free_terms)
    slope_power = float(slope @ absorber @ slope)
    if slope_power <= 0:
        return None
    centre = rest + float(shifts[best])
    centre_error = noise / math.sqrt(slope_power)
    uncertainty = math.hypot(centre_error, line.rest_uncertainty_angstrom)
    return WavelengthZeroPointLine(
        name=line.name,
        rest_wavelength_angstrom=float(rest),
        measured_wavelength_angstrom=float(centre),
        offset_angstrom=float(centre - rest),
        uncertainty_angstrom=float(uncertainty),
        depth=depth,
        significance=float(significance[best]),
    )


def combine_line_offsets(
    lines: list[WavelengthZeroPointLine],
) -> tuple[float, float, float | None, float | None]:
    """Combine the lines' offsets into one error-weighted mean.

    Parameters
    ----------
    lines : `list` [`WavelengthZeroPointLine`]
        At least one measured line.

    Returns
    -------
    offset : `float`
        The weighted mean of the offsets, in Angstroms. Each line's weight is
        one over its squared error.
    uncertainty : `float`
        The error of that mean, in Angstroms.
    chi_square : `float` or `None`
        The sum of the squared, error-scaled differences of the offsets from
        the mean. `None` for a single line.
    p_value : `float` or `None`
        The chance of a chi-square at least this large if the lines agree.
        `None` for a single line.
    """
    offsets = np.array([line.offset_angstrom for line in lines])
    errors = np.array([line.uncertainty_angstrom for line in lines])
    weights = 1.0 / errors**2
    mean = float(np.sum(weights * offsets) / np.sum(weights))
    uncertainty = float(1.0 / np.sqrt(np.sum(weights)))
    if len(lines) < 2:
        return mean, uncertainty, None, None
    chi_square = float(np.sum(((offsets - mean) / errors) ** 2))
    return mean, uncertainty, chi_square, float(chi2.sf(chi_square, len(lines) - 1))


def measure_wavelength_zero_point(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    resolution_element_angstrom: float,
    line_spread_profile: ResolutionProfile | None = None,
    zero_order_saturated_pixel_fraction: float | None = None,
) -> WavelengthZeroPointRecord:
    """Measure one spectrum's zero-point offset and decide whether to apply it.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The spectrum's wavelengths, in Angstroms.
    intensity : `numpy.ndarray`
        The spectrum's brightness at each wavelength. It can be in any
        units and need not be flat; each line is normalized on its own.
        Samples that are not finite are left out.
    resolution_element_angstrom : `float`
        The instrument blur (FWHM) to use at any wavelength the line-spread
        profile does not cover, and everywhere when there is no profile.
    line_spread_profile : `ResolutionProfile`, optional
        The camera's stored line-spread profile (see
        `load_line_spread_profile`). It gives the blur at each line.
    zero_order_saturated_pixel_fraction : `float`, optional
        The fraction of the zero-order image that was saturated, 0 to 1.
        Only recorded.

    Returns
    -------
    record : `WavelengthZeroPointRecord`
        The offset, its error, the lines behind it, and ``is_applied``. The
        spectrum itself is not changed; use `shift_wavelengths`.
    """
    wavelengths = np.asarray(wavelength_angstrom, dtype=float)
    brightness = np.asarray(intensity, dtype=float)
    usable = np.isfinite(wavelengths) & np.isfinite(brightness)
    order = np.argsort(wavelengths[usable])
    wavelengths = wavelengths[usable][order]
    brightness = brightness[usable][order]

    lines: list[WavelengthZeroPointLine] = []
    if wavelengths.size >= 2 * MINIMUM_SIDE_BAND_SAMPLES + MINIMUM_CORE_SAMPLES:
        for line in ZERO_POINT_LINES:
            spread = _line_spread_angstrom(
                line.rest_wavelength_angstrom, line_spread_profile, resolution_element_angstrom
            )
            try:
                measured = measure_line(wavelengths, brightness, line, spread)
            except DATA_ERRORS as error:
                # A line that cannot be fitted is a line not found. It must
                # not stop the spectrum's other processing.
                logger.debug("Zero-point line %s could not be fitted: %s", line.name, error)
                continue
            if measured is not None:
                lines.append(measured)

    saturation_flag = (
        None
        if zero_order_saturated_pixel_fraction is None
        else bool(is_saturation_significant(float(zero_order_saturated_pixel_fraction)))
    )
    saturation_fraction = (
        None if zero_order_saturated_pixel_fraction is None else float(zero_order_saturated_pixel_fraction)
    )
    if not lines:
        return WavelengthZeroPointRecord(
            zero_order_saturated_pixel_fraction=saturation_fraction, is_zero_order_saturated=saturation_flag
        )
    offset, uncertainty, chi_square, p_value = combine_line_offsets(lines)
    is_applied = (
        len(lines) >= MINIMUM_LINES_TO_APPLY
        and p_value is not None
        and (p_value >= AGREEMENT_MINIMUM_P_VALUE)
    )
    return WavelengthZeroPointRecord(
        offset_angstrom=offset,
        uncertainty_angstrom=uncertainty,
        line_count=len(lines),
        chi_square=chi_square,
        agreement_p_value=p_value,
        is_applied=bool(is_applied),
        zero_order_saturated_pixel_fraction=saturation_fraction,
        is_zero_order_saturated=saturation_flag,
        lines=lines,
    )


def shift_wavelengths(wavelength_angstrom: np.ndarray, record: WavelengthZeroPointRecord) -> np.ndarray:
    """Remove a measured zero-point offset from a set of wavelengths.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The spectrum's wavelengths, in Angstroms.
    record : `WavelengthZeroPointRecord`
        The result of `measure_wavelength_zero_point`.

    Returns
    -------
    corrected : `numpy.ndarray`
        The wavelengths minus the offset when the record says it was applied,
        and a copy of the input otherwise.
    """
    wavelengths = np.array(wavelength_angstrom, dtype=float, copy=True)
    if record.is_applied and record.offset_angstrom is not None:
        wavelengths -= record.offset_angstrom
    return wavelengths


def zero_point_metrics(record: WavelengthZeroPointRecord) -> tuple[list[StageQualityMetric], list[str]]:
    """Build the checkpoint 1 metrics and flags for a zero-point record.

    Parameters
    ----------
    record : `WavelengthZeroPointRecord`
        The result of `measure_wavelength_zero_point`.

    Returns
    -------
    metrics : `list` [`StageQualityMetric`]
        Four metrics:

        * ``wavelength_zero_point_offset_angstrom``: the size of the offset
          before correction, in Angstroms (absolute value; the sign is in the
          record). Its limit is `ZERO_POINT_OFFSET_LIMIT_ANGSTROM`, a designed
          value. `None` when no line was found.
        * ``wavelength_zero_point_uncertainty_angstrom``: the error of the
          offset. No limit.
        * ``wavelength_zero_point_line_count``: the lines behind the offset.
        * ``wavelength_zero_point_applied``: 1 when the offset was removed
          from the wavelengths, 0 when it was only measured.
    flags : `list` [`str`]
        ``wavelength_zero_point_unconstrained`` when fewer than two lines were
        found; ``wavelength_zero_point_lines_disagree`` when two or more lines
        were found but disagree; ``wavelength_zero_point_large`` when the
        offset is over its limit.
    """
    offset = metric(
        "wavelength_zero_point_offset_angstrom",
        None if record.offset_angstrom is None else abs(record.offset_angstrom),
        "angstrom",
        limit=ZERO_POINT_OFFSET_LIMIT_ANGSTROM,
        higher_is_better=False,
        note=(
            "absolute offset of the wavelength scale, measured from known lines; the limit is designed "
            "as half a resolution element at 5000 A (about 20 A), not measured"
        ),
    )
    metrics = [
        offset,
        metric(
            "wavelength_zero_point_uncertainty_angstrom",
            record.uncertainty_angstrom,
            "angstrom",
            note="error of the offset: the fit errors and the uncertainty of the lines' rest positions",
        ),
        metric(
            "wavelength_zero_point_line_count",
            float(record.line_count),
            "",
            note="lines that passed the significance test",
        ),
        metric(
            "wavelength_zero_point_applied",
            1.0 if record.is_applied else 0.0,
            "flag",
            note="1 when the offset was removed from the wavelengths, 0 when it was only measured",
        ),
    ]
    flags = []
    if record.line_count < MINIMUM_LINES_TO_APPLY:
        flags.append("wavelength_zero_point_unconstrained")
    elif not record.is_applied:
        flags.append("wavelength_zero_point_lines_disagree")
    if offset.passed is False:
        flags.append("wavelength_zero_point_large")
    return metrics, flags
