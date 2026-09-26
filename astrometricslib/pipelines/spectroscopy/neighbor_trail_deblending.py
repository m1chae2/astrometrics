"""Works out how much of a bright neighbour leaks into a faint star's box.

Two stars close together on the sky give two streaks side by side in a
slitless image. Each streak is a narrow bright core with faint wings (the
blur of the telescope and grating). The wings of the bright streak reach
across the gap and land inside the faint star's reading box, so the faint
star's spectrum picks up some of the bright star's light. The extra light
grows toward the wavelengths where the bright star is brightest compared with
the faint one. On Albireo (a K giant 17 pixels from a B star) the K giant's
light was 1% of the B star's box at 4100 A, 11% at 6000 A, 24% at 7000 A and
36% at 7500 A (2026-09-26, the pipeline's own extraction of the aligned 4 s
frames of 2026-09-25).

This module is the "neighbour wing" stage of the spectroscopy pipeline. It
does four things:

1. Measure the blur of the equipment from an ISOLATED star: cut its streak
   into bands along the streak, average each band across the streak, and keep
   the shape (`measure_empirical_blur`). The shape is measured, not assumed,
   because the blur is lopsided and changes with wavelength, and a neighbour's
   wing is 1000 times fainter than its core, so a formula for the shape
   gave the wrong wing (a fitted Gaussian core plus Moffat wing was tried on
   2026-09-26 and was off by 40-80% on a real test).
2. On the crowded image, cut the same bands and fit each band's profile as the
   sum of one copy of that measured shape per star plus a flat sky level
   (`fit_neighbor_amplitudes`). The only free numbers are how bright each
   star is in each band, the small position corrections, and ONE stretch
   factor (the "dilation") that widens or narrows the whole measured shape.
   The stretch is needed because the focus moves with temperature.
3. From the fitted shape, work out what fraction of a neighbour's light lands
   inside another star's box (`enclosed_fraction`), and multiply it by the
   neighbour's brightness at every sample (`neighbor_wing_flux`).
4. Take that light out of the faint star's box flux, and keep the share that
   was removed at every sample as an audit number
   (`subtract_neighbor_wings`).

What the equipment does (measured 2026-09-25 on Vega, Deneb, Schedar, Mirach
and Navi, single-frame FWHM across the streak in pixels; see
`spectrum_extractor` for the blur that changes with wavelength): the streak is
sharpest in the blue (about 3.2 px at 4100 A), widest in the middle (4-5 px
at 5000 A) and narrower again in the red (3.3-4 px at 6800 A). The focuser sat
at one position all night while its temperature fell from 22.3 C to 18.3 C, and
the width at 5000 A grew with it: 4.2 px at 22.3 C, 4.8-5.0 px at 21.3 C
and 6.9 px at 18.3 C, about 0.7 px per degree. So a blur measured on one
star only describes another star taken at about the same focuser
temperature, or after the stretch factor has been fitted.

How well it works (2026-09-26, module functions only): a profile measured on
Schedar, used to predict the light of Deneb inside the box of a Mirach copy
injected 17 px away, gave 0.94-1.02 of the true light from 4300 to 7000 A and
0.81 at 7900 A. It left the faint star's box within 0.4% of the truth, and
within 3.7% at 7900 A, against 5-19% without the correction. On Albireo the
profiles of Deneb, Schedar and Vega (focuser temperatures 21.3, 21.3 and 22.3
C) gave the same contamination to within 1.5 percentage points, and every fit
passed the residual gate (1.4-1.8%).

What this module does NOT do:

* It does not touch any image or catalog. It works on arrays only.
* It does not correct second-order light or the instrument response.
* One stretch factor is fitted for the whole spectrum, but the real change with
  focus is not the same at every wavelength. The residual of the fit
  (`NeighborFit.relative_residual`) shows how much is left over.
* The neighbour's own box flux is used as its brightness, and that box holds
  a little of the faint star's light too (under 0.5% on Albireo), which is
  ignored.
* The predicted light runs low against the truth at the red end (0.81 at
  7900 A on real stars, 0.81-0.85 on the synthetic pair in the tests), partly
  because the blur beyond the measured window is taken to be zero.

Not connected to the pipeline yet. It is meant to be switched on by a config
option, off by default, once it has been checked on isolated stars, synthetic
pairs and real pairs.
"""

from dataclasses import dataclass

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.optimize import least_squares

# How finely the blur is sampled when the light inside a stretch is worked
# out, in samples per pixel. Twelve is far finer than the blur, so the sums
# are accurate to well under 0.1% of a star's light.
_FINE_SAMPLES_PER_PIXEL = 12

# The window and the sky. The measured blur runs 75 px either side of the
# star, and the sky is the middle value of the outermost `SKY_EDGE_SAMPLES`
# samples on each side. Measured on 2026-09-26: with a window of only 45 px,
# the sky came from where the wings still hold light, took it off, and the
# predicted neighbour light ran 15% too low; widening the window to 75 px
# brought it to 10% too low.
DEFAULT_HALF_WIDTH_PX = 75
SKY_EDGE_SAMPLES = 8

# The centre of a star's streak is found from the middle few pixels.
_CENTROID_HALF_WIDTH_PX = 4

# How far a star's fitted position may move from where the centre line says,
# in pixels. The centre lines are measured, so this is only a small correction.
_MAXIMUM_CENTRE_SHIFT_PX = 2.0

# How much the measured blur may be stretched or squeezed to match the crowded
# image. Across the 2026-09-25 stars the stretch needed was 1.0-1.4 for a
# 4 C change of focuser temperature; a fit that reaches either limit is
# not describing a focus change.
MINIMUM_DILATION = 0.6
MAXIMUM_DILATION = 2.0

# A fit is only trusted when the profile is matched to within this fraction
# of the brightest star's peak, judged by the root-mean-square residual over
# all the bands. Measured 2026-09-26: 0.6-0.8% on injected pairs of real
# stars and 1.3-1.7% on the real Albireo pair. 2% leaves a little room above
# the worst of those. It has not been checked against a fit that is wrong
# in a way the profile cannot show.
MAXIMUM_TRUSTED_RELATIVE_RESIDUAL = 0.02


@dataclass(frozen=True)
class EmpiricalBlur:
    """The blur of the equipment across the streak, from an isolated star.

    Attributes
    ----------
    offsets_px : `numpy.ndarray`
        Offsets across the streak from the star's centre, in pixels, one per
        profile point. Symmetric about 0.
    profiles : `numpy.ndarray`
        The measured shape in each band, shape (bands, offsets). Each row
        adds up to 1: it is the fraction of the star's light in each pixel.
    band_positions_px : `numpy.ndarray`
        How far along the streak each band is, in pixels from the zero-order
        star. Increases from one band to the next.
    """

    offsets_px: np.ndarray
    profiles: np.ndarray
    band_positions_px: np.ndarray


@dataclass(frozen=True)
class NeighborFit:
    """The result of fitting the measured blur to crowded band profiles.

    Attributes
    ----------
    dilation : `float`
        How much the measured blur had to be stretched (above 1) or squeezed
        (below 1) to match. Usually a focus change.
    centre_shifts_px : `numpy.ndarray`
        The correction to each star's centre, in pixels. The first star (the
        reference) is always 0.
    amplitudes : `numpy.ndarray`
        The light of each star in each band, shape (bands, stars), in the
        same units as the profiles.
    sky_levels : `numpy.ndarray`
        The flat background found in each band.
    relative_residual : `float`
        Root-mean-square of what the fit could not explain, over all bands,
        as a fraction of the brightest star's peak.
    is_reliable : `bool`
        `True` when the residual is small enough and the stretch is inside its
        limits (see `MAXIMUM_TRUSTED_RELATIVE_RESIDUAL`).
    """

    dilation: float
    centre_shifts_px: np.ndarray
    amplitudes: np.ndarray
    sky_levels: np.ndarray
    relative_residual: float
    is_reliable: bool


def measure_empirical_blur(
    plane: np.ndarray,
    is_vertical_dispersion: bool,
    centre_at_rows_px: np.ndarray,
    band_edges: np.ndarray,
    zero_order_position_px: float,
    half_width_px: int = DEFAULT_HALF_WIDTH_PX,
) -> EmpiricalBlur:
    """Measure the blur of the equipment from an isolated star's streak.

    Each row (or column, when the streak runs left to right) is shifted so
    the streak is centred, so a tilted streak still lines up. The rows of a
    band are averaged, the sky is taken off, the band is re-centred on its
    own middle, and the result is scaled to add up to 1.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The image, with an isolated bright star and nothing else within
        `half_width_px` of its streak.
    is_vertical_dispersion : `bool`
        `True` when the streak runs top to bottom (each row is one cross
        section), `False` when it runs left to right (each column is).
    centre_at_rows_px : `numpy.ndarray`
        Where the streak's centre is across the streak, in pixels, for every
        row (or column) of the image.
    band_edges : `numpy.ndarray`
        Row (or column) numbers that bound the bands; band `i` covers
        `band_edges[i]` up to but not including `band_edges[i + 1]`.
    zero_order_position_px : `float`
        The row (or column) of the zero-order star, so each band's position
        along the streak is measured from it.
    half_width_px : `int`, optional
        How far either side of the streak to measure, in pixels.

    Returns
    -------
    blur : `EmpiricalBlur`
        The measured shape in every band.
    """
    image = plane if is_vertical_dispersion else plane.T
    cross_positions = np.arange(image.shape[1])
    offsets_px = np.arange(-half_width_px, half_width_px + 1)
    n_bands = len(band_edges) - 1
    profiles = np.zeros((n_bands, len(offsets_px)))
    band_positions = np.zeros(n_bands)
    middle = np.abs(offsets_px) <= _CENTROID_HALF_WIDTH_PX
    for band_index in range(n_bands):
        rows = np.arange(int(band_edges[band_index]), int(band_edges[band_index + 1]))
        summed = np.zeros(len(offsets_px))
        for row in rows:
            summed += np.interp(centre_at_rows_px[row] + offsets_px, cross_positions, image[row])
        summed /= len(rows)
        sky = np.median(np.concatenate([summed[:SKY_EDGE_SAMPLES], summed[-SKY_EDGE_SAMPLES:]]))
        signal = summed - sky
        centroid = float((offsets_px[middle] * signal[middle]).sum() / signal[middle].sum())
        recentred = np.interp(offsets_px + centroid, offsets_px, signal, left=0.0, right=0.0)
        profiles[band_index] = recentred / recentred.sum()
        band_positions[band_index] = float(rows.mean()) - zero_order_position_px
    return EmpiricalBlur(offsets_px=offsets_px, profiles=profiles, band_positions_px=band_positions)


def _profile_at(blur: EmpiricalBlur, position_px: float) -> np.ndarray:
    """Give the measured shape at one place along the streak.

    Between two measured bands the shape is a straight-line blend of the
    two; beyond the first or last band it is that band's shape.

    Parameters
    ----------
    blur : `EmpiricalBlur`
        The measured blur.
    position_px : `float`
        Distance along the streak from the zero-order star, in pixels.

    Returns
    -------
    profile : `numpy.ndarray`
        The shape, adding up to 1.
    """
    positions = blur.band_positions_px
    if position_px <= positions[0]:
        return blur.profiles[0]
    if position_px >= positions[-1]:
        return blur.profiles[-1]
    upper = int(np.searchsorted(positions, position_px))
    lower = upper - 1
    weight = (position_px - positions[lower]) / (positions[upper] - positions[lower])
    return (1.0 - weight) * blur.profiles[lower] + weight * blur.profiles[upper]


def _evaluate_profile(blur: EmpiricalBlur, position_px: float, offsets_px: np.ndarray) -> np.ndarray:
    """Read the measured shape at any offsets, between the measured pixels.

    A straight line between neighbouring pixels flattens a narrow core (a core
    a pixel or two wide loses several percent of its peak that way), and the
    fit then widens the blur to make up for it. A cubic curve through the
    measured points keeps the core.

    Parameters
    ----------
    blur : `EmpiricalBlur`
        The measured blur.
    position_px : `float`
        Distance along the streak from the zero-order star, in pixels.
    offsets_px : `numpy.ndarray`
        The offsets, in pixels, to read the shape at.

    Returns
    -------
    values : `numpy.ndarray`
        The measured shape at those offsets; 0 outside the measured window.
    """
    spline = CubicSpline(blur.offsets_px, _profile_at(blur, position_px), extrapolate=False)
    return np.nan_to_num(spline(offsets_px), nan=0.0)


def _cumulative_light(
    blur: EmpiricalBlur, position_px: float, dilation: float
) -> tuple[np.ndarray, np.ndarray]:
    """Give the fraction of a star's light to the left of each point.

    Parameters
    ----------
    blur : `EmpiricalBlur`
        The measured blur.
    position_px : `float`
        Distance along the streak from the zero-order star, in pixels.
    dilation : `float`
        How much to stretch the measured shape.

    Returns
    -------
    grid_px : `numpy.ndarray`
        Offsets from the star's centre, in pixels, on a fine grid.
    cumulative : `numpy.ndarray`
        For each grid point, the fraction of the light at or to the left of
        it. Runs from 0 up to 1.
    """
    offsets = blur.offsets_px
    grid_px = np.linspace(offsets[0], offsets[-1], (len(offsets) - 1) * _FINE_SAMPLES_PER_PIXEL + 1)
    density = _evaluate_profile(blur, position_px, grid_px / dilation)
    cumulative = np.cumsum(density)
    return grid_px, cumulative / cumulative[-1]


def enclosed_fraction(
    blur: EmpiricalBlur,
    position_px: float,
    dilation: float,
    source_offset_px: float,
    interval_low_px: float,
    interval_high_px: float,
) -> float:
    """Find how much of one star's light falls in a stretch across the streak.

    Parameters
    ----------
    blur : `EmpiricalBlur`
        The measured blur.
    position_px : `float`
        Distance along the streak from the zero-order star, in pixels.
    dilation : `float`
        How much to stretch the measured shape (1 leaves it as measured).
    source_offset_px : `float`
        Where the star's streak is, across the streak, in pixels.
    interval_low_px, interval_high_px : `float`
        The two ends of the stretch, in the same coordinates (for example the
        edges of another star's reading box).

    Returns
    -------
    fraction : `float`
        The fraction of the star's light between the two ends, from 0 to 1.
    """
    grid_px, cumulative = _cumulative_light(blur, position_px, dilation)
    at_low = np.interp(interval_low_px - source_offset_px, grid_px, cumulative, left=0.0, right=1.0)
    at_high = np.interp(interval_high_px - source_offset_px, grid_px, cumulative, left=0.0, right=1.0)
    return float(at_high - at_low)


def pixel_profile(
    blur: EmpiricalBlur,
    position_px: float,
    dilation: float,
    source_offset_px: float,
    pixel_centres_px: np.ndarray,
) -> np.ndarray:
    """Give the light one star puts into each pixel across the streak.

    Parameters
    ----------
    blur : `EmpiricalBlur`
        The measured blur.
    position_px : `float`
        Distance along the streak from the zero-order star, in pixels.
    dilation : `float`
        How much to stretch the measured shape.
    source_offset_px : `float`
        Where the star's streak is, in pixels.
    pixel_centres_px : `numpy.ndarray`
        The centre of each pixel, in the same coordinates.

    Returns
    -------
    profile : `numpy.ndarray`
        The fraction of the star's light in each pixel, read from the
        measured shape at the pixel centres.
    """
    # The measured profile is already the light that fell in each whole pixel,
    # so it is read at the pixel centres, not integrated over the pixel again
    # (integrating twice widened the core: with it, a true stretch of 1.10 was
    # fitted as 1.03 in the tests).
    return (
        _evaluate_profile(
            blur, position_px, (np.asarray(pixel_centres_px, dtype=float) - source_offset_px) / dilation
        )
        / dilation
    )


def build_band_profiles(
    plane: np.ndarray,
    is_vertical_dispersion: bool,
    reference_centre_at_rows_px: np.ndarray,
    band_edges: np.ndarray,
    zero_order_position_px: float,
    half_width_px: int = DEFAULT_HALF_WIDTH_PX,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Average a crowded image across the streaks in bands along them.

    Unlike `measure_empirical_blur`, nothing is re-centred, the sky is left in
    and the bands are not scaled: the fit finds the stars' light and the sky.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The image.
    is_vertical_dispersion : `bool`
        `True` when the streaks run top to bottom, `False` when left to right.
    reference_centre_at_rows_px : `numpy.ndarray`
        Where the reference star's streak is across the streak, in pixels,
        for every row (or column) of the image.
    band_edges : `numpy.ndarray`
        Row (or column) numbers that bound the bands.
    zero_order_position_px : `float`
        The row (or column) of the reference star's zero order.
    half_width_px : `int`, optional
        How far either side of the reference streak to keep, in pixels.

    Returns
    -------
    offsets_px : `numpy.ndarray`
        The offsets from the reference streak, from `-half_width_px` to
        `half_width_px`.
    profiles : `numpy.ndarray`
        The mean cross-section of each band, shape (bands, offsets).
    band_positions_px : `numpy.ndarray`
        How far along the streak each band is, from the zero-order star.
    """
    image = plane if is_vertical_dispersion else plane.T
    cross_positions = np.arange(image.shape[1])
    offsets_px = np.arange(-half_width_px, half_width_px + 1)
    n_bands = len(band_edges) - 1
    profiles = np.zeros((n_bands, len(offsets_px)))
    band_positions = np.zeros(n_bands)
    for band_index in range(n_bands):
        rows = np.arange(int(band_edges[band_index]), int(band_edges[band_index + 1]))
        summed = np.zeros(len(offsets_px))
        for row in rows:
            summed += np.interp(reference_centre_at_rows_px[row] + offsets_px, cross_positions, image[row])
        profiles[band_index] = summed / len(rows)
        band_positions[band_index] = float(rows.mean()) - zero_order_position_px
    return offsets_px, profiles, band_positions


def _solve_amplitudes(
    blur: EmpiricalBlur,
    dilation: float,
    offsets_px: np.ndarray,
    profiles: np.ndarray,
    band_positions_px: np.ndarray,
    star_offsets_px: np.ndarray,
    weights: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Find, for a given stretch and positions, the best amplitudes and sky.

    For fixed shapes the profile is a straight-line combination of the stars'
    shapes and a flat level, so the amplitudes come from linear least
    squares, one band at a time.

    Parameters
    ----------
    blur : `EmpiricalBlur`
        The measured blur.
    dilation : `float`
        How much to stretch the measured shape.
    offsets_px : `numpy.ndarray`
        Offsets across the streak, in pixels.
    profiles : `numpy.ndarray`
        The band profiles, shape (bands, offsets).
    band_positions_px : `numpy.ndarray`
        How far along the streak each band is.
    star_offsets_px : `numpy.ndarray`
        Where each star's streak is, in pixels.
    weights : `numpy.ndarray`
        How much each point counts, same shape as `profiles`.

    Returns
    -------
    amplitudes : `numpy.ndarray`
        Light of each star in each band, shape (bands, stars), never below 0.
    sky_levels : `numpy.ndarray`
        Flat level in each band.
    residuals : `numpy.ndarray`
        The weighted differences, same shape as `profiles`.
    """
    n_bands = profiles.shape[0]
    amplitudes = np.zeros((n_bands, len(star_offsets_px)))
    sky_levels = np.zeros(n_bands)
    residuals = np.zeros_like(profiles)
    for band_index in range(n_bands):
        columns = [
            pixel_profile(blur, float(band_positions_px[band_index]), dilation, float(offset), offsets_px)
            for offset in star_offsets_px
        ]
        design = np.column_stack([*columns, np.ones(len(offsets_px))])
        weighted_design = design * weights[band_index][:, None]
        solution, *_ = np.linalg.lstsq(
            weighted_design, profiles[band_index] * weights[band_index], rcond=None
        )
        solution[:-1] = np.clip(solution[:-1], 0.0, None)
        amplitudes[band_index] = solution[:-1]
        sky_levels[band_index] = solution[-1]
        residuals[band_index] = (profiles[band_index] - design @ solution) * weights[band_index]
    return amplitudes, sky_levels, residuals


def fit_neighbor_amplitudes(
    blur: EmpiricalBlur,
    offsets_px: np.ndarray,
    profiles: np.ndarray,
    band_positions_px: np.ndarray,
    star_offsets_px: np.ndarray,
    noise_floor: float,
) -> NeighborFit:
    """Fit the measured blur to the band profiles of a crowded image.

    Parameters
    ----------
    blur : `EmpiricalBlur`
        The blur measured on an isolated star (`measure_empirical_blur`).
    offsets_px : `numpy.ndarray`
        Offsets across the streak, in pixels, one per profile point.
    profiles : `numpy.ndarray`
        The mean cross-section of each band, shape (bands, offsets).
    band_positions_px : `numpy.ndarray`
        How far along the streak each band is, from the zero-order star.
    star_offsets_px : `numpy.ndarray`
        Where each star's streak starts, across the streak, in pixels. The
        first is the reference star.
    noise_floor : `float`
        The typical scatter of one profile point in the empty sky, in the
        profile's units. Points are weighted as one over the larger of this
        and the square root of the profile, so the faint wings count as much
        as the bright core.

    Returns
    -------
    fit : `NeighborFit`
        The stretch, positions, amplitudes and how good the match is.
    """
    star_offsets_px = np.asarray(star_offsets_px, dtype=float)
    n_stars = len(star_offsets_px)
    weights = 1.0 / np.sqrt(noise_floor**2 + np.clip(profiles, 0.0, None))

    def shifts_from(parameters: np.ndarray) -> np.ndarray:
        """Give the star position corrections that go with the free numbers.

        Returns
        -------
        shifts : `numpy.ndarray`
            One correction per star, the first being 0.
        """
        return np.concatenate([[0.0], parameters[1:]])

    def residual_vector(parameters: np.ndarray) -> np.ndarray:
        """Give the fitter the weighted misfit for one set of free numbers.

        Returns
        -------
        residuals : `numpy.ndarray`
            The weighted differences, flattened.
        """
        _, _, residuals = _solve_amplitudes(
            blur,
            float(parameters[0]),
            offsets_px,
            profiles,
            band_positions_px,
            star_offsets_px + shifts_from(parameters),
            weights,
        )
        return residuals.ravel()

    lower = [MINIMUM_DILATION, *([-_MAXIMUM_CENTRE_SHIFT_PX] * (n_stars - 1))]
    upper = [MAXIMUM_DILATION, *([_MAXIMUM_CENTRE_SHIFT_PX] * (n_stars - 1))]
    result = least_squares(residual_vector, [1.0, *([0.0] * (n_stars - 1))], bounds=(lower, upper))
    dilation = float(result.x[0])
    shifts = shifts_from(result.x)
    amplitudes, sky_levels, _ = _solve_amplitudes(
        blur, dilation, offsets_px, profiles, band_positions_px, star_offsets_px + shifts, weights
    )

    # Judge the fit on the plain (unweighted) misfit, as a fraction of the
    # brightest star's peak, so the number means the same for every image.
    misfit = np.zeros_like(profiles)
    for band_index in range(profiles.shape[0]):
        model = sky_levels[band_index] + sum(
            amplitudes[band_index, star_index]
            * pixel_profile(
                blur,
                float(band_positions_px[band_index]),
                dilation,
                float(star_offsets_px[star_index] + shifts[star_index]),
                offsets_px,
            )
            for star_index in range(n_stars)
        )
        misfit[band_index] = profiles[band_index] - model
    peak = float(np.max(profiles - np.median(profiles, axis=1, keepdims=True)))
    relative_residual = float(np.sqrt(np.mean(misfit**2)) / peak) if peak > 0 else float("inf")
    dilation_is_plausible = MINIMUM_DILATION * 1.001 < dilation < MAXIMUM_DILATION * 0.999
    return NeighborFit(
        dilation=dilation,
        centre_shifts_px=shifts,
        amplitudes=amplitudes,
        sky_levels=sky_levels,
        relative_residual=relative_residual,
        is_reliable=bool(relative_residual <= MAXIMUM_TRUSTED_RELATIVE_RESIDUAL and dilation_is_plausible),
    )


def neighbor_wing_flux(
    blur: EmpiricalBlur,
    dilation: float,
    positions_px: np.ndarray,
    neighbor_box_flux: np.ndarray,
    neighbor_box_half_width_px: np.ndarray,
    neighbor_offset_px: np.ndarray,
    target_box_half_width_px: np.ndarray,
    target_offset_px: np.ndarray,
) -> np.ndarray:
    """Work out how much of a neighbour's light lands in the target's box.

    The neighbour's total light at a sample is its own box flux divided by
    the fraction its own box holds. The share that falls in the target's box
    then comes from `enclosed_fraction`.

    Parameters
    ----------
    blur : `EmpiricalBlur`
        The measured blur.
    dilation : `float`
        The stretch found by `fit_neighbor_amplitudes`.
    positions_px : `numpy.ndarray`
        Distance along the streak from the zero-order star at each sample.
    neighbor_box_flux : `numpy.ndarray`
        The neighbour's sky-subtracted box flux at each sample.
    neighbor_box_half_width_px : `numpy.ndarray`
        Half-width of the neighbour's box at each sample, in pixels.
    neighbor_offset_px : `numpy.ndarray`
        Where the neighbour's streak is across the streak at each sample.
    target_box_half_width_px : `numpy.ndarray`
        Half-width of the target's box at each sample.
    target_offset_px : `numpy.ndarray`
        Where the target's streak is across the streak at each sample, in
        the same coordinates as `neighbor_offset_px`.

    Returns
    -------
    wing_flux : `numpy.ndarray`
        The neighbour's light inside the target's box at each sample, in the
        units of `neighbor_box_flux`.
    """
    count = len(neighbor_box_flux)
    wing_flux = np.zeros(count)
    for sample in range(count):
        own = enclosed_fraction(
            blur,
            float(positions_px[sample]),
            dilation,
            float(neighbor_offset_px[sample]),
            float(neighbor_offset_px[sample] - neighbor_box_half_width_px[sample]),
            float(neighbor_offset_px[sample] + neighbor_box_half_width_px[sample]),
        )
        if own <= 0:
            continue
        inside_target = enclosed_fraction(
            blur,
            float(positions_px[sample]),
            dilation,
            float(neighbor_offset_px[sample]),
            float(target_offset_px[sample] - target_box_half_width_px[sample]),
            float(target_offset_px[sample] + target_box_half_width_px[sample]),
        )
        wing_flux[sample] = neighbor_box_flux[sample] / own * inside_target
    return wing_flux


def subtract_neighbor_wings(
    target_box_flux: np.ndarray,
    wing_fluxes: list[np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    """Take the neighbours' light out of the target's box flux.

    Parameters
    ----------
    target_box_flux : `numpy.ndarray`
        The target's sky-subtracted box flux at each sample.
    wing_fluxes : `list` [`numpy.ndarray`]
        One array per neighbour, from `neighbor_wing_flux`. An empty list
        (a star with no neighbour) leaves the flux unchanged.

    Returns
    -------
    corrected_flux : `numpy.ndarray`
        The box flux with the neighbours' light removed. It is never below
        zero, since a negative flux would only come from the correction
        overshooting.
    wing_fraction : `numpy.ndarray`
        The share of the original box flux that came from neighbours, at each
        sample (0 where the box flux is not positive). This is the
        per-wavelength audit number.
    """
    total_wing = np.zeros_like(target_box_flux, dtype=float)
    for wing_flux in wing_fluxes:
        total_wing += wing_flux
    corrected_flux = np.clip(target_box_flux - total_wing, 0.0, None)
    wing_fraction = np.divide(
        total_wing, target_box_flux, out=np.zeros_like(total_wing), where=target_box_flux > 0
    )
    return corrected_flux, wing_fraction
