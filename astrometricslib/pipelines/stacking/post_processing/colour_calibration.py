"""Balances the colours of a colour stack from its own stars.

A colour camera's red, green and blue channels do not respond equally to the
same light, and the sky glow adds a different amount to each. The raw stack
therefore shows grey stars as green, a grey sky as a tinted background and a
galaxy as nearly colourless. This module corrects both effects on the linear
stack, before it is stretched. It needs no catalogue and no network, so its
result is the same every time and can be checked by hand.

1. *Background.* The sky level of each channel is found as the median of its
   pixels, since stars cover only a small part of the frame. Each channel's
   sky level is subtracted, so the sky has the same level in all three.
2. *Star colours.* Stars are found on the summed channels. Each one is
   measured in each channel with aperture photometry: the light inside a small
   circle, minus the sky level measured in a ring around it. Only stars in the
   middle of the brightness range are used. The brightest may be saturated or
   leave the sensor's linear range, and the faintest are too noisy.
3. *Gains.* A typical star in a magnitude-limited star field is close to the
   Sun in colour, so the median star should come out neutral. The red and
   blue gains that make the median red-to-green and blue-to-green flux
   ratios equal to 1 are applied to the channels. The green channel is the
   reference and is not changed.

The result is an approximation. Real stars range from red to blue, so the
median star is neutral only if the field's stars are typical. Siril's
photometric colour calibration compares stars with a catalogue and does not
make that assumption (see the comparison in this folder's README).

The stack itself is never changed. This works on the scratch copy that the
preview is made from.
"""

import logging
import warnings
from dataclasses import dataclass

import numpy as np
from astropy.stats import SigmaClip
from photutils.aperture import ApertureStats, CircularAnnulus, CircularAperture
from photutils.detection import DAOStarFinder
from photutils.utils.exceptions import NoDetectionsWarning

from astrometricslib._internal.drivers.fits_access import read_data, read_header, write_image
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import factor_for
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    RunBasis,
    TunedParameter,
)
from astrometricslib.pipelines.stacking.post_processing.stack_image import (
    MAD_TO_SIGMA,
    robust_sigma,
)

logger = logging.getLogger(__name__)

# The star finder's expected star width, in pixels. Stars in the Nikon stacks
# (405 mm, 3.9 micron pixels, about 2 arcseconds per pixel) are 2.5 to 3.5
# pixels wide. The finder is not sensitive to a value within 30 percent of the
# true width.
# On the reference setup this stands for 3.0 pixels; the code scales it as a
# multiple of the star width, so it follows the star width the run measures.
FINDER_FWHM_PIXELS = TunedParameter(
    name="stacking.finder_fwhm_pixels",
    default=factor_for(3.0, RunBasis.STAR_WIDTH_PX),
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=3.0,
    unit="× star width",
    description="Star width the star finder expects.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D17",
)

# A star must stand this many sky-noise widths above the sky to be found.
# Chosen to give several hundred stars on a typical Nikon stack while keeping
# noise peaks out. Not tuned beyond that.
DETECTION_SIGMA = TunedParameter(
    name="stacking.detection_sigma",
    default=8.0,
    unit="sigma",
    description="Sky-noise widths above the sky a star must stand to be found.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D17",
)

# The radius of the circle that sums a star's light, in pixels. About 1.2
# times the star's full width at half maximum catches most of its light and
# keeps the noise down.
# On the reference setup this stands for 3.5 pixels; the code scales it as a
# multiple of the star width, so it follows the star width the run measures.
APERTURE_RADIUS_PIXELS = TunedParameter(
    name="stacking.aperture_radius_pixels",
    default=factor_for(3.5, RunBasis.STAR_WIDTH_PX),
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=3.5,
    unit="× star width",
    description="Radius of the circle that sums a star's light.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D17",
)

# The ring that measures the sky around a star, in pixels. It lies well
# outside the star's wings. Sigma clipping drops any neighbouring star.
# On the reference setup this stands for 7.0 pixels; the code scales it as a
# multiple of the star width, so it follows the star width the run measures.
ANNULUS_INNER_RADIUS_PIXELS = TunedParameter(
    name="stacking.annulus_inner_radius_pixels",
    default=factor_for(7.0, RunBasis.STAR_WIDTH_PX),
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=7.0,
    unit="× star width",
    description="Inner radius of the ring that measures the sky around a star.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D17",
)
# On the reference setup this stands for 11.0 pixels; the code scales it as a
# multiple of the star width, so it follows the star width the run measures.
ANNULUS_OUTER_RADIUS_PIXELS = TunedParameter(
    name="stacking.annulus_outer_radius_pixels",
    default=factor_for(11.0, RunBasis.STAR_WIDTH_PX),
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=11.0,
    unit="× star width",
    description="Outer radius of the ring that measures the sky around a star.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D17",
)

# Only stars between these percentiles of total brightness are used. The
# brightest stars may be saturated or leave the sensor's linear range, and
# the faintest have noisy colours. The limits are design choices, not fitted
# values; the result changes by less than the scatter between star subsets
# when they move by 10 percentage points.
BRIGHTNESS_PERCENTILE_RANGE = TunedParameter(
    name="stacking.brightness_percentile_range",
    default=(30.0, 85.0),
    unit="percentile",
    description="Percentiles of total brightness between which stars are used for colour calibration.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-27",
)

# Fewer stars than this give a gain too uncertain to apply.
MINIMUM_STARS = TunedParameter(
    name="stacking.minimum_stars",
    default=40,
    unit="stars",
    description="Fewest stars that give a colour gain certain enough to apply.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-27",
)


# Stars closer than this to the image edge are skipped, so the ring is whole.
def _edge_margin_pixels() -> int:
    """Give how close to the edge a star may be before it is skipped.

    Returns
    -------
    value : `int`
        Pixels.
    """
    return int(resolve(ANNULUS_OUTER_RADIUS_PIXELS).value) + 2


@dataclass(frozen=True)
class ColourCalibration:
    """What the calibration found and applied.

    Attributes
    ----------
    red_gain : `float`
        The factor applied to the red channel (green is 1).
    blue_gain : `float`
        The factor applied to the blue channel.
    star_count : `int`
        How many stars gave the gains.
    red_uncertainty : `float`
        The relative uncertainty of the red gain: the scatter of the star
        ratios divided by the square root of the star count.
    blue_uncertainty : `float`
        The same for the blue gain.
    sky_levels : `tuple` [`float`, `float`, `float`]
        The sky level that was subtracted from each channel (red, green, blue).
    """

    red_gain: float
    blue_gain: float
    star_count: int
    red_uncertainty: float
    blue_uncertainty: float
    sky_levels: tuple[float, float, float]


def measure_sky_levels(image: np.ndarray) -> np.ndarray:
    """Find the sky level of each channel.

    Parameters
    ----------
    image : `numpy.ndarray`
        A colour image, channels first, with shape (3, height, width).

    Returns
    -------
    sky_levels : `numpy.ndarray`
        The median of each channel. Stars cover a small part of the frame,
        so the median is the sky.
    """
    return np.array([np.median(channel) for channel in image], dtype=np.float64)


def _find_stars(summed: np.ndarray, noise: float) -> np.ndarray:
    """Find star positions on the summed channels.

    Parameters
    ----------
    summed : `numpy.ndarray`
        The sum of the three channels with the sky removed, shape (height,
        width).
    noise : `float`
        The sky noise of `summed`, as a standard deviation.

    Returns
    -------
    positions : `numpy.ndarray`
        An array of shape (n, 2) holding the x and y pixel positions of the
        stars away from the image edge. Empty if none were found.
    """
    finder = DAOStarFinder(
        fwhm=resolve(FINDER_FWHM_PIXELS).value, threshold=resolve(DETECTION_SIGMA).value * noise
    )
    with warnings.catch_warnings():
        # An image with no stars is an ordinary case, handled below.
        warnings.simplefilter("ignore", NoDetectionsWarning)
        found = finder(summed)
    if found is None or len(found) == 0:
        return np.empty((0, 2))
    height, width = summed.shape
    # photutils 3 renamed the centroid columns; the old names still work.
    prefix = ("x_", "y_") if "x_centroid" in found.colnames else ("x", "y")
    x, y = np.asarray(found[f"{prefix[0]}centroid"]), np.asarray(found[f"{prefix[1]}centroid"])
    inside = (
        (x > _edge_margin_pixels())
        & (x < width - _edge_margin_pixels())
        & (y > _edge_margin_pixels())
        & (y < height - _edge_margin_pixels())
    )
    return np.column_stack([x[inside], y[inside]])


def _star_fluxes(channel: np.ndarray, positions: np.ndarray) -> np.ndarray:
    """Measure each star's light in one channel, with the local sky removed.

    Parameters
    ----------
    channel : `numpy.ndarray`
        One channel, shape (height, width).
    positions : `numpy.ndarray`
        Star positions, shape (n, 2), as x and y.

    Returns
    -------
    fluxes : `numpy.ndarray`
        For each star, the sum of the light in the aperture minus the
        median sky of the surrounding ring times the aperture area.
    """
    aperture = CircularAperture(positions, r=resolve(APERTURE_RADIUS_PIXELS).value)
    ring = CircularAnnulus(
        positions,
        r_in=resolve(ANNULUS_INNER_RADIUS_PIXELS).value,
        r_out=resolve(ANNULUS_OUTER_RADIUS_PIXELS).value,
    )
    sky = ApertureStats(channel, ring, sigma_clip=SigmaClip(sigma=3.0, maxiters=5)).median
    total = ApertureStats(channel, aperture).sum
    return np.asarray(total) - np.asarray(sky) * aperture.area


def _sky_noise(channel: np.ndarray, sky_level: float) -> float:
    """Estimate the sky noise of a channel from the median absolute deviation.

    Returns
    -------
    noise : `float`
        The standard deviation the sky pixels would have if they were
        Gaussian. Stars barely change the median deviation.
    """
    return float(MAD_TO_SIGMA * np.median(np.abs(channel - sky_level)))


def measure_gains(image: np.ndarray) -> ColourCalibration | None:
    """Find the red and blue gains that make the median star neutral.

    Parameters
    ----------
    image : `numpy.ndarray`
        A colour image, channels first, with shape (3, height, width).

    Returns
    -------
    calibration : `ColourCalibration` or `None`
        The gains and how well they are known, or `None` when fewer than
        `MINIMUM_STARS` usable stars were found. The gains here belong to
        the image as given; the sky is removed from it inside this function
        only to find the stars.
    """
    minimum_stars = resolve(MINIMUM_STARS).value
    sky_levels = measure_sky_levels(image)
    flat = image - sky_levels[:, None, None]
    summed = flat.sum(axis=0)
    noise = _sky_noise(summed, 0.0)
    if noise <= 0.0:
        return None
    positions = _find_stars(summed, noise)
    if len(positions) < minimum_stars:
        return None
    red, green, blue = (_star_fluxes(flat[index], positions) for index in range(3))
    usable = (red > 0) & (green > 0) & (blue > 0)
    total = red + green + blue
    low, high = (
        np.percentile(total[usable], resolve(BRIGHTNESS_PERCENTILE_RANGE).value)
        if usable.any()
        else (0.0, 0.0)
    )
    chosen = usable & (total >= low) & (total <= high)
    if chosen.sum() < minimum_stars:
        return None
    log_red = np.log(red[chosen] / green[chosen])
    log_blue = np.log(blue[chosen] / green[chosen])
    count = int(chosen.sum())

    def robust_spread(values: np.ndarray) -> float:
        """Estimate the standard deviation from the median deviation.

        Returns
        -------
        spread : `float`
            1.4826 times the median absolute deviation.
        """
        return float(robust_sigma(values))

    return ColourCalibration(
        red_gain=float(np.exp(-np.median(log_red))),
        blue_gain=float(np.exp(-np.median(log_blue))),
        star_count=count,
        red_uncertainty=robust_spread(log_red) / np.sqrt(count),
        blue_uncertainty=robust_spread(log_blue) / np.sqrt(count),
        sky_levels=(float(sky_levels[0]), float(sky_levels[1]), float(sky_levels[2])),
    )


def calibrate_colours(image: np.ndarray) -> tuple[np.ndarray, ColourCalibration | None]:
    """Neutralise the sky and balance the star colours of a colour image.

    Parameters
    ----------
    image : `numpy.ndarray`
        A linear colour image, channels first, with shape (3, height, width).

    Returns
    -------
    calibrated : `numpy.ndarray`
        The image with each channel's sky level removed, the red and blue
        gains applied, and the green channel's sky level added back to all
        three, as 32-bit floats. The image is returned unchanged if too few
        stars were found.
    calibration : `ColourCalibration` or `None`
        What was applied, or `None` if nothing was.
    """
    calibration = measure_gains(image)
    if calibration is None:
        return image.astype(np.float32), None
    sky = np.asarray(calibration.sky_levels)
    gains = np.array([calibration.red_gain, 1.0, calibration.blue_gain])
    calibrated = (image - sky[:, None, None]) * gains[:, None, None] + sky[1]
    return calibrated.astype(np.float32), calibration


def calibrate_colours_in_file(input_path: str, output_path: str) -> bool:
    """Calibrate the colours of a linear FITS image and save the result.

    Parameters
    ----------
    input_path : `str`
        Path of the linear colour image.
    output_path : `str`
        Path to write. The header of the input is kept.

    Returns
    -------
    succeeded : `bool`
        `True` if a calibrated copy was written. `False` if the input could
        not be read, is not a three-channel image, or held too few stars. The
        reason is logged.
    """
    try:
        data = read_data(input_path)
        header = read_header(input_path)
    except (OSError, ValueError, KeyError) as error:
        logger.warning("Could not read '%s' to calibrate its colours: %s.", input_path, error)
        return False
    if data is None or data.ndim != 3 or data.shape[0] != 3:
        logger.warning("Could not calibrate the colours of '%s': it is not a colour image.", input_path)
        return False
    calibrated, calibration = calibrate_colours(np.asarray(data, dtype=np.float64))
    if calibration is None:
        logger.warning("Too few usable stars to calibrate the colours of '%s'.", input_path)
        return False
    logger.info(
        "Colour calibration from %d stars: red x%.3f (+/-%.1f%%), blue x%.3f (+/-%.1f%%).",
        calibration.star_count,
        calibration.red_gain,
        100 * calibration.red_uncertainty,
        calibration.blue_gain,
        100 * calibration.blue_uncertainty,
    )
    write_image(output_path, calibrated, header)
    return True
