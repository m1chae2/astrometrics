# Astrometrics Library Implementation Overview

*Version 1.2 · 2026-10-14 · Status: current*

While the theoretical algorithms and data flow are covered in [Astrometrics_Library_Architecture.md](./Astrometrics_Library_Architecture.md), this map serves as a direct index to the Python source code where those algorithms are physically implemented.

Terms are defined in the [glossary](./Astrometrics_Library_Architecture.md#appendix-b-glossary). Astronomy terms are also explained briefly where they first appear here.

Due to the internal nature of these modules, they are deliberately hidden from the public API Reference: they aren't meant to be imported directly. The one supported entry point is the package root: the names listed in `astrometricslib.__all__`, imported as `from astrometricslib import Astrometrics` (and the other names there). The root re-exports the facade from `api/` together with the driver interfaces, the job framework, the error types and a few constants.

Developers wishing to review or modify the core algorithms should refer to the following directories within `astrometricslib/`. The layout is layered, bottom to top:

- `models/`, `utilities/`, `foundation/`: the shared vocabulary. Data shapes, small generic helpers, configuration, logging, the job framework and the generic storage layer. It imports no layer above it.
- `drivers/`: wraps every external tool and service plus foundational, pipeline-agnostic primitives: FITS file access, the shared image container, the SQLite-backed target, star, catalog-cache, calibration and provenance stores (provenance: the record of which run made each data product), and the camera profiles. The external tools and services are:
  - Siril, an open-source program that calibrates, aligns and stacks images.
  - Astrometry.net, a plate solver: it finds where an image points on the sky by matching its star patterns to an index.
  - SIMBAD, an online database of named astronomical objects.
  - The Gaia catalog, a space telescope's catalog of precise star positions and motions. Gaia XP spectra are its low-resolution spectra, and Gaia reddening values say how much interstellar dust has reddened each star.
- `pipelines/shared/` and `pipelines/pipeline_base.py`: code every stellar or moving-object pipeline reuses: frame scanning and grouping, target-record CRUD (create, read, update and delete), image format conversion, frame-quality/statistics primitives, provenance and camera-profile recording, and the common pipeline contract. It imports no specific pipeline.
- `pipelines/<name>/`: the five pipelines themselves (stacking, astrometry, photometry, spectroscopy, asteroid detection). One pipeline may import another (sideways), but none imports the runners above it.
- `pipelines/tasks.py` and `pipelines/target_batch.py`: the dispatcher that ties the pipelines together and the many-target batch runner.
- `api/` and the package root (`astrometricslib/__init__.py`): the public gate everything above calls through.

`visualization/` (the plotting helpers) sits outside this ladder; see the Implementation Matrix below.

## Implementation Matrix

The library is organized into six layers across five specialized scientific pipelines and a shared orchestration spine.

The "Ratchet Rule" forbids a layer from importing from a layer above it. (A ratchet, in this repository, is a test whose list of allowed exceptions may only shrink, so the code can only move toward the rule.) This keeps the dependency graph one-directional: a lower layer never has to know a higher layer exists, so it can be tested, reused, or replaced without touching anything above it.

When a lower layer needs work that belongs to a higher one, the higher layer passes it in. For example, the stacking pipeline hands its flat check and spectral frame alignment to the stacking driver as a `PipelineSteps` (`drivers/interfaces/stacking_driver.py`).

A test enforces the rule (`astrometricslib/test/test_layer_boundaries.py`, `test_no_new_upward_imports`). It reads every import in the library's production files, including imports inside function bodies, and fails on a new upward one. Its exact rules are:

| Files | May not import |
| :--- | :--- |
| `models/`, `foundation/`, `utilities/` | `drivers`, `pipelines`, `api`, `visualization`, the package root |
| `drivers/` | `pipelines`, `api`, `visualization`, the package root |
| `pipelines/shared/`, `pipelines/pipeline_base.py` | any specific pipeline, `pipelines.tasks`, `pipelines.target_batch`, `api`, the package root |
| `pipelines/<name>/` | `pipelines.tasks`, `pipelines.target_batch`, `api`, the package root |
| `pipelines/tasks.py`, `pipelines/target_batch.py` | `api`, the package root |

What the ratchet does not check:

- `visualization/` has no rule of its own. Drivers and the vocabulary may not import it, but nothing limits what it imports, and the pipelines and the API may import it.
- `api/` and the package root are the top, so nothing limits them.
- `scripts/` has no layer rule. The only rule there is an import-linter contract that one script never imports another.
- Test modules are skipped.
- Imports inside one layer (for example, one vocabulary module importing another, or one pipeline importing another) are allowed.

The table below maps the layers to the pipelines. Its columns are the five pipelines plus a column for code shared across all of them; rows are the six layers, numbered 1 (public-facing) down to 6 (the shared vocabulary). Rows 1 to 6 match the six bullets above, in reverse order. Each cell lists key modules that pipeline uses at that layer, with module paths relative to `astrometricslib/`. The note under each layer number says what it calls into.

| Layer | Stacking | Astrometry | Photometry | Spectroscopy | Asteroid Detection | Shared by All |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Public API**<br>*(calls L2 to L5)* | `ProcessingPipelines.stack` | `ProcessingPipelines.process_target`<br>`stages=["astrometry"]` | `ProcessingPipelines.process_target`<br>`stages=["photometry"]` | `ProcessingPipelines.process_target`<br>`stages=["spectroscopy"]` | `ProcessingPipelines.process_target`<br>`stages=["asteroids"]` | `Astrometrics` facade<br>`api/processing.py`<br>`astrometricslib/__init__.py` |
| **2. Pipeline Runners**<br>*(calls L3)* | `pipelines.tasks.stack_frames_with_timeout`<br>*(batch run only; `stack` goes straight to L3)* | `pipelines.tasks.run_target_stages`<br>`pipelines.tasks.analyze_target` | `pipelines.tasks.run_target_stages`<br>`pipelines.tasks.analyze_target` | `pipelines.tasks.run_target_stages`<br>`pipelines.tasks.analyze_target` | `pipelines.tasks.run_target_stages`<br>`pipelines.tasks.analyze_target` | `pipelines.tasks`<br>`pipelines.target_batch` |
| **3. Pipelines**<br>*(calls L4, L5, L6; sideways allowed)* | `pipelines.stacking.stack_request`<br>`pipelines.stacking.stage`<br>`pipelines.stacking.stack_runner` | `pipelines.astrometry.runner`<br>`pipelines.astrometry.processing.star_identifier`<br>`pipelines.astrometry.processing.wcs_refinement` | `pipelines.photometry.runner`<br>`pipelines.photometry.batch`<br>`pipelines.photometry.processing.variability_analyzer`<br>`pipelines.photometry.processing.comparison_ensemble` | `pipelines.spectroscopy.runner`<br>`pipelines.spectroscopy.pipeline`<br>`pipelines.spectroscopy.pre_processing.spectrum_extractor`<br>`pipelines.spectroscopy.pre_processing.spectral_star_registration` | `pipelines.asteroid_detection.runner`<br>`pipelines.asteroid_detection.detection`<br>`pipelines.asteroid_detection.ephemeris` | *(one pipeline may import another)* |
| **4. Shared Pipeline Code**<br>*(calls L5, L6)* | `pipelines.shared.frame_grouping`<br>`pipelines.shared.quality.saturation` | `pipelines.shared.session_identification`<br>`pipelines.shared.star_recording` | `pipelines.shared.target_sessions`<br>`pipelines.shared.star_recording` | `pipelines.shared.catalog_star_identity`<br>`pipelines.shared.target_sessions` | `pipelines.shared.angles` | `pipelines.pipeline_base`<br>`pipelines.shared.provenance_recording`<br>`pipelines.shared.applied_camera_profile` |
| **5. Drivers**<br>*(calls L6)* | `drivers.siril_stacking_driver`<br>`drivers.siril_interface`<br>`drivers.calibration_library` | `drivers.astrometry_net_driver`<br>`drivers.gaia_cone_driver`<br>`drivers.catalog_store` | `drivers.catalog_access` | `drivers.interfaces` (Gaia XP and reddening)<br>`drivers.catalog_access` | `drivers.fits_access` | `drivers.fits_access`<br>`drivers.image`<br>`drivers.camera_profile_store`<br>`drivers.driver_set` |
| **6. Shared Vocabulary**<br>*(imports no layer)* | `models.stacking_quality` | `models.astrometry_quality` | `models.photometry_quality` | `models.spectroscopy_quality` | `models.moving_object`<br>`models.moving_object_config` | `models/`<br>`foundation/`<br>`utilities/` |
| **Outside** | Siril (headless) | Astrometry.net<br>Gaia (VizieR or ESA), SIMBAD | *(via astrometry)* | Gaia XP and Gaia DR3 reddening | IMCCE SkyBoT, reached through astroquery from `ephemeris.py`, not through a driver | FITS on disk<br>SQLite |

:::{note}
**Shared Vocabulary:** `models/`, `foundation/` and `utilities/` hold data shapes, settings, errors, logging, the job framework and small generic helpers. They may be imported by any module in the library. They are not all free of I/O: `foundation/config.py` reads the configuration file, `foundation/storage/` and `foundation/jobs/` read and write SQLite, and `models/moving_object_config.py` can load its settings from the configuration. The rule they follow is the layer rule only: they import nothing from `drivers/`, `pipelines/`, `api/` or `visualization/`.
:::

:::{note}
**One entry point for four columns:** `ProcessingPipelines.process_target` (`api/processing.py`) is the single Layer 1 entry point for the Astrometry, Photometry, Spectroscopy and Asteroid Detection columns. For one target it runs the chosen stages in a fixed order, astrometry, photometry, spectroscopy, asteroids (the first three by default), through `pipelines.tasks.run_target_stages`. Photometry's result is passed into the spectroscopy stage. Each stage takes its own options dictionary, and a caller that wants one stage passes only that stage. For a list of targets, `process_target` instead runs the full stack-solve-photometry-spectroscopy sequence for each target in its own worker process (`pipelines.target_batch.process_targets_in_parallel`).
:::

## Other Ratchets and Import Contracts

`astrometricslib/test/test_layer_boundaries.py` holds three more ratchets. Each keeps an allowlist that may only shrink; a stale entry fails the test too.

- **Raw FITS access** (`KNOWN_FITS_ACCESS_SITES`): a new production file that calls `fits.open`, `getheader`, `getdata`, `writeto`, `getval` or `setval` fails the test. New code reads FITS files through `drivers/fits_access.py` or `drivers/image.py`, which apply the HDU0/HDU1 rule. A FITS file (the standard astronomy image format) holds one or more HDUs (header-data units: a block of named header entries plus its data). The pixel data may sit in the primary HDU or in the next one.
- **Wall clock** (`KNOWN_WALL_CLOCK_SITES`): no file under `pipelines/` may call `datetime.now()` or `datetime.utcnow()`. A frame's time comes from its own header. The one listed exception dates the quarantine move log.
- **Unseeded randomness** (`KNOWN_UNSEEDED_RANDOM_SITES`, empty): no file under `pipelines/` may draw random numbers without a fixed seed (a bare `default_rng()`, a legacy `numpy.random` function, or the standard `random` module).

The `[tool.importlinter]` contracts in `pyproject.toml` check the boundaries between packages, which the ratchet above does not see:

- `astrometricslib` never imports `wayfindinglib`, `backend` or `mcp_servers`.
- `wayfindinglib`, the backend's own code and the MCP servers use only the public API of `astrometricslib` (the names imported from the package root), never a subpackage.
- One library script never imports another script.
- Further contracts keep the backend, the MCP servers and `wayfindinglib`'s own layers apart; see `pyproject.toml`.

## Core Processing Pipelines

### Stacking
*Located in:* `astrometricslib/pipelines/stacking/`
- **Public entry and frame choice:** `stack_request.py` (`choose_frames_to_stack` picks the light frames, one camera and one kind at a time; `run_stack` runs the stacking stage as a tracked job inside a stacking slot)
- **The stacking stage:** `stage.py` (`stack_frames`: the pre-checks, the run, the quality summary and its gates, and the provenance record)
- **Running the engine:** `stack_runner.py` (spectral registration fallback, stacking one exposure length at a time, and handing `PIPELINE_STEPS` to the `StackingDriver`)
- **Dispatch for the batch run:** `astrometricslib/pipelines/tasks.py` (`stack_frames_with_timeout` runs the stacking stage as a tracked job with a hard timeout; `analyze_target` picks which analysis pipeline runs by name; `run_full_pipeline` runs a target's full stack-solve-photometry-spectroscopy sequence) and `astrometricslib/pipelines/target_batch.py` (runs many targets through that sequence in parallel)
- **Calibration ingest:** `calibration_ingest.py` (the report of what a calibration-library rescan found), on top of `astrometricslib/drivers/calibration_library.py`
- **Sub-exposure screening** (sub-exposure: one raw frame of a stack): `pre_processing/frame_quarantine.py` (moves clouded or trailed frames to `_excluded`, and restores them), `pre_processing/frame_homogeneity.py` (gain), `pre_processing/background_homogeneity.py` (sky-level splits), `pre_processing/camera_selection.py` (one camera per stack) and `pre_processing/flat_calibration.py` (flat level and noise, and smoothing of a noisy master flat)
- **Other inputs:** `pre_processing/exposure_weighting.py` (weights for mixed exposure lengths) and `pre_processing/stack_inputs.py` (skips a restack whose inputs did not change)
- **Pixel rejection and integration:** `astrometricslib/utilities/rejection_thresholds.py` (the floored Chauvenet cutoffs: how many standard deviations from the median a pixel may sit before the stack throws it out) and `stage.py`
- **Frame-count-adaptive filtering:** `astrometricslib/utilities/stack_filter_floor.py` (loosens Siril's sharpness filter so enough frames survive)
- **Exposure groups:** `processing/exposure_groups.py`, `processing/group_alignment.py`, `processing/group_derotation.py`, `processing/star_pattern_candidates.py` and `processing/spectral_frame_alignment.py`
- **Quality checks after the stack:** `pre_processing/assess_input_quality.py` (flat and calibration gates), `post_processing/assess_output_quality.py` (the six output gates), `post_processing/stack_quality.py` (the limits behind them), `post_processing/exposure_group_report.py` (the three exposure-group gates) and `post_processing/stack_quality_report.py` (the stack reports the API returns)
- **Previews** (screen pictures stretched from the stack's linear data, which looks almost black as stored): `post_processing/stack_preview.py`, `post_processing/colour_calibration.py`, `post_processing/starnet_stretch.py`, `post_processing/sky_level.py`, `post_processing/star_tone.py`, `post_processing/bright_object.py`, `post_processing/stack_crop.py` and `post_processing/preview_remake.py`
- **Restacks:** `post_processing/previous_stack.py`, `post_processing/stack_comparison.py` and `post_processing/interrupted_jobs.py`

### Astrometry
*Located in:* `astrometricslib/pipelines/astrometry/`
- **Plate solving** (finding the WCS, or World Coordinate System: the FITS header entries that convert a pixel position into a sky position): `astrometricslib/drivers/astrometry_net_driver.py`
- **Star detection and centroiding** (centroid: a star's brightness-weighted center): `processing/star_identifier.py` and `pre_processing/source_detection.py`
- **Star width** (FWHM, the full width of a star's image at half its peak brightness): `pre_processing/fwhm.py`
- **Refitting the solution to Gaia:** `processing/wcs_refinement.py`, called from `processing/star_identifier.py`
- **Pipeline orchestration:** `pipeline.py`; the `AnalysisPipeline` adapter is `runner.py`
- **Writing the WCS into the FITS header:** `astrometricslib/pipelines/shared/session_identification.py` (`write_wcs_to_fits_header`), called from `runner.py`
- **Run gates:** `post_processing/run_gates.py` and `post_processing/assess_match_quality.py`
- **Gaia catalog:** `astrometricslib/drivers/gaia_cone_driver.py` (cone downloads) and `astrometricslib/drivers/catalog_store.py` (the local cache); `utilities/catalog_seeding.py` fills the cache

### Photometry
*Located in:* `astrometricslib/pipelines/photometry/`
- **Adapter and session handling:** `runner.py` (the `AnalysisPipeline` adapter) and `batch.py` (one run per observing session, cross-session matching and merging, and the period search)
- **Aperture photometry** (measuring a star's brightness by summing the pixels in a circle on it and subtracting the sky): `pre_processing/frame_photometry.py`, with `pre_processing/detector_noise.py` (gain, the electrons per stored pixel step, and read noise, the noise added each time the sensor is read) and `pre_processing/observation_times.py` (BJD_TDB times: the time the light would reach the solar system's center of mass, which removes the effect of Earth's orbit)
- **Comparison stars and light curves** (a light curve is a star's brightness over time; the comparison ensemble is a group of steady stars whose combined signal cancels changes in the sky): `processing/comparison_ensemble.py` and `processing/variability_analyzer.py`
- **Variability indices:** `processing/variability_indices.py`
- **Period search:** `processing/periodicity_search.py` (Lomb-Scargle and box search, with false-alarm probabilities), `processing/period_checks.py` (hold-out and alias checks) and `processing/family_wise_correction.py` (correction for the number of searches)
  - Lomb-Scargle fits sine waves of many periods to unevenly spaced data. The box search fits a repeating flat-bottomed dip, the shape of an eclipse.
  - A false-alarm probability is the chance that noise alone gives a result at least as strong.
  - The hold-out check leaves one night out and tests whether the period predicts it. The alias check looks for false echo periods caused by observing once a night.
- **Quality checks:** `pre_processing/assess_input_quality.py`, `post_processing/run_gates.py`, `post_processing/variability_skill.py`, `post_processing/known_variability_labels.py` and `post_processing/assess_output_quality.py`

### Spectroscopy
*Located in:* `astrometricslib/pipelines/spectroscopy/`
- **Orchestration:** `pipeline.py` (`SpectroscopyPipeline`; `_apply_result_to_stellar_object` sets the order of the calibration chain), `runner.py` (the `AnalysisPipeline` adapter) and `record_and_flag_spectroscopy_stars.py`
- **Per-frame and per-session batch processing:** `frame_analysis.py` and `batch.py`
- **Star identification:** `pre_processing/spectral_star_registration.py` (copies star names from a normal image when the plate solution cannot)
- **Spectrum extraction** (turning a star's trail, the streak its spread-out light makes on the image, into brightness at each step): `pre_processing/spectrum_extractor.py` and `pre_processing/_extractor_c.c`, with `pre_processing/neighbor_trail_deblending.py` and `pre_processing/neighbor_wing_correction.py` for crowded fields
- **Wavelength calibration:** `pre_processing/spectrum_calibrator.py`, `pre_processing/optics_physics.py` and `utilities/calibration_tuner.py`
- **Wavelength corrections:** `pre_processing/differential_refraction.py` and `pre_processing/atmospheric_refraction.py` (refraction: the air bends blue light more than red), `pre_processing/wavelength_zero_point.py` (zero point: the offset of the whole wavelength scale)
- **Flux calibration:** `pre_processing/quantum_efficiency_correction.py` (quantum efficiency: the share of light the sensor records at each wavelength), `pre_processing/instrument_response.py` (the colour tilt of the whole instrument, measured on a star of known spectrum) and `pre_processing/atmospheric_extinction.py` (dimming by the air, which grows with airmass, the amount of atmosphere the light crossed)
- **Resolution** (line spread: how wide the instrument smears a single wavelength): `pre_processing/measured_line_spread.py` and `pre_processing/spectral_resolution.py`
- **Classification and features:** `processing/spectral_classifier.py`, `processing/spectrum_analysis.py`, `processing/star_reddening.py` with `processing/interstellar_extinction.py` (dereddening: removing the reddening that interstellar dust adds to a star's light), and the feature and line modules in `processing/`
- **Checks after extraction:** `post_processing/run_gates.py`, `post_processing/compare_to_gaia_xp.py`, `post_processing/compare_to_catalog.py` and `post_processing/wavelength_scale.py`
- **Registration quality of spectral stacks** (registration: aligning frames so each star falls on the same pixel): `utilities/registration_quality.py`, called by the stacking stage and by the processing API

### Asteroid Detection (Moving Objects)
*Located in:* `astrometricslib/pipelines/asteroid_detection/`
- **Blink analysis and tracking** (comparing frames taken at different times to find dots that move): `detection.py` and `pipeline.py`; the `AnalysisPipeline` adapter is `runner.py`
- **Per-frame WCS from the mount's pointing:** `frame_wcs_composer.py`
- **Known-object matching against IMCCE SkyBoT** (an online service that lists the known asteroids and comets in a patch of sky at a given time): `ephemeris.py`
- **Run gates:** `run_gates.py`
- **Not yet called by any pipeline step:** `mpc_report.py` writes MPC 80-column and ADES text for a track (the two report formats of the Minor Planet Center, which collects asteroid and comet positions), and `rotation_period.py` runs the photometry period search on a track's brightness. Both are tested, but no run uses them yet.

### Shared Pipeline Code
*Located in:* `astrometricslib/pipelines/shared/`, unless noted otherwise.

This is where the plain, non-analysis reads and writes the public API calls directly (scanning a folder for FITS files, target CRUD, converting a FITS file to a PNG) live alongside the frame-quality primitives every stellar pipeline shares.
- **Frame selection and grouping by camera/optic:** `frame_grouping.py` and `quality/frame_selection.py`
- **Filesystem frame scanning:** `frame_scanning.py`
- **Target catalog CRUD:** `target_records.py`
- **Image format conversion for display:** `image_conversions.py`
- **Image scaling math shared by `image_conversions.py` and the visualization overlay:** `image_scaling.py`
- **Star recording shared by all three stellar pipelines:** `star_recording.py`
- **Rules for when two catalog names are one star (position tolerance, same-catalog check, HD/BD/Gaia name preference), shared by `star_recording.py` and `scripts/merge_duplicate_catalog_stars.py`:** `catalog_star_identity.py`
- **Observing-session grouping:** `target_sessions.py`
- **Plate solving a session's frame, reusing a WCS already in the file, and writing a WCS to a FITS header:** `session_identification.py`
- **Per-image analysis state:** `analysis_context.py`
- **Provenance and camera-profile recording on each run's quality summary:** `provenance_recording.py` and `applied_camera_profile.py`
- **Saturation checks and image/hardware quality telemetry:** `quality/saturation.py` and `quality/quality_metrics.py`
- **Frame statistics and background level measurement:** `quality/frame_statistics.py` and `quality/background_measurement.py`
- **Per-frame cloud and trail measurements:** `quality/raw_frame_check.py`

### Pixel-Level & FITS Primitives
*Located in:* `astrometricslib/drivers/`
- **FITS HDU0/HDU1 access:** `fits_access.py`
- **The `AstrometricsImage` data container:** `image.py`
- **Filter-type detection from FITS headers:** `filter_detection.py`
- **Reading frames ahead into a network drive's local cache (optional):** `frame_prefetch.py`

### Drivers
*Located in:* `astrometricslib/drivers/`. Wraps every external tool the pipelines depend on, plus the database access layer everything above it reaches through.
- **Driver interfaces:** `interfaces/` holds one abstract base class per outside program or service: `StackingDriver`, `PlateSolveDriver`, `SimbadDriver`, `GaiaXpDriver` and `ReddeningDriver`. It also holds the plain data types that cross those interfaces: `StackSettings`, `StackRunResult`, `PipelineSteps` (the stacking pipeline's own steps, handed to the stacking driver), `PlateSolveHeader` and `ReddeningEstimate`. The pipelines depend on these, and each concrete driver below implements one.
- **Choosing the drivers for a run:** `driver_set.py` (`Drivers`, which builds the built-in driver for any slot the caller left empty)
- **Siril stacking/registration:** `siril_stacking_driver.py` (`SirilStackingDriver`), on top of the Siril session class `ImageProcessing` in `siril_interface.py` and the log parsing in `siril_output_parsing.py`
- **Astrometry.net plate solving:** `astrometry_net_driver.py` (`AstrometryNetPlateSolveDriver`)
- **Gaia DR3 cone downloads (VizieR or the ESA archive):** `gaia_cone_driver.py`
- **Local Gaia catalog cache:** `catalog_store.py`
- **Gaia DR3 XP spectra, cached on disk:** `gaia_xp_driver.py` (`AstroqueryGaiaXpDriver`)
- **Gaia DR3 interstellar reddening:** `astroquery_gaia_reddening_driver.py` (`AstroqueryGaiaReddeningDriver`)
- **SIMBAD star lookups:** `astroquery_simbad_driver.py` (`AstroquerySimbadDriver`)
- **Calibration frame library (darks/bias/flats):** `calibration_library.py`
- **Camera profiles, read from the configuration file:** `camera_profile_store.py`
- **IVOA provenance records in `astrometrics_log.db`** (records in the International Virtual Observatory Alliance's data-lineage format): `provenance_store.py` (`ProvenanceStore`)
- **Target reads and writes, and database backups, for `astrometrics.db`:** `local_database.py`
- **Target/stellar-catalog repository (`CatalogAccess`), the front door every other layer reaches for the database through:** `catalog_access.py`

`catalog_access.py` doesn't execute SQL itself. Most of its reads and writes go through a generic, keyed-record SQLite store (the Butler below) that it shares with wayfindinglib.

Two paths do not: reading the target catalog goes through `local_database.load_targets`, which owns the targets table's read path, and raw frames and stacked images are resolved to file paths on disk rather than stored in the database.

### Shared Storage Backend
*Located in:* `astrometricslib/foundation/storage/`
- **Generic keyed-model storage (get/put/exists/merge, one table per dataset type):** `butler.py`
- **SQLite connection setup and JSON encoding:** `local_database.py`
- **Cross-process file locking for shared hardware/storage resources:** `process_locks.py`
- **Refusing to write under a frames mount point when no drive is mounted there:** `mount.py`

### Job Framework
*Located in:* `astrometricslib/foundation/jobs/`. Shared by both libraries, the backend and the MCP servers.
- **Job list and job log lines in `astrometrics_log.db` (`JobStore`, `DbLogHandler`):** `store.py`
- **Recording a job and collecting its log through the job log router (`registered_job`, `capture_job_logs`, `background_job`, `run_as_background_job`, `close_interrupted_jobs`):** `runner.py`
- **Job records (`ProcessingJob`):** `models.py`
- **Telling whether the program that owns a job still runs:** `process_identity.py`

## Empirical Validation Campaign — Implementation Notes

[Astrometrics_Library_Architecture.md](./Astrometrics_Library_Architecture.md)'s Empirical Validation section (§8) describes an 8-session validation campaign against real telescope data, in plain terms. It also covers the bugs that campaign surfaced. This section maps those findings back to the actual code, for developers who need to trace a finding to its source.

- **Validation scripts:** The sessions are driven through the public API by `astrometricslib/scripts/run_empirical_validation.py`.
- **Finding 6 (cross-session photometry tracking bug):** `analyze_target(pipeline_type="photometry")` was running `VariabilityAnalyzer` across a target's entire frame history in one pass. It used a single reference frame from whichever session came first. The fix scopes each `VariabilityAnalyzer` run to one `TargetSession` at a time.
- **Finding 7 (cross-session star identity matching):**
  - The matching itself is implemented per Architecture §5.2 (concept 6), in `pipelines/photometry/batch.py` (`_match_and_merge_across_sessions`).
  - An unconditional per-star SIMBAD query was being triggered by routing through the shared `AstrometryPipeline` entry point, the same one "astrometry"/"spectroscopy" use, when only the already-solved WCS was actually needed. Photometry now builds a `StarIdentifier()` directly and passes it to `resolve_frame_wcs` (`pipelines/shared/session_identification.py`), which reuses a WCS already in the file and solves only when there is none.
  - An initial pairwise `SkyCoord.separation()` loop (comparing every star to every other star) did not scale past a few hundred stars per session. It was replaced with a KD-tree-backed `search_around_sky` call. A KD-tree is a search structure that splits space into boxes, so finding the near neighbors of a point does not require checking every other point.
  - Repeatability was verified by checking that two consecutive runs produced a byte-for-byte identical `stellar_catalog` row set.
  - One session was excluded from matching: a light frame in it actually referenced `M 13/M_13_Stacked.fits`, a different target's stack, due to a pre-existing library data-labeling error. This was correctly isolated via `sessions_missing_wcs`, without affecting the other 7 sessions.
- **Finding 5 (star/track linkage speedup):** An RA/Dec (Right Ascension and Declination, the sky's longitude and latitude) bounding-box pre-filter, applied before computing angular separations (`MovingObjectDetector._chain_detections_by_persistence` in `pipelines/asteroid_detection/detection.py`), skips the exact distance calculation for almost every pair. The search is still $O(N \times M)$ (its work grows with the product of the two list sizes): the box test itself looks at every pair, but as one vectorized NumPy comparison instead of a Python loop. Tested on M 81's ~150,000 detected sources across 46 frames.

## Batch Processing & Maintenance Scripts

*Located in:* `astrometricslib/scripts/`
These top-level scripts orchestrate the pipeline across multiple targets and manage execution environments.
- **Batch execution:** `run_all_target_processing.py`
- **Empirical validation:** `run_empirical_validation.py`
- **Catalog seeding:** `seed_local_star_catalog.py`
- **Concurrency benchmarking:** `benchmark_siril_concurrency.py`
- **Data backfilling:** `backfill_focal_length.py`
