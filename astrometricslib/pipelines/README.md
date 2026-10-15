# Pipeline architecture

A pipeline is a fixed sequence of steps that turns a target's raw telescope
images into a scientific result. This folder holds five pipelines:

- **Stacking** aligns many short exposures of one target and averages them
  into one image with less noise.
- **Astrometry** works out where in the sky an image points (plate solving:
  matching its stars to a reference star catalogue) and names its stars.
- **Photometry** measures how bright each star is, and how that changes over
  time.
- **Spectroscopy** spreads a star's light into its colours (a spectrum) and
  uses the spectrum to classify the star.
- **Asteroid detection** finds dots that move between frames and checks them
  against known asteroids.

This README describes the rules every pipeline follows, so that a reader who
knows one pipeline can find their way around the others.

Terms are defined in the
[glossary](../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-b-glossary).

Every pipeline under `astrometricslib/pipelines/` follows two independent
conventions. `pipeline_base.py`'s `AnalysisPipeline` governs how a pipeline
talks to the batch/session runner from the *outside*; this doc describes how
a pipeline is organized on the *inside*. Neither replaces the other — a
pipeline's `runner.py` adapter still implements `AnalysisPipeline` exactly as
before, regardless of how its own `pipeline.py` is laid out internally.

## The external contract: `AnalysisPipeline`

Astrometry, spectroscopy, photometry, and asteroid detection are four very
different pieces of science, but each exposes the same four-step shape to
whatever calls it: check the input, do the work, check the output, hand back
a result (`process_input` -> `run` -> `validate_output` -> `to_result_dict`,
see `pipeline_base.py`). Stacking, the fifth pipeline, has no
`AnalysisPipeline` adapter: `ProcessingPipelines.stack` and the full-pipeline
run call `stacking/stage.py`, which hands the pixel work to
`stacking/stack_runner.py`. This is imposed from the outside — the pipeline's
own algorithm class (`SpectroscopyPipeline`, `AstrometryPipeline`, ...) is
never touched by it; a separate adapter class (`SpectroscopyPipelineAdapter`
in `runner.py`, and so on) owns the algorithm instance and reshapes its calls
to fit.

`validate_output` builds a batch-wide `*QualitySummary` (`models/quality_summary.py`),
a record of how well a whole batch run went, which already has its own
shared-base-class convention:
`PipelineQualitySummaryBase` for fields every pipeline's summary carries, and
a `StarIdentificationMetrics` mixin for the ones that identify stars against
a catalog. This doc's convention operates one level lower than that summary —
per star, not per batch — and composes underneath it, not instead of it.

**Gates.** A gate is a pass/fail quality check on a run. A summary also carries `gates`: one `GateResult`
(`models/gate_result.py`) for each run-level quality check. A gate is
`passed`, `failed` or `not_checked`. A check that could not run (for example, a
session with too few frames to judge) is `not_checked` and never reads as a
pass. Record a gate with `summary.record_gate(...)`; a failed gate flags the run
and adds its sentence to `flag_reasons`. All five pipelines record their
run-level checks this way; each pipeline's README lists its gates. The records
kept on each star or spectrum, and a few stacking checks (edge crop, exposure
saturation), are not gates yet.

**Adding a gate.** Build it from a module-level constant whose name ends in `GATE_NAME` (a gate built from a bare string cannot be found), add it to `GATE_REGISTRY` in `test/test_gate_conformance.py` with the statuses it can reach, and write tests that make it `failed` and `not_checked`, each naming the gate in the same test function. That test fails if any of these is missing. It reads the code without running it, so it catches a forgotten gate or test, not a weak one.

## The internal convention: pre-processing / processing / post-processing

Spectroscopy (`pipelines/spectroscopy/`) is the first pipeline organized this
way, and is the reference to copy from:

```
spectroscopy/
├── pipeline.py, runner.py, frame_analysis.py, batch.py,
│   record_and_flag_spectroscopy_stars.py
│   # orchestration: spans multiple stages, or drives a whole batch/session.
│   # stays at the package root, not inside any one stage's folder.
├── pre_processing/
│   # turning raw data into something the next stage can use, plus judging
│   # how good that raw data was (resolution, saturation, coverage).
├── processing/
│   # finding things in the calibrated data (features, a classification).
├── post_processing/
│   # judging the result: does it agree with independent, external
│   # knowledge (a catalog), and how much should it be trusted overall.
├── utilities/
│   # standalone tools nothing in the per-run pipeline calls: deriving
│   # camera calibration constants ahead of time, checking stack
│   # alignment quality after the fact. Not a stage, not orchestration.
└── data/
    # reference files the stages read: template spectra, the atmospheric
    # extinction table, and per-camera calibration JSON files.
```

A file goes at the root only if it genuinely spans stages or drives a batch —
not by default. Something that fits one stage goes in that stage's folder,
even if it's the only file there. A standalone tool that isn't part of the
per-run pipeline at all (not called by `pipeline.py`/`runner.py`/`batch.py`/
`frame_analysis.py`) goes in `utilities/` instead of the root, so the root
stays limited to the actual orchestration chain.

### The structured-object pattern

This pattern gives two kinds of results a fixed home: a judgement of
quality, and a comparison with a catalogue. Without it, they tend to end up
as scattered fields or free-text notes. Before this pattern, for example, "is this classification any good"
lived across three unrelated functions in `spectral_classifier.py`, and "does
this agree with the catalog" was a string built inline inside the middle of
classification, not a value on its own.

The pattern instead:

1. **The class lives in `models/`, not in the pipeline** — same place
   `SpectroscopyResult` and `SpectroscopyQualitySummary` already live, so a
   reader (or the UI, or another pipeline) has one place to look for what a
   field means. `models/spectroscopy_quality.py` holds `CatalogComparison`,
   `InputQualityAssessment`, `OutputQualityAssessment`.
2. **A builder function lives in the pipeline's own stage folder** — the
   logic that computes the value. `pre_processing/assess_input_quality.py`,
   `post_processing/compare_to_catalog.py`, `post_processing/assess_output_quality.py`.
3. **The result is stored as an additive, optional field** on the pipeline's
   existing per-object result model (`SpectroscopyResult.catalog_comparison`,
   `.input_quality`, `.output_quality`) — never a required field, so existing
   stored data and existing readers are unaffected.
4. **Codegen registration is part of the work, not a follow-up** (codegen
   is the build step that writes matching TypeScript types for the user
   interface from the Python models) — a new
   model in `models/` must be added to `build/codegen/generate_types.py`'s
   `interfaces = [...]` list in the same change, or `npm run type-check`
   fails against the regenerated `ui/common/types/backendTypes.ts`.

## Stacking

`stacking/` follows the three-folder split (`pre_processing/`, `processing/`, `post_processing/`) and the structured-object pattern: `StackingInputQuality` and `StackingOutputQuality` live in `models/stacking_quality.py`, are built by `pre_processing/assess_input_quality.py` and `post_processing/assess_output_quality.py`, and are stored on `StackQualitySummary` as `inputQuality` and `outputQuality`. The pixel work runs behind the `StackingDriver` contract in `drivers/interfaces/stacking_driver.py`, so the pipeline does not depend on the commands or files of Siril (the external image-processing program that does the stacking).

## Astrometry and photometry

Both follow the three-folder split. `astrometry/` has `pre_processing/`
(source detection, star width), `processing/` (catalog star identification,
WCS refinement; the WCS, World Coordinate System, is the formula in the image
header that converts a pixel position to a sky position) and `post_processing/` (match quality, gates), with
`pipeline.py` and `runner.py` at the root and a `utilities/` folder for
catalog seeding. `photometry/` has `pre_processing/` (per-frame photometry,
noise, observation times, input quality), `processing/` (comparison stars,
that is, steady stars used as a brightness reference;
variability indices, period search) and `post_processing/` (gates, output
quality, variability labels), with `batch.py` and `runner.py` at the root.
Each stage folder has its own README.

## Shared code and batch runs

- `shared/` holds code that more than one pipeline uses: frame grouping,
  angle helpers, star records, target records, catalog queries, and the
  `quality/` measurements. Its README describes each module.
- `tasks.py` runs one target through the stages. `analyze_target` picks one
  analysis pipeline by name from `PIPELINE_RUNNERS`, and `run_full_pipeline`
  stacks, plate-solves, measures brightness and extracts spectra, saving the
  target's record after each stage.
- `target_batch.py` runs `run_full_pipeline` for many targets, each in its
  own worker process, so one target that crashes does not stop the others.

For exact behavior, read the code.
