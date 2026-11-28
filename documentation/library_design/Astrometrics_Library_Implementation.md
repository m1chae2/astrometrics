# Astrometrics library implementation

*Version 2.1 · 2026-11-25 · Status: current*

## Abstract

This paper maps the design in `Astrometrics_Library_Architecture.md` to `astrometricslib/`, naming code, tests and tuned values with status labels.

## 1. Scope

**Statement of need.** The [glossary](../Glossary.md) defines the terms. Theory: [Astrometrics_Library_Architecture.md](Astrometrics_Library_Architecture.md). Evidence: [validation register](validation/index.md). Paths are relative to `astrometricslib/`; [review guides](pipelines/index.md) detail stages.

The entry is the package root (`from astrometricslib import Astrometrics`). Besides 16 root names, `exceptions.py`, `reports.py`, `jobs.py` and `drivers/` are public. It uses Astropy [1]; `wayfindinglib`, the backend and MCP servers [2] use it.

## 2. Package layering

Each layer imports only from layers below (Appendix A ratchet).

**Table 1.** The six layers.

| Layer | Holds | Calls into | Key modules |
| --- | --- | --- | --- |
| 1. Public API | The `Astrometrics` facade, public modules | Layers 2 to 5 | `api/`, `__init__.py`, `exceptions.py`, `reports.py`, `jobs.py`, `drivers/` |
| 2. Pipeline runners | Stage dispatcher; batch runner | Layer 3 | `pipelines/tasks.py`, `pipelines/target_batch.py` |
| 3. Pipelines | Stacking, astrometry, photometry, spectroscopy, asteroid detection | Layers 4 to 6; sideways between pipelines | `pipelines/<name>/` |
| 4. Shared pipeline code | Frame scanning, target records, image conversion, quality primitives, run recording | Layers 5 and 6 | `pipelines/shared/`, `pipelines/pipeline_base.py` |
| 5. Drivers | External tool wrappers, FITS access, SQLite stores | Layer 6 | `_internal/drivers/` |
| 6. Shared vocabulary | Models, configuration, logging, jobs, storage | No layer | `models/`, `foundation/`, `utilities/` |

`visualization/` is outside the layers.

Outside services: Siril (stacking), Astrometry.net [3], Gaia through VizieR [4] or the ESA archive via astroquery [5], and SIMBAD [6] (plate solving, star identification), Gaia XP and DR3 (spectroscopy), and IMCCE SkyBoT (asteroid detection).

Each `processing.<pipeline>.run` (`pipelines/pipeline_runs.py`) runs its stage through `pipelines.tasks.run_target_stages`, adding prerequisites per `STAGE_PREREQUISITES` (never astrometry; see `missing_astrometry_reason`); scripts batch targets with `pipelines.target_batch.process_targets_in_parallel`. <!-- impl: astrometricslib.pipelines.tasks.missing_astrometry_reason -->

## 3. Data models

`models/` holds only fields and validation (Table 2 maps Architecture §2).

**Table 2.** Architecture records and their classes.

| Architecture record | Classes | Module |
| --- | --- | --- |
| Observation target (§2.1) | `Target`, `FrameRecord`, `ImageType` | `models/target.py` |
| Stellar object (§2.1) | `StellarObject`, with `PhotometryResult`, `PeriodogramResult`, `TransitCandidate`, `SpectroscopyResult` | `models/stellar_source.py`, `models/spectral_cross_checks.py` |
| Moving object candidate (§2.1) | `AsteroidDetectionCandidate`, `FrameDetection`, `MovingObjectTrack`, `EphemerisMatch` | `models/moving_object.py` |
| Quality record (§2.1) | `PipelineQualitySummaryBase`, a subclass per pipeline (e.g. `StackQualitySummary`), `GateResult`, `GateStatus` | `models/quality/quality_summary.py`, `models/quality/gate_result.py` |
| Camera profile (§2.3) | `CameraProfile` (schema 2), `SensorSettingNoise`, `ProvenancedValue`, `AppliedCameraProfile` | `models/camera_profile.py`, `models/applied_camera_profile.py` |
| Provenance (§2.3) | IVOA Provenance Data Model [7] classes (`Agent`, `Entity`) | `models/provenance.py` |

Camera profiles: `_internal/drivers/camera_profile_store.py`, `camera_profile_migration.py`, `pipelines/shared/applied_camera_profile.py`. Provenance: `pipelines/shared/provenance_recording.py`, `_internal/drivers/provenance_store.py`. `CameraProfile` gives two saturation levels: `pixel_saturation_level_adu` (capped at the clip ceiling) counts saturated pixels; `usable_peak_level_adu` (also capped at the linearity limit) decides if a star is photometric ([STK-36](validation/stacking.md#stk-36)).

## 4. Pipelines

Paths are relative to the pipeline's folder unless they start `astrometricslib/`.

### 4.1 Stacking (§3)

*Code:* `astrometricslib/pipelines/stacking/`

- **Interfaces (§3.1).** `stack_request.py`, `stage.py` (`stack_frames`), `stack_runner.py`.
- **Calibration frames (§3.2).** `calibration_ingest.py`, `astrometricslib/_internal/drivers/calibration_library.py`, `pre_processing/flat_calibration.py`.
- **Gain and read noise (§3.2).** `CalibrationCatalog.measure_sensor_noise` calls `sensor_noise_run.py` and `pre_processing/sensor_noise.py`. `CameraProfile.sensor_noise_for` matches gain, offset and binning exactly; with `clipped_at_zero=False` (`CLIPPED_FRAME_ZERO_FRACTION` in `shared/quality/clipped_frames.py`, shared with photometry and spectroscopy) it uses the nearest offset at that gain and binning, ties higher. <!-- impl: astrometricslib.models.camera_profile.CameraProfile.sensor_noise_for --> [STK-35](validation/stacking.md#stk-35); the fallback is a design estimate ([table](validation/stacking.md#design-estimates)).
- **Flat bias.** `staging._select_flat_bias_frames` ([STK-57](validation/stacking.md#stk-57)).
- **Flat clipping (§3.4.1).** `assess_flats` leaves out flats clipped in any channel (`find_clipped_channels`; `stacking.flat_clipped_share_limit` 0.001, STK-D18); master recipe 7. <!-- impl: astrometricslib.pipelines.stacking.pre_processing.flat_calibration.assess_flats --> <!-- impl: astrometricslib.pipelines.stacking.pre_processing.flat_calibration.find_clipped_channels --> <!-- impl: astrometricslib._internal.drivers.siril.stacking.staging.FLAT_MASTER_RECIPE -->
- **Stack prediction (§3.3).** `stacking.predict` runs `screen_frames` (`dry_run=True`), splits with `split_into_stack_batches`, picks calibration with `choose_batch_calibration` and `choose_flat_bias`.
- **Master darks.** Median-combined (`stack dark med -nonorm`); bias and flat masters keep sigma rejection (STK-D02, STK-D76).
- **Dark defects (§3.4.1).** `dark_defect_run.py`, `pre_processing/dark_defect_judgement.py`; `choose_batch_calibration` skips them (STK-D77). <!-- impl: astrometricslib.pipelines.stacking.dark_defect_run.measure_dark_defects_in_library --> <!-- impl: astrometricslib.pipelines.stacking.pre_processing.dark_defect_judgement.judge_dark_defects --> <!-- impl: astrometricslib.pipelines.shared.quality.dark_defect_measurement.measure_dark_defects -->
- **Rejection and filtering (§3.2).** `astrometricslib/utilities/rejection_thresholds.py`, `stack_filter_floor.py`.
- **Exposure groups and weights (§3.2).** `processing/exposure_groups.py` and its `group_*.py` helpers; `pre_processing/exposure_weighting.py`. `plan_exposure_folding` assigns each length a group; `_drop_unfoldable_exposures` leaves out frames beyond `MAXIMUM_EXPOSURE_FOLD_RATIO` 2 (STK-D79). `background_homogeneity` buckets by exact exposure. <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.plan_exposure_folding --> <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.fold_exposure_lengths --> <!-- impl: astrometricslib.pipelines.stacking.processing.exposure_groups.MAXIMUM_EXPOSURE_FOLD_RATIO --> `combine_exposure_group_images` returns an `ExposureGroupCombination`: each group's applied gain (`stacking.group_gain_applied`), sky level, weight and masks. <!-- impl: astrometricslib.pipelines.stacking.processing.group_combination.ExposureGroupCombination --> `post_processing/stack_membership.group_frame_weights` gives frame i of group g the raw weight `W_g * w_i / sum(w)` without the gain, and leaves weights unknown, with a reason, in the README's cases. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_membership.group_frame_weights --> Siril scales frame `i` of a folded group to its reference by `k_i = t_ref / t_i` (`group_exposure_scales`; design estimate). <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_membership.group_exposure_scales --> `group_references` finds the reference frame; `stacking.group_reference_exposure_s` stores its exposure. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_membership.group_references --> A group stack's reference is the identity line of its heaviest group (`registration_reference_from_diagnostics`). <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_membership.registration_reference_from_diagnostics --> A group's frame noise `s_g` clips neighbor differences at `NOISE_CLIP_SIGMAS`. <!-- impl: astrometricslib.pipelines.stacking.processing.group_gains.NOISE_CLIP_SIGMAS -->
- **Image alignment (§3.2).** `processing/image_registration.py`: `fit_transform`, `warp` (STK-D85). <!-- impl: astrometricslib.pipelines.stacking.processing.image_registration.fit_transform --> <!-- impl: astrometricslib.pipelines.stacking.processing.image_registration.warp --> <!-- impl: astrometricslib.pipelines.stacking.processing.image_registration.POLYNOMIAL_MINIMUM_PAIRS_PER_QUADRANT -->
- **Sequence and restacks (§3.3).** `pre_processing/stack_inputs.py`; `post_processing/previous_stack.py`, `stack_comparison.py`, `interrupted_jobs.py`. Imaging registration uses `seqapplyreg -interp=la -noclamp` (`IMAGING_REGISTRATION_INTERPOLATION_OPTIONS`); partly measured ([STK-25](validation/stacking.md#stk-25)). <!-- impl: astrometricslib._internal.drivers.siril.stacking.script_builders.IMAGING_REGISTRATION_INTERPOLATION_OPTIONS -->
- **Previews (§3.3).** `post_processing/stack_preview.py`, `preview_remake.py`. `masked_stretch.py` (`MASKED_STRETCH_STAR_PEAK_LIMIT` 0.35, `MASKED_STRETCH_KNEE` 0.4). `Target.preview_bright_object` can disable it. `sun_like_balance.py`. Design estimate (STK-D75, STK-D78, STK-D81, STK-D86). <!-- impl: astrometricslib.pipelines.stacking.post_processing.masked_stretch.MASKED_STRETCH_STAR_PEAK_LIMIT --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.masked_stretch.MASKED_STRETCH_KNEE --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.stretch_sky.CLIPPED_SHARE_LIMIT --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.sun_like_balance.SUN_LIKE_MAXIMUM_CORRECTION -->
- **Color from another stack (§3.3.1).** `post_processing/color_from_other_stack.py` (`make_color_preview_for_target`), `other_stack_alignment.py` (`align_with_stars`), `star_color_measurement.py` (`measure_star_colors`). <!-- impl: astrometricslib.pipelines.stacking.post_processing.color_from_other_stack.make_color_preview_for_target --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.color_from_other_stack.choose_color_stack --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.color_from_other_stack.write_color_preview --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.other_stack_alignment.align_with_stars --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.star_color_measurement.measure_star_colors --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.color_from_other_stack.COLOR_PREVIEW_MAXIMUM_ALIGNMENT_RMS_PIXELS --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.color_from_other_stack.COLOR_PREVIEW_MINIMUM_COVERAGE --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.color_from_other_stack.COLOR_PREVIEW_COLOR_FADE --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.star_color_measurement.STAR_COLOR_INDEX_ERROR_LIMIT -->
- **Measurement (§3.4).** `QualityFrames.record` (`frame_measurement_run.py`). [AST-17](validation/astrometry.md#ast-17), [AST-18](validation/astrometry.md#ast-18).
- **Geometry (§3.4).** `post_processing/field_rotation.py` (`field_rotation_measurements`) stores rotations. `shared/registration_record.py` keeps `<stack>_registration_frames.json` for astrometry (§4.2) and its reader (`is_already_registered`). `record_quality.FrameGeometryRows` separates rotation and pointing rows. [STK-43](validation/stacking.md#stk-43).
- **Input screening (§3.4.1).** `pre_processing/frame_quarantine.py`, `frame_homogeneity.py`, `background_homogeneity.py`, `shared/camera_selection.py`/`shared/optic_selection.py`, `assess_input_quality.py`.
- **Calibration checks (§3.4.1).** Limits: `ZERO_PIXEL_FRACTION_LIMIT` 0.001, `VALUE_STEP_ALLOWANCE` 2, `NOISE_RATIO_LOW` and `NOISE_RATIO_HIGH` 0.9 and 1.1, `CEILING_SHARE_LIMIT` 0.001, `DARK_CURRENT_ALLOWANCE_ADU_PER_SECOND` 0.002 and `THIN_SET_FRAME_COUNT` 10. [STK-47](validation/stacking.md#stk-47) to [STK-56](validation/stacking.md#stk-56); STK-D36. <!-- impl: astrometricslib.pipelines.shared.quality.calibration_frame_measurement.measure_calibration_frame --> <!-- impl: astrometricslib.pipelines.stacking.calibration_measurement_run.measure_calibration_frames --> <!-- impl: astrometricslib.pipelines.stacking.pre_processing.calibration_assessment.assess_dark_or_bias_frames --> <!-- impl: astrometricslib.pipelines.stacking.pre_processing.stack_calibration_health.assess_stack_calibration -->
- **Output checks (§3.4.2).** `post_processing/assess_output_quality.py`, `stack_quality.py`, `exposure_group_report.py`, `stack_quality_report.py`. Eq. 5: `fwhm_sampling.measure_fwhm_degradation` (definition version 2). <!-- impl: astrometricslib.pipelines.stacking.fwhm_sampling.stored_frame_widths --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.expected_stack_fwhm -->
- **Stack measurements (§3.4).** `stage._record_stack_quality` calls `record_stacking_quality` (`post_processing/record_quality.py`). <!-- impl: astrometricslib.pipelines.stacking.post_processing.record_quality.record_stacking_quality --> With no solution, the noise step tries up to `MEMBER_FRAME_ATTEMPTS` reference-group frames. <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.MEMBER_FRAME_ATTEMPTS --> `_plate_solve_stack_wcs` then solves it with `AstrometryPipeline.solve_wcs`, refit from the Gaia cache (`PLATE_SOLVE_REFIT_WCS_SOURCE` or `PLATE_SOLVE_UNREFINED_WCS_SOURCE`). <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.PLATE_SOLVE_REFIT_WCS_SOURCE --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.PLATE_SOLVE_UNREFINED_WCS_SOURCE --> The aperture set file keeps one set per camera, optic and reference exposure (`camera|telescope|focal|exp=<s>`), which subset and odd–even stacks reuse (`find_library_set_key`). <!-- impl: astrometricslib.pipelines.stacking.post_processing.aperture_storage.record_library_set_key --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.aperture_storage.find_library_set_key --> `stacking.frames_stacked_count` (definition version 2) counts the member rows' frames (`stack_membership.count_frames_stacked`). <!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_membership.count_frames_stacked --> `post_processing/normalization_scale.py` stores Siril's normalization scale as `stacking.normalization_scale`; it multiplies the expected-noise rows (definition version 2). <!-- impl: astrometricslib.pipelines.stacking.post_processing.normalization_scale.EXPOSURE_MATCH_FRACTION --> [STK-36](validation/stacking.md#stk-36), [STK-07](validation/stacking.md#stk-07), [STK-38](validation/stacking.md#stk-38), [STK-39](validation/stacking.md#stk-39), [STK-40](validation/stacking.md#stk-40), [STK-41](validation/stacking.md#stk-41).
- **Predicted noise of an exposure-group stack (§3.4).** `expected_noise.expected_exposure_group_noise` <!-- impl: astrometricslib.pipelines.stacking.post_processing.expected_noise.expected_exposure_group_noise --> gives frame `i` of group `g` the coefficient `W_g ŵ_i / (ΣW · G_g)` (`G_g` 1 when unmeasured, marking assumed rows); `k_i`: `FrameNoiseInput.exposure_scale`; model in the stacking README. `record_science.compute_expected_noise` refuses (not measured) when `clipped_exposure_groups` is non-empty, giving the membership's reason for unknown weights. [STK-38](validation/stacking.md#stk-38), [STK-59](validation/stacking.md#stk-59), [STK-60](validation/stacking.md#stk-60) (failed).
- **Aperture set identity (§3.4).** `stacking.aperture_set_number` (each noise stack) and its odd–even twin hold the set's identity (`aperture_identity_to_number`, `aperture_identity_from_number`, `aperture_set_measurement`). Design estimate: [STK-36](validation/stacking.md#stk-36), [STK-42](validation/stacking.md#stk-42). <!-- impl: astrometricslib.pipelines.stacking.post_processing.record_quality.aperture_set_measurement -->
- **Subset stacks (§3.4).** `subset_noise_run.py`, `subset_stack.stack_frame_subset`. `stack_subset(..., record_forecast_rows=True)` stores forecast rows; the restack labels its all-but-the-last-night subset `EARLIER_NIGHTS_VARIANT`. <!-- impl: astrometricslib.reports.EARLIER_NIGHTS_VARIANT --> [STK-37](validation/stacking.md#stk-37), [STK-38](validation/stacking.md#stk-38).
- **Empty-sky noise components (§3.4.3, Table 4).** Random terms: `record_science.compute_expected_noise`. <!-- impl: astrometricslib.pipelines.stacking.post_processing.record_science.compute_expected_noise --> Master dark term: `expected_noise.expected_master_dark_noise` (`stacking.expected_master_dark_noise_stack_unit`, one group only). <!-- impl: astrometricslib.pipelines.stacking.post_processing.expected_noise.expected_master_dark_noise --> Master flat term: `stacking.flat_noise_in_aperture_stack_unit` (`flat_noise_contribution.py`). `record_science.noise_ratio_measurements` stores the measured-to-predicted ratio. <!-- impl: astrometricslib.pipelines.stacking.post_processing.record_science.noise_ratio_measurements --> [STK-36](validation/stacking.md#stk-36), [STK-39](validation/stacking.md#stk-39).
- **Odd–even difference noise (§3.4.3).** `QualityStacks.record_odd_even_noise` runs `odd_even_difference_run.run_odd_even_difference_noise` on library stacks with at least `ODD_EVEN_DIFFERENCE_MINIMUM_FRAMES_PER_HALF` frames a half. <!-- impl: astrometricslib.pipelines.stacking.odd_even_difference_run.ODD_EVEN_DIFFERENCE_MINIMUM_FRAMES_PER_HALF --> It calls `difference_noise.measure_difference_noise`, divides by `ODD_EVEN_DIFFERENCE_NOISE_BIAS` and stores the `stacking.odd_even_difference_*` rows (quality store schema 5). <!-- impl: astrometricslib.pipelines.stacking.post_processing.difference_noise.ODD_EVEN_DIFFERENCE_NOISE_BIAS --> `odd_even_apertures.py` writes the record `<stack>_odd_even_apertures.json` (`save_odd_even_aperture_record`, `load_odd_even_aperture_record`) and four row and column scatter rows (`measure_line_pattern`). <!-- impl: astrometricslib.pipelines.stacking.post_processing.odd_even_apertures.save_odd_even_aperture_record --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.odd_even_apertures.load_odd_even_aperture_record --> `scripts/measure_odd_even_bias.py` measures it by injection (`odd_even_bias.measure_odd_even_bias`; not run). <!-- impl: astrometricslib.pipelines.stacking.odd_even_bias.measure_odd_even_bias --> [STK-42](validation/stacking.md#stk-42).
- **Aperture size and depth (§3.4.3).** `aperture_size_noise.measure_aperture_size_noise` stores `stacking.empty_aperture_noise_size_exponent`. <!-- impl: astrometricslib.pipelines.stacking.post_processing.aperture_size_noise.measure_aperture_size_noise --> `depth_limits.measure_depth_limits` stores `stacking.point_source_depth_5sigma_mag` and `stacking.surface_brightness_limit_3sigma_10arcsec_mag_per_arcsec2`, zero point from `gaia_zero_point`. <!-- impl: astrometricslib.pipelines.stacking.post_processing.depth_limits.measure_depth_limits --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.depth_limits.gaia_zero_point --> [STK-36](validation/stacking.md#stk-36).
- **Re-measurement (§3.4).** The sky-rate, sky-noise and read-noise metrics are at versions 3, 3, 2. `frames_whose_camera_profile_gap_closed` queues frames whose profile entry now exists. <!-- impl: astrometricslib.pipelines.stacking.frame_measurement_run.frames_whose_camera_profile_gap_closed --> [AST-17](validation/astrometry.md#ast-17).
- **Half-stack pattern (§3.4).** `post_processing/half_pattern.py`, called from `odd_even_difference_run._half_pattern_safely`. It adds eight rows to the difference's nine. [STK-45](validation/stacking.md#stk-45).
- **Flat-to-light match (§3.4).** `post_processing/flat_match.py` (`flat_match_rows`) stores ten rows. `_internal/drivers/calibration_library.py` selects flats and biases (`select_flat_frames`, `select_bias_frames`); staging records them under `flat_frame_paths` and `bias_frame_paths`. Config key `flat_match_time_limit_seconds`. [STK-46](validation/stacking.md#stk-46).
- **Sky background (§3.4.4).** `background_gradient.py` (STK-D74). <!-- impl: astrometricslib.pipelines.shared.binned_sky_plane.fit_clipped_plane --> `sky_plane.py`: `SKY_PLANE_GRADIENT_LIMIT` (0.03), `RADIAL_FALLOFF_LIMIT` (0.012) ([STK-61](validation/stacking.md#stk-61)). `cluster_nebula.py`: nebula clusters. <!-- impl: astrometricslib.pipelines.stacking.post_processing.background_gradient.record_background_gradient --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.background_gradient.BACKGROUND_BIN_COUNT --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.sky_plane.SKY_PLANE_GRADIENT_LIMIT --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.sky_plane.choose_background_method --> <!-- impl: astrometricslib.pipelines.stacking.post_processing.cluster_nebula.refine_object_category -->
- **Registration step and drift (§3.4).** `post_processing/registration_shifts.py`, called by `frame_geometry_measurements`. [STK-44](validation/stacking.md#stk-44).

### 4.2 Astrometry (§4)

*Code:* `astrometricslib/pipelines/astrometry/`

- **Interfaces (§4.1).** `pipeline.py`; `runner.py` (adapter).
- **Detection and centroids (§4.2).** `processing/star_identifier.py`, `star_detection.py`, `pre_processing/source_detection.py`, `fwhm.py`.
- **Plate solving, Gaia and the refit (§4.2).** `processing/plate_solution_steps.py`, `wcs_refinement.py`; `astrometricslib/_internal/drivers/astrometry_net_driver.py`, `gaia_cone_driver.py`, `catalog_store.py`, `sky_tiles.py`; `utilities/catalog_seeding.py`.
- **Gaia tiles (§4.2).** `processing/gaia_depth.py` sets depth; `gaia_tile_queries.py`, `gaia_field_stars.py` fetch and read tiles. Missing tiles raise `astrometry.notice_gaia_tiles_missing`.
- **WCS header (§4.3).** `pipelines/shared/session_identification.write_wcs_to_fits_header`. The refit is around the center (`proj_point="center"`); `save_refit_stack_plate_solution` adds the `WCSREFIT` card; detections file version 3.
- **Extended-source hint (§4.2).** `check_extended_source` falls back to the saved target record; type rule `target_object_category`. <!-- impl: astrometricslib.utilities.target_object_category.target_object_category -->
- **Checks (§4.4).** `post_processing/record_quality.py`, `assess_match_quality.py`.
- **Target clipping (§4.4).** `post_processing/target_clipping.py` (`target_clipping_measurements`). [AST-19](validation/astrometry.md#ast-19).
- **Expected residual (§4.4).** `post_processing/expected_residual.py` stores four `astrometry.expected_*` metrics. [AST-21](validation/astrometry.md#ast-21).
- **Target position.** `astrometricslib/pipelines/shared/target_position.py` takes a target's RA/Dec from SIMBAD, not a frame; `scripts/audit_target_positions.py` checks them.

### 4.3 Photometry (§5)

*Code:* `astrometricslib/pipelines/photometry/`

- **Interfaces (§5.1).** `runner.py` (adapter); `batch.py`.
- **Aperture photometry (§5.2).** `pre_processing/frame_photometry.py`, `detector_noise.py`, `observation_times.py`.
- **Comparison stars and light curves (§5.2).** `processing/comparison_ensemble.py`, `catalog_zero_point.py`, `variability_analyzer.py`, `variability_indices.py`. `MINIMUM_COMPARISON_SIGNAL_TO_NOISE` 50, `MINIMUM_MEASURED_FRAME_SHARE` 0.95 (PHO-21, PHO-D07). <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.MINIMUM_COMPARISON_SIGNAL_TO_NOISE --> <!-- impl: astrometricslib.pipelines.photometry.processing.comparison_ensemble.MINIMUM_MEASURED_FRAME_SHARE -->
- **Period search (§5.2).** `processing/periodicity_search.py`, `period_checks.py`, `family_wise_correction.py`.
- **Checks (§5.4).** `pre_processing/assess_input_quality.py`; `post_processing/record_quality.py`, `variability_skill.py`, `known_variability_labels.py`, `assess_output_quality.py`. `not_judged_reason` says why a star is not judged: `instrumental_artifacts.step_artifact_reason`, `coverage_reason` or `edge_reason` (PHO-20). <!-- impl: astrometricslib.pipelines.photometry.processing.instrumental_artifacts.step_artifact_reason --> <!-- impl: astrometricslib.pipelines.photometry.processing.instrumental_artifacts.coverage_reason --> <!-- impl: astrometricslib.pipelines.photometry.processing.instrumental_artifacts.edge_reason -->
- **Session tie (§5.2).** `session_tie.MINIMUM_TIE_STARS` (20), `TIE_CLIP_SIGMA` (3) (PHO-D37, design estimate). `_tie_sessions_to_the_field` in `batch.py` picks the reference session (most measured stars, the earliest on a tie) and scales the others before `_merge_light_curves`. The factor, uncertainty and spread are kept in `sessionTies` and each `SessionPhotometrySummary` (`tieStatus`, `tieFactor`, `tieUncertainty`).
- **Scintillation term (§5.2).** `scintillation_term.build_scintillation_term` measures the coefficient (5 stars, 20 frames, range 0.5 to 3, fallback 1.5, 5σ clip; PHO-D38) and `add_scintillation_in_quadrature` adds it at the start of `normalize_light_curves`. `noise_budget.remove_scintillation_in_quadrature` removes it before the background term. Each session keeps a `SessionScintillationRecord`.
- **Run outcomes (§5.2).** `processing/noise_budget.py`, `observing_window.py`, `target_amplitude.py`, the matching `post_processing/record_*.py` steps, and `shared/variable_star_catalogs.py` (VSX range). [PHO-17](validation/photometry.md#pho-17), [PHO-18](validation/photometry.md#pho-18), [PHO-19](validation/photometry.md#pho-19).

### 4.4 Spectroscopy (§6)

*Code:* `astrometricslib/pipelines/spectroscopy/`

- **Interfaces (§6.1).** `pipeline.py`, `runner.py` (adapter), `record_and_flag_spectroscopy_stars.py`, `frame_analysis.py`, `batch.py`.
- **Star identification (§6.2).** `pre_processing/spectral_star_registration.py`, `shared/target_center_hint.py`.
- **Extraction (§6.2).** `pre_processing/spectrum_extractor.py`, `_extractor_c.c`, `neighbor_trail_deblending.py`, `neighbor_wing_correction.py`.
- **Wavelength calibration (§6.2).** `pre_processing/spectrum_calibrator.py`, `optics_physics.py`, `differential_refraction.py`, `wavelength_zero_point.py`.
- **Flux and resolution (§6.2).** `pre_processing/quantum_efficiency_correction.py`, `instrument_response.py`, `atmospheric_extinction.py`, `measured_line_spread.py`, `spectral_resolution.py`; `record_measured_spectral_bases` runs in `_process_target_stars` after extraction ([SPE-05](validation/spectroscopy.md#spe-05)). <!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution.record_measured_spectral_bases -->
- **Classification and features (§6.2).** `processing/spectral_classifier.py`, `spectrum_analysis.py`, `star_reddening.py`, `interstellar_extinction.py`/`detection_label_limits.py`.
- **Stack registration quality (§6.3).** `utilities/registration_quality.py`.
- **Checks (§6.4).** `post_processing/record_quality.py`, `compare_to_gaia_xp.py`, `compare_to_catalog.py`, `wavelength_scale.py`.
- **Signal-to-noise and class gap (§6.2).** `processing/spectral_line_indices.py` (`line_window_signal_to_noise`) fills `snr_at_classifying_lines`; `processing/class_gap_calibration.py` and `data/class_gap_against_snr_zwo_asi_533mm_pro.json` hold the table. [SPE-30](validation/spectroscopy.md#spe-30).

### 4.5 Asteroid detection (§7)

*Code:* `astrometricslib/pipelines/asteroid_detection/`

- **Interfaces (§7.1).** `pipeline.py`; `runner.py` (adapter).
- **Detection (§7.2).** `detection.py` (blink analysis, tracking), `frame_wcs_composer.py` (per-frame WCS), `ephemeris.py` (IMCCE SkyBoT).
- **Checks (§7.4).** `record_quality.py`.
- **Unused.** `mpc_report.py` (MPC report formats [8]) and `rotation_period.py`.

### 4.6 Shared pipeline code

*Code:* `astrometricslib/pipelines/shared/`

- **Frames and targets.** `frame_grouping.py`, `frame_scanning.py`, `target_records.py`, `target_sessions.py`.
- **Images, stars and state.** `image_conversions.py`, `star_recording.py`, `catalog_star_identity.py`, `session_identification.py`, `analysis_context.py`.
- **Recording.** `provenance_recording.py`, `applied_camera_profile.py`, `quality/notices.py` ([STK-58](validation/stacking.md#stk-58)). <!-- impl: astrometricslib.pipelines.shared.quality.notices.notice_measurement -->
- **Quality primitives.** `quality/` (`saturation.py`, `raw_frame_check.py`, `frame_measurement.py`, `background_measurement.py`: own sky estimator). <!-- impl: astrometricslib.utilities.robust_statistics.robust_sigma --> <!-- impl: astrometricslib.pipelines.shared.header_wcs.celestial_wcs --> <!-- impl: astrometricslib.pipelines.shared.stack_sidecar.read_stamped_record --> <!-- impl: astrometricslib.pipelines.shared.image_scaling.measure_smoothed_sky_background -->
- **Star card shape.** `measure_single_star_shape` is the one-star frame fit; round is at most `TRAILED_ELONGATION_LIMIT` ([AST-17](validation/astrometry.md#ast-17)). <!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.measure_single_star_shape -->

### 4.7 Drivers and pixel-level primitives

*Code:* `astrometricslib/_internal/drivers/`.

- **Interfaces.** `interfaces/` holds `StackingDriver`, `PlateSolveDriver`, `SimbadDriver`, `GaiaXpDriver`, `ReddeningDriver` and their types; `driver_set.py` (`Drivers`) fills empty slots with built-ins.
- **Siril.** `siril/`.
- **Catalogs and services.** `_internal/drivers/outside_services` is the request gateway (AST-D26, AST-D27). `astrometry_net_driver.py`, `gaia_cone_driver.py`, `catalog_store.py`, `gaia_xp_driver.py`, `astroquery_simbad_driver.py`. `catalog_cache.answer_through_cache` stores each answer with its fetch time (`SIMBAD_ANSWER_LIFETIME_DAYS` 180, `VSX_ANSWER_LIFETIME_DAYS` 90; AST-D24); see `catalog_answer_store.py`, `cached_simbad_driver.py`. <!-- impl: astrometricslib._internal.drivers.catalog_cache.SIMBAD_ANSWER_LIFETIME_DAYS --> <!-- impl: astrometricslib._internal.drivers.catalog_cache.VSX_ANSWER_LIFETIME_DAYS -->
- **Stores.** `calibration_library.py`, `camera_profile_store.py`, `provenance_store.py`, `catalog_access.py`.
- **Images.** `fits_access.py`, `image.py`. <!-- impl: astrometricslib._internal.drivers.fits_access.header_is_plate_solved -->

`catalog_access.py` uses the Butler (Section 4.8) [9], not SQL.

### 4.8 Shared storage backend

*Code:* `astrometricslib/foundation/storage/`: `butler.py`, `local_database.py`, `process_locks.py`, `mount.py` (no writes to an unmounted frames mount).

### 4.9 Job framework

*Code:* `astrometricslib/foundation/jobs/`, shared by both libraries, the backend and the MCP servers: `store.py`, `runner.py`, `models.py`, `process_identity.py`.

## 5. Tuned parameters and validation status

Tables 3 to 7 list tuned values. Status labels ([register](validation/index.md#3-status-labels)) link to evidence; hidden links name the code.

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
| Frame screening: fewest frames to judge; clouded frame star count; trailed frame elongation | 8; below 0.5 of session median; above 1.32 and 0.12 over median | Partly measured ([STK-01](validation/stacking.md#stk-01)) | §3.4.1 |
| Elongation rise, color camera | at least 0.22 over median | Measured ([STK-02](validation/stacking.md#stk-02)) | §3.4.1 |
| Warning share of a session set aside | 25% | Partly measured ([STK-03](validation/stacking.md#stk-03)) | §3.4.1 |
| Dark temperature slot width | 3 °C | Partly measured ([STK-06](validation/stacking.md#stk-06)) | §3.2 |
| Minimum frames kept after quality filtering | 5 | Partly measured ([STK-10](validation/stacking.md#stk-10)) | §3.3 (Table 3, step 3) and §3.4.1 |
| Brightness scale: fewest shared stars before pixel fallback | 16 | Measured ([STK-19](validation/stacking.md#stk-19)) | §3.2 |
| Pixel fallback: percentile range; bright-end fraction | 20th to 80th; 1% | Design estimate ([table](validation/stacking.md#design-estimates)) | §3.2 |
| Group frame noise: clip limit | 4 rough scales | Design estimate ([table](validation/stacking.md#design-estimates)) | §3.2 |
| Gain and read noise at another offset: frames allowed | under 20% exact zeros | Design estimate ([table](validation/stacking.md#design-estimates)) | §3.2 |
| Stack noise plate solution: member frames tried | 5 | Design estimate ([table](validation/stacking.md#design-estimates)) | §3.4 |
| Normalization scale: fewest unsaturated reference stars; reference exposure match | 5; 1% | Partly measured ([STK-38](validation/stacking.md#stk-38)) | §3.4 |
| Linearity tolerance between exposure groups | 5% | Partly measured ([STK-20](validation/stacking.md#stk-20)) | §3.2 |
| Brightest sky per ISO allowed for a group | 3 times reference | Partly measured ([STK-21](validation/stacking.md#stk-21)) | §3.2 |
| Noise penalty above which frames are noise-weighted | 1.05 | Design estimate ([table](validation/stacking.md#design-estimates)); [STK-07](validation/stacking.md#stk-07) | §3.2 |
| Share of spectral frames that must register before fallback | 0.9 | Design estimate ([table](validation/stacking.md#design-estimates)); [STK-12](validation/stacking.md#stk-12) | §3.3 |
| Rejected pixel share that flags a stack | 15% | Partly measured ([STK-24](validation/stacking.md#stk-24)) | §3.4.2 |
| Star sharpness warning ratio (fails above) | 1.2 | Partly measured ([STK-25](validation/stacking.md#stk-25)) | §3.4.2 |
| Noise apertures off faint sources: threshold; smoothing widths; high-pass; clearance; fewest clean apertures; file version | 3.5σ; 1.5, 3, 6 px; 12 px; 13 px; 20; 3 | Partly measured ([STK-36](validation/stacking.md#stk-36)) | §3.4 |
| Reference-star match: largest rotation; step; negligible shift, rotation | 0.5°; 1 px; 0.05 px, 0.001° | Design estimate (STK-D30) | §3.4 |
| Odd–even difference noise: fewest frames per half | 8 | Design estimate (STK-D25) | §3.4 |
| Odd–even difference bias divisor | 1.0 (injection test not run) | Design estimate (STK-D31) | §3.4.3 |
| Aperture sizes: radius multiples; clearance | 0.5, 1, 1.5, 2; 17 px | Design estimate (STK-D32) | §3.4.3 |
| Line pattern: fewest pixels per line; running-mean lines; fewest lines | 100; 31; 20 | Design estimate (STK-D33) | §3.4.3 |
| Depth: star match radius; contamination margin; fewest zero-point stars; thresholds; box | 3 arcsec; 3 mag; 3; 5σ, 3σ; 10 arcsec | Design estimate (STK-D34) | §3.4.3 |
| Bias test: smoothing width; real-pixel radius | 3 px; 4 px | Design estimate (STK-D35) | §3.4.3 |
| Registration step: degenerate above; unmeasured above degenerate, identity shares; jump; fewest steps, frames; drift significance; axis agreement | 50 px; 10%, 50%; median + 5 x max(1.4826 MAD, 0.1 px); 5, 5; 3 errors; 0.5 | Partly measured ([STK-44](validation/stacking.md#stk-44)) | §3.4 |
| Half-stack pattern: filter; clearance; tile; resamples; lag; least extent | 3 px; 10 px; 64 px; 400; 6 px; 12 px | Partly measured ([STK-45](validation/stacking.md#stk-45)) | §3.4 |
| Flat-to-light match: cell; band pass; trend degree; flats; lights, fewest; sky; target disc; shadow limits; placement limit; search; time limit | 8 px; 2 and 30 cells; 4; 16; 24, 8; 10 times read noise; 25 cells; 5σ, 0.15%, 25 cells; 3.1 null spreads; ±160 px, ±3°; 300 s | Partly measured ([STK-46](validation/stacking.md#stk-46)) | §3.4 |
| `stacking.color_preview_*` | `maximum_alignment_rms_pixels=1.0`; `minimum_coverage=0.95`; `star_widths_pixels=(3.5,6.0,4.0)`; `detection_sigmas=6.0`; `pairing_radii_pixels=(8.0,2.0)`; `shift_search_pixels=40.0`; `chroma_blur_pixels=(3.0,2.0)`; `color_fade=(2.0,8.0,6.0)`; `nebula_chroma_gain=1.25`; `lightness_ratio_limits=(0.5,2.0)`; `star_core_taper=(25.0,0.35)`; `sky_noise_multiple=2.0`; `sky_tint_limits=(0.002,0.03)`; `sky_tint_smoothing=(150.0,0.05)`; `halo_widths=2.0`; `footprint_noise_multiples=(3.0,2.5)`; `aperture_fwhm_multiple=1.6`; `aperture_radius_range_pixels=(6.0,12.0)`; `crowded_aperture_pixels=5.0`; `sky_ring_gaps_pixels=(4.0,10.0)`; `blend_fraction=0.15`; `color_index_error_limit=0.10`; `saturation_shares=(0.85,0.5)`; `wing_radii_pixels=(6.0,16.0,22.0,30.0)`; `wing_calibration=(97.0,0.03)`; `resampling_noise_factor=1.5`; `gaia_match_pixels=2.0` | Partly measured ([STK-62](validation/stacking.md#stk-62)) | §3.3.1 |

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
<!-- impl: astrometricslib.pipelines.stacking.post_processing.half_pattern.source_clearance_px -->
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
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.TARGET_MASK_SIZE_MARGIN -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.TARGET_MASK_MAXIMUM_FIELD_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.SHADOW_MINIMUM_SIGMAS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.SHADOW_MINIMUM_DEPTH_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.SHADOW_MINIMUM_AREA_CELLS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.PLACEMENT_LIMIT_SIGMAS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.MAXIMUM_SHIFT_CELLS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.COARSE_ANGLE_RANGE_DEG -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.flat_match.DEFAULT_TIME_LIMIT_SECONDS -->
<!-- impl: astrometricslib._internal.drivers.calibration_library.FLAT_FRAME_PATHS_KEY -->
<!-- impl: astrometricslib._internal.drivers.calibration_library.BIAS_FRAME_PATHS_KEY -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.aperture_placement.FAINT_SOURCE_THRESHOLD_SIGMAS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.aperture_placement.FAINT_SOURCE_SMOOTHING_SIGMAS_PX -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.aperture_placement.FAINT_SOURCE_HIGH_PASS_SIGMA_PX -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.aperture_placement.faint_source_clearance_px -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.aperture_placement.MINIMUM_USABLE_APERTURES -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.empty_apertures.APERTURE_FILE_VERSION -->
<!-- impl: astrometricslib.pipelines.stacking.odd_even_difference_run.ODD_EVEN_DIFFERENCE_MINIMUM_FRAMES_PER_HALF -->
<!-- impl: astrometricslib.utilities.rejection_thresholds.rejection_bounds -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.MAXIMUM_ROTATION_DEG -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.ROTATION_STEP_PX -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.NEGLIGIBLE_SHIFT_PX -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.NEGLIGIBLE_ROTATION_DEG -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_noise.POOR_ALIGNMENT_RMS_PX -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.difference_noise.ODD_EVEN_DIFFERENCE_NOISE_BIAS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.aperture_size_noise.SIZE_RADIUS_MULTIPLES -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.odd_even_apertures.MINIMUM_PIXELS_PER_LINE -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.odd_even_apertures.RUNNING_MEAN_LINES -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.odd_even_apertures.MINIMUM_LINES -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.depth_limits.STAR_MATCH_RADIUS_ARCSEC -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.depth_limits.CONTAMINATION_MAGNITUDE_MARGIN -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.depth_limits.MINIMUM_ZERO_POINT_STARS -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.depth_limits.POINT_SOURCE_SIGMA -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.depth_limits.SURFACE_BRIGHTNESS_SIGMA -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.depth_limits.SURFACE_BRIGHTNESS_BOX_ARCSEC -->
<!-- impl: astrometricslib.pipelines.stacking.odd_even_bias.SMOOTHING_SIGMA_PX -->
<!-- impl: astrometricslib.pipelines.stacking.odd_even_bias.KEEP_RADIUS_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.time_budget.FRAME_MEASUREMENT_TIME_BUDGET_SECONDS -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_GRID_CELL_COUNT -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_CANDIDATES_PER_CELL -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_FIT_BOX_HALF_WIDTH_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_ISOLATION_RADIUS_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_SATURATION_MARGIN_FRACTION -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_SUBPIXEL_FACTOR -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_BETA_MINIMUM -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_BETA_MAXIMUM -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_BETA_START -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_PROFILE_RADIUS_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_CUT_HALF_LENGTH_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_CENTROID_BOX_HALF_WIDTH_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_SKY_ANNULUS_WIDTH_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_CLIPPED_PEAK_FRACTION -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_CUT_STEP_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_SNAP_RADIUS_LIMIT_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_SNAP_SMOOTHING_SIGMA_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_MINIMUM_SIGNAL_TO_NOISE -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_MINIMUM_FWHM_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_MAXIMUM_FWHM_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile.STAR_MAXIMUM_EXTENT_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_MAXIMUM_RADIUS_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_INNER_PEAK_FRACTION -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_OUTER_NOISE_MULTIPLE -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_OUTER_MARGIN_FACTOR -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_SKY_INNER_FRACTION -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_MINIMUM_WIDTH_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_MINIMUM_INNER_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_TARGET_MATCH_RADIUS_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_NEIGHBOR_MASK_NOISE_MULTIPLE -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_NEIGHBOR_SEARCH_MARGIN_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_NORMALIZER_FLOOR_NOISE -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_HARMONIC_COUNT -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_SIGNIFICANCE_THRESHOLD -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_BIN_DEGREES -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_NEIGHBOR_MINIMUM_SIGNAL_TO_NOISE -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_MINIMUM_PIXELS_PER_FITTED_NUMBER -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_ANNULUS_MINIMUM_SHARE -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_ring.STAR_RING_NEIGHBOR_PEAK_SEARCH_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_MINIMUM_R_SQUARED -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_MAXIMUM_CENTER_SHIFT_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_MINIMUM_FWHM_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_MAXIMUM_FWHM_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOFFAT_MINIMUM_STAR_COUNT -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOMENT_APERTURE_FWHM_MULTIPLE -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.MOMENT_APERTURE_MINIMUM_RADIUS_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.frame_statistics.BOOTSTRAP_RESAMPLE_COUNT -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.STAR_FINDER_EXPECTED_FWHM_PX -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.STAR_FINDER_THRESHOLD_SIGMA -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.STAR_FINDER_BOX_FRACTION -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.PIXEL_BLUR_FWHM_SQUARED_FACTOR -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.FIELD_GRID_CELLS_PER_SIDE -->
<!-- impl: astrometricslib.pipelines.shared.quality.star_profile_fitting.FIELD_REGION_CELLS_PER_SIDE -->
<!-- impl: astrometricslib.pipelines.stacking.frame_measurement_run.MEASUREMENT_WORKER_COUNT -->
<!-- impl: astrometricslib.pipelines.stacking.frame_measurement_run.BIAS_FRAMES_FOR_LEVEL -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.MINIMUM_FLAT_PAIRS -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.MINIMUM_BIAS_PAIRS -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.MAXIMUM_TEMPERATURE_SPREAD_C -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.sensor_noise.CENTRAL_REGION_SHAPE_PIXELS -->
<!-- impl: astrometricslib._internal.drivers.calibration_library.DEFAULT_DARK_TEMPERATURE_TOLERANCE_C -->
<!-- impl: astrometricslib.utilities.stack_filter_floor.DEFAULT_MINIMUM_SURVIVING_FRAMES -->
<!-- impl: astrometricslib.pipelines.stacking.processing.group_gains.GAIN_MID_RANGE_PERCENTILES -->
<!-- impl: astrometricslib.pipelines.stacking.processing.group_gains.GAIN_BRIGHTEST_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.processing.group_gains.DEFAULT_GAIN_DISAGREEMENT_TOLERANCE -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.DEFAULT_FWHM_DEGRADATION_RATIO -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.SESSION_GAP_SECONDS -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.MINIMUM_FRAMES_TO_JUDGE -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.CLOUD_STAR_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.TRAILED_ELONGATION_LIMIT -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.ELONGATION_RISE_ABOVE_MEDIAN -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.WARN_QUARANTINED_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.exposure_weighting.MINIMUM_PENALTY_TO_WEIGHT -->
<!-- impl: astrometricslib.pipelines.stacking.stack_runner.MINIMUM_REGISTERED_FRACTION -->
<!-- impl: astrometricslib.pipelines.stacking.post_processing.stack_quality.DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD -->
<!-- impl: astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.COLOUR_CAMERA_ELONGATION_RISE_ABOVE_MEDIAN -->
<!-- impl: astrometricslib.pipelines.stacking.processing.group_star_gains.STAR_GAIN_MINIMUM_STARS -->
<!-- impl: astrometricslib.pipelines.stacking.processing.group_gains.SKY_RATIO_LIMIT -->

### 5.2 Astrometry settings

**Table 4.** Tuned settings of the astrometry pipeline.

| Parameter | Default | Status | Architecture section |
| --- | --- | --- | --- |
| Detection threshold; sharpness; roundness | 5σ; 0.2 to 1.0; ±0.6 | Design estimate ([table](validation/astrometry.md#design-estimates)) | §4.2 (step 1) |
| Fewest stars for a solve | 4 | Design estimate ([table](validation/astrometry.md#design-estimates)) | §4.4.1 |
| Fewest stars for a local solve | 10 | Partly measured ([AST-13](validation/astrometry.md#ast-13)) | §4.4.1 |
| Pixel-scale window; solver widening | ±5%; 20% | Design estimate ([table](validation/astrometry.md#design-estimates)) | §4.4.1 |
| Stars sent to the online solver | 100 | Design estimate ([table](validation/astrometry.md#design-estimates)) | §4.2 (step 2) |
| Gaia refit: fewest matched stars | 40 | Design estimate ([table](validation/astrometry.md#design-estimates)) | §4.2 (step 6) |
| Gaia refit: largest shift | 30″ | Partly measured ([AST-02](validation/astrometry.md#ast-02)) | §4.2 (step 6) |
| Gaia refit: match radii; clip; passes; SIP order | 4 and 1.5 px; 3σ; 5; 2 | Partly measured ([AST-02](validation/astrometry.md#ast-02)) | §4.2 (step 6) |
| Gaia time limits; depth steps after a slow answer | 45 s, 60 s; 2 of 1 mag | Partly measured ([AST-08](validation/astrometry.md#ast-08)) | §4.2 (step 4) |
| Gaia depth: throughput; star width; sky; signal-to-noise; margin; cap; default | 0.62; 5″; 18.7 mag/″²; 11.6; 1 mag; G 21; G 18 | Partly measured ([table](validation/astrometry.md#design-estimates)) | §4.2 (step 4) |
| Gaia tile level | 7, split at 50,000 rows | Partly measured ([table](validation/astrometry.md#design-estimates)) | §4.2 (step 4) |
| Match radius; map-check circle/share | 10″; 2×/chance+3σ∈[0.35,0.5] | Design estimate ([table](validation/astrometry.md#design-estimates)) | §4.2, §4.4.2 |
| Same-star tolerance; no Gaia number | 2″; 3″ | Measured ([AST-11](validation/astrometry.md#ast-11)) | §4.2 (step 4) |
| Unresolved companions radius | 3″ | Partly measured ([AST-10](validation/astrometry.md#ast-10)) | §4.2 (step 4) |
| Variable-star match radius | 3″ | Partly measured ([AST-12](validation/astrometry.md#ast-12)) | §5.2 (concept 4) |
| Unsolved spectral center star: radius; area; faintest peak | 5′; 0.10 of shorter side; 0.10 of peak | Partly measured ([AST-09](validation/astrometry.md#ast-09)) | §6.2 (concept 6) |
| Fewest catalog-matched stars | 20 | Partly measured ([AST-05](validation/astrometry.md#ast-05)) | §4.4.2 |
| Residual limit | 0.5 of a star width | Partly measured ([AST-04](validation/astrometry.md#ast-04)) | §4.4.2 |
| Failed lookups allowed | under 50% | Design estimate ([table](validation/astrometry.md#design-estimates)) | §4.4.2 |
| Target clipping: disc radius; frame cap; read time; least peak | 30″; 200; 120 s; 5σ | Partly measured ([AST-19](validation/astrometry.md#ast-19)) | §4.4 |
| Expected residual: centroid factor; pixel-phase rms at widths 1.5 to 5 px | √(2/π); 0.092 to 0.008 px | Partly measured ([AST-21](validation/astrometry.md#ast-21)) | §4.4 |
| Target position tolerance: floor; extent factor | 15′; 1.0 of the radius | Partly measured ([AST-20](validation/astrometry.md#ast-20)) | §4.2 |
| Margin around the solved frame for a catalog position | 15′ | Design estimate ([table](validation/astrometry.md#design-estimates)) | §4.2 |

<!-- impl: astrometricslib.pipelines.astrometry.pre_processing.source_detection.SourceDetector -->
<!-- impl: astrometricslib._internal.drivers.astrometry_net_driver.MINIMUM_SOURCES_FOR_LOCAL_SOLVE -->
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
<!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_tile_queries.GAIA_VIZIER_TIMEOUT_SECONDS -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_tile_queries.GAIA_ESA_TIMEOUT_SECONDS -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_tile_queries.GAIA_DEPTH_STEP_MAGNITUDES -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_tile_queries.GAIA_DEPTH_STEPS_DOWN -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_tile_queries.GAIA_DEFAULT_MAGNITUDE_LIMIT -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.star_detection.MINIMUM_STARS_FOR_DEPTH_ESTIMATE -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_depth.OPTICS_THROUGHPUT -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_depth.DEFAULT_FWHM_ARCSEC -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_depth.FALLBACK_SKY_MAG_PER_ARCSEC2 -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_depth.DETECTION_SIGNAL_TO_NOISE -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_depth.GAIA_DEPTH_MARGIN -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.gaia_depth.GAIA_DEPTH_CAP -->
<!-- impl: astrometricslib._internal.drivers.catalog_store.GAIA_TILE_LEVEL -->
<!-- impl: astrometricslib.pipelines.shared.catalog_star_identity.SAME_STAR_POSITION_TOLERANCE_ARCSEC -->
<!-- impl: astrometricslib.pipelines.shared.session_identification.MIN_CATALOG_MATCH_FRACTION_FOR_REUSED_WCS -->
<!-- impl: astrometricslib.pipelines.shared.session_identification.MIN_STARS_TO_VERIFY_REUSED_WCS -->
<!-- impl: astrometricslib.pipelines.shared.session_identification.MIN_MATCH_CONCENTRATION -->
<!-- impl: astrometricslib.pipelines.shared.session_identification.MATCH_CONCENTRATION_SIGMAS --> <!-- impl: astrometricslib.pipelines.shared.session_identification.MAX_DECIDABLE_CONCENTRATION_LIMIT -->
<!-- impl: astrometricslib.pipelines.shared.session_identification.SOLUTION_CHECK_RADIUS_FACTOR -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.simbad_matching.UNRESOLVED_COMPANION_RADIUS_ARCSEC -->
<!-- impl: astrometricslib.pipelines.shared.variable_star_catalogs.MATCH_RADIUS_ARCSEC -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.simbad_matching.POINTING_ERROR_SEARCH_RADIUS_ARCSEC -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.simbad_matching.CENTRE_STAR_SEARCH_RADIUS_FRACTION -->
<!-- impl: astrometricslib.pipelines.astrometry.processing.simbad_matching.CENTRE_STAR_MINIMUM_PEAK_FRACTION -->

### 5.3 Photometry settings

**Table 5.** Tuned settings of the photometry pipeline.

| Parameter | Default | Status | Architecture section |
| --- | --- | --- | --- |
| Aperture radius; sky ring radii; re-centering box; refinement passes; largest move | 4 px; 7 to 12 px; 11 px; 3; 1.5 px | Design estimate ([table](validation/photometry.md#design-estimates)) | §5.2 (concept 1) |
| Between-session flag: significance; amplitude | above 3; at least 0.02 mag | Design estimate ([table](validation/photometry.md#design-estimates)) | §5.2 (concept 6) |
| Comparison stars: brightest skipped; band ends at; candidates vetted; minimum and cap | 2%; 30%; 100; 5 and 20 | Design estimate ([table](validation/photometry.md#design-estimates)) | §5.2 (concept 4) |
| Vetting limit; usable-frame share; frame and per-star outlier clips | 3σ; nine tenths; 3σ (at least 5 frames), 5σ | Design estimate ([table](validation/photometry.md#design-estimates)) | §5.2 (concept 4) |
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
| Minimum signal-to-noise: spectrum and emission-line test; absorption-line test | 1.5; 7 | Partly measured ([SPE-10](validation/spectroscopy.md#spe-10), [SPE-31](validation/spectroscopy.md#spe-31), [SPE-32](validation/spectroscopy.md#spe-32)) | §6.2 (concept 5) |
| Wing correction; fitted neighbors if on | Off; 8 | Partly measured ([SPE-34](validation/spectroscopy.md#spe-34)); [SPE-D27](validation/spectroscopy.md#design-estimates) | §6.2 |
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
<!-- impl: astrometricslib.pipelines.spectroscopy.processing.spectrum_signal.MINIMUM_FEATURE_ANALYSIS_SIGNAL_TO_NOISE -->
<!-- impl: astrometricslib.pipelines.spectroscopy.pre_processing.neighbor_wing_correction.MAXIMUM_FITTED_NEIGHBORS -->
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

Code behind the [validation sessions](validation/index.md#6-validation-sessions):

- **Scripts** in `astrometricslib/scripts/`: `run_empirical_validation.py` (campaign), `run_all_target_processing.py` (batch; restack options in the scripts README), `seed_local_star_catalog.py`, `benchmark_siril_concurrency.py`, `backfill_focal_length.py`, `calibrate_class_gap_against_snr.py` (class gap table, [SPE-30](validation/spectroscopy.md#spe-30)), `backfill_variable_star_flags.py` (with `--refresh-vsx-ranges`, [PHO-19](validation/photometry.md#pho-19)).
- **History fill.** `scripts/backfill_quality_measurements.py`; record: [Section 6.4](validation/index.md#64-history-fill-of-the-quality-store).
- **Calibration identities.** `QualityStore.write_calibration_identities` (`_internal/drivers/quality_store.py`, schema 3; columns in the drivers README) stores one row per library stack. `QualityMeasurements.get_calibration_identities` and MCP `quality_calibration_identities` read it; `record_stacking_quality` writes it through `record_calibration_identities` (`pipelines/stacking/post_processing/calibration_record.py`).
- **Capture settings.** `CaptureSetting` rounds `pixel_scale_arcsec` to `PIXEL_SCALE_SIGNIFICANT_FIGURES` = 4 (`round_pixel_scale`). <!-- impl: astrometricslib.models.quality.capture_setting.PIXEL_SCALE_SIGNIFICANT_FIGURES --> Schema 4 merges equal settings on first open; back up first. Design estimate ([STK-D29](validation/stacking.md#design-estimates)).
- **Per-session photometry.** `analyze_target(pipeline_type="photometry")` runs `VariabilityAnalyzer` per `TargetSession` ([PHO-14](validation/photometry.md#pho-14)).
- **Cross-session star matching** (§5.2): `_match_and_merge_across_sessions` (`pipelines/photometry/batch.py`) uses `search_around_sky` (KD-tree [10]); `sessions_missing_wcs` isolates sessions lacking a WCS.
- **Track linking.** `MovingObjectDetector._chain_detections_by_persistence` in `pipelines/asteroid_detection/detection.py` pre-filters pairs with an RA/Dec box; the search is still $O(N \times M)$ ([MOV-07](validation/asteroid_detection.md#mov-07)).

## Appendix A. Ratchets and import contracts

A ratchet is a test whose exceptions only shrink. `test_no_new_upward_imports` (`astrometricslib/test/test_layer_boundaries.py`) fails on a new upward import, even inside a function (Table 8).

**Table 8.** Layer rules enforced by the ratchet test.

| Files | May not import |
| --- | --- |
| `models/`, `foundation/`, `utilities/` | `_internal/drivers`, `pipelines`, `api`, `visualization`, the package root |
| `_internal/drivers/` | `pipelines`, `api`, `visualization`, the package root |
| `pipelines/shared/`, `pipelines/pipeline_base.py` | any specific pipeline, `pipelines.tasks`, `pipelines.target_batch`, `api`, the package root |
| `pipelines/<name>/` | `pipelines.tasks`, `pipelines.target_batch`, `api`, the package root |
| `pipelines/tasks.py`, `pipelines/target_batch.py` | `api`, the package root |

Not checked: `visualization/`, `api/`, the package root, tests and same-layer imports.

Three more ratchets (stale entries fail):

- **Raw FITS access** (`KNOWN_FITS_ACCESS_SITES`). New code reads FITS through `_internal/drivers/fits_access.py` or `_internal/drivers/image.py`.
- **Wall clock** (`KNOWN_WALL_CLOCK_SITES`). Nothing under `pipelines/` calls `datetime.now()` or `utcnow()`.
- **Unseeded randomness** (`KNOWN_UNSEEDED_RANDOM_SITES`, empty). Nothing under `pipelines/` draws unseeded random numbers.

The `pyproject.toml` import contracts bar `astrometricslib` from importing `wayfindinglib`, `backend` or `mcp_servers`.

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
