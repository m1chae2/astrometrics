# Models

This folder holds the library's pydantic data structures: the shapes of the data the pipelines produce, store, and hand back to callers. Nothing here does any calculation — a model file defines fields and validation, not behavior.

## What each file is for

- `target.py` — `Target` and `FrameRecord`, the records for an astronomical target and its individual raw photographs.
- `stellar_source.py` — `StellarObject` and its nested per-domain results: a star's photometry (light curve, with per-point flux uncertainties and mid-exposure BJD_TDB times) and spectroscopy results.
- `astrometry_quality.py`, `photometry_quality.py`, `spectroscopy_quality.py` — per-star quality judgments each of those three pipelines attaches to a star's result (for example, how confident a catalog match is, or how good the raw data behind a light curve was).
- `stacking_quality.py` — the two judgments a stacking run attaches to its summary: whether the inputs were sound (`StackingInputQuality`) and whether the stack came out well (`StackingOutputQuality`).
- `known_variability.py` — sorts what the catalogs say about a star into known variable, suspected variable, not listed as variable, or unknown. It reads three sources: SIMBAD's object types, Gaia DR3's `phot_variable_flag`, and the AAVSO Variable Star Index (VSX), which also lists stars checked and found constant. The answer describes the catalogs' record, not the sky, and `describe_known_variability` names which catalogs were consulted; `unknown` means none were, and is never the same as `not_listed_as_variable`. `is_confirmed_constant` asks for a positive statement (Gaia `CONSTANT` or VSX `CST`), not just silence. The SIMBAD code lists come from SIMBAD's own definition table and need no network.
- `quality_summary.py` — the batch-wide `*QualitySummary` classes each pipeline run produces, and the shared base class every one of them extends.
- `calibration_ingest.py` — the results of checking calibration frames: `FlatSetAssessment` (is one set of flats good enough, and if not, why) and `CalibrationIngestReport` (what a rescan of the calibration library added, per group, with an assessment of each new flat set).
- `calibration_inventory.py` — `CalibrationEntry` and `CalibrationStats`, how many dark, bias and flat frames the calibration library holds for each camera setting (the shape `CalibrationLibrary.get_stats` returns).
- `excluded_frames.py` — the descriptions of light frames the stacker sets aside: `SetAsideFrame` (one frame, with the measurements behind the decision), `QuarantinePreview` (what the check would move) and `RestoreReport` (what a restore listed or moved back).
- `stack_comparison.py` — the result of comparing two stacks: `StackMeasurements` (sky level, pixel noise, large-scale flatness, star width, zero fraction of one stack) and `StackComparison` (two of them, the change in each, and a sentence for each).
- `moving_object.py`, `moving_object_config.py` — the data structures for tracking a candidate moving object (asteroid) through the detection cascade, and the settings that control that search.
- `camera_profile.py` — what the pipelines need to know about one camera model: saturation threshold, sensor characteristics, and optionally the conversion gain (electrons per ADU) and read noise (electrons) that photometry uses for flux uncertainties.
- `provenance.py` — the IVOA Provenance Data Model (PROV-DM) classes recording which run, at what software version, consuming which inputs, produced a given result. See `astrometricslib/drivers/provenance_store.py` for where this is stored and queried.

For exact behavior, read the code — the code is always the source of truth.
