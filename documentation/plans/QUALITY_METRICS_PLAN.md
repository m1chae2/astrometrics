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
- one shared unit for losses: efficiency,
- the metrics wanted at each checkpoint,
- what happens to today's verdicts and gates,
- the impact of the work and how its risk is kept low,
- the order of work.

The findings come from a read-only survey of the repository on 2026-10-17. The code is the source
of truth. Line numbers drift, so this plan names files and functions instead.

## Status

Status on 2026-10-17: design and order of work agreed, nothing built. Phase 0 is next. Tracked in
issue #93.

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
   gate that asks "is this result good enough for a purpose?" moves out.
3. **The shared unit is efficiency.** Efficiency is a number from 0 to 1: the effective exposure
   time divided by the exposure time an ideal frame would need for the same result. It is shown
   as time for planning and as magnitudes for imaging depth. A loss that more exposure cannot
   repair carries the label "not recoverable with more time".
4. **The equipment fingerprint stays in `wayfindinglib`.** Grouping measurements into one
   equipment setup's baseline is interpretation, so it belongs with the judgments. Each
   `astrometricslib` measurement carries the equipment facts its frames record (camera, telescope,
   focal length, pixel scale, binning, gain setting, filter). `wayfindinglib` builds the
   fingerprint from those facts plus the guide optics it already knows. This replaces the earlier
   decision to move `build_equipment_fingerprint` down.
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

## 3. The shared shape

The design separates three records that the current shapes mix together.

| Record | What it is | Lives in | Stored? |
| :--- | :--- | :--- | :--- |
| Measurement | A fact: what was measured, about what, with what uncertainty, by which run | `astrometricslib` | Yes, append-only, with history |
| Judgment | A measurement compared with a limit | Self-checks in `astrometricslib`; fitness-for-use in `wayfindinglib` | Self-checks yes; `wayfindinglib` judgments recomputed |
| Recommendation | What to change, ranked by efficiency lost | `wayfindinglib` | No, recomputed (as `session_analysis` does now) |

A measurement never goes stale. A judgment depends on a limit, and a limit changes when the
equipment or the validation changes, so a judgment must be cheap to redo.

### 3.1 Measurement

A sketch. Names follow the repository's descriptive-naming standard and may change in review.

```python
class SubjectLevel(StrEnum):
    FRAME = "frame"
    NIGHT = "night"
    STACK = "stack"
    TARGET = "target"
    STAR = "star"
    RUN = "run"


class QualitySubject(BaseModel):
    level: SubjectLevel
    subject_id: str  # frame path, stack path, star id or job id
    target_id: str | None
    observing_night_id: str | None  # utilities/observing_night.py
    equipment: EquipmentFacts  # camera, telescope, focal length, binning, gain setting, filter (decision 4)


class MeasurementStatus(StrEnum):
    MEASURED = "measured"
    ASSUMED = "assumed"  # e.g. a camera gain taken from the maker's curve
    NOT_MEASURED = "not_measured"  # always carries a reason; never a default value


class QualityMeasurement(BaseModel):
    metric: MetricName  # a registry key, never free text
    subject: QualitySubject
    status: MeasurementStatus
    value: float | None
    uncertainty: float | None
    sample_count: int | None  # e.g. the number of stars behind a width
    expected_value: float | None  # what the equipment should achieve, when known
    expected_value_source: str | None
    efficiency: float | None  # 0 to 1, see section 4
    recoverable_with_time: bool | None
    reason: str = ""
    pipeline_version: str
    job_id: str | None  # links to prov_activity
    measured_at: datetime
```

A processing decision is a measurement with an extra `action_taken` field, for example "frame
moved to `_excluded/`".

### 3.2 Limit

`QualityLimit` merges `GateResult`'s limit fields with `wayfindinglib`'s `PerformanceThreshold`.
It lives in `astrometricslib`, and `wayfindinglib` imports it from the top-level package.

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

`QualityJudgment` replaces `GateResult`. It points at the measurement instead of copying the
value, so the number lives in one place.

```python
class QualityJudgment(BaseModel):
    name: str  # the gate's name; existing names do not change
    metric: MetricName
    subject: QualitySubject
    status: GateStatus  # passed, failed or not_checked, as today
    limit: QualityLimit | None
    detail: str
```

### 3.4 The metric registry

The registry is what makes the shape shared in practice. Each pipeline declares its metrics in
its own `metrics.py` (decision 6). Metrics shared by several pipelines, such as frame star width,
are declared in `pipelines/shared/quality/metrics.py`. `metric_registry.py` assembles every
declaration into one registry. Names carry their pipeline or checkpoint, for example
`stacking.noise_excess_ratio` or `frame.star_width_arcsec`, and `wayfindinglib` refers to them by
these names. Two estimators of one quantity, such as Siril's FWHM and the library's own, get two
names, never one.

```python
# pipelines/shared/quality/metrics.py
FRAME_METRICS = (
    MetricSpec(
        name="frame.star_width_arcsec",
        unit="arcsec",
        checkpoint=Checkpoint.FRAME,
        levels=frozenset({SubjectLevel.FRAME, SubjectLevel.NIGHT}),
        better=Direction.LOWER,
        meaning="Median full width at half maximum of the stars on the frame.",
        decisions=("focus", "guiding", "seeing"),
        estimator="Library FWHM fit to isolated unsaturated stars",
        computed_by="pipelines.astrometry.pre_processing.fwhm.measure_image_fwhm",
        uncertainty_method="Scatter of the per-star widths over the square root of the star count",
        register_entry=None,
        efficiency=efficiency_from_star_width_ratio,
    ),
)
```

A `MetricSpec` holds:

- the name and the unit, the unit checked against every measurement,
- the estimator, the function that computes it, how its uncertainty is found and its register
  entry, which is what an auditor checks,
- the checkpoint (section 5) and the subject levels it applies to,
- which direction is better,
- a one-line meaning in plain words,
- the decisions it informs,
- an optional function that turns a value and its expected value into an efficiency.

The review-guide generator renders each pipeline's declarations as a metrics table in that
pipeline's guide. The conformance test checks that every pipeline folder contributes a
`metrics.py`, so a missed file cannot silently drop a pipeline's metrics from the registry.

### 3.5 Storage

One table, `quality_measurements`, in `astrometrics.db`, created through the `DiskButler` like the
other tables. It has one row per measurement and is append-only. Columns: metric, subject level,
subject id, target id, observing night id, equipment facts, value, unit, uncertainty, sample
count, expected value, efficiency, recoverable flag, status, reason, pipeline version, job id and
time. Judgments made by `astrometricslib` self-checks go in a companion table,
`quality_judgments`.

The table can be searched across targets and nights, keeps history, and survives a restack. The
JSON summaries stay as a convenience view until their readers move (phase 7).

Agreed with the user on 2026-10-17, so the table scales:

- **Narrow rows for frames, nights, stacks and runs.** Adding a metric adds a registry entry, never
  a column.
- **Stars stay in their star records.** Per-star results are the largest volume (about 2,000 stars
  times 10 metrics per run). They stay in `stellar_objects`. A star-level metric that a
  recommendation needs is summarized per run (median, spread, count) into the narrow table.
- **Indexes** on (metric, observing night), (target, metric) and (subject id).
- **A "latest" view** picks the newest pipeline version for each subject and metric, so readers do
  not see superseded rows by default.
- **Nothing is deleted at first.** Rows are about 100 bytes, so a million rows is about 100 MB. A
  retention rule, such as dropping superseded versions after a year, comes later if size becomes a
  problem.
- **The table is backed up with the database.** It cannot be rebuilt cheaply, because the raw
  frames live on remote storage.

### 3.6 Reading

One public call with parameters, not one function per metric:

```python
library.quality.query(
    metric=..., level=..., target_id=..., observing_night_id=...,
    camera=..., telescope=..., since=..., pipeline_version=...,
) -> list[QualityMeasurement]
```

`wayfindinglib`, the backend and the MCP servers all read through it.

### 3.7 Conformance tests

A test in the style of `pipelines/test/test_gate_conformance.py` checks that:

- every measurement a pipeline emits in its tests uses a registered name and the registered unit;
- every registered metric is produced by at least one pipeline;
- every `wayfindinglib` recommendation rule declares the metrics it reads
  (`requires=("frame.star_width_arcsec", ...)`), and each one is registered;
- every pipeline folder contributes a `metrics.py`, and no two declarations share a name;
- every `VALIDATED_DATASET` limit names a register entry that exists;
- a `NOT_MEASURED` measurement has a reason and no value.

With these tests, a rule can no longer depend on a field that was renamed or dropped without a
test failing.

### 3.8 Where the code lives

Quality code is spread out today: ten model files loose in `models/`, frame checks in
`pipelines/shared/quality/`, gate files in four pipelines, and `QualityDiagnostics` inside
`api/processing.py`. The rule for this work is one quality home per layer. Agreed with the user
on 2026-10-17:

- All quality models move into one subpackage, `models/quality/`, in phase 1. The top-level
  imports do not change.
- `QualityDiagnostics` moves from `api/processing.py` to `api/quality.py`, so every quality read,
  stored or live, starts at `astrometrics.quality`. `test_public_surface.py` and the MCP tools
  follow.
- Each pipeline's `run_gates.py` is renamed `record_quality.py`, because it will emit measurements
  as well as self-checks. The gate-conformance test and the review-guide generator follow.

| Layer | Home | Holds |
| :--- | :--- | :--- |
| Models (shapes only) | `astrometricslib/models/quality/` | Measurement, limit and judgment shapes; today's quality models, moved |
| Shared calculations | `astrometricslib/pipelines/shared/quality/` | The assembled registry, efficiency functions, the frame measurement step |
| Each pipeline | `<pipeline>/post_processing/record_quality.py` and `<pipeline>/metrics.py` | What the pipeline emits, and the declaration of its metrics (decision 6) |
| Storage | `astrometricslib/drivers/quality_store.py` | The two tables, through the butler |
| Public API | `astrometricslib/api/quality.py` | `query` plus the live checks |
| Interpretation | `wayfindinglib/session_analysis/` | Recommendations, and a new `result_quality/` folder holding the moved fitness-for-use rules as a table |

```text
astrometricslib/
├── models/quality/
│   ├── measurement.py        QualitySubject, QualityMeasurement, MeasurementStatus
│   ├── judgment.py           QualityLimit, LimitTier, QualityJudgment (replaces gate_result.py)
│   ├── metric_spec.py        MetricSpec, Checkpoint, Direction
│   └── (moved)               quality_summary, quality_reports, the per-pipeline *_quality files,
│                             excluded_frames, stack_comparison
├── pipelines/
│   ├── shared/quality/
│   │   ├── metrics.py        frame and calibration metric declarations
│   │   ├── metric_registry.py    assembles every metrics.py into one registry
│   │   ├── efficiency.py
│   │   └── frame_measurement.py  the pre-stack step, built from the existing measuring functions
│   └── <pipeline>/
│       ├── metrics.py        that pipeline's metric declarations
│       └── post_processing/record_quality.py    (stacking gains one; its checks stay put)
├── drivers/quality_store.py
└── api/quality.py            QualityDiagnostics moves here

wayfindinglib/session_analysis/
├── guiding/, capture/, sky/  read measurements through astrometrics.quality.query
└── result_quality/
    ├── rules.py              one row per moved gate: metric, limit, wording, required metrics
    └── judge_results.py
```

The count is about nine new modules, four renamed, ten moved and one merged away, plus tests.
The layer contracts do not change. Measurements living in `astrometricslib` also lets
`wayfindinglib` Planning read measured performance directly, which it could not do if the data
lived in `session_analysis`, because Planning may not import Control.

## 4. Efficiency: the shared unit for losses

Efficiency $\eta$ is the effective exposure time divided by the ideal exposure time, from 0 to 1.
When a loss lowers the signal-to-noise ratio (SNR) and SNR grows as the square root of time,
$\eta$ is the squared ratio of the SNR achieved to the SNR an ideal frame would reach in the same
time.

It converts to the two views people use:

- **Time.** Effective time is $\eta$ times the exposure time. Summed over a night's frames it gives
  a loss account, for example "4.0 h captured, 2.6 h effective; clouds cost 0.8 h".
- **Depth.** When the sky is the main noise source, the depth lost is $1.25\log_{10}(1/\eta)$
  magnitudes. When the star itself is the main noise source the conversion differs, so the depth
  view must state its noise regime.

Some losses cannot be converted, because more exposure does not repair them: lost resolution,
saturation, a systematic noise floor, gaps in the time coverage. These carry
`recoverable_with_time = False`. That label is advice in itself, such as "fix the focus; another
three hours will not help".

Efficiency functions are design estimates until checked on real data. Each one gets a validation
register entry before `wayfindinglib` ranks advice by it.

## 5. Metrics at each checkpoint

The tables list the metrics worth having. "Exists" means the code already measures the value, so
the work is to emit it in the new shape. "New" means it must be built.

### 5.1 Each frame, before stacking

A new frame measurement step measures each light frame once, when it enters the library. This
step is the pre-stack checkpoint (decision 5). The frame is the record that joins to guiding,
temperature, altitude and the Moon.

| Metric | Decision it informs | State |
| :--- | :--- | :--- |
| Frame efficiency (star flux over star area, over sky noise) and the night's loss account | Which frames to keep; what cost the night | New |
| Sky brightness (e⁻ per second per pixel; mag per square arcsecond) | Moon, light pollution, twilight | Partly exists (background level) |
| Catalog zero point of the frame | Transparency, cloud | New (photometry has the method) |
| Background gradient size and direction | Moon, horizon or light source | New |
| Sky noise over read noise | Exposure too short | New; needs gain and read noise in the camera profile |
| Pixels clipped at zero | Camera offset too low | Exists for stacks, not per frame |
| Target clipped, located through the WCS | Exposure too long for the target | New |
| Star width (arcsec), elongation and its angle | Focus, guiding, polar alignment, backlash | Width and roundness exist; angle new |
| Center-to-corner star width | Tilt, field curvature, collimation | New |
| Shift and rotation from registration | Dithering, polar alignment | Shift exists; rotation new |
| Star count, roundness and sky, for every frame | Cloud or trailing | Measured for quarantine but stored only for moved frames |
| Sensor temperature against set point and dark library | Calibration match | Partly exists |

### 5.2 Calibration health

| Metric | Decision it informs | State |
| :--- | :--- | :--- |
| Flat noise and flat level | Retake flats | Exists |
| Flat-to-light match (dust shadows moved, camera rotated) | Retake flats | New |
| Dark temperature and exposure match | Retake darks | Partly exists |
| Fixed pattern left on calibrated frames | Calibration or dithering | Partly exists (flat residual study) |

### 5.3 After stacking

| Metric | Decision it informs | State |
| :--- | :--- | :--- |
| Limiting magnitude (5σ point source) and surface-brightness limit | Depth reached | New |
| Measured noise over noise expected from √N, from subset stacks | More frames, dithering, calibration | New |
| Stack star width over input star width | Registration or stacking loss | Exists |
| Rejected-pixel fraction and where it falls | Satellites, aircraft | Fraction exists |
| Walking noise (noise stronger along the drift direction) | Dithering | New |
| Background gradient left after stacking | Flats, gradient removal | New |
| Each night's contribution to the stack | Drop poor nights on purpose | New |

### 5.4 After each pipeline

| Pipeline | Metric | Decision it informs | State |
| :--- | :--- | :--- | :--- |
| Astrometry | Fit residual over the residual expected from star width and SNR | Distortion or tilt against noise | Residual exists; expected new |
| Astrometry | Residual pattern across the field | Distortion model | New |
| Astrometry | Measured plate scale over configured plate scale | Camera spacing changed | New |
| Astrometry | Pointing error | Mount model | New |
| Photometry | Precision at each magnitude over the noise model (photons, sky, read noise, scintillation) | Systematic floor, flats, comparison stars | Partly exists (`noiseModelCurve`) |
| Photometry | Scintillation share | Longer exposures or defocus for bright targets | New |
| Photometry | Period sensitivity from the window function | Which nights to add to break an alias | Partly exists (`alias_ambiguity`) |
| Spectroscopy | SNR per resolution element at the classifying lines | Exposure needed for a subtype | Partly exists |
| Spectroscopy | Resolution achieved over resolution allowed by focus and trail width | Focus | Partly exists |
| Spectroscopy | Wavelength scale and response stability | Calibration | Exists |
| Spectroscopy | Contamination from neighboring spectra | Rotate the camera | New |
| Spectroscopy | Differential refraction | Observe nearer the meridian | Partly exists (checkpoint 0) |
| Asteroid detection | Completeness from injected synthetic movers | What the night could find | New |
| Asteroid detection | False-positive rate | Trust in candidates | New (measured once, in the register) |
| Asteroid detection | Position uncertainty against MPC needs | Whether to report | Exists per detection |
| Asteroid detection | Arc length | Observe again soon to link the object | New |

## 6. What happens to today's verdicts and gates

Each existing verdict field and gate is sorted into one of four groups during phase 6, one
pipeline at a time (section 8).

| Group | Rule | Examples |
| :--- | :--- | :--- |
| Processing decision | Stays; recorded as a measurement plus `action_taken` | `frame_quarantine`, `gain_homogeneity`, stacking timeout |
| Self-check | Stays in `astrometricslib` as a `QualityJudgment` | `plate_solve`, `source_detection`, `spectra_extracted`, `search_frames`, `capture_timestamps` |
| Verdict field | Becomes a measurement with an uncertainty | `is_poor_match`, `is_ambiguous`, `is_class_ambiguous`, `is_trustworthy`, `is_low_confidence`, `catalog_agrees`, `saturation_flagged`, `fwhm_degraded`, the periodogram and transit `verdict`, the spectral feature `verdict` (detected, possible, inconclusive) |
| Fitness-for-use gate | Moves to a `wayfindinglib` rule with a `QualityLimit` | `stack_sharpness`, `scatter_population`, `variability_discrimination`, `catalog_agreement`, `gaia_xp_agreement` |

The examples are a first sort, not a final one. Before any code changes for a pipeline, a written
inventory lists each of its verdict fields and gates with its group, every reader of it and its
replacement. The user approves the inventory, so each move is a deliberate choice.

Two existing structures need care:

- `flagged` and `flag_reasons` on every summary are interpretation. They stay readable while the
  UI and backend move to judgments, then go. The stacking timeout in `pipelines/tasks.py`, which
  sets `flagged` without a gate, becomes a processing-decision measurement.
- The falsification audit work and `test_gate_conformance.py` assume every gate is a pipeline
  output. The conformance test splits in two: one for `astrometricslib` self-checks, one for
  `wayfindinglib` rules. The `failed` and `not_checked` test requirement keeps applying to both.

## 7. Impact and risk

The work is three kinds of change, and only one carries real risk. Agreed with the user on
2026-10-17: take the clean split (decision 1) as a conscious choice, do the mechanical refactor
first, and carry out the behavior-changing refactor last, one pipeline at a time, shadowed and
approved from an inventory.

| Kind | Phases | What it touches (survey of 2026-10-17) | Risk |
| :--- | :--- | :--- | :--- |
| Mechanical refactor: moves and renames, no logic change | 0 | About 80 modules and 55 test files import the ten quality model files; 42 files name `run_gates`; 27 files name `QualityDiagnostics`, including READMEs, the backend's API doc generator, the golden suite and `test_public_surface.py` | Low per change but wide. Merge conflicts with open branches are the main hazard. |
| Enhancement: new code next to the old | 1, 2, 3, 4, 5 | New models, tables and API; the ingest path (phase 2); each pipeline's quality file; `wayfindinglib` session analysis and planning advisory | Low to medium. Today's outputs keep working throughout. |
| Behavior-changing refactor | 6, 7 | Verdict fields and fitness gates leave `astrometricslib`. Readers outside it are listed below. | High. It changes what users see. |

Readers of today's verdicts outside `astrometricslib`:

- **The desktop app.** `ui/common/utils/starDisplayFormat.ts` and its tests use `isPoorMatch`,
  `isAmbiguous`, `isClassAmbiguous` and the periodogram and transit `verdict`.
  `StellarAnalysisDetails.tsx`, `SpectrumViewer.tsx` (spectral feature verdicts) and
  `PhotometryViewer.tsx` also read them. The generated `ui/common/types/backendTypes.ts` follows
  the models, so removing a field changes the UI's types.
- **The planning advisory.** `wayfindinglib/tasks/planning_tasks/quality_advisory_tasks.py` builds
  the planner's quality advisory from `flagged` and `flag_reasons` on all six summaries.
- **Validation scripts.** `measure_variability_cutoff.py`, `validate_photometry_injection.py`,
  `run_empirical_validation.py` and `spectral_registration_quality_analysis.py` read the flags,
  and validation register entries cite the limits that would move.
- **The gate machinery.** `test_gate_conformance.py`, the review-guide generator,
  `Pipeline_Audit_Report.md`, `test_pipeline_doc_parity.py` and the falsification audit work assume
  every gate is a pipeline output.

Safeguards:

1. **Mechanical refactor alone, first.** Phase 0 is one pull request with moves, renames and import
   edits only, so `git diff -M` shows nothing else. It merges when few other branches are open, so
   new code is written in its final place and never moved twice.
2. **Add before removing.** New measurements are emitted next to today's summaries (phase 3).
   Readers switch one at a time (phase 4). Nothing is deleted before phase 7.
3. **Fill the history from existing records.** A one-time script copies the values already stored
   in `targets.data_json` into the measurement table, so readers can switch without restacking.
   The raw frames live on remote storage, so a restack is slow. The script's row counts are
   checked against the JSON.
4. **Shadow mode for phase 6.** For each pipeline, the new `wayfindinglib` rules run on every
   target in the live database next to today's verdicts, and the differences are compared. The
   desktop app switches only once each difference is explained as a bug fixed or a change the
   user approved.
5. **A written inventory per pipeline** (section 6), approved by the user before phase 6 code for
   that pipeline.
6. **One pull request per phase.** Issue #93 becomes an epic with one sub-issue per phase, each
   with its own Analysis and Plan.
7. **Data safety.** The new tables only add; existing table schemas do not change before phase 7.
   `astrometrics.db` is backed up before the first write and before the history fill.
8. **Numbers stay fixed through the refactors.** The M 13 golden suite and a real-data spot check
   (one stack, one photometry run, one spectrum) give identical science numbers after phase 0 and
   after each pipeline's phase 6.

## 8. Order of work

Each phase ends with its tests passing, `ruff` clean, the import-linter contracts unchanged, and
the affected READMEs updated. Each phase is one pull request.

0. **Consolidate (mechanical refactor).** Move the quality models into `models/quality/`, move
   `QualityDiagnostics` to `api/quality.py`, and rename each `run_gates.py` to
   `record_quality.py`. Update imports, the review-guide generator, the generated guides, the
   READMEs and `test_public_surface.py`. No logic changes. Done when the full test suite,
   import-linter, the golden suite and the documentation tests pass, and the diff shows only moves,
   renames and import edits.
1. **Foundations.** Add `QualityMeasurement`, `QualitySubject`, `QualityLimit`,
   `QualityJudgment`, `MetricSpec`, the `metrics.py` declaration pattern and the assembled
   registry with a handful of metrics. Add the `quality_measurements` and `quality_judgments`
   tables through `drivers/quality_store.py`, the `quality.query` call and the conformance test.
   Done when a test writes and reads back a measurement and the conformance test runs on the
   registry.
2. **Frame measurement step.** Measure each light frame once on ingest and write frame-level
   measurements, including what `FrameRecord.measurements` holds today and the quarantine
   measurements of every frame. Add frame efficiency and the night's loss account. Done when a
   real night's frames have measurements and the loss account adds up to the captured time.
3. **Existing pipeline metrics in the new shape.** Each pipeline's `record_quality.py` emits
   measurements next to its current summary. Fix the dropped asteroid `residual_*` counts. Run the
   history fill (safeguard 3). Done when every metric in the five summaries has a registered name,
   appears in the table after a run, and the filled history matches the JSON.
4. **Recommendations from measurements.** Change `Recommendation.evidence` to a typed list of
   measurement and judgment references. Rules declare the metrics they require. Rank advice by
   efficiency lost, and show the night's loss account. Switch readers one at a time, starting with
   `quality_advisory_tasks.py`. Done when the guiding and capture recommendations and the planning
   advisory read through `quality.query`.
5. **New metrics.** First key gain and read noise by camera and setting, and add the calibration
   measurement that computes them from flat and bias pairs (decision 7). Then build the "new" rows
   of section 5 in order of value: sky noise over read noise, noise over √N from subset stacks,
   rotation and elongation angle, dithering advice, target clipping through the WCS,
   center-to-corner width, injection tests. Each tuned limit gets a validation register entry.
6. **Verdicts and gates, one pipeline at a time.** Photometry goes first, because its labels and
   its `variability_discrimination` gate already exist (decision 8). For each pipeline: write and
   approve the inventory (section 6), build the `wayfindinglib` rules, run them in shadow mode
   (safeguard 4), switch the desktop app and scripts, then turn that pipeline's verdict fields into
   measurements and keep its self-checks. Done for a pipeline when its shadow differences are
   explained and no field of its models states a verdict about a result.
7. **Retire duplicates.** Only once no reader of a field is left: remove the values that the JSON
   summaries repeat, and drop `flagged` and `flag_reasons`. Done when a search of the libraries,
   the backend, the desktop app, the MCP servers and the scripts finds no reader of a removed
   field.

Phases 2 and 3 can run at the same time once phase 1 is done. Phase 5 can start any time after
phase 1.

## 9. Documentation

- `Astrometrics_Library_Architecture.md` gains a section on quality measurement: the three
  records, efficiency and the checkpoints, with no code names.
- `Astrometrics_Library_Implementation.md` gains the registry, the tables and the efficiency
  functions, with their status labels.
- Each efficiency function and each moved limit gets an entry in the validation register.
- `astrometricslib/models/README.md`, `pipelines/README.md` and the
  `wayfindinglib/session_analysis` READMEs change with the code.

## 10. Open questions and risks

- **Where metrics are declared (settled).** See decision 6 and section 3.4.
- **Night boundaries (settled).** `observing_night_id` uses the processing computer's local time.
  The user confirmed on 2026-10-17 that the computer and the observatory share a time zone.
- **Gain and read noise (settled, see decision 7).** On 2026-10-17 only the ZWO ASI 533MM Pro had
  measured values, at gain 0. The Nikon D5300 and the ZWO ASI120MC-S guide camera had none. Until a
  setting is measured, its frames get relative efficiency only, not absolute.
- **Calibrated probabilities (settled, see decision 8).** A probability needs an acceptance rule in
  the register: a minimum held-out sample size and a check that predicted and observed rates
  agree. Catalog labels are imperfect, so each register entry states how its labels were checked.
- **Table growth (settled).** See section 3.5: narrow rows, stars kept in their own records,
  indexes, a "latest" view, and retention only if needed.
