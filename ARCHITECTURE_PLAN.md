# Architecture Consolidation Plan

This plan moves domain logic out of the backend, the user interface (UI), and the Model Context
Protocol (MCP) servers, and into `astrometricslib` and `wayfindinglib`. It also shrinks each
library's public API to a small set of general functions that take arguments. Today the libraries
expose many narrow, single-purpose functions instead.

The plan covers:

- the target layer rules,
- the rules for growing the library API,
- the target public API,
- where each piece of misplaced logic goes,
- the work needed to make the two libraries consistent,
- how the rules will be enforced,
- the order of work.

The findings come from a read-only review of the repository on 2026-10-04. The code is the source
of truth. Line numbers drift, so this plan names files and functions instead.

## 1. Target layering

The repository has four kinds of code. Each layer may call only the layer below it.

1. **Domain libraries** (`astrometricslib/`, `wayfindinglib/`). These hold all astronomy math,
   image processing, catalog rules, planning decisions, and hardware-control sequences. They never
   import `backend`.
2. **Delivery adapters**, which sit side by side at the same level:
   - `backend/` serves the desktop app over `/api/rpc`. RPC (remote procedure call) means the UI
     names a function and the backend runs it.
   - The MCP servers serve AI agents. MCP is the protocol AI clients use to call tools.
   - Both adapters call only the libraries' public API. They hold state, connections, and
     serialization, and no domain rules.
3. **Backend services** (`backend/services/`). These hold application state such as threads, the
   live INDI connection, job queues, and the event socket, plus orchestration. INDI is the
   hardware-control protocol the observatory uses. Services call the public library API and never
   import `backend.container` or `backend.routers`.
4. **UI** (`ui/`, `electron/`). The UI only draws data. It reaches the backend only through
   `callBackend(method, params)`. The exceptions are the event and terminal WebSockets and the
   static image file routes; CLAUDE.md should list these by name.

"Public API" means the names each library exports from its package root (`astrometricslib.__all__`,
`wayfindinglib.__all__`): the top-level class (`Astrometrics`, `Wayfinder`), its sub-API classes,
the data models those classes accept and return, the exceptions, and the drivers a caller must
inject. Everything else is internal.

## 2. Summary of findings

- **Import direction is correct.** Neither library imports `backend`. No service imports the
  container or the routers.
- **Logic sits too high.** About 1,900 lines of domain logic live in `backend/services/`. About 550
  of those lines copy functions the libraries already have, and several copies now give different
  answers from the library versions. The MCP servers and the UI hold smaller amounts of domain
  logic. Section 5 lists each item.
- **The public API is too wide.** The libraries expose about 110 single-purpose methods. Newer
  general methods sit beside them without replacing them. Examples of the general methods are
  `TargetCatalog.query`, `StellarCatalog.query`, `ObservatoryControl.night_history(kind=...)`, and
  `ProcessingPipelines.process_target(stages=...)`. The MCP servers build one AI tool for every
  public method by *reflection*, meaning they read the method list at run time. So every narrow
  method also becomes an AI tool. `backend/mcp/tool_dispositions.py` (about 1,270 lines) exists
  mainly to hide those tools again. Its `PROPOSED_TOOLS` table already describes 26 general tools
  that would replace about 110 narrow ones. About 8 of the 26 exist as library methods.
- **wayfindinglib's public surface is too narrow.** It exports 7 names and no models. The backend
  therefore imports wayfindinglib internals 11 times, and no lint rule catches it.
- **The two libraries differ in API style and structure.** Their API styles differ in:
  - sub-API size: `ObservatoryControl` has 112 public members;
  - where the work happens: astrometricslib does much of it inside `api/`;
  - error reporting: astrometricslib mostly returns `{"error": ...}`, wayfindinglib mostly raises;
  - argument names for targets, coordinates, time, and variants.

  Their structure differs in exceptions, driver interfaces, duplicated models, leftover packages,
  and file naming. Section 6 sets shared conventions and lists each difference.
- **Six confirmed bugs** turned up during the review. See Appendix A.

## 3. Rules for growing the library API

These rules decide whether a change becomes an argument on an existing function or a new function.

1. **Same data, different filter or level of detail: add an argument.** Use filter arguments,
   `detail=` (how much of each record to return), or `include=` (which optional sections to
   attach) on the existing `query` or `get`.
2. **A fixed set of variants that return the same kind of result: add `kind: Literal[...]`.**
   `night_history` already works this way. The public function only checks its arguments and hands
   the call to the right variant. Each variant's code lives in a private helper under `tasks/` or
   `pipelines/`.
3. **Different side effects mean separate functions.** A read, a data change, and a hardware action
   never share one function. The MCP profiles allow or block whole tools by class (`observe`,
   `compute`, `change-data`, `actuate`). A function that merges a read with a write takes the
   stricter class, and the read-only investigator profile loses the read. A boolean that turns a
   read into a write (`save=True`) breaks this rule too.
4. **Accept richer inputs instead of adding resolver functions.** For example, use
   `target: str | Target`, `time: str | Time | None`, and `objects: list[str | Target | StellarObject]`.
   The library converts the input once, in one private helper.
5. **No boolean flags that switch the algorithm.** Use `kind=` for that. A boolean is fine for a
   small option such as `refresh` or `dry_run`.
6. **An object accessor and a report are different functions.** `TargetCatalog.get` returns a
   `Target` for code to work with. `TargetCatalog.query` returns a bounded summary for people and
   agents. Both are needed, and neither should absorb the other.
7. **A narrow method that a general one replaces becomes a private helper.** For one release, a
   thin public alias stays in place and raises `DeprecationWarning`, because scripts, tests, and
   RPC method names still call it.

Two guards apply to every `kind=` or `include=` function:

- **Reject arguments that do not apply.** If an argument only matters for some kinds, the function
  raises `ValueError` when it gets that argument with any other kind. The docstring lists which
  arguments each kind uses. `night_history` needs this today: `session_id` only matters for some
  kinds, and `ekos_file_id` only for one.
- **Return typed models, not `dict[str, Any]`.** Each `kind` returns its own Pydantic model. The UI's
  TypeScript types are generated from Pydantic models (`build/codegen/generate_types.py`). About 60
  UI response types are written by hand because the general methods return plain dictionaries.

## 4. Target public API

The tables below turn `backend/mcp/tool_dispositions.py` `PROPOSED_TOOLS` into library methods,
with rule 3 applied. "Exists" means the method is already public and only needs the narrow methods
folded in. "Extend" means an existing method gains arguments or fields. "New" means a new public
name that replaces several old ones.

### 4.1 astrometricslib

| Method | Status | Replaces |
|---|---|---|
| `StellarCatalog.query(...)` | Exists | `get_object`, `find_by_id_or_name`, `find_all_by_id_or_name`, `existing_ids`, `find_by_position`, `list_objects`, `list_object_ids`, `list_object_summaries`, `list_object_summaries_in_region`, `list_objects_by_ids`, `list_objects_for_target`, `list_objects_in_region`, `list_spectrum_object_ids`, `get_audit` |
| `TargetCatalog.get(target_id, refresh=False)` | Extend | Object accessor. `refresh=True` re-reads the catalog from disk. |
| `TargetCatalog.query(...)` | Exists | `list`, `list_camera_names`, `camera_index` |
| `TargetCatalog.reindex_frames(target: Target \| None = None, paths=None, ...)` | Extend | `ProcessingPipelines.scan_target_directory`, `add_frame`, `ProcessingPipelines.create_frame_record`, and the backend's full-library reindex. `target=None` means every target. `paths` adds only those files. |
| `CalibrationCatalog.query(kind=None, refresh=False, ...)` | New | `stats`, `load`, `TargetCatalog.get_calibration_frame_statistics`. `CalibrationCatalog.get` stays as the object accessor. |
| `ProcessingPipelines.process_target(target: Target \| list[Target] \| None, stages=...)` | Extend | `run_astrometry`, `run_photometry`, `run_spectroscopy`, `MovingObjectRecovery.detect_asteroids` (a new `"asteroids"` stage), and `Astrometrics.process_all_targets`. `target=None` means every target. |
| `ProcessingPipelines.stack(target, frames=None, filter_name=None, first_file=None, last_file=None, since=None, until=None, plan_only=False, ...)` | New | `Astrometrics.stack`, `ProcessingPipelines.run_stacking`, and the exported `stack_frames`. One function selects the frames and stacks them. Siril options (rejection, weighting, output paths) stay as keyword arguments. |
| `ProcessingPipelines.remake_preview(...)` | Moved | `Astrometrics.remake_preview`. The root object keeps no methods (section 6.1). |
| `QualityDiagnostics.stack_quality(path_or_target, include=[...])` | New | `compare_stacks`, `ProcessingPipelines.compare_with_previous_stack`, `measure_stack_fwhm`, `measure_stack_rejected_fraction`, `parse_stack_registration_seq`, `parse_stack_zero_order_star` |
| `QualityDiagnostics.frame_quality(...)` | Exists | `check_raw_frames`, `ProcessingPipelines.preview_quarantine`, `TargetCatalog.measure_frame_input_quality`, and the read half of the proposed `excluded_frames` tool (`include=["excluded"]`) |
| `ProcessingPipelines.restore_excluded_frames(...)` | Exists | Stays separate, because it writes (rule 3). |
| `Visualization.render_fits(...)` | Exists | `convert_fits_to_png`, `convert_fits_to_png_with_stats`, `get_light_frame_data` |
| `Visualization.plot(kind, target, ...)` | New | `plot_target_dashboard`, `plot_star_dashboard`, `plot_astrometry`, `plot_photometry`, `plot_spectroscopy`, `plot_focus_vs_temperature`, `plot_asteroid_detection` |
| `Jobs.query(...)` | Exists | Also answers "is a sync running?" (`control.remote.is_syncing` today), because syncs run as jobs. |

### 4.2 wayfindinglib

`Wayfinder` keeps its three sub-APIs: `control` (operate the observatory), `planning` (decide what
to observe), and `execution` (run and recover an observing session). `ObservatoryControl` has 112
public members today. To bring it under the size limit in section 6.1, it gains seven children
named by topic. Each child is built once, when `control` is built.

Children share one internal context object that holds the configuration, the `DiskButler`, and the
driver lookup. They do not hold a reference back to `control`. The driver properties
(`mount_driver`, `camera_driver`, and the rest) stay on `control` as the place where tests and the
backend inject drivers. Setting one updates the shared context.

Moved methods keep their current names. Methods this plan creates take names relative to their
child, for example `control.remote.list(kind)` rather than `control.remote.list_remote(kind)`.
Every child that has readable state gets one `status(include=[...])` read. `include` chooses the
sections of the reply, such as live hardware readings and saved models.

| Attribute | What it covers | Public members after this plan | Folded in from today's `ObservatoryControl` |
|---|---|---|---|
| `control` | Driver injection | `driver`, `mount_driver`, `focuser_driver`, `filter_wheel_driver`, `camera_driver`, `guide_camera_driver`, `enclosure_driver`, `switch_driver`, `weather_driver`, `remote_transfer_driver` | `guiding_service` and `sync_service` go away (section 7.1) |
| `control.mount` | Pointing and tracking | `status`, `slew_to_target`, `slew_to_coordinates(ra_deg, dec_deg, center=False, tolerance_arcsec=..., max_iterations=...)`, `sync_coordinates`, `park`, `unpark`, `set_tracking`, `manual_move`, `abort_motion`, `set_slew_rate`, `compute_pointing_correction`, `run_polar_alignment_assist` | `get_telescope_status`, plus the INDI mount decoding in `backend/mcp/tool_registry.py` (pier side, park, tracking) |
| `control.imaging` | Main camera, filter wheel, focuser | `status`, `capture_image(exposure_seconds, count=1, filter_name=None, dither=False)`, `set_filter`, `focus_move`, `compute_focus_correction`, `save_focus_model` | `get_filter_names`, `get_focuser_position`, `active_focus_model` |
| `control.guiding` | Guide camera, guide pulses, guider models | `status`, `pulse_guide`, `guide_expose`, `get_guide_image`, `drain_external_pulses`, `compute_guiding_correction`, `run_guider_calibration`, `run_backlash_calibration`, `run_guide_exposure_test`, `refit_guiding_spectrum`, `save_guider_calibration`, `save_guiding_spectrum_analysis`, `save_guiding_run` | `active_guider_calibration`, `active_guiding_spectrum_analysis`, `guider_plate_scale_arcsec_per_px` |
| `control.remote` | The observatory computer's files and logs | `list(kind, folder_name=None, sizes=False)`, `check_remote_connection`, `frame_status`, `sync_remote_frames(target_id=None, dry_run=False, ...)`, `sync_remote_logs` | `list_remote_targets`, `list_remote_target_folders`, `list_remote_calibration_folders`, `discover_unassociated_remote_targets`, `list_remote_files`, `list_remote_files_with_sizes`, `check_for_new_remote_images`, `download_remote_frames`, `download_remote_targets`, `sync`, `sync_calibration_folder`, `sync_all_remote_folders`, `ingest_guiding_log_file`, `fetch_and_ingest_new_guide_logs`, `ingest_ekos_session_logs`. `is_syncing` moves to `Jobs.query`. |
| `control.history` | Past and current observing sessions | `night_history(kind, ...)`, `get_live_session_status`, `frame_guiding`, `get_performance_envelope`, `save_ekos_session_context` | `analyze_capture_session`, `analyze_guiding_session`, `analyze_sky_coverage`, `summarize_capture_sessions`, `summarize_guiding_sessions`, `summarize_recurring_issues`, `list_ekos_session_summaries`, `get_ekos_session_context`, `list_guiding_runs`, `get_pointing_model`. `night_history` gains `kind="alignment"` (section 5). |
| `control.safety` | Weather, enclosure, and who is allowed to act | `status`, `assess_safety`, `save_safety_rule_set`, `execute_safe_state`, `open_enclosure`, `close_enclosure`, `apply_promotion_decision`, `enter_monitoring_mode`, `enter_controller_mode` | `get_safety_rule_set`, `active_enclosure`, `get_enclosure_state`, `delegation_policy`, `summarize_divergence_evidence`, and `refresh_safety_assessment` if it only reads |
| `control.equipment` | The equipment profile and device connections | `status`, `connect`, `disconnect`, `set_active_telescope`, `set_active_camera`, `cooling_ramp_rate`, `summarize_device`, `save_commissioning_run` | `active_telescope`, `active_camera`, `active_guide_scope`, `active_guide_camera`, `list_camera_profiles`, `get_equipment_configuration`, `get_observer_location`, `indi_diagnostics`, `get_commissioning_runs` |

`control` drops from 112 public members to about 70, spread over the root and seven children. The
largest child, `control.guiding`, has 13. Every member of today's `ObservatoryControl` appears in
exactly one row above.

The MCP servers derive tool names from the attribute path. Moving members into children therefore
renames their tools, for example from `observatory_park` to `observatory_mount_park`.
`backend/mcp/tool_dispositions.py` and the RPC method names change with them.

`planning` and `execution` stay single classes:

| Method | Status | Replaces |
|---|---|---|
| `ObservationPlanning.get_visibility(objects, time=None, end_time=None, step_minutes=None, include=[...])` | Extend | `get_visibility_over_time`, `get_meridian_status` (`include=["meridian"]`), and the copy in `backend/services/data/stellar_service.py` |
| `ObservationPlanning.get_advisory(kind, ...)` | New | `get_target_quality_advisory`, `get_calibration_advisory` |
| `ObservationPlanning.calculate_panels(...)` | Exists | Stays the read/compute half of the proposed `plan_mosaic` tool. |
| `ObservationPlanning.create_mosaic(..., packages=True)` | New | `create_mosaic_targets`, `generate_mosaic_packages`, the write half of `plan_mosaic`. Both are writes, so they may share a function. |
| `ObservationPlanning.create_plan(kind, ...)` | New | `create_observation_package`, `create_empty_session`, `plan_observation_session` |
| `ObservationPlanning.edit_queue(session_id, add=None, order=None)` | New | `add_to_queue`, `reorder_queue` |
| `ObservationPlanning.get_plan(session_id=None)` | New | The direct `DiskButler` reads in `backend/services/observatory/execution_service.py`. `None` lists the sessions. This is the only new read that replaces nothing in the library. |
| `ObservationPlanning.deep_catalog_status(include_estimate=False)` | New | `get_deep_catalog_status`, `estimate_deep_catalog_size`. `build_deep_star_catalog` stays separate, because it writes. |
| `ObservationExecution.abort_session`, `reconcile_session` | Exist | Stay separate: one is a hardware action and the other a data change. This replaces the proposed `execution_session` tool. |

`ObservationExecution.reconcile_session` and several `ObservationPlanning` methods take an
`astrometrics` argument today. That argument goes away: `Wayfinder` holds one `Astrometrics`
handle and passes it down (section 6.2, item 3).

### 4.3 Backend MCP tools

`app_controls` mixes UI actions (navigate, show a notification) with pipeline actions (pause,
resume). Split it into two tools with different classes.

## 5. Logic to move into the libraries

Each row names the current location, what the code does, and where it goes. The destinations
follow section 4, and no row adds a function beyond those listed there.

### 5.1 Copies to delete (the library function already exists)

| Current location | Library function to call instead |
|---|---|
| `backend/services/observatory/target_imaging_planner.py` `create_sequence_plan` | `ObservationPlanning.create_sequence_plan`, then `create_plan` |
| `backend/services/observatory/mosaic_service.py` `create_mosaic_targets` | `ObservationPlanning.create_mosaic_targets`, then `create_mosaic` |
| `backend/services/data/stellar_service.py` spectral-class labels, aliases, summary, and by-class listing | `StellarCatalog.spectral_class_counts`, `StellarCatalog.query(spectral_class=...)` |
| `backend/services/data/stellar_service.py` `get_visible_targets` | `ObservationPlanning.get_visibility` |
| `backend/services/observatory/observatory_service.py` humidity safety rule | `control.safety.assess_safety` |
| `backend/services/observatory/telescope_service.py` `_infer_target_at_coordinates` (small-angle distance, 1° match) | `TargetCatalog.query(ra=..., dec=..., radius_deg=...)` |
| `backend/services/analysis/analysis_orchestrator.py` frame classification (spectroscopy versus photometry) | `ProcessingPipelines.process_target(stages=...)`, which already chooses, using `frame_is_spectral` |
| `backend/services/infrastructure/sync_service.py` folder layout, calibration and log sync | `control.remote.sync_remote_frames`, `control.remote.sync_remote_logs` |
| `backend/services/processing/ingestion_service.py` remote folder name matching and calibration folder loop | `control.remote.list`, `control.remote.sync_remote_frames`. The library name matcher is the single rule. |
| `backend/services/data/image_service.py` `get_filter_type` | `FrameRecord.normalize_filter` (the backend copy appears unused) |
| `backend/services/observatory/alignment_service.py` raw `sqlite3` read of `astrometrics.db` | `TargetCatalog.query`, with night ids from `observing_night_id` |

### 5.2 Logic to move

| Current location | What it does | Destination |
|---|---|---|
| `backend/services/observatory/guiding_service.py` | Merges guide pulses, converts pulses to arcseconds, computes root-mean-square (RMS) guiding error, and runs a simulated guiding loop | A guiding driver chosen by the `protocol` setting (`phd2`, `internal`, `simulator`), using the same driver registry `mount_driver` uses. RMS goes into `control.history.get_live_session_status`. The backend keeps only thread start and stop. |
| `backend/services/observatory/alignment_service.py` | Runs the capture, plate-solve, sync, re-slew loop, and reads the solved center from the WCS header. WCS (world coordinate system) is the FITS header block that maps pixels to sky positions. | `control.mount.slew_to_coordinates(center=True)`, using `control.mount.compute_pointing_correction` |
| `backend/services/infrastructure/sync_service.py` pointing-error extraction | Parses FITS headers for commanded and solved positions and computes the pointing error | A post-download step of `control.remote.sync_remote_frames`. The results are read through `control.history.night_history(kind="alignment")`. |
| `backend/services/observatory/target_imaging_executor.py` | Keeps its own observation queue and run loop | `ObservationPlanning.edit_queue` and `ObservationExecution.advance_session` |
| `backend/services/observatory/imaging_service.py` | Runs a capture loop with filter changes and dithering. Dithering means shifting the pointing slightly between exposures. | `control.imaging.capture_image(count=..., dither=...)` |
| `backend/services/data/stellar_service.py` overlay code | Projects catalog stars onto an image through its WCS and ranks them | `StellarCatalog.query(target_id=..., detail="overlay")`, which adds pixel positions |
| `backend/services/data/stellar_service.py` filtering and paging | Hides single-frame detections and invalid magnitudes, sorts, and pages | `StellarCatalog.query`, with an `include_unresolved=False` default |
| `backend/services/data/stellar_service.py` planetarium sources | Chooses the longest LIGHT exposure and applies magnitude limits | `ObservationPlanning.get_sources`, returning typed models |
| `backend/services/data/target_service.py` | Encodes the `frames/lights/` layout and the processed-image extension rule | `TargetCatalog.reindex_frames`, `TargetCatalog.query` |
| `astrometricslib/mcp/reflection.py`, `wayfindinglib/mcp/argument_resolution.py` | Look up a target by name with a fresh read, resolve sky objects through the library or SIMBAD (an online star database), parse `"now"` and ISO times, inject the `Astrometrics` handle | Rule 4: the library methods accept `str \| Target` and `str \| Time`, and `TargetCatalog.get(refresh=True)` does the fresh read. Remove the `astrometrics` argument. The MCP code then shrinks to argument passing. |
| `astrometricslib/mcp/reflection.py` job wrapping and quality snapshots | Runs calls as background jobs and records stack quality before and after | A consistent `register_job=` argument, as `process_target` already has. `process_target` returns the quality summary. |
| `astrometricslib/mcp/tool_registry.py`, `backend/services/data/image_service.py`, `astrometricslib/utilities/config_loader.py`, `astrometricslib/utilities/storage_mount.py` | Four copies of the data-folder path check and the `/media` to `/run/media` swap | One helper built from `storage_mount._path_variants` and `_is_below`, exposed on `AppConfiguration` |
| `backend/mcp/tool_registry.py` `app_status` | Decodes INDI mount properties | `control.mount.status` |
| `ui/planetariumDisplay/utils/alignmentClustering.ts` | Groups plate-solve attempts into sessions, then computes RMS jitter and drift rates | `control.history.night_history(kind="alignment")`, built on the existing `get_alignment_sessions` so that "alignment session" has one definition. The RA average must wrap at 0h/24h. |
| `ui/planetariumDisplay/layers/TrackingRiskOverlay.ts` | Scores mount risk by sky position (meridian side, high declination, low altitude) and by measured RMS | A field on the `PerformanceEnvelope` model that `control.history.get_performance_envelope` returns. The UI only colors it. |
| `ui/astronomyManager/utils/starDisplayFormat.ts` | Applies spectral-match thresholds: poor fit, disagreement with the catalog, separation between candidates | Fields on the spectroscopy result: `is_poor_match`, `differs_from_catalog`, `candidate_separation` |
| `ui/common/hooks/targetListFiltering.ts` | Classifies targets by name (solar system body, Messier, NGC, IC, comet) | An `object_type` field on `Target` and a `TargetCatalog.query(object_type=...)` filter, built on `is_solar_system_target` |
| `ui/common/fitsViewer/mtfStretchGL.ts` | Computes auto-stretch parameters with a different method from the library: background 0.05 instead of 0.25, standard deviation instead of the median absolute deviation | Stretch parameters as fields on the `ViewableImage` that `render_fits` returns. The UI keeps the GPU drawing. |
| `ui/planetariumDisplay/hooks/useEquipmentConfiguration.ts`, `StarOverlay.ts`, `StellarAnalysisDetails.tsx` | Compute plate scale and field of view, and hold copied magnitude limits and minimum point counts | Fields from `control.equipment.status` and `StellarCatalog.query` (`has_catalog_magnitude`, `can_run_period_search`) |
| `backend/main_backend.py` IERS setup and the AltAz warm-up | Configures offline Earth-rotation data (IERS) in four places | One library helper, called by both libraries and the backend |

### 5.3 Logic that stays in the UI

The planetarium projection (`ui/planetariumDisplay/utils/projectionMath.ts`, local sidereal time in
`CelestialSkyMap.tsx`) redraws at 60 frames per second and stays in the UI as drawing code. Two
follow-ups apply:

- Add a test that compares the TypeScript results with `wayfindinglib/astronomy/coordinate_transforms.py` at fixed reference times.
- Check whether the mount position reaches the UI in the current-epoch frame (JNow) while catalog
  stars use J2000. The two frames differ by about 0.36° in 2026.

## 6. API conventions and library consistency

### 6.1 Shared API conventions

Both libraries follow the conventions below. The counts come from parsing every public method on
the API classes: 95 in astrometricslib and 150 in wayfindinglib. Section 10 lists the two
conventions that still need a decision.

| Convention | astrometricslib changes | wayfindinglib changes |
|---|---|---|
| **Root object.** `Astrometrics` and `Wayfinder` only build and hold sub-APIs. They have no methods of their own. | Move `stack`, `remake_preview`, and `process_all_targets` to `processing` (section 4.1). | None. |
| **Nesting.** Root object, then sub-APIs, then children: at most two levels. A sub-API gets children named by topic when it would pass about 25 public members. Each child is built once and shared. | Keep `processing.calibration` and `processing.diagnostics`. `TargetCatalog` uses `processing.calibration` instead of building a second `CalibrationCatalog`. | `control` gains seven children (section 4.2). |
| **Constructors.** Every sub-API takes `(config: AppConfiguration, storage, *, drivers...)` with real types. Children receive a shared context object, not a reference to their parent. | `Visualization` takes the whole `Astrometrics` object, typed `Any`. `Jobs` types `config` as `Any`. `MovingObjectRecovery` takes no configuration. `TargetCatalog` types `catalog_access` as `object`. | `ObservationPlanning` and `ObservationExecution` gain `config`. Remove the `astrometrics=` argument. |
| **Thin API layer.** API methods check arguments and hand off. The work happens in `pipelines/` (astrometricslib) or `tasks/` (wayfindinglib). | Move `api/target_overview.py`, `api/star_analysis.py`, `api/stellar_operations.py`, and `api/batch.py` out of `api/`. Move the long bodies of `frame_quality`, `spectral_frame_check`, `Astrometrics.stack`, `StellarCatalog.query`, and `find_or_create_by_position` too. | Move `_derive_performance_envelope` and the night analysis helpers into `tasks/`. |
| **Errors.** Pending decision 7. The recommendation: a bad argument raises `ValueError` or the library's own error type, a missing item makes `get` return `None`, and the MCP and RPC adapters turn exceptions into error replies. | 31 `return {"error": ...}` sites. | 4 `return {"error": ...}` sites. |
| **Results.** Pydantic models, one per `kind`. | 26 methods return `dict`. | 22 methods return `dict`. |
| **Naming the subject.** Pending decision 8. The recommendation: `target: str \| Target` everywhere, and `<noun>_id` for every other identifier. | `camera` and `camera_name` become `camera_id`. | `target_name` becomes `target`. |
| **Units in names.** `ra_deg`, `dec_deg`, `radius_deg`, `tolerance_arcsec`, `exposure_seconds`. | `ra`, `dec`, and `radius` on `TargetCatalog.query`, `StellarCatalog.query`, and the region methods. | `ra` and `dec` on `slew_to_coordinates` and `sync_coordinates`. |
| **Time.** `since`, `until`, and `time` accept `str \| datetime \| Time \| None`. An observing night is `night_id: str`. | `since` and `until` accept only ISO strings today. | `time_input: Any` becomes `time`. `night_date: date` and `session_id` used to name a night become `night_id`. |
| **Choosing a variant.** `kind=` picks one variant. `include=` adds optional sections. `detail=` sets how much each record says. The libraries use no `mode=` argument and no boolean that switches variants. | `frame_quality(mode=...)` becomes `kind=`. `spectral: bool` on `stack_summary`, `compare_with_previous_stack`, `discard_previous_stack`, and `swap_with_previous_stack` becomes `kind=`. `include_fwhm` and `include_spectra` become `include=`. | `frame_guiding(include_quality=...)` becomes `include=`. |
| **Background jobs.** Long-running methods use `@background_job` and take `register_job: bool = True`. | 14 decorated methods; `register_job` appears on 4. | 4 decorated methods; `register_job` appears on 1. |
| **Exports.** The package root exports the root object, the sub-APIs, the models, the exceptions, and the driver base classes. A test pins the list. | Remove about 35 internal names, among them `AstrometryPipeline`, `StarIdentifier`, `FrameSelection`, `select_library_frames`, `close_interrupted_jobs`, `resolve_worker_counts`, `run_parallel_batch`, `DbLogHandler`, `LoggerInterface`, `run_siril_stack`, `stack_frames`, `ImageProcessing`, `SATURATED_FRAME_FRACTION`, and `SATURATED_BLOB_MINIMUM_PIXELS`. Stop importing `background_job` into the package root, where wayfindinglib picks it up. | Add the models and exceptions. Add a public-surface test like `astrometricslib/test/test_public_surface.py`. |
| **`api/__init__.py`.** One lazy-export lookup table and one statement of how to import. | Its docstring says not to import from `api`. Align the guidance. | Replace the `if` chain with the lookup table. |
| **Docstrings.** numpydoc, with no architecture section numbers and no mention of MCP or agents. | 13 mentions of MCP or agents, for example "Called through the MCP server, this runs as a background job". | 26 `§` citations, some to sections that do not exist, and 7 mentions of MCP or agents. |

### 6.2 Library consistency work

| # | Item | Action |
|---|---|---|
| 1 | Duplicate models and a second storage path in wayfindinglib: two `ObservationSession` classes, two `EquipmentConfiguration` classes, a copy of `AbstractButler`, and `drivers/local_database.save_model`/`load_models` | Keep one model in `models/`. Use `datastore.AbstractButler`. Send all storage through `DiskButler`, matching astrometricslib's single path through `CatalogAccess`. |
| 2 | Leftover packages: `wayfindinglib/sky.py`, `observation.py`, `observationlib/`, `observatorylib/` | Move logic into `tasks/planning_tasks`, models into `models/`, and configuration reads into `data_access/`, then delete the packages. |
| 3 | wayfindinglib builds a fresh `Astrometrics` at about 18 sites, and some ignore the configuration they were given | `Wayfinder` holds one injected handle and passes it down. |
| 4 | Exceptions: `astrometricslib/utilities/exceptions.py` `AstroLibError` versus `wayfindinglib/exceptions.py` `AstrometryHardwareError`, with stray `RuntimeError` and `ValueError` subclasses | Each library gets a top-level `exceptions.py` with one base class (`AstrometricsError`, `WayfindingError`). Every library error derives from it. The old names stay as aliases for one release. |
| 5 | Driver interfaces: astrometricslib mixes a typing `Protocol`, an `Abstract*` class, and concrete-only classes | Use wayfindinglib's style, `abc.ABC` base classes named `*Driver`, for the Siril, plate-solve, and SIMBAD interfaces. Rename `wayfindinglib/drivers/protocols/`, because it holds abstract classes, not typing Protocols. |
| 6 | Shared infrastructure owned by astrometricslib: the MCP registry and reflection, `job_logging`, and the configuration loader | Move them to a neutral package next to `datastore/`, so wayfindinglib stops importing astrometricslib internals. |
| 7 | File names: `wayfindinglib/api/*_registry.py` hold ordinary classes, not registries | Rename the files to `control.py`, `planning.py`, `execution.py`. Put the `control` children in a `control/` package with one module per child. |
| 8 | Scripts: 16 of 23 astrometricslib scripts and `wayfindinglib/scripts/build_deep_star_catalog.py` import internals | Scripts use the public API only, or the scripts README states the exemption. |
| 9 | Alignment logs live in `astrometricslib/drivers/logger_interface.py`, but mount alignment is a wayfindinglib concern | Decide which library owns alignment records before building `control.history.night_history(kind="alignment")`. |
| 10 | Documentation drift: garbled "astrometrics" wording in wayfindinglib docstrings, citations to architecture sections that do not exist, 353 `ruff: ignore` suppressions in wayfindinglib | Fix these as each file is touched, per CLAUDE.md. `Wayfinding_Library_Architecture.md` gains the `control` children. |

## 7. Backend and MCP structure

### 7.1 Backend

- **Router logic.** Move the router logic in `backend/routers/rpc_router.py` into services:
  - `_save_config` calls the private `indi_driver._sync_config()`.
  - `_start_alignment` parses coordinates.
  - `_get_session_alignment` builds a default reply.
- **Calls that skip the services.** The lambdas that call the `Wayfinder` and `Astrometrics`
  objects directly, and `calibration:get_stats`, go through services.
- **Dynamic fallback.** The router's dynamic fallback exposes every public library method over RPC.
  Replace it with the explicit list of section 4 methods. Delete the dead `method_aliases` entries
  and the unused `resilient_service` decorator.
- **Routes in `main_backend.py`.** Move the handoff, figure, and WebSocket routes into
  `backend/routers/` modules, and startup tasks into `backend/startup.py`.
- **Dependency inversion.** Remove it in `backend/container.py`: today the container hands the
  backend's `GuidingService` and `SyncService` to `wayfinder.control`, so library code calls
  backend objects. The guiding driver (section 5.2) and the library-owned sync remove both
  injections.
- **Production stubs.** Remove the test code that runs in production:
  - the `unittest.mock.Mock` branches in `analysis_orchestrator.py` and `stellar_service.py`;
  - the dummy `alignment_latest.fits` writer in `imaging_service.py`.

### 7.2 MCP servers

- Move the MCP packages out of the libraries into one top-level package beside `backend/` and
  `ui/`. The package name is open (section 9). It must not be `mcp/`, which would hide the `mcp`
  SDK on `sys.path`. Suggested layout:
  - `common/`: registry, reflection, profiles, serialization;
  - `astrometrics_core/` and `wayfinding_core/`: reflection only;
  - `backend/`: HTTP only;
  - `gaps/`;
  - `devtools/`: type-generation check, UI checks, test runners;
  - `inventory/`: tool inventory, dispositions, manifests.
- Make `mcp` an optional dependency in `pyproject.toml`, so the domain libraries no longer need the
  MCP SDK.
- Merge the two `ToolRegistry` classes (`astrometricslib/mcp/tool_registry.py`,
  `backend/mcp/tool_registry.py`) and the five near-identical server entry points.
- Keep one profile definition in Python and generate the TypeScript constants from it. Today
  `profile.py`, `ui/mcp/src/profile.ts`, and `tool_dispositions.INVESTIGATOR_CLASSES` disagree.
- Delete `backend/mcp/tools/*` and `backend/mcp/mcp_diagnostics.py`. Nothing imports them, and
  they contain broken paths. This also removes the second tool named `typegen_contract_validator`.
- Route `_active_jobs` and the notifications resource in `backend/mcp` through `/api/rpc`.
- After section 4 is done, trim `tool_dispositions.py` to a tool-class table. The library API itself
  then carries the consolidation that `PROPOSED_TOOLS` records today.

## 8. Enforcement

| Check | Purpose |
|---|---|
| Ruff `TID251` banned-API entries for `wayfindinglib.drivers`, `.tasks`, `.models`, `.analytics`, `.session_analysis`, `.data_access`, `.observationlib`, `.observatorylib`, `.watchdog`, plus `astrometricslib.visualization`, with a per-file exemption for each library's own tree | Blocks the internal imports that pass lint today |
| import-linter contracts: layers `backend.routers` above `backend.services` above the libraries; libraries never import `backend`; `backend.services` never imports `backend.container` or `backend.routers`; the MCP package imports only public library names | Turns the CLAUDE.md layer rules from convention into a failing check |
| Public-surface tests for both libraries | Keeps the export lists from growing or leaking again |
| A test that the generated MCP tool list equals the public API minus the dispositions table | Makes every new public method a deliberate choice of AI tool |
| A CI check that `ui/common/types/backendTypes.ts` matches a fresh code generation run | Keeps UI types in step with the Pydantic models |
| `serialize_rpc_result` converts NumPy values with `.item()` and `.tolist()`, and fails or warns on unknown types instead of calling `str()` | Makes violations of the CLAUDE.md serialization rule visible |

## 9. Order of work

Each phase ends with `ruff check`, the affected `pytest` suites, and, for `ui/` changes,
`npm run type-check` and `npm test`.

1. **Fix the confirmed bugs** in Appendix A.
   Done when: each bug has a test or a manual check that shows the fix.
2. **Add guardrails.** Add the `TID251` entries and the import-linter contracts, with an explicit
   allow-list of today's violations so CI stays green.
   Done when: CI runs both checks, and the allow-list is the to-do list for phases 3 to 5.
3. **Delete the copies in section 5.1.**
   Done when: those backend functions are gone, and the RPC methods call the library functions.
4. **Consolidate the library API (section 4) and apply the conventions in section 6.1.** Add
   arguments and new general methods. Split `control` into its children. Turn the narrow methods
   into private helpers with deprecated aliases. Return typed models.
   Done when: both public-surface tests pin the section 4 list, and the MCP tool list matches it.
5. **Move the logic in section 5.2.**
   Done when: the allow-lists from phase 2 are empty.
6. **Restructure the MCP servers (section 7.2) and the backend (section 7.1).**
   Done when: the domain libraries contain no `mcp/` package, and `main_backend.py` holds only app
   setup.
7. **Finish the library consistency work (section 6.2).**
   Done when: each library has one exception base, one model per concept, and no leftover packages.
8. **Remove the deprecated aliases** after one release.

Phases 3 and 4 can overlap. Phase 5 depends on phase 4, because the moved logic lands in the
consolidated methods.

## 10. Decisions for the owner

Decided:

- **wayfindinglib grouping (2026-10-04).** `Wayfinder` keeps `control`, `planning`, and
  `execution`. `control` gains children named by topic (section 4.2). Two levels of nesting is the
  rule for both libraries (section 6.1).

Open:

1. The name and location of the top-level MCP package (for example `agent_servers/`).
2. How long deprecated aliases stay: one release, or until no caller in the repository uses them.
3. Whether maintenance scripts may import library internals.
4. Which library owns alignment records (section 6.2, item 9).
5. Whether `control.mount.slew_to_target` folds into `slew_to_coordinates` through
   `target: str | Target`.
6. The exact list of non-RPC routes the UI may use (WebSockets, static images, handoff), to record
   in CLAUDE.md.
7. How the libraries report a bad argument. The recommendation is to raise an exception and let
   the MCP and RPC adapters turn it into an error reply. The alternative keeps the
   `{"error": ...}` replies that astrometricslib returns today, in both libraries.
8. How callers name a target. The recommendation is `target: str | Target` everywhere. The
   alternative is `target_id: str` everywhere, with `TargetCatalog.get` as the only way to obtain
   a `Target`.

## Appendix A. Confirmed bugs

Each of these was checked against the code during the review.

1. `backend/mcp/tool_registry.py` imports `serialize_rpc_result` from `backend.routers.rpc_router`,
   which does not define it; it lives in `backend/services/rpc_protocol.py`. The in-process
   path of `execute_rpc` raises `ImportError`.
2. `ui/App.tsx` and `electron/ipc_handlers.js` send the emergency park request to
   `/api/telescope/park`, a route the backend does not have, and both ignore the error. The tray
   park button does nothing. The fix is to call the mount park RPC method through `callBackend`.
3. `backend/services/observatory/alignment_service.py` computes the RA error as
   `(solve.ra - target_ra) * 3600`, with no cos(dec) factor and no wrap at 0°/360°. Near the
   celestial pole or across 0h, the reported error is wrong.
4. `backend/services/observatory/guiding_service.py` adds `random.gauss` noise to guide-pulse
   measurements from real INDI hardware, so recorded guiding data includes invented noise.
5. `ui/imageViewerDisplay/targetDetailsManager/targetDetails/TargetDetails.tsx` formats both RA and
   Dec with `formatRaDecString`, so RA in hours appears with degree symbols.
6. `backend/services/rpc_protocol.py` `serialize_rpc_result` falls back to `str(obj)`, so NumPy
   integers, booleans, and arrays reach the UI as strings.

Other issues the review reported but did not re-check:

- `backend/container.py` assigns `wayfinder.control.driver` before the driver exists. A later
  assignment corrects it.
- `astrometricslib/mcp/tools/contract_validator.py` scans from the repository root, not from the
  library its docstring names.
- The planetarium alignment clustering averages RA without wrapping at 0h/24h.
