"""Purpose: Judge whether a night's guiding data is good.

Description: Looks at how the guiding data was gathered, before anyone
interprets the guiding error. A night with many lost frames, a weak guide
star or an impossible calibration produces error numbers that say little
about the mount, so these problems are reported first.

Each check compares the night with a limit worked out from the equipment in
use (see `wayfindinglib/analytics/performance_envelope.py`). A check whose
limit cannot yet be worked out, because the equipment has too little history,
reports `None`: the night is neither passed nor failed.
"""

import math
import statistics
from collections.abc import Sequence
from itertools import pairwise
from typing import Any

from wayfindinglib.analytics.performance_envelope import (
    MINIMUM_SAMPLES_PER_SESSION,
    SIDEREAL_RATE_ARCSEC_PER_SECOND,
)
from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.models.session.session_quality import GuidingInputQuality

_MAXIMUM_CADENCE_GAP_SECONDS = 60.0
"""A gap between samples longer than this is a pause, not the guide cadence."""


def _limit(envelope: PerformanceEnvelope | None, name: str, limits_equipment_match: str) -> float | None:
    """Read one limit, or `None` if none applies to this night's equipment.

    Returns
    -------
    limit : `float` or `None`
        The limit's value, or `None` if the night's equipment does not match
        the equipment the limits were worked out for, or the limit could not
        be worked out.
    """
    if envelope is None or limits_equipment_match == "none":
        return None
    return envelope.value(name)


def _calibration_problems(runs: Sequence[GuidingRunSummary], speed_limit: float) -> list[str]:
    """Find runs whose calibrated mount speed cannot be right.

    A mount's guide speed is a fraction of the sidereal rate. A calibration
    that measured a faster speed, or none at all, went wrong (for example
    cloud or a lost star during the calibration).

    Returns
    -------
    problems : `list` [`str`]
        One sentence for each run and axis with an impossible speed.
    """
    problems = []
    for run in runs:
        if run.samples_stored == 0:
            continue
        for axis, rate in (("RA", run.ra_rate_arcsec_per_second), ("Dec", run.dec_rate_arcsec_per_second)):
            if rate is None:
                continue
            if rate <= 0.0:
                problems.append(
                    f"{run.id}: the {axis} guide speed is {rate:.1f} arcsec/s, which is not positive."
                )
            elif rate > speed_limit:
                problems.append(
                    f"{run.id}: the {axis} guide speed is {rate:.1f} arcsec/s, above the "
                    f"sidereal rate of {speed_limit:.1f} arcsec/s."
                )
    return problems


def assess_guiding_input_quality(
    samples: Sequence[dict[str, Any]],
    runs: Sequence[GuidingRunSummary],
    envelope: PerformanceEnvelope | None,
    limits_equipment_match: str,
    guide_scale_matches_configuration: bool | None,
) -> GuidingInputQuality:
    """Judge how good one night's guiding data is.

    Parameters
    ----------
    samples : `Sequence` [`dict`]
        The night's measured guide samples (``timestamp``, ``dra``,
        ``ddec`` and ``snr``; errors in arcseconds).
    runs : `Sequence` [`GuidingRunSummary`]
        The night's guiding runs, which record the frames that were lost.
    envelope : `PerformanceEnvelope` or `None`
        The equipment-derived limits.
    limits_equipment_match : `str`
        ``"exact"``, ``"guide_optics_only"`` or ``"none"``: how well the
        night's equipment matches the equipment the limits are for. With
        ``"none"`` no limit is applied.
    guide_scale_matches_configuration : `bool` or `None`
        Whether the plate scale in the guide log agrees with the configured
        guide optics.

    Returns
    -------
    input_quality : `GuidingInputQuality`
        Coverage, lost frames, excursions, guide-star signal, calibration
        problems and the verdict on each against its limit.
    """
    frames_total = sum(run.frames_total for run in runs)
    frames_lost = sum(run.frames_lost for run in runs)
    lost_fraction = frames_lost / frames_total if frames_total else None
    lost_limit = _limit(envelope, "guide_lost_fraction_high_limit", limits_equipment_match)

    excursion_limit = _limit(envelope, "guide_excursion_limit", limits_equipment_match)
    excursion_count = excursion_fraction = None
    if excursion_limit is not None and samples:
        excursion_count = sum(
            1 for sample in samples if math.hypot(sample["dra"], sample["ddec"]) > excursion_limit
        )
        excursion_fraction = excursion_count / len(samples)
    excursion_fraction_limit = _limit(envelope, "guide_excursion_fraction_high_limit", limits_equipment_match)

    snr_values = [sample["snr"] for sample in samples if sample.get("snr") is not None]
    median_snr = statistics.median(snr_values) if snr_values else None
    snr_limit = _limit(envelope, "guide_snr_low_limit", limits_equipment_match)

    star_mass_values = [sample["star_mass"] for sample in samples if sample.get("star_mass")]
    median_star_mass = statistics.median(star_mass_values) if star_mass_values else None
    star_mass_limit = _limit(envelope, "guide_star_mass_low_limit", limits_equipment_match)
    typical_star_mass = None
    typical_cadence = None
    if envelope is not None and limits_equipment_match != "none":
        mass_threshold = envelope.thresholds.get("guide_star_mass_low_limit")
        median_input = mass_threshold.inputs.get("median") if mass_threshold else None
        typical_star_mass = float(median_input) if isinstance(median_input, int | float) else None
        exposure_threshold = envelope.thresholds.get("minimum_star_measurement_exposure")
        cadence_input = exposure_threshold.inputs.get("guide_cadence_seconds") if exposure_threshold else None
        typical_cadence = float(cadence_input) if isinstance(cadence_input, int | float) else None

    times = sorted(sample["timestamp"] for sample in samples)
    gaps = [later - earlier for earlier, later in pairwise(times)]
    cadence_gaps = [gap for gap in gaps if gap < _MAXIMUM_CADENCE_GAP_SECONDS]

    speed_limit = _limit(envelope, "max_credible_guide_speed", limits_equipment_match)
    if speed_limit is None:
        speed_limit = SIDEREAL_RATE_ARCSEC_PER_SECOND

    return GuidingInputQuality(
        runs=len(runs),
        guided_seconds=sum(run.duration_seconds or 0.0 for run in runs),
        frames_total=frames_total,
        frames_lost=frames_lost,
        lost_fraction=lost_fraction,
        lost_fraction_limit=lost_limit,
        has_high_loss=None if lost_fraction is None or lost_limit is None else lost_fraction > lost_limit,
        samples_analyzed=len(samples),
        cadence_seconds=statistics.median(cadence_gaps) if cadence_gaps else None,
        excursion_count=excursion_count,
        excursion_fraction=excursion_fraction,
        excursion_fraction_limit=excursion_fraction_limit,
        has_frequent_excursions=(
            None
            if excursion_fraction is None or excursion_fraction_limit is None
            else excursion_fraction > excursion_fraction_limit
        ),
        median_snr=median_snr,
        snr_limit=snr_limit,
        has_low_signal=None if median_snr is None or snr_limit is None else median_snr < snr_limit,
        median_star_mass=median_star_mass,
        star_mass_limit=star_mass_limit,
        typical_star_mass=typical_star_mass,
        has_dim_star=(
            None
            if median_star_mass is None or star_mass_limit is None
            else median_star_mass < star_mass_limit
        ),
        typical_cadence_seconds=typical_cadence,
        calibration_problems=_calibration_problems(runs, speed_limit),
        guide_scale_matches_configuration=guide_scale_matches_configuration,
        has_enough_samples=len(samples) >= MINIMUM_SAMPLES_PER_SESSION,
        limits_equipment_match=limits_equipment_match,
    )
