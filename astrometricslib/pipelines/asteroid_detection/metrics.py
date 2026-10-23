"""The quality metrics the asteroid detection pipeline declares.

Each `MetricSpec` describes one number the pipeline already counted or one
self-check it already made. Nothing here measures anything: the `computed_by`
field points at the code that does, and `record_quality.py` emits the values.

There are two kinds. The counts and residual values come from the pipeline's
``last_run_metrics`` and are measured once per run. The ``gate_*`` metrics are
the run's self-checks (see `record_quality.asteroid_run_gates`), stored with
their outcome (passed, failed or not checked) and the limit each used. They
stay self-checks: they flag the run for a look and say nothing about the sky.
All of them are about one run, so their subject level is ``run``.

Names follow ``<checkpoint>.<snake_case>``, so a gate's name is joined to the
prefix ``gate_`` (for example ``asteroid_detection.gate_track_residuals``).
"""

from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.models.quality.metric_spec import (
    Checkpoint,
    Direction,
    MetricPurpose,
    MetricSpec,
    UncertaintyKind,
)

ASTEROID_FRAMES_WITH_WCS_ESTIMATE = "asteroid_detection.frames_with_wcs_estimate"
ASTEROID_FRAMES_EXCLUDED_MISSING_POINTING = "asteroid_detection.frames_excluded_missing_pointing_metadata"
ASTEROID_CANDIDATES_DETECTED = "asteroid_detection.candidates_detected"
ASTEROID_CANDIDATES_PERSISTENCE_CONFIRMED = "asteroid_detection.candidates_persistence_confirmed"
ASTEROID_CANDIDATES_RATE_LINEARITY_CONFIRMED = "asteroid_detection.candidates_rate_linearity_confirmed"
ASTEROID_CANDIDATES_EPHEMERIS_MATCHED = "asteroid_detection.candidates_ephemeris_matched"
ASTEROID_EPHEMERIS_QUERIES_ATTEMPTED = "asteroid_detection.ephemeris_queries_attempted"
ASTEROID_EPHEMERIS_QUERIES_FAILED = "asteroid_detection.ephemeris_queries_failed"
ASTEROID_RESIDUAL_CHAINS_TESTED = "asteroid_detection.residual_chains_tested"
ASTEROID_RESIDUAL_CHAINS_REJECTED = "asteroid_detection.residual_chains_rejected"
ASTEROID_RESIDUAL_CHAINS_NON_MONOTONIC = "asteroid_detection.residual_chains_non_monotonic"
ASTEROID_RESIDUAL_ASSUMED_ERROR_DETECTIONS = "asteroid_detection.residual_assumed_error_detections"
ASTEROID_RESIDUAL_WORST_ACCEPTED_RATIO = "asteroid_detection.residual_worst_accepted_ratio"

ASTEROID_GATE_SEARCH_FRAMES = "asteroid_detection.gate_search_frames"
ASTEROID_GATE_POINTING_METADATA = "asteroid_detection.gate_pointing_metadata"
ASTEROID_GATE_EPHEMERIS_CROSS_MATCH = "asteroid_detection.gate_ephemeris_cross_match"
ASTEROID_GATE_UNMATCHED_MOVERS = "asteroid_detection.gate_unmatched_movers"
ASTEROID_GATE_TRACK_RESIDUALS = "asteroid_detection.gate_track_residuals"

_RUN_ONLY = (SubjectLevel.RUN,)
_NO_UNCERTAINTY = "No uncertainty is reported. The value is an exact count."
_BAND = "Not applicable: the search uses every light frame of the target, whatever its filter."
_PIPELINE_CLASS = "pipelines.asteroid_detection.pipeline.AsteroidDetectionPipeline"
_DETECTOR_CLASS = "pipelines.asteroid_detection.detection.MovingObjectDetector"
_GATE_BUILDER = "pipelines.asteroid_detection.record_quality.asteroid_run_gates"
_GATE_PURPOSE_REASON = (
    "The run flags itself for a manual look when this check fails, so a clean empty result "
    "is told apart from a search that could not have found anything."
)


def _count_metric(
    name: str,
    meaning: str,
    estimator: str,
    sample_definition: str,
    purpose_reason: str,
    *,
    better: Direction = Direction.NONE,
    computed_by: str = _PIPELINE_CLASS,
) -> MetricSpec:
    """Declare one per-run count of the asteroid pipeline.

    Parameters
    ----------
    name : `str`
        The metric name.
    meaning : `str`
        One line in plain words.
    estimator : `str`
        How the count is made.
    sample_definition : `str`
        What is counted.
    purpose_reason : `str`
        Why the count is kept.
    better : `Direction`, optional
        Which direction is better. Most counts have none.
    computed_by : `str`, optional
        Dotted path of the code that counts it.

    Returns
    -------
    spec : `MetricSpec`
        A context metric on the run level, unit ``count``, with no uncertainty.
    """
    return MetricSpec(
        name=name,
        definition_version=1,
        unit="count",
        checkpoint=Checkpoint.ASTEROID_DETECTION,
        levels=_RUN_ONLY,
        better=better,
        meaning=meaning,
        estimator=estimator,
        sample_definition=sample_definition,
        band=_BAND,
        computed_by=computed_by,
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method=_NO_UNCERTAINTY,
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=purpose_reason,
    )


def _gate_metric(
    name: str,
    unit: str,
    meaning: str,
    estimator: str,
    sample_definition: str,
    *,
    register_entry: str | None = None,
) -> MetricSpec:
    """Declare one self-check gate of the asteroid pipeline as a metric.

    Parameters
    ----------
    name : `str`
        The metric name, ``asteroid_detection.gate_<gate name>``.
    unit : `str`
        The unit of the value and of the limit.
    meaning : `str`
        One line in plain words.
    estimator : `str`
        What the stored value is and how the outcome is decided.
    sample_definition : `str`
        What the check looks at.
    register_entry : `str` or `None`, optional
        The validation register entry that backs the limit, if one does.

    Returns
    -------
    spec : `MetricSpec`
        A decision metric on the run level with no uncertainty.
    """
    return MetricSpec(
        name=name,
        definition_version=1,
        unit=unit,
        checkpoint=Checkpoint.ASTEROID_DETECTION,
        levels=_RUN_ONLY,
        better=Direction.NONE,
        meaning=meaning,
        decisions=("run flag",),
        estimator=estimator,
        sample_definition=sample_definition,
        band=_BAND,
        computed_by=_GATE_BUILDER,
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method=_NO_UNCERTAINTY,
        register_entry=register_entry,
        purpose=MetricPurpose.DECISION,
        purpose_reason=_GATE_PURPOSE_REASON,
    )


ASTEROID_DETECTION_METRICS: tuple[MetricSpec, ...] = (
    _count_metric(
        ASTEROID_FRAMES_WITH_WCS_ESTIMATE,
        "How many light frames the search could place on the sky.",
        "Count of the target's light frames for which a pixel-to-sky map (WCS) could be built from the "
        "frame's own pointing header and the stack's map.",
        "Every light frame of the target that has a capture time.",
        "A search over too few frames cannot form a track, so this count says how much was searched.",
        better=Direction.HIGHER,
    ),
    _count_metric(
        ASTEROID_FRAMES_EXCLUDED_MISSING_POINTING,
        "How many light frames were left out because their header lacks pointing.",
        "Count of light frames dropped before the search for a missing RA, Dec or image size in the header.",
        "Every light frame of the target that has a capture time.",
        "Every excluded frame is a frame the search could not look at.",
        better=Direction.LOWER,
    ),
    _count_metric(
        ASTEROID_CANDIDATES_DETECTED,
        "How many candidate chains of dots the search formed, rejected ones included.",
        "Length of the list that the detection step returns: every chain, whatever cascade stage it reached.",
        "All chains the detector linked across the searched frames.",
        "It is the start of the funnel the later counts are read against.",
        computed_by=_DETECTOR_CLASS,
    ),
    _count_metric(
        ASTEROID_CANDIDATES_PERSISTENCE_CONFIRMED,
        "How many chains appeared in enough frames to count.",
        "Count of candidates whose cascade stage is anything other than rejected for appearing "
        "in a single frame.",
        "All candidates.",
        "It shows how many chains survived the first cut of the rejection cascade.",
    ),
    _count_metric(
        ASTEROID_CANDIDATES_RATE_LINEARITY_CONFIRMED,
        "How many chains moved at a steady rate along a straight line.",
        "Count of candidates at the rate-linearity-confirmed stage or the ephemeris-matched stage.",
        "All candidates.",
        "These are the movers that reach the known-asteroid check, and the surviving candidates are kept.",
    ),
    _count_metric(
        ASTEROID_CANDIDATES_EPHEMERIS_MATCHED,
        "How many confirmed movers matched a known asteroid.",
        "Count of candidates at the ephemeris-matched stage, found by the SkyBoT cone searches.",
        "All candidates.",
        "A match says the mover is already known; the difference from the confirmed count is what is new.",
    ),
    _count_metric(
        ASTEROID_EPHEMERIS_QUERIES_ATTEMPTED,
        "How many questions were sent to the known-asteroid database (SkyBoT).",
        "Count of SkyBoT cone searches sent, up to two per confirmed mover.",
        "The cone searches at the first and last detection of each confirmed mover.",
        "A mover with no question asked has not been checked against known asteroids.",
        computed_by="pipelines.asteroid_detection.ephemeris.EphemerisCrossMatcher",
    ),
    _count_metric(
        ASTEROID_EPHEMERIS_QUERIES_FAILED,
        "How many questions to the known-asteroid database failed.",
        "Count of SkyBoT cone searches that raised an error. A failed search looks like an empty field, "
        "so this count tells them apart.",
        "The cone searches counted by the attempted-queries metric.",
        "A failure means confirmed movers were not checked against known asteroids.",
        better=Direction.LOWER,
        computed_by="pipelines.asteroid_detection.ephemeris.EphemerisCrossMatcher",
    ),
    _count_metric(
        ASTEROID_RESIDUAL_CHAINS_TESTED,
        "How many chains reached the straight-line (residual) test.",
        "Count of chains the residual criterion was applied to.",
        "Chains that passed the earlier steps of the cascade and had enough dots to fit a line.",
        "It is the number the residual rejections are read against.",
        computed_by=_DETECTOR_CLASS,
    ),
    _count_metric(
        ASTEROID_RESIDUAL_CHAINS_REJECTED,
        "How many tested chains missed their fitted line by too much.",
        "Count of tested chains whose RMS residual exceeded the allowed multiple of the position error "
        "(the residual_rms_max_multiple setting).",
        "The chains counted by the tested-chains metric.",
        "A high share means the dots do not line up, as for noise chains.",
        computed_by=_DETECTOR_CLASS,
    ),
    _count_metric(
        ASTEROID_RESIDUAL_CHAINS_NON_MONOTONIC,
        "How many chains passed the line test but moved back along their direction.",
        "Count of chains rejected because they reversed direction by more than the position error.",
        "Tested chains that passed the other straight-line conditions.",
        "A track that reverses is not an asteroid on a steady path.",
        computed_by=_DETECTOR_CLASS,
    ),
    _count_metric(
        ASTEROID_RESIDUAL_ASSUMED_ERROR_DETECTIONS,
        "How many dots were judged with an assumed position error instead of a measured one.",
        "Count of detections, in the tested chains, whose frame had no measured position error and so used "
        "the astrometric_error_default_arcsec setting.",
        "Every detection of every tested chain.",
        "The straight-line verdict rests on a number nobody measured for these dots.",
        better=Direction.LOWER,
        computed_by=_DETECTOR_CLASS,
    ),
    MetricSpec(
        name=ASTEROID_RESIDUAL_WORST_ACCEPTED_RATIO,
        definition_version=1,
        unit="",
        checkpoint=Checkpoint.ASTEROID_DETECTION,
        levels=_RUN_ONLY,
        better=Direction.LOWER,
        meaning="The worst straight-line fit among the accepted tracks, in position errors.",
        estimator=(
            "For each accepted chain, the larger of the RA and Dec ratios of RMS residual to that axis's "
            "position error. The value is the largest such ratio among accepted chains. The pipeline gives "
            "0.0 when no accepted chain had a measured error, and that is stored as not measured."
        ),
        sample_definition="The chains that passed the residual test and the direction check.",
        band=_BAND,
        computed_by=_DETECTOR_CLASS,
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        register_entry="MOV-03",
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=(
            "It shows how close the best-looking tracks came to the limit, which is the "
            "residual_rms_max_multiple setting."
        ),
    ),
    _gate_metric(
        ASTEROID_GATE_SEARCH_FRAMES,
        "count",
        "Whether enough frames were searched for a track to be possible.",
        "The value is the number of frames searched. The limit is min_frames_for_persistence. The check "
        "passes at or above the limit and is not checked below it, because a search with fewer frames "
        "than a track needs could not have found a mover.",
        "The light frames that had a pixel-to-sky map.",
    ),
    _gate_metric(
        ASTEROID_GATE_POINTING_METADATA,
        "count",
        "Whether any frame was left out for a missing pointing header.",
        "The value is the number of frames excluded for a missing RA, Dec or image size. The limit is 0. "
        "The check fails above 0 and is not checked when no frame was considered.",
        "The light frames of the target.",
    ),
    _gate_metric(
        ASTEROID_GATE_EPHEMERIS_CROSS_MATCH,
        "count",
        "Whether the known-asteroid database could be asked about the confirmed movers.",
        "The value is the number of failed SkyBoT cone searches. The limit is 0. The check fails above 0 "
        "and is not checked when no mover was confirmed or no search was sent. The gate's own record "
        "holds the number of matches in its detail.",
        "The cone searches sent for the confirmed movers.",
    ),
    _gate_metric(
        ASTEROID_GATE_UNMATCHED_MOVERS,
        "count",
        "Whether any confirmed mover matched no known asteroid.",
        "The value is the number of movers confirmed as moving in a straight line but not matched to a "
        "known body. The limit is 0. The check fails above 0, which is worth a manual look, and is not "
        "checked when no mover was confirmed.",
        "The confirmed movers.",
    ),
    _gate_metric(
        ASTEROID_GATE_TRACK_RESIDUALS,
        "",
        "Whether the straight-line test was judged against a measured position error.",
        "When the check passes, the value is the worst accepted ratio of RMS residual to position error "
        "and the limit is the residual_rms_max_multiple setting. The check fails when any tested chain "
        "used an assumed position error; the value is then not stored, because the gate's own number "
        "in that case is a count of detections. It is not checked when no chain reached the test.",
        "The chains that reached the straight-line test.",
        register_entry="MOV-03",
    ),
)
