"""The quality metrics the astrometry pipeline declares.

Each `MetricSpec` describes one number the pipeline already measured or one
self-check it already made. Nothing here measures anything: the `computed_by`
field points at the code that does, and `post_processing/record_quality.py`
emits the values next to the quality summary, which stays as it is.

There are four kinds. The counts of what the run did (solver uploads and
catalog lookups) and the self-checks are about the run. The target's clipped
share is about each raw frame of the stack. Every other value is read from the
one stacked image, so it is about that stack:

- Counts of stars and of catalog lookups (unit ``count``).
- Distances and sizes: the three position-error numbers (the catalog match
  separation, the plate solver's fit residual and the Gaia refit RMS), the
  size of the Gaia refit's move, the pixel scale and the star width. Each
  keeps its current estimator under a name that says which one it is. In
  particular `astrometry.catalog_match_separation_rms_arcsec` is not the
  plate-solve fit residual (see validation register entry AST-01).
- The share of the target's own pixels that are clipped or nonlinear in each
  frame of the stack (``frame_target_*_fraction``), counted after a successful
  solve of the stack. It needs the plate solution, which a fresh stack does not
  have, so it is measured here and not by the stacking run (validation register
  entry AST-19).
- The run's five self-checks (``gate_*``), stored with their outcome (passed,
  failed or not checked) and the limit each used. They stay self-checks: they
  flag the run for a look and say nothing about the sky.

The summary's ``astrometry_flags`` (a list of words) and its true/false
fields (``solve_attempted``, ``plate_solve_succeeded`` and
``remote_catalog_circuit_breaker_tripped``) are not numbers, so they are not
declared here.

Names follow ``<checkpoint>.<snake_case>``, so a gate's name is joined to the
prefix ``gate_`` (for example ``astrometry.gate_astrometric_residual``).
"""

from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.models.quality.metric_spec import (
    Checkpoint,
    Direction,
    MetricPurpose,
    MetricSpec,
    UncertaintyKind,
)

ASTROMETRY_SOURCES_DETECTED = "astrometry.sources_detected_count"
ASTROMETRY_SIMBAD_MATCHED_COUNT = "astrometry.simbad_matched_count"
ASTROMETRY_CATALOG_MATCHED_STAR_COUNT = "astrometry.catalog_matched_star_count"
ASTROMETRY_POSITION_ONLY_STAR_COUNT = "astrometry.position_only_star_count"
ASTROMETRY_UNRESOLVED_STAR_COUNT = "astrometry.unresolved_star_count"
ASTROMETRY_PLATE_SOLVE_ATTEMPTS = "astrometry.plate_solve_attempts_count"
ASTROMETRY_PLATE_SOLVE_MATCHED_STAR_COUNT = "astrometry.plate_solve_matched_star_count"
ASTROMETRY_GAIA_FIT_STAR_COUNT = "astrometry.gaia_fit_star_count"
ASTROMETRY_REMOTE_CATALOG_QUERIES_ATTEMPTED = "astrometry.remote_catalog_queries_attempted_count"
ASTROMETRY_REMOTE_CATALOG_QUERIES_FAILED = "astrometry.remote_catalog_queries_failed_count"
ASTROMETRY_CATALOG_MATCH_SEPARATION_RMS_ARCSEC = "astrometry.catalog_match_separation_rms_arcsec"
ASTROMETRY_PLATE_SOLVE_FIT_RESIDUAL_RMS_ARCSEC = "astrometry.plate_solve_fit_residual_rms_arcsec"
ASTROMETRY_GAIA_FIT_RMS_ARCSEC = "astrometry.gaia_fit_rms_arcsec"
ASTROMETRY_GAIA_FIT_RMS_BEFORE_ARCSEC = "astrometry.gaia_fit_rms_before_arcsec"
ASTROMETRY_WCS_REFINEMENT_SHIFT_ARCSEC = "astrometry.wcs_refinement_shift_arcsec"
ASTROMETRY_PLATE_SCALE_ARCSEC_PER_PIXEL = "astrometry.plate_scale_arcsec_per_pixel"
ASTROMETRY_STAR_WIDTH_GAUSSIAN_PX = "astrometry.star_width_gaussian_px"

# The share of the target's own pixels, in each frame of the stack, that reach
# the clip ceiling or leave the sensor's linear range. Counts are not stored:
# a count is the share times the row's sample count (the pixels of the disc).
ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION = "astrometry.frame_target_clipped_fraction"
ASTROMETRY_FRAME_TARGET_NONLINEAR_FRACTION = "astrometry.frame_target_nonlinear_fraction"

ASTROMETRY_GATE_PLATE_SOLVE = "astrometry.gate_plate_solve"
ASTROMETRY_GATE_SOURCE_DETECTION = "astrometry.gate_source_detection"
ASTROMETRY_GATE_CATALOG_MATCHES = "astrometry.gate_catalog_matches"
ASTROMETRY_GATE_ASTROMETRIC_RESIDUAL = "astrometry.gate_astrometric_residual"
ASTROMETRY_GATE_CATALOG_LOOKUP = "astrometry.gate_catalog_lookup"

# A value read from the one stacked image (its stars, its solution, its pixel
# scale) belongs to that stack. A count of what the run did (solver uploads,
# online lookups) and each self-check belong to the run (plan section 3.1).
_STACK_ONLY = (SubjectLevel.STACK,)
_RUN_ONLY = (SubjectLevel.RUN,)
_FRAME_BAND = (
    "Each frame's own filter, as recorded. A color frame's planes are reduced to their maximum at each "
    "position."
)
_NO_UNCERTAINTY = "No uncertainty is reported. The value is an exact count."
_BAND = "Not applicable: the run works on one stacked image, whatever its filter."
_ADAPTER = "pipelines.astrometry.runner.AstrometryPipelineAdapter"
_IDENTIFIER = "pipelines.astrometry.processing.star_identifier.StarIdentifier"
_GATE_BUILDER = "pipelines.astrometry.post_processing.record_quality.astrometry_run_gates"
_GATE_PURPOSE_REASON = (
    "The run flags itself for a manual look when this check fails, so a solution that cannot be "
    "trusted is told apart from one that can."
)
_POSITION_ERROR_UNCERTAINTY = (
    "No uncertainty is reported. The RMS is a single summary of the star-to-star distances and the "
    "pipeline keeps no error on it."
)


def _count_metric(
    name: str,
    meaning: str,
    estimator: str,
    sample_definition: str,
    purpose_reason: str,
    *,
    better: Direction = Direction.NONE,
    computed_by: str = _ADAPTER,
    register_entry: str | None = None,
    levels: tuple[SubjectLevel, ...] = _STACK_ONLY,
) -> MetricSpec:
    """Declare one count of the astrometry pipeline.

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
    register_entry : `str` or `None`, optional
        The validation register entry that backs the count, if one does.
    levels : `tuple` [`SubjectLevel`], optional
        What the count is about. The stacked image unless the caller says run.

    Returns
    -------
    spec : `MetricSpec`
        A context metric, unit ``count``, with no uncertainty.
    """
    return MetricSpec(
        name=name,
        definition_version=1,
        unit="count",
        checkpoint=Checkpoint.ASTROMETRY,
        levels=levels,
        better=better,
        meaning=meaning,
        estimator=estimator,
        sample_definition=sample_definition,
        band=_BAND,
        computed_by=computed_by,
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method=_NO_UNCERTAINTY,
        register_entry=register_entry,
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=purpose_reason,
    )


def _value_metric(
    name: str,
    unit: str,
    meaning: str,
    estimator: str,
    sample_definition: str,
    purpose_reason: str,
    *,
    better: Direction = Direction.NONE,
    computed_by: str = _ADAPTER,
    register_entry: str | None = None,
    purpose: MetricPurpose = MetricPurpose.CONTEXT,
    decisions: tuple[str, ...] = (),
    levels: tuple[SubjectLevel, ...] = _STACK_ONLY,
) -> MetricSpec:
    """Declare one measured value of the astrometry pipeline.

    Parameters
    ----------
    name : `str`
        The metric name.
    unit : `str`
        The unit of the value.
    meaning : `str`
        One line in plain words.
    estimator : `str`
        How the value is made.
    sample_definition : `str`
        What is measured.
    purpose_reason : `str`
        Why the value is kept.
    better : `Direction`, optional
        Which direction is better.
    computed_by : `str`, optional
        Dotted path of the code that measures it.
    register_entry : `str` or `None`, optional
        The validation register entry that backs the value, if one does.
    purpose : `MetricPurpose`, optional
        Why the metric exists. Context unless a self-check reads it.
    decisions : `tuple` [`str`], optional
        The self-checks that read the value, as documentation only.
    levels : `tuple` [`SubjectLevel`], optional
        What the value is about. The stacked image unless the caller says run.

    Returns
    -------
    spec : `MetricSpec`
        A metric with no uncertainty.
    """
    return MetricSpec(
        name=name,
        definition_version=1,
        unit=unit,
        checkpoint=Checkpoint.ASTROMETRY,
        levels=levels,
        better=better,
        meaning=meaning,
        decisions=decisions,
        estimator=estimator,
        sample_definition=sample_definition,
        band=_BAND,
        computed_by=computed_by,
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method=_POSITION_ERROR_UNCERTAINTY,
        register_entry=register_entry,
        purpose=purpose,
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
    """Declare one self-check gate of the astrometry pipeline as a metric.

    Parameters
    ----------
    name : `str`
        The metric name, ``astrometry.gate_<gate name>``.
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
        checkpoint=Checkpoint.ASTROMETRY,
        levels=_RUN_ONLY,
        better=Direction.NONE,
        meaning=meaning,
        decisions=("run flag",),
        estimator=estimator,
        sample_definition=sample_definition,
        band=_BAND,
        computed_by=_GATE_BUILDER,
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported. The value is compared with a fixed limit.",
        register_entry=register_entry,
        purpose=MetricPurpose.DECISION,
        purpose_reason=_GATE_PURPOSE_REASON,
    )


_TARGET_CLIPPING_ESTIMATOR = (
    "The target is located by its catalog position (the target record's right ascension and "
    "declination, as the record held them before the run) through this run's own plate solution of "
    "the stack. A run that did not solve the stack gets not-measured rows: the registration reference "
    "frame's solution is the mount's commanded pointing and was 157 arcsec off on the M 57 night "
    "checked (AST-19), and the pixel center is never used instead. A position outside the stack is "
    "not measured. Before counting, a source must stand in the disc: its brightest pixel on the "
    "stack must be at least 5 sky sigma above the median of a ring around it (the sigma is 1.4826 "
    "times the ring's median absolute deviation); otherwise the rows are not measured, with the "
    "right ascension, declination and pixel used. In a crowded field any star passes, so this finds a "
    "position on blank sky and not a wrong star. The position on the stack is moved onto each "
    "frame by the inverse of Siril's registration transform for the frame, kept beside the stack by "
    "the stacking run. The region is the disc around that position with a radius of the larger of "
    "30 arcseconds (about 16 px at 1.9 arcsec/px, which holds a point source and its seeing disc "
    "many times over and allows a position error of several pixels) and the target's semi-major "
    "axis when a size is stored (none is today, so a target larger than 30 arcseconds is examined "
    "in its core only). Every pixel of the disc counts as a target pixel, so in a crowded field the "
    "clipped pixels of field stars in the disc are counted too. The pixels are the raw frame's own "
    "values, with a color frame's planes reduced to their maximum at each position."
)

_TARGET_CLIPPING_SAMPLE = (
    "The target's disc in each frame of the stack, for up to 200 frames spread evenly through the run "
    "and for at most 120 seconds of reading. The sample count of a row is the number of disc pixels "
    "inside the frame, so a count is the share times it. One row for each frame in the stack's "
    "registration record, in the frame's own subject (the same subject as `stacking.frame_weight`). A "
    "frame Siril could not register, a frame not sampled, a frame not read in time, a stack without a "
    "registration record (stacked before the record existed) and a spectral stack give rows that are "
    "not measured, with the reason, or none when there is no record."
)

_TARGET_CLIPPING_PURPOSE = (
    "It tells whether the target's own pixels were clipped or left the sensor's linear range in a "
    "frame, which would flatten the target in the stack or bend its brightness. Nothing decides from it yet."
)

_TARGET_CLIPPING_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        name=ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION,
        definition_version=1,
        unit="fraction",
        checkpoint=Checkpoint.ASTROMETRY,
        levels=(SubjectLevel.FRAME,),
        better=Direction.LOWER,
        meaning=(
            "The share of the pixels in the target's disc, in one raw frame, that reach the camera's "
            "clip ceiling."
        ),
        decisions=("exposure length",),
        estimator=(
            "Pixels of the target's disc at or above 98 percent of the camera profile's clip ceiling "
            "(the same 2 percent tolerance the saturation check uses), divided by the number of disc "
            "pixels that lie inside the frame. " + _TARGET_CLIPPING_ESTIMATOR
        ),
        sample_definition=_TARGET_CLIPPING_SAMPLE,
        band=_FRAME_BAND,
        computed_by="pipelines.astrometry.post_processing.target_clipping.target_clipping_measurements",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported. The value is a count divided by a count.",
        register_entry="AST-19",
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=_TARGET_CLIPPING_PURPOSE,
    ),
    MetricSpec(
        name=ASTROMETRY_FRAME_TARGET_NONLINEAR_FRACTION,
        definition_version=1,
        unit="fraction",
        checkpoint=Checkpoint.ASTROMETRY,
        levels=(SubjectLevel.FRAME,),
        better=Direction.LOWER,
        meaning=(
            "The share of the pixels in the target's disc, in one raw frame, that reach the camera's "
            "photometric linearity limit (clipped pixels included)."
        ),
        decisions=("exposure length",),
        estimator=(
            "Pixels of the target's disc at or above the camera profile's photometric linearity limit, "
            "divided by the number of disc pixels that lie inside the frame; not measured when the "
            "profile has no limit. A limit from a datasheet or an assumption, or the generic fallback "
            "profile of a camera that is not listed, stores the row as assumed. " + _TARGET_CLIPPING_ESTIMATOR
        ),
        sample_definition=_TARGET_CLIPPING_SAMPLE,
        band=_FRAME_BAND,
        computed_by="pipelines.astrometry.post_processing.target_clipping.target_clipping_measurements",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported. The value is a count divided by a count.",
        register_entry="AST-19",
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=_TARGET_CLIPPING_PURPOSE,
    ),
)

ASTROMETRY_METRICS: tuple[MetricSpec, ...] = (
    _count_metric(
        ASTROMETRY_SOURCES_DETECTED,
        "How many stars the detector found in the stacked image.",
        "Count of the sources left after the detector's duplicates are removed.",
        "The detections in the one stacked image the run works on.",
        "A solve needs stars to match, so this count says how much the solution had to work with.",
        better=Direction.HIGHER,
        computed_by=_IDENTIFIER,
    ),
    _count_metric(
        ASTROMETRY_SIMBAD_MATCHED_COUNT,
        "How many saved stars carry a spectral type from SIMBAD.",
        "Count of the identified stars that have a spectral type, which only a SIMBAD match supplies.",
        "The stars the run identified, after the unresolved ones are dropped.",
        "A low count on a well-solved field points at a SIMBAD lookup problem.",
    ),
    _count_metric(
        ASTROMETRY_CATALOG_MATCHED_STAR_COUNT,
        "How many identified stars were matched to a catalog entry.",
        "Count of the stars that SIMBAD or Gaia named.",
        "The stars the run identified, after the unresolved ones are dropped.",
        "The `catalog_matches` self-check compares this count with its limit.",
        better=Direction.HIGHER,
        computed_by="pipelines.shared.star_recording._drop_unresolved_stars",
    ),
    _count_metric(
        ASTROMETRY_POSITION_ONLY_STAR_COUNT,
        "How many identified stars have a position but no catalog match.",
        "Count of the stars given an id built from their sky position only.",
        "The stars the run identified, after the unresolved ones are dropped.",
        "These stars are real detections that no catalog could name.",
        computed_by="pipelines.shared.star_recording._drop_unresolved_stars",
    ),
    _count_metric(
        ASTROMETRY_UNRESOLVED_STAR_COUNT,
        "How many detected stars were dropped for having no sky position.",
        "Count of the stars whose id is still a placeholder, so they cannot be saved.",
        "All the stars the run detected.",
        "A high count means many detections could not be placed on the sky.",
        better=Direction.LOWER,
        computed_by="pipelines.shared.star_recording._drop_unresolved_stars",
    ),
    _count_metric(
        ASTROMETRY_PLATE_SOLVE_ATTEMPTS,
        "How many times the image was sent to the plate solver.",
        "The number of solve uploads in this run, retries included.",
        "The solve requests made during the run.",
        "A solve that needed retries is told apart from one that worked the first time.",
        better=Direction.LOWER,
        computed_by="drivers.astrometry_net_driver.get_plate_solve_attempt_count",
        levels=_RUN_ONLY,
    ),
    _count_metric(
        ASTROMETRY_PLATE_SOLVE_MATCHED_STAR_COUNT,
        "How many stars the plate solver matched to its own reference stars.",
        "The number of usable rows in the solver's match table.",
        "The stars in the solver's match table. Missing when the solver gave no table, as the online "
        "solver does.",
        "The fit residual is an RMS over these stars, so the count says how far to trust it.",
        better=Direction.HIGHER,
        computed_by="drivers.astrometry_net_driver.read_corr_fit_statistics",
    ),
    _count_metric(
        ASTROMETRY_GAIA_FIT_STAR_COUNT,
        "How many stars the Gaia refit of the solution used.",
        "The number of stars in the refit's final set, after its outlier clipping.",
        "The detected stars matched to a Gaia position. Missing when the refit was not tried.",
        "The Gaia RMS is over these stars, so the count says how far to trust it.",
        better=Direction.HIGHER,
        computed_by="pipelines.astrometry.processing.wcs_refinement.refine_wcs_with_gaia",
        register_entry="AST-02",
    ),
    _count_metric(
        ASTROMETRY_REMOTE_CATALOG_QUERIES_ATTEMPTED,
        "How many Gaia catalog lookups the run made.",
        "The process-wide count of lookups, reset at the start of the run.",
        "The lookups made for this run.",
        "A run with no lookup has not checked the catalogs at all.",
        computed_by="pipelines.astrometry.processing.star_identifier.get_gaia_query_statistics",
        register_entry="AST-08",
        levels=_RUN_ONLY,
    ),
    _count_metric(
        ASTROMETRY_REMOTE_CATALOG_QUERIES_FAILED,
        "How many of the Gaia catalog lookups failed.",
        "The process-wide count of failed lookups, reset at the start of the run.",
        "The lookups counted by the attempted-lookups metric.",
        "Failed lookups leave stars unnamed, so the count helps explain a low match count.",
        better=Direction.LOWER,
        computed_by="pipelines.astrometry.processing.star_identifier.get_gaia_query_statistics",
        register_entry="AST-08",
        levels=_RUN_ONLY,
    ),
    _value_metric(
        ASTROMETRY_CATALOG_MATCH_SEPARATION_RMS_ARCSEC,
        "arcsec",
        "How far the detected stars sit from their nearest SIMBAD or Gaia star, as a root mean square.",
        "Root mean square of the distance from each detected star to its nearest catalog star, over the "
        "stars within the catalog match radius. It is not the plate-solve fit residual: wrong matches "
        "raise it, and SIMBAD's uneven position precision does too. Rounded to 4 decimals.",
        "The detected stars that matched a catalog star. Missing when none matched.",
        "The `astrometric_residual` self-check falls back to it when neither the Gaia refit nor the "
        "solver gave a residual.",
        better=Direction.LOWER,
        computed_by=_IDENTIFIER,
        register_entry="AST-01",
    ),
    _value_metric(
        ASTROMETRY_PLATE_SOLVE_FIT_RESIDUAL_RMS_ARCSEC,
        "arcsec",
        "How far the plate solver's fitted star positions sit from its own reference stars, as a root "
        "mean square.",
        "Root mean square of the distance between each matched field star's position under the solved "
        "coordinates and its reference star's position, read from the solver's match table. The "
        "reference stars carry their own position errors, so this is a pessimistic check of the "
        "solution.",
        "The stars in the solver's match table. Missing when the solver gave no table.",
        "The `astrometric_residual` self-check uses it when the Gaia refit gave no residual.",
        better=Direction.LOWER,
        computed_by="drivers.astrometry_net_driver.read_corr_fit_statistics",
        register_entry="AST-01",
    ),
    _value_metric(
        ASTROMETRY_GAIA_FIT_RMS_ARCSEC,
        "arcsec",
        "How far the detected stars sit from their Gaia DR3 positions under the kept solution, as a "
        "root mean square.",
        "Root mean square of the distance between each refit star and its Gaia position, moved to the "
        "image's date. It uses the refined solution when the refit was applied and the solver's own "
        "otherwise, so it does not depend on the plate solver.",
        "The stars of the refit's final set. Missing when the refit was not tried or had too few stars.",
        "The `astrometric_residual` self-check judges it first, because it is independent of the solver.",
        better=Direction.LOWER,
        computed_by="pipelines.astrometry.processing.wcs_refinement.refine_wcs_with_gaia",
        register_entry="AST-02",
        purpose=MetricPurpose.DECISION,
        decisions=("run flag",),
    ),
    _value_metric(
        ASTROMETRY_GAIA_FIT_RMS_BEFORE_ARCSEC,
        "arcsec",
        "How far the same refit stars sit from their Gaia positions under the solver's own solution.",
        "Root mean square of the distance between each refit star and its Gaia position under the "
        "solution as the plate solver returned it, over the refit's final set of stars.",
        "The stars of the refit's final set. Missing when the refit was not tried or had too few stars.",
        "Read beside the value after the refit, it shows how much the refit helped.",
        better=Direction.LOWER,
        computed_by="pipelines.astrometry.processing.wcs_refinement.refine_wcs_with_gaia",
        register_entry="AST-02",
    ),
    _value_metric(
        ASTROMETRY_WCS_REFINEMENT_SHIFT_ARCSEC,
        "arcsec",
        "How far the Gaia refit moved the solution.",
        "The largest move, over a 5 by 5 grid of image points, between the solver's solution and the "
        "refined one. It is 0 when the refit was tried and not applied.",
        "The grid points of the image. Missing when the refit was not tried or had too few stars.",
        "A large move means the plate solver was far off, which the solver's own fit does not show.",
        better=Direction.LOWER,
        computed_by="pipelines.astrometry.processing.wcs_refinement.refine_wcs_with_gaia",
        register_entry="AST-02",
    ),
    _value_metric(
        ASTROMETRY_PLATE_SCALE_ARCSEC_PER_PIXEL,
        "arcsec/px",
        "The size of one pixel on the sky, in arcseconds per pixel.",
        "The mean of the two axis pixel scales of the solved coordinates, times 3600.",
        "The solved coordinates of the one stacked image. Missing when the image was not solved.",
        "The `astrometric_residual` self-check needs it to turn a residual in arcseconds into a "
        "fraction of the star width.",
        computed_by="pipelines.astrometry.runner._plate_scale_arcsec_per_pixel",
        purpose=MetricPurpose.DECISION,
        decisions=("run flag",),
    ),
    _value_metric(
        ASTROMETRY_STAR_WIDTH_GAUSSIAN_PX,
        "px",
        "The width (FWHM) of the brightest stars in the stacked image, in pixels, from Gaussian fits.",
        "Median full width at half maximum of Gaussian fits to up to 15 of the brightest unsaturated "
        "stars. A star stretched by more than 1.5 is kept at its narrow-axis width, so a trailed "
        "image reads sharp. When the stacking step already stored the width of the same file, that "
        "stored value is reused.",
        "Up to 15 stars of the one stacked image. Missing when no star could be measured.",
        "The `astrometric_residual` self-check judges the residual as a fraction of this width.",
        better=Direction.LOWER,
        computed_by="pipelines.astrometry.pre_processing.fwhm.measure_image_fwhm",
        register_entry="AST-07",
        purpose=MetricPurpose.DECISION,
        decisions=("run flag",),
    ),
    *_TARGET_CLIPPING_METRICS,
    _gate_metric(
        ASTROMETRY_GATE_PLATE_SOLVE,
        "count",
        "Whether the image was solved to sky coordinates.",
        "The value is 1 when the image was solved and 0 when it was not. The limit is 1. The check "
        "passes when a solution exists and fails otherwise.",
        "The one stacked image.",
    ),
    _gate_metric(
        ASTROMETRY_GATE_SOURCE_DETECTION,
        "count",
        "Whether the detector found any star.",
        "The value is the number of detected stars. The limit is 1. The check fails below the limit.",
        "The one stacked image.",
    ),
    _gate_metric(
        ASTROMETRY_GATE_CATALOG_MATCHES,
        "count",
        "Whether enough stars were matched to a catalog for the fit to be reliable.",
        "The value is the number of catalog-matched stars. The limit is 20. The check fails below the "
        "limit and is not checked when the image was not solved.",
        "The stars identified in the one stacked image.",
        register_entry="AST-05",
    ),
    _gate_metric(
        ASTROMETRY_GATE_ASTROMETRIC_RESIDUAL,
        "fraction",
        "Whether the plate solution misses its stars by a large part of a star's width.",
        "The value is the RMS residual divided by the star width in arcseconds (the plate scale times "
        "the star width in pixels). The residual is the Gaia refit RMS when there is one, else the "
        "solver's fit residual, else the catalog match separation; the reason says which. The limit "
        "is 0.5. The check fails above the limit and is not checked when the residual, the plate "
        "scale or the star width is missing.",
        "The matched stars of the one stacked image.",
        register_entry="AST-04",
    ),
    _gate_metric(
        ASTROMETRY_GATE_CATALOG_LOOKUP,
        "fraction",
        "Whether the online catalog lookups worked.",
        "The value is the share of lookups that failed. The limit is 0.5. The check fails at or above "
        "the limit, and fails when the lookup circuit breaker tripped during the run. It is not checked "
        "when no lookup was made, including when the breaker was already open.",
        "The catalog lookups made during the run.",
    ),
)
