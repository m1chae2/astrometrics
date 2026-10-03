# Models

This folder holds the library's pydantic data structures: the shapes of the data the pipelines produce, store, and hand back to callers. Nothing here does any calculation — a model file defines fields and validation, not behavior.

## What each file is for

- `target.py` — `Target` and `FrameRecord`, the records for an astronomical target and its individual raw photographs.
- `stellar_source.py` — `StellarObject` and its nested per-domain results: a star's photometry (light curve) and spectroscopy results.
- `astrometry_quality.py`, `photometry_quality.py`, `spectroscopy_quality.py` — per-star quality judgments each of those three pipelines attaches to a star's result (for example, how confident a catalog match is, or how good the raw data behind a light curve was).
- `stacking_quality.py` — the two judgments a stacking run attaches to its summary: whether the inputs were sound (`StackingInputQuality`) and whether the stack came out well (`StackingOutputQuality`).
- `quality_summary.py` — the batch-wide `*QualitySummary` classes each pipeline run produces, and the shared base class every one of them extends.
- `calibration_ingest.py` — the results of checking calibration frames: `FlatSetAssessment` (is one set of flats good enough, and if not, why) and `CalibrationIngestReport` (what a rescan of the calibration library added, per group, with an assessment of each new flat set).
- `excluded_frames.py` — the descriptions of light frames the stacker sets aside: `SetAsideFrame` (one frame, with the measurements behind the decision), `QuarantinePreview` (what the check would move) and `RestoreReport` (what a restore listed or moved back).
- `moving_object.py`, `moving_object_config.py` — the data structures for tracking a candidate moving object (asteroid) through the detection cascade, and the settings that control that search.
- `camera_profile.py` — what the pipelines need to know about one camera model: saturation threshold, sensor characteristics.
- `provenance.py` — the IVOA Provenance Data Model (PROV-DM) classes recording which run, at what software version, consuming which inputs, produced a given result. See `astrometricslib/drivers/provenance_store.py` for where this is stored and queried.

For exact behavior, read the code — the code is always the source of truth.
