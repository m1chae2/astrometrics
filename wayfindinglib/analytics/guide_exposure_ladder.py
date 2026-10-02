"""Purpose: Analyse a ladder of guide frames taken at different exposures.

Description: Turns guide-camera frames, taken in short series at several
exposure lengths, into the answer to "what is the shortest exposure this guide
camera needs?". Every step works on arrays and numbers, so the same analysis
serves a live test and recorded frames.

For each exposure length it does three things.

1. Finds the brightest star in the first frame and measures its peak, its
   total light above the background, and the background and its scatter.
2. Follows that star through the series and measures where its centre is in
   each frame. The mount tracks without guiding, so the star drifts slowly.
   The second difference of a position series, ``p[i+2] - 2 p[i+1] + p[i]``,
   cancels a steady drift and leaves noise: for white noise of size s it has
   a standard deviation of ``s * sqrt(6)``. That gives the position noise
   directly, in arcseconds, with no assumption about how signal-to-noise is
   defined.
3. Checks that the star does not reach the camera's ceiling.

Seeing moves the star at every exposure length, and no camera setting removes
it. What a short exposure can be blamed for is only the noise above the floor
that the best exposure reaches. So each length's excess noise is
``sqrt(noise^2 - floor^2)``, where the floor is the lowest noise among the
unsaturated lengths.

A length is acceptable when all of these hold:

* its star is found and is not saturated;
* its excess noise is within the allowed share of the guiding error. Noise
  adds in quadrature to the rest of the error, so keeping it to half the blur
  tolerance ``f`` means ``excess <= limit * sqrt((1 + f / 2)^2 - 1)``;
* its total noise is within the guiding error itself, since noise above the
  whole error budget cannot be guided out.

The limit and ``f`` come from the equipment's performance envelope.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import uniform_filter

from wayfindinglib.models.session.guide_exposure_ladder import GuideExposureLadder, GuideExposureResult

APERTURE_RADIUS_PIXELS = 6
"""Radius, in pixels, of the circle used for a star's light and centre.

The guide scope's star is about one pixel wide (a 5.6 arcsec star on a 6.4
arcsec pixel), so this is wide enough to hold the whole star and its wings
without taking in neighbours.
"""

MINIMUM_FRAMES = 5
"""Fewest frames with the star found for a position noise to be worked out.

The second difference needs three positions, and five give three of them.
"""

SATURATION_FRACTION = 0.8
"""Share of the camera's ceiling at which the star counts as saturated.

The same 80 percent the science side uses when it plans exposures: far enough
below the ceiling that a change of seeing does not clip the star.
"""

DETECTION_SIGMA = 5.0
"""How many background scatters above the background a star's peak must be."""

_SMOOTHING_PIXELS = 3
"""Width of the box used to find the brightest star without a hot pixel."""


@dataclass
class StarMeasurement:
    """One star in one frame.

    Attributes
    ----------
    x : `float`
        Centre, in pixels along the image's columns.
    y : `float`
        Centre, in pixels along the image's rows.
    peak : `float`
        Brightest pixel inside the aperture.
    flux : `float`
        Total light above the background inside the aperture.
    background : `float`
        Median pixel value of the frame.
    noise : `float`
        Scatter of the frame's background pixels.
    """

    x: float
    y: float
    peak: float
    flux: float
    background: float
    noise: float


def measure_star(frame: np.ndarray, expected_xy: tuple[float, float] | None = None) -> StarMeasurement | None:
    """Find a star in one frame and measure it.

    Parameters
    ----------
    frame : `numpy.ndarray`
        A two-dimensional guide frame, in camera counts.
    expected_xy : `tuple` [`float`, `float`] or `None`, optional
        Where to look, as (x, y). Left out, the brightest star is used.

    Returns
    -------
    star : `StarMeasurement` or `None`
        The measurement, or `None` if no star stands out from the background
        or the light above the background is not positive.
    """
    data = np.asarray(frame, dtype=float)
    background = float(np.median(data))
    noise = float(1.4826 * np.median(np.abs(data - background)))
    if expected_xy is None:
        smoothed = uniform_filter(data, size=_SMOOTHING_PIXELS)
        y0, x0 = np.unravel_index(int(np.argmax(smoothed)), smoothed.shape)
    else:
        x0, y0 = round(expected_xy[0]), round(expected_xy[1])
    rows, columns = np.ogrid[: data.shape[0], : data.shape[1]]
    inside = (rows - y0) ** 2 + (columns - x0) ** 2 <= APERTURE_RADIUS_PIXELS**2
    peak = float(data[inside].max())
    if noise <= 0 or peak < background + DETECTION_SIGMA * noise:
        return None
    weights = np.where(inside, np.clip(data - background, 0.0, None), 0.0)
    flux = float(weights.sum())
    if flux <= 0:
        return None
    y = float((weights * rows).sum() / flux)
    x = float((weights * columns).sum() / flux)
    return StarMeasurement(x=x, y=y, peak=peak, flux=flux, background=background, noise=noise)


def position_noise_pixels(positions: Sequence[tuple[float, float]]) -> float | None:
    """Work out the noise in a series of star positions, ignoring drift.

    Parameters
    ----------
    positions : `Sequence` [`tuple` [`float`, `float`]]
        The star's (x, y) in each frame, in time order.

    Returns
    -------
    noise : `float` or `None`
        Noise per axis, in pixels, from the second differences of each axis.
        `None` if there are fewer than `MINIMUM_FRAMES` positions.
    """
    if len(positions) < MINIMUM_FRAMES:
        return None
    points = np.asarray(positions, dtype=float)
    variances = []
    for axis in range(2):
        second_difference = points[2:, axis] - 2.0 * points[1:-1, axis] + points[:-2, axis]
        variances.append(float(np.var(second_difference)) / 6.0)
    return math.sqrt(sum(variances) / 2.0)


def _measure_series(
    exposure_seconds: float,
    frames: Sequence[np.ndarray],
    plate_scale_arcsec_per_px: float,
    ceiling_adu: float,
    rms_limit_arcsec: float | None,
) -> GuideExposureResult:
    """Measure one exposure length's series of frames.

    Returns
    -------
    result : `GuideExposureResult`
        What the series showed, apart from the excess noise, which needs the
        other series. A series in which the star is not found reports only
        its length.
    """
    result = GuideExposureResult(exposure_seconds=exposure_seconds)
    first = measure_star(frames[0]) if len(frames) else None
    if first is None:
        return result
    measurements = [first]
    for frame in frames[1:]:
        measurement = measure_star(frame, (first.x, first.y))
        if measurement is not None:
            measurements.append(measurement)
    result.frames_measured = len(measurements)
    result.peak_adu = max(m.peak for m in measurements)
    result.flux_adu = float(np.median([m.flux for m in measurements]))
    result.flux_per_second = result.flux_adu / exposure_seconds
    result.background_adu = float(np.median([m.background for m in measurements]))
    result.noise_adu = float(np.median([m.noise for m in measurements]))
    result.saturated = result.peak_adu >= SATURATION_FRACTION * ceiling_adu
    noise_pixels = position_noise_pixels([(m.x, m.y) for m in measurements])
    if noise_pixels is not None:
        result.jitter_arcsec = noise_pixels * plate_scale_arcsec_per_px
        if rms_limit_arcsec:
            result.jitter_fraction_of_limit = result.jitter_arcsec / rms_limit_arcsec
    return result


def _summarise(results: Sequence[GuideExposureResult], recommended: float | None) -> str:
    """Say what the test found, in plain language.

    Returns
    -------
    summary : `str`
        The finding and, when no exposure worked, the likely reason.
    """
    if not results or all(result.frames_measured == 0 for result in results):
        return (
            "No star was found in any frame. Point the guide scope at a bright star, "
            "check that the cap is off, and try again."
        )
    if recommended is not None:
        shortest = results[0].exposure_seconds
        if recommended <= shortest:
            return (
                f"The shortest exposure tried, {recommended:g} s, already has small enough extra "
                "position noise and does not saturate. A longer guide exposure is not needed, "
                "and it would only slow the guider's corrections."
            )
        return (
            f"{recommended:g} s is the shortest exposure with small enough extra position noise and no "
            "saturation. Shorter exposures were too noisy."
        )
    if all(result.saturated for result in results if result.frames_measured):
        return (
            "The star saturates at every exposure tried. Try shorter exposures or a lower gain, "
            "or pick a fainter star."
        )
    return (
        "No exposure tried had small enough position noise without saturating. Longer exposures, "
        "a higher gain, or a brighter star are needed. If the star is much fainter than usual, look "
        "for focus, dew, an obstruction or a gain or binning setting that changed."
    )


def analyze_guide_exposure_ladder(
    frames_by_exposure: Mapping[float, Sequence[np.ndarray]],
    plate_scale_arcsec_per_px: float,
    ceiling_adu: float,
    guiding_rms_limit_arcsec: float | None,
    blur_tolerance_fraction: float,
) -> GuideExposureLadder:
    """Find the shortest guide exposure that works.

    Parameters
    ----------
    frames_by_exposure : `Mapping` [`float`, `Sequence` [`numpy.ndarray`]]
        The frames taken at each exposure length, in time order.
    plate_scale_arcsec_per_px : `float`
        The guide camera's plate scale.
    ceiling_adu : `float`
        The value at which the guide camera saturates.
    guiding_rms_limit_arcsec : `float` or `None`
        The acceptable guiding error per axis for this equipment, from the
        performance envelope. Without it no exposure can be called acceptable.
    blur_tolerance_fraction : `float`
        The most the error may widen a star, as a fraction of its width.

    Returns
    -------
    test : `GuideExposureLadder`
        One result per exposure length, the shortest acceptable exposure, and
        a plain-language summary.
    """
    jitter_limit = (
        guiding_rms_limit_arcsec * math.sqrt((1.0 + blur_tolerance_fraction / 2.0) ** 2 - 1.0)
        if guiding_rms_limit_arcsec
        else None
    )
    results = [
        _measure_series(
            exposure,
            frames_by_exposure[exposure],
            plate_scale_arcsec_per_px,
            ceiling_adu,
            guiding_rms_limit_arcsec,
        )
        for exposure in sorted(frames_by_exposure)
    ]
    usable = [r.jitter_arcsec for r in results if r.jitter_arcsec is not None and not r.saturated]
    floor = min(usable) if len(usable) > 1 else 0.0
    for result in results:
        if result.jitter_arcsec is None:
            continue
        result.excess_jitter_arcsec = math.sqrt(max(result.jitter_arcsec**2 - floor**2, 0.0))
        result.acceptable = (
            not result.saturated
            and jitter_limit is not None
            and guiding_rms_limit_arcsec is not None
            and result.excess_jitter_arcsec <= jitter_limit
            and result.jitter_arcsec <= guiding_rms_limit_arcsec
        )
    acceptable = [result.exposure_seconds for result in results if result.acceptable]
    recommended = min(acceptable) if acceptable else None
    return GuideExposureLadder(
        results=results,
        recommended_exposure_seconds=recommended,
        guiding_rms_limit_arcsec=guiding_rms_limit_arcsec,
        jitter_limit_arcsec=jitter_limit,
        plate_scale_arcsec_per_px=plate_scale_arcsec_per_px,
        ceiling_adu=ceiling_adu,
        summary=_summarise(results, recommended),
    )
