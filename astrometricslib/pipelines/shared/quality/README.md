# Shared quality measurements

This folder holds measurements of how good an image or a batch of frames is, independent of any one pipeline. They work on pixel data that is already loaded and own no outside resource such as a subprocess or a database file. Stacking, spectroscopy and the API layer all use them.

Terms are defined in the [glossary](../../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-a-glossary). This document also defines each term the first time it appears.

## What each file is for

- `quality_metrics.py` — functions that measure how good an image is, such as star width (FWHM, full width at half maximum) and the count of saturated pixels (pixels at the sensor's maximum value).
- `saturation.py` — counts saturated pixels and finds the level at which a raw frame's pixels count as saturated, from the camera profile (`resolve_saturation_threshold`). The raw frame check and the spectral frame check share it, so they agree on the level.
- `background_measurement.py` — reads image data and measures how bright the sky background is. The decision about whether the background changed too much during a stacking session lives in `stacking/pre_processing/background_homogeneity.py`.
- `frame_statistics.py` — counts the frames of a target that share the same camera settings, and measures basic per-frame statistics.
- `frame_selection.py` — chooses which frames a quality check measures, by filter, file-name range and time, for frames in the library and for frames in a plain folder. It only reads.
- `raw_frame_check.py` — the quick quality check of raw light frames before ingestion. It measures each frame's stars, sky level, saturated pixels, long straight lines from satellites or aircraft, and shift from the previous frame. It flags the frames that stand out from the batch.
- `frame_quality_report.py` — builds the reports that `QualityDiagnostics.frame_quality` returns (`raw_check_report` and `input_quality_report`).
- `frame_trends.py` — spots a slow, steady change in one number across a run of frames, using the median of all pairwise slopes (the Theil-Sen slope).
- `detection_confidence.py` — turns a detection's signal-to-noise into one confidence score from 0 to 1. The curve is a heuristic, not a calibrated detection probability.

For exact behavior, read the code.
