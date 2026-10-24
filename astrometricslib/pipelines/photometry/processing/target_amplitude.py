"""The size of the target's brightness change, and where it came from.

It comes from the target's own light curve or from a catalog.

The photometry finish line is "the light curve shows the change the star is
known to make". To judge that, a reader needs the size of the change, and
needs to know where the number came from. This module finds it two ways and
never mixes them:

* **From the target's own light curve.** The light curve is folded on a period
  (the target's periodogram result when the search found one, else the period
  VSX lists), cut into equal bins of phase, and the amplitude is the
  difference between the faintest and brightest bin medians. That needs the
  bins to be filled: a curve that covers only part of the cycle misses the
  minimum or the maximum, and its range is too small. "Enough" coverage is
  defined by `folded_amplitude` below.
* **From the VSX catalog.** The magnitude range VSX lists for the star. It is
  a catalog value, often from old data and for another band, and it is
  labeled as one.

Both are measurements of different things, so they are stored as two metrics.
A reader takes the light-curve one when it exists and the catalog one when it
does not.

This module only calculates. The photometry run chooses the target star.
"""

import math
from dataclasses import dataclass

import numpy as np

__all__ = [
    "MAXIMUM_TARGET_DISTANCE_ARCSEC",
    "MINIMUM_FILLED_BIN_FRACTION",
    "MINIMUM_POINTS_PER_BIN",
    "PHASE_BIN_COUNT",
    "FoldedAmplitude",
    "folded_amplitude",
    "magnitudes_from_flux",
]

# The folded curve is cut into this many equal bins of phase. Ten bins resolve
# a sine and a typical eclipse shape without leaving the bins empty; a sharp
# eclipse narrower than a tenth of the period is smoothed away, and the
# amplitude then reads too small (PHO-19).
PHASE_BIN_COUNT = 10

# A bin needs this many points for its median to be steadier than one point.
# With three, the median is the middle point and one outlier cannot move it.
MINIMUM_POINTS_PER_BIN = 3

# The share of the phase bins that must hold enough points for the amplitude
# to count as covered. Eight of ten leaves room for at most one empty bin
# beside the one that the minimum or maximum might be hiding in. Chosen by
# judgment; not tuned (PHO-D28 in the validation register).
MINIMUM_FILLED_BIN_FRACTION = 0.8

# The target's own star must lie within this distance of the target's catalog
# position, in arcseconds. A target star is centered in the frame and the
# target position comes from SIMBAD to well under an arcsecond. The pointing
# error of the mount is under two arcminutes, but the catalog position is
# used, not the pointing, so the allowance only covers proper motion and the
# measured star position (about 2 pixels = 4 arcsec). 30 arcsec is generous
# and still rejects the neighboring star of a crowded field.
MAXIMUM_TARGET_DISTANCE_ARCSEC = 30.0


@dataclass(frozen=True)
class FoldedAmplitude:
    """The amplitude of a folded light curve, or why there is none.

    Parameters
    ----------
    amplitude_mag : `float` or `None`
        The difference between the faintest and brightest bin medians, in
        magnitudes. `None` when the coverage is not enough.
    error_mag : `float` or `None`
        The standard error of that difference: the two bin medians' standard
        errors added in quadrature.
    filled_fraction : `float`
        The share of the phase bins that hold at least
        `MINIMUM_POINTS_PER_BIN` points.
    point_count : `int`
        How many points were folded.
    reason : `str`
        Why `amplitude_mag` is `None`. Empty when it is not.
    """

    amplitude_mag: float | None
    error_mag: float | None
    filled_fraction: float
    point_count: int
    reason: str


def magnitudes_from_flux(flux: np.ndarray) -> np.ndarray:
    """Turn fluxes into magnitudes relative to the median flux.

    Parameters
    ----------
    flux : `numpy.ndarray`
        The brightness values (any units). A value that is not above zero
        becomes NaN.

    Returns
    -------
    magnitudes : `numpy.ndarray`
        ``-2.5 log10(flux / median(flux))``, NaN where the flux is not above
        zero.
    """
    flux = np.asarray(flux, dtype=float)
    positive = flux > 0
    magnitudes = np.full(flux.shape, np.nan)
    if not positive.any():
        return magnitudes
    median = float(np.median(flux[positive]))
    magnitudes[positive] = -2.5 * np.log10(flux[positive] / median)
    return magnitudes


def folded_amplitude(time_days: np.ndarray, flux: np.ndarray, period_days: float) -> FoldedAmplitude:
    """Find a light curve's amplitude by folding it on a period.

    Parameters
    ----------
    time_days : `numpy.ndarray`
        The measurement times, in days.
    flux : `numpy.ndarray`
        The brightness at those times. Only its relative size matters.
    period_days : `float`
        The period to fold on, in days.

    Returns
    -------
    result : `FoldedAmplitude`
        The amplitude, or the reason there is none (no period, too few
        points, or too few bins with enough points).
    """
    times = np.asarray(time_days, dtype=float)
    magnitudes = magnitudes_from_flux(np.asarray(flux, dtype=float))
    usable = np.isfinite(times) & np.isfinite(magnitudes)
    point_count = int(usable.sum())
    if not math.isfinite(period_days) or period_days <= 0:
        return FoldedAmplitude(None, None, 0.0, point_count, "there is no period to fold on")
    needed = math.ceil(MINIMUM_FILLED_BIN_FRACTION * PHASE_BIN_COUNT) * MINIMUM_POINTS_PER_BIN
    if point_count < needed:
        return FoldedAmplitude(
            None, None, 0.0, point_count, f"only {point_count} usable points, and {needed} are needed"
        )
    phase = np.mod(times[usable] / period_days, 1.0)
    bin_index = np.minimum((phase * PHASE_BIN_COUNT).astype(int), PHASE_BIN_COUNT - 1)
    medians: list[float] = []
    errors: list[float] = []
    for index in range(PHASE_BIN_COUNT):
        values = magnitudes[usable][bin_index == index]
        if values.size < MINIMUM_POINTS_PER_BIN:
            continue
        median = float(np.median(values))
        spread = 1.4826 * float(np.median(np.abs(values - median)))
        medians.append(median)
        # The standard error of a median is 1.2533 times that of a mean.
        errors.append(1.2533 * spread / math.sqrt(values.size))
    filled_fraction = len(medians) / PHASE_BIN_COUNT
    if filled_fraction < MINIMUM_FILLED_BIN_FRACTION:
        return FoldedAmplitude(
            None,
            None,
            filled_fraction,
            point_count,
            f"the light curve fills {len(medians)} of {PHASE_BIN_COUNT} phase bins and "
            f"{math.ceil(MINIMUM_FILLED_BIN_FRACTION * PHASE_BIN_COUNT)} are needed",
        )
    faintest, brightest = int(np.argmax(medians)), int(np.argmin(medians))
    amplitude = medians[faintest] - medians[brightest]
    error = math.hypot(errors[faintest], errors[brightest])
    return FoldedAmplitude(amplitude, error, filled_fraction, point_count, "")
