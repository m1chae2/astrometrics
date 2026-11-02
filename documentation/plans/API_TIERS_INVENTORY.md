# API tiers inventory for `astrometricslib`

*Working plan for issue #97 · 2026-10-31 · Status: the user's final decisions of 2026-10-31 are applied (see "Decisions after the first draft" and "Final decisions"). Section 9 lists the methods of the sub-APIs with the decided outcome of each. Section 11 lists what is still undecided. Nothing has moved.*

This document sorts every name in `astrometricslib.__all__` into its new home. It records the user's decisions and does not reopen them. Rows the user has not decided say "open" and carry a suggestion; there are 6.

**This replaces the flat `astrometricslib.api` tier that #97 planned.** That plan gave `wayfindinglib`, the backend and the MCP servers a shared tier of integration names. Under the new decision, "private" means only `astrometricslib` itself may use a name. `wayfindinglib`, the backend and the MCP servers use public names only. The issue text (the "Public API in two tiers" items, the `ignore_imports` item and the "Naming to settle" note about `wayfindinglib.api`) needs rewriting to match.

## 1. The decisions recorded here

| Destination | Names | What it is for |
| :--- | :--- | :--- |
| Root, `from astrometricslib import ...` | 16 | Entry and setup, the sub-API classes (`Jobs` moved out), the core objects |
| `astrometricslib.exceptions` | 17 | The errors a script catches, `ErrorInfo`, `to_error_info`, `error_from_info` and `FITS_READ_ERRORS`. Not at the root |
| `astrometricslib.reports` | 20 | Group 8's 11 reports, the 7 quality row shapes, `CaptureSetting` and `observing_night_id`. Importable |
| `astrometricslib.jobs` | 12 | Job tracking, including the `Jobs` class (`astrometrics.jobs` is an instance of it) |
| `astrometricslib.drivers` | 10 | Tools other apps can use, including `is_path_inside` and `resolve_mounted_path` |
| `astrometricslib._internal` | see below | Everything else, in logical groups. Only `astrometricslib` itself may use it |
| Dropped | 1 | `export_target_lineage_as_prov_xml`. Lineage comes with what results return |

Other decisions:

- `resolve_target` becomes a call on `targets`.
- `check_choice`, `check_include`, `reject_unused_arguments`, `to_epoch_seconds` and `safe_json_dumps` become `wayfindinglib`'s own copies.
- Frame selection helpers become calls on `targets`. Stack weights become a call on `processing`.
- Swapping drivers goes private. The driver interfaces sit in `_internal.drivers`.
- `ErrorInfo`, `to_error_info`, `error_from_info` and `FITS_READ_ERRORS` go in `exceptions`. `RPC_CODES` goes private. `DATA_ERRORS` and `ONLINE_QUERY_ERRORS` go private, with `wayfindinglib` copies.
- `CaptureSetting` and `observing_night_id` go in `reports`. `resolve_camera_profile` becomes a public call. `is_path_inside` and `resolve_mounted_path` go in `drivers`.
- Science limits become values on returned rows. `MovingObjectConfig` is private and passed as a dictionary.

The rows come out as follows.

| Destination | Rows | Note |
| :--- | :--- | :--- |
| Root | 16 | The 17 first listed, less `Jobs` (now in `jobs`) |
| `exceptions` | 17 | 13 plus `ErrorInfo`, `to_error_info`, `error_from_info`, `FITS_READ_ERRORS` |
| `reports` | 20 | 18 plus `CaptureSetting`, `observing_night_id`. `EARLIER_NIGHTS_VARIANT` is still open; if it joins, 21 |
| `jobs` | 12 | 11 plus `Jobs` |
| `drivers` | 10 | 8 plus `is_path_inside`, `resolve_mounted_path` |
| `_internal`, settled | 126 | Everything private by a decision, including `MovingObjectConfig`, `RPC_CODES`, `DATA_ERRORS`, `ONLINE_QUERY_ERRORS`, `resolve_camera_profile` (behind a new call) and the science limits |
| Open | 6 | `EARLIER_NIGHTS_VARIANT` and five group 10 names (S12 to S15 and `UNKNOWN_CAPTURE_SETTING_CAMERA`); each has a suggestion |
| Dropped | 1 | Same as 1 |
| Total | 208 | Same as 208 |

## 2. How the readers were checked

A script parsed every Python file in the repository with Python's `ast` module. It also parsed the code cells of the notebooks and the Python blocks of `documentation/how_to/`. It counts:

- `from astrometricslib import X`, at any depth, including inside functions and under `TYPE_CHECKING`;
- `import astrometricslib as x`, then `x.X` and `getattr(x, "X")`;
- `from astrometricslib.models... import X`, the direct imports of submodules (listed again in Section 6).

Each number in the tables is a count of files. Column key:

- **Wayfinding**: files in `wayfindinglib/`, without tests. `2s` counts files in `wayfindinglib/scripts/`.
- **Library scripts**: files in `astrometricslib/scripts/`, which are inside the library and may use `_internal`. `1b` counts files outside the packages, in `build/` (nearly always `build/codegen/generate_types.py`).
- **Tests**: `a` is tests under `astrometricslib/`, `w` under `wayfindinglib/`, and `o` under `backend/tests/`, `mcp_servers/` or the root `conftest.py`.

## Decisions after the first draft

The user reviewed the draft on 2026-10-31. The rows above keep their readers as evidence. These decisions apply on top of them.

**Decided:**

- `_internal` holds everything private, in logical groups. Only `astrometricslib` and tests (of either library) may import it. `wayfindinglib`, the backend and the MCP servers use public names only.
- Anything that only tests use is private.
- Results come back from calls. A simple value is an argument. A dictionary is for a group of options or a list of items. A caller never needs to import an input type.
- `MovingObjectConfig` is private under the plain-dictionaries rule, because `process_target` already takes a dictionary (decided). The validation register imports it (`validation/asteroid_detection.md`, lines 36, 253 and 291), so those entries need the dictionary form.
- The answers to the open rows are in Section 5.3. The final decisions below apply on top.

**Final decisions (2026-10-31).**

- **Public home of each open row.** `ErrorInfo`, `to_error_info`, `error_from_info` and `FITS_READ_ERRORS` go in `exceptions` (17 names). `CaptureSetting` and `observing_night_id` go in `reports` (20). `is_path_inside` and `resolve_mounted_path` go in `drivers` (10). `Jobs` moves from the root to `jobs` (root 16, `jobs` 12).
- **Private.** `RPC_CODES` goes private. `DATA_ERRORS` and `ONLINE_QUERY_ERRORS` are `wayfindinglib` copies. The science limits become values on returned rows. `MovingObjectConfig` is private and passed as a dictionary. `resolve_camera_profile` becomes a public call (N6).
- **`EARLIER_NIGHTS_VARIANT`.** The science-limits decision does not cover it: it is the label of a variant, an argument value of `quality.query`, not a limit on a row. It keeps its suggestion (a public constant in `reports`, or a documented literal) and is still open.
- **`drivers` takes over the name.** Today's `astrometricslib/drivers/` contents move to `_internal.drivers` (F1).
- **The constructor.** `Astrometrics(...)` loses its driver arguments. Tests set drivers through `_internal` (F5).
- **Four script-only methods go private.** `processing.restore_excluded_frames`, `stack_subset`, `detect_point_sources` and `tune_spectroscopy_calibration`. Scripts inside `astrometricslib` may use `_internal`. Section 8 lists the register entries whose repeat steps need rewriting.
- **Job history.** The backend's job history reads move to `jobs.query` (Section 9). `JobStore` stays in `jobs` for the other readers.
- **`group_alignment_attempts`** becomes a public call in `wayfindinglib`; its models stay private. `fit_reply_to_budget` moves into `mcp_servers`. See `WAYFINDINGLIB_API_REVIEW.md`.
- **Names.** `targets.select_frames`, `targets.resolve` and `processing.predict_stack_weights` (Section 7).
- **Known bug the naming change fixes.** `wayfindinglib`'s `calculate_panels` reads a bare text right ascension as hours. Taking `ra_deg` as a number removes the misreading. This was shown by a direct test of the task function only, not through the backend and UI. `WAYFINDINGLIB_API_REVIEW.md` (D13) has the details.

**Conflicts the decisions left, and how they came out.** Each name below is private by the user's decision, yet code that must use public names only reads it. `WAYFINDINGLIB_API_REVIEW.md` has the same list.

| Name | Who reads it | Outcome |
| :--- | :--- | :--- |
| `group_alignment_attempts` (and its result types `AlignmentAttempt`, `AlignmentTargetSession`) | The backend | Decided: it becomes a public call; the models stay private. Name to be proposed (`WAYFINDINGLIB_API_REVIEW.md`). |
| `fit_reply_to_budget` | The MCP servers (`mcp_servers/common/tool_registry.py`) | Decided: move it into `mcp_servers`. |
| `MountPointingModel`, `PolarAlignmentStatus` | The backend, for annotations | Still a suggestion: remove the annotations. Listed in Section 11. |

## 3. Where the decisions do not fit

These are the places where a decision meets something in the code that it did not plan for. Each needs a word from the user before the move.

- **F1. `astrometricslib.drivers` already exists.** It is a package holding 20-odd internal modules, among them `siril_interface`, `calibration_library` and `catalog_access`. The new public `drivers` must take over that name. So its `__init__.py` holds only the 8 public names, and the current contents move to `_internal.drivers`, or the public group takes another name. The ruff ban on `astrometricslib.drivers` and the contract "astrometricslib drivers never import pipelines" both name the old package and need rewriting. `_internal.drivers` is then the third meaning of "drivers": public tools, the Siril and catalog interfaces, and today's folder. **Decided: the public `drivers` takes the name, and today's contents move to `_internal.drivers`.** The "8 public names" above are now 10.
- **F2. `CaptureSetting` cannot simply go private.** Group 7 is private and `reports` does not list it, but 21 `wayfindinglib` files use it. It is a field type in 5 `wayfindinglib` model files (`statements.py`, `tonight_ranking.py`, `target_report.py`, `capture_summary.py` and `observation_session.py`), a dictionary key, and something `efficiency.py` builds. See O5. **Decided: `CaptureSetting` goes in `reports`.**
- **F3. `reports.QualityMeasurement` has private field types, and tests build these rows.** `QualitySubject` and `MeasurementOrigin` are not in the `reports` list, yet a `QualityMeasurement` cannot be built without them. No `wayfindinglib` code outside its tests imports them; it reads `row.subject` as an attribute. But 13 `wayfindinglib` test files build rows with `QualitySubject`, 11 with `MeasurementOrigin` and 7 with `QualityQueryResult` (the file `analytics/test/frame_measurement_helpers.py` also reaches past the root for them). "Public names only" needed a ruling for tests. Decided: tests of either library may import `_internal`, while code outside `astrometricslib` may not. So the two names stay in `_internal.quality`. Section 5.4 counts the test files.
- **F4. `MovingObjectConfig` is an input, not a result.** `processing.process_target(asteroids={"moving_object_config": ...})` takes one, and the validation register (`validation/asteroid_detection.md`, lines 36, 253 and 291) tells a reviewer to import it. A caller cannot pass what it cannot import. Under the plain-dictionaries rule it is suggested private (to confirm), and the register entry then needs the dictionary form. See O6. **Decided: private, passed as a dictionary.**
- **F5. Private driver types sit on the public constructor.** `Astrometrics(config, catalog_access, *, plate_solve_driver=, stacking_driver=, simbad_driver=, gaia_xp_driver=, reddening_driver=)` names six types that go private. No outside code passes them (the backend calls `Astrometrics(self.config_service)` and `Wayfinder` calls `Astrometrics(self.config)`). The library's own tests do. The public constructor should keep only `config`, with a private way in for those tests. **Decided: `Astrometrics(...)` loses its driver arguments, and tests set drivers through `_internal`.**
- **F6. Result models have no documented home.** Group 5 and 6 models become private, but a script writer still has to learn the fields of a `StackResult`. Today they are on the API page through `automodapi`. After the move, a return annotation points at an undocumented name. This is a documentation question and does not reopen the decision. Suggestion: an API page section that documents what each call returns, built from the `_internal` models, with no import path shown.
- **F7. Two scans of the root name list read `__all__`.** `backend/services/infrastructure/agent_code_policy.py` allows AI-written code to import only the names in `__all__`, and it refuses any import of a submodule. With 17 root names, `astrometricslib.exceptions` and the rest would be refused. `backend/services/infrastructure/api_doc_generator.py` looks up fixed lists of root names with `getattr` (`classify_and_sort_fits_files`, `derive_target_sessions`, `parse_coordinate_string`, `FitsHeaderEntry`, `PhotometryResult`, `SpectroscopyResult`, `AsteroidDetectionCandidate`, `AnalysisResult`, `BatchRunSummary`, `ProcessingJob`, and others), and quietly skips a name that has gone. Both need updating in the same change.

## 4. The inventory

One row for each of the 208 names, in the 15 logical groups of the review.

### Group 1 Entry and setup (6)

| Name | Kind | Backend | MCP | Wayfinding | Notebooks and how-to | Library scripts | Tests | Destination | Replacement for outside readers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `Astrometrics` | class | 5 | 1 | 9 | 15 | 31, 1b | 8a 5w 2o | root | No change. |
| `AppConfiguration` | class | 3 | - | 12 | - | - | 50a 34w 3o | root | No change. |
| `get_configuration` | function | 4 | 2 | 7 | 3 | 9 | 19a 1o | root | No change. |
| `configure_logging` | function | 1 | 3 | 0, 2s | 1 | 19 | 1a | root | No change. |
| `configure_offline_iers` | function | 1 | - | 1 | - | - | 1a | root | No change. |
| `warm_earth_orientation_data` | function | 1 | - | - | - | - | 1a | root | No change. |

### Group 2 Sub-API classes (7)

| Name | Kind | Backend | MCP | Wayfinding | Notebooks and how-to | Library scripts | Tests | Destination | Replacement for outside readers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `TargetCatalog` | class | 2 | - | - | - | - | 3a | root | No change. |
| `StellarCatalog` | class | 1 | - | - | - | - | 5a 1w | root | No change. |
| `ProcessingPipelines` | class | 1 | - | - | - | - | 11a 1o | root | No change. |
| `CalibrationCatalog` | class | 1 | - | - | - | - | 4a 1o | root | No change. |
| `QualityRecords` | class | 1 | - | - | - | - | 8a | root | No change. |
| `Visualization` | class | 1 | - | - | - | - | 2a 1o | root | No change. |
| `Jobs` | class | - | - | - | - | - | 1a | `jobs` | Import from `astrometricslib.jobs`. `astrometrics.jobs` is an instance of it (decided). |

### Group 3 Core objects (4)

| Name | Kind | Backend | MCP | Wayfinding | Notebooks and how-to | Library scripts | Tests | Destination | Replacement for outside readers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `Target` | class | 1 | - | 19 | - | 1, 1b | 62a 15w 4o | root | No change. |
| `FrameRecord` | class | - | - | 1 | - | 1b | 44a 10w 2o | root | No change. |
| `StellarObject` | class | 1 | - | 9 | - | 7, 1b | 68a 5w 2o | root | No change. |
| `FilterType` | enum | 2 | - | 10 | 1 | 2, 1b | 12a 8w | root | No change. |

### Group 4 Errors (20)

| Name | Kind | Backend | MCP | Wayfinding | Notebooks and how-to | Library scripts | Tests | Destination | Replacement for outside readers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `AstrometricsError` | class | 10 | 1 | 17 | - | 5, 1b | 2a | `exceptions` | Import from `astrometricslib.exceptions`. |
| `NotFoundError` | class | 7 | 2 | 16 | - | - | 15a 13w 12o | `exceptions` | Import from `astrometricslib.exceptions`. |
| `InvalidArgumentError` | class | 9 | 1 | 30, 1s | - | 3 | 42a 22w 11o | `exceptions` | Import from `astrometricslib.exceptions`. |
| `ProcessingError` | class | 1 | - | 2 | - | 2 | 13a 2w 2o | `exceptions` | Import from `astrometricslib.exceptions`. |
| `ConflictError` | class | 3 | - | 10, 1s | - | - | 7a 10w 6o | `exceptions` | Import from `astrometricslib.exceptions`. |
| `ConfigurationError` | class | 4 | - | 17 | - | 1 | 3a 9w 4o | `exceptions` | Import from `astrometricslib.exceptions`. |
| `HardwareError` | class | 1 | - | 5 | - | - | 1a 5w 3o | `exceptions` | Import from `astrometricslib.exceptions`. |
| `StorageError` | class | 1 | - | 1 | - | 1 | 9a 1w 1o | `exceptions` | Import from `astrometricslib.exceptions`. |
| `StorageNotMountedError` | class | 1 | - | - | - | - | 2a 2w | `exceptions` | Import from `astrometricslib.exceptions`. |
| `ExternalServiceError` | class | 3 | 2 | 7, 1s | - | 3 | 11a 5w 2o | `exceptions` | Import from `astrometricslib.exceptions`. |
| `PermissionDeniedError` | class | - | 2 | 1 | - | - | 1a 3w 4o | `exceptions` | Import from `astrometricslib.exceptions`. |
| `DeviceInUseError` | class | - | - | - | - | - | - | `exceptions` | No outside reader yet. |
| `PlateSolveFailedError` | class | - | - | 1 | - | - | 2a 1w | `exceptions` | Import from `astrometricslib.exceptions`. |
| `ErrorInfo` | class | 2 | 3 | - | - | 1b | 1a | `exceptions` | Import from `astrometricslib.exceptions`. (decided, O1/O2) |
| `error_from_info` | function | - | 1 | - | - | - | 1a | `exceptions` | Import from `astrometricslib.exceptions`. (decided, O1/O2) |
| `to_error_info` | function | 2 | 2 | - | - | - | 1a | `exceptions` | Import from `astrometricslib.exceptions`. (decided, O1/O2) |
| `RPC_CODES` | constant | - | - | - | - | - | 1a | `_internal.errors` | None. Private (decided); no outside reader. |
| `DATA_ERRORS` | constant | - | - | 1 | - | 3, 1b | - | `_internal.errors` | `wayfindinglib` copy (decided). |
| `FITS_READ_ERRORS` | constant | 2 | - | 2 | - | 4 | 1a | `exceptions` | Import from `astrometricslib.exceptions`. (decided, O1/O2) |
| `ONLINE_QUERY_ERRORS` | constant | - | - | 3 | - | - | - | `_internal.errors` | `wayfindinglib` copy (decided). |

### Group 5 Processing results (23)

| Name | Kind | Backend | MCP | Wayfinding | Notebooks and how-to | Library scripts | Tests | Destination | Replacement for outside readers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `StackSettings` | class | - | - | - | - | - | 4a | `_internal.drivers` | None. No outside reader. |
| `MovingObjectConfig` | class | - | - | - | - | 1 | 9a | `_internal.results` | A plain dictionary; `process_target` already takes one (decided). The register needs the dictionary form (O6). |
| `StackResult` | class | - | - | - | - | - | - | `_internal.results` | None. No outside reader. |
| `StackRunResult` | class | - | - | - | - | - | 4a | `_internal.drivers` | None. No outside reader. |
| `SubsetStackResult` | class | - | - | - | - | - | 2a | `_internal.results` | None. No outside reader. |
| `ProcessTargetResult` | class | - | - | - | - | - | - | `_internal.results` | None. No outside reader. |
| `PreviewRemakeResult` | class | - | - | - | - | 1b | 1o | `_internal.results` | Tests only (fit issue F3). |
| `PhotometryResult` | class | - | - | - | - | 1, 1b | 31a 2w | `_internal.results` | Tests only (fit issue F3). |
| `SpectroscopyResult` | class | - | - | - | - | 1, 1b | 25a 2w 1o | `_internal.results` | Tests only (fit issue F3). |
| `AnalysisResult` | class | - | - | - | - | 1b | - | `_internal.results` | Generated types only (Section 6). |
| `VariableCandidate` | class | - | - | - | - | 1b | 2a | `_internal.results` | Generated types only (Section 6). |
| `AsteroidDetectionCandidate` | class | - | - | - | - | 1, 1b | 7a | `_internal.results` | Generated types only (Section 6). |
| `BatchRunSummary` | class | - | - | - | - | 1 | 2a 1o | `_internal.results` | Tests only (fit issue F3). |
| `SpectroscopyCalibrationResult` | class | - | - | - | - | - | - | `_internal.results` | None. No outside reader. |
| `CalibrationLineFit` | class | - | - | - | - | - | - | `_internal.results` | None. No outside reader. |
| `PointSource` | class | - | - | - | - | - | - | `_internal.results` | None. No outside reader. |
| `PointSourceDetection` | class | - | - | - | - | - | - | `_internal.results` | None. No outside reader. |
| `PlotData` | class | - | - | - | - | 1b | - | `_internal.results` | Generated types only (Section 6). |
| `NoiseGrowthResult` | class | - | - | - | - | - | 1a | `_internal.results` | None. No outside reader. |
| `NightContribution` | class | - | - | - | - | - | - | `_internal.results` | None. No outside reader. |
| `NightContributionResult` | class | - | - | - | - | - | 1a | `_internal.results` | None. No outside reader. |
| `OddEvenDifferenceNoiseResult` | class | - | - | - | - | - | 1a | `_internal.results` | None. No outside reader. |
| `RestoreReport` | class | - | - | - | - | - | 1a 1w | `_internal.results` | Tests only (fit issue F3). |

### Group 6 Catalog results (18)

| Name | Kind | Backend | MCP | Wayfinding | Notebooks and how-to | Library scripts | Tests | Destination | Replacement for outside readers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `TargetQueryResult` | class | - | - | - | - | - | 1w 1o | `_internal.catalog` | Tests only (fit issue F3). |
| `StarQueryResult` | class | 1 | - | - | - | - | 1a 2o | `_internal.catalog` | Backend: remove the return annotation (S1). |
| `CalibrationQueryResult` | class | - | - | - | - | 1b | 2w | `_internal.catalog` | Tests only (fit issue F3). |
| `OverlayStar` | class | - | - | - | - | 1b | 1o | `_internal.catalog` | Tests only (fit issue F3). |
| `ReindexReport` | class | 1 | - | - | - | - | 1o | `_internal.catalog` | Backend: remove the return annotation (S1). |
| `TargetReindexChange` | class | - | - | - | - | - | - | `_internal.catalog` | None. No outside reader. |
| `TargetStarCount` | class | - | - | - | - | 1b | 1o | `_internal.catalog` | Tests only (fit issue F3). |
| `TargetFilesResponse` | class | - | - | - | - | 1b | - | `_internal.catalog` | Generated types only (Section 6). |
| `FileItem` | class | - | - | - | - | 1b | - | `_internal.catalog` | Generated types only (Section 6). |
| `GroupedFrameStat` | class | - | - | - | - | 1b | - | `_internal.catalog` | Generated types only (Section 6). |
| `TargetSessionContribution` | class | - | - | - | - | 1b | - | `_internal.catalog` | Generated types only (Section 6). |
| `FieldCenter` | class | - | - | - | - | - | - | `_internal.catalog` | None. No outside reader. |
| `ImagedFieldCenters` | class | - | - | - | - | - | - | `_internal.catalog` | None. No outside reader. |
| `ImageDeletionResult` | class | - | - | - | - | - | - | `_internal.catalog` | None. No outside reader. |
| `FailedDeletion` | class | - | - | - | - | - | - | `_internal.catalog` | None. No outside reader. |
| `ViewableImage` | class | - | - | - | - | - | 1a | `_internal.catalog` | None. No outside reader. |
| `RenderedImage` | class | 1 | - | - | - | 1b | - | `_internal.catalog` | Backend: remove the annotation (S1). |
| `FitsHeaderEntry` | class | - | - | - | - | 1b | - | `_internal.catalog` | Generated types only (Section 6). |

### Group 7 Quality records (20)

| Name | Kind | Backend | MCP | Wayfinding | Notebooks and how-to | Library scripts | Tests | Destination | Replacement for outside readers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `QualityMeasurement` | class | 1 | - | 9 | - | 1, 1b | 24a 14w 1o | `reports` | Import from `astrometricslib.reports`. |
| `QualitySubject` | class | - | - | - | - | 1, 1b | 11a 14w 1o | `_internal.quality` | No code outside reads the name; `row.subject` is read as an attribute. Tests build rows with it (fit issue F3). |
| `SubjectLevel` | enum | - | - | 8 | - | 1, 1b | 21a 11w 1o | `reports` | Import from `astrometricslib.reports`. |
| `MeasurementOrigin` | enum | - | - | - | - | 1, 1b | 12a 12w 1o | `_internal.quality` | Tests only build rows with it (fit issue F3). |
| `MeasurementReference` | class | - | - | 17 | - | 1b | 1a 9w | `reports` | Import from `astrometricslib.reports`. |
| `MeasurementStatus` | enum | - | - | 5 | - | 1, 1b | 37a 12w 2o | `reports` | Import from `astrometricslib.reports`. |
| `StarResultReference` | class | - | - | 1 | - | 1b | 1a | `reports` | Import from `astrometricslib.reports`. |
| `QualityQueryResult` | class | - | - | - | - | - | 7w 2o | `_internal.quality` | Return of `quality.query`. Tests only build fakes with it (fit issue F3). |
| `QualityRowFile` | class | - | - | - | - | 1b | 1o | `_internal.quality` | Tests only (fit issue F3). |
| `QualityRowResolution` | class | - | - | - | - | 1b | 1o | `_internal.quality` | Tests only (fit issue F3). |
| `CaptureSetting` | class | - | - | 21 | - | 1b | 5a 19w | `reports` | Import from `astrometricslib.reports`. (decided, O5) |
| `QualityLimit` | class | - | - | 11 | - | 1b | 4a 5w | `reports` | Import from `astrometricslib.reports`. |
| `LimitTier` | enum | - | - | 5 | - | 1, 1b | 10a 6w | `reports` | Import from `astrometricslib.reports`. |
| `MetricSpec` | class | - | - | - | - | 1, 1b | 3a 1w | `_internal.quality` | Tests only. |
| `MetricSummary` | class | - | - | - | - | 1b | 1a | `_internal.quality` | Generated types only (Section 6). |
| `MetricPurpose` | enum | - | - | - | - | 1b | 1a 2w | `_internal.quality` | Tests only. |
| `Checkpoint` | enum | - | - | - | - | 1b | 1a | `_internal.quality` | Generated types only (Section 6). |
| `Direction` | enum | - | - | - | - | 1b | 1a | `_internal.quality` | Generated types only (Section 6). |
| `UncertaintyKind` | enum | - | - | - | - | 1b | 1a | `_internal.quality` | Generated types only (Section 6). |
| `FrameMeasurementSummary` | class | - | - | - | - | - | 1w | `_internal.quality` | Tests only. |

### Group 8 Quality reports (11)

| Name | Kind | Backend | MCP | Wayfinding | Notebooks and how-to | Library scripts | Tests | Destination | Replacement for outside readers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `InputQualityReport` | class | - | - | - | - | - | - | `reports` | No outside reader yet. |
| `RawFrameCheckReport` | class | - | - | - | - | - | - | `reports` | No outside reader yet. |
| `SpectralFrameCheckReport` | class | - | - | - | - | - | - | `reports` | No outside reader yet. |
| `StackQualityReport` | class | - | - | - | - | 1b | 1o | `reports` | Import from `astrometricslib.reports`. |
| `StackSummary` | class | - | - | - | - | 1b | 1o | `reports` | Import from `astrometricslib.reports`. |
| `QuarantinePreview` | class | - | - | - | - | - | - | `reports` | No outside reader yet. |
| `SetAsideFrame` | class | - | - | - | - | - | 1a 1w | `reports` | Import from `astrometricslib.reports`. |
| `SetAsideFrameRecord` | class | - | - | - | - | 1b | 1o | `reports` | Import from `astrometricslib.reports`. |
| `ExposureGroupSummary` | class | - | - | - | - | 1b | 3a | `reports` | No outside reader yet. |
| `AstrometryPipelineQualityMetrics` | class | - | - | - | - | 1b | 4a | `reports` | No outside reader yet. |
| `AstrometryQualitySummary` | class | - | - | - | - | 1b | 5a | `reports` | No outside reader yet. |

### Group 9 Frame helpers (14)

| Name | Kind | Backend | MCP | Wayfinding | Notebooks and how-to | Library scripts | Tests | Destination | Replacement for outside readers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `frame_is_spectral` | function | 1 | - | 3 | - | 1 | 1a 1w | `_internal.frames` | Value on a row: `FrameRecord.is_spectral`, `CaptureSetting.is_spectral` (S2). |
| `classify_and_sort_fits_files` | function | - | - | 1 | - | - | 1a | `_internal.frames` | New call N4. |
| `derive_target_sessions` | function | - | - | 1 | - | - | 1a | `_internal.frames` | New call N5. |
| `FrameSelection` | class | - | - | 2 | - | - | 1a 1w | `_internal.frames` | New call N1 (decided). |
| `select_library_frames` | function | - | - | 1 | - | - | 1a | `_internal.frames` | New call N1 (decided). |
| `observing_night_id` | function | 3 | - | 9 | - | - | 2a 5w | `reports` | Import from `astrometricslib.reports`. (decided, O7) |
| `target_object_category` | function | - | - | 1 | - | 1 | 1a | `_internal.frames` | Value on a row: `Target.object_category` (S3). |
| `preview_path_for` | function | 1 | - | - | - | 1 | 3a | `_internal.frames` | Value on a row: the target's stacking record gives the preview path (S4). |
| `parse_coordinate_string` | function | 2 | - | 11 | - | - | 1a 1w | `_internal.frames` | Value on a row for targets; a small copy for typed text (S5). |
| `parse_iso_time` | function | - | - | 1 | - | - | 1a | `_internal.frames` | New call N1 (decided). |
| `has_catalog_magnitude` | function | - | - | 1 | - | - | 1a | `_internal.frames` | `wayfindinglib` copy (S6). |
| `frame_subject_id` | function | - | - | 4 | - | 1 | 6a 6w | `_internal.frames` | Value on a row: `FrameRecord.subject_id` (S7). |
| `AppliedCameraProfile` | class | - | - | - | - | 1b | - | `_internal.frames` | No outside code reader. Generated types only (Section 6). |
| `CameraProfile` | class | - | - | - | - | - | - | `_internal.frames` | Returned by `resolve_camera_profile`; added by the merge. |
| `TargetSession` | class | - | - | - | - | - | - | `_internal.frames` | Returned by `targets.sessions`; added by the merge. |
| `resolve_camera_profile` | function | - | - | 6 | - | 1 | 7a 1w | `_internal.frames` | New public call N6 (decided; name open). See O4. |

### Group 10 Stacking numbers (18)

| Name | Kind | Backend | MCP | Wayfinding | Notebooks and how-to | Library scripts | Tests | Destination | Replacement for outside readers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `AMBIGUOUS_RMS_GAP` | constant | - | - | 1 | - | 1 | 7a | `_internal.stacking` | Value on a row (S8). Decided: science limits become values on returned rows. |
| `NO_GOOD_MATCH_RMS` | constant | - | - | 1 | - | - | 6a | `_internal.stacking` | Value on a row (S8). Decided: science limits become values on returned rows. |
| `DEFAULT_DARK_TEMPERATURE_TOLERANCE_C` | constant | - | - | 5 | - | - | 1a | `_internal.stacking` | Value on a row (S9). Decided: science limits become values on returned rows. |
| `EARLIER_NIGHTS_VARIANT` | constant | - | - | 2 | - | 1 | 2a 1w | open; suggest `reports` | Open. A variant label, not a limit, so the science-limits decision does not cover it. Suggested public constant (S10). See O3. |
| `SATURATED_BLOB_MINIMUM_PIXELS` | constant | - | - | 3 | - | - | 1a | `_internal.stacking` | Value on a row (S11). Decided: science limits become values on returned rows. |
| `SATURATED_FRAME_FRACTION` | constant | - | - | 2 | - | - | - | `_internal.stacking` | Value on a row (S11). Decided: science limits become values on returned rows. |
| `UNKNOWN_CAPTURE_SETTING_CAMERA` | constant | - | - | 2 | - | - | - | open; suggest `_internal.quality`; method on `CaptureSetting` | Method on `CaptureSetting` (S12). See O3. |
| `PIXEL_BLUR_FWHM_SQUARED_FACTOR` | constant | - | - | 1 | - | - | 1a 1w | open; suggest `_internal.quality`; `wayfindinglib` copy | `wayfindinglib` copy (S13). See O3. |
| `MINIMUM_PENALTY_TO_WEIGHT` | constant | - | - | - | - | - | - | `_internal.stacking` | None. No outside reader. |
| `NOISE_WEIGHT_MODE` | constant | - | - | 1 | - | - | - | `_internal.stacking` | New call N3 (decided). |
| `aperture_identity_from_number` | function | - | - | 1 | - | - | 3a | open; suggest `_internal.stacking`; compare numbers | Compare the stored numbers; remove (S14). See O3. |
| `aperture_identity_to_number` | function | - | - | - | - | - | 3a 2w | `_internal.stacking` | Tests only; follows S14. |
| `load_odd_even_aperture_record` | function | - | - | 1 | - | - | 2a | open; suggest `_internal.stacking`; new call | New call N7. See O3. |
| `group_frame_weights` | function | - | - | 1 | - | - | 1a | `_internal.stacking` | New call N3 (decided). |
| `wfwhm_weights` | function | - | - | 1 | - | - | 2a | `_internal.stacking` | New call N3 (decided). |
| `choose_stack_weight` | function | - | - | 1 | - | - | - | `_internal.stacking` | New call N3 (decided). |
| `StackWeightPrediction` | class | - | - | - | - | - | - | `_internal.stacking` | Returned by `processing.predict_stack_weights`; added by the merge. |
| `equal_weight_noise_penalty` | function | - | - | - | - | - | - | `_internal.stacking` | None. No outside reader. |
| `fold_exposure_lengths` | function | - | - | 3 | - | - | 1a 1w | open; suggest `_internal.stacking`; new call | Part of new call N3 (S15). See O3. |

### Group 11 Jobs and logging (16)

| Name | Kind | Backend | MCP | Wayfinding | Notebooks and how-to | Library scripts | Tests | Destination | Replacement for outside readers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `ProcessingJob` | class | 1 | - | 1 | - | 1b | 3a 1o | `jobs` | Import from `astrometricslib.jobs`. |
| `JobStore` | class | 2 | - | 1 | - | - | 8a 1w 1o | `jobs` | Import from `astrometricslib.jobs`. |
| `DbLogHandler` | class | 1 | - | - | - | - | 1a 1w | `jobs` | Import from `astrometricslib.jobs`. |
| `background_job` | function | - | - | 4 | - | - | 1o | `jobs` | Import from `astrometricslib.jobs`. |
| `capture_job_logs` | function | 3 | - | 1 | - | - | 1a | `jobs` | Import from `astrometricslib.jobs`. |
| `close_interrupted_jobs` | function | 1 | 1 | - | - | - | - | `jobs` | Import from `astrometricslib.jobs`. |
| `get_current_job` | function | - | - | 6 | - | - | - | `jobs` | Import from `astrometricslib.jobs`. |
| `registered_job` | function | 1 | - | 4 | - | - | 1a 1o | `jobs` | Import from `astrometricslib.jobs`. |
| `run_as_background_job` | function | 2 | 1 | - | - | - | 1a | `jobs` | Import from `astrometricslib.jobs`. |
| `get_log_context` | function | - | - | - | - | - | 1a | `_internal.jobs` | None. No outside reader. |
| `log_context` | function | 2 | 1 | - | - | - | 1a | `jobs` | Import from `astrometricslib.jobs`. |
| `new_request_id` | function | 2 | 2 | - | - | - | 1a | `jobs` | Import from `astrometricslib.jobs`. |
| `JobLogLine` | class | - | - | - | - | - | - | `_internal.jobs` | None. No outside reader. |
| `JobQueryResult` | class | - | - | - | - | - | - | `_internal.jobs` | None. No outside reader. |
| `JobSummary` | class | - | - | - | - | - | - | `_internal.jobs` | None. No outside reader. |
| `LineageRun` | class | - | - | - | - | - | - | `_internal.jobs` | None. No outside reader. |

### Group 12 Storage (13)

| Name | Kind | Backend | MCP | Wayfinding | Notebooks and how-to | Library scripts | Tests | Destination | Replacement for outside readers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `AbstractButler` | class | - | - | 1 | - | - | 1w | `drivers` | Import from `astrometricslib.drivers`. |
| `Butler` | class | - | - | 1 | - | - | 2a | `drivers` | Import from `astrometricslib.drivers`. |
| `DatasetSpec` | class | - | - | 1 | - | - | 2a | `drivers` | Import from `astrometricslib.drivers`. |
| `NumpyEncoder` | class | - | - | - | - | - | - | `_internal.storage` | None. No outside reader. |
| `connect_db` | function | - | - | 4 | - | - | - | `drivers` | Import from `astrometricslib.drivers`. |
| `safe_json_dumps` | function | - | - | 1 | - | - | 1a | `_internal.storage` | `wayfindinglib` copy (decided). |
| `require_mounted_storage` | function | 1 | - | 2 | - | - | 1a | `drivers` | Import from `astrometricslib.drivers`. |
| `acquire_resource_slot` | function | - | - | - | - | - | 2a | `_internal.storage` | None. No outside reader. |
| `file_lock` | function | - | - | 1 | - | - | - | `drivers` | Import from `astrometricslib.drivers`. |
| `is_path_inside` | function | - | 1 | - | - | - | 1o | `drivers` | Import from `astrometricslib.drivers`. (decided, O8) |
| `locks_directory` | function | - | - | 1 | - | 1 | 3a | `drivers` | Import from `astrometricslib.drivers`. |
| `resolve_mounted_path` | function | 1 | 1 | - | - | - | - | `drivers` | Import from `astrometricslib.drivers`. (decided, O8) |
| `scratch_directory` | function | 1 | - | 1 | - | 4 | 1a | `drivers` | Import from `astrometricslib.drivers`. |

### Group 13 Argument checks (5)

| Name | Kind | Backend | MCP | Wayfinding | Notebooks and how-to | Library scripts | Tests | Destination | Replacement for outside readers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `check_choice` | function | - | - | 1 | - | - | - | `_internal.arguments` | `wayfindinglib` copy (decided). |
| `check_include` | function | - | - | 1 | - | - | - | `_internal.arguments` | `wayfindinglib` copy (decided). |
| `reject_unused_arguments` | function | - | - | 1 | - | - | - | `_internal.arguments` | `wayfindinglib` copy (decided). |
| `resolve_target` | function | - | - | 1 | - | - | 1a | `_internal.arguments` | New call N2 (decided). |
| `to_epoch_seconds` | function | - | - | 1 | - | - | - | `_internal.arguments` | `wayfindinglib` copy (decided). |

### Group 14 Drivers (9)

| Name | Kind | Backend | MCP | Wayfinding | Notebooks and how-to | Library scripts | Tests | Destination | Replacement for outside readers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `AbstractCatalogAccess` | class | - | - | - | - | - | 3a | `_internal.drivers` | None outside. See fit issue F5. |
| `CatalogAccess` | class | - | - | - | - | - | 17a 1w | `_internal.drivers` | Tests only. `wayfindinglib/test/test_sky_sources.py` builds a `StellarCatalog` by hand (fit issue F3). |
| `GaiaXpDriver` | class | - | - | - | - | 1 | 2a | `_internal.drivers` | None outside. See fit issue F5. |
| `PlateSolveDriver` | class | - | - | - | - | - | 3a | `_internal.drivers` | None outside. See fit issue F5. |
| `PlateSolveHeader` | class | - | - | - | - | - | 1a | `_internal.drivers` | None. No outside reader. |
| `ReddeningDriver` | class | - | - | - | - | - | 2a | `_internal.drivers` | None outside. See fit issue F5. |
| `ReddeningEstimate` | class | - | - | - | - | - | 3a | `_internal.drivers` | None. No outside reader. |
| `SimbadDriver` | class | - | - | - | - | 1 | 5a | `_internal.drivers` | None outside. See fit issue F5. |
| `StackingDriver` | class | - | - | - | - | - | 4a | `_internal.drivers` | None outside. See fit issue F5. |

### Group 15 Provenance (24)

| Name | Kind | Backend | MCP | Wayfinding | Notebooks and how-to | Library scripts | Tests | Destination | Replacement for outside readers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `Activity` | class | - | - | - | - | - | 5a | `_internal.provenance` | None. No outside reader. |
| `ActivityDescription` | class | - | - | - | - | - | 3a | `_internal.provenance` | None. No outside reader. |
| `Agent` | class | - | - | - | - | - | 4a | `_internal.provenance` | None. No outside reader. |
| `AgentType` | enum | - | - | - | - | - | 2a | `_internal.provenance` | None. No outside reader. |
| `Collection` | class | - | - | - | - | - | 2a | `_internal.provenance` | None. No outside reader. |
| `ConfigFile` | class | - | - | - | - | - | 2a | `_internal.provenance` | None. No outside reader. |
| `ConfigFileDescription` | class | - | - | - | - | - | 2a | `_internal.provenance` | None. No outside reader. |
| `DatasetDescription` | class | - | - | - | - | - | 2a | `_internal.provenance` | None. No outside reader. |
| `DatasetEntity` | class | - | - | - | - | - | 3a | `_internal.provenance` | None. No outside reader. |
| `Entity` | class | - | - | - | - | - | 3a | `_internal.provenance` | None. No outside reader. |
| `EntityDescription` | class | - | - | - | - | - | 2a | `_internal.provenance` | None. No outside reader. |
| `GenerationDescription` | class | - | - | - | - | - | 2a | `_internal.provenance` | None. No outside reader. |
| `Parameter` | class | - | - | - | - | - | 3a | `_internal.provenance` | None. No outside reader. |
| `ParameterDescription` | class | - | - | - | - | - | 3a | `_internal.provenance` | None. No outside reader. |
| `UsageDescription` | class | - | - | - | - | - | 2a | `_internal.provenance` | None. No outside reader. |
| `Used` | class | - | - | - | - | - | 4a | `_internal.provenance` | None. No outside reader. |
| `ValueDescription` | class | - | - | - | - | - | 2a | `_internal.provenance` | None. No outside reader. |
| `ValueEntity` | class | - | - | - | - | - | 2a | `_internal.provenance` | None. No outside reader. |
| `WasAssociatedWith` | class | - | - | - | - | - | 4a | `_internal.provenance` | None. No outside reader. |
| `WasAttributedTo` | class | - | - | - | - | - | 2a | `_internal.provenance` | None. No outside reader. |
| `WasConfiguredBy` | class | - | - | - | - | - | 2a | `_internal.provenance` | None. No outside reader. |
| `WasGeneratedBy` | class | - | - | - | - | - | 4a | `_internal.provenance` | None. No outside reader. |
| `ProvenanceStore` | class | - | - | - | - | - | 5a | `_internal.provenance` | None. No outside reader. |
| `export_target_lineage_as_prov_xml` | function | - | - | - | - | - | 1a | dropped | None. Lineage comes with what results return (decided). Only the library's own tests read it. |

## 5. What each outside caller really does

46 names going internal or open have a reader in the backend, the MCP servers, `wayfindinglib` or the notebooks. No notebook or how-to page reads any of them. I read each call site. The verdict column says what the caller needs: a new public call (N), a value on a row the caller already holds (S), a copy, removal, or a user decision (O).

### 5.1 Settled by the user

| Names | What the caller does | Needs |
| :--- | :--- | :--- |
| `FrameSelection`, `parse_iso_time`, `select_library_frames` | `control.history.frame_guiding` turns its arguments into a selection, then `frame_guiding.py` picks lights from `target.frames`. It also reads `selection.has_bounds` to pick the first or the last `limit` frames. | New call N1 |
| `resolve_target` | `planning.py:357` turns an id or a `Target` into a `Target`, once, in `_target`. | New call N2 |
| `choose_stack_weight`, `wfwhm_weights`, `group_frame_weights`, `NOISE_WEIGHT_MODE` | `noise_growth.py` predicts what the stacker would do, so the forecast uses the same weights. | New call N3 |
| `check_choice`, `check_include`, `reject_unused_arguments` | `planning.py` checks its own arguments. | Copy |
| `to_epoch_seconds` | `visibility_report.py:119` turns a time argument into seconds. | Copy |
| `safe_json_dumps` | `butler.py` writes `model_dump(mode="json")` to the database, four times. The library's version adds `NumpyEncoder`. Since the input is already plain JSON values, `json.dumps` may do; the copy should be checked for this before it brings the encoder along. | Copy, or plain `json.dumps` |

### 5.2 Read from the call site, with a suggestion

| Ref | Names | What the caller does | Really needs |
| :--- | :--- | :--- | :--- |
| S1 | `StarQueryResult`, `ReindexReport`, `RenderedImage` | The backend uses them only in annotations and docstrings (`stellar_service.py`, `target_service.py`, `image_service.py`). `RenderedImage` is only `model_dump`ed. | Removal: drop the annotation. |
| S2 | `frame_is_spectral` | The backend picks `kind="spectral"` for a stack when all frames are spectral. `wayfindinglib` asks about a frame, or about a capture setting through a made-up `{"filter": name}` dictionary (`target_report_tasks.py`, `science_goal.py`). | Value on a row: `FrameRecord.is_spectral`, and `CaptureSetting.is_spectral` if O5 makes it public. Four call sites hold only a filter name. |
| S3 | `target_object_category` | `depth_goal.py:145` sorts a target from its SIMBAD type and id. | Value on a row: `Target.object_category`. `depth_kind_of_target` takes the two strings today, so its callers pass the target instead. |
| S4 | `preview_path_for` | `target_service.py:238` finds the stack's JPEG. | Value on a row: the target's stacking record gives the path. |
| S5 | `parse_coordinate_string` | 11 `wayfindinglib` files and 2 backend files. Nine `wayfindinglib` files parse `target.ra` and `target.dec` and nothing else. `mosaic_tasks.py`, `frame_pointing.py` and both backend files parse typed or header text. | Value on a row for the first kind: `Target.ra_deg`, `Target.dec_deg`. A small `wayfindinglib` function for the second (the library's own is a thin check around `astropy` `Angle`), offered to the backend through `SkyPosition`, which it already imports. |
| S6 | `has_catalog_magnitude` | `SkySource.has_catalog_magnitude` applies the rule to a magnitude. Not every `SkySource` comes from a `StellarObject`, so `StellarObject.has_catalog_magnitude` does not cover it. | Copy. It carries one limit, `BRIGHTEST_CATALOG_MAGNITUDE`. A shared test can pin the two together. |
| S7 | `frame_subject_id` | 4 files join quality rows to frames. All hold the `FrameRecord` and the target id. | Value on a row: `FrameRecord.subject_id`, filled when the target is read. `FrameRecord` does not know its target today, so check this before committing to it. |
| S8 | `NO_GOOD_MATCH_RMS`, `AMBIGUOUS_RMS_GAP` | `star_result_rules.py` only quotes them in the text of a statement, next to `is_poor_match` and `is_class_ambiguous`. | Value on a row. This matches the agreed rule that a label is reported with the limit that set it. Decided: science limits become values on returned rows. |
| S9 | `DEFAULT_DARK_TEMPERATURE_TOLERANCE_C` | 5 files. `report_rules.py` and `calibration_advisory_tasks.py` judge whether a dark would match; the capture pipeline judges and quotes it. They must agree with the library's matcher, so a copy can drift. | Value on a row: `calibration.query` reports the tolerance it matched with. The capture pipeline holds no such row today, so it would make that query. Decided: science limits become values on returned rows. |
| S10 | `EARLIER_NIGHTS_VARIANT` | `target_report_inputs.py` passes it to `quality.query(variant=...)`; `forecast.py` compares it. It is a value of a public argument. | Public constant in `reports`, or a documented literal, `"earlier-nights"`. Not covered by the science-limits decision: still open. |
| S11 | `SATURATED_BLOB_MINIMUM_PIXELS`, `SATURATED_FRAME_FRACTION` | The capture analysis in `session_analysis/capture/` repeats the stacker's saturation rule per exposure, from stored counts, and quotes both limits. | Value on a row: expose them as limits of the metric. Today `MetricSpec` holds no limit field, so this adds one, or the stored row gives its `QualityLimit`. Decided: science limits become values on returned rows. |
| S12 | `UNKNOWN_CAPTURE_SETTING_CAMERA` | `efficiency.py` builds a stand-in `CaptureSetting`; `target_report_tasks.py` tests for it. | Method on `CaptureSetting`, once O5 settles: `CaptureSetting.unknown()`, `.is_unknown`. |
| S13 | `PIXEL_BLUR_FWHM_SQUARED_FACTOR` | `efficiency.py:502`. It is a mathematical value, not a tuned one: $2 \ln 2 / 3$. | Copy. |
| S14 | `aperture_identity_from_number` | `forecast.py` turns the stored number into text and compares two such texts (lines 1526 and 1690) to ask "same apertures?". The library's docstring says two rows match exactly when the text matches. | Removal: compare the numbers. Check first that nothing shows the text to a user, and that the half-record comparison at line 1526 has a number to compare. |
| S15 | `fold_exposure_lengths` | `forecast.py`, `noise_growth.py` and `target_report_tasks.py` ask how the stacker would split frames into exposure groups. | Part of N3. |

### 5.3 The open rows, with the answers of 2026-10-31

| Ref | Names | Answer | Why |
| :--- | :--- | :--- | :--- |
| O1 | `ErrorInfo`, `to_error_info`, `error_from_info`, `RPC_CODES` | Decided: the first three in `exceptions`. `RPC_CODES` private. | The backend and MCP servers turn an error into a reply, and the MCP backend server turns a reply back into an error. Copies would split the error contract. Nobody outside reads `RPC_CODES`. |
| O2 | `FITS_READ_ERRORS`, `DATA_ERRORS`, `ONLINE_QUERY_ERRORS` | Decided: `FITS_READ_ERRORS` in `exceptions`. The other two private, with `wayfindinglib` copies. | Each is a tuple of outside error types. Callers mostly catch `FITS_READ_ERRORS` around public library calls (`targets.read_frame_header`, `calibration.reindex_frames`), so it describes the library's behavior. Better still, those calls would raise a library error and the tuple could go; that is more work. |
| O3 | Group 10 names, as in S8 to S15 | Decided for the science limits: values on returned rows (S8, S9, S11). The rest keep the suggestions in Section 5.2 and are still open: `EARLIER_NIGHTS_VARIANT` (S10), `UNKNOWN_CAPTURE_SETTING_CAMERA` (S12), `PIXEL_BLUR_FWHM_SQUARED_FACTOR` (S13), `aperture_identity_from_number` (S14), `fold_exposure_lengths` (S15). `load_odd_even_aperture_record` follows N7. | No name in this group has a script or notebook reader. |
| O4 | `resolve_camera_profile` | Decided: a public call (N6). Name and home open. | Eight call sites read aliases, saturation limits with their source, and gains per setting. This is real data `wayfindinglib` needs and cannot copy. It takes `config`, and several callers hold no `Astrometrics` handle, so they need one passed in. The returned `CameraProfile` is not exported at all today, and its shape needs a public home. `validation/wayfinding.md:462` also calls it. |
| O5 | `CaptureSetting` | Decided: `reports`. | See F2. It is a quality row shape in everything but the list. |
| O6 | `MovingObjectConfig` | Decided: private. `process_target` takes a plain dictionary of options. | See F4. The validation register imports the class in three places and needs the dictionary form. |
| O7 | `observing_night_id` | Decided: public function in `reports`. | 3 backend and 9 `wayfindinglib` files, often on raw timestamps from guide logs, so no row holds the value. It names `QualitySubject.observing_night_id`, the join key across the two libraries. A copy that drifted would break every join silently. |
| O8 | `is_path_inside`, `resolve_mounted_path` | Decided: `drivers`. | The MCP sandbox check calls both in one line (`tool_registry.py:333`), and it must know both `/media` and `/run/media` spellings. Three backend calls resolve a path before a library call; if `targets.get_header` and `render_fits` resolved it themselves, the backend's use would go away. |

### 5.4 Tests outside the library

47 test files under `wayfindinglib/`, `backend/tests/`, `mcp_servers/` or the root read 32 names going internal or open. The Tests columns of Section 4 give the counts. The largest uses:

| Name | Test files outside the library |
| :--- | :--- |
| `CaptureSetting` | 19 |
| `QualitySubject` | 15 |
| `MeasurementOrigin` | 13 |
| `QualityQueryResult` | 9 |
| `frame_subject_id` | 6 |
| `observing_night_id` | 5 |
| `SpectroscopyResult` | 3 |
| `CalibrationQueryResult` | 2 |
| `MetricPurpose` | 2 |
| `PhotometryResult` | 2 |

## 6. Direct imports of submodules

These go past the root today. The non-test code outside the library has only one case, and it is excepted.

All 19 `lint-imports` contracts are kept today. The ruff `TID` check passes on `backend`, `mcp_servers`, `build`, `conftest.py` and `documentation`.

**Non-test code outside the library:**

| File | Statements | Rule status |
| :--- | :--- | :--- |
| `build/codegen/generate_types.py` | 26 | Excepted: ruff `banned-api` is ignored for this file. `build` is not a root package of the import contracts. It reads the full model surface to make TypeScript types, so it may stay on `_internal` or the old paths. |

**Tests outside the library:**

| File | Line | Imports | Rule status |
| :--- | :--- | :--- | :--- |
| `conftest.py` | 109 | `astrometricslib.foundation`: `config` | Root test file, not a package |
| `backend/tests/test_rpc_error_handling.py` | 27 | `astrometricslib.foundation.logging`: `ContextFilter` | Inline `# ruff: ignore[banned-api]` |
| `wayfindinglib/test/test_documentation_papers.py` | 20 | `astrometricslib.test.test_architecture_doc_claims`: `IMPL_COMMENT`, `_resolve` | Ruff ignore for `wayfindinglib/**` |
| `wayfindinglib/test/test_documentation_papers.py` | 21 | `astrometricslib.test.test_documentation_rules`: `ARCHITECTURE_WORD_LIMIT`, `IMPLEMENTATION_WORD_LIMIT`, `WAYFINDING_QUALITY_JUDGMENT_WORD_LIMIT`, `assert_names_no_code` and others | Ruff ignore for `wayfindinglib/**` |
| `wayfindinglib/tasks/control_tasks/test/test_equipment_activation.py` | 334 | `astrometricslib.models.camera_profile`: `CameraProfile`, `ProvenancedValue`, `SensorSettingNoise`, `ValueProvenance` | Ruff ignore for `wayfindinglib/**` |
| `wayfindinglib/tasks/planning_tasks/test/test_target_report_tasks.py` | 36 | `astrometricslib.models.catalog_queries`: `CalibrationQueryResult` | Ruff ignore for `wayfindinglib/**` |
| `wayfindinglib/tasks/planning_tasks/test/test_target_report_tasks.py` | 37 | `astrometricslib.models.target`: `FrameRecord` | Ruff ignore for `wayfindinglib/**` |
| `wayfindinglib/analytics/test/test_capture_history.py` | 199 | `astrometricslib.pipelines.shared.quality.metric_registry`: `get_metric_registry` | Ruff ignore for `wayfindinglib/**` |
| `wayfindinglib/analytics/test/frame_measurement_helpers.py` | 19 | `astrometricslib.models.quality.measurement`: `MeasurementOrigin`, `QualitySubject`, `SubjectLevel` | Ruff ignore for `wayfindinglib/**` |
| `wayfindinglib/analytics/result_quality/test/test_noise_growth.py` | 223 | `astrometricslib.pipelines.stacking.post_processing.expected_noise`: `ExposureGroupNoiseFacts`, `FrameNoiseInput`, `effective_aperture_area_px`, `expected_exposure_group_noise` | Ruff ignore for `wayfindinglib/**` |
| `wayfindinglib/analytics/result_quality/test/test_noise_growth.py` | 827 | `astrometricslib.pipelines.stacking.post_processing.expected_noise`: `ExposureGroupNoiseFacts`, `FrameNoiseInput`, `effective_aperture_area_px`, `expected_exposure_group_noise` | Ruff ignore for `wayfindinglib/**` |
| `wayfindinglib/analytics/result_quality/test/test_forecast.py` | 35 | `astrometricslib.models.quality.calibration_identities`: `CalibrationFileIdentity`, `CalibrationIdentities` | Ruff ignore for `wayfindinglib/**` |
| `wayfindinglib/analytics/result_quality/test/test_fixed_pattern_history.py` | 30 | `astrometricslib.models.quality.calibration_identities`: `CalibrationFileIdentity`, `CalibrationIdentities` | Ruff ignore for `wayfindinglib/**` |
| `wayfindinglib/api/test/test_planning.py` | 25 | `astrometricslib.models.catalog_queries`: `CalibrationQueryResult` | Ruff ignore for `wayfindinglib/**` |

**String references that act like imports** (these break without any import error):

- `wayfindinglib/tasks/control_tasks/test/test_remote_transfer_tasks.py` patches `astrometricslib.classify_and_sort_fits_files` (11 places), `astrometricslib.get_configuration`, `astrometricslib.require_mounted_storage`, `astrometricslib.get_current_job` and `astrometricslib.Astrometrics`.
- `wayfindinglib/drivers/test/test_stellarmate_interface.py:373` patches `astrometricslib.foundation.storage.mount.get_configuration`.
- `backend/tests/test_image_processing_service.py` patches `astrometricslib.pipelines.stacking.stage.stack_frames`, `astrometricslib.capture_job_logs` and `astrometricslib.pipelines.stacking.post_processing.stack_preview`.
- `build/documentation/generate_pipeline_review_docs.py` and `audit_pipelines.py` name about 50 module paths as strings. If `_internal` holds only index modules at first (the agreed layout note), these keep working. They need attention only when a module really moves.
- `<!-- impl: astrometricslib.api... -->` comments in the papers and register entries name `api/` paths, and the tests check them.

**Inside the library:** `astrometricslib/scripts/` has 171 direct import statements and `astrometricslib` tests have 1976. Both are inside the library, so the new rule allows them.

## 7. New public calls this requires

| Ref | Call | Replaces | Status | Notes |
| :--- | :--- | :--- | :--- | :--- |
| N1 | `targets.select_frames(target, filter_name=, first_file=, last_file=, since=, until=, limit=)` | `FrameSelection`, `select_library_frames`, `parse_iso_time` | Decided; name agreed | Takes ISO text and raises `InvalidArgumentError`. `limit` moves in because the caller reads `has_bounds` to choose which end to keep. Returns `FrameRecord` rows. |
| N2 | `targets.resolve(target)` | `resolve_target` | Decided; name agreed | An id or a `Target` in, a `Target` out, `NotFoundError` if absent. `targets.get` returns `None` instead, and "one call with parameters" might suggest merging. Its use is one line. |
| N3 | `processing.predict_stack_weights(...)` | `choose_stack_weight`, `wfwhm_weights`, `group_frame_weights`, `NOISE_WEIGHT_MODE`, and by suggestion `fold_exposure_lengths` | Decided; name agreed, shape open | Three questions with different inputs: which mode, the weights within a group, the weights across groups. Under the rules, a parameter may pick what is read only when inputs match, so one call needs a result model holding the mode, the weights, the folded lengths and the reasons, built from one set of frame facts. |
| N4 | `targets.add_frames(paths, target, telescope=)` and `processing.calibration.add_frames(paths, telescope=)` | `classify_and_sort_fits_files` | Open | `remote_transfer_tasks.py` moves downloaded frames into the library and needs the list of new paths. The call today passes `"Calibration"` as a target id. Two clear write verbs fit the rules better than one with a magic id. Note that `calibration.add` was removed on #135 for a different job. |
| N5 | `targets.sessions(target)` | `derive_target_sessions` | Open | `post_session_reconciliation.py` needs each session's `id` and `night_date` for one night. It could also be a value on `Target`, if cheap. |
| N6 | `processing.calibration.camera_profile(camera_name)` | `resolve_camera_profile` | Decided; name and home open | See O4 (decided: a public call). Could sit elsewhere; the profile is not calibration data as such. |
| N7 | A read of the per-aperture noise record, for example `quality.get_aperture_record(path)` | `load_odd_even_aperture_record` | Open | `forecast.py:1883` reads the file whose path a stack run row gives. `load_record` is an injected default, so tests replace it. |

Values to add on rows the caller already holds (no new call, but new public fields): `FrameRecord.is_spectral`, `FrameRecord.subject_id`, `Target.object_category`, `Target.ra_deg`, `Target.dec_deg`, the preview path on the stacking record, the two spectral match limits, the dark temperature tolerance on `calibration.query`, the saturation limits. Each comes from Section 5.2.

`Target.ra_deg` and the rest change `serialize` output and generated TypeScript if they are stored fields. Make them computed properties that are not saved.

## 8. What else must change

**`astrometricslib/api/README.md`:** rewrite "Injecting drivers" and "Where the drivers reach", because swapping drivers goes private. Add the five public groups and say what `_internal` means. `Astrometrics(...)` loses its driver arguments, so the "Injecting drivers" text goes with them.

**API pages:** `documentation/api/astrometricslib.rst` has nine "Names by purpose" lists and an `automodapi` on the root. Replace these with `automodapi` on the root and on `exceptions`, `reports`, `jobs` and `drivers`. #97 planned the page to hold only its title and the directives for two tiers; that item needs the new list. Settle F6 at the same time. `api_doc_generator.py` (F7) produces a second reference.

**Architecture and Implementation papers:** the Architecture paper names no code, so it needs at most one statement of the idea: a small public surface, with private results reached through calls. The Implementation paper changes in Section 1 ("It re-exports the `api/` facade, driver interfaces, job framework and error types") and Section 2 (layer table), Section 4.7 (drivers) and Section 4.9 (jobs). `llms.txt` line 27 still holds true.

**Validation register (repeat steps to rewrite):** entries that tell a reviewer to import or call something going private: `validation/asteroid_detection.md` (`MovingObjectConfig`, now a dictionary), `validation/astrometry.md` (`processing.detect_point_sources`, now private), `validation/stacking.md` and `validation/wayfinding.md` (`processing.stack_subset`, now private), `validation/spectroscopy.md` (`processing.tune_spectroscopy_calibration`, now private), `validation/stacking.md:268` (`exposure_weighting.MINIMUM_PENALTY_TO_WEIGHT` by module path). Two need a note and not a rewrite: `validation/astrometry.md:621` (`PlateSolveFailedError` moves to `exceptions`) and `validation/wayfinding.md:462` (`resolve_camera_profile` becomes a public call). The register's rule is that an entry gives the library calls a reviewer would make, so each needs a public route or an honest note. Because scripts inside `astrometricslib` may use `_internal`, the repeat step for a private method can name the script that calls it.

**`astrometricslib/test/test_public_surface.py`:** `EXPECTED_PUBLIC_NAMES` shrinks to 16. Add one pinned set for each of the four modules. `KEPT_FOR_OUTSIDE_CALLERS` and its comment go. Add a check that nothing outside the library and its tests imports `_internal`. `SUB_API_MEMBERS` gains the new calls. #97 also wants the design rules enforced here.

**`pyproject.toml`:**

- Ruff `banned-api`: add `astrometricslib._internal`. Today's bans name `api`, `models`, `utilities`, `pipelines`, `drivers`, `foundation` and `visualization`, and their messages say "import from the top-level namespace". Lift the ban on `astrometricslib.drivers` for the public module (F1), and rewrite the messages.
- Import contracts: "wayfindinglib uses only the public API", "the MCP servers use only the public API" and "the backend uses only the public API" forbid `astrometricslib.*`, which would now forbid `exceptions`, `reports`, `jobs` and `drivers` too. Each needs those four allowed. #97's `ignore_imports` item already asks whether `**` works with import-linter 2.15; this needs the same check. Add a contract that only `astrometricslib` imports `astrometricslib._internal`, with tests of either library excepted.
- Contract "astrometricslib drivers never import pipelines": point it at the new home.

**`CLAUDE.md`, Section 2:** add the rule that `_internal` is private to `astrometricslib`.

**`agent_code_policy.py`:** allow the four public modules. See F7.

**`wayfindinglib/__init__.py`:** imports `configure_offline_iers` at load time from the root, which stays. Importing a public submodule runs `astrometricslib/__init__.py` first, so the deferred loading table there must hold.

**`build/codegen/generate_types.py`:** no rule change. Its paths change only when a module really moves.

**Issue #135:** the agreed order finishes the renames and merges on #135 first. Names here are as they stand on `134-integration`.

## 9. Methods of the sub-APIs

Sections 1 to 8 sort the names that `astrometricslib.__all__` exports. This section sorts the 55 public methods of the sub-APIs, the calls a script makes through `Astrometrics`. The user's rules for them are the same as for `wayfindinglib`. A method that only tests use, or that nobody reads, is private. A method only the library itself uses is private. A method only an AI tool reads is kept and listed. The user decided the results on 2026-10-31; "Rule result" and "Suggested" now hold those decisions.

**Result (decided 2026-10-31).** 51 methods are public and 4 are private. The four private methods are `processing.restore_excluded_frames`, `processing.detect_point_sources`, `processing.stack_subset` and `processing.tune_spectroscopy_calibration`: only scripts inside `astrometricslib` call them, and those scripts may use `_internal`. `jobs.query` becomes public with a reader, because the backend's job history reads move to it. Section 8 lists the register entries whose repeat steps need rewriting.

**How the readers were checked.** A call reaches a method through an object, so the scan looked at the receiver and not only at the name. A script parsed every Python file, the notebook code cells and the Python blocks of the Markdown files, and kept each attribute access whose name is a method name. It then decided which sub-API the receiver is by its text (`astrometrics.targets`, `self._astrometrics.quality`, `self.astrometrics.processing.calibration`, `processing_api`, `self._calibration`, `self._targets`, `context.astrometrics.targets` and similar), and for a bare name such as `catalog` by the test file it sits in. I read every receiver that was left over for the backend, the MCP servers, the scripts and `wayfindinglib`, and I dropped the ones that are not a sub-API (for example a dictionary named `targets` in `night_quality_task.py`). A text search of the Markdown files added the validation register. The RPC table (`backend/routers/rpc_router.py`) calls seven methods directly by name, and the table marks them `RPC`. The MCP column is the disposition in `mcp_servers/astrometrics_core/tool_manifest.json`. The server offers a tool only when its disposition is `keep`. `fix first` means the manifest plans a tool once a known problem is fixed. A method whose name starts with `delete` is withheld by a rule in `mcp_servers/common/reflection.py`, not by the manifest.

Column key: counts are files, not call sites. **Backend** is `backend/` without tests, and `RPC` means the RPC table calls it. **Notebooks and how-to** counts the notebooks, the pages in `documentation/how_to/` and the scripts beside the notebooks; `r` counts validation register entries, which tell a reviewer to call the method. **Library scripts** is `astrometricslib/scripts/`, and `1b` is a script in `build/`. **Wayfinding** is `wayfindinglib/` without tests, and `s` counts files in `wayfindinglib/scripts/`. **Tests** uses `a`, `w` and `o` as in Section 2. **Inside library** is the rest of `astrometricslib/`, outside tests and scripts.

### Targets (`astrometrics.targets`, 14)

| Member | Kind | Backend | MCP | Notebooks and how-to | Library scripts | Wayfinding | Tests | Inside library | Rule result | Suggested (not decided) | Note |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `targets.add` | method, writes | 1 | withheld | - | - | 1 | 1a | - | Public (outside reader) | Public. | Keep with `create` and `save`. |
| `targets.create` | method, writes | 1 | withheld | 4 | 1 | 2 | 5a 1o | 1 | Public (outside reader) | Public. | Keep with `add` and `save`, the write set for targets. |
| `targets.delete` | method, deletes | 1 | withheld (name rule) | - | - | - | 1a | - | Public (outside reader) | Public. | Keep with `delete_images`, as `disconnect` stayed with `connect`. Not offered to AI because the name starts with `delete`. |
| `targets.delete_images` | method, deletes | 1 | withheld (name rule) | - | - | - | 1a | - | Public (outside reader) | Public. | Only the backend (`image_service.py`). Keep with `delete`. |
| `targets.get` | method, reads | 2 | dropped | 13, 5r | 9 | 10 | 6a 4w 6o | 5 | Public (outside reader) | Public. | Allows small spelling differences. `read_saved` reads storage directly. |
| `targets.get_frame` | method, reads | 1 | offered | - | - | - | 1a | - | Public (outside reader) | Public. | Only the backend (`image_service.py`) and the AI tool. |
| `targets.get_header` | method, reads | 2 | offered | 1 | - | - | 1a | - | Public (outside reader) | Public. | Keep with `read_frame_header`: one reads a target's file as text cards, the other reads any file by path. |
| `targets.imaged_field_centers` | method, reads | - | offered | - | - | 1 | - | - | Public (outside reader) | Public. | The wayfinding reader is `planning.get_imaged_field_centers`, which the wayfinding review removes. Its one script then calls this directly, so the reader stays. The count may be low if a caller goes through another wrapper. |
| `targets.list` | method, reads | 2 | dropped | 2, 2r | 9 | 10 | 7a 3w | 2 | Public (outside reader) | Public. | Read twin of `get` and `query`. Wayfinding holds a dictionary named `_targets` (`library_inputs.py`) that is not this call; it was left out. |
| `targets.query` | method, reads | 2 | offered | - | - | 1 | 2a 1o | - | Public (outside reader) | Public. |  |
| `targets.read_frame_header` | method | 1 | dropped | - | - | 1 | 1o | - | Public (outside reader) | Public. | Keep with `get_header`. |
| `targets.read_saved` | method | 1 | dropped | - | - | - | 1a | - | Public (outside reader) | Public. | Only the backend (`target_service.read_saved_target`). It may overlap `get(refresh=True)`; I did not compare them. |
| `targets.reindex_frames` | method, writes | 1 | withheld | 4 | 2 | 1 | 2a 1w 1o | 1 | Public (outside reader) | Public. | Keep with `calibration.reindex_frames`. Ingestion calls both. |
| `targets.save` | method, writes | 1 | withheld | 3, 2r | 6 | 2 | 5a 2o | 3 | Public (outside reader) | Public. | Keep with `create` and `add`. The library also calls it through `self` and `_targets`. |

### Stars (`astrometrics.stars`, 8)

| Member | Kind | Backend | MCP | Notebooks and how-to | Library scripts | Wayfinding | Tests | Inside library | Rule result | Suggested (not decided) | Note |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `stars.analyze_periodicity` | method, writes | 1 | fix first | 1r | - | - | 1a 1o | - | Public (outside reader) | Public. | Only the backend. The AI tool needs a fix first (it writes the catalog). |
| `stars.create` | method, writes | 1 | withheld | - | - | - | - | 1 | Public (outside reader) | Public. | Keep the four star writes together: `create`, `update`, `delete`, `find_or_create_by_position`. `find_or_create_by_position` calls the other three inside the library. |
| `stars.dataset_version` | method | 1 | dropped | - | - | - | 1a 1o | - | Public (outside reader) | Public. | Only the backend (`stellar_service.py:200`, a cache key). Not an AI tool. |
| `stars.delete` | method, deletes | 1 | withheld (name rule) | - | - | - | - | 1 | Public (outside reader) | Public. | Keep with `create`. Not offered to AI because the name starts with `delete`. |
| `stars.find_or_create_by_position` | method, writes | 1 | withheld | - | - | - | 1a | - | Public (outside reader) | Public. | Only the backend. Keep with the other star writes. |
| `stars.get` | method, reads | 1 | offered | 2, 1r | - | 1 | 2a | 2 | Public (outside reader) | Public. |  |
| `stars.query` | method, reads | 1 | offered | 3, 3r | 2 | 3 | 6a 1w 2o | 1 | Public (outside reader) | Public. |  |
| `stars.update` | method, writes | 1 | withheld | - | - | - | 1a | 1 | Public (outside reader) | Public. | Keep with `create`, `delete` and `find_or_create_by_position`. |

### Processing (`astrometrics.processing`, 14)

| Member | Kind | Backend | MCP | Notebooks and how-to | Library scripts | Wayfinding | Tests | Inside library | Rule result | Suggested (not decided) | Note |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `processing.acquire_job_slot` | method, writes | 1 | dropped | - | 1 | - | 1a | 1 | Public (outside reader) | Public. | A context manager, not a data call. The backend wraps `process_target` in it. Library code uses it through `self`. |
| `processing.calibration` | property | 2 | - | 1, 2r | - | 2 | 1a 2o | 2 | Public (outside reader) | Public. | A property that returns `CalibrationCatalog`. It must stay while any `calibration` call is public. The `getattr` on the wayfinding side is on `quality`, not this. |
| `processing.process_target` | method, writes | 1 | withheld | 5, 4r | 2 | - | 4a | - | Public (outside reader) | Public. |  |
| `processing.remake_preview` | method, runs a pipeline | 1 | offered | 1, 1r | - | - | 1a | - | Public (outside reader) | Public. |  |
| `processing.restore_excluded_frames` | method, writes | - | withheld | - | 1 | - | 3a 1w | - | Private (decided; library scripts only) | Private. | One library script (`scripts/restore_excluded_frames.py`, a command-line wrapper) and tests. It undoes what `quality.set_aside_frames` lists; the script may use `_internal`. |
| `processing.run_spectroscopy_by_session` | method, writes | 1 | fix first | - | - | - | - | - | Public (outside reader) | Public. | Only the backend (`analysis_orchestrator.py`). The AI tool needs a fix first. |
| `processing.measure_noise` | method, writes | 1 | withheld | 2r | 1 | - | 1a 1o | - | Public (outside reader) | Public. |  |
| `processing.detect_point_sources` | method, computes | - | fix first | 1r | 1 | - | - | - | Private (decided; library scripts only) | Private. | One library script and one register entry (`validation/astrometry.md`, repeat step to rewrite). The manifest planned an AI tool once it takes a file path; that plan now needs a public route or is dropped. Partner of `plate_solve`. |
| `processing.open_in_siril` | method | 1 | dropped | - | - | - | 1a 1o | - | Public (outside reader) | Public. | Only the backend. Not offered to AI. |
| `processing.plate_solve` | method, computes | - | fix first | 1r | - | 1 | 2a | - | Public (outside reader) | Public. | The reader is wayfinding (`centering.py`) and the register. |
| `processing.stack` | method, runs a pipeline | 1 | offered | 3, 1r | 3 | - | 3a | - | Public (outside reader) | Public. |  |
| `processing.stack_subset` | method, writes | - | withheld | 2r | 1 | - | 1a | - | Private (decided; library scripts only) | Private. | One library script (`run_all_target_processing.py`). Two register entries (`stacking.md`, `wayfinding.md`) tell a reviewer to call it, and need their repeat steps rewritten. Its output is read back with `quality.query(variant=EARLIER_NIGHTS_VARIANT)`, which wayfinding calls. |
| `processing.stack_summary` | method, reads | RPC | offered | 1r | - | - | 1a | - | Public (outside reader) | Public. | Reached only through the RPC table and the AI tool. |
| `processing.tune_spectroscopy_calibration` | method, writes | - | withheld | 1r | 1 | - | - | - | Private (decided; library scripts only) | Private. | One library script (`run_empirical_validation.py`) and one register entry (`validation/spectroscopy.md`, repeat step to rewrite). It writes the live configuration. |

### Calibration (`astrometrics.processing.calibration`, 6)

| Member | Kind | Backend | MCP | Notebooks and how-to | Library scripts | Wayfinding | Tests | Inside library | Rule result | Suggested (not decided) | Note |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `processing.calibration.assess` | method, computes | RPC | offered | 1r | - | - | 2a | - | Public (outside reader) | Public. | Reached through the RPC table and the AI tool. Tests and the register call it too. |
| `processing.calibration.measure_frames` | method, computes | 1 | withheld | 1r | - | - | 1a | - | Public (outside reader) | Public. | Same name as `quality.measure_frames`, a different call. |
| `processing.calibration.measure_sensor_noise` | method, computes | RPC | withheld | 2r | - | - | 1a | - | Public (outside reader) | Public. | Reached only through the RPC table (and the register). Writes the configuration when `save` is on. |
| `processing.calibration.query` | method, reads | 3, RPC | offered | 1, 1r | - | 1 | 2a | - | Public (outside reader) | Public. |  |
| `processing.calibration.reindex_frames` | method, writes | 1 | withheld | - | - | 1 | 3a | - | Public (outside reader) | Public. | Keep with `save` and with `targets.reindex_frames`. |
| `processing.calibration.save` | method, writes | 1 | withheld | - | - | 1 | - | - | Public (outside reader) | Public. | Keep with `reindex_frames`: ingestion and wayfinding call them one after the other. |

### Quality (`astrometrics.quality`, 9)

| Member | Kind | Backend | MCP | Notebooks and how-to | Library scripts | Wayfinding | Tests | Inside library | Rule result | Suggested (not decided) | Note |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `quality.calibration_identities` | method, reads | - | offered | - | - | 1 | 1a | - | Public (outside reader) | Public. | Wayfinding reads it with `getattr(astrometrics.quality, "calibration_identities", None)` (`target_report_inputs.py` lines 189 and 245). A search for `.calibration_identities(` would miss it. It is counted here. |
| `quality.capture_settings` | method, reads | - | offered | 1r | - | 5 | 1a | 1 | Public (outside reader) | Public. | Five wayfinding files. `set_aside_frames` calls it inside the library. |
| `quality.measure_frames` | method, computes | - | offered | 1r | - | 1 | 4a | - | Public (outside reader) | Public. | Same name as `calibration.measure_frames`, a different call. |
| `quality.measure_stack` | method, computes | 1 | offered | 1, 1r | 2 | - | 1a | - | Public (outside reader) | Public. |  |
| `quality.metrics` | method, reads | RPC | offered | - | - | 1 | 1a 4w | - | Public (outside reader) | Public. | Four wayfinding test files read it, plus one wayfinding module. |
| `quality.query` | method, reads | 1 | offered | 4r | 1 | 9 | 1a 1o | 1 | Public (outside reader) | Public. |  |
| `quality.record_frame_measurements` | method, writes | 1 | withheld | 2r | 1 | 1 | - | - | Public (outside reader) | Public. |  |
| `quality.resolve_rows` | method, reads | RPC | offered | - | - | - | 1a | - | Public (outside reader) | Public. | Reached through the RPC table (the UI calls `quality:resolve_rows`) and the AI tool. |
| `quality.set_aside_frames` | method, reads | RPC | offered | 1, 1r | - | 1 | 1a | - | Public (outside reader) | Public. | Keep with `processing.restore_excluded_frames`. |

### Visualization (`astrometrics.visualization`, 3)

| Member | Kind | Backend | MCP | Notebooks and how-to | Library scripts | Wayfinding | Tests | Inside library | Rule result | Suggested (not decided) | Note |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `visualization.get_last_captured_image` | method, reads | 1 | fix first | - | - | - | 1a | - | Public (outside reader) | Public. | Only the backend. The AI tool needs a fix first (an image too large for a reply). |
| `visualization.plot` | method, computes | - | offered | 3 | - | - | 1a | - | Public (outside reader) | Public. | Only notebooks and scripts under `documentation/notebooks/`, plus the AI tool. |
| `visualization.render_fits` | method, reads | 1 | offered | - | 1b | - | 2a 1o | - | Public (outside reader) | Public. | The `1b` reader is `build/linux/generate_fits_thumbnail.py`, outside the library packages. The backend and the AI tool read it too. |

### Jobs (`astrometrics.jobs`, 1)

| Member | Kind | Backend | MCP | Notebooks and how-to | Library scripts | Wayfinding | Tests | Inside library | Rule result | Suggested (not decided) | Note |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `jobs.query` | method, reads | - (planned: 1) | offered | - | - | - | 1a | - | Public (backend job history moves here) | Public. | Today only tests and the AI tool read it. Decided: the backend's job history reads move from `JobStore` to this call, so the second route goes away. `astrometricslib.jobs` is the public module for the rest of job tracking. |

**Pairs that should stay together.** These pairs stay together, as `equipment.disconnect` stayed with `connect`:

- `targets.create`, `add` and `save` (the writes), and `targets.delete` with `targets.delete_images`.
- `stars.create`, `update`, `delete` and `find_or_create_by_position`. The last calls the other three inside the library.
- `targets.get_header` with `targets.read_frame_header`.
- `calibration.reindex_frames` with `calibration.save` (ingestion calls them one after the other), and both with `targets.reindex_frames`.
- `quality.set_aside_frames` with `processing.restore_excluded_frames`. The second is now private, but it undoes what the first lists, so its script keeps working through `_internal`.
- `processing.calibration` (the property) with every `calibration` call. It must stay public while any of them is.

**Where the search could be wrong.**

- A reader that reaches a method through `getattr`, a variable passed between functions, or a mock may be missed. The one `getattr` use found is `quality.calibration_identities` in `target_report_inputs.py`; it is counted.
- The backend scripting console gives AI-written code the live `astrometrics` object (`scripting_service.py:278`), and `api_doc_generator.py` lists the public methods of each class by reflection. Neither reads a particular method, so a method used only from a console session would show as having no reader.
- The user's own scripts and notebooks outside the repository are not seen.
- A test double such as `fake_astrometrics.targets` is counted as a test. A fake in a non-test file would be counted as a real reader.
- The wayfinding reader of `targets.imaged_field_centers` is a wrapper that the wayfinding review removes; the script that uses it would then call the method directly.
- Markdown files other than the register, the how-to pages and the notebooks mention methods in prose and code blocks. The scan counted only Python code blocks, plus a text search of the register.

**Answers of 2026-10-31.** The four private candidates are private. `jobs.query` is public, with the backend's job history reads moving to it. `targets.read_saved` is still open: only the backend reads it, and it may overlap `targets.get(refresh=True)`. Keep it as it is, or merge under "one call with parameters"? (Section 11.)

## 10. What I could not check

- Private scripts of the user, outside the repository.
- Attribute reads with no import: code can use a model it gets back without naming it, which this scan does not count and does not need to.
- Reader counts come from `134-integration` as of this writing. A name's count can change as #134 and #135 land.
- I did not run any move, so no claim here about a new call being enough has been tested. The verdicts in Section 5 come from reading call sites.
- The method table in Section 9 comes from the receiver reading described there, not from type information. Where the receiver was a short name such as `catalog`, I used the test file it sits in.
- `lint-imports` keeps `wayfindinglib/test/test_documentation_papers.py` although it imports `astrometricslib.test...`. I did not work out why (the test may fall outside its graph). It matters if the new contract is meant to cover tests.

## 11. Remaining before building

Only what is really undecided. Everything else in this document is decided or follows from a decision.

1. **Group 10 leftovers.** `EARLIER_NIGHTS_VARIANT` (public constant in `reports`, or a documented literal; S10), and the suggestions for `UNKNOWN_CAPTURE_SETTING_CAMERA` (a method on `CaptureSetting`; S12), `PIXEL_BLUR_FWHM_SQUARED_FACTOR` (a copy; S13), `aperture_identity_from_number` (compare the numbers; S14) and `fold_exposure_lengths` (part of N3; S15). None is covered by the science-limits answer.
2. **Shapes and homes of the new calls.** N3 (`processing.predict_stack_weights`: one result model for mode, weights, folded lengths and reasons); N4 (`add_frames`), N5 (`targets.sessions`) and N7 (the aperture-record read) are open; N6 is decided as a public call, with name and home open (`processing.calibration.camera_profile` is only a suggestion, and callers that hold only `config` need a handle).
3. **Suggestions from the call-site reading that the answers did not mention.** S1 (drop the backend annotations), S2 to S7 (values on rows, a `wayfindinglib` coordinate parser, `FrameRecord.subject_id` once it is checked that a `FrameRecord` can know its target), and the `safe_json_dumps` copy (check first whether plain `json.dumps` does). Confirm in one pass.
4. **`targets.read_saved`.** Keep, or merge with `targets.get(refresh=True)`.
5. **Documentation of results (F6).** Where a script writer learns the fields of a returned model once the models are private. Suggestion: an API page section built from the `_internal` models with no import path shown.
6. **The `wayfindinglib` items** that touch this document: the name of the public `group_alignment_attempts` call, and the `MountPointingModel` and `PolarAlignmentStatus` annotations. See `WAYFINDINGLIB_API_REVIEW.md`, Section 12.
