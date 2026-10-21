# Quality models

This folder holds the pydantic data structures (pydantic is a Python package for classes with typed, validated fields) that describe how well a pipeline run went: the gate results, the batch-wide quality summaries, the per-pipeline input and output quality assessments, and the report shapes that the quality tools return. Like the rest of [`models/`](../README.md), nothing here does any calculation.

Terms are defined in the [glossary](../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What each file is for

- `astrometry_quality.py`, `photometry_quality.py`, `spectroscopy_quality.py` — per-star quality judgments each of those three pipelines attaches to a star's result (for example, how confident a catalog match is, or how good the raw data behind a light curve was).
- `stacking_quality.py` — the two judgments a stacking run attaches to its summary: whether the inputs were sound (`StackingInputQuality`) and whether the stack came out well (`StackingOutputQuality`).
- `quality_summary.py` — the batch-wide `*QualitySummary` classes each pipeline run produces, and the shared base class every one of them extends.
- `gate_result.py` — `GateResult` and `GateStatus`, one record per run-level quality check (a gate): `passed`, `failed` or `not_checked`, with the measured value, the limit it was compared with, and where the limit came from. A check that could not run is `not_checked` with its reason, never `passed`. Each `*QualitySummary` keeps its gates in `gates`.
- `quality_reports.py` — what `QualityDiagnostics` and `ProcessingPipelines.stack_summary` return: `RawFrameCheckReport`, `InputQualityReport`, `SpectralFrameCheckReport`, `StackQualityReport` and `StackSummary`. These only describe; they never change a target.
- `gaia_xp_comparison.py` — the comparison of one calibrated spectrum with Gaia DR3's low-resolution XP spectrum (`GaiaXpComparison`, with per-band residuals), stored on `SpectroscopyResult.gaia_xp_comparison`, and the roll-up for a whole run (`GaiaXpRunSummary`). The calculation is in `pipelines/spectroscopy/post_processing/compare_to_gaia_xp.py`.
- `excluded_frames.py` — the descriptions of light frames the stacker sets aside: `SetAsideFrame` (one frame, with the measurements behind the decision), `QuarantinePreview` (what the check would move) and `RestoreReport` (what a restore listed or moved back).
- `stack_comparison.py` — the result of comparing two stacks: `StackMeasurements` (sky level, pixel noise, large-scale flatness, star width, zero fraction of one stack) and `StackComparison` (two of them, the change in each, and a sentence for each).

For exact behavior, read the code.
