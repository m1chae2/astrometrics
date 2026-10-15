# Wayfinding library implementation

*Version 2.0 · 2026-10-15 · Status: current*

## Abstract

This paper maps the design in `Wayfinding_Library_Architecture.md` to the Python source in `wayfindinglib/`. It names the layers of the package and the rule that keeps them apart, the model classes that hold each record, and the modules that carry out planning, execution and control. It lists the settings the code uses, with their values. It also names the tests and import contracts that check the structure, and it states which parts of the design have code behind them and which do not yet. The Wayfinding library has no validation register yet, so its settings are design choices that have not been measured.

Terms are defined in the [glossary](./Wayfinding_Library_Architecture.md#appendix-a-glossary). This document also defines each term the first time it appears.

## 1. Scope

**Statement of need.** A reader of the architecture paper needs to find the code behind each idea. A developer who changes the code needs to know which idea and which settings it touches. This paper is that index. Theory stays in [Wayfinding_Library_Architecture.md](./Wayfinding_Library_Architecture.md). Module paths in this paper are relative to `wayfindinglib/` unless a path starts with another folder.

**Settings.** The architecture paper states no tuned value. This paper lists them in tables. The Wayfinding library has no validation register yet, so every value here is a design choice that has not been measured. A Notes column gives the reasoning behind a value, and any observation that informed it.

The library's internal modules are hidden from the public API reference. The one supported entry point is the package root, which loads its names on first use so that importing the library loads no hardware code. The root offers `Wayfinder`, which holds three sub-APIs: `control` (`ObservatoryControl`), `planning` (`ObservationPlanning`) and `execution` (`ObservationExecution`). It also offers the models those sub-APIs take and return, the pure function `group_alignment_attempts`, and the library's error class. The error categories themselves come from `astrometricslib`. Importing the package also turns off downloads of Earth-rotation tables, so the library works offline.

<!-- impl: wayfindinglib.Wayfinder -->
<!-- impl: wayfindinglib.api.control.control.ObservatoryControl -->
<!-- impl: wayfindinglib.api.planning.ObservationPlanning -->
<!-- impl: wayfindinglib.api.execution.ObservationExecution -->
<!-- impl: wayfindinglib.analytics.alignment_sessions.group_alignment_attempts -->

## 2. System architecture

### 2.1 Layers and import contracts

The architecture paper's Figure 1 maps onto the folders in Table 1. Every folder is a regular Python package (it has an `__init__.py`), because the import linter cannot see a folder without one. Imports inside a `TYPE_CHECKING` block do not count, because they never run.

**Table 1.** Layers and their folders.

| Layer | Folders and modules |
| --- | --- |
| Foundation | `models/`, `astronomy/`, `data_access/`, `analytics/`, `drivers/butler.py`, `drivers/control_record_store.py`, `drivers/catalog/` |
| Control | `tasks/control_tasks/`, `session_analysis/`, `watchdog/`, `api/control/`, and the hardware drivers: `drivers/indi/`, `drivers/indi_interface.py`, `drivers/interfaces/`, `drivers/phd2/`, `drivers/simulators/`, `drivers/ekos/`, `drivers/stellarmate_interface.py` |
| Planning | `tasks/planning_tasks/`, `api/planning.py` |
| Execution | `tasks/execution_tasks/`, `api/execution.py` |
| Composition root | `wayfindinglib/__init__.py` (`Wayfinder` and the public exports), `api/__init__.py` |
| Scripts | `scripts/` |

Three shared definitions sit in the foundation so that a lower layer never imports a higher one to use them.

- **Protocol names.** `models/equipment_and_site/driver_protocols.py` lists the valid protocol name for each device type (`MOUNT_PROTOCOLS`, `GUIDING_PROTOCOLS` and the rest). `data_access/equipment_catalog_reader.py` checks the configured names against these sets, so reading the equipment catalog loads no driver. `drivers/interfaces/registry.py` keys its driver classes by the same names. `test_protocol_names_match_the_driver_registry_keys` in `drivers/interfaces/test/test_driver_interfaces.py` keeps the two equal.
- **Sensor readings.** `SensorReadings`, the mapping the safety monitor evaluates, is defined in `models/policy/safety.py`. The weather drivers and `tasks/control_tasks/safety_monitor.py` import it from there. `drivers/interfaces/weather_driver.py` re-exports it as part of its interface.
- **Alignment grouping.** `group_alignment_attempts` in `analytics/alignment_sessions.py` groups plate solves into `AlignmentTargetSession` records. The package root exports it. The model itself carries no behavior.

<!-- impl: wayfindinglib.models.equipment_and_site.driver_protocols.MOUNT_PROTOCOLS -->
<!-- impl: wayfindinglib.drivers.interfaces.registry.build_mount_driver_registry -->
<!-- impl: wayfindinglib.models.policy.safety.SensorReadings -->
<!-- impl: wayfindinglib.models.session.telemetry.AlignmentTargetSession -->

### 2.2 Precedent and divergence from Rubin (Architecture Section 2.2)

The five-state vocabulary is the enumeration `DeviceSummaryState` in `models/policy/device_state.py` (`OFFLINE`, `STANDBY`, `DISABLED`, `ENABLED`, `FAULT`). The terms of the Rubin system that match each class are in Appendix E of the architecture paper.

<!-- impl: wayfindinglib.models.policy.device_state.DeviceSummaryState -->

### 2.3 Capability delegation (Architecture Section 2.3)

The six capabilities are the members of `ObservatoryCapability`: `MOUNT_CONTROL`, `PLATE_SOLVE_ALIGNMENT`, `AUTOGUIDING`, `AUTOFOCUS`, `CAPTURE_ORCHESTRATION` and `OBSERVATORY_SAFETY`. The states are `DelegationState` (`DELEGATED`, `SHADOWED`, `AUTHORITATIVE`). `DelegationPolicy` holds one `CapabilityDelegation` for each capability. All three classes live in `models/policy/delegation.py`. A capability with no entry resolves to `DELEGATED`, the safe default, because that state issues no command the system did not issue before.

The rules of the architecture are enforced in `data_access/delegation_policy_reader.py`, not in the models, which define shape only.

| Rule | Enforced by |
| --- | --- |
| Safety is never shadowed | `validate_delegation_policy` raises `DelegationPolicyValidationError` when `OBSERVATORY_SAFETY` is `SHADOWED` |
| Capture waits for the corrections | `validate_delegation_policy` refuses capture orchestration outside `DELEGATED` unless alignment, guiding and focus are all `AUTHORITATIVE` |
| Calibration comes first | `validate_delegation_policy` refuses `AUTOGUIDING` outside `DELEGATED` without a `GuiderCalibration`, and `AUTOFOCUS` without a `FocusModel` |
| Computed corrections pass through shadow | `promote_capability` refuses `AUTHORITATIVE` for alignment, guiding and focus unless the prior state is `SHADOWED`. It compares the prior and requested states, because a policy snapshot holds no history. |

Every hardware command in `tasks/control_tasks/hardware_operations.py` starts with `_require_authoritative`, which raises `PermissionDeniedError` unless the command's capability is `AUTHORITATIVE`. Reads make no such check. Table 2 lists the capability each command needs.

**Table 2.** Capability required by each hardware command.

| Capability | Commands |
| --- | --- |
| `MOUNT_CONTROL` | `slew`, `park`, `unpark`, `set_tracking`, `manual_move`, `abort_motion`, `set_slew_rate` |
| `PLATE_SOLVE_ALIGNMENT` | `sync_mount` |
| `AUTOGUIDING` | `pulse`, `guide_expose` |
| `AUTOFOCUS` | `focus_move` |
| `CAPTURE_ORCHESTRATION` | `set_filter`, `capture_image` |
| `OBSERVATORY_SAFETY` | `open_enclosure`, `close_enclosure` |

`tasks/control_tasks/capability_promotion.py` holds the decision layer above `promote_capability`. `apply_promotion_decision` re-validates the whole policy and records the result in the Butler (the storage layer). `summarize_divergence_evidence` returns a `DivergenceEvidenceSummary` with the sample count and agreement rate (Architecture Equation (2)). `set_all_capabilities` runs the bulk change and returns a `BulkDelegationOutcome` with the capabilities that `applied` and those `rejected`, each with its reason. It promotes toward `AUTHORITATIVE` in the order safety, mount control, alignment, guiding, focus and capture. It demotes toward `DELEGATED` in the reverse order. The facade offers it as `control.safety.enter_monitoring_mode` and `control.safety.enter_controller_mode`, and offers the single change as `control.safety.apply_promotion_decision`.

<!-- impl: wayfindinglib.models.policy.delegation.CapabilityDelegation -->
<!-- impl: wayfindinglib.data_access.delegation_policy_reader.DelegationPolicyValidationError -->
<!-- impl: wayfindinglib.data_access.delegation_policy_reader.get_delegation_policy -->
<!-- impl: wayfindinglib.tasks.control_tasks.capability_promotion.apply_promotion_decision -->
<!-- impl: wayfindinglib.tasks.control_tasks.capability_promotion.set_all_capabilities -->
<!-- impl: wayfindinglib.api.control.safety.SafetyControl -->

### 2.4 Delegation phases (Architecture Section 2.4)

The phases are not a type in the code. The phase of the observatory is whatever combination of states `DelegationPolicy` holds. `CommissioningRun` in `models/policy/commissioning.py` carries a `phase` field from 0 to 5, a list of `CommissioningObservation` records and an `aborted` flag. It is append-only. `control.equipment.save_commissioning_run` stores it. The `criterion` text of each observation is meant to match a gate criterion word for word.

<!-- impl: wayfindinglib.models.policy.commissioning.CommissioningObservation -->
<!-- impl: wayfindinglib.api.control.equipment.EquipmentControl.save_commissioning_run -->

### 2.5 Domain models and operations (Architecture Section 2.5)

Table 3 maps each record of the architecture's Table 2 to its classes. All are pydantic classes (pydantic is a Python package for typed, validated classes) that hold fields and validation and do no calculation.

**Table 3.** Records of the architecture paper and the classes that hold them.

| Architecture record | Classes | Module |
| --- | --- | --- |
| Telescope specification | `Telescope` (also `meridian_flip_delay_min`, derived from `flip_hour_angle_deg`) | `models/equipment_and_site/equipment.py` |
| Camera specification | `Camera` | `models/equipment_and_site/equipment.py` |
| Guide optics | `GuideScope`, with the `active_guide_scope_id` and `active_guide_camera_id` fields of the catalog | `models/equipment_and_site/equipment.py` |
| Equipment catalog and active pairing | `EquipmentCatalog`, `EquipmentConfiguration` (pixel scale and field of view) | `models/equipment_and_site/equipment.py` |
| Site profile | `SiteProfile`, `AvoidanceZone` | `models/equipment_and_site/site_profile.py` |
| Enclosure specification | `Enclosure`, `EnclosureType`, `EnclosureState` | `models/equipment_and_site/enclosure.py` |
| Guider calibration | `GuiderCalibration` | `models/equipment_and_site/guider_calibration.py` |
| Focus model | `FocusModel`, `ApproachDirection` | `models/equipment_and_site/focus_model.py` |
| Calibration inventory and advisory | `CalibrationEntry`, `CalibrationStats`, `CalibrationAdvisory` | `models/equipment_and_site/calibration.py` |
| Delegation policy | `DelegationPolicy`, `CapabilityDelegation` | `models/policy/delegation.py` |
| Safety rule set and assessment | `SafetyRule`, `SafetyRuleSet`, `SafetyAssessment`, `SafetyVerdict` | `models/policy/safety.py` |
| Recovery policy and fault record | `RecoveryPolicy`, `RecoveryAttempt`, `FaultRecord` | `models/policy/recovery.py` |
| Correction settings | `CorrectionConfig` | `models/session/correction_config.py` |
| Performance envelope | `PerformanceEnvelope`, `PerformanceThreshold`, `ThresholdTier`, `ThresholdStatus` | `models/equipment_and_site/performance_envelope.py` |
| Observation package | `ObservationPackage`, `ExposureRequest`, `FrameType`, `DitherConfig` | `models/planning/observation_package.py` |
| Mosaic panel set | `MosaicPlan`, `MosaicPanel` | `models/planning/mosaic.py` |
| Target quality advisory | `TargetQualityAdvisory`, `QualityFlagSummary`, `ScienceOutcomeSummary` | `models/planning/quality_advisory.py` |
| Observation session, queue entry, diagnostic | `ObservationSession`, `QueuedObservationPackage`, `InfeasibilityReason`, `InfeasibilityReasonCode`, `SessionStatus`, `QueueEntryStatus`, `StartTimeMode`, `QueueRequest` | `models/session/observation_session.py` |
| Divergence record | `DivergenceRecord` | `models/session/divergence.py` |
| Commissioning run | `CommissioningRun` | `models/policy/commissioning.py` |
| Session record | `EkosSessionContext` | `models/session/ekos_session.py` |
| Device summary state | `DeviceState`, `DeviceRole`, `DeviceSummaryState` | `models/policy/device_state.py` |
| Telemetry | `GuidingSample`, `GuidingSampleSource`, `WeatherSample` (in `observation_session.py`), `MountPointingModel`, `GuidingSpectrumAnalysis` | `models/session/telemetry.py` |
| Safe-state and flip outcomes | `SafeStateOutcome`, `MeridianFlipOutcome`, `GuideStarLossEvent` | `models/session/safe_state.py`, `meridian_flip.py`, `guide_star_loss.py` |

The architecture's Table 3 lists the operations. The facade exposes them through the seven children of `ObservatoryControl` (Section 6.1), through `ObservationPlanning` (Section 4) and through `ObservationExecution` (Section 5).

<!-- impl: wayfindinglib.models.equipment_and_site.equipment.Camera -->
<!-- impl: wayfindinglib.models.session.divergence.DivergenceRecord -->
<!-- impl: wayfindinglib.models.session.ekos_session.EkosSessionContext -->
<!-- impl: wayfindinglib.models.policy.device_state.DeviceState -->

## 3. Foundation (Architecture Section 3)

**Equipment catalog.** `data_access/equipment_catalog_reader.py` builds the `EquipmentCatalog` from the configuration file and checks each protocol name against `models/equipment_and_site/driver_protocols.py`. `tasks/control_tasks/equipment_activation.py` changes the active telescope or camera. It validates the id before it records the choice, so a typo cannot leave the active entry pointing at nothing. `EquipmentConfiguration` derives the pixel scale from `PLATE_SCALE_CONSTANT` (206.265, Architecture Equation (1)) and the field of view. A `GuideScope`, when active, supplies the focal length for the guide camera's pixel scale (`ControlContext.guider_plate_scale_arcsec_per_px`).

**Site.** `data_access/site_profile_reader.py` reads the `[Observatory.Location]` section of the configuration (`configured_observer_location`), falls back to the site in `FALLBACK_SITE` (Denver: latitude 39.7392, longitude -104.9903, elevation 1600 m) with a logged warning (`resolve_site_location`), and seeds the default `SiteProfile` once (`get_or_seed_default_site_profile`). Planning reads the site only through this module. Control reads the observer position through `hardware_operations.observer_location`, which asks the mount first and then uses the configuration. The slew limit check in `drivers/indi/mount_controller.py` reads the position from the mount's `GEOGRAPHIC_COORD` property. The single-source rule of the architecture's Section 3.3 therefore holds for planning only.

**Policies.** `data_access/safety_policy_reader.py` returns the stored `SafetyRuleSet`, or `None` when none exists, never a default set. `data_access/delegation_policy_reader.py` is described in Section 2.3.

**Storage.** `drivers/butler.py` holds `DiskButler`, the one storage path for the library's records. It implements the shared `AbstractButler` of `astrometricslib` and stores each dataset type as its own table in `wayfinding.db`, through the generic SQLite `Butler` of `astrometricslib`. `_DATASET_TYPES` lists the dataset types: observation sessions, packages, site profiles, calibrations, policies and the rest. `DiskButler.library_path` is the folder that holds the database and the files kept next to it, such as downloaded Ekos logs. High-volume records are kept in plain SQL tables of the same file by `ControlRecordStore` in `drivers/control_record_store.py`: `alignment_attempts`, `guiding_samples` and `polar_alignments`. `DiskButler.control_records` gives the store, and `control` reads it as `ControlContext.records`. A row's `session_id` names its observing night, and a record saved without one is filed under the night its time stamp falls in (twelve hours earlier, in local time, so a night that crosses midnight stays one night). `build/migrations/move_guiding_alignment_records.py` moves records that an older version kept in `astrometrics_log.db`.

<!-- impl: wayfindinglib.data_access.equipment_catalog_reader.get_equipment_catalog -->
<!-- impl: wayfindinglib.tasks.control_tasks.equipment_activation.set_active_telescope -->
<!-- impl: wayfindinglib.data_access.site_profile_reader.get_or_seed_default_site_profile -->
<!-- impl: wayfindinglib.data_access.safety_policy_reader.get_safety_rule_set -->
<!-- impl: wayfindinglib.drivers.butler.DiskButler -->
<!-- impl: wayfindinglib.drivers.control_record_store.ControlRecordStore -->

## 4. Observation planning (Architecture Section 4)

Folder: `tasks/planning_tasks/`. The facade is `ObservationPlanning` in `api/planning.py`. Importing it loads no hardware driver, which a contract and a test check (Section 7.1).

| Idea | Module and names |
| --- | --- |
| Night-window resolution | `night_window.py` (`resolve_night_window`), using `astronomy/solar_position.py` (`solar_altitude_deg`) |
| Visibility policy | `visibility_tasks.py` (`effective_altitude_floor`, `visibility_state_at`, `find_earliest_visible_window`, `ever_clears_floor`, `ever_visible`) over `compute_altaz` in `astronomy/coordinate_transforms.py` |
| Placement | `scheduling.py` (`PlacementRequest`, `place`, `schedule_session`, `_apply_advisory_boost`, `_diagnose_infeasibility`) |
| Mosaic panels | `mosaic_tasks.py` (`calculate_panels`, `create_mosaic`) |
| Archive-informed advice | `quality_advisory_tasks.py` (`build_target_quality_advisory`) and `calibration_advisory_tasks.py` (`build_calibration_advisory`) |
| Visibility report | `visibility_report.py` (`build_visibility_report`), with the hour-angle and rise and set helpers in `visibility_operations.py`. It answers `ObservationPlanning.get_visibility`, for one moment and any number of objects, or over a night for up to 30. |
| Sequence plans | `planning_operations.py` builds the plan the application's sequencer queue runs (`create_plan(kind="sequence")`) |
| Sky engine | `sky_engine.py` holds `SkyEngine`. `ObservationPlanning` builds it once and passes it to the planning functions. It holds the site (read through `data_access/site_profile_reader.py`), the meridian-flip delay, the library handle, the catalog drivers and the constellation lines. `coordinate_operations.py` (local sidereal time), `resolution_operations.py`, `visibility_operations.py`, `catalog_operations.py` and `constellation_operations.py` hold the calculations. |
| Sky sources | `sky_sources.py` and the catalog drivers in `drivers/catalog/` (local bright-star, SIMBAD and deep-star catalogs) |

The plan kinds of `create_plan` are `sequence`, `package`, `empty_session` and `scheduled_session`. The advisory kinds of `get_advisory` are `quality` and `calibration`. `edit_queue` adds or changes entries by hand. The application's sequencer queue is one observing session edited with `ObservationPlanning.edit_queue`.

The placement rule is in `scheduling.place`. Fixed-time requests are checked for continuous visibility over their span and for overlaps, and they go into the queue first. Each soonest-available request then gets its earliest free visible start (`_earliest_unoccupied_visible_start`). The request with the earliest start is placed, and a tie goes to the larger sum of `priority` and the applied boost. The five reasons are the members of `InfeasibilityReasonCode`: `NEVER_CLEARS_ALTITUDE`, `BLOCKED_BY_AVOIDANCE_ZONE`, `WINDOW_TOO_SHORT`, `NIGHT_FULLY_COMMITTED` and `FIXED_TIME_CONFLICT`. A queue entry has no infeasible status, because unplaced packages never enter the queue. The boost is added only for an opted-in package (`quality_weighting_enabled`): one amount when any pipeline flagged the target, and a second when it has a confirmed asteroid candidate or a variable-star candidate. The variable-star count is always 0 because the star catalog cannot yet list a target's stars cheaply. `visibility_state_at` tests the altitude floor, the telescope's `max_altitude_deg` when `altitude_limits_enabled` is true, and the avoidance zones. It does not apply the hour-angle bound.

<!-- impl: wayfindinglib.tasks.planning_tasks.scheduling.PlacementRequest -->
<!-- impl: wayfindinglib.tasks.planning_tasks.visibility_tasks.find_earliest_visible_window -->
<!-- impl: wayfindinglib.tasks.planning_tasks.sky_engine.SkyEngine -->
<!-- impl: wayfindinglib.tasks.planning_tasks.planning_operations -->
<!-- impl: wayfindinglib.models.planning.sequence_plan.SequencePlan -->

**Table 4.** Planning settings. `PlanningConfig` is in `models/planning/planning_config.py`.

| Setting | Value | Where | Notes |
| --- | --- | --- | --- |
| Time step of the night window and of visibility sampling | 5 min | `PlanningConfig.night_window_time_step_min` | Coarse by design. It fits the scheduling step size. |
| Twilight limit of the night window | -12° of solar altitude (nautical twilight) | `PlanningConfig.twilight_sun_altitude_deg` | A standard astronomical definition. |
| Boost for a target flagged by any pipeline | 2 | `PlanningConfig.flagged_quality_priority_boost` | Priority is only a tie-breaker, so the boost reorders packages that could start together. |
| Boost for a confirmed asteroid or a variable-star candidate | 1 | `PlanningConfig.science_outcome_priority_boost` | The two boosts add, so the largest boost is 3. |
| Default dither cadence and size | every 3 frames, 3 pixels | `PlanningConfig.dither_every_n_frames_default`, `DitherConfig` | |
| Mosaic overlap | 10% | `PlanningConfig.mosaic_panel_overlap_percent` | |
| Field of view when the configuration gives none | 23.5 by 15.6 mm sensor, 400 mm focal length | `DEFAULT_SENSOR_WIDTH_MM`, `DEFAULT_SENSOR_HEIGHT_MM`, `DEFAULT_FOCAL_LENGTH_MM` in `mosaic_tasks.py` | Stand-in values for a mosaic only. |
| Telescope altitude range | 0° to 90° | `Telescope.min_altitude_deg`, `max_altitude_deg` | Limits are on by default (`altitude_limits_enabled`). |
| Telescope hour-angle bound | 2 h, off by default | `Telescope.max_hour_angle_hours`, `hour_angle_limits_enabled` | Checked at slew time, not in placement. |
| Meridian flip hour angle | 1° past transit (4 min) | `Telescope.flip_hour_angle_deg` | `meridian_flip_delay_min` is derived from it (Architecture Equation (3)). |

## 5. Observation execution (Architecture Section 5)

Folder: `tasks/execution_tasks/`. The facade is `ObservationExecution` in `api/execution.py`. Execution is imported only by the composition root.

| Idea | Module and names |
| --- | --- |
| Queue advancement (Architecture Table 5) | `session_runner.py`: `advance_session`, `abort_session`, `dispose_action`, `ActionDisposition` and `SessionRunnerDependencies` |
| Running a queue to its end | `queue_runner.py`: `run_queue`, which repeats a cycle until nothing is left to run, the session ends or is suspended, or `stop` is set |
| Imaging queue | `imaging_queue.py`: `imaging_dependencies` builds the `SessionRunnerDependencies` of an imaging queue from `control` (`capture_entry`, `filter_name`) |
| Divergence recording | `divergence_recording.py`: `record_divergence`, and `UNCOMPARABLE_CAPABILITIES` (mount control, capture orchestration and safety) |
| Meridian flip | `meridian_flip.py`: `execute_meridian_flip` with `MeridianFlipSteps` |
| Fault recovery | `fault_recovery.py`: `attempt_recovery`, `recover_fault` |
| Guide-star loss | `guide_star_loss_recovery.py`: `attempt_guide_star_recovery`, producing a `GuideStarLossEvent` |
| Session recording | `session_recorder.py`: `ObservationSessionRecorder` |
| Reconciliation | `post_session_reconciliation.py`: `compute_calibration_stats`, `attach_target_session_ids`, `reconcile_session` |

`advance_session` runs the steps of the architecture's Table 5 in this order: the safety gate (`SafetyAssessment.permits_observing`, true only for `SAFE`), selection of the first `PENDING` entry whose `computed_start_time` has arrived, the readiness check on that entry's device states, the meridian check, disposition of each action, outcome capture, the status change and the checkpoint. A non-safe assessment sets the session to `SUSPENDED`. When every entry has a terminal status (`COMPLETED`, `FAILED` or `SKIPPED`) the session becomes `COMPLETED`. `dispose_action` issues an `AUTHORITATIVE` action, records a `SHADOWED` and comparable one through `record_divergence`, and does nothing for a `DELEGATED` one. Every hardware-facing step is a callable in `SessionRunnerDependencies`, so the module has no hardware import.

`imaging_dependencies` supplies only a slew to the entry's target (the `MOUNT_CONTROL` action) and a capture of each exposure request through `control.imaging.capture_image` with the package's dithering. It passes no device states (`required_device_states` returns an empty list), triggers no meridian flip (`check_meridian_crossing` returns `False` and `perform_meridian_flip` raises `ConflictError`), and builds no guiding or focus action. It captures calibration requests with the same call as light requests. The facade's `advance_session(session, deps=None, stop=None)` uses it when no `deps` is given. `execute_meridian_flip` runs six steps, and each of the two repeating steps is bounded by a `CorrectionConfig` setting (Table 5). `recover_fault` returns each device to `STANDBY`, re-enables it and reads the state back, and it calls its `escalate` callable when the attempts run out. `ObservationSessionRecorder.run` loads an existing session, polls the guiding service, appends the drained `GuidingSample` records and one `WeatherSample`, and saves the session at every interval. The weather sample reads `temperature` and `humidity` from the INDI driver status. `reconcile_session` recomputes `CalibrationStats` for the session's camera from every terminal session and attaches `target_session_ids` with `astrometricslib.derive_target_sessions`. It raises `ConflictError` if the session is not `COMPLETED` or `ABORTED`.

<!-- impl: wayfindinglib.tasks.execution_tasks.session_runner.SessionRunnerDependencies -->
<!-- impl: wayfindinglib.tasks.execution_tasks.session_runner.ActionDisposition -->
<!-- impl: wayfindinglib.tasks.execution_tasks.imaging_queue.imaging_dependencies -->
<!-- impl: wayfindinglib.tasks.execution_tasks.divergence_recording.UNCOMPARABLE_CAPABILITIES -->
<!-- impl: wayfindinglib.tasks.execution_tasks.meridian_flip.MeridianFlipSteps -->
<!-- impl: wayfindinglib.tasks.execution_tasks.fault_recovery.attempt_recovery -->
<!-- impl: wayfindinglib.models.session.guide_star_loss.GuideStarLossEvent -->
<!-- impl: wayfindinglib.tasks.execution_tasks.post_session_reconciliation.attach_target_session_ids -->
<!-- impl: wayfindinglib.models.policy.safety.SafetyAssessment.permits_observing -->

The facade also offers `execute_meridian_flip`, `recover_fault`, `recover_guide_star_loss`, `record_divergence`, `create_recorder` and `reconcile_session`. A caller may hand in the bundles of hardware steps (`SessionRunnerDependencies`, `MeridianFlipSteps`).

**Table 5.** Execution settings.

| Setting | Value | Where | Notes |
| --- | --- | --- | --- |
| Recovery attempts | 3 | `RecoveryPolicy.max_attempts` | |
| First wait before a retry | 30 s | `RecoveryPolicy.initial_interval_sec` | The first attempt has no wait. |
| Growth factor of the wait | 2.0 | `RecoveryPolicy.backoff_multiplier` | Waits are 0 s, 30 s and 60 s for three attempts (Architecture Equation (4)). |
| Alignment rounds after a flip, and in centering | 3 | `CorrectionConfig.alignment_iteration_limit` | |
| Guide-star re-acquisition attempts | 3 | `CorrectionConfig.guide_reacquire_attempts` | Used by the flip and by guide-star loss recovery. |
| Alignment convergence tolerance | 30 arcsec | `CorrectionConfig.alignment_convergence_tolerance_arcsec` | |
| Recording and save interval | 300 s | `snapshot_interval_seconds` of `ObservationSessionRecorder` | Data loss is bounded to one interval. |
| Wait between queue cycles while the next start is ahead | 5 s | `POLL_SECONDS` in `queue_runner.py` | |

## 6. Observatory control (Architecture Section 6)

### 6.1 Facade and children

`ObservatoryControl` in `api/control/control.py` holds the device drivers and seven children, each a subclass of `ControlChild` in `api/control/context.py`. All children share one `ControlContext`, which supplies the configuration, the Butler, the drivers, the delegation policy, the `SafetyMonitor` and the active equipment. Table 6 lists the children.

**Table 6.** The seven children of `ObservatoryControl`.

| Child | Module | Main calls |
| --- | --- | --- |
| `control.mount` | `api/control/mount.py` | `status`, `slew(destination, center=False, tolerance_arcsec, max_iterations)`, `sync`, `park`, `unpark`, `set_tracking`, `manual_move`, `abort_motion`, `set_slew_rate`, `compute_pointing_correction`, `run_polar_alignment_assist` |
| `control.imaging` | `api/control/imaging.py` | `status`, `capture_image(exposure, count, filter_name, dither)`, `set_filter`, `focus_move`, `compute_focus_correction`, `save_focus_model` |
| `control.guiding` | `api/control/guiding.py` | `status`, `run_loop`, `pulse`, `expose`, `get_image`, `drain_external_pulses`, `compute_correction`, `run_calibration`, `run_backlash_calibration`, `run_exposure_test`, `refit_spectrum`, `save_calibration`, `save_spectrum_analysis`, `save_run` |
| `control.remote` | `api/control/remote.py` | `list(kind, folder_name)`, `check_connection`, `frame_status`, `sync_frames`, `sync_logs` |
| `control.history` | `api/control/history.py` | `query(kind=...)`, `get_live_session_status`, `frame_guiding`, `get_performance_envelope`, `save_ekos_session_context` |
| `control.safety` | `api/control/safety.py` | `status`, `assess`, `save_rule_set`, `execute_safe_state`, `open_enclosure`, `close_enclosure`, `apply_promotion_decision`, `enter_monitoring_mode`, `enter_controller_mode` |
| `control.equipment` | `api/control/equipment.py` | `status`, `connect`, `disconnect`, `set_active_telescope`, `set_active_camera`, `set_device_property`, `cooling_ramp_rate`, `summarize_device`, `save_commissioning_run` |

The `kind` values of `control.history.query` are `capture`, `guiding`, `sky_coverage`, `recurring_issues`, `ekos_sessions`, `guiding_runs`, `pointing_model` and `alignment`. A reply is shrunk until it fits under a size limit, with a flag that says it was shrunk (`tasks/control_tasks/night_history.py`). Each status call takes `include`, the list of sections wanted. The `STATUS_SECTIONS` tuples name the sections.

The positions the mount reports are in the current-epoch frame (JNow). `hardware_operations.mount_position_to_icrs` converts them to ICRS (J2000), and `_mount_frame_position` converts the other way, so everything in the application compares positions in one frame.

<!-- impl: wayfindinglib.api.control.context.ControlContext -->
<!-- impl: wayfindinglib.api.control.context.ControlChild -->
<!-- impl: wayfindinglib.tasks.control_tasks.hardware_operations.mount_position_to_icrs -->
<!-- impl: wayfindinglib.tasks.control_tasks.night_history -->

### 6.2 Drivers

The per-device-type interfaces are in `drivers/interfaces/`: `MountDriver`, `CameraDriver`, `FocuserDriver`, `FilterWheelDriver`, `EnclosureDriver`, `SwitchDriver`, `WeatherDriver` and `GuidingDriver`, all subclasses of `ProtocolDriver` in `base_protocol_driver.py`. Their methods are `async`. `registry.py` builds a registry for each type that maps a protocol name to a driver class. The `ControlContext` builds one driver for each active device. The first implementation of the first seven is INDI, in `drivers/indi/` (`mount_driver.py` and its `mount_controller.py`, and the same pair for the camera, focuser, filter wheel, enclosure, switch and weather source), over the shared session in `drivers/indi_interface.py`. `drivers/simulators/indi_simulator.py` is a simulated INDI session for tests and demonstrations.

`RemoteTransferDriver` in `drivers/interfaces/remote_transfer_driver.py` is a separate abstract class. Its first implementation is `drivers/stellarmate_interface.py`, which copies files with `rsync` (`_run_rsync`, also used for the log downloads).

The guiding driver is chosen by `Telescope.guiding_protocol` through `build_guiding_driver_registry`: `phd2` (`drivers/phd2/guiding_driver.py`), `internal` (`drivers/indi/guiding_driver.py`, which reads the pulses the mount received and so gives pulse estimates) and `simulator` (`drivers/simulators/guiding_simulator.py`). Only the simulator can run the application's own guide loop. Pulse arithmetic and the RMS error are in `analytics/guide_pulses.py` (`pulse_ms_to_arcsec`, `arcsec_to_pulse_ms`, `merge_close_pulses`, `signed_pulses_ms`, `rms_arcsec`). `tasks/control_tasks/live_guiding.py` keeps the `LiveGuidingMonitor`, polls the driver for `control.guiding.status(include=["live"])`, and runs `control.guiding.run_loop`. A polled sample is stored with the source it came from. The loop's own samples are shown but not stored.

The slew limit checks are in the INDI mount controller (`validate_altitude_limits`, `validate_hour_angle_limits` in `drivers/indi/mount_controller.py`), and the mount driver calls them before a slew. The altitude range comes from the active `Telescope`, or from the `[Observatory.Constraints]` section when no telescope is configured. The hour-angle bound has no such fallback and is off without an active telescope.

<!-- impl: wayfindinglib.drivers.interfaces.registry.build_guiding_driver_registry -->
<!-- impl: wayfindinglib.drivers.indi_interface.IndiInterface -->
<!-- impl: wayfindinglib.drivers.simulators.indi_simulator.SimulatorIndiInterface -->
<!-- impl: wayfindinglib.drivers.stellarmate_interface.StellarMateInterface -->
<!-- impl: wayfindinglib.drivers.phd2.guiding_driver -->
<!-- impl: wayfindinglib.drivers.simulators.guiding_simulator -->
<!-- impl: wayfindinglib.analytics.guide_pulses.rms_arcsec -->
<!-- impl: wayfindinglib.tasks.control_tasks.live_guiding.LiveGuidingMonitor -->
<!-- impl: wayfindinglib.drivers.indi.mount_controller.MountController.validate_altitude_limits -->

### 6.3 Device summary state

`tasks/control_tasks/device_state_tasks.py` has `classify_device_state(is_present, is_connected, allow_commands, has_alert)`: not present gives `OFFLINE`, present and not connected gives `STANDBY`, an alert gives `FAULT`, commands withheld gives `DISABLED`, and anything else gives `ENABLED`. `summarize_device` builds a `DeviceState` and keeps the fault text only for a fault. `find_alert_detail` finds the first INDI property element in the `Alert` state. `control.equipment.summarize_device` exposes the classification. No code gathers the raw signals for every `DeviceRole`.

<!-- impl: wayfindinglib.tasks.control_tasks.device_state_tasks.summarize_device -->
<!-- impl: wayfindinglib.tasks.control_tasks.device_state_tasks.find_alert_detail -->

### 6.4 Corrections

| Idea | Module and names |
| --- | --- |
| Pointing (Equation (5)) | `pointing_correction.py`: `compute_pointing_correction`, with `angular_separation_arcsec` and `signed_offset_components_arcsec` from `astronomy/coordinate_transforms.py`. It returns a `PointingCorrection`. The model term uses `predict_pointing_error` in `analytics/pointing_model.py`. A model with `confidence` of `insufficient_data` is skipped. |
| Guiding (Equations (6) and (7)) | `guiding_correction.py`: `compute_guiding_correction`, returning a `GuidingCorrection`. `_signed_pulse_ms` is the control law, `_periodic_error_feedforward_ms` and `_backlash_feedforward_ms` are the feed-forward terms, and `_clamp_to_max_pulse` limits the sum. |
| Focus (Equations (8) and (9)) | `focus_correction.py`: `sample_focus_curve` and `compute_focus_correction`, returning a `FocusCorrection` made of `FocusCurvePoint` records. `numpy.polyfit` fits the parabola. |
| Guider calibration | `guider_calibration_tasks.py` derives a `GuiderCalibration` from a pulse run. `calibration_routines.py` has `run_guider_calibration`, `run_backlash_calibration` and `run_polar_alignment_assist`, built on `GuiderCalibrationSteps` and `BacklashCalibrationSteps`, which the caller supplies. |
| Focus model | `focus_model_tasks.py` derives the backlash, the thermal coefficient (by regression against temperature across sessions) and the per-filter offsets. |
| Centering | `centering.py`: `center_on`, behind `control.mount.slew(destination, center=True)`. It captures, plate solves with `astrometrics.stars.plate_solve`, calls `compute_pointing_correction`, syncs and slews again. It needs `MOUNT_CONTROL`, `PLATE_SOLVE_ALIGNMENT` and `CAPTURE_ORCHESTRATION`. `control.mount.abort_motion` stops it. |

The `GuidingSpectrumAnalysis` that feeds the guiding feed-forward is read by `ControlContext.active_guiding_spectrum_analysis`. The pointing model is not looked up automatically, because it belongs to one night. `analytics/pointing_model.py` fits the terms IH and ID (index errors), ME and MA (polar axis elevation and azimuth), CH (cone error) and TF (tube flexure) in `fit_pointing_model`.

`sample_focus_curve` approaches the sampled span from one side by `FocusModel.approach_direction` and runs up past the first position, so the move into the sweep never reverses.

<!-- impl: wayfindinglib.models.session.correction_result.PointingCorrection -->
<!-- impl: wayfindinglib.models.session.correction_result.GuidingCorrection -->
<!-- impl: wayfindinglib.models.session.correction_result.FocusCorrection -->
<!-- impl: wayfindinglib.tasks.control_tasks.guider_calibration_tasks -->
<!-- impl: wayfindinglib.tasks.control_tasks.focus_model_tasks -->
<!-- impl: wayfindinglib.tasks.control_tasks.calibration_routines.GuiderCalibrationSteps -->

**Table 7.** Correction settings, all in `CorrectionConfig` (`models/session/correction_config.py`).

| Setting | Value | Field | Notes |
| --- | --- | --- | --- |
| Guiding aggressiveness | 0.7 | `guiding_aggressiveness` | Between 0 and 1. |
| Guiding deadband | 0.15 arcsec | `guiding_deadband_arcsec` | |
| Longest guide pulse | 1000 ms | `guiding_max_pulse_ms` | |
| Guiding agreement tolerance | 0.5 arcsec | `guiding_divergence_tolerance_arcsec` | |
| Alignment agreement tolerance | 5 arcsec | `alignment_divergence_tolerance_arcsec` | |
| Focus samples | 9 | `focus_sample_count` | |
| Focus sweep span | 2000 focuser steps | `focus_sample_span_steps` | |
| Focus re-run temperature change | 1 °C | `focus_temperature_delta_trigger_c` | |
| Focus fit quality floor | 0.90 | `focus_fit_quality_floor` | |
| Focus agreement tolerance | 50 focuser steps | `focus_divergence_tolerance_steps` | |
| Guider calibration pulse | 3 s on each axis | `run_calibration` defaults | |
| Backlash probe | 1 s settling pulse, 0.05 s probing pulses, at most 40, star motion of 0.5 pixel counts as a response | `run_backlash_calibration` defaults | |
| Guide exposure test | 0.5, 1, 2 and 4 s, 8 frames at each | `run_exposure_test` defaults | |

### 6.5 Safety

`tasks/control_tasks/safety_monitor.py` holds `SafetyMonitor` and the rule evaluation. `_evaluate_rule` gives `UNKNOWN` for a missing or stale reading and applies `greater_than` or `less_than` against `unsafe_threshold`, and against `marginal_threshold` when it is set. An unrecognized comparison raises `ConfigurationError`. `_evaluate_rules` gives `UNKNOWN` for a missing or empty rule set, then for the first rule with an unknown reading, and otherwise takes the most severe of `SAFE`, `MARGINAL` and `UNSAFE`. `SafetyMonitor.evaluate` applies the hysteresis: a non-safe result is published at once and its time is kept, and a safe result is published only when `settling_period_sec` has passed since then. `SafetyAssessment.permits_observing` is true only for `SAFE`. `hardware_operations.refresh_safety_assessment` reads the weather driver and calls `assess_safety`. The INDI weather controller (`drivers/indi/weather_controller.py`) reads every element of the powerbox's `WEATHER_PARAMETERS` vector and stamps each with the time of the read.

Table 8 gives the safety settings.

**Table 8.** Safety and enclosure settings.

| Setting | Value | Where | Notes |
| --- | --- | --- | --- |
| Age bound of a reading | 120 s | `SafetyRule.staleness_bound_sec` | |
| Settling period before reopening | 900 s | `SafetyRuleSet.settling_period_sec` | |
| Clearance tolerance of the mount about the park position | 2° on each axis | `Enclosure.clearance_tolerance_deg` | |
| Enclosure motion timeout | 180 s | `Enclosure.motion_timeout_sec` | |

<!-- impl: wayfindinglib.tasks.control_tasks.safety_monitor.SafetyMonitor.evaluate -->
<!-- impl: wayfindinglib.drivers.indi.weather_controller.WeatherController -->

### 6.6 Enclosure, safe state and watchdog

`tasks/control_tasks/enclosure_control.py` holds pure checks: `mount_within_clearance`, `can_close_enclosure` (both axes within the tolerance of the park position), `can_leave_park` (true only for `OPEN`) and `check_motion_timeout` (returns `FAULT` when an opening or closing lasts past the timeout). `hardware_operations.close_enclosure` needs `OBSERVATORY_SAFETY`, reads the mount position and calls `can_close_enclosure`. It raises `ConflictError` when the mount is outside the clearance, and `HardwareError` when the position is unknown. `can_leave_park` and `check_motion_timeout` are not called by any command. The shutter commands themselves are in `drivers/indi/enclosure_controller.py`.

`tasks/control_tasks/safe_state.py` has `execute(trigger, steps)`, with the steps in `SafeStateSteps` (`abandon_exposure`, `stop_guiding`, `park_mount`, `get_mount_position`, `close_enclosure`, `warm_sensor`, `close_session`, `enclosure`). It tries every step, treats an exception as a failure, and skips the enclosure when the mount did not park or sits outside the clearance. It returns a `SafeStateOutcome` with a flag for each step and `failed_step`, the first failure. The trigger is a string such as `unsafe_verdict`, `recovery_exhausted` or `watchdog`. `control.safety.execute_safe_state` runs it. No default `SafeStateSteps` is built in the library.

`tasks/control_tasks/cooling_control.py` holds the cooling arithmetic: `effective_ramp_rate_c_per_min`, `compute_ramped_setpoint`, `is_settled` and `has_settle_timed_out`, over `CoolingPolicy`. `control.equipment.cooling_ramp_rate` exposes the rate. No code runs the polling loop that applies the setpoints.

`watchdog/heartbeat.py` has `write_heartbeat`, `read_heartbeat` and `heartbeat_is_stale`. The heartbeat is a file that holds `time.monotonic()`, which is comparable across processes on one machine. A missing or unreadable file is stale. `watchdog/watchdog_process.py` has `check_and_escalate` and `run_watchdog_loop`, which call `safe_state.execute("watchdog", steps)` when the heartbeat is stale. The watchdog imports only the safe-state module and its models, and it is left out of `Wayfinder`. Nothing in the library calls `write_heartbeat` or starts the loop, and `watchdog_timeout_sec` has no stored default.

**Table 9.** Cooling settings. `CoolingPolicy` is in `models/equipment_and_site/equipment.py`.

| Setting | Value | Where | Notes |
| --- | --- | --- | --- |
| Target temperature | -10 °C | `CoolingPolicy.target_temp_c` | |
| Ramp rate | 2 °C/min | `CoolingPolicy.ramp_c_per_min` | The camera's own limit applies if lower. |
| Settled tolerance | 0.5 °C | `CoolingPolicy.settle_tolerance_c` | |
| Settle timeout | 900 s | `CoolingPolicy.settle_timeout_sec` | |
| Camera default cooling floor and ramp limit | -30 °C and 2 °C/min | `Camera.min_cooling_temp_c`, `max_cooling_ramp_c_per_min` | |

<!-- impl: wayfindinglib.tasks.control_tasks.enclosure_control.mount_within_clearance -->
<!-- impl: wayfindinglib.tasks.control_tasks.cooling_control.effective_ramp_rate_c_per_min -->
<!-- impl: wayfindinglib.tasks.control_tasks.cooling_control.is_settled -->
<!-- impl: wayfindinglib.watchdog.heartbeat.write_heartbeat -->
<!-- impl: wayfindinglib.watchdog.watchdog_process.run_watchdog_loop -->
<!-- impl: wayfindinglib.drivers.indi.enclosure_controller -->
<!-- impl: wayfindinglib.models.equipment_and_site.equipment.CoolingPolicy -->

### 6.7 Mount-mechanical models and bulk modes

`tasks/control_tasks/guiding_log_ingestion.py` chains download, parse, analyze and store for guide logs (`ingest_guide_log_file`, `fetch_and_ingest_new_guide_logs`) and persists the refit periodic-error and backlash analysis as standing, cross-night mount state. `pointing_log_ingestion.py` fits and exposes a session-scoped pointing model from the local alignment attempts and never stores it. The bulk modes are in Section 2.3.

`analyze_guiding_telemetry` in `analytics/guiding_spectrum.py` splits the samples into continuous stretches at every pause of `SEGMENT_BREAK_SECONDS`, analyzes each with its true time stamps (Lomb-Scargle), and combines them. A stretch counts toward a period only if it holds `MINIMUM_CYCLES` full cycles of it. Peaks slower than `_LONGEST_MECHANICAL_PERIOD_SECONDS` are labeled slow drift and are never the dominant period. The error it analyzes is the offset left after the guider's corrections, not the mount's unguided periodic error.

<!-- impl: wayfindinglib.tasks.control_tasks.guiding_log_ingestion.fetch_and_ingest_new_guide_logs -->
<!-- impl: wayfindinglib.analytics.guiding_spectrum.SEGMENT_BREAK_SECONDS -->
<!-- impl: wayfindinglib.analytics.guiding_spectrum.MINIMUM_CYCLES -->

### 6.8 Incumbent logs and the performance envelope

`tasks/control_tasks/ekos_log_ingestion.py` downloads the guide logs and analyze logs from the telescope computer, stores the guiding samples, stores one session record for each analyze file, and ties each session to its equipment (`attribute_session_equipment`). Its entry point on the facade is `control.remote.sync_logs`. The parsers are `drivers/phd2/guide_log_parser.py` (PHD2-format logs, which Ekos also writes) and `drivers/ekos/analyze_log_parser.py`. Both downloads use `StellarMateInterface._run_rsync`, the same runner as the image downloads. `ControlRecordStore.replace_guiding_samples` stores the samples, each with a `source` from `GuidingSampleSource` in `models/session/telemetry.py`. `MEASURED_GUIDING_SAMPLE_SOURCES` lists the sources a refit may read. The session record is `EkosSessionContext`, stored under the `ekos_session_context` dataset type. After each copy of frames, `tasks/control_tasks/frame_pointing.py` (`record_frame_pointing_errors`) records the pointing error of every new plate-solved frame as an alignment attempt, using the frame's `CRVAL1` and `CRVAL2` against `OBJCTRA` and `OBJCTDEC` (or `RA` and `DEC`). `control.remote.list(kind, folder_name=...)` exposes the one folder-name matcher (`matching_remote_folder`).

`build_equipment_fingerprint` in `models/equipment_and_site/equipment_fingerprint.py` names a setup. Names are compared after case, spaces and punctuation are removed, and the guide optics are identified by their rounded numbers. `derive_performance_envelope` in `analytics/performance_envelope.py` is the pure derivation (Architecture Equations (10) to (12)). `tasks/control_tasks/performance_envelope_tasks.py` gathers its inputs: the camera profile, the star width measured from the equipment's own frames, and the baseline from earlier sessions of the same fingerprint. The facade entry point is `control.history.get_performance_envelope`. The guide camera and guide scope that feed it are configured in `astrometrics.config.toml` (`[Observatory.GuideScope.*]`, and `default_guide_camera` or `default_secondary_camera` under `[Observatory.Camera]`). `SIGMA_TO_FWHM` (2.3548), `SIDEREAL_RATE_ARCSEC_PER_SECOND` (15.041) and `MAD_TO_SIGMA` (1.4826) are exact or standard constants. `analytics/tracking_risk.py` (`build_tracking_risk_map`) builds the tracking-risk grid that the envelope carries as `tracking_risk`.

<!-- impl: wayfindinglib.tasks.control_tasks.ekos_log_ingestion.attribute_session_equipment -->
<!-- impl: wayfindinglib.models.session.telemetry.MEASURED_GUIDING_SAMPLE_SOURCES -->
<!-- impl: wayfindinglib.tasks.control_tasks.frame_pointing.record_frame_pointing_errors -->
<!-- impl: wayfindinglib.analytics.performance_envelope.SIGMA_TO_FWHM -->
<!-- impl: wayfindinglib.tasks.control_tasks.performance_envelope_tasks.tracking_risk_map -->

### 6.9 Session-quality analysis

`session_analysis/` holds the analyses. `pipeline_base.py` gives the shared interface (`SessionAnalysisPipeline` and `SessionAnalysisRequest`), with `process_input` (pre-processing), `run` (processing), `validate_output` (post-processing) and `to_result_dict`. Every step is a pure function of the request.

| Analysis | Modules | Result classes and entry |
| --- | --- | --- |
| Guiding | `guiding/` with `pre_processing/assess_guiding_input_quality.py`, `processing/measure_guiding_performance.py`, `post_processing/recommend_for_guiding.py` and `pipeline.py` | `GuidingSessionAnalysis`, `GuidingInputQuality`, `GuidingPerformance` and `Recommendation` in `models/session/session_quality.py`. `session_analysis_tasks.py` gathers a night's data, `night_analysis.py` runs the analysis, and the entry point is `control.history.query(kind="guiding")`. Per-run facts are `GuidingRunSummary` (`models/session/guiding_run.py`, dataset type `guiding_run`), written during ingestion. |
| Capture | `capture/` with `pre_processing/assess_capture_input_quality.py`, `processing/measure_capture_performance.py`, `post_processing/recommend_for_capture.py` and `pipeline.py` | `CaptureSessionAnalysis`, `CaptureInputQuality`, `CapturePerformance` and `ExposureClipping` in `models/session/capture_quality.py`, with `CaptureFrame` and `StackSaturationVerdict` as input (`capture_frame.py`). `capture_analysis_tasks.py` reads the equipment's light frames and the stacking summaries' saturation verdicts from `astrometricslib`. The entry point is `control.history.query(kind="capture")`. |
| Exposure length | `guiding/processing/measure_exposure_feasibility.py` | `ExposureFeasibility` records on `GuidingPerformance`, with lengths from `capture_analysis_tasks.exposure_lengths_in_use`. The recommendation is `RecommendationKind.EXPOSURE_LENGTH_LIMITED`. |
| Guide-star brightness | `guiding/pre_processing/assess_guiding_input_quality.py` | The guide log's star mass is stored for each sample (`guiding_samples.star_mass`). `collect_baseline_values` adds a `guide_star_mass` series, the envelope derives `guide_star_mass_low_limit`, and `GuidingInputQuality` carries `medianStarMass`, `starMassLimit`, `typicalStarMass`, `hasDimStar` and `typicalCadenceSeconds`. The `check_guide_signal` warning says how many times fainter the star was and how much a shorter guide cycle could explain. |
| Guide exposure test | `analytics/guide_exposure_ladder.py` (`measure_star`, `position_noise_pixels`, `analyze_guide_exposure_ladder`) and `tasks/control_tasks/guide_exposure_ladder_tasks.py` | `GuideExposureLadder` in `models/session/guide_exposure_ladder.py`, run by `control.guiding.run_exposure_test`. It has been tested against a simulated camera and has not been run against the real guide camera. |
| Sky position | `sky/` with `sky_bins.py`, `pre_processing/assess_sky_input_quality.py`, `processing/measure_sky_performance.py`, `post_processing/recommend_for_sky.py` and `pipeline.py` | `SkyAnalysis` and `SkySample` in `models/session/sky_quality.py`, `sky_analysis_tasks.py` to gather samples, and `control.history.query(kind="sky_coverage")`. |
| Recurring issues | `recurring_issues.py` | `RecurringIssue` in `models/session/recurring_issue.py`, and `control.history.query(kind="recurring_issues")`. |
| Live status | `live_status.py` and `tasks/control_tasks/live_session_status_task.py` | `control.history.get_live_session_status`. It downloads the newest Ekos analyze log and KStars text log and judges them with pure functions. It stores nothing. |
| Frame and guide error match | `tasks/control_tasks/frame_guiding.py` | `control.history.frame_guiding` cuts the guide-log samples to each light frame's exposure window. Only guide-log samples are used. |

The envelope takes a `before_night` argument so each night is judged against earlier nights only. The capture analysis imports from `astrometricslib` only its top-level names: `SATURATED_BLOB_MINIMUM_PIXELS`, `SATURATED_FRAME_FRACTION` and `DEFAULT_DARK_TEMPERATURE_TOLERANCE_C`, which are the science side's own definitions. Frames whose file name contains `processed` are left out as a second line of defense. The science side's frame scan skips stacked outputs itself (`is_stacked_output`). The frame library and the stacking summaries are read once for each call through `night_analysis.capture_library`.

<!-- impl: wayfindinglib.session_analysis.pipeline_base.SessionAnalysisRequest -->
<!-- impl: wayfindinglib.session_analysis.guiding.processing.measure_exposure_feasibility.LOST_STAR_CYCLES -->
<!-- impl: wayfindinglib.models.session.session_quality.GuidingSessionAnalysis -->
<!-- impl: wayfindinglib.models.session.capture_quality.CaptureSessionAnalysis -->
<!-- impl: wayfindinglib.models.session.sky_quality.SkyAnalysis -->
<!-- impl: wayfindinglib.models.session.recurring_issue.RecurringIssue -->
<!-- impl: wayfindinglib.models.session.guide_exposure_ladder.GuideExposureLadder -->
<!-- impl: wayfindinglib.tasks.control_tasks.night_analysis -->
<!-- impl: wayfindinglib.tasks.control_tasks.live_session_status_task -->
<!-- impl: wayfindinglib.tasks.control_tasks.frame_guiding -->
<!-- impl: wayfindinglib.tasks.control_tasks.alignment_history -->

`tasks/control_tasks/alignment_history.py` answers `control.history.query(kind="alignment")` with a circular mean of right ascension for each night.

**Table 10.** Settings of the performance envelope and the session-quality analyses. The Notes column gives the reasoning behind each value and any observation that informed it.

| Setting | Value | Where | Notes |
| --- | --- | --- | --- |
| Blur tolerance $\phi$ | 0.10 | `DEFAULT_BLUR_TOLERANCE_FRACTION` | Policy choice. On this observatory's measured 5.6 arcsec star width it gives 1.09 arcsec per axis, inside the 0.8 to 1.4 arcsec its 19 guide logs show on good nights. |
| Guide cycles an exposure must span | 3 | `MINIMUM_GUIDE_CYCLES_PER_EXPOSURE` | With the measured 3.2 s guide cycle it keeps exposures of 9.6 s or longer. Without the cut, bright single stars at 1 s to 4 s read 10 to 16 arcsec wide and moved the median from 5.6 to 8.0 arcsec. |
| Baseline spread multiplier $k$ | 3 | `BASELINE_SPREAD_MULTIPLIER` | Convention for an outlier (about 1 in 740 by chance for normal data). |
| Fewest frames behind a star width | 20 | `MINIMUM_IMAGE_QUALITY_SAMPLES` | About one night of one target. |
| Fewest sessions behind a baseline | 5 | `MINIMUM_BASELINE_SESSIONS` | A robust spread is not stable below about five values. |
| Fewest samples for a session to count | 100 | `MINIMUM_SAMPLES_PER_SESSION` | About six minutes of guiding. |
| Guide-scale agreement | 2% | `GUIDE_SCALE_AGREEMENT_FRACTION` | Above the rounding in a log, below any real change of optics. |
| Excursion size | the larger of 5 times the guiding-error limit and one guide pixel | `EXCURSION_SIGMA_MULTIPLE` | A 5σ event happens about once in 1.7 million samples by chance for Gaussian noise. The pixel floor is the guider's resolution. |
| Percentile for fraction limits | 90th (nearest rank) | `BASELINE_FRACTION_PERCENTILE` | Lost and excursion fractions are zero on most nights, which makes a median plus spread degenerate. |
| Shortest run for a net Dec correction rate | 600 s | `MINIMUM_RUN_SECONDS_FOR_CORRECTION_RATE` | Longer than one worm-gear period on mounts of this class (8 to 10 min). Not measured on this mount. |
| Fewest samples for a run's own error | 20 | `MINIMUM_SAMPLES_PER_RUN` | A robust spread is not stable with fewer. |
| Brightness limit for a guide star | median of earlier nights times exp(-3 times the robust spread of the logarithms) | `guide_star_mass_low_limit` | Same rule as the SNR limit. On this observatory's real nights it flags 2026-05-01 and 2026-09-21, 23 and 24. |
| Saturation share for a guide star | 0.8 of the ceiling | `SATURATION_FRACTION` | The science side's 80% rule. |
| Fewest frames for a position noise | 5 | `MINIMUM_FRAMES` | The second difference needs three positions. |
| Pause that ends a guiding stretch | 60 s | `SEGMENT_BREAK_SECONDS` | The guide cycle is 2 to 6 s, so a minute is a stop. |
| Cycles a stretch needs to count toward a period | 2 | `MINIMUM_CYCLES` | With fewer, a period cannot be told from a trend. |
| Longest period attributed to the gears | 650 s | `_LONGEST_MECHANICAL_PERIOD_SECONDS` | The worm gear turns once in 8 to 10 min on mounts of this class. Not measured on this mount. |
| Gap that means a lost star | 4 guide cycles | `LOST_STAR_CYCLES` | One missed frame (two cycles) is normal guiding, three in a row is a recovery. |
| Clean share at which an exposure length is reliable | 0.5 | `RELIABLE_CLEAN_FRACTION` | Below it, more exposures are ruined than kept. |
| Start-time step for the exposure-length view | 0.25 of the exposure | `_STRIDE_FRACTION` | |
| Fewest nights a part of the sky needs to be judged | 3 | `MINIMUM_NIGHTS_PER_BIN` | The smallest number whose middle value is not an extreme. |
| Fewest measurements a night needs, and a part of the sky needs | star width and roundness: 10 and 5; guiding error: 3 and 1 | `METRIC_SPECS` | A night's frames number in the tens. A night has only a few guiding runs, each a separate calibration. |
| Poor part of the sky | at least the blur tolerance worse, and z of at least 3 | `BASELINE_SPREAD_MULTIPLIER` | The 10% is the envelope's policy choice and 3 is the outlier convention. On this observatory's real data no part reached it. |
| Fewest measured frames for a night's star quality | 10 | `MINIMUM_FRAMES_PER_NIGHT` | The median of 10 frames has a standard error near 40% of one frame's scatter. |
| Fewest exposures for a night's cancelled share | 20 | `MINIMUM_ATTEMPTS_PER_NIGHT` | Below 20, one cancellation moves the share by 5% or more. |
| Window for matching an Ekos exposure to a frame | 30 s | `MATCH_WINDOW_SECONDS` | Matched pairs start within about 2 s, a few up to about 10 s. 30 s covers them and is shorter than the gap between most exposures. |
| Smallest saturated blob that is a star | 4 pixels | `SATURATED_BLOB_MINIMUM_PIXELS` (science side) | Set by the science side on this observatory's frames (ASI533 cores of 9 to 1,700 pixels, D5300 hot pixels of 1 pixel). The capture analysis counts saturated pixels, not connected blobs. It agreed with the science side's verdict for 67 of 69 exposure groups, and the two that differed are Moon frames. |
| Share of frames that makes an exposure length clipped | 0.5 | `SATURATED_FRAME_FRACTION` (science side) | One clipped frame in a group can be a cosmic ray or a satellite streak. |
| Dark-frame temperature tolerance | 3 °C | `DEFAULT_DARK_TEMPERATURE_TOLERANCE_C` (science side) | Taken from Arcturus darks, which clustered near 0 °C and -10 °C with nothing between. |

**Table 11.** Settings of the tracking-risk map in `analytics/tracking_risk.py`.

| Setting | Value | Notes |
| --- | --- | --- |
| Prior west of the meridian | rises by up to 0.10 by an hour angle of 75° | A mount balanced east-heavy can let its gears float. |
| Prior near the pole | rises by up to 0.08 above declination 75° | The declination bearing can stick. |
| Prior near the horizon | rises by up to 0.40 at the horizon, 1 below it, from 20° of altitude | The air blurs stars and the load arm is long. |
| Jitter that is safe, and that trails | below 0.75 pixel, and above 1.25 pixels | Scatter of a target's repeated plate solves against the pixel scale. |
| Spread of a target's pull | 25°, Gaussian | |
| Solves at full weight | 30 | |
| Largest measured share of a score | 85% | |

## 7. Verification (Architecture Section 7)

### 7.1 Structural checks

The five checks, and where each lives:

| Check | Static (import-linter contract in `pyproject.toml`) | Runtime (fresh-process test) |
| --- | --- | --- |
| Layering conformance | "wayfindinglib Foundation imports nothing above it", "wayfindinglib Control never imports Planning", "wayfindinglib Planning never imports Control", "wayfindinglib models are plain schemas that import no Foundation behavior" | None |
| Execution is optional | "wayfindinglib Execution is imported only by the composition root" | `test_control_and_planning_work_without_loading_execution` in `wayfindinglib/test/test_layer_independence.py` |
| Safety runs without execution | None | `test_safety_runs_without_loading_execution` in `wayfindinglib/test/test_layer_independence.py` |
| Planning is hardware-free | "wayfindinglib Planning is hardware-free, even indirectly" | `test_planning_module_tree_imports_no_device_driver` in `wayfindinglib/api/test/test_planning.py` |
| Cross-library dependency | "astrometricslib never imports wayfindinglib or backend" | None |

The runtime tests share `wayfindinglib/test/import_probe.py`, which runs a code snippet in a new interpreter with an isolated configuration (one telescope, one camera) and returns the `wayfindinglib` modules in its `sys.modules`. Run the static checks with `.venv/bin/lint-imports` from the repository root. The contracts set `exclude_type_checking_imports = true`.

<!-- impl: wayfindinglib.test.import_probe.wayfindinglib_modules_loaded_by -->

### 7.2 Promotion gates

The gate criteria of the architecture's Table 8 are an operator procedure. No module applies a gate. `DivergenceEvidenceSummary.meets_phase_gate(minimum_sample_count, minimum_agreement_rate)` tests a summary against thresholds the caller passes in. `CommissioningRun` records drill evidence. Table 12 gives the criteria with their counts. Every value is a design estimate chosen to be conservative, because no body of past divergence measurements exists.

**Table 12.** Delegation phase promotion gates with their values.

| Entering phase | Gate |
| --- | --- |
| 1 | Mount slew, park, unpark and tracking commanded through this system over at least 2 nights and at least 20 commands with the incumbent disengaged from the mount. The envelope check rejects an out-of-envelope command on real hardware. Every device reports a `DeviceSummaryState`. Zero unintended motion events. |
| 2 | A `GuiderCalibration` and a `FocusModel` exist for the active pairing. All three correction functions run on every real night without issuing commands, shown by a test with a stand-in control layer and by zero commanded motion attributable to this system. |
| 3 | Over at least 5 nights: alignment and guiding each at least 500 paired comparisons, focus at least 30 paired sweeps. Agreement rate at least 95% for each capability. Mean signed divergence within half the tolerance. Zero sign or axis inversions. Alignment converging within its iteration limit on at least 95% of attempts. The comparison counts approximate one night of guiding at a typical cadence taken five times, and focus sweeps at a typical trigger rate. |
| 4 | Over at least 5 nights with corrections authoritative: guiding RMS not worse than the Phase 2 delegated baseline by more than 20%. Zero unrecovered guide-star losses. At least 10 meridian flips completed and resumed with zero unrecovered. Cooling reaching and holding its target on every session. |
| 5 | A hardware enclosure interlock independent of this system is installed and verified. The safety monitor gives correct verdicts for induced stale, absent and unsafe readings. The enclosure opens and closes under the interlock over at least 20 cycles with zero interlock violations. `safe_state.execute` is verified end to end from each of its three triggers (unsafe verdict, recovery exhaustion, watchdog expiry), including with the controlling process killed. Fault recovery is exercised against an induced device fault. |

## 8. Known gaps (Architecture Section 8)

This section lists where the code does not yet match a statement of the architecture. The architecture's Section 8 states the same gaps in words.

- The imaging queue wires only a slew and a capture (Section 5).
- No code builds a default `SafeStateSteps`, `MeridianFlipSteps`, `GuiderCalibrationSteps` or `BacklashCalibrationSteps`.
- No code calls `write_heartbeat` or starts `run_watchdog_loop` (Section 6.6).
- `can_leave_park` and `check_motion_timeout` are not called by any command (Section 6.6).
- `hardware_operations.observer_location` and the slew limit check read the position from the mount (Section 3).
- `classify_device_state` has no caller that gathers signals for every `DeviceRole` (Section 6.3).
- `control.guiding.run_loop` runs only with the simulator (Section 6.2).
- `INDI_PROTOCOL` is the only protocol name for the mount, camera, focuser and filter wheel (`models/equipment_and_site/driver_protocols.py`).
- The variable-star count of `ScienceOutcomeSummary` is always 0 (Section 4).
- `visibility_state_at` does not apply `max_hour_angle_hours` (Section 4).

## Appendix A. Correspondence with KStars and Ekos

Table 13 maps the desktop modules of KStars and Ekos [1] onto the calls of the facade (`Wayfinder.control`, `.planning` and `.execution`).

**Table 13.** Ekos module to library call mapping.

| Ekos module | Library calls | Notes |
| --- | --- | --- |
| Mount | `control.mount.slew`, `park`, `unpark`, `set_tracking` | `hardware_operations.py` |
| Align | `control.mount.compute_pointing_correction`, `control.mount.slew(center=True)` | `pointing_correction.py`, `centering.py` |
| Focus | `control.imaging.compute_focus_correction` | `focus_correction.py`. The library has no call that runs an autofocus sweep on its own. |
| Guide | `control.guiding.compute_correction`, `control.guiding.status(include=["live"])`, `control.imaging.capture_image(dither=...)` | `guiding_correction.py`, `live_guiding.py` |
| Scheduler | `planning.create_plan(kind="scheduled_session")` | `scheduling.py` |
| Capture | `planning.create_plan(kind="package")` | `models/planning/observation_package.py` |
| Dome and weather interlock | `control.safety.execute_safe_state`, `control.safety.assess` | `safe_state.py`, `safety_monitor.py` |
| Session recording and analysis | `execution.advance_session`, `execution.create_recorder`, `control.history.query` | `session_runner.py`, `session_recorder.py`, `night_analysis.py` |

## References

<!-- vale off -->

[1] J. Mutlaq et al., "KStars: Free, open source, cross-platform astronomy software," KDE Project. Accessed: Oct. 15, 2026. [Online]. Available: <https://edu.kde.org/kstars>

<!-- vale on -->
