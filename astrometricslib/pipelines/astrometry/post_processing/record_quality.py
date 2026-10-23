"""Builds and records the quality of one astrometry run.

Astrometry finds the stars in an image, works out where the image points
(a "plate solve") and names the stars by looking them up in online catalogs.
Five checks say whether the run can be trusted: were any stars found, did the
plate solve succeed, is the solution accurate, were enough stars matched to a
catalog, and did the catalog lookups work.

The residual check judges the RMS distance to Gaia stars when the run refit
the solution against Gaia, because that number does not depend on the plate
solver. Otherwise it judges the plate solver's own fit residual (how far the
fitted position of each star it matched sits from the reference star). When
the solver reported none, as the online solver does, the check falls back to
the catalog match separation (the distance from each detected star to the
nearest SIMBAD or Gaia star). The gate's detail says which number it used.

Each check is returned as a `GateResult`. The plate-solve gate is built by the
runner; the other four are built here. A catalog lookup that was
never attempted is ``not_checked``; it is not a pass.

The same module turns the run's numbers and gates into quality measurements
(`astrometry_run_measurements`) and stores them (`record_astrometry_quality`).
The gates and the summary stay as they are; the measurements are written next
to them.
"""

import logging
from collections.abc import Sequence

from astrometricslib.models.quality.gate_result import (
    GateResult,
    GateStatus,
    failed_gate,
    passed_gate,
    unchecked_gate,
)
from astrometricslib.models.quality.limit import LimitTier
from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.models.quality.metric_spec import MetricSpec
from astrometricslib.models.quality.quality_summary import AstrometryPipelineQualityMetrics
from astrometricslib.pipelines.astrometry.metrics import (
    ASTROMETRY_CATALOG_MATCH_SEPARATION_RMS_ARCSEC,
    ASTROMETRY_CATALOG_MATCHED_STAR_COUNT,
    ASTROMETRY_GAIA_FIT_RMS_ARCSEC,
    ASTROMETRY_GAIA_FIT_RMS_BEFORE_ARCSEC,
    ASTROMETRY_GAIA_FIT_STAR_COUNT,
    ASTROMETRY_GATE_ASTROMETRIC_RESIDUAL,
    ASTROMETRY_GATE_CATALOG_LOOKUP,
    ASTROMETRY_GATE_CATALOG_MATCHES,
    ASTROMETRY_GATE_PLATE_SOLVE,
    ASTROMETRY_GATE_SOURCE_DETECTION,
    ASTROMETRY_METRICS,
    ASTROMETRY_PLATE_SCALE_ARCSEC_PER_PIXEL,
    ASTROMETRY_PLATE_SOLVE_ATTEMPTS,
    ASTROMETRY_PLATE_SOLVE_FIT_RESIDUAL_RMS_ARCSEC,
    ASTROMETRY_PLATE_SOLVE_MATCHED_STAR_COUNT,
    ASTROMETRY_POSITION_ONLY_STAR_COUNT,
    ASTROMETRY_REMOTE_CATALOG_QUERIES_ATTEMPTED,
    ASTROMETRY_REMOTE_CATALOG_QUERIES_FAILED,
    ASTROMETRY_SIMBAD_MATCHED_COUNT,
    ASTROMETRY_SOURCES_DETECTED,
    ASTROMETRY_STAR_WIDTH_GAUSSIAN_PX,
    ASTROMETRY_UNRESOLVED_STAR_COUNT,
    ASTROMETRY_WCS_REFINEMENT_SHIFT_ARCSEC,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    GateLimitSource,
    PipelineRunContext,
    build_and_record_measurements,
    build_measurement,
    gate_measurement,
)

logger = logging.getLogger(__name__)

# The name the plate-solve check goes by in an astrometry run's gate record.
# The runner builds that gate; its name lives here so the record step can
# find it without importing the runner.
PLATE_SOLVE_GATE_NAME = "plate_solve"
SOURCE_DETECTION_GATE_NAME = "source_detection"
RESIDUAL_GATE_NAME = "astrometric_residual"
MATCHED_STARS_GATE_NAME = "catalog_matches"
CATALOG_LOOKUP_GATE_NAME = "catalog_lookup"

# How large the plate-solve residual may be, as a fraction of the stars' own
# width (FWHM). Centroid noise on a well-measured star is a few percent of its
# width, so a residual near half a width means the fit is dominated by
# something else: a wrong match, a bad distortion model or a wrong field.
# Partly measured (validation register AST-04). On the 8 saved solves whose
# stacked image carries a solved plate scale (2026-10-09) the residual was 0.14
# to 0.44 of the width (median 0.25), so none fires; it is there to catch a
# failed solve, not to rank good ones. Those 8 numbers are catalog match
# separations (see
# `AstrometryPipelineQualityMetrics.catalog_match_separation_rms_arcsec`), not
# plate-solver fit residuals. A fit residual is expected to be the smaller of
# the two, so this limit is the more lenient one for it. It has not been
# re-measured on fit residuals.
MAXIMUM_RESIDUAL_FRACTION_OF_FWHM = 0.5

# The fewest catalog-matched stars for the residual to mean anything. The
# residual is a root mean square over the matched stars; with a handful of
# them it is not a reliable measure of the fit. A design estimate. On the 42
# saved solves, 4 had fewer than 20 matches (4, 6, 11 and 19), and they are
# among the five with the largest residuals (5.8, 5.9, 9.0 and 7.1 arcsec).
MINIMUM_MATCHED_STARS = 20

# The share of catalog lookups that may fail before the star names cannot be
# trusted. A design estimate: the first failures usually come from one slow
# or unreachable service, and half failing means most stars went unnamed.
MAXIMUM_FAILED_LOOKUP_FRACTION = 0.5


def _residual_and_basis(metrics: AstrometryPipelineQualityMetrics) -> tuple[float | None, str]:
    """Pick the residual the gate judges and say where it came from.

    Parameters
    ----------
    metrics : `AstrometryPipelineQualityMetrics`
        What the run measured.

    Returns
    -------
    residual : `float` or `None`
        The RMS distance to the Gaia stars in arcseconds when the refit of
        the solution measured one (it does not depend on the solver).
        Otherwise the plate solver's fit residual when it reported one, then
        the catalog match separation RMS, or `None` when none was measured.
    basis : `str`
        A short phrase naming the number used, for the gate's detail.
    """
    if metrics.gaia_fit_rms_arcsec is not None and metrics.gaia_fit_star_count:
        return (
            metrics.gaia_fit_rms_arcsec,
            f"RMS against {metrics.gaia_fit_star_count} Gaia stars, independent of the plate solver",
        )
    fit_residual = metrics.plate_solve_fit_residual_rms_arcsec
    if fit_residual is not None:
        matched = metrics.plate_solve_matched_star_count
        stars = f" over {matched} matched stars" if matched is not None else ""
        return fit_residual, f"plate-solve fit residual RMS{stars}"
    return (
        metrics.catalog_match_separation_rms_arcsec,
        "catalog match separation RMS, because the plate solver reported no fit residual",
    )


def astrometry_run_gates(metrics: AstrometryPipelineQualityMetrics) -> list[GateResult]:
    """Build the source-detection and catalog-lookup gates.

    Parameters
    ----------
    metrics : `AstrometryPipelineQualityMetrics`
        What the run measured.

    Returns
    -------
    gates : `list` [`GateResult`]
        The ``catalog_matches``, ``astrometric_residual``, ``source_detection``
        and ``catalog_lookup`` gates.
    """
    gates: list[GateResult] = []
    matched = metrics.catalog_matched_star_count
    matches_source = f"at least {MINIMUM_MATCHED_STARS} catalog-matched stars (design estimate)"
    if not metrics.plate_solve_succeeded:
        gates.append(
            unchecked_gate(
                MATCHED_STARS_GATE_NAME, "the image was not solved, so no stars were matched", matches_source
            )
        )
    elif matched < MINIMUM_MATCHED_STARS:
        gates.append(
            failed_gate(
                MATCHED_STARS_GATE_NAME,
                f"only {matched} star(s) were matched to a catalog, too few for the fit to be reliable",
                float(matched),
                float(MINIMUM_MATCHED_STARS),
                matches_source,
            )
        )
    else:
        gates.append(
            passed_gate(MATCHED_STARS_GATE_NAME, float(matched), float(MINIMUM_MATCHED_STARS), matches_source)
        )

    residual_source = f"at most {MAXIMUM_RESIDUAL_FRACTION_OF_FWHM:g} of a star's width (design estimate)"
    residual, basis = _residual_and_basis(metrics)
    scale = metrics.plate_scale_arcsec_per_pixel
    fwhm = metrics.star_fwhm_px
    if residual is None or not scale or not fwhm:
        gates.append(
            unchecked_gate(
                RESIDUAL_GATE_NAME,
                "the residual, the plate scale or the star width was not measured, "
                "so the fit cannot be judged",
                residual_source,
            )
        )
    else:
        fraction = residual / (scale * fwhm)
        if fraction > MAXIMUM_RESIDUAL_FRACTION_OF_FWHM:
            gates.append(
                failed_gate(
                    RESIDUAL_GATE_NAME,
                    f"the plate solution misses its stars by {residual:.1f} arcsec ({basis}), "
                    f"{fraction:.0%} of a star's width, so it may be wrong",
                    fraction,
                    MAXIMUM_RESIDUAL_FRACTION_OF_FWHM,
                    residual_source,
                )
            )
        else:
            gates.append(
                passed_gate(
                    RESIDUAL_GATE_NAME,
                    fraction,
                    MAXIMUM_RESIDUAL_FRACTION_OF_FWHM,
                    residual_source,
                    f"{residual:.2f} arcsec ({basis})",
                )
            )

    if metrics.sources_detected <= 0:
        gates.append(
            failed_gate(
                SOURCE_DETECTION_GATE_NAME,
                "no stars were detected in the image",
                float(metrics.sources_detected),
                1.0,
            )
        )
    else:
        gates.append(passed_gate(SOURCE_DETECTION_GATE_NAME, float(metrics.sources_detected), 1.0))

    lookup_source = (
        f"under {MAXIMUM_FAILED_LOOKUP_FRACTION:.0%} of lookups failed and the circuit "
        "breaker did not trip (design estimate)"
    )
    attempted = metrics.remote_catalog_queries_attempted
    failed = metrics.remote_catalog_queries_failed
    if metrics.remote_catalog_circuit_breaker_tripped and attempted == 0:
        # The breaker was already open when the run began (it is shared by the
        # whole process), so no lookup was made. The run did not cause this and
        # did not check the catalogs.
        gates.append(
            unchecked_gate(
                CATALOG_LOOKUP_GATE_NAME,
                "the catalog service was already marked unreachable, so no lookup was made",
                lookup_source,
            )
        )
    elif metrics.remote_catalog_circuit_breaker_tripped:
        gates.append(
            failed_gate(
                CATALOG_LOOKUP_GATE_NAME,
                "catalog lookups kept failing, so the service was dropped and some stars are unnamed",
                float(failed),
                limit_source=lookup_source,
            )
        )
    elif attempted == 0:
        gates.append(
            unchecked_gate(CATALOG_LOOKUP_GATE_NAME, "no catalog lookup was attempted", lookup_source)
        )
    elif failed / attempted >= MAXIMUM_FAILED_LOOKUP_FRACTION:
        gates.append(
            failed_gate(
                CATALOG_LOOKUP_GATE_NAME,
                f"{failed} of {attempted} catalog lookup(s) failed; many stars may be unnamed",
                failed / attempted,
                MAXIMUM_FAILED_LOOKUP_FRACTION,
                lookup_source,
            )
        )
    else:
        gates.append(
            passed_gate(
                CATALOG_LOOKUP_GATE_NAME,
                failed / attempted,
                MAXIMUM_FAILED_LOOKUP_FRACTION,
                lookup_source,
                f"{failed} of {attempted} catalog lookup(s) failed",
            )
        )
    return gates


# Each count or measured value, as the summary's field name, with the metric
# that holds it. A field the run could not fill is `None` on the summary and
# becomes a ``not_measured`` record with a reason.
_VALUE_METRIC_BY_FIELD: dict[str, str] = {
    "sources_detected": ASTROMETRY_SOURCES_DETECTED,
    "simbad_matched_count": ASTROMETRY_SIMBAD_MATCHED_COUNT,
    "catalog_matched_star_count": ASTROMETRY_CATALOG_MATCHED_STAR_COUNT,
    "position_only_star_count": ASTROMETRY_POSITION_ONLY_STAR_COUNT,
    "unresolved_star_count": ASTROMETRY_UNRESOLVED_STAR_COUNT,
    "plate_solve_attempts": ASTROMETRY_PLATE_SOLVE_ATTEMPTS,
    "plate_solve_matched_star_count": ASTROMETRY_PLATE_SOLVE_MATCHED_STAR_COUNT,
    "gaia_fit_star_count": ASTROMETRY_GAIA_FIT_STAR_COUNT,
    "remote_catalog_queries_attempted": ASTROMETRY_REMOTE_CATALOG_QUERIES_ATTEMPTED,
    "remote_catalog_queries_failed": ASTROMETRY_REMOTE_CATALOG_QUERIES_FAILED,
    "catalog_match_separation_rms_arcsec": ASTROMETRY_CATALOG_MATCH_SEPARATION_RMS_ARCSEC,
    "plate_solve_fit_residual_rms_arcsec": ASTROMETRY_PLATE_SOLVE_FIT_RESIDUAL_RMS_ARCSEC,
    "gaia_fit_rms_arcsec": ASTROMETRY_GAIA_FIT_RMS_ARCSEC,
    "gaia_fit_rms_before_arcsec": ASTROMETRY_GAIA_FIT_RMS_BEFORE_ARCSEC,
    "wcs_refinement_shift_arcsec": ASTROMETRY_WCS_REFINEMENT_SHIFT_ARCSEC,
    "plate_scale_arcsec_per_pixel": ASTROMETRY_PLATE_SCALE_ARCSEC_PER_PIXEL,
    "star_fwhm_px": ASTROMETRY_STAR_WIDTH_GAUSSIAN_PX,
}

# The metrics that describe the run (what it did), not the stacked image.
# Every other value in the table above is read from the stack, so it is stored
# on the stack's subject. The gates flag the run and are stored on the run.
_RUN_LEVEL_METRICS: frozenset[str] = frozenset({
    ASTROMETRY_PLATE_SOLVE_ATTEMPTS,
    ASTROMETRY_REMOTE_CATALOG_QUERIES_ATTEMPTED,
    ASTROMETRY_REMOTE_CATALOG_QUERIES_FAILED,
})

# Why a value may be missing, shown on its ``not_measured`` record.
_NO_SOLVER_TABLE_REASON = "The plate solver gave no match table (an online solve, or a missing table)."
_MISSING_REASON_BY_FIELD: dict[str, str] = {
    "plate_solve_matched_star_count": _NO_SOLVER_TABLE_REASON,
    "gaia_fit_star_count": "The Gaia refit was not tried or had too few stars.",
    "catalog_match_separation_rms_arcsec": "No detected star was matched to a catalog star.",
    "plate_solve_fit_residual_rms_arcsec": _NO_SOLVER_TABLE_REASON,
    "gaia_fit_rms_arcsec": "The Gaia refit was not tried or had too few stars.",
    "gaia_fit_rms_before_arcsec": "The Gaia refit was not tried or had too few stars.",
    "wcs_refinement_shift_arcsec": "The Gaia refit was not tried or had too few stars.",
    "plate_scale_arcsec_per_pixel": "The image was not solved, so it has no pixel scale.",
    "star_fwhm_px": "No star in the image could be measured.",
}

# The metric that stands for each gate, and how its limit was obtained. The
# history fill (`scripts/backfill_quality_measurements.py`) reads this table
# too, so a limit is described in one place.
#
# The plate solve and the one-star minimum follow from geometry: sky
# coordinates need a solution and a solution needs a star. The other three
# limits are designed values that no one has measured as a limit, and the
# validation register lists them that way: the 20-star minimum
# (`MINIMUM_MATCHED_STARS`, AST-05), the residual share of a star's width
# (`MAXIMUM_RESIDUAL_FRACTION_OF_FWHM`, AST-04) and the failed-lookup share
# (`MAXIMUM_FAILED_LOOKUP_FRACTION`, AST-D09). AST-04 and AST-05 hold the
# partial evidence behind two of them; none claims the tier of a validated
# data set.
GATE_LIMIT_SOURCES: dict[str, GateLimitSource] = {
    PLATE_SOLVE_GATE_NAME: GateLimitSource(ASTROMETRY_GATE_PLATE_SOLVE, LimitTier.GEOMETRY),
    SOURCE_DETECTION_GATE_NAME: GateLimitSource(ASTROMETRY_GATE_SOURCE_DETECTION, LimitTier.GEOMETRY),
    MATCHED_STARS_GATE_NAME: GateLimitSource(
        ASTROMETRY_GATE_CATALOG_MATCHES, LimitTier.DESIGN_ESTIMATE, "AST-05"
    ),
    RESIDUAL_GATE_NAME: GateLimitSource(
        ASTROMETRY_GATE_ASTROMETRIC_RESIDUAL, LimitTier.DESIGN_ESTIMATE, "AST-04"
    ),
    CATALOG_LOOKUP_GATE_NAME: GateLimitSource(
        ASTROMETRY_GATE_CATALOG_LOOKUP, LimitTier.DESIGN_ESTIMATE, "AST-D09"
    ),
}

_SPEC_BY_NAME: dict[str, MetricSpec] = {spec.name: spec for spec in ASTROMETRY_METRICS}


def gate_for_measurement(
    gate: GateResult,
    *,
    plate_solve_succeeded: bool | None,
    remote_catalog_queries_attempted: int | float | None,
    remote_catalog_queries_failed: int | float | None,
) -> GateResult | None:
    """Adjust a gate so its number means one thing in every outcome.

    The plate-solve gate carries no number, and the lookup gate quotes the
    failed share when it passes and the failed count when the circuit breaker
    tripped. A stored metric has one unit and one meaning, so the measurement
    uses a steady number instead. The gate itself is not changed.

    The run's record step and the history fill both call this function, so a
    backfilled gate means what a live one does. The fill reads the numbers
    from a stored summary that may lack them, hence the optional arguments.

    Parameters
    ----------
    gate : `GateResult`
        The gate as recorded on the summary.
    plate_solve_succeeded : `bool` or `None`
        Whether the image was solved. `None` when unknown.
    remote_catalog_queries_attempted : `int` or `float` or `None`
        How many remote catalog lookups were made. `None` when unknown.
    remote_catalog_queries_failed : `int` or `float` or `None`
        How many of them failed. `None` when unknown.

    Returns
    -------
    gate : `GateResult` or `None`
        A copy for the measurement. The plate-solve gate carries 1 when the
        image was solved and 0 when it was not, with the limit 1. The lookup
        gate carries the failed share whenever a lookup was made. `None` when
        the steady number needs a fact that is unknown (only the history fill
        can meet that case).
    """
    if gate.name == PLATE_SOLVE_GATE_NAME:
        if plate_solve_succeeded is None:
            return None
        return gate.model_copy(
            update={
                "measured_value": 1.0 if plate_solve_succeeded else 0.0,
                "limit": 1.0,
                "limit_source": "sky coordinates need a plate solution",
            }
        )
    if gate.name == CATALOG_LOOKUP_GATE_NAME and gate.status is not GateStatus.NOT_CHECKED:
        if remote_catalog_queries_attempted is None or remote_catalog_queries_failed is None:
            return None
        if remote_catalog_queries_attempted > 0:
            return gate.model_copy(
                update={"measured_value": remote_catalog_queries_failed / remote_catalog_queries_attempted}
            )
    return gate


def astrometry_run_measurements(
    metrics: AstrometryPipelineQualityMetrics,
    gates: Sequence[GateResult],
    context: PipelineRunContext,
    stack_file_name: str | None,
) -> list[QualityMeasurement]:
    """Build every measurement the astrometry pipeline declares for one run.

    Parameters
    ----------
    metrics : `AstrometryPipelineQualityMetrics`
        What the run measured.
    gates : `Sequence` [`GateResult`]
        The run's gates: the plate-solve gate from the runner and the four
        from `astrometry_run_gates`.
    context : `PipelineRunContext`
        The run's shared facts.
    stack_file_name : `str` or `None`
        The analyzed stacked image's file name (not a mount path). The values
        read from that image are stored on its stack subject. When it is
        `None` those values are left out and a warning says so, because a
        stack value must not be filed under the run.

    Returns
    -------
    measurements : `list` [`QualityMeasurement`]
        One measurement for each declared metric. A value the run could not
        fill is ``not_measured`` with a reason. The values read from the
        stacked image are about the stack; the run's own counts and the gate
        metrics are about the run, and the gates carry their outcome and
        limit.
    """
    subject = context.run_subject()
    stack_subject = None if stack_file_name is None else context.stack_subject(stack_file_name)
    if stack_subject is None:
        logger.warning(
            "The astrometry run on %s has no stack file name, so its stacked-image values were not stored.",
            context.target_id,
        )
    measurements: list[QualityMeasurement] = []
    for field_name, metric_name in _VALUE_METRIC_BY_FIELD.items():
        value_subject = subject if metric_name in _RUN_LEVEL_METRICS else stack_subject
        if value_subject is None:
            continue
        value = getattr(metrics, field_name)
        measurements.append(
            build_measurement(
                _SPEC_BY_NAME[metric_name],
                None if value is None else float(value),
                context,
                value_subject,
                reason=_MISSING_REASON_BY_FIELD.get(field_name, f"The run did not report {field_name}."),
            )
        )

    gates_by_name = {gate.name: gate for gate in gates}
    for gate_name, limit_source in GATE_LIMIT_SOURCES.items():
        gate = gates_by_name.get(gate_name)
        if gate is None:
            continue
        spec = _SPEC_BY_NAME[limit_source.metric_name]
        adjusted = gate_for_measurement(
            gate,
            plate_solve_succeeded=metrics.plate_solve_succeeded,
            remote_catalog_queries_attempted=metrics.remote_catalog_queries_attempted,
            remote_catalog_queries_failed=metrics.remote_catalog_queries_failed,
        )
        measurements.append(
            gate_measurement(
                gate if adjusted is None else adjusted,
                spec,
                context,
                subject,
                limit_tier=limit_source.tier,
                register_entry=limit_source.register_entry,
            )
        )
    return measurements


def record_astrometry_quality(
    metrics: AstrometryPipelineQualityMetrics,
    gates: Sequence[GateResult],
    context: PipelineRunContext,
    stack_file_name: str | None,
    *,
    config: object = None,
) -> int:
    """Store the run's measurements; no failure here ever fails the run.

    Parameters
    ----------
    metrics : `AstrometryPipelineQualityMetrics`
        What the run measured.
    gates : `Sequence` [`GateResult`]
        The run's gates.
    context : `PipelineRunContext`
        The run's shared facts.
    stack_file_name : `str` or `None`
        The analyzed stacked image's file name (see
        `astrometry_run_measurements`).
    config : `AppConfiguration`, optional
        Where the quality store lives. The process-wide one if omitted.

    Returns
    -------
    new_count : `int`
        How many rows were new. It is 0 when anything went wrong: a storage
        failure, or a failure while building the rows (both are logged).
    """
    return build_and_record_measurements(
        lambda: astrometry_run_measurements(metrics, gates, context, stack_file_name),
        f"astrometry run on {context.target_id}",
        config=config,
    )
