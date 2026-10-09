r"""Recompute the saved headline numbers from the raw files, independently.

A pipeline's own summary of its work is the weakest evidence of that work: a
bug in a measurement is also in the number reported from it. This script
recomputes a set of headline numbers with different code that shares nothing
with the pipeline's own path, and lists every disagreement.

- For each stored stack (the FITS file on disk): the share of pixels that are
  exactly zero, the share at the camera's saturation level, and the star width
  (FWHM). The pipeline fits Gaussians to stars found by a detector; this script
  takes the half-maximum width of each star's azimuthally averaged profile,
  with no assumption about its shape. A Gaussian fit to a real, non-Gaussian
  star comes out wider than its half-maximum width (about 1.2 to 1.5 times on
  this library), so the two are compared as a scale factor and the targets
  that depart from it are listed. The pipeline's own method is also re-run on
  each stored stack to catch numbers saved by an older version of the code.
- For each star with a light curve: the coefficient of variation (CV) and the
  mean brightness, from the stored measurements.
- For each star with a "detected" or "possible" period: the strongest period
  of a fresh Lomb-Scargle periodogram of its stored light curve.

It reads the catalog database and the stack files and writes nothing.

    python -m astrometricslib.scripts.recompute_headline_numbers

A disagreement is not necessarily a bug in the pipeline: a stored number may
come from an earlier version of the code or from a longer light curve than the
one stored. It is a place to look, and the report says which.
"""

import json
import os
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from astropy.io import fits
from astropy.timeseries import LombScargle
from scipy.ndimage import maximum_filter

from astrometricslib import Astrometrics
from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_image_fwhm

# How closely two star widths must agree to count as the same measurement.
# The two methods weigh a star's wings differently, so they are not expected
# to match exactly.
FWHM_RELATIVE_TOLERANCE = 0.25

# How closely a stored star width must match the pipeline's own method re-run
# on the same file. A larger gap means the stored number is from older code.
FWHM_REPRODUCTION_TOLERANCE = 0.15

# How far a target's ratio of stored to independent width may stray from the
# library's median ratio before the target is listed.
FWHM_SCALE_SPREAD_TOLERANCE = 0.25

# How closely the two fractions must agree, in absolute terms.
FRACTION_TOLERANCE = 0.001

# How closely two CVs must agree, relatively.
CV_RELATIVE_TOLERANCE = 0.01

# The brightness (in noise sigmas above the sky) a pixel must have to count as
# the peak of a star, the isolation required around it, and how many stars to
# measure.
_PEAK_SIGMA = 30.0
_ISOLATION_PX = 15
_STARS_TO_MEASURE = 40
_PROFILE_RADIUS_PX = 12
_BACKGROUND_INNER_PX = 14
_BACKGROUND_OUTER_PX = 20


@dataclass
class Comparison:
    """How one quantity compared across everything that was checked.

    Attributes
    ----------
    name : `str`
        What was recomputed.
    compared : `int`
        How many items had both a stored and a recomputed value.
    note : `str`
        Anything the reader needs to read the numbers, such as a scale factor.
    disagreements : `list` [`str`]
        One line per item whose two values disagree beyond the tolerance.
    """

    name: str
    note: str = ""
    compared: int = 0
    disagreements: list[str] = field(default_factory=list)

    def record(self, item: str, stored: float, recomputed: float, agrees: bool) -> None:
        """Add one comparison.

        Parameters
        ----------
        item : `str`
            What was compared (a target or a star).
        stored : `float`
            The saved value.
        recomputed : `float`
            The independently recomputed value.
        agrees : `bool`
            Whether the two agree within the tolerance.
        """
        self.compared += 1
        if not agrees:
            self.disagreements.append(f"{item}: stored {stored:.4g}, recomputed {recomputed:.4g}")


def radial_profile_fwhm(data: np.ndarray, x: int, y: int) -> float | None:
    """Measure a star's width from its azimuthally averaged profile.

    The profile is the mean brightness in rings around the peak, with the sky
    (median of a distant ring) removed. The width is twice the radius at
    which the profile falls to half its central height, by linear
    interpolation. No shape is assumed.

    Parameters
    ----------
    data : `numpy.ndarray`
        The image.
    x, y : `int`
        The pixel of the star's peak.

    Returns
    -------
    fwhm : `float` or `None`
        The width in pixels, or `None` if the star is too close to an edge or
        its profile never falls to half.
    """
    outer = _BACKGROUND_OUTER_PX
    if x < outer or y < outer or x >= data.shape[1] - outer or y >= data.shape[0] - outer:
        return None
    cutout = data[y - outer : y + outer + 1, x - outer : x + outer + 1].astype(float)
    yy, xx = np.mgrid[-outer : outer + 1, -outer : outer + 1]
    radius = np.hypot(xx, yy)
    sky = float(np.median(cutout[(radius >= _BACKGROUND_INNER_PX) & (radius <= outer)]))
    peak = float(np.max(cutout[radius <= 1.5])) - sky
    if peak <= 0:
        return None
    means = []
    for ring in range(_PROFILE_RADIUS_PX + 1):
        in_ring = (radius >= ring - 0.5) & (radius < ring + 0.5)
        means.append(float(np.mean(cutout[in_ring])) - sky)
    half = 0.5 * peak
    for ring in range(1, len(means)):
        if means[ring] <= half < means[ring - 1]:
            fraction = (means[ring - 1] - half) / (means[ring - 1] - means[ring])
            return 2.0 * (ring - 1 + fraction)
    return None


def independent_fwhm(data: np.ndarray) -> float | None:
    """Measure the median star width of an image by the radial-profile method.

    Parameters
    ----------
    data : `numpy.ndarray`
        The image (2-D).

    Returns
    -------
    fwhm : `float` or `None`
        The median width, in pixels, of up to `_STARS_TO_MEASURE` of the
        brightest isolated, unsaturated stars; `None` if none can be measured.
    """
    image = np.asarray(data, dtype=float)
    finite = image[np.isfinite(image)]
    if finite.size == 0:
        return None
    sky = float(np.median(finite))
    sigma = 1.4826 * float(np.median(np.abs(finite - sky))) or 1e-12
    local_maximum = maximum_filter(image, size=_ISOLATION_PX) == image
    ceiling = float(np.nanmax(image))
    peaks = np.argwhere(local_maximum & (image > sky + _PEAK_SIGMA * sigma) & (image < 0.98 * ceiling))
    brightness = image[peaks[:, 0], peaks[:, 1]]
    widths = []
    for index in np.argsort(-brightness):
        y, x = peaks[index]
        width = radial_profile_fwhm(image, int(x), int(y))
        if width is not None and 0.5 < width < 2 * _PROFILE_RADIUS_PX:
            widths.append(width)
        if len(widths) >= _STARS_TO_MEASURE:
            break
    return float(np.median(widths)) if widths else None


def pixel_fractions(data: np.ndarray, saturation_level: float | None) -> tuple[float, float | None]:
    """Count the pixels that are exactly zero and the pixels at saturation.

    Parameters
    ----------
    data : `numpy.ndarray`
        The image; a colour stack is judged by its worst channel.
    saturation_level : `float` or `None`
        The brightness that counts as saturated, or `None`.

    Returns
    -------
    zero_fraction, saturated_fraction : `tuple` [`float`, `float` or `None`]
        The two shares, from 0 to 1.
    """
    planes = list(data) if data.ndim == 3 else [data]
    zero = max(float(np.count_nonzero(plane == 0) / plane.size) for plane in planes)
    saturated = (
        max(float(np.count_nonzero(plane >= saturation_level) / plane.size) for plane in planes)
        if saturation_level is not None
        else None
    )
    return zero, saturated


def recompute_cv(fluxes: Sequence[float]) -> tuple[float, float] | None:
    """Recompute a star's coefficient of variation and mean brightness.

    Parameters
    ----------
    fluxes : `Sequence` [`float`]
        The stored brightness measurements.

    Returns
    -------
    cv, mean : `tuple` [`float`, `float`] or `None`
        Population standard deviation over mean, and the mean, of the positive
        values; `None` if fewer than three are positive.
    """
    values = np.array([flux for flux in fluxes if flux and flux > 0], dtype=float)
    if values.size < 3 or float(np.mean(values)) <= 0:
        return None
    return float(np.std(values) / np.mean(values)), float(np.mean(values))


def recompute_period(time_days: np.ndarray, flux: np.ndarray, minimum: float, maximum: float) -> float:
    """Find the strongest Lomb-Scargle period in a range.

    Parameters
    ----------
    time_days : `numpy.ndarray`
        The measurement times, in days.
    flux : `numpy.ndarray`
        The brightness.
    minimum, maximum : `float`
        The shortest and longest period to try, in days.

    Returns
    -------
    period : `float`
        The period of the strongest peak, in days.
    """
    frequency, power = LombScargle(time_days, flux).autopower(
        minimum_frequency=1.0 / maximum, maximum_frequency=1.0 / minimum, samples_per_peak=10
    )
    return float(1.0 / frequency[int(np.argmax(power))])


def check_stacks(connection: sqlite3.Connection) -> list[Comparison]:
    """Recompute each stack's headline numbers from its file.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the catalog database.

    Returns
    -------
    comparisons : `list` [`Comparison`]
        The zero fraction, saturated fraction, and two star-width checks.
    """
    zero_check = Comparison("stack: share of zero pixels")
    saturation_check = Comparison("stack: share of saturated pixels")
    reproduction_check = Comparison("stack: star width, pipeline's method re-run on the file (px)")
    fwhm_check = Comparison("stack: star width, independent method (px)")
    ratios: list[tuple[str, float, float]] = []
    combined_skipped = 0
    for target_id, data_json in connection.execute("SELECT id, data_json FROM targets"):
        document = json.loads(data_json)
        stacking = document.get("stacking") or {}
        metrics = (stacking.get("qualitySummary") or {}).get("stackingMetrics") or {}
        path = stacking.get("stackedImage") or ""
        if not metrics or not path or not os.path.exists(path):
            continue
        try:
            with fits.open(path, memmap=False) as hdul:
                data = np.asarray(hdul[0].data)
        except OSError:
            continue
        level = ((stacking.get("qualitySummary") or {}).get("cameraProfile") or {}).get(
            "saturationThresholdAdu"
        )
        zero, saturated = pixel_fractions(data, level)
        if metrics.get("zeroPixelFraction") is not None:
            stored = float(metrics["zeroPixelFraction"])
            zero_check.record(target_id, stored, zero, abs(stored - zero) <= FRACTION_TOLERANCE)
        if metrics.get("saturatedPixelFraction") is not None and saturated is not None and level:
            stored = float(metrics["saturatedPixelFraction"])
            saturation_check.record(
                target_id, stored, saturated, abs(stored - saturated) <= FRACTION_TOLERANCE
            )
        stored_width = metrics.get("stackedFwhmPx")
        if not stored_width:
            continue
        stored_width = float(stored_width)
        # A stack combined from several exposure lengths has its width measured
        # away from the bright cores the combine patched, so re-running the
        # method on the whole file is not like for like; leave it out of the
        # re-run and the scale-factor checks.
        if len(metrics.get("exposureGroups") or []) > 1:
            combined_skipped += 1
            continue
        pipeline_width = measure_image_fwhm(path)
        if pipeline_width:
            reproduction_check.record(
                target_id,
                stored_width,
                pipeline_width,
                abs(stored_width - pipeline_width) <= FWHM_REPRODUCTION_TOLERANCE * stored_width,
            )
        plane = data[0] if data.ndim == 3 else data
        independent = independent_fwhm(plane)
        if independent:
            ratios.append((target_id, stored_width, independent))
    reproduction_check.note = f"{combined_skipped} stack(s) combined from several exposure lengths left out"
    if ratios:
        median_ratio = float(np.median([stored / independent for _target, stored, independent in ratios]))
        fwhm_check.note = (
            f"median stored/independent width ratio {median_ratio:.2f}; "
            f"{combined_skipped} combined-exposure stack(s) left out"
        )
        for target_id, stored_width, independent in ratios:
            expected = independent * median_ratio
            fwhm_check.record(
                target_id,
                stored_width,
                expected,
                abs(stored_width - expected) <= FWHM_SCALE_SPREAD_TOLERANCE * expected,
            )
    return [zero_check, saturation_check, reproduction_check, fwhm_check]


def check_light_curves(connection: sqlite3.Connection) -> list[Comparison]:
    """Recompute each star's CV, mean brightness and best period.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the catalog database.

    Returns
    -------
    comparisons : `list` [`Comparison`]
        The CV, mean brightness and period comparisons.
    """
    cv_check = Comparison("light curve: coefficient of variation")
    mean_check = Comparison("light curve: mean brightness")
    period_check = Comparison("light curve: best period of a detected or possible cycle")
    query = (
        """SELECT id, json_extract(data_json, '$.photometry') FROM stellar_objects WHERE has_photometry = 1"""
    )
    for star_id, photometry_json in connection.execute(query):
        photometry = json.loads(photometry_json) if photometry_json else {}
        fluxes = photometry.get("fluxesDetrended") or photometry.get("fluxesNormalized") or []
        recomputed = recompute_cv(fluxes)
        stored_cv = photometry.get("coefficientOfVariation")
        if recomputed is not None and stored_cv is not None:
            cv_check.record(
                star_id,
                float(stored_cv),
                recomputed[0],
                abs(float(stored_cv) - recomputed[0]) <= CV_RELATIVE_TOLERANCE * max(float(stored_cv), 1e-9),
            )
            stored_mean = photometry.get("meanFlux")
            if stored_mean:
                mean_check.record(
                    star_id,
                    float(stored_mean),
                    recomputed[1],
                    abs(float(stored_mean) - recomputed[1])
                    <= CV_RELATIVE_TOLERANCE * abs(float(stored_mean)),
                )
        periodogram = photometry.get("periodogram") or {}
        if periodogram.get("verdict") in ("detected", "possible") and periodogram.get("bestPeriodDays"):
            stamps = photometry.get("timestamps") or []
            count = min(len(stamps), len(fluxes))
            if count >= 8:
                from datetime import datetime

                times = np.array([
                    datetime.fromisoformat(stamp).timestamp() / 86400.0 for stamp in stamps[:count]
                ])
                times -= times.min()
                span = float(times.max())
                stored_period = float(periodogram["bestPeriodDays"])
                minimum = float(periodogram.get("searchedMinPeriodDays") or stored_period / 2)
                maximum = float(periodogram.get("searchedMaxPeriodDays") or stored_period * 2)
                fresh = recompute_period(times, np.array(fluxes[:count], dtype=float), minimum, maximum)
                period_check.record(
                    star_id,
                    stored_period,
                    fresh,
                    abs(1.0 / stored_period - 1.0 / fresh) <= 2.0 / max(span, 1e-9),
                )
    return [cv_check, mean_check, period_check]


def format_report(comparisons: Sequence[Comparison], maximum_listed: int = 8) -> str:
    """Write the comparisons as a report.

    Parameters
    ----------
    comparisons : `Sequence` [`Comparison`]
        The results of the checks.
    maximum_listed : `int`, optional
        How many disagreements to list for each quantity.

    Returns
    -------
    text : `str`
        The report.
    """
    lines = []
    for comparison in comparisons:
        lines.append(
            f"{comparison.name}: {comparison.compared} compared, {len(comparison.disagreements)} disagree"
            + (f" ({comparison.note})" if comparison.note else "")
        )
        lines.extend(f"    {entry}" for entry in comparison.disagreements[:maximum_listed])
        if len(comparison.disagreements) > maximum_listed:
            lines.append(f"    ... and {len(comparison.disagreements) - maximum_listed} more")
    return "\n".join(lines)


def main() -> int:
    """Run the recomputation from the command line.

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``1`` if the catalog database cannot be found.
    """
    database = os.path.join(str(Astrometrics().config.get_library_path()), "astrometrics.db")
    if not os.path.exists(database):
        print(f"No catalog database at {database}.")
        return 1
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        comparisons = [*check_stacks(connection), *check_light_curves(connection)]
    finally:
        connection.close()
    print(format_report(comparisons))
    return 0


if __name__ == "__main__":
    sys.exit(main())
