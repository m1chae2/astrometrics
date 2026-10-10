"""A spectrum's own blur against wavelength, measured from its trail width.

A slitless star is a small disc, and the grating turns that disc into a trail.
When the spectrum is in good focus, the disc is the same size across the
trail (the cross-dispersion direction) and along it (the dispersion
direction). So the width of the trail across the dispersion, measured at each
step, also tells how wide the blur is along it. That blur is the resolution
element: the smallest wavelength difference the spectrum can show.

`spectral_resolution` already turns the trail width into one resolution
element for the whole spectrum, or into a coarse profile with 700 A bands.
This module makes a finer profile for quality checking: 400 A bands over
4200-8000 A, with the scatter of each band. It also builds the checkpoint 1
numbers that compare the red end of the spectrum with the blue end.

Why the red-to-blue comparison matters: a grating in a converging beam
(a focused telescope beam) does not put the first-order spectrum in a flat
plane. If the camera is focused on the zero order (the star's undispersed
image), the red end of the spectrum can sit away from focus. That is chromatic
defocus. It widens the trail in the red, and widens the red lines with it, so
it limits how well the classifier can tell spectral types apart there.

What the profile assumes:

* The star's image is round, so the blur along the dispersion equals the
  width across it. A trail with tracking errors or coma (a comet-shaped
  blur toward the edge of the field) breaks this, and the profile then
  understates the blur along the dispersion.
* The trail-width fit measures the star only. Scattered light and a
  neighbouring star's wing widen the fit and so widen the profile.

The stored, camera-wide line-spread profile (`load_line_spread_profile`) was
fitted to line depths on other stars. It may include effects this measurement
does not see, such as scattered light. The checkpoint reports the ratio of the
two at H-alpha (`measured_vs_stored_line_spread_ratio_halpha`) so a reader can
see how far apart they are. Nothing in the pipeline replaces one with the
other unless the pipeline is built with `use_measured_line_spread=True`.
"""

import numpy as np

from astrometricslib.models.measured_line_spread import MeasuredLineSpread
from astrometricslib.models.spectroscopy_quality import StageQualityMetric, metric
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    FWHM_PER_SIGMA,
    MINIMUM_RESOLUTION_ELEMENT_PIXELS,
    ResolutionProfile,
)

# The bands of the profile. They start at 4200 A, where the instrument
# response is first usable, step by 400 A and stop at 8000 A, the longest
# wavelength the response is fitted to. The last band is cut off at 8000 A,
# so it is 200 A wide. A 400 A band holds about 35 samples at 11.4 A per
# pixel, enough for a steady median; 400 A is also about the width over which
# the stored profile changes by 10 A in the blue. Chosen by judgement.
PROFILE_START_ANGSTROM = 4200.0
PROFILE_END_ANGSTROM = 8000.0
PROFILE_BAND_ANGSTROM = 400.0

# The fewest working width fits a band needs. A failed fit is stored as 0.0
# and is not counted. Fewer than this and a few noisy fits would set the
# band's median. About a third of a full band. Chosen by judgement.
MINIMUM_SAMPLES_PER_BAND = 10

# The two windows whose widths are compared for the chromatic defocus check.
# Each is 800 A wide, about 70 samples, so the median is steady. The blue
# window sits in the Balmer-line region and the red one stays clear of the
# H-alpha line and of the oxygen band at 7600 A.
BLUE_WINDOW_ANGSTROM = (4200.0, 5000.0)
RED_WINDOW_ANGSTROM = (6200.0, 7000.0)

# The fewest working width fits a window needs for its median to be used.
MINIMUM_SAMPLES_PER_WINDOW = 20

# H-alpha, where the measured and stored profiles are compared. The stored
# profile has its widest value (148 A) at this wavelength.
HALPHA_ANGSTROM = 6563.0

# The red-to-blue width ratio above which the spectrum is flagged for
# chromatic defocus. DESIGNED, NOT MEASURED. A spectrum that is in focus
# end to end changes width only through the seeing (the blur from the air),
# which varies roughly as wavelength to the power -0.2. From the middle of the
# blue window (4600 A) to the middle of the red one (6600 A) that gives a
# red-to-blue ratio of about 0.92: the red end is slightly sharper. A ratio of
# 1.3 means the red end is 30 percent wider than the blue, 40 percent more than
# an in-focus spectrum would show. It is a round number chosen well clear of
# the in-focus value, not fitted to any observed spectra. It needs to be
# checked against real focus sweeps (`scripts/spectral_focus_sweep.py`).
CHROMATIC_DEFOCUS_RATIO_LIMIT = 1.3


def _local_dispersion_angstrom_per_step(
    wavelength_angstrom: np.ndarray, sample_distances_px: np.ndarray | None
) -> np.ndarray:
    """Find how many Angstroms one step along the trail covers, at each step.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The wavelength of each step, in Angstroms.
    sample_distances_px : `numpy.ndarray` or `None`
        The distance of each step from the zero order, in pixels, measured
        along the trail. When given, the result is Angstroms per pixel of
        that distance. When `None`, the steps are taken to be one pixel
        apart, which is how the extractor spaces them.

    Returns
    -------
    dispersion : `numpy.ndarray`
        The local dispersion (Angstroms per pixel), always positive. It is
        NaN where it cannot be found.
    """
    change_in_wavelength = np.gradient(wavelength_angstrom)
    if sample_distances_px is None:
        step = np.ones_like(change_in_wavelength)
    else:
        step = np.gradient(sample_distances_px)
    with np.errstate(divide="ignore", invalid="ignore"):
        dispersion = np.abs(change_in_wavelength / step)
    return np.where(np.isfinite(dispersion) & (dispersion > 0), dispersion, np.nan)


def _usable_samples(
    wavelength_angstrom: np.ndarray | list[float] | None,
    trail_width_px: np.ndarray | list[float] | None,
    sample_distances_px: np.ndarray | list[float] | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Pick out the steps with a wavelength, a working fit and a dispersion.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray` or `list` [`float`] or `None`
        The wavelength of each step, in Angstroms.
    trail_width_px : `numpy.ndarray` or `list` [`float`] or `None`
        The trail's sigma in pixels at each step. `0.0` marks a failed fit.
    sample_distances_px : `numpy.ndarray` or `list` [`float`] or `None`
        The distance of each step from the zero order, in pixels, if known.

    Returns
    -------
    samples : `tuple` or `None`
        The wavelengths, the FWHM in pixels (2.355 times sigma) and the local
        dispersion in Angstroms per pixel, for the usable steps only. `None`
        when the inputs are missing, do not line up, or leave no usable step.
    """
    if wavelength_angstrom is None or trail_width_px is None:
        return None
    wavelengths = np.asarray(wavelength_angstrom, dtype=float)
    widths = np.asarray(trail_width_px, dtype=float)
    if wavelengths.ndim != 1 or widths.shape != wavelengths.shape or wavelengths.size < 3:
        return None
    distances = None
    if sample_distances_px is not None:
        distances = np.asarray(sample_distances_px, dtype=float)
        if distances.shape != wavelengths.shape:
            distances = None
    dispersion = _local_dispersion_angstrom_per_step(wavelengths, distances)
    usable = np.isfinite(wavelengths) & np.isfinite(widths) & (widths > 0) & np.isfinite(dispersion)
    if not usable.any():
        return None
    return wavelengths[usable], FWHM_PER_SIGMA * widths[usable], dispersion[usable]


def measure_line_spread(
    wavelength_angstrom: np.ndarray | list[float] | None,
    trail_width_px: np.ndarray | list[float] | None,
    sample_distances_px: np.ndarray | list[float] | None = None,
) -> MeasuredLineSpread | None:
    """Measure a spectrum's blur against wavelength from its trail width.

    The steps are sorted into bands of `PROFILE_BAND_ANGSTROM` from
    `PROFILE_START_ANGSTROM` to `PROFILE_END_ANGSTROM`. In each band the
    median trail sigma becomes a FWHM in pixels (times 2.355). The FWHM in
    Angstroms is that FWHM times the band's median local dispersion, so a
    pixel counts for fewer Angstroms in the blue than in the red. The scatter
    of a band is 1.4826 times the median absolute deviation of its per-step
    FWHM values, which a few bad fits cannot inflate.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray` or `list` [`float`]
        The spectrum's wavelengths, in Angstroms, one per step along the
        trail.
    trail_width_px : `numpy.ndarray` or `list` [`float`]
        The trail's sigma in pixels at each step, lined up one to one with
        the wavelengths. `0.0` marks a step whose fit failed.
    sample_distances_px : `numpy.ndarray` or `list` [`float`], optional
        Each step's distance from the zero order, in pixels, measured along
        the trail. It makes the local dispersion exact when the steps are
        not one pixel apart. Without it the steps are taken to be one pixel
        apart.

    Returns
    -------
    profile : `MeasuredLineSpread` or `None`
        The profile. `None` when the widths are missing or do not line up
        with the wavelengths, or when fewer than two bands have
        `MINIMUM_SAMPLES_PER_BAND` working fits.
    """
    samples = _usable_samples(wavelength_angstrom, trail_width_px, sample_distances_px)
    if samples is None:
        return None
    wavelengths, fwhm_px, dispersion = samples

    centres: list[float] = []
    fwhm_angstrom: list[float] = []
    fwhm_pixels: list[float] = []
    scatter: list[float] = []
    counts: list[int] = []
    for low in np.arange(PROFILE_START_ANGSTROM, PROFILE_END_ANGSTROM, PROFILE_BAND_ANGSTROM):
        high = min(float(low) + PROFILE_BAND_ANGSTROM, PROFILE_END_ANGSTROM)
        in_band = (wavelengths >= low) & (wavelengths < high)
        if int(in_band.sum()) < MINIMUM_SAMPLES_PER_BAND:
            continue
        band_fwhm = fwhm_px[in_band]
        median_fwhm = float(np.median(band_fwhm))
        centres.append(0.5 * (float(low) + high))
        fwhm_pixels.append(median_fwhm)
        fwhm_angstrom.append(median_fwhm * float(np.median(dispersion[in_band])))
        scatter.append(1.4826 * float(np.median(np.abs(band_fwhm - median_fwhm))))
        counts.append(int(in_band.sum()))
    if len(centres) < 2:
        return None
    return MeasuredLineSpread(
        wavelength_angstrom=centres,
        fwhm_angstrom=fwhm_angstrom,
        fwhm_px=fwhm_pixels,
        scatter_px=scatter,
        sample_count=counts,
    )


def median_fwhm_px(
    wavelength_angstrom: np.ndarray | list[float] | None,
    trail_width_px: np.ndarray | list[float] | None,
    window_angstrom: tuple[float, float],
) -> float | None:
    """Find the median trail FWHM, in pixels, inside one wavelength window.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray` or `list` [`float`]
        The spectrum's wavelengths, in Angstroms.
    trail_width_px : `numpy.ndarray` or `list` [`float`]
        The trail's sigma in pixels at each step. `0.0` marks a failed fit.
    window_angstrom : `tuple` [`float`, `float`]
        The lowest wavelength (included) and the highest (excluded) of the
        window, in Angstroms.

    Returns
    -------
    fwhm_px : `float` or `None`
        The median FWHM (2.355 times the median sigma) of the working fits
        in the window, or `None` when fewer than `MINIMUM_SAMPLES_PER_WINDOW`
        fits work or the inputs are missing.
    """
    samples = _usable_samples(wavelength_angstrom, trail_width_px, None)
    if samples is None:
        return None
    wavelengths, fwhm_px, _ = samples
    in_window = (wavelengths >= window_angstrom[0]) & (wavelengths < window_angstrom[1])
    if int(in_window.sum()) < MINIMUM_SAMPLES_PER_WINDOW:
        return None
    return float(np.median(fwhm_px[in_window]))


def measured_fwhm_angstrom_at(profile: MeasuredLineSpread | None, wavelength_angstrom: float) -> float | None:
    """Read a measured profile's FWHM at one wavelength.

    Parameters
    ----------
    profile : `MeasuredLineSpread` or `None`
        The measured profile.
    wavelength_angstrom : `float`
        The wavelength to read, in Angstroms.

    Returns
    -------
    fwhm_angstrom : `float` or `None`
        The FWHM in Angstroms, straight-line interpolated between band
        centres. `None` when there is no profile or the wavelength lies
        outside the first and last band centres (it is never extrapolated).
    """
    if profile is None or len(profile.wavelength_angstrom) < 2:
        return None
    if not profile.wavelength_angstrom[0] <= wavelength_angstrom <= profile.wavelength_angstrom[-1]:
        return None
    return float(np.interp(wavelength_angstrom, profile.wavelength_angstrom, profile.fwhm_angstrom))


def to_resolution_profile(profile: MeasuredLineSpread | None) -> ResolutionProfile | None:
    """Turn a measured profile into the form the classifier blurs with.

    The classifier and the response fit take a `ResolutionProfile`, a set of
    band centres with a resolution element (a FWHM, in Angstroms) at each.
    The measured FWHM is raised to `MINIMUM_RESOLUTION_ELEMENT_PIXELS` before
    it is converted, because a spectrum sampled once per pixel cannot show
    anything sharper than that, whatever a noisy fit says.

    Parameters
    ----------
    profile : `MeasuredLineSpread` or `None`
        The measured profile.

    Returns
    -------
    resolution_profile : `ResolutionProfile` or `None`
        The profile in Angstroms, or `None` when there is no profile.
    """
    if profile is None or len(profile.wavelength_angstrom) < 2:
        return None
    fwhm_angstrom = np.array(profile.fwhm_angstrom, dtype=float)
    fwhm_px = np.array(profile.fwhm_px, dtype=float)
    # Raise any band narrower than the sampling limit by the same factor in
    # pixels and Angstroms, so the pixel size of the band stays consistent.
    raised = np.maximum(fwhm_px, MINIMUM_RESOLUTION_ELEMENT_PIXELS) / fwhm_px
    return ResolutionProfile(np.array(profile.wavelength_angstrom, dtype=float), fwhm_angstrom * raised)


def line_spread_checkpoint_items(
    wavelength_angstrom: np.ndarray | list[float] | None,
    trail_width_px: np.ndarray | list[float] | None,
    measured_profile: MeasuredLineSpread | None,
    stored_profile: ResolutionProfile | None,
) -> tuple[list[StageQualityMetric], list[str]]:
    """Build the line-spread metrics and flag for quality checkpoint 1.

    The checkpoint is the one for the calibrated spectrum (``pre_processing``).
    The metrics are:

    * ``trail_fwhm_blue_px``: the median trail FWHM from 4200 to 5000 A.
    * ``trail_fwhm_red_px``: the median trail FWHM from 6200 to 7000 A.
    * ``chromatic_defocus_ratio``: red divided by blue. The limit is
      `CHROMATIC_DEFOCUS_RATIO_LIMIT`, a designed value (see that constant).
      A ratio above it adds the ``chromatic_defocus`` flag.
    * ``measured_vs_stored_line_spread_ratio_halpha``: the measured FWHM at
      6563 A divided by the stored profile's value there. It has no limit.
      A ratio far from 1 means the trail width and the stored profile
      disagree about the blur.

    Every metric has no value when the trail width is missing or too few
    fits work.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray` or `list` [`float`]
        The spectrum's wavelengths, in Angstroms.
    trail_width_px : `numpy.ndarray` or `list` [`float`]
        The trail's sigma in pixels at each step. `0.0` marks a failed fit.
    measured_profile : `MeasuredLineSpread` or `None`
        The profile from `measure_line_spread` for the same spectrum.
    stored_profile : `ResolutionProfile` or `None`
        The camera's stored line-spread profile, or `None` when there is
        none.

    Returns
    -------
    metrics : `list` [`StageQualityMetric`]
        The four metrics, in the order above.
    flags : `list` [`str`]
        ``["chromatic_defocus"]`` when the ratio is above its limit,
        otherwise empty.
    """
    blue = median_fwhm_px(wavelength_angstrom, trail_width_px, BLUE_WINDOW_ANGSTROM)
    red = median_fwhm_px(wavelength_angstrom, trail_width_px, RED_WINDOW_ANGSTROM)
    ratio = red / blue if blue is not None and red is not None and blue > 0 else None

    measured_halpha = measured_fwhm_angstrom_at(measured_profile, HALPHA_ANGSTROM)
    stored_halpha = float(stored_profile.at(np.array([HALPHA_ANGSTROM]))[0]) if stored_profile else None
    halpha_ratio = (
        measured_halpha / stored_halpha
        if measured_halpha is not None and stored_halpha is not None and stored_halpha > 0
        else None
    )

    defocus = metric(
        "chromatic_defocus_ratio",
        ratio,
        "ratio",
        limit=CHROMATIC_DEFOCUS_RATIO_LIMIT,
        higher_is_better=False,
        note=(
            "red (6200-7000 A) over blue (4200-5000 A) trail FWHM; the limit is designed, not measured: "
            "an in-focus spectrum gives about 0.92 from the weak wavelength dependence of seeing "
            "(about wavelength to the power -0.2)"
        ),
    )
    metrics = [
        metric(
            "trail_fwhm_blue_px",
            blue,
            "pixel",
            note="median trail FWHM across the dispersion, 4200-5000 A",
        ),
        metric(
            "trail_fwhm_red_px",
            red,
            "pixel",
            note="median trail FWHM across the dispersion, 6200-7000 A",
        ),
        defocus,
        metric(
            "measured_vs_stored_line_spread_ratio_halpha",
            halpha_ratio,
            "ratio",
            note=(
                "the spectrum's own measured FWHM at 6563 A over the stored line-spread "
                "profile's; reported only"
            ),
        ),
    ]
    flags = ["chromatic_defocus"] if defocus.passed is False else []
    return metrics, flags
