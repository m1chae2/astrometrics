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

## Parameters

| Call | Old | New |
| --- | --- | --- |
| `stars.get`, `analyze_periodicity`, `delete`, `update`, `create` | `object_id` | `star_id` |
| `processing.calibration.assess_flats` | `filter_type` | `filter_name` |
| `processing.calibration.measure_sensor_noise` | `filter_type`, `temperature` | `filter_name`, `temperature_c` |
| `targets.reindex_frames` | `filter_type` | `filter_name` |
| `processing.process_target(photometry={...})` | option `filter_type` | option `filter_name` |
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

## Left as they were, on purpose

- `visualization.get_last_captured_image` is separate from `render_fits`: it finds the newest file itself and
  returns `None` when there is none, while `render_fits` names a frame and raises an error.
- `targets.read_saved` is separate from `targets.get`: it reads the stored record and leaves the objects held in
  memory alone.
- `astronomy:list` and `astronomy:count` keep `filter_type`, which picks a kind of star list, not a filter.
- `wayfindinglib` keeps its own `get_advisory(object_id=)` and the sync RPC `object_id`.
