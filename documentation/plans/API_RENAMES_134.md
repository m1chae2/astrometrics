# API renames of issue 134

This table lists every old and new name from the `astrometricslib` API cleanup, so private scripts can be
updated. No old name is kept as an alias. Rows that only moved a call keep its arguments unless the table
says otherwise.

Rules the cleanup follows:

- `get(id)` reads one thing. `query(filters, detail=, limit=, offset=)` searches and returns a result model.
- `measure_*(target, kind=, include=)` measures.
- Changing data uses a clear verb. An optional write is off by default (`save=False`, `apply=False`).
- A parameter holding an id string is named `<thing>_id`. One that takes an object or an id is named `<thing>`.

## Removed calls

`stars.save_all`, `calibration.add`, `processing.discard_previous_stack` and `processing.swap_with_previous_stack` were removed. `calibration.get` and `calibration.library` became private.

## Calls

| Old | New |
| --- | --- |
| `quality.check_spectral_frames(target, ...)` | `quality.measure_frames(target, kind="spectral", ...)` |
| `processing.measure_noise_growth(target, maximum_steps=, include_night_contributions=, include_odd_even_difference=)` | `processing.measure_noise(target, kind="growth", maximum_steps=, include=[...])` |
| `processing.measure_night_contributions(target)` | `processing.measure_noise(target, kind="night_contributions")` |
| `processing.measure_odd_even_difference_noise(target, keep_halves=)` | `processing.measure_noise(target, kind="odd_even_difference", keep_halves=)` |
| `stars.plate_solve(path)` | `processing.plate_solve(path)` |
| `stars.detect_point_sources(data)` | `processing.detect_point_sources(data)` |
| `stars.tune_spectroscopy_calibration(...)` | `processing.tune_spectroscopy_calibration(...)` |
| `processing.record_frame_measurements(target, ...)` | `quality.record_frame_measurements(target, ...)` |
| `processing.calibration.refresh(kind, prune_missing=)` | `processing.calibration.reindex_frames(kind, prune_missing=)` |
| `quality.flag_value_outliers(...)` | Removed. Use `astrometricslib.pipelines.spectroscopy.utilities.registration_quality.flag_outliers` |
| `quality.spectral_registration_thresholds` | Removed. Use the constants `MIN_MATCHED_STAR_PAIRS`, `ZERO_ORDER_AMPLITUDE_OUTLIER_SIGMA` and `ZERO_ORDER_POSITION_JUMP_OUTLIER_SIGMA` in the same module |
| `processing.calibration.library` | Removed. Use `calibration.query(detail="counts")`, `calibration.reindex_frames(kind)` and `calibration.save()` |
| `processing.run_spectroscopy_by_session(astrometrics, target, frame_records, ...)` | `processing.run_spectroscopy_by_session(target, frame_records, ...)` |
| `processing.calibration.assess_flats(telescope=, camera_id=, filter_name=, gain=, offset=)` | `processing.calibration.assess(kind="flat", camera_id=, gain=, offset=, binning=, telescope=, filter_name=)`. One call for darks, biases and flats. It returns `list[CalibrationSetHealth]` (checks as `GateResult` records) in place of `list[FlatSetAssessment]`. No alias. MCP tool `calibration_assess_flats` is now `calibration_assess` |
| `CalibrationIngestReport.flat_assessments` (`flatAssessments`, a `list[FlatSetAssessment]`, flats only) | `CalibrationIngestReport.assessments` (`list[CalibrationSetHealth]`), the same model `processing.calibration.assess` returns. It covers darks and biases too, judged from stored numbers without measuring during the rescan; a new frame with no numbers gives `not_checked` with the reason. No alias |
| (new) | `processing.calibration.measure_frames(kind=None, camera_id=None, force=False)` measures dark and bias frames for `assess`. MCP tool `calibration_measure_frames` (withheld from the AI) |

## Parameters

| Call | Old | New |
| --- | --- | --- |
| `stars.get`, `analyze_periodicity`, `delete`, `update`, `create` | `object_id` | `star_id` |
| `processing.calibration.assess_flats` (now `assess`) | `filter_type` | `filter_name` |
| `processing.calibration.measure_sensor_noise` | `filter_type`, `temperature` | `filter_name`, `temperature_c` |
| `targets.reindex_frames` | `filter_type` | `filter_name` |
| `processing.process_target(photometry={...})` | option `filter_type` | option `filter_name` |
| `quality.measure_frames` | `include=["excluded"]` (and the `excluded` field of its reports) | Removed, with no alias. `quality.set_aside_frames(target)` is the one listing of set-aside frames; each record now has `restorable` and `restore_note` |
| `quality.measure_frames` | (none) | new `exposure_seconds` and `predict_exposure_seconds`, for `kind="spectral"` only |
| `processing.measure_noise` | (none) | `include` takes `"night_contributions"` and `"odd_even_difference"`. Left out, `kind="growth"` includes `"odd_even_difference"`; pass `include=[]` for neither |
| `quality.query` | (none) | new `limit` (default none) and `offset` |

## Return types

| Call | Old | New |
| --- | --- | --- |
| `jobs.query` | `dict` | `JobQueryResult` (fields: `jobs`, `job`, `log_lines`, `runs` and so on; `job.id` replaces `job["id"]`) |
| `targets.delete_images` | `dict` | `ImageDeletionResult` (`deleted`, `failed`) |
| `targets.imaged_field_centers` | `list[dict]` | `ImagedFieldCenters` (`centers`, a list of `FieldCenter`) |
| `processing.detect_point_sources` | `list[dict]` | `PointSourceDetection` (`sources`, a list of `PointSource` with `x_centroid`, `y_centroid`, `flux` and so on) |
| `processing.tune_spectroscopy_calibration` | `dict` | `SpectroscopyCalibrationResult` (`rms_error_nm` and so on) |
| `quality.query` | `list[QualityMeasurement]` | `QualityQueryResult` (`rows`, `total_matching`, `offset`, `truncated`) |

## RPC methods (the app's backend)

| Old | New |
| --- | --- |
| `processing:measure_noise_growth`, `processing:measure_night_contributions`, `processing:measure_odd_even_difference_noise` | `processing:measure_noise` with payload `target_id`, `kind`, `maximum_steps`, `include`, `keep_halves` |
| `processing:record_frame_measurements` | `quality:record_frame_measurements` |
| `astronomy:get`, `astronomy:analyze_periodicity`, `astronomy:delete` payload `object_id` | `star_id` |
| `analysis:analyze_image`, `analysis:get_results`, `analysis:cancel` payload `filter_type` | `filter_name` |
| `calibration:measure_sensor_noise` payload `filter_type`, `temperature` | `filter_name`, `temperature_c` |

The reply of `quality:query`, `images:delete` and `calibration:get_stats` keeps its shape.

## MCP tools

| Old | New |
| --- | --- |
| `quality_check_spectral_frames` | `quality_measure_frames` with `kind="spectral"` |
| `processing_measure_noise_growth`, `processing_measure_night_contributions`, `processing_measure_odd_even_difference_noise` | `processing_measure_noise` with `kind` |
| `star_detect_point_sources` | `processing_detect_point_sources` |
| `star_tune_spectroscopy_calibration` | `processing_tune_spectroscopy_calibration` |
| `star_plate_solve` | `processing_plate_solve` |
| `processing_record_frame_measurements` | `quality_record_frame_measurements` |
| `calibration_refresh` | `calibration_reindex_frames` |
| `quality_flag_value_outliers` | Removed |
| `quality_query` | Same name; new `limit` and `offset` arguments |

## `wayfindinglib` planning (`wayfinder.planning`)

Done on the same branch. No old name is kept as an alias. The MCP tool of each call is `planning_<name>`.

### Planning calls

| Old | New |
| --- | --- |
| `planning.resolve_target_coordinates(name)` and `planning.lookup_coordinates(name)` | `planning.lookup_coordinates(name)`, one call. The MCP tool `planning_resolve_target_coordinates` is removed |
| `planning.find_sources(ra_deg, dec_deg, radius_deg, magnitude_min=, magnitude_max=, include=["targets"], limit=100)` | `planning.query_sources(ra_deg, dec_deg, radius_deg, source=["targets", "stars"], magnitude_min=, magnitude_max=, limit=100)`. `include=[]` (stars only) becomes `source=["stars"]` |
| `planning.get_sources(ra_deg, dec_deg, radius_deg, include=["stars"], limiting_magnitude=, include_stars_without_catalog_magnitude=)` | `planning.query_sources(..., source=["targets", "stars"], magnitude_max=, include_stars_without_catalog_magnitude=, limit=None)`. The old `include=["online"]` is `source=["simbad"]`; targets are listed only when `source` has `"targets"` |
| `planning.get_library_star_summaries(ra_deg, dec_deg, radius_deg, magnitude_range=)` | `planning.query_sources(..., source=["stars"], magnitude_min=, magnitude_max=)` |
| `planning.get_online_catalog_sources(ra_deg, dec_deg, radius_deg, enabled_driver_names, magnitude_limit=)` | `planning.query_sources(..., source=["catalogs"], catalog_drivers=[...], magnitude_max=)` |
| `planning.get_imaged_field_centers()` | Removed. Use `astrometrics.targets.imaged_field_centers().centers`, a list of `FieldCenter` (read `center.right_ascension_deg`, not `center["right_ascension_deg"]`). The MCP tool `planning_get_imaged_field_centers` was already withheld; `target_imaged_field_centers` stays |
| `planning.create_plan("sequence", target=, plan_items=)` | `planning.create_sequence(target, plan_items)` |
| `planning.create_plan("package", target=, exposure_requests=, plan_items=, dither_config=, minimum_altitude_deg=, priority=, quality_weighting_enabled=, notes=)` | `planning.create_package(target, exposure_requests=, plan_items=, dither_config=, minimum_altitude_deg=, priority=, quality_weighting_enabled=, notes=)`. Exposure rows and the dither setting are plain dictionaries, checked by the library. A bad row raises `InvalidArgumentError` |
| `planning.create_plan("empty_session", night_id=)` | `planning.create_session(observing_night_id)` |
| `planning.create_plan("scheduled_session", requests=, site_profile=, telescope=, camera_id=, night_id=)` | `planning.create_session(observing_night_id, requests=[...], site_profile=, telescope=, camera_id=)`. `requests` is now what makes the session scheduled. `site_profile`, `telescope` and `camera_id` are optional here too (the recorded site and the active telescope and camera) |
| `planning.edit_queue(session_id, add=[...])` | `planning.add_to_queue(session_id, requests)` |
| `planning.edit_queue(session_id, order=[...])` | `planning.reorder_queue(session_id, entry_ids)` |
| `planning.edit_queue(session_id, remove=[...])` | `planning.remove_from_queue(session_id, entry_ids)` |
| `planning.edit_queue(session_id, set_exposures={...})` | `planning.set_entry_exposures(session_id, exposures)` |
| `planning.edit_queue(session_id, add_checklist=[...])` | `planning.add_checklist_items(session_id, items)` |
| `planning.edit_queue(session_id, framing={...})` | `planning.set_framing(session_id, framing)` |
| `planning.edit_package(package_id, exposure_requests=, dither_config=, minimum_altitude_deg=, priority=, notes=, name=, framing=)` | `planning.update_package(package_id, exposure_requests=, dither_config=, minimum_altitude_deg=, priority=, notes=, name=, framing=)` |
| `planning.edit_package(package_id, reopen=True)` | `planning.reopen_package(package_id)`. It can no longer come with value changes in one call |
| `planning.edit_package(package_id, dismiss=True, source_key=)` | `planning.dismiss_package(package_id, source_key=)` |
| `planning.edit_package(package_id, undismiss=True, source_key=)` | `planning.undismiss_package(package_id, source_key=)` |
| `planning.edit_package(package_id, delete=True)` | `planning.delete_package(package_id)` |
| `planning.edit_queue(session_id)` and `planning.edit_package(package_id)` with nothing to change | No longer possible for the queue, because each verb has one argument that must be given. `planning.update_package(package_id)` with no value still raises `InvalidArgumentError` |
| `planning.edit_queue(...)` with several changes in one call | One call per change. Each is its own write with its own revision. `expected_revision` stays on every call |
| `planning.get_plan(session_id)`, `planning.get_plan(night_id=)` | `planning.get_plan(session_id)`, `planning.get_plan(observing_night_id=)`. It reads one session. Neither argument raises `InvalidArgumentError` |
| `planning.get_plan()` (no argument), `planning.get_plan(include=["progress"])` | `planning.query_plans()`, `planning.query_plans(include=["progress"])` |
| `planning.get_plan(kind="packages", status=, target=, include_dismissed=)` | `planning.query_plans(kind="packages", status=, target=, include_dismissed=)` |
| `planning.get_plan(kind="suggestion_log", target=)` | `planning.query_plans(kind="suggestion_log", target=)` |
| `planning.get_plan(kind="packages", target=, pick="one")` | `planning.get_package_for_target(target)`. A new call, because it reads one package, not a list |
| `planning.get_advisory("tonight", at=)` | `planning.get_tonight_advice(at=)` |
| `planning.get_advisory("calibration", camera_id=, frame_type=, exposure_seconds=, filter=, gain_setting=, offset=, binning=, sensor_temperature_c=)` | `planning.get_calibration_advice(camera_id, frame_type, exposure_seconds=, filter_name=, gain=, offset=, binning=, temperature_c=)` |
| `planning.get_advisory(kind, target, object_id=)` with `kind="quality"` or `"result"` | Stays on `planning` for now, with the two kinds only and `target` required. It becomes `review.get_advisory` when the `review` group is added. The `calibration` and `tonight` kinds, and the arguments they used (`camera_id`, `frame_type`, `at` and the rest), are gone from it |
| `planning.list_catalog_driver_metadata()` | `planning.query_catalogs()` |
| `planning.deep_catalog_status(include=, healpix_level=, magnitude_limit=, sample_count=, query_timeout_seconds=, request_delay_seconds=, register_job=)` | `planning.get_deep_catalog_status(include=, healpix_level=, magnitude_max=, sample_count=, query_timeout_seconds=, request_delay_seconds=, register_job=)`. The parameter `magnitude_limit` is `magnitude_max`, as in `query_sources` |

### Planning parameters

| Call | Old | New |
| --- | --- | --- |
| `planning.calculate_panels` | `center_ra`, `center_dec` (text; a bare number was read as hours) | `ra_deg`, `dec_deg` (numbers, in degrees). This fixes a real bug: `calculate_panels("10.684", ...)` centered the panels at 160.26 degrees. `dec_deg` outside -90 to 90 raises `InvalidArgumentError`. Sexagesimal text is no longer read; convert it first |
| `planning.get_advisory("calibration", ...)` (now `get_calibration_advice`) | `filter`, `gain_setting`, `sensor_temperature_c` | `filter_name`, `gain`, `temperature_c` |
| `planning.get_plan` | `night_id` | `observing_night_id` |
| `planning.create_session` (was `create_plan` with a session kind) | `night_id` | `observing_night_id` |
| `planning.refresh_suggestions` | `target_ids` (a list) | `target`: one id or a list of ids |
| `planning.get_deep_catalog_status` | `magnitude_limit` | `magnitude_max` |
| `planning.query_sources` (was `find_sources`, `get_sources`, `get_online_catalog_sources`, `get_library_star_summaries`) | `limiting_magnitude`, `magnitude_limit`, `magnitude_range` | `magnitude_max`, with `magnitude_min` for the bright end |
| `planning.query_sources` (was `get_online_catalog_sources`) | `enabled_driver_names` | `catalog_drivers` |

### Planning return types

| Call | Old | New |
| --- | --- | --- |
| `planning.lookup_coordinates` | `dict` | `CoordinateLookup` (`id`, `name`, `kind`, `ra_deg`, `dec_deg`, `ra`, `dec`, `magnitude`, `spectral_type`). `result["ra_deg"]` becomes `result.ra_deg` |
| `planning.find_sources`, `get_sources`, `get_library_star_summaries`, `get_online_catalog_sources` | `dict`, `list[SkySource]`, `list[dict]`, `list[SkySource]` | `SourceQueryResult` (`sources`, a list of `SkySource`, then `total_stars` and `stars_truncated`). A library star is a `SkySource`, so the `targetIds` of the old summary rows is gone. The RPCs `planetarium:get_sources`, `planetarium:get_targets` and `planetarium:get_catalog_sources` still reply with a list of `SkySource` records |
| `planning.query_catalogs` (was `list_catalog_driver_metadata`) | `list[dict]` | `list[CatalogDriverInfo]` (`driver_name`, `display_name`, `maximum_query_radius_degrees`). The RPC `planetarium:list_catalog_drivers` still replies with the same dictionaries |
| `planning.get_constellation_lines` | `list[dict]` | `list[ConstellationLineSegment]` (`constellation`, `ra1`, `dec1`, `ra2`, `dec2`). The RPC `planetarium:get_constellation_lines` still replies with the same dictionaries |
| `planning.build_deep_star_catalog` | `dict` | `DeepCatalogBuildReport` (`pixels_total`, `pixels_previously_done`, `pixels_downloaded`, `pixels_failed`, `stars_added`, `elapsed_seconds`, `stopped_early`). `report["stars_added"]` becomes `report.stars_added` |

### Planning RPC methods

| Old | New |
| --- | --- |
| `plan:edit` payload with more than one of `add`, `order`, `remove`, `set_exposures`, `add_checklist`, `framing` | Refused with `invalid_argument`. Give exactly one. The UI never sent more than one; the payload fields and the reply are the same |
| `plan:edit_package` with `delete`, `dismiss` or `undismiss` together with a change, or with each other | Refused with `invalid_argument`, as before (the backend now checks it). `reopen` with value changes still works: the backend reopens first, then updates with the new revision. The payload and the reply are the same |
| `mosaic:preview` payload `center_ra`, `center_dec` (sent in degrees) | Same payload. The backend used to turn the numbers into text, and the library read a bare right ascension as hours, so the panels were centered in the wrong place. They are now centered on the degrees sent |

## wayfindinglib: `review`, `control` and `execution`

`Wayfinder` has a new sub-API, `wayfinder.review` (`wayfindinglib/api/review.py`, class `ObservationReview`). No
old name is kept as an alias.

| Old | New |
| --- | --- |
| `wayfinder.control.history` (class `HistoryControl`) | `wayfinder.review` (class `ObservationReview`, built from `control`) |
| `control.history.query` | `review.query` |
| `control.history.get_live_session_status` | `review.get_live_session_status` |
| `control.history.frame_guiding` | `review.frame_guiding` (stays its own call: it takes a target and a frame range, and `query` takes a night) |
| `control.history.get_performance_envelope` | `review.get_performance_envelope` |
| `control.history.save_ekos_session_context` | `review.save_ekos_session_context` (made private further down) |
| `group_alignment_attempts(attempts)` from `wayfindinglib` | `review.query_alignment_sessions(attempts)`; takes a list of plain dictionaries and returns `AlignmentSessionsResult` (`attempts`, `targets`) |
| RPC `telescope:get_performance_envelope` target `wayfinder.control.history` | `wayfinder.review`; the reply keeps its shape |
| MCP `observatory_history_query`, `_get_live_session_status`, `_frame_guiding`, `_get_performance_envelope`, `_save_ekos_session_context` | `review_query`, `review_get_live_session_status`, `review_frame_guiding`, `review_get_performance_envelope`, `review_save_ekos_session_context` |
| `review.query` returned a `dict`, a typed list or a `NightQuality` by `kind` | `ReviewQueryResult` (`wayfindinglib/models/session/review_query.py`). The analysis kinds fill named fields (`analysis`, `sessions`, `runs`, `model`, `total`, `shown`, `truncated` and so on) and drop the empty ones when dumped, so the data has the same keys as before. The typed kinds fill `night_list`, `target_changes` and `night_quality`; the backend reads those, so the RPC replies keep their shape. An MCP reply for those three kinds is now `{"kind": ..., "night_list": [...]}` and so on, not a bare list |
| (new) | MCP `review_query_alignment_sessions` (dropped: the backend calls it) |

### Made private

These members got a leading underscore. They are no longer public, no longer MCP tools, and no alias is kept.
Tests and the library's own code reach them by the new name. A private call runs exactly as before; monitor mode
still refuses the ones that command hardware (`wayfindinglib/test/test_monitor_mode_sends_no_commands.py` calls
them by their private names).

| Old | New |
| --- | --- |
| `execution.execute_meridian_flip`, `recover_fault`, `recover_guide_star_loss` | `execution._execute_meridian_flip`, `_recover_fault`, `_recover_guide_star_loss` |
| `control.mount.sync`, `compute_pointing_correction`, `run_polar_alignment_assist` | `control.mount._sync`, `_compute_pointing_correction`, `_run_polar_alignment_assist` |
| `control.imaging.save_focus_model` | `control.imaging._save_focus_model` |
| `control.guiding.pulse`, `get_image`, `drain_external_pulses`, `compute_correction`, `run_calibration`, `run_backlash_calibration`, `run_exposure_test`, `save_calibration`, `save_spectrum_analysis`, `save_run` | the same names with a leading underscore on `control.guiding` |
| `control.safety.assess`, `save_rule_set`, `execute_safe_state`, `open_enclosure`, `close_enclosure` | the same names with a leading underscore on `control.safety` |
| `control.equipment.save_commissioning_run` | `control.equipment._save_commissioning_run` |
| `review.save_ekos_session_context` | `review._save_ekos_session_context` |
| `control.driver`, `mount_driver`, `focuser_driver`, `filter_wheel_driver`, `camera_driver`, `guide_camera_driver`, `enclosure_driver`, `switch_driver`, `weather_driver`, `remote_transfer_driver`, `guiding_driver` | the same names with a leading underscore on `control` |
| `execution.create_recorder(guiding_service, indi_driver, snapshot_interval_seconds=)` | `execution.create_recorder(guiding_service, snapshot_interval_seconds=)`; the recorder takes the INDI session from `control` |

Deleted, because nothing (tests included) used them: `control.imaging.compute_focus_correction`,
`control.equipment.cooling_ramp_rate` and `control.equipment.summarize_device`. The functions they wrapped in
`tasks.control_tasks` stay, with their own tests.

The MCP tools for all of these are gone (`observatory_guiding_pulse`, `observatory_mount_sync`,
`observatory_safety_assess` and so on, and `execution_execute_meridian_flip`, `execution_recover_fault`,
`execution_recover_guide_star_loss`).

### The driver and the INDI worker

`Wayfinder` now builds its own INDI session. The backend no longer builds `SimulatorIndiInterface`,
`IndiWorkerClient` or `IndiWorkerProxy`.

| Old | New |
| --- | --- |
| `Wayfinder(config, astrometrics)`; the backend then set `wayfinder.control.driver` | `Wayfinder(config, astrometrics, *, simulated=None, separate_process=False)`. `simulated=None` uses the simulator when `ASTROMETRICS_TESTING` is set. `separate_process=True` runs the session in a process of its own (the worker), started on the first call that needs INDI |
| `ObservatoryControl(..., driver=)` | adds `simulated=` and `separate_process=` |
| `backend/services/infrastructure/indi_worker.py` (`IndiWorkerClient`, `IndiWorkerProxy`, `serve_requests`) | `wayfindinglib/drivers/indi_worker.py`, private; code unchanged apart from the imports. `control.close()` stops the worker |
| the backend's `SettingsService(config, driver=)` calling `driver.sync_configuration()` | `SettingsService(config, equipment=)` calling the new `control.equipment.apply_configuration()` |
| `AlignmentService.poll_external_syncs(indi_interface=)` reading `control.driver` | `AlignmentService.poll_external_syncs()` calling the new `control.mount.poll_external_alignment()` (returns `ExternalAlignmentPoll`) |
| `AlignmentService.get_polar_alignment` reading `control.driver` and the records | the new `control.mount.get_polar_alignment()` (returns `PolarAlignmentStatus`); the reply keeps its keys |
| `TelescopeService(driver=)` and `TelescopeService.indi_interface` | Removed; nothing read them |
| (new) MCP | `observatory_mount_poll_external_alignment`, `observatory_mount_get_polar_alignment` and `observatory_equipment_apply_configuration`, all dropped (the backend calls them) |

### `mount.status` and the divergence record

| Old | New |
| --- | --- |
| `control.mount.status(include=[...])` returned a `dict` with the mount, filter, focuser and camera readings | `control.mount.status()` returns `MountControlStatus` and reads only the mount: `ra`, `dec`, `altitude`, `azimuth`, `temperature`, `humidity`, `tracking_status`, `connection_status`, `target_name`, `pier_side`, `parked`, `track_mode`. It has no `include`, because nothing is left to choose |
| `mount.status` keys `filter`, `focuserPosition`, `cameraTemperature`, `cameraStatus` | `control.imaging.status(include=[...])` fields `current_filter`, `focuser_position`, `camera_temperature`, `camera_status`. New sections: `current_filter` and `camera`. `ImagingStatus` has the new fields |
| RPC `telescope:get_status` | Same reply: the backend merges the two reads |
| MCP `observatory_mount_status` argument `include` | Removed; use `observatory_imaging_status` for the filter, focuser and camera |
| `execution.record_divergence` | `execution.build_divergence_record` |
| RPC `execution:record_divergence` | `execution:build_divergence_record` (backend service method renamed the same way) |
| MCP `execution_record_divergence` | `execution_build_divergence_record` |

The function `record_divergence` in `tasks.execution_tasks.divergence_recording` keeps its name; it is not public.

### Result models for the remaining dictionary returns

Each model leaves out the fields a case did not fill (`SparseResult`), so the data keeps the keys of the old
dictionary. A name the model does not list is kept as an extra field.

| Call | Old | New |
| --- | --- | --- |
| `control.remote.frame_status` | `dict` | `FrameStatusReport` (`counts`, four `FrameDifference` fields, `telescope_error`, `library_error`, `note`) |
| `control.remote.sync_frames` | `dict` | `FrameSyncResult` (`success`, `target`, `to_transfer`, `transferred`, `measurements`, `succeeded`, `failed`, `job_id` and so on) |
| `control.remote.sync_logs` | `dict` | `LogSyncResult` (`dry_run`, `destination_dir`, `guide_logs` and `ekos_analyze_logs` as `LogPlan`, `ingested`) |
| `control.guiding.expose` | `Any` (a `bool` in practice) | `GuideExposureResult` (`started`, `exposure_seconds`, `gain`) |
| `review.frame_guiding` | `dict` | `FrameGuidingReport` |
| `review.query` | (see above) | `ReviewQueryResult`; its `model` field is now a `MountPointingModel` |
| `fit_reply_to_budget` | `wayfindinglib.fit_reply_to_budget` (`wayfindinglib/analytics/result_quality/reply_budget.py`) | `mcp_servers/common/reply_budget.py`. It is no longer exported from `wayfindinglib` |
| backend `TelescopePulse.polar_alignment` | annotated `PolarAlignmentStatus` | `dict[str, Any]`; the backend imports neither `PolarAlignmentStatus` nor `MountPointingModel` any more |

## Left as they were, on purpose

- `visualization.get_last_captured_image` is separate from `render_fits`: it finds the newest file itself and
  returns `None` when there is none, while `render_fits` names a frame and raises an error.
- `targets.read_saved` is separate from `targets.get`: it reads the stored record and leaves the objects held in
  memory alone.
- `astronomy:list` and `astronomy:count` keep `filter_type`, which picks a kind of star list, not a filter.
- `wayfindinglib` keeps its own `get_advisory(object_id=)` and the sync RPC `object_id`.
