# Astrometrics library implementation

*Version 1.8 · 2026-10-23 · Status: current*

## Abstract

This paper maps the design in `Astrometrics_Library_Architecture.md` to the Python source in `astrometricslib/`. It names the layers, the model classes, the modules of each of the five pipelines, and the scripts and tests that check them. It lists each pipeline's tuned values with a status label and a link to its evidence.

The [glossary](../Glossary.md) defines the terms, and this document defines each at first use.

## 1. Scope

**Statement of need.** Theory is in [Astrometrics_Library_Architecture.md](./Astrometrics_Library_Architecture.md). Evidence is in the [validation register](./validation/index.md). Module paths are relative to `astrometricslib/`; the [review guides](./pipelines/index.md) give stage detail.

The one supported entry point is the package root (`from astrometricslib import Astrometrics`). It re-exports the `api/` facade, the driver interfaces (Section 4.7), the job framework (Section 4.9) and the error types. The package builds on Astropy [1]; `wayfindinglib`, the backend and the MCP servers [2] use it.

## 2. Package layering

Each of the six layers may import only from the layers below it; the ratchet test in Appendix A enforces this.

**Table 1.** The six layers.

| Layer | Holds | Calls into | Key modules |
| --- | --- | --- | --- |
| 1. Public API | The `Astrometrics` facade and the root exports | Layers 2 to 5 | `api/`, `astrometricslib/__init__.py` |
| 2. Pipeline runners | Stage dispatcher; batch runner | Layer 3 | `pipelines/tasks.py`, `pipelines/target_batch.py` |
| 3. Pipelines | Stacking, astrometry, photometry, spectroscopy, asteroid detection | Layers 4 to 6; sideways between pipelines | `pipelines/<name>/` |
| 4. Shared pipeline code | Frame scanning, target records, image conversion, quality primitives, run recording | Layers 5 and 6 | `pipelines/shared/`, `pipelines/pipeline_base.py` |
| 5. Drivers | External tool wrappers, FITS access, SQLite stores | Layer 6 | `drivers/` |
| 6. Shared vocabulary | Models, configuration, logging, jobs, storage | No layer | `models/`, `foundation/`, `utilities/` |

`visualization/` sits outside the layers.

The pipelines delegate computation and catalog lookups to outside services: Siril for headless stacking, Astrometry.net [3] and Gaia through VizieR [4] or the ESA archive via astroquery [5] and SIMBAD [6] for plate solving and star identification, Gaia XP and DR3 for spectroscopy, and IMCCE SkyBoT for asteroid detection.

`process_target` (`api/processing.py`) runs the chosen stages in a fixed order through `pipelines.tasks.run_target_stages`, and each target of a list in its own process (`pipelines.target_batch.process_targets_in_parallel`).

## 3. Data models

Pydantic classes in `models/` hold fields and validation only. Table 2 maps Architecture §2's records to classes.

**Table 2.** Architecture records and their classes.

| Architecture record | Classes | Module |
| --- | --- | --- |
| Observation target (§2.1) | `Target`, `FrameRecord`, `ImageType` | `models/target.py` |
| Stellar object (§2.1) | `StellarObject`, with `PhotometryResult`, `PeriodogramResult`, `TransitCandidate`, `SpectroscopyResult` and others | `models/stellar_source.py`, `models/spectral_cross_checks.py` |
| Moving object candidate (§2.1) | `AsteroidDetectionCandidate`, `FrameDetection`, `MovingObjectTrack`, `EphemerisMatch` | `models/moving_object.py` |
| Quality record (§2.1) | `PipelineQualitySummaryBase`, a subclass per pipeline (such as `StackQualitySummary`), `GateResult`, `GateStatus` | `models/quality/quality_summary.py`, `models/quality/gate_result.py` |
| Camera profile (§2.3) | `CameraProfile` (schema 2), `SensorSettingNoise`, `ProvenancedValue`, `AppliedCameraProfile` | `models/camera_profile.py`, `models/applied_camera_profile.py` |
| Provenance (§2.3) | IVOA Provenance Data Model [7] classes such as `Agent` and `Entity` | `models/provenance.py` |

Camera profiles: `drivers/camera_profile_store.py`, `camera_profile_migration.py`, `pipelines/shared/applied_camera_profile.py`. Provenance: `pipelines/shared/provenance_recording.py`, `drivers/provenance_store.py`. Two `CameraProfile` properties give the saturation levels: `pixel_saturation_level_adu` (the threshold capped at the clip ceiling) counts saturated pixels, and `usable_peak_level_adu` (also capped at the linearity limit) decides whether a star is photometric ([STK-36](validation/stacking.md#stk-36)).

## 4. Pipelines

Paths are relative to the pipeline's folder unless they start with `astrometricslib/`.

### 4.1 Stacking (Architecture §3)

*Located in:* `astrometricslib/pipelines/stacking/`

- **Interfaces (§3.1).** `stack_request.py`, `stage.py` (`stack_frames`), `stack_runner.py`.
- **Calibration frames (§3.2).** `calibration_ingest.py`, `astrometricslib/drivers/calibration_library.py`, `pre_processing/flat_calibration.py`.
- **Gain and read noise (§3.2).** `CalibrationCatalog.measure_sensor_noise` (`astrometricslib/api/processing.py`) calls `sensor_noise_run.py` and `pre_processing/sensor_noise.py`. `save=True` writes the camera profile. The MCP server withholds `calibration:measure_sensor_noise`. `CameraProfile.sensor_noise_for` matches gain, offset and binning exactly; with `clipped_at_zero=False` it falls back to the entry of the same gain and binning at the nearest other offset (ties to the higher), and the source text says so. Frame measurement passes the flag from the frame's share of exact zeros against `CLIPPED_FRAME_ZERO_FRACTION`. <!-- impl: astrometricslib.models.camera_profile.CameraProfile.sensor_noise_for --> Evidence: [STK-35](validation/stacking.md#stk-35); the fallback is a design estimate ([table](validation/stacking.md#design-estimates)).
- **Rejection and filtering (§3.2).** `astrometricslib/utilities/rejection_thresholds.py`, `stack_filter_floor.py`.
- **Exposure groups and weights (§3.2).** `processing/exposure_groups.py` and its helpers; `pre_processing/exposure_weighting.py`. `fold_exposure_lengths` decides which group each exposure length joins. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.fold_exposure_lengths --> `combine_exposure_group_images` returns an `ExposureGroupCombination` with the applied gain of each group (stored as `stacking.group_gain_applied`), its sky level, weight and masks. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.ExposureGroupCombination --> `post_processing/stack_membership.group_frame_weights` gives frame i of group g the raw weight `W_g * w_i / sum(w)` with `W_g = N t^2 / s^2`, without the gain, and leaves a group's weights unknown, with a reason, in the five cases the stacking README lists. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_membership.group_frame_weights --> A group stack's reference frame is the identity line of its heaviest group (`registration_reference_from_diagnostics`); frames Siril dropped, and their all-zero lines, do not count. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_membership.registration_reference_from_diagnostics --> A group's frame noise `s_g` is the standard deviation of neighbor differences kept within `NOISE_CLIP_SIGMAS` rough (median absolute deviation) scales, so it is not stuck on a camera's storage steps. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.NOISE_CLIP_SIGMAS --> Empty apertures stay `UNCOVERED_CLEARANCE_PX` away from any group's uncovered border. <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.UNCOVERED_CLEARANCE_PX -->
- **Sequence and restacks (§3.3).** `pre_processing/stack_inputs.py`; `post_processing/previous_stack.py`, `stack_comparison.py`, `interrupted_jobs.py`.
- **Previews (§3.3).** `post_processing/stack_preview.py` and its helpers, `preview_remake.py`.
- **Frame measurement (§3.4).** `ProcessingPipelines.record_frame_measurements` (`frame_measurement_run.py`). Evidence: [AST-17](validation/astrometry.md#ast-17), [AST-18](validation/astrometry.md#ast-18).
- **Frame geometry (§3.4).** `post_processing/field_rotation.py` (`field_rotation_measurements`) stores frame rotations. `shared/registration_record.py` keeps `<stack>_registration_frames.json` for astrometry (§4.2) and the one registration reader (`is_already_registered`). `record_quality.FrameGeometryRows` keeps rotation and pointing rows apart. Evidence: [STK-43](validation/stacking.md#stk-43).
- **Input screening (§3.4.1).** `pre_processing/frame_quarantine.py`, `frame_homogeneity.py`, `background_homogeneity.py`, `camera_selection.py`, `assess_input_quality.py`.
- **Output checks (§3.4.2).** `post_processing/assess_output_quality.py`, `stack_quality.py`, `exposure_group_report.py`, `stack_quality_report.py`.
- **Stack measurements (§3.4).** `stage._record_stack_quality` calls `record_stacking_quality` (`post_processing/record_quality.py`). <!-- impl: astrometricslib.pipelines.stacking.post_processing.record_quality.record_stacking_quality --> When neither the stack header nor a named reference frame gives a plate solution, the noise step tries up to `MEMBER_FRAME_ATTEMPTS` member frames of the reference group (`reference_group_frame_paths`) and moves the solution by the shift the stack's brightest stars agree on. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.MEMBER_FRAME_ATTEMPTS --> The aperture set file keeps one set per camera, optic and reference exposure (`camera|telescope|focal|exp=<s>`); a key without the exposure is an old one, matches nothing and is dropped when a new set is stored. Subset and half-difference stacks use the deepest set of the camera and optic (`find_library_set_key`). The set's identity covers its apertures, reference stars and key. <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.find_library_set_key --> `stacking.frames_stacked_count` (definition version 2) counts the same frames as the member rows: the combined groups, less unreadable files and frames Siril left out (`stack_membership.count_frames_stacked`). <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_membership.count_frames_stacked --> `post_processing/normalization_scale.py` measures Siril's normalization scale from the reference stars (stack flux over registration reference frame flux, divided by `STACK_UNITS_PER_ADU * t_bar / t_ref`) and stores `stacking.normalization_scale`; the expected-noise rows (definition version 2) are multiplied by it. <!-- impl: astrometricslib.pipelines.stacking.post_processing.normalization_scale.EXPOSURE_MATCH_FRACTION --> Evidence: [STK-38](validation/stacking.md#stk-38). The noise and weights come from `post_processing/stack_noise.py`, `empty_apertures.py`, `difference_noise.py`, `stack_image.py`, `stack_membership.py`, `expected_noise.py`, `flat_noise_contribution.py`, `record_science.py`. Evidence: [STK-36](validation/stacking.md#stk-36), [STK-07](validation/stacking.md#stk-07), [STK-38](validation/stacking.md#stk-38), [STK-39](validation/stacking.md#stk-39), [STK-40](validation/stacking.md#stk-40), [STK-41](validation/stacking.md#stk-41).
- **Predicted noise of an exposure-group stack (§3.4).** `expected_noise.expected_exposure_group_noise` <!-- impl: astrometricslib.pipelines.stacking.post_processing.expected_noise.expected_exposure_group_noise --> gives frame `i` of group `g` the coefficient `W_g ŵ_i / (ΣW · G_g)` in counts per second, where `W_g` is the combine weight, `ŵ_i` the frame's share inside its group, `ΣW` the sum of the combine weights and `G_g` the gain the combine applied (1 when it could not measure one, which marks the rows assumed). The variance in counts per second squared is the sum over frames of the squared coefficient times the frame's variance over the group's squared exposure. The noise is the mean exposure per frame (weighted by frame count) times the unit scale times the square root of the aperture multiplier times that variance. The predicted sky is the heaviest group's sky per second times the same mean exposure. With one group, a gain of 1 and the mean exposure equal to the group's, this is the single-group formula. `record_science.compute_expected_noise` refuses (not measured) when `clipped_exposure_groups` is not empty, so a clipped single-group stack is refused too, on purpose, and gives the membership's reason when the frame weights are unknown. Register entry: [STK-38](validation/stacking.md#stk-38).
- **Subset stacks (§3.4).** `subset_noise_run.py`, `subset_stack.stack_frame_subset`. Evidence: [STK-37](validation/stacking.md#stk-37).
- **Half-difference noise (§3.4).** Job `measure_half_difference_noise` runs `subset_noise_run.run_half_difference_noise`, which calls `difference_noise.measure_difference_noise` and stores nine metrics. Each half needs `HALF_DIFFERENCE_MINIMUM_FRAMES_PER_HALF` frames. Evidence: [STK-42](validation/stacking.md#stk-42).
- **Half-stack pattern (§3.4).** `post_processing/half_pattern.py`, called by `subset_noise_run._half_pattern_safely`. It adds eight rows to the difference's nine; `read_stack_plane` reads each half once for `measure_difference_noise`. Evidence: [STK-45](validation/stacking.md#stk-45).
- **Flat-to-light match (§3.4).** `post_processing/flat_match.py` (`flat_match_rows`) stores ten rows. `drivers/calibration_library.py` selects the flats and biases (`select_flat_frames`, `select_bias_frames`), and the staging step records them under `flat_frame_paths` and `bias_frame_paths`. Config key `flat_match_time_limit_seconds`. Evidence: [STK-46](validation/stacking.md#stk-46).
- **Registration step and drift (§3.4).** `post_processing/registration_shifts.py`, called by `frame_geometry_measurements`. Evidence: [STK-44](validation/stacking.md#stk-44).

### 4.2 Astrometry (Architecture §4)

*Located in:* `astrometricslib/pipelines/astrometry/`

- **Interfaces (§4.1).** `pipeline.py`; `runner.py` (adapter).
- **Detection and centroids (§4.2).** `processing/star_identifier.py`, `pre_processing/source_detection.py`, `pre_processing/fwhm.py`.
- **Plate solving, Gaia and the refit (§4.2).** `processing/wcs_refinement.py`; `astrometricslib/drivers/astrometry_net_driver.py`, `gaia_cone_driver.py`, `catalog_store.py`; `utilities/catalog_seeding.py`.
- **WCS header (§4.3).** `astrometricslib/pipelines/shared/session_identification.py` (`write_wcs_to_fits_header`).
- **Checks (§4.4).** `post_processing/record_quality.py`, `assess_match_quality.py`.
- **Target clipping (§4.4).** `post_processing/target_clipping.py` (`target_clipping_measurements`). Evidence: [AST-19](validation/astrometry.md#ast-19).
- **Expected residual (§4.4).** `post_processing/expected_residual.py` stores four `astrometry.expected_*` metrics. Evidence: [AST-21](validation/astrometry.md#ast-21).
- **Target position.** `astrometricslib/pipelines/shared/target_position.py` takes a target's RA/Dec from its SIMBAD position (ICRS, epoch J2000), not from a frame center; `astrometricslib/scripts/audit_target_positions.py` checks the stored ones.

### 4.3 Photometry (Architecture §5)

*Located in:* `astrometricslib/pipelines/photometry/`

- **Interfaces (§5.1).** `runner.py` (adapter); `batch.py`.
- **Aperture photometry (§5.2).** `pre_processing/frame_photometry.py`, `detector_noise.py`, `observation_times.py`.
- **Comparison stars and light curves (§5.2).** `processing/comparison_ensemble.py`, `catalog_zero_point.py`, `variability_analyzer.py`, `variability_indices.py`.
- **Period search (§5.2).** `processing/periodicity_search.py`, `period_checks.py`, `family_wise_correction.py`.
- **Checks (§5.4).** `pre_processing/assess_input_quality.py`; `post_processing/record_quality.py`, `variability_skill.py`, `known_variability_labels.py`, `assess_output_quality.py`.
- **Run outcomes (§5.2).** `processing/noise_budget.py`, `observing_window.py`, `target_amplitude.py`, the matching `post_processing/record_*.py` steps, and `shared/variable_star_catalogs.py` (VSX range). Evidence: [PHO-17](validation/photometry.md#pho-17), [PHO-18](validation/photometry.md#pho-18), [PHO-19](validation/photometry.md#pho-19).

### 4.4 Spectroscopy (Architecture §6)

*Located in:* `astrometricslib/pipelines/spectroscopy/`

- **Interfaces (§6.1).** `pipeline.py`, `runner.py` (adapter), `record_and_flag_spectroscopy_stars.py`, `frame_analysis.py`, `batch.py`.
- **Star identification (§6.2).** `pre_processing/spectral_star_registration.py`.
- **Extraction (§6.2).** `pre_processing/spectrum_extractor.py`, `_extractor_c.c`, `neighbor_trail_deblending.py`, `neighbor_wing_correction.py`.
- **Wavelength calibration (§6.2).** `pre_processing/spectrum_calibrator.py`, `optics_physics.py`, `differential_refraction.py`, `wavelength_zero_point.py`.
- **Flux and resolution (§6.2).** `pre_processing/quantum_efficiency_correction.py`, `instrument_response.py`, `atmospheric_extinction.py`, `measured_line_spread.py`, `spectral_resolution.py`.
- **Classification and features (§6.2).** `processing/spectral_classifier.py`, `spectrum_analysis.py`, `star_reddening.py`, `interstellar_extinction.py`.
- **Stack registration quality (§6.3).** `utilities/registration_quality.py`.
- **Checks (§6.4).** `post_processing/record_quality.py`, `compare_to_gaia_xp.py`, `compare_to_catalog.py`, `wavelength_scale.py`.
- **Signal-to-noise and class gap (§6.2).** `processing/spectral_line_indices.py` (`line_window_signal_to_noise`) fills `snr_at_classifying_lines`; `processing/class_gap_calibration.py` and `data/class_gap_against_snr_zwo_asi_533mm_pro.json` hold the table. Evidence: [SPE-30](validation/spectroscopy.md#spe-30).

### 4.5 Asteroid detection (Architecture §7)

*Located in:* `astrometricslib/pipelines/asteroid_detection/`

- **Interfaces (§7.1).** `pipeline.py`; `runner.py` (adapter).
- **Detection (§7.2).** `detection.py` (blink analysis, tracking), `frame_wcs_composer.py` (per-frame WCS), `ephemeris.py` (IMCCE SkyBoT).
- **Checks (§7.4).** `record_quality.py`.
- **Unused.** `mpc_report.py` (MPC report formats [8]) and `rotation_period.py`.

### 4.6 Shared pipeline code

*Located in:* `astrometricslib/pipelines/shared/`

- **Frames and targets.** `frame_grouping.py`, `frame_scanning.py`, `target_records.py`, `target_sessions.py`.
- **Images, stars and state.** `image_conversions.py`, `star_recording.py`, `catalog_star_identity.py`, `session_identification.py`, `analysis_context.py`.
- **Recording.** `provenance_recording.py`, `applied_camera_profile.py`.
- **Quality primitives.** `quality/` (`saturation.py`, `raw_frame_check.py`, `frame_measurement.py`; the shared README lists the rest).

### 4.7 Drivers and pixel-level primitives

*Located in:* `astrometricslib/drivers/`

- **Interfaces.** `interfaces/` holds `StackingDriver`, `PlateSolveDriver`, `SimbadDriver`, `GaiaXpDriver`, `ReddeningDriver` and the types that cross them. `driver_set.py` (`Drivers`) fills empty slots with the built-in drivers.
- **Siril.** `siril_stacking_driver.py`, `siril_interface.py`.
- **Catalogs and services.** `astrometry_net_driver.py`, `gaia_cone_driver.py`, `catalog_store.py`, `gaia_xp_driver.py`, `astroquery_simbad_driver.py`.
- **Stores.** `calibration_library.py`, `camera_profile_store.py`, `provenance_store.py`, `catalog_access.py`.
- **Images.** `fits_access.py`, `image.py`. The drivers README lists the rest.

`catalog_access.py` uses the Butler (Section 4.8) [9], not SQL.

### 4.8 Shared storage backend

*Located in:* `astrometricslib/foundation/storage/`: `butler.py`, `local_database.py`, `process_locks.py`, `mount.py` (no writes under an unmounted frames mount).

### 4.9 Job framework

*Located in:* `astrometricslib/foundation/jobs/`, shared by both libraries, the backend and the MCP servers: `store.py`, `runner.py`, `models.py`, `process_identity.py`.

## 5. Tuned parameters and validation status

Tables 3 to 7 list each pipeline's tuned values and the section that explains it. Each status label (defined in the [register](./validation/index.md#3-status-labels)) links to its evidence. Hidden links after each table name each value's code.

### 5.1 Stacking settings

**Table 3.** Tuned settings of the stacking pipeline.

| Parameter | Default | Status | Architecture section |
| --- | --- | --- | --- |
| Rejection cutoffs: floor on the high one; margin of the low one above it | 2.5σ; 0.5σ | Partly measured ([STK-08](validation/stacking.md#stk-08)); simulation only | §3.2 |
| Minimum frames per master calibration frame | 3 | Design estimate ([table](validation/stacking.md#design-estimates)) | §3.2 |
| Gain and read noise measurement: fewest flat pairs; fewest bias pairs; temperature spread; region | 5; 3; 1 °C; central 1000 by 1000 px | Measured ([STK-35](validation/stacking.md#stk-35)) | §3.2 |
| Frame measurement: CPU time; fit grid cells | 60 s; 196 | Design estimate ([AST-17](validation/astrometry.md#ast-17)) | §3.4 |
| Pixel blur factor $c$; field grid and region cells per side | 2 ln 2 / 3; 14 and 4 | Design estimate ([AST-18](validation/astrometry.md#ast-18)) | §3.4 |
| Frame screening: session gap | 4 h | Design estimate ([table](validation/stacking.md#design-estimates)) | §3.4.1 |
| Frame screening: fewest frames to judge; clouded frame star count; trailed frame roundness | 8; below 0.5 of session median; below 0.80 and 0.15 under median | Partly measured ([STK-01](validation/stacking.md#stk-01)) | §3.4.1 |
| Roundness drop, color camera | at least 0.20 under median | Measured ([STK-02](validation/stacking.md#stk-02)) | §3.4.1 |
| Largest share of a session moved aside | 25% | Partly measured ([STK-03](validation/stacking.md#stk-03)) | §3.4.1 |
| Dark temperature slot width | 3 °C | Partly measured ([STK-06](validation/stacking.md#stk-06)) | §3.2 |
| Minimum frames kept after quality filtering | 5 | Partly measured ([STK-10](validation/stacking.md#stk-10)) | §3.3 (Table 3, step 3) and §3.4.1 |
| Brightness scale: fewest shared stars before pixel fallback | 16 | Measured ([STK-19](validation/stacking.md#stk-19)) | §3.2 |
| Pixel fallback: percentile range; bright-end fraction | 20th to 80th; 1% | Design estimate ([table](validation/stacking.md#design-estimates)) | §3.2 |
| Group frame noise: clip limit | 4 rough scales | Design estimate ([table](validation/stacking.md#design-estimates)) | §3.2 |
| Gain and read noise at another offset: frames allowed | under 20% exact zeros | Design estimate ([table](validation/stacking.md#design-estimates)) | §3.2 |
| Stack noise plate solution: member frames tried | 5 | Design estimate ([table](validation/stacking.md#design-estimates)) | §3.4 |
| Normalization scale: fewest unsaturated reference stars; reference exposure match | 5; 1% | Partly measured ([STK-38](validation/stacking.md#stk-38)) | §3.4 |
| Linearity tolerance between exposure groups | 5% | Partly measured ([STK-20](validation/stacking.md#stk-20)) | §3.2 |
| Brightest sky allowed for an exposure group | 3 times reference | Partly measured ([STK-21](validation/stacking.md#stk-21)) | §3.2 |
| Noise penalty above which frames are noise-weighted | 1.05 | Design estimate ([table](validation/stacking.md#design-estimates)); [STK-07](validation/stacking.md#stk-07) | §3.2 |
| Share of spectral frames that must register before fallback | 0.9 | Design estimate ([table](validation/stacking.md#design-estimates)); [STK-12](validation/stacking.md#stk-12) | §3.3 |
| Rejected pixel share that flags a stack | 15% | Partly measured ([STK-24](validation/stacking.md#stk-24)) | §3.4.2 |
| Star sharpness warning ratio (fails above) | 1.2 | Partly measured ([STK-25](validation/stacking.md#stk-25)) | §3.4.2 |
| Noise apertures off faint sources: threshold; smoothing widths; high-pass; clearance; fewest clean apertures; file version | 3.5σ; 1.5, 3, 6 px; 12 px; 13 px; 20; 3 | Partly measured ([STK-36](validation/stacking.md#stk-36)) | §3.4 |
| Half-difference noise: fewest frames per half | 8 | Design estimate (STK-D25) | §3.4 |
| Registration step: degenerate above; unmeasured above degenerate, identity shares; jump; fewest steps, frames; drift significance; axis agreement | 50 px; 10%, 50%; median + 5 x max(1.4826 MAD, 0.1 px); 5, 5; 3 errors; 0.5 | Partly measured ([STK-44](validation/stacking.md#stk-44)) | §3.4 |
| Half-stack pattern: filter; clearance; tile; resamples; lag; least extent | 3 px; 10 px; 64 px; 400; 6 px; 12 px | Partly measured ([STK-45](validation/stacking.md#stk-45)) | §3.4 |
| Flat-to-light match: cell; band pass; trend degree; flats; lights, fewest; sky; target disc; shadow limits; placement limit; search; time limit | 8 px; 2 and 30 cells; 4; 16; 24, 8; 10 times read noise; 25 cells; 5σ, 0.15%, 25 cells; 3.1 null spreads; ±160 px, ±3°; 300 s | Partly measured ([STK-46](validation/stacking.md#stk-46)) | §3.4 |

<!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.MAXIMUM_PLAUSIBLE_STEP_PX -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.MAXIMUM_DEGENERATE_SHARE -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.MAXIMUM_IDENTITY_SHARE -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.STEP_JUMP_SIGMAS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.MINIMUM_STEP_SCALE_PX -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.MINIMUM_STEPS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.MINIMUM_FRAMES_FOR_DRIFT_FIT -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.DRIFT_SIGNIFICANCE -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.registration_shifts.MINIMUM_AXIS_AGREEMENT -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.half_pattern.FINE_SCALE_SIGMA_PX -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.half_pattern.SOURCE_CLEARANCE_PX -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.half_pattern.TILE_PX -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.half_pattern.BOOTSTRAP_COUNT -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.half_pattern.WALKING_LAG_PX -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.half_pattern.MINIMUM_EXTENT_IN_LAGS -->

<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.MAP_CELL_PX -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.LOW_PASS_SIGMA_CELLS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.HIGH_PASS_SIGMA_CELLS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.TREND_DEGREE -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.MAXIMUM_FLAT_FRAMES -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.MAXIMUM_LIGHT_FRAMES -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.MINIMUM_LIGHT_FRAMES -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.SKY_READ_NOISE_FACTOR -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.TARGET_MASK_RADIUS_CELLS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.SHADOW_MINIMUM_SIGMAS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.SHADOW_MINIMUM_DEPTH_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.SHADOW_MINIMUM_AREA_CELLS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.PLACEMENT_LIMIT_SIGMAS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.MAXIMUM_SHIFT_CELLS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.COARSE_ANGLE_RANGE_DEG -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.DEFAULT_TIME_LIMIT_SECONDS -->
<!-- impl: astrometricslib.drivers.calibration_library.FLAT_FRAME_PATHS_KEY -->
<!-- impl: astrometricslib.drivers.calibration_library.BIAS_FRAME_PATHS_KEY -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.FAINT_SOURCE_THRESHOLD_SIGMAS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.FAINT_SOURCE_SMOOTHING_SIGMAS_PX -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.FAINT_SOURCE_HIGH_PASS_SIGMA_PX -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.FAINT_SOURCE_CLEARANCE_PX -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.MINIMUM_USABLE_APERTURES -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.APERTURE_FILE_VERSION -->
<!-- impl: astrometricslib.pipelines.stacking.subset_noise_run.HALF_DIFFERENCE_MINIMUM_FRAMES_PER_HALF -->
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
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.PIXEL_BLUR_FWHM_SQUARED_FACTOR -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.FIELD_GRID_CELLS_PER_SIDE -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_measurement.FIELD_REGION_CELLS_PER_SIDE -->
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

**Table 4.** Tuned settings of the astrometry pipeline.

| Parameter | Default | Status | Architecture section |
| --- | --- | --- | --- |
| Star detection threshold; sharpness and roundness ranges | 5σ; 0.2 to 1.0; ±0.6 | Design estimate ([table](validation/astrometry.md#design-estimates)) | §4.2 (step 1) |
| Minimum stars to attempt a solve | 4 | Design estimate ([table](validation/astrometry.md#design-estimates)) | §4.4.1 |
| Minimum stars for a local solve | 10 | Partly measured ([AST-13](validation/astrometry.md#ast-13)) | §4.4.1 |
| Pixel-scale window; further widening by the solver | ±5%; 20% | Design estimate ([table](validation/astrometry.md#design-estimates)) | §4.4.1 |
| Brightest stars sent to the online solver | 100 | Design estimate ([table](validation/astrometry.md#design-estimates)) | §4.2 (step 2) |
| Gaia refit: fewest matched stars | 40 | Design estimate ([table](validation/astrometry.md#design-estimates)) | §4.2 (step 6) |
| Gaia refit: largest field shift | 30 arcsec | Partly measured ([AST-02](validation/astrometry.md#ast-02)) | §4.2 (step 6) |
| Gaia refit: match radii; clip; passes; distortion order | 4 px and 1.5 px; 3σ; 5; second-order SIP | Partly measured ([AST-02](validation/astrometry.md#ast-02)) | §4.2 (step 6) |
| Gaia query time limits; depth steps on a time-out | 45 s and 60 s; 2 steps of 1 mag | Partly measured ([AST-08](validation/astrometry.md#ast-08)) | §4.2 (step 4) |
| Gaia search depth: margin; limits; default | 1.0 mag; G 14 to 20; G 18 | Design estimate ([table](validation/astrometry.md#design-estimates)) | §4.2 (step 4) |
| Catalog match radius | 10 arcsec | Design estimate ([table](validation/astrometry.md#design-estimates)) | §4.2 (step 4) |
| Same-star position tolerance between catalogs | 2 arcsec | Measured ([AST-11](validation/astrometry.md#ast-11)) | §4.2 (step 4) |
| Radius for unresolved catalog companions | 3 arcsec | Partly measured ([AST-10](validation/astrometry.md#ast-10)) | §4.2 (step 4) |
| Variable-star catalog match radius | 3 arcsec | Partly measured ([AST-12](validation/astrometry.md#ast-12)) | §5.2 (concept 4) |
| Unsolved spectral frame center star: search radius; area; faintest peak | 5 arcmin; 0.10 of shorter side; 0.10 of brightest pixel | Partly measured ([AST-09](validation/astrometry.md#ast-09)) | §6.2 (concept 6) |
| Minimum catalog-matched stars | 20 | Partly measured ([AST-05](validation/astrometry.md#ast-05)) | §4.4.2 |
| Residual limit | 0.5 of a star width | Partly measured ([AST-04](validation/astrometry.md#ast-04)) | §4.4.2 |
| Failed catalog lookups allowed | under 50% | Design estimate ([table](validation/astrometry.md#design-estimates)) | §4.4.2 |
| Target clipping: disc radius; frame cap; read time limit; least peak above sky | 30 arcsec; 200; 120 s; 5σ | Partly measured ([AST-19](validation/astrometry.md#ast-19)) | §4.4 |
| Expected residual: centroid factor; pixel-phase rms per axis at widths 1.5 to 5 px | √(2/π); 0.092 to 0.008 px (six widths) | Partly measured ([AST-21](validation/astrometry.md#ast-21)) | §4.4 |
| Target position tolerance: floor; extent factor | 15 arcmin; 1.0 of the object's radius | Partly measured ([AST-20](validation/astrometry.md#ast-20)) | §4.2 |
| Margin around the solved frame for a catalog position | 15 arcmin | Design estimate ([astrometry table](validation/astrometry.md#design-estimates)) | §4.2 |

<!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SourceDetector -->
<!-- impl: astrometricslib.drivers.astrometry_net_driver.MINIMUM_SOURCES_FOR_LOCAL_SOLVE -->
<!-- impl: astrometricslib.pipelines.shared.catalog_star_identity.CATALOG_MATCH_RADIUS_ARCSEC -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.record_quality.MINIMUM_MATCHED_STARS -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.record_quality.MAXIMUM_RESIDUAL_FRACTION_OF_FWHM -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.record_quality.MAXIMUM_FAILED_LOOKUP_FRACTION -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.target_clipping.TARGET_REGION_RADIUS_ARCSEC -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.target_clipping.MAXIMUM_FRAMES_READ -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.target_clipping.MAXIMUM_READ_SECONDS -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.target_clipping.MINIMUM_PEAK_SIGMA -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.expected_residual.CRAMER_RAO_PEAK_FACTOR -->
<!-- impl: astrometricslib.pipelines.astrometry.post_processing.expected_residual.PIXEL_PHASE_RMS_PX_BY_FWHM_PX -->
<!-- impl: astrometricslib.pipelines.shared.target_position.POSITION_TOLERANCE_FLOOR_ARCMIN -->
<!-- impl: astrometricslib.pipelines.shared.target_position.EXTENT_TOLERANCE_FACTOR -->
<!-- impl: astrometricslib.pipelines.shared.target_position.FOOTPRINT_MARGIN_ARCMIN -->
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

**Table 5.** Tuned settings of the photometry pipeline.

| Parameter | Default | Status | Architecture section |
| --- | --- | --- | --- |
| Aperture radius; sky ring radii; re-centering box; refinement passes; largest move | 4 px; 7 to 12 px; 11 px; 3; 1.5 px | Design estimate ([table](validation/photometry.md#design-estimates)) | §5.2 (concept 1) |
| Between-session flag: significance; amplitude | above 3; at least 0.02 mag | Design estimate ([table](validation/photometry.md#design-estimates)) | §5.2 (concept 6) |
| Comparison stars: brightest skipped; band ends at; candidates vetted; minimum and cap | 2%; 30%; 100; 5 and 20 | Design estimate ([table](validation/photometry.md#design-estimates)) | §5.2 (concept 4) |
| Vetting limit; usable-frame share; frame and per-star outlier clips | 3σ; one half; 3σ (at least 5 frames), 5σ | Design estimate ([table](validation/photometry.md#design-estimates)) | §5.2 (concept 4) |
| Noise model: minimum stars; stars per bin; most bins | 20; 10; 10 | Design estimate ([table](validation/photometry.md#design-estimates)) | §5.2 (concept 5) |
| Thresholds: excess scatter; chi-square and Stetson J | 1.5; field median + 2.326 robust σ | Partly measured ([PHO-06](validation/photometry.md#pho-06)); synthetic only | §5.2 (concept 5) |
| Small-field cutoff on the coefficient of variation | median + 7.4 MAD (about 5 robust σ), never below 0.02 | Measured ([PHO-10](validation/photometry.md#pho-10)) | §5.2 (concept 5) |
| Period verdicts: false-alarm limit for "possible"; for "detected"; shuffles | 0.05; 0.01 with at least 3 cycles; 300 | Partly measured ([PHO-12](validation/photometry.md#pho-12)); synthetic only | §5.2 (concept 8) |
| Zero point cuts: `CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION`; `MINIMUM_STARS_FOR_ZERO_POINT`; `MINIMUM_SIGNAL_TO_NOISE`; `SESSION_STAR_FRAME_SHARE`; linearity limit from the camera profile | 0.5; 10; 20; 80% | Design estimate (PHO-D24); partly measured ([PHO-15](validation/photometry.md#pho-15)) | §5.2 |
| Zero point extras: `MINIMUM_STARS_FOR_COLOR_SLOPE`; `MINIMUM_COLOR_RANGE_MAG`; `MINIMUM_FRAMES_FOR_SCATTER`; `STANDARD_ERROR_OF_MEDIAN_FACTOR` | 20; 0.4 mag; 10; 1.2533 | Design estimate (PHO-D25, PHO-D26) | §5.2 |
| Frames rejected before the run is flagged; discrimination AUC floor; largest acceptable smallest detectable change (peak to peak) | 25%; 0.7; 0.3 mag | Design estimate ([table](validation/photometry.md#design-estimates)); [PHO-09](validation/photometry.md#pho-09) | §5.4.2 |
| Scintillation: coefficient; airmass power; scale height | 0.09; 1.75; 8000 m | Design estimate (PHO-D27); partly measured ([PHO-17](validation/photometry.md#pho-17)) | §5.2 |
| Noise model magnitudes; alias peaks; highest alias frequency | G 10, 12, 14; 3; 2 per day | Design estimate (PHO-D29); partly measured ([PHO-17](validation/photometry.md#pho-17), [PHO-18](validation/photometry.md#pho-18)) | §5.2 |
| Target amplitude: phase bins; points per bin; filled share; target distance | 10; 3; 80%; 30 arcsec | Design estimate (PHO-D28, [PHO-19](validation/photometry.md#pho-19)) | §5.2 |

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
<!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION -->
<!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.MINIMUM_STARS_FOR_ZERO_POINT -->
<!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.MINIMUM_SIGNAL_TO_NOISE -->
<!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.SESSION_STAR_FRAME_SHARE -->
<!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.MINIMUM_STARS_FOR_COLOR_SLOPE -->
<!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.MINIMUM_COLOR_RANGE_MAG -->
<!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.MINIMUM_FRAMES_FOR_SCATTER -->
<!-- impl: astrometricslib.pipelines.photometry.processing.catalog_zero_point.STANDARD_ERROR_OF_MEDIAN_FACTOR -->
<!-- impl: astrometricslib.pipelines.photometry.processing.noise_budget.YOUNG_COEFFICIENT -->
<!-- impl: astrometricslib.pipelines.photometry.processing.noise_budget.YOUNG_AIRMASS_POWER -->
<!-- impl: astrometricslib.pipelines.photometry.processing.noise_budget.SCINTILLATION_SCALE_HEIGHT_M -->
<!-- impl: astrometricslib.pipelines.photometry.metrics.NOISE_MODEL_MAGNITUDES -->
<!-- impl: astrometricslib.pipelines.photometry.processing.observing_window.ALIAS_PEAK_COUNT -->
<!-- impl: astrometricslib.pipelines.photometry.processing.observing_window.MAXIMUM_ALIAS_FREQUENCY_PER_DAY -->
<!-- impl: astrometricslib.pipelines.photometry.processing.target_amplitude.PHASE_BIN_COUNT -->
<!-- impl: astrometricslib.pipelines.photometry.processing.target_amplitude.MINIMUM_POINTS_PER_BIN -->
<!-- impl: astrometricslib.pipelines.photometry.processing.target_amplitude.MINIMUM_FILLED_BIN_FRACTION -->
<!-- impl: astrometricslib.pipelines.photometry.processing.target_amplitude.MAXIMUM_TARGET_DISTANCE_ARCSEC -->
<!-- impl: astrometricslib.pipelines.photometry.post_processing.variability_skill.MINIMUM_DISCRIMINATION_AUC -->
<!-- impl: astrometricslib.pipelines.photometry.post_processing.record_quality.MAXIMUM_DETECTABLE_AMPLITUDE_MAG -->
<!-- impl: astrometricslib.pipelines.photometry.post_processing.record_quality.MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG -->
<!-- impl: astrometricslib.pipelines.photometry.processing.variability_analyzer.DEFAULT_VARIABILITY_SIGMA_THRESHOLD -->
<!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.FALSE_ALARM_FOR_DETECTION -->
<!-- impl: astrometricslib.pipelines.photometry.processing.periodicity_search.verdict_from_false_alarm -->

### 5.4 Spectroscopy settings

**Table 6.** Tuned settings of the spectroscopy pipeline.

| Parameter | Default | Status | Architecture section |
| --- | --- | --- | --- |
| Reading box half-width; width smoothing | 2.5 times the trail width; 61 steps | Design estimate ([table](validation/spectroscopy.md#design-estimates)); [SPE-04](validation/spectroscopy.md#spe-04) | §6.2 (concept 2) |
| Minimum signal-to-noise for analyzing a spectrum | 1.5 | Partly measured ([SPE-10](validation/spectroscopy.md#spe-10)) | §6.2 (concept 5) |
| Line significance for the wavelength zero point | 6 | Partly measured ([SPE-11](validation/spectroscopy.md#spe-11)) | §6.2 (concept 4) |
| Sky strip gap and width | 6 px; 10 px | Partly measured ([SPE-01](validation/spectroscopy.md#spe-01)) | §6.2 (concept 2) |
| Sky contamination limit | 3σ | Partly measured ([SPE-03](validation/spectroscopy.md#spe-03)); simulation only | §6.2 (concept 2) |
| Fewest pixels in a sky strip | 4 | Design estimate ([table](validation/spectroscopy.md#design-estimates)) | §6.2 (concept 2) |
| Telluric bands left out of classification | oxygen B 6860 to 6940 Å; oxygen A 7490 to 7720 Å; water 8100 to 8320 Å | Partly measured ([SPE-15](validation/spectroscopy.md#spe-15)) | §6.2 (concept 5) |
| Poor-match limit | 0.15 relative RMS | Partly measured ([SPE-16](validation/spectroscopy.md#spe-16)) | §6.2 (concept 5) |
| No-type limit | 0.45 relative RMS | Partly measured ([SPE-17](validation/spectroscopy.md#spe-17)) | §6.2 (concept 5) |
| Ambiguity gap | 0.02 relative RMS | Measured ([SPE-18](validation/spectroscopy.md#spe-18)) | §6.2 (concept 5) and §6.4.2 |
| Catalog disagreement | 20 subtype steps (two letter classes) | Partly measured ([SPE-19](validation/spectroscopy.md#spe-19)) | §6.4.2 |
| Wavelength-fit rejection tolerance | 10 nm RMS | Design estimate ([table](validation/spectroscopy.md#design-estimates)) | §6.2 (concept 3) and §6.4.2 |
| Wavelength-scale limit (scatter of corrected offsets) | 11 Å (one pixel) | Design estimate ([table](validation/spectroscopy.md#design-estimates)) | §6.4.2 |
| Gaia XP agreement: largest median tilt | 3% per 1000 Å | Design estimate ([table](validation/spectroscopy.md#design-estimates)) | §6.4.2 |
| Processing quality: largest share of spectra failing a metric | 50% | Design estimate ([table](validation/spectroscopy.md#design-estimates)) | §6.4.2 |
| Class gap table: share counted as reached; signal-to-noise grid; noise draws; field resamples | 50%; 3 to 30; 5; 400 | Design estimate (SPE-D25); partly measured ([SPE-30](validation/spectroscopy.md#spe-30)) | §6.2 |

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
<!-- impl: astrometricslib.pipelines.spectroscopy.processing.class_gap_calibration.REACHED_SHARE -->
<!-- impl: astrometricslib.pipelines.spectroscopy.processing.class_gap_calibration.DEFAULT_SNR_GRID -->

### 5.5 Asteroid detection settings

**Table 7.** Tuned settings of the asteroid detection pipeline.

| Parameter | Default | Status | Architecture section |
| --- | --- | --- | --- |
| Frames needed for a track | 3 | Design estimate ([table](validation/asteroid_detection.md#design-estimates)) | §7.2 (concept 2) |
| Rate range; chaining radius cap | 1 to 300 arcsec/h; 300 arcsec | Design estimate ([table](validation/asteroid_detection.md#design-estimates)) | §7.2 (concepts 2 and 4) |
| Stationary tolerances | 1.5 px; 3 arcsec | Design estimate ([table](validation/asteroid_detection.md#design-estimates)); [MOV-02](validation/asteroid_detection.md#mov-02) | §7.2 (concept 3) |
| Residual multiple; displacement multiple | 2; 3 times the position error | Partly measured ([MOV-03](validation/asteroid_detection.md#mov-03)); synthetic only | §7.2 (concept 4) |
| Assumed position error; centroid error | 10 arcsec; 0.5 px | Design estimate ([table](validation/asteroid_detection.md#design-estimates)) | §7.2 (concept 1) and §7.4.2 |
| Ephemeris match radius | 10 arcsec | Design estimate ([table](validation/asteroid_detection.md#design-estimates)); [MOV-06](validation/asteroid_detection.md#mov-06) | §7.2 (concept 5) |
| Detection threshold; detection width | 5σ; 4 px | Partly measured ([MOV-01](validation/asteroid_detection.md#mov-01)); synthetic only | §7.4.1 |

<!-- impl: astrometricslib.models.moving_object_config.MovingObjectConfig -->

## 6. Validation tooling

This section maps behaviors the [validation sessions](./validation/index.md#6-validation-sessions) rely on to code.

- **Scripts** in `astrometricslib/scripts/`: `run_empirical_validation.py` (campaign), `run_all_target_processing.py` (batch), `seed_local_star_catalog.py`, `benchmark_siril_concurrency.py`, `backfill_focal_length.py`, `calibrate_class_gap_against_snr.py` (class gap table, [SPE-30](./validation/spectroscopy.md#spe-30)), `backfill_variable_star_flags.py` (with `--refresh-vsx-ranges`, [PHO-19](./validation/photometry.md#pho-19)).
- **History fill.** `scripts/backfill_quality_measurements.py`; record: [Section 6.4](./validation/index.md#64-history-fill-of-the-quality-store).
- **Calibration identities.** `QualityStore.write_calibration_identities` (`drivers/quality_store.py`) stores one row per library stack in the side table `quality_calibration_identity` (schema version 3). The key is `run_id` and `subject_id`. The columns are `target_id`, `camera`, `optic`, `focal_length_mm`, `flat_masters`, `bias_masters` and `dark_masters` (each a list of path, size and modification time), `siril_version`, `stacking_algorithm_version` (`STACKING_ALGORITHM_VERSION`) and `recorded_at`. A second write for the same key is ignored. `QualityRecords.calibration_identities(run_ids, target_id)` reads it, and the MCP tool `quality_calibration_identities` offers it read-only. The migration from version 2 only creates the table; run `scripts/backup_library_databases.py` before the first write to a real library. Each library stacking run writes its record through `record_calibration_identities` (`pipelines/stacking/post_processing/calibration_record.py`), called from `record_stacking_quality`: the files are the flat, bias and dark frames the run staged for Siril (`FLAT_FRAME_PATHS_KEY`, `BIAS_FRAME_PATHS_KEY`, `DARK_FRAME_PATHS_KEY` in the run's diagnostics, joined across exposure groups by `_merge_diagnostics`), not every slot the frames could use, and the Siril version comes from `cached_siril_version`.
- **Per-session photometry.** `analyze_target(pipeline_type="photometry")` runs `VariabilityAnalyzer` per `TargetSession` ([PHO-14](./validation/photometry.md#pho-14)).
- **Cross-session star matching** (Architecture §5.2, concept 6): `_match_and_merge_across_sessions` (`pipelines/photometry/batch.py`) uses `search_around_sky` (a KD-tree [10]); `sessions_missing_wcs` isolates a session without a WCS.
- **Track linking.** `MovingObjectDetector._chain_detections_by_persistence` in `pipelines/asteroid_detection/detection.py` pre-filters pairs with an RA/Dec box; the search is still $O(N \times M)$ ([MOV-07](./validation/asteroid_detection.md#mov-07)).

## Appendix A. Ratchets and import contracts

A ratchet is a test whose exception list may only shrink. `test_no_new_upward_imports` in `astrometricslib/test/test_layer_boundaries.py` fails on a new upward import, even inside a function. Table 8 lists its rules.

**Table 8.** Layer rules enforced by the ratchet test.

| Files | May not import |
| --- | --- |
| `models/`, `foundation/`, `utilities/` | `drivers`, `pipelines`, `api`, `visualization`, the package root |
| `drivers/` | `pipelines`, `api`, `visualization`, the package root |
| `pipelines/shared/`, `pipelines/pipeline_base.py` | any specific pipeline, `pipelines.tasks`, `pipelines.target_batch`, `api`, the package root |
| `pipelines/<name>/` | `pipelines.tasks`, `pipelines.target_batch`, `api`, the package root |
| `pipelines/tasks.py`, `pipelines/target_batch.py` | `api`, the package root |

Not checked: `visualization/`, `api/`, the package root, tests, and imports within one layer.

Three more ratchets (a stale entry fails):

- **Raw FITS access** (`KNOWN_FITS_ACCESS_SITES`). New code reads FITS through `drivers/fits_access.py` or `drivers/image.py`.
- **Wall clock** (`KNOWN_WALL_CLOCK_SITES`). Nothing under `pipelines/` calls `datetime.now()` or `datetime.utcnow()`.
- **Unseeded randomness** (`KNOWN_UNSEEDED_RANDOM_SITES`, empty). Nothing under `pipelines/` draws random numbers without a fixed seed.

The `pyproject.toml` import contracts keep `astrometricslib` from importing `wayfindinglib`, `backend` or `mcp_servers`.

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
