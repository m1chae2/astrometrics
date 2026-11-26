"""Measures the color of each star of a monochrome picture on a color stack.

The color picture of a monochrome stack (see `color_from_other_stack`) does
not take a star's color from the color picture pixel by pixel. The color
stack's stars are wider than the monochrome stack's (another camera and
optic), so their color would spill around the sharp monochrome stars as a
colored halo. Instead each star's color is measured once, as a number, and
painted only where the monochrome star is.

1. *Footprints.* The stars-only monochrome picture (the picture minus its
   starless copy) is cut into one region per star: every pixel above three
   noise widths belongs to the nearest peak (a watershed split, which follows
   the valleys between touching stars).
2. *Photometry.* Each star's red, green and blue light is summed on the
   linear color stack, already moved onto the monochrome grid, in a circle,
   minus the sky measured in a ring around it. The circle is 1.6 times the
   red star width (6 to 12 pixels), or 5 pixels when a bright neighbor is
   near; 5 pixels still holds about 89% of the light and changes the color
   by about 1.5%. Neighbors' footprints are left out of the sky ring.
3. *Saturated stars.* A star whose core reaches the top of the color stack's
   range has a wrong color in its core. Its color is read from its wing (a
   ring of 6 to 16 pixels, unsaturated pixels only) and corrected by the
   median wing-to-total color difference of bright unsaturated stars. The
   wing is not trusted when the color stack's red stars are wider than its
   green ones by more than a small tolerance (a lens that focuses red light
   less sharply, such as the Nikkor at 300 mm): the far wing of a bright star
   is then mostly red light, so a hot blue star would come out red. Such a
   saturated star takes a catalog color instead: its Gaia ``BP - RP`` when
   it is in the color table, else the ``B - V`` of a catalog of bright stars
   (SIMBAD), turned into ``BP - RP``. With no catalog color it is white.
4. *White balance.* The linear color stack is not color calibrated, so the
   red and blue ratios are scaled until stars with the Sun's Gaia color come
   out white. The white point is the library's one (`sun_like_balance`),
   measured with its own aperture photometry. When the Gaia colors cannot be
   had, there is no white point: the colors are uncalibrated, so every star
   is left gray, and the record says so.
5. *The color of each star.* A star whose color index is known to better
   than 0.10 magnitude, and that has no bright neighbor, is colored by its
   measurement. Otherwise a Gaia ``BP - RP`` from the cache gives its color
   through a straight-line relation fitted on the measured stars (flagged as
   catalog-colored). A bright star in a blend with no Gaia color takes the
   smoothed color of the color picture pixel by pixel, except a saturated
   star whose wing is not trusted. Every other star is left neutral (gray).

The result also compares the measured color index with Gaia's ``BP - RP``
over every cleanly measured star, as a check of the whole measurement.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from astropy.wcs import WCS
from scipy import ndimage
from scipy.spatial import cKDTree
from skimage.color import rgb2lab
from skimage.segmentation import watershed

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.stacking.post_processing.star_width_match import (
    find_measurable_stars,
    measure_channel_widths,
)
from astrometricslib.pipelines.stacking.post_processing.sun_like_balance import (
    SUN_BP_RP,
    SUN_LIKE_MINIMUM_STARS,
    find_sun_like_stars,
    measure_sun_like_ratios,
)
from astrometricslib.utilities.robust_statistics import robust_sigma

__all__ = [
    "SOURCE_NAMES",
    "STAR_APERTURE_RADIUS_RANGE_PIXELS",
    "STAR_COLOR_INDEX_ERROR_LIMIT",
    "STAR_CROWDED_APERTURE_PIXELS",
    "STAR_RED_WIDE_TOLERANCE",
    "StarColors",
    "StarFootprints",
    "bp_rp_from_b_v",
    "chroma_per_lightness",
    "find_star_footprints",
    "measure_star_colors",
]

# A star's footprint is every connected pixel of the stars-only picture above
# the first number of noise widths; a star is a peak above the second number
# times that level. Three noise widths keeps the sky noise out of the
# footprints on the M 27 picture and still reaches into the faint wing of a
# bright star; a peak 2.5 times higher (7.5 noise widths) is a star and not a
# noise bump (STK-62).
STAR_FOOTPRINT_NOISE_MULTIPLES = TunedParameter(
    name="stacking.color_preview_footprint_noise_multiples",
    default=(3.0, 2.5),
    unit="noise widths, multiple of the footprint level",
    description="Level above which a pixel belongs to a star, and the peak height that makes a star.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-62",
)

# The circle that sums a star's light is this many times the red star width
# (FWHM, measured from the second moments of bright isolated stars on the
# linear color stack). The Nikon D5300's red star is about 5.3 pixels wide on
# the M 27 grid, which gives 8 pixels; 1.6 widths holds nearly all the light
# of a Gaussian star (STK-62).
STAR_APERTURE_FWHM_MULTIPLE = TunedParameter(
    name="stacking.color_preview_aperture_fwhm_multiple",
    default=1.6,
    unit="× red star width",
    description="Radius of the circle that sums a star's color light, in red star widths.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-62",
)

# The smallest and largest circle radius, in pixels. Below 6 pixels the
# circle cuts the red wing, which is wider than the green, so the star reads
# too blue; above 12 the ring holds more neighbors than sky on
# a crowded field such as M 27's (STK-62).
STAR_APERTURE_RADIUS_RANGE_PIXELS = TunedParameter(
    name="stacking.color_preview_aperture_radius_range_pixels",
    default=(6.0, 12.0),
    unit="pixels",
    description="Smallest and largest radius of the circle that sums a star's color light.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# The radius used when a bright neighbor is near. A 5 pixel circle holds about
# 89% of a Nikon star's light on the M 27 grid and changes its color by about
# 1.5% (the red wing is a little wider). On M 27 it let 2,126 of 5,081
# measured stars be measured that the full circle would have refused (STK-62).
STAR_CROWDED_APERTURE_PIXELS = TunedParameter(
    name="stacking.color_preview_crowded_aperture_pixels",
    default=5.0,
    unit="pixels",
    description="Radius of the smaller circle used for a star with a bright neighbor.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# The gap between the circle and the inner edge of the sky ring, and between
# the circle and its outer edge, in pixels. A 4 pixel gap keeps the star's
# wing out of the sky; 6 more pixels give a ring of several hundred pixels.
STAR_SKY_RING_GAPS_PIXELS = TunedParameter(
    name="stacking.color_preview_sky_ring_gaps_pixels",
    default=(4.0, 10.0),
    unit="pixels",
    description="Gap from the star circle to the inner and outer edge of its sky ring.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# A neighbor within the inner edge of the sky ring (the circle plus 4 pixels)
# whose core is brighter than this
# share of the star's own core makes the star blended. 0.15 lets a faint
# companion through: it changes the measured color by a few percent at most.
STAR_BLEND_FRACTION = TunedParameter(
    name="stacking.color_preview_blend_fraction",
    default=0.15,
    unit="fraction of the star's core",
    description="Neighbor brightness, as a share of a star's core, that makes the star blended.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-62",
)

# A star's measured color is used only when its color index (blue minus red,
# in magnitudes) is known to better than this. 0.10 magnitude is about the
# robust scatter of the measured index around Gaia's on M 27 (0.12), so a
# noisier star would add more noise than color (STK-62).
STAR_COLOR_INDEX_ERROR_LIMIT = TunedParameter(
    name="stacking.color_preview_color_index_error_limit",
    default=0.10,
    unit="mag",
    description="Largest uncertainty of a star's measured color index that is still used.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-62",
)

# A star is saturated when a pixel of its circle exceeds this share of the
# channel's top (the 99.9995th percentile of the covered pixels); its wing
# color then uses only pixels below the second share. The top of a stack is
# not a hard limit (the frames were scaled before they were added), so the
# first share leaves room, and half the top is well inside the straight part
# of the camera's response.
STAR_SATURATION_SHARES = TunedParameter(
    name="stacking.color_preview_saturation_shares",
    default=(0.85, 0.5),
    unit="fraction of the channel's top",
    description="Share of a channel's top that marks a saturated star, and the limit of usable wing pixels.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-62",
)

# A saturated star's wing color is not trusted when the color stack's red
# star width is more than this share wider than its green star width. The
# widths are the second-moment widths of `_star_widths`, the same numbers the
# star width matching judges (`star_width_match.measure_channel_widths`).
# Measured on the color records of 2026-11-25 (with the earlier estimator):
# the Nikon D5300 at 405 mm gave red over green 0.94 (M 27) and 1.14 (M 31);
# at 300 mm 0.97 to 0.99 (M 13, M 81), 1.14 (M 101), 1.22 (M 45) and 1.25
# (M 42). On M 45 the bright Pleiades, hot blue-white stars, came out red
# and magenta. 0.15 keeps both 405 mm pairs on the wing color and moves the
# two worst 300 mm pairs to catalog colors; M 31 (1.14) is close to the limit
# (STK-62).
STAR_RED_WIDE_TOLERANCE = TunedParameter(
    name="stacking.color_preview_red_wide_tolerance",
    default=0.15,
    unit="fraction of the green star width",
    description="How much wider than green the red stars may be before a saturated star's wing is not used.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-62",
)

# The ring that reads the color of a saturated star's wing, and the ring that
# measures the sky around it, in pixels. The wing ring starts outside the
# saturated core of the brightest M 27 stars and stays inside the sky ring.
# The wing-to-total offset measured on 459 bright unsaturated M 27 stars
# scattered by 0.26 magnitude, so a wing color is rougher than a full one
# (STK-62).
STAR_WING_RADII_PIXELS = TunedParameter(
    name="stacking.color_preview_wing_radii_pixels",
    default=(6.0, 16.0, 22.0, 30.0),
    unit="pixels",
    description="Inner and outer radius of a saturated star's wing ring, then of its sky ring.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# The wing-to-total color offset is calibrated on stars whose core is
# brighter than the first number (a percentile of all star cores) and whose
# full color index is known to better than the second (in magnitudes). On
# M 27 this gave 459 calibration stars (STK-62).
STAR_WING_CALIBRATION = TunedParameter(
    name="stacking.color_preview_wing_calibration",
    default=(97.0, 0.03),
    unit="percentile, mag",
    description="Core-brightness percentile and color-index error of the stars that calibrate wing colors.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-62",
)

# The noise of a pixel on the moved linear stack is shared with its
# neighbors: the bilinear resampling mixes four pixels. The flux error from
# the sky spread is multiplied by this factor to allow for it. 1.5 is the
# square root of the noise-correlation area of a bilinear resampling at about
# the 1:1 scale (about 2.25 pixels).
STAR_RESAMPLING_NOISE_FACTOR = TunedParameter(
    name="stacking.color_preview_resampling_noise_factor",
    default=1.5,
    unit="factor",
    description="Factor on the flux error of a star measured on a resampled stack.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-62",
)

# The distance within which a Gaia star is the partner of a monochrome star,
# in monochrome pixels (about 2 arcseconds on the ZWO ASI533MM at 405 mm).
# Plate solutions agree to well under a pixel, and stars rarely lie closer.
STAR_GAIA_MATCH_PIXELS = TunedParameter(
    name="stacking.color_preview_gaia_match_pixels",
    default=2.0,
    unit="pixels",
    description="Distance within which a Gaia star is taken as the partner of a picture star.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# The Johnson B - V color of the Sun (Ramirez et al. 2012 give 0.653). With
# `SUN_BP_RP` (from `sun_like_balance`) it sets the slope of
# `bp_rp_from_b_v`. A published value, not tuned.
_SUN_B_V = 0.653

# The width of the smoothing of the stars-only picture before its peaks are
# found, in pixels, and the size of the peak search box. Plumbing of the
# footprint search, not tuned.
_FOOTPRINT_SMOOTHING_PIXELS = 1.0
_PEAK_BOX_PIXELS = 5

# The lowest footprint level, in stretched units (0 to 1), so a very smooth
# stars-only picture does not make every pixel a star. Not tuned.
_MINIMUM_FOOTPRINT_LEVEL = 0.01

# The smallest footprint that counts as a star, in pixels. Not tuned.
_MINIMUM_FOOTPRINT_AREA = 4

# The percentile of the covered pixels that is a channel's top. Not tuned.
_CHANNEL_TOP_PERCENTILE = 99.9995

# The fewest sky pixels a ring needs before the neighbors are left in. Not
# tuned.
_MINIMUM_SKY_PIXELS = 30

# The magnitude factor 2.5 / ln(10), which turns a relative flux error into a
# magnitude error. A math constant.
_MAGNITUDE_PER_RELATIVE_ERROR = 1.0857

# What the codes of `StarColors.sources` mean.
SOURCE_NAMES = ("none", "measured", "measured_wing", "gaia", "neutral", "pixel_fallback", "catalog")
_NONE, _MEASURED, _WING, _GAIA, _NEUTRAL, _PIXEL, _CATALOG = range(7)


@dataclass
class StarFootprints:
    """The stars of the monochrome picture, one region per star.

    Attributes
    ----------
    labels : `numpy.ndarray`
        The region number of each pixel (0 outside every star).
    starlight : `numpy.ndarray`
        The stars-only picture (picture minus starless, at least 0).
    centroids : `numpy.ndarray`
        Shape ``(n, 2)``: the row and column of each region's light center;
        row ``k`` is region ``k + 1``.
    flux : `numpy.ndarray`
        Each region's summed starlight.
    area : `numpy.ndarray`
        Each region's size in pixels.
    keep : `numpy.ndarray`
        Whether each region is big enough to count as a star.
    """

    labels: np.ndarray
    starlight: np.ndarray
    centroids: np.ndarray
    flux: np.ndarray
    area: np.ndarray
    keep: np.ndarray

    @property
    def count(self) -> int:
        """The number of regions.

        Returns
        -------
        count : `int`
            The largest region number.
        """
        return len(self.flux)


@dataclass
class StarColors:
    """The color painted on each star, and how it was found.

    Attributes
    ----------
    chroma_a, chroma_b : `numpy.ndarray`
        For each region number (index 0 is no star), the CIE Lab ``a`` and
        ``b`` color per unit of lightness. Zero is gray.
    sources : `numpy.ndarray`
        For each region number, where the color came from (see
        `SOURCE_NAMES`).
    counts : `dict` [`str`, `int`]
        How many stars took their color from each source.
    record : `dict` [`str`, `Any`]
        The measured numbers for the JSON record: star widths, aperture,
        white balance, wing correction and the check against Gaia.
    notes : `list` [`str`]
        Plain sentences about what was skipped or fell back.
    untrusted_wing : `numpy.ndarray` or `None`
        For each region number, whether the star is saturated and its wing
        color was not trusted (the color stack's red stars are too wide).
        The extended color around such a star is not trusted either (see
        `color_from_other_stack.extended_color`). `None` when no star is.
    """

    chroma_a: np.ndarray
    chroma_b: np.ndarray
    sources: np.ndarray
    counts: dict[str, int]
    record: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    untrusted_wing: np.ndarray | None = None


def find_star_footprints(luminance: np.ndarray, starless: np.ndarray) -> StarFootprints:
    """Cut the monochrome picture's stars into one region per star.

    Parameters
    ----------
    luminance : `numpy.ndarray`
        The monochrome picture, values 0 to 1.
    starless : `numpy.ndarray`
        The same picture with the stars taken out (StarNet).

    Returns
    -------
    footprints : `StarFootprints`
        The regions and their centers, sums and sizes.
    """
    luminance = np.asarray(luminance, dtype=np.float32)
    starlight = np.clip(luminance - np.asarray(starless, dtype=np.float32), 0.0, None)
    noise = float(robust_sigma((luminance - ndimage.median_filter(luminance, 5))[::3, ::3].ravel()))
    level_multiple, peak_multiple = (float(value) for value in resolve(STAR_FOOTPRINT_NOISE_MULTIPLES).value)
    level = max(level_multiple * noise, _MINIMUM_FOOTPRINT_LEVEL)
    smooth = ndimage.gaussian_filter(starlight, _FOOTPRINT_SMOOTHING_PIXELS)
    peaks = (ndimage.maximum_filter(smooth, size=_PEAK_BOX_PIXELS) == smooth) & (
        smooth > peak_multiple * level
    )
    markers, count = ndimage.label(peaks)
    if count == 0:
        empty = np.zeros(0)
        return StarFootprints(
            np.zeros(luminance.shape, dtype=np.int32),
            starlight,
            np.zeros((0, 2)),
            empty,
            empty,
            empty.astype(bool),
        )
    labels = watershed(-smooth, markers, mask=starlight > level).astype(np.int32)
    index = np.arange(1, count + 1)
    centroids = np.array(ndimage.center_of_mass(starlight, labels, index), dtype=np.float64).reshape(-1, 2)
    flux = np.asarray(ndimage.sum(starlight, labels, index), dtype=np.float64)
    area = np.asarray(ndimage.sum(np.ones_like(starlight), labels, index), dtype=np.float64)
    keep = (area >= _MINIMUM_FOOTPRINT_AREA) & np.isfinite(centroids).all(axis=1)
    return StarFootprints(labels, starlight, centroids, flux, area, keep)


def chroma_per_lightness(red_ratio: np.ndarray, blue_ratio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Turn white-balanced red and blue ratios into a color per unit lightness.

    The color (red, 1, blue), scaled so its largest part is 1, is encoded
    as display values (sRGB) and turned into CIE Lab. Its ``a`` and ``b``
    are divided by its lightness, so the color can be laid on a star of any
    brightness.

    Parameters
    ----------
    red_ratio, blue_ratio : `numpy.ndarray`
        Red over green and blue over green, white-balanced (1 is white).

    Returns
    -------
    a_per_l, b_per_l : `numpy.ndarray`
        The Lab ``a`` and ``b`` per unit of lightness ``L``.
    """
    red_ratio = np.atleast_1d(np.asarray(red_ratio, dtype=np.float64))
    blue_ratio = np.atleast_1d(np.asarray(blue_ratio, dtype=np.float64))
    rgb = np.stack([red_ratio, np.ones_like(red_ratio), blue_ratio], axis=-1)
    rgb = np.clip(rgb, 1e-6, None)
    rgb = rgb / rgb.max(axis=-1, keepdims=True)
    encoded = np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * rgb ** (1 / 2.4) - 0.055)
    lab = rgb2lab(encoded[:, np.newaxis, :])[:, 0, :]
    lightness = np.maximum(lab[:, 0], 1e-6)
    return lab[:, 1] / lightness, lab[:, 2] / lightness


def bp_rp_from_b_v(b_minus_v: np.ndarray) -> np.ndarray:
    """Turn a Johnson ``B - V`` color into a Gaia ``BP - RP`` color.

    Both color scales are tied to Vega, so a star of Vega's type (A0 V) has
    zero in both. The Sun has ``B - V`` = 0.653 and ``BP - RP`` = 0.82. The
    relation is the straight line through those two points: ``BP - RP`` is
    1.256 times ``B - V``. It is used only for bright saturated stars, which
    are not in the Gaia color table. Compared with stars that have both
    colors in the stored catalog answers (V brighter than 11, 2,931 stars on
    2026-11-25), the line is within 0.1 magnitude of the median ``BP - RP``
    from ``B - V`` = 0.2 to 1.0. Redder stars lie below the line (1.41
    against 1.70 at ``B - V`` = 1.35), where the catalogs' ``B - V`` of
    faint stars is least certain. A blue-white B star with ``B - V`` = -0.1
    gets ``BP - RP`` = -0.13.

    Parameters
    ----------
    b_minus_v : `numpy.ndarray`
        Johnson ``B - V`` colors, in magnitudes.

    Returns
    -------
    bp_rp : `numpy.ndarray`
        Gaia ``BP - RP`` colors, in magnitudes.
    """
    return np.asarray(b_minus_v, dtype=np.float64) * (SUN_BP_RP / _SUN_B_V)


def _star_widths(linear: np.ndarray) -> np.ndarray | None:
    """Measure the star width in each color on the linear color stack.

    The widths are the color preview's one star width
    (`star_width_match.measure_channel_widths`) on the stars that the star
    width matching measures (`star_width_match.find_measurable_stars`), so
    `STAR_RED_WIDE_TOLERANCE` judges the same number as the width matching.

    Parameters
    ----------
    linear : `numpy.ndarray`
        The linear color stack on the monochrome grid, shape
        (3, rows, columns). Where the moved stack holds no data its pixels
        are zero, and no star is measured there.

    Returns
    -------
    widths : `numpy.ndarray` or `None`
        The median red, green and blue width in pixels, or `None` when no
        star could be measured.
    """
    widths = np.asarray(measure_channel_widths(linear, find_measurable_stars(linear)), dtype=np.float64)
    if widths.size != 3 or not np.all(np.isfinite(widths)):
        return None
    return widths


def _white_point(linear: np.ndarray, wcs: WCS | None, gaia_stars: Any) -> dict[str, Any]:
    """Find the gains that make the stars like the Sun white.

    The white point is the library's one (`sun_like_balance`): the Gaia stars
    like the Sun, measured with its aperture photometry.

    Parameters
    ----------
    linear : `numpy.ndarray`
        The linear color stack on the monochrome grid, shape
        (3, rows, columns).
    wcs : `astropy.wcs.WCS` or `None`
        The monochrome picture's plate solution.
    gaia_stars : `astropy.table.Table` or `None`
        The field's Gaia stars (see `gaia_star_colors_in_footprint`).

    Returns
    -------
    balance : `dict` [`str`, `Any`]
        ``method``, ``stars``, ``red_gain`` and ``blue_gain`` when there is a
        white point. Otherwise ``method`` is ``"uncalibrated"`` and
        ``reason`` says why; no gain is given.
    """
    minimum_stars = int(resolve(SUN_LIKE_MINIMUM_STARS).value)
    if wcs is None or gaia_stars is None or len(gaia_stars) == 0:
        return {"method": "uncalibrated", "stars": 0, "reason": "no Gaia star colors"}
    positions, _ = find_sun_like_stars(gaia_stars, wcs, (linear.shape[1], linear.shape[2]))
    measured = measure_sun_like_ratios(linear, positions) if len(positions) else None
    found = 0 if measured is None else measured[4]
    if measured is None or found < minimum_stars:
        return {
            "method": "uncalibrated",
            "stars": found,
            "reason": f"only {found} sun-like Gaia stars were measured; at least {minimum_stars} are needed",
        }
    return {
        "method": "gaia_sun_like_stars",
        "stars": found,
        "red_gain": round(1.0 / measured[0], 4),
        "blue_gain": round(1.0 / measured[1], 4),
    }


def _photometry(
    linear: np.ndarray,
    covered: np.ndarray,
    footprints: StarFootprints,
    aperture: float,
) -> list[dict[str, Any]]:
    """Sum each star's light in each color, minus the sky around it.

    Parameters
    ----------
    linear : `numpy.ndarray`
        The linear color stack on the monochrome grid, shape
        (3, rows, columns).
    covered : `numpy.ndarray`
        Where the moved stack holds real data.
    footprints : `StarFootprints`
        The monochrome stars.
    aperture : `float`
        The full circle radius, in pixels.

    Returns
    -------
    stars : `list` [`dict`]
        One entry per kept star: its region number (``label``), position,
        and either ``status = "edge"`` or the fluxes, flux errors, color
        index error, blend and saturation flags and (for a bright star) the
        wing fluxes.
    """
    rows, columns = covered.shape
    small = float(resolve(STAR_CROWDED_APERTURE_PIXELS).value)
    inner_gap, outer_gap = (float(value) for value in resolve(STAR_SKY_RING_GAPS_PIXELS).value)
    inner, outer = aperture + inner_gap, aperture + outer_gap
    blend_fraction = float(resolve(STAR_BLEND_FRACTION).value)
    saturated_share, wing_share = (float(value) for value in resolve(STAR_SATURATION_SHARES).value)
    wing_inner, wing_outer, wing_sky_inner, wing_sky_outer = (
        float(value) for value in resolve(STAR_WING_RADII_PIXELS).value
    )
    noise_factor = float(resolve(STAR_RESAMPLING_NOISE_FACTOR).value)
    wing_percentile, _ = (float(value) for value in resolve(STAR_WING_CALIBRATION).value)
    channel_top = np.array([np.percentile(linear[c][covered], _CHANNEL_TOP_PERCENTILE) for c in range(3)])

    # The core brightness of each star in green, above a coarse sky, judges
    # which neighbor is bright enough to spoil a measurement.
    green_smooth = ndimage.gaussian_filter(linear[1], 1.5)
    green_sky = ndimage.zoom(ndimage.median_filter(linear[1][::8, ::8], size=7), 8, order=1)
    green_sky = np.pad(
        green_sky,
        ((0, max(0, rows - green_sky.shape[0])), (0, max(0, columns - green_sky.shape[1]))),
        mode="edge",
    )[:rows, :columns]
    centre_rows = np.clip(np.round(footprints.centroids[:, 0]).astype(int), 0, rows - 1)
    centre_columns = np.clip(np.round(footprints.centroids[:, 1]).astype(int), 0, columns - 1)
    core = np.clip(
        green_smooth[centre_rows, centre_columns] - green_sky[centre_rows, centre_columns], 0.0, None
    )
    del green_smooth, green_sky

    kept = np.nonzero(footprints.keep)[0]
    tree = cKDTree(footprints.centroids[kept])
    half = int(outer) + 1
    yy, xx = np.mgrid[-half : half + 1, -half : half + 1]
    wing_half = int(wing_sky_outer)
    wy, wx = np.mgrid[-wing_half : wing_half + 1, -wing_half : wing_half + 1]
    others_mask = ndimage.binary_dilation(footprints.labels > 0, iterations=2)
    wing_core_limit = float(np.percentile(core[kept], wing_percentile)) if len(kept) else 0.0
    stars: list[dict[str, Any]] = []
    for star in kept:
        row, column = footprints.centroids[star]
        entry: dict[str, Any] = {"label": int(star + 1), "row": float(row), "column": float(column)}
        near_row, near_column = round(row), round(column)
        if (
            not (half < near_row < rows - half and half < near_column < columns - half)
            or not covered[near_row, near_column]
        ):
            entry["status"] = "edge"
            stars.append(entry)
            continue
        blended = True
        star_aperture = aperture
        for star_aperture in (aperture, small):
            neighbors = [
                kept[n]
                for n in tree.query_ball_point((row, column), star_aperture + inner_gap)
                if kept[n] != star
            ]
            if not any(core[n] > blend_fraction * max(core[star], 1e-9) for n in neighbors):
                blended = False
                break
        window = (
            slice(near_row - half, near_row + half + 1),
            slice(near_column - half, near_column + half + 1),
        )
        distance = np.hypot(yy - (row - near_row), xx - (column - near_column))
        cut = linear[:, window[0], window[1]]
        own = footprints.labels[window] == star + 1
        ring = (distance >= inner) & (distance <= outer)
        sky_pixels = ring & ~(others_mask[window] & ~own)
        if sky_pixels.sum() < _MINIMUM_SKY_PIXELS:
            sky_pixels = ring
        circle = distance <= star_aperture
        saturated = bool(np.any(cut[:, circle].max(axis=1) > saturated_share * channel_top))
        sky_values = cut[:, sky_pixels]
        sky = np.median(sky_values, axis=1)
        sky_spread = np.array([float(robust_sigma(values)) for values in sky_values])
        used = circle
        if saturated:
            used = circle & np.all(cut < wing_share * channel_top[:, None, None], axis=0)
        pixel_count = int(used.sum())
        flux = (cut[:, used] - sky[:, None]).sum(axis=1)
        flux_error = sky_spread * np.sqrt(pixel_count) * noise_factor
        entry.update({
            "blended": bool(blended),
            "saturated": saturated,
            "flux": flux,
            "flux_error": flux_error,
            "aperture": float(star_aperture),
            "core": float(core[star]),
        })
        if np.all(flux > 0):
            entry["ci_error"] = float(
                _MAGNITUDE_PER_RELATIVE_ERROR * np.hypot(flux_error[2] / flux[2], flux_error[0] / flux[0])
            )
        if (
            (saturated or core[star] > wing_core_limit)
            and wing_half < near_row < rows - wing_half
            and wing_half < near_column < columns - wing_half
        ):
            wing_window = (
                slice(near_row - wing_half, near_row + wing_half + 1),
                slice(near_column - wing_half, near_column + wing_half + 1),
            )
            wing_distance = np.hypot(wy - (row - near_row), wx - (column - near_column))
            wing_cut = linear[:, wing_window[0], wing_window[1]]
            wing_others = others_mask[wing_window] & ~(footprints.labels[wing_window] == star + 1)
            wing_sky_pixels = (
                (wing_distance >= wing_sky_inner) & (wing_distance <= wing_sky_outer) & ~wing_others
            )
            wing_pixels = (
                (wing_distance >= wing_inner)
                & (wing_distance <= wing_outer)
                & np.all(wing_cut < wing_share * channel_top[:, None, None], axis=0)
                & ~wing_others
            )
            if wing_sky_pixels.any() and wing_pixels.any():
                wing_sky = np.median(wing_cut[:, wing_sky_pixels], axis=1)
                wing_flux = (wing_cut[:, wing_pixels] - wing_sky[:, None]).sum(axis=1)
                if np.all(wing_flux > 0):
                    entry["wing_flux"] = wing_flux
        stars.append(entry)
    return stars


def _match_gaia(stars: list[dict[str, Any]], gaia_stars: Any, wcs: WCS, shape: tuple[int, int]) -> np.ndarray:
    """Find the Gaia ``BP - RP`` color of each measured star.

    Parameters
    ----------
    stars : `list` [`dict`]
        The measured stars, with ``row`` and ``column``.
    gaia_stars : `astropy.table.Table` or `None`
        Gaia stars with ``ra``, ``dec`` and ``bp_rp``.
    wcs : `astropy.wcs.WCS`
        The monochrome picture's plate solution.
    shape : `tuple` [`int`, `int`]
        The picture's rows and columns.

    Returns
    -------
    bp_rp : `numpy.ndarray`
        One color per star, NaN when no Gaia partner has a color.
    """
    bp_rp = np.full(len(stars), np.nan)
    if gaia_stars is None or len(gaia_stars) == 0 or not stars:
        return bp_rp
    gaia_columns, gaia_rows = wcs.world_to_pixel_values(
        np.asarray(gaia_stars["ra"], dtype=np.float64), np.asarray(gaia_stars["dec"], dtype=np.float64)
    )
    inside = (
        (gaia_columns > -5) & (gaia_columns < shape[1] + 5) & (gaia_rows > -5) & (gaia_rows < shape[0] + 5)
    )
    if not inside.any():
        return bp_rp
    tree = cKDTree(np.column_stack([gaia_rows[inside], gaia_columns[inside]]))
    positions = np.array([[star["row"], star["column"]] for star in stars])
    distance, index = tree.query(positions, distance_upper_bound=float(resolve(STAR_GAIA_MATCH_PIXELS).value))
    matched = np.isfinite(distance)
    colors = np.asarray(gaia_stars["bp_rp"], dtype=np.float64)[inside]
    bp_rp[matched] = colors[index[matched]]
    return bp_rp


def _match_catalog_by_footprint(
    stars: list[dict[str, Any]], catalog: Any, wcs: WCS, labels: np.ndarray
) -> np.ndarray:
    """Find each star's catalog ``BP - RP`` color from its footprint.

    A saturated star's light center can sit a few pixels from its catalog
    position, so a catalog star is paired with the monochrome star whose
    footprint holds its position, and that lies within the star's
    photometry circle of the footprint's light center. The circle keeps a
    fainter star that sits in the large footprint of a bright star's glow
    from coloring the whole glow. When a footprint holds several such
    catalog stars, the brightest one (smallest V) is used.

    Parameters
    ----------
    stars : `list` [`dict`]
        The measured stars, with their region number (``label``), light
        center (``row``, ``column``) and photometry circle (``aperture``).
    catalog : `astropy.table.Table` or `None`
        Catalog stars with ``ra``, ``dec``, ``v`` and ``b_minus_v``.
    wcs : `astropy.wcs.WCS`
        The monochrome picture's plate solution.
    labels : `numpy.ndarray`
        The footprint region number of each pixel.

    Returns
    -------
    bp_rp : `numpy.ndarray`
        One color per star (see `bp_rp_from_b_v`), NaN when its footprint
        holds no catalog star with a color.
    """
    bp_rp = np.full(len(stars), np.nan)
    if catalog is None or len(catalog) == 0 or not stars:
        return bp_rp
    b_minus_v = np.ma.filled(np.ma.asarray(catalog["b_minus_v"], dtype=np.float64), np.nan)
    visual = np.ma.filled(np.ma.asarray(catalog["v"], dtype=np.float64), np.nan)
    columns, rows = wcs.world_to_pixel_values(
        np.asarray(catalog["ra"], dtype=np.float64), np.asarray(catalog["dec"], dtype=np.float64)
    )
    rows = np.asarray(rows, dtype=np.float64)
    columns = np.asarray(columns, dtype=np.float64)
    centers = {star["label"]: (star["row"], star["column"], star["aperture"]) for star in stars}
    usable = (
        np.isfinite(b_minus_v)
        & np.isfinite(rows)
        & np.isfinite(columns)
        & (rows >= 0)
        & (rows < labels.shape[0] - 0.5)
        & (columns >= 0)
        & (columns < labels.shape[1] - 0.5)
    )
    label_color: dict[int, tuple[float, float]] = {}
    for index in np.nonzero(usable)[0]:
        label = int(labels[round(rows[index]), round(columns[index])])
        if label not in centers:
            continue
        center_row, center_column, aperture = centers[label]
        if np.hypot(rows[index] - center_row, columns[index] - center_column) > aperture:
            continue
        brightness = visual[index] if np.isfinite(visual[index]) else np.inf
        if label not in label_color or brightness < label_color[label][0]:
            label_color[label] = (brightness, float(b_minus_v[index]))
    for k, star in enumerate(stars):
        if star["label"] in label_color:
            bp_rp[k] = float(bp_rp_from_b_v(label_color[star["label"]][1]))
    return bp_rp


def _glow_parents(
    labels: np.ndarray, measured: list[dict[str, Any]], untrusted: np.ndarray
) -> dict[int, int]:
    """Find the regions that are pieces of a saturated star's glow.

    The footprint of a bright saturated star is large, and the footprint
    search cuts it into many regions: its core, each diffraction spike and
    parts of its halo. Every region in the same connected patch of starlight
    as a saturated star whose wing is not trusted counts as a piece of its
    glow, and belongs to the nearest such star of the patch.

    Parameters
    ----------
    labels : `numpy.ndarray`
        The footprint region number of each pixel.
    measured : `list` [`dict`]
        The measured stars, with their region number (``label``), ``row``
        and ``column``.
    untrusted : `numpy.ndarray`
        Whether each measured star is saturated with an untrusted wing.

    Returns
    -------
    parents : `dict` [`int`, `int`]
        For each region in such a patch (other than the saturated stars
        themselves), the region number of its saturated star.
    """
    if not np.any(untrusted):
        return {}
    untrusted_labels = np.zeros(int(labels.max()) + 1, dtype=bool)
    untrusted_labels[[star["label"] for star, flag in zip(measured, untrusted, strict=True) if flag]] = True
    patches, _ = ndimage.label(labels > 0)
    glow_patches = np.unique(patches[untrusted_labels[labels]])
    glow_patches = glow_patches[glow_patches > 0]
    parents: dict[int, int] = {}
    boxes = ndimage.find_objects(patches)
    for patch in glow_patches:
        box = boxes[patch - 1]
        inside = patches[box] == patch
        patch_labels = np.where(inside, labels[box], 0)
        seeds = untrusted_labels[patch_labels] & inside
        _, (seed_rows, seed_columns) = ndimage.distance_transform_edt(~seeds, return_indices=True)
        nearest = patch_labels[seed_rows, seed_columns]
        for label in np.unique(patch_labels[inside & ~seeds]):
            if label == 0:
                continue
            pixels = patch_labels == label
            parents[int(label)] = int(np.bincount(nearest[pixels]).argmax())
    return parents


def _saturated_record(
    measured: list[dict[str, Any]],
    saturated: np.ndarray,
    sources: np.ndarray,
    red_to_green: float,
    wing_trusted: bool,
) -> dict[str, Any]:
    """Count where the saturated stars took their color from.

    Parameters
    ----------
    measured : `list` [`dict`]
        The measured stars, with their region number (``label``).
    saturated : `numpy.ndarray`
        Whether each measured star is saturated.
    sources : `numpy.ndarray`
        The color source of each region (see `SOURCE_NAMES`).
    red_to_green : `float`
        The color stack's red star width over its green star width.
    wing_trusted : `bool`
        Whether the wing color of a saturated star could be used.

    Returns
    -------
    record : `dict` [`str`, `Any`]
        The width ratio, whether the wing was trusted, and the number of
        saturated stars colored from their wing, from a catalog (Gaia or
        the bright-star catalog), left white, and given the pixel-by-pixel
        color. These counts are part of the counts by source, not added to
        them.
    """
    saturated_sources = [
        int(sources[star["label"]]) for star, flag in zip(measured, saturated, strict=True) if flag
    ]
    return {
        "red_to_green_width_ratio": round(red_to_green, 3) if np.isfinite(red_to_green) else None,
        "wing_trusted": bool(wing_trusted),
        "saturated_wing": saturated_sources.count(_WING),
        "saturated_catalog": saturated_sources.count(_GAIA) + saturated_sources.count(_CATALOG),
        "saturated_neutral": saturated_sources.count(_NEUTRAL),
        "saturated_pixel_fallback": saturated_sources.count(_PIXEL),
    }


def measure_star_colors(
    luminance: np.ndarray,
    starless: np.ndarray,
    linear: np.ndarray,
    covered: np.ndarray,
    wcs: WCS | None,
    gaia_stars: Any,
    gaia_note: str = "",
    bright_star_catalog: Callable[[], tuple[Any, str]] | None = None,
) -> tuple[StarFootprints, StarColors]:
    """Find the monochrome stars and the color each one is painted with.

    Parameters
    ----------
    luminance : `numpy.ndarray`
        The monochrome picture, values 0 to 1.
    starless : `numpy.ndarray`
        The same picture without stars.
    linear : `numpy.ndarray`
        The linear color stack moved onto the monochrome grid, shape
        (3, rows, columns).
    covered : `numpy.ndarray`
        Where the moved stack holds real data.
    wcs : `astropy.wcs.WCS` or `None`
        The monochrome picture's plate solution, to place the Gaia stars.
    gaia_stars : `astropy.table.Table` or `None`
        Gaia stars of the field with ``ra``, ``dec`` and ``bp_rp``, or `None`
        when the colors could not be had.
    gaia_note : `str`, optional
        Why there are no Gaia stars, for the notes.
    bright_star_catalog : `Callable`, optional
        Gives the catalog colors of the field's bright stars, as a table with
        ``ra``, ``dec``, ``v`` and ``b_minus_v`` (or `None`), and a note
        saying why there are none (or an empty string). It is called only
        when a saturated star needs a catalog color, so a field that needs
        none asks no catalog.

    Returns
    -------
    footprints : `StarFootprints`
        The monochrome stars.
    colors : `StarColors`
        The color of each star and how it was found.
    """
    footprints = find_star_footprints(luminance, starless)
    labels_total = footprints.count + 1
    chroma_a = np.zeros(labels_total, dtype=np.float32)
    chroma_b = np.zeros(labels_total, dtype=np.float32)
    sources = np.zeros(labels_total, dtype=np.int8)
    counts = dict.fromkeys(SOURCE_NAMES[1:], 0)
    counts["frame_edge"] = 0
    colors = StarColors(chroma_a, chroma_b, sources, counts)
    if not footprints.keep.any():
        colors.notes.append("No stars were found on the monochrome picture, so no star color was measured.")
        return footprints, colors

    widths = _star_widths(linear)
    low, high = (float(value) for value in resolve(STAR_APERTURE_RADIUS_RANGE_PIXELS).value)
    if widths is None:
        aperture = float(np.clip(8.0, low, high))
        colors.notes.append("The color stack's star width could not be measured; an 8 pixel circle was used.")
    else:
        aperture = float(
            np.clip(np.round(float(resolve(STAR_APERTURE_FWHM_MULTIPLE).value) * widths[0]), low, high)
        )
        colors.record["color_star_width_pixels_rgb"] = [round(float(value), 2) for value in widths]
    colors.record["aperture_pixels"] = aperture
    # A saturated star's wing is trusted unless the red stars are clearly
    # wider than the green ones (or the widths are unknown, as before).
    red_to_green = float(widths[0] / widths[1]) if widths is not None and widths[1] > 0 else float("nan")
    wing_trusted = not red_to_green > 1.0 + float(resolve(STAR_RED_WIDE_TOLERANCE).value)

    stars = _photometry(linear, covered, footprints, aperture)
    measured = [star for star in stars if star.get("status") != "edge"]
    for star in stars:
        if star.get("status") == "edge":
            sources[star["label"]] = _NEUTRAL
            counts["frame_edge"] += 1
    if not measured:
        colors.notes.append("No star lies far enough inside the color stack to be measured.")
        return footprints, colors

    error_limit = float(resolve(STAR_COLOR_INDEX_ERROR_LIMIT).value)
    _, wing_error = (float(value) for value in resolve(STAR_WING_CALIBRATION).value)
    minimum_stars = int(resolve(SUN_LIKE_MINIMUM_STARS).value)

    def good(star: dict[str, Any]) -> bool:
        """Tell whether a star's measured color is clean enough to use.

        Returns
        -------
        good : `bool`
            True when its color index error is below the limit and it has
            no bright neighbor.
        """
        return "ci_error" in star and star["ci_error"] < error_limit and not star["blended"]

    with np.errstate(divide="ignore", invalid="ignore"):
        raw_red = np.array([s["flux"][0] / s["flux"][1] if "ci_error" in s else np.nan for s in measured])
        raw_blue = np.array([s["flux"][2] / s["flux"][1] if "ci_error" in s else np.nan for s in measured])
    saturated = np.array([s["saturated"] for s in measured])
    clean = np.array([good(s) for s in measured]) & ~saturated
    bp_rp = (
        _match_gaia(measured, gaia_stars, wcs, covered.shape)
        if wcs is not None
        else np.full(len(measured), np.nan)
    )
    colors.record["gaia_matched"] = int(np.isfinite(bp_rp).sum())

    # White balance: the library's white point, the Gaia stars like the Sun
    # (see `sun_like_balance`). With no white point the colors are left
    # uncalibrated: every star stays gray, and the record says so.
    balance = _white_point(linear, wcs, gaia_stars)
    if "red_gain" not in balance:
        colors.record["white_balance"] = balance
        colors.notes.append(
            f"The color stack has no white point ({balance['reason']}{gaia_note}); its colors are "
            "uncalibrated, so every star is left gray."
        )
        for star in measured:
            sources[star["label"]] = _NEUTRAL
            counts["neutral"] += 1
        return footprints, colors
    red_gain, blue_gain = balance["red_gain"], balance["blue_gain"]
    colors.record["white_balance"] = balance

    # Saturated stars: color from the wing, corrected by the wing-to-total
    # offset of bright clean unsaturated stars.
    with np.errstate(divide="ignore", invalid="ignore"):
        wing_red = np.array([
            s["wing_flux"][0] / s["wing_flux"][1] if "wing_flux" in s else np.nan for s in measured
        ])
        wing_blue = np.array([
            s["wing_flux"][2] / s["wing_flux"][1] if "wing_flux" in s else np.nan for s in measured
        ])
    calibrators = (
        clean & np.isfinite(wing_red) & np.array([s.get("ci_error", 9.0) < wing_error for s in measured])
    )
    usable_wing = np.zeros(len(measured), dtype=bool)
    if calibrators.sum() >= minimum_stars:
        offset_red = np.log10(wing_red[calibrators] / raw_red[calibrators])
        offset_blue = np.log10(wing_blue[calibrators] / raw_blue[calibrators])
        colors.record["wing_calibration"] = {
            "stars": int(calibrators.sum()),
            "log_offset_red": round(float(np.median(offset_red)), 4),
            "log_offset_blue": round(float(np.median(offset_blue)), 4),
            "scatter_color_index_mag": round(float(2.5 * robust_sigma(offset_blue - offset_red)), 4),
        }
        usable_wing = saturated & np.isfinite(wing_red) & wing_trusted
        raw_red[usable_wing] = wing_red[usable_wing] / 10 ** np.median(offset_red)
        raw_blue[usable_wing] = wing_blue[usable_wing] / 10 ** np.median(offset_blue)
    else:
        colors.notes.append(
            "Too few bright stars to calibrate the wing color; saturated stars use Gaia or gray."
        )
    for k in np.nonzero(saturated & ~usable_wing)[0]:
        measured[k].pop("ci_error", None)
    red_ratio, blue_ratio = raw_red * red_gain, raw_blue * blue_gain
    with np.errstate(divide="ignore", invalid="ignore"):
        color_index = -2.5 * np.log10(blue_ratio / red_ratio)

    # The check against Gaia, and the relation for catalog-colored stars.
    check = clean & np.isfinite(bp_rp) & np.isfinite(color_index)
    relation = None
    if check.sum() >= minimum_stars:
        slope, intercept = np.polyfit(bp_rp[check], color_index[check], 1)
        scatter = float(robust_sigma(color_index[check] - (slope * bp_rp[check] + intercept)))
        colors.record["gaia_check"] = {
            "stars": int(check.sum()),
            "pearson_r": round(float(np.corrcoef(bp_rp[check], color_index[check])[0, 1]), 4),
            "slope": round(float(slope), 4),
            "intercept": round(float(intercept), 4),
            "robust_scatter_mag": round(scatter, 4),
        }
        relation = (
            np.polyfit(bp_rp[check] - SUN_BP_RP, np.log10(red_ratio[check]), 1),
            np.polyfit(bp_rp[check] - SUN_BP_RP, np.log10(blue_ratio[check]), 1),
        )
    elif gaia_stars is not None:
        colors.notes.append(
            f"Only {int(check.sum())} measured stars have a Gaia color; no Gaia check was made."
        )

    def from_gaia(bp_rp_value: float) -> tuple[float, float]:
        """Give a star's color from its Gaia color, through the fitted line.

        Parameters
        ----------
        bp_rp_value : `float`
            The star's ``BP - RP`` color.

        Returns
        -------
        a_per_l, b_per_l : `float`
            The Lab color per unit of lightness.
        """
        offset = bp_rp_value - SUN_BP_RP
        a_value, b_value = chroma_per_lightness(
            10 ** np.polyval(relation[0], offset), 10 ** np.polyval(relation[1], offset)
        )
        return float(a_value[0]), float(b_value[0])

    good_flags = np.array([good(s) for s in measured])
    bright_core = (
        float(np.median([s["core"] for s, flag in zip(measured, good_flags, strict=True) if flag]))
        if good_flags.any()
        else np.inf
    )
    measured_a, measured_b = chroma_per_lightness(
        np.where(np.isfinite(red_ratio), red_ratio, 1.0), np.where(np.isfinite(blue_ratio), blue_ratio, 1.0)
    )
    has_gaia = np.isfinite(bp_rp) & (relation is not None)
    # Saturated stars whose wing is not trusted: a catalog color or white.
    untrusted = saturated & (not wing_trusted)
    # The pieces of a saturated star's glow (its diffraction spikes and the
    # rest of its large footprint, cut into regions of their own) were
    # measured on the same red wing, so they are not trusted either.
    glow_parent = _glow_parents(footprints.labels, measured, untrusted)
    in_glow = np.array([star["label"] in glow_parent for star in measured], dtype=bool)
    catalog_bp_rp = np.full(len(measured), np.nan)
    if (
        relation is not None
        and wcs is not None
        and bright_star_catalog is not None
        and np.any((untrusted | in_glow) & ~has_gaia)
    ):
        catalog, catalog_note = bright_star_catalog()
        if catalog_note:
            colors.notes.append(catalog_note)
        catalog_bp_rp = _match_catalog_by_footprint(measured, catalog, wcs, footprints.labels)
    for k, star in enumerate(measured):
        label = star["label"]
        if untrusted[k] or in_glow[k]:
            if has_gaia[k]:
                chroma_a[label], chroma_b[label] = from_gaia(bp_rp[k])
                sources[label] = _GAIA
                counts["gaia"] += 1
            elif relation is not None and np.isfinite(catalog_bp_rp[k]):
                chroma_a[label], chroma_b[label] = from_gaia(catalog_bp_rp[k])
                sources[label] = _CATALOG
                counts["catalog"] += 1
            elif untrusted[k]:
                sources[label] = _NEUTRAL
                counts["neutral"] += 1
            # A glow piece with no catalog star takes its star's color below.
        elif not star["blended"] and good_flags[k]:
            chroma_a[label], chroma_b[label] = measured_a[k], measured_b[k]
            sources[label] = _WING if star["saturated"] else _MEASURED
            counts["measured_wing" if star["saturated"] else "measured"] += 1
        elif has_gaia[k]:
            chroma_a[label], chroma_b[label] = from_gaia(bp_rp[k])
            sources[label] = _GAIA
            counts["gaia"] += 1
        elif star["blended"] and star["core"] > bright_core:
            sources[label] = _PIXEL
            counts["pixel_fallback"] += 1
        else:
            sources[label] = _NEUTRAL
            counts["neutral"] += 1
    glow_painted = 0
    for k, star in enumerate(measured):
        label = star["label"]
        if in_glow[k] and sources[label] == _NONE:
            parent = glow_parent[label]
            chroma_a[label], chroma_b[label] = chroma_a[parent], chroma_b[parent]
            sources[label] = sources[parent]
            counts[SOURCE_NAMES[sources[parent]]] += 1
            glow_painted += 1
    colors.record["saturated_stars"] = _saturated_record(
        measured, saturated, sources, red_to_green, wing_trusted
    )
    colors.record["saturated_stars"]["glow_pieces_colored_as_their_star"] = glow_painted
    if untrusted.any():
        colors.untrusted_wing = np.zeros(labels_total, dtype=bool)
        untrusted_labels = [
            star["label"] for star, flag in zip(measured, untrusted | in_glow, strict=True) if flag
        ]
        colors.untrusted_wing[untrusted_labels] = True
        record = colors.record["saturated_stars"]
        colors.notes.append(
            f"The color stack's red stars are {100 * (red_to_green - 1):.0f}% wider than its green ones, "
            f"so the wing color of saturated stars was not used: {record['saturated_catalog']} took a "
            f"catalog color and {record['saturated_neutral']} were left white."
        )
    colors.record["aperture_use"] = {
        "full": int(
            sum(1 for s, f in zip(measured, good_flags, strict=True) if f and s["aperture"] == aperture)
        ),
        "small": int(
            sum(1 for s, f in zip(measured, good_flags, strict=True) if f and s["aperture"] != aperture)
        ),
    }
    return footprints, colors
