# Scripts

This folder holds standalone command-line scripts: batch processing over the whole catalog, one-time data migrations and cleanups, and empirical validation/analysis of pipeline behavior against real stored data. Each script is meant to be run directly (`python <script>.py [args]`), not imported by the library or the backend.

## Import rule

A script here may import astrometricslib's internal modules. It never imports another script: code that two scripts share lives in the library. For example, `merge_duplicate_into_survivor` lives in `pipelines/shared/catalog_star_identity.py`, and `backup_catalog_database` lives in `drivers/local_database.py`. The import-linter contract "a library script never imports another script" in `pyproject.toml` checks this rule.

## Batch processing

- `run_all_target_processing.py` — runs the full pipeline (stacking, astrometry, photometry, spectroscopy, asteroid detection) on every target in the catalog. A stack whose frames, calibration frames and settings are unchanged since it was made is skipped; `--force-restack` rebuilds every stack.
- `reindex_all_targets.py` — cycles through every target and re-scans its frames from disk.
- `rebuild_stacks.py` — finds stacks that should be rebuilt (for example, after a calibration change) and rebuilds them only when asked.

## One-time migrations and cleanups

- `migrate_config_to_toml.py` — migrates the old INI configuration file and JSON camera profiles into the current TOML configuration format.
- `measure_variability_cutoff.py` — read-only. Checks the photometry pipeline's variable-star cutoff (`median + k x MAD`, k = 7.4) against the catalogs: for each multiplier, the share of stars the catalogs list as variable that it flags, the share of unlisted stars, and an AUC for how well scatter separates the two, overall and between stars of similar brightness. On the library (2026-10-09) the scatter separates them no better than chance.
- `report_gate_limits.py` — read-only. For every numeric quality limit, the spread of the saved values across all targets and how many targets are beyond the limit; also the spread of numbers with no limit yet (such as the astrometric residual), which a limit would have to be derived from.
- `recompute_headline_numbers.py` — read-only. Independent re-check of saved headline numbers: recomputes light-curve variability (CV), mean brightness and best period, and stack zero/saturated shares and star widths from the files, then lists every saved value that disagrees. Star widths are checked twice: by re-running the pipeline method and by a separate half-maximum method.
- `validate_emission_line_detector.py` — read-only. Runs the emission-line detector on every stored spectrum of a star not known to emit (any detected line is false) and on the same spectra with a line of known height added, giving the false-line rate and the share of injected lines recovered at each height.
- `validate_asteroid_detection.py` — measures the asteroid detector: the share of injected synthetic movers it recovers at each brightness and speed, the tracks it confirms in fields with no mover, and (read-only) the confirmed movers on real frames of targets far from the ecliptic, where they are almost all false.
- `merge_duplicate_catalog_stars.py`, `merge_spectroscopy_star_rows.py`, `reconcile_position_only_star_catalog.py` — fold duplicate or split stellar catalog rows for the same real star back into one row.
- `backfill_focal_length.py` — fills in a missing `FOCALLEN` header value on frames captured before the pipeline started requiring it.
- `move_stacks_to_stacks_path.py` — moves the pipeline's existing output (stacks, group stacks, rejection maps, previews, processed pictures) from the frames folder to the `stacks_path` folder, and rewrites the paths in the database. Dry run by default; the backend must be stopped for `--apply`.
- `restore_excluded_frames.py` — a command-line wrapper around `ProcessingPipelines.restore_excluded_frames`. It lists the frames the stacking pipeline moved into `_excluded` folders (clouds or trailed stars) and, with `--apply`, moves them back and re-scans their targets. Dry run by default.
- `backfill_simbad_object_types.py` — fills in the SIMBAD object types of stars saved before they were recorded, so the library can say whether a star is already listed as a variable. It asks SIMBAD about 200 identifiers at a time (SIMBAD-named and Gaia-numbered stars only; about 40,000 of the 283,000 stars), rejects any answer more than 30 arcseconds from the star's stored position, and changes only the `simbad_object_types` field. It writes nothing without `--apply`, which first backs up the catalog database and then saves after every request, so a stopped run keeps its progress and can be run again. Try `--limit 400` first.
- `backfill_variable_star_flags.py` — looks up each stored star in the AAVSO Variable Star Index (VSX) and in Gaia DR3 (through CDS XMatch, 2,000 stars per request, matched within 3 arcseconds) and stores the answers, so the library can say "not listed as variable in SIMBAD, Gaia DR3 or VSX" and pick out stars a catalog calls constant. Only stars with an identity are looked up unless `--include-position-only` is given. It writes nothing without `--apply`, which backs up the catalog database first and changes only the two flag fields.
- `backfill_stack_previews.py` — makes the stretched JPEG and FITS pictures for stacks that were made before stacking saved them (it never restacks), and records the FITS as the target's processed image (replacing a picture a person attached unless `--keep-attached-pictures` is given; the attached file stays where it is).
- `report_equipment_disagreements.py` — reports stored telescope/ISO values that a fresh re-scan of the frame would change, without changing anything itself.

## Empirical validation and analysis

- `run_empirical_validation.py` — the master script that runs astrometricslib's pipelines against real stored data to validate their design against actual results, not synthetic test cases.
- `validate_spectral_and_period_analysis.py` — checks that the spectral and period-search analyses actually tell a real signal from noise.
- `compare_group_steps_with_siril.py` — compares Siril's registration and stacking with our own group alignment and group combining on the group stacks a target already has, to show whether a replacement would lose quality (it changes nothing in the library).
- `rejection_threshold_analysis.py` — an empirical grid search over stacking's sigma/filter-percentile rejection thresholds.
- `rejection_small_n_check.py` — simulates pure noise at 5, 8, 15 and 40 frames per pixel and reports how many good samples each rejection rule throws out and how often it catches an injected outlier. Needs no library data.
- `spectral_registration_quality_analysis.py` — checks spectral frame registration quality for the SA200 grating.
- `recompute_spectral_analysis.py` — re-runs the current spectral analysis over every already-stored spectrum, without re-extracting from the raw frames. It applies the same airmass extinction correction as the pipeline, using the airmass stored with each spectrum.
- `derive_instrument_response.py` — derives a camera's instrument response curve from a known star's master stack and stores it, with the standard star's airmass (`reference_airmass`), as a JSON file.
- `benchmark_siril_concurrency.py` — measures how many Siril stacking runs a given machine should be allowed to run at once.

## Demos and one-off tools

- `seed_local_star_catalog.py` — fills the local Gaia DR3 cache so later batch runs need no network access.
- `reset_stellar_object_library.py`, `stellar_catalog_audit.py` — demo scripts showing how to clear the stellar object library and audit stellar catalog quality.
- `skip_targets.txt` — a plain-text list of target IDs the batch scripts above skip, not a Python module.

For exact behavior, read the code — the code is always the source of truth.
