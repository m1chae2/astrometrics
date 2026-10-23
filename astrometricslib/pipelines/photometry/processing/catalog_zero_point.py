"""Find each frame's catalog zero point, and how much it wanders in a night.

The zero point ties the camera's counts to the catalog's brightness scale.
A star of catalog magnitude ``m`` that gives ``f`` ADU per second in a frame
has the instrumental magnitude ``-2.5 log10(f)``. The offset
``m + 2.5 log10(f)`` is the same for every star of a frame if the frame is
clean and the star's catalog magnitude matches the camera's band. The frame's
zero point is the median of those offsets over its catalog stars. Thin cloud
makes the flux of every star drop together, so the zero point of that frame
falls. The spread of the zero points over a night shows how steady the sky
and the camera were.

This module measures. It does not say whether a night was photometric. That
judgment belongs to the observatory side (``wayfindinglib``).

What the zero point is, in this library:

* **One catalog band per session.** A star's catalog magnitude is either a
  SIMBAD V magnitude or a Gaia G magnitude, and the star record says which
  catalog it came from (``catalog_match_quality.matched_via``). The two bands
  are different filters, so mixing them would add a scatter that is not the
  sky's. `choose_catalog_band` picks the band that has more stars in the
  central region, with Gaia G winning a tie, and every frame of the session
  uses that band alone. A star with no recorded source is left out.
* **No airmass correction.** The zero point is the raw offset. Correcting for
  the thickness of the air needs an extinction coefficient, and finding one
  needs several photometric nights, which this observatory has not had. The
  frame's airmass is stored next to the zero point so a reader can see how
  much of a trend it could explain.
* **A color slope, reported but not applied.** The L filter and the sensor
  do not match the catalog bands, so a star's offset can depend on its color.
  When enough V-band stars have a B-V color (`MINIMUM_STARS_FOR_COLOUR_SLOPE`
  spread over `MINIMUM_COLOUR_RANGE_MAG`), `estimate_frame_zero_point` also
  fits the slope of offset against B-V. The stored zero point stays the plain
  median, so it means the same thing in every frame. Gaia G stars carry no
  color in this library, so their frames have no slope.
* **A central region only.** Photometry measures frames that are not
  flat-fielded, so vignetting dims stars near the corners. Only stars within
  `CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION` of the half-diagonal from the frame
  center are used.

Every number here is exploratory. The cuts below are design estimates, and
the register entry for the zero point says what the real-data check showed.
"""

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import numpy as np
from scipy.stats import theilslopes

from astrometricslib.models.stellar_source import BRIGHTEST_CATALOG_MAGNITUDE, StellarObject
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import UNKNOWN_AIRMASS_DEFAULT
from astrometricslib.pipelines.shared.quality.frame_measurement import (
    BOOTSTRAP_RESAMPLE_COUNT,
    BOOTSTRAP_SEED,
    bootstrap_median_error,
)

logger = logging.getLogger(__name__)

# A star must be within this fraction of the frame's half-diagonal from the
# frame center. Photometry does not flat-field its frames, so a star near a
# corner is dimmed by vignetting that the zero point would then absorb. The
# Apertura 75Q at f/5.4 is a short refractor with a flattener; its vignetting
# at the corner has not been measured on these frames, so 0.5 is a design
# estimate. It keeps the middle quarter of the area, about 34 arcminutes
# around the center of the 3008 px ASI 533MM field. The real-data check in the
# register (PHO-15) compares the offsets of the inner and outer stars of one
# frame.
CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION = 0.5

# The fewest stars a frame needs for a median and a bootstrap error worth
# storing. Ten is twice the five that the Moffat width bootstrap in
# `frame_measurement` needs, because the stars here also scatter from the
# catalog magnitudes' own errors (a few hundredths of a magnitude for SIMBAD,
# a few thousandths for Gaia G). A design estimate.
MINIMUM_STARS_FOR_ZERO_POINT = 10

# The fewest frames a night needs for a spread worth storing. Ten frames give
# a spread whose own error is about a quarter of its value (the standard error
# of a robust sigma is near 1.2 sigma / sqrt(n)). A design estimate.
MINIMUM_FRAMES_FOR_SCATTER = 10

# A star is used only when its flux is at least this many times its own
# 1-sigma error. A faint star's aperture flux is clamped at zero when the sky
# estimate is too high, which pushes its offset up; a signal-to-noise of 20
# (a 5 percent error, or 0.05 mag) keeps that clamp out of the sample. A
# design estimate.
MINIMUM_SIGNAL_TO_NOISE = 20.0

# The color slope needs this many stars with a B-V color, after the cuts
# above, and a spread of colors at least this wide (95th minus 5th percentile,
# in magnitudes). Without a spread the slope is a ratio of two noise terms.
# The photometry fields hold B-V from about 0.0 to 1.6 for G, K and M stars
# (the SIMBAD fields M 57, M 27 and M 13 hold 220, 363 and 296 such stars).
# A design estimate.
MINIMUM_STARS_FOR_COLOUR_SLOPE = 20
MINIMUM_COLOUR_RANGE_MAG = 0.4

# A median absolute deviation times this number estimates the standard
# deviation of normally distributed data (the same constant `mad_std` uses).
MAD_TO_SIGMA = 1.4826


class CatalogBand(StrEnum):
    """The catalog magnitude a zero point is tied to."""

    GAIA_G = "gaia_g"
    SIMBAD_V = "simbad_v"


_BAND_BY_MATCH_SOURCE: dict[str, CatalogBand] = {
    "gaia": CatalogBand.GAIA_G,
    "simbad": CatalogBand.SIMBAD_V,
}


@dataclass(frozen=True)
class FrameZeroPointEstimate:
    """The zero point of one frame, or the reason there is none.

    Parameters
    ----------
    zero_point_mag : `float` or `None`
        The median offset ``catalog magnitude + 2.5 log10(flux in ADU per
        second)``, in magnitudes. `None` when the frame had too few usable
        stars.
    error_mag : `float` or `None`
        The bootstrap standard error of the median, in magnitudes.
    star_count : `int`
        The stars that passed the cuts.
    reason : `str`
        Why `zero_point_mag` is `None`. Empty when it has a value.
    color_slope : `float` or `None`
        The Theil-Sen slope of the offset against B-V, in magnitudes per
        magnitude of color. `None` when it was not fitted.
    color_reason : `str`
        Why `color_slope` is `None`. Empty when it has a value.
    """

    zero_point_mag: float | None
    error_mag: float | None
    star_count: int
    reason: str
    color_slope: float | None
    color_reason: str


@dataclass(frozen=True)
class FrameZeroPoint:
    """The zero point of one frame, with what identifies the frame.

    Parameters
    ----------
    frame_path : `str`
        The frame's path.
    timestamp : `datetime.datetime`
        The exposure start in UTC with no zone (the same as the light
        curves').
    airmass : `float` or `None`
        The airmass the pipeline used for the frame. `None` when the header
        gave none (the pipeline then used a stand-in of 1.0, which is not
        stored as a measurement).
    estimate : `FrameZeroPointEstimate`
        The zero point and color slope.
    """

    frame_path: str
    timestamp: datetime
    airmass: float | None
    estimate: FrameZeroPointEstimate


@dataclass(frozen=True)
class SessionZeroPoints:
    """The zero points of every frame of one session.

    Parameters
    ----------
    session_id : `str`
        The session's id.
    band : `CatalogBand` or `None`
        The catalog band every frame used. `None` when no band could be
        chosen.
    band_reason : `str`
        Why there is no band, or an empty string.
    frames : `tuple` [`FrameZeroPoint`]
        One record per frame the pipeline measured, in time order.
    """

    session_id: str
    band: CatalogBand | None
    band_reason: str
    frames: tuple[FrameZeroPoint, ...]


@dataclass(frozen=True)
class ZeroPointScatter:
    """The spread of a night's frame zero points, or why there is none.

    Parameters
    ----------
    value_mag : `float` or `None`
        The MAD-sigma of the frame zero points, in magnitudes.
    error_mag : `float` or `None`
        The bootstrap standard error of that spread, in magnitudes.
    frame_count : `int`
        The frames with a zero point.
    reason : `str`
        Why `value_mag` is `None`. Empty when it has a value.
    """

    value_mag: float | None
    error_mag: float | None
    frame_count: int
    reason: str


@dataclass(frozen=True)
class ZeroPointStarSet:
    """The stars a session's zero points are made from.

    Parameters
    ----------
    band : `CatalogBand` or `None`
        The chosen band.
    reason : `str`
        Why no band was chosen, or an empty string.
    star_indices : `tuple` [`int`]
        Positions in the star list of the stars that belong to the set.
    catalog_magnitudes : `numpy.ndarray`
        Their catalog magnitudes.
    colors : `numpy.ndarray`
        Their B-V colors, with NaN where the catalog gave none.
    """

    band: CatalogBand | None
    reason: str
    star_indices: tuple[int, ...]
    catalog_magnitudes: np.ndarray
    colors: np.ndarray


def _optional_float(value: Any) -> float | None:
    """Turn a stored number into a finite float, or `None`.

    Parameters
    ----------
    value : `Any`
        A value that may be a number, a NumPy scalar or `None`.

    Returns
    -------
    number : `float` or `None`
        The value as a float when it is finite, else `None`.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) else None


def catalog_band_of(star: StellarObject) -> CatalogBand | None:
    """Say which catalog band a star's magnitude comes from.

    Parameters
    ----------
    star : `StellarObject`
        A star with its catalog match record.

    Returns
    -------
    band : `CatalogBand` or `None`
        Gaia G for a Gaia match and V for a SIMBAD match. `None` when the
        star has no match record or an unknown source, because its magnitude
        cannot then be tied to a band.
    """
    quality = star.catalog_match_quality
    if quality is None or quality.matched_via is None:
        return None
    return _BAND_BY_MATCH_SOURCE.get(quality.matched_via)


def _star_position_px(star: StellarObject) -> tuple[float, float] | None:
    """Read a star's position on the reference frame.

    Parameters
    ----------
    star : `StellarObject`
        A star whose ``star_data`` holds its detection.

    Returns
    -------
    position : `tuple` [`float`, `float`] or `None`
        ``(x, y)`` in pixels, or `None` when the star has no position.
    """
    data = star.star_data if isinstance(star.star_data, dict) else {}
    x_px = _optional_float(data.get("xcentroid", data.get("x_centroid")))
    y_px = _optional_float(data.get("ycentroid", data.get("y_centroid")))
    if x_px is None or y_px is None:
        return None
    return x_px, y_px


def choose_catalog_band(stars: Sequence[StellarObject], frame_shape: tuple[int, int]) -> ZeroPointStarSet:
    """Pick the catalog band of a session and the stars that use it.

    A star can be used when it has a catalog magnitude, a recorded catalog
    source, a position inside the central region and a match that was not
    flagged ambiguous (two catalog entries within the companion radius, so
    the magnitude may belong to a neighbor). The band with more such stars
    wins, and Gaia G wins a tie.

    Parameters
    ----------
    stars : `Sequence` [`StellarObject`]
        The session's stars.
    frame_shape : `tuple` [`int`, `int`]
        ``(height, width)`` of the reference frame, in pixels.

    Returns
    -------
    star_set : `ZeroPointStarSet`
        The band and its stars, or no band with the reason.
    """
    height, width = frame_shape
    center_x, center_y = (width - 1) / 2.0, (height - 1) / 2.0
    radius_px = CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION * math.hypot(width, height) / 2.0
    members: dict[CatalogBand, list[int]] = {band: [] for band in CatalogBand}
    with_magnitude = with_band = unambiguous = 0
    for index, star in enumerate(stars):
        magnitude = _optional_float(star.magnitude)
        if magnitude is None or magnitude <= BRIGHTEST_CATALOG_MAGNITUDE or star.photometry is None:
            continue
        with_magnitude += 1
        band = catalog_band_of(star)
        if band is None:
            continue
        with_band += 1
        if star.catalog_match_quality is not None and star.catalog_match_quality.is_ambiguous:
            continue
        unambiguous += 1
        position = _star_position_px(star)
        if position is None or math.hypot(position[0] - center_x, position[1] - center_y) > radius_px:
            continue
        members[band].append(index)
    chosen = max(CatalogBand, key=lambda band: (len(members[band]), band is CatalogBand.GAIA_G))
    if not members[chosen]:
        reason = (
            f"no star is usable for a zero point: {with_magnitude} have a catalog magnitude, "
            f"{with_band} of them a recorded catalog source, {unambiguous} of those an unambiguous match, "
            f"and none of those lies in the central region ({radius_px:.0f} px from the center)"
        )
        return ZeroPointStarSet(None, reason, (), np.empty(0), np.empty(0))
    indices = tuple(members[chosen])
    magnitudes = np.array([_optional_float(stars[index].magnitude) for index in indices], dtype=float)
    colors = np.array(
        [
            value if (value := _optional_float(stars[index].b_minus_v)) is not None else math.nan
            for index in indices
        ],
        dtype=float,
    )
    return ZeroPointStarSet(chosen, "", indices, magnitudes, colors)


def estimate_frame_zero_point(
    catalog_magnitudes: np.ndarray,
    flux_rates: np.ndarray,
    flux_errors: np.ndarray,
    is_saturated: np.ndarray,
    colors: np.ndarray,
    band: CatalogBand,
) -> FrameZeroPointEstimate:
    """Find the zero point of one frame from its catalog stars.

    A star is used when its flux is positive, it is not saturated, its error
    is positive and its flux is at least `MINIMUM_SIGNAL_TO_NOISE` times that
    error. The zero point is the median of ``m + 2.5 log10(flux)`` over the
    used stars, with a seeded bootstrap error.

    Parameters
    ----------
    catalog_magnitudes : `numpy.ndarray`
        Catalog magnitude of each star in the session's star set.
    flux_rates : `numpy.ndarray`
        The star's flux in this frame, in ADU per second (NaN if missing).
    flux_errors : `numpy.ndarray`
        The 1-sigma error of each flux, in ADU per second.
    is_saturated : `numpy.ndarray`
        `True` where the star's aperture held saturated pixels.
    colors : `numpy.ndarray`
        B-V color of each star, NaN where unknown.
    band : `CatalogBand`
        The session's band (decides whether a color slope is possible).

    Returns
    -------
    estimate : `FrameZeroPointEstimate`
        The zero point and color slope, each with the reason it is missing.
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        usable = (
            np.isfinite(flux_rates)
            & (flux_rates > 0.0)
            & ~is_saturated
            & np.isfinite(flux_errors)
            & (flux_errors > 0.0)
            & (flux_rates >= MINIMUM_SIGNAL_TO_NOISE * flux_errors)
        )
    star_count = int(usable.sum())
    if star_count < MINIMUM_STARS_FOR_ZERO_POINT:
        reason = (
            f"only {star_count} of {catalog_magnitudes.size} central catalog stars passed the cuts "
            f"(positive flux, not saturated, signal-to-noise of at least {MINIMUM_SIGNAL_TO_NOISE:g}); "
            f"at least {MINIMUM_STARS_FOR_ZERO_POINT} are needed"
        )
        return FrameZeroPointEstimate(None, None, star_count, reason, None, reason)
    offsets = catalog_magnitudes[usable] + 2.5 * np.log10(flux_rates[usable])
    generator = np.random.default_rng(BOOTSTRAP_SEED)
    slope, slope_reason = _color_slope(offsets, colors[usable], band)
    return FrameZeroPointEstimate(
        float(np.median(offsets)),
        bootstrap_median_error(offsets, generator),
        star_count,
        "",
        slope,
        slope_reason,
    )


def _color_slope(offsets: np.ndarray, colors: np.ndarray, band: CatalogBand) -> tuple[float | None, str]:
    """Fit the slope of the offsets against the B-V color.

    Parameters
    ----------
    offsets : `numpy.ndarray`
        Offset of each used star, in magnitudes.
    colors : `numpy.ndarray`
        B-V color of each used star, NaN where unknown.
    band : `CatalogBand`
        The session's band.

    Returns
    -------
    slope : `float` or `None`
        The Theil-Sen slope, in magnitudes per magnitude of B-V, or `None`.
    reason : `str`
        Why there is no slope, or an empty string.
    """
    if band is not CatalogBand.SIMBAD_V:
        return None, "color term not fitted: Gaia G stars carry no B-V color in this library"
    known = np.isfinite(colors)
    known_count = int(known.sum())
    if known_count < MINIMUM_STARS_FOR_COLOUR_SLOPE:
        return None, (
            f"color term not fitted: only {known_count} used stars have a B-V color "
            f"(at least {MINIMUM_STARS_FOR_COLOUR_SLOPE} are needed)"
        )
    spread = float(np.percentile(colors[known], 95) - np.percentile(colors[known], 5))
    if spread < MINIMUM_COLOUR_RANGE_MAG:
        return None, (
            f"color term not fitted: the colors span only {spread:.2f} mag from the 5th to the 95th "
            f"percentile (at least {MINIMUM_COLOUR_RANGE_MAG:g} mag are needed)"
        )
    slope = float(theilslopes(offsets[known], colors[known])[0])
    return slope, ""


def session_zero_points(
    session_id: str,
    stars: Sequence[StellarObject],
    frame_paths: dict[datetime, str],
    frame_shape: tuple[int, int],
) -> SessionZeroPoints:
    """Find the zero point of every frame of a session.

    The stars' raw fluxes are read as they stand after the per-frame
    measurement, before any frame is dropped as an outlier, so a cloudy frame
    gets a (low) zero point rather than vanishing.

    Parameters
    ----------
    session_id : `str`
        The session's id.
    stars : `Sequence` [`StellarObject`]
        The session's stars, with raw light curves and catalog data. Their
        light curves must still hold every measured frame.
    frame_paths : `dict` [`datetime.datetime`, `str`]
        The path of each measured frame, keyed by its exposure start.
    frame_shape : `tuple` [`int`, `int`]
        ``(height, width)`` of the reference frame, in pixels.

    Returns
    -------
    session : `SessionZeroPoints`
        One record per frame. With no usable band, each frame's estimate says
        so.
    """
    star_set = choose_catalog_band(stars, frame_shape)
    # Every star's light curve holds the same frames in the same order, so the
    # first one that has any names them.
    frame_timestamps = next(
        (star.photometry.timestamps for star in stars if star.photometry and star.photometry.timestamps), []
    )
    frame_count = len(frame_timestamps)
    flux = np.full((len(star_set.star_indices), frame_count), np.nan)
    flux_error = np.full_like(flux, np.nan)
    saturated = np.zeros(flux.shape, dtype=bool)
    airmass = np.full(frame_count, np.nan)
    for row, star_index in enumerate(star_set.star_indices):
        light_curve = stars[star_index].photometry
        if light_curve is None or len(light_curve.fluxes) != frame_count:
            continue
        flux[row] = light_curve.fluxes
        if len(light_curve.flux_errors) == frame_count:
            flux_error[row] = light_curve.flux_errors
        if len(light_curve.is_saturated) == frame_count:
            saturated[row] = light_curve.is_saturated
        if np.isnan(airmass).all() and len(light_curve.airmasses) == frame_count:
            airmass = np.asarray(light_curve.airmasses, dtype=float)
    frames: list[FrameZeroPoint] = []
    for column, timestamp in enumerate(frame_timestamps):
        if star_set.band is None:
            estimate = FrameZeroPointEstimate(None, None, 0, star_set.reason, None, star_set.reason)
        else:
            estimate = estimate_frame_zero_point(
                star_set.catalog_magnitudes,
                flux[:, column],
                flux_error[:, column],
                saturated[:, column],
                star_set.colors,
                star_set.band,
            )
        value = float(airmass[column])
        known = math.isfinite(value) and value != UNKNOWN_AIRMASS_DEFAULT
        frames.append(
            FrameZeroPoint(
                frame_paths.get(timestamp, "Unknown"), timestamp, value if known else None, estimate
            )
        )
    return SessionZeroPoints(session_id, star_set.band, star_set.reason, tuple(frames))


def zero_point_scatter(zero_points_mag: Sequence[float]) -> ZeroPointScatter:
    """Measure how much one night's frame zero points spread.

    The spread is the median absolute deviation times 1.4826 (a robust
    estimate of the standard deviation), so a few cloudy frames do not set
    it. Its error is the standard deviation of that spread over
    `BOOTSTRAP_RESAMPLE_COUNT` seeded resamples of the frames.

    Parameters
    ----------
    zero_points_mag : `Sequence` [`float`]
        The zero points of the frames of one session that have one, in
        magnitudes.

    Returns
    -------
    scatter : `ZeroPointScatter`
        The spread, its error and the frame count, or the reason there is no
        spread.
    """
    values = np.array([value for value in zero_points_mag if math.isfinite(value)], dtype=float)
    if values.size < MINIMUM_FRAMES_FOR_SCATTER:
        return ZeroPointScatter(
            None,
            None,
            int(values.size),
            f"only {values.size} frames have a zero point (at least {MINIMUM_FRAMES_FOR_SCATTER} are needed)",
        )
    spread = float(MAD_TO_SIGMA * np.median(np.abs(values - np.median(values))))
    generator = np.random.default_rng(BOOTSTRAP_SEED)
    draws = generator.integers(0, values.size, size=(BOOTSTRAP_RESAMPLE_COUNT, values.size))
    samples = values[draws]
    medians = np.median(samples, axis=1, keepdims=True)
    spreads = MAD_TO_SIGMA * np.median(np.abs(samples - medians), axis=1)
    return ZeroPointScatter(spread, float(np.std(spreads)), int(values.size), "")
