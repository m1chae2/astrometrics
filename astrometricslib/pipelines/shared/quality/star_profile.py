"""Measuring one star's radial profile and a straight cut through it.

The viewer lets a person click a star. This module answers two questions
about it:

1. How does brightness fall off with distance from the center? Every pixel
   within a radius is listed with its distance and its value above the sky,
   and a Moffat curve and a Gaussian curve are fitted to them.
2. What does the star look like along one line? Values are read along a
   straight line through the center, which shows a one-sided flare or a
   close pair that a radial profile averages away.

Positions are 1-based (FITS and DS9): the center of the first pixel is (1, 1).
Inside the code, positions are 0-based array indices, and the two are
converted only at the edges. The cut angle is measured from the +y axis
(increasing row number) toward the +x axis (increasing column number), so
0 degrees runs along a column and 90 degrees runs along a row.

`QualityRecords.measure_star_profile` is the public entry point. The module
reads a file but changes nothing.
"""

import math
from collections.abc import Callable

import numpy as np
from astropy.stats import sigma_clipped_stats
from scipy import ndimage, optimize

from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import factor_for
from astrometricslib.models.quality.star_profile import ProfileFit, StarProfileMeasurement
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    RunBasis,
    TunedParameter,
)
from astrometricslib.pipelines.shared.quality.star_ring import analyze_star_ring

__all__ = ["measure_star_profile_in_file"]

# The default radius of the profile. A Moffat star with a typical beta has
# most of its light within about 3 FWHM, and the wings out to 6 FWHM give the
# fit a baseline to tell the star from the sky. 6 x 2.33 px = 14 px on the
# reference setup.
# Stands for the profile radius. It follows the star width: the value is a
# factor times the star width the run measured (14 on the reference setup).
STAR_PROFILE_RADIUS_PX = TunedParameter(
    name="shared.star_profile_radius_px",
    default=factor_for(14, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Default radius of a star's radial profile.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=14,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="AST-17",
)

# The default reach of the cut, each way from the center. It is a little
# longer than the profile so the cut shows the sky on both sides and a
# neighbor that sits just outside the profile. 8 x 2.33 px = 19 px.
# Stands for the half length of the cut. It follows the star width: the
# value is a factor times the star width the run measured (19 on the reference
# setup).
STAR_CUT_HALF_LENGTH_PX = TunedParameter(
    name="shared.star_cut_half_length_px",
    default=factor_for(19, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Default reach of a straight cut through a star, each way from the center.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=19,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="AST-17",
)

# Half the side of the box in which the clicked position is refined to the
# brightness-weighted centroid. A click lands within about one FWHM of the
# star, so a box 1.5 FWHM each way holds the core and the click error.
# Stands for the half width of the centroid box. It follows the star width:
# the value is a factor times the star width the run measured (3.5 on the
# reference setup).
STAR_CENTROID_BOX_HALF_WIDTH_PX = TunedParameter(
    name="shared.star_centroid_box_half_width_px",
    default=factor_for(3.5, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Half the side of the box where a clicked star position is refined to its centroid.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=3.5,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="AST-17",
)

# The sky annulus starts at the profile radius and is this wide. 3 FWHM
# (7 px on the reference setup) holds hundreds of pixels, so the clipped
# median is steady, while staying close enough that the sky has not drifted.
# Stands for the width of the sky annulus. It follows the star width: the
# value is a factor times the star width the run measured (7 on the reference
# setup).
STAR_SKY_ANNULUS_WIDTH_PX = TunedParameter(
    name="shared.star_sky_annulus_width_px",
    default=factor_for(7, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Width of the ring outside a star's profile radius that gives the sky level.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=7,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="AST-17",
)

# The peak counts as clipped only when it is at least this fraction of the
# image maximum and two or more pixels share it. A clipped star has a flat
# top at the sensor's ceiling, which is also the brightest value in the frame.
# 0.95 allows a few ADU of difference between flat tops after calibration.
STAR_CLIPPED_PEAK_FRACTION = TunedParameter(
    name="shared.star_clipped_peak_fraction",
    default=0.95,
    unit="fraction",
    description="Fraction of the image maximum above which a flat-topped peak counts as clipped.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-17",
)

# The spacing of the samples along a cut, in pixels. Half a pixel samples
# the bilinear surface twice per pixel, enough to show a close pair without
# making the list long.
STAR_CUT_STEP_PX = TunedParameter(
    name="shared.star_cut_step_px",
    default=0.5,
    unit="pixels",
    description="Spacing of the samples along a straight cut through a star.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-17",
)

# The most a click may be moved to reach a star. The viewer sends a few screen
# pixels converted to image pixels, which is large when zoomed out. Beyond
# about 50 px the nearest bright peak is rarely the star meant, and the
# smoothing window would grow without a gain.
STAR_SNAP_RADIUS_LIMIT_PX = TunedParameter(
    name="shared.star_snap_radius_limit_px",
    default=50.0,
    unit="pixels",
    description="Largest distance a clicked position may be moved to the nearest star peak.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-17",
)

# Sigma of the Gaussian that smooths the image before the brightest peak is
# picked, so one hot pixel cannot win over a star. A star's Gaussian sigma is
# its width divided by 2.355 (1 px at 2.33 px width), which matches a star.
# Stands for the smoothing sigma. It follows the star width (1.0 on the
# reference setup).
STAR_SNAP_SMOOTHING_SIGMA_PX = TunedParameter(
    name="shared.star_snap_smoothing_sigma_px",
    default=factor_for(1.0, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Sigma of the smoothing used to find the nearest star peak.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=1.0,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="AST-17",
)

# A star must rise this many times the sky noise above the sky. Pure noise
# gives a brightest pixel near 3 sigma in a profile disc; 5 sigma is the usual
# detection floor and leaves room above that.
STAR_MINIMUM_SIGNAL_TO_NOISE = TunedParameter(
    name="shared.star_minimum_signal_to_noise",
    default=5.0,
    unit="× sky noise",
    description="Smallest peak above the sky, in sky noise, for a profile to count as a star.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="AST-17",
)

# A star narrower than this is a hot pixel or noise. One pixel is the
# sampling limit: no optics make a star sharper.
# Stands for the narrowest star width. It follows the star width (1.0 px on
# the reference setup).
STAR_MINIMUM_FWHM_PX = TunedParameter(
    name="shared.star_minimum_fwhm_px",
    default=factor_for(1.0, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Narrowest fitted width that still counts as a star.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=1.0,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="AST-17",
)

# A star wider than this is a galaxy or nebula. Eight times the typical width
# is far past focus drift and seeing, and keeps the broad stars of a bad
# night.
# Stands for the widest star width. It follows the star width (18.6 px on the
# reference setup, 8 x 2.33).
STAR_MAXIMUM_FWHM_PX = TunedParameter(
    name="shared.star_maximum_fwhm_px",
    default=factor_for(18.6, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Widest fitted width that still counts as a star.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=18.6,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="AST-17",
)

# The largest radius, cut half length and cutout half size, in pixels. A
# cutout of 100 px each way holds 201 x 201 = 40,401 numbers, a few hundred
# kilobytes as text, so a huge request cannot return megabytes. A star's
# wings and any close pair sit well inside 100 px.
STAR_MAXIMUM_EXTENT_PX = TunedParameter(
    name="shared.star_maximum_extent_px",
    default=100.0,
    unit="pixels",
    description="Largest profile radius or cut half length, which also bounds the returned cutout.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-17",
)

# Smallest radius accepted, in pixels. A Moffat fit has four free numbers and
# needs more pixels than that: a radius of 3 px holds about 28 pixels.
MINIMUM_RADIUS_PX = 3.0

# The curve for plotting has this many points from the center to the radius.
_CURVE_POINT_COUNT = 60

_FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))
# Moffat limits keep the fit from running off to a flat or a spike curve.
_BETA_LIMITS = (1.0, 10.0)


def _moffat_values(distance: np.ndarray, peak: float, alpha: float, beta: float) -> np.ndarray:
    """Give a Moffat curve at some distances from the center.

    Returns
    -------
    values : `numpy.ndarray`
        ``peak * (1 + (r / alpha)**2) ** -beta``.
    """
    return peak * (1.0 + (distance / alpha) ** 2) ** (-beta)


def _gaussian_values(distance: np.ndarray, peak: float, sigma: float) -> np.ndarray:
    """Give a Gaussian curve at some distances from the center.

    Returns
    -------
    values : `numpy.ndarray`
        ``peak * exp(-r**2 / (2 sigma**2))``.
    """
    return peak * np.exp(-(distance**2) / (2.0 * sigma**2))


def _pixel_scale_arcsec(image: AstrometricsImage) -> float | None:
    """Read the pixel scale from the header, in arcseconds per pixel.

    The header keywords ``SECPIX1`` or ``SCALE`` are tried first, then the
    image's sky coordinates (WCS).

    Returns
    -------
    scale : `float` or `None`
        The scale, or `None` when the header does not give a usable one.
    """
    header = image.header
    for keyword in ("SECPIX1", "SCALE"):
        try:
            value = float(header.get(keyword))
        except TypeError, ValueError:
            continue
        if math.isfinite(value) and value > 0:
            return value
    world = image.wcs
    if world is not None and world.has_celestial:
        from astropy.wcs.utils import proj_plane_pixel_scales

        scales = proj_plane_pixel_scales(world.celestial)
        scale = float(np.mean(scales)) * 3600.0
        if math.isfinite(scale) and scale > 0:
            return scale
    return None


def _refine_center(data: np.ndarray, column: float, row: float, half_width: float) -> tuple[float, float]:
    """Move a clicked position to the brightness-weighted centroid.

    The box is centered on the click. The weights are the values above the
    box's lowest value, so the sky level does not pull the center toward
    the middle of the box.

    Returns
    -------
    column, row : `float`
        The refined 0-based position. The click itself when the box has no
        light above its minimum.
    """
    half = math.ceil(half_width)
    column0, row0 = round(column), round(row)
    x_low, x_high = max(column0 - half, 0), min(column0 + half + 1, data.shape[1])
    y_low, y_high = max(row0 - half, 0), min(row0 + half + 1, data.shape[0])
    box = data[y_low:y_high, x_low:x_high]
    weights = box - np.nanmin(box)
    weights[~np.isfinite(weights)] = 0.0
    total = float(weights.sum())
    if total <= 0.0:
        return column, row
    rows, columns = np.mgrid[y_low:y_high, x_low:x_high]
    return float((weights * columns).sum() / total), float((weights * rows).sum() / total)


def _snap_to_peak(data: np.ndarray, column: float, row: float, search_radius: float) -> tuple[float, float]:
    """Move a position to the brightest smoothed peak within a radius.

    The image is smoothed with a Gaussian first, so one hot pixel does not
    beat a star.

    Returns
    -------
    column, row : `float`
        The 0-based position of the peak, or the input when the disc has no
        finite pixel.
    """
    sigma = float(resolve(STAR_SNAP_SMOOTHING_SIGMA_PX).value)  # type: ignore[arg-type]
    margin = math.ceil(search_radius + 4.0 * sigma)
    x_low, x_high = max(round(column) - margin, 0), min(round(column) + margin + 1, data.shape[1])
    y_low, y_high = max(round(row) - margin, 0), min(round(row) + margin + 1, data.shape[0])
    window = np.nan_to_num(data[y_low:y_high, x_low:x_high], nan=float(np.nanmedian(data)))
    smooth = ndimage.gaussian_filter(window, sigma)
    rows, columns = np.mgrid[y_low:y_high, x_low:x_high]
    disc = np.hypot(columns - column, rows - row) <= search_radius
    if not disc.any():
        return column, row
    best = np.where(disc, smooth, -np.inf)
    index = np.unravel_index(int(np.argmax(best)), best.shape)
    return float(columns[index]), float(rows[index])


def _fit_curves(
    values: np.ndarray,
    radius: float,
    peak_guess: float,
    width_guess: float,
    scale: float | None,
    positions: tuple[np.ndarray, np.ndarray, float, float],
) -> tuple[ProfileFit, ProfileFit]:
    """Fit a 2-D Moffat and a 2-D Gaussian to the pixels of the profile.

    Both are round and have a free center. The sky is already subtracted.

    Returns
    -------
    moffat, gaussian : `ProfileFit`
        The two fits. A fit that failed says why.
    """
    columns, rows, center_column, center_row = positions
    total_variance = float(np.sum((values - values.mean()) ** 2))

    def fit_one(name: str) -> ProfileFit:
        """Fit one curve.

        Returns
        -------
        fit : `ProfileFit`
            The fit, or a failed one with a reason.
        """

        def model(parameters: np.ndarray) -> np.ndarray:
            """Evaluate the curve at the pixels for a set of parameters.

            Returns
            -------
            values : `numpy.ndarray`
                The curve value at each pixel.
            """
            offset = np.hypot(columns - parameters[1], rows - parameters[2])
            if name == "moffat":
                return _moffat_values(offset, parameters[0], parameters[3], parameters[4])
            return _gaussian_values(offset, parameters[0], parameters[3])

        if name == "moffat":
            alpha = width_guess / (2.0 * math.sqrt(2.0 ** (1.0 / 3.0) - 1.0))
            start = [peak_guess, center_column, center_row, alpha, 3.0]
            low = [0.0, center_column - 2.0, center_row - 2.0, 0.1, _BETA_LIMITS[0]]
            high = [np.inf, center_column + 2.0, center_row + 2.0, radius * 4.0, _BETA_LIMITS[1]]
        else:
            start = [peak_guess, center_column, center_row, width_guess / _FWHM_PER_SIGMA]
            low = [0.0, center_column - 2.0, center_row - 2.0, 0.1]
            high = [np.inf, center_column + 2.0, center_row + 2.0, radius * 4.0]
        try:
            solution = optimize.least_squares(lambda p: model(p) - values, start, bounds=(low, high))
        except (ValueError, FloatingPointError) as error:
            return ProfileFit(model=name, fitted=False, reason=f"the fit failed: {error}")
        parameters = solution.x
        if name == "moffat":
            fwhm = 2.0 * parameters[3] * math.sqrt(2.0 ** (1.0 / parameters[4]) - 1.0)
            beta: float | None = float(parameters[4])
        else:
            fwhm = _FWHM_PER_SIGMA * parameters[3]
            beta = None
        residual = float(np.sum(solution.fun**2))
        grid = np.linspace(0.0, radius, _CURVE_POINT_COUNT)
        if name == "moffat":
            curve = _moffat_values(grid, parameters[0], parameters[3], parameters[4])
        else:
            curve = _gaussian_values(grid, parameters[0], parameters[3])
        return ProfileFit(
            model=name,
            fitted=True,
            fwhm_px=float(fwhm),
            fwhm_arcsec=None if scale is None else float(fwhm * scale),
            peak=float(parameters[0]),
            beta=beta,
            center_x=float(parameters[1] + 1.0),
            center_y=float(parameters[2] + 1.0),
            curve_radius_px=[float(v) for v in grid],
            curve_values=[float(v) for v in curve],
            r_squared=float(1.0 - residual / total_variance) if total_variance > 0 else None,
        )

    return fit_one("moffat"), fit_one("gaussian")


def _judge_star(peak: float, sky_noise: float, moffat: ProfileFit) -> tuple[bool, str | None]:
    """Say whether a profile looks like a star, and if not, why.

    Returns
    -------
    star_found, reason : `bool`, `str` or `None`
        `True` and `None` for a star; otherwise `False` and a sentence.
    """
    minimum_ratio = float(resolve(STAR_MINIMUM_SIGNAL_TO_NOISE).value)  # type: ignore[arg-type]
    ratio = peak / sky_noise if sky_noise > 0 else math.inf
    if ratio < minimum_ratio:
        return False, f"No star here: the peak is only {ratio:.1f} times the sky noise."
    if not moffat.fitted or moffat.fwhm_px is None:
        return False, "No star here: the profile could not be fitted."
    fwhm = moffat.fwhm_px
    if fwhm < float(resolve(STAR_MINIMUM_FWHM_PX).value):  # type: ignore[arg-type]
        return False, f"Too narrow for a star (FWHM {fwhm:.2f} px): probably a hot pixel or noise."
    if fwhm > float(resolve(STAR_MAXIMUM_FWHM_PX).value):  # type: ignore[arg-type]
        return False, f"Too wide for a star (FWHM {fwhm:.1f} px): probably a galaxy or nebula."
    return True, None


def measure_star_profile_in_file(
    path: str,
    x: float,
    y: float,
    radius_px: float | None = None,
    cut_angle_degrees: float = 0.0,
    cut_half_length_px: float | None = None,
    search_radius_px: float | None = None,
    find_neighbors: Callable[[np.ndarray], list[tuple[float, float]]] | None = None,
) -> StarProfileMeasurement:
    """Measure a star's radial profile, a straight cut and the ring around it.

    Parameters
    ----------
    path : `str`
        A FITS image.
    x, y : `float`
        The clicked position, 1-based (column, row).
    radius_px : `float`, optional
        The radius of the profile. Taken from the star width when `None`.
    cut_angle_degrees : `float`, optional
        The cut direction, measured from +y toward +x.
    cut_half_length_px : `float`, optional
        How far the cut reaches each way. Taken from the star width when
        `None`.
    search_radius_px : `float`, optional
        When given, the click is first moved to the brightest smoothed peak
        within this many pixels (at most the declared limit). `None` keeps
        the click and refines it only in the small centroid box.
    find_neighbors : callable, optional
        A star finder for the ring's neighbor mask: it takes a piece of the
        image and returns the 0-based ``(column, row)`` of each star in it.
        `None` leaves neighbors in the ring.

    Returns
    -------
    measurement : `StarProfileMeasurement`
        The profile, the fits, the cut and the ring analysis.

    Raises
    ------
    InvalidArgumentError
        If the position is not inside the image, the radius is too small, a
        number is not finite, or the image has no data. A file that does not
        exist raises `NotFoundError`.
    """
    for name, value in (("x", x), ("y", y), ("cut_angle_degrees", cut_angle_degrees)):
        if not math.isfinite(value):
            raise InvalidArgumentError(f"{name} must be a finite number.")
    radius_from_width = radius_px is None or cut_half_length_px is None
    radius = float(resolve(STAR_PROFILE_RADIUS_PX).value) if radius_px is None else float(radius_px)  # type: ignore[arg-type]
    cut_half = (
        float(resolve(STAR_CUT_HALF_LENGTH_PX).value)
        if cut_half_length_px is None
        else float(cut_half_length_px)  # type: ignore[arg-type]
    )
    if not math.isfinite(radius) or radius < MINIMUM_RADIUS_PX:
        raise InvalidArgumentError(f"radius_px must be at least {MINIMUM_RADIUS_PX} pixels.")
    if not math.isfinite(cut_half) or cut_half <= 0:
        raise InvalidArgumentError("cut_half_length_px must be above zero.")
    extent_limit = float(resolve(STAR_MAXIMUM_EXTENT_PX).value)  # type: ignore[arg-type]
    if radius > extent_limit or cut_half > extent_limit:
        raise InvalidArgumentError(
            f"radius_px and cut_half_length_px must be at most {extent_limit:g} pixels."
        )

    image = AstrometricsImage(path)
    data = np.asarray(image.data, dtype=float)
    if data.ndim != 2 or data.size == 0:
        raise InvalidArgumentError("The image has no pixel data.")
    height, width = data.shape
    if not (1.0 <= x <= width and 1.0 <= y <= height):
        raise InvalidArgumentError(
            f"The position ({x}, {y}) is outside the image ({width} x {height} pixels)."
        )
    try:
        from astrometricslib._internal.drivers.fits_access import read_header

        with_color = read_header(path).get("NAXIS", 2) == 3
    except OSError:
        with_color = False
    color_note = (
        "The image has color channels; the mean of the channels was measured." if with_color else None
    )

    start_column, start_row = x - 1.0, y - 1.0
    if search_radius_px is not None:
        if not math.isfinite(search_radius_px) or search_radius_px < 0:
            raise InvalidArgumentError("search_radius_px must be zero or more.")
        limit = float(resolve(STAR_SNAP_RADIUS_LIMIT_PX).value)  # type: ignore[arg-type]
        start_column, start_row = _snap_to_peak(
            data, start_column, start_row, min(float(search_radius_px), limit)
        )
    center_column, center_row = _refine_center(
        data,
        start_column,
        start_row,
        float(resolve(STAR_CENTROID_BOX_HALF_WIDTH_PX).value),  # type: ignore[arg-type]
    )

    # Sky: the annulus outside the profile radius.
    annulus_width = float(resolve(STAR_SKY_ANNULUS_WIDTH_PX).value)  # type: ignore[arg-type]
    outer = radius + annulus_width
    x_low, x_high = max(int(center_column - outer) - 1, 0), min(int(center_column + outer) + 2, width)
    y_low, y_high = max(int(center_row - outer) - 1, 0), min(int(center_row + outer) + 2, height)
    rows, columns = np.mgrid[y_low:y_high, x_low:x_high]
    distance = np.hypot(columns - center_column, rows - center_row)
    window = data[y_low:y_high, x_low:x_high]
    annulus = (distance > radius) & (distance <= outer) & np.isfinite(window)
    if int(annulus.sum()) < 10:
        raise InvalidArgumentError("Too little image around the star to measure the sky.")
    _, sky, sky_noise = sigma_clipped_stats(window[annulus], sigma=3.0, maxiters=5)

    inside = (distance <= radius) & np.isfinite(window)
    star_distance = distance[inside]
    star_values = window[inside] - float(sky)
    star_columns, star_rows = columns[inside].astype(float), rows[inside].astype(float)

    peak_raw = float(window[inside].max())
    image_maximum = float(np.nanmax(data))
    at_peak = int(np.sum(window[inside] >= peak_raw))
    clipped = at_peak >= 2 and peak_raw >= float(resolve(STAR_CLIPPED_PEAK_FRACTION).value) * image_maximum  # type: ignore[arg-type]
    if clipped:
        reason = f"{at_peak} pixels share the peak value, which is at the image maximum."
    elif at_peak >= 2:
        reason = f"{at_peak} pixels share the peak value, but it is below the image maximum."
    else:
        reason = "The peak is a single pixel, so there is no flat top."

    scale = _pixel_scale_arcsec(image)
    peak = float(star_values.max())
    star_width_guess = max(2.0, radius / 3.0)
    moffat, gaussian = _fit_curves(
        star_values,
        radius,
        max(peak, 1e-6),
        star_width_guess,
        scale,
        (star_columns, star_rows, center_column, center_row),
    )

    snap_distance = math.hypot(center_column - (x - 1.0), center_row - (y - 1.0))
    star_found, not_a_star_reason = _judge_star(peak, float(sky_noise), moffat)

    # The cut: angle from +y toward +x, so the step is (sin, cos) in (x, y).
    angle = math.radians(cut_angle_degrees)
    step = float(resolve(STAR_CUT_STEP_PX).value)  # type: ignore[arg-type]
    count = math.floor(cut_half / step)
    offsets = np.arange(-count, count + 1) * step
    cut_columns = center_column + offsets * math.sin(angle)
    cut_rows = center_row + offsets * math.cos(angle)
    inside_image = (
        (cut_columns >= 0) & (cut_columns <= width - 1) & (cut_rows >= 0) & (cut_rows <= height - 1)
    )
    sampled = ndimage.map_coordinates(data, [cut_rows[inside_image], cut_columns[inside_image]], order=1)

    cutout_half = math.ceil(cut_half) + 1
    cutout_column0 = round(center_column) - cutout_half
    cutout_row0 = round(center_row) - cutout_half
    side = 2 * cutout_half + 1
    cutout = np.full((side, side), np.nan)
    source_columns = slice(max(cutout_column0, 0), min(cutout_column0 + side, width))
    source_rows = slice(max(cutout_row0, 0), min(cutout_row0 + side, height))
    cutout[
        source_rows.start - cutout_row0 : source_rows.stop - cutout_row0,
        source_columns.start - cutout_column0 : source_columns.stop - cutout_column0,
    ] = data[source_rows, source_columns] - float(sky)

    ring = analyze_star_ring(
        data,
        center_column,
        center_row,
        float(sky),
        float(sky_noise),
        peak,
        star_found,
        cut_half,
        find_neighbors,
    )

    return StarProfileMeasurement(
        path=path,
        color_note=color_note,
        clicked_x=float(x),
        clicked_y=float(y),
        center_x=float(center_column + 1.0),
        center_y=float(center_row + 1.0),
        radius_px=radius,
        radius_from_star_width=bool(radius_from_width),
        sky=float(sky),
        sky_noise=float(sky_noise),
        sky_pixel_count=int(annulus.sum()),
        pixel_scale_arcsec=scale,
        profile_radius_px=[float(v) for v in star_distance],
        profile_values=[float(v) for v in star_values],
        peak=peak,
        peak_raw=peak_raw,
        image_maximum=image_maximum,
        clipped=bool(clipped),
        clipped_reason=reason,
        moffat=moffat,
        gaussian=gaussian,
        cut_angle_degrees=float(cut_angle_degrees),
        cut_half_length_px=cut_half,
        cut_distance_px=[float(v) for v in offsets[inside_image]],
        cut_values=[float(v) - float(sky) for v in sampled],
        cut_step_px=step,
        cutout=[[float(v) if math.isfinite(v) else None for v in row] for row in cutout],
        cutout_origin_x=int(cutout_column0 + 1),
        cutout_origin_y=int(cutout_row0 + 1),
        snapped=bool(search_radius_px is not None and snap_distance >= 1.0),
        snap_distance_px=float(snap_distance),
        star_found=star_found,
        not_a_star_reason=not_a_star_reason,
        ring_inner_px=ring.inner_px,
        ring_outer_px=ring.outer_px,
        ring_sky=ring.sky,
        ring_bin_angle_degrees=ring.bin_angles,
        ring_bin_values=ring.bin_values,
        ring_bin_counts=ring.bin_counts,
        ring_harmonics=ring.harmonics,
        ring_constant=ring.constant,
        ring_scale_value=ring.scale_value,
        ring_neighbors=ring.neighbors,
        ring_masked_neighbor_count=len(ring.neighbors),
        ring_masked_fraction=ring.masked_fraction,
        ring_note=ring.note,
    )
