# Quality Metrics Plan

This plan gives every quality metric in `astrometricslib` one shared shape. The aim is metrics
that people can act on. An observer should learn what to change before the next night. A
processing step should learn which data to use. A science user should learn how far to trust a
result. `wayfindinglib` builds its observation recommendations from these metrics, so they must
be easy to find, compare and trust.

The plan covers:

- what the code records today, and why that is hard to build on,
- the decisions agreed on 2026-10-17,
- the shared shape: measurement, limit, judgment and the metric registry,
- efficiency, the unit `wayfindinglib` uses to rank losses,
- the metrics this plan carries (new metrics are in `QUALITY_SCIENCE_PLAN.md`),
- the target dataset report: the warnings and recommendations the user sees for a target,
- what happens to today's verdicts and gates,
- the impact of the work and how its risk is kept low,
- the order of work.

The findings come from a read-only survey of the repository on 2026-10-17. The code is the source
of truth. Line numbers drift, so this plan names files and functions instead.

## Status

Status on 2026-10-17: design, target dataset report and order of work agreed, nothing built. Phase 0 is next. Tracked in
issue #93.

A technical review board (an observational astronomer, a software engineer and a technical
architect) reviewed the plan on 2026-10-17. All three approved it with changes. Appendix A lists
the changes and where each one landed. After the review, the new science metrics moved to their
own plan, `QUALITY_SCIENCE_PLAN.md`.

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
| `GateResult` | `models/gate_result.py`, every pipeline | no | no | free text |
| `StageQualityMetric` | `models/spectroscopy_quality.py` | yes | no | no |
| `*PipelineQualityMetrics` (five) | `models/quality_summary.py` | in the field name | rarely | none |
| `FrameRecord.measurements` | `models/target.py` | in the field name | no | none |
| `ProvenancedValue` | `models/camera_profile.py` | no | no | yes |
| `PerformanceThreshold` | `wayfindinglib/models/equipment_and_site/performance_envelope.py` | yes | sample count only | yes (tier) |

`wayfindinglib`'s `Recommendation` stores its evidence as an untyped dictionary. Each
recommendation rule must therefore know each pipeline's internal field names. When a field is
renamed or dropped, as the `residual_*` counts were, nothing fails.

## 2. Decisions

Agreed with the user on 2026-10-17:

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
    NIGHT = "night"
    STACK = "stack"
    TARGET = "target"
    RUN = "run"


class QualitySubject(BaseModel):
    level: SubjectLevel
    subject_id: str  # a stable id: target plus relative frame path, stack id or run id; never a mount path
    target_id: str | None
    observing_night_id: str | None  # utilities/observing_night.py
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
measurement row it judged, so a judgment over an append-only history is never ambiguous.

```python
class QualityJudgment(BaseModel):
    name: str  # the rule's name; a moved gate keeps its existing name
    measurement_row_id: int  # the row in quality_measurements it judged
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
        levels=frozenset({SubjectLevel.FRAME, SubjectLevel.NIGHT}),
        better=Direction.LOWER,
        meaning="Median full width at half maximum of stars spread over the whole frame.",
        decisions=("focus", "guiding", "seeing"),
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
- the decisions it informs,
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
  (`PRAGMA user_version`), and the store migrates it explicitly.
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

- **Narrow rows for frames, nights, stacks and runs.** Adding a metric adds a registry entry, never
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
    camera=..., telescope=..., since=..., latest_only=True,
) -> list[QualityMeasurement]
```

`library.quality.metrics()` returns the registry. `wayfindinglib`, the backend and the MCP servers
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

Quality code is spread out today: ten model files loose in `models/`, frame checks in
`pipelines/shared/quality/`, gate files in four pipelines, and `QualityDiagnostics` inside
`api/processing.py`. The rule for this work is one quality home per layer. Agreed with the user
on 2026-10-17:

- All quality models move into one subpackage, `models/quality/`, in phase 0. The top-level
  imports do not change.
- The `QualityDiagnostics` module moves from `api/processing.py` to `api/quality.py` in phase 0,
  keeping the path `astrometrics.processing.diagnostics`. Phase 1 adds the `astrometrics.quality`
  root and renames the MCP tools (decision 9).
- Each pipeline's `run_gates.py` is renamed `record_quality.py` in that pipeline's phase 3 change
  (decision 10).

| Layer | Home | Holds |
| :--- | :--- | :--- |
| Models (shapes only) | `astrometricslib/models/quality/` | Measurement and limit shapes; today's quality models, moved |
| Shared calculations | `astrometricslib/pipelines/shared/quality/` | The assembled registry, the frame measurement step |
| Each pipeline | `<pipeline>/metrics.py` and its `record_quality.py` | The declaration of its metrics, and what it emits |
| Storage | `astrometricslib/drivers/quality_store.py` | The measurement and equipment tables, in plain SQL |
| Public API | `astrometricslib/api/quality.py` | `query`, `metrics` and the live checks |
| Judgments and efficiency | `wayfindinglib/analytics/result_quality/` | The fitness-for-use rules as a table, the efficiency references and functions |
| Recommendations | `wayfindinglib/session_analysis/` | Guiding, capture and sky recommendations |

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
└── api/quality.py            QualityDiagnostics module moves here in phase 0

wayfindinglib/
├── analytics/result_quality/
│   ├── rules.py              one row per moved gate: metric, limit, wording, required metrics
│   ├── judge_results.py
│   └── efficiency.py         references, goals, noise regimes, loss split
└── session_analysis/         guiding/, capture/, sky/ read through astrometrics.quality.query
```

The count is about ten new modules, four renamed, eleven moved and one merged away, plus tests.
The layer contracts do not change.

## 4. Efficiency: how `wayfindinglib` ranks losses

Efficiency $\eta$ is the effective exposure time divided by the time a reference frame would need
for the same result, from 0 to 1. `wayfindinglib` computes it from raw measurements (decision 3).

**The reference.** A named, versioned reference sets each factor from a physical floor and the
equipment's own baseline, and reports $\eta$ against both:

- star width: the larger of the telescope's diffraction limit and the site's seeing, combined with
  the guiding error;
- sky brightness: the site's dark-zenith value;
- transparency: the equipment's best zero point, corrected for airmass.

Against the baseline alone, a lasting fault would read as normal: if focus is always soft,
$\eta$ against the baseline is about 1. The physical reference shows it.

**The goal and the noise regime.** The exponents differ by goal and regime, so $\eta$ is always
stated for one goal in one regime. The regime is found from the measured ratios of sky noise, read
noise and source signal.

| Goal and regime | Seeing factor | Notes |
| :--- | :--- | :--- |
| Faint point source, sky-limited | $(\mathrm{FWHM}_0/\mathrm{FWHM})^2$ | Transparency enters as $(T/T_0)^2$: one magnitude of cloud costs a factor of 6.3 in time. Sky enters as $B_0/B$. |
| Faint point source, read-noise-limited | Depends on sky noise over read noise | Per frame, $\eta_\mathrm{RN} = \sigma_\mathrm{sky}^2/(\sigma_\mathrm{sky}^2 + \mathrm{RN}^2)$. Short frames and narrowband reach this regime. |
| Astrometric precision | $(\mathrm{FWHM}_0/\mathrm{FWHM})^4$ | Position error scales as width over SNR. |
| Extended object | Not a time loss | Seeing costs resolution, which more time does not repair. |
| Bright-star photometry | Not a time loss beyond the floor | Scintillation or a systematic floor limits it. |
| Slitless spectral resolution | Not a time loss | Seeing sets the resolution. |
| Period detection | Not a time loss | The spacing of the nights (the window function) limits it, not the hours. |

**Combining causes.** Factors multiply. To say how much each cause cost, `wayfindinglib` splits
$\ln\eta$ across the causes in proportion to each factor's $\ln$. The split adds up to the
captured time minus the effective time, which makes the night's loss account testable.

It converts to the two views people use:

- **Time.** Effective time is $\eta$ times the exposure time, for example "4.0 h captured, 2.6 h
  effective; clouds cost 0.8 h".
- **Depth.** For a sky-limited point source the depth lost is $1.25\log_{10}(1/\eta)$ magnitudes.
  The depth view states its regime, because the formula does not hold in the others.

Losses that more exposure does not repair carry `recoverable_with_time = False`. That label is
advice in itself, such as "fix the focus; another three hours will not help".

Efficiency functions and references are design estimates until checked on real data. Each one gets
a validation register entry before `wayfindinglib` ranks advice by it.

Phase 4 builds efficiency from the metrics of section 5.1: star width, sky and read noise. The
transparency factor needs the catalog zero point, which is in `QUALITY_SCIENCE_PLAN.md`. Until that
lands, efficiency states that transparency is not included.

## 5. Metrics this plan carries

This plan carries the metrics that already exist, moved into the new shape, plus the few new ones
that efficiency and the target dataset report (section 6) need first. Every other new metric, with its definition and its evidence, is in
`QUALITY_SCIENCE_PLAN.md`.

### 5.1 New early, because efficiency and the target report need them

| Metric | Definition | Why now |
| :--- | :--- | :--- |
| Star width from many stars across the field, with elongation and its angle | A pixel-integrated Moffat fit to up to about 200 isolated, unsaturated stars on a grid over the field, with no elongation cut. Elongation and its angle come from second moments. Stored in pixels and in arcsec, with the plate-scale source. | Today's `measure_image_fwhm` takes the 15 brightest stars, returns pixels and skips stars with elongation above 1.5, so on a trailed frame it measures only the round stars. Efficiency's seeing factor would inherit that bias. |
| Sky brightness in electrons | Background in electrons per second per pixel, from the background level, the gain for the frame's setting and the exposure time | Efficiency's sky factor and the read-noise regime |
| Sky noise over read noise | The ratio of sky noise to read noise for the frame's setting | Decides the noise regime (section 4), and says when an exposure is too short |
| Gain and read noise for each camera setting | Photon transfer from flat pairs and the noise of bias pairs, at each gain setting, offset and binning in use (decision 7) | Every metric above in electrons depends on it |
| Stack noise in empty apertures (phase 3) | Noise of the stack in random apertures of the photometric size, placed away from sources, with the aperture stated. Registration correlates neighboring pixels, so pixel noise underestimates the real noise. | The "more frames" recommendation and the forecast check (sections 6.4 and 6.5) |
| Stack noise against frame count (phase 3) | Empty-aperture noise and the weighted effective frame count and time of each stack run, kept in the history, plus subset stacks when a run has too few predecessors | Gives the count in "take N more", and shows when noise stops falling |

### 5.2 Existing metrics emitted in the new shape (phases 2 and 3)

| Checkpoint | Metrics | Today's home |
| :--- | :--- | :--- |
| Each frame | Background level, saturated fraction, registration star width, roundness, shift and star count; quarantine star count, roundness and sky for every frame; sensor temperature | `FrameRecord.measurements`, the quarantine manifest |
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
A warning reads like "frames at 120 s clip the target core". A recommendation reads like "take 50
more frames at 30 s". Agreed with the user on 2026-10-17 (decisions 11 to 15).

### 6.1 Where it lives

```text
Desktop app: the user selects a target
  → backend RPC method
    → wayfindinglib Planning: get_advisory(kind="quality", target_id=...)
        reads astrometrics.quality.query(target_id=...)     the measurements
        + camera profiles and the equipment catalog          what is possible
        + the performance envelope                           the limits
        + visibility, Moon and sky history                   when to observe
        applies the rules in wayfindinglib/analytics/result_quality/
      → TargetDatasetReport, recomputed on each request and never stored
```

The report extends the planner's existing quality advisory (decision 12), which already has the
right owner. It is forward-looking, so it belongs to Planning. The per-night view stays in
`session_analysis` (Control). Both use the same rules from `analytics/`, which both layers may
import.

### 6.2 What it holds

The unit of the report is the **capture setting**: one camera and telescope, with one filter,
exposure length, gain setting, offset and binning. Warnings and recommendations are statements
about one setting, so every measurement refers to its capture setting (section 3.1).

- **Dataset summary.** One row per capture setting: frames taken, nights, frames set aside,
  captured time, effective time, and the loss account by cause.
- **Warnings.** Problems in the data already taken.
- **Recommendations.** What to do in the next session on this target, with numbers.

A sketch:

```python
class ActionClass(StrEnum):
    CAPTURE_SETTINGS = "capture_settings"
    SCHEDULING = "scheduling"
    CALIBRATION = "calibration"
    DATA_SELECTION = "data_selection"
    SESSION_PROCEDURE = "session_procedure"
    ADJUSTMENT = "adjustment"


class TargetAdvice(BaseModel):
    rule: str  # the rule that produced it
    category: Literal["warning", "recommendation"]
    action_class: ActionClass
    capture_setting: CaptureSetting | None
    message: str  # one plain sentence
    proposed_action: ProposedAction | None  # typed values: exposure, frame count, filter, dither, window...
    expected_effect: ExpectedEffect | None  # e.g. noise down 29%, efficiency up 0.2
    evidence: list[int]  # quality_measurements row ids
    limits: list[QualityLimit]
    confidence: str  # the rule's validation register status


class TargetDatasetReport(BaseModel):
    target_id: str
    capture_settings: list[CaptureSettingSummary]
    warnings: list[TargetAdvice]
    recommendations: list[TargetAdvice]
    forecast_checks: list[ForecastCheck]  # section 6.5
    constraints: list[str]  # limits of the equipment, stated, never advice (section 6.3)
    generated_at: datetime
```

Every item shows its evidence and its confidence. A rule whose register entry is still a design
estimate is shown as tentative. "Insufficient data" is an explicit outcome, never silence.

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

- **The next step.** "Doubling your data (60 more 30 s frames, about 30 min) would cut the noise
  by about 29%." Doubling is the default step, because it is about the smallest change the eye
  sees. The count comes from the measured noise against frame count of this target.
- **The stop signal.** "Noise stopped falling with more frames after about 2 h. More frames will
  not help until you dither or retake flats."
- **The sharpness trade-off.** "Keeping the sharpest 70% of frames sharpens stars by 12% and
  raises the noise by 15%."
- **Comparing targets.** The marginal gain per hour lets the planner say which visible target
  gains most from the next hour.

Where a use has a natural finish line, the report uses it, taken from the pipeline rather than set
by the user:

| Use | Natural goal | Source |
| :--- | :--- | :--- |
| Spectroscopy | SNR enough for the classifier to separate the best subtype from the next | The classifier's gap between its two best matches |
| Photometry | Precision fine enough to detect variability of a given size | The detectable-amplitude measurement |
| Asteroid search | An arc long enough to link the object | Arc length and motion rate |
| Imaging | None by default | Marginal value, as above |

An explicit imaging goal per target (a depth, or a noise level) can be added later if wanted.

### 6.5 Checking the forecast

A recommendation is a forecast: "60 more frames cut the noise by 29%". Once the user takes more
frames and stacks them, the report checks the forecast against the new stack (decision 15):

- The expected noise comes from the previous stack's measured noise and the effective time of the
  old and new frames together, so worse new frames (Moon, cloud) lower the expectation fairly.
- The report says whether the gain arrived as forecast, fell short for a known reason (the new
  frames' lower efficiency), or fell short for no known reason. An unexplained shortfall points to
  a noise floor and raises the "more frames will not help" warning.
- The recommendation is then recomputed from the new stack.

The check needs nothing stored beyond the measurements. Each stack run's noise, frame count and
effective time stay in the append-only history (section 3.5), so the forecast for any stack can be
rebuilt from the run before it. This keeps the rule that recommendations are never stored.

Across targets, the forecast checks are evidence about the marginal-value model itself. They feed
its validation register entry, so the report's confidence in "take N more" is earned from the
user's own results.

### 6.6 Decision catalog

Each warning and recommendation names the metrics it reads. A first catalog:

| Item | Metrics it reads | Example text | Class |
| :--- | :--- | :--- | :--- |
| Overexposed setting | Target clipping through the WCS, saturated fraction; zero-order saturation for spectra | "120 s frames clip the target core in 38 of 40. Use 45 s or less." | Capture settings |
| Underexposed setting | Sky noise over read noise | "30 s frames are limited by read noise. 90 s reaches the same depth in 25% less time." | Capture settings |
| Offset too low | Pixels clipped at zero | "Raise the offset: 2% of pixels clip at zero." | Capture settings |
| Guiding limits exposure | Exposure feasibility from the guide logs | "At 120 s only 40% of frames stay sharp; at 60 s, 85%." | Capture settings |
| Frames lost | Set-aside counts, cloud and trailing, efficiency | "12 of 60 frames set aside; clouds from 22:40 to 23:10." | Data selection |
| More frames | Stack noise against frame count | "Take 60 more at 30 s to cut the noise by about 29%." | Capture settings |
| More frames will not help | Stack noise over the noise expected from the frame count; forecast check | "Dither every 3 frames by about 10 px before adding frames." | Capture settings |
| Focus drift | Star width against focuser temperature | "Refocus every 1.5 °C." | Session procedure |
| Calibration gap | Calibration match, flat-to-light match | "Take darks at gain 100, 60 s, −10 °C." | Calibration |
| Sky cost | Sky brightness, Moon, gradient | "Frames with the Moon above 60% and within 40° lost 45%. Schedule moonless nights." | Scheduling |
| Altitude cost | Efficiency against altitude | "Frames below 30° lost 35%. Image above 35°." | Scheduling |
| Best window | Visibility, Moon, sky history | "Best nights: Oct 24 to 30, 22:00 to 02:00." | Scheduling |
| Period alias | Window function | "Two more nights, 3 days apart, would rule out the alias." | Scheduling |
| Spectrum SNR | SNR per resolution element | "8 more 20 s spectra for a subtype." | Capture settings |
| Asteroid linking | Arc length | "Re-observe within 24 h to link the object." | Scheduling |
| Polar alignment | Field rotation | "Field rotation shows a polar error; redo the polar alignment." | Adjustment |
| Tilt | Center-to-corner star width | "Stars in one corner are 30% wider; check the camera tilt." | Adjustment |

Items whose metrics are in `QUALITY_SCIENCE_PLAN.md` arrive with those metrics.

### 6.7 Every metric has a purpose

Every registered metric declares one purpose (decision 14), and the tests in section 3.7 hold it to
it:

| Purpose | Meaning | Test |
| :--- | :--- | :--- |
| Decision | Feeds at least one warning or recommendation | At least one rule declares it in `requires` |
| Context | Explains other items and is shown as their evidence, such as airmass or sensor temperature | A stated reason, and at least one report section that shows it |
| Exploratory | Collected to answer a question | The question and a review date. Never shown as advice. After the date the test fails until the metric is promoted or removed. |

The review date keeps "exploratory" from becoming a permanent home for unused metrics.

## 7. What happens to today's verdicts and gates

Each existing verdict field and gate is sorted into one of four groups during phase 6, one
pipeline at a time (section 9).

| Group | Rule | Examples |
| :--- | :--- | :--- |
| Processing decision | Stays; recorded as a measurement plus `action_taken` | `frame_quarantine`, `gain_homogeneity`, stacking timeout |
| Self-check | Stays in `astrometricslib` as a measurement with a check outcome | `plate_solve`, `source_detection`, `spectra_extracted`, `search_frames`, `capture_timestamps` |
| Verdict field | Becomes a measurement with an uncertainty | `is_poor_match`, `is_ambiguous`, `is_class_ambiguous`, `is_trustworthy`, `is_low_confidence`, `catalog_agrees`, `saturation_flagged`, `fwhm_degraded`, the periodogram and transit `verdict`, the spectral feature `verdict` (detected, possible, inconclusive) |
| Fitness-for-use gate | Moves to a `wayfindinglib/analytics/result_quality/` rule with a `QualityLimit` | `stack_sharpness`, `scatter_population`, `variability_discrimination`, `catalog_agreement`, `gaia_xp_agreement` |

The examples are a first sort, not a final one. Before any code changes for a pipeline, a written
inventory lists each of its verdict fields and gates with its group, every reader of it and its
replacement. The user approves the inventory, so each move is a deliberate choice.

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
| Enhancement: new code next to the old | 1 to 5 | New models, store and API; a new frame job and a stacking hook; each pipeline's quality file; `wayfindinglib` analytics and session analysis; the backend RPC, the UI types and the MCP tools for the new calls | Low to medium. Today's outputs keep working throughout. |
| Behavior-changing refactor | 6, 7 | Verdict fields and fitness gates leave `astrometricslib`. Readers outside it are listed below. | High. It changes what users see. |

Readers of today's verdicts outside `astrometricslib`:

- **The desktop app.** `ui/common/utils/starDisplayFormat.ts` and its tests use `isPoorMatch`,
  `isAmbiguous`, `isClassAmbiguous` and the periodogram and transit `verdict`.
  `StellarAnalysisDetails.tsx`, `SpectrumViewer.tsx` (spectral feature verdicts) and
  `PhotometryViewer.tsx` also read them. The generated `ui/common/types/backendTypes.ts` follows
  the models, so removing a field changes the UI's types.
- **The planning advisory.** `wayfindinglib/tasks/planning_tasks/quality_advisory_tasks.py` builds
  the planner's quality advisory from `flagged` and `flag_reasons` on all six summaries. It reads
  verdicts only, so it switches in phase 6, not phase 4.
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
6. **One pull request per phase.** Issue #93 becomes an epic with one sub-issue per phase, each
   with its own Analysis and Plan.
7. **Data safety.** The new tables only add; existing table schemas do not change before phase 7.
   `astrometrics.db` is backed up with `scripts/backup_library_databases` before the first write
   and before the history fill.
8. **Numbers stay fixed through the refactors.** The M 13 golden suite must run, not skip: it skips
   when the Git LFS frames are missing. It covers frame quality, detection, star width, the spectral
   frame check and photometry. A real-data spot check (one stack, one plate solve, one asteroid
   search, one spectrum) covers the rest. Both give identical science numbers after phase 0 and
   after each pipeline's phase 6.

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
   metrics of section 5.1. Gain and read noise are first keyed by camera and setting, with the
   calibration measurement that computes them from flat and bias pairs (decision 7). Done when a
   real night's frames have measurements, a second run measures nothing again, and the new star
   width is checked against the current estimator on round and on trailed frames.
3. **Existing pipeline metrics in the new shape.** In each pipeline's change, rename its
   `run_gates.py` to `record_quality.py` (decision 10) and emit measurements next to its current
   summary. In the stacking change, add the stack noise in empty apertures and the effective frame
   count and time of each run (section 5.1). Fix the dropped asteroid `residual_*` counts and
   regenerate the UI types. Run the history fill (safeguard 3). Done when every metric in the five
   summaries has a registered name, appears in the table after a run, and the filled history
   matches the JSON.
4. **Efficiency and recommendations from measurements.** Build
   `wayfindinglib/analytics/result_quality/efficiency.py`: the references, goals, noise regimes
   and the loss split (section 4). Change `Recommendation.evidence` to a typed list of measurement
   references; this changes a reflected MCP tool's output, so note it there. Migrate
   `PerformanceThreshold` to `QualityLimit`. Rules declare the metrics they require. Switch the
   guiding and capture recommendations to `quality.query`. Then build the target dataset report
   (section 6): the action classes, the rules of the decision catalog whose metrics exist, marginal
   value and the forecast check, extended `get_advisory(kind="quality")`, its backend RPC method,
   regenerated UI types, and a data-quality panel on the target view in the desktop app
   (`UI_DISPLAY_PLAN.md`, phase 0). Done when
   the guiding and capture recommendations read through `quality.query`, a real night's loss
   account adds up to the captured time, and a real target's report gives a "take N more"
   recommendation whose forecast is checked after the next stack.
5. **New metrics.** Built under `QUALITY_SCIENCE_PLAN.md`, in its own order, once phase 1 exists.
   This plan only provides the shape they fill.
6. **Verdicts and gates, one pipeline at a time.** Photometry goes first, because its labels and
   its `variability_discrimination` gate already exist (decision 8). For each pipeline: write and
   approve the inventory (section 7), build the rules in `wayfindinglib/analytics/result_quality/`,
   run them in shadow mode (safeguard 4), switch the desktop app, the planning advisory and the
   scripts, add any backend RPC and UI type changes, then turn that pipeline's verdict fields into
   measurements and keep its self-checks. Done for a pipeline when its shadow differences are all
   marked and no field of its models states a verdict about a result.
7. **Retire duplicates.** Only once no reader of a field is left: remove the values that the JSON
   summaries repeat, drop `flagged` and `flag_reasons`, and decide whether `processing_jobs`
   `output_metrics` is retired or derived from the new table. Done when a search of the libraries,
   the backend, the desktop app, the MCP servers and the scripts finds no reader of a removed
   field.

Phases 2 and 3 can run at the same time once phase 1 is done. Phase 5 can start any time after
phase 1.

## 10. Documentation

- `Astrometrics_Library_Architecture.md` gains a section on quality measurement: the measurement
  shape, the checkpoints and the split between measuring and judging, with no code names.
- `Astrometrics_Library_Implementation.md` gains the registry and the store, with their status
  labels.
- The `wayfindinglib` documentation gains efficiency, its references and the result-quality rules.
- The desktop-app guides under `documentation/user_interface/` gain the target data-quality panel:
  how to read its warnings, recommendations, evidence and confidence.
- Each efficiency function, each reference and each moved limit gets an entry in the validation
  register.
- `astrometricslib/models/README.md`, `pipelines/README.md`, `api/README.md` and the
  `wayfindinglib` analytics and `session_analysis` READMEs change with the code.

## 11. Open questions and risks

- **A separate science plan (settled).** The user agreed on 2026-10-17 to move the new metrics into
  `QUALITY_SCIENCE_PLAN.md`. This plan keeps the shared shape, the migration and the four metrics
  efficiency needs first (section 5.1).
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
| Asteroid completeness grid, per-night false-positive rate, timing error | Astronomer | `QUALITY_SCIENCE_PLAN.md` section 2.4 |
| Probability calibration on injections, held out by night or field, minimum sample size | Astronomer | Section 7 |
| Store failures, error type and logging | Engineer | Section 3.5 |
| Golden suite must run, not skip; shadow-mode mechanism | Engineer | Safeguards 4 and 8 |
| The rename of `run_gates.py` can wait for each pipeline's phase 3 | Architect | Decision 10 |

One finding was not adopted: a suggested change to the night id for daylight saving. The night's
date changes at local noon, so a one-hour shift stays in daylight (section 11).
