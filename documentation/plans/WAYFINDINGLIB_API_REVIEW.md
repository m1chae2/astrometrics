# `wayfindinglib` API review

*Decisions recorded · 2026-10-31 · Status: the user's final decisions, including the agreed names, are applied to the proposals below. Section 12 lists what is still undecided. Nothing has been reorganized in code.*

This document records the review of `wayfindinglib`'s public API. It follows the process the user set for `astrometricslib`: break the API into logical groups first; then decide for each group whether it is public or private; then, for each public group, which names and calls are. The user has now made those decisions, which the next section lists. The evidence tables stay, with a Decision column added. Reader counts are evidence only.

The groups are `planning`, `execution`, `review` and `control`, which the user agreed.

## Decisions

The user decided these on 2026-10-31.

**Rules (the same as for `astrometricslib`):**

- `_internal` holds everything private, in logical groups. Only `wayfindinglib` and tests (of either library) may import it. The backend and the MCP servers use public names only.
- Anything that only tests use is private.
- Results come back from calls. A simple value is an argument. A dictionary is for a group of options or a list of items, and the library checks it.

**Public modules:**

| Module | Names |
| :--- | :--- |
| `wayfindinglib` (root, 6) | `Wayfinder`, `ObservatoryControl`, `ObservationPlanning`, `ObservationExecution`, `ObservationSession`, `ObservationPackage` |
| `wayfindinglib.exceptions` (2) | `DelegationPolicyValidationError`, `PlanRevisionConflictError` |
| `wayfindinglib.reports` (4) | `RunState`, `PackageStatus`, `DepthGoalState`, `DepthLevel` |

Everything else exported goes to `_internal`, in the groups `planning`, `execution`, `review` and `control`. There is no `wayfindinglib.requests` and no `wayfindinglib.drivers`. `PlanChange` is a private result. `ExposureRequest`, `DitherConfig`, `QueueRequest`, `StartTimeMode`, `FrameType`, `SkyPosition` and `ObservatoryCapability` are private, and calls take plain dictionaries for groups of options and lists of items. `IndiInterface`, `SimulatorIndiInterface` and `ControlRecordStore` are private. `Wayfinder` builds its own driver and record store from configuration (for example `simulated=True`).

**Sub-APIs:** `planning`, `execution`, `review` and `control`. `control` has `mount`, `imaging`, `guiding`, `safety`, `equipment` and `remote`.

- **`control` root:** the driver properties are private, and `close` is public. How the backend's separate-process INDI worker (`backend/services/infrastructure/indi_worker.py`, `backend/container.py:147,196`) starts is to be solved when building.
- **`review`:** `query`, `get_live_session_status`, `frame_guiding` (suggested as a kind of `query`), `get_performance_envelope`, and `get_advisory` for results and data. `query` returns a result model.
- **`planning`:** as today, plus `get_depth_goal` and `get_advisory`'s tonight and calibration kinds (check that the split is clean). There is one coordinate lookup with a proper result, `lookup_coordinates`. `find_sources`, `get_sources`, `get_library_star_summaries` and `get_online_catalog_sources` become one `query_sources(..., source=)` once the code confirms they can share a shape. `get_imaged_field_centers` is removed. `create_plan(kind=)` becomes `create_sequence`, `create_package` and `create_session` (scheduling optional). `edit_package` and `edit_queue` become one verb per action: `update_package`, `dismiss_package`, `delete_package`, `add_to_queue`, `reorder_queue` and `remove_from_queue` (the actions the code has beyond these are in Section 12). `get_plan` reads one and `query_plans` lists. `list_catalog_driver_metadata` becomes `query_catalogs` and `deep_catalog_status` becomes `get_deep_catalog_status`.
- **`execution`:** public `advance_session`, `abort_session`, `reconcile_session`, `record_divergence` (renamed `build_divergence_record`) and `create_recorder`.
- **Private:** `execute_meridian_flip`, `recover_fault`, `recover_guide_star_loss`, `save_ekos_session_context`, and the other test-only members in Section 8, except `equipment.disconnect`, which stays public. Also private: `mount.sync`, `guiding.pulse`, `drain_external_pulses`, `run_calibration`, `save_calibration`, `safety.assess`, `execute_safe_state`, `open_enclosure` and `close_enclosure`. Polar alignment and the spectrum fits stay in `control`, so `run_polar_alignment_assist` and `save_spectrum_analysis` are private under the test-only rule, while `refit_spectrum` stays public in `guiding`.
- **Other `control` decisions:** `mount.status` gets a result model and covers only the mount. `remote` stays as 5 public calls under `control`.
- **No reader:** `imaging.compute_focus_correction`, `equipment.cooling_ramp_rate` and `equipment.summarize_device` are private, and candidates for deletion. `SequenceItem` and `SequencePlan` are private; check whether they can go.
- **Result models (D14, D15).** Every public call that returns a plain dictionary or `Any` gets a result model, so a caller reads named fields. `mount.status` and `history.query` were already decided. The rest are: `lookup_coordinates`, `query_sources` (from `find_sources` and `get_library_star_summaries`), `query_catalogs` (was `list_catalog_driver_metadata`), `get_constellation_lines`, `build_deep_star_catalog`, `remote.frame_status`, `remote.sync_frames`, `remote.sync_logs`, `history.frame_guiding` (whether it stays its own call or becomes a `query` kind, it gets the same kind of model) and `guiding.expose`. `drain_external_pulses` and `guiding.get_image` are private; they get models if they stay. `get_imaged_field_centers` is removed. The model names are open.
- **Parameter names (D13, D8).** Names match `astrometricslib`. `get_advisory` takes `filter_name` instead of `filter`, as a `str` like the other calls (`FilterType` is a `StrEnum`, so an enum value still works). `calculate_panels` takes `ra_deg` and `dec_deg` as numbers in degrees instead of text. `get_plan` and `create_plan` take `observing_night_id` instead of `night_id`, the name `quality.query` uses. `refresh_suggestions` takes `target`, one id or a list, as `processing.process_target` does, instead of `target_ids`. Section 7 (D13) has the details, one finding that came out of reading the code, and other names that may need to change.
- **`status` is a pattern.** Each `control` sub-group (`mount`, `imaging`, `guiding`, `safety`, `equipment`) has a `status` call with the same shape. This is a documented pattern, not a rule-breaking exception, so `status` keeps its name. Write the pattern up in the `control` README when it is built.

**Meridian flips and fault recovery.** No real caller of `execute_meridian_flip`, `recover_fault` or `recover_guide_star_loss` was found; only tests call them. The user chose not to investigate this now. This document does not say what a real run does about flips or faults.

**Conflicts, and how they came out.** Each name below is private by the user's decision, yet code that must use public names only reads it.

| Name | Who reads it | Outcome |
| :--- | :--- | :--- |
| `group_alignment_attempts` (and `AlignmentAttempt`, `AlignmentTargetSession`) | The backend (`alignment_service.py`) | Decided: it becomes a public call, not a backend copy. The models stay private, so the call takes and returns plain data with result models. Proposed name: `review.query_alignment_sessions` (it groups plate solves into one session per target). Name to confirm. |
| `fit_reply_to_budget` | The MCP servers | Decided: it moves into `mcp_servers`. |
| `MountPointingModel`, `PolarAlignmentStatus` | The backend, for annotations | Still a suggestion: remove the annotations. Listed in Section 12. |

**Counts after the decisions.** Members are today's 104 public members of the sub-APIs. New calls such as `query_sources` are not counted.

| Destination | Members |
| :--- | :--- |
| `planning`, public | 21 |
| `execution`, public | 5 |
| `review`, public | 5 |
| `control`, public | 34 (`close`, `mount` 9, `imaging` 4, `guiding` 5, `safety` 4, `equipment` 6, `remote` 5) |
| Private | 38 |
| Removed | 1 |
| Total | 104 |

`get_advisory` is counted once, under `review`, although its tonight and calibration kinds go to `planning`. `planning` is 20 of today's members plus `get_depth_goal`. Of today's 71 exported names, 6 are root, 2 are in `exceptions`, 4 are in `reports`, and 59 were private. Two of those leave the list: `group_alignment_attempts` becomes a public call and `fit_reply_to_budget` moves into `mcp_servers`. That leaves 69 exported names and 57 private: `control` 16, `planning` 27, `execution` 4 and `review` 10. The new calls (`query_sources`, `create_sequence` and the others) are not counted in the member table above.

**Still open.** Section 12 lists it: the name of the public `group_alignment_attempts` call, the two annotation names, the checks before building, the INDI worker, and the names of the new result models and the extra parameters.

## 1. What the library exports

`wayfindinglib` has no plain list in `__all__`. Its `__init__.py` holds a table, `_LAZY_EXPORTS`, from each name to the module that defines it, and computes `__all__ = sorted([*_LAZY_EXPORTS, "Wayfinder"])`. A module `__getattr__` loads a name on first use, so importing the library loads no hardware code. Importing it also calls `configure_offline_iers()`.

That gives **71 exported names**, and **104 public members** of the three sub-APIs:

| Sub-API | File | Members |
| :--- | :--- | :--- |
| `planning` | `api/planning.py` | 23 |
| `execution` | `api/execution.py` | 8 |
| `control` root | `api/control/control.py` | 12 (11 driver properties and `close`) |

| `control.mount` | `api/control/mount.py` | 12 |
| `control.imaging` | `api/control/imaging.py` | 6 |
| `control.guiding` | `api/control/guiding.py` | 15 |
| `control.remote` | `api/control/remote.py` | 5 |
| `control.history` | `api/control/history.py` | 5 |
| `control.safety` | `api/control/safety.py` | 9 |
| `control.equipment` | `api/control/equipment.py` | 9 |

`wayfindinglib/test/test_public_surface.py` pins all of this, and caps `control` at 72 members.

## 2. How the readers were checked

**Exported names:** the same `ast` scan as `API_TIERS_INVENTORY.md`, counting files. It covers root imports, `import wayfindinglib as x`, function-level imports and direct imports of submodules.

**Members:** a regular-expression search, because a call reaches a member through an object, not an import. It looks for `mount.park`, for the RPC table's form `("wayfinder.control.mount", "park")`, and, for a name found on only one sub-API, for any `.name(`. So a count may be high for a common name such as `park`, which other objects also have. A generic `status` or `query` is matched only through its sub-API name, so a count may be low if a caller holds the object under another name. Treat "no reader" as a strong hint, and read the code before removing anything.

Column key: **MCP** is the disposition in `mcp_servers/wayfinding_core/tool_manifest.json`: offered (`keep`), withheld from AI (`withhold`), or dropped (`drop`). The server reflects every public method and the manifest filters them. **Scripts** are `wayfindinglib/scripts/`, `astrometricslib/scripts/` and `build/`. **Library** is other files in `wayfindinglib/`, which call a member of the same name. The app reaches members only through the backend, so the desktop UI has no column.

## 3. Logical groups

| Group | Members today | What it answers |
| :--- | :--- | :--- |
| Planning | 21 | What should I observe, and what is the plan? Sky catalogs, visibility, mosaics, packages, the night plan. |
| Execution | 6 | Run the plan and recover from faults during the night. |
| Control | 65 | Operate the equipment now. Mount, imaging, guiding, safety, equipment, the observatory computer, driver slots. |
| Review | 12 | How did it go, and what do the results say? History, target report, advisory, judgment of results. |
| Total | 104 | |

Section 4 lists the members under these four headings. After the decisions the public members sit as the Decisions section shows. Inside the groups, the natural subgroups are:

- **Planning:** sky catalog (coordinates, sources, catalog drivers, constellation lines, deep catalog); visibility and framing; plans and packages (including `edit_queue`, `tick_checklist_item`, depth goal setting).
- **Execution:** the session runner; recovery; the recorder.
- **Control:** the seven-topic split it already has, less `history`, plus the driver slots on the root.
- **Review:** session history; the advisory and target report; records of what happened.

The library's architecture calls Control, Planning and Execution three independent functions, with import contracts behind them: Control and Planning never import each other, Planning is hardware-free, and only the composition root imports Execution. A Review group is new, and the user agreed it. It reads all three and is read by Planning's suggestions, so **it needs its own place in those contracts**. Today its calls live under `planning` and `control.history`.

## 4. Member inventory

### Planning (21)

| Member | Kind | Backend | MCP | Notebooks and how-to | Scripts | Tests | Library | Note | Decision |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `planning.resolve_target_coordinates` | method, computes | - | offered | - | - | 2 | 3 | Near-duplicate of `lookup_coordinates` (D1). | Public. Becomes `lookup_coordinates`, one lookup with a result model (name agreed). |
| `planning.lookup_coordinates` | method | - | offered | - | - | 1 | - | AI only. Near-duplicate (D1). | Public. Becomes `lookup_coordinates`, one lookup with a result model (name agreed). |
| `planning.find_sources` | method | - | offered | - | - | 1 | - | AI only. Overlap (D2). | Public. Becomes `query_sources(..., source=)` (name agreed) once the code confirms one shape. |
| `planning.get_sources` | method | 2 | offered | - | - | 5 | 3 | Overlap (D2). | Public. Becomes `query_sources(..., source=)` (name agreed) once the code confirms one shape. |
| `planning.get_library_star_summaries` | method | - | offered | - | - | 1 | 2 | AI only. Overlap (D2). | Public. Becomes `query_sources(..., source=)` (name agreed) once the code confirms one shape. |
| `planning.get_online_catalog_sources` | method | 1 | offered | - | - | 1 | 1 | Overlap (D2). | Public. Becomes `query_sources(..., source=)` (name agreed) once the code confirms one shape. |
| `planning.list_catalog_driver_metadata` | method | 1 | offered | - | - | - | 2 | `list`, not `query` or `get` (D8). | Public. Renamed `query_catalogs`. |
| `planning.get_constellation_lines` | method | 1 | dropped | - | - | 1 | 1 | The tool manifest says it returns an empty list because of a wrong file path. Not checked further. | Public, as today. |
| `planning.get_imaged_field_centers` | method | - | withheld | - | 1 | - | - | One script. Wraps `targets.imaged_field_centers` (D3). | Removed. Scripts use `targets.imaged_field_centers`. |
| `planning.get_visibility` | method | 2 | offered | 1 | - | 2 | - |  | Public, as today. |
| `planning.calculate_panels` | method, computes | 1 | offered | 1 | - | 4 | - |  | Public, as today. |
| `planning.create_mosaic` | method, writes | 1 | withheld | - | - | 1 | - |  | Public, as today. |
| `planning.create_plan` | method, writes | 2 | withheld | - | - | 8 | 3 | `kind` picks the write (D4). | Public. Becomes `create_sequence`, `create_package` and `create_session` (scheduling optional). |
| `planning.edit_queue` | method, writes | 1 | withheld | - | - | 5 | 1 |  | Public. Becomes `add_to_queue`, `reorder_queue` and `remove_from_queue`; the call also sets exposures, adds checklist items and sets framing (Section 12). |
| `planning.edit_package` | method, writes | 1 | withheld | - | - | 3 | - | One call changes, reopens, dismisses or deletes (D4). | Public. Becomes `update_package`, `dismiss_package` and `delete_package`; the call also reopens and undismisses (Section 12). |
| `planning.tick_checklist_item` | method, writes | 1 | withheld | - | - | 1 | - |  | Public, as today. |
| `planning.get_plan` | method | 4 | offered | - | - | 7 | 1 | `get` that lists; should read as `query` (D5). | Public. Reads one plan; `query_plans` lists. |
| `planning.set_depth_goal` | method, writes | 1 | withheld | - | - | 2 | - |  | Public, as today. |
| `planning.refresh_suggestions` | property, writes | 1 | withheld | - | - | 3 | - | Writes packages from judgments. Fits badly (Section 5). | Public, as today. |
| `planning.deep_catalog_status` | property, computes | 1 | offered | - | 1 | 2 | - | Name lacks `get` (D8). | Public. Renamed `get_deep_catalog_status`. |
| `planning.build_deep_star_catalog` | method, writes | - | withheld | - | 1 | - | - |  | Public, as today. |

### Execution (6)

| Member | Kind | Backend | MCP | Notebooks and how-to | Scripts | Tests | Library | Note | Decision |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `execution.advance_session` | method, moves hardware | 1 | dropped | - | - | 2 | - |  | Public. |
| `execution.abort_session` | method, writes | 2 | withheld | - | - | 3 | - |  | Public. |
| `execution.execute_meridian_flip` | method, moves hardware | - | dropped | - | - | 1 | - | Tests only (Section 7). | Private. Only tests use it (Section 8). |
| `execution.recover_fault` | method, moves hardware | - | dropped | - | - | 1 | - | Tests only (Section 7). | Private. Only tests use it (Section 8). |
| `execution.recover_guide_star_loss` | method, moves hardware | - | dropped | - | - | 1 | - | Tests only (Section 7). | Private. Only tests use it (Section 8). |
| `execution.create_recorder` | method, writes | - | dropped | 1 | - | 1 | - | Builds an object for a notebook script. | Public. |

### Control (65)

| Member | Kind | Backend | MCP | Notebooks and how-to | Scripts | Tests | Library | Note | Decision |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `control.close` | method | 1 | - | - | - | 1 | - |  | Public. |
| `control.driver` | property | 2 | - | 1 | - | 2 | - | Property with a setter. Backend sets it. | Private. `Wayfinder` builds its own driver from configuration. |
| `control.mount_driver` | property | - | - | - | - | 2 | - |  | Private. `Wayfinder` builds its own driver from configuration. |
| `control.focuser_driver` | property | - | - | - | - | - | - |  | Private. `Wayfinder` builds its own driver from configuration. |
| `control.filter_wheel_driver` | property | - | - | - | - | - | - |  | Private. `Wayfinder` builds its own driver from configuration. |
| `control.camera_driver` | property | - | - | - | - | - | - |  | Private. `Wayfinder` builds its own driver from configuration. |
| `control.guide_camera_driver` | property | - | - | - | - | - | - |  | Private. `Wayfinder` builds its own driver from configuration. |
| `control.enclosure_driver` | property | - | - | - | - | - | - |  | Private. `Wayfinder` builds its own driver from configuration. |
| `control.switch_driver` | property | - | - | - | - | - | - |  | Private. `Wayfinder` builds its own driver from configuration. |
| `control.weather_driver` | property | - | - | - | - | 1 | - |  | Private. `Wayfinder` builds its own driver from configuration. |
| `control.guiding_driver` | property | - | - | - | - | - | - |  | Private. `Wayfinder` builds its own driver from configuration. |
| `control.remote_transfer_driver` | property | - | - | 1 | - | 1 | - |  | Private. `Wayfinder` builds its own driver from configuration. |
| `control.mount.status` | method | 2 | offered | 1 | - | 4 | - | Also reads imaging devices; overlaps `imaging.status` (D7). | Public. Gets a result model and covers only the mount. |
| `control.mount.slew` | method, moves hardware | 2 | dropped | - | - | 5 | 8 |  | Public, as today. |
| `control.mount.sync` | method, moves hardware | - | dropped | - | - | 2 | 2 |  | Private. Only the library calls it. |
| `control.mount.park` | method, moves hardware | 2 | dropped | - | - | 4 | 4 |  | Public, as today. |
| `control.mount.unpark` | method, moves hardware | 2 | dropped | - | - | 3 | 5 |  | Public, as today. |
| `control.mount.set_tracking` | method, moves hardware | 2 | dropped | - | - | 3 | 4 |  | Public, as today. |
| `control.mount.manual_move` | method, moves hardware | 2 | dropped | - | - | 1 | 1 |  | Public, as today. |
| `control.mount.abort_motion` | method | 2 | dropped | - | - | 2 | 4 |  | Public, as today. |
| `control.mount.cancel_centering` | method | 1 | dropped | - | - | 5 | 1 |  | Public, as today. |
| `control.mount.set_slew_rate` | method, moves hardware | 2 | dropped | - | - | 2 | 3 |  | Public, as today. |
| `control.mount.compute_pointing_correction` | method, computes | - | dropped | - | - | 1 | - | Tests only. Name family (D9). | Private. Only tests use it (Section 8). |
| `control.imaging.status` | method | 1 | offered | - | - | 1 | - |  | Public, as today. |
| `control.imaging.capture_image` | property, moves hardware | 1 | dropped | - | - | 2 | 4 |  | Public, as today. |
| `control.imaging.set_filter` | method, moves hardware | 2 | dropped | - | - | 2 | 2 |  | Public, as today. |
| `control.imaging.focus_move` | method, moves hardware | 2 | dropped | - | - | 2 | 2 |  | Public, as today. |
| `control.imaging.compute_focus_correction` | method, computes | - | dropped | - | - | - | - | No reader found, tests included. Name family (D9). | Private. No reader found; candidate for deletion. |
| `control.imaging.save_focus_model` | method, writes | - | withheld | - | - | 1 | - |  | Private. Only tests use it (Section 8). |
| `control.guiding.status` | method | 1 | offered | - | - | 1 | 3 |  | Public, as today. |
| `control.guiding.run_loop` | method, moves hardware | 1 | dropped | - | - | 2 | 1 |  | Public, as today. |
| `control.guiding.stop_loop` | method | 1 | dropped | - | - | 3 | - |  | Public, as today. |
| `control.guiding.pulse` | method, moves hardware | - | dropped | - | - | 2 | 5 |  | Private. Only the library calls it. |
| `control.guiding.expose` | method, moves hardware | 1 | dropped | - | - | 3 | 3 |  | Public, as today. |
| `control.guiding.get_image` | method | - | dropped | - | - | 1 | - |  | Private. Only tests use it (Section 8). |
| `control.guiding.drain_external_pulses` | method, writes | - | dropped | - | - | 1 | 2 |  | Private. Only the library calls it. |
| `control.guiding.compute_correction` | method, computes | - | dropped | - | - | 1 | - | Tests only. Name family (D9). | Private. Only tests use it (Section 8). |
| `control.guiding.run_calibration` | method, moves hardware | - | dropped | - | - | 1 | 1 | Measures (D10). | Private. Only the library calls it. |
| `control.guiding.run_backlash_calibration` | method, moves hardware | - | dropped | - | - | 1 | - | Measures (D10). | Private. Only tests use it (Section 8). |
| `control.guiding.run_exposure_test` | method, moves hardware | - | dropped | - | - | 1 | - | Measures (D10). | Private. Only tests use it (Section 8). |
| `control.guiding.save_calibration` | method, writes | - | withheld | - | - | 1 | 1 |  | Private. Only the library calls it. |
| `control.guiding.save_run` | method, writes | - | dropped | - | - | 2 | - |  | Private. Only tests use it (Section 8). |
| `control.remote.list` | method | 1 | offered | 1 | - | 2 | - |  | Public, as today. |
| `control.remote.check_connection` | method | - | offered | 1 | 1 | 2 | 1 |  | Public, as today. |
| `control.remote.frame_status` | method | - | offered | - | - | - | - | AI only. See Section 6. | Public, as today. |
| `control.remote.sync_frames` | property | 2 | offered | 1 | 1 | 1 | 1 | See Section 6. | Public, as today. |
| `control.remote.sync_logs` | property | 1 | offered | - | - | 1 | - | See Section 6. | Public, as today. |
| `control.safety.status` | method | 1 | offered | - | - | 3 | 1 |  | Public, as today. |
| `control.safety.assess` | method | - | dropped | - | - | 4 | 1 |  | Private. Only the library calls it. |
| `control.safety.save_rule_set` | method, writes | - | withheld | - | - | 1 | - |  | Private. Only tests use it (Section 8). |
| `control.safety.execute_safe_state` | method | - | dropped | - | - | 1 | 1 |  | Private. Only the library calls it. |
| `control.safety.open_enclosure` | method, moves hardware | - | dropped | - | - | 2 | 1 | Moves hardware, under `safety`. Could be equipment. | Private. Only the library calls it. |
| `control.safety.close_enclosure` | method, moves hardware | - | dropped | - | - | 2 | 1 | Moves hardware, under `safety`. Could be equipment. | Private. Only the library calls it. |
| `control.safety.apply_promotion_decision` | method, writes | 1 | withheld | - | - | 3 | - | Family (D11). Backend through RPC table. | Public, as today. |
| `control.safety.enter_monitoring_mode` | method, writes | 1 | withheld | - | - | 3 | - | Family with `apply_promotion_decision` (D11). | Public, as today. |
| `control.safety.enter_controller_mode` | method, writes | 1 | withheld | - | - | 1 | 1 | Family with `apply_promotion_decision` (D11). | Public, as today. |
| `control.equipment.status` | method | 3 | offered | 1 | - | 3 | - |  | Public, as today. |
| `control.equipment.connect` | method, moves hardware | 2 | dropped | 1 | - | 2 | - |  | Public, as today. |
| `control.equipment.disconnect` | method, moves hardware | - | dropped | - | - | 1 | - |  | Public. Stays public although only tests read it. |
| `control.equipment.set_active_telescope` | method, writes | 1 | withheld | - | - | 2 | - |  | Public, as today. |
| `control.equipment.set_active_camera` | method, writes | 1 | withheld | - | - | 1 | - |  | Public, as today. |
| `control.equipment.set_device_property` | method, moves hardware | 1 | dropped | - | - | 1 | - |  | Public, as today. |
| `control.equipment.cooling_ramp_rate` | method | - | dropped | - | - | - | - | No reader found. Arithmetic on the camera policy. | Private. No reader found; candidate for deletion. |
| `control.equipment.summarize_device` | method | - | dropped | - | - | - | - | No reader found. Classifies values the caller passes. | Private. No reader found; candidate for deletion. |
| `control.equipment.save_commissioning_run` | method, writes | - | dropped | - | - | 1 | - |  | Private. Only tests use it (Section 8). |

### Review (12)

| Member | Kind | Backend | MCP | Notebooks and how-to | Scripts | Tests | Library | Note | Decision |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `planning.get_advisory` | method | 1 | offered | - | - | 2 | 4 | `kind` changes inputs and return type (D5). Fits two groups (Section 5). | Public. Results and data kinds stay in `review`; the tonight and calibration kinds go to `planning` (check the split is clean). |
| `planning.get_depth_goal` | method | 1 | offered | - | - | 2 | 1 | Fits Planning too (Section 5). | Public, with `set_depth_goal`. |
| `execution.record_divergence` | method, computes | 1 | dropped | - | - | 1 | 1 | Builds a record and writes nothing. Fits badly (Section 5). | Public. Renamed `build_divergence_record`. |
| `execution.reconcile_session` | method, writes | 1 | withheld | - | - | 1 | - | Runs after a night and writes. Fits badly (Section 5). | Public. |
| `control.mount.run_polar_alignment_assist` | method, computes | - | dropped | - | - | 1 | - | Tests only. Fits a model, moves nothing. | Private. Only tests use it (Section 8). |
| `control.guiding.refit_spectrum` | method, writes | 1 | dropped | - | - | 2 | 1 | Analysis and a save in one call. Fits badly (Section 5). | Public, in `guiding`. |
| `control.guiding.save_spectrum_analysis` | method, writes | - | withheld | - | - | 1 | - |  | Private. Only tests use it (Section 8). |
| `control.history.query` | property, computes | 2 | offered | - | - | 12 | 14 | Returns a dict or a typed list by `kind` (D6). | Public. Returns a result model. |
| `control.history.get_live_session_status` | method | 1 | offered | - | - | 2 | 1 | About the running session. Fits Execution too. `refresh` copies logs (Section 6). | Public. |
| `control.history.frame_guiding` | method, computes | - | offered | - | - | - | - | AI only. Could be a `query` kind (D6). | Public. Suggested as a kind of `query`. |
| `control.history.get_performance_envelope` | method | 1 | offered | - | - | 3 | 1 |  | Public. |
| `control.history.save_ekos_session_context` | method, writes | - | dropped | - | - | - | 2 | The one write in `history`. Fits badly (Section 5). | Private. Only the library calls it. |

## 5. What fits badly

The sort in Section 4 was one reading. These calls did not sit cleanly under any one of the four, and the Decision column says where each went:

| Call | Why it is awkward | Decision |
| :--- | :--- | :--- |
| `planning.get_advisory` | `kind="quality"` is a judgment of results (Review). `kind="tonight"` ranks targets to image (Planning). `kind="calibration"` says what calibration to take (Planning). It is three questions in one call. Splitting by kind would place each one easily. | Decided: split. Results and data stay in `review`; tonight and calibration go to `planning`. Check the split is clean. |
| `planning.refresh_suggestions` | It reads judged results (Review), and writes suggested packages (Planning). It is what connects the two. | Decided: stays in `planning`. |
| `planning.get_depth_goal` | It reports how close a target is to its goal (Review). Its partner `set_depth_goal` chooses a goal (Planning). A get and a set in different groups would read strangely. | Decided: `planning`, with `set_depth_goal`. |
| `execution.reconcile_session` | Runs after a night, writes calibration counts and the session record. It is Execution's last step and also a record of what happened. | Decided: `execution`. |
| `execution.record_divergence` | It compares a computed and an observed value and writes nothing. It belongs with the delegation evidence. The backend's execution service calls it. | Decided: `execution`, renamed `build_divergence_record`. |
| `control.history.get_live_session_status` | About the session running now. Execution has the session; Review has the analysis. Its `refresh` copies logs, which is remote work. | Decided: `review`. |
| `control.history.save_ekos_session_context` | The one write in `history`. No outside reader; its only callers are in the library. It is storage, not a public question. | Decided: private. |
| `control.guiding.refit_spectrum`, `save_spectrum_analysis` | A fit of past guiding data, in the group that sends pulses. | Decided: both stay in `control`. `refit_spectrum` is public; `save_spectrum_analysis` is private (test-only). |
| `control.mount.run_polar_alignment_assist` | Fits an error from past plate solves and moves nothing. | Decided: stays in `control`; private (test-only). |
| `control.safety.open_enclosure`, `close_enclosure` | Hardware moves. They sit under safety because closing must refuse if the mount is not clear. | Decided: private (only the library calls them). |
| `compute_*` corrections | Control has three (`mount`, `imaging`, `guiding`). They compute intent for the delegation design; none is called outside tests. They may belong together. | Decided: private (test-only or no reader). |
| The 11 driver properties on `control` | Injection points, for tests and the backend. Under the `astrometricslib` decision, swapping drivers went private. The same question applies here. | Decided: private. `Wayfinder` builds its own driver. |

## 6. Does remote sync belong under Control or Execution?

The members are `control.remote.list`, `check_connection`, `frame_status`, `sync_frames` and `sync_logs`. **Decided: they stay as 5 public calls under `control`.** The evidence behind the question:

**Reasons for Control, where it lives:**

- The Architecture paper lists "remote file transfer" among Control's topics.
- It works with no session running. The backend's `sync_service.py` and `ingestion_service.py` call it, as does `wayfindinglib/scripts/download_all_target_frames.py`. The redesign record gives the same argument for safety living in Control.
- Execution sits on top and is required by neither peer. `control.history.get_live_session_status(refresh=True)` copies the newest logs. If the transfer moved to Execution, a Control or Review call would have to reach up, against the contract that only the composition root imports Execution. The transfer tasks would have to stay below.

**Reasons for Execution:**

- In the user's workflow, copying frames and logs is a step of a night. `execution.reconcile_session` could follow it.
- It writes to the library and records new frame measurements. It moves no hardware, so "operating equipment" describes it poorly.

**A third reading:** neither. It is data transfer: reach the computer, copy, report. It could be its own small group, or sit with Review, as it feeds the history. `frame_status` is a read and `sync_*` are writes, with no equipment involved except the computer at the telescope.

## 7. Overlaps and design-rule findings

The agreed rules: `get(id)` reads one; `query(filters, detail=, limit=, offset=)` searches and returns a result model; `measure_*` measures; changing data uses a clear verb, never chosen by a parameter; an optional write is off by default; one call with parameters over near-duplicates, but merge only when the verb and inputs match.

| Ref | Calls | Finding | Decision |
| :--- | :--- | :--- | :--- |
| D1 | `planning.resolve_target_coordinates`, `lookup_coordinates` | Same search, through the same sky engine. `lookup_coordinates` shapes a short dictionary for the AI. `resolve_target_coordinates` returns a `Target` or a `StellarObject`. Neither has a backend reader. | Decided: one coordinate lookup, `lookup_coordinates`, with a result model. |
| D2 | `planning.find_sources`, `get_sources`, `get_library_star_summaries`, `get_online_catalog_sources` | Four ways to ask what is near a point. Inputs match closely (`ra_deg`, `dec_deg`, `radius_deg`). Returns differ: `dict`, `list[SkySource]`, `list[dict]`, `list[SkySource]`. A candidate for one `query_sources(..., source=, detail=)`. Two are AI only. | Decided: one `query_sources(..., source=)` (name agreed), once the code confirms a shared shape. |
| D3 | `planning.get_imaged_field_centers` | A wrapper on `astrometrics.targets.imaged_field_centers`. One script reads it, and the manifest says the other tool replaced it. | Decided: remove `get_imaged_field_centers`. |
| D4 | `planning.create_plan(kind=)`, `edit_package`, `edit_queue` | The rules say a write's verb is not chosen by a parameter. `create_plan` makes a sequence, a package or a session by `kind`, each with its own arguments. `edit_package` also dismisses and deletes. `edit_queue` adds, reorders and removes. | Decided: clear-verb calls for all three: `create_sequence`, `create_package`, `create_session`; `update_package`, `dismiss_package`, `delete_package`; `add_to_queue`, `reorder_queue`, `remove_from_queue`. The code has actions beyond these (Section 12). |
| D5 | `planning.get_advisory(kind=)`, `get_plan(kind=)` | `kind` changes the required arguments and the return type. The rule allows a parameter to pick what is read only when the inputs match. `get_plan` also lists, which the rules call `query`. Both check their arguments by kind at run time (`reject_unused_arguments`). | Decided for `get_plan` (one) with `query_plans` (lists; name agreed), and `get_advisory` split by group. Open for the exact kinds. |
| D6 | `control.history.query(kind=)`, `frame_guiding`, `get_live_session_status`, `get_performance_envelope` | `query` has 11 kinds, and returns a `dict` for 8 and a typed list for 3. The rule asks for a result model. `frame_guiding` could be a kind. | Decided: `query` returns a result model; `frame_guiding` suggested as a kind. |
| D7 | `status` on `mount`, `imaging`, `guiding`, `safety`, `equipment` | One clear pattern, each with `include`. `mount.status` also reads imaging devices, which overlaps `imaging.status`. Its return is a plain `dict`; the others return models. `get` might be the rule's word. | Decided for `mount.status`: result model, mount only. `status` is a documented pattern on the `control` sub-groups, so the name stays. |
| D8 | `deep_catalog_status`, `list_catalog_driver_metadata`, `check_connection`, `cooling_ramp_rate` | Names do not follow the verb rules. | Decided: rename `list_catalog_driver_metadata` to `query_catalogs` and `deep_catalog_status` to `get_deep_catalog_status`. `check_connection` is open; `cooling_ramp_rate` is private. |
| D9 | `mount.compute_pointing_correction`, `imaging.compute_focus_correction`, `guiding.compute_correction` | One family with uneven names. | Decided by the test-only rule: all three are private. |
| D10 | `guiding.run_calibration`, `run_backlash_calibration`, `run_exposure_test` | These measure, and save too. `measure_*` with `save=` is the rule's shape, though `run_` says plainly that hardware moves. | Decided by the test-only and library-only rules: all three are private. |
| D11 | `safety.apply_promotion_decision`, `enter_monitoring_mode`, `enter_controller_mode` | The two `enter_*` set all capabilities at once; the other sets one. The verbs are clear, so this may be right as it is. | No decision needed; the three stay public. |
| D12 | `save_focus_model`, `save_calibration`, `save_spectrum_analysis`, `save_run`, `save_rule_set`, `save_ekos_session_context`, `save_commissioning_run` | Seven saves, on five children. None has a backend or notebook reader. | Decided by the test-only and library-only rules: all seven are private. |
| D13 | Parameter names | `get_advisory` takes `filter`, while `capture_image`, `set_filter` and `frame_guiding` take `filter_name`. `calculate_panels` takes `center_ra`, `center_dec` as text while the source calls take `ra_deg`, `dec_deg`. `get_plan` takes both `session_id` and `night_id`. `refresh_suggestions` takes `target_ids`, others take `target`. | Decided: match `astrometricslib`. `filter` becomes `filter_name` (a `str`). `center_ra`, `center_dec` become `ra_deg`, `dec_deg` in degrees. `night_id` becomes `observing_night_id`, and `session_id` stays. `target_ids` becomes `target`, one id or a list. The text form of `calculate_panels` is not needed by the app: the backend and the UI already send degrees. See "What reading the code showed" below. The user asked that the `calculate_panels` bug be noted; it is. |
| D14 | Plain dictionary returns | 14 members return `dict[str, Any]` in whole or in part: `planning.lookup_coordinates`, `planning.find_sources`, `planning.get_library_star_summaries`, `planning.list_catalog_driver_metadata`, `planning.get_constellation_lines`, `planning.get_imaged_field_centers`, `planning.build_deep_star_catalog`, `control.mount.status`, `control.guiding.drain_external_pulses`, `control.remote.frame_status`, `control.remote.sync_frames`, `control.remote.sync_logs`, `control.history.query`, `control.history.frame_guiding`. | Decided: every public member in the list gets a result model. Some are merged, renamed or removed, and the Decisions section lists the members. `drain_external_pulses` is private. |
| D15 | `guiding.expose`, `get_image` | No return type declared beyond `Any`. | Decided: `expose` gets a result model. `get_image` is private and gets one if it stays. |

**What reading the code showed about D13.**

- **`calculate_panels` has a bug that the change fixes (noted at the user's request).** `parse_coordinate_string` reads a bare number as hours for a right ascension, so the text "10.684" means 10.684 hours, not degrees. The UI sends degrees (`useMosaicPreview.ts` documents "in degrees"), and `MosaicService.preview_mosaic` turns them into text with `str()`. A right ascension of 10.684 degrees therefore came out as panels centered at 160.26 degrees when I ran `calculate_panels` on it. The test value "180" hides it, because 180 hours is 2700 degrees, which is also 180 degrees once wrapped. Taking `ra_deg` as a number removes the misreading. I did not run the full UI path, and I changed no code.
- **Who uses the text form.** Only tests use it: `wayfindinglib/api/test/test_planning.py` (2 calls) and `wayfindinglib/tasks/planning_tasks/test/test_mosaic_tasks.py` (15 calls, with strings such as "20h 58m 47s", "10:00:00" and "180"). They would change to degrees. The MCP server offers `planning_calculate_panels`, so an AI that holds sexagesimal text would have to convert it first. The text form is nowhere needed by the app.
- **`filter` is also the name of a model field.** `CalibrationEntry.filter` is a field of a `wayfindinglib` model. The call's parameter can change without renaming the field. The MCP tool schema for `planning_get_advisory` changes with the parameter name.
- **Other names that may need to change.** These are not in D13; they are suggestions to confirm. `get_advisory` takes `gain_setting` and `sensor_temperature_c`, where `astrometricslib` uses `gain` and `temperature_c` (suggest `gain`, `temperature_c`). `get_sources` takes `limiting_magnitude`, `get_online_catalog_sources` and `deep_catalog_status` take `magnitude_limit`, and `get_library_star_summaries` takes `magnitude_range`, where `stars.query` uses `magnitude_min` and `magnitude_max` (suggest those two; the merge into `query_sources` is the natural time). `get_visibility` takes `time` and `end_time`, while `astrometricslib` uses `since` and `until` for time ranges, but the meaning differs (a moment and a span, not a filter on records), so I suggest no change. A few docstrings (`star_result_rules.py`, `result_judgment_tasks.py`) still write `get_advisory(..., target_id=...)`, while the parameter is `target`.

## 8. Members with no reader, and things only AI tools use

Decided: every member in the first three lists below is private, except `control.equipment.disconnect`, which stays public. The "Only AI tools read them" list is not decided as a group: its members sit in the decisions above.

**No reader found at all, tests included (10):** `control.focuser_driver`, `control.filter_wheel_driver`, `control.camera_driver`, `control.guide_camera_driver`, `control.enclosure_driver`, `control.switch_driver`, `control.guiding_driver`, `control.imaging.compute_focus_correction`, `control.equipment.cooling_ramp_rate`, `control.equipment.summarize_device`.

**Only tests read them (17):** `execution.execute_meridian_flip`, `execution.recover_fault`, `execution.recover_guide_star_loss`, `control.mount_driver`, `control.weather_driver`, `control.mount.compute_pointing_correction`, `control.mount.run_polar_alignment_assist`, `control.imaging.save_focus_model`, `control.guiding.get_image`, `control.guiding.compute_correction`, `control.guiding.run_backlash_calibration`, `control.guiding.run_exposure_test`, `control.guiding.save_spectrum_analysis`, `control.guiding.save_run`, `control.safety.save_rule_set`, `control.equipment.disconnect`, `control.equipment.save_commissioning_run`. Most driver properties are in this list or the one above; a property is set in tests, so a count here means little.

**Only the library itself calls them, with no outside reader (10):** `control.mount.sync`, `control.guiding.pulse`, `control.guiding.drain_external_pulses`, `control.guiding.run_calibration`, `control.guiding.save_calibration`, `control.history.save_ekos_session_context`, `control.safety.assess`, `control.safety.execute_safe_state`, `control.safety.open_enclosure`, `control.safety.close_enclosure`. These are candidates for private methods, if the library can reach them another way.

**Only AI tools read them (6):** `planning.resolve_target_coordinates`, `planning.lookup_coordinates`, `planning.find_sources`, `planning.get_library_star_summaries`, `control.remote.frame_status`, `control.history.frame_guiding`. All are offered on the MCP server. The library's users are people writing scripts and notebooks, and AI agents use the MCP tools, so these have no user the Python API is meant for. The question is whether they stay public methods because the MCP server reflects public methods.

Three exported names also look like tool support rather than user API: `fit_reply_to_budget` is read only by `mcp_servers/common/tool_registry.py`, and its own comment says the library replies in full. `SimulatorIndiInterface` and `IndiInterface` are injected by the backend.

## 9. Exported names

71 names today: 6 at the root, 2 in `exceptions`, 4 in `reports` and 59 private. After the decisions `group_alignment_attempts` becomes a public call and `fit_reply_to_budget` moves to `mcp_servers`, which leaves 69 names and 57 private. Readers are counts of files; "d" adds direct imports of the submodule, which tests inside `wayfindinglib` and `build/codegen/generate_types.py` use.

### Entry and sub-APIs (4)

| Name | Kind | Backend | MCP | Notebooks and how-to | Scripts | Tests | Note | Decision |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `Wayfinder` | class | 6 | 1 | 7 | 2 | 5 |  | Public, at the root. |
| `ObservatoryControl` | class | 7 | - | - | - | 16 |  | Public, at the root. |
| `ObservationPlanning` | class | 1 | - | - | - | 12 |  | Public, at the root. |
| `ObservationExecution` | class | 1 | - | - | - | 5 |  | Public, at the root. |

### Drivers and stores (3)

| Name | Kind | Backend | MCP | Notebooks and how-to | Scripts | Tests | Note | Decision |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `IndiInterface` | class | 1 | - | - | - | 7 | Driver swapping went private in `astrometricslib`. | Private (`_internal.control`). `Wayfinder` builds its own driver and store from configuration. |
| `SimulatorIndiInterface` | class | 2 | - | - | - | 1 | A test and demo driver. | Private (`_internal.control`). `Wayfinder` builds its own driver and store from configuration. |
| `ControlRecordStore` | class | 2 | - | - | - | 8 | A storage class; the backend builds one. Looks like an internal part. | Private (`_internal.control`). `Wayfinder` builds its own driver and store from configuration. |

### Control models (13)

| Name | Kind | Backend | MCP | Notebooks and how-to | Scripts | Tests | Note | Decision |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `SkyPosition` | class | 2 | - | - | - | 7 |  | Private (`_internal.control`). Calls take plain dictionaries. |
| `MountPointingModel` | class | 1 | - | - | 1 | 4 |  | Private (`_internal.control`), but the backend imports it for annotations, to be removed. Still a suggestion (Section 12). |
| `LiveGuidingStatus` | class | - | - | - | 1 | 2 |  | Private (`_internal.control`). |
| `PolarAlignmentStatus` | class | 1 | - | - | 1 | - |  | Private (`_internal.control`), but the backend imports it for annotations, to be removed. Still a suggestion (Section 12). |
| `ObservatoryCapability` | enum | 1 | - | - | - | 12 |  | Private (`_internal.control`). Calls take plain dictionaries. |
| `ImagingStatus` | class | - | - | - | - | - |  | Private (`_internal.control`). |
| `GuidingStatus` | class | - | - | - | - | - |  | Private (`_internal.control`). |
| `SafetyStatus` | class | - | - | - | 1 | - |  | Private (`_internal.control`). |
| `EquipmentStatus` | class | - | - | - | - | 1 |  | Private (`_internal.control`). |
| `CaptureResult` | class | - | - | - | - | 1 |  | Private (`_internal.control`). |
| `SiteProfile` | class | - | - | - | - | 9 | Could be Planning as well. | Private (`_internal.control`). |
| `Telescope` | class | - | - | - | 1 | 10 | Could be Planning as well. | Private (`_internal.control`). |
| `EquipmentConfiguration` | class | - | - | - | - | 5 |  | Private (`_internal.control`). |

### Planning models (31)

| Name | Kind | Backend | MCP | Notebooks and how-to | Scripts | Tests | Note | Decision |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `VisibilityReport` | class | - | - | - | - | 1 |  | Private (`_internal.planning`). |
| `ObjectVisibility` | class | - | - | - | 1 | - |  | Private (`_internal.planning`). |
| `VisibilitySpan` | class | - | - | - | 1 | - |  | Private (`_internal.planning`). |
| `MeridianStatus` | class | - | - | - | 1 | - |  | Private (`_internal.planning`). |
| `NightConditions` | class | - | - | - | - | - |  | Private (`_internal.planning`). |
| `HorizonZone` | class | - | - | - | - | 1 |  | Private (`_internal.planning`). |
| `MosaicPanel` | class | - | - | - | 1 | 2 |  | Private (`_internal.planning`). |
| `MosaicPlan` | class | - | - | - | - | - | Return of `create_mosaic`; no import found. | Private (`_internal.planning`). |
| `SequenceItem` | class | - | - | - | 1 | - | Older model; the redesign record says generated types depend on it. | Private (`_internal.planning`). Check whether it can go; generated types depend on it. |
| `SequencePlan` | class | - | - | - | 1 | 1 | Older model; the redesign record says generated types depend on it. | Private (`_internal.planning`). Check whether it can go; generated types depend on it. |
| `SequenceSummary` | class | - | - | - | 1 | 1 |  | Private (`_internal.planning`). |
| `ObservationPackage` | class | 1 | - | - | 1 | 7 | Moved to the root with `ObservationSession`, because both are what a caller holds after a plan call. | Public, at the root. |
| `PackageStatus` | enum | - | - | - | - | 3 |  | Public, in `wayfindinglib.reports`. |
| `PackageSuggestion` | class | - | - | - | - | 1 |  | Private (`_internal.planning`). |
| `PackageChange` | class | - | - | - | 1 | 1 |  | Private (`_internal.planning`). |
| `SuggestionLogEntry` | class | - | - | - | 1 | - |  | Private (`_internal.planning`). |
| `ExposureRequest` | class | 1 | - | - | 1 | 13 |  | Private (`_internal.planning`). Calls take plain dictionaries. |
| `DitherConfig` | class | 1 | - | - | - | 5 |  | Private (`_internal.planning`). Calls take plain dictionaries. |
| `FrameType` | enum | - | - | - | - | 16 |  | Private (`_internal.planning`). Calls take plain dictionaries. |
| `DeepCatalogStatus` | class | - | - | - | - | 1 |  | Private (`_internal.planning`). |
| `DeepCatalogEstimate` | class | - | - | - | - | - |  | Private (`_internal.planning`). |
| `SkySource` | class | - | - | - | 1 | 2 |  | Private (`_internal.planning`). |
| `DepthGoalStatus` | class | - | - | - | 1 | - |  | Private (`_internal.planning`). |
| `DepthGoalState` | enum | - | - | - | - | 1 |  | Public, in `wayfindinglib.reports`. |
| `DepthLevel` | enum | - | - | - | - | 2 |  | Public, in `wayfindinglib.reports`. |
| `QueueRequest` | class | 1 | - | - | - | 2 |  | Private (`_internal.planning`). Calls take plain dictionaries. |
| `StartTimeMode` | enum | 1 | - | - | - | 11 |  | Private (`_internal.planning`). Calls take plain dictionaries. |
| `ChecklistItem` | class | - | - | - | 1 | 3 |  | Private (`_internal.planning`). |
| `Framing` | class | - | - | - | 1 | 1 |  | Private (`_internal.planning`). |
| `PlanChange` | class | 1 | - | - | 1 | 3 |  | Private (`_internal.planning`), a result. |
| `PlanChangeItem` | class | - | - | - | 1 | 3 |  | Private (`_internal.planning`). |

### Execution models (6)

| Name | Kind | Backend | MCP | Notebooks and how-to | Scripts | Tests | Note | Decision |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `ObservationSession` | class | 1 | - | 1 | 1 | 18 | Read by Planning (`get_plan`) and Execution. | Public, at the root. |
| `ObservationSessionSummary` | class | - | - | - | - | - | No reader found. | Private (`_internal.execution`). |
| `RunState` | enum | - | - | - | - | 2 |  | Public, in `wayfindinglib.reports`. |
| `SessionProgress` | class | - | - | - | - | 1 |  | Private (`_internal.execution`). |
| `EntryProgress` | class | - | - | - | - | - | No reader found. | Private (`_internal.execution`). |
| `ObservationSessionWithProgress` | class | - | - | - | 1 | 1 |  | Private (`_internal.execution`). |

### Review models and helpers (12 today; 10 stay private)

| Name | Kind | Backend | MCP | Notebooks and how-to | Scripts | Tests | Note | Decision |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `TargetDatasetReport` | class | - | - | - | 1 | 5 |  | Private (`_internal.review`). |
| `TonightRanking` | class | - | - | - | 1 | 3 |  | Private (`_internal.review`). |
| `TargetStatement` | class | - | - | - | 1 | 11 |  | Private (`_internal.review`). |
| `CalibrationAdvisory` | class | - | - | - | - | 2 |  | Private (`_internal.review`). |
| `NightListEntry` | class | - | - | - | 1 | 1 |  | Private (`_internal.review`). |
| `TargetChange` | class | - | - | - | 1 | 1 |  | Private (`_internal.review`). |
| `NightQuality` | class | - | - | - | 1 | 2 |  | Private (`_internal.review`). |
| `LiveSessionStatus` | class | - | - | - | 1 | - |  | Private (`_internal.review`). |
| `AlignmentAttempt` | class | 1 | - | - | 1 | 2 |  | Private (`_internal.review`). Result type of the public `group_alignment_attempts` call; the model stays private. |
| `AlignmentTargetSession` | class | 1 | - | - | 1 | - |  | Private (`_internal.review`). Result type of the public `group_alignment_attempts` call; the model stays private. |
| `group_alignment_attempts` | function | 1 | - | - | - | 1 | A function among models; one backend reader. | Becomes a public call (decided; name proposed `review.query_alignment_sessions`). Its models stay private. |
| `fit_reply_to_budget` | function | - | 1 | - | - | 4 | MCP helper. Only MCP reads it. | Moves into `mcp_servers` (decided). |

### Errors (2)

| Name | Kind | Backend | MCP | Notebooks and how-to | Scripts | Tests | Note | Decision |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `DelegationPolicyValidationError` | error | - | - | - | - | 2 | The library's one error class of its own. The categories come from `astrometricslib`. | Public, in `wayfindinglib.exceptions`. |
| `PlanRevisionConflictError` | error | - | - | - | - | 5 | Lives in a session model module, not with errors. | Public, in `wayfindinglib.exceptions`. |

**Returned but not exported.** Public members return these, and none is in `__all__`: `BulkDelegationOutcome`, `DelegationPolicy`, `DeviceState`, `DivergenceRecord`, `FaultRecord`, `FocusCorrection`, `GuideExposureLadder`, `GuideStarLossEvent`, `GuiderCalibration`, `GuidingCorrection`, `GuidingSpectrumAnalysis`, `MeridianFlipOutcome`, `ObservationSessionRecorder`, `PerformanceEnvelope`, `PointingCorrection`, `SafeStateOutcome`, `SafetyAssessment`. Some members also take unexported types as arguments, such as `SafetyRuleSet`, `FocusModel` and `SessionRunnerDependencies`. The `astrometricslib` decision says results come back from public calls with no import needed, so this may be fine. Inputs are the harder case: a caller cannot build what it cannot import.

**Direct submodule imports:** the backend's code and the MCP servers use root imports only. `documentation/notebooks/wayfinding/execution/scripts/observatory_session_recorder.py` imports four submodules and has a ruff exception. `build/codegen/generate_types.py` imports 23 submodules, also excepted. Twenty direct imports in `backend/tests/` reach `wayfindinglib.api.control...`, `wayfindinglib.drivers...` and others. Tests under `wayfindinglib/` do so freely. `wayfindinglib/scripts/build_deep_star_catalog.py` and `generate_projection_reference.py` are inside the library.

## 10. What `wayfindinglib` needs from `astrometricslib` under `API_TIERS_INVENTORY.md`

`wayfindinglib` code (not tests) would import the following public names.

- **Root (9):** `Astrometrics`, `AppConfiguration`, `get_configuration`, `configure_logging`, `configure_offline_iers`, `Target`, `FrameRecord`, `StellarObject`, `FilterType`.
- **`exceptions` (12):** `AstrometricsError`, `NotFoundError`, `InvalidArgumentError`, `ProcessingError`, `ConflictError`, `ConfigurationError`, `HardwareError`, `StorageError`, `ExternalServiceError`, `PermissionDeniedError`, `PlateSolveFailedError`, `FITS_READ_ERRORS`.
- **`reports` (9):** `QualityMeasurement`, `SubjectLevel`, `MeasurementStatus`, `MeasurementReference`, `StarResultReference`, `QualityLimit`, `LimitTier`, `CaptureSetting`, `observing_night_id`.
- **`jobs` (6):** `JobStore`, `ProcessingJob`, `background_job`, `registered_job`, `capture_job_logs`, `get_current_job`.
- **`drivers` (8):** `connect_db`, `Butler`, `AbstractButler`, `DatasetSpec`, `file_lock`, `locks_directory`, `scratch_directory`, `require_mounted_storage`.

It also uses 34 names that are going internal or are open: `DATA_ERRORS`, `ONLINE_QUERY_ERRORS`, `frame_is_spectral`, `classify_and_sort_fits_files`, `derive_target_sessions`, `FrameSelection`, `select_library_frames`, `target_object_category`, `parse_coordinate_string`, `parse_iso_time`, `has_catalog_magnitude`, `frame_subject_id`, `resolve_camera_profile`, `AMBIGUOUS_RMS_GAP`, `NO_GOOD_MATCH_RMS`, `DEFAULT_DARK_TEMPERATURE_TOLERANCE_C`, `EARLIER_NIGHTS_VARIANT`, `SATURATED_BLOB_MINIMUM_PIXELS`, `SATURATED_FRAME_FRACTION`, `UNKNOWN_CAPTURE_SETTING_CAMERA`, `PIXEL_BLUR_FWHM_SQUARED_FACTOR`, `NOISE_WEIGHT_MODE`, `aperture_identity_from_number`, `load_odd_even_aperture_record`, `group_frame_weights`, `wfwhm_weights`, `choose_stack_weight`, `fold_exposure_lengths`, `safe_json_dumps`, `check_choice`, `check_include`, `reject_unused_arguments`, `resolve_target`, `to_epoch_seconds`.

Under Document 1, these resolve as follows. Section 5 and Section 7 of `API_TIERS_INVENTORY.md` give each one's reasoning.

- **New calls on `targets`:** `select_frames` (decided), `resolve` (decided), `add_frames`, `sessions`.
- **New calls on `processing`:** `predict_stack_weights` (decided), `calibration.add_frames`, `calibration.camera_profile` (a public call is decided; name and home open). Several camera callers hold only `config` and would need an `Astrometrics` handle passed in.
- **New call on `quality`:** a read of the per-aperture noise record.
- **New values on rows:** `FrameRecord.is_spectral` and `subject_id`; `Target.object_category`, `ra_deg` and `dec_deg`; the spectral match limits; the dark temperature tolerance; the saturation limits.
- **Own copies:** `check_choice`, `check_include`, `reject_unused_arguments`, `to_epoch_seconds`, `safe_json_dumps`, `DATA_ERRORS`, `ONLINE_QUERY_ERRORS` (decided); by suggestion `has_catalog_magnitude`, `PIXEL_BLUR_FWHM_SQUARED_FACTOR` and a coordinate text parser.
- **Values on returned rows (decided):** the science limits (`AMBIGUOUS_RMS_GAP`, `NO_GOOD_MATCH_RMS`, `DEFAULT_DARK_TEMPERATURE_TOLERANCE_C`, `SATURATED_BLOB_MINIMUM_PIXELS`, `SATURATED_FRAME_FRACTION`).
- **Open:** `EARLIER_NIGHTS_VARIANT` (not a limit, so the science-limits decision does not cover it; suggested public in `reports`).

`wayfindinglib`'s calls on `Astrometrics` sub-APIs today are `targets.get`, `list`, `query`, `create`, `add`, `save`, `reindex_frames`, `read_frame_header`, `imaged_field_centers`; `stars.get`, `query`; `quality.query`, `capture_settings`, `metrics`, `measure_frames`, `record_frame_measurements`, `set_aside_frames`; `processing.plate_solve`; `processing.calibration.query`, `reindex_frames`, `save`. They use public calls and need no change for this review. That list comes from a text search for `astrometrics.` chains, so a call through another variable name may be missing.

The same "public only" ruling now applies here, and it is decided: the backend and MCP servers use only the public modules in the Decisions section, and tests of either library may import `_internal` (see F3 in `API_TIERS_INVENTORY.md`). The three unresolved conflicts are the places where this does not yet hold.

## 11. What I could not check

- Member reader counts come from a text search, as Section 2 explains. Generic names may be undercounted through other object names. Nothing here should be removed on the count alone.
- The `tool_class` shown in the Kind column comes from the MCP manifest, so a member with no manifest entry shows no class.
- The manifest's note on `get_constellation_lines` (empty list because of a wrong file path) was not confirmed by a run.
- The user's private scripts, outside the repository.
- Meridian flips and fault recovery. No real caller was found, and the user chose not to look into it now.
- The `calculate_panels` misreading was shown by calling the task function once, not through the backend and UI.

## 12. Remaining before building

Only what is really undecided. Everything else in this document is decided.

1. **`group_alignment_attempts`.** It becomes a public call and its models stay private (decided). The name is open: the proposal is `review.query_alignment_sessions`. The backend would pass plain data, so the input shape needs fixing too.
2. **`MountPointingModel` and `PolarAlignmentStatus`.** The backend imports them for annotations. Suggestion: remove the annotations.
3. **Checks before building.** Whether the four source calls share a shape for `query_sources`; whether the `get_advisory` tonight and calibration kinds split cleanly; whether `SequenceItem` and `SequencePlan` can go; whether `frame_guiding` fits as a `query` kind.
4. **Verbs for the actions the code has beyond the agreed six.** `edit_package` also reopens, undismisses (and dismisses one row by `source_key`); `edit_queue` also sets entry exposures, adds checklist items and sets framing. The agreed names do not cover these.
5. **The INDI worker.** How the backend's separate-process worker starts once `Wayfinder` builds its own driver.
6. **Result model names** for the calls in the Decisions section, the extra parameter names at the end of Section 7 (`gain`, `temperature_c`, `magnitude_min`, `magnitude_max`), and the `check_connection` name (D8).
