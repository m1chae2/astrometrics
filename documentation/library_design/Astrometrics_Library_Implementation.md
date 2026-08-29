# Astrometrics Library Implementation Overview

While the theoretical algorithms and data flow are covered in the [Astrometrics Library Architecture](./Astrometrics_Library_Architecture.md) document, this map serves as a direct index to the Python source code where those algorithms are physically implemented.

Due to the internal nature of these modules, they are deliberately hidden from the public API Reference. Developers wishing to review or modify the core algorithms should refer to the following directories within `astrometricslib/`. The layout is layered, bottom to top: `models/`/`utilities/` hold data shapes and configuration with no dependency on anything else in the library; `image_processing/` holds pixel-level primitives (FITS access, source detection, saturation checks); `drivers/` wraps every external tool (Siril, Astrometry.net, the SQLite catalog cache, calibration-frame storage); `data_access/` reads and writes the target/frame/stellar-object database; `pipelines/` holds the five analysis columns plus the dispatcher and cross-column shared code; `api/` is the public gate everything above calls through.

## Core Processing Pipelines

### Stacking
*Located in:* `astrometricslib/pipelines/stacking/`
- **Dispatch and orchestration:** `astrometricslib/pipelines/dispatch.py` and `astrometricslib/api/batch.py`
- **Sub-exposure quality evaluation:** `frame_homogeneity.py` and `background_homogeneity.py`
- **Raw frame quality & statistics:** `astrometricslib/data_access/frame_statistics.py`
- **Mount tracking analysis:** `tracking_analysis.py`
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

### Asteroid Recovery (Moving Object Detection)
*Located in:* `astrometricslib/pipelines/asteroid_recovery/`
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

### External-Tool Drivers
*Located in:* `astrometricslib/drivers/`
- **Siril stacking/registration:** `siril_interface.py`
- **Astrometry.net plate solving:** `plate_solve_interface.py`
- **Local Gaia catalog cache:** `catalog_store.py`
- **Calibration frame library (darks/bias/flats):** `calibration_library.py`
- **Disk-backed target/stellar-object storage:** `disk_interface.py`
- **Job logging:** `job_logging.py` and `logger_interface.py`

### Database Access
*Located in:* `astrometricslib/data_access/`
- **Target/frame/stellar-object recording:** `catalog_access.py` and `target_records.py`
- **Filesystem frame scanning:** `frame_scanning.py`
- **Frame statistics:** `frame_statistics.py`
- **Image format conversion and scaling for display:** `image_conversions.py` and `image_scaling.py`

## Batch Processing & Maintenance Scripts

*Located in:* `astrometricslib/scripts/`
These top-level scripts orchestrate the pipeline across multiple targets and manage execution environments.
- **Batch execution:** `run_all_target_processing.py`
- **Catalog seeding:** `seed_local_star_catalog.py`
- **Concurrency benchmarking:** `benchmark_siril_concurrency.py`
- **Data backfilling:** `backfill_focal_length.py`
