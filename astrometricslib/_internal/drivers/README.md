# Drivers

This folder holds the code that talks to something outside the pipeline logic itself: disk files, external processes, external databases, and external astronomy services. Pipelines and the API layer call into these drivers rather than opening files or databases themselves, so there is one place responsible for each outside dependency.

Terms are defined in the [glossary](../../../documentation/Glossary.md). This document also defines each term the first time it appears.

Several terms recur below:

- FITS is the standard astronomy image file format. Its text header holds named cards such as `CCD-TEMP` (the sensor temperature).
- A light frame (a "light") is a frame of the sky. Siril is the external program that stacks lights (aligns and averages many frames into one).
- Calibration frames measure the camera itself: bias frames (its fixed electronic offset), darks (the signal heat adds, taken with the shutter closed) and flats (frames of an evenly lit surface, showing how sensitive each pixel is). A master is the combination of many frames of one kind.
- The gain setting is the camera's amplifier setting, a unitless number in the FITS `GAIN` card. It is not the same as the gain in electrons per ADU (analog-to-digital unit, one count of the camera's output) that photometry uses.
- The offset is a constant the camera adds to every pixel, so that noise below zero is still recorded.
- A camera profile is the stored description of one camera model (see `camera_profile_store.py`), including its clip ceiling: the pixel value at which the sensor saturates.
- The stacking README is `../pipelines/stacking/README.md`.

## How a driver gets its settings

A driver that needs the configuration takes an `AppConfiguration` when it is built and keeps it as `self.config`. If none is passed, it uses the shared one. Its methods read `self.config` and never fetch the shared configuration themselves. A module-level driver function that needs settings takes an optional `config` argument in the same way.

## What each file is for

- `image.py`, `fits_access.py` — `AstrometricsImage`, the main data container for a loaded astronomical image, and the lower-level functions that read pixel data and metadata out of a FITS file.
  - `AstrometricsImage` fixes header keywords that older software writes. It renames `RADECSYS` (a retired spelling of `RADESYS`, the card that names the sky coordinate frame) and fills in a missing `MJD-OBS` (the observation start as a Modified Julian Date, a running day count) from `DATE-OBS`. It makes these fixes in memory only and never rewrites a frame, so a frame on a backed-up network drive stays as it was downloaded.
  - `fits_access.read_header`, `read_data` and `read_header_and_data` read a file's header and pixels. They look for the pixel data in HDU 0, then HDU 1. `choose_pixel_hdu` makes that choice from the `NAXIS` card of HDU 0 alone (`NAXIS = 0` means HDU 0 has no data array), so a header read never loads the pixels. `AstrometricsImage` uses the same choice, and `image.read_header_with_fixes` gives its fixed header without building a sky map. Pass `first_hdu_only=True` to read HDU 0 alone. `update_first_header` changes the header of HDU 0 in place. Code outside `fits_access.py` should use these instead of calling `fits.open`.
  - `fits_access.FITS_READ_ERRORS` names the errors that reading a damaged or missing FITS file can raise. Code that reads a file catches that tuple instead of every exception.
  - `fits_access.header_is_plate_solved` says whether a header's sky map came from a plate solve. It needs both sky axes (`CTYPE`, `CRVAL` and `CRPIX` of axes 1 and 2) and either the `PLTSOLVD` card set true or SIP distortion terms (`A_ORDER`). `header_card_is_true` reads the card: a FITS logical, or the text `T`, `TRUE` or `1`; the text `F` is false. The map that INDI writes from the mount's pointing has neither mark. Other apps reach it as `astrometricslib.drivers.header_is_plate_solved`.
- `filter_detection.py` — reads an image's settings to work out what kind of frame it is: which filter (the colored or clear glass in front of the camera), and whether it is a light, dark, flat or bias.
- `interfaces/` — the abstract base classes (`*Driver`) the rest of the library uses instead of a particular program or service:
  - `StackingDriver` — a stacking program. It comes with the `StackSettings` it takes and the `StackRunResult` it returns. It also takes a `PipelineSteps`: two of the stacking pipeline's own steps (the flat check and the alignment of spectral frames) that the stacking program must call partway through its run, between its own commands.
  - `PlateSolveDriver` — a plate solver, which works out which part of the sky an image shows. It returns a `PlateSolveHeader`, a FITS header with two optional extra fields, `fit_residual_rms_arcsec` and `matched_star_count`. A driver that cannot report them returns a plain header, and `read_fit_statistics` reads both cases.
  - `SimbadDriver` — the SIMBAD database of named astronomical objects.
  - `GaiaXpDriver` — Gaia DR3's low-resolution XP spectra, which the spectroscopy pipeline compares its spectra with.
  - `ReddeningDriver` — a catalog of interstellar reddening values. It returns a `ReddeningEstimate`: an E(B-V) in magnitudes (the color excess that dust adds; see `astroquery_gaia_reddening_driver.py` below), a note on its source and the Gaia id used.
- `driver_set.py` — `Drivers`, a frozen record of the plate solver, stacking program, SIMBAD driver, Gaia XP driver and reddening driver a caller chose, with `None` for "use the built-in one". Its `plate_solve_or_default`, `stacking_or_default`, `simbad_or_default`, `gaia_xp_or_default` and `reddening_or_default` methods return the chosen driver or build the built-in one. Code in `pipelines/` and `api/` gets a driver through these methods and does not name a built-in driver class itself (see "Injecting drivers" below). The one listed exception is `pipelines/stacking/stack_runner.py`, which wraps a Siril session its caller already holds.
- `frame_prefetch.py` — reads a run's staged frames (the lights and calibration frames gathered into the run's working folder) before Siril does. Raw frames can sit on a network drive whose mount keeps a local disk cache. Reading them ahead, several at once, puts them in that cache, so Siril does not wait for each download in turn. See "Optional pre-fetch" in the stacking README.
- `siril/` — the Siril driver, the only code that starts the Siril program or writes Siril commands (see "Talking to Siril" below). It is laid out like the pipelines it serves:
  - `core/` — what every Siril run needs. `program.py` finds Siril (a Flatpak or a native program, on Linux or Windows) and builds its command lines; a Flatpak gets the host's files (`--filesystem=host`). `settings.py` makes the private settings file. `lock.py` holds the machine-wide Siril slot. `script_run.py` runs one short script to the end (`run_siril_script`, with an optional offline mode, `-o`). `session.py` and `pipes.py` run the long pipe session of a stack. `version.py` asks Siril for its version. `output_parsing.py` is the one parser of what Siril prints and writes: the registration and stacking totals, frames with many negative pixels and a poor color calibration fit, which become job warnings or preview notices, and the files described below.
  - `stacking/` — calibrate, register and stack: `script_builders.py` (imaging registration applies the frames with `seqapplyreg -interp=la -noclamp`; the spectral path keeps `register -transf=shift`), `staging.py`, `spectral_stacker.py`, `imaging_stacker.py`, `run_models.py` and `image_processing.py`, which joins them as `ImageProcessing`; `process_target` takes one `SirilStackRequest`. `stacking_driver.py` holds `SirilStackingDriver`, Siril as the stacking program, which wraps `ImageProcessing` and turns Siril's files into the plain values the interface names. `comparison_scripts.py` writes the short register and stack scripts of two study scripts in `astrometricslib/scripts`.
  - `astrometry/` — `plate_solve.py` writes Siril's `platesolve` command, always with `-noflip`. Today only the preview's color calibration uses it; the app's plate-solve driver behind `PlateSolveDriver` will be built on it.
  - `preview/` — the stack preview's Siril steps: `color_calibration.py` (photometric color calibration, `pcc`, which plate-solves an unsolved stack first), `stretch.py` (the stretch, `autostretch` or `mtf`, the green removal, `rmgreen`, and saving the FITS and JPEG, `savejpg`) and `starnet.py` (StarNet).
  - `output_parsing.parse_seq_file` reads each registration line by Siril's own field order: FWHM, weighted FWHM, roundness, quality, background level, star count and the 3 by 3 transform (from which it derives the shift, rotation and scale). The line holds no registration error. Earlier code read the background level as `rmse` and the weighted FWHM as `fwhm_y`; both names were wrong. `measure_rejected_fraction` reads the rejection map. `siril/stacking/script_builders.py` also writes the Siril script that builds the master calibration frames and applies them to the lights. The rules for that script follow this list, under "The Siril calibration script".
  - `siril/test/test_siril_driver_rules.py` fails when any other module of the library, the backend, the MCP servers or the simulator starts Siril or holds a Siril command line.
- `astrometry_net_driver.py` — `AstrometryNetPlateSolveDriver`, Astrometry.net as the plate solver. Register entry AST-23 compares its local `solve-field` run with Siril's own plate solver on the same frames, and records the choice of Siril's solver for local solves (partly measured; see [AST-23](../../../documentation/library_design/validation/astrometry.md#ast-23)).
  - The local `solve-field` program writes a `.corr` table next to its solved image. Each row is one field star (a star in the image) that the solver matched to a reference star. The row holds the field star's fitted sky position (`field_ra`, `field_dec`) and the reference star's sky position (`index_ra`, `index_dec`), in degrees.
  - `read_corr_fit_statistics` opens that table (with `memmap=False`), drops rows with a missing position, and returns two numbers: the fit residual (the root mean square of the sky distance between each pair, in arcseconds) and the matched-star count. The driver returns them on a `PlateSolveHeader`.
  - The online service writes no match table, so an online solve returns a plain header and the statistics read as unknown (`None`).
- `gaia_xp_driver.py` — `AstroqueryGaiaXpDriver`, the Gaia XP driver.
  - It downloads the sampled XP spectrum of a Gaia DR3 source with astroquery (3360 to 10,200 Å in 20 Å steps, flux in W m⁻² nm⁻¹).
  - It keeps each answer in one compressed `.npz` file per source id, with the download date, in `~/.cache/astrometrics/catalogs/gaia_xp/`. The Gaia star cache in `catalog_store.py` sits in the same `catalogs` folder. Both hold only downloads, so deleting the folder is safe.
  - A source without an XP spectrum is cached as empty. A failed download raises `GaiaXpDownloadError` and is logged once. Repeated failures make the gateway pause the Gaia archive (see "Talking to outside services"), and the driver then raises at once. Only a missing spectrum gives `None`. The Gaia comparison catches the error, so a spectrum check never stops a pipeline.
- `astroquery_simbad_driver.py` — `AstroquerySimbadDriver`, the SIMBAD driver. It holds no client and no retry code. It passes each question to the gateway (`outside_services/`) and raises a failed one as `ExternalServiceError`, with `offline` or `paused` in its details when that is why nothing was sent.
- `catalog_answer_store.py`, `catalog_cache.py`, `cached_simbad_driver.py` — the one rule every outside catalog follows: ask once, store the answer, reuse it.
  - `catalog_answer_store.py` opens `catalog_answers.db` in the `catalogs` cache folder, beside the Gaia star cache. One row holds one question (catalog, key), the time the answer arrived, and the rows as compressed text. An answer of "nothing there" is stored too.
  - `catalog_cache.py` holds the rule. A stored answer younger than the catalog's lifetime is used with no network call. Otherwise the catalog is asked live through the gateway and the answer is stored. If the live question fails, the stored answer is used even when old, with a warning that names the catalog and the date. With nothing stored, `CatalogUnavailableError` (an `ExternalServiceError`) says so, and the caller carries on with the star unverified. An unreachable catalog is a status, so every message is a warning. A sky question is cut into the HEALPix tiles of `sky_tiles.py`, so two images of one field share an answer. Lifetimes are SIMBAD 180 days, VSX 90 days and Gaia DR3 variability 3,650 days; the tile levels are 6 for SIMBAD and 5 for VSX and Gaia variability (design estimates; see [AST-D24 and AST-D25](../../../documentation/library_design/validation/astrometry.md)). `catalog_use_scope()` records which answers were stored, live, stale or unavailable, and what the gateway did (requests sent, requests skipped, pauses, waits), so a run can list its warnings and log a line such as "12 live, 340 stored; SIMBAD paused 4 min" (`CatalogUseLog.summary()`); `refresh=True` asks again. Two threads that ask the same question at the same time share one request.
  - `cached_simbad_driver.py` wraps `AstroquerySimbadDriver` with that rule for region, name and ADQL questions. `Drivers.simbad_or_default()` returns it. `stored_region` reads only stored tiles. The Enhance object list uses it, so it keeps no list of its own beside the stack. Gaia DR3 star positions keep their own tile tables (`catalog_store.py`), and Gaia XP spectra keep one file per source (they never change, so they never expire).
- `outside_services/` — the gateway every request to an outside service passes through (see "Talking to outside services" below). `services.py` lists the services and their limits, `host_state.py` keeps the shared request times and pauses, `failures.py` sorts errors, `gateway.py` holds `call_service`, and `clients.py` is the only code that builds a network client.
- `gaia_cone_driver.py` — downloads the Gaia DR3 stars of one sky tile.
  - It asks the ESA Gaia archive first (`query_gaia_tile_esa`, table `gaiadr3.gaia_source`, by the tile's range of source IDs) and VizieR second (an online service hosting copies of many catalogs; `query_gaia_tile_vizier`, catalog `I/355/gaiadr3`, which asks for the circle around the tile with `query_gaia_cone_vizier` and keeps the tile's stars). `fetch_gaia_tile` tries them in turn and says how deep the answer is complete.
  - Both return the same columns, brightest first: `source_id`, `ra` and `dec` (right ascension and declination, the sky's longitude and latitude) at epoch 2016.0 (the date the positions refer to), `phot_g_mean_mag`, `designation`, `pmra` and `pmdec`.
  - VizieR has two servers with the same catalogs. The gateway asks the one named by `[Catalogs] vizier_preferred_server` first (default `vizier.cfa.harvard.edu`, the Harvard mirror; the other is Strasbourg, `vizier.cds.unistra.fr`) and falls back as described under "Talking to outside services". The VSX field lookup (`pipelines/shared/variable_star_catalogs.py`) uses the same call.
  - They do not retry a service themselves. The gateway repeats a "too busy" reply and pauses a service that keeps failing. The star identifier (see the astrometry README, `../pipelines/astrometry/README.md`) decides which service to ask first and how long to wait for each.
- `astroquery_gaia_reddening_driver.py` — `AstroqueryGaiaReddeningDriver`, the reddening driver.
  - Gaia measures brightness in three broad bands: G (most of the visible and near-infrared light), BP (the blue part) and RP (the red part). Dust between Earth and a star dims it (extinction) and dims blue light more than red (reddening).
  - The driver asks the Gaia DR3 table `gaiadr3.astrophysical_parameters` for one star's `ebpminrp_gspphot` and `ag_gspphot`, both in magnitudes. The first is the color excess E(BP-RP): how much redder dust has made the star, as the change in the BP − RP magnitude difference. The second is A(G), the extinction in the G band.
  - It converts to E(B-V), the color excess in the standard blue (B) and visual (V) bands. It divides E(BP-RP) by 1.339 and falls back to A(G) divided by 2.740 (ratios for a typical star from Casagrande and VandenBerg 2018, MNRAS 479, L102).
  - It keeps answers in memory, including "no value", because the local Gaia catalog cache (`catalog_store.py`) has no column for either value. A failed query is raised as `ExternalServiceError`.
  - A run asks for all its stars at once. `prefetch_reddening` takes every Gaia id the run needs and sends one query per `GAIA_BATCH_SIZE` (200) ids, so a run is usually one query instead of one for each star. Each later `get_reddening` is a lookup in memory, and a star gets the same value as from a one-star query. One-star queries were slow and erratic: each is a job on the Gaia archive, which allows only a few running jobs per user, and a timed-out query keeps running in an abandoned thread. The archive's answer time is erratic (see SPE-21 in the spectroscopy validation register), so the batch wait is longer than the one-star wait of 60 seconds.
  - A failed batch costs one wait of at most `GAIA_BATCH_TIMEOUT_SECONDS` (180) per run, because the prefetch stops at the first failed chunk. The ids it did not answer fail at once for the rest of the run, with no new query; ids it did answer stay usable. The next prefetch tries again, and a success clears the failure count. For an id that was not prefetched, `get_reddening` still sends a one-star query and stops asking after three failures in a row. The log line for a failure includes the reason (for example an HTTP 503 reply). The spectroscopy pipeline counts the stars that went without reddening and stores the notice `spectroscopy.notice_reddening_lookup_failed`, which appears in the job's warnings.
- `camera_profile_store.py` — finds and loads the stored camera profile (sensor characteristics, saturation threshold, gain and read noise for each camera setting) for a given camera. `save_sensor_setting_noise` writes one setting's gain and read noise back to the config file, after copying the file to a timestamped `.bak` file. A config that still holds the old single gain and read noise loads, but those values are never applied, and a warning is logged once per camera. A save copies the config to a timestamped `.bak` first and keeps at most 10 of them (`MAXIMUM_CONFIG_BACKUPS`), and the whole read-modify-write holds the config's write lock. An entry also keeps the gain spread, each flat pair's gain, the robust read noise and each bias pair's read noise.
- `camera_profile_migration.py` — `migrate_camera_profiles`, which moves a camera's old single gain and read noise into a setting-keyed entry for the setting you state. It reports only (a dry run) unless told to write, backs the config up first, and refuses a camera that already has setting-keyed entries.
- `batch_calibration.py` — `choose_batch_calibration` and `choose_flat_bias`, which pick the dark, bias, flat and flat-bias frames for one batch of lights (an exposure group) and change nothing on disk. The Siril staging code and the stack check (`stacking.predict`) both call them, so the check shows the frames a stack uses. The batch's representative exposure, temperature and binning helpers live here too. The chosen darks pass through `CalibrationLibrary.apply_dark_defect_verdicts`, so a dark the dark defect job left out is not chosen and each marked dark adds one flag sentence.
- `calibration_library.py` — the model of the library of dark, bias and flat frames, and the rules that pick frames for a batch of lights. It can also list the flats as sets, one per telescope, camera, filter, gain setting, offset and binning (`list_flat_groups`), so each set is checked on its own. It lists the dark and bias sets too (`list_calibration_sets`) and keeps the stamp of each measured dark and bias frame (`measurement_stamps`), because those frames have no target to hold it. It also keeps the dark defect verdicts (`dark_defect_verdicts`: a warning or a left-out mark with its reason, by file path), written by `pipelines/stacking/dark_defect_run.py`. A left-out dark stays where it is; `without_omitted_darks` and `apply_dark_defect_verdicts` drop it from a choice, and `clear_dark_defect_verdicts` puts it back. The section "The setting key and picking darks" below gives the filing and picking rules.
- `local_database.py`, `catalog_store.py` — the local SQLite databases holding the target and star catalog and the cached Gaia star catalog.
  - The Gaia cache stores each star's epoch 2016.0 position, G magnitude, designation and proper motion: the star's slow drift across the sky, in milliarcseconds per year. `pmdec` is the drift in declination. `pmra` is the drift in right ascension multiplied by the cosine of the declination. Lines of equal right ascension crowd together toward the poles, so the cosine turns an angle of right ascension into a true angle on the sky.
  - A cache file written before the proper motion columns existed gets them added when it is next opened, and its old rows read as `None` (unknown). Callers that ask `stars_in_tiles` or `stars_by_source_id` for `include_proper_motion` get the two values at the end of each row.
  - The cache also keeps a `gaia_cache_regions` table: one row per downloaded region with its center, radius, magnitude limit and whether the download hit the row limit. The cache answers a request only when a recorded region covers the whole request, is at least as deep, and was not truncated. Otherwise the pipeline downloads. A cache written before this table existed holds rows of unknown depth, so each region is refreshed once and then recorded.
- `catalog_access.py` — reads and writes catalog data (targets, stars) to and from the local database. It also answers the plain star listings and counts inside the database (`query_star_summary_page`), so a listing of the star catalog reads one page, not every star.
  - A star's row keeps its id, name, position, brightness and targets in their own columns. The stored record (`data_json`, built by `stellar_storage_payload`) leaves out each of these when the column gives it back exactly, every field still at its default value, and the values worked out from other fields (`hasSpectra`, `plotData` and the rest).
  - **Only measured stars are saved.** `merge_and_record` on the star catalog does not write a star whose merged record has neither a light curve nor a spectrum (`holds_measurements`), and leaves its saved row as it was. A merge rule may also give `None` to save nothing for a record. `put`, which replaces the whole table, is not checked; only tests and seeding use it.
  - `stellar_object_from_row` puts the column values back when a row is read, so a star reads back equal to the one saved, and a row saved in the older full form reads as before. Older versions saved an unknown position, flux, magnitude or B-V color as blank text (`""`); it reads as `None`, the value the column holds for it. A script that reads the raw record must read the columns too (see `STAR_RECORD_COLUMNS`) and expect a missing key wherever a field is at its default. `StellarObject.serialize` still gives the full form for the app.
  - `read_star_radii` reads only the saved star radius out of each record, for the star overlay.
- `provenance_store.py` — repository for the IVOA (International Virtual Observatory Alliance) provenance graph, which records which run, at what version, produced a result (see `astrometricslib/models/provenance.py`). It is stored next to the job records (`astrometricslib/foundation/jobs/`) in `astrometrics_log.db`. Its tables are made by an ordered list of migration steps (`_MIGRATION_STEPS`), one per table with its indexes; opening a database adds any table that is missing and changes nothing else. A schema change is a new step at the end of the list.
- `observatory_configuration_store.py` — `ObservatoryConfigurationStore`, the table `observatory_configurations` in `astrometrics.db`: one row per observatory configuration, with its camera-and-focal-length key as a column. A configuration is never deleted. `insert` refuses an id already stored, and `replace` saves only when the stored revision is the one the caller read, both under the table's file lock.
- `quality_store.py` — `QualityStore`, the append-only store of quality measurements (see `astrometricslib/models/quality/measurement.py`) in `astrometrics.db`. Each field of a measurement is its own column. A second write of the same run, metric, subject and channel is ignored (the insert says `ON CONFLICT ... DO NOTHING`, so any other constraint error still raises), never replaced. The only rows ever removed are those of a deleted frame: `delete_frame_measurements` removes the frame-level rows of the given subject ids, and the weight rows of those frames inside subset stacks (`<frame id>#<label>`), in one transaction. It creates no file or table, and never touches a stack or a run. The view `quality_measurements_latest` gives the newest row for each subject, metric and channel. Capture settings (`CaptureSetting`) are stored once in `quality_capture_setting` and measurements point at them by id. Reading and writing are separate paths. `query` and `get_capture_setting` open the file read-only, take no write lock and never migrate; a missing file or missing tables read as empty, and a database written by newer code raises `StorageError`. Only a write (`write_measurements` or `get_or_create_capture_setting`) creates the file and the tables. A batch commits whole or not at all. Times are stored as UTC with `+00:00`. A stored row that fails the model checks raises `StorageError` naming the row. `query` filters the rows; given `row_ids` it returns exactly those rows, even if newer ones exist. A read of one target's newest rows, or of one metric's newest rows over the whole library (`target_id` or `metric` given, newest rows only, and no filter other than the metric, level, target and subset label), is kept in memory when it gives at least `LATEST_READ_MINIMUM_ROWS` rows; up to `LATEST_READ_MAXIMUM_ENTRIES` reads are kept. The next read of the same question reads only the subjects that have rows numbered above the last one seen, instead of the window query over the whole table, and gives the rows a full read would give. It reads in full when a row below the last one seen was deleted or replaced (the count and the sum of the row numbers up to it, and the row at it, are checked), too many subjects changed, or the schema version differs. Each caller gets its own copies of the rows. `data_version(metric, level, target_id)` gives a short opaque text made from the count, newest row number, row-number sum and newest time of the matching rows, the row at the newest number, the schema version and the path, in one short query (with a target it also counts every row of the target's subjects, so a frame moved to another target changes it); `quality.records.get_data_version` hands it to callers, so a cache outside the library keeps its own results without seeing row numbers. Evidence: [validation register](../../../documentation/library_design/validation/wayfinding.md), WAY-D107 (Design estimate, with a measurement on a copy of the real library). The schema version is kept in its own one-row table, `quality_schema_version`; the store never reads or writes `PRAGMA user_version`, which is shared by the whole file. A database from the first release (quality tables but no version table) is recognized as version 1 and gets the table. Version 2 adds the column `variant` (the label of a subset stack; empty for a library stack). The first write to a version 1 file adds the column, fills it for the rows whose subject id holds `/_subsets/<label>/`, and rebuilds the latest view. `query` reads library and subset rows alike unless given `variant=<label>` or `library_only=True`. Version 3 adds the side table `quality_calibration_identity` (one row per run and library stack: the flat, bias and dark masters, optic, camera, Siril version and stacking algorithm version). `write_calibration_identities` adds a row and ignores a repeat of the same run and subject; `get_calibration_identities` reads by run ids or target, and gives an empty list for a file older than version 3. The move from version 2 only creates the table. Version 4 rounds the pixel scale in `quality_capture_setting` to the same 4 significant figures `CaptureSetting` uses (`round_pixel_scale`), because frame headers write one scale with different precision and each value had become its own setting. The move from version 3 merges the rows that then match on every field (NULL matches NULL) into the one with the smallest id, points their measurements at it, and deletes the others, all in the write's one transaction; only `quality_measurements` refers to a setting id. A second run changes nothing. Version 6 adds the nullable column `flat_bias_masters` to `quality_calibration_identity`; older rows keep NULL, meaning not recorded. Version 7 adds the index `quality_measurements_job_id` on `job_id`, so reading the warnings of one job (`Jobs.query` with `detail="warnings"`) does not scan the table; the move only creates the index. A read never migrates, so a file is upgraded by its first write; until then the read still works, only slower. Run `scripts/backup_library_databases.py` first. A database error is raised as `StorageError`.

## Talking to outside services

Every question to a computer outside this one goes through `outside_services/`: SIMBAD, VizieR, the ESA Gaia archive (tiles, reddening and XP spectra), SkyBoT, Astrometry.net, file downloads for program set-up and the connection check for the server Siril asks for Gaia stars (`SIRIL_GAIA_CATALOG`). Nothing else in `astrometricslib` or `wayfindinglib` may import a network client. The import-linter contract "only the outside-service gateway imports network clients" in `pyproject.toml` and `test/test_outside_services_enforcement.py` check that, so a new `import requests` fails the lint step. Error classes (`requests.exceptions`, `astroquery.exceptions`, `pyvo.dal.exceptions`) may still be imported, because they send nothing. `wayfindinglib` reaches the same services only through public names: `get_simbad_driver()` and `get_gaia_archive()` in `astrometricslib.drivers`.

`call_service(service, ask)` applies these rules to each question, in this order:

1. **Offline.** A launch that sends nothing off the computer refuses at once (`OfflineModeError`). That is a status, not an error.
2. **Pauses.** A host that is paused is skipped. When every host of the service is paused, `ServicePausedError` is raised and nothing is sent. The stored-answer layer (`catalog_cache.py`) then uses an old stored answer and logs a warning; with nothing stored, the star stays unverified.
3. **Rate limit.** Each host is sent at most a set number of requests per second: 4 for SIMBAD and VizieR, 1 for the others. The file `outside_services.db` next to `catalog_answers.db` holds each host's next free time slot, so the limit holds across the backend, its job workers and scripts together (register entry AST-D26).
4. **Server choice.** SIMBAD and VizieR each have two servers. The one named by `[Catalogs] simbad_preferred_server` or `vizier_preferred_server` is asked first, or the one that answered last, until the setting changes. A host that cannot be reached, is too busy, has a server error or answers 404 or 410 (the query address is gone; see register entry AST-D27) hands the question to the other. A reply that arrived but cannot be read is raised at once, because the other server would answer the same. A call for one object or file (Gaia XP spectra, file downloads and Astrometry.net) reads a 404 as "there is none": no other server is asked and no pause starts.
5. **Busy repeats.** A "too busy" reply is repeated twice, 2 s apart, before the other server is asked. A reply with `Retry-After` is not repeated.
6. **Pauses start.** A 429, a 404 or 410 on a query address, a timeout, or a 503 that persists, pauses the host for the `Retry-After` time or, without one, for 60 s that doubles to at most 900 s. The 404 pause starts at once, because a missing address does not come back in seconds. A timeout also pauses at once, because it costs the whole time limit and jobs started together would each pay it; the shared state file makes the others skip the host. Two refused connections or server errors in a row also pause it. One good answer clears the pause. A pause is shared by every process (register entry AST-D27).

`catalog_use_scope()` counts what the gateway did while it is open: requests sent, failed and skipped, the pauses that started and the waits the rate limit caused. A job logs `log.summary()` and lists `log.warnings()` so a pause is never lost.

SIMBAD lookups by name are grouped where a caller has several names: `resolve_names` asks for a target's id and common name in one ADQL question (`resolve_catalog_positions_in_one_request`), and the backfill scripts ask about 20 targets or 200 identifiers at a time.

To add a service, add an `OutsideService` row to `services.py`, build its client in `clients.py`, and add its limits to the register entries AST-D26 and AST-D27.

## Talking to Siril

Only the Siril driver (`siril/`) starts Siril. Every run it starts follows three rules:

- **A private settings file.** Siril reads its settings at start and writes them back at exit, and some commands change them: after each plate solve, Siril stores the solved focal length and pixel size as its new defaults. So every run is given the app's own copy (`-i`), never the user's file. `prepare_private_settings` copies the user's Siril settings (`config.<version>.ini` in Siril's settings folder; under `~/.var/app/org.siril.Siril` for the Flatpak) the first time, copies in the StarNet and GraXpert paths (`starnet_exe`, `graxpert_path`) on every run, and sets `update_default_scale=false`. The copy is `siril/siril_settings.ini` in the app's data folder. The user's own file is only read. The one exception is the interactive Siril window that `open_in_viewer` opens: it is the user's own Siril, so it keeps the user's own settings (the user's decision). The driver's tests never read the user's settings: an autouse fixture in `siril/test/conftest.py` (and the same one in the stacking tests' `conftest.py`) points the home and settings folders at a temporary folder with a made-up settings file, and stops a test that tries to read the real one.
- **One lock.** Every run first waits for a free Siril slot (`siril_process_lock`, `max_concurrent_jobs` slots shared with the analysis jobs), and the wait is not counted in its timeout. The GraXpert run of the preview takes the same slot. Only a short run that must never wait behind a stack (mount centering, later) passes `wait_for_slot=False` to `run_siril_script`.
- **Stoppable, with a timeout.** Each run is registered with the job it belongs to, so cancelling the job stops its Siril and everything Siril started. A short script run is stopped after its timeout (120 s by default).

A short script (the preview steps, the study scripts) runs with `siril -i <settings> -d <folder> -s <script>`. A stack runs in a pipe session: Siril reads commands from one pipe and writes its answers to another (`siril -i <settings> -p -r <command pipe> -w <output pipe>`). Siril runs one command at a time and reports each with a "status: success" or "status: error" line.

- The first command waits for Siril's "ready" line. Siril opens its output pipe a moment after it opens the command pipe, and a status sent before that is lost. If "ready" does not come within `SIRIL_PIPE_CONNECT_TIMEOUT_SECONDS` (30), the run stops with an error.
- Quick setup commands (`setext`, `set32bits`, `cd` and similar, `QUICK_COMMAND_WORDS`) wait `SIRIL_QUICK_COMMAND_TIMEOUT_SECONDS` (30) for their status and are sent once more if none comes. Siril answers them in milliseconds, and they are safe to repeat. Every other command waits `SIRIL_COMMAND_TIMEOUT_SECONDS` (300) and is never resent.
- A command that gets no answer is recorded in `last_run_diagnostics` under `siril_communication_error` ("Siril did not answer the command X within N s") and logged as an error. The stacking pipeline then stops that batch. It does not try the relaxed star detection, because no star detection was involved.

## The Siril calibration script

The `siril/stacking/` package writes one Siril script per run that builds the master calibration frames and applies them to the lights. It follows these rules.

### Script start and bit depth

Every script starts with `setext fits` and `set32bits` (`SIRIL_SCRIPT_PREAMBLE`). The second command makes Siril save 32-bit floating-point pixels.

**Why.** Without it, Siril uses the bit depth in the user's own preferences. A 16-bit preference rounds the normalized stack to 65536 levels. That loses faint signal and breaks the linear link between pixel value and light collected after `-norm=addscale` (Siril's normalization, which matches each frame's background level and spread before combining).

The command lasts for the whole Siril session, so it comes before every `convert` and `stack` line. The calibration run and the stacking run of spectral frames each start with it. (Spectral frames are aligned by phase correlation, which finds the shift between two images from their Fourier transforms; see the stacking README.) Siril 1.2 and later have the command.

### Bias and dark masters

Several bias frames are stacked with sigma rejection, which throws out pixel values far from the rest. The command `rej 3 3` sets the low and high limits to 3σ from the middle value (design estimate; see [stacking design estimates](../../../documentation/library_design/validation/stacking.md#design-estimates), STK-D02). A lone frame has nothing to compare with, so it becomes the master unchanged.

Several darks are combined with the median (`stack dark med -nonorm`), not with rejection.

**Why.** A cosmic-ray track in one dark must not reach the master. Sigma rejection left one such track (3,500 ADU at its peak, in 1 of 109 30 s darks) in the master at about 4 times the level around it, and that printed a dark line into every calibrated 30 s light. The median ignores any single frame. It costs noise: the median of many frames is about 1.25 times as noisy as their mean, which is small next to the read noise of each light (design estimate; see [STK-D76](../../../documentation/library_design/validation/stacking.md#design-estimates)). The bias master keeps rejection: a zero-length frame is hit by almost no cosmic rays, and changing it would also rebuild every master flat.

The cache of built masters (`MasterCache` in the Siril work folder) keys each master on its source frames and a recipe name. The dark recipe name (`DARK_MASTER_RECIPE`) changes with the combine method, so a master dark built the old way is never reused.

### Flat master

The bias master comes off every flat, including a lone one.

**Why.** A flat holds the bias level (the camera's offset) on top of the light. If the offset stays in, every pixel of the master flat is raised by the same amount, so the flat looks more uniform than the optics really are. The vignette (dimming toward the corners) then looks shallower than it is, and the lights come out under-corrected.

- Several flats go through `calibrate` (with the bias master when there is one) and `stack`.
- A lone flat goes through `calibrate_single`, because Siril's `convert` writes no sequence file for one frame. Without a bias master, a lone flat is used as it is.

### Flat check

Before Siril builds the master flat, `assess_staged_flats` measures the staged flats with the stacking pipeline's `assess_flats`, which the pipeline passes in as `PipelineSteps.assess_flats`. The check judges each flat's brightness against the clip ceiling in the camera profile of the lights' camera.

The check can also smooth the master flat.

**Why.** Dividing a light by the master flat copies the flat's own noise into every light, as the same pattern each time, so stacking does not average it away. When the master flat would be noisier than the limit, Siril blurs it with a light Gaussian. Vignetting and dust shadows are many pixels wide, so the blur keeps them and removes the pixel-to-pixel noise (see `pipelines/stacking/pre_processing/flat_calibration.py`).

Without `PipelineSteps.assess_flats`, the flats are neither checked nor smoothed.

### Lights

Every light is calibrated with the dark and flat masters, and with the bias master when there is no dark. A stack of one light uses `calibrate_single` with the same masters, so a lone light is never recorded as a calibrated stack without being calibrated.

### Matching the calibration frames to the lights

`CalibrationLibrary` files each frame under a setting key (see the next section) and picks frames for a batch of lights in `select_dark_frames`, `select_bias_frames` and `select_flat_frames`. `build_directories` calls them with the batch's settings:

- gain setting and offset;
- binning, read from the first readable light's `XBINNING` and `YBINNING`;
- exposure;
- mean `CCD-TEMP`.

Each call returns the frames and two lists of one-sentence flags. The driver copies both lists into `last_run_diagnostics`:

- `calibration_mismatch_flags` (soft). The frames are still applied. One sentence says either:
  - that no frame matched the lights' gain setting and offset, naming the gain setting and offset applied and the lights they were applied to; or
  - that no dark was within 3 C (`DEFAULT_DARK_TEMPERATURE_TOLERANCE_C`, partly measured; see [STK-06](../../../documentation/library_design/validation/stacking.md#stk-06)) of the lights, naming the lights' temperature, the master's temperature and the tolerance.
  - One case gives no flag: flats taken at another offset than the lights, at the same gain, when the library holds bias frames at the flats' own offset and the lights have bias frames (`select_flat_frames` checks this). Stacking calibrates those flats with their own bias, so the offset difference changes nothing. Without that bias the flat flag stays.
- `calibration_match_blocking_flags` (blocking). No frame of that kind is applied. One sentence says that the lights' binning has no calibration frames and names the binning the library holds. Frames binned differently have different pixels, so they cannot calibrate the lights.

The stacking pipeline reads both lists and fails its `calibration_metadata` gate (see the stacking README).

The driver also records the flat and bias files it chose and linked into the work folder (the ones that were readable). They go into `last_run_diagnostics` as lists of paths under `flat_frame_paths` and `bias_frame_paths` (`FLAT_FRAME_PATHS_KEY` and `BIAS_FRAME_PATHS_KEY`). Recording them where `select_flat_frames` and `select_bias_frames` pick them spares the quality step from guessing from the work folder. The stacking pipeline's flat-to-light match reads both lists (see the stacking README). A run that applied no flats or no biases leaves the key out.

A kind of calibration frame that the library does not hold at all raises neither flag, because both flags describe frames that do not match, and there is no frame to compare. The run records which kinds it applied in `last_run_diagnostics["calibration_applied"]`. When no kind was applied, the `calibration_metadata` gate reads `not_checked`.

### Frame counts

A master built from fewer frames than `minimum_calibration_frames` (default 3, in `[Processing.Siril]`) is still built, because the library can legitimately hold a single flat or dark. The run records one sentence per short master in `last_run_diagnostics["calibration_blocking_flags"]` and logs a warning.

The stacking pipeline reads that list and fails its `calibration_frame_count` gate (see the stacking README), which flags the stack. The flag tells the reader that the masters are weak; it does not mean the pixels are unusable.

## The setting key and picking darks

### The setting key

`CalibrationLibrary` files each frame under a setting key, built by `calibration_setting_key` and written `gain[@offset=N][@bin=AxB][@temp=T]`. The key holds:

- **Gain setting and offset.** A frame at one gain setting or offset cannot calibrate lights at another. A key with no `@offset` means offset 0.
- **Binning** (`XBINNING`, `YBINNING`). Binning merges neighboring pixels, so a 2x2 frame has different pixels from a 1x1 frame. Darks, bias frames and flats all key on it. A key with no `@bin` means 1x1, which is also what a header with no binning cards gets.
- **Temperature slot** (darks only). Dark current (the signal heat adds to each pixel) changes with temperature, so darks in different slots never stack into one master.
  - The dark's `CCD-TEMP` (or `SET-TEMP` when there is no `CCD-TEMP`) rounds to `round(T / tolerance) * tolerance`, with the tolerance `DEFAULT_DARK_TEMPERATURE_TOLERANCE_C` (3 C).
  - With the default tolerance, -10.2 C and -9.0 C share the -9 C slot, and +5 C is in the +6 C slot.
  - Bias and flat frames have no temperature slot.

Two settings are not in the key:

- **Exposure** (darks only). Darks are filed by key first, and then by exposure within each key. A dark matches the lights when their exposures differ by less than 0.1 s.
- **Readout mode** (a setting on some cameras that chooses between ways of reading out the sensor). The ZWO ASI533MM Pro frames in the sample data carry no readout-mode card, so there is nothing to read.

### Picking darks

The library narrows the darks in this order:

1. **Exposure.**
2. **Binning.** It must match exactly; otherwise the darks are blocked.
3. **Gain setting and offset.** Without a match, it uses the darks at the other settings, with a soft flag.
4. **Temperature.** The slot nearest the lights' temperature wins. The colder slot wins a tie, because a warmer dark over-subtracts.

After choosing a slot, the library checks it:

- It reads the `CCD-TEMP` of every dark in that slot, not only the first, and compares their mean with the lights' temperature. It flags the master when the two differ by more than the tolerance.
- It keeps each dark's header temperature in memory under the file's absolute path, modification time and size (at most 4096 files), so picking darks for many groups of lights opens each dark once. A changed file is read again; `clear_dark_temperature_cache` forgets them all.
- When the lights record no temperature and the darks span several slots, the slot with the most darks wins, with a soft flag.
- Darks with no temperature are used only when no dark has one.

### Libraries saved with shorter keys

A rescan (`refresh_dark_frames`, `refresh_bias_frames` and `refresh_flat_frames`) moves a frame filed under an older, shorter key to its new key. A library saved before binning and temperature were tracked therefore updates on its next rescan. Until then its frames count as 1x1.

Its darks are still not pooled across temperatures. When a dark's key has no temperature, the library reads that dark's own `CCD-TEMP` and places it in a slot before choosing. Without this rule, lights would receive a master pooled from darks of two temperatures, with no flag. The test `test_a_library_saved_before_temperatures_does_not_pool_them` covers a real case (partly measured; see [STK-06](../../../documentation/library_design/validation/stacking.md#stk-06)).

## Injecting drivers

Only a test replaces a built-in driver. `Astrometrics(...)` takes no driver arguments. A test builds the root with `astrometrics_with_drivers` (in `_internal/testing.py`) and passes a subclass of `PlateSolveDriver`, `StackingDriver`, `SimbadDriver`, `GaiaXpDriver` or `ReddeningDriver` (`plate_solve_driver=`, `stacking_driver=`, `simbad_driver=`, `gaia_xp_driver=`, `reddening_driver=`). The root wraps them in a `Drivers` and hands that to the sub-APIs, which pass it to the pipelines. See `api/README.md` for which calls use which driver.

To add a pipeline step that needs a driver:

1. Take a `drivers: Drivers | None = None` keyword.
2. Ask `(drivers or Drivers())` for the driver, for example `.simbad_or_default()`.
3. Do not import or call a built-in driver class in `pipelines/`. `test/test_driver_construction.py` fails if a pipeline module does.

A built-in driver class is not public. It lives in `astrometricslib._internal.drivers`, which only `astrometricslib` may import, and code inside the library goes through `Drivers`.

For exact behavior, read the code.
