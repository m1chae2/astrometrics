"""Works out how faint a stack sees: point-source depth and surface limit.

Space missions state depth in magnitudes, so this module turns a stack's
measured empty-sky noise into two such numbers:

* **Point-source depth, 5 sigma.** The magnitude of a star whose light in a
  circular aperture (radius equal to the stack's star width, FWHM) is five
  times the noise of an empty aperture of that size.
* **Surface-brightness limit, 3 sigma, 10 arcsec by 10 arcsec.** The faintest
  surface brightness, in magnitudes per square arcsecond, that is three times
  the noise of an empty 10 by 10 arcsecond box (the convention of Roman et al.
  2020, used by the Euclid mission and by deep-imaging surveys).

Both need two things the noise measurement does not give by itself:

1. **The noise at another area.** The stack's empty-aperture noise is measured
   at four aperture sizes (`aperture_size_noise.py`). The noise at the wanted
   area is scaled from the measured size nearest in area, with the measured
   exponent: noise grows as (pixel count) to the power beta, with beta = 0.5
   for independent pixels and larger when neighboring pixels are correlated.
   Without a measured exponent, beta = 0.5 is used and the result says so; it
   is then optimistic (too deep), because correlated noise adds up faster.
   A 10 arcsec box is turned into the circle of the same area.
2. **The stack's zero point.** A magnitude needs the offset between the
   stack's counts and a magnitude scale. The restack runs without the
   photometry pipeline, so the stack gets its own zero point from the target's
   stored reference stars. Each star is measured on this stack in a large
   aperture (3 star widths, at least 8 px, so it holds nearly all the star's
   light), and matched to its Gaia DR3 G magnitude in the local Gaia cache.
   The zero point is the median over the stars of ``G + 2.5 log10(flux)``,
   with the flux in the same units as the noise (both are sums over pixels of
   this stack's image, so no scale needs to be undone).

The band is Gaia G (330 to 1050 nm), a broad band close to the luminance
filter's. The L filter and the sensor response are not G, so the offset
carries a color term that is not corrected, and the depth is a depth for stars
of the reference stars' typical color.

The photometry pipeline's zero points are not a fallback: they belong to single
frames, are in counts per second and use a fixed 4 px aperture, so they cannot
be carried over to a stack's units.

Every number here is a design estimate until a real stack has been checked
against an independent depth (for example an injected-star recovery).
"""

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from astrometricslib.pipelines.stacking.post_processing.stack_image import (
    APERTURE_RADIUS_PX,
    MAD_TO_SIGMA,
    MEDIAN_ERROR_FACTOR,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import StackNoiseResult

logger = logging.getLogger(__name__)

__all__ = [
    "MINIMUM_ZERO_POINT_STARS",
    "STAR_MATCH_RADIUS_ARCSEC",
    "DepthLimit",
    "DepthLimits",
    "GaiaLookup",
    "StackZeroPoint",
    "cached_gaia_lookup",
    "gaia_zero_point",
    "measure_depth_limits",
]

# The noise exponent of independent pixels: noise grows as the square root of
# the pixel count. Used when the exponent was not measured.
INDEPENDENT_PIXEL_EXPONENT = 0.5

# The detection threshold of the point-source depth, in sigma of the noise in
# the aperture. 5 is the usual statement of a point-source depth in NASA and
# ESA mission papers.
POINT_SOURCE_SIGMA = 5.0

# The detection threshold of the surface-brightness limit, in sigma of the
# noise in the box. 3 sigma over 10 arcsec boxes is the convention of Roman et
# al. (2020) and of Euclid's surface-brightness limit.
SURFACE_BRIGHTNESS_SIGMA = 3.0
SURFACE_BRIGHTNESS_BOX_ARCSEC = 10.0

# A stored reference star is matched to a Gaia star within this distance. It is
# about 1.5 pixels at 1.9 arcsec per pixel: wider than the position error of
# the stack's solution after it is moved onto the stars (under 1 px, see
# `stack_noise.MAXIMUM_ROTATION_DEG`) and than a Gaia star moves in the 10
# years since 2016 at the usual 100 mas/yr, and small enough that a wrong star
# is rarely inside it. A design estimate.
STAR_MATCH_RADIUS_ARCSEC = 3.0

# A reference star is left out when another Gaia star inside its large
# measuring aperture is brighter than the star's magnitude plus this. A
# companion 3 mag fainter adds 6% to the aperture's light, which is 0.06 mag
# of zero-point error for one star and much less for the median. A design
# estimate.
CONTAMINATION_MAGNITUDE_MARGIN = 3.0

# At least this many matched stars are needed for a median and its error. Three
# is the fewest for which the scatter says anything; the 10 stored stars are
# the brightest isolated ones, so most are expected to match. A design
# estimate.
MINIMUM_ZERO_POINT_STARS = 3

# Why a stack has no zero point when its aperture set's sky positions did not
# come from a plate solve of the stack that chose it (such positions can be
# tens of arcseconds off the true ones; a copy of the library, 2026-10-27).
UNTRUSTED_SKY_POSITIONS_REASON = (
    "the aperture set's sky positions do not come from a plate solve of the stack"
)

# When fewer than MINIMUM_ZERO_POINT_STARS stars match, the median distance to
# the nearest Gaia star is compared with this many match radii. A set with
# true positions has stars within about 1 arcsec (M 13: 0.1 to 0.9 arcsec); a
# set with wrong positions has them 11 to 50 arcsec off. Three radii is
# between the two (a design estimate, to be checked on the next real run).
POSITION_MISFIT_RADIUS_FACTOR = 3.0

# A Gaia lookup takes a star's right ascension and declination (degrees) and a
# radius (degrees) and gives the rows ``(right ascension, declination, G)`` of
# the Gaia stars inside it, or `None` when no Gaia data are at hand. It must
# not use the network.
GaiaLookup = Callable[[float, float, float], "Sequence[tuple[float, float, float]] | None"]


@dataclass(frozen=True)
class StackZeroPoint:
    """The offset between a stack's counts and Gaia G magnitudes.

    Parameters
    ----------
    zero_point_mag : `float`
        Median over the matched stars of ``G + 2.5 log10(flux)``. A source of
        flux F in the stack's units then has magnitude ``zero_point_mag -
        2.5 log10(F)``.
    uncertainty_mag : `float`
        Its 1-sigma uncertainty: 1.2533 times 1.4826 times the median absolute
        deviation of the stars' offsets, divided by the square root of the
        number of stars (the standard error of a median).
    star_count : `int`
        How many stars gave an offset.
    note : `str`
        How it was found, in one sentence.
    """

    zero_point_mag: float
    uncertainty_mag: float
    star_count: int
    note: str


@dataclass(frozen=True)
class DepthLimit:
    """One depth, or the reason there is none.

    Parameters
    ----------
    value_mag : `float` or `None`
        The depth in magnitudes (per square arcsecond for the surface
        brightness limit), or `None` when it could not be worked out.
    uncertainty_mag : `float` or `None`
        Its 1-sigma uncertainty, in the same unit.
    sample_count : `int` or `None`
        The stars behind the zero point.
    assumed : `bool`
        True when the noise exponent was not measured and 0.5 was used.
    note : `str`
        How it was found (when there is a value) or why there is none.
    """

    value_mag: float | None
    uncertainty_mag: float | None
    sample_count: int | None
    assumed: bool
    note: str


@dataclass(frozen=True)
class DepthLimits:
    """The two depths of one stack.

    Parameters
    ----------
    point_source : `DepthLimit`
        The 5 sigma point-source depth in an aperture of radius one star width.
    surface_brightness : `DepthLimit`
        The 3 sigma surface-brightness limit over 10 by 10 arcsecond boxes.
    """

    point_source: DepthLimit
    surface_brightness: DepthLimit


def cached_gaia_lookup(config: object) -> GaiaLookup:
    """Make a Gaia lookup that reads only the local Gaia cache.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings, which say where the cache is.

    Returns
    -------
    lookup : `GaiaLookup`
        Gives the cached Gaia stars inside a small circle; `None` when the
        cache cannot be read. It never downloads anything.
    """
    from astrometricslib.drivers.catalog_store import query_gaia_sources_in_bounds

    def lookup(
        right_ascension: float, declination: float, radius_deg: float
    ) -> list[tuple[float, float, float]] | None:
        """Give the cached Gaia stars inside a circle around a position.

        Parameters
        ----------
        right_ascension, declination : `float`
            The center, in degrees.
        radius_deg : `float`
            The radius, in degrees.

        Returns
        -------
        rows : `list` or `None`
            ``(right ascension, declination, G)`` of each star, or `None` if
            the cache could not be read.
        """
        half_width = radius_deg / max(math.cos(math.radians(declination)), 0.05)
        try:
            found = query_gaia_sources_in_bounds(
                config,
                right_ascension - half_width,
                right_ascension + half_width,
                declination - radius_deg,
                declination + radius_deg,
            )
        except Exception:  # an unreadable cache only means no zero point
            logger.exception("The Gaia cache could not be read for the stack's zero point")
            return None
        return [(row[1], row[2], row[3]) for row in found if row[3] is not None]

    return lookup


def gaia_zero_point(
    star_fluxes: Sequence[tuple[float, float, float]],
    gaia_lookup: GaiaLookup | None,
    reference_radius_px: float,
    pixel_scale_arcsec: float,
) -> tuple[StackZeroPoint | None, str]:
    """Find a stack's zero point from its reference stars and Gaia G.

    Parameters
    ----------
    star_fluxes : `Sequence` [`tuple` [`float`, `float`, `float`]]
        For each reference star: right ascension and declination in degrees
        and its flux in the large aperture of this stack, in the stack's units.
    gaia_lookup : `GaiaLookup` or `None`
        Gives the Gaia stars near a position without using the network. With
        `None` there is no Gaia data and no zero point.
    reference_radius_px : `float`
        The radius of the large measuring aperture, in pixels. A Gaia star
        inside it that is bright enough pollutes the flux, so the star is left
        out.
    pixel_scale_arcsec : `float`
        The pixel scale, in arcseconds per pixel.

    Returns
    -------
    zero_point : `StackZeroPoint` or `None`
        The zero point, or `None` when too few stars matched.
    reason : `str`
        Why there is none. Empty when there is one.
    """
    if gaia_lookup is None:
        return None, "no Gaia star data were available to match the reference stars"
    if not star_fluxes:
        return None, "the stack has no measured reference stars"
    aperture_arcsec = reference_radius_px * pixel_scale_arcsec
    search_deg = (aperture_arcsec + STAR_MATCH_RADIUS_ARCSEC) / 3600.0
    offsets = []
    nearest_separations = []
    for right_ascension, declination, flux in star_fluxes:
        if flux <= 0:
            continue
        rows = gaia_lookup(right_ascension, declination, search_deg)
        if not rows:
            continue
        table = np.asarray(rows, dtype=float)
        table = table[np.isfinite(table[:, 2])]
        if table.size == 0:
            continue
        cosine = math.cos(math.radians(declination))
        distance_arcsec = 3600.0 * np.hypot(
            (table[:, 0] - right_ascension) * cosine, table[:, 1] - declination
        )
        nearest = int(np.argmin(distance_arcsec))
        nearest_separations.append(float(distance_arcsec[nearest]))
        if distance_arcsec[nearest] > STAR_MATCH_RADIUS_ARCSEC:
            continue
        star_magnitude = float(table[nearest, 2])
        others = np.arange(len(table)) != nearest
        crowded = np.any(
            others
            & (distance_arcsec <= aperture_arcsec)
            & (table[:, 2] < star_magnitude + CONTAMINATION_MAGNITUDE_MARGIN)
        )
        if crowded:
            continue
        offsets.append(star_magnitude + 2.5 * math.log10(flux))
    if len(offsets) < MINIMUM_ZERO_POINT_STARS:
        median_separation = float(np.median(nearest_separations)) if nearest_separations else 0.0
        if median_separation > POSITION_MISFIT_RADIUS_FACTOR * STAR_MATCH_RADIUS_ARCSEC:
            return None, (
                "the stored reference-star positions do not land on Gaia stars "
                f"(median nearest separation {median_separation:.0f} arcsec)"
            )
        return None, (
            f"only {len(offsets)} of {len(star_fluxes)} reference stars matched an uncrowded Gaia G "
            f"star in the local cache; {MINIMUM_ZERO_POINT_STARS} are needed"
        )
    values = np.asarray(offsets)
    zero_point = float(np.median(values))
    error = (
        MEDIAN_ERROR_FACTOR
        * MAD_TO_SIGMA
        * float(np.median(np.abs(values - zero_point)))
        / math.sqrt(values.size)
    )
    note = (
        f"Zero point {zero_point:.3f} Gaia G mag from {values.size} of {len(star_fluxes)} reference stars "
        f"measured in a {reference_radius_px:.1f} px aperture of this stack (the aperture holds nearly all "
        "of a star's light; the little beyond it, a few percent at most, makes the depth up to 0.03 mag "
        "shallower, a design estimate)."
    )
    return StackZeroPoint(zero_point, error, int(values.size), note), ""


def _noise_at_area(result: StackNoiseResult, area_px: float) -> tuple[float, float, bool, str]:
    """Scale the measured noise to an aperture of another area.

    Parameters
    ----------
    result : `StackNoiseResult`
        The stack's noise, with the noise at several aperture sizes and the
        exponent when they were measured.
    area_px : `float`
        The wanted area, in pixels.

    Returns
    -------
    sigma : `float`
        The noise of the sum over that area, in the stack's units.
    relative_error : `float`
        Its 1-sigma uncertainty as a fraction of itself: the noise's own
        error and the exponent's error acting over the distance in area.
    assumed : `bool`
        True when the exponent was not measured and 0.5 was used.
    note : `str`
        How the scaling was done, including whether it reaches beyond the
        measured areas.
    """
    relative_error = result.noise_uncertainty_adu / result.noise_adu
    outcome = result.size_noise
    measured = outcome.result if outcome is not None else None
    if measured is None or not math.isfinite(measured.exponent):
        base_area = math.pi * APERTURE_RADIUS_PX**2
        base_noise = result.noise_adu
        exponent = INDEPENDENT_PIXEL_EXPONENT
        exponent_error = 0.0
        assumed = True
        extrapolated = False
        basis = (
            f"The noise at {area_px:.1f} px comes from the {APERTURE_RADIUS_PX:g} px aperture's noise "
            "with an exponent of 0.5: assumes independent pixels (a lower noise, so a deeper, optimistic "
            "limit)"
        )
        if outcome is not None and outcome.reason:
            basis += f"; the exponent was not measured ({outcome.reason})"
        basis += "."
    else:
        areas = np.asarray(measured.pixel_counts, dtype=float)
        nearest = int(np.argmin(np.abs(np.log(areas / area_px))))
        base_area = float(areas[nearest])
        base_noise = float(measured.noises[nearest])
        exponent = measured.exponent
        exponent_error = measured.exponent_error if math.isfinite(measured.exponent_error) else 0.0
        assumed = False
        extrapolated = area_px < areas.min() or area_px > areas.max()
        basis = (
            f"The noise at {area_px:.1f} px is scaled from the measured {measured.radii_px[nearest]:g} px "
            f"radius ({base_area:.0f} px) with the measured exponent {exponent:.2f}"
            + (
                ", an extrapolation beyond the measured apertures."
                if extrapolated
                else ", between the measured apertures."
            )
        )
    distance = math.log(area_px / base_area)
    sigma = base_noise * math.exp(exponent * distance)
    relative_error = math.hypot(relative_error, distance * exponent_error)
    return sigma, relative_error, assumed, basis


def _not_measured(reason: str) -> DepthLimit:
    """Make a depth that has no value.

    Parameters
    ----------
    reason : `str`
        Why there is none.

    Returns
    -------
    limit : `DepthLimit`
        A limit with no value and that reason.
    """
    return DepthLimit(None, None, None, False, reason)


def measure_depth_limits(
    result: StackNoiseResult | None,
    stacked_fwhm_px: float | None,
    gaia_lookup: GaiaLookup | None,
    reason: str = "",
) -> DepthLimits:
    """Work out the two depths of a stack.

    Parameters
    ----------
    result : `StackNoiseResult` or `None`
        The stack's noise measurement, or `None` when there is none.
    stacked_fwhm_px : `float` or `None`
        The star width (FWHM) of the stack, in pixels. It sets the radius of
        the point-source aperture.
    gaia_lookup : `GaiaLookup` or `None`
        Gives Gaia G magnitudes near a position, from the cache only.
    reason : `str`, optional
        Why there is no noise result, when `result` is `None`.

    Returns
    -------
    limits : `DepthLimits`
        Both depths, each with its uncertainty and a note, or with the reason
        it could not be worked out.
    """
    if result is None:
        missing = _not_measured(reason or "the stack's noise was not measured")
        return DepthLimits(missing, missing)
    if not (result.noise_adu > 0 and math.isfinite(result.noise_adu)):
        missing = _not_measured("the stack's noise is not a usable number")
        return DepthLimits(missing, missing)
    zero_point, zero_point_reason = (
        (None, UNTRUSTED_SKY_POSITIONS_REASON)
        if not result.sky_positions_from_plate_solve
        else gaia_zero_point(
            result.reference_star_fluxes,
            gaia_lookup,
            result.reference_aperture_radius_px,
            result.pixel_scale_arcsec,
        )
        if result.pixel_scale_arcsec > 0
        else (None, "the stack's pixel scale is not known")
    )
    if zero_point is None:
        missing = _not_measured(f"no zero point for this stack: {zero_point_reason}")
        return DepthLimits(missing, missing)

    def limit(area_px: float, sigma_count: float, per_area_arcsec2: float | None, label: str) -> DepthLimit:
        """Turn the noise at one area into a depth in magnitudes.

        Parameters
        ----------
        area_px : `float`
            The area of the aperture or box, in pixels.
        sigma_count : `float`
            How many sigma of the noise the detection needs.
        per_area_arcsec2 : `float` or `None`
            The area in square arcseconds to divide the flux by, or `None` for
            a flux in the aperture.
        label : `str`
            What the limit is, for the note.

        Returns
        -------
        depth : `DepthLimit`
            The depth with its uncertainty and note.
        """
        sigma, relative_error, assumed, basis = _noise_at_area(result, area_px)
        flux = sigma_count * sigma
        if per_area_arcsec2 is not None:
            flux /= per_area_arcsec2
        value = zero_point.zero_point_mag - 2.5 * math.log10(flux)
        uncertainty = math.hypot(zero_point.uncertainty_mag, 2.5 / math.log(10.0) * relative_error)
        return DepthLimit(
            value,
            uncertainty,
            zero_point.star_count,
            assumed,
            f"{label} {basis} {zero_point.note}",
        )

    point_source = _not_measured("the stack's star width (FWHM) is not known")
    if stacked_fwhm_px is not None and math.isfinite(stacked_fwhm_px) and stacked_fwhm_px > 0:
        point_source = limit(
            math.pi * stacked_fwhm_px**2,
            POINT_SOURCE_SIGMA,
            None,
            (
                f"5 sigma in a circular aperture of radius {stacked_fwhm_px:.2f} px (one star width); "
                "the aperture holds about 94% of a Gaussian star's light, which is not corrected "
                "(0.07 mag)."
            ),
        )
    box_pixels = (SURFACE_BRIGHTNESS_BOX_ARCSEC / result.pixel_scale_arcsec) ** 2
    surface = limit(
        box_pixels,
        SURFACE_BRIGHTNESS_SIGMA,
        SURFACE_BRIGHTNESS_BOX_ARCSEC**2,
        (
            f"3 sigma over a {SURFACE_BRIGHTNESS_BOX_ARCSEC:g} by {SURFACE_BRIGHTNESS_BOX_ARCSEC:g} arcsec "
            f"box ({box_pixels:.1f} px at {result.pixel_scale_arcsec:.2f} arcsec per pixel, treated as the "
            "circle of the same area)."
        ),
    )
    return DepthLimits(point_source, surface)
