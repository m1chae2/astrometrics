"""Find each frame's catalog zero point, so a session's frames can be compared.

The zero point ties the camera's counts to the catalog's brightness scale.
A star of catalog magnitude ``m`` that gives ``f`` ADU per second in a frame
has the instrumental magnitude ``-2.5 log10(f)``. The offset
``m + 2.5 log10(f)`` would be the same for every star of a frame if the frame
were clean and the star's catalog magnitude matched the camera's band. In
practice each star has its own offset (its color, a neighbor's light, an
error in its catalog magnitude), so the stars scatter by about 0.2 mag.
That scatter is the same in every frame of a session, so it hides nothing
about the sky: only the change from frame to frame is the sky's.

This module therefore reports a **differential zero point**. It picks one
fixed set of stars for the whole session. For each star it takes the
session median of the star's offsets as that star's reference. The frame's
zero point is the session level (the median of the references) plus the
median, over the fixed stars, of how far each star's offset in this frame
sits from its own reference. Thin cloud lowers every star together, so the
frame's zero point falls by the same amount. The frame-to-frame precision is
a few thousandths of a magnitude. The absolute level is only as good as the
star sample (its error is reported separately) and carries the systematic
effects listed below.

This module measures. It does not say whether a night was photometric. That
judgment, and any summary over a night, belongs to the observatory side
(``wayfindinglib``).

What the zero point is, in this library:

* **One catalog band per session.** A star's catalog magnitude is either a
  Gaia G magnitude or a SIMBAD V magnitude, and the star record says which
  catalog it came from (``catalog_match_quality.matched_via``). The two bands
  are different filters, so mixing them would add scatter that is not the
  sky's. `session_zero_points` prefers Gaia G whenever its fixed set holds at
  least `MINIMUM_STARS_FOR_ZERO_POINT` stars, and falls back to SIMBAD V.
  SIMBAD is matched before Gaia, so the Gaia stars are the ones SIMBAD did
  not match.
* **A fixed star set.** A star belongs to the set when it passes the cuts in
  at least `SESSION_STAR_FRAME_SHARE` of the session's frames. Using the same
  stars in every frame keeps a star that drops out in a cloudy frame from
  moving the median.
* **For a star of the field's median color.** The level is the median over
  the set, so it describes a star of the set's median color. No color,
  extinction or aperture correction is applied.
* **No airmass correction.** Correcting for the thickness of the air needs
  an extinction coefficient, and finding one needs several photometric
  nights, which this observatory has not had.
* **The aperture is fixed.** Photometry uses an aperture 4 px in radius with
  no aperture correction, so a blurrier frame puts less of each star's light
  inside it, and the zero point falls with the star width (a tenth to two
  tenths of a magnitude at a width of 3.3 px). A change in focus or seeing
  looks like a change in transparency.
* **A color slope, reported but not applied.** The L filter and the sensor
  do not match the catalog bands, so a star's offset can depend on its color.
  For SIMBAD V sessions, when enough stars have a B-V color
  (`MINIMUM_STARS_FOR_COLOR_SLOPE` spread over `MINIMUM_COLOR_RANGE_MAG`),
  `differential_frame_estimates` also fits the slope of offset against B-V.
  Gaia G stars carry no color in this library, so their sessions have no
  slope.
* **A central region only.** Photometry measures frames that are not
  flat-fielded, so vignetting dims stars near the corners. Only stars within
  `CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION` of the half-diagonal from the frame
  center are used.
* **No bright stars.** A star whose brightest aperture pixel lies above the
  camera profile's photometric linearity limit is left out, because the
  sensor no longer answers in proportion to the light there. Without a limit
  in the profile nothing is measured.

Every number here is exploratory. The cuts below are design estimates, and
the register entry for the zero point says what the real-data check showed.
"""

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import numpy as np
from astropy.stats import mad_std
from scipy.stats import theilslopes

from astrometricslib.models.stellar_source import BRIGHTEST_CATALOG_MAGNITUDE, StellarObject
from astrometricslib.pipelines.shared.quality.frame_statistics import (
    BOOTSTRAP_RESAMPLE_COUNT,
    BOOTSTRAP_SEED,
)

logger = logging.getLogger(__name__)

# A star must be within this fraction of the frame's half-diagonal from the
# frame center. Photometry does not flat-field its frames, so a star near a
# corner is dimmed by vignetting that the zero point would then absorb. The
# Apertura 75Q at f/5.4 is a short refractor with a flattener; its vignetting
# at the corner has not been measured on these frames, so 0.5 is a design
# estimate. A circle of this radius covers pi/8, about 39 percent, of a
# square frame's area, and is about 34 arcminutes across on the 3008 px ASI
# 533MM field. The real-data check in the register (PHO-15) compares the
# offsets of the inner and outer stars of one frame.
CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION = 0.5

# The fewest stars a session's fixed set, and a frame, needs for a median
# worth storing. Ten is twice the five that the Moffat width bootstrap in
# `frame_measurement` needs, because the stars here also scatter from the
# catalog magnitudes' own errors (a few hundredths of a magnitude for SIMBAD,
# a few thousandths for Gaia G). A design estimate.
MINIMUM_STARS_FOR_ZERO_POINT = 10

# A star joins the session's fixed set when it passes the cuts in at least
# this share of the session's frames. 80 percent tolerates the share of
# frames a night loses to passing cloud or a bad exposure (8 of 61 frames,
# 13 percent, on the M 57 night of PHO-15) and still asks a star to be bright
# enough to be seen in nearly every frame. A design estimate.
SESSION_STAR_FRAME_SHARE = 0.8

# The fewest frames a night needs for a spread worth storing by the helper
# `zero_point_scatter`. Ten frames give a spread whose own error is about a
# quarter of its value (the standard error of a robust sigma is near
# 1.2 sigma / sqrt(n)). A design estimate.
MINIMUM_FRAMES_FOR_SCATTER = 10

# A star is used only when its flux is at least this many times its own
# 1-sigma error. A faint star's aperture flux is clamped at zero when the sky
# estimate is too high, which pushes its offset up; a signal-to-noise of 20
# (a 5 percent error, or 0.05 mag) keeps that clamp out of the sample. The
# error comes from the CCD equation with the camera profile's gain (see
# `pre_processing/detector_noise.py`); when no gain is known the pipeline
# assumes 1 electron per ADU and the zero point rows are marked assumed. A
# design estimate.
MINIMUM_SIGNAL_TO_NOISE = 20.0

# The color slope needs this many stars with a B-V color, after the cuts
# above, and a spread of colors at least this wide (95th minus 5th percentile,
# in magnitudes). Without a spread the slope is a ratio of two noise terms.
# The photometry fields hold B-V from about 0.0 to 1.6 for G, K and M stars
# (the SIMBAD fields M 57, M 27 and M 13 hold 220, 363 and 296 such stars).
# A design estimate.
MINIMUM_STARS_FOR_COLOR_SLOPE = 20
MINIMUM_COLOR_RANGE_MAG = 0.4

# For normally distributed data the standard error of a median is this many
# times the standard deviation over the square root of the sample size
# (it is sqrt(pi / 2)). The standard deviation is the robust one from the
# median absolute deviation (`astropy.stats.mad_std`), so a blended star does
# not inflate it. PHO-15 compares this with a bootstrap on the M 57 stars.
STANDARD_ERROR_OF_MEDIAN_FACTOR = 1.2533


class CatalogBand(StrEnum):
    """The catalog magnitude a zero point is tied to."""

    GAIA_G = "gaia_g"
    SIMBAD_V = "simbad_v"


_BAND_BY_MATCH_SOURCE: dict[str, CatalogBand] = {
    "gaia": CatalogBand.GAIA_G,
    "simbad": CatalogBand.SIMBAD_V,
}

# The bands in the order a session tries them: Gaia G first, because its
# magnitudes are a single homogeneous system, then SIMBAD V.
_BANDS_IN_PREFERENCE_ORDER: tuple[CatalogBand, ...] = (CatalogBand.GAIA_G, CatalogBand.SIMBAD_V)

NO_BAND_REASON_PREFIX = "no catalog band had enough stars"


@dataclass(frozen=True)
class FrameZeroPointEstimate:
    """The zero point of one frame, or the reason there is none.

    Parameters
    ----------
    zero_point_mag : `float` or `None`
        The session level plus the median offset change of the fixed stars
        in this frame, in magnitudes. `None` when the frame had too few
        usable stars.
    error_mag : `float` or `None`
        The standard error of that median change, in magnitudes. It is the
        frame-to-frame precision of the zero point, not the error of the
        absolute level.
    star_count : `int`
        The fixed-set stars that passed the cuts in this frame.
    reason : `str`
        Why `zero_point_mag` is `None`. Empty when it has a value.
    color_slope : `float` or `None`
        The Theil-Sen slope of the offset against B-V, in magnitudes per
        magnitude of color. `None` when it was not fitted.
    color_reason : `str`
        Why `color_slope` is `None`. Empty when it has a value.
    star_sample_error_mag : `float` or `None`
        The statistical error of the absolute level from the star sample, in
        magnitudes (the same for every frame of the session). `None` when
        the frame has no zero point.
    """

    zero_point_mag: float | None
    error_mag: float | None
    star_count: int
    reason: str
    color_slope: float | None
    color_reason: str
    star_sample_error_mag: float | None = None


@dataclass(frozen=True)
class FrameZeroPoint:
    """The zero point of one frame, with what identifies the frame.

    Parameters
    ----------
    frame_path : `str` or `None`
        The frame's path. `None` when the caller did not give a path for
        every frame; such a frame cannot be stored.
    timestamp : `datetime.datetime`
        The exposure start in UTC with no zone (the same as the light
        curves').
    estimate : `FrameZeroPointEstimate`
        The zero point and color slope.
    """

    frame_path: str | None
    timestamp: datetime
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
        One record per frame the pipeline measured, sorted by exposure start.
    star_set_size : `int`
        The stars in the session's fixed set (0 when there is no band).
    errors_assume_unit_gain : `bool`
        Whether the flux errors behind the signal-to-noise cut assumed 1
        electron per ADU because no gain was known.
    """

    session_id: str
    band: CatalogBand | None
    band_reason: str
    frames: tuple[FrameZeroPoint, ...]
    star_set_size: int = 0
    errors_assume_unit_gain: bool = False


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


def central_candidates_by_band(
    stars: Sequence[StellarObject], frame_shape: tuple[int, int]
) -> tuple[dict[CatalogBand, list[int]], str]:
    """List the stars that could join a band's set, before any frame is read.

    A star qualifies when it has a catalog magnitude, a recorded catalog
    source, a position inside the central region and a match that was not
    flagged ambiguous (two catalog entries within the companion radius, so
    the magnitude may belong to a neighbor).

    Parameters
    ----------
    stars : `Sequence` [`StellarObject`]
        The session's stars.
    frame_shape : `tuple` [`int`, `int`]
        ``(height, width)`` of the reference frame, in pixels.

    Returns
    -------
    members : `dict` [`CatalogBand`, `list` [`int`]]
        For each band, the positions in `stars` of the qualifying stars.
    summary : `str`
        How many stars each filter left, for a reason text.
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
    summary = (
        f"{with_magnitude} stars have a catalog magnitude, {with_band} of them a recorded catalog source, "
        f"{unambiguous} of those an unambiguous match, and {len(members[CatalogBand.GAIA_G])} Gaia G and "
        f"{len(members[CatalogBand.SIMBAD_V])} SIMBAD V of those lie in the central region "
        f"({radius_px:.0f} px from the center)"
    )
    return members, summary


def usable_star_mask(
    flux_rates: np.ndarray,
    flux_errors: np.ndarray,
    is_saturated: np.ndarray,
    peak_adu: np.ndarray,
    linearity_limit_adu: float,
) -> np.ndarray:
    """Say which star measurements pass the cuts.

    A measurement is used when its flux is positive, the star is not
    saturated, its error is positive, its flux is at least
    `MINIMUM_SIGNAL_TO_NOISE` times that error, and its peak pixel is known
    and not above the photometric linearity limit.

    Parameters
    ----------
    flux_rates : `numpy.ndarray`
        The flux in ADU per second (NaN if missing), of any shape.
    flux_errors : `numpy.ndarray`
        The 1-sigma error of each flux, in ADU per second, same shape.
    is_saturated : `numpy.ndarray`
        `True` where the star's aperture held saturated pixels, same shape.
    peak_adu : `numpy.ndarray`
        The largest raw pixel value in the aperture, in ADU, same shape (NaN
        where unknown).
    linearity_limit_adu : `float`
        The camera's photometric linearity limit, in ADU.

    Returns
    -------
    usable : `numpy.ndarray`
        A boolean array of the same shape.
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        return (
            np.isfinite(flux_rates)
            & (flux_rates > 0.0)
            & ~is_saturated
            & np.isfinite(flux_errors)
            & (flux_errors > 0.0)
            & (flux_rates >= MINIMUM_SIGNAL_TO_NOISE * flux_errors)
            & np.isfinite(peak_adu)
            & (peak_adu <= linearity_limit_adu)
        )


def fixed_star_rows(usable: np.ndarray) -> np.ndarray:
    """Pick the stars that pass the cuts in enough of the session's frames.

    Parameters
    ----------
    usable : `numpy.ndarray`
        A boolean array with one row per star and one column per frame.

    Returns
    -------
    rows : `numpy.ndarray`
        The rows whose share of passing frames is at least
        `SESSION_STAR_FRAME_SHARE`.
    """
    if usable.shape[1] == 0:
        return np.empty(0, dtype=int)
    return np.flatnonzero(usable.mean(axis=1) >= SESSION_STAR_FRAME_SHARE)


def standard_error_of_median(values: np.ndarray) -> float:
    """Estimate the standard error of a median from the robust spread.

    Parameters
    ----------
    values : `numpy.ndarray`
        The sample, with no NaN.

    Returns
    -------
    error : `float`
        `STANDARD_ERROR_OF_MEDIAN_FACTOR` times the median-absolute-
        deviation sigma over the square root of the sample size.
    """
    return float(STANDARD_ERROR_OF_MEDIAN_FACTOR * mad_std(values) / math.sqrt(values.size))


def differential_frame_estimates(
    catalog_magnitudes: np.ndarray,
    flux_rates: np.ndarray,
    usable: np.ndarray,
    colors: np.ndarray,
    band: CatalogBand,
) -> list[FrameZeroPointEstimate]:
    """Find the differential zero point of every frame from a fixed star set.

    Each star's offset in a frame is ``m + 2.5 log10(flux)``. Its reference
    is the median of its offsets over the frames where it passed the cuts.
    The session level is the median of the references. A frame's zero point
    is the level plus the median, over the stars that passed in that frame,
    of the offset minus the star's reference.

    Parameters
    ----------
    catalog_magnitudes : `numpy.ndarray`
        Catalog magnitude of each star of the fixed set.
    flux_rates : `numpy.ndarray`
        The flux of each star in each frame, in ADU per second, with one row
        per star and one column per frame.
    usable : `numpy.ndarray`
        Where the measurement passed the cuts (see `usable_star_mask`), same
        shape as `flux_rates`. Every row must pass in at least one frame.
    colors : `numpy.ndarray`
        B-V color of each star, NaN where unknown.
    band : `CatalogBand`
        The session's band (decides whether a color slope is possible).

    Returns
    -------
    estimates : `list` [`FrameZeroPointEstimate`]
        One per frame (column), each with a value or the reason it has none.
    """
    star_count, frame_count = flux_rates.shape
    with np.errstate(invalid="ignore", divide="ignore"):
        offsets = np.where(
            usable,
            catalog_magnitudes[:, np.newaxis] + 2.5 * np.log10(np.where(usable, flux_rates, 1.0)),
            np.nan,
        )
    references = np.nanmedian(offsets, axis=1)
    level_mag = float(np.median(references))
    star_sample_error_mag = standard_error_of_median(references)
    differences = offsets - references[:, np.newaxis]
    estimates = []
    for column in range(frame_count):
        passed = usable[:, column]
        passed_count = int(passed.sum())
        if passed_count < MINIMUM_STARS_FOR_ZERO_POINT:
            reason = (
                f"only {passed_count} of the {star_count} stars of the session's fixed set passed the cuts "
                f"in this frame (positive flux, not saturated, below the linearity limit, signal-to-noise "
                f"of at least {MINIMUM_SIGNAL_TO_NOISE:g}); at least "
                f"{MINIMUM_STARS_FOR_ZERO_POINT} are needed"
            )
            estimates.append(FrameZeroPointEstimate(None, None, passed_count, reason, None, reason))
            continue
        frame_differences = differences[passed, column]
        slope, slope_reason = _color_slope(offsets[passed, column], colors[passed], band)
        estimates.append(
            FrameZeroPointEstimate(
                level_mag + float(np.median(frame_differences)),
                standard_error_of_median(frame_differences),
                passed_count,
                "",
                slope,
                slope_reason,
                star_sample_error_mag,
            )
        )
    return estimates


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
    if known_count < MINIMUM_STARS_FOR_COLOR_SLOPE:
        return None, (
            f"color term not fitted: only {known_count} used stars have a B-V color "
            f"(at least {MINIMUM_STARS_FOR_COLOR_SLOPE} are needed)"
        )
    spread = float(np.percentile(colors[known], 95) - np.percentile(colors[known], 5))
    if spread < MINIMUM_COLOR_RANGE_MAG:
        return None, (
            f"color term not fitted: the colors span only {spread:.2f} mag from the 5th to the 95th "
            f"percentile (at least {MINIMUM_COLOR_RANGE_MAG:g} mag are needed)"
        )
    slope = float(theilslopes(offsets[known], colors[known])[0])
    return slope, ""


def _light_curve_matrices(
    stars: Sequence[StellarObject],
    indices: Sequence[int],
    frame_count: int,
    peak_adu_by_star_id: Mapping[str, Sequence[float]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Stack the chosen stars' light curves into one array per quantity.

    A star whose light curve does not hold exactly one entry per frame keeps
    NaN fluxes and peaks, so it fails every cut.

    Parameters
    ----------
    stars : `Sequence` [`StellarObject`]
        The session's stars.
    indices : `Sequence` [`int`]
        The positions of the stars to stack.
    frame_count : `int`
        The number of frames.
    peak_adu_by_star_id : `Mapping` [`str`, `Sequence` [`float`]]
        Each star's peak pixel in each frame, in the order of its light curve.

    Returns
    -------
    flux, flux_error, saturated, peak : `tuple` [`numpy.ndarray`]
        Arrays with one row per star and one column per frame.
    """
    shape = (len(indices), frame_count)
    flux = np.full(shape, np.nan)
    flux_error = np.full(shape, np.nan)
    saturated = np.zeros(shape, dtype=bool)
    peak = np.full(shape, np.nan)
    for row, star_index in enumerate(indices):
        star = stars[star_index]
        light_curve = star.photometry
        if light_curve is None or len(light_curve.fluxes) != frame_count:
            continue
        flux[row] = light_curve.fluxes
        if len(light_curve.flux_errors) == frame_count:
            flux_error[row] = light_curve.flux_errors
        if len(light_curve.is_saturated) == frame_count:
            saturated[row] = light_curve.is_saturated
        star_peaks = peak_adu_by_star_id.get(star.id)
        if star_peaks is not None and len(star_peaks) == frame_count:
            peak[row] = star_peaks
    return flux, flux_error, saturated, peak


def session_zero_points(
    session_id: str,
    stars: Sequence[StellarObject],
    frame_paths: Sequence[str],
    frame_shape: tuple[int, int],
    *,
    photometric_linearity_limit_adu: float | None,
    peak_adu_by_star_id: Mapping[str, Sequence[float]],
    errors_assume_unit_gain: bool = False,
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
    frame_paths : `Sequence` [`str`]
        The path of each measured frame, in the order of the light curves.
        When its length is not the number of frames, no frame gets a path.
    frame_shape : `tuple` [`int`, `int`]
        ``(height, width)`` of the reference frame, in pixels.
    photometric_linearity_limit_adu : `float` or `None`
        The camera profile's photometric linearity limit. Without it nothing
        is measured, because bright, nonlinear stars could not be left out.
    peak_adu_by_star_id : `Mapping` [`str`, `Sequence` [`float`]]
        Each star's largest aperture pixel in each frame, in the order of
        its light curve.
    errors_assume_unit_gain : `bool`, optional
        Whether the flux errors assumed 1 electron per ADU. It is carried to
        the record step, which marks the rows assumed.

    Returns
    -------
    session : `SessionZeroPoints`
        One record per frame, sorted by exposure start. With no usable band,
        each frame's estimate says so.
    """
    # Every star's light curve holds the same frames in the same order, so the
    # first one that has any names them.
    timestamps = next(
        (star.photometry.timestamps for star in stars if star.photometry and star.photometry.timestamps), []
    )
    frame_count = len(timestamps)
    paths: list[str | None] = list(frame_paths) if len(frame_paths) == frame_count else [None] * frame_count
    if len(frame_paths) != frame_count:
        logger.warning(
            "The zero point of session %s has %s frames but %s frame paths; no frame can be stored.",
            session_id or "(unnamed)",
            frame_count,
            len(frame_paths),
        )

    estimates: list[FrameZeroPointEstimate]
    band: CatalogBand | None = None
    band_reason = ""
    star_set_size = 0
    candidates, candidate_summary = central_candidates_by_band(stars, frame_shape)
    if photometric_linearity_limit_adu is None:
        band_reason = (
            "the camera profile gives no photometric linearity limit, so stars in the nonlinear range "
            "cannot be left out"
        )
    else:
        counts: dict[CatalogBand, int] = {}
        for candidate_band in _BANDS_IN_PREFERENCE_ORDER:
            indices = candidates[candidate_band]
            flux, flux_error, saturated, peak = _light_curve_matrices(
                stars, indices, frame_count, peak_adu_by_star_id
            )
            usable = usable_star_mask(flux, flux_error, saturated, peak, photometric_linearity_limit_adu)
            rows = fixed_star_rows(usable)
            counts[candidate_band] = int(rows.size)
            if rows.size >= MINIMUM_STARS_FOR_ZERO_POINT:
                band = candidate_band
                star_set_size = int(rows.size)
                chosen_indices = [indices[row] for row in rows]
                estimates = differential_frame_estimates(
                    np.array(
                        [_optional_float(stars[index].magnitude) for index in chosen_indices], dtype=float
                    ),
                    flux[rows],
                    usable[rows],
                    np.array(
                        [
                            value
                            if (value := _optional_float(stars[index].b_minus_v)) is not None
                            else math.nan
                            for index in chosen_indices
                        ],
                        dtype=float,
                    ),
                    band,
                )
                break
        if band is None:
            band_reason = (
                f"{NO_BAND_REASON_PREFIX}: of the stars in the central region, {counts[CatalogBand.GAIA_G]} "
                f"Gaia G and {counts[CatalogBand.SIMBAD_V]} SIMBAD V passed the cuts in at least "
                f"{SESSION_STAR_FRAME_SHARE:.0%} of the frames, and {MINIMUM_STARS_FOR_ZERO_POINT} are "
                f"needed ({candidate_summary})"
            )
    if band is None:
        estimates = [FrameZeroPointEstimate(None, None, 0, band_reason, None, band_reason)] * frame_count
    order = sorted(range(frame_count), key=timestamps.__getitem__)
    frames = tuple(FrameZeroPoint(paths[column], timestamps[column], estimates[column]) for column in order)
    return SessionZeroPoints(session_id, band, band_reason, frames, star_set_size, errors_assume_unit_gain)


def zero_point_scatter(zero_points_mag: Sequence[float]) -> ZeroPointScatter:
    """Measure how much one night's frame zero points spread.

    This is a helper for the observatory side to use when it summarizes a
    night; the photometry pipeline does not store it (a night summary is not
    a pipeline measurement). The spread is the median absolute deviation
    times 1.4826 (a robust estimate of the standard deviation), so a few
    cloudy frames do not set it. Its error is the standard deviation of that
    spread over `BOOTSTRAP_RESAMPLE_COUNT` seeded resamples of the frames.

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
    spread = float(mad_std(values))
    generator = np.random.default_rng(BOOTSTRAP_SEED)
    draws = generator.integers(0, values.size, size=(BOOTSTRAP_RESAMPLE_COUNT, values.size))
    spreads = mad_std(values[draws], axis=1)
    return ZeroPointScatter(spread, float(np.std(spreads)), int(values.size), "")
