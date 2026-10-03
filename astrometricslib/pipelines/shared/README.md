# Shared pipeline tools

This folder holds code that more than one pipeline (astrometry, photometry, spectroscopy, asteroid detection, stacking) depends on. Nothing here runs on its own — it is called from inside a pipeline's own `pipeline.py`/`runner.py`/`batch.py`.

## What each file is for

- `analysis_context.py` — the shared container a pipeline run passes between its own stages: the loaded image, detected sources, and results found so far.
- `frame_grouping.py`, `frame_optics.py` — sort a target's frames by camera, optic, and role (light/dark/flat/bias), and work out which physical telescope or lens took a given frame.
- `frame_scanning.py` — finds image files on disk and reads them into frame records. It skips the output of processing: a file whose name contains `_stacked`, `starless`, `starmask` or `processed`, or whose header has a positive `STACKCNT` (Siril writes it only on a stack, and keeps `IMAGETYP = Light Frame` on it). The sorter files such a stack under `others`, not under the lights.
- `quarantine_path.py` — the name of the `_excluded` folder where stacking moves frames with clouds or trailed stars, and the one test for "is this path inside it". Frame scanning skips that folder so moved frames do not rejoin the target.
- `calibration_ingest.py` — turns a rescan of the calibration library into a report. It compares the library's frame lists from before and after the rescan, lists the groups that gained or lost frames, and measures each new set of flats with the stacker's own flat check (`stacking/pre_processing/flat_calibration.py`). `CalibrationCatalog.refresh` and `CalibrationCatalog.assess_flats` call it.
- `previous_stack_path.py` — the name of the `_previous` folder where a restack keeps the stack it replaced. Frame scanning skips that folder so the old stack never counts as an input.
- `session_identification.py`, `target_sessions.py` — group a target's frames into observing sessions (one continuous night or run with the same setup), and figure out which stars an image sequence contains.
- `camera_passes.py` — plans which cameras a batch run should process, and which targets go to each camera pass.
- `catalog_star_identity.py` — decides whether two different catalog names refer to the same star.
- `star_recording.py` — the shared rule every pipeline uses to decide which stars are worth keeping, and how to save them to the star catalog.
- `applied_camera_profile.py` — records which camera profile (sensor characteristics, saturation threshold) a pipeline run actually used, on that run's quality summary.
- `target_center_hint.py` — turns a target's catalog right ascension/declination into a starting position hint for plate solving.
- `target_records.py` — reads and writes `Target` records in the catalog database.
- `image_conversions.py`, `image_scaling.py` — convert images between formats and adjust brightness/contrast for on-screen display. For a big frame, the display stretch measures the sky from a sample of the pixels (`measure_sky(..., sample_pixels=True)`), which gives the same picture in a fraction of the time.
- `provenance_recording.py`, `staleness.py` — the single call every pipeline makes to record its IVOA provenance (which run, at what version, produced a result — see `astrometricslib/models/provenance.py`), and the matching check for whether an already-saved result still reflects the pipeline's current version.
- `quality/` — shared quality-measurement helpers (background level, saturation, detection confidence, per-frame statistics) that more than one pipeline's own quality checks build on. `quality/raw_frame_check.py` checks raw light frames that are not in the library yet: it measures stars, trails and the move since the previous frame, and flags frames that differ from their batch (see `QualityDiagnostics.check_raw_frames`).

For exact behavior, read the code — the code is always the source of truth.
