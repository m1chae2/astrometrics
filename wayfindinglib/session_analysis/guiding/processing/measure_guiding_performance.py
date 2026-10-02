"""Purpose: Measure what a night's guiding data shows.

Description: Turns the night's guide samples into the numbers that describe
how well the mount tracked: the guiding error, what that error does to the
stars, and how much the guider had to move the mount along declination.

The guiding error is a robust one (a median absolute deviation), so a few
excursions, such as a lock on the wrong star, cannot distort it. The
excursions are counted by pre-processing and left out here; the ordinary
root-mean-square including them is reported beside it, to show how much they
matter.

The net declination correction is reported as what it is: how far the
guider moved the mount. It is not turned into a polar alignment estimate.
Tested on this observatory's real nights, the per-run values change sign
between runs at nearly the same hour angle, which a polar misalignment cannot
do, so the corrections are dominated by other effects.
"""

import bisect
import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from wayfindinglib.analytics.performance_envelope import (
    SIDEREAL_RATE_ARCSEC_PER_SECOND,
    SIGMA_TO_FWHM,
    robust_median_and_spread,
)
from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.models.session.session_quality import GuidingPerformance, GuidingRunPerformance
from wayfindinglib.session_analysis.guiding.processing.measure_exposure_feasibility import (
    measure_exposure_feasibility,
)

MINIMUM_RUN_SECONDS_FOR_CORRECTION_RATE = 600.0
"""Shortest guiding run whose net declination correction is used.

A mount's periodic error (the worm gear's once-per-turn error) repeats about
every 8 to 10 minutes on mounts of this class, and it moves the star in
declination too. A rate measured over less than one period mixes that error
in. This is a design estimate, not a measurement of this mount.
"""

MINIMUM_SAMPLES_PER_RUN = 20
"""Fewest samples a run needs to get its own guiding error.

With fewer, a robust spread is not stable.
"""


def _per_axis_error(samples: Sequence[dict[str, Any]]) -> tuple[float | None, float | None, float | None]:
    """Measure the robust guiding error of some samples.

    Returns
    -------
    rms_ra : `float` or `None`
        Robust error along RA, in arcseconds.
    rms_dec : `float` or `None`
        Robust error along Dec, in arcseconds.
    rms_per_axis : `float` or `None`
        The two combined per axis. `None` for all three if there are fewer
        than two samples.
    """
    if len(samples) < 2:
        return None, None, None
    _, rms_ra = robust_median_and_spread([sample["dra"] for sample in samples])
    _, rms_dec = robust_median_and_spread([sample["ddec"] for sample in samples])
    return rms_ra, rms_dec, math.sqrt((rms_ra**2 + rms_dec**2) / 2.0)


def _net_dec_correction_arcsec_per_minute(
    run: GuidingRunSummary, run_samples: Sequence[dict[str, Any]]
) -> float | None:
    """Find how far the guider moved the mount along declination, per minute.

    Adds up the declination corrections the guider issued (each a signed
    length in milliseconds), multiplies by the mount's calibrated declination
    speed, and divides by the run's length. A calibrated speed that is not
    possible (not positive, or above the sidereal rate) makes the conversion
    meaningless, so such a run is skipped.

    Returns
    -------
    rate : `float` or `None`
        Net correction in arcseconds per minute, positive when the guider
        moved the mount north. `None` if the run is too short, its calibrated
        speed is unknown or impossible, or the guider issued no declination
        corrections.
    """
    duration = run.duration_seconds
    if duration is None or duration < MINIMUM_RUN_SECONDS_FOR_CORRECTION_RATE:
        return None
    speed = run.dec_rate_arcsec_per_second
    if speed is None or not 0.0 < speed <= SIDEREAL_RATE_ARCSEC_PER_SECOND:
        return None
    pulses_ms = [sample["pulse_dec"] for sample in run_samples]
    if not any(pulses_ms):
        return None
    return sum(pulses_ms) / 1000.0 * speed / (duration / 60.0)


def measure_guiding_performance(
    samples: Sequence[dict[str, Any]],
    runs: Sequence[GuidingRunSummary],
    envelope: PerformanceEnvelope | None,
    limits_equipment_match: str,
    exposure_lengths_seconds: Sequence[float] = (),
) -> GuidingPerformance:
    """Measure the guiding error and the declination drift for one night.

    Parameters
    ----------
    samples : `Sequence` [`dict`]
        The night's measured guide samples (``timestamp``, ``dra``, ``ddec``
        and ``pulse_dec``; errors in arcseconds, pulses in signed
        milliseconds).
    runs : `Sequence` [`GuidingRunSummary`]
        The night's guiding runs. They supply each run's calibrated speed
        and sky position.
    envelope : `PerformanceEnvelope` or `None`
        The equipment-derived limits. The excursion limit decides which
        samples are left out of the guiding error, and the measured star
        width gives the effect on star size.
    limits_equipment_match : `str`
        ``"exact"``, ``"guide_optics_only"`` or ``"none"``. With ``"none"``
        no limit is applied, so no sample is treated as an excursion.
    exposure_lengths_seconds : `Sequence` [`float`], optional
        The exposure lengths the equipment is used with. For each, the result
        says how often guiding would have stayed clean for a whole exposure.

    Returns
    -------
    performance : `GuidingPerformance`
        The guiding error, its effect on star width, and the net declination
        correction rate, plus a per-run breakdown.
    """
    applies = envelope is not None and limits_equipment_match != "none"
    excursion_limit = envelope.value("guide_excursion_limit") if applies else None

    def is_excursion(sample: dict[str, Any]) -> bool:
        """Say whether a sample's error is an event, not noise.

        Returns
        -------
        excursion : `bool`
            `True` if the total error exceeds the excursion limit.
        """
        return excursion_limit is not None and math.hypot(sample["dra"], sample["ddec"]) > excursion_limit

    kept = [sample for sample in samples if not is_excursion(sample)]
    rms_ra, rms_dec, rms_per_axis = _per_axis_error(kept)
    rms_including = (
        math.sqrt(float(np.mean([(s["dra"] ** 2 + s["ddec"] ** 2) / 2.0 for s in samples])))
        if samples
        else None
    )

    widening = None
    if applies and rms_per_axis is not None:
        star_width = envelope.thresholds["guiding_rms_limit"].inputs.get("fwhm_arcsec")
        if isinstance(star_width, float) and star_width > 0.0:
            widening = math.hypot(1.0, SIGMA_TO_FWHM * rms_per_axis / star_width) - 1.0

    starts = [run.started_at for run in runs]
    samples_by_run: dict[int, list[dict[str, Any]]] = {index: [] for index in range(len(runs))}
    for sample in kept:
        index = bisect.bisect_right(starts, sample["timestamp"]) - 1
        if index >= 0:
            samples_by_run[index].append(sample)

    run_results = []
    weighted_rates: list[tuple[float, float]] = []
    for index, run in enumerate(runs):
        run_samples = samples_by_run[index]
        run_rms = None
        if len(run_samples) >= MINIMUM_SAMPLES_PER_RUN:
            _, _, run_rms = _per_axis_error(run_samples)
        rate = _net_dec_correction_arcsec_per_minute(run, run_samples)
        if rate is not None and run.duration_seconds is not None:
            weighted_rates.append((rate, run.duration_seconds))
        run_results.append(
            GuidingRunPerformance(
                run_id=run.id,
                started_at=run.started_at,
                duration_seconds=run.duration_seconds,
                samples=len(run_samples),
                rms_per_axis_arcsec=run_rms,
                lost_fraction=run.lost_fraction,
                net_dec_correction_arcsec_per_minute=rate,
                altitude_degrees=run.altitude_degrees,
                azimuth_degrees=run.azimuth_degrees,
                declination_degrees=run.declination_degrees,
                pier_side=run.pier_side,
            )
        )

    net_rate = None
    if weighted_rates:
        total_seconds = sum(seconds for _, seconds in weighted_rates)
        net_rate = sum(rate * seconds for rate, seconds in weighted_rates) / total_seconds

    feasibility, longest_reliable = measure_exposure_feasibility(
        samples, exposure_lengths_seconds, envelope, limits_equipment_match, runs
    )
    return GuidingPerformance(
        rms_ra_arcsec=rms_ra,
        rms_dec_arcsec=rms_dec,
        rms_per_axis_arcsec=rms_per_axis,
        rms_including_excursions_arcsec=rms_including,
        expected_star_widening_fraction=widening,
        net_dec_correction_arcsec_per_minute=net_rate,
        runs=run_results,
        exposure_feasibility=feasibility,
        longest_reliable_exposure_seconds=longest_reliable,
    )
