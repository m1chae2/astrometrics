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
- the order of work.

The findings come from a read-only survey of the repository on 2026-10-17. The code is the source
of truth. Line numbers drift, so this plan names files and functions instead.

## Status

Status on 2026-10-17: design agreed, nothing built.

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
4. **The equipment fingerprint moves into `astrometricslib`.** `build_equipment_fingerprint`
   moves down from `wayfindinglib`, so both libraries use one definition, and the science side can
   key its own baselines by it.
5. **On-demand diagnostics stay read-only.** `QualityDiagnostics` keeps its rule that a check
   never saves anything, so the read-only AI tools can keep calling it. The pipelines are the only
   writers of measurements. A diagnostic reads stored measurements first and computes only what is
   missing, without writing.

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
    equipment_fingerprint: str | None  # moved down from wayfindinglib (decision 4)


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

The registry is what makes the shape shared in practice. Each metric is registered once:

```python
METRICS: dict[MetricName, MetricSpec] = {
    MetricName.FRAME_STAR_WIDTH_ARCSEC: MetricSpec(
        unit="arcsec",
        checkpoint=Checkpoint.FRAME,
        levels=frozenset({SubjectLevel.FRAME, SubjectLevel.NIGHT}),
        better=Direction.LOWER,
        meaning="Median full width at half maximum of the stars on the frame.",
        decisions=("focus", "guiding", "seeing"),
        efficiency=efficiency_from_star_width_ratio,
    ),
}
```

A `MetricSpec` holds:

- the unit, checked against every measurement,
- the checkpoint (section 5) and the subject levels it applies to,
- which direction is better,
- a one-line meaning in plain words,
- the decisions it informs,
- an optional function that turns a value and its expected value into an efficiency.

### 3.5 Storage

One table, `quality_measurements`, in `astrometrics.db`, created through the `DiskButler` like the
other tables. It has one row per measurement and is append-only. Columns: metric, subject level,
subject id, target id, observing night id, equipment fingerprint, value, unit, uncertainty, sample
count, expected value, efficiency, recoverable flag, status, reason, pipeline version, job id and
time. Judgments made by `astrometricslib` self-checks go in a companion table,
`quality_judgments`.

The table can be searched across targets and nights, keeps history, and survives a restack. The
JSON summaries stay as a convenience view until their readers move (phase 6).

### 3.6 Reading

One public call with parameters, not one function per metric:

```python
library.quality.query(
    metric=..., level=..., target_id=..., observing_night_id=...,
    equipment_fingerprint=..., since=..., pipeline_version=...,
) -> list[QualityMeasurement]
```

`wayfindinglib`, the backend and the MCP servers all read through it.

### 3.7 Conformance tests

A test in the style of `pipelines/test/test_gate_conformance.py` checks that:

- every measurement a pipeline emits in its tests uses a registered name and the registered unit;
- every registered metric is produced by at least one pipeline;
- every `wayfindinglib` recommendation rule declares the metrics it reads
  (`requires=(MetricName.FRAME_STAR_WIDTH_ARCSEC, ...)`), and each one is registered;
- every `VALIDATED_DATASET` limit names a register entry that exists;
- a `NOT_MEASURED` measurement has a reason and no value.

With these tests, a rule can no longer depend on a field that was renamed or dropped without a
test failing.

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

Each existing verdict field and gate is sorted into one of four groups during phase 4.

| Group | Rule | Examples |
| :--- | :--- | :--- |
| Processing decision | Stays; recorded as a measurement plus `action_taken` | `frame_quarantine`, `gain_homogeneity`, stacking timeout |
| Self-check | Stays in `astrometricslib` as a `QualityJudgment` | `plate_solve`, `source_detection`, `spectra_extracted`, `search_frames`, `capture_timestamps` |
| Verdict field | Becomes a measurement with an uncertainty | `is_poor_match`, `is_ambiguous`, `is_class_ambiguous`, `is_trustworthy`, `is_low_confidence`, `catalog_agrees`, `saturation_flagged`, `fwhm_degraded`, periodogram `verdict` |
| Fitness-for-use gate | Moves to a `wayfindinglib` rule with a `QualityLimit` | `stack_sharpness`, `scatter_population`, `variability_discrimination`, `catalog_agreement`, `gaia_xp_agreement` |

The examples are a first sort, not a final one. Each gate is checked against the self-check
question in decision 2 before it moves.

Two existing structures need care:

- `flagged` and `flag_reasons` on every summary are interpretation. They stay readable while the
  UI and backend move to judgments, then go. The stacking timeout in `pipelines/tasks.py`, which
  sets `flagged` without a gate, becomes a processing-decision measurement.
- The falsification audit work and `test_gate_conformance.py` assume every gate is a pipeline
  output. The conformance test splits in two: one for `astrometricslib` self-checks, one for
  `wayfindinglib` rules. The `failed` and `not_checked` test requirement keeps applying to both.

## 7. Order of work

Each phase ends with its tests passing, `ruff` clean, the import-linter contracts unchanged, and
the affected READMEs updated.

1. **Foundations.** Add `QualityMeasurement`, `QualitySubject`, `QualityLimit`,
   `QualityJudgment`, `MetricSpec` and the registry with a handful of metrics. Move
   `build_equipment_fingerprint` into `astrometricslib` and import it in `wayfindinglib` from the
   top-level package. Add the `quality_measurements` and `quality_judgments` tables, the
   `quality.query` call and the conformance test. Done when a test writes and reads back a
   measurement and the conformance test runs on the registry.
2. **Frame measurement step.** Measure each light frame once on ingest and write frame-level
   measurements, including what `FrameRecord.measurements` holds today and the quarantine
   measurements of every frame. Add frame efficiency and the night's loss account. Done when a
   real night's frames have measurements and the loss account adds up to the captured time.
3. **Existing pipeline metrics in the new shape.** Each pipeline's `validate_output` emits
   measurements next to its current summary. Fix the dropped asteroid `residual_*` counts. Done
   when every metric in the five summaries has a registered name and appears in the table after a
   run.
4. **Verdicts and gates.** Sort them as in section 6. Turn verdict fields into measurements, move
   fitness-for-use gates to `wayfindinglib` rules, and keep self-checks. Done when no
   `astrometricslib` model field states a verdict about a result.
5. **Recommendations from measurements.** Change `Recommendation.evidence` to a typed list of
   measurement and judgment references. Rules declare the metrics they require. Rank advice by
   efficiency lost, and show the night's loss account. Done when the existing guiding and capture
   recommendations read through `quality.query`.
6. **New metrics.** Build the "new" rows of section 5 in order of value: sky noise over read noise
   (after the camera profiles hold measured gain and read noise), noise over √N from subset
   stacks, rotation and elongation angle, dithering advice, target clipping through the WCS,
   center-to-corner width, injection tests. Each tuned limit gets a validation register entry.
7. **Retire duplicates.** Once every reader uses `quality.query`, remove the values that the JSON
   summaries repeat, and drop `flagged` and `flag_reasons`.

Phases 2 and 3 can run at the same time once phase 1 is done.

## 8. Documentation

- `Astrometrics_Library_Architecture.md` gains a section on quality measurement: the three
  records, efficiency and the checkpoints, with no code names.
- `Astrometrics_Library_Implementation.md` gains the registry, the tables and the efficiency
  functions, with their status labels.
- Each efficiency function and each moved limit gets an entry in the validation register.
- `astrometricslib/models/README.md`, `pipelines/README.md` and the
  `wayfindinglib/session_analysis` READMEs change with the code.

## 9. Open questions and risks

- **Night boundaries.** `observing_night_id` uses the processing computer's local time. If that
  computer and the observatory are in different time zones, nights split in the wrong place. The
  site's coordinates are not yet in the configuration.
- **Guide optics in the fingerprint.** The fingerprint includes the guide scope and guide camera,
  which frame headers do not record. Frame measurements need these values from the configuration,
  or a fingerprint for the imaging train alone.
- **Gain and read noise.** Several efficiency functions need measured gain and read noise. The
  camera profile model has fields for them, but the profiles may not hold measured values yet.
- **Calibrated probabilities.** Turning a verdict into a probability needs labeled examples. Where
  none exist, the measurement keeps the raw statistic and its uncertainty, and gives no
  probability.
- **Table growth.** One row per metric per frame adds up over many nights. Indexes on metric,
  night and target, and a retention rule for superseded pipeline versions, may be needed.
