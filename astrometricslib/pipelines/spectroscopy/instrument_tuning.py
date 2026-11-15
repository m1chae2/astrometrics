"""Purpose: Derive instrument-dependent spectroscopy values from stored data.

Description: Issue #175 Phase D. Six tuners, each setting one family of
values from the spectra already in the library:

* ``spectroscopy.usable_range``: every wavelength range that follows from
  where the instrument gives usable signal (validation entries SPE-D31 and
  SPE-13).
* ``spectroscopy.line_spread_windows``: the two windows and the limit of the
  chromatic defocus check (SPE-D09).
* ``spectroscopy.response_fit``: the polynomial degree and the band skipped
  around each line when the instrument response is fitted (SPE-D55).
* ``spectroscopy.second_order_share``: the share of brightness that makes
  second-order light risky (SPE-D18).
* ``spectroscopy.gaia_xp_limits``: the residual and tilt limits against
  Gaia XP (SPE-D14 and SPE-D15).
* ``spectroscopy.colour_limit``: the colour disagreement limit (SPE-27).

Each tuner keeps its arithmetic in small functions that take plain numbers
and arrays. The numbers a method needs (a share of cases, a count of
folds) are declared here as hardcoded parameters with the reason for each.
"""

import math
from collections.abc import Sequence
from itertools import pairwise

import numpy as np

from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningResult, TuningSkippedError
from astrometricslib.models.stellar_source import NO_GOOD_MATCH_RMS
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
)
from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_catalog import (
    COLOUR_DISAGREEMENT_MAGNITUDES,
)
from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp import (
    COMPARISON_RANGE_ANGSTROM,
    GAIA_XP_RESIDUAL_RMS_LIMIT,
    GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.differential_refraction import (
    BLUE_REPORT_WAVELENGTH_ANGSTROM,
    RED_REPORT_WAVELENGTH_ANGSTROM,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import (
    _LINE_SKIP_HALF_WIDTH_PER_FWHM,
    _LINES_TO_SKIP_ANGSTROM,
    _MINIMUM_LINE_SKIP_HALF_WIDTH_ANGSTROM,
    _RESPONSE_POLYNOMIAL_DEGREE,
    _WAVELENGTH_CENTER_ANGSTROM,
    _WAVELENGTH_SCALE_ANGSTROM,
    DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM,
    line_skip_half_widths_angstrom,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.intensity_variance import (
    SIGNAL_TO_NOISE_MAXIMUM_WAVELENGTH_ANGSTROM,
    SIGNAL_TO_NOISE_MINIMUM_WAVELENGTH_ANGSTROM,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.measured_line_spread import (
    BLUE_WINDOW_ANGSTROM,
    CHROMATIC_DEFOCUS_RATIO_LIMIT,
    MINIMUM_SAMPLES_PER_WINDOW,
    PROFILE_END_ANGSTROM,
    PROFILE_START_ANGSTROM,
    RED_WINDOW_ANGSTROM,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    load_line_spread_profile,
)
from astrometricslib.pipelines.spectroscopy.processing.emission_line_detector import (
    MAXIMUM_FITTED_WAVELENGTH_ANGSTROM,
)
from astrometricslib.pipelines.spectroscopy.processing.second_order_risk import (
    SECOND_ORDER_RISKY_SHARE_OF_BRIGHTNESS,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import (
    _get_reference_templates,
    blurred_reference_spectrum,
)
from astrometricslib.pipelines.spectroscopy.processing.synthetic_colour import synthetic_b_minus_v
from astrometricslib.pipelines.spectroscopy.spectrum_tuning_data import (
    StoredStarSpectrum,
    declare_method_choice,
    load_stored_star_spectra,
    reference_label,
    require_spectral_filter,
    robust_sigma,
    symmetric_limit_from_spread,
    upper_limit_from_spread,
)

# --- Usable range (SPE-D31, SPE-13) -----------------------------------------

# The width of one wavelength band of the signal-to-noise curve. A band holds
# about nine samples at 11 Angstroms a sample, enough for a steady median,
# and it is the rounding of the range that results.
RANGE_BAND_ANGSTROM = declare_method_choice(
    "spectroscopy.tuning_range_band_angstrom",
    100.0,
    "Å",
    "Width of one band of the signal-to-noise curve the usable range is read from.",
    "SPE-D31",
    HardcodedKind.METHOD_CHOICE,
)

# The usable range is where the typical signal-to-noise stays at or above
# this share of its peak. Raw signal-to-noise follows the square root of the
# detected light, and the sensor's quantum efficiency falls from 27% to 6%
# between the peak and 8000 Angstroms, a square root ratio of about 0.5, which
# is the point where the entry says correcting for it starts to multiply the
# noise too far.
RANGE_PEAK_SHARE = declare_method_choice(
    "spectroscopy.tuning_range_peak_share",
    0.5,
    "fraction",
    "Share of the peak signal-to-noise that marks the edge of the usable range.",
    "SPE-D31",
    HardcodedKind.STATISTICS,
)

# The fewest stars that must have a band for its median to be used.
RANGE_MINIMUM_STARS_PER_BAND = declare_method_choice(
    "spectroscopy.tuning_range_minimum_stars_per_band",
    3.0,
    "count",
    "The fewest stars a band needs for its median signal-to-noise to be used.",
    "SPE-D31",
    HardcodedKind.STATISTICS,
)


def signal_to_noise_by_band(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    intensity_error: np.ndarray,
    band_edges_angstrom: np.ndarray,
) -> np.ndarray:
    """Find one spectrum's median signal-to-noise in each wavelength band.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The wavelengths, in Angstroms.
    intensity : `numpy.ndarray`
        The brightness.
    intensity_error : `numpy.ndarray`
        The 1-sigma error of each brightness.
    band_edges_angstrom : `numpy.ndarray`
        The edges of the bands, in increasing order.

    Returns
    -------
    signal_to_noise : `numpy.ndarray`
        One value for each band: the median of brightness over error for the
        samples in it, or NaN when none has a positive error and brightness.
    """
    usable = (
        np.isfinite(wavelength_angstrom)
        & np.isfinite(intensity)
        & np.isfinite(intensity_error)
        & (intensity > 0)
        & (intensity_error > 0)
    )
    ratio = np.full(band_edges_angstrom.size - 1, np.nan)
    for band in range(ratio.size):
        in_band = usable & (wavelength_angstrom >= band_edges_angstrom[band])
        in_band &= wavelength_angstrom < band_edges_angstrom[band + 1]
        if in_band.any():
            ratio[band] = float(np.median(intensity[in_band] / intensity_error[in_band]))
    return ratio


def usable_range_from_curve(
    band_edges_angstrom: np.ndarray, curve: np.ndarray, peak_share: float
) -> tuple[float, float] | None:
    """Read the usable wavelength range off a signal-to-noise curve.

    Parameters
    ----------
    band_edges_angstrom : `numpy.ndarray`
        The edges of the bands the curve is given in; one more than the
        curve has values.
    curve : `numpy.ndarray`
        The typical signal-to-noise of each band (NaN where unknown).
    peak_share : `float`
        The share of the peak the curve must stay at or above.

    Returns
    -------
    usable_range : `tuple` [`float`, `float`] or `None`
        The low edge of the first band and the high edge of the last band of
        the unbroken run of bands, around the peak, that stay at or above
        the share. `None` when the curve has no finite value.
    """
    if not np.isfinite(curve).any():
        return None
    peak_band = int(np.nanargmax(curve))
    threshold = peak_share * float(curve[peak_band])
    first = peak_band
    while first > 0 and np.isfinite(curve[first - 1]) and curve[first - 1] >= threshold:
        first -= 1
    last = peak_band
    while last < curve.size - 1 and np.isfinite(curve[last + 1]) and curve[last + 1] >= threshold:
        last += 1
    return float(band_edges_angstrom[first]), float(band_edges_angstrom[last + 1])


def median_curve_across_stars(curves: np.ndarray, minimum_stars: int) -> np.ndarray:
    """Take the median of each band across stars.

    Parameters
    ----------
    curves : `numpy.ndarray`
        An array with one row for each star and one column for each band;
        NaN where a star has no value.
    minimum_stars : `int`
        The fewest stars a band needs.

    Returns
    -------
    curve : `numpy.ndarray`
        The median of each band, or NaN where fewer stars have a value.
    """
    counts = np.sum(np.isfinite(curves), axis=0)
    with np.errstate(all="ignore"):
        medians = np.nanmedian(curves, axis=0)
    return np.where(counts >= minimum_stars, medians, np.nan)


def measure_usable_range(spectra: Sequence[StoredStarSpectrum]) -> tuple[tuple[float, float], int]:
    """Find the usable wavelength range of a set of stored spectra.

    Parameters
    ----------
    spectra : `Sequence` [`StoredStarSpectrum`]
        The stored spectra.

    Returns
    -------
    usable_range : `tuple` [`float`, `float`]
        The low and high wavelength, in Angstroms.
    star_count : `int`
        The number of stars that had errors and so counted.

    Raises
    ------
    TuningSkippedError
        If no star has stored errors, or the curve has no usable band.
    """
    band_angstrom = RANGE_BAND_ANGSTROM.default
    edges = np.arange(3000.0, 10000.0 + band_angstrom, band_angstrom)
    rows = [
        signal_to_noise_by_band(
            spectrum.wavelength_angstrom, spectrum.intensity, spectrum.intensity_error, edges
        )
        for spectrum in spectra
        if spectrum.intensity_error is not None
    ]
    if not rows:
        raise TuningSkippedError("no stored spectrum has per-sample errors")
    curve = median_curve_across_stars(np.vstack(rows), int(RANGE_MINIMUM_STARS_PER_BAND.default))
    found = usable_range_from_curve(edges, curve, RANGE_PEAK_SHARE.default)
    if found is None:
        raise TuningSkippedError("too few stars share a wavelength band to read a usable range")
    return found, len(rows)


def tune_usable_range(inputs: TuningInputs) -> TuningResult:
    """Derive every wavelength range that follows from the usable one.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read.

    Returns
    -------
    result : `TuningResult`
        The usable range, written to each parameter that holds it.

    """
    require_spectral_filter(inputs)
    spectra = load_stored_star_spectra(inputs.service("astrometrics"))
    (low, high), star_count = measure_usable_range(spectra)
    values = {
        COMPARISON_RANGE_ANGSTROM.name: (low, high),
        DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM.name: (low, high),
        SIGNAL_TO_NOISE_MINIMUM_WAVELENGTH_ANGSTROM.name: low,
        SIGNAL_TO_NOISE_MAXIMUM_WAVELENGTH_ANGSTROM.name: high,
        BLUE_REPORT_WAVELENGTH_ANGSTROM.name: low,
        RED_REPORT_WAVELENGTH_ANGSTROM.name: high,
        PROFILE_START_ANGSTROM.name: low,
        PROFILE_END_ANGSTROM.name: high,
        MAXIMUM_FITTED_WAVELENGTH_ANGSTROM.name: high,
    }
    return TuningResult(
        values=values,
        sample=star_count,
        source=(
            f"Wavelength range where the median signal-to-noise of {star_count} stored star spectra "
            "stays at or above half its peak, read in 100 Å bands"
        ),
    )


USABLE_RANGE_TUNER = Tuner(
    name="spectroscopy.usable_range",
    parameters=(
        COMPARISON_RANGE_ANGSTROM,
        DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM,
        SIGNAL_TO_NOISE_MINIMUM_WAVELENGTH_ANGSTROM,
        SIGNAL_TO_NOISE_MAXIMUM_WAVELENGTH_ANGSTROM,
        BLUE_REPORT_WAVELENGTH_ANGSTROM,
        RED_REPORT_WAVELENGTH_ANGSTROM,
        PROFILE_START_ANGSTROM,
        PROFILE_END_ANGSTROM,
        MAXIMUM_FITTED_WAVELENGTH_ANGSTROM,
    ),
    method=(
        "Reads the stored star spectra and takes the median signal-to-noise of all stars in each 100 Å band. "
        "The usable range is the unbroken run of bands around the peak that stay at or above half the peak."
    ),
    tune=tune_usable_range,
    per_filter=True,
)


# --- Chromatic defocus windows (SPE-D09) ------------------------------------

# The width of each of the two windows whose trail widths are compared. About
# 70 samples at 11 Angstroms a sample, so each median is steady.
DEFOCUS_WINDOW_WIDTH_ANGSTROM = declare_method_choice(
    "spectroscopy.tuning_defocus_window_width_angstrom",
    800.0,
    "Å",
    "Width of each window the chromatic defocus check compares.",
    "SPE-D09",
    HardcodedKind.METHOD_CHOICE,
)

# Where the red window starts, as a share of the usable range from its blue
# end. 0.526 puts a 800 Angstrom window at 6200 Angstroms in a 4200 to
# 8000 range, clear of the oxygen A band at 7600 Angstroms.
DEFOCUS_RED_WINDOW_START_SHARE = declare_method_choice(
    "spectroscopy.tuning_defocus_red_window_start_share",
    0.526,
    "fraction",
    "Where the red defocus window starts, as a share of the usable range.",
    "SPE-D09",
    HardcodedKind.METHOD_CHOICE,
)

# How many robust standard deviations above the median ratio the limit sits:
# a spectrum 3 sigma out of the ordinary spread is a real outlier.
DEFOCUS_LIMIT_SIGMA_MULTIPLE = declare_method_choice(
    "spectroscopy.tuning_defocus_limit_sigma_multiple",
    3.0,
    "sigma",
    "Robust standard deviations above the median ratio at which defocus is flagged.",
    "SPE-D09",
    HardcodedKind.STATISTICS,
)

# The relative error the median of a window's width fits may have. The ratio
# of two such medians then has about 7% error, a fifth of the margin between
# the in-focus ratio and the limit.
DEFOCUS_MEDIAN_RELATIVE_ERROR = declare_method_choice(
    "spectroscopy.tuning_defocus_median_relative_error",
    0.05,
    "fraction",
    "The relative error a window's median width may have.",
    "SPE-D09",
    HardcodedKind.STATISTICS,
)

# For a normal spread the standard error of a median is this many times the
# standard error of the mean.
_MEDIAN_ERROR_FACTOR = math.sqrt(math.pi / 2.0)


def defocus_windows(
    usable_low: float, usable_high: float, window_width: float, red_start_share: float
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Place the blue and red defocus windows inside the usable range.

    Parameters
    ----------
    usable_low, usable_high : `float`
        The usable range, in Angstroms.
    window_width : `float`
        The width of each window, in Angstroms.
    red_start_share : `float`
        Where the red window starts, as a share of the usable range.

    Returns
    -------
    blue_window, red_window : `tuple` [`float`, `float`]
        The two windows, each as (low, high).
    """
    red_start = usable_low + red_start_share * (usable_high - usable_low)
    return (usable_low, usable_low + window_width), (red_start, red_start + window_width)


def window_median_width(
    wavelength_angstrom: np.ndarray, trail_width_px: np.ndarray, window: tuple[float, float]
) -> tuple[float | None, float | None, int]:
    """Measure the median trail width in one window, and how much it scatters.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The wavelengths, in Angstroms.
    trail_width_px : `numpy.ndarray`
        The trail's sigma at each step, in pixels (0.0 marks a failed fit).
    window : `tuple` [`float`, `float`]
        The low (included) and high (excluded) wavelength of the window.

    Returns
    -------
    median_width : `float` or `None`
        The median of the working widths in the window, or `None` with none.
    relative_scatter : `float` or `None`
        The robust standard deviation of those widths over their median.
    fit_count : `int`
        How many working widths the window holds.
    """
    in_window = (
        np.isfinite(wavelength_angstrom)
        & np.isfinite(trail_width_px)
        & (trail_width_px > 0)
        & (wavelength_angstrom >= window[0])
        & (wavelength_angstrom < window[1])
    )
    widths = trail_width_px[in_window]
    if widths.size == 0:
        return None, None, 0
    median = float(np.median(widths))
    return median, robust_sigma(widths) / median, int(widths.size)


def samples_for_median_error(relative_scatter: float, target_relative_error: float) -> int:
    """Count the fits a median needs to reach a relative error.

    Parameters
    ----------
    relative_scatter : `float`
        The robust standard deviation of the fits over their median.
    target_relative_error : `float`
        The relative error the median may have.

    Returns
    -------
    count : `int`
        The smallest count whose median has at most that error.
    """
    return max(1, math.ceil((_MEDIAN_ERROR_FACTOR * relative_scatter / target_relative_error) ** 2))


def tune_line_spread_windows(inputs: TuningInputs) -> TuningResult:
    """Derive the defocus windows, their fit count and the ratio limit.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read.

    Returns
    -------
    result : `TuningResult`
        The two windows, the fewest fits a window needs and the ratio limit.

    Raises
    ------
    TuningSkippedError
        If the filter is not the grating or too few spectra have trail widths.
    """
    require_spectral_filter(inputs)
    spectra = load_stored_star_spectra(inputs.service("astrometrics"))
    (usable_low, usable_high), _ = measure_usable_range(spectra)
    blue_window, red_window = defocus_windows(
        usable_low,
        usable_high,
        DEFOCUS_WINDOW_WIDTH_ANGSTROM.default,
        DEFOCUS_RED_WINDOW_START_SHARE.default,
    )
    ratios: list[float] = []
    scatters: list[float] = []
    for spectrum in spectra:
        if spectrum.trail_width_px is None:
            continue
        blue, blue_scatter, _ = window_median_width(
            spectrum.wavelength_angstrom, spectrum.trail_width_px, blue_window
        )
        red, red_scatter, _ = window_median_width(
            spectrum.wavelength_angstrom, spectrum.trail_width_px, red_window
        )
        if blue is None or red is None or blue_scatter is None or red_scatter is None:
            continue
        ratios.append(red / blue)
        scatters.extend([blue_scatter, red_scatter])
    if not ratios:
        raise TuningSkippedError("no stored spectrum has trail widths in both windows")
    return TuningResult(
        values={
            BLUE_WINDOW_ANGSTROM.name: blue_window,
            RED_WINDOW_ANGSTROM.name: red_window,
            MINIMUM_SAMPLES_PER_WINDOW.name: samples_for_median_error(
                float(np.median(scatters)), DEFOCUS_MEDIAN_RELATIVE_ERROR.default
            ),
            CHROMATIC_DEFOCUS_RATIO_LIMIT.name: upper_limit_from_spread(
                ratios, DEFOCUS_LIMIT_SIGMA_MULTIPLE.default
            ),
        },
        sample=len(ratios),
        source=(
            f"Windows inside the usable range; fit count for a 5% median error; ratio limit 3 robust "
            f"standard deviations above the median red-to-blue trail width of {len(ratios)} "
            "stored star spectra"
        ),
    )


LINE_SPREAD_WINDOWS_TUNER = Tuner(
    name="spectroscopy.line_spread_windows",
    parameters=(
        BLUE_WINDOW_ANGSTROM,
        RED_WINDOW_ANGSTROM,
        MINIMUM_SAMPLES_PER_WINDOW,
        CHROMATIC_DEFOCUS_RATIO_LIMIT,
    ),
    method=(
        "Places two 800 Å windows in the usable range, takes the fewest fits that give each window's median "
        "width a 5% error, and puts the ratio limit 3 robust standard deviations above the median "
        "red-to-blue "
        "ratio of the stored star spectra."
    ),
    tune=tune_line_spread_windows,
    per_filter=True,
)


# --- Instrument response fit (SPE-D55) --------------------------------------

# The polynomial degrees tried, lowest and highest. The entry compares
# degrees 3, 4 and 5; one either side shows when the choice has run out.
RESPONSE_LOWEST_DEGREE = declare_method_choice(
    "spectroscopy.tuning_response_lowest_degree",
    2.0,
    "degree",
    "The lowest polynomial degree tried for the response fit.",
    "SPE-D55",
    HardcodedKind.METHOD_CHOICE,
)
RESPONSE_HIGHEST_DEGREE = declare_method_choice(
    "spectroscopy.tuning_response_highest_degree",
    6.0,
    "degree",
    "The highest polynomial degree tried for the response fit.",
    "SPE-D55",
    HardcodedKind.METHOD_CHOICE,
)

# The spectrum is cut into this many unbroken pieces; each piece is predicted
# from the rest, so a degree that only follows noise predicts badly.
RESPONSE_CROSS_VALIDATION_BLOCKS = declare_method_choice(
    "spectroscopy.tuning_response_cross_validation_blocks",
    5.0,
    "count",
    "How many unbroken pieces the response fit is cross-validated over.",
    "SPE-D55",
    HardcodedKind.METHOD_CHOICE,
)

# The smallest degree whose error is within this share of the best is taken:
# the entry found a higher degree only "marginally better".
RESPONSE_DEGREE_TOLERANCE = declare_method_choice(
    "spectroscopy.tuning_response_degree_tolerance",
    0.05,
    "fraction",
    "How close to the best cross-validated error a lower degree must be to be preferred.",
    "SPE-D55",
    HardcodedKind.RELATIVE,
)

# The response must hold to 1 percent (the figure the line-skip comment
# gives), so a line's wing counts as ended once it stays below that.
RESPONSE_WING_TOLERANCE = declare_method_choice(
    "spectroscopy.tuning_response_wing_tolerance",
    0.01,
    "fraction",
    "The residual below which the wing of a line no longer matters to the response.",
    "SPE-D55",
    HardcodedKind.RELATIVE,
)

# The distance from a line out to which its wing is searched, and the width
# of the distance bins, in line spreads and in Angstroms.
RESPONSE_WING_SEARCH_FWHM = declare_method_choice(
    "spectroscopy.tuning_response_wing_search_fwhm",
    4.0,
    "× FWHM",
    "How far from a line, in line spreads, its wing is searched.",
    "SPE-D55",
    HardcodedKind.METHOD_CHOICE,
)
RESPONSE_WING_BIN_FWHM = declare_method_choice(
    "spectroscopy.tuning_response_wing_bin_fwhm",
    0.25,
    "× FWHM",
    "Width of a distance bin, in line spreads.",
    "SPE-D55",
    HardcodedKind.METHOD_CHOICE,
)
RESPONSE_WING_SEARCH_ANGSTROM = declare_method_choice(
    "spectroscopy.tuning_response_wing_search_angstrom",
    300.0,
    "Å",
    "How far from a line, in Angstroms, its wing is searched.",
    "SPE-D55",
    HardcodedKind.METHOD_CHOICE,
)
RESPONSE_WING_BIN_ANGSTROM = declare_method_choice(
    "spectroscopy.tuning_response_wing_bin_angstrom",
    15.0,
    "Å",
    "Width of a distance bin, in Angstroms.",
    "SPE-D55",
    HardcodedKind.METHOD_CHOICE,
)


def cross_validated_error(
    scaled_wavelength: np.ndarray, log_ratio: np.ndarray, degree: int, block_count: int
) -> float | None:
    """Measure how well a polynomial predicts pieces it was not fitted to.

    Parameters
    ----------
    scaled_wavelength : `numpy.ndarray`
        The scaled wavelengths, in increasing order.
    log_ratio : `numpy.ndarray`
        The logarithm of observed over reference.
    degree : `int`
        The polynomial degree.
    block_count : `int`
        How many unbroken pieces to leave out in turn.

    Returns
    -------
    error : `float` or `None`
        The root-mean-square prediction error over all pieces, or `None`
        when the pieces left too few samples for the degree.
    """
    squared_errors: list[np.ndarray] = []
    for held_out in np.array_split(np.arange(scaled_wavelength.size), block_count):
        kept = np.ones(scaled_wavelength.size, dtype=bool)
        kept[held_out] = False
        if kept.sum() <= degree:
            return None
        coefficients = np.polyfit(scaled_wavelength[kept], log_ratio[kept], degree)
        squared_errors.append(
            (np.polyval(coefficients, scaled_wavelength[held_out]) - log_ratio[held_out]) ** 2
        )
    return float(np.sqrt(np.mean(np.concatenate(squared_errors))))


def choose_degree(error_by_degree: dict[int, float], tolerance: float) -> int:
    """Pick the lowest degree whose error is close to the best.

    Parameters
    ----------
    error_by_degree : `dict` [`int`, `float`]
        The cross-validated error of each degree.
    tolerance : `float`
        How much above the best error a degree may be and still be chosen.

    Returns
    -------
    degree : `int`
        The lowest degree with an error at most `tolerance` above the best.
    """
    best = min(error_by_degree.values())
    return min(degree for degree, error in error_by_degree.items() if error <= best * (1.0 + tolerance))


def wing_radius(
    distance: np.ndarray,
    residual: np.ndarray,
    baseline: float,
    bin_width: float,
    search_limit: float,
    tolerance: float,
) -> float | None:
    """Find how far from a line its wing reaches into the fit residual.

    Parameters
    ----------
    distance : `numpy.ndarray`
        Each sample's distance from the line.
    residual : `numpy.ndarray`
        The sample's residual from the smooth fit, as a fraction.
    baseline : `float`
        The typical size of the residual far from every line.
    bin_width : `float`
        The width of a distance bin, in the units of `distance`.
    search_limit : `float`
        The farthest distance searched, in the units of `distance`.
    tolerance : `float`
        The residual above the baseline that still counts as wing.

    Returns
    -------
    radius : `float` or `None`
        The outer edge of the last bin whose median residual stands more than
        `tolerance` above the baseline (0.0 when none does), or `None` when
        no bin holds a sample.
    """
    edges = np.arange(0.0, search_limit + bin_width / 2.0, bin_width)
    last_wing_edge: float | None = None
    seen_any = False
    for low, high in pairwise(edges):
        in_bin = (distance >= low) & (distance < high)
        if not in_bin.any():
            continue
        seen_any = True
        if float(np.median(np.abs(residual[in_bin]))) - baseline > tolerance:
            last_wing_edge = float(high)
    if not seen_any:
        return None
    return 0.0 if last_wing_edge is None else last_wing_edge


def _response_standards(
    spectra: Sequence[StoredStarSpectrum], labels: Sequence[str]
) -> list[tuple[StoredStarSpectrum, str]]:
    """Pick the stored spectra that can stand in for a response standard.

    Parameters
    ----------
    spectra : `Sequence` [`StoredStarSpectrum`]
        The stored spectra.
    labels : `Sequence` [`str`]
        The labels of the bundled reference spectra.

    Returns
    -------
    standards : `list` [`tuple` [`StoredStarSpectrum`, `str`]]
        Spectra with a brightness corrected for quantum efficiency, a
        catalog type that has a bundled reference, and a match to the
        references already better than the poor-match limit.
    """
    poor_match = resolve(NO_GOOD_MATCH_RMS).value
    standards = []
    for spectrum in spectra:
        label = reference_label(spectrum.catalog_type, labels)
        if (
            label is not None
            and spectrum.quantum_efficiency_corrected is not None
            and spectrum.own_rms is not None
            and spectrum.own_rms <= poor_match
        ):
            standards.append((spectrum, label))
    return standards


def tune_response_fit(inputs: TuningInputs) -> TuningResult:
    """Derive the response polynomial degree and the band skipped at lines.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read.

    Returns
    -------
    result : `TuningResult`
        The degree, the skipped half width per line spread and its floor.

    Raises
    ------
    TuningSkippedError
        If the filter is not the grating or no stored star can stand in as a
        standard.
    """
    require_spectral_filter(inputs)
    spectra = load_stored_star_spectra(inputs.service("astrometrics"))
    labels = tuple(_get_reference_templates())
    standards = _response_standards(spectra, labels)
    if not standards:
        raise TuningSkippedError(
            "no stored star has a catalog type with a bundled reference and a good match"
        )
    profile = load_line_spread_profile(inputs.setup.camera_name)
    fallback = resolve(FALLBACK_RESOLUTION_ELEMENT_ANGSTROM).value
    low, high = resolve(DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM).value
    lines = np.array(_LINES_TO_SKIP_ANGSTROM)
    line_spread = profile.at(lines) if profile is not None else np.full(lines.size, fallback)
    skip_half_widths = line_skip_half_widths_angstrom(profile, fallback)

    lowest, highest = int(RESPONSE_LOWEST_DEGREE.default), int(RESPONSE_HIGHEST_DEGREE.default)
    errors: dict[int, list[float]] = {degree: [] for degree in range(lowest, highest + 1)}
    wing_samples: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    baselines: list[float] = []
    current_degree = int(resolve(_RESPONSE_POLYNOMIAL_DEGREE).value)
    for spectrum, label in standards:
        wavelength = spectrum.wavelength_angstrom
        template_wavelength, template_flux = blurred_reference_spectrum(
            label,
            resolution_element_angstrom=spectrum.resolution_element_angstrom,
            resolution_profile=profile,
        )
        observed = spectrum.quantum_efficiency_corrected
        in_range = np.isfinite(observed) & (observed > 0) & (wavelength >= low) & (wavelength <= high)
        in_range &= (wavelength >= template_wavelength.min()) & (wavelength <= template_wavelength.max())
        if in_range.sum() < RESPONSE_CROSS_VALIDATION_BLOCKS.default * (highest + 1):
            continue
        wavelength = wavelength[in_range]
        log_ratio = np.log(observed[in_range] / np.interp(wavelength, template_wavelength, template_flux))
        scaled = (wavelength - _WAVELENGTH_CENTER_ANGSTROM) / _WAVELENGTH_SCALE_ANGSTROM

        outside_skipped = np.all(
            np.abs(wavelength[:, None] - lines[None, :]) > skip_half_widths[None, :], axis=1
        )
        for degree in errors:
            error = cross_validated_error(
                scaled[outside_skipped],
                log_ratio[outside_skipped],
                degree,
                int(RESPONSE_CROSS_VALIDATION_BLOCKS.default),
            )
            if error is not None:
                errors[degree].append(error)

        far_from_lines = np.all(
            np.abs(wavelength[:, None] - lines[None, :])
            > RESPONSE_WING_SEARCH_FWHM.default * line_spread[None, :],
            axis=1,
        )
        if far_from_lines.sum() <= current_degree:
            continue
        coefficients = np.polyfit(scaled[far_from_lines], log_ratio[far_from_lines], current_degree)
        residual = log_ratio - np.polyval(coefficients, scaled)
        baselines.append(float(np.median(np.abs(residual[far_from_lines]))))
        nearest_line = np.argmin(np.abs(wavelength[:, None] - lines[None, :]), axis=1)
        wing_samples.append((wavelength, residual, nearest_line))
    standard_count = len(wing_samples)
    mean_errors = {degree: float(np.mean(values)) for degree, values in errors.items() if values}
    if not mean_errors or standard_count == 0:
        raise TuningSkippedError("too few usable standard spectra to fit the response")

    baseline = float(np.median(baselines))
    radius_per_fwhm: list[float] = []
    radius_angstrom: list[float] = []
    for line_index, (line, spread) in enumerate(zip(lines, line_spread, strict=True)):
        distance = np.concatenate([
            np.abs(wl - line)[nearest == line_index] for wl, _, nearest in wing_samples
        ])
        residual = np.concatenate([res[nearest == line_index] for _, res, nearest in wing_samples])
        in_fwhm = wing_radius(
            distance / spread,
            residual,
            baseline,
            RESPONSE_WING_BIN_FWHM.default,
            RESPONSE_WING_SEARCH_FWHM.default,
            RESPONSE_WING_TOLERANCE.default,
        )
        in_angstrom = wing_radius(
            distance,
            residual,
            baseline,
            RESPONSE_WING_BIN_ANGSTROM.default,
            RESPONSE_WING_SEARCH_ANGSTROM.default,
            RESPONSE_WING_TOLERANCE.default,
        )
        if in_fwhm is not None and in_angstrom is not None:
            radius_per_fwhm.append(in_fwhm)
            radius_angstrom.append(in_angstrom)
    if not radius_per_fwhm:
        raise TuningSkippedError("no strong line has samples near it in the standard spectra")
    return TuningResult(
        values={
            _RESPONSE_POLYNOMIAL_DEGREE.name: choose_degree(mean_errors, RESPONSE_DEGREE_TOLERANCE.default),
            _LINE_SKIP_HALF_WIDTH_PER_FWHM.name: float(np.median(radius_per_fwhm)),
            _MINIMUM_LINE_SKIP_HALF_WIDTH_ANGSTROM.name: float(np.min(radius_angstrom)),
        },
        sample=standard_count,
        source=(
            f"Cross-validated degree and 1% line-wing radius from {standard_count} stored star spectra "
            "that have a bundled reference of their catalog type"
        ),
    )


RESPONSE_FIT_TUNER = Tuner(
    name="spectroscopy.response_fit",
    parameters=(
        _RESPONSE_POLYNOMIAL_DEGREE,
        _LINE_SKIP_HALF_WIDTH_PER_FWHM,
        _MINIMUM_LINE_SKIP_HALF_WIDTH_ANGSTROM,
    ),
    method=(
        "Divides stored star spectra by their blurred reference and takes the lowest polynomial "
        "degree within 5% "
        "of the best five-block cross-validated error. The skipped band reaches where the median line-wing "
        "residual falls to 1% above the baseline, in line spreads and, for the floor, in Angstroms."
    ),
    tune=tune_response_fit,
    per_filter=True,
)


# --- Second-order share (SPE-D18) -------------------------------------------


def median_relative_noise(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    intensity_error: np.ndarray,
    low: float,
    high: float,
) -> float | None:
    """Find a spectrum's typical per-sample error over its brightness.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The wavelengths, in Angstroms.
    intensity : `numpy.ndarray`
        The brightness.
    intensity_error : `numpy.ndarray`
        The 1-sigma error of each brightness.
    low, high : `float`
        The wavelength range to measure over, in Angstroms.

    Returns
    -------
    relative_noise : `float` or `None`
        The median of error over brightness in the range, or `None` when no
        sample has a positive brightness and error.
    """
    usable = (
        np.isfinite(intensity)
        & np.isfinite(intensity_error)
        & (intensity > 0)
        & (intensity_error > 0)
        & (wavelength_angstrom >= low)
        & (wavelength_angstrom <= high)
    )
    if not usable.any():
        return None
    return float(np.median(intensity_error[usable] / intensity[usable]))


def tune_second_order_share(inputs: TuningInputs) -> TuningResult:
    """Derive the share of brightness that makes second-order light risky.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read.

    Returns
    -------
    result : `TuningResult`
        The share: the typical per-sample noise of the red half of the range.

    Raises
    ------
    TuningSkippedError
        If the filter is not the grating or no spectrum has stored errors.
    """
    require_spectral_filter(inputs)
    spectra = load_stored_star_spectra(inputs.service("astrometrics"))
    (usable_low, usable_high), _ = measure_usable_range(spectra)
    middle = 0.5 * (usable_low + usable_high)
    noise_values = [
        value
        for spectrum in spectra
        if spectrum.intensity_error is not None
        for value in [
            median_relative_noise(
                spectrum.wavelength_angstrom,
                spectrum.intensity,
                spectrum.intensity_error,
                middle,
                usable_high,
            )
        ]
        if value is not None
    ]
    if not noise_values:
        raise TuningSkippedError("no stored spectrum has per-sample errors in the red half of the range")
    return TuningResult(
        values={SECOND_ORDER_RISKY_SHARE_OF_BRIGHTNESS.name: float(np.median(noise_values))},
        sample=len(noise_values),
        source=(
            f"Median per-sample error over brightness in the red half of the usable range, "
            f"over {len(noise_values)} stored star spectra"
        ),
    )


SECOND_ORDER_SHARE_TUNER = Tuner(
    name="spectroscopy.second_order_share",
    parameters=(SECOND_ORDER_RISKY_SHARE_OF_BRIGHTNESS,),
    method=(
        "Takes the median per-sample error over brightness of the stored star spectra in the red half of the "
        "usable range: second-order light below it cannot change a spectrum's shape visibly."
    ),
    tune=tune_second_order_share,
    per_filter=True,
)


# --- Gaia XP limits (SPE-D14, SPE-D15) --------------------------------------

# How many robust standard deviations the Gaia XP limits sit above the
# ordinary spread. Three leaves about one in a thousand ordinary stars out.
GAIA_LIMIT_SIGMA_MULTIPLE = declare_method_choice(
    "spectroscopy.tuning_gaia_limit_sigma_multiple",
    3.0,
    "sigma",
    "Robust standard deviations above the ordinary spread at which a Gaia XP limit sits.",
    "SPE-D14",
    HardcodedKind.STATISTICS,
)


def tune_gaia_xp_limits(inputs: TuningInputs) -> TuningResult:
    """Derive the Gaia XP residual and tilt limits.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read.

    Returns
    -------
    result : `TuningResult`
        The residual RMS limit and the tilt limit.

    Raises
    ------
    TuningSkippedError
        If the filter is not the grating or no spectrum has a stored
        Gaia XP comparison.
    """
    require_spectral_filter(inputs)
    spectra = load_stored_star_spectra(inputs.service("astrometrics"))
    residuals = [s.gaia_residual_rms for s in spectra if s.gaia_residual_rms is not None]
    slopes = [s.gaia_slope_percent for s in spectra if s.gaia_slope_percent is not None]
    if not residuals or not slopes:
        raise TuningSkippedError("no stored spectrum has a Gaia XP comparison")
    multiple = GAIA_LIMIT_SIGMA_MULTIPLE.default
    return TuningResult(
        values={
            GAIA_XP_RESIDUAL_RMS_LIMIT.name: upper_limit_from_spread(residuals, multiple),
            GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM.name: symmetric_limit_from_spread(slopes, multiple),
        },
        sample=min(len(residuals), len(slopes)),
        source=(
            f"Residual limit 3 robust standard deviations above the median, and tilt limit 3 above zero, "
            f"over {len(residuals)} stored Gaia XP comparisons"
        ),
    )


GAIA_XP_LIMITS_TUNER = Tuner(
    name="spectroscopy.gaia_xp_limits",
    parameters=(GAIA_XP_RESIDUAL_RMS_LIMIT, GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM),
    method=(
        "Reads the stored Gaia XP comparisons and puts each limit 3 robust standard deviations "
        "beyond the median "
        "(the residual) or beyond zero (the tilt) of the spectra compared."
    ),
    tune=tune_gaia_xp_limits,
    per_filter=True,
)


# --- Colour disagreement (SPE-27) -------------------------------------------

# How many robust standard deviations the colour limit sits beyond the
# ordinary spread, as for the Gaia XP limits.
COLOUR_LIMIT_SIGMA_MULTIPLE = declare_method_choice(
    "spectroscopy.tuning_colour_limit_sigma_multiple",
    3.0,
    "sigma",
    "Robust standard deviations beyond the ordinary spread at which the colour limit sits.",
    "SPE-27",
    HardcodedKind.STATISTICS,
)


def colour_differences(spectra: Sequence[StoredStarSpectrum]) -> list[float]:
    """Find the spectrum colour minus the catalog colour for each star.

    Parameters
    ----------
    spectra : `Sequence` [`StoredStarSpectrum`]
        The stored spectra.

    Returns
    -------
    differences : `list` [`float`]
        The synthetic B-V minus the catalog B-V, in magnitudes, for each
        star that has a response-corrected spectrum and a catalog colour.
    """
    differences = []
    for spectrum in spectra:
        if spectrum.response_corrected is None or spectrum.catalog_b_minus_v is None:
            continue
        colour = synthetic_b_minus_v(spectrum.wavelength_angstrom, spectrum.response_corrected)
        if colour is not None and np.isfinite(colour):
            differences.append(float(colour) - spectrum.catalog_b_minus_v)
    return differences


def tune_colour_limit(inputs: TuningInputs) -> TuningResult:
    """Derive the colour disagreement limit.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read.

    Returns
    -------
    result : `TuningResult`
        The limit, in magnitudes.

    Raises
    ------
    TuningSkippedError
        If the filter is not the grating or no star has both colours.
    """
    require_spectral_filter(inputs)
    spectra = load_stored_star_spectra(inputs.service("astrometrics"))
    differences = colour_differences(spectra)
    if not differences:
        raise TuningSkippedError("no stored star has a response-corrected spectrum and a catalog B-V")
    return TuningResult(
        values={
            COLOUR_DISAGREEMENT_MAGNITUDES.name: symmetric_limit_from_spread(
                differences, COLOUR_LIMIT_SIGMA_MULTIPLE.default
            )
        },
        sample=len(differences),
        source=(
            f"Median offset plus 3 robust standard deviations of synthetic minus catalog B-V "
            f"over {len(differences)} stored stars"
        ),
    )


COLOUR_LIMIT_TUNER = Tuner(
    name="spectroscopy.colour_limit",
    parameters=(COLOUR_DISAGREEMENT_MAGNITUDES,),
    method=(
        "Takes the synthetic B-V of each stored star spectrum minus its catalog B-V, and puts the "
        "limit at the "
        "size of the median offset plus 3 robust standard deviations."
    ),
    tune=tune_colour_limit,
    per_filter=True,
)
