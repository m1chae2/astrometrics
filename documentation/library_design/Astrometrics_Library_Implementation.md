# Astrometrics library implementation

*Version 1.7 · 2026-10-15 · Status: current*

## Abstract

This paper maps the design in `Astrometrics_Library_Architecture.md` to the Python source in `astrometricslib/`. It names the layers and the rule that keeps them apart, the model classes, the modules of each of the five pipelines, and the scripts and tests that check them. It lists each pipeline's tuned values with a status label and a link to the register entry that holds the evidence. It is written for developers who review or change the algorithms.

Terms are defined in the [glossary](../Glossary.md). This document also defines each term the first time it appears.

## 1. Scope

**Statement of need.** This paper indexes the theory to the code. Theory is in [Astrometrics_Library_Architecture.md](./Astrometrics_Library_Architecture.md). Evidence is in the validation register, [validation/index.md](./validation/index.md). Module paths are relative to `astrometricslib/`. Stage detail is in the [pipeline review guides](./pipelines/index.md) and the READMEs.

The one supported entry point is the package root (`from astrometricslib import Astrometrics`, names in `astrometricslib.__all__`). It re-exports the facade from `api/`, the driver interfaces (Section 4.7), the job framework (Section 4.9), and the error types. The package builds on Astropy [1]. `wayfindinglib`, the backend and the MCP servers [2] use the library. The settings each pipeline uses are listed in Section 5.

## 2. Package layering

The package has six layers. Each layer may import only from the layers below it. Table 1 lists them, and the ratchet test in Appendix A enforces the rule.

**Table 1.** The six layers, from the top.

| Layer | Holds | Calls into | Key modules |
| --- | --- | --- | --- |
| 1. Public API | The `Astrometrics` facade and the root exports | Layers 2 to 5 | `api/processing.py`, `api/quality.py`, `astrometricslib/__init__.py` |
| 2. Pipeline runners | Stage dispatcher; batch runner | Layer 3 | `pipelines/tasks.py`, `pipelines/target_batch.py` |
| 3. Pipelines | Stacking, astrometry, photometry, spectroscopy, asteroid detection | Layers 4 to 6; sideways between pipelines | `pipelines/<name>/` |
| 4. Shared pipeline code | Frame scanning, target records, image conversion, quality primitives, run recording, pipeline contract | Layers 5 and 6 | `pipelines/shared/`, `pipelines/pipeline_base.py` |
| 5. Drivers | External tool wrappers, FITS access, SQLite stores | Layer 6 | `drivers/` |
| 6. Shared vocabulary | Models, configuration, logging, jobs, storage | No layer | `models/`, `foundation/`, `utilities/` |

`visualization/` sits outside the layers (Table 9).

**Table 2.** Public entry point and outside services of each pipeline.

| Pipeline | Public call | Outside services |
| --- | --- | --- |
| Stacking | `ProcessingPipelines.stack`; `pipelines.tasks.stack_frames_with_timeout` for the batch run | Siril (headless) |
| Astrometry | `ProcessingPipelines.process_target`, `stages=["astrometry"]` | Astrometry.net [3]; Gaia through VizieR [4] or the ESA archive, via astroquery [5]; SIMBAD [6] |
| Photometry | `process_target`, `stages=["photometry"]` | None; star names come from astrometry |
| Spectroscopy | `process_target`, `stages=["spectroscopy"]` | Gaia XP spectra; Gaia DR3 reddening |
| Asteroid detection | `process_target`, `stages=["asteroids"]` | IMCCE SkyBoT, through astroquery in `ephemeris.py` |

`process_target` (`api/processing.py`) runs the chosen stages in a fixed order through `pipelines.tasks.run_target_stages`. For a list of targets it runs the full sequence per target in its own process (`pipelines.target_batch.process_targets_in_parallel`).

## 3. Data models

Pydantic classes in `models/` hold fields and validation only (see `models/README.md`). Table 3 maps each record of Architecture §2 to its classes.

**Table 3.** Records of the architecture paper and the classes that hold them.

| Architecture record | Classes | Module |
| --- | --- | --- |
| Observation target (§2.1) | `Target`, `FrameRecord`, `ImageType` | `models/target.py` |
| Stellar object (§2.1) | `StellarObject`, with `PhotometryResult`, `PeriodogramResult`, `TransitCandidate`, `SessionPhotometrySummary`, `StellarSessionMatch` and `SpectroscopyResult` | `models/stellar_source.py`, `models/spectral_cross_checks.py` |
| Moving object candidate (§2.1) | `AsteroidDetectionCandidate`, `FrameDetection`, `MovingObjectTrack`, `EphemerisMatch`, `CascadeStage` | `models/moving_object.py` |
| Quality record (§2.1) | `PipelineQualitySummaryBase` and one subclass per pipeline (`StackQualitySummary`, `AstrometryQualitySummary`, and so on); gates are `GateResult` with a `GateStatus` | `models/quality/quality_summary.py`, `models/quality/gate_result.py` |
| Camera profile (§2.3) | `CameraProfile` (schema 2), `SensorSettingNoise` (gain and read noise per gain setting, offset and binning), `ProvenancedValue`, `QuantumEfficiencyRecord`, `AppliedCameraProfile` | `models/camera_profile.py`, `models/applied_camera_profile.py` |
| Provenance (§2.3) | IVOA Provenance Data Model [7] classes such as `Agent`, `Entity`, `Used`, `WasGeneratedBy` | `models/provenance.py` |

Camera profiles: `drivers/camera_profile_store.py` (`resolve_camera_profile`, `save_sensor_setting_noise`), `drivers/camera_profile_migration.py` (`migrate_camera_profiles`), `pipelines/shared/applied_camera_profile.py` (`record_camera_profile`). Provenance: `pipelines/shared/provenance_recording.py` (`record_pipeline_run`), `drivers/provenance_store.py` (`ProvenanceStore`, `export_target_lineage_as_prov_xml`).

## 4. Pipelines

Paths are relative to the pipeline's folder unless they start with `astrometricslib/`.

### 4.1 Stacking (Architecture §3)

*Located in:* `astrometricslib/pipelines/stacking/`

- **Interfaces (§3.1).** `stack_request.py` (`choose_frames_to_stack`, `run_stack`), `stage.py` (`stack_frames`), `stack_runner.py` (engine run, `PIPELINE_STEPS`). Batch dispatch is in `astrometricslib/pipelines/tasks.py` and `target_batch.py`.
- **Calibration frames (§3.2).** `calibration_ingest.py`, `astrometricslib/drivers/calibration_library.py`, `pre_processing/flat_calibration.py`.
- **Gain and read noise (§3.2).** `CalibrationCatalog.measure_sensor_noise` (`astrometricslib/api/processing.py`) calls `sensor_noise_run.py`, which finds a setting's flats and bias frames and calls `pre_processing/sensor_noise.py` (`measure_sensor_noise`). With `save=True` it writes the camera profile. The RPC is `calibration:measure_sensor_noise`; the MCP server withholds it. Evidence: [STK-35](validation/stacking.md#stk-35).
- **Rejection and filtering (§3.2).** `astrometricslib/utilities/rejection_thresholds.py`, `astrometricslib/utilities/stack_filter_floor.py`.
- **Exposure groups and weights (§3.2).** `processing/exposure_groups.py`, `group_alignment.py`, `group_derotation.py`, `star_pattern_candidates.py`, `spectral_frame_alignment.py`; `pre_processing/exposure_weighting.py`.
- **Sequence and restacks (§3.3).** `pre_processing/stack_inputs.py`; `post_processing/previous_stack.py`, `stack_comparison.py`, `interrupted_jobs.py`.
- **Previews (§3.3).** `post_processing/stack_preview.py`, `colour_calibration.py`, `starnet_stretch.py`, `sky_level.py`, `star_tone.py`, `bright_object.py`, `stack_crop.py`, `preview_remake.py`.
- **Frame measurement (§2.1).** `ProcessingPipelines.record_frame_measurements` (`frame_measurement_run.py`) measures each light frame with `measure_frame`.
- **Input screening (§3.4.1).** `pre_processing/frame_quarantine.py`, `frame_homogeneity.py`, `background_homogeneity.py`, `camera_selection.py`, `assess_input_quality.py`.
- **Output checks (§3.4.2).** `post_processing/assess_output_quality.py`, `stack_quality.py`, `exposure_group_report.py`, `stack_quality_report.py`.
- **Stack measurements (§3.4.2).** `stage._record_stack_quality` calls `post_processing/record_quality.py` (`record_stacking_quality`), which stores the summary, 16 gates, the stack noise and the frame weights. <!-- impl: astrometricslib.pipelines.stacking.post_processing.record_quality.record_stacking_quality -->
- **Stack noise and weights (§3.4.2).** `post_processing/stack_noise.py` (`measure_stack_noise`) and `stack_membership.py` (`wfwhm_weights`). Evidence: [STK-36](validation/stacking.md#stk-36), [STK-07](validation/stacking.md#stk-07).
- **Subset stacks (§3.4.2).** `subset_noise_run.py` and `subset_stack.stack_frame_subset` stack parts of the library stack into `_subsets/`. Evidence: [STK-36](validation/stacking.md#stk-36), [STK-37](validation/stacking.md#stk-37).

### 4.2 Astrometry (Architecture §4)

*Located in:* `astrometricslib/pipelines/astrometry/`

- **Interfaces (§4.1).** `pipeline.py`; `runner.py` is the `AnalysisPipeline` adapter (four-step contract in `pipelines/pipeline_base.py`).
- **Detection and centroids (§4.2).** `processing/star_identifier.py`, `pre_processing/source_detection.py`, `pre_processing/fwhm.py`.
- **Plate solving and Gaia (§4.2).** `astrometricslib/drivers/astrometry_net_driver.py`, `gaia_cone_driver.py`, `catalog_store.py`; `utilities/catalog_seeding.py`.
- **Gaia refit (§4.2).** `processing/wcs_refinement.py`.
- **WCS header (§4.3).** `astrometricslib/pipelines/shared/session_identification.py` (`write_wcs_to_fits_header`).
- **Checks (§4.4).** `post_processing/record_quality.py`, `assess_match_quality.py`.

### 4.3 Photometry (Architecture §5)

*Located in:* `astrometricslib/pipelines/photometry/`

- **Interfaces (§5.1).** `runner.py` (adapter); `batch.py` (per-session runs, cross-session matching, period search).
- **Aperture photometry (§5.2).** `pre_processing/frame_photometry.py`, `detector_noise.py`, `observation_times.py`.
- **Comparison stars and light curves (§5.2).** `processing/comparison_ensemble.py`, `variability_analyzer.py`, `variability_indices.py`.
- **Period search (§5.2).** `processing/periodicity_search.py`, `period_checks.py`, `family_wise_correction.py`.
- **Checks (§5.4).** `pre_processing/assess_input_quality.py`; `post_processing/record_quality.py`, `variability_skill.py`, `known_variability_labels.py`, `assess_output_quality.py`.

### 4.4 Spectroscopy (Architecture §6)

*Located in:* `astrometricslib/pipelines/spectroscopy/`

- **Interfaces (§6.1).** `pipeline.py` (`SpectroscopyPipeline`), `runner.py` (adapter), `record_and_flag_spectroscopy_stars.py`, `frame_analysis.py`, `batch.py`.
- **Star identification (§6.2).** `pre_processing/spectral_star_registration.py`.
- **Extraction (§6.2).** `pre_processing/spectrum_extractor.py`, `_extractor_c.c`, `neighbor_trail_deblending.py`, `neighbor_wing_correction.py`.
- **Wavelength calibration (§6.2).** `pre_processing/spectrum_calibrator.py`, `optics_physics.py`, `differential_refraction.py`, `atmospheric_refraction.py`, `wavelength_zero_point.py`; `utilities/calibration_tuner.py`.
- **Flux and resolution (§6.2).** `pre_processing/quantum_efficiency_correction.py`, `instrument_response.py`, `atmospheric_extinction.py`, `measured_line_spread.py`, `spectral_resolution.py`.
- **Classification and features (§6.2).** `processing/spectral_classifier.py`, `spectrum_analysis.py`, `star_reddening.py`, `interstellar_extinction.py`.
- **Stack registration quality (§6.3).** `utilities/registration_quality.py`.
- **Checks (§6.4).** `post_processing/record_quality.py`, `compare_to_gaia_xp.py`, `compare_to_catalog.py`, `wavelength_scale.py`.

### 4.5 Asteroid detection (Architecture §7)

*Located in:* `astrometricslib/pipelines/asteroid_detection/`

- **Interfaces (§7.1).** `pipeline.py`; `runner.py` (adapter).
- **Detection (§7.2).** `detection.py` (blink analysis, tracking), `frame_wcs_composer.py` (per-frame WCS), `ephemeris.py` (IMCCE SkyBoT).
- **Checks (§7.4).** `record_quality.py`.
- **Unused by any pipeline step.** `mpc_report.py` (MPC report formats [8]) and `rotation_period.py`. Both are tested.

### 4.6 Shared pipeline code

*Located in:* `astrometricslib/pipelines/shared/`

- **Frames and targets.** `frame_grouping.py`, `quality/frame_selection.py`, `frame_scanning.py`, `target_records.py`, `target_sessions.py`.
- **Images.** `image_conversions.py`, `image_scaling.py`.
- **Stars.** `star_recording.py`; `catalog_star_identity.py`.
- **Solving and state.** `session_identification.py`, `analysis_context.py`.
- **Recording.** `provenance_recording.py`, `applied_camera_profile.py`.
- **Quality primitives.** `quality/saturation.py`, `quality_metrics.py`, `frame_statistics.py`, `background_measurement.py`, `raw_frame_check.py`, `frame_measurement.py`.
- **Run measurements.** Each pipeline's `record_quality.py` stores its run's numbers and gates as the metrics declared in its `metrics.py`.

### 4.7 Drivers and pixel-level primitives

*Located in:* `astrometricslib/drivers/`

- **Interfaces.** `interfaces/` holds `StackingDriver`, `PlateSolveDriver`, `SimbadDriver`, `GaiaXpDriver`, `ReddeningDriver` and the types that cross them (`StackSettings`, `StackRunResult`, `PipelineSteps`, `PlateSolveHeader`, `ReddeningEstimate`). `driver_set.py` (`Drivers`) fills empty slots with the built-in drivers.
- **Siril.** `siril_stacking_driver.py` (`SirilStackingDriver`), `siril_interface.py` (`ImageProcessing`), `siril_output_parsing.py`.
- **Catalogs and services.** `astrometry_net_driver.py` (`AstrometryNetPlateSolveDriver`), `gaia_cone_driver.py`, `catalog_store.py`, `gaia_xp_driver.py`, `astroquery_gaia_reddening_driver.py`, `astroquery_simbad_driver.py`.
- **Stores.** `calibration_library.py`, `camera_profile_store.py`, `provenance_store.py`, `local_database.py`, `catalog_access.py` (`CatalogAccess`).
- **Images.** `fits_access.py`, `image.py` (`AstrometricsImage`), `filter_detection.py`, `frame_prefetch.py`.

`catalog_access.py` runs no SQL itself. It uses the Butler, a keyed-record SQLite store shared with wayfindinglib and modeled on the Rubin data butler [9] (Section 4.8).

### 4.8 Shared storage backend

*Located in:* `astrometricslib/foundation/storage/`: `butler.py` (get, put, exists, merge), `local_database.py` (connections, JSON), `process_locks.py` (cross-process locks), `mount.py` (no writes under an unmounted frames mount).

### 4.9 Job framework

*Located in:* `astrometricslib/foundation/jobs/`, shared by both libraries, the backend and the MCP servers: `store.py` (`JobStore`, `DbLogHandler`), `runner.py` (`registered_job`, `capture_job_logs`, `background_job`, `run_as_background_job`, `close_interrupted_jobs`), `models.py` (`ProcessingJob`), `process_identity.py`.

## 5. Tuned parameters and validation status

Tables 4 to 8 list each pipeline's tuned values and the architecture section that explains each. Each status label (*Measured*, *Partly measured*, *Design estimate*; defined in the [register](./validation/index.md#3-status-labels)) links to its evidence entry or design-estimate table. A hidden link after each table names the code that holds each value, and a test checks it.

### 5.1 Stacking settings

**Table 4.** Tuned settings of the stacking pipeline and their validation status.

| Parameter | Default | Status | Architecture section |
| --- | --- | --- | --- |
| Floor on the high rejection cutoff | 2.5σ | Partly measured ([STK-08](validation/stacking.md#stk-08)); simulation only | §3.2 |
| Margin of the low cutoff above the high one | 0.5σ | Partly measured ([STK-08](validation/stacking.md#stk-08)); simulation only | §3.2 |
| Minimum frames per master calibration frame | 3 | Design estimate ([stacking table](validation/stacking.md#design-estimates)) | §3.2 |
| Gain and read noise measurement: fewest flat pairs; fewest bias pairs; temperature spread; region | 5; 3; 1 °C; central 1000 by 1000 px | Measured ([STK-35](validation/stacking.md#stk-35)) | §3.2 |
| Frame measurement: CPU time; fit grid cells | 60 s; 196 | Design estimate ([AST-17](validation/astrometry.md#ast-17)) | §2.1 |
| Frame screening: session gap | 4 h | Design estimate ([stacking table](validation/stacking.md#design-estimates)) | §3.4.1 |
| Frame screening: fewest frames to judge | 8 | Partly measured ([STK-01](validation/stacking.md#stk-01)) | §3.4.1 |
| Clouded frame star count; trailed frame roundness | below 0.5 of session median; below 0.80 and 0.15 under median | Partly measured ([STK-01](validation/stacking.md#stk-01)) | §3.4.1 |
| Roundness drop, color camera | at least 0.20 under median | Measured ([STK-02](validation/stacking.md#stk-02)) | §3.4.1 |
| Largest share of a session moved aside | 25% | Partly measured ([STK-03](validation/stacking.md#stk-03)) | §3.4.1 |
| Dark temperature slot width | 3 °C | Partly measured ([STK-06](validation/stacking.md#stk-06)) | §3.2 |
| Minimum frames kept after quality filtering | 5 | Partly measured ([STK-10](validation/stacking.md#stk-10)) | §3.3 (Architecture Table 3, step 3) and §3.4.1 |
| Brightness scale: fewest shared stars before pixel fallback | 16 | Measured ([STK-19](validation/stacking.md#stk-19)) | §3.2 |
| Pixel fallback: percentile range; bright-end fraction | 20th to 80th; 1% | Design estimate ([stacking table](validation/stacking.md#design-estimates)) | §3.2 |
| Linearity tolerance between exposure groups | 5% | Partly measured ([STK-20](validation/stacking.md#stk-20)) | §3.2 |
| Brightest sky allowed for an exposure group | 3 times reference | Partly measured ([STK-21](validation/stacking.md#stk-21)) | §3.2 |
| Noise penalty above which frames are noise-weighted | 1.05 | Design estimate ([stacking table](validation/stacking.md#design-estimates)); benefit in [STK-07](validation/stacking.md#stk-07) | §3.2 |
| Share of spectral frames that must register before fallback | 0.9 | Design estimate ([stacking table](validation/stacking.md#design-estimates)); counts in [STK-12](validation/stacking.md#stk-12) | §3.3 |
| Rejected pixel share that flags a stack | 15% | Partly measured ([STK-24](validation/stacking.md#stk-24)) | §3.4.2 |
| Star sharpness warning ratio (fails above) | 1.2 | Partly measured ([STK-25](validation/stacking.md#stk-25)) | §3.4.2 |

<!-- impl: astrometricslib.utilities.rejection_thresholds.rejection_bounds -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.FRAME_MEASUREMENT_TIME_BUDGET_SECONDS -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.MOFFAT_GRID_CELL_COUNT -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.MOFFAT_CANDIDATES_PER_CELL -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.MOFFAT_FIT_BOX_HALF_WIDTH_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.MOFFAT_ISOLATION_RADIUS_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.MOFFAT_SATURATION_MARGIN_FRACTION -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.MOFFAT_SUBPIXEL_FACTOR -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.MOFFAT_BETA_MINIMUM -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.MOFFAT_BETA_MAXIMUM -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.MOFFAT_BETA_START -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.MOFFAT_MINIMUM_R_SQUARED -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.MOFFAT_MAXIMUM_CENTER_SHIFT_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.MOFFAT_MINIMUM_FWHM_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.MOFFAT_MAXIMUM_FWHM_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.MOFFAT_MINIMUM_STAR_COUNT -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.MOMENT_APERTURE_FWHM_MULTIPLE -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.MOMENT_APERTURE_MINIMUM_RADIUS_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.BOOTSTRAP_RESAMPLE_COUNT -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.STAR_FINDER_EXPECTED_FWHM_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.STAR_FINDER_THRESHOLD_SIGMA -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.STAR_FINDER_BOX_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.frame_measurement_run.MEASUREMENT_WORKER_COUNT -->
<!-- impl: astrometricslib.pipelines.stacking.frame_measurement_run.BIAS_FRAMES_FOR_LEVEL -->
<!-- impl: astrometricslib.pipelines.stacking.frame_measurement_run.STACKING_STAGE_MEASUREMENT_LIMIT -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.MINIMUM_FLAT_PAIRS -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.MINIMUM_BIAS_PAIRS -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.MAXIMUM_TEMPERATURE_SPREAD_C -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.CENTRAL_REGION_SHAPE_PIXELS -->
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

### 5.2 Astrometry settings

**Table 5.** Tuned settings of the astrometry pipeline and their validation status.

| Parameter | Default | Status | Architecture section |
| --- | --- | --- | --- |
| Star detection threshold; sharpness and roundness ranges | 5σ; 0.2 to 1.0; ±0.6 | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.2 (step 1) |
| Minimum stars to attempt a solve | 4 | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.4.1 |
| Minimum stars for a local solve | 10 | Partly measured ([AST-13](validation/astrometry.md#ast-13)) | §4.4.1 |
| Pixel-scale window; further widening by the solver | ±5%; 20% | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.4.1 |
| Brightest stars sent to the online solver | 100 | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.2 (step 2) |
| Gaia refit: fewest matched stars | 40 | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.2 (step 6) |
| Gaia refit: largest field shift | 30 arcsec | Partly measured ([AST-02](validation/astrometry.md#ast-02)) | §4.2 (step 6) |
| Gaia refit: match radii; clip; passes; distortion order | 4 px and 1.5 px; 3σ; 5; second-order SIP | Partly measured ([AST-02](validation/astrometry.md#ast-02)); used, not varied | §4.2 (step 6) |
| Gaia query time limits; depth steps on a time-out | 45 s and 60 s; 2 steps of 1 mag | Partly measured ([AST-08](validation/astrometry.md#ast-08)) | §4.2 (step 4) |
| Gaia search depth: margin; limits; default | 1.0 mag; G 14 to 20; G 18 | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.2 (step 4) |
| Catalog match radius | 10 arcsec | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.2 (step 4) |
| Same-star position tolerance between catalogs | 2 arcsec | Measured ([AST-11](validation/astrometry.md#ast-11)) | §4.2 (step 4) |
| Radius for unresolved catalog companions | 3 arcsec | Partly measured ([AST-10](validation/astrometry.md#ast-10)) | §4.2 (step 4) |
| Variable-star catalog match radius | 3 arcsec | Partly measured ([AST-12](validation/astrometry.md#ast-12)) | §5.2 (concept 4) |
| Unsolved spectral frame center star: search radius; area; faintest peak | 5 arcmin; 0.10 of shorter side; 0.10 of brightest pixel | Partly measured ([AST-09](validation/astrometry.md#ast-09)) | §6.2 (concept 6) |
| Minimum catalog-matched stars | 20 | Partly measured ([AST-05](validation/astrometry.md#ast-05)) | §4.4.2 |
| Residual limit | 0.5 of a star width | Partly measured ([AST-04](validation/astrometry.md#ast-04)) | §4.4.2 |
| Failed catalog lookups allowed | under 50% | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.4.2 |

<!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SourceDetector -->
<!-- impl: astrometricslib.drivers.astrometry_net_driver.MINIMUM_SOURCES_FOR_LOCAL_SOLVE -->
<!-- impl: astrometricslib.pipelines.shared.catalog_star_identity.CATALOG_MATCH_RADIUS_ARCSEC -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.record_quality.MINIMUM_MATCHED_STARS -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.record_quality.MAXIMUM_RESIDUAL_FRACTION_OF_FWHM -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.record_quality.MAXIMUM_FAILED_LOOKUP_FRACTION -->
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

### 5.3 Photometry settings

**Table 6.** Tuned settings of the photometry pipeline and their validation status.

| Parameter | Default | Status | Architecture section |
| --- | --- | --- | --- |
| Aperture radius; sky ring radii | 4 px; 7 to 12 px | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.2 (concept 1) |
| Re-centering box; refinement passes; largest move | 11 px; 3; 1.5 px | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.2 (concept 1) |
| Comparison band: brightest skipped; band ends at | 2%; 30% of candidates | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.2 (concept 4) |
| Candidates vetted; minimum and cap of comparison stars | 100; 5 and 20 | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.2 (concept 4) |
| Vetting limit; share of typical stars for a usable frame | 3σ; one half | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.2 (concept 4) |
| Frame outlier clip; per-star outlier clip | 3σ (at least 5 frames); 5σ | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.2 (concept 4) |
| Share of frames rejected before the run is flagged | 25% | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.4.2 |
| Noise model: minimum stars; stars per bin; most bins | 20; 10; 10 | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.2 (concept 5) |
| Thresholds: excess scatter; chi-square and Stetson J | 1.5; field median + 2.326 robust σ | Partly measured ([PHO-06](validation/photometry.md#pho-06)); synthetic only | §5.2 (concept 5) |
| Small-field cutoff on the coefficient of variation | median + 7.4 MAD (about 5 robust σ), never below 0.02 | Measured ([PHO-10](validation/photometry.md#pho-10)) | §5.2 (concept 5) |
| Period verdicts: false-alarm limit for "possible"; for "detected"; shuffles | 0.05; 0.01 with at least 3 cycles; 300 | Partly measured ([PHO-12](validation/photometry.md#pho-12)); synthetic only | §5.2 (concept 8) |
| Between-session flag: significance; amplitude | above 3; at least 0.02 mag | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.2 (concept 6) |
| Discrimination AUC floor | 0.7 | Design estimate ([photometry table](validation/photometry.md#design-estimates)) | §5.4.2 |
| Largest acceptable smallest detectable change (peak to peak) | 0.3 mag | Design estimate ([photometry table](validation/photometry.md#design-estimates)); real-field value in [PHO-09](validation/photometry.md#pho-09) | §5.4.2 |

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
<!-- impl: astrometricslib.pipelines.photometry.post_processing.record_quality.MAXIMUM_DETECTABLE_AMPLITUDE_MAG -->
<!-- impl: astrometricslib.pipelines.photometry.post_processing.record_quality.MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.DEFAULT_VARIABILITY_SIGMA_THRESHOLD -->
<!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.FALSE_ALARM_FOR_DETECTION -->
<!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.verdict_from_false_alarm -->

### 5.4 Spectroscopy settings

**Table 7.** Tuned settings of the spectroscopy pipeline and their validation status.

| Parameter | Default | Status | Architecture section |
| --- | --- | --- | --- |
| Reading box half-width; width smoothing | 2.5 times the trail width; 61 steps | Design estimate ([spectroscopy table](validation/spectroscopy.md#design-estimates)); smoothing effect in [SPE-04](validation/spectroscopy.md#spe-04) | §6.2 (concept 2) |
| Minimum signal-to-noise for analyzing a spectrum | 1.5 | Partly measured ([SPE-10](validation/spectroscopy.md#spe-10)) | §6.2 (concept 5) |
| Line significance for the wavelength zero point | 6 | Partly measured ([SPE-11](validation/spectroscopy.md#spe-11)); synthetic only | §6.2 (concept 4) |
| Sky strip gap and width | 6 px; 10 px | Partly measured ([SPE-01](validation/spectroscopy.md#spe-01)) | §6.2 (concept 2) |
| Sky contamination limit | 3σ | Partly measured ([SPE-03](validation/spectroscopy.md#spe-03)); simulation only | §6.2 (concept 2) |
| Fewest pixels in a sky strip | 4 | Design estimate ([spectroscopy table](validation/spectroscopy.md#design-estimates)) | §6.2 (concept 2) |
| Telluric bands left out of classification | oxygen B 6860 to 6940 Å; oxygen A 7490 to 7720 Å; water 8100 to 8320 Å | Partly measured ([SPE-15](validation/spectroscopy.md#spe-15)) | §6.2 (concept 5) |
| Poor-match limit | 0.15 relative RMS | Partly measured ([SPE-16](validation/spectroscopy.md#spe-16)) | §6.2 (concept 5) |
| No-type limit | 0.45 relative RMS | Partly measured ([SPE-17](validation/spectroscopy.md#spe-17)) | §6.2 (concept 5) |
| Ambiguity gap | 0.02 relative RMS | Measured ([SPE-18](validation/spectroscopy.md#spe-18)) | §6.2 (concept 5) and §6.4.2 |
| Catalog disagreement | 20 subtype steps (two letter classes) | Partly measured ([SPE-19](validation/spectroscopy.md#spe-19)) | §6.4.2 |
| Wavelength-fit rejection tolerance | 10 nm RMS | Design estimate ([spectroscopy table](validation/spectroscopy.md#design-estimates)) | §6.2 (concept 3) and §6.4.2 |
| Wavelength-scale limit (scatter of corrected offsets) | 11 Å (one pixel) | Design estimate ([spectroscopy table](validation/spectroscopy.md#design-estimates)) | §6.4.2 |
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
<!-- impl: astrometricslib.pipelines.spectroscopy.post_processing.record_quality.PROCESSING_QUALITY_MAXIMUM_FAILED_FRACTION -->

### 5.5 Asteroid detection settings

**Table 8.** Tuned settings of the asteroid detection pipeline and their validation status.

| Parameter | Default | Status | Architecture section |
| --- | --- | --- | --- |
| Frames needed for a track | 3 | Design estimate ([asteroid detection table](validation/asteroid_detection.md#design-estimates)) | §7.2 (concept 2) |
| Rate range; chaining radius cap | 1 to 300 arcsec/h; 300 arcsec | Design estimate ([asteroid detection table](validation/asteroid_detection.md#design-estimates)) | §7.2 (concepts 2 and 4) |
| Stationary tolerances | 1.5 px; 3 arcsec | Design estimate ([asteroid detection table](validation/asteroid_detection.md#design-estimates)); effect on slow movers in [MOV-02](validation/asteroid_detection.md#mov-02) | §7.2 (concept 3) |
| Residual multiple; displacement multiple | 2; 3 times the position error | Partly measured ([MOV-03](validation/asteroid_detection.md#mov-03)); synthetic only | §7.2 (concept 4) |
| Assumed position error; centroid error | 10 arcsec; 0.5 px | Design estimate ([asteroid detection table](validation/asteroid_detection.md#design-estimates)) | §7.2 (concept 1) and §7.4.2 |
| Ephemeris match radius | 10 arcsec | Design estimate ([asteroid detection table](validation/asteroid_detection.md#design-estimates)); near one frame's position error ([MOV-06](validation/asteroid_detection.md#mov-06)) | §7.2 (concept 5) |
| Detection threshold; detection width | 5σ; 4 px | Partly measured ([MOV-01](validation/asteroid_detection.md#mov-01)); synthetic only | §7.4.1 |

<!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->

## 6. Validation tooling

Evidence is in the [validation register](./validation/index.md). This section maps behaviors the [validation sessions](./validation/index.md#6-validation-sessions) rely on to code.

- **Scripts** in `astrometricslib/scripts/`: `run_empirical_validation.py` (campaign through the public API), `run_all_target_processing.py` (batch), `seed_local_star_catalog.py`, `benchmark_siril_concurrency.py`, `backfill_focal_length.py`.
- **History fill.** `scripts/backfill_quality_measurements.py` copies saved summaries into the quality store. Record: [Section 6.4](./validation/index.md#64-history-fill-of-the-quality-store).
- **Per-session photometry.** `analyze_target(pipeline_type="photometry")` runs `VariabilityAnalyzer` once per `TargetSession` ([PHO-14](./validation/photometry.md#pho-14)).
- **Cross-session star matching** (Architecture §5.2, concept 6): `_match_and_merge_across_sessions` in `pipelines/photometry/batch.py`. It uses `search_around_sky`, backed by a KD-tree [10]. Photometry passes its own `StarIdentifier()` to `resolve_frame_wcs`, so SIMBAD is not queried per star. `sessions_missing_wcs` isolates a session that lacks a WCS.
- **Track linking.** `MovingObjectDetector._chain_detections_by_persistence` in `pipelines/asteroid_detection/detection.py` filters pairs with a vectorized RA/Dec box before computing separations. The search is still $O(N \times M)$ ([MOV-07](./validation/asteroid_detection.md#mov-07)).

## Appendix A. Ratchets and import contracts

A ratchet is a test whose list of known exceptions may only shrink. `test_no_new_upward_imports` in `astrometricslib/test/test_layer_boundaries.py` reads every import in production files, including those inside functions, and fails on a new upward one. Table 9 lists its rules.

**Table 9.** Layer rules enforced by the ratchet test.

| Files | May not import |
| --- | --- |
| `models/`, `foundation/`, `utilities/` | `drivers`, `pipelines`, `api`, `visualization`, the package root |
| `drivers/` | `pipelines`, `api`, `visualization`, the package root |
| `pipelines/shared/`, `pipelines/pipeline_base.py` | any specific pipeline, `pipelines.tasks`, `pipelines.target_batch`, `api`, the package root |
| `pipelines/<name>/` | `pipelines.tasks`, `pipelines.target_batch`, `api`, the package root |
| `pipelines/tasks.py`, `pipelines/target_batch.py` | `api`, the package root |

Not checked: `visualization/`, `api/`, the package root, test modules, and imports within one layer.

The same test file holds three more ratchets with shrink-only allowlists (a stale entry also fails):

- **Raw FITS access** (`KNOWN_FITS_ACCESS_SITES`). New code reads FITS through `drivers/fits_access.py` or `drivers/image.py`, which apply the HDU0/HDU1 rule.
- **Wall clock** (`KNOWN_WALL_CLOCK_SITES`). Nothing under `pipelines/` calls `datetime.now()` or `datetime.utcnow()`. A frame's time comes from its header.
- **Unseeded randomness** (`KNOWN_UNSEEDED_RANDOM_SITES`, empty). Nothing under `pipelines/` draws random numbers without a fixed seed.

The `[tool.importlinter]` contracts in `pyproject.toml` keep `astrometricslib` from importing `wayfindinglib`, `backend` or `mcp_servers`. Other packages may use only its public API, and no script imports another.

## References

<!-- vale off -->

[1] Astropy Collaboration, A. M. Price-Whelan, et al., "The Astropy Project: Sustaining and growing a community-oriented open-source project and the latest major release (v5.0) of the core package," *Astrophys. J.*, vol. 935, no. 2, p. 167, 2022, doi: 10.3847/1538-4357/ac7c74, bibcode: 2022ApJ...935..167A.

[2] Model Context Protocol, "Model Context Protocol specification, revision 2025-11-25." [Online]. Available: <https://modelcontextprotocol.io/specification/2025-11-25>

[3] D. Lang, D. W. Hogg, K. Mierle, M. Blanton, and S. Roweis, "Astrometry.net: Blind astrometric calibration of arbitrary astronomical images," *Astron. J.*, vol. 139, no. 5, pp. 1782–1800, 2010, doi: 10.1088/0004-6256/139/5/1782.

[4] F. Ochsenbein, P. Bauer, and J. Marcout, "The VizieR database of astronomical catalogues," *Astron. Astrophys. Suppl. Ser.*, vol. 143, pp. 23–32, 2000, doi: 10.1051/aas:2000169, bibcode: 2000A&AS..143...23O.

[5] A. Ginsburg et al., "astroquery: An astronomical web-querying package in Python," *Astron. J.*, vol. 157, no. 3, p. 98, 2019, doi: 10.3847/1538-3881/aafc33.

[6] M. Wenger et al., "The SIMBAD astronomical database," *Astron. Astrophys. Suppl. Ser.*, vol. 143, pp. 9–22, 2000, doi: 10.1051/aas:2000332, bibcode: 2000A&AS..143....9W.

[7] M. Servillat et al., "IVOA Provenance Data Model, version 1.0," International Virtual Observatory Alliance, IVOA Recommendation, Apr. 2020. [Online]. Available: <https://www.ivoa.net/documents/ProvenanceDM/>

[8] Minor Planet Center, "ADES data submission," MPC Public Documentation Hub. [Online]. Available: <https://docs.minorplanetcenter.net/mpc-ops-docs/observations/ades-format/>

[9] T. Jenness et al., "The Vera C. Rubin Observatory Data Butler and pipeline execution system," in *Proc. SPIE 12189, Software and Cyberinfrastructure for Astronomy VII*, 2022, p. 1218911, doi: 10.1117/12.2629569, arXiv: 2206.14941.

[10] J. L. Bentley, "Multidimensional binary search trees used for associative searching," *Commun. ACM*, vol. 18, no. 9, pp. 509–517, 1975, doi: 10.1145/361002.361007.

<!-- vale on -->
