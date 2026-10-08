# Pipeline architecture

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
see `pipeline_base.py`). This is imposed from the outside — the pipeline's
own algorithm class (`SpectroscopyPipeline`, `AstrometryPipeline`, ...) is
never touched by it; a separate adapter class (`SpectroscopyPipelineAdapter`
in `runner.py`, and so on) owns the algorithm instance and reshapes its calls
to fit.

`validate_output` builds a batch-wide `*QualitySummary` (`models/quality_summary.py`),
which already has its own shared-base-class convention:
`PipelineQualitySummaryBase` for fields every pipeline's summary carries, and
a `StarIdentificationMetrics` mixin for the ones that identify stars against
a catalog. This doc's convention operates one level lower than that summary —
per star, not per batch — and composes underneath it, not instead of it.

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
└── utilities/
    # standalone tools nothing in the per-run pipeline calls: deriving
    # camera calibration constants ahead of time, checking stack
    # alignment quality after the fact. Not a stage, not orchestration.
```

A file goes at the root only if it genuinely spans stages or drives a batch —
not by default. Something that fits one stage goes in that stage's folder,
even if it's the only file there. A standalone tool that isn't part of the
per-run pipeline at all (not called by `pipeline.py`/`runner.py`/`batch.py`/
`frame_analysis.py`) goes in `utilities/` instead of the root, so the root
stays limited to the actual orchestration chain.

### The structured-object pattern

Before this pattern, two kinds of results tended to end up as scattered
fields or free-text notes with no home: "is this classification any good"
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
4. **Codegen registration is part of the work, not a follow-up** — a new
   model in `models/` must be added to `build/codegen/generate_types.py`'s
   `interfaces = [...]` list in the same change, or `npm run type-check`
   fails against the regenerated `ui/common/types/backendTypes.ts`.

## Stacking

`stacking/` follows the three-folder split (`pre_processing/`, `processing/`, `post_processing/`) and the structured-object pattern: `StackingInputQuality` and `StackingOutputQuality` live in `models/stacking_quality.py`, are built by `pre_processing/assess_input_quality.py` and `post_processing/assess_output_quality.py`, and are stored on `StackQualitySummary` as `inputQuality` and `outputQuality`. The pixel work runs behind the `StackingDriver` contract in `drivers/interfaces/stacking_driver.py`, so the pipeline does not depend on Siril's commands or files.

## Applying this to astrometry and photometry

Not done yet, and not a mechanical copy. `astrometry/` (10 top-level files:
detection, plate-solving, catalog identification, session logic) is a
plausible candidate for the same three-folder split. `photometry/` (4
top-level files) is probably too small for a folder split to add clarity —
it likely only needs the structured-object *pattern* (a real
`input_quality`/`output_quality` model in `models/`, builder functions
alongside the existing code) without new subfolders. Decide this per
pipeline, after spectroscopy's version has had a chance to prove out, not
by applying the split unconditionally.
