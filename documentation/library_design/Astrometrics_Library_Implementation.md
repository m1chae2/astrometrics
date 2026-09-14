# Astrometrics Library Implementation Overview

While the theoretical algorithms and data flow are covered in the [Astrometrics Library Architecture](./Astrometrics_Library_Architecture.md) document, this map serves as a direct index to the Python source code where those algorithms are physically implemented.

Due to the internal nature of these modules, they are deliberately hidden from the public API Reference. Developers wishing to review or modify the core algorithms should refer to the following directories within `astrometricslib/`. The layout is layered, bottom to top: `models/`/`utilities/` hold data shapes and configuration with no dependency on anything else in the library; `image_processing/` holds pixel-level primitives (FITS access, source detection, saturation checks); `drivers/` wraps every external tool (Siril, Astrometry.net, the SQLite catalog cache, calibration-frame storage); `data_access/` reads and writes the target/frame/stellar-object database; `catalog_services/` is where the public API reaches directly for plain reads and writes that are not an analysis run -- scanning a folder for FITS files, target CRUD, converting a FITS file to a PNG; `pipelines/` holds the five analysis columns plus the dispatcher and cross-column shared code; `api/` is the public gate everything above calls through.

## Implementation Matrix

The library is organized into five layers across five specialized scientific pipelines and a shared orchestration spine. The "Ratchet Rule" strictly forbids any layer from importing from a layer above it.

| Layer | Stacking | Astrometry | Photometry | Spectroscopy | Asteroid Det. | Shared by All |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Public API**<br>*(calls L2)* | `run_stacking` | `run_astrometry` | `run_photometry` | `run_spectroscopy` | `detect_asteroids` | **Astrometrics facade**<br>`api/`<br>`mcp/` |
| **2. Public Helpers**<br>*(calls L3)* | `stack_frames_with_timeout`<br>*(pre-stage)* | `analyze_target`<br>*(runner)* | `analyze_target`<br>*(runner)* | `analyze_target`<br>*(runner)* | `analyze_target`<br>*(runner)* | **pipelines/**<br>`tasks`<br>`pipeline_base`<br>`runners` |
| **3. Pipelines**<br>*(calls L4)* | **stacking/**<br>`stage`<br>`stack_quality` | **astrometry/**<br>`star_identifier`<br>`catalog_seeding`<br>`spectral_star_reg` | **photometry/**<br>`variability_anal.`<br>`ensemble normal.` | **spectroscopy/**<br>`spectrum_extract`<br>`optics_physics`<br>`calibration_tuner` | **asteroid_detection/**<br>`detection`<br>`ephemeris` | **image_processing/**<br>**pipelines/shared/**<br>`frame_grouping`<br>`star_recording` |
| **4. Driver Access**<br>*(exposed via L1)* | *(handed one by stack_frames_with_timeout)* | `catalog_access` | `catalog_access` | `catalog_access` | *(skips L4/L5)* | **driver_access/**<br>`catalog_access`<br>`frame_scanning` |
| **5. Drivers**<br>*(edge)* | `siril_interface` | `plate_solve_store` | `plate_solve_iface` | *(reaches through astrometry)* | *(none)* | **drivers/**<br>`logger`<br>`local_db` |
| **Outside** | Siril (headless) | astrometry.net<br>Gaia, SIMBAD | astrometry.net | *(via astrometry)* | IMCCE SkyBoT | FITS on disk<br>SQLite |

> [!NOTE]
> **Shared Vocabulary:** Modules like `models/`, `enums.py`, `exceptions.py`, and `config_schema.py` contain pure data structures. They perform no I/O, contain no behavior, and import nothing from any layer. They may be safely imported by any module in the system.
## Core Processing Pipelines

### Stacking
*Located in:* `astrometricslib/pipelines/stacking/`
- **Dispatch and orchestration:** `astrometricslib/pipelines/tasks.py` (runs Siril stacking as a tracked job with a hard timeout, picks which analysis pipeline runs by name, and runs a target's full stack-solve-photometry-spectroscopy sequence) and `astrometricslib/api/batch.py` (runs many targets through that sequence in parallel)
- **Sub-exposure quality evaluation:** `frame_homogeneity.py` and `background_homogeneity.py`
- **Raw frame quality & statistics:** `astrometricslib/data_access/frame_statistics.py`
- **Pixel rejection and integration:** `astrometricslib/utilities/rejection_thresholds.py`, `astrometricslib/utilities/stack_filter_floor.py`, and `stage.py`
- **Frame-count-adaptive filtering:** `stack_quality.py`

### Astrometry
*Located in:* `astrometricslib/pipelines/astrometry/`
- **Plate solving:** `astrometricslib/drivers/plate_solve_interface.py`
- **Star detection and centroiding:** `star_identifier.py`
- **Coordinate transformations (WCS) and pipeline orchestration:** `pipeline.py`
- **Local Gaia catalog cache:** `astrometricslib/drivers/catalog_store.py`

### Photometry
*Located in:* `astrometricslib/pipelines/photometry/`
- **Aperture photometry:** `variability_analyzer.py`
- **Light curve generation:** `variability_analyzer.py`

### Spectroscopy
*Located in:* `astrometricslib/pipelines/spectroscopy/`
- **Spectrum extraction:** `spectrum_extractor.py` and `_extractor_c.c`
- **Wavelength calibration:** `spectrum_calibrator.py` and `calibration_tuner.py`
- **Flux calibration:** `quantum_efficiency_correction.py` and `optics_physics.py`
- **Star alignment and registration:** `registration_quality.py`
- **Per-frame and per-session batch processing:** `frame_analysis.py` and `batch.py`

### Asteroid Detection (Moving Objects)
*Located in:* `astrometricslib/pipelines/asteroid_detection/`
- **Blink analysis and tracking:** `detection.py` and `pipeline.py`
- **Ephemeris calculation:** `ephemeris.py` and `frame_wcs_composer.py`

### Shared Pipeline Code
*Located in:* `astrometricslib/pipelines/shared/`
- **Frame selection and grouping by camera/optic:** `frame_grouping.py`
- **Star recording shared by all three stellar pipelines:** `star_recording.py`
- **Observing-session grouping:** `target_sessions.py`
- **Per-image analysis state:** `analysis_context.py`

### Pixel-Level Primitives
*Located in:* `astrometricslib/image_processing/`
- **FITS HDU0/HDU1 access:** `fits_access.py`
- **The `AstrometricsImage` data container:** `image.py`
- **Source detection:** `source_detection.py`
- **Saturation checks:** `saturation.py`
- **Image quality & hardware telemetry:** `quality_metrics.py`
- **Filter-type detection from FITS headers:** `filter_detection.py`

### Catalog Services
*Located in:* `astrometricslib/catalog_services/`
The plain, non-analysis reads and writes the public API calls directly, without
running a pipeline:
- **Filesystem frame scanning:** `frame_scanning.py`
- **Target catalog CRUD:** `target_records.py`
- **Image format conversion for display:** `image_conversions.py`
- **Image scaling math shared by `image_conversions.py` and the visualization overlay:** `utilities/image_scaling.py`

### External-Tool Drivers
*Located in:* `astrometricslib/drivers/`
- **Siril stacking/registration:** `siril_interface.py`
- **Astrometry.net plate solving:** `plate_solve_interface.py`
- **Local Gaia catalog cache:** `catalog_store.py`
- **Calibration frame library (darks/bias/flats):** `calibration_library.py`
- **One-time startup migration and schema backfill for the target/stellar catalogs:** `local_database.py`
- **Job logging:** `job_logging.py` and `logger_interface.py`

### Database Access
*Located in:* `astrometricslib/data_access/`
- **Target/stellar-catalog repository (`CatalogAccess`), the front door every other layer reaches for the database through:** `catalog_access.py`
- **Frame statistics:** `frame_statistics.py`
- **Background level measurement:** `background_measurement.py`

`catalog_access.py` records through a generic, keyed-record SQLite store shared with
wayfindinglib, rather than executing SQL itself:
*Located in:* `datastore/`
- **Generic keyed-model storage (get/put/exists/merge, one table per dataset type):** `butler.py`
- **SQLite connection setup and JSON encoding:** `local_database.py`
- **Cross-process file locking for shared hardware/storage resources:** `process_locks.py`

## Empirical Validation Campaign — Implementation Notes

The [Astrometrics Library Architecture](./Astrometrics_Library_Architecture.md) document's Empirical Validation section (§8) describes, in plain terms, the results of an 8-session validation campaign against real telescope data and the bugs it surfaced. This section maps those findings to the actual code, for developers who need to trace a finding back to its source.

- **Validation scripts:** The 8 sessions were driven through the public API via the scripts in `documentation/notebooks/astrometrics/target_stacking_and_analysis/scripts/`.
- **Finding 6 (cross-session photometry tracking bug):** `analyze_target(pipeline_type="photometry")` was running `VariabilityAnalyzer` across a target's entire frame history in one pass, using a single reference frame from whichever session came first. The fix scopes each `VariabilityAnalyzer` run to one `TargetSession` at a time.
- **Finding 7 (cross-session star identity matching):**
  - The matching itself is implemented per Architecture §5.2 (concept 3).
  - An unconditional per-star SIMBAD query was being triggered by routing through the shared `AstrometryPipeline` entry point (the same one "astrometry"/"spectroscopy" use), when only the already-solved WCS was actually needed. Fixed by calling `PlateSolver` directly instead.
  - An initial pairwise `SkyCoord.separation()` loop (comparing every star to every other star) did not scale past a few hundred stars per session; replaced with a KD-tree-backed `search_around_sky` call.
  - Repeatability was verified by checking that two consecutive runs produced a byte-for-byte identical `stellar_catalog` row set.
  - The one session excluded from matching (a light frame that actually referenced `M 13/M_13_Stacked.fits`, a different target's stack, due to a pre-existing library data-labeling error) was correctly isolated via `sessions_missing_wcs`, without affecting the other 7 sessions.
- **Finding 5 (star/track linkage speedup):** An RA/Dec bounding-box pre-filter, applied before computing angular separations, cut the pairwise search from $O(N \times M)$ to $O(N \log M)$ on M 81's ~150,000 detected sources across 46 frames.

## Batch Processing & Maintenance Scripts

*Located in:* `astrometricslib/scripts/`
These top-level scripts orchestrate the pipeline across multiple targets and manage execution environments.
- **Batch execution:** `run_all_target_processing.py`
- **Catalog seeding:** `seed_local_star_catalog.py`
- **Concurrency benchmarking:** `benchmark_siril_concurrency.py`
- **Data backfilling:** `backfill_focal_length.py`
