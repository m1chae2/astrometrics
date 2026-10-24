"""Find the target's amplitude, with its source, and store it.

The photometry run is judged against the size of the change the target is
known to make. This module finds that size from two places and keeps them
apart (see `processing/target_amplitude.py`): the target star's own folded
light curve, when its cycle is covered, and the VSX catalog's range, which is
labeled as a catalog value.

Only a target that is a single star gets an amplitude. The target's kind is
read from its name (`Target.object_type`); a cluster, galaxy, nebula, solar
system body or comet is ``not_measured`` with the reason. The target's star is
found by its catalog identity (`find_star_named`): the identified star whose
id or name equals the target's id or common name. How far that star lies from
the target's position is a cross-check only: farther than
`MAXIMUM_TARGET_DISTANCE_ARCSEC` means the match is not trusted and nothing is
stored. The light-curve fold uses the same candidates as the automatic period
search (stars with enough points, `can_run_period_search`), so the period it
folds on is the period search's own result for that star. The period to fold
on is the star's own periodogram result when the search called it detected
or possible, else the period VSX lists. The period's source, with the verdict
label when it is a "possible" period, is stored with the amplitude.

Nothing here says whether the light curve is good enough. That call belongs to
the observing side (``wayfindinglib``).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import TargetObjectType
from astrometricslib.pipelines.photometry.batch import find_star_named, separation_arcsec
from astrometricslib.pipelines.photometry.metrics import (
    PHOTOMETRY_METRICS,
    PHOTOMETRY_TARGET_CATALOG_AMPLITUDE_MAG,
    PHOTOMETRY_TARGET_LIGHT_CURVE_AMPLITUDE_MAG,
    PHOTOMETRY_TARGET_PHASE_COVERAGE_FRACTION,
)
from astrometricslib.pipelines.photometry.processing.periodicity_search import (
    VERDICT_DETECTED,
    VERDICT_POSSIBLE,
)
from astrometricslib.pipelines.photometry.processing.target_amplitude import (
    MAXIMUM_TARGET_DISTANCE_ARCSEC,
    FoldedAmplitude,
    folded_amplitude,
)
from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_and_record_measurements,
    build_measurement,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

_SPEC_BY_NAME = {spec.name: spec for spec in PHOTOMETRY_METRICS}


@dataclass(frozen=True)
class TargetAmplitudeInputs:
    """What was found about the target's amplitude.

    Where a part is missing, the reason says why.

    Parameters
    ----------
    star_id : `str`
        The target star's id. Empty when no target star was found.
    separation_arcsec : `float` or `None`
        How far that star lies from the target's position, a cross-check
        that is stored in the provenance.
    light_curve : `FoldedAmplitude` or `None`
        The folded light curve's result. `None` when no fold was tried.
    light_curve_reason : `str`
        Why no fold was tried, or why it gave no amplitude.
    period_days : `float` or `None`
        The period the fold used.
    period_source : `str`
        Where that period came from, in words.
    catalog_amplitude_mag : `float` or `None`
        The VSX range, in magnitudes.
    catalog_reason : `str`
        Why there is no catalog amplitude. Empty when there is one.
    catalog_band : `str`
        The band VSX names for the range.
    """

    star_id: str = ""
    separation_arcsec: float | None = None
    light_curve: FoldedAmplitude | None = None
    light_curve_reason: str = "no target star was looked for"
    period_days: float | None = None
    period_source: str = ""
    catalog_amplitude_mag: float | None = None
    catalog_reason: str = "no target star was looked for"
    catalog_band: str = ""


def _saved_catalog_row(catalog_access: Any, star_id: str) -> StellarObject | None:
    """Load the saved row of one star, which holds its VSX entry.

    Parameters
    ----------
    catalog_access : `CatalogAccess` or `None`
        Loads the saved stars.
    star_id : `str`
        The star's id.

    Returns
    -------
    row : `StellarObject` or `None`
        The saved row, or `None` when it is missing or cannot be read.
    """
    if catalog_access is None:
        return None
    try:
        rows = list(catalog_access.get_by_ids("stellar_catalog", [star_id]))
    except (AstrometricsError, *DATA_ERRORS):
        logger.warning("Could not load the saved row of %s for its VSX entry.", star_id, exc_info=True)
        return None
    return rows[0] if rows else None


def target_amplitude_inputs(
    stellar_objects: Sequence[StellarObject],
    target: Any,
    center_ra: float | None,
    center_dec: float | None,
    catalog_access: Any,
) -> TargetAmplitudeInputs:
    """Find the target star and its amplitude from the light curve and VSX.

    Parameters
    ----------
    stellar_objects : `Sequence` [`StellarObject`]
        The run's stars, with their light curves and period searches.
    target : `Target`
        The run's target: its id, common name and kind.
    center_ra, center_dec : `float` or `None`
        The target's position in degrees, for the distance cross-check.
    catalog_access : `CatalogAccess` or `None`
        Loads the saved stars (for the VSX entry).

    Returns
    -------
    inputs : `TargetAmplitudeInputs`
        Both amplitudes, or the reason for each gap.
    """
    if target.object_type is not TargetObjectType.STAR:
        reason = (
            f"the target {target.id!r} is not a single star (read from its name as a "
            f"{target.object_type.value} object), so it has no single amplitude"
        )
        return TargetAmplitudeInputs(light_curve_reason=reason, catalog_reason=reason)
    star = find_star_named(list(stellar_objects), (target.id, target.common_name))
    if star is None:
        reason = f"no catalog-identified star in the run carries the target's name {target.id!r}"
        return TargetAmplitudeInputs(light_curve_reason=reason, catalog_reason=reason)
    distance: float | None = None
    if center_ra is not None and center_dec is not None:
        distance = separation_arcsec(star, center_ra, center_dec)
        if distance > MAXIMUM_TARGET_DISTANCE_ARCSEC:
            reason = (
                f"the star named {star.id!r} lies {distance:.0f} arcsec from the target's position, "
                f"farther than {MAXIMUM_TARGET_DISTANCE_ARCSEC:.0f}, so the match is not trusted"
            )
            return TargetAmplitudeInputs(light_curve_reason=reason, catalog_reason=reason)

    saved = _saved_catalog_row(catalog_access, star.id)
    catalog_amplitude = None if saved is None else saved.vsx_amplitude_mag
    if catalog_amplitude is not None:
        catalog_reason = ""
    elif saved is None:
        catalog_reason = "the target star's saved row could not be loaded"
    elif not saved.vsx_variability_type:
        catalog_reason = "VSX was never asked about the target star"
    elif not saved.vsx_range_checked:
        catalog_reason = "saved before VSX ranges were kept; run backfill with --refresh-vsx-ranges"
    else:
        catalog_reason = "VSX lists no brightness range for the target star"
    catalog_band = "" if saved is None else saved.vsx_magnitude_band

    if not star.can_run_period_search:
        return TargetAmplitudeInputs(
            star_id=star.id,
            separation_arcsec=distance,
            light_curve_reason="the target star has too few measurements for a period search or a fold",
            catalog_amplitude_mag=catalog_amplitude,
            catalog_reason=catalog_reason,
            catalog_band=catalog_band,
        )
    periodogram = star.photometry.periodogram if star.photometry is not None else None
    if periodogram is not None and periodogram.verdict in (VERDICT_DETECTED, VERDICT_POSSIBLE):
        period_days: float | None = periodogram.best_period_days
        label = "possible, not confirmed" if periodogram.verdict == VERDICT_POSSIBLE else periodogram.verdict
        period_source = f"the target star's own periodogram ({label})"
    elif saved is not None and saved.vsx_period_days is not None:
        period_days = saved.vsx_period_days
        period_source = "the VSX period"
    else:
        period_days, period_source = None, ""
    common = {
        "star_id": star.id,
        "separation_arcsec": distance,
        "catalog_amplitude_mag": catalog_amplitude,
        "catalog_reason": catalog_reason,
        "catalog_band": catalog_band,
    }
    if period_days is None:
        return TargetAmplitudeInputs(
            light_curve=None,
            light_curve_reason="neither the periodogram nor VSX gave a period to fold on",
            **common,
        )
    try:
        time_days, flux = VariabilityAnalyzer.light_curve_arrays(star)
        folded = folded_amplitude(np.asarray(time_days), np.asarray(flux), period_days)
    except (AstrometricsError, *DATA_ERRORS) as fold_error:
        return TargetAmplitudeInputs(
            light_curve_reason=f"the light curve could not be folded: {fold_error}",
            period_days=period_days,
            period_source=period_source,
            **common,
        )
    return TargetAmplitudeInputs(
        light_curve=folded,
        light_curve_reason=folded.reason,
        period_days=period_days,
        period_source=period_source,
        **common,
    )


def target_amplitude_measurements(
    inputs: TargetAmplitudeInputs, context: PipelineRunContext
) -> list[QualityMeasurement]:
    """Build the three amplitude measurements of one run.

    Parameters
    ----------
    inputs : `TargetAmplitudeInputs`
        What was found.
    context : `PipelineRunContext`
        The run's shared facts.

    Returns
    -------
    measurements : `list` [`QualityMeasurement`]
        The light-curve amplitude, the phase coverage of the fold and the
        catalog amplitude, each ``not_measured`` with its reason when it
        could not be found.
    """
    subject = context.run_subject()
    folded = inputs.light_curve
    distance_note = (
        "" if inputs.separation_arcsec is None else f"; {inputs.separation_arcsec:.1f} arcsec from the target"
    )
    amplitude_note = (
        f"target {inputs.star_id}{distance_note}; period {inputs.period_days:.6g} d "
        f"from {inputs.period_source}"
        if inputs.period_days is not None
        else ""
    )
    return [
        build_measurement(
            _SPEC_BY_NAME[PHOTOMETRY_TARGET_LIGHT_CURVE_AMPLITUDE_MAG],
            None if folded is None else folded.amplitude_mag,
            context,
            subject,
            reason=inputs.light_curve_reason,
            uncertainty=None if folded is None else folded.error_mag,
            sample_count=None if folded is None else folded.point_count,
            provenance=amplitude_note,
        ),
        build_measurement(
            _SPEC_BY_NAME[PHOTOMETRY_TARGET_PHASE_COVERAGE_FRACTION],
            None if folded is None else folded.filled_fraction,
            context,
            subject,
            reason=inputs.light_curve_reason,
            sample_count=None if folded is None else folded.point_count,
            provenance=amplitude_note,
        ),
        build_measurement(
            _SPEC_BY_NAME[PHOTOMETRY_TARGET_CATALOG_AMPLITUDE_MAG],
            inputs.catalog_amplitude_mag,
            context,
            subject,
            reason=inputs.catalog_reason,
            provenance=(
                f"VSX, band {inputs.catalog_band or 'not named'}; target {inputs.star_id}{distance_note}"
            ),
        ),
    ]


def record_target_amplitude_quality(
    stellar_objects: Sequence[StellarObject],
    target: Any,
    center_ra: float | None,
    center_dec: float | None,
    catalog_access: Any,
    context: PipelineRunContext,
    *,
    config: object = None,
) -> int:
    """Find and store the target's amplitude; no failure here fails the run.

    Parameters
    ----------
    stellar_objects : `Sequence` [`StellarObject`]
        The run's stars.
    target : `Target`
        The run's target.
    center_ra, center_dec : `float` or `None`
        The target's position in degrees.
    catalog_access : `CatalogAccess` or `None`
        Loads the saved stars.
    context : `PipelineRunContext`
        The run's shared facts.
    config : `AppConfiguration`, optional
        Where the quality store lives. The process-wide one if omitted.

    Returns
    -------
    new_count : `int`
        How many rows were new. It is 0 when anything went wrong (logged).
    """
    return build_and_record_measurements(
        lambda: target_amplitude_measurements(
            target_amplitude_inputs(stellar_objects, target, center_ra, center_dec, catalog_access), context
        ),
        f"photometry target amplitude on {context.target_id}",
        config=config,
    )
