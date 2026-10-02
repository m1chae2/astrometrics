# Scripts

This folder holds standalone command-line scripts: batch processing over the whole catalog, one-time data migrations and cleanups, and empirical validation/analysis of pipeline behavior against real stored data. Each script is meant to be run directly (`python <script>.py [args]`), not imported by the library or the backend.

## Batch processing

- `run_all_target_processing.py` — runs the full pipeline (stacking, astrometry, photometry, spectroscopy, asteroid detection) on every target in the catalog.
- `reindex_all_targets.py` — cycles through every target and re-scans its frames from disk.
- `rebuild_stacks.py` — finds stacks that should be rebuilt (for example, after a calibration change) and rebuilds them only when asked.

## One-time migrations and cleanups

- `migrate_config_to_toml.py` — migrates the old INI configuration file and JSON camera profiles into the current TOML configuration format.
- `merge_duplicate_catalog_stars.py`, `merge_spectroscopy_star_rows.py`, `reconcile_position_only_star_catalog.py` — fold duplicate or split stellar catalog rows for the same real star back into one row.
- `backfill_focal_length.py` — fills in a missing `FOCALLEN` header value on frames captured before the pipeline started requiring it.
- `backfill_stack_previews.py` — makes the preview picture for stacks that were made before stacking started saving one, and records it as the target's processed image (never replacing a picture a person attached).
- `report_equipment_disagreements.py` — reports stored telescope/ISO values that a fresh re-scan of the frame would change, without changing anything itself.

## Empirical validation and analysis

- `run_empirical_validation.py` — the master script that runs astrometricslib's pipelines against real stored data to validate their design against actual results, not synthetic test cases.
- `validate_spectral_and_period_analysis.py` — checks that the spectral and period-search analyses actually tell a real signal from noise.
- `compare_group_steps_with_siril.py` — compares Siril's registration and stacking with our own group alignment and group combining on the group stacks a target already has, to show whether a replacement would lose quality (it changes nothing in the library).
- `rejection_threshold_analysis.py` — an empirical grid search over stacking's sigma/filter-percentile rejection thresholds.
- `spectral_registration_quality_analysis.py` — checks spectral frame registration quality for the SA200 grating.
- `recompute_spectral_analysis.py` — re-runs the current spectral analysis over every already-stored spectrum, without re-extracting from the raw frames.
- `derive_instrument_response.py` — derives a camera's instrument response curve from a known star's master stack.
- `benchmark_siril_concurrency.py` — measures how many Siril stacking runs a given machine should be allowed to run at once.

## Demos and one-off tools

- `seed_local_star_catalog.py` — fills the local Gaia DR3 cache so later batch runs need no network access.
- `reset_stellar_object_library.py`, `stellar_catalog_audit.py` — demo scripts showing how to clear the stellar object library and audit stellar catalog quality.
- `skip_targets.txt` — a plain-text list of target IDs the batch scripts above skip, not a Python module.

For exact behavior, read the code — the code is always the source of truth.
