"""Checks a color stack's white balance against stars like the Sun.

Siril's photometric color calibration (PCC) scales the red and blue channels
of a color stack so that its stars match the colors the Gaia catalog gives
them. It is not always right. Siril itself logs "imprecise solution" when the
fit scatters, and on two Nikon stacks it left the stars like the Sun with
colors that depend strongly on how they are measured (see
`SUN_LIKE_APERTURE_RADIUS_PIXELS`). The star-based fallback
(`colour_calibration`) assumes the median star is neutral, which is the same
kind of guess. A picture in which sun-like stars are not white has a color
cast, however neutral its sky is.
A picture in which sun-like stars
are not white has a color cast, however neutral its sky is.

This module checks and corrects the balance after either calibration, on the
linear data, before the stretch:

1. *Stars.* The Gaia DR3 colors of the stars in the field come from
   `gaia_star_colors_in_footprint`. A star is used when its ``BP - RP``
   color lies in a narrow band around the Sun's (0.82), its G magnitude is
   in the range that is bright enough to measure and not bright enough to
   saturate, and it has no catalog neighbor closer than the isolation
   distance. If too few
   stars qualify, the band is widened once. If there are still too few, the
   check is skipped and says why.
2. *Measurement.* Each star's light is summed in a large circle and the sky is
   taken from a ring around it, in each channel. A small circle misses the
   wings of a star, and a star's wings are redder or bluer than its core
   when the lens or the atmosphere spreads colors. A large circle gives the
   whole star's color. The median of the stars' red-to-green and
   blue-to-green flux ratios is the balance of a sun-like star.
3. *Correction.* The sun-like star should be white, so the red and blue
   channels are divided by those ratios. Each channel's own sky level is
   measured on the empty sky with the library's sky measurement
   (`measure_sky_background`) and taken out first, and the green channel's sky
   level is put back in all three, so the three skies end up equal. A median
   of every pixel would not do for the sky level: in a frame with a nebula it
   lies above the empty sky. The equal skies matter because the stretch puts
   the black point within three noise widths of the sky: a difference of a
   tenth of a noise width between channels is a visible tint in the stretched
   sky, and a calibration leaves differences of that size. The ratios are
   measured again on the corrected image and both sets are recorded.
4. *Guards.* A correction larger than `SUN_LIKE_MAXIMUM_CORRECTION` is not
   applied: it means a wrong plate solution or a field where dust has
   reddened the stars more than the camera has, and a large change made on
   that evidence could do more harm than good. The result says so, so the
   preview's record and notices can flag it.

The stack itself is never changed. This works on the scratch copy that the
preview is made from.
"""

import logging
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from astropy.stats import SigmaClip
from astropy.table import Table
from astropy.wcs import WCS
from photutils.aperture import ApertureStats, CircularAnnulus, CircularAperture
from scipy.spatial import cKDTree

from astrometricslib._internal.drivers.fits_access import read_data, read_header, write_image
from astrometricslib.foundation.errors import ExternalServiceError
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
from astrometricslib.pipelines.shared.gaia_star_colors import gaia_star_colors_in_footprint
from astrometricslib.pipelines.shared.image_scaling import measure_sky_background
from astrometricslib.pipelines.stacking.post_processing.stack_image import robust_sigma

logger = logging.getLogger(__name__)

__all__ = [
    "SUN_LIKE_MAXIMUM_CORRECTION",
    "SunLikeBalance",
    "balance_sun_like_stars",
    "balance_sun_like_stars_in_file",
    "measure_sun_like_ratios",
    "select_sun_like_stars",
]

# The Gaia BP - RP colors that count as sun-like. The Sun's is about 0.82. The
# narrow band is the first choice; the wide band is used only when the narrow
# one holds fewer than the minimum number of stars. Near the Sun's color the
# measured red-to-green ratio changes by about 0.03 and the blue-to-green
# ratio by about 0.05 for each 0.1 of BP - RP (NGC 6992, STK-D86: R/G 1.110,
# 1.152, 1.197 and B/G 1.036, 0.975, 0.875 for stars at BP - RP 0.6 to 0.75,
# 0.75 to 0.9 and 0.9 to 1.1). The narrow band's half-width of 0.075 therefore
# spreads the blue ratio by about 0.04, and the wide band's 0.15 by 0.08, which
# the median of many stars averages down.
SUN_LIKE_COLOUR_RANGE = TunedParameter(
    name="stacking.sun_like_colour_range",
    default=(0.75, 0.90),
    unit="BP - RP (mag)",
    description="Gaia BP - RP colors of the stars that count as sun-like (the Sun is about 0.82).",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D86",
)

SUN_LIKE_WIDE_COLOUR_RANGE = TunedParameter(
    name="stacking.sun_like_wide_colour_range",
    default=(0.70, 1.00),
    unit="BP - RP (mag)",
    description="Wider band of sun-like colors, used when the narrow band holds too few stars.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D86",
)

# The Gaia G magnitudes of the stars that are measured. Stars brighter than
# 10.5 may saturate the camera, which changes the color of the core, and
# stars fainter than 13.5 give a noisy ratio for each star. A design estimate.
SUN_LIKE_MAGNITUDE_RANGE = TunedParameter(
    name="stacking.sun_like_magnitude_range",
    default=(10.5, 13.5),
    unit="G (mag)",
    description="Gaia G magnitudes of the sun-like stars that are measured.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D86",
)

# Fewer stars than this give a balance too uncertain to apply. The median of
# 15 ratios that scatter by 0.1 each is known to about 0.03.
SUN_LIKE_MINIMUM_STARS = TunedParameter(
    name="stacking.sun_like_minimum_stars",
    default=15,
    unit="stars",
    description="Fewest sun-like stars that give a white balance certain enough to apply.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D86",
)

# The largest change of the red or blue channel that is applied. Siril's PCC
# was off by 4 percent (NGC 7023) and 11 to 12 percent (NGC 6992) on the two
# stacks of STK-D86. A change of 20 percent is above both, and above what
# dust that reddens stars more than the camera does would explain in a field
# with a color excess below about 0.15.
SUN_LIKE_MAXIMUM_CORRECTION = TunedParameter(
    name="stacking.sun_like_maximum_correction",
    default=0.20,
    unit="fraction",
    description="Largest change of the red or blue channel the white-balance check applies.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D86",
)

# The radius of the circle that sums a star's light, in pixels: 10 pixels on
# the reference setup, 3.3 times the star width. The red wings of a Nikon
# star are wider than its green wings, so the measured red-to-green ratio of
# sun-like stars grows with the radius. On NGC 6992 it levels off from 6
# pixels (1.141 at 6, 1.151 at 10, 1.143 at 12). On NGC 7023 (300 mm) it was
# still rising at 12 pixels (0.968 at 5, 1.100 at 8, 1.136 at 10, 1.155 at
# 12, with steps that shrink), so a small circle would read the star's core,
# not its color. 10 pixels is within about 3 percent of the leveling-off
# value on both stacks and stays inside the sky ring (STK-D86).
SUN_LIKE_APERTURE_RADIUS_PIXELS = TunedParameter(
    name="stacking.sun_like_aperture_radius_pixels",
    default=factor_for(10.0, RunBasis.STAR_WIDTH_PX),
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=10.0,
    unit="× star width",
    description="Radius of the circle that sums a sun-like star's light.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D86",
)

# The inner and outer radius of the ring that measures the sky around a star,
# in pixels: 15 and 21 on the reference setup. The ring lies outside the
# star's wings and inside the isolation distance below.
SUN_LIKE_ANNULUS_RADII_PIXELS = TunedParameter(
    name="stacking.sun_like_annulus_radii_pixels",
    default=factor_for((15.0, 21.0), RunBasis.STAR_WIDTH_PX),
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=(15.0, 21.0),
    unit="× star width",
    description="Inner and outer radius of the ring that measures the sky around a sun-like star.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D86",
)

# A star is used only if no other catalog star (G up to 15) lies within this
# distance, in pixels: 24 on the reference setup, a little beyond the ring.
SUN_LIKE_ISOLATION_PIXELS = TunedParameter(
    name="stacking.sun_like_isolation_pixels",
    default=factor_for(24.0, RunBasis.STAR_WIDTH_PX),
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=24.0,
    unit="× star width",
    description="Distance within which a catalog neighbor keeps a star out of the white-balance check.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D86",
)

# How far past the image's edge the catalog question reaches, in degrees, so a
# star at the edge still has the neighbors that could touch its ring. The
# isolation distance is 24 pixels, about 0.02 degrees at 3 arcseconds a pixel.
_FIELD_MARGIN_DEGREES = 0.05


@dataclass(frozen=True)
class SunLikeBalance:
    """What the white-balance check found, and what it did.

    Attributes
    ----------
    applied : `bool`
        Whether the red and blue gains were applied to the image.
    reason : `str`
        A sentence that says why the check was applied or not.
    star_count : `int`
        How many sun-like stars were measured (0 if none were).
    colour_range : `tuple` [`float`, `float`] or `None`
        The ``BP - RP`` band the stars came from.
    aperture_radius_pixels : `float` or `None`
        The radius of the circle that summed each star's light.
    red_to_green_before, blue_to_green_before : `float` or `None`
        The median flux ratios of the sun-like stars before the correction.
        A white star reads 1.
    red_gain, blue_gain : `float` or `None`
        The factors that were (or, when not applied, would have been)
        applied to the red and blue channels.
    red_to_green_after, blue_to_green_after : `float` or `None`
        The same ratios measured again on the corrected image. `None` when
        nothing was applied.
    red_uncertainty, blue_uncertainty : `float` or `None`
        The relative uncertainty of each ratio: the star-to-star scatter
        divided by the square root of the star count.
    red_sky_shift, blue_sky_shift : `float` or `None`
        What was added to each channel's sky level to make it equal to the
        green channel's, in the image's own units. `None` when nothing was
        applied.
    """

    applied: bool
    reason: str
    star_count: int = 0
    colour_range: tuple[float, float] | None = None
    aperture_radius_pixels: float | None = None
    red_to_green_before: float | None = None
    blue_to_green_before: float | None = None
    red_gain: float | None = None
    blue_gain: float | None = None
    red_to_green_after: float | None = None
    blue_to_green_after: float | None = None
    red_uncertainty: float | None = None
    blue_uncertainty: float | None = None
    red_sky_shift: float | None = None
    blue_sky_shift: float | None = None

    def record(self) -> dict[str, Any]:
        """Give the numbers for the preview's settings record.

        Returns
        -------
        record : `dict` [`str`, `Any`]
            Every field, with the band as a list, ready to save as JSON.
        """
        return {
            "applied": self.applied,
            "reason": self.reason,
            "star_count": self.star_count,
            "colour_range": list(self.colour_range) if self.colour_range is not None else None,
            "aperture_radius_pixels": self.aperture_radius_pixels,
            "red_to_green_before": self.red_to_green_before,
            "blue_to_green_before": self.blue_to_green_before,
            "red_gain": self.red_gain,
            "blue_gain": self.blue_gain,
            "red_to_green_after": self.red_to_green_after,
            "blue_to_green_after": self.blue_to_green_after,
            "red_uncertainty": self.red_uncertainty,
            "blue_uncertainty": self.blue_uncertainty,
            "red_sky_shift": self.red_sky_shift,
            "blue_sky_shift": self.blue_sky_shift,
        }

    def step_sentence(self) -> str:
        """Say in one sentence what the check did, for the list of steps.

        Returns
        -------
        sentence : `str`
            For example ``"White balance check on 41 sun-like stars:
            red x0.912, blue x1.020"``, or the reason when it was not applied.
        """
        if not self.applied:
            return f"White balance check not applied ({self.reason})"
        return (
            f"White balance check on {self.star_count} sun-like stars: "
            f"red x{self.red_gain:.3f}, blue x{self.blue_gain:.3f}"
        )


def _not_applied(reason: str, **measured: Any) -> SunLikeBalance:
    """Build the result of a check that changed nothing.

    Parameters
    ----------
    reason : `str`
        Why nothing was changed.
    **measured : `Any`
        Any numbers that were measured before the check stopped.

    Returns
    -------
    result : `SunLikeBalance`
        A result with ``applied`` set to `False`.
    """
    return SunLikeBalance(applied=False, reason=reason, **measured)


def select_sun_like_stars(
    stars: Table,
    wcs: WCS,
    image_shape: tuple[int, int],
    colour_range: tuple[float, float],
) -> np.ndarray:
    """Pick the sun-like stars to measure, and give their pixel positions.

    A star is picked when its color is in `colour_range`, its G magnitude is
    in `SUN_LIKE_MAGNITUDE_RANGE`, no other star of the table lies within the
    isolation distance, and its ring lies inside the image.

    Parameters
    ----------
    stars : `astropy.table.Table`
        The field's Gaia stars, with the columns ``ra``, ``dec``, ``g`` and
        ``bp_rp`` (see `gaia_star_colors_in_footprint`).
    wcs : `astropy.wcs.WCS`
        The image's plate solution.
    image_shape : `tuple` [`int`, `int`]
        The image's height and width, in pixels.
    colour_range : `tuple` [`float`, `float`]
        The lowest and highest ``BP - RP`` to accept.

    Returns
    -------
    positions : `numpy.ndarray`
        An array of shape (n, 2) with the x and y pixel positions of the
        chosen stars. Empty when none qualify.
    """
    height, width = image_shape
    x, y = wcs.all_world2pix(np.asarray(stars["ra"], dtype=float), np.asarray(stars["dec"], dtype=float), 0)
    positions = np.column_stack([x, y])
    isolation = float(resolve(SUN_LIKE_ISOLATION_PIXELS).value)
    outer_radius = float(resolve(SUN_LIKE_ANNULUS_RADII_PIXELS).value[1])
    margin = outer_radius + 2.0
    nearest_neighbor = np.full(len(positions), np.inf)
    finite = np.isfinite(positions).all(axis=1)
    if finite.sum() >= 2:
        # Query two neighbors: the nearest is the star itself.
        distances, _ = cKDTree(positions[finite]).query(positions[finite], k=2)
        nearest_neighbor[finite] = distances[:, 1]
    g_low, g_high = resolve(SUN_LIKE_MAGNITUDE_RANGE).value
    bp_rp = np.asarray(stars["bp_rp"], dtype=float)
    g = np.asarray(stars["g"], dtype=float)
    chosen = (
        finite
        & np.isfinite(bp_rp)
        & (bp_rp >= colour_range[0])
        & (bp_rp <= colour_range[1])
        & (g >= g_low)
        & (g <= g_high)
        & (nearest_neighbor > isolation)
        & (positions[:, 0] > margin)
        & (positions[:, 0] < width - margin)
        & (positions[:, 1] > margin)
        & (positions[:, 1] < height - margin)
    )
    return positions[chosen]


def _star_fluxes(
    channel: np.ndarray, positions: np.ndarray, radius: float, ring: tuple[float, float]
) -> np.ndarray:
    """Measure each star's light in one channel, with the local sky removed.

    Parameters
    ----------
    channel : `numpy.ndarray`
        One channel, shape (height, width).
    positions : `numpy.ndarray`
        Star positions, shape (n, 2), as x and y.
    radius : `float`
        The radius of the circle that sums the star's light.
    ring : `tuple` [`float`, `float`]
        The inner and outer radius of the ring that measures the sky.

    Returns
    -------
    fluxes : `numpy.ndarray`
        For each star, the sum of the light in the circle minus the clipped
        median of the ring times the circle's area.
    """
    aperture = CircularAperture(positions, r=radius)
    sky_ring = CircularAnnulus(positions, r_in=ring[0], r_out=ring[1])
    sky = ApertureStats(channel, sky_ring, sigma_clip=SigmaClip(sigma=3.0, maxiters=5)).median
    total = ApertureStats(channel, aperture).sum
    return np.asarray(total) - np.asarray(sky) * aperture.area


def measure_sun_like_ratios(
    image: np.ndarray, positions: np.ndarray
) -> tuple[float, float, float, float, int] | None:
    """Measure the colors of the chosen stars on a color image.

    Parameters
    ----------
    image : `numpy.ndarray`
        A linear color image, channels first (red, green, blue), shape
        (3, height, width).
    positions : `numpy.ndarray`
        The stars' x and y pixel positions, shape (n, 2).

    Returns
    -------
    ratios : `tuple` or `None`
        ``(red_to_green, blue_to_green, red_uncertainty, blue_uncertainty,
        star_count)``: the medians of the stars' flux ratios, their relative
        uncertainties, and the number of stars with a positive flux in all
        three channels. `None` when no star has one.
    """
    radius = float(resolve(SUN_LIKE_APERTURE_RADIUS_PIXELS).value)
    ring = tuple(float(value) for value in resolve(SUN_LIKE_ANNULUS_RADII_PIXELS).value)
    red, green, blue = (
        _star_fluxes(image[index], positions, radius, (ring[0], ring[1])) for index in range(3)
    )
    usable = (red > 0) & (green > 0) & (blue > 0)
    count = int(usable.sum())
    if count == 0:
        return None
    log_red = np.log(red[usable] / green[usable])
    log_blue = np.log(blue[usable] / green[usable])
    return (
        float(np.exp(np.median(log_red))),
        float(np.exp(np.median(log_blue))),
        float(robust_sigma(log_red) / np.sqrt(count)),
        float(robust_sigma(log_blue) / np.sqrt(count)),
        count,
    )


def _empty_sky_level(channel: np.ndarray) -> float:
    """Measure the level of the empty sky in one channel.

    Parameters
    ----------
    channel : `numpy.ndarray`
        One channel, shape (height, width).

    Returns
    -------
    level : `float`
        The sky level from `measure_sky_background`, which ignores nebulae
        and galaxies, or the channel's median when the sky cannot be measured.
    """
    measured = measure_sky_background(channel)
    return measured[0] if measured is not None else float(np.median(channel))


def balance_sun_like_stars(
    image: np.ndarray, wcs: WCS, stars: Table | None
) -> tuple[np.ndarray | None, SunLikeBalance]:
    """Check the white balance of an image and correct it if it is off.

    Parameters
    ----------
    image : `numpy.ndarray`
        A linear color image, channels first, shape (3, height, width).
    wcs : `astropy.wcs.WCS`
        The image's plate solution.
    stars : `astropy.table.Table` or `None`
        The field's Gaia stars with their colors (see
        `gaia_star_colors_in_footprint`), or `None` when none are known.

    Returns
    -------
    corrected : `numpy.ndarray` or `None`
        The corrected image as 32-bit floats, or `None` when nothing was
        applied.
    result : `SunLikeBalance`
        What was found and done.
    """
    if stars is None or len(stars) == 0:
        return None, _not_applied("the field has no Gaia star colors")
    minimum_stars = int(resolve(SUN_LIKE_MINIMUM_STARS).value)
    positions = np.empty((0, 2))
    used_range = tuple(resolve(SUN_LIKE_COLOUR_RANGE).value)
    for band in (SUN_LIKE_COLOUR_RANGE, SUN_LIKE_WIDE_COLOUR_RANGE):
        used_range = tuple(float(value) for value in resolve(band).value)
        positions = select_sun_like_stars(stars, wcs, (image.shape[1], image.shape[2]), used_range)
        if len(positions) >= minimum_stars:
            break
    if len(positions) < minimum_stars:
        return None, _not_applied(
            f"only {len(positions)} isolated sun-like stars were found; at least {minimum_stars} are needed",
            star_count=len(positions),
            colour_range=used_range,
        )
    measured = measure_sun_like_ratios(image, positions)
    radius = float(resolve(SUN_LIKE_APERTURE_RADIUS_PIXELS).value)
    if measured is None or measured[4] < minimum_stars:
        found = 0 if measured is None else measured[4]
        return None, _not_applied(
            f"only {found} sun-like stars had light in all three channels; "
            f"at least {minimum_stars} are needed",
            star_count=found,
            colour_range=used_range,
            aperture_radius_pixels=radius,
        )
    red_to_green, blue_to_green, red_uncertainty, blue_uncertainty, count = measured
    red_gain, blue_gain = 1.0 / red_to_green, 1.0 / blue_to_green
    found_numbers = {
        "star_count": count,
        "colour_range": used_range,
        "aperture_radius_pixels": radius,
        "red_to_green_before": red_to_green,
        "blue_to_green_before": blue_to_green,
        "red_gain": red_gain,
        "blue_gain": blue_gain,
        "red_uncertainty": red_uncertainty,
        "blue_uncertainty": blue_uncertainty,
    }
    limit = float(resolve(SUN_LIKE_MAXIMUM_CORRECTION).value)
    largest = max(abs(red_gain - 1.0), abs(blue_gain - 1.0))
    if largest > limit:
        return None, _not_applied(
            f"the change it asks for (red x{red_gain:.3f}, blue x{blue_gain:.3f}) is more than "
            f"{100 * limit:.0f} percent, so it was not applied",
            **found_numbers,
        )
    corrected = np.array(image, dtype=np.float32)
    green_sky = _empty_sky_level(corrected[1])
    for index, gain in ((0, red_gain), (2, blue_gain)):
        # Take out the channel's own empty-sky level, scale the stars, and put
        # the green sky level back. The three skies are then equal, and the
        # sky is not scaled with the stars.
        sky = _empty_sky_level(corrected[index])
        found_numbers["red_sky_shift" if index == 0 else "blue_sky_shift"] = green_sky - sky
        corrected[index] = ((corrected[index] - sky) * gain + green_sky).astype(np.float32)
    after = measure_sun_like_ratios(corrected, positions)
    found_numbers["red_to_green_after"] = after[0] if after is not None else None
    found_numbers["blue_to_green_after"] = after[1] if after is not None else None
    reason = (
        f"the stars like the Sun read red {red_to_green:.3f} and blue {blue_to_green:.3f} of green; "
        "the red and blue channels were scaled to make them white"
    )
    return corrected, SunLikeBalance(applied=True, reason=reason, **found_numbers)


def _footprint_corners(wcs: WCS, image_shape: tuple[int, int]) -> list[tuple[float, float]]:
    """Find the sky positions of an image's four corners.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The image's plate solution.
    image_shape : `tuple` [`int`, `int`]
        The image's height and width, in pixels.

    Returns
    -------
    corners : `list` [`tuple` [`float`, `float`]]
        ``(ra, dec)`` in degrees, in order around the edge, at the outer
        edges of the corner pixels.
    """
    height, width = image_shape
    ra, dec = wcs.all_pix2world(
        [-0.5, width - 0.5, width - 0.5, -0.5], [-0.5, -0.5, height - 0.5, height - 0.5], 0
    )
    return [(float(a), float(b)) for a, b in zip(ra, dec, strict=True)]


def balance_sun_like_stars_in_file(
    input_path: str,
    output_path: str,
    load_stars: Callable[[list[tuple[float, float]], float], Table | None] | None = None,
) -> SunLikeBalance:
    """Check a color FITS image's white balance and save a corrected copy.

    Nothing is written unless the correction is applied. A catalog that
    cannot be reached, a missing plate solution and a file that cannot be
    read are not errors: the check is skipped and the result says why.

    Parameters
    ----------
    input_path : `str`
        Path of the linear color image, with a plate solution in its header.
    output_path : `str`
        Path to write when the correction is applied. The header of the input
        is kept.
    load_stars : `Callable`, optional
        Gives the Gaia stars under a footprint (the corners as ``(ra, dec)``
        in degrees, and the margin in degrees) as the table of
        `gaia_star_colors_in_footprint`. By default the stored answers are
        read and the Gaia archive is asked for what is missing.

    Returns
    -------
    result : `SunLikeBalance`
        What was found and done.
    """
    try:
        data = read_data(input_path)
        header = read_header(input_path)
    except (OSError, ValueError, KeyError) as error:
        return _not_applied(f"the image could not be read ({error})")
    if data is None or data.ndim != 3 or data.shape[0] != 3:
        return _not_applied("the image is not a color image")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wcs = WCS(header, naxis=2)
    if not wcs.has_celestial:
        return _not_applied("the image has no plate solution")
    try:
        corners = _footprint_corners(wcs, (data.shape[1], data.shape[2]))
        stars = (load_stars or gaia_star_colors_in_footprint)(corners, _FIELD_MARGIN_DEGREES)
    except ExternalServiceError as error:
        return _not_applied(f"the Gaia star colors are not available ({error})")
    except (ValueError, ConnectionError) as error:
        return _not_applied(f"the field's stars could not be found ({error})")
    corrected, result = balance_sun_like_stars(np.asarray(data), wcs, stars)
    if corrected is not None:
        write_image(output_path, corrected, header)
    logger.info("%s.", result.step_sentence())
    return result
