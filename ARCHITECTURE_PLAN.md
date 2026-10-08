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

## Status

Status on 2026-10-08: phases 1 to 8 of section 10 are done. Sections 2 to 9 describe the review
and the target design. Some of the paths they name have since moved, for example
`backend/mcp/tool_dispositions.py` is now `mcp_servers/inventory/tool_dispositions.py`.

### Done

- **Phases 1 to 3.** The Appendix A bugs are fixed. The enforcement checks in section 9 run in CI:
  - `TID251` banned-API entries;
  - eleven import-linter contracts in `pyproject.toml`;
  - public-surface tests for both libraries;
  - the served-tools test in `mcp_servers/inventory/test/`;
  - the backend public-interface test;
  - the ESLint `fetch`/`WebSocket` rule;
  - the generated-types currency test;
  - the `BLE`, `TRY`, `LOG`, `G`, and `T20` rules and the `logging.basicConfig` ban.

  `astrometricslib/foundation/` holds the error model, `configure_logging`, the log context, the
  job framework (`foundation/jobs/`), and the storage classes (`foundation/storage/`).
- **Phases 4 to 6.** The section 5.1 copies and the section 6.3 legacy code are gone. The library
  API has the section 4 shape, including the seven `control` children. Library code raises the
  section 7.2 categories. The section 5.2 logic lives in the libraries.
- **Phase 7.** The MCP servers live in `mcp_servers/`, and `mcp` is an optional dependency. The
  backend declares its public interface in `backend/public_interface.py`. Startup work lives in
  `backend/startup.py`, and the routes live in `backend/routers/`.
- **Phase 8.** Section 6.2 items 1 to 11 are done:
  - one storage path through `DiskButler`;
  - no leftover packages;
  - one injected `Astrometrics` handle;
  - one exception tree;
  - `abc.ABC` driver interfaces in `drivers/interfaces/` in both libraries;
  - job records in `foundation/jobs/`;
  - alignment and guiding records in `wayfindinglib/drivers/control_record_store.py`.

  Scripts follow the item 8 rule. The import-linter contract "a library script never imports
  another script" checks it, and import-linter also keeps `wayfindinglib/scripts/` on
  astrometricslib's public API. Library docstrings no longer cite architecture section numbers or
  mention MCP and agents.

### Behavior changes

- The alignment overlay groups plate-solve attempts by target in the library
  (`AlignmentTargetSession`). The cumulative view no longer merges live attempts.
- The tracking-risk map blends measured guiding error by hour angle and declination at the time
  of each observation, not by RA and Dec today. With no equipment profile, the map is empty.
- The candidate separation of a spectral match ranks candidates by RMS (root-mean-square) fit
  error, not by the order the backend returned them.
- Ceres is classified as a planet (`TargetObjectType`). A target with no object type appears only
  under the "All" and "No Image" filters.
- The mount's INDI readback and commands convert between the current-epoch frame (JNow) and J2000
  in `hardware_operations`.
- The queue runner applies the real safety gate. An `UNKNOWN` safety verdict suspends the queue.
- The internal guiding protocol's `run_cycle` raises `ConfigurationError`, because the library has
  no guide-star measurement step. The PHD2 protocol's `run_cycle` raises `ConflictError`.

### Remaining

- Nothing in the UI passes `stretchParameters` yet. No current UI flow shows a raw FITS file with
  stretching on.
- `astrometricslib/pipelines/spectroscopy/processing/spectral_classifier.py` keeps its own
  `POOR_MATCH_RMS_THRESHOLD` and spectral-letter order beside the ones in
  `models/stellar_source.py`.
- The UI keeps its own `PlanetariumSource` type instead of the generated `SkySource`. The two
  differ in UI-only fields and in which fields may be empty.
- Positions that Ekos writes when it syncs the mount itself are in JNow. Their conversion is not
  checked.
- Two copies of the calibration inventory models remain: `CalibrationEntry` and
  `CalibrationStats` in both `wayfindinglib/models/equipment_and_site/calibration.py` and
  `astrometricslib/models/calibration_inventory.py`.
- The meridian-flip delay has two sources that can disagree. Planning reads
  `meridian_flip_delay_min` from the `[Observatory.Telescope]` configuration section (default 5
  minutes). The `Telescope` model derives its own `meridian_flip_delay_min` from
  `flip_hour_angle_deg`.
- Only the method names of the UI's `ActionRegistry` are generated. Its payload types are written
  by hand.
- INDI image delivery for the main camera is not turned on, so `control.imaging.capture_image`
  returns no image from real INDI hardware.
- The internal guiding protocol cannot run a guide loop on its own (see "Behavior changes").
- The sequencer (`ObservationExecution.advance_session`) does not run meridian flips or fault
  recovery.
- `astrometricslib/foundation/config.py` reads the `ASTROMETRICS_TESTING` environment variable, a
  test switch inside library code.
- The section 9 allow-list in `pyproject.toml` still lists:
  - `banned-api` for `backend/services/infrastructure/indi_worker.py`,
    `backend/services/observatory/execution_service.py`, three backend tests,
    `documentation/notebooks/wayfinding/execution/scripts/observatory_session_recorder.py`, and
    `mcp_servers/gaps/__main__.py`;
  - `blind-except` for the logging handler in `socket_manager.py`;
  - `print` for the notebooks and the command-line tools whose output is their result.
- `wayfindinglib/test/test_observatory_session_recorder.py` imports a documentation script.
- `ruff: ignore` suppressions remain:
  - 346 in astrometricslib, mostly missing argument types;
  - 13 in backend;
  - 10 in wayfindinglib, for PyIndi's camelCase names and similar cases.
- `ui/mcp/dist` was not rebuilt after the profile constants moved into the generated
  `ui/mcp/src/profileRules.ts`.

## 1. Target layering

The repository has four kinds of code. Each layer may call only the layer below it.

1. **Domain libraries** (`astrometricslib/`, `wayfindinglib/`). These hold all astronomy math,
   image processing, catalog rules, planning decisions, and hardware-control sequences. They never
   import `backend`. The two libraries form two layers of their own:
   - wayfindinglib builds on astrometricslib and uses only astrometricslib's public API;
   - astrometricslib never imports wayfindinglib.
   
   astrometricslib therefore also holds the infrastructure both libraries share: the error classes,
   logging setup, the job framework, the configuration loader (section 7), and generic storage and
   process locks (section 6.2, item 11). No other shared package exists.
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
4. **UI** (`ui/`, `electron/`). The UI only draws data. It uses only the backend's public
   interface, which the backend declares in one place (section 8.1):
   - the RPC methods, called through `callBackend(method, params)`;
   - a short list of routes for what RPC cannot carry:
     - the event and terminal WebSockets;
     - the static image and FITS file routes;
     - the Matplotlib figure routes;
     - the startup routes `/api/ready`, `/api/session-token`, and `/api/pairing-info`.
   
   Nothing outside that interface is reachable from the UI. Handoff and the emergency park
   command become RPC methods. CLAUDE.md records the same list.

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
- **Errors and logs have no shared design.** The code returns error replies in 137 places
  instead of raising. About 400 broad `except Exception` handlers mostly log one line and carry on.
  Each program and some library modules set up logging their own way. Section 7 defines one model
  for both.
- **Eight confirmed bugs** turned up during the review. See Appendix A.

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
7. **No aliases and no legacy names.** A narrow method that a general one replaces becomes a
   private helper or goes away. A renamed function, class, module, RPC method, or MCP tool keeps
   no alias and raises no `DeprecationWarning`. The change that renames or removes it also updates
   every caller in the repository:
   - library code, scripts, and tests;
   - backend services and RPC method names;
   - the UI's `ActionRegistry` and its callers;
   - the MCP dispositions and manifests;
   - `.claude/` agent tool lists and permission rules;
   - documentation.
8. **No compatibility code for old formats of our own.** When the code reads an older format of
   its own configuration or stored data, a one-time migration script converts the data, and the
   reading code supports only the current format. Formats defined by other software (FITS
   keywords, INDI properties, Siril files) are outside this rule.

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

Names inside a child do not repeat the child's topic, for example `control.guiding.run_calibration`
rather than `control.guiding.run_guider_calibration`, and `control.remote.list(kind)` rather than
`control.remote.list_remote(kind)`. Names and arguments also follow section 6.1. The table lists
the final names. `SkyPosition` is a small model with `ra_deg` and `dec_deg`. It turns into JSON
directly, so RPC and MCP calls can pass it. Every child that has readable state gets one `status(include=[...])` read. `include` chooses the
sections of the reply, such as live hardware readings and saved models.

| Attribute | What it covers | Public members after this plan | Folded in from today's `ObservatoryControl` |
|---|---|---|---|
| `control` | Driver injection | `driver`, `mount_driver`, `focuser_driver`, `filter_wheel_driver`, `camera_driver`, `guide_camera_driver`, `enclosure_driver`, `switch_driver`, `weather_driver`, `remote_transfer_driver` | `guiding_service` and `sync_service` go away (section 8.1) |
| `control.mount` | Pointing and tracking | `status`, `slew(destination: str \| Target \| SkyPosition, center=False, tolerance_arcsec=..., max_iterations=...)` (was `slew_to_target` and `slew_to_coordinates`), `sync(position: SkyPosition)` (was `sync_coordinates`), `park`, `unpark`, `set_tracking`, `manual_move`, `abort_motion`, `set_slew_rate`, `compute_pointing_correction`, `run_polar_alignment_assist` | `get_telescope_status`, plus the INDI mount decoding in `backend/mcp/tool_registry.py` (pier side, park, tracking) |
| `control.imaging` | Main camera, filter wheel, focuser | `status`, `capture_image(exposure_seconds, count=1, filter_name=None, dither=False)`, `set_filter`, `focus_move`, `compute_focus_correction`, `save_focus_model` | `get_filter_names`, `get_focuser_position`, `active_focus_model` |
| `control.guiding` | Guide camera, guide pulses, guider models | `status`, `pulse` (was `pulse_guide`), `expose` (was `guide_expose`), `get_image` (was `get_guide_image`), `drain_external_pulses`, `compute_correction` (was `compute_guiding_correction`), `run_calibration` (was `run_guider_calibration`), `run_backlash_calibration`, `run_exposure_test` (was `run_guide_exposure_test`), `refit_spectrum` (was `refit_guiding_spectrum`), `save_calibration` (was `save_guider_calibration`), `save_spectrum_analysis` (was `save_guiding_spectrum_analysis`), `save_run` (was `save_guiding_run`) | `active_guider_calibration`, `active_guiding_spectrum_analysis`, `guider_plate_scale_arcsec_per_px` |
| `control.remote` | The observatory computer's files and logs | `list(kind, folder_name=None, sizes=False)`, `check_connection` (was `check_remote_connection`), `frame_status(target)`, `sync_frames(target=None, dry_run=False, ...)` (was `sync_remote_frames`), `sync_logs` (was `sync_remote_logs`) | `list_remote_targets`, `list_remote_target_folders`, `list_remote_calibration_folders`, `discover_unassociated_remote_targets`, `list_remote_files`, `list_remote_files_with_sizes`, `check_for_new_remote_images`, `download_remote_frames`, `download_remote_targets`, `sync`, `sync_calibration_folder`, `sync_all_remote_folders`, `ingest_guiding_log_file`, `fetch_and_ingest_new_guide_logs`, `ingest_ekos_session_logs`. `is_syncing` moves to `Jobs.query`. |
| `control.history` | Past and current observing sessions | `query(kind, ...)` (was `night_history`), `get_live_session_status`, `frame_guiding`, `get_performance_envelope`, `save_ekos_session_context` | `analyze_capture_session`, `analyze_guiding_session`, `analyze_sky_coverage`, `summarize_capture_sessions`, `summarize_guiding_sessions`, `summarize_recurring_issues`, `list_ekos_session_summaries`, `get_ekos_session_context`, `list_guiding_runs`, `get_pointing_model`. `query` gains `kind="alignment"` (section 5). |
| `control.safety` | Weather, enclosure, and who is allowed to act | `status`, `assess` (was `assess_safety`), `save_rule_set` (was `save_safety_rule_set`), `execute_safe_state`, `open_enclosure`, `close_enclosure`, `apply_promotion_decision`, `enter_monitoring_mode`, `enter_controller_mode` | `get_safety_rule_set`, `active_enclosure`, `get_enclosure_state`, `delegation_policy`, `summarize_divergence_evidence`, and `refresh_safety_assessment` if it only reads |
| `control.equipment` | The equipment profile and device connections | `status`, `connect`, `disconnect`, `set_active_telescope`, `set_active_camera`, `cooling_ramp_rate`, `summarize_device`, `save_commissioning_run` | `active_telescope`, `active_camera`, `active_guide_scope`, `active_guide_camera`, `list_camera_profiles`, `get_equipment_configuration`, `get_observer_location`, `indi_diagnostics`, `get_commissioning_runs` |

`control` drops from 112 public members to about 70, spread over the root and seven children. The
largest child, `control.guiding`, has 13. Every member of today's `ObservatoryControl` appears in
exactly one row above.

The MCP servers derive tool names from the attribute path. Moving members into children therefore
renames their tools, for example from `observatory_park` to `observatory_mount_park`. The same
change updates the RPC method names, `backend/mcp/tool_dispositions.py`, the generated manifests,
and the tool lists in `.claude/agents/` and `.claude/settings.json` (rule 7).

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
| `backend/services/data/stellar_service.py` spectral-class labels, aliases, summary, and by-class listing | `StellarCatalog.query(detail="class_counts")`, `StellarCatalog.query(spectral_class=..., order="match")`. The backend copy also hid single-frame detections, so deleting it depends on moving that "displayable star" filter into `StellarCatalog.query` (`include_unresolved=False`, section 5.2). |
| `backend/services/data/stellar_service.py` `get_visible_targets` | `ObservationPlanning.get_visibility`. This row was not a copy: the backend held visibility logic of its own, and phase 5 moved that logic into `get_visibility`. |
| `backend/services/observatory/observatory_service.py` humidity safety rule | `control.safety.assess` |
| `backend/services/observatory/telescope_service.py` `_infer_target_at_coordinates` (small-angle distance, 1° match) | `TargetCatalog.query(ra=..., dec=..., radius_deg=...)` |
| `backend/services/analysis/analysis_orchestrator.py` frame classification (spectroscopy versus photometry) | `ProcessingPipelines.process_target(stages=...)`, which already chooses, using `frame_is_spectral` |
| `backend/services/infrastructure/sync_service.py` folder layout, calibration and log sync | `control.remote.sync_frames`, `control.remote.sync_logs` |
| `backend/services/processing/ingestion_service.py` remote folder name matching and calibration folder loop | `control.remote.list`, `control.remote.sync_frames`. The library name matcher is the single rule. |
| `backend/services/data/image_service.py` `get_filter_type` | `FrameRecord.normalize_filter` (the backend copy appears unused) |
| `backend/services/observatory/alignment_service.py` raw `sqlite3` read of `astrometrics.db` | `TargetCatalog.query`, with night ids from `observing_night_id` |

### 5.2 Logic to move

| Current location | What it does | Destination |
|---|---|---|
| `backend/services/observatory/guiding_service.py` | Merges guide pulses, converts pulses to arcseconds, computes root-mean-square (RMS) guiding error, and runs a simulated guiding loop | A guiding driver chosen by the `protocol` setting (`phd2`, `internal`, `simulator`), using the same driver registry `mount_driver` uses. RMS goes into `control.history.get_live_session_status`. The backend keeps only thread start and stop. |
| `backend/services/observatory/alignment_service.py` | Runs the capture, plate-solve, sync, re-slew loop, and reads the solved center from the WCS header. WCS (world coordinate system) is the FITS header block that maps pixels to sky positions. | `control.mount.slew(destination, center=True)`, using `control.mount.compute_pointing_correction` |
| `backend/services/infrastructure/sync_service.py` pointing-error extraction | Parses FITS headers for commanded and solved positions and computes the pointing error | A post-download step of `control.remote.sync_frames`. The results are read through `control.history.query(kind="alignment")`. |
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
| `ui/planetariumDisplay/utils/alignmentClustering.ts` | Groups plate-solve attempts into sessions, then computes RMS jitter and drift rates | `control.history.query(kind="alignment")`, built on the existing `get_alignment_sessions` so that "alignment session" has one definition. The RA average must wrap at 0h/24h. |
| `ui/planetariumDisplay/layers/TrackingRiskOverlay.ts` | Scores mount risk by sky position (meridian side, high declination, low altitude) and by measured RMS | A field on the `PerformanceEnvelope` model that `control.history.get_performance_envelope` returns. The UI only colors it. |
| `ui/astronomyManager/utils/starDisplayFormat.ts` | Applies spectral-match thresholds: poor fit, disagreement with the catalog, separation between candidates | Fields on the spectroscopy result: `is_poor_match`, `differs_from_catalog`, `candidate_separation` |
| `ui/common/hooks/targetListFiltering.ts` | Classifies targets by name (solar system body, Messier, NGC, IC, comet) | An `object_type` field on `Target` and a `TargetCatalog.query(object_type=...)` filter, built on `is_solar_system_target` |
| `ui/common/fitsViewer/mtfStretchGL.ts` | Computes auto-stretch parameters with a different method from the library: background 0.05 instead of 0.25, standard deviation instead of the median absolute deviation | Stretch parameters as fields on the `ViewableImage` that `render_fits` returns. The UI keeps the GPU drawing. |
| `ui/planetariumDisplay/hooks/useEquipmentConfiguration.ts`, `StarOverlay.ts`, `StellarAnalysisDetails.tsx` | Compute plate scale and field of view, and hold copied magnitude limits and minimum point counts | Fields from `control.equipment.status` and `StellarCatalog.query` (`has_catalog_magnitude`, `can_run_period_search`) |
| `backend/main_backend.py` IERS setup and the AltAz warm-up | Configures offline Earth-rotation data (IERS) in four places | One library helper, called by both libraries and the backend: `configure_offline_iers` and `warm_earth_orientation_data` in `astrometricslib/foundation/astropy_setup.py` |

### 5.3 Logic that stays in the UI

The planetarium projection (`ui/planetariumDisplay/utils/projectionMath.ts`, local sidereal time in
`CelestialSkyMap.tsx`) redraws at 60 frames per second and stays in the UI as drawing code. Two
follow-ups apply:

- Add a test that compares the TypeScript results with `wayfindinglib/astronomy/coordinate_transforms.py` at fixed reference times.
- Check whether the mount position reaches the UI in the current-epoch frame (JNow) while catalog
  stars use J2000. The two frames differ by about 0.36° in 2026.

## 6. API conventions, library consistency, and legacy code

### 6.1 Shared API conventions

Both libraries follow the conventions below. The counts come from parsing every public method on
the API classes: 95 in astrometricslib and 150 in wayfindinglib. Section 7 covers errors and
logging in detail.

| Convention | astrometricslib changes | wayfindinglib changes |
|---|---|---|
| **Root object.** `Astrometrics` and `Wayfinder` only build and hold sub-APIs. They have no methods of their own. | Move `stack`, `remake_preview`, and `process_all_targets` to `processing` (section 4.1). | None. |
| **Nesting.** Root object, then sub-APIs, then children: at most two levels. A sub-API gets children named by topic when it would pass about 25 public members. Each child is built once and shared. | Keep `processing.calibration` and `processing.diagnostics`. `TargetCatalog` uses `processing.calibration` instead of building a second `CalibrationCatalog`. | `control` gains seven children (section 4.2). |
| **Constructors.** Every sub-API takes `(config: AppConfiguration, storage, *, drivers...)` with real types. Children receive a shared context object, not a reference to their parent. | `Visualization` takes the whole `Astrometrics` object, typed `Any`. `Jobs` types `config` as `Any`. `MovingObjectRecovery` takes no configuration. `TargetCatalog` types `catalog_access` as `object`. | `ObservationPlanning` and `ObservationExecution` gain `config`. Remove the `astrometrics=` argument. |
| **Thin API layer.** API methods check arguments and hand off. The work happens in `pipelines/` (astrometricslib) or `tasks/` (wayfindinglib). | Move `api/target_overview.py`, `api/star_analysis.py`, `api/stellar_operations.py`, and `api/batch.py` out of `api/`. Move the long bodies of `frame_quality`, `spectral_frame_check`, `Astrometrics.stack`, `StellarCatalog.query`, and `find_or_create_by_position` too. | Move `_derive_performance_envelope` and the night analysis helpers into `tasks/`. |
| **Errors.** Library code raises a category from section 7.2 and never returns an error reply. `get` returns `None` for a missing item. The MCP and RPC adapters turn exceptions into error replies (section 7.4). | 48 `{"error": ...}` returns, 31 of them in `api/`. | 17 `{"error": ...}` returns. |
| **Results.** Pydantic models, one per `kind`. | 26 methods return `dict`. | 22 methods return `dict`. |
| **Naming the subject.** `target: str \| Target` everywhere (decided, section 11). A string that names no target raises `NotFoundError`. Every other identifier is `<noun>_id`. | Methods that take `target: Target` or `target_id: str` accept `str \| Target`. `camera` and `camera_name` become `camera_id`. | `target_name` and `target_id` arguments become `target: str \| Target`. |
| **Units in names.** `ra_deg`, `dec_deg`, `radius_deg`, `tolerance_arcsec`, `exposure_seconds`. | `ra`, `dec`, and `radius` on `TargetCatalog.query`, `StellarCatalog.query`, and the region methods. | `slew_to_coordinates(ra, dec)` and `sync_coordinates(ra, dec)` become `control.mount.slew(destination)` and `control.mount.sync(position)`, which take a `SkyPosition` (section 4.2). |
| **Time.** `since`, `until`, and `time` accept `str \| datetime \| Time \| None`. An observing night is `night_id: str`. | `since` and `until` accept only ISO strings today. | `time_input: Any` becomes `time`. `night_date: date` and `session_id` used to name a night become `night_id`. |
| **Choosing a variant.** `kind=` picks one variant. `include=` adds optional sections. `detail=` sets how much each record says. The libraries use no `mode=` argument and no boolean that switches variants. | `frame_quality(mode=...)` becomes `kind=`. `spectral: bool` on `stack_summary`, `compare_with_previous_stack`, `discard_previous_stack`, and `swap_with_previous_stack` becomes `kind=`. `include_fwhm` and `include_spectra` become `include=`. | `frame_guiding(include_quality=...)` becomes `include=`. |
| **Background jobs.** Long-running methods use `@background_job` and take `register_job: bool = True`. | 14 decorated methods; `register_job` appears on 4. | 4 decorated methods; `register_job` appears on 1. |
| **Exports.** The package root exports the root object, the sub-APIs, the models, the exceptions, and the driver base classes. A test pins the list. | Remove about 35 internal names, among them `AstrometryPipeline`, `StarIdentifier`, `FrameSelection`, `select_library_frames`, `close_interrupted_jobs`, `resolve_worker_counts`, `run_parallel_batch`, `DbLogHandler`, `LoggerInterface`, `run_siril_stack`, `stack_frames`, `ImageProcessing`, `SATURATED_FRAME_FRACTION`, and `SATURATED_BLOB_MINIMUM_PIXELS`. Stop importing `background_job` into the package root, where wayfindinglib picks it up. | Add the models and exceptions. Add a public-surface test like `astrometricslib/test/test_public_surface.py`. |
| **`api/__init__.py`.** One lazy-export lookup table and one statement of how to import. | Its docstring says not to import from `api`. Align the guidance. | Replace the `if` chain with the lookup table. |
| **Docstrings.** numpydoc, with no architecture section numbers and no mention of MCP or agents. | 13 mentions of MCP or agents, for example "Called through the MCP server, this runs as a background job". | 26 citations of architecture section numbers, some to sections that do not exist, and 7 mentions of MCP or agents. |

### 6.2 Library consistency work

| # | Item | Action |
|---|---|---|
| 1 | Duplicate models and a second storage path in wayfindinglib: two `ObservationSession` classes, two `EquipmentConfiguration` classes, a copy of `AbstractButler`, and `drivers/local_database.save_model`/`load_models` | Keep one model in `models/`. Use the shared `AbstractButler` in `astrometricslib/foundation/storage/` (item 11). Send all storage through `DiskButler`, matching astrometricslib's single path through `CatalogAccess`. |
| 2 | Leftover packages: `wayfindinglib/sky.py`, `observation.py`, `observationlib/`, `observatorylib/` | Move logic into `tasks/planning_tasks`, models into `models/`, and configuration reads into `data_access/`, then delete the packages. |
| 3 | wayfindinglib builds a fresh `Astrometrics` at about 18 sites, and some ignore the configuration they were given | `Wayfinder` holds one injected handle and passes it down. |
| 4 | Exceptions: `AstroLibError`, `AstrometryHardwareError`, `StorageNotMountedError(RuntimeError)`, `DelegationPolicyValidationError(ValueError)`, and `backend/exceptions.py` are unrelated | Replace them with the shared error model in section 7.2. |
| 5 | Driver interfaces: astrometricslib mixes a typing `Protocol`, an `Abstract*` class, and concrete-only classes | Use wayfindinglib's style, `abc.ABC` base classes named `*Driver`, for the Siril, plate-solve, and SIMBAD interfaces. Rename `wayfindinglib/drivers/protocols/`, because it holds abstract classes, not typing Protocols. |
| 6 | wayfindinglib imports astrometricslib internals for shared infrastructure (`LoggerInterface`, `DbLogHandler`, `capture_job_logs`, `get_current_job`) and for pipeline helpers (`FrameSelection`, `select_library_frames`, `derive_field_centers`, `classify_and_sort_fits_files`, `derive_target_sessions`, `resolve_camera_profile`, `SATURATED_*`, `DEFAULT_DARK_TEMPERATURE_TOLERANCE_C`) | The shared infrastructure becomes documented public API in astrometricslib's `foundation/` subpackage (section 7). Each pipeline helper becomes a proper public method, or the wayfindinglib code that needs it moves into astrometricslib. The MCP registry and reflection move to `mcp_servers/` (section 8.2). |
| 7 | File names: `wayfindinglib/api/*_registry.py` hold ordinary classes, not registries | Rename the files to `control.py`, `planning.py`, `execution.py`. Put the `control` children in a `control/` package with one module per child. |
| 8 | Scripts: 16 of 23 astrometricslib scripts and `wayfindinglib/scripts/build_deep_star_catalog.py` import internals | Allowed (decided, section 11). A script may import the internals of the library it lives in, and the public API of any library below it. `TID251` exempts each library's `scripts/` folder for its own library only. A script never imports another script; code that two scripts share moves into the library. |
| 9 | Alignment and guiding records live in `astrometricslib/drivers/logger_interface.py`, but only wayfindinglib and backend services that move into wayfindinglib read or write them | wayfindinglib owns them (decided, section 11). They move into wayfindinglib storage behind `DiskButler`. `control.history.query(kind="alignment")` reads them from there. |
| 10 | Documentation drift: garbled "astrometrics" wording in wayfindinglib docstrings, citations to architecture sections that do not exist, 353 `ruff: ignore` suppressions in wayfindinglib | Fix these as each file is touched, per CLAUDE.md. `Wayfinding_Library_Architecture.md` gains the `control` children. |
| 11 | `datastore/` is a third shared layer below both libraries: the generic `Butler`, SQLite connection setup, file-based process locks, and `DeviceInUseError`. Only the two libraries import it. | Move it into astrometricslib as `foundation/storage/` (decided, section 11). `DeviceInUseError` becomes a subclass of `ConflictError`. wayfindinglib's `DiskButler`, database drivers, and INDI hardware lock use the storage classes through astrometricslib's public API. The `datastore/test/` files move with the code. |

### 6.3 Legacy code to remove

Under section 3, rules 7 and 8, the code below goes away. Each removal updates every caller in the
same change.

| Location | What it keeps alive | Action |
|---|---|---|
| `astrometricslib/pipelines/astrometry/pipeline.py` | A wrapper that keeps the old name of `process` | Delete it. Callers use `process`. |
| `astrometricslib/utilities/parallel_batch.py` | A worker initializer marked deprecated | Delete it. Callers use `_initialize_worker_process`. |
| `backend/services/data/target_service.py`, `backend/services/data/stellar_service.py`, `backend/services/infrastructure/scripting_service.py` | A deprecated method, two no-op methods kept for compatibility, and a legacy wrapper around `execute` | Delete them and their RPC registrations. |
| `backend/services/infrastructure/base_service.py`, `backend/services/infrastructure/system_status_service.py` | Fallback paths: an in-memory job when no `JobService` is given, and on-demand status aggregation | Delete the fallbacks. The container always provides the real dependency. |
| `backend/routers/rpc_router.py` | The two-part `namespace:method` reflection fallback, `method_aliases`, and duplicate registrations such as `target:get`/`target:get_targets` and `observatory:connect`/`telescope:connect` | One explicit RPC name per method (section 8.1). |
| `wayfindinglib/drivers/phd2/phd2_events.py`, `phd2_guiding_service.py` | `to_legacy_history_entry` and the legacy-shaped `get_status()` | Use `GuidingSample` from end to end. |
| `wayfindinglib/data_access/equipment_catalog_reader.py`, `astrometricslib/utilities/spectroscopy_models.py` | Reading of the old `[Telescope]` configuration section and conversion of old configuration values | A one-time configuration migration script, then current-format reading only (rule 8). |
| `wayfindinglib/sky.py`, `observation.py`, `observationlib/`, `observatorylib/` | Older planning engines and models | Section 6.2, item 2. |
| `backend/mcp/tools/*`, `backend/mcp/mcp_diagnostics.py` | Dead MCP tools | Section 8.2. |
| `build/mcp/astrometrics_mcp_server.py`, the `legacy` `.claude/mcp.json` output of `build/mcp/generate_client_configs.py` | A backward-compatible server entry point and client configuration | Delete them. |
| `ui/common/services/telescopeService.ts`, `ui/common/services/imagingService.ts` | Modules that only re-export other services for backward compatibility | Delete them. Callers import the real services. |
| `ui/common/context/TargetContext.tsx`, `ui/common/types/vite-env.d.ts` | A type alias kept for old hook signatures, and the `electronAPI` declaration | Delete them. |
| `ui/common/services/backendApi.ts`, `ui/setupTests.ts` | Parsing of legacy REST error shapes (`detail`, `error`), and REST routing in the test mock | JSON-RPC errors only (section 7.4). |
| `backend/main_backend.py` | A global filter that hides `AstropyDeprecationWarning` | Remove the filter and update the astropy calls it hides. |
| About 15 wayfindinglib module docstrings | Text that describes a deprecated predecessor ("relocated verbatim from the deprecated ...", "pre-redesign") | Rewrite to describe current behavior, per the `code-documentation-style` skill. |

Some code handles older data that cannot be upgraded, or formats that other software defines. It
stays:

- `astrometricslib/pipelines/shared/staleness.py` treats a result with no recorded provenance as
  "unknown". A migration cannot recreate provenance that was never recorded.
- `astrometricslib/drivers/image.py` reads the FITS `RADECSYS` keyword.
- `wayfindinglib/drivers/indi/filter_wheel_controller.py` falls back to the INDI text property.
- `astrometricslib/pipelines/stacking/stage.py` handles Siril `r_` sequences.

## 7. Errors and logging

This section defines one way to report errors and one way to write logs, for both libraries, the
backend, the MCP servers, and the scripts. The code lives in a `foundation/` subpackage of
astrometricslib, and astrometricslib exports it from its package root. wayfindinglib, the backend,
the MCP servers, and the scripts all import it from there (section 1, item 1).

### 7.1 Current state

| Area | What the code does today |
|---|---|
| Exception classes | Eleven classes, unrelated to each other: `AstroLibError` (raised in 2 places), `StorageNotMountedError(RuntimeError)`, `DelegationPolicyValidationError(ValueError)`, `AstrometryHardwareError`, `datastore.DeviceInUseError`, and six classes in `backend/exceptions.py`. Only `telescope_service.py` and `imaging_service.py` use the backend classes. |
| What code raises | Mostly built-in types: about 155 `ValueError`, 41 `RuntimeError`, 13 `FileNotFoundError`, and 8 `TimeoutError` across the libraries and the backend. |
| Error replies instead of exceptions | 48 `{"error": ...}` returns in astrometricslib, 17 in wayfindinglib, and 72 `{"status": "error"}`-style returns in the backend. These travel inside a *success* reply, so 77 places in the UI check for them by hand. |
| Broad `except Exception` | About 400 handlers outside tests and scripts: 209 in astrometricslib, 58 in wayfindinglib, 138 in the backend. About 25 re-raise. Most of the others log one line without the traceback and carry on, which hides the cause of failures. |
| RPC error mapping (`backend/routers/rpc_router.py` `handle_rpc`) | Any `KeyError` from inside a service becomes "Method not found" with HTTP 404. Any `ValueError` becomes "Invalid params" with HTTP 400. Everything else becomes "Internal error" with HTTP 500. The UI keeps only the message text and drops the error code. |
| MCP error mapping (`astrometricslib/mcp/tool_registry.py`) | A failed tool returns ordinary text that starts "Error during tool execution", not an MCP error result. The exception is not logged. An `{"error": ...}` reply from the library looks like success. If the path sandbox check itself raises an unexpected exception, the server logs it at debug level and runs the tool anyway. |
| Logging setup | `backend/main_backend.py` calls `logging.basicConfig` at DEBUG level with a log file that never rotates. Each MCP server and most scripts call their own `basicConfig`. Library code also changes logging setup: `drivers/job_logging.py` sets the package logger level, and `base_service.py` and `drivers/siril_interface.py` attach their own file handlers to loggers named per target or per run. |
| Logger names | 159 modules use `getLogger(__name__)`. A few use ad-hoc names such as `job_{id}` and `siril_{id}`. |
| `print` | 344 calls in astrometricslib and 56 in wayfindinglib, mostly in scripts. Seven library modules outside `scripts/` also print. |
| Job logs | `job_logging.capture_job_logs` attaches handlers to one library's package logger for the length of a job. A job therefore records messages from one library only. |
| The logs database | `drivers/logger_interface.py` (1,646 lines) stores job records and job log lines, but also alignment attempts, guiding samples, polar-alignment runs, session telemetry, AI interactions, and a knowledge table. |

### 7.2 Error model

`astrometricslib/foundation/errors.py` defines one base class and a small set of categories. Every error the libraries
or the backend raise on purpose belongs to one category.

| Class | `code` | Meaning | Examples today |
|---|---|---|---|
| `AstrometricsError` | (base) | Base of every expected error. It carries `code`, `message` (one plain sentence a user can read), `details` (a dictionary of JSON-safe values), and `retryable` (whether the same call may succeed later). | `AstroLibError` |
| `InvalidArgumentError` | `invalid_argument` | The caller passed a bad value. | `{"error": "detail must be one of: ..."}`, backend `InvalidArgumentError` |
| `NotFoundError` | `not_found` | A named target, star, job, session, or file does not exist. | backend `TargetNotFoundError`, `FilterNotFoundError`, `ToolNotFoundError` |
| `ConflictError` | `conflict` | The request is valid, but the current state forbids it: a device is in use, a job is already running, the mount is parked. | `datastore.DeviceInUseError` |
| `PermissionDeniedError` | `permission_denied` | A policy forbids the action: the delegation policy, a read-only profile, a path outside the allowed folders. | MCP sandbox check |
| `ConfigurationError` | `configuration` | The configuration is missing or invalid. | `DelegationPolicyValidationError` |
| `StorageError` | `storage` | Disk or database trouble: storage not mounted, a file cannot be read, the database is locked. | `StorageNotMountedError` |
| `HardwareError` | `hardware` | A device command failed or timed out. Usually `retryable`. | `AstrometryHardwareError`, backend `HardwareCommandError` |
| `ExternalServiceError` | `external_service` | Another program or service failed: Siril, the plate solver, SIMBAD, or the observatory computer over the network. Usually `retryable`. | `TimeoutError` and `ConnectionError` from drivers |
| `ProcessingError` | `processing` | The input was valid, but a pipeline could not produce a result from it, for example a plate solve that found too few stars. | `RuntimeError` raised by pipelines |

Any other exception means a bug and has the code `internal`.

Each library adds specific subclasses where callers need to tell cases apart, for example
`PlateSolveFailedError(ProcessingError)` or `MountParkedError(ConflictError)`. astrometricslib
exports the base class, the categories, and its own subclasses from its package root. wayfindinglib
imports the categories from astrometricslib and exports only its own subclasses.

No category subclasses a built-in exception such as `ValueError` or `KeyError`. Code that catches
an `AstrometricsError` category therefore never catches an error from Python itself or from a
third-party library by accident. That accident is the cause of the `KeyError` bug in Appendix A,
item 7.

`ErrorInfo` is the serializable form of an error, a Pydantic model with fields
`code`, `message`, `details`, `retryable`, and `request_id`. Every adapter sends this shape, and
batch results use it to list failed items.

### 7.3 Error rules

1. **Library and service code raises.** It never returns `{"error": ...}` or
   `{"status": "error"}`. The 137 error-reply sites in section 7.1 become raises.
2. **Translate at the edge of the library.** Each driver turns third-party exceptions into a category
   with `raise HardwareError(...) from exc`. Third-party exceptions include those from `sqlite3`,
   `subprocess`, PyIndi, `astropy`, and the network clients. Keeping the cause with `from` keeps
   the original traceback.
3. **Catch `Exception` only at a boundary.** A boundary is one of:
   - an adapter (RPC router, MCP registry);
   - the job runner;
   - a loop over independent items, where one failed item must not stop the rest. Such a loop
     records the failure as an `ErrorInfo` in its result and logs it once with the traceback.
   
   Anywhere else, catch the specific exception, or don't catch at all.
4. **A missing item is not always an error.** `get` returns `None` when the item does not exist.
   Every other method that needs an existing item raises `NotFoundError`. The decision on naming targets makes this
   concrete: a method that takes `target: str | Target` raises `NotFoundError` when the string
   names no target.
5. **Degraded results are not errors.** When a method produces a result of reduced quality, it
   reports that in the result's quality fields and logs a warning. It raises only when it cannot
   produce a result at all.
6. **Log an error once, where it is handled, with the traceback** (`logger.exception`). Code that
   raises or re-raises does not also log.

### 7.4 How each adapter reports errors

`to_error_info(exc)` converts any exception to an `ErrorInfo`, and one table maps
each category to its transport codes.

| `code` | JSON-RPC code | MCP |
|---|---|---|
| `invalid_argument` | -32602 | `isError: true` |
| `not_found` | -32001 | `isError: true` |
| `conflict` | -32002 | `isError: true` |
| `permission_denied` | -32003 | `isError: true` |
| `configuration` | -32004 | `isError: true` |
| `storage` | -32005 | `isError: true` |
| `hardware` | -32010 | `isError: true` |
| `external_service` | -32011 | `isError: true` |
| `processing` | -32012 | `isError: true` |
| `internal` | -32603 | `isError: true` |

Every well-formed JSON-RPC reply uses HTTP status 200, whether it reports success or an error.
The error code inside the reply tells the client what went wrong. The backend uses other HTTP
statuses only for problems below the RPC layer: 400 for a request that is not valid JSON-RPC, and
401 for a missing or wrong session token. The UI therefore has one error path.

- **RPC router.** `handle_rpc` catches `KeyError` only around the method lookup, not around the
  call. It sends `ErrorInfo` in the JSON-RPC `error.data` field. For `internal` errors it sends a
  generic message plus the `request_id`, and keeps the traceback in the log.
- **MCP servers.** They return an MCP result with `isError: true`, with `code: message` as the text
  and `details` as JSON. They log the exception. A failure inside the path sandbox check refuses
  the call instead of letting it through.
- **UI.** `callBackend` throws a `BackendError` that keeps `code`, `details`, `retryable`, and
  `requestId`. One place in the UI decides how each code is shown: an inline field message for
  `invalid_argument`, a "Try again" button when `retryable`, and the `request_id` in the toast for
  `internal`. The 77 per-call checks for `status === 'error'` go away.
- **Background jobs.** A failed job stores its `ErrorInfo` on the job record. `Jobs.query` returns
  it, so the UI and agents read a job failure in the same shape as a call failure.
- **Scripts.** A script's entry point catches `AstrometricsError`, prints the message, and exits with
  a non-zero code. Any other exception prints its traceback.

`backend/exceptions.py` goes away. Its classes map to `InvalidArgumentError`, `NotFoundError`, and
`HardwareError`.

### 7.5 Logging model

1. **Two kinds of "log" become separate.**
   - *Diagnostic logs* are Python `logging` messages for developers.
   - *Operational records* are data the app shows and analyzes: job records, job log lines,
     alignment attempts, guiding samples, session telemetry.
   
   `LoggerInterface` holds both kinds today. Job records and job log lines move to a
   job store in `astrometricslib/foundation/jobs.py`, next to the job framework. Alignment, guiding, and telemetry records move to wayfindinglib, behind
   `DiskButler` (section 6.2, item 9). AI interactions and the knowledge table
   move to the backend.
2. **One logger per module.** Every module uses `logger = logging.getLogger(__name__)`. The
   `job_{id}`, `siril_{id}`, and per-target worker loggers go away. Job and run identity travel in
   the log context (item 5).
3. **Libraries never configure logging.** Library and service modules never call `basicConfig`,
   `addHandler`, `setLevel`, or set `propagate`. Each package root adds a `NullHandler`, which
   silences the "no handler" message when no program has set up logging. Library modules do not
   call `print`.
4. **One setup function per program.** Each program calls
   `astrometricslib.configure_logging(program, level, log_dir)` once at startup. The programs are
   the backend, each MCP server, and each script. The function installs:
   - a rotating log file in JSON Lines format, one JSON object per line, so tools and agents can
     filter it (for example 10 MB per file, 5 files kept);
   - a readable console handler: stderr for MCP servers, whose stdout carries the protocol;
   - the context filter from item 5;
   - the job router from item 6;
   - quieter levels for noisy third-party loggers (`httpx`, `uvicorn`, `astropy`, `matplotlib`).
   
   This replaces the setup code in `main_backend.py`, the three MCP `__main__.py` files, and the
   scripts.
5. **Context on every record.** A `contextvars` log context carries `request_id`, `job_id`,
   `target_id`, `session_id`, and the RPC method or MCP tool name. A context variable is a value
   that follows the current thread or async task. `job_logging` already keeps the current job this
   way, and this generalizes it. The RPC router and the MCP registry start a context for each
   call. The job runner starts one for each job. The setup filter copies the context onto each
   record, so every line in the file says which call or job wrote it. Error replies include the
   same `request_id`, so a user or agent can quote it and a developer can find the matching lines.
6. **Job logs by context, not by attaching handlers.** `configure_logging` installs one permanent
   handler. It sends each record that carries a `job_id` to that job's log file and log rows. This
   replaces the attach-and-detach logic in `capture_job_logs`. Job logs then include messages
   from both libraries, and no handler can leak.
7. **Level policy.**
   - `DEBUG`: internal detail.
   - `INFO`: the start and end of jobs, and every hardware command.
   - `WARNING`: a degraded result, a retry, or a fallback.
   - `ERROR`: an operation failed, logged once by the boundary that handled it (rule 6 in
     section 7.3).
   
   Messages use `%`-style arguments rather than f-strings, so a message that is filtered out costs
   nothing to build.

## 8. Backend and MCP structure

### 8.1 Backend

- **One declaration of the public interface.** One module lists every RPC method and every
  non-RPC route from section 1, item 4. The router registers only those methods, and the app mounts
  only those routes. The UI's `ActionRegistry` types are generated from the same list.
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

### 8.2 MCP servers

- Move the MCP packages out of the libraries, and out of `backend/`, into one top-level package
  `mcp_servers/` beside `backend/` and `ui/`. Its name matches `mcp_servers.example.json`. It is not
  `mcp/`, because that name would hide the `mcp` SDK on `sys.path`. Layout:
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

## 9. Enforcement

| Check | Purpose |
|---|---|
| Ruff `TID251` banned-API entries for `wayfindinglib.drivers`, `.tasks`, `.models`, `.analytics`, `.session_analysis`, `.data_access`, `.observationlib`, `.observatorylib`, `.watchdog`, plus `astrometricslib.visualization`, with a per-file exemption for each library's own tree | Blocks the internal imports that pass lint today |
| import-linter contracts: layers `backend.routers` above `backend.services` above the libraries; libraries never import `backend`; `astrometricslib` never imports `wayfindinglib`; `backend.services` never imports `backend.container` or `backend.routers`; the MCP package imports only public library names | Turns the CLAUDE.md layer rules from convention into a failing check |
| Public-surface tests for both libraries | Keeps the export lists from growing or leaking again |
| A test that the generated MCP tool list equals the public API minus the dispositions table | Makes every new public method a deliberate choice of AI tool |
| A backend test that every route mounted on the FastAPI app appears in the public-interface declaration (section 8.1) | Keeps the backend's public interface explicit |
| An ESLint rule that allows `fetch` and `WebSocket` only inside `ui/common/services/backendApi.ts` and `ui/common/utils/socketClient.ts`, and a check that every path they use is in the declaration | Keeps the UI on the backend's public interface |
| A CI check that `ui/common/types/backendTypes.ts` matches a fresh code generation run | Keeps UI types in step with the Pydantic models |
| Ruff rules `BLE` (blind `except Exception`), `TRY` (exception style, including `logger.exception` in handlers), `LOG` and `G` (logging calls), and `T20` (`print`, with scripts exempt). Like the import rules, they start with an allow-list of today's sites. | Holds the error and logging rules in section 7 |
| `TID251` bans `logging.basicConfig` everywhere except `astrometricslib/foundation/logging.py` and program entry points | Keeps logging setup in one place |
| A test that every `AstrometricsError` subclass maps to a row of the section 7.4 table | Keeps the adapters complete when a library adds an error class |
| `serialize_rpc_result` converts NumPy values with `.item()` and `.tolist()`, and fails or warns on unknown types instead of calling `str()` | Makes violations of the CLAUDE.md serialization rule visible |

## 10. Order of work

Each phase ends with `ruff check`, the affected `pytest` suites, and, for `ui/` changes,
`npm run type-check` and `npm test`.

1. **Fix the confirmed bugs** in Appendix A.
   Done when: each bug has a test or a manual check that shows the fix.
2. **Add guardrails.** Add the `TID251` entries, the import-linter contracts, and the error and
   logging lint rules from section 9, with an explicit
   allow-list of today's violations so CI stays green.
   Done when: CI runs both checks, and the allow-list is the to-do list for phases 4 to 6.
3. **Build the shared error and logging code (section 7).** Create `astrometricslib/foundation/`,
   move `datastore/` into it as `foundation/storage/` (section 6.2, item 11), and add the error
   classes, `ErrorInfo`, `to_error_info`, `configure_logging`, the log context, and the job log
   router. Switch the RPC router, the MCP registries, the UI's `callBackend`, and every program
   entry point to them. While library code still raises built-in types, the adapters report a
   plain `ValueError` as `invalid_argument`. Phase 5 deletes that mapping.
   Done when: every RPC and MCP error reply carries an `ErrorInfo` with a `request_id`, and every
   program sets up logging through `configure_logging`.
4. **Delete the copies in section 5.1 and the legacy code in section 6.3.**
   Done when: those backend functions and shims are gone, the configuration migration has run, and
   the RPC methods call the library functions.
5. **Consolidate the library API (section 4) and apply the conventions in section 6.1.** Add
   arguments and new general methods. Split `control` into its children under their final names.
   Make the narrow methods private or delete them, and update every caller in the same change
   (section 3, rule 7). Return typed models. Replace error replies with raises, narrow the broad
   `except Exception` handlers (section 7.3), and delete the adapters' `ValueError` mapping.
   Done when: both public-surface tests pin the section 4 list, the MCP tool list matches it, and
   no old name remains anywhere in the repository.
6. **Move the logic in section 5.2.**
   Done when: the allow-lists from phase 2 are empty.
7. **Restructure the MCP servers (section 8.2) and the backend (section 8.1).**
   Done when: the domain libraries contain no `mcp/` package, and `main_backend.py` holds only app
   setup.
8. **Finish the library consistency work (section 6.2).** This includes splitting
   `LoggerInterface` (section 7.5, item 1).
   Done when: no library defines its own unrelated exception classes, each concept has one model,
   and no leftover packages remain.

Phases 4 and 5 can overlap. Phase 5 depends on phase 3, because the raises it adds need the
adapters to report them. Phase 6 depends on phase 5, because the moved logic lands in the
consolidated methods.

## 11. Decisions for the owner

Decided on 2026-10-04:

- **wayfindinglib grouping.** `Wayfinder` keeps `control`, `planning`, and `execution`.
  `control` gains children named by topic (section 4.2). Two levels of nesting is the rule for
  both libraries (section 6.1).
- **Naming a target.** Every method that acts on a target takes `target: str | Target`
  (section 6.1).
- **No deprecated or legacy names.** Renames and removals keep no aliases. The change that makes
  them updates every caller (section 3, rules 7 and 8; section 6.3).
- **MCP package.** All MCP servers move to a top-level `mcp_servers/` package (section 8.2).
- **Alignment and guiding records.** wayfindinglib owns them (section 6.2, item 9).
- **Slewing.** `control.mount.slew(destination: str | Target | SkyPosition, ...)` replaces
  `slew_to_target` and `slew_to_coordinates`, and `control.mount.sync(position)` replaces
  `sync_coordinates` (section 4.2).
- **The UI's access to the backend.** The UI uses only the backend's declared public interface
  (section 1, item 4; section 8.1). The backend uses only the libraries' public API.
- **Shared infrastructure.** wayfindinglib builds on astrometricslib, and astrometricslib never
  imports wayfindinglib. astrometricslib holds the shared errors, logging setup, job framework, and
  configuration in its `foundation/` subpackage. There is no separate shared package (section 1,
  item 1; section 7).
- **`datastore/`.** It moves into astrometricslib as `foundation/storage/` (section 6.2, item 11).
- **Maintenance scripts.** A script in a library's `scripts/` folder may import that library's
  internals (section 6.2, item 8).
- **Errors and logging.** The design in section 7 stands, including:
  - the nine error categories;
  - HTTP 200 for every well-formed JSON-RPC reply;
  - JSON Lines as the log file format.

Open: none. The "Remaining" list in the Status section holds the work left after phase 8.

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
7. `backend/routers/rpc_router.py` `handle_rpc` wraps the whole call, including the service method,
   in `except KeyError`. A `KeyError` raised inside any service is therefore reported as
   "Method not found" with HTTP 404.
8. `astrometricslib/mcp/tool_registry.py` logs an unexpected exception from the path sandbox check
   at debug level and then runs the tool. The check fails open: an argument it cannot check is
   allowed through.

Other issues the review reported but did not re-check:

- `backend/container.py` assigns `wayfinder.control.driver` before the driver exists. A later
  assignment corrects it.
- `astrometricslib/mcp/tools/contract_validator.py` scans from the repository root, not from the
  library its docstring names.
- The planetarium alignment clustering averages RA without wrapping at 0h/24h.

On 2026-10-08 all three no longer apply. The container assigns `wayfinder.control.driver` once,
after it builds the driver. The contract validator (now `mcp_servers/devtools/contract_validator.py`)
scans the astrometricslib package. The alignment grouping moved into
`wayfindinglib/analytics/alignment_sessions.py`, whose `mean_position_deg` takes the circular mean
of RA.
