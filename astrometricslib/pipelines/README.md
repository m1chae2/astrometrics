# Pipelines

This folder holds the five analysis pipelines and the rules every pipeline follows, so that a reader who knows one pipeline can find their way around the others. A pipeline is a fixed sequence of steps that turns a target's raw telescope frames into a scientific result.

Terms are defined in the [glossary](../../documentation/Glossary.md). This document also defines each term the first time it appears.

The five pipelines are:

- `stacking/` aligns many short exposures of one target and averages them into one image with less noise. See the [stacking README](stacking/README.md).
- `astrometry/` works out where in the sky an image points (plate solving: matching its stars to a reference star catalog) and names its stars. See the [astrometry README](astrometry/README.md).
- `photometry/` measures how bright each star is, and how that changes over time.
- `spectroscopy/` spreads a star's light into its colors (a spectrum) and uses the spectrum to classify the star.
- `asteroid_detection/` finds dots that move between frames and checks them against known asteroids. See the [asteroid detection README](asteroid_detection/README.md).

## Two conventions, and which pipelines follow them

A pipeline is organized by two separate conventions:

- **Inside**, its files are split into three stage folders: pre-processing, processing and post-processing. [The internal convention](#the-internal-convention-pre-processing-processing-and-post-processing) describes them.
- **Outside**, `AnalysisPipeline` in `pipeline_base.py` sets how a pipeline talks to the batch and session runner. The runner is the code that runs one pipeline over a batch of frames or over a session. A session is the frames from one continuous run with the same setup; each pipeline states its own grouping rule.

Neither convention replaces the other. A pipeline's `runner.py` adapter implements `AnalysisPipeline` the same way however its own `pipeline.py` is laid out inside.

Astrometry, photometry and spectroscopy follow both conventions. Two pipelines are exceptions:

- Stacking follows the inside convention but has no `AnalysisPipeline` adapter. [The external contract](#the-external-contract-analysispipeline) explains how it is called.
- Asteroid detection has an `AnalysisPipeline` adapter but keeps all its files flat at the package root, with no stage folders.

## The internal convention: pre-processing, processing and post-processing

Spectroscopy (`pipelines/spectroscopy/`) is the reference layout to copy:

```text
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

A file goes at the root only if it spans stages or drives a batch. A file that fits one stage goes in that stage's folder, even if it is the only file there. A standalone tool that is not part of the per-run pipeline at all (not called by `pipeline.py`, `runner.py`, `batch.py` or `frame_analysis.py`) goes in `utilities/`, so the root stays limited to the orchestration chain.

### The structured-object pattern

This pattern gives two kinds of results a fixed home: a judgment of quality, and a comparison with a catalog. Without it, they end up as scattered fields or free-text notes. For example, the judgment "is this classification any good" would spread across several functions in `spectral_classifier.py`, and "does this agree with the catalog" would be a string built in the middle of classification.

The pattern has four parts:

1. The class lives in `models/`, not in the pipeline. This gives a reader, the user interface or another pipeline one place to look for what a field means. `SpectroscopyResult` and `SpectroscopyQualitySummary` live there too. `models/quality/spectroscopy_quality.py` holds `CatalogComparison`, `InputQualityAssessment` and `OutputQualityAssessment`.
2. A builder function lives in the pipeline's own stage folder. It holds the logic that computes the value: `pre_processing/assess_input_quality.py`, `post_processing/compare_to_catalog.py` and `post_processing/assess_output_quality.py`.
3. The result is stored as an additive, optional field on the pipeline's per-object result model (`SpectroscopyResult.catalog_comparison`, `.input_quality` and `.output_quality`). It is never a required field, so existing stored data and existing readers are unaffected.
4. Codegen registration is part of the same change. Codegen is the build step that writes matching TypeScript types for the user interface from the Python models. A new model in `models/` must be added to the `interfaces = [...]` list in `build/codegen/generate_types.py`, or `npm run type-check` fails against the regenerated `ui/common/types/backendTypes.ts`.

These structured objects describe one star or one spectrum. They sit one level below the batch-wide quality summary described in [the external contract](#the-external-contract-analysispipeline), and add to it.

## The external contract: `AnalysisPipeline`

Astrometry, spectroscopy, photometry and asteroid detection are four different pieces of science. Each exposes the same four-step shape to whatever calls it: check the input, do the work, check the output, hand back a result (`process_input`, `run`, `validate_output`, `to_result_dict`; see `pipeline_base.py`).

The four-step shape is imposed from the outside. The pipeline's own algorithm class (`SpectroscopyPipeline`, `AstrometryPipeline` and so on) never sees it. A separate adapter class (`SpectroscopyPipelineAdapter` in `runner.py`, and so on) owns the algorithm instance and reshapes its calls to fit.

Stacking, the fifth pipeline, has no `AnalysisPipeline` adapter. `ProcessingPipelines.stack` and the full-pipeline run call `stacking/stage.py`, which hands the pixel work to `stacking/stack_runner.py`.

`validate_output` builds a batch-wide `*QualitySummary` (`models/quality/quality_summary.py`), a record of how well a whole batch run went. Every summary shares two building blocks. The base class `PipelineQualitySummaryBase` holds the fields every pipeline's summary carries. The mixin `StarIdentificationMetrics` (a small class whose fields are added to another class) holds the fields of the pipelines that identify stars against a catalog. The per-star structured objects described above sit one level below this summary.

**Gates.** A gate is a pass/fail quality entry on a run. A summary carries `gates`: one `GateResult` (`models/quality/gate_result.py`) for each run-level quality check. A gate is `passed`, `failed` or `not_checked`. A check that could not run (for example, a session with too few frames to judge) is `not_checked` and never reads as a pass. Record a gate with `summary.record_gate(...)`. A failed gate flags the run and adds its sentence to `flag_reasons`. All five pipelines record their run-level checks this way, and each pipeline's README lists its gates. The records kept on each star or spectrum, and a few stacking checks (edge crop, exposure saturation), are not gates yet.

**Adding a gate.** Build it from a module-level constant whose name ends in `GATE_NAME`, because a gate built from a bare string cannot be found. Add it to `GATE_REGISTRY` in `test/test_gate_conformance.py` with the statuses it can reach. Write tests that make it `failed` and `not_checked`, each naming the gate in the same test function. That test fails if any of these is missing. It reads the code without running it, so it catches a forgotten gate or test, not a weak one.

## Stacking

`stacking/` follows the three-folder split (`pre_processing/`, `processing/`, `post_processing/`) and the structured-object pattern. `StackingInputQuality` and `StackingOutputQuality` live in `models/quality/stacking_quality.py`. `pre_processing/assess_input_quality.py` and `post_processing/assess_output_quality.py` build them, and `StackQualitySummary` stores them as `inputQuality` and `outputQuality`. The pixel work runs behind the `StackingDriver` contract in `drivers/interfaces/stacking_driver.py`. The pipeline therefore does not depend on the commands or files of Siril, the external image-processing program that does the stacking.

## Astrometry and photometry

Both follow the three-folder split, and each stage folder has its own README.

- `astrometry/` has `pre_processing/` (source detection, star width), `processing/` (catalog star identification, WCS refinement) and `post_processing/` (match quality, gates). The WCS, World Coordinate System, is the formula in the image header that converts a pixel position to a sky position. `pipeline.py` and `runner.py` sit at the root, and a `utilities/` folder holds catalog seeding.
- `photometry/` has `pre_processing/` (per-frame photometry, noise, observation times, input quality), `processing/` (comparison stars, variability indices, period search) and `post_processing/` (gates, output quality, variability labels). Comparison stars are steady stars used as a brightness reference. `batch.py` and `runner.py` sit at the root.

## Asteroid detection

`asteroid_detection/` does not use the three-folder split. `pipeline.py`, `runner.py`, `record_quality.py` and the step modules (`frame_wcs_composer.py`, `detection.py`, `ephemeris.py`, `rotation_period.py`, `mpc_report.py`) all sit at the package root. Its README says which step each file implements.

## Shared code and batch runs

- `shared/` holds code that more than one pipeline uses: frame grouping, angle helpers, star records, target records, catalog queries and the `quality/` measurements. See the [shared README](shared/README.md).
- `tasks.py` runs one target through the stages. `analyze_target` picks one analysis pipeline by name from `PIPELINE_RUNNERS`. `run_full_pipeline` stacks, plate-solves, measures brightness and extracts spectra, and saves the target's record after each stage.
- `target_batch.py` runs `run_full_pipeline` for many targets, each in its own worker process, so one target that crashes does not stop the others.

For exact behavior, read the code.
