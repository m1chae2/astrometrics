# Quality Metrics Plan

This plan gives every quality metric in `astrometricslib` one shared shape. The aim is metrics
that people can act on. An observer should learn what to change before the next night. A
processing step should learn which data to use. A science user should learn how far to trust a
result. `wayfindinglib` builds its observation recommendations from these metrics, so they must
be easy to find, compare and trust. `astrometricslib` is for science: it measures. All observing
advice, ranking and night-level summaries live in `wayfindinglib`. The desktop app is a thin
wrapper over the two libraries, so every behavior the user sees has a named library owner.

The plan covers:

- what the code records today, and why that is hard to build on,
- the decisions agreed on 2026-10-17,
- the shared shape: measurement, limit, judgment and the metric registry,
- efficiency, the unit `wayfindinglib` uses to rank losses and targets,
- the metrics this plan carries (new metrics are in `QUALITY_SCIENCE_PLAN.md`),
- the target dataset report: the warnings and recommendations the user sees for a target, the
  library owner of each behavior, and the stored night plan,
- what happens to today's verdicts and gates,
- the impact of the work and how its risk is kept low,
- the order of work.

The findings come from a read-only survey of the repository on 2026-10-17. The code is the source
of truth. Line numbers drift, so this plan names files and functions instead.

## Status

Status on 2026-10-23: design, target dataset report and order of work agreed after two board
reviews. Phase 0 is merged (#100, Appendix C). Phase 1 is built and board-reviewed on branch
`94-phase1-foundations` (Appendix D, PR #103). Phase 2 is built and board-reviewed on branch
`94-phase2a-sensor-noise` (Appendix E, PR #107). Phase 3 is built and board-reviewed on branch
`94-phase3-pipeline-metrics` (Appendix F). The science metrics are next. Tracked in issue #93.

A technical review board (an observational astronomer, a software engineer and a technical
architect) reviewed the plan on 2026-10-17. All three approved it with changes. Appendix A lists
the changes and where each one landed. After the review, the new science metrics moved to their
own plan, `QUALITY_SCIENCE_PLAN.md`.

A second board (the same three roles plus a user-experience reviewer) reviewed this plan,
`QUALITY_USE_CASES.md`, `QUALITY_SCIENCE_PLAN.md` and `UI_DISPLAY_PLAN.md` together later on
2026-10-17. All four approved with changes. Decisions 16 to 21 and Appendix B record the outcome.

## 1. Findings from the survey

Quality data is written in many shapes and places today.

| Where | What it holds | Problem |
| :--- | :--- | :--- |
| `targets.data_json` in `astrometrics.db` | Every run's `*QualitySummary`, all 46 run-level gates, frame records, asteroid candidates | One JSON blob per target. It cannot be searched across targets, and a re-run overwrites the previous summary. |
| `stellar_objects.data_json` | Per-star results: `CatalogMatchQuality`, `PhotometryResult`, `SpectroscopyResult`, the per-star `verdict_checks` | A re-run replaces the whole block. No API reads `verdict_checks`. |
| `prov_*` and `processing_jobs` in `astrometrics_log.db` | Resolved parameters, pipeline version, lineage | No gate outcomes. `output_metrics` partly repeats the summaries. |
| Sidecar files (stacking only) | `_excluded/excluded_frames.json`, `<stack>_inputs.json`, `groups/<stack>_manifest.json` | Frame quality lives in three places. Quarantine measurements of frames that were kept are not stored. |
| FITS headers | WCS, `EXPTIME`, `STACKCNT` | No quality or version keywords. A copied stack carries no record of its quality. |
| Not stored | `QualityDiagnostics` reports, stack comparisons, the asteroid `residual_*` counts, rejected asteroid chains | The `residual_*` counts are passed to `AsteroidDetectionPipelineQualityMetrics`, which has no fields for them, so pydantic drops them silently. |

The models that hold "a number about quality" also differ:

| Shape | Where | Unit | Uncertainty | Limit source |
| :--- | :--- | :--- | :--- | :--- |
| `GateResult` | `models/quality/gate_result.py`, every pipeline | no | no | free text |
| `StageQualityMetric` | `models/quality/spectroscopy_quality.py` | yes | no | no |
| `*PipelineQualityMetrics` (five) | `models/quality/quality_summary.py` | in the field name | rarely | none |
| `FrameRecord.measurements` | `models/target.py` | in the field name | no | none |
| `ProvenancedValue` | `models/camera_profile.py` | no | no | yes |
| `PerformanceThreshold` | `wayfindinglib/models/equipment_and_site/performance_envelope.py` | yes | sample count only | yes (tier) |

`wayfindinglib`'s `Recommendation` stores its evidence as an untyped dictionary. Each
recommendation rule must therefore know each pipeline's internal field names. When a field is
renamed or dropped, as the `residual_*` counts were, nothing fails.

## 2. Decisions

Agreed with the user on 2026-10-17. Decisions 1 to 15 came with the first board review and
decisions 16 to 21 with the second.

1. **`astrometricslib` measures; it does not interpret.** It determines quality metrics.
   `wayfindinglib` or the user decides what they mean. Three kinds of existing code are treated
   as follows:
   - **Processing decisions** stay. A pipeline cannot run without choices such as setting a
     cloudy frame aside or stopping when a plate solve fails. Each choice is recorded as the
     measurement it rested on plus the action taken.
   - **Verdicts about results** become measurements. Instead of `is_poor_match = True`, the
     pipeline reports the fit's relative RMS, the gap to the next class, and an uncertainty or a
     calibrated probability where one can be estimated.
   - **Fitness-for-use judgments** move to `wayfindinglib`, or become rules the user can
     configure. They use the shared limit shape, and the validation register still backs each
     limit.
2. **Self-checks stay in `astrometricslib`.** A self-check asks "did my method work on this
   input?", for example "the plate solve converged" or "enough comparison stars were found". A
   gate that asks "is this result good enough for a purpose?" moves out. A self-check is stored as
   a measurement with a check outcome, not in a table of its own (section 3.3).
3. **Efficiency is computed in `wayfindinglib`, for each science goal and noise regime.**
   Efficiency is a number from 0 to 1: the effective exposure time divided by the exposure time a
   reference frame would need for the same result. It depends on a reference, so it is a judgment,
   not a measurement. `astrometricslib` stores only the raw quantities (star width, sky brightness,
   zero point, read noise and so on). `wayfindinglib` computes efficiency from them against a
   named, versioned reference, for a stated goal and noise regime (section 4). It is shown as time
   for planning and as magnitudes for imaging depth. A loss that more exposure cannot repair
   carries the label "not recoverable with more time". This replaces the earlier decision to
   store efficiency on each measurement.
4. **The equipment fingerprint stays in `wayfindinglib`.** Grouping measurements into one
   equipment setup's baseline is interpretation, so it belongs with the judgments. Each
   `astrometricslib` measurement refers to the capture setting its frames record (camera,
   telescope, focal length, pixel scale, filter, exposure length, gain setting, offset, binning).
   `wayfindinglib` builds the fingerprint from those facts plus the guide optics it already knows.
5. **On-demand diagnostics stay read-only.** `QualityDiagnostics` keeps its rule that a check
   never saves anything, so the read-only AI tools can keep calling it. The pipelines are the only
   writers of measurements. A diagnostic reads stored measurements first and computes only what is
   missing, without writing.
6. **Each pipeline declares its own metrics.** A `metrics.py` beside each pipeline's code holds its
   declarations, and one assembled registry gives `wayfindinglib` a single vocabulary (section
   3.4). A scientist auditing one pipeline finds its metrics in one place, next to the code that
   computes them.
7. **Gain and read noise are keyed by camera and setting.** A camera profile holds gain and read
   noise for each (gain setting, offset, binning) the camera is used at. A frame whose setting has
   no entry gets `NOT_MEASURED` with a reason, never the nearest value. For ZWO cameras the maker's
   published curve may fill a gap, always marked `ASSUMED`. A calibration measurement computes
   gain and read noise from flat and bias pairs, so measuring a new setting is one library call.
   The user works mainly with ZWO cameras.
8. **Raw statistics are always stored; probabilities only where validated.** A verdict field is
   replaced by its raw statistic and uncertainty. A calibrated probability is added as its own
   metric only where a validation register entry shows it was checked on held-out data, and it
   cites that entry. Variability is the first candidate, because its catalog labels and its
   `variability_discrimination` gate already exist.
9. **The public quality API gets its own root, and the MCP tools follow it.** In phase 1,
   `astrometrics.quality` becomes the start of every quality read, stored or live, and the
   diagnostics MCP tools are renamed under it.
10. **Each pipeline's `run_gates.py` is renamed in that pipeline's phase 3 change**, not in
    phase 0, which keeps the phase 0 diff small while many branches are open.
11. **Recommendations never ask for new hardware.** Every warning and recommendation belongs to an
    allowed action class: capture settings, scheduling, calibration, data selection, session
    procedure, or adjustment of existing equipment such as polar alignment, tilt, camera rotation
    or the dew heater. When only new hardware would help, the report states the limit as a
    constraint and gives no advice (section 6.3).
12. **The target dataset report extends the planner's quality advisory.** The user selects a
    target and sees warnings about its data and recommendations for the next session.
    `get_advisory(kind="quality")` in `wayfindinglib` Planning builds it, recomputed on each
    request (section 6).
13. **Imaging uses marginal value, not user-set goals.** The report gives the next step (doubling
    the data), the point where more data stops helping, and the gain per hour across targets.
    Spectroscopy, photometry and asteroid searches use the natural finish line their pipelines
    already measure (section 6.4).
14. **Every metric has a purpose:** decision, context or exploratory, enforced by tests. An
    exploratory metric names its question and a review date (section 6.7).
15. **Forecasts are checked.** When new frames are stacked, the report compares the measured gain
    with the forecast and recomputes the recommendation. The check is rebuilt from the measurement
    history, so recommendations are still never stored (section 6.5).
16. **Syncs stay manual.** A "Sync now" action on Observation Execution fetches the Ekos logs and
    the frames. Frame ingest, the frame measurement job and the report refresh all run after a
    sync the user started. Target changes during the night are read after the sync from the Ekos
    analyze log: a slew that ends in tracking at a new position, matched to the nearest library
    target. `wayfindinglib/drivers/ekos/analyze_log_parser.py` already reads the mount state, the
    mount coordinates and each finished capture. The app shows when the last sync ran, for example
    "last sync 23:40, 25 min ago". Automatic syncing is a later option and is not designed now.
17. **Each night's contribution to a stack comes right after subset stacking.** It reuses the
    subset-stacking step that noise against frame count already needs (section 5.1, phase 3), so it
    is not postponed. The asteroid completeness grid and the asteroid null search (the per-night
    false-positive rate) wait until asteroid searches are a regular part of the user's nights.
18. **`astrometricslib` holds no observing advice, including the advice it holds today.** It keeps
    only the measurement. The exposure recommendation and the "take more flats" message move to
    `wayfindinglib`'s result-quality rules (section 7, fifth group). Night and target summaries,
    including the loss account, are computed when read in `wayfindinglib`, so the measurement shape
    has no night or target level (section 3.1). Guide RMS, pointing error, dew, Moon position and
    airmass are computed in `wayfindinglib` when read (`QUALITY_SCIENCE_PLAN.md`).
19. **The desktop app is a thin wrapper, and every behavior has a library owner.** No ranking,
    grouping, classification, text composition, unit conversion or join of library facts happens
    in `ui/`, `electron/` or `backend/`. Section 6.1 names the owner of each behavior.
20. **Monitor mode is `wayfindinglib`'s existing delegation policy.** It means every capability is
    delegated through `control.safety.enter_monitoring_mode` (`wayfindinglib/api/control/safety.py`).
    The app reads the policy and greys its controls. The guarantee that no command is sent is tested
    in `wayfindinglib`, where backend and MCP calls are refused.
21. **The night plan is stored in `wayfindinglib`.** It holds the capture setting (including gain
    setting, offset and binning), the checklist and the framing. It is written from the first edit,
    has a revision check so two windows cannot overwrite each other, and raises a plan-changed
    event (section 6.9).
22. **Real-data checks use data already taken.** Agreed with the user on 2026-10-19: no phase
    needs a new observing night. Phase 0 ends with a read-only check on a library target. Phase 2
    first runs on one recorded night of one target, after a database and config backup. The
    forecast check (section 6.5) replays a target's recorded nights.
23. **The quality root is `QualityRecords`.** Agreed with the user on 2026-10-21, in phase 1, so
    the public API breaks once, together with the move from `processing.diagnostics`. Its live
    checks are `measure_frames`, `measure_stack` and `check_spectral_frames`, beside `query` and
    `metrics`. The MCP tools are `quality_measure_frames`, `quality_measure_stack`,
    `quality_check_spectral_frames`, `quality_query` and `quality_metrics`.
24. **Frames are measured in worker processes.** Agreed with the user on 2026-10-23, in phase 2:
    four worker processes started with "spawn", chosen for speed over threads (which the star
    fitting cannot use in parallel) and over a threads-only default. The cost is memory (about
    2.7 GB on real data) and one rule for callers: a script that starts the job must keep its
    top-level code under a `__main__` guard (Appendix E).
25. **The spectral registration check no longer tests the background level.** Agreed with the user
    on 2026-10-23, in phase 3: Siril's sequence column the check read as a registration error is
    the frame's sky background level (Siril's reader, `src/io/seqfile.c`), so that part never
    tested alignment; it is dropped, because the quarantine's cloud check and the
    background-homogeneity check already watch the sky. A real alignment check comes with the
    field-rotation metric (`QUALITY_SCIENCE_PLAN.md`).
26. **A limit tier for design estimates.** Agreed with the user on 2026-10-23, in phase 3:
    `LimitTier` gains `design_estimate`, for a limit that was set by design and not yet
    measured, matching the validation register's "Design estimate" status. Such limits were filed as
    `physical_budget`, which overstated them; `physical_budget` now means a limit derived from
    physics only.

## 3. The shared shape

The design separates three records that the current shapes mix together.

| Record | What it is | Lives in | Stored? |
| :--- | :--- | :--- | :--- |
| Measurement | A fact: what was measured, about what, with what uncertainty, by which run. A self-check is a measurement with a check outcome. | `astrometricslib` | Yes, append-only, with history |
| Judgment | A measurement compared with a limit, for a purpose | `wayfindinglib` | No, recomputed |
| Recommendation | What to change, ranked by efficiency lost | `wayfindinglib` | No, recomputed (as `session_analysis` does now) |

A measurement never goes stale. A judgment depends on a limit or a reference, and those change
when the equipment, the baseline or the validation changes, so a judgment must be cheap to redo.

### 3.1 Measurement

A sketch. Names follow the repository's descriptive-naming standard and may change in review.

```python
class SubjectLevel(StrEnum):
    FRAME = "frame"
    STACK = "stack"
    RUN = "run"


class QualitySubject(BaseModel):
    level: SubjectLevel
    subject_id: str  # a stable id: target plus relative frame path, stack id or run id; never a mount path
    target_id: str | None
    observing_night_id: str | None  # a plain time label from utilities/observing_night.py
    capture_setting_id: int | None  # row in the capture-setting table (decision 4, section 6.2)
    channel: str | None  # color channel for a one-shot-color camera, else None


class MeasurementStatus(StrEnum):
    MEASURED = "measured"
    ASSUMED = "assumed"  # e.g. a camera gain taken from the maker's curve
    NOT_MEASURED = "not_measured"  # always carries a reason; never a default value


class MeasurementOrigin(StrEnum):
    MEASURED = "measured"  # written by a pipeline run
    BACKFILLED = "backfilled"  # copied from an existing JSON summary


class QualityMeasurement(BaseModel):
    metric: str  # a registered metric name, e.g. "frame.star_width_moffat_arcsec"
    definition_version: int  # the registered definition version of that metric
    subject: QualitySubject
    status: MeasurementStatus
    value: float | None
    uncertainty: float | None
    sample_count: int | None  # e.g. the number of stars behind a width
    reason: str = ""
    # A self-check or a processing decision also carries these:
    check_outcome: GateStatus | None  # passed, failed or not_checked
    check_limit: QualityLimit | None
    action_taken: str | None  # e.g. "frame moved to _excluded/"
    origin: MeasurementOrigin
    pipeline_version: str | None  # None when a backfilled row did not record it
    run_id: str  # the job id, or a generated id when there is no job
    job_id: str | None  # soft link to prov_activity in astrometrics_log.db
    measured_at: datetime
```

A measurement holds no efficiency and no baseline-relative expected value, because both depend on
a reference (decision 3). An expected value that physics or the sensor profile fixes, such as the
noise expected from √N with a measured read noise, is stored as its own metric with its source,
so a ratio's numerator, denominator and model version stay separate.

There is no night or target level (decision 18). A night summary or a target summary, such as
the median star width of a night or a target's loss account, is an aggregate that `wayfindinglib`
computes when it is read. `observing_night_id` stays on each measurement as a plain time label, so
`wayfindinglib` can group by it.

Per-star results stay in the star records (section 3.5). Per-star self-checks, such as today's
`verdict_checks`, stay there too, in the same measurement shape.

### 3.2 Limit

`QualityLimit` merges `GateResult`'s limit fields with `wayfindinglib`'s `PerformanceThreshold`.
It lives in `astrometricslib`, because self-checks use it, and `wayfindinglib` imports it from the
top-level package. `PerformanceThreshold` migrates to it in phase 4, so the two models do not
co-exist.

```python
class LimitTier(StrEnum):
    GEOMETRY = "geometry"
    SENSOR_PROFILE = "sensor_profile"
    PHYSICAL_BUDGET = "physical_budget"
    OWN_BASELINE = "own_baseline"
    VALIDATED_DATASET = "validated_dataset"


class QualityLimit(BaseModel):
    value: float | None
    unit: str
    tier: LimitTier
    derivation: str
    sample_count: int | None
    register_entry: str | None  # e.g. "STK-07"; required for VALIDATED_DATASET
```

### 3.3 Judgment

`QualityJudgment` lives in `wayfindinglib` and is never stored. It refers to the exact
measurement it judged, so a judgment over an append-only history is never ambiguous. A row in
`quality_measurements` has a row id. A per-star result in `stellar_objects` does not, so it is
referenced by star, run and metric instead.

```python
class StarResultReference(BaseModel):
    star_id: str
    run_id: str
    metric: str  # a registered metric name


class MeasurementReference(BaseModel):
    row_id: int | None  # a row in quality_measurements
    star_result: StarResultReference | None  # a per-star result in stellar_objects
    # exactly one of the two is set


class QualityJudgment(BaseModel):
    name: str  # the rule's name; a moved gate keeps its existing name
    measurement: MeasurementReference  # what it judged
    status: GateStatus  # passed, failed or not_checked, as today
    limit: QualityLimit | None
    detail: str
```

A self-check in `astrometricslib` is not a `QualityJudgment`. It is a measurement with
`check_outcome`, `check_limit` and `reason` filled in (section 3.1). This keeps one store. The
asymmetry is deliberate: a self-check is a fact about one run against a method limit, while a
fitness judgment depends on a baseline that changes.

### 3.4 The metric registry

The registry is what makes the shape shared in practice. Each pipeline declares its metrics in
its own `metrics.py` (decision 6). Metrics shared by several pipelines, such as frame star width,
are declared in `pipelines/shared/quality/metrics.py`. `metric_registry.py` assembles every
declaration into one registry, which is exported as public API (read-only), so `wayfindinglib`,
the backend and the MCP servers can read it through `astrometricslib`'s top-level package. Names
carry their pipeline or checkpoint, for example `stacking.noise_excess_ratio` or
`frame.star_width_moffat_arcsec`. Two estimators of one quantity, such as Siril's FWHM and the
library's own, get two names, never one.

```python
# pipelines/shared/quality/metrics.py
FRAME_STAR_WIDTH_MOFFAT_ARCSEC = "frame.star_width_moffat_arcsec"

FRAME_METRICS = (
    MetricSpec(
        name=FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
        definition_version=1,
        unit="arcsec",
        checkpoint=Checkpoint.FRAME,
        levels=frozenset({SubjectLevel.FRAME}),
        better=Direction.LOWER,
        meaning="Median full width at half maximum of stars spread over the whole frame.",
        decisions=("focus", "guiding", "seeing"),  # documentation only; nothing in astrometricslib reads it
        estimator="Pixel-integrated Moffat fit; no elongation cut",
        sample_definition="Up to 200 isolated, unsaturated stars on a grid over the field",
        band="Frame filter",
        computed_by="pipelines.shared.quality.frame_measurement.measure_star_width",
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method="Bootstrap of the median",
        register_entry=None,
        purpose=MetricPurpose.DECISION,  # section 6.7
    ),
)
```

A `MetricSpec` holds:

- the name, its `definition_version` and the unit, the unit checked against every measurement,
- the estimator, the sample definition, the band or channel, the function that computes it, the
  kind of uncertainty and how it is found, and its register entry, which is what an auditor
  checks,
- the checkpoint (section 5) and the subject levels it applies to,
- which direction is better,
- a one-line meaning in plain words,
- the decisions it informs, as plain documentation: nothing in `astrometricslib` reads this field,
  and the rules that act on a metric live in `wayfindinglib`,
- its purpose: decision, context or exploratory, with a reason, or a question and a review date
  (section 6.7).

A change to a metric's estimator, sample or constants raises its `definition_version`, which is
stored on every row. `pipeline_version` changes for unrelated reasons, so it cannot show this. A
renamed metric gets no alias: every reader is updated and the stored rows are migrated once, as
rule 7 of `ARCHITECTURE_PLAN.md` requires for the public API.

The review-guide generator renders each pipeline's declarations as a metrics table in that
pipeline's guide.

### 3.5 Storage

One table, `quality_measurements`, in `astrometrics.db`, written by
`drivers/quality_store.py`. The store uses plain SQL, modeled on `drivers/provenance_store.py`,
not the generic `Butler`: the `Butler` is a key/value store that writes with `INSERT OR REPLACE`
and has no views, ordering or exact filters, which an append-only, filtered table needs.

- **Typed columns**, one per field in section 3.1, with no JSON copy. The schema carries a version
  in its own table, `quality_schema_version`, and the store migrates it explicitly. It never uses
  `PRAGMA user_version`, which is one number for the whole file: the real `astrometrics.db` already
  held 2 from an older stellar-catalog mechanism (found on real data, 2026-10-22).
- **Capture settings in their own table**, `quality_capture_setting`, referenced by
  `capture_setting_id`. A capture setting is the camera, telescope, focal length, pixel scale,
  filter, exposure length, gain setting, offset and binning a frame was taken with. Nine
  repeated columns on every row would make a row 300 to 600 bytes. With the reference, a row is
  closer to 150 bytes.
- **No duplicates.** A `UNIQUE(run_id, metric, subject_level, subject_id, channel)` constraint
  means a re-run of a work item after a crashed worker cannot write a row twice.
- **One transaction per run.** Parallel workers each write their rows in one transaction, inside
  the existing busy timeout of `local_database.connect_db`.
- **A soft link to provenance.** `job_id` points into `astrometrics_log.db`, so the database cannot
  enforce the link. Readers that join the two must expect a missing job.
- **A failed write never fails the science run.** It raises `StorageError` (from
  `foundation/errors.py`), the run logs it and the row count, and the run's own outputs are kept.

Agreed with the user on 2026-10-17, so the table scales:

- **Narrow rows for frames, stacks and runs.** Adding a metric adds a registry entry, never
  a column.
- **Stars stay in their star records.** Per-star results are the largest volume (about 2,000 stars
  times 10 metrics per run). They stay in `stellar_objects`. A star-level metric that a
  recommendation needs is summarized per run (median, spread, count) into the narrow table.
- **Indexes** on (metric, observing night), (target, metric) and (subject id).
- **A "latest" view** picks, for each subject, metric and channel, the row with the newest
  `measured_at`. Version strings do not sort reliably ("1.10.0" sorts before "1.9.0") and a
  restack at the same version is common, so the view does not use `pipeline_version`.
- **Nothing is deleted at first.** A retention rule, such as dropping superseded rows after a year,
  comes later if size becomes a problem.
- **The table is backed up with the database.** It cannot be rebuilt cheaply, because the raw
  frames live on remote storage.

The JSON summaries stay as a convenience view until their readers move (phase 7).

### 3.6 Reading

One public call with parameters, not one function per metric (decision 9):

```python
library.quality.query(
    metric=..., level=..., target_id=..., observing_night_id=...,
    camera=..., telescope=..., since=..., row_ids=..., latest_only=True,
) -> list[QualityMeasurement]
```

The `row_ids` filter returns exactly the rows a statement cites as evidence, so the app can show
them without a join of its own. `library.quality.metrics()` returns the registry. `wayfindinglib`, the backend and the MCP servers
all read through these calls. Phase 1 adds the backend RPC methods to
`backend/public_interface.py`, regenerates the UI types with `build/codegen/generate-types.sh`,
and sets the MCP tool dispositions for the new calls.

### 3.7 Conformance tests

The tests check statically, like `pipelines/test/test_gate_conformance.py`, so they do not depend
on which other tests ran:

- emitters refer to metric-name constants from a `metrics.py`, never to string literals;
- each `MetricSpec.computed_by` resolves to a real function;
- no two declarations share a name, and units are known units;
- each pipeline on an explicit list of pipeline folders contributes a `metrics.py`;
- a pipeline that has reached phase 3 produces every metric it declares (this check is switched on
  per pipeline as it reaches phase 3);
- every `VALIDATED_DATASET` limit names a register entry that exists;
- a `NOT_MEASURED` measurement has a reason and no value.

The checks that cross the two libraries live in `wayfindinglib/test/`, because `astrometricslib`
and its tests may not import `wayfindinglib`:

- every rule's `requires=(...)` names a registered metric;
- every rule declares an allowed action class (section 6.3);
- every metric's purpose holds: a decision metric is required by at least one rule, a context
  metric is shown by at least one report section, and an exploratory metric's review date has not
  passed (section 6.7).

With these tests, a rule can no longer depend on a field that was renamed or dropped without a
test failing.

### 3.8 Where the code lives

Quality code was spread out: ten model files loose in `models/`, frame checks in
`pipelines/shared/quality/`, gate files in four pipelines, and `QualityDiagnostics` inside
`api/processing.py`. Phase 0 moved the models and `QualityDiagnostics`; the gate files move in
phase 3. The rule for this work is one quality home per layer. Agreed with the user
on 2026-10-17:

- All quality models moved into one subpackage, `models/quality/`, in phase 0. The top-level
  imports did not change.
- `QualityDiagnostics` moved from `api/processing.py` to `api/quality.py` in phase 0, keeping the
  path `astrometrics.processing.diagnostics`. The stacking argument helpers it shared with
  `processing.py` went to `pipelines/shared/api_arguments.py` (Appendix C). Phase 1 made it the
  `astrometrics.quality` root, renamed `QualityRecords`, removed `processing.diagnostics` and
  renamed the MCP tools (decisions 9 and 23).
- Every write of a measurement goes through `pipelines/shared/quality/record_measurements.py`,
  which checks it against the registry (name, definition version, subject level, limit unit and
  uncertainty kind) before the store saves it. The store cannot do this itself, because `drivers/`
  may not import `pipelines/` (Appendix D).
- Each pipeline's `run_gates.py` is renamed `record_quality.py` in that pipeline's phase 3 change
  (decision 10).

| Layer | Home | Holds |
| :--- | :--- | :--- |
| Models (shapes only) | `astrometricslib/models/quality/` | Measurement and limit shapes; today's quality models, moved |
| Shared calculations | `astrometricslib/pipelines/shared/quality/` | The assembled registry, the frame measurement step |
| Each pipeline | `<pipeline>/metrics.py` and its `record_quality.py` | The declaration of its metrics, and what it emits |
| Storage | `astrometricslib/drivers/quality_store.py` | The measurement and equipment tables, in plain SQL |
| Public API | `astrometricslib/api/quality.py` | `query`, `metrics` and the live checks |
| Judgments and efficiency | `wayfindinglib/analytics/result_quality/` | The fitness-for-use rules and the observing-advice rules as a table, the efficiency references and functions, night and target aggregates, the focus-temperature slope fit |
| Recommendations | `wayfindinglib/session_analysis/` | Guiding, capture and sky recommendations |
| Night plan | `wayfindinglib/models/planning/` and Planning's plan store | The stored night plan, its checklist and framing (section 6.9) |

The rules and efficiency live in `wayfindinglib/analytics/`, next to `performance_envelope.py`,
because both Control (`session_analysis`) and Planning (`quality_advisory_tasks.py`) need them,
and Planning may not import Control.

```text
astrometricslib/
├── models/quality/
│   ├── measurement.py        QualitySubject, QualityMeasurement, MeasurementStatus, MeasurementOrigin
│   ├── limit.py              QualityLimit, LimitTier (replaces gate_result.py)
│   ├── metric_spec.py        MetricSpec, Checkpoint, Direction, UncertaintyKind
│   └── (moved)               quality_summary, quality_reports, the per-pipeline *_quality files,
│                             excluded_frames, stack_comparison, gaia_xp_comparison
├── pipelines/
│   ├── shared/quality/
│   │   ├── metrics.py        frame and calibration metric declarations
│   │   ├── metric_registry.py    assembles every metrics.py into one registry
│   │   └── frame_measurement.py  the frame measurement step
│   ├── asteroid_detection/
│   │   ├── metrics.py
│   │   └── record_quality.py     (renamed from run_gates.py; this pipeline has no post_processing/)
│   └── <other pipelines>/
│       ├── metrics.py
│       └── post_processing/record_quality.py    (stacking gains one; its checks stay put)
├── drivers/quality_store.py
└── api/quality.py            QualityRecords: the quality root (moved in phase 0, renamed in phase 1)

wayfindinglib/
├── analytics/result_quality/
│   ├── rules.py              one row per moved gate or advice rule: metric, limit, wording, required metrics
│   ├── judge_results.py
│   ├── aggregates.py         night and target summaries, computed when read
│   └── efficiency.py         references, goals, noise regimes, loss account, tonight's predicted efficiency
├── models/planning/
│   ├── night_plan.py         the stored night plan, checklist items, framing
│   └── target_report.py      TargetDatasetReport, SequenceSummary (replaces quality_advisory.py)
└── session_analysis/         guiding/, capture/, sky/ read through astrometrics.quality.query
```

The count is about thirteen new modules, four renamed, eleven moved and one merged away, plus tests.
The layer contracts do not change.

## 4. Efficiency: how `wayfindinglib` ranks losses

Efficiency $\eta$ is the effective exposure time divided by the time a reference frame would need
for the same result, from 0 to 1. `wayfindinglib` computes it from raw measurements (decision 3).

**The reference.** A named, versioned reference sets each factor from a physical floor and the
equipment's own baseline, and reports $\eta$ against both:

- star width: the larger of the telescope's diffraction limit and the site's seeing, combined with
  the guiding error and the pixel scale;
- sky brightness: the site's dark-zenith value;
- transparency: the equipment's best zero point, corrected for airmass.

Against the baseline alone, a lasting fault would read as normal: if focus is always soft,
$\eta$ against the baseline is about 1. The physical reference shows it.

**Undersampling.** When the pixels are large next to the star, the pixel size adds to the width
the detector records. The seeing factor therefore uses an effective width,
$\mathrm{FWHM}_\mathrm{eff}^2 \approx \mathrm{FWHM}^2 + c\,p^2$, where $p$ is the pixel size in
arcsec and $c$ is fitted on this setup. The physical reference includes the pixel scale for the
same reason.

**The goal and the noise regime.** The exponents differ by goal and regime, so $\eta$ is always
stated for one goal in one regime. The regime is found from the measured sky noise, read noise and
source signal, which are stored as separate parts (section 5.1).

| Goal and regime | Seeing factor | Notes |
| :--- | :--- | :--- |
| Faint point source, sky-limited | $(\mathrm{FWHM}_{\mathrm{eff},0}/\mathrm{FWHM}_\mathrm{eff})^2$ | Transparency enters as $(T/T_0)^2$: one magnitude of cloud costs a factor of 6.3 in time. Sky enters as $B_0/B$. |
| Faint point source, read-noise-limited | Depends on sky noise over read noise | Per frame, $\eta_\mathrm{RN} = \sigma_\mathrm{sky}^2/(\sigma_\mathrm{sky}^2 + \mathrm{RN}^2)$. Short frames and narrowband reach this regime. |
| Astrometric precision | $(\mathrm{FWHM}_{\mathrm{eff},0}/\mathrm{FWHM}_\mathrm{eff})^4$ | Position error scales as width over SNR. |
| Extended object | Not a time loss | Seeing costs resolution, which more time does not repair. |
| Bright-star photometry | Not a time loss beyond the floor | Scintillation or a systematic floor limits it. |
| Slitless spectral resolution | Not a time loss | Seeing sets the resolution. |
| Period detection | Not a time loss | The spacing of the nights (the window function) limits it, not the hours. |

**Combining causes.** Factors multiply. To say how much each cause cost, `wayfindinglib` splits
$\ln\eta$ across the causes in proportion to each factor's $\ln$.

It converts to the two views people use:

- **Time.** Effective time is $\eta$ times the exposure time, for example "4.0 h captured, 2.6 h
  effective; clouds cost 0.8 h".
- **Depth.** For a sky-limited point source the depth lost is $1.25\log_{10}(1/\eta)$ magnitudes.
  The depth view states its regime, because the formula does not hold in the others.

Losses that more exposure does not repair carry `recoverable_with_time = False`. That label is
advice in itself, such as "fix the focus; another three hours will not help".

### 4.1 The loss account

A night's or a target's loss account is computed in `wayfindinglib` when it is read (decision 18).
It has two parts that are never added into one number without saying so:

- **Frames set aside.** Their exposure time is time really lost, with the reason the stacker or
  the live status gave. At night, set-aside reasons exist only after stacking moves frames to
  `_excluded/`, so until then the account shows the frames the live status marks as ruined.
- **The efficiency deficit of kept frames.** Captured time minus effective time, split by cause.

Each account states its goal and its reference. For an extended object seeing is not a time loss,
so it is not listed as one. Transparency is shown as "not included" until the catalog zero point
of `QUALITY_SCIENCE_PLAN.md` exists. The Moon's share needs a sky-brightness model, so it is
labeled "model-derived".

The split always adds up, because whatever no cause explains is listed as "cause unknown". A test
that the account adds up therefore cannot fail. The attribution is validated instead on nights
whose cause is known, such as a night with logged cloud, a night with a known focus fault and a
moonlit night, and the result goes into the validation register.

### 4.2 Ranking targets

`get_advisory(kind="tonight", at=...)` in `wayfindinglib` Planning ranks the candidate targets
for a night, and ranks them again when called later in the night with a new time. The app shows the
list as it comes. A target with no data yet is marked "new" and is not ranked, because it has no
noise history to rank from.

- **Predicted efficiency for tonight.** The rank needs the efficiency each target would have
  tonight: sky brightness from the Moon and the target's altitude, plus extinction. This is a
  model, labeled as one, with its own validation register entry.
- **Wall-clock time.** Gains are per hour of the night, counting downloads, dither settling and
  autofocus, not only exposure time. For 60 frames of 30 s with a dither every 3 frames, exposure
  time alone understates the hour by about 10 to 30%.
- **Stop signal.** A target whose noise has stopped falling (section 6.4) ranks at zero until the
  cause is fixed.
- **Thin datasets.** The fractional noise gain from one more hour favors targets with little data.
  The ranking states this, so a new or thin target leading the list is not a surprise.
- **Separate lists.** Photometry and spectroscopy targets are ranked apart from imaging targets,
  because their gains are in different units. Asteroid searches are not ranked.

**"Take N more" in effective time.** When the recommended exposure length differs from the one
already used, N is counted in effective time, not frame count, using $\eta_\mathrm{RN}$ above. A
30 s frame that is read-noise-limited counts for less than 30 s of a sky-limited frame.

Efficiency functions, references and the tonight model are design estimates until checked on
real data. Each one gets a validation register entry before `wayfindinglib` ranks advice by it.

Phase 4 builds efficiency from the metrics of section 5.1: star width, sky noise and read noise.
The transparency factor needs the catalog zero point, which is in `QUALITY_SCIENCE_PLAN.md`. Until
that lands, efficiency states that transparency is not included.

## 5. Metrics this plan carries

This plan carries the metrics that already exist, moved into the new shape, plus the few new ones
that efficiency and the target dataset report (section 6) need first. Every other new metric,
with its definition and its evidence, is in `QUALITY_SCIENCE_PLAN.md`.

### 5.1 New early, because efficiency and the target report need them

| Metric | Definition | Why now |
| :--- | :--- | :--- |
| Star width from many stars across the field, with elongation and its angle | A pixel-integrated Moffat fit to up to about 200 isolated, unsaturated stars on a grid over the field, with no elongation cut. Elongation and its angle come from second moments. Stored in pixels and in arcsec, with the plate-scale source. | Today's `measure_image_fwhm` fits a Gaussian to the first 15 bright stars that fit cleanly and returns pixels. A star stretched by more than 1.5 is kept at its narrow-axis width, so on a trailed frame the value reads sharp (registered as `frame.star_width_gaussian_brightest_px`, Appendix D). Efficiency's seeing factor would inherit that bias. |
| Sky brightness in electrons | Background in electrons per second per pixel, from the background level, the gain for the frame's setting and the exposure time | Efficiency's sky factor and the read-noise regime |
| Sky noise and read noise, stored as two parts | The sky noise of the frame in electrons, and the read noise for the frame's setting. The ratio is computed when read, never stored alone. | Decides the noise regime (section 4), says when an exposure is too short, and counts "take N more" in effective time |
| Gain and read noise for each camera setting | Photon transfer from flat pairs and the noise of bias pairs, at each gain setting, offset and binning in use (decision 7) | Every metric above in electrons depends on it |
| Focuser temperature | The focuser temperature each frame records (`FrameRecord.focuser_temperature_c` today), declared as a frame metric | With star width, gives the focus drift slope that `wayfindinglib` fits (section 6.6) |
| Stack noise in empty apertures (phase 3) | Noise of the stack in random apertures of the photometric size, placed away from sources, with the aperture stated. The apertures are fixed in sky coordinates through the WCS, so every stack of a target uses the same ones and two stacks compare as a paired ratio. The noise is given on a fixed flux scale: normalized to the zero point, or as the SNR of a fixed set of reference stars. Registration correlates neighboring pixels, so pixel noise underestimates the real noise. | The "more frames" recommendation and the forecast check (sections 6.4 and 6.5) |
| Stack noise against frame count (phase 3) | Empty-aperture noise and the weighted effective frame count and time of each stack run, kept in the history, plus subset stacks when a run has too few predecessors | Gives the count in "take N more", and shows when noise stops falling |
| Frame membership and weights of each stack run (phase 3) | One frame-level row per input frame of a run: the frame, the run and the weight the stacker gave it. Today `<stack>_inputs.json` holds this but is overwritten on each stack. | The forecast check models the weights the stacker really used (section 6.5) |
| Each night's contribution to a stack (phase 3) | The noise of subset stacks with and without each night's frames (decision 17) | Shows which nights helped a target and which did not |

### 5.2 Existing metrics emitted in the new shape (phases 2 and 3)

| Checkpoint | Metrics | Today's home |
| :--- | :--- | :--- |
| Each frame | Background level, saturated fraction, registration star width, roundness, shift and star count; quarantine star count, roundness and sky for every frame; sensor temperature; brightest star's peak as a fraction of saturation at each exposure length | `FrameRecord.measurements`, the quarantine manifest |
| Calibration | Flat noise and level, calibration mismatches, master frame counts | `StackQualitySummary` |
| After stacking | Rejected-pixel fraction, saturated and zero-pixel fractions, negative pixels, stack star width against input width, exposure-group results | `StackQualitySummary` |
| Astrometry | Plate-solve residual and star count, catalog separation, Gaia refit residual and shift, match counts | `AstrometryQualitySummary`, `CatalogMatchQuality` |
| Photometry | Flux errors, input and output quality, variability indices, ensemble scatter, noise-model curve, period and transit statistics | `PhotometryQualitySummary`, `PhotometryResult` |
| Spectroscopy | The four stage checkpoints, wavelength zero point, Gaia XP comparison, line spread, noise model, classification statistics | `SpectroscopyQualitySummary`, `SpectroscopyResult` |
| Asteroid detection | Cascade counts, per-frame position error, track fit, ephemeris separations, the `residual_*` counts (no longer dropped) | `AsteroidDetectionQualitySummary`, candidates |

Each existing metric keeps its current estimator under a name that says so. An improved estimator
from the science plan gets a new name (decision 6).

## 6. Target dataset report

This is where the measurements reach the user. The user selects a target and sees, for that
target's data, warnings about what was already taken and recommendations for the next session.
A warning reads like "frames at 120 s clip the target core". A recommendation reads like "take 20
more frames at 90 s". Agreed with the user on 2026-10-17 (decisions 11 to 15, and 18 to 21).

### 6.1 Where it lives

```text
Desktop app: the user selects a target
  → backend RPC method
    → wayfindinglib Planning: get_advisory(kind="quality", target_id=...)
        reads astrometrics.quality.query(target_id=...)     the measurements
        + camera profiles and the equipment catalog          what is possible
        + the calibration library (CalibrationCatalog.query) what calibration exists
        + the performance envelope                           the limits
        + the stored night plan                              the checklist (section 6.9)
        + visibility, Moon and sky history                   when to observe
        applies the rules in wayfindinglib/analytics/result_quality/
      → TargetDatasetReport, recomputed on each request and never stored
```

The report extends the planner's existing quality advisory (decision 12), which already has the
right owner. It is forward-looking, so it belongs to Planning. The per-night view stays in
`session_analysis` (Control). Both use the same rules from `analytics/`, which both layers may
import.

`TargetDatasetReport` replaces `TargetQualityAdvisory` as the return of
`get_advisory(kind="quality")`. There is no second shape, as rule 7 of `ARCHITECTURE_PLAN.md`
requires. `TargetQualityAdvisory` is also read by `_apply_advisory_boost` in
`wayfindinglib/tasks/planning_tasks/scheduling.py`, which raises a package's priority for flagged
targets, so that reader is updated in the same change. The top-level export in
`wayfindinglib/__init__.py` changes with it.

**Every behavior has a library owner (decision 19).** The app shows what the libraries return. It
does no ranking, grouping, classification, text composition, unit conversion or join of its own.
Each behavior the app or an AI tool needs has an owner and a backend RPC method:

| Behavior | Library owner | Notes |
| :--- | :--- | :--- |
| The target dataset report | `wayfindinglib` Planning `get_advisory(kind="quality", target_id=...)` | Section 6.2 |
| Tonight's ranking and re-ranking during the night | `get_advisory(kind="tonight", at=...)` | Returns the ranked candidates; new targets are marked "new", not ranked (section 4.2) |
| Trust in one science result | `get_advisory(kind="result", target_id=..., object_id=...)` | Uses the per-star reference of section 3.3. The Astronomy Manager shows it as a quality chip with the same statement component as the report. |
| Night review | `wayfindinglib` `control.history.query(kind="night_quality", night_id=...)` | A new kind with a typed model and an RPC method. Today `query` returns untyped dictionaries, and no RPC method reads a night's quality. |
| Live session status | `control.history.get_live_session_status` | A typed model and an RPC method, neither of which exists today |
| List of nights with data | A `control.history` call that lists nights | Fills the app's night selector |
| Sequence summary for Ekos | `SequenceSummary`, built from the stored night plan | Rows, not a string (section 6.9) |
| Calibration advice | Folded into the report's rules | Section 6.10 |
| Framing | `wayfindinglib` Planning `calculate_panels` | The app does no geometry of its own (section 6.9) |
| Evidence for a statement | `astrometrics.quality.query(row_ids=...)` | Section 3.6 |
| Refresh after new measurements | A backend event, `quality:measured` | Below |

The night review and live status reads are called with `register_job=False` and `refresh=False`.
They read what the last sync copied (decision 16) and do not start a job or a log copy of their
own.

When the frame measurement job finishes, it raises a declared backend event,
`quality:measured` with the target id and the observing night ids (`observingNightIds`). It is modeled on the `catalog:changed`
event in `backend/services/data/stellar_service.py` and declared in
`backend/public_interface.py`. Today `backend/services/processing/ingestion_service.py` raises no
event. Writes made from the MCP process do not raise it; the app sees those rows on its next
read.

### 6.2 What it holds

The unit of the report is the **capture setting**: one camera and telescope, with one filter,
exposure length, gain setting, offset and binning. Warnings and recommendations are statements
about one setting, so every measurement refers to its capture setting (section 3.1).

- **Dataset summary.** One row per capture setting: frames taken, nights, frames set aside,
  captured time, effective time, and the loss account (section 4.1).
- **Statements.** Warnings about the data already taken, recommendations for the next session,
  limits of the equipment, and data that is missing. They arrive in priority order.
- **Needs attention.** The count and worst severity of the warnings, so the night review can mark a
  target without reading the whole report.
- **Starting point.** For a target with no frames yet, a first capture setting (section 6.8).

A sketch:

```python
class ActionClass(StrEnum):
    CAPTURE_SETTINGS = "capture_settings"
    SCHEDULING = "scheduling"
    CALIBRATION = "calibration"
    DATA_SELECTION = "data_selection"
    SESSION_PROCEDURE = "session_procedure"
    ADJUSTMENT = "adjustment"


class StatementKind(StrEnum):
    WARNING = "warning"
    RECOMMENDATION = "recommendation"
    CONSTRAINT = "constraint"  # a limit of the equipment, shown as "Limit"; never advice
    INSUFFICIENT_DATA = "insufficient_data"  # says what is missing and how to get it


class Confidence(StrEnum):  # the validation register's status labels
    MEASURED = "measured"
    PARTLY_MEASURED = "partly_measured"
    DESIGN_ESTIMATE = "design_estimate"  # shown as "Tentative"


class TargetStatement(BaseModel):
    rule: str  # the rule that produced it
    kind: StatementKind
    severity: Severity | None  # for a warning
    action_class: ActionClass | None  # None for a constraint or insufficient data
    capture_setting: CaptureSetting | None
    message: str  # one plain sentence
    reason: str  # why; for insufficient data, what is missing
    proposed_action: ProposedAction | None  # typed values: exposure, frame count, filter, dither, window...
    expected_effect: ExpectedEffect | None  # e.g. noise down 29%, efficiency up 0.2
    evidence: list[MeasurementReference]  # section 3.3
    limits: list[QualityLimit]
    confidence: Confidence
    checklist_return: ChecklistReturn | None  # set when a ticked checklist item came back (section 6.9)


class NeedsAttention(BaseModel):
    warning_count: int
    worst_severity: Severity | None


class TargetDatasetReport(BaseModel):
    target_id: str
    capture_settings: list[CaptureSettingSummary]
    statements: list[TargetStatement]  # already in priority order
    needs_attention: NeedsAttention
    starting_point: StartingPoint | None  # section 6.8
    forecast_checks: list[ForecastCheck]  # section 6.5
    generated_at: datetime
```

The library orders the statements and sets their kind and confidence. The app does no sorting or
classification: it shows the first three and offers "show all N". Every statement shows its
evidence and its confidence. A rule whose register entry is still a design estimate is shown as
"Tentative". Missing data is an explicit `insufficient_data` statement with its reason, never
silence.

### 6.3 What may be recommended

The report never recommends changing hardware: no new telescope, camera, mount, guide scope,
filter or other purchase. Every rule declares one of these action classes (decision 11):

| Class | Examples | Allowed values come from |
| :--- | :--- | :--- |
| Capture settings | Exposure length, frame count, gain setting, offset, binning, filter, dither interval and size | The camera profile's measured settings and the filters in the equipment catalog |
| Scheduling | Altitude window, Moon limits, nights to add, revisit within a time | Visibility and planning |
| Calibration | Take or retake darks, flats or bias for a setting | The calibration library |
| Data selection | Frames set aside or restored, restack | The quality measurements |
| Session procedure | Refocus interval, guider recalibration, guide exposure | The performance envelope |
| Adjustment of existing equipment | Polar alignment, tilt or collimation, camera rotation, dew heater | The equipment the user has |

A test in `wayfindinglib/test/` fails a rule that declares no class, and a proposed value outside
the equipment's own options cannot be built. When only new hardware would help, the report states
the limit as a constraint, for example "seeing limits the resolution on this target; more time
adds depth, not detail", and gives no advice about it.

### 6.4 How much more data

"More data makes a better image" is true with two catches. Returns shrink, because noise falls as
the square root of the time: doubling the data cuts noise by about 29%. And some problems more data
cannot fix, such as a fixed pattern or soft focus. For imaging the report therefore uses marginal
value instead of a goal the user must set (decision 13):

- **The next step.** "Doubling your effective time (20 more 90 s frames, about 35 min with
  downloads and dithers) would cut the noise by about 29%." Doubling is the default step, because
  it is about the smallest change the eye sees. The count comes from the measured noise against
  frame count of this target, counted in effective time when the exposure length changes
  (section 4.2). Here the earlier 30 s frames were read-noise-limited, so the step uses 90 s.
- **The stop signal.** "Noise stopped falling with more frames after about 2 h. More frames will
  not help until you retake flats or dither." A target past its stop signal ranks at zero
  (section 4.2).
- **The sharpness trade-off.** "Keeping the sharpest 70% of frames sharpens stars by 12% and
  raises the noise by 15%."
- **Comparing targets.** The marginal gain per hour lets the planner say which visible target
  gains most from the next hour.

Where a use has a natural finish line, the report uses it, taken from the pipeline rather than set
by the user:

| Use | Natural goal | Source |
| :--- | :--- | :--- |
| Spectroscopy | SNR enough for the classifier to separate the best subtype from the next | The classifier's gap between its two best matches, calibrated against SNR by degrading high-SNR spectra with added noise. When no SNR separates the two classes, the outcome is "not reachable at this resolution". |
| Photometry | Precision fine enough to detect variability of a given size | The detectable-amplitude measurement, compared with the target's amplitude. The amplitude comes from the magnitude range in its variable star catalog record (VSX), which the library matches today but does not yet store. Without a catalog range the user states an amplitude, and without either the report gives an `insufficient_data` statement. |
| Asteroid search | An arc long enough to link the object | Arc length and motion rate |
| Imaging | None by default | Marginal value, as above |

An explicit imaging goal per target (a depth, or a noise level) can be added later if wanted.

### 6.5 Checking the forecast

A recommendation is a forecast: "20 more 90 s frames cut the noise by about 29%". Once the user
takes more frames and stacks them, the report checks the forecast against the new stack (decision
15).

- **One flux scale.** Stack noise is compared on a fixed flux scale, normalized to the zero point
  or given as the SNR of a fixed set of reference stars. It is measured in the same empty
  apertures, fixed in sky coordinates, in every stack of the target, so old and new stacks give a
  paired ratio (section 5.1).
- **The weights the stacker used.** The forecast models the stacker's real weighting.
  `choose_stack_weight` in `astrometricslib/pipelines/stacking/pre_processing/exposure_weighting.py`
  weights frames by noise only when their exposure lengths differ enough
  (`MINIMUM_PENALTY_TO_WEIGHT = 1.05`). Frames of equal length get equal weights, even when some
  are moonlit, so a naive noise-weighted forecast would be too hopeful. In one example the
  expected drop is 19.7% with noise weights and 16.1% with the equal weights the stacker used. The
  forecast reads each run's frame membership and weights from the frame-level rows that phase 3
  stores.
- **Three numbers.** The check reports the forecast made when the session was planned, the
  forecast given the frames actually taken (so worse new frames from Moon or cloud lower it
  fairly), and the measured result. Each has a tolerance from the uncertainty of the aperture
  noise. The noise is $1.4826$ times the median absolute deviation of the $n$ aperture sums, so its
  error is about $1.17/\sqrt{n}$ of itself (not the $1/\sqrt{2(n-1)}$ of a standard deviation),
  about 12% for 100 apertures. A comparison of two stacks measured in the same apertures uses the
  paired error, which resamples the apertures together and is smaller.
- **Outcome.** The gain arrived as forecast, fell short for a known reason (the new frames' lower
  efficiency), or fell short for no known reason.
- **Noise floor.** A floor is inferred only from a fit of $\sigma^2 = a/t_\mathrm{eff} + c\,\mathrm{RN}^2/\tau^2 + b$ over two
  or more steps, never from one shortfall. Here $t_\mathrm{eff}$ is `stacking.effective_time_s`
  (photon noise) and $\tau$ is `stacking.effective_time_read_noise_s` (read noise, which weights frames
  by $1/t^2$ instead of $1/t$), with the read noise RN from phase 2a. With frames of one length the two
  terms are the same shape and the fit needs only $t_\mathrm{eff}$. A fitted floor raises the "more frames will not help"
  warning.
- The recommendation is then recomputed from the new stack.

The check needs nothing stored beyond the measurements. Each stack run's noise, frame membership,
weights and effective time stay in the append-only history (section 3.5), so the forecast for any
stack can be rebuilt from the run before it. This keeps the rule that recommendations are never
stored.

The check is first tested on a synthetic history with known noise and weights. The real check
replays a target's recorded nights (decision 22): it stacks the frames of the first nights as a
subset stack, forecasts the next night's gain, then stacks with that night added and compares.
The subset stacks go to a scratch folder and never replace the library stack. Older runs did not
store their membership, so the replay builds its own.

Across targets, the forecast checks are evidence about the marginal-value model itself. They feed
its validation register entry, so the report's confidence in "take N more" is earned from the
user's own results.

### 6.6 Decision catalog

Each statement names the metrics it reads. A first catalog:

| Item | Metrics it reads | Example text | Class |
| :--- | :--- | :--- | :--- |
| Overexposed setting | Target clipping through the WCS, brightest star's peak as a fraction of saturation at each exposure, saturated fraction; zero-order saturation for spectra | "120 s frames clip the target core in 38 of 40. Use 45 s or less." | Capture settings |
| Underexposed setting | Sky noise and read noise | "30 s frames are limited by read noise. 90 s reaches the same depth in 25% less time." | Capture settings |
| Offset too low | Pixels clipped at zero | "Raise the offset: 2% of pixels clip at zero." | Capture settings |
| Guiding limits exposure | Exposure feasibility from the guide logs | "At 120 s only 40% of frames stay sharp; at 60 s, 85%." | Capture settings |
| Frames lost | Set-aside counts, cloud and trailing, efficiency; at night before stacking, the frames the live status marks as ruined | "12 of 60 frames set aside; clouds from 22:40 to 23:10." | Data selection |
| More frames | Stack noise against frame count, sky noise and read noise (effective time) | "Take 20 more at 90 s (about 35 min with downloads and dithers) to cut the noise by about 29%." | Capture settings |
| More frames will not help | Noise floor fitted over two or more stack steps; forecast check | "Noise stopped falling after about 2 h. Retake flats before adding frames." | Capture settings |
| Flat noise limits the stack | Master flat noise against stack noise in empty apertures | "The master flat adds 30% to the stack noise. Take 40 more flats." | Calibration |
| Focus drift | Star width against focuser temperature; the slope is fitted in `wayfindinglib` | "Stars widen by 0.7 px per °C. To stay within 1 px of best focus, refocus every 1.4 °C." | Session procedure |
| Calibration gap | Calibration match on exposure, filter, gain setting, offset, binning and temperature; flat-to-light match | "Take darks at gain 100, offset 50, bin 1, 60 s, −10 °C." | Calibration |
| Sky cost | Sky brightness, Moon (model-derived), gradient | "Frames with the Moon above 60% and within 40° lost about 45% (model-derived). Schedule moonless nights." | Scheduling |
| Altitude cost | Efficiency against altitude | "Frames below 30° lost 35%. Image above 35°." | Scheduling |
| Best window | Visibility, Moon, sky history | "Best nights: Oct 24 to 30, 22:00 to 02:00." | Scheduling |
| Period alias | Window function | "Two more nights, 3 days apart, would rule out the alias." | Scheduling |
| Spectrum SNR | SNR per resolution element, class gap against SNR | "8 more 20 s spectra for a subtype." Or: "A subtype is not reachable at this resolution." | Capture settings |
| Asteroid linking | Arc length | "Re-observe within 24 h to link the object." | Scheduling |
| Polar alignment | Field rotation per frame from `astrometricslib`, grouped by guiding run and converted to a polar error in `wayfindinglib` | "Field rotation shows a polar error; redo the polar alignment." | Adjustment |
| Tilt | Center-to-corner star width | "Stars in one corner are 30% wider; check the camera tilt." | Adjustment |

Refocus advice always states its tolerance: the allowed width increase divided by the measured
slope. Advice on dither
size has no metric until the dithering step of `QUALITY_SCIENCE_PLAN.md`, so it comes later. Items
whose metrics are in `QUALITY_SCIENCE_PLAN.md` arrive with those metrics.

### 6.7 Every metric has a purpose

Every registered metric declares one purpose (decision 14), and the tests in section 3.7 hold it to
it:

| Purpose | Meaning | Test |
| :--- | :--- | :--- |
| Decision | Feeds at least one warning or recommendation | At least one rule declares it in `requires` |
| Context | Explains other items and is shown as their evidence, such as airmass or sensor temperature | A stated reason, and at least one report section that shows it |
| Exploratory | Collected to answer a question | The question and a review date. Never shown as advice. After the date the test fails until the metric is promoted or removed. |

The review date keeps "exploratory" from becoming a permanent home for unused metrics.

### 6.8 Starting point for a new target

A target with no frames has no noise history, so the report gives a starting point instead of
"take N more". `wayfindinglib` computes it as the `starting_point` field of `TargetDatasetReport`,
and it is always labeled a starting point.

- **Exposure length.** Long enough that the sky noise dominates: $\sigma_\mathrm{sky}^2 \ge
  k\,\mathrm{RN}^2$, from the expected sky in electrons for the filter and the Moon phase and the
  read noise for the capture setting. The constant $k$ is validated on the user's own data and has
  a register entry.
- **Clipping cap.** The exposure is capped so the target core and the brightest field stars do not
  clip. Their peaks are predicted from catalog magnitudes and the zero point.
- **Total time.** It depends on the target's surface brightness, not its integrated magnitude,
  because an extended object spreads its light over many pixels.

### 6.9 The stored night plan

Today the night plan lives only in each app window's memory (`PlanningContext`).
`create_plan(kind="sequence")` in `wayfindinglib/api/planning.py` builds a plan but does not store
it, and `ExposureRequest` in `wayfindinglib/models/planning/observation_package.py` has no gain
setting, offset or binning. No plan model has a checklist. Decision 21 adds a stored night plan to
`wayfindinglib`:

- **Capture setting.** Each request holds filter, exposure length, count, gain setting, offset and
  binning. The library rejects a value the camera profile does not allow and says why. It merges or
  rejects a duplicate or conflicting addition, so the app never has to.
- **Checklist.** Items carry the rule name, the capture setting and the time the user ticked them.
  A write call ticks an item.
- **Framing.** The field center, the panels from `calculate_panels`, and the camera rotation. The
  mosaic code in `wayfindinglib/tasks/planning_tasks/mosaic_tasks.py` has no rotation today, so it
  gains one. The first version may show the field, the panels and the rotation as numbers only.
- **Storage.** The plan is written from the first edit, read with `get_plan`, and carries a
  revision number. A write with an old revision is refused, so two windows cannot overwrite each
  other. Each write raises a plan-changed event.

**The checklist return rule.** A ticked item comes back when the measurements show the fix did not
work. The rule lives in `wayfindinglib`: `get_advisory` reads the plan with `get_plan` and compares
the tick time with the measurements taken since. The statement says why it came back, for example
"ticked Oct 12; stars still 28% wider Oct 15". Keeping the rule in the library means the app and
the AI tools give the same answer.

**Sequence summary for Ekos.** `SequenceSummary` is a `wayfindinglib` model built from the stored
plan, shared by the app and the MCP tools. It holds rows, not a string. The rows follow the fields
and order of the Ekos Capture module (filter, exposure, count, binning, gain, offset), and each row
says which Ekos screen its value goes on. Dither size is set in the Ekos Guide options, and dither
frequency is set per capture job.

### 6.10 Calibration advice: one rule set

`get_advisory(kind="calibration")`
(`wayfindinglib/tasks/planning_tasks/calibration_advisory_tasks.py`) counts calibration frames
today. Its rules fold into the report, so there is one rule set for calibration advice.
`kind="calibration"` stays only as an inventory view of the calibration library, or is removed.

`CalibrationAdvisory` (`wayfindinglib/models/equipment_and_site/calibration.py`) matches only
exposure and filter today. The match gains gain setting, offset, binning and sensor temperature.
A calibration warning links to the exact calibration-library row it means. The join of library
facts (`CalibrationCatalog.query`) with the advice happens in `wayfindinglib`, not in the app.

## 7. What happens to today's verdicts and gates

Each existing verdict field, gate and piece of advice is sorted into one of five groups during
phase 6, one pipeline at a time (section 9).

| Group | Rule | Examples |
| :--- | :--- | :--- |
| Processing decision | Stays; recorded as a measurement plus `action_taken` | `frame_quarantine`, `gain_homogeneity`, stacking timeout |
| Self-check | Stays in `astrometricslib` as a measurement with a check outcome | `plate_solve`, `source_detection`, `spectra_extracted`, `search_frames`, `capture_timestamps` |
| Verdict field | Becomes a measurement with an uncertainty | `is_poor_match`, `is_ambiguous`, `is_class_ambiguous`, `is_trustworthy`, `is_low_confidence`, `catalog_agrees`, `saturation_flagged`, `fwhm_degraded`, the periodogram and transit `verdict`, the spectral feature `verdict` (detected, possible, inconclusive) |
| Fitness-for-use gate | Moves to a `wayfindinglib/analytics/result_quality/` rule with a `QualityLimit` | `stack_sharpness`, `scatter_population`, `variability_discrimination`, `catalog_agreement`, `gaia_xp_agreement` |
| Observing advice | `astrometricslib` keeps only the measurement; the advice moves to a `wayfindinglib/analytics/result_quality/` rule (decision 18) | The exposure recommendation and the "take more flats" message, below |

The examples are a first sort, not a final one. Before any code changes for a pipeline, a written
inventory lists each of its verdict fields and gates with its group, every reader of it and its
replacement. The user approves the inventory, so each move is a deliberate choice.

The observing advice `astrometricslib` holds today:

- **The exposure recommendation.** `recommend_exposure_seconds` and
  `recommend_stack_exposure_seconds` in
  `astrometricslib/pipelines/stacking/post_processing/exposure_saturation.py`, and
  `recommended_exposure_for_groups` in `exposure_group_report.py`, fill
  `StackingPipelineQualityMetrics.recommended_exposure_seconds` (`models/quality/quality_summary.py`).
  `wayfindinglib` reads it as `science_recommended_exposure_seconds`
  (`wayfindinglib/models/session/capture_quality.py`, read in
  `session_analysis/capture/post_processing/recommend_for_capture.py`). `astrometricslib` keeps
  the measurement: the brightest star's peak as a fraction of saturation at each exposure length.
  The recommendation becomes a `wayfindinglib` rule.
- **The flat message.** `stacking/pre_processing/flat_calibration.py` adds "take more flats" to
  its flat assessment. `astrometricslib` keeps the flat noise, and the advice becomes a
  `wayfindinglib` rule. The decision to smooth a noisy flat is a processing decision and stays.

Two existing structures need care:

- `flagged` and `flag_reasons` on every summary are interpretation. They stay readable while the
  UI, the backend and the planning advisory move to judgments, then go. The stacking timeout in
  `pipelines/tasks.py`, which sets `flagged` without a gate, becomes a processing-decision
  measurement.
- The falsification audit work and `test_gate_conformance.py` assume every gate is a pipeline
  output. The conformance test splits in two: one for `astrometricslib` self-checks, one for
  `wayfindinglib` rules. The `failed` and `not_checked` test requirement keeps applying to both.

Validation of a probability (decision 8) follows these rules:

- Calibrate on injected signals, conditioned on amplitude and magnitude. Catalog labels are only a
  cross-check: VSX lists many variables a session cannot detect, and the "unlisted" stars include
  unknown variables.
- Hold data out by night or by field, never by star, because stars in one field share
  systematics.
- The register entry states a minimum sample size and a confidence interval (Wilson or bootstrap),
  and may conclude "insufficient evidence". With one telescope and a few nights, many entries will
  honestly end there.

## 8. Impact and risk

The work is three kinds of change, and only one carries real risk. Agreed with the user on
2026-10-17: take the clean split (decision 1) as a conscious choice, do the mechanical refactor
first, and carry out the behavior-changing refactor last, one pipeline at a time, shadowed and
approved from an inventory.

| Kind | Phases | What it touches (review of 2026-10-17) | Risk |
| :--- | :--- | :--- | :--- |
| Mechanical refactor: moves only, no logic change | 0 | About 52 modules and 42 test files import the ten quality model files. `build/codegen/generate_types.py` and `build/documentation/generate_pipeline_review_docs.py` name the module paths. The `*_PIPELINE_VERSION` constants move with `quality_summary.py` and must not change. | Low per change but wide. Merge conflicts with open branches are the main hazard. |
| Enhancement: new code next to the old | 1 to 5 | New models, store and API; a new frame job and a stacking hook; each pipeline's quality file; `wayfindinglib` analytics and session analysis; the stored night plan; the backend RPC, the UI types and the MCP tools for the new calls | Low to medium. Today's outputs keep working throughout. |
| Schema and shape changes inside the enhancement | 2, 4 | Phase 2: camera profiles keyed by setting change the profile schema, so the config is migrated and every reader of gain and read noise is updated. Phase 4: `TargetDatasetReport` replaces `TargetQualityAdvisory`, with its reader in `scheduling.py`. | Medium. The golden suite shows the science numbers do not change. |
| Behavior-changing refactor | 6, 7 | Verdict fields and fitness gates leave `astrometricslib`. Readers outside it are listed below. | High. It changes what users see. |

Readers of today's verdicts outside `astrometricslib`:

- **The desktop app.** `ui/common/utils/starDisplayFormat.ts` and its tests use `isPoorMatch`,
  `isAmbiguous`, `isClassAmbiguous` and the periodogram and transit `verdict`.
  `StellarAnalysisDetails.tsx`, `SpectrumViewer.tsx` (spectral feature verdicts) and
  `PhotometryViewer.tsx` also read them. The generated `ui/common/types/backendTypes.ts` follows
  the models, so removing a field changes the UI's types.
- **The planning advisory.** `wayfindinglib/tasks/planning_tasks/quality_advisory_tasks.py` builds
  the planner's quality advisory from `flagged` and `flag_reasons` on all six summaries. Its
  return shape becomes `TargetDatasetReport` in phase 4, together with
  `_apply_advisory_boost` in `scheduling.py`. Its verdict inputs are verdicts only, so they switch
  to judgments in phase 6, not phase 4.
- **Capture recommendations.** `wayfindinglib` reads the stacking exposure recommendation as
  `science_recommended_exposure_seconds`. It switches to the `wayfindinglib` rule when stacking's
  observing advice moves (section 7).
- **Validation scripts.** `measure_variability_cutoff.py`, `validate_photometry_injection.py`,
  `run_empirical_validation.py` and `spectral_registration_quality_analysis.py` read the flags,
  and validation register entries cite the limits that would move.
- **The gate machinery.** `test_gate_conformance.py`, the review-guide generator,
  `Pipeline_Audit_Report.md`, `test_pipeline_doc_parity.py` and the falsification audit work assume
  every gate is a pipeline output.
- **The public API path.** `astrometrics.processing.diagnostics` is used by
  `wayfindinglib/tasks/control_tasks/frame_guiding.py`, two scripts, `test_public_surface.py`, and
  the MCP tool names through `mcp_servers/astrometrics_core/definition.py`, the tool manifest and
  `mcp_servers/inventory/`. Phase 0 keeps this path; phase 1 changes it (decision 9).

Cross-plan order: the Observation Execution phase of `UI_DISPLAY_PLAN.md` needs phases 2 and 4
of this plan (frame measurements, then the night reads and the stored night plan). Its
Observation Planner phase needs phase 4 (the report, the tonight ranking and the night plan).

Safeguards:

1. **Mechanical refactor alone, first.** Phase 0 is one pull request with moves and import edits
   only, so `git diff -M` shows nothing else. It merges when few other branches are open.
2. **Add before removing.** New measurements are emitted next to today's summaries (phase 3).
   Readers switch one at a time (phases 4 and 6). Nothing is deleted before phase 7.
3. **Fill the history from existing records, honestly.** A one-time script reads the raw JSON in
   `targets.data_json`, not the models, because loading a summary fills in today's version and the
   current time. It writes rows with `origin = backfilled`, leaves an unknown version or time
   empty, maps `provenanceActivityId` to `job_id`, and tags each row with the definition its
   estimator had. It follows the existing `scripts/backfill_*` pattern, with a dry run and a
   row-count check against the JSON. The JSON holds only the latest summary per pipeline, so the
   fill is one snapshot, not a history.
4. **Shadow mode for phase 6.** For each pipeline, a script runs the new `wayfindinglib` rules on
   every target in the live database next to today's verdicts and writes a difference report. Each
   difference is marked as a bug fixed or a change the user approved. The desktop app switches only
   when no difference is left unmarked.
5. **A written inventory per pipeline** (section 7), approved by the user before phase 6 code for
   that pipeline.
6. **Four sub-issues, by layer.** Issue #93 is the epic. Its sub-issues follow the layers:
   #94, `astrometricslib` measures everything the use cases need (phases 0 to 3 and the
   `QUALITY_SCIENCE_PLAN.md` metrics that back a use case); #95, `wayfindinglib` judgments,
   efficiency and recommendations (phase 4 and the result-quality rules); #96, the UI tier
   (`UI_DISPLAY_PLAN.md`, the backend and the MCP servers); #97, cleanup across the layers (phases
   6 and 7). Each sub-issue takes several pull requests marked "Part of", and its last pull request
   closes it. Each has its own Analysis and Plan.
7. **Data safety.** The new tables only add; existing table schemas do not change before phase 7.
   `astrometrics.db` is backed up with `scripts/backup_library_databases` before the first write
   and before the history fill.
8. **Numbers stay fixed through the refactors.** The M 13 golden suite must run, not skip: it skips
   when the Git LFS frames are missing. It covers frame quality, detection, star width, the spectral
   frame check and photometry. A real-data spot check (one stack, one plate solve, one asteroid
   search, one spectrum) covers the rest. Both give identical science numbers after phase 0 and
   after each pipeline's phase 6.
9. **Removals wait for their replacement.** Nothing is removed or renamed until its replacement
   exists and every reader has switched. All removals belong to #97. For example,
   `recommended_exposure_seconds` stays in `astrometricslib` while #94 adds the
   replacement measurement (the brightest star's peak as a fraction of saturation at each
   exposure); it is removed in #97, after `wayfindinglib`'s rule replaces it and its reader in the
   capture analysis has switched.
10. **A pull request that changes a public interface updates every caller.** The tickets follow
    the layers, but a single pull request may cross them. The phase 1 move of `QualityDiagnostics`
    to `astrometrics.quality` updates `wayfindinglib`'s `frame_guiding.py`, the scripts, the MCP
    tool definitions, manifest and dispositions, and `test_public_surface.py` in the same pull
    request. The phase 2 camera-profile change updates every reader in `astrometricslib` and
    `wayfindinglib`'s performance envelope in the same pull request, and its configuration
    migration accepts the old shape until it has run, after a backup. `main` passes every check
    after every merge.

## 9. Order of work

Each phase ends with its tests passing, `ruff` clean, the import-linter contracts unchanged, and
the affected READMEs updated. Each phase is one pull request.

0. **Consolidate (mechanical refactor).** Move the quality models into `models/quality/` and the
   `QualityDiagnostics` module into `api/quality.py`, keeping its public path. Update imports,
   `build/codegen/generate_types.py`, the review-guide generator, the generated guides and the
   READMEs. No logic changes and no version changes. Done when the full test suite, import-linter,
   the golden suite (run, not skipped) and the documentation tests pass, the regenerated
   `backendTypes.ts` is byte-identical, and the diff shows only moves and import edits.
1. **Foundations.** Add `QualityMeasurement`, `QualitySubject`, `QualityLimit`, `MetricSpec`, the
   `metrics.py` declaration pattern and the assembled registry with a handful of metrics. Add
   `drivers/quality_store.py` with the measurement and equipment tables. Add the
   `astrometrics.quality` root with `query`, `metrics` and the live checks, and rename the
   diagnostics MCP tools under it (decision 9). Add the backend RPC methods, regenerate the UI
   types, set the MCP tool dispositions, and add the static conformance tests. Done when a test
   writes and reads back a measurement, a re-run writes no duplicates, and the conformance tests
   pass on the registry.
2. **Frame measurement step.** A registered background job, run after the frame scan, measures each
   light frame once, and a hook in the stacking stage records the same metrics where the pixels are
   already loaded. The scan itself stays header-only. The job skips frames whose file state is
   unchanged, using the stamp `_is_unchanged` already checks, and keeps to a stated time budget per
   frame, because frames are read over the remote storage mount. It records what
   `FrameRecord.measurements` holds today, the quarantine measurements of every frame, and the new
   metrics of section 5.1, including the focuser temperature. The job runs after a sync the user
   started (decision 16) and raises `quality:measured` when it finishes (section 6.1). Gain and
   read noise are first keyed by camera and setting, with the calibration measurement that
   computes them from flat and bias pairs (decision 7). This needs:
   - a write path for camera profiles, because `drivers/camera_profile_store.py` only loads them
     today;
   - a migration of the camera profile config, because keying by setting changes its schema;
   - updates to every reader of gain and read noise: `photometry/pre_processing/detector_noise.py`,
     `photometry/pre_processing/frame_photometry.py`,
     `spectroscopy/pre_processing/intensity_variance.py`, the photometry `record_quality.py` and
     `models/stellar_source.py`;
   - a backend RPC method for the measurement call. The call writes a profile, so it is withheld
     from the read-only MCP profile.

   Done when a real night's frames have measurements, a second run measures nothing again, the new
   star width is checked against the current estimator on round and on trailed frames, and the
   golden suite gives the same science numbers with the profiles keyed by setting.
3. **Existing pipeline metrics in the new shape.** In each pipeline's change, rename its
   `run_gates.py` to `record_quality.py` (decision 10) and emit measurements next to its current
   summary. In the stacking change (its own `post_processing/record_quality.py`), add the stack noise in empty apertures, the effective frame
   count and time of each run, and each run's frame membership and weights as frame-level rows
   (section 5.1). Each night's contribution to a stack follows right after the subset stacks
   (decision 17); the subset stacks are built by `subset_stack.py` and `subset_noise_run.py` in the stacking folder
   and write only under the target's `_subsets/` folder. Fix the dropped asteroid `residual_*` counts and
   regenerate the UI types. Run the history fill (safeguard 3). Done when every metric in the five
   summaries has a registered name, appears in the table after a run, and the filled history
   matches the JSON.
4. **Efficiency and recommendations from measurements.** Build
   `wayfindinglib/analytics/result_quality/efficiency.py`: the references, goals, noise regimes,
   the loss account and the tonight model (section 4). Build the night and target aggregates in
   `aggregates.py`. Change `Recommendation.evidence` to a typed list of measurement references;
   this changes a reflected MCP tool's output, so note it there. Migrate `PerformanceThreshold` to
   `QualityLimit`. Rules declare the metrics they require. Switch the guiding and capture
   recommendations to `quality.query`. Then build the target dataset report (section 6):
   - the action classes, the statement kinds and confidence labels, and the rules of the decision
     catalog whose metrics exist, including the folded calibration rules (section 6.10);
   - marginal value, the starting point, the forecast check and `get_advisory(kind="tonight")`;
   - `TargetDatasetReport` in place of `TargetQualityAdvisory`, with `scheduling.py` updated in the
     same change, and `get_advisory(kind="result")`;
   - the stored night plan with its checklist, framing, revision check and plan-changed event, and
     `SequenceSummary` (section 6.9);
   - the typed night review and live status reads, the night list, the `row_ids` filter of
     `quality.query`, and their backend RPC methods;
   - regenerated UI types, and a data-quality panel on the target view in the desktop app
     (`UI_DISPLAY_PLAN.md`, phase 0).

   Phase 4 is large, so it may land as two pull requests: first efficiency, the rules and the
   target report; then the night reads, the stored night plan and the tonight ranking. Done when
   the guiding and capture recommendations read through `quality.query`, the loss account's
   attribution is checked on nights with a known cause (section 4.1), and a real target's report
   gives a "take N more" recommendation whose forecast is checked by replaying its recorded
   nights (section 6.5).
5. **New metrics.** Built under `QUALITY_SCIENCE_PLAN.md`, in its own order, once phase 1 exists.
   This plan only provides the shape they fill.
6. **Verdicts and gates, one pipeline at a time.** Photometry goes first, because its labels and
   its `variability_discrimination` gate already exist (decision 8). For each pipeline: write and
   approve the inventory (section 7), build the rules in `wayfindinglib/analytics/result_quality/`,
   run them in shadow mode (safeguard 4), switch the desktop app, the planning advisory and the
   scripts, move any observing advice the pipeline holds to `wayfindinglib` (section 7; stacking
   holds the exposure recommendation and the flat message), add any backend RPC and UI type changes, then turn that pipeline's verdict fields into
   measurements and keep its self-checks. Done for a pipeline when its shadow differences are all
   marked and no field of its models states a verdict about a result.
7. **Retire duplicates.** Only once no reader of a field is left: remove the values that the JSON
   summaries repeat, drop `flagged` and `flag_reasons`, and decide whether `processing_jobs`
   `output_metrics` is retired or derived from the new table. Done when a search of the libraries,
   the backend, the desktop app, the MCP servers and the scripts finds no reader of a removed
   field.

Phases 2 and 3 can run at the same time once phase 1 is done. Phase 5 can start any time after
phase 1. The Observation Execution phase of `UI_DISPLAY_PLAN.md` waits for phases 2 and 4, and its
Observation Planner phase waits for phase 4 (section 8).

## 10. Documentation

- `Astrometrics_Library_Architecture.md` gains a section on quality measurement: the measurement
  shape, the checkpoints and the split between measuring and judging, with no code names.
- `Astrometrics_Library_Implementation.md` gains the registry and the store, with their status
  labels.
- The `wayfindinglib` documentation gains efficiency, its references, the loss account, the
  tonight ranking, the result-quality rules (including the observing advice moved out of
  `astrometricslib`), the stored night plan and the owner of each behavior in section 6.1.
- The desktop-app guides under `documentation/user_interface/` gain the target data-quality panel:
  how to read its statement kinds, evidence and confidence labels, and the "Sync now" action with
  its last-sync line.
- Each efficiency function, each reference and each moved limit gets an entry in the validation
  register. So do the tonight model, the Moon sky model, the loss attribution on known-cause
  nights, the undersampling constant $c$, the starting-point constant $k$ and the spectroscopy
  class gap against SNR.
- `astrometricslib/models/README.md`, `pipelines/README.md`, `api/README.md` and the
  `wayfindinglib` analytics and `session_analysis` READMEs change with the code.

## 11. Open questions and risks

- **A separate science plan (settled).** The user agreed on 2026-10-17 to move the new metrics into
  `QUALITY_SCIENCE_PLAN.md`. This plan keeps the shared shape, the migration and the metrics
  efficiency and the report need first (section 5.1).
- **Where metrics are declared (settled).** See decision 6 and section 3.4.
- **Night boundaries (settled).** `observing_night_id` uses the processing computer's local time.
  The user confirmed on 2026-10-17 that the computer and the observatory share a time zone. The
  date changes at local noon, so a daylight-saving shift cannot split a night.
- **Gain and read noise (settled, see decision 7).** On 2026-10-17 only the ZWO ASI 533MM Pro had
  measured values, at gain 0. The Nikon D5300 and the ZWO ASI120MC-S guide camera had none. Until a
  setting is measured, `wayfindinglib` computes efficiency against the baseline only, not the
  physical reference, and says so.
- **Calibrated probabilities (settled, see decision 8 and section 7).**
- **Table growth (settled).** See section 3.5.
- **Night and target levels (settled).** Dropped from the measurement shape; `wayfindinglib`
  computes those aggregates when read (decision 18).
- **Syncing (settled).** Manual for now (decision 16). Automatic syncing is not designed.
- **Asteroid completeness and the null search (deferred).** They wait until asteroid searches are a
  regular part of the user's nights (decision 17).
- **Open: overhead figures for wall-clock ranking.** Download, dither settling and autofocus times
  should come from the Ekos logs of the user's own nights, not from fixed guesses. Where they are
  read from is decided in phase 4.

## Appendix A. Review board changes

The board met on 2026-10-17. Each finding below was accepted and placed as shown.

| Finding | Raised by | Where it landed |
| :--- | :--- | :--- |
| Efficiency depends on a reference, so it is a judgment; it is not one currency across goals and regimes; losses multiply | Astronomer, architect, engineer | Decision 3, section 4, phase 4 |
| The store cannot use the `Butler` (or `wayfindinglib`'s `DiskButler`); duplicates, version sorting, backfilled rows | Architect, engineer | Section 3.5, safeguard 3 |
| Self-check judgments need no table of their own | Architect | Decision 2, section 3.3 |
| Moving `QualityDiagnostics` changes a public path and the MCP tool names | Architect, engineer | Section 3.8, decision 9, phases 0 and 1 |
| File counts, generators and the asteroid path in phase 0 | Engineer | Section 8, phase 0 |
| The judgment rules must be reachable from Planning | Architect | Section 3.8, `wayfindinglib/analytics/result_quality/` |
| The registry must be public API; the cross-library check lives in `wayfindinglib/test/`; a per-metric definition version | Architect | Sections 3.4, 3.6, 3.7 |
| The planning advisory reads verdicts only, so it switches in phase 6 | Architect | Section 8, phases 4 and 6 |
| Backend RPC, UI types and MCP dispositions for the new calls | Architect, engineer | Section 3.6, phases 1, 4 and 6 |
| The frame step must not run inside the header-only scan; idempotence; stable frame ids | Engineer, architect | Section 3.1, phase 2 |
| Conformance tests must be static and switched on per pipeline | Engineer | Section 3.7 |
| Today's FWHM estimator is biased on trailed frames and returns pixels | Astronomer | Sections 3.4 and 5.1, phase 2 |
| Bands, color terms, airmass and per-channel metrics for color cameras | Astronomer | Section 3.1; `QUALITY_SCIENCE_PLAN.md` section 2.1 |
| Correlated noise after stacking; limiting magnitude definition | Astronomer | `QUALITY_SCIENCE_PLAN.md` section 2.3 |
| Asteroid completeness grid, per-night false-positive rate, timing error | Astronomer | `QUALITY_SCIENCE_PLAN.md` section 2.4; the grid and the false-positive rate were deferred to its section 7 after the second review |
| Probability calibration on injections, held out by night or field, minimum sample size | Astronomer | Section 7 |
| Store failures, error type and logging | Engineer | Section 3.5 |
| Golden suite must run, not skip; shadow-mode mechanism | Engineer | Safeguards 4 and 8 |
| The rename of `run_gates.py` can wait for each pipeline's phase 3 | Architect | Decision 10 |

One finding was not adopted: a suggested change to the night id for daylight saving. The night's
date changes at local noon, so a one-hour shift stays in daylight (section 11).

## Appendix B. Review board changes (second review)

The second board met on 2026-10-17 and reviewed this plan with `QUALITY_USE_CASES.md`,
`QUALITY_SCIENCE_PLAN.md` and `UI_DISPLAY_PLAN.md`. The rows below are the findings that touched
this plan. The user's decisions from the review are decisions 16 and 17; the settled architecture
points are decisions 18 to 21.

| Finding | Raised by | Where it landed |
| :--- | :--- | :--- |
| `astrometricslib` holds observing advice today (exposure recommendation, flat message) | Architect | Decision 18, section 7 (fifth group), phase 6 |
| Night and target subject levels put aggregates in `astrometricslib` | Architect | Decision 18, section 3.1, section 3.5 |
| `MetricSpec.decisions` must not drive behavior in `astrometricslib` | Architect | Section 3.4 |
| The app must not rank, group, classify or join; each behavior needs a library owner | Architect, user-experience reviewer | Decision 19, section 6.1 |
| Tonight ranking, night review, live status, night list and sequence summary have no library call or typed model | Architect, engineer | Sections 6.1 and 6.9, phase 4 |
| Report statements need kinds, priority order, confidence labels and an insufficient-data item | User-experience reviewer, astronomer | Section 6.2 |
| Needs-attention field and starting point for a new target, with the astronomer's exposure method | User-experience reviewer, astronomer | Sections 6.2 and 6.8 |
| Checklist return rule must live in the library | Architect | Section 6.9 |
| Two calibration rule sets; calibration match lacks gain, offset, binning and temperature | Engineer, architect | Section 6.10 |
| Per-star results have no row id, so `QualityJudgment` cannot reference them | Engineer | Section 3.3, `get_advisory(kind="result")` in section 6.1 |
| Framing needs `calculate_panels` and a rotation | Architect | Section 6.9 |
| No event tells the app that new measurements exist | Engineer | Section 6.1 (`quality:measured`), phase 2 |
| Evidence needs a `row_ids` filter | Engineer | Section 3.6 |
| `TargetDatasetReport` must replace `TargetQualityAdvisory`, including its reader in `scheduling.py` | Architect, engineer | Section 6.1, section 8, phase 4 |
| No stored night plan; requests lack gain, offset and binning | Engineer, architect | Decision 21, section 6.9, phase 4 |
| Monitor mode already exists as the delegation policy | Architect | Decision 20 |
| Forecast check physics: flux scale, paired apertures, real stack weights, three numbers, noise floor fit | Astronomer, engineer | Section 6.5, sections 5.1 and phase 3 |
| Loss account cannot fail its "adds up" test; set-aside time and efficiency deficit differ | Astronomer | Section 4.1, phase 4 |
| Ranking needs a tonight model, wall-clock time, a zero at the stop signal and separate lists | Astronomer | Section 4.2 |
| "Take N more" must use effective time; inconsistent catalog examples | Astronomer | Sections 4.2, 6.4 and 6.6 |
| Undersampling in the seeing factor | Astronomer | Section 4 |
| Spectroscopy finish line needs an SNR calibration and an unreachable outcome; photometry needs an amplitude source | Astronomer | Section 6.4 |
| Refocus tolerance, focuser temperature metric, dither advice has no metric yet | Astronomer, engineer | Sections 5.1 and 6.6 |
| Flat noise against stack noise rule | Astronomer | Section 6.6 |
| Store sky noise and read noise as parts | Astronomer | Section 5.1 |
| Gain and read noise per setting need a write path, config migration, reader updates and a withheld RPC | Engineer | Phase 2, section 8 |
| Cross-plan order with the UI plan | Architect | Sections 8 and 9 |
| Set-aside reasons exist only after stacking | Engineer | Sections 4.1 and 6.6 |
| Manual syncs (user decision) | User | Decision 16, section 6.1, phase 2 |
| Each night's contribution after subset stacking; asteroid completeness deferred (user decision) | User | Decision 17, section 5.1, phase 3 |

No findings were rejected.

## Appendix C. Review board changes (phase 0)

The board reviewed the phase 0 commit (4d19c7c0) on 2026-10-21. The astronomer and the software
engineer approved; the architect approved with changes. All findings were minor and had one clear
fix, so none needed a user decision.

| Finding | Raised by | Where it landed |
| :--- | :--- | :--- |
| `processing.py` imported stacking argument helpers, including the private `_make_selection`, from `api/quality.py` | Architect, engineer, astronomer | Moved to `pipelines/shared/api_arguments.py` as `make_frame_selection`; section 3.8 |
| The plan status and section 3.8 still described the code before phase 0 | Architect | Status, section 3.8 |
| The Public API row of the Implementation doc did not list `api/quality.py` | Engineer | `Astrometrics_Library_Implementation.md` |
| `AppliedCameraProfile` sits in `models/quality/quality_summary.py`, not beside `models/camera_profile.py` | Architect | Deferred to phase 2, which adds the camera-profile write path |
| Default `pytest` test paths leave out `astrometricslib/models` and `astrometricslib/pipelines` (true before phase 0) | Engineer | Deferred; phases that add tests there run `build/linux/run_tests.sh`, which names them |
| The M 57 real-data check is a no-change smoke test, not new evidence | Astronomer | Pull request text; nothing added to the validation register |

No findings were rejected.

## Appendix D. Review board changes (phase 1)

The board reviewed the phase 1 commits (cd61d541, 535688aa) on 2026-10-21. All three reviewers
approved with changes. Every critical and major claim was checked against the code. The fixes
are in 67450677; the user's naming decision is decision 23 (57db826a).

| Finding | Raised by | Where it landed |
| :--- | :--- | :--- |
| The width metric's text said elongated stars are skipped; the code keeps them at their narrow-axis width, so a trailed frame reads sharp | Astronomer | Metric renamed `frame.star_width_gaussian_brightest_px`, estimator text corrected; section 5.1 |
| The rejected-pixel fraction is the mean of Siril's rejection map, not a pixel count | Astronomer | `stacking/metrics.py` |
| "Star count" counts detected regions, including hot pixels and nebulosity | Astronomer | Renamed `frame.detected_region_count`, direction none |
| Color planes are averaged before measuring; a raw mosaic is measured as is | Astronomer | Each metric's band text |
| NaN values were accepted and read back as missing, breaking reads | Engineer, astronomer | Models reject non-finite values; a bad stored row raises `StorageError` |
| Times with and without a time zone were stored in two forms | Engineer, architect | `measured_at` must carry a time zone; stored as UTC |
| Every read took the write lock and could migrate the schema | Engineer, architect | Reads open the file read-only; only writes migrate |
| `INSERT OR IGNORE` hid every constraint error | Engineer | `ON CONFLICT ... DO NOTHING` on the unique key only |
| `ASSUMED` needed no source; a check outcome could lack its limit | Astronomer | Validators in `measurement.py` |
| Nothing checked a write against the registry | Astronomer, architect | `record_measurements.py`; section 3.8 |
| `quality_metrics` returned a Python frozenset as text | Architect | `MetricSpec.levels` is a sorted tuple; codegen handles tuples; MCP JSON test |
| The root class name would break the API a second time later | Architect | Renamed now (decision 23) |
| An unknown metric name returned nothing; phase 4 needs lookup by name | Architect | `query` rejects unknown names; `metrics(name=...)` |
| Unused registry methods | Architect | Removed |
| Test gaps: batch rollback, two writers, read path under a lock | Engineer | `test_quality_store.py` |
| `ARCHITECTURE_PLAN.md` still said to keep `processing.diagnostics` | Engineer, architect | Updated |
| The width metric has no star count or elongation | Astronomer | Deferred to phase 2, where the frame measurement step returns them |
| RPC parameters with unknown names are dropped silently | Engineer | Comment on `quality:query`; existing router behavior |

No findings were rejected. The cross-library conformance checks (section 3.7) wait for the phase 4
rules, because `astrometricslib` tests may not import `wayfindinglib`.

## Appendix E. Review board changes (phase 2)

The board reviewed phase 2 (fb06b954 to 4cf878bf) on 2026-10-22. All three reviewers approved with
changes. Every critical and major claim was checked against the code, and the starred rows were
also seen on real data (M 57, night 2026-10-03, 61 frames, run on copies of the configuration and
database). None of the findings needed a user decision.

| Finding | Raised by | Where it landed |
| :--- | :--- | :--- |
| The quality store kept its version in `PRAGMA user_version`, which the real database already set to 2 (*) | Systems engineer, on real data | A `quality_schema_version` table; section 3.5 |
| The sync, ingestion, RPC and stacking triggers had no shared per-target guard, so two runs could write the same rows | Architect, engineer | One per-target lock in the library; the backend only starts and announces |
| A sync measured every unmeasured frame of the target, not only the frames it added | Engineer | The sync and ingestion measure only added frames; older frames wait for an explicit run |
| `quality:measured` had two sources, and one counted other runs' rows | Architect, engineer | The run's own summary is the only source |
| Frame stamps were saved only at the end, and the save overwrote other changes to the target | Engineer | Stamps saved every 20 frames, merged by frame path |
| Failures other than four named error types could fail a stack or end a run | Engineer | Every error is caught at the stage boundary and per frame |
| The sky (clipped median) and the bias (plain mean) used different estimators; the median takes values in 4 ADU steps on the ASI 533 (*) | Astronomer | Both use a 3σ clipped mean; sky metrics at definition version 2 |
| The sky includes dark current; sky noise is the expected Poisson noise, not measured scatter | Astronomer | Metric texts; a frame with no sensor temperature is stored as ASSUMED |
| A raw color mosaic biases star moments and angles | Astronomer | Star shapes measured on 2x2 super-pixels |
| The quarantine roundness uses image x and y widths, so a diagonal trail reads round | Astronomer | Metric text |
| Elongation is biased above 1 for faint stars; round stars dominated the angle | Astronomer | Metric text with measured values; angle from averaged ellipticity components (version 2) |
| Trailed frames lose their most trailed stars to the isolation and fit cuts | Astronomer | Refused-candidate counts per cut in each width row's reason |
| Photon transfer did not check linearity and paired flats by file name across sessions | Astronomer | Flats above the linearity limit refused; pairs matched by observation time within 300 s |
| STK-35 was labeled Measured without an acceptance criterion | Astronomer | Partly measured, with a 0.5% gain criterion that the data meets |
| AST-17 expected the two widths to agree within 10% | Astronomer | The synthetic ratio (0.825 to 0.914) and the real M 57 result recorded (*) |
| Copied star finder, binning normalizer and exact-setting bias lookup | Architect | One of each, in shared code |
| The stacking stage built its own calibration library on each stack | Architect | The caller passes it in |
| Configuration writes were not atomic | Engineer | Temporary file and replace, under a lock; at most 10 backups |
| The 60 s per-frame budget counted wall time, so a busy machine cut short 46 of 61 frames, and those frames were stamped and never retried (*) | Systems engineer, on real data | The budget counts the frame's own CPU time; a frame cut short is not stamped and is retried |
| Threads gave almost no speed-up, because the fitting holds Python's interpreter lock | Systems engineer, measured | Four worker processes (decision 24) |

Real data after the fixes:

- **Flats paired by time:** they give the same gain and spread as before (0.7823 and 0.7825 e-/ADU, spread 0.0024 and 0.0014), because each flat set came from one session. The spread above the hand value of 0.0004 is still unexplained.
- **Speed:** with four worker processes, the 61 frames took 345 s (about 5.7 s a frame) and about 2.7 GB of memory, and every metric of every frame was measured. Four threads had taken 654 to 884 s.

No findings were rejected. One minor engineer finding (a header gain fallback could pick up a wrong
maker value) was checked against sampled ASI 533 and Nikon frames: none carry `EGAIN` or
`RDNOISE`, so it cannot happen with today's data, and it stays a note.

## Appendix F. Review board changes (phase 3)

The board reviewed phase 3 (ddbe05cf to 57a4c0de) on 2026-10-23. All three reviewers approved with
changes. Every critical and major claim was checked against the code. Several findings came from
real data before the review: a real stack of M 57 (night 2026-10-03, 61 frames) on copies of the
configuration and database, with quarantine off and the light frames untouched. The user's
decisions are 25 and 26.

| Finding | Raised by | Where it landed |
| :--- | :--- | :--- |
| Siril's sequence columns were mislabeled since before #94: field 2 is the weighted FWHM, field 5 the background level (not an RMSE) | Systems engineer, from Siril's `seqfile.c` | Parser and `FrameMeasurements` fields; old fields left for #97 |
| The spectral registration check tested the background level | Systems engineer | Dropped (decision 25) |
| Stack noise was never measured on real data: the reference-frame check dropped the brightest stars, and Ekos writes the commanded pointing as CRVAL | Systems engineer, on real data | Saturation cut fixed; WCS shifted onto reference stars, always checked; rotation over 0.1° refused |
| Siril's `wfwhm` weights were not recorded | Systems engineer, on real data | Rebuilt with Siril's formula (`median_and_mean.c`) |
| A measured row carried a not-measured reason | Systems engineer, on real data | `build_measurement` |
| The noise error used the standard-deviation formula on a MAD spread (about 1.65 times too small) | Astronomer | 1.1664σ/√n; STK-36 and section 6.5 |
| The flux scale used the scatter between stars, depended on seeing, and judged saturation by the stack itself | Astronomer | Large aperture, ratios to first-stack values, camera-profile saturation |
| T_eff holds only when sky noise dominates | Astronomer | `stacking.effective_time_read_noise_s` |
| Leave-one-night-out errors were treated as independent; its text interpreted the result | Astronomer | `stacking.noise_difference_without_night` with a paired error; `stacking.subset_expected_noise_ratio`; wording removed |
| The history fill wrote old values under today's definition version | Astronomer | Unknown version 0 unless the estimator history is checked |
| The history fill could store a run twice; its run id lacked the summary kind | Engineer | Skips summaries with live rows (by time and job id); kind in the run id; no frame-time fallback |
| A NaN or other error while building rows could fail a science run | Engineer | Non-finite values become not measured; every record step is guarded |
| `stack_subset` could write over protected stacks; two subset jobs could share a work folder | Engineer | Output confined to `_subsets/`; per-target lock that refuses; label in the work folder |
| Subset rows looked like library rows | Architect | `QualitySubject.variant`, store schema 2, `query(variant, library_only)` |
| The subset stack copied the stage's settings | Architect | `pipelines/stacking/stack_settings.py` shared by both |
| The history fill copied the gate adjustments | Architect | Each pipeline publishes `GATE_LIMIT_SOURCES` and `gate_for_measurement` |
| Metric names followed no unit rule | Architect | About 55 renames; a test that each name ends with its unit |
| Limits that are design estimates were filed as physical budget | Astronomer | `design_estimate` tier (decision 26); every gate re-tiered with its register entry |
| Two test files of one name in two non-package folders | Systems engineer, full suite | Stacking's renamed |
| A test wrote a stray folder outside its temporary path; a concurrency test failed under load | Systems engineer | Fixed |

Real data after the fixes:

- **Stack noise, M 57:** the normalized noise of the library stack is 2.65e-4 (± 3.1e-5) on the new flux scale.
- **Subsets of 15, 30 and 45 frames:** their noise is within 0.26σ, 0.87σ and 0.22σ of the prediction.
- **History fill:** it is re-run on a database copy under the new rules before its counts are quoted.

Deferred:

- the 12 Gaia XP band names, which carry the band edges in the name (kept: the bands are fixed today);
- thinning the 49 spectroscopy checkpoint medians (kept as context metrics);
- making `stack_subset` private (kept public for phase 4's add-a-night check).

No findings were rejected.
