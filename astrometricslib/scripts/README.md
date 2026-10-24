# Scripts

This folder holds standalone command-line scripts: batch processing over the whole catalog, one-time data migrations and cleanups, backups, and empirical validation and analysis of pipeline behavior against real stored data. Each script is meant to be run directly (`python <script>.py [args]`), not imported by the library or the backend.

Terms are defined in the [glossary](../../documentation/Glossary.md). This document also defines each term the first time it appears.

## Import rule

A script here may import astrometricslib's internal modules. It never imports another script: code that two scripts share lives in the library. For example, `merge_duplicate_into_survivor` lives in `pipelines/shared/catalog_star_identity.py`, and `backup_catalog_database` lives in `drivers/local_database.py`. The import-linter contract "a library script never imports another script" in `pyproject.toml` checks this rule.

## Batch processing

- `run_all_target_processing.py` — runs the full pipeline (stacking, astrometry, photometry, spectroscopy, asteroid detection) on every target in the catalog. A stack whose frames, calibration frames and settings are unchanged since it was made is skipped; `--force-restack` rebuilds every stack.
- `reindex_all_targets.py` — cycles through every target and re-scans its frames from disk.
- `rebuild_stacks.py` — finds stacks that should be rebuilt (for example, after a calibration change) and rebuilds them only when asked.

## One-time migrations and cleanups

- `migrate_config_to_toml.py` — migrates the INI configuration file and JSON camera profiles into the current TOML configuration format.
- `merge_duplicate_catalog_stars.py`, `merge_spectroscopy_star_rows.py`, `reconcile_position_only_star_catalog.py` — fold duplicate or split stellar catalog rows for the same real star back into one row.
- `backfill_focal_length.py` — fills in a missing `FOCALLEN` header value on frames that lack it.
- `move_stacks_to_stacks_path.py` — moves the pipeline's existing output (stacks, group stacks, rejection maps, previews, processed images) from the frames folder to the `stacks_path` folder, and rewrites the paths in the database. Dry run by default; the backend must be stopped for `--apply`.
- `restore_excluded_frames.py` — a command-line wrapper around `ProcessingPipelines.restore_excluded_frames`. It lists the frames the stacking pipeline moved into `_excluded` folders because of clouds or trailed stars (stars smeared into short lines). With `--apply`, it moves them back and re-scans their targets. Dry run by default.
- `backfill_simbad_object_types.py` — fills in the SIMBAD object types of stars saved without them, so the library can say whether a star is already listed as a variable.
  - It asks SIMBAD about 200 identifiers at a time. Only SIMBAD-named and Gaia-numbered stars are asked: about 40,000 of the 283,000 stars.
  - It rejects any answer more than 30 arcsec from the star's stored position, and changes only the `simbad_object_types` field.
  - It writes nothing without `--apply`. With `--apply` it first backs up the catalog database and then saves after every request, so a stopped run keeps its progress and can be run again. Try `--limit 400` first.
- `backfill_variable_star_flags.py` — looks up each stored star in the AAVSO Variable Star Index (VSX) and in Gaia DR3, and stores the answers. The library can then say "not listed as variable in SIMBAD, Gaia DR3 or VSX" and pick out stars a catalog calls constant.
  - It uses CDS XMatch, the cross-match service of the Strasbourg astronomical Data Centre (CDS), which pairs a list of positions with a catalog's stars. It sends 2,000 stars per request and accepts matches within 3 arcsec.
  - It looks up only stars with an identity unless `--include-position-only` is given. A position-only star is one that no catalog could name, so the library knows it only by its sky position (ids starting `FIELD_J`).
  - It writes nothing without `--apply`, which backs up the catalog database first and changes only the two flag fields.
- `backfill_stack_previews.py` — makes the stretched JPEG and FITS images for stacks that have no saved previews. (A stretched image has its brightness scale rescaled for display, so faint detail shows.) It never restacks. It records the FITS as the target's processed image, replacing an image a person attached unless `--keep-attached-pictures` is given; the attached file stays where it is.
- `audit_target_positions.py` — lists the targets whose stored RA/Dec is farther from the SIMBAD position of their name than a tolerance, with the separation. The tolerance is the larger of 15 arcmin (about one sixth of the 1.60 degree field) and the object's radius when SIMBAD gives its size. It also lists the targets SIMBAD could not resolve, those whose id and common name resolve to different places, and the moving or calibration targets it did not compare ([AST-20](../../documentation/library_design/validation/astrometry.md#ast-20)). It opens the database read-only, creates no file (even for a database in WAL mode) and changes nothing; it takes the database path as an argument (`python -m astrometricslib.scripts.audit_target_positions /path/to/astrometrics.db`). A listed extended object needs a human look, since large objects are framed away from their center on purpose. A listed target can be fixed by setting its position by hand.
- `report_equipment_disagreements.py` — reports stored telescope and ISO values that a fresh re-scan of the frame would change, without changing anything itself. ISO is a consumer camera's sensitivity setting (the header's `ISOSPEED`).

## Backups

The backup scripts copy the library to Google Drive with rclone, a command-line program that copies and syncs files with cloud storage. `drive:` is the name of the rclone remote (the configured Google Drive account), so `drive:Library` is the `Library` folder on that Drive.

### `backup_library_databases.py`

Makes a safe copy of the databases and uploads it.

- **What it copies.** It copies `astrometrics.db` and `astrometrics_log.db` with SQLite's online backup, then checks each copy's integrity. It uploads the copies, `calibration_frames.json` and the configuration file to `drive:Library/_backup/<timestamp>/`. It works with the backend running or stopped.
- **When it skips.** It does nothing when no source file changed since the last good upload (`--force` overrides). A folder whose upload failed stays on disk and is uploaded first on the next run.
- **What it keeps.** On Drive it keeps the 7 newest snapshots plus the newest of each calendar month.
- **Options.** `--dry-run` prints the actions only; `--rclone` and `--remote` choose the program and remote.
- **When it runs.** `build/linux/install_backup_timer.sh` sets it up to run every 4 h. `run_backend.sh stop` also starts it in the background, because with the backend stopped the databases are quiet. That backup can never make `stop` fail or wait.

### `upload_library.py`

Makes the Google Drive copy (`drive:Library`) match the local library (`frames_path`) with `rclone sync`.

- **Deleted and renamed files.** A file deleted or replaced locally moves into `drive:Library_deleted/<timestamp>/`. Recycle folders older than 90 days are removed after each good run (`--keep-deleted-days`). A renamed or moved file is moved on Drive, not uploaded again (`--track-renames`).
- **Safety limits.** One run deletes at most 100 files (`--max-delete`). The run is refused (exit code 2) when the library is missing, empty, or holds fewer than half the files of the last good run (`--accept-file-count` overrides). A file lock stops two runs at once.
- **What it leaves out.** `_backup`, hidden files and half-written temporary files.
- **Exit codes.** Exit code 1 and a desktop notice mean rclone failed. `--dry-run` reports only.
- **When it runs.** `build/linux/watch_library_uploads.sh` (the `astrometrics-library-watch` user service) starts it once the library has been quiet for 5 min, and a timer runs it every 4 h.

### `restore_library_databases.py`

`--list` shows the snapshots on Drive. `--snapshot NAME --destination DIR` downloads one into a new folder, checks each database, and prints how to put the files in place. It never writes to the live library.

### `check_drive_integrity.py`

Read-only. Checks that no raw frame on Drive has gone missing or changed.

- **What it records.** It lists every file under `drive:Library` with `rclone lsjson` (size, MD5 checksum, modification time). It saves the list as a gzipped manifest in `~/.cache/astrometrics/integrity` (newest 10 kept) and compares it with the previous one. The first run records a baseline.
- **Serious problems.** A raw frame under `lights/`, `darks/`, `biases/` or `flats/` that is missing, or has a different checksum or size, gives exit code 2, plus a `notify-send` desktop notice unless `--no-notify`.
- **Moves.** A file whose checksum and size reappear at a new path (such as a frame moved into `_excluded`) is reported as moved, not missing.
- **Information only.** Derived files (stacks, rejection maps, processed images, JPEGs, `.seq` files, `stacks/` folders) are listed but never count as problems. The `_backup` folder is skipped.
- **Other exit codes and output.** Exit code 1 means `rclone` failed. `--json` prints a machine-readable summary.

## Empirical validation and analysis

These scripts measure how well the pipelines work on real stored data. Unless an entry says otherwise, they read only.

### Whole-pipeline checks

- `run_empirical_validation.py` — the master script that runs astrometricslib's pipelines against real stored data to validate their design against actual results, not synthetic test cases. Its runs are recorded in [AST-14](../../documentation/library_design/validation/astrometry.md#ast-14), [PHO-13](../../documentation/library_design/validation/photometry.md#pho-13), [PHO-14](../../documentation/library_design/validation/photometry.md#pho-14), [SPE-12](../../documentation/library_design/validation/spectroscopy.md#spe-12), [SPE-29](../../documentation/library_design/validation/spectroscopy.md#spe-29), [STK-24](../../documentation/library_design/validation/stacking.md#stk-24), [STK-25](../../documentation/library_design/validation/stacking.md#stk-25) and [MOV-04](../../documentation/library_design/validation/asteroid_detection.md#mov-04).
- `report_gate_limits.py` — read-only. For every numeric quality limit, it reports the spread of the saved values across all targets and how many targets are beyond the limit. It also reports the spread of numbers with no limit yet (such as the astrometric residual), which a limit would have to be derived from.
- `recompute_headline_numbers.py` — read-only. An independent re-check of saved headline numbers. It recomputes from the files the light-curve variability (CV, the coefficient of variation: standard deviation divided by mean), the mean brightness and the best period, and the stacks' zero and saturated pixel shares and star widths. It then lists every saved value that disagrees. Star widths are checked twice: by re-running the pipeline method and by a separate half-maximum method.

### Photometry and variability

- `measure_variability_cutoff.py` — read-only. Checks the photometry pipeline's variable-star cutoff against the catalogs.
  - **The cutoff.** A star is flagged when the CV of its light curve is above `median + k x MAD` of the CVs of all stars in the field. MAD, the median absolute deviation, is a spread measure that a few wild values cannot inflate. The pipeline uses k = 7.4, chosen to flag roughly the top 3% of stars and set slightly low on purpose so that a new supernova is not missed.
  - **What it measures.** For each multiplier k it tries (3 to 16, including 7.4), it reports the share of stars the catalogs list as variable that the cutoff flags, and the share of unlisted stars it flags. It also reports an AUC (area under the ROC curve) against catalog labels for how well a star's CV separates listed from unlisted stars, overall and between stars of similar brightness. An AUC of 0.5 means no better than chance, 1 means perfect separation.
  - **Result.** See [PHO-10](../../documentation/library_design/validation/photometry.md#pho-10).
- `validate_photometry_injection.py` — read-only. Measures which variability signals the pipeline's variable-candidate rule finds. That rule flags a star when its variability score is above 1 and its false-discovery q-value is below 0.01 (see `pipelines/photometry/processing/variability_indices.py`).
  - **Method.** In each of many random draws, it picks about 8% of a target's stars as hosts (stars that receive an injected signal), leaving out stars the rule already flags and stars the catalogs list as variable. It adds a signal of known depth to each host's real light curve: a sinusoid or a box transit (a flat-bottomed dip like a planet passing in front of its star), 0.005 to 0.16 mag deep. A magnitude difference of 0.01 is about a 1% change in brightness. It then runs the rule on the whole field.
  - **Output.** The share of injected stars flagged, by depth and star brightness, and how many untouched stars are flagged.
  - **Scope.** It measures the rule and the noise of the photometry, not the aperture photometry itself, because the signals are added to light curves, not pixels.
  - **Result.** See [PHO-09](../../documentation/library_design/validation/photometry.md#pho-09), which also gives the library calls to repeat it on your own data.
- `validate_spectral_and_period_analysis.py` — checks that the spectral and period-search analyses actually tell a real signal from noise. Results: [PHO-12](../../documentation/library_design/validation/photometry.md#pho-12), [SPE-13](../../documentation/library_design/validation/spectroscopy.md#spe-13), [SPE-16](../../documentation/library_design/validation/spectroscopy.md#spe-16) and [SPE-24](../../documentation/library_design/validation/spectroscopy.md#spe-24).

### Stacking

Stacking groups a target's frames by exposure length, makes one stack per group (a group stack), lines the group stacks up with each other (group alignment), and combines them.

- `compare_group_steps_with_siril.py` — compares Siril's registration and stacking with the pipeline's own group alignment and group combining, on the group stacks a target already has. It shows whether replacing one of the pipeline's steps with Siril's would lose quality. It changes nothing in the library. Result: [STK-16](../../documentation/library_design/validation/stacking.md#stk-16).
- `rejection_threshold_analysis.py` — a grid search over two stacking settings, measuring the stacked image's star width and the share of rejected pixels for each pair. The settings are the rejection limit in standard deviations (sigma, 2 to 4) and Siril's sharpness filter percentile (keep the sharpest 80% or 90% of frames, or all). Results: [STK-09](../../documentation/library_design/validation/stacking.md#stk-09) and [STK-24](../../documentation/library_design/validation/stacking.md#stk-24).
- `rejection_small_n_check.py` — simulates pure noise at 5, 8, 15 and 40 frames per pixel and reports how many good samples each rejection rule throws out and how often it catches an injected outlier. Needs no library data. Result: [STK-08](../../documentation/library_design/validation/stacking.md#stk-08).
- `benchmark_siril_concurrency.py` — measures how many Siril stacking runs a given machine should be allowed to run at once.

### Spectroscopy

The spectroscopy scripts use these terms:

- A standard star is a star of known spectrum (Vega, type A0V, by default) observed to calibrate the instrument.
- The instrument response is how sensitive the telescope, grating and camera together are at each wavelength. A response-corrected spectrum has been divided by it.
- A master stack is the stacked image of all of a target's spectral frames.
- An emission line is a narrow bump in a spectrum where hot gas gives off light at one wavelength.

The scripts are:

- `derive_instrument_response.py` — derives a camera's instrument response curve from a standard star's master stack and stores it, with the standard star's airmass (`reference_airmass`), as a JSON file.
- `compare_instrument_responses.py` — compares two instrument-response JSON files over their common wavelength range after scaling both to 1 at 5500 Å. It prints the largest and RMS fractional difference and the difference in red-to-blue tilt (the response at 7000 Å divided by the response at 4500 Å). It exits non-zero when either exceeds `--tolerance`. The default is 0.02 (design estimate; see [spectroscopy design estimates](../../documentation/library_design/validation/spectroscopy.md#design-estimates)). Use it to check that two standard stars observed in the same session give the same response.
- `compare_spectra_with_gaia_xp.py` — read-only. For every star of a processed target, compares its stored response-corrected spectrum with the star's Gaia DR3 XP spectrum.
  - Per star, it prints one line: the tilt (the straight-line slope of the ratio observed / XP, in % per 1000 Å), the RMS residual, the wavelength shift, and the median ratio observed / XP in four bands.
  - It then prints the run summary: the median ratio and scatter per band, which is the residual instrument response, and the `gaia_xp_agreement` gate verdict.
  - **Result.** See [SPE-28](../../documentation/library_design/validation/spectroscopy.md#spe-28).
  - XP spectra are downloaded once and cached in `~/.cache/astrometrics/catalogs/gaia_xp`.
- `recompute_spectral_analysis.py` — re-runs the current spectral analysis over every already-stored spectrum, without re-extracting from the raw frames. It applies the same airmass extinction correction as the pipeline, using the airmass stored with each spectrum. That correction removes the dimming by the atmosphere, which grows with airmass, the amount of atmosphere the light passed through.
- `validate_emission_line_detector.py` — read-only. Measures how often the emission-line detector reports a line that is not there, and how often it misses one.
  - **False lines.** It runs the detector on every stored spectrum. Most are ordinary stars with no emission lines, so a detected line counts as false. Some stars may truly emit (not every stored spectrum has a type), so the rate is an upper bound.
  - **Recovery.** It adds a line of known height to the same spectra and reports the share recovered at each height.
  - **Result.** See [SPE-25](../../documentation/library_design/validation/spectroscopy.md#spe-25).
- `spectral_registration_quality_analysis.py` — checks spectral frame registration quality for the SA200 grating (the Star Analyser 200, a grating with 200 lines per millimeter). Result: [SPE-29](../../documentation/library_design/validation/spectroscopy.md#spe-29).
- `spectral_focus_sweep.py` — the tool for choosing the focuser position at the telescope.
  - **Input.** Spectral frames of one bright star taken at several focuser positions (the `FOCUSPOS` header card). Take at least five frames that straddle the best focus. Pass `--star-position X Y` when the star is not the source nearest the frame center.
  - **Method.** It extracts each spectrum with the normal pipeline extractor and measures the spectrum trail's FWHM (full width at half maximum, the width where brightness falls to half its peak) in 400 Å (ångström, 0.1 nm) bands. It fits a parabola of FWHM against focuser position for each band.
  - **Output.** A table of the best focus per band, the position that minimizes the FWHM at 5500 Å, and the FWHM that position gives at 4500 and 6500 Å. Nothing is saved to the catalog.

### Asteroids

- `validate_asteroid_detection.py` — measures the asteroid detector. It reports the share of injected synthetic movers it recovers at each brightness and speed, and the tracks it confirms in fields with no mover. Read-only, it also counts the confirmed movers on real frames of targets far from the ecliptic (the band of sky around the plane of the Earth's orbit, where most asteroids appear). There, almost all of them are false. Results: [MOV-01](../../documentation/library_design/validation/asteroid_detection.md#mov-01) to [MOV-04](../../documentation/library_design/validation/asteroid_detection.md#mov-04).

## Demos and one-off tools

- `seed_local_star_catalog.py` — fills the local Gaia DR3 cache so later batch runs need no network access.
- `reset_stellar_object_library.py`, `stellar_catalog_audit.py` — demo scripts showing how to clear the stellar object library and audit stellar catalog quality.
- `skip_targets.txt` — a plain-text list of target IDs the batch scripts above skip, not a Python module.

For exact behavior, read the code.
