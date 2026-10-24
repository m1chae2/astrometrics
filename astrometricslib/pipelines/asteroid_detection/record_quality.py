"""Builds and records the quality of one asteroid-detection run.

Asteroid detection searches a target's frames for sources that move in a
straight line across them, then asks a database of known asteroids (SkyBoT)
whether each mover is already known. A run that finds nothing says very
little unless the search could actually have found something, so the checks
here ask: were enough frames searched, did every frame have pointing data,
could the known-asteroid database be asked, did any mover stay unknown, and
were the straight-line tracks judged against a measured position error.

Each check is returned as a `GateResult`. A check that could not look (too
few frames to form a track, no candidate to look up) is ``not_checked``; it
is not a pass, and an empty result from it is not a clean null result.

The same module turns the run's counts and gates into quality measurements
(`asteroid_run_measurements`) and stores them (`record_asteroid_quality`).
The gates and the summary stay as they are; the measurements are written next
to them.
"""

from collections.abc import Mapping, Sequence

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
from astrometricslib.pipelines.asteroid_detection.metrics import (
    ASTEROID_CANDIDATES_DETECTED,
    ASTEROID_CANDIDATES_EPHEMERIS_MATCHED,
    ASTEROID_CANDIDATES_PERSISTENCE_CONFIRMED,
    ASTEROID_CANDIDATES_RATE_LINEARITY_CONFIRMED,
    ASTEROID_DETECTION_METRICS,
    ASTEROID_EPHEMERIS_QUERIES_ATTEMPTED,
    ASTEROID_EPHEMERIS_QUERIES_FAILED,
    ASTEROID_FRAMES_EXCLUDED_MISSING_POINTING,
    ASTEROID_FRAMES_WITH_WCS_ESTIMATE,
    ASTEROID_GATE_EPHEMERIS_CROSS_MATCH,
    ASTEROID_GATE_POINTING_METADATA,
    ASTEROID_GATE_SEARCH_FRAMES,
    ASTEROID_GATE_TRACK_RESIDUALS,
    ASTEROID_GATE_UNMATCHED_MOVERS,
    ASTEROID_RESIDUAL_ASSUMED_ERROR_DETECTIONS,
    ASTEROID_RESIDUAL_CHAINS_NON_MONOTONIC,
    ASTEROID_RESIDUAL_CHAINS_REJECTED,
    ASTEROID_RESIDUAL_CHAINS_TESTED,
    ASTEROID_RESIDUAL_WORST_ACCEPTED_RATIO,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    GateLimitSource,
    PipelineRunContext,
    build_and_record_measurements,
    build_measurement,
    gate_measurement,
)

SEARCH_FRAMES_GATE_NAME = "search_frames"
POINTING_METADATA_GATE_NAME = "pointing_metadata"
EPHEMERIS_GATE_NAME = "ephemeris_cross_match"
UNMATCHED_MOVERS_GATE_NAME = "unmatched_movers"
RESIDUAL_CRITERION_GATE_NAME = "track_residuals"


def asteroid_run_gates(
    metrics: Mapping[str, float],
    minimum_frames_for_a_track: int,
    candidates_awaiting_recovery: int,
    residual_rms_max_multiple: float = 2.0,
) -> list[GateResult]:
    """Build the gates for one asteroid-detection run.

    Parameters
    ----------
    metrics : `Mapping` [`str`, `float`]
        The pipeline's counts for the run (its ``last_run_metrics``).
    minimum_frames_for_a_track : `int`
        The fewest frames a moving object must appear in to count (the
        ``min_frames_for_persistence`` setting).
    candidates_awaiting_recovery : `int`
        Movers confirmed as moving in a straight line but not matched to a
        known asteroid.
    residual_rms_max_multiple : `float`, optional
        How many times the position error a track's RMS residual may be
        (the ``residual_rms_max_multiple`` setting). Quoted as the limit of
        the ``track_residuals`` gate.

    Returns
    -------
    gates : `list` [`GateResult`]
        Five gates, in a fixed order.
    """
    gates: list[GateResult] = []
    searched = int(metrics.get("frames_with_wcs_estimate", 0))
    excluded = int(metrics.get("frames_excluded_missing_pointing_metadata", 0))

    track_source = "the min_frames_for_persistence setting"
    if searched < minimum_frames_for_a_track:
        gates.append(
            unchecked_gate(
                SEARCH_FRAMES_GATE_NAME,
                f"only {searched} frame(s) could be searched and a track needs {minimum_frames_for_a_track}, "
                "so no mover could have been found",
                track_source,
            )
        )
    else:
        gates.append(
            passed_gate(
                SEARCH_FRAMES_GATE_NAME, float(searched), float(minimum_frames_for_a_track), track_source
            )
        )

    if excluded > 0:
        gates.append(
            failed_gate(
                POINTING_METADATA_GATE_NAME,
                f"{excluded} frame(s) excluded for missing RA/DEC/NAXIS pointing metadata",
                float(excluded),
                0.0,
            )
        )
    elif searched == 0:
        gates.append(unchecked_gate(POINTING_METADATA_GATE_NAME, "no frame was considered"))
    else:
        gates.append(passed_gate(POINTING_METADATA_GATE_NAME, 0.0, 0.0))

    confirmed = int(metrics.get("candidates_rate_linearity_confirmed", 0))
    attempted = int(metrics.get("ephemeris_queries_attempted", 0))
    failed = int(metrics.get("ephemeris_queries_failed", 0))
    matched = int(metrics.get("candidates_ephemeris_matched", 0))
    ephemeris_source = "SkyBoT cone searches at the first and last detection of each mover"
    if confirmed == 0:
        gates.append(
            unchecked_gate(EPHEMERIS_GATE_NAME, "no mover reached the known-asteroid check", ephemeris_source)
        )
    elif attempted == 0:
        gates.append(
            unchecked_gate(
                EPHEMERIS_GATE_NAME, "the known-asteroid database was never asked", ephemeris_source
            )
        )
    elif failed > 0:
        gates.append(
            failed_gate(
                EPHEMERIS_GATE_NAME,
                "the known-asteroid database could not be reached, so confirmed movers were not "
                "checked against known asteroids",
                float(failed),
                0.0,
                ephemeris_source,
            )
        )
    else:
        gates.append(
            passed_gate(
                EPHEMERIS_GATE_NAME,
                float(matched),
                limit_source=ephemeris_source,
                detail=f"{matched} of {confirmed} confirmed mover(s) matched a known asteroid",
            )
        )

    if confirmed == 0:
        gates.append(unchecked_gate(UNMATCHED_MOVERS_GATE_NAME, "no mover was confirmed"))
    elif candidates_awaiting_recovery > 0:
        gates.append(
            failed_gate(
                UNMATCHED_MOVERS_GATE_NAME,
                f"{candidates_awaiting_recovery} candidate(s) confirmed as movers but not "
                "matched to a known body -- worth a manual look",
                float(candidates_awaiting_recovery),
                0.0,
            )
        )
    else:
        gates.append(passed_gate(UNMATCHED_MOVERS_GATE_NAME, 0.0, 0.0))

    gates.append(_residual_gate(metrics, residual_rms_max_multiple))
    return gates


def _residual_gate(metrics: Mapping[str, float], residual_rms_max_multiple: float) -> GateResult:
    """Build the gate for the straight-line (residual) test.

    The test accepts a track when its dots miss the fitted line by no more
    than a multiple of the position error of one picture. That error is
    measured per picture from the reference stars. When it could not be
    measured, an assumed value is used instead, and the verdict then rests
    on a number nobody measured.

    Parameters
    ----------
    metrics : `Mapping` [`str`, `float`]
        The pipeline's counts for the run.
    residual_rms_max_multiple : `float`
        The allowed multiple, quoted as the gate's limit.

    Returns
    -------
    gate : `GateResult`
        ``not_checked`` when no chain reached the test; ``failed`` when any
        tested chain used an assumed position error; otherwise ``passed``,
        with the largest accepted ratio as the measured value. The detail
        line also counts the chains rejected for reversing direction.
    """
    tested = int(metrics.get("residual_chains_tested", 0))
    rejected = int(metrics.get("residual_chains_rejected", 0))
    reversing = int(metrics.get("residual_chains_non_monotonic", 0))
    assumed = int(metrics.get("residual_assumed_error_detections", 0))
    limit_source = "the residual_rms_max_multiple setting, times the position error of one picture"
    if tested == 0:
        return unchecked_gate(
            RESIDUAL_CRITERION_GATE_NAME,
            "no chain reached the straight-line test, so no residual was judged",
            limit_source,
        )
    if assumed > 0:
        return failed_gate(
            RESIDUAL_CRITERION_GATE_NAME,
            f"{assumed} detection(s) in the tested chains had no measured position error, "
            "so the assumed error from the settings was used for them",
            float(assumed),
            0.0,
            limit_source,
        )
    return passed_gate(
        RESIDUAL_CRITERION_GATE_NAME,
        float(metrics.get("residual_worst_accepted_ratio", 0.0)),
        residual_rms_max_multiple,
        limit_source,
        f"{tested} chain(s) tested, {rejected} rejected for missing a straight line by more than the limit, "
        f"{reversing} rejected for moving back along their direction by more than the position error",
    )


# The metric that holds each count, keyed by its name in ``last_run_metrics``.
_COUNT_METRIC_BY_KEY: dict[str, str] = {
    "frames_with_wcs_estimate": ASTEROID_FRAMES_WITH_WCS_ESTIMATE,
    "frames_excluded_missing_pointing_metadata": ASTEROID_FRAMES_EXCLUDED_MISSING_POINTING,
    "candidates_detected": ASTEROID_CANDIDATES_DETECTED,
    "candidates_persistence_confirmed": ASTEROID_CANDIDATES_PERSISTENCE_CONFIRMED,
    "candidates_rate_linearity_confirmed": ASTEROID_CANDIDATES_RATE_LINEARITY_CONFIRMED,
    "candidates_ephemeris_matched": ASTEROID_CANDIDATES_EPHEMERIS_MATCHED,
    "ephemeris_queries_attempted": ASTEROID_EPHEMERIS_QUERIES_ATTEMPTED,
    "ephemeris_queries_failed": ASTEROID_EPHEMERIS_QUERIES_FAILED,
    "residual_chains_tested": ASTEROID_RESIDUAL_CHAINS_TESTED,
    "residual_chains_rejected": ASTEROID_RESIDUAL_CHAINS_REJECTED,
    "residual_chains_non_monotonic": ASTEROID_RESIDUAL_CHAINS_NON_MONOTONIC,
    "residual_assumed_error_detections": ASTEROID_RESIDUAL_ASSUMED_ERROR_DETECTIONS,
}

# The metric that stands for each gate, and how its limit was obtained. The
# history fill (`scripts/backfill_quality_measurements.py`) reads this table
# too, so a limit is described in one place.
#
# A count that must be zero follows from the geometry of the search: a frame
# without pointing cannot be searched, and a mover that is confirmed but
# matches no known body is, by definition, one to look at. The two limits
# that are tunable settings are designed values: the fewest frames in a track
# (3, the ``min_frames_for_persistence`` setting, row MOV-D01 of the
# design-estimates table) and the straight-line residual, a multiple of the
# position error of one picture (2.0, the ``residual_rms_max_multiple``
# setting). Entry MOV-03 holds the only evidence for the residual multiple:
# 40 synthetic fields, none on real data. Neither is a measured limit.
GATE_LIMIT_SOURCES: dict[str, GateLimitSource] = {
    SEARCH_FRAMES_GATE_NAME: GateLimitSource(
        ASTEROID_GATE_SEARCH_FRAMES, LimitTier.DESIGN_ESTIMATE, "MOV-D01"
    ),
    POINTING_METADATA_GATE_NAME: GateLimitSource(ASTEROID_GATE_POINTING_METADATA, LimitTier.GEOMETRY),
    EPHEMERIS_GATE_NAME: GateLimitSource(ASTEROID_GATE_EPHEMERIS_CROSS_MATCH, LimitTier.GEOMETRY),
    UNMATCHED_MOVERS_GATE_NAME: GateLimitSource(ASTEROID_GATE_UNMATCHED_MOVERS, LimitTier.GEOMETRY),
    RESIDUAL_CRITERION_GATE_NAME: GateLimitSource(
        ASTEROID_GATE_TRACK_RESIDUALS, LimitTier.DESIGN_ESTIMATE, "MOV-03"
    ),
}

_SPEC_BY_NAME: dict[str, MetricSpec] = {spec.name: spec for spec in ASTEROID_DETECTION_METRICS}


def gate_for_measurement(gate: GateResult, ephemeris_queries_failed: float | None) -> GateResult | None:
    """Adjust a gate so its number means one thing in every outcome.

    Two gates quote a different number depending on how they ended. The
    known-asteroid gate gives the matches when it passes and the failed
    queries when it fails; the residual gate gives a ratio when it passes and
    a count of detections when it fails. A stored metric has one unit and one
    meaning, so the measurement uses a steady number instead. The gate itself
    is not changed.

    The run's record step and the history fill both call this function.

    Parameters
    ----------
    gate : `GateResult`
        The gate as recorded on the summary.
    ephemeris_queries_failed : `float` or `None`
        How many known-asteroid queries failed. `None` when unknown (only a
        stored summary can lack it).

    Returns
    -------
    gate : `GateResult` or `None`
        A copy for the measurement. The known-asteroid gate carries the number
        of failed queries and the limit 0 unless it was not checked; the
        residual gate carries no number unless it passed. `None` when the
        number is needed and unknown.
    """
    if gate.name == EPHEMERIS_GATE_NAME and gate.status is not GateStatus.NOT_CHECKED:
        if ephemeris_queries_failed is None:
            return None
        return gate.model_copy(update={"measured_value": float(ephemeris_queries_failed), "limit": 0.0})
    if gate.name == RESIDUAL_CRITERION_GATE_NAME and gate.status is GateStatus.FAILED:
        return gate.model_copy(update={"measured_value": None})
    return gate


def asteroid_run_measurements(
    metrics: Mapping[str, float],
    gates: Sequence[GateResult],
    context: PipelineRunContext,
) -> list[QualityMeasurement]:
    """Build every measurement the asteroid pipeline declares for one run.

    Parameters
    ----------
    metrics : `Mapping` [`str`, `float`]
        The pipeline's ``last_run_metrics``.
    gates : `Sequence` [`GateResult`]
        The run's gates from `asteroid_run_gates`.
    context : `PipelineRunContext`
        The run's shared facts.

    Returns
    -------
    measurements : `list` [`QualityMeasurement`]
        One measurement for each declared metric, all about the run. A count
        missing from `metrics` is ``not_measured`` with a reason. The worst
        accepted ratio is ``not_measured`` when no chain was tested or the
        pipeline gave 0.0, its marker for "no accepted chain had a measured
        error". The gate metrics carry the gate's outcome and limit.
    """
    subject = context.run_subject()
    measurements: list[QualityMeasurement] = []
    for key, metric_name in _COUNT_METRIC_BY_KEY.items():
        value = metrics.get(key)
        measurements.append(
            build_measurement(
                _SPEC_BY_NAME[metric_name],
                None if value is None else float(value),
                context,
                subject,
                reason=f"The pipeline did not report {key} for this run.",
            )
        )

    ratio = float(metrics.get("residual_worst_accepted_ratio", 0.0))
    ratio_missing = int(metrics.get("residual_chains_tested", 0)) == 0 or ratio <= 0.0
    measurements.append(
        build_measurement(
            _SPEC_BY_NAME[ASTEROID_RESIDUAL_WORST_ACCEPTED_RATIO],
            None if ratio_missing else ratio,
            context,
            subject,
            reason="No accepted chain had a measured position error, so there is no ratio to report.",
        )
    )

    gates_by_name = {gate.name: gate for gate in gates}
    for gate_name, limit_source in GATE_LIMIT_SOURCES.items():
        gate = gates_by_name.get(gate_name)
        if gate is None:
            continue
        spec = _SPEC_BY_NAME[limit_source.metric_name]
        adjusted = gate_for_measurement(gate, metrics.get("ephemeris_queries_failed", 0))
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


def record_asteroid_quality(
    metrics: Mapping[str, float],
    gates: Sequence[GateResult],
    context: PipelineRunContext,
    *,
    config: object = None,
) -> int:
    """Store the run's measurements; no failure here ever fails the run.

    Parameters
    ----------
    metrics : `Mapping` [`str`, `float`]
        The pipeline's ``last_run_metrics``.
    gates : `Sequence` [`GateResult`]
        The run's gates.
    context : `PipelineRunContext`
        The run's shared facts.
    config : `AppConfiguration`, optional
        Where the quality store lives. The process-wide one if omitted.

    Returns
    -------
    new_count : `int`
        How many rows were new. It is 0 when anything went wrong, either in
        building the rows or in storing them (both are logged).
    """
    return build_and_record_measurements(
        lambda: asteroid_run_measurements(metrics, gates, context),
        f"asteroid detection run on {context.target_id}",
        config=config,
    )
