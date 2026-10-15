# Astrometrics Library Implementation Overview

*Version 1.5 · 2026-10-15 · Status: current*

While the theoretical algorithms and data flow are covered in [Astrometrics_Library_Architecture.md](./Astrometrics_Library_Architecture.md), this map serves as a direct index to the Python source code where those algorithms are physically implemented.

Terms are defined in the [glossary](./Astrometrics_Library_Architecture.md#appendix-a-glossary). Astronomy terms are also explained briefly where they first appear here.

Due to the internal nature of these modules, they are deliberately hidden from the public API Reference: they aren't meant to be imported directly. The one supported entry point is the package root: the names listed in `astrometricslib.__all__`, imported as `from astrometricslib import Astrometrics` (and the other names there).

The root re-exports (imports and passes on under its own name) four kinds of things:

- The facade from `api/`: the `Astrometrics` object, a single front door that hides the many modules behind it.
- The driver interfaces: the abstract classes each external program or service must implement (see Drivers below).
- The job framework: a job is one recorded unit of work, such as a stack or an analysis run, with a status and a log that can be watched while it runs and read afterwards (see Job Framework below).
- The error types and a few constants.

Three other parts of the repository use the library, and are named below:

- `wayfindinglib`, the companion library for telescope control (mount, focuser, guiding, and observation planning).
- The backend, the local server program that the desktop application talks to.
- The MCP servers, adapters that let an AI agent call the libraries' tools through the Model Context Protocol.

The architecture paper states no tuned values. The settings each pipeline uses, with their status and the evidence behind them, are listed here in [Tuned Parameters and Validation Status](#tuned-parameters-and-validation-status).

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

The layer rule forbids a layer from importing from a layer above it. A ratchet test enforces it: a test whose list of known exceptions may only shrink, so the code can only move toward the rule, never away from it. (The pair is called the "Ratchet Rule".) The rule keeps the dependency graph one-directional: a lower layer never has to know a higher layer exists, so it can be tested, reused, or replaced without touching anything above it.

When a lower layer needs work that belongs to a higher one, the higher layer passes it in. For example, the stacking pipeline hands its flat check and spectral frame alignment to the stacking driver as a `PipelineSteps` (`drivers/interfaces/stacking_driver.py`).

A test enforces the rule (`astrometricslib/test/test_layer_boundaries.py`, `test_no_new_upward_imports`). It reads every import in the library's production files, including imports inside function bodies, and fails on a new upward one. Its exact rules are listed in Table 1.

**Table 1.** Layer rules enforced by the ratchet test.

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

Table 2 maps the layers to the pipelines. Its columns are the five pipelines plus a column for code shared across all of them; rows are the six layers, numbered 1 (public-facing) down to 6 (the shared vocabulary). Rows 1 to 6 match the six bullets above, in reverse order. Each cell lists key modules that pipeline uses at that layer, with module paths relative to `astrometricslib/`. The note under each layer number says what it calls into.

**Table 2.** Implementation matrix: layers against pipelines.

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
**One entry point for four columns:** `ProcessingPipelines.process_target` (`api/processing.py`) is the single Layer 1 entry point for the Astrometry, Photometry, Spectroscopy and Asteroid Detection columns. For one target it runs the chosen stages in a fixed order, astrometry, photometry, spectroscopy, asteroids (the first three by default), through `pipelines.tasks.run_target_stages`. Photometry's result is handed to the spectroscopy stage as one of its options, but the spectroscopy stage does not read it today. Each stage takes its own options dictionary, and a caller that wants one stage passes only that stage. For a list of targets, `process_target` instead runs the full stack-solve-photometry-spectroscopy sequence for each target in its own worker process (`pipelines.target_batch.process_targets_in_parallel`).
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
- **Quality checks before and after the stack:** `pre_processing/assess_input_quality.py` (flat and calibration gates, checked before stacking), `post_processing/assess_output_quality.py` (the six output gates), `post_processing/stack_quality.py` (the limits behind them), `post_processing/exposure_group_report.py` (the three exposure-group gates) and `post_processing/stack_quality_report.py` (the stack reports the API returns)
- **Previews** (screen pictures stretched from the stack's linear data, which looks almost black as stored): `post_processing/stack_preview.py`, `post_processing/colour_calibration.py`, `post_processing/starnet_stretch.py`, `post_processing/sky_level.py`, `post_processing/star_tone.py`, `post_processing/bright_object.py`, `post_processing/stack_crop.py` and `post_processing/preview_remake.py`
- **Restacks:** `post_processing/previous_stack.py`, `post_processing/stack_comparison.py` and `post_processing/interrupted_jobs.py`

### Astrometry
*Located in:* `astrometricslib/pipelines/astrometry/`
- **Plate solving** (finding the WCS, or World Coordinate System: the FITS header entries that convert a pixel position into a sky position): `astrometricslib/drivers/astrometry_net_driver.py`
- **Star detection and centroiding** (centroid: a star's brightness-weighted center): `processing/star_identifier.py` and `pre_processing/source_detection.py`
- **Star width** (FWHM, the full width of a star's image at half its peak brightness): `pre_processing/fwhm.py`
- **Refitting the solution to Gaia:** `processing/wcs_refinement.py`, called from `processing/star_identifier.py`
- **Pipeline orchestration:** `pipeline.py`; the `AnalysisPipeline` adapter is `runner.py`. An adapter is a small class that wraps a pipeline's own algorithm class and reshapes its calls into the four steps every analysis pipeline shares (check the input, do the work, check the output, return a result), defined in `pipelines/pipeline_base.py`.
- **Writing the WCS into the FITS header:** `astrometricslib/pipelines/shared/session_identification.py` (`write_wcs_to_fits_header`), called from `runner.py`
- **Run gates:** `post_processing/run_gates.py` and `post_processing/assess_match_quality.py`
- **Gaia catalog:** `astrometricslib/drivers/gaia_cone_driver.py` (cone downloads) and `astrometricslib/drivers/catalog_store.py` (the local cache); `utilities/catalog_seeding.py` fills the cache

### Photometry
*Located in:* `astrometricslib/pipelines/photometry/`
- **Adapter and session handling:** `runner.py` (the `AnalysisPipeline` adapter) and `batch.py` (one run per observing session, cross-session matching and merging, and the period search)
- **Aperture photometry** (measuring a star's brightness by summing the pixels in a circle on it and subtracting the sky): `pre_processing/frame_photometry.py`, with `pre_processing/detector_noise.py` (gain in e-/ADU, the electrons per stored pixel step, and read noise, the noise added each time the sensor is read) and `pre_processing/observation_times.py` (BJD_TDB times: the time the light would reach the solar system's center of mass, which removes the effect of Earth's orbit)
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
- **Spectrum extraction** (turning a star's spectrum trail, the band its spread-out light makes on the image, into brightness at each step): `pre_processing/spectrum_extractor.py` and `pre_processing/_extractor_c.c`, with `pre_processing/neighbor_trail_deblending.py` and `pre_processing/neighbor_wing_correction.py` for crowded fields
- **Wavelength calibration:** `pre_processing/spectrum_calibrator.py`, `pre_processing/optics_physics.py` and `utilities/calibration_tuner.py`
- **Wavelength corrections:** `pre_processing/differential_refraction.py` and `pre_processing/atmospheric_refraction.py` (refraction: the air bends blue light more than red), `pre_processing/wavelength_zero_point.py` (wavelength zero point: the offset of the whole wavelength scale)
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

`catalog_access.py` doesn't execute SQL itself. Most of its reads and writes go through a generic, keyed-record SQLite store that it shares with wayfindinglib. The store is called the Butler, after the data butler of the Rubin Observatory software whose get/put/exists shape it copies (see Shared Storage Backend below).

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

## Validation — Implementation Notes

The evidence behind every tuned value is kept in the validation register, [validation/index.md](./validation/index.md), not here. Each entry names the library calls that were made, the script or test that repeats them, and the result on our data. This section maps a few behaviours that the first validation campaign ([validation/index.md](./validation/index.md#5-validation-sessions), Section 5) relies on to the code that implements them.

- **Validation scripts:** `astrometricslib/scripts/run_empirical_validation.py` runs the campaign's targets through the public API. Each register entry names the script, command line and test that automate it.
- **Per-session photometry tracking:** `analyze_target(pipeline_type="photometry")` runs `VariabilityAnalyzer` once per `TargetSession`, each with its own reference frame, because a star's pixel position is stable only within one session (see [PHO-14](./validation/photometry.md#pho-14)).
- **Cross-session star identity matching** (see [PHO-14](./validation/photometry.md#pho-14)):
  - The matching itself is implemented per Architecture §5.2 (concept 6), in `pipelines/photometry/batch.py` (`_match_and_merge_across_sessions`).
  - Photometry builds a `StarIdentifier()` directly and passes it to `resolve_frame_wcs` (`pipelines/shared/session_identification.py`), which reuses a WCS already in the file and solves only when there is none. It does not route through the shared `AstrometryPipeline` entry point, which would query SIMBAD once per star.
  - Neighbours are found with a KD-tree-backed `search_around_sky` call, not a pairwise `SkyCoord.separation()` loop. A KD-tree is a search structure that splits space into boxes, so finding the near neighbors of a point does not require checking every other point.
  - Repeatability is checked by comparing the `stellar_catalog` row set of two consecutive runs.
  - A session whose light frame references another target's stack is isolated via `sessions_missing_wcs` and left out, without affecting the other sessions.
- **Moving-object track linking:** an RA/Dec (Right Ascension and Declination, the sky's longitude and latitude) bounding-box pre-filter, applied before computing angular separations (`MovingObjectDetector._chain_detections_by_persistence` in `pipelines/asteroid_detection/detection.py`), skips the exact distance calculation for almost every pair. The search is still $O(N \times M)$ (its work grows with the product of the two list sizes): the box test itself looks at every pair, but as one vectorized NumPy comparison instead of a Python loop (see [MOV-07](./validation/asteroid_detection.md#mov-07)).

## Batch Processing & Maintenance Scripts

*Located in:* `astrometricslib/scripts/`
These top-level scripts orchestrate the pipeline across multiple targets and manage execution environments.
- **Batch execution:** `run_all_target_processing.py`
- **Empirical validation:** `run_empirical_validation.py` (the register entries say which checks it repeats)
- **Catalog seeding:** `seed_local_star_catalog.py`
- **Concurrency benchmarking:** `benchmark_siril_concurrency.py`
- **Data backfilling:** `backfill_focal_length.py`

## Tuned Parameters and Validation Status

`Astrometrics_Library_Architecture.md` explains each pipeline's method without its tuned values. This section lists those values: the thresholds, limits and defaults the pipelines use. Tables 3 to 7 give one table per pipeline. Each row names the setting, its default, its status label, and the section of the architecture paper that explains what the setting does.

Each value carries one of three status labels. *Measured* means a measurement on real data chose or confirmed the value. *Partly measured* means the measurement is incomplete, for example synthetic data only or a single target. *Design estimate* means the value is a reasoned choice that has not been measured. Each label links to the validation register ([validation/index.md](./validation/index.md)): to the entry that holds the evidence, or to the pipeline's table of design estimates, which says how the value could be measured. Some values are configuration settings and others are fixed in the code; the register entry says which. A hidden link after each table names the code that holds each value, and a test checks that each named object still exists.

### Stacking settings

**Table 3.** Tuned settings of the stacking pipeline and their validation status.

| Parameter | Default | Status | Architecture section |
|---|---|---|---|
| Floor on the high rejection cutoff | 2.5 $\sigma$ | Partly measured ([STK-08](validation/stacking.md#stk-08)); simulation only | §3.2 |
| Margin of the low cutoff above the high one | 0.5 $\sigma$ | Partly measured ([STK-08](validation/stacking.md#stk-08)); simulation only | §3.2 |
| Minimum frames per master calibration frame | 3 | Design estimate ([stacking table](validation/stacking.md#design-estimates)) | §3.2 |
| Frame screening: session gap | 4 h | Design estimate ([stacking table](validation/stacking.md#design-estimates)) | §3.4.1 |
| Frame screening: fewest frames to judge | 8 | Partly measured ([STK-01](validation/stacking.md#stk-01)) | §3.4.1 |
| Frame screening: star count for a clouded frame; roundness for a trailed frame (0 to 1 scale) | below 0.5 of the session median; below 0.80 and at least 0.15 under the session median | Partly measured ([STK-01](validation/stacking.md#stk-01)) | §3.4.1 |
| Frame screening: roundness drop for a colour camera | at least 0.20 under the session median | Measured ([STK-02](validation/stacking.md#stk-02)) | §3.4.1 |
| Largest share of a session moved aside | 25% | Partly measured ([STK-03](validation/stacking.md#stk-03)) | §3.4.1 |
| Dark temperature slot width | 3 °C | Partly measured ([STK-06](validation/stacking.md#stk-06)) | §3.2 |
| Minimum frames kept after quality filtering | 5 | Partly measured ([STK-10](validation/stacking.md#stk-10)) | §3.3 (Table 3, step 3) and §3.4.1 |
| Brightness scale: fewest shared stars before the pixel fallback | 16 | Measured ([STK-19](validation/stacking.md#stk-19)) | §3.2 |
| Brightness scale, pixel fallback: pixels between percentiles; bright-end fraction | 20th to 80th; 1% of pixels | Design estimate ([stacking table](validation/stacking.md#design-estimates)) | §3.2 |
| Linearity tolerance between exposure groups | 5% | Partly measured ([STK-20](validation/stacking.md#stk-20)) | §3.2 |
| Brightest sky allowed for an exposure group, relative to the reference | 3 times | Partly measured ([STK-21](validation/stacking.md#stk-21)) | §3.2 |
| Noise penalty of equal weights above which frames are noise-weighted | 1.05 | Design estimate ([stacking table](validation/stacking.md#design-estimates)); the benefit of weighting is in [STK-07](validation/stacking.md#stk-07) | §3.2 |
| Share of spectral frames that must register before the alignment fallback | 0.9 | Design estimate ([stacking table](validation/stacking.md#design-estimates)); registration counts are in [STK-12](validation/stacking.md#stk-12) | §3.3 |
| Rejected pixel share that flags a stack | 15% | Partly measured ([STK-24](validation/stacking.md#stk-24)) | §3.4.2 |
| Star sharpness warning ratio (fails above) | 1.2 | Partly measured ([STK-25](validation/stacking.md#stk-25)) | §3.4.2 |

<!-- impl: astrometricslib.utilities.rejection_thresholds.rejection_bounds -->
<!-- impl: astrometricslib.drivers.calibration_library.DEFAULT_DARK_TEMPERATURE_TOLERANCE_C -->
<!-- impl: astrometricslib.utilities.stack_filter_floor.DEFAULT_MINIMUM_SURVIVING_FRAMES -->
<!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.GAIN_MID_RANGE_PERCENTILES -->
<!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.GAIN_BRIGHTEST_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.DEFAULT_GAIN_DISAGREEMENT_TOLERANCE -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.DEFAULT_FWHM_DEGRADATION_RATIO -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.SESSION_GAP_SECONDS -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.MINIMUM_FRAMES_TO_JUDGE -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.CLOUD_STAR_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.TRAILED_ROUNDNESS_LIMIT -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.ROUNDNESS_DROP_BELOW_MEDIAN -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.MAXIMUM_QUARANTINED_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.exposure_weighting.MINIMUM_PENALTY_TO_WEIGHT -->
<!-- impl: astrometricslib.pipelines.stacking.stack_runner.MINIMUM_REGISTERED_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.COLOUR_CAMERA_ROUNDNESS_DROP_BELOW_MEDIAN -->
<!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.STAR_GAIN_MINIMUM_STARS -->
<!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.SKY_RATIO_LIMIT -->

### Astrometry settings

**Table 4.** Tuned settings of the astrometry pipeline and their validation status.

| Parameter | Default | Status | Architecture section |
|---|---|---|---|
| Star detection threshold; sharpness and roundness ranges | 5 $\sigma$; 0.2 to 1.0; $\pm$0.6 | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.2 (step 1) |
| Minimum stars to attempt a solve | 4 | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.4.1 |
| Minimum stars for a local solve | 10 | Partly measured ([AST-13](validation/astrometry.md#ast-13)) | §4.4.1 |
| Pixel-scale window; further widening by the solver | $\pm$5%; 20% | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.4.1 |
| Brightest stars sent to the online solver | 100 | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.2 (step 2) |
| Gaia refit: fewest matched stars | 40 | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.2 (step 6) |
| Gaia refit: largest allowed shift of the field | 30 arcsec | Partly measured ([AST-02](validation/astrometry.md#ast-02)) | §4.2 (step 6) |
| Gaia refit: first and later match radius; outlier clip; most passes; distortion order | 4 px and 1.5 px; 3 $\sigma$; 5; second-order SIP | Partly measured ([AST-02](validation/astrometry.md#ast-02)); used, not varied | §4.2 (step 6) |
| Gaia query time limits; depth steps on a time-out | 45 s and 60 s for the two services; 2 steps of 1 mag | Partly measured ([AST-08](validation/astrometry.md#ast-08)) | §4.2 (step 4) |
| Gaia search depth: margin; limits; default | 1.0 mag; G 14 to 20; G 18 | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.2 (step 4) |
| Catalog match radius | 10 arcsec | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.2 (step 4) |
| Same-star position tolerance between catalogs | 2 arcsec | Measured ([AST-11](validation/astrometry.md#ast-11)) | §4.2 (step 4) |
| Radius for unresolved catalog companions | 3 arcsec | Partly measured ([AST-10](validation/astrometry.md#ast-10)) | §4.2 (step 4) |
| Variable-star catalog match radius | 3 arcsec | Partly measured ([AST-12](validation/astrometry.md#ast-12)) | §5.2 (concept 4) |
| Centre star of an unsolved spectral frame: pointing search radius; search area; faintest peak | 5 arcmin; 0.10 of the shorter side; 0.10 of the brightest pixel | Partly measured ([AST-09](validation/astrometry.md#ast-09)) | §6.2 (concept 6) |
| Minimum catalog-matched stars | 20 | Partly measured ([AST-05](validation/astrometry.md#ast-05)) | §4.4.2 |
| Residual limit | 0.5 of a star width | Partly measured ([AST-04](validation/astrometry.md#ast-04)) | §4.4.2 |
| Failed catalog lookups allowed | under 50% | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.4.2 |

<!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SourceDetector -->
<!-- impl: astrometricslib.drivers.astrometry_net_driver.MINIMUM_SOURCES_FOR_LOCAL_SOLVE -->
<!-- impl: astrometricslib.pipelines.shared.catalog_star_identity.CATALOG_MATCH_RADIUS_ARCSEC -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.MINIMUM_MATCHED_STARS -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.MAXIMUM_RESIDUAL_FRACTION_OF_FWHM -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.run_gates.MAXIMUM_FAILED_LOOKUP_FRACTION -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.MAXIMUM_PLATE_SOLVE_SOURCES -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.MINIMUM_MATCHED_STARS -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.MAXIMUM_SHIFT_ARCSEC -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.INITIAL_MATCH_RADIUS_PIXELS -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.REFINED_MATCH_RADIUS_PIXELS -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.CLIP_SIGMAS -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.MAXIMUM_PASSES -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.wcs_refinement.SIP_DEGREE -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_VIZIER_TIMEOUT_SECONDS -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_ESA_TIMEOUT_SECONDS -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_DEPTH_STEP_MAGNITUDES -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_DEPTH_STEPS_DOWN -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_MAGNITUDE_LIMIT_MARGIN -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_MINIMUM_MAGNITUDE_LIMIT -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_MAXIMUM_MAGNITUDE_LIMIT -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.GAIA_DEFAULT_MAGNITUDE_LIMIT -->
<!-- impl: astrometricslib.pipelines.shared.catalog_star_identity.SAME_STAR_POSITION_TOLERANCE_ARCSEC -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.UNRESOLVED_COMPANION_RADIUS_ARCSEC -->
<!-- impl: astrometricslib.pipelines.shared.variable_star_catalogs.MATCH_RADIUS_ARCSEC -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.POINTING_ERROR_SEARCH_RADIUS_ARCSEC -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.CENTRE_STAR_SEARCH_RADIUS_FRACTION -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_identifier.CENTRE_STAR_MINIMUM_PEAK_FRACTION -->

### Photometry settings

**Table 5.** Tuned settings of the photometry pipeline and their validation status.

| Parameter | Default | Status | Architecture section |
|---|---|---|---|
| Aperture radius; sky ring radii | 4 px; 7 to 12 px | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.2 (concept 1) |
| Re-centering box; refinement passes; largest allowed move | 11 px; 3; 1.5 px | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.2 (concept 1) |
| Comparison band: brightest skipped; band ends at | 2%; 30% of candidates | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.2 (concept 4) |
| Candidates vetted; minimum and cap of comparison stars | 100; 5 and 20 | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.2 (concept 4) |
| Vetting consistency limit; share of typical stars for a usable frame | 3 $\sigma$; one half | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.2 (concept 4) |
| Frame outlier clip; per-star outlier clip | 3 $\sigma$ (at least 5 frames); 5 $\sigma$ | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.2 (concept 4) |
| Share of frames rejected before the run is flagged | 25% | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.4.2 |
| Noise model: minimum stars; stars per bin; most bins | 20; 10; 10 | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.2 (concept 5) |
| Thresholds: excess scatter; chi-square and Stetson J | 1.5; field median + 2.326 robust $\sigma$ | Partly measured ([PHO-06](validation/photometry.md#pho-06)); synthetic fields only | §5.2 (concept 5) |
| Small-field cutoff on the coefficient of variation | median + 7.4 MAD (unscaled; about 5 robust $\sigma$, since 7.4 / 1.4826 = 5.0), never below 0.02 | Measured ([PHO-10](validation/photometry.md#pho-10)) | §5.2 (concept 5) |
| Period verdicts: false-alarm limit for "possible"; for "detected"; shuffles | 0.05; 0.01 with at least 3 cycles; 300 | Partly measured ([PHO-12](validation/photometry.md#pho-12)); synthetic only | §5.2 (concept 8) |
| Between-session flag: significance; amplitude | above 3; at least 0.02 mag | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.2 (concept 6) |
| Discrimination AUC floor | 0.7 | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.4.2 |
| Largest acceptable value of the smallest detectable change (peak to peak) | 0.3 mag | Design estimate ([photometry table](validation/photometry.md#design-estimates)); the smallest change found on a real field is in [PHO-09](validation/photometry.md#pho-09) | §5.4.2 |

<!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.measure_aperture_photometry -->
<!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.CENTROID_BOX_HALF_WIDTH_PX -->
<!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.MAX_CENTROID_SHIFT_PX -->
<!-- impl: astrometricslib.pipelines.photometry.pre_processing.frame_photometry.CENTROID_REFINEMENT_PASSES -->
<!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.BRIGHTEST_SKIPPED_FRACTION -->
<!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.FAINTEST_BAND_FRACTION -->
<!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.MAXIMUM_VETTING_CANDIDATES -->
<!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.MINIMUM_COMPARISON_STARS -->
<!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.DEFAULT_MAXIMUM_COMPARISON_STARS -->
<!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.EXCESS_SCATTER_SIGMA -->
<!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.MINIMUM_USABLE_FRAME_FRACTION -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.VariabilityAnalyzer._reject_outlier_frames -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.MINIMUM_STARS_FOR_NOISE_MODEL -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.MINIMUM_STARS_PER_NOISE_BIN -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.MAXIMUM_NOISE_BINS -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.EXCESS_SCATTER_THRESHOLD -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_indices.CALIBRATION_SIGMAS -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.MINIMUM_LONG_TERM_AMPLITUDE_MAG -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.DEFAULT_LONG_TERM_SIGNIFICANCE_THRESHOLD -->
<!-- impl: astrometricslib.pipelines.photometry.post_processing.variability_skill.MINIMUM_DISCRIMINATION_AUC -->
<!-- impl: astrometricslib.pipelines.photometry.post_processing.run_gates.MAXIMUM_DETECTABLE_AMPLITUDE_MAG -->
<!-- impl: astrometricslib.pipelines.photometry.post_processing.run_gates.MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.DEFAULT_VARIABILITY_SIGMA_THRESHOLD -->
<!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.FALSE_ALARM_FOR_DETECTION -->
<!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.verdict_from_false_alarm -->

### Spectroscopy settings

**Table 6.** Tuned settings of the spectroscopy pipeline and their validation status.

| Parameter | Default | Status | Architecture section |
|---|---|---|---|
| Reading box half-width; width smoothing | 2.5 times the trail width; 61 steps | Design estimate ([spectroscopy table](validation/spectroscopy.md#design-estimates)); the effect of smoothing is in [SPE-04](validation/spectroscopy.md#spe-04) | §6.2 (concept 2) |
| Minimum signal-to-noise for analysing a spectrum | 1.5 | Partly measured ([SPE-10](validation/spectroscopy.md#spe-10)) | §6.2 (concept 5) |
| Line significance for the wavelength zero point | 6 | Partly measured ([SPE-11](validation/spectroscopy.md#spe-11)); synthetic only | §6.2 (concept 4) |
| Sky strip gap and width | 6 px; 10 px | Partly measured ([SPE-01](validation/spectroscopy.md#spe-01)) | §6.2 (concept 2) |
| Sky contamination limit | 3 $\sigma$ | Partly measured ([SPE-03](validation/spectroscopy.md#spe-03)); simulation only | §6.2 (concept 2) |
| Fewest pixels in a sky strip | 4 | Design estimate ([spectroscopy table](validation/spectroscopy.md#design-estimates)) | §6.2 (concept 2) |
| Atmospheric (telluric) bands left out of classification | oxygen B 6860 to 6940 Å; oxygen A 7490 to 7720 Å; water 8100 to 8320 Å | Partly measured ([SPE-15](validation/spectroscopy.md#spe-15)) | §6.2 (concept 5) |
| Poor-match limit | 0.15 relative RMS | Partly measured ([SPE-16](validation/spectroscopy.md#spe-16)) | §6.2 (concept 5) |
| No-type limit | 0.45 relative RMS | Partly measured ([SPE-17](validation/spectroscopy.md#spe-17)) | §6.2 (concept 5) |
| Ambiguity gap | 0.02 relative RMS | Measured ([SPE-18](validation/spectroscopy.md#spe-18)) | §6.2 (concept 5) and §6.4.2 |
| Catalog disagreement | 20 subtype steps (two whole letter classes) | Partly measured ([SPE-19](validation/spectroscopy.md#spe-19)) | §6.4.2 |
| Wavelength-fit rejection tolerance | 10 nm RMS | Design estimate ([spectroscopy table](validation/spectroscopy.md#design-estimates)) | §6.2 (concept 3) and §6.4.2 |
| Wavelength-scale limit (scatter of the corrected offsets) | 11 Å (one pixel of dispersion) | Design estimate ([spectroscopy table](validation/spectroscopy.md#design-estimates)) | §6.4.2 |
| Gaia XP agreement: largest median tilt | 3% per 1000 Å | Design estimate ([spectroscopy table](validation/spectroscopy.md#design-estimates)) | §6.4.2 |
| Processing quality: largest share of spectra failing a metric | 50% | Design estimate ([spectroscopy table](validation/spectroscopy.md#design-estimates)) | §6.4.2 |

<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.APERTURE_SIGMA_MULTIPLIER -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.APERTURE_SIGMA_SMOOTHING_STEPS -->
<!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectrum_signal.MINIMUM_SPECTRUM_SIGNAL_TO_NOISE -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.wavelength_zero_point.MINIMUM_LINE_SIGNIFICANCE -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_BAND_GAP_PX -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_BAND_WIDTH_PX -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_CONTAMINATION_SIGMA -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectrum_extractor.SKY_BAND_MINIMUM_SAMPLE_COUNT -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_mask.ATMOSPHERIC_BANDS_ANGSTROM -->
<!-- impl: astrometricslib.models.stellar_source.NO_GOOD_MATCH_RMS -->
<!-- impl: astrometricslib.models.stellar_source.UNRELIABLE_MATCH_RMS -->
<!-- impl: astrometricslib.models.stellar_source.AMBIGUOUS_RMS_GAP -->
<!-- impl: astrometricslib.models.stellar_source.DIFFERS_FROM_CATALOG_SUBTYPES -->
<!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.wavelength_scale.WAVELENGTH_SCALE_LIMIT_ANGSTROM -->
<!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp.GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM -->
<!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.run_gates.PROCESSING_QUALITY_MAXIMUM_FAILED_FRACTION -->

### Moving object detection settings

**Table 7.** Tuned settings of the moving object detection pipeline and their validation status.

| Parameter | Default | Status | Architecture section |
|---|---|---|---|
| Frames needed for a track | 3 | Design estimate ([asteroid detection table](validation/asteroid_detection.md#design-estimates)) | §7.2 (concept 2) |
| Rate range; chaining radius cap | 1 to 300 arcsec/h; 300 arcsec | Design estimate ([asteroid detection table](validation/asteroid_detection.md#design-estimates)) | §7.2 (concepts 2 and 4) |
| Stationary tolerances | 1.5 px; 3 arcsec | Design estimate ([asteroid detection table](validation/asteroid_detection.md#design-estimates)); their effect on slow movers is in [MOV-02](validation/asteroid_detection.md#mov-02) | §7.2 (concept 3) |
| Residual multiple; displacement multiple | 2; 3 times the position error | Partly measured ([MOV-03](validation/asteroid_detection.md#mov-03)); synthetic only | §7.2 (concept 4) |
| Assumed position error; centroid error | 10 arcsec; 0.5 px | Design estimate ([asteroid detection table](validation/asteroid_detection.md#design-estimates)) | §7.2 (concept 1) and §7.4.2 |
| Ephemeris match radius | 10 arcsec | Design estimate ([asteroid detection table](validation/asteroid_detection.md#design-estimates)); close to a single frame's position error ([MOV-06](validation/asteroid_detection.md#mov-06)) | §7.2 (concept 5) |
| Detection threshold; detection width | 5 $\sigma$; 4 px | Partly measured ([MOV-01](validation/asteroid_detection.md#mov-01)); synthetic only | §7.4.1 |

<!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->
