# Wayfinding library implementation

*Version 2.2 · 2026-10-24 · Status: current*

## Abstract

This paper maps the design in `Wayfinding_Library_Architecture.md` to the Python source in `wayfindinglib/`. It names the layers of the package and the rule that keeps them apart, the model classes that hold each record, and the modules that carry out planning, execution and control. It lists the settings the code uses, with their values. It also names the tests and import contracts that check the structure, and it states which parts of the design have code behind them and which do not yet. Evidence for the settings is in the validation register, and each setting here carries its register identifier.

Terms are defined in the [glossary](./../Glossary.md). This document also defines each term the first time it appears.

## 1. Scope

This paper is an index from the theory to the code. Theory stays in [Wayfinding_Library_Architecture.md](./Wayfinding_Library_Architecture.md). Module paths are relative to `wayfindinglib/` unless a path starts with another folder.

The one supported entry point is the package root, which loads its names on first use so that importing the library loads no hardware code. It offers `Wayfinder` with three sub-APIs, `control`, `planning` and `execution`, their models, `group_alignment_attempts` and the error class.

<!-- impl: wayfindinglib.Wayfinder -->
<!-- impl: wayfindinglib.api.control.control.ObservatoryControl -->
<!-- impl: wayfindinglib.api.planning.ObservationPlanning -->
<!-- impl: wayfindinglib.api.execution.ObservationExecution -->
<!-- impl: wayfindinglib.analytics.alignment_sessions.group_alignment_attempts -->

## 2. System architecture

### 2.1 Layers and import contracts

Table 1 maps Figure 1 of the architecture paper onto folders.

**Table 1.** Layers and their folders.

| Layer | Folders and modules |
| --- | --- |
| Foundation | `models/`, `astronomy/`, `data_access/`, `analytics/`, `drivers/butler.py`, `drivers/control_record_store.py`, `drivers/catalog/` |
| Control | `tasks/control_tasks/`, `session_analysis/`, `watchdog/`, `api/control/`, and the hardware drivers (`drivers/`, except those above) |
| Planning | `tasks/planning_tasks/`, `api/planning.py` |
| Execution | `tasks/execution_tasks/`, `api/execution.py` |
| Composition root | `wayfindinglib/__init__.py`, `api/__init__.py` |

Shared definitions in the foundation: protocol names (`models/equipment_and_site/driver_protocols.py`, checked against the driver registry keys by `test_protocol_names_match_the_driver_registry_keys`), `SensorReadings` (`models/policy/safety.py`) and `group_alignment_attempts` (`analytics/alignment_sessions.py`).

<!-- impl: wayfindinglib.models.equipment_and_site.driver_protocols.MOUNT_PROTOCOLS -->
<!-- impl: wayfindinglib.drivers.interfaces.registry.build_mount_driver_registry -->
<!-- impl: wayfindinglib.models.policy.safety.SensorReadings -->
<!-- impl: wayfindinglib.models.session.telemetry.AlignmentTargetSession -->

### 2.2 Precedent and divergence from Rubin (Architecture Section 2.2)

`DeviceSummaryState` in `models/policy/device_state.py` holds the five states (`OFFLINE`, `STANDBY`, `DISABLED`, `ENABLED`, `FAULT`).

<!-- impl: wayfindinglib.models.policy.device_state.DeviceSummaryState -->

### 2.3 Capability delegation (Architecture Section 2.3)

`models/policy/delegation.py` holds `ObservatoryCapability` (the six capabilities of Table 2), `DelegationState` (`DELEGATED`, `SHADOWED`, `AUTHORITATIVE`), `DelegationPolicy` and `CapabilityDelegation`. `data_access/delegation_policy_reader.py` enforces the rules.

`validate_delegation_policy` raises `DelegationPolicyValidationError` for a shadowed safety capability, early capture orchestration, or missing calibration. `promote_capability` forces shadowing first.

Each command in `tasks/control_tasks/hardware_operations.py` starts with `_require_authoritative`, which raises `PermissionDeniedError` unless its capability is `AUTHORITATIVE`. Table 2 lists them.

**Table 2.** Capability required by each hardware command.

| Capability | Commands |
| --- | --- |
| `MOUNT_CONTROL` | `slew`, `park`, `unpark`, `set_tracking`, `manual_move`, `abort_motion`, `set_slew_rate` |
| `PLATE_SOLVE_ALIGNMENT` | `sync_mount` |
| `AUTOGUIDING` | `pulse`, `guide_expose` |
| `AUTOFOCUS` | `focus_move` |
| `CAPTURE_ORCHESTRATION` | `set_filter`, `capture_image` |
| `OBSERVATORY_SAFETY` | `open_enclosure`, `close_enclosure` |

`tasks/control_tasks/capability_promotion.py` holds `apply_promotion_decision`, `summarize_divergence_evidence` (Architecture Equation (2)) and `set_all_capabilities`.

<!-- impl: wayfindinglib.models.policy.delegation.CapabilityDelegation -->
<!-- impl: wayfindinglib.data_access.delegation_policy_reader.DelegationPolicyValidationError -->
<!-- impl: wayfindinglib.data_access.delegation_policy_reader.get_delegation_policy -->
<!-- impl: wayfindinglib.tasks.control_tasks.capability_promotion.apply_promotion_decision -->
<!-- impl: wayfindinglib.tasks.control_tasks.capability_promotion.set_all_capabilities -->
<!-- impl: wayfindinglib.api.control.safety.SafetyControl -->

### 2.4 Delegation phases (Architecture Section 2.4)

The phase is the combination of states `DelegationPolicy` holds, not a type. `CommissioningRun` (`models/policy/commissioning.py`) records phase 0 to 5 evidence as `CommissioningObservation` records. `control.equipment.save_commissioning_run` stores it.

<!-- impl: wayfindinglib.models.policy.commissioning.CommissioningObservation -->
<!-- impl: wayfindinglib.api.control.equipment.EquipmentControl.save_commissioning_run -->

### 2.5 Domain models and operations (Architecture Section 2.5)

Table 3 maps the architecture's Table 2 records to pydantic classes.

**Table 3.** Records of the architecture paper and the classes that hold them.

| Architecture record | Classes | Module (under `models/`) |
| --- | --- | --- |
| Telescope, camera, guide optics, catalog | `Telescope`, `Camera`, `GuideScope`, `EquipmentCatalog` | `equipment_and_site/equipment.py` |
| Site profile | `SiteProfile`, `AvoidanceZone` | `equipment_and_site/site_profile.py` |
| Enclosure specification | `Enclosure`, `EnclosureState` | `equipment_and_site/enclosure.py` |
| Guider calibration; focus model | `GuiderCalibration`; `FocusModel` | `equipment_and_site/guider_calibration.py`, `focus_model.py` |
| Calibration inventory and advisory | `CalibrationEntry`, `CalibrationAdvisory` | `equipment_and_site/calibration.py` |
| Performance envelope | `PerformanceEnvelope`, `QualityLimit` (from `astrometricslib`) | `equipment_and_site/performance_envelope.py` |
| Delegation policy; commissioning run | `DelegationPolicy`, `CapabilityDelegation`; `CommissioningRun` | `policy/delegation.py`, `commissioning.py` |
| Safety rules and assessment | `SafetyRule`, `SafetyRuleSet`, `SafetyAssessment`, `SafetyVerdict` | `policy/safety.py` |
| Recovery policy and fault record | `RecoveryPolicy`, `RecoveryAttempt`, `FaultRecord` | `policy/recovery.py` |
| Device summary state; correction settings | `DeviceState`, `DeviceRole`, `DeviceSummaryState`; `CorrectionConfig` | `policy/device_state.py`, `session/correction_config.py` |
| Observation package; mosaic | `ObservationPackage`, `ExposureRequest`, `DitherConfig`; `MosaicPlan` | `planning/observation_package.py`, `mosaic.py` |
| Target dataset report | `TargetDatasetReport`, `ScienceOutcomeSummary`, `ContextReading`, `StartingPoint`, `ForecastCheck` | `planning/target_report.py` |
| Tonight's ranking | `TonightRanking`, `RankedList`, `RankedCandidate`, `TonightSkyModel` | `planning/tonight_ranking.py` |
| Night review | `NightQuality`, `NightTargetQuality`, `GuidingNightSummary` | `session/night_quality.py` |
| Session, queue entry, diagnostic | `ObservationSession`, `QueuedObservationPackage`, `InfeasibilityReason`, `SessionStatus`, `QueueRequest` | `session/observation_session.py` |
| Divergence record; session record | `DivergenceRecord`; `EkosSessionContext` | `session/divergence.py`, `ekos_session.py` |
| Telemetry | `GuidingSample`, `WeatherSample`, `MountPointingModel`, `GuidingSpectrumAnalysis` | `session/telemetry.py` |
| Safe-state and flip outcomes | `SafeStateOutcome`, `MeridianFlipOutcome`, `GuideStarLossEvent` | `session/safe_state.py`, `meridian_flip.py`, `guide_star_loss.py` |

The architecture's Table 3 operations are exposed by `ObservatoryControl` (Section 6.1), `ObservationPlanning` (Section 4) and `ObservationExecution` (Section 5).

<!-- impl: wayfindinglib.models.equipment_and_site.equipment.Camera -->
<!-- impl: wayfindinglib.models.session.divergence.DivergenceRecord -->
<!-- impl: wayfindinglib.models.session.ekos_session.EkosSessionContext -->
<!-- impl: wayfindinglib.models.policy.device_state.DeviceState -->

## 3. Foundation (Architecture Section 3)

- **Equipment catalog.** `data_access/equipment_catalog_reader.py` builds the `EquipmentCatalog`. `equipment_activation.py` validates an id before recording the active choice. `EquipmentConfiguration` uses `PLATE_SCALE_CONSTANT` (206.265, Architecture Equation (1)).
- **Site.** `data_access/site_profile_reader.py` reads `[Observatory.Location]`, falls back to `FALLBACK_SITE` (Denver: 39.7392, -104.9903, 1600 m) with a warning, and seeds the default `SiteProfile`. Control uses `hardware_operations.observer_location` (mount first), so the single-source rule holds for planning only.
- **Storage.** `drivers/butler.py` holds `DiskButler`, the one storage path: `AbstractButler` from `astrometricslib`, one table per dataset type in `wayfinding.db`. `ControlRecordStore` keeps `alignment_attempts`, `guiding_samples` and `polar_alignments`. An alignment attempt worked out from a frame names it in `source_frame`, so the same frame brought in again, or solved again, updates its one row; `remove_repeated_alignment_attempts` reports, and with `apply=True` deletes, rows stored twice by older versions. When the store opens, it adds the `source_frame` column to an older database.

<!-- impl: wayfindinglib.data_access.equipment_catalog_reader.get_equipment_catalog -->
<!-- impl: wayfindinglib.tasks.control_tasks.equipment_activation.set_active_telescope -->
<!-- impl: wayfindinglib.data_access.site_profile_reader.get_or_seed_default_site_profile -->
<!-- impl: wayfindinglib.data_access.safety_policy_reader.get_safety_rule_set -->
<!-- impl: wayfindinglib.drivers.butler.DiskButler -->
<!-- impl: wayfindinglib.drivers.control_record_store.ControlRecordStore -->

## 4. Observation planning (Architecture Section 4)

Folder: `tasks/planning_tasks/`. Facade: `ObservationPlanning` in `api/planning.py`, which loads no hardware driver (Section 7.1).

- **Night window and visibility.** `night_window.py` (`resolve_night_window`), `astronomy/solar_position.py`, `visibility_tasks.py` (`visibility_state_at`, `find_earliest_visible_window`, `ever_visible`), `visibility_report.py`, `visibility_operations.py`.
- **Placement.** `scheduling.py` (`PlacementRequest`, `place`, `schedule_session`, `_apply_advisory_boost`). Reasons: `InfeasibilityReasonCode`. The boost needs `quality_weighting_enabled`.
- **Mosaics and advice.** `mosaic_tasks.py` (`calculate_panels` takes `rotation_deg`), `calibration_advisory_tasks.py`. The quality judgments are in Section 6.10.
- **Plans.** `planning_operations.py` (`create_plan` kinds: `sequence`, `package`, `empty_session`, `scheduled_session`; `get_advisory` kinds: `quality`, `calibration`, `tonight`, `result`; `get_plan`, `edit_queue`, `tick_checklist_item`).
- **Sky.** `sky_engine.py` (`SkyEngine`), `sky_sources.py`, `drivers/catalog/`, and the `*_operations.py` calculation modules.

<!-- impl: wayfindinglib.tasks.planning_tasks.scheduling.PlacementRequest -->
<!-- impl: wayfindinglib.tasks.planning_tasks.visibility_tasks.find_earliest_visible_window -->
<!-- impl: wayfindinglib.tasks.planning_tasks.sky_engine.SkyEngine -->
<!-- impl: wayfindinglib.tasks.planning_tasks.planning_operations -->
<!-- impl: wayfindinglib.models.planning.sequence_plan.SequencePlan -->

**Table 4.** Planning settings. `PlanningConfig` is in `models/planning/planning_config.py`.

| Setting | Value | Where |
| --- | --- | --- |
| Night window and visibility time step | 5 min | `PlanningConfig.night_window_time_step_min` |
| Twilight limit | -12° solar altitude | `PlanningConfig.twilight_sun_altitude_deg` |
| Boost for a flagged target | 2 | `PlanningConfig.flagged_quality_priority_boost` |
| Boost for an asteroid or variable-star candidate | 1 | `PlanningConfig.science_outcome_priority_boost` |
| Default dither | every 3 frames, 3 pixels | `PlanningConfig.dither_every_n_frames_default`, `DitherConfig` |
| Mosaic overlap | 10% | `PlanningConfig.mosaic_panel_overlap_percent` |
| Mosaic field of view when none is configured | 23.5 by 15.6 mm, 400 mm | `DEFAULT_SENSOR_WIDTH_MM`, `DEFAULT_SENSOR_HEIGHT_MM`, `DEFAULT_FOCAL_LENGTH_MM` |
| Telescope altitude range | 0° to 90°, on by default | `Telescope.min_altitude_deg`, `max_altitude_deg`, `altitude_limits_enabled` |
| Hour-angle bound | 2 h, off by default; checked at slew time | `Telescope.max_hour_angle_hours`, `hour_angle_limits_enabled` |
| Meridian flip hour angle | 1° past transit (4 min) | `Telescope.flip_hour_angle_deg` (Architecture Equation (3)) |

## 5. Observation execution (Architecture Section 5)

Folder: `tasks/execution_tasks/`. Facade: `ObservationExecution` in `api/execution.py`, imported only by the composition root.

- **Queue advancement (Architecture Table 4).** `session_runner.py`: `advance_session`, `abort_session`, `dispose_action`, `ActionDisposition`, `SessionRunnerDependencies` (hardware steps are callables, so no hardware import). `queue_runner.py`: `run_queue`. `SafetyAssessment.permits_observing` gates each advance.
- **Imaging queue.** `imaging_queue.py`: `imaging_dependencies` supplies only a slew and a capture.
- **Divergence and recovery.** `divergence_recording.py` (`record_divergence`), `meridian_flip.py` (`execute_meridian_flip`, `MeridianFlipSteps`), `fault_recovery.py` (`attempt_recovery`, `recover_fault`), `guide_star_loss_recovery.py` (`attempt_guide_star_recovery`).
- **Recording.** `session_recorder.py` (`ObservationSessionRecorder`), `post_session_reconciliation.py` (`compute_calibration_stats`, `attach_target_session_ids`, `reconcile_session`).

<!-- impl: wayfindinglib.tasks.execution_tasks.session_runner.SessionRunnerDependencies -->
<!-- impl: wayfindinglib.tasks.execution_tasks.session_runner.ActionDisposition -->
<!-- impl: wayfindinglib.tasks.execution_tasks.imaging_queue.imaging_dependencies -->
<!-- impl: wayfindinglib.tasks.execution_tasks.divergence_recording.UNCOMPARABLE_CAPABILITIES -->
<!-- impl: wayfindinglib.tasks.execution_tasks.meridian_flip.MeridianFlipSteps -->
<!-- impl: wayfindinglib.tasks.execution_tasks.fault_recovery.attempt_recovery -->
<!-- impl: wayfindinglib.models.session.guide_star_loss.GuideStarLossEvent -->
<!-- impl: wayfindinglib.tasks.execution_tasks.post_session_reconciliation.attach_target_session_ids -->
<!-- impl: wayfindinglib.models.policy.safety.SafetyAssessment.permits_observing -->

**Table 5.** Execution settings.

| Setting | Value | Where |
| --- | --- | --- |
| Recovery attempts | 3 | `RecoveryPolicy.max_attempts` |
| First wait before a retry | 30 s | `RecoveryPolicy.initial_interval_sec` |
| Growth factor of the wait | 2.0 (waits 0, 30, 60 s; Architecture Equation (4)) | `RecoveryPolicy.backoff_multiplier` |
| Alignment rounds after a flip and in centering | 3 | `CorrectionConfig.alignment_iteration_limit` |
| Guide-star re-acquisition attempts | 3 | `CorrectionConfig.guide_reacquire_attempts` |
| Alignment convergence tolerance | 30 arcsec | `CorrectionConfig.alignment_convergence_tolerance_arcsec` |
| Recording and save interval | 300 s | `snapshot_interval_seconds` of `ObservationSessionRecorder` |
| Wait between queue cycles | 5 s | `POLL_SECONDS` in `queue_runner.py` |

## 6. Observatory control (Architecture Section 6)

### 6.1 Facade and children

`ObservatoryControl` (`api/control/control.py`) holds the drivers and seven children, each a `ControlChild` sharing one `ControlContext` (`api/control/context.py`). Each child lives in `api/control/<child>.py` and has `status(include=...)`. `close()` stops control's threads at shutdown.

**Table 6.** The seven children of `ObservatoryControl`.

| Child | Main calls |
| --- | --- |
| `control.mount` | `slew`, `sync`, `park`, `unpark`, `set_tracking`, `compute_pointing_correction` |
| `control.imaging` | `capture_image`, `set_filter`, `focus_move`, `compute_focus_correction` |
| `control.guiding` | `run_loop`, `compute_correction`, `run_calibration`, `run_exposure_test` |
| `control.remote` | `list`, `sync_frames`, `sync_logs` |
| `control.history` | `query`, `get_live_session_status`, `frame_guiding`, `get_performance_envelope` |
| `control.safety` | `assess`, `execute_safe_state`, `open_enclosure`, `close_enclosure`, `apply_promotion_decision` |
| `control.equipment` | `connect`, `set_active_telescope`, `cooling_ramp_rate`, `summarize_device` |

The `kind` values of `control.history.query` are `capture`, `guiding`, `sky_coverage`, `recurring_issues`, `ekos_sessions`, `guiding_runs`, `pointing_model`, `alignment`. `night_history.py` shrinks long replies. `hardware_operations.mount_position_to_icrs` converts the mount's JNow positions to ICRS.

<!-- impl: wayfindinglib.api.control.context.ControlContext -->
<!-- impl: wayfindinglib.api.control.context.ControlChild -->
<!-- impl: wayfindinglib.tasks.control_tasks.hardware_operations.mount_position_to_icrs -->
<!-- impl: wayfindinglib.tasks.control_tasks.night_history -->

### 6.2 Drivers

`drivers/interfaces/` holds `MountDriver`, `CameraDriver`, `FocuserDriver`, `FilterWheelDriver`, `EnclosureDriver`, `SwitchDriver`, `WeatherDriver` and `GuidingDriver` (subclasses of `ProtocolDriver`), with `registry.py` mapping protocol names to classes. INDI implements the first seven in `drivers/indi/` over `drivers/indi_interface.py`. `drivers/simulators/indi_simulator.py` simulates a session. `drivers/stellarmate_interface.py` implements `RemoteTransferDriver`.

`Telescope.guiding_protocol` picks the guiding driver (`build_guiding_driver_registry`): `phd2`, `internal` or `simulator`. Only the simulator runs the guide loop. `analytics/guide_pulses.py` has the pulse arithmetic. `validate_altitude_limits` and `validate_hour_angle_limits` in `drivers/indi/mount_controller.py` run before each slew. The altitude range falls back to `[Observatory.Constraints]`. The hour-angle bound has no fallback.

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

`tasks/control_tasks/device_state_tasks.py`: `classify_device_state`, `summarize_device` and `find_alert_detail`. No code gathers the raw signals for every `DeviceRole`.

<!-- impl: wayfindinglib.tasks.control_tasks.device_state_tasks.summarize_device -->
<!-- impl: wayfindinglib.tasks.control_tasks.device_state_tasks.find_alert_detail -->

### 6.4 Corrections

All in `tasks/control_tasks/` unless noted.

- **Pointing (Equation (5)).** `pointing_correction.py`: `compute_pointing_correction` (`PointingCorrection`), using `analytics/pointing_model.py`.
- **Guiding (Equations (6) and (7)).** `guiding_correction.py`: `compute_guiding_correction` (`GuidingCorrection`), with `_signed_pulse_ms` and the feed-forward terms.
- **Focus (Equations (8) and (9)).** `focus_correction.py`: `sample_focus_curve`, `compute_focus_correction` returning a `FocusCorrection`.
- **Calibration.** `guider_calibration_tasks.py`, `focus_model_tasks.py`, `calibration_routines.py` (`run_guider_calibration`, `run_backlash_calibration`, with caller-supplied `GuiderCalibrationSteps`).
- **Centering.** `centering.py`: `center_on`, behind `slew(center=True)`.

<!-- impl: wayfindinglib.models.session.correction_result.PointingCorrection -->
<!-- impl: wayfindinglib.models.session.correction_result.GuidingCorrection -->
<!-- impl: wayfindinglib.models.session.correction_result.FocusCorrection -->
<!-- impl: wayfindinglib.tasks.control_tasks.guider_calibration_tasks -->
<!-- impl: wayfindinglib.tasks.control_tasks.focus_model_tasks -->
<!-- impl: wayfindinglib.tasks.control_tasks.calibration_routines.GuiderCalibrationSteps -->

**Table 7.** Correction settings, all in `CorrectionConfig` (`models/session/correction_config.py`).

| Setting | Value | Field |
| --- | --- | --- |
| Guiding aggressiveness; deadband; longest pulse | 0.7; 0.15 arcsec; 1000 ms | `guiding_aggressiveness`, `guiding_deadband_arcsec`, `guiding_max_pulse_ms` |
| Agreement tolerance: guiding; alignment | 0.5 arcsec; 5 arcsec | `guiding_divergence_tolerance_arcsec`, `alignment_divergence_tolerance_arcsec` |
| Focus samples; sweep span | 9; 2000 steps | `focus_sample_count`, `focus_sample_span_steps` |
| Focus re-run temperature change; fit quality floor; agreement tolerance | 1 °C; 0.90; 50 steps | `focus_temperature_delta_trigger_c`, `focus_fit_quality_floor`, `focus_divergence_tolerance_steps` |
| Guider calibration pulse | 3 s per axis | `run_calibration` defaults |
| Backlash probe | 1 s settling pulse; 0.05 s probes, at most 40; 0.5 pixel counts as a response | `run_backlash_calibration` defaults |
| Guide exposure test | 0.5, 1, 2, 4 s; 8 frames each | `run_exposure_test` defaults |

### 6.5 Safety

`safety_monitor.py` holds `SafetyMonitor`: `_evaluate_rule` gives `UNKNOWN` for a stale reading, `_evaluate_rules` takes the most severe result, and `evaluate` publishes a safe result only after `settling_period_sec`. `hardware_operations.refresh_safety_assessment` reads the weather driver.

**Table 8.** Safety and enclosure settings.

| Setting | Value | Where |
| --- | --- | --- |
| Age bound of a reading | 120 s | `SafetyRule.staleness_bound_sec` |
| Settling period before reopening | 900 s | `SafetyRuleSet.settling_period_sec` |
| Mount clearance about the park position | 2° per axis | `Enclosure.clearance_tolerance_deg` |
| Enclosure motion timeout | 180 s | `Enclosure.motion_timeout_sec` |

<!-- impl: wayfindinglib.tasks.control_tasks.safety_monitor.SafetyMonitor.evaluate -->
<!-- impl: wayfindinglib.drivers.indi.weather_controller.WeatherController -->

### 6.6 Enclosure, safe state and watchdog

- **Enclosure.** `enclosure_control.py`: `mount_within_clearance`, `can_close_enclosure`, `can_leave_park`, `check_motion_timeout`.
- **Safe state.** `safe_state.py`: `execute(trigger, steps)` with `SafeStateSteps`, returning a `SafeStateOutcome`.
- **Cooling.** `cooling_control.py`: `effective_ramp_rate_c_per_min`, `compute_ramped_setpoint`, `is_settled`, over `CoolingPolicy`. No code runs the polling loop.
- **Watchdog.** `watchdog/heartbeat.py` (`write_heartbeat`, `heartbeat_is_stale`), `watchdog/watchdog_process.py` (`check_and_escalate`, `run_watchdog_loop`), left out of `Wayfinder`.

**Table 9.** Cooling settings. `CoolingPolicy` is in `models/equipment_and_site/equipment.py`.

| Setting | Value | Where |
| --- | --- | --- |
| Target temperature | -10 °C | `CoolingPolicy.target_temp_c` |
| Ramp rate | 2 °C/min (the camera's limit applies if lower) | `CoolingPolicy.ramp_c_per_min` |
| Settled tolerance; timeout | 0.5 °C; 900 s | `CoolingPolicy.settle_tolerance_c`, `settle_timeout_sec` |
| Camera cooling floor and ramp limit | -30 °C; 2 °C/min | `Camera.min_cooling_temp_c`, `max_cooling_ramp_c_per_min` |

<!-- impl: wayfindinglib.tasks.control_tasks.enclosure_control.mount_within_clearance -->
<!-- impl: wayfindinglib.tasks.control_tasks.cooling_control.effective_ramp_rate_c_per_min -->
<!-- impl: wayfindinglib.tasks.control_tasks.cooling_control.is_settled -->
<!-- impl: wayfindinglib.watchdog.heartbeat.write_heartbeat -->
<!-- impl: wayfindinglib.watchdog.watchdog_process.run_watchdog_loop -->
<!-- impl: wayfindinglib.drivers.indi.enclosure_controller -->
<!-- impl: wayfindinglib.models.equipment_and_site.equipment.CoolingPolicy -->

### 6.7 Mount-mechanical models and bulk modes

`guiding_log_ingestion.py` (`fetch_and_ingest_new_guide_logs`) stores the periodic-error and backlash analysis as standing mount state. `pointing_log_ingestion.py` fits a session pointing model and never stores it. `analyze_guiding_telemetry` (`analytics/guiding_spectrum.py`) runs Lomb-Scargle on each guiding stretch. Bulk modes: Section 2.3.

<!-- impl: wayfindinglib.tasks.control_tasks.guiding_log_ingestion.fetch_and_ingest_new_guide_logs -->
<!-- impl: wayfindinglib.analytics.guiding_spectrum.SEGMENT_BREAK_SECONDS -->
<!-- impl: wayfindinglib.analytics.guiding_spectrum.MINIMUM_CYCLES -->

### 6.8 Incumbent logs and the performance envelope

`ekos_log_ingestion.py` downloads guide and analyze logs (`control.remote.sync_logs`) and ties each session to its equipment (`attribute_session_equipment`). Parsers: `drivers/phd2/guide_log_parser.py`, `drivers/ekos/analyze_log_parser.py`. `MEASURED_GUIDING_SAMPLE_SOURCES` lists the samples a refit may read. `frame_pointing.py` records each solved frame's pointing error, one record per frame file name.

`derive_performance_envelope` (`analytics/performance_envelope.py`) is the pure derivation (Architecture Equations (10) to (12)); `performance_envelope_tasks.py` gathers its inputs; the entry is `control.history.get_performance_envelope`. Constants: `SIGMA_TO_FWHM` (2.3548), `SIDEREAL_RATE_ARCSEC_PER_SECOND` (15.041), `MAD_TO_SIGMA` (1.4826). `analytics/tracking_risk.py` (`build_tracking_risk_map`) builds `tracking_risk`.

<!-- impl: wayfindinglib.tasks.control_tasks.ekos_log_ingestion.attribute_session_equipment -->
<!-- impl: wayfindinglib.models.session.telemetry.MEASURED_GUIDING_SAMPLE_SOURCES -->
<!-- impl: wayfindinglib.tasks.control_tasks.frame_pointing.record_frame_pointing_errors -->
<!-- impl: wayfindinglib.analytics.performance_envelope.SIGMA_TO_FWHM -->
<!-- impl: wayfindinglib.tasks.control_tasks.performance_envelope_tasks.tracking_risk_map -->

### 6.9 Session-quality analysis

`session_analysis/pipeline_base.py` defines `SessionAnalysisPipeline` and `SessionAnalysisRequest`.

| Analysis | Modules | Results and entry |
| --- | --- | --- |
| Guiding (exposure length in `processing/measure_exposure_feasibility.py`) | `session_analysis/guiding/` | `GuidingSessionAnalysis`, `GuidingPerformance` (`models/session/session_quality.py`); `kind="guiding"` |
| Capture | `session_analysis/capture/`, `wayfindinglib/analytics/capture_history.py` | `CaptureSessionAnalysis`, `CapturePerformance` (`models/session/capture_quality.py`); `kind="capture"` |
| Guide exposure test | `analytics/guide_exposure_ladder.py`, `guide_exposure_ladder_tasks.py` | `GuideExposureLadder`; `control.guiding.run_exposure_test`. Tested only against a simulated camera. |
| Sky position | `session_analysis/sky/` | `SkyAnalysis`, `SkySample`; `kind="sky_coverage"` |
| Recurring issues; live status; frame and guide error; alignment history | `recurring_issues.py`, `live_status.py`, `frame_guiding.py`, `alignment_history.py` | `RecurringIssue`; `get_live_session_status`; `frame_guiding`; `kind="alignment"` |

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

**Table 10.** Settings of the performance envelope and the session-quality analyses. The Basis column gives an observation or reason, where there is one.

| Setting | Value | Where | Basis |
| --- | --- | --- | --- |
| Blur tolerance $\phi$ | 0.10 | `DEFAULT_BLUR_TOLERANCE_FRACTION` | Policy choice. 5.6 arcsec star width gives 1.09 arcsec; 19 guide logs show 0.8 to 1.4. |
| Guide cycles an exposure must span | 3 | `MINIMUM_GUIDE_CYCLES_PER_EXPOSURE` | Measured 3.2 s cycle keeps exposures of 9.6 s or longer. Without it, median width rose 5.6 to 8.0 arcsec. |
| Baseline spread multiplier $k$; poor sky part (at least the blur tolerance worse, z of at least 3) | 3 | `BASELINE_SPREAD_MULTIPLIER` | Outlier convention. On real data no sky part reached the poor-part test. |
| Fewest frames behind a star width; sessions behind a baseline | 20; 5 | `MINIMUM_IMAGE_QUALITY_SAMPLES`, `MINIMUM_BASELINE_SESSIONS` | About one night of one target; a robust spread is unstable below about five values. |
| Fewest samples for a session; for a run's error | 100; 20 | `MINIMUM_SAMPLES_PER_SESSION`, `MINIMUM_SAMPLES_PER_RUN` | About six minutes of guiding; a robust spread is unstable with fewer. |
| Guide-scale agreement | 2% | `GUIDE_SCALE_AGREEMENT_FRACTION` | Above log rounding, below any real change of optics. |
| Excursion size | larger of 5 × the guiding-error limit and one guide pixel | `EXCURSION_SIGMA_MULTIPLE` | A 5σ event is about 1 in 1.7 million for Gaussian noise. |
| Percentile for fraction limits | 90th (nearest rank) | `BASELINE_FRACTION_PERCENTILE` | Lost and excursion fractions are zero on most nights. |
| Shortest run for a Dec correction rate | 600 s | `MINIMUM_RUN_SECONDS_FOR_CORRECTION_RATE` | Longer than a worm period (8 to 10 min). Not measured on this mount. |
| Guide-star brightness limit | earlier-night median × exp(-3 × robust log spread) | `guide_star_mass_low_limit` | Same rule as the SNR limit. Flags 2026-05-01 and 2026-09-21, 23, 24. |
| Saturation share for a guide star | 0.8 of ceiling | `SATURATION_FRACTION` | The science side's 80% rule. |
| Fewest frames for a position noise | 5 | `MINIMUM_FRAMES` | The second difference needs three positions. |
| Pause that ends a guiding stretch; cycles a stretch needs | 60 s; 2 | `SEGMENT_BREAK_SECONDS`, `MINIMUM_CYCLES` | Guide cycle is 2 to 6 s; with fewer cycles a period looks like a trend. |
| Longest period attributed to the gears | 650 s | `_LONGEST_MECHANICAL_PERIOD_SECONDS` | Worm period 8 to 10 min. Not measured on this mount. |
| Gap that means a lost star | 4 guide cycles | `LOST_STAR_CYCLES` | One missed frame is normal; three in a row is a recovery. |
| Clean share for a reliable exposure length; start-time step | 0.5; 0.25 of the exposure | `RELIABLE_CLEAN_FRACTION`, `_STRIDE_FRACTION` | Below 0.5, more exposures are ruined than kept. |
| Fewest nights per sky part | 3 | `MINIMUM_NIGHTS_PER_BIN` | Smallest number whose middle value is not an extreme. |
| Fewest measurements per night and per sky part | star width and roundness: 10, 5; guiding error: 3, 1 | `METRIC_SPECS` | A night has tens of frames but only a few guiding runs. |
| Fewest frames for a night's star quality; exposures for its cancelled share | 10; 20 | `MINIMUM_FRAMES_PER_NIGHT`, `MINIMUM_ATTEMPTS_PER_NIGHT` | Median of 10 has error near 40% of scatter; below 20, one cancellation moves the share 5%. |
| Window to match an Ekos exposure to a frame | 30 s | `MATCH_WINDOW_SECONDS` | Pairs start within about 2 s, a few up to 10 s. |
| Smallest saturated blob that is a star | 4 pixels | `SATURATED_BLOB_MINIMUM_PIXELS` (science side) | ASI533 cores 9 to 1,700 pixels; D5300 hot pixels 1. Agreed on 67 of 69 groups; two Moon frames differ. |
| Share of frames that clips an exposure length | 0.5 | `SATURATED_FRAME_FRACTION` (science side) | One clipped frame can be a cosmic ray or satellite streak. |
| Dark-frame temperature tolerance | 3 °C | `DEFAULT_DARK_TEMPERATURE_TOLERANCE_C` (science side) | Arcturus darks clustered near 0 and -10 °C, nothing between. |

**Table 11.** Settings of the tracking-risk map in `analytics/tracking_risk.py`.

| Setting | Value | Basis |
| --- | --- | --- |
| Priors: west of the meridian; near the pole; near the horizon | up to 0.10 by hour angle 75°; up to 0.08 above declination 75°; up to 0.40 at the horizon, 1 below it, from altitude 20° | East-heavy balance lets gears float; the declination bearing can stick; air blurs stars. |
| Jitter that is safe; that trails | below 0.75 pixel; above 1.25 pixels | Scatter of a target's repeated plate solves against pixel scale. |
| Spread of a target's pull; solves at full weight; largest measured share | 25°, Gaussian; 30; 85% | |

### 6.10 Quality judgment and advice (Quality Judgment paper)

The theory is in [Wayfinding quality judgment](Wayfinding_Quality_Judgment.md) (Architecture Section 6.11 gives its scope). Planning and Control share the vocabulary in `models/quality/`. The judging code is in `analytics/result_quality/` (Foundation, so both can call it) and is described in its README. The entry points are `get_advisory` (kinds `quality`, `tonight`, `result`, `calibration`) and `control.history.query` (kinds `night_quality`, `nights`, `target_changes`). A rule's confidence is the status label of its register entry (partly measured; see [WAY-01](validation/wayfinding.md#way-01)).

**Table 12.** Where each part of the judgment lives. Paths are relative to `wayfindinglib/`. Register entries hold the evidence, and WAY-D rows hold the settings of Table 13.

| Part | Code | Register |
| --- | --- | --- |
| Statement, kinds, action classes, confidence | `TargetStatement`, `StatementKind`, `ActionClass`, `Confidence` (`models/quality/statements.py`) | [WAY-01](validation/wayfinding.md#way-01) |
| Rule tables and conformance | `QualityRule`; `RESULT_QUALITY_RULES`, `STAR_RESULT_RULES`, `SESSION_RULES`, joined as `ALL_RULES` (`all_rules.py`); `judge_results`, `needs_attention`; `wayfindinglib/test/test_quality_rule_conformance.py` | [WAY-05](validation/wayfinding.md#way-05); WAY-D05, WAY-D06, WAY-D07, WAY-D08, WAY-D09, WAY-D10, WAY-D11 |
| Per-night rules | `session_rules.py`; guiding, capture and sky pipelines of Section 6.9 | [WAY-45](validation/wayfinding.md#way-45), [WAY-46](validation/wayfinding.md#way-46), [WAY-47](validation/wayfinding.md#way-47); WAY-D45, WAY-D46, WAY-D47, WAY-D48 |
| Envelope limits the rules read | `envelope_assembly.assemble_performance_envelope`, `performance_envelope.py` (Section 6.8) | [WAY-40](validation/wayfinding.md#way-40), [WAY-41](validation/wayfinding.md#way-41), [WAY-42](validation/wayfinding.md#way-42), [WAY-43](validation/wayfinding.md#way-43), [WAY-44](validation/wayfinding.md#way-44); WAY-D40, WAY-D41, WAY-D42, WAY-D43, WAY-D44 |
| Efficiency, goal, references | `physical_reference`, `baseline_reference`, `noise_regime`, `assess_efficiency` (`efficiency.py`); `science_goal_for_setting` (`science_goal.py`); `observing_values.py` | [WAY-20](validation/wayfinding.md#way-20); WAY-D01, WAY-D20, WAY-D21, WAY-D22, WAY-D23, WAY-D24, WAY-D25, WAY-D26, WAY-D27, WAY-D28, WAY-D74 |
| Loss account and night review | `loss_account`, `capture_setting_summaries`, `night_summary`, `target_summary` (`aggregates.py`); `set_aside_reasons` (`library_inputs.py`, reads `Astrometrics.quality.set_aside_frames`); `build_night_quality` (`night_quality_task.py`) | [WAY-74](validation/wayfinding.md#way-74); WAY-D75, WAY-D76 |
| Noise growth and the stop signal | `forecast_stack_noise`, `frames_to_cut_noise`, `fit_noise_floor`, `stop_signal`, group mode (`forecast_group_noise`, `plan_group_stack`), folded frames (`StackGroup.frame_noise_factors`, `group_frame_rate_variances`), floor (`measure_stack_noise_floor`, `choose_noise_floor`) (`noise_growth.py`) | [WAY-21](validation/wayfinding.md#way-21); WAY-D30, WAY-D31, WAY-D32, WAY-D34 |
| Forecast check | `forecast_check`, `stack_groups_of_run`, `attach_calibration_identities` (`forecast.py`); confound rules in `_confound_reason`, which compare group lengths (`RunGroups.group_lengths`) | [WAY-70](validation/wayfinding.md#way-70); WAY-D70 |
| Target dataset report | `build_target_dataset_report` (`target_report_tasks.py`), `target_report_inputs.py`, `report_rules.py`, `starting_point.py`; `TargetDatasetReport` (`models/planning/target_report.py`) | [WAY-60](validation/wayfinding.md#way-60); WAY-D60, WAY-D61, WAY-D62, WAY-D63, WAY-D64, WAY-D65, WAY-D66, WAY-D67, WAY-D68, WAY-D69 |
| One star's result | `judge_star_result` (`result_judgment_tasks.py`), `star_result_rules.py` | [WAY-73](validation/wayfinding.md#way-73); WAY-D73 |
| Sky model, overheads, tonight's ranking | `fit_sky_model` (`sky_model.py`), `estimate_capture_overheads` (`capture_overheads.py`), `rank_tonight`, `score_candidate` (`tonight_ranking_tasks.py`) | [WAY-71](validation/wayfinding.md#way-71), [WAY-72](validation/wayfinding.md#way-72); WAY-D29, WAY-D33, WAY-D71, WAY-D72 |
| Night plan | `ObservationSession` fields `checklist`, `framing`, `revision`; `PlanChange`, `PlanRevisionConflictError`; `night_plan_tasks.py`; `AllowedCaptureValues`; `SequenceSummary` | [WAY-50](validation/wayfinding.md#way-50), [WAY-51](validation/wayfinding.md#way-51), [WAY-52](validation/wayfinding.md#way-52), [WAY-53](validation/wayfinding.md#way-53), [WAY-54](validation/wayfinding.md#way-54); WAY-D50, WAY-D51, WAY-D52, WAY-D53, WAY-D54 |
| Night list and target changes | `build_night_list`, `build_night_history` (`night_history.py`); `find_target_changes` (`target_changes.py`) | [WAY-55](validation/wayfinding.md#way-55), [WAY-57](validation/wayfinding.md#way-57); WAY-D55, WAY-D56, WAY-D57 |
| Monitor mode | `ALLOWED_IN_MONITOR_MODE` and the three `test_monitor_mode_*` tests; guards in `hardware_operations.py` | [WAY-56](validation/wayfinding.md#way-56) |
| Reply size | `reply_budget.py`; the MCP tool registry trims replies; `fit_status_to_budget` for the live status | WAY-D58, WAY-D77 |

<!-- impl: wayfindinglib.models.quality.statements.TargetStatement -->
<!-- impl: wayfindinglib.models.quality.rule.QualityRule -->
<!-- impl: wayfindinglib.analytics.result_quality.all_rules.ALL_RULES -->
<!-- impl: wayfindinglib.analytics.result_quality.judge_results.judge_results -->
<!-- impl: wayfindinglib.analytics.result_quality.efficiency.assess_efficiency -->
<!-- impl: wayfindinglib.analytics.result_quality.science_goal.science_goal_for_setting -->
<!-- impl: wayfindinglib.analytics.result_quality.aggregates.loss_account -->
<!-- impl: wayfindinglib.analytics.result_quality.noise_growth.stop_signal -->
<!-- impl: wayfindinglib.analytics.result_quality.forecast.forecast_check -->
<!-- impl: wayfindinglib.analytics.result_quality.sky_model.fit_sky_model -->
<!-- impl: wayfindinglib.analytics.result_quality.capture_overheads.estimate_capture_overheads -->
<!-- impl: wayfindinglib.analytics.result_quality.library_inputs.set_aside_reasons -->
<!-- impl: wayfindinglib.analytics.result_quality.starting_point.build_starting_point -->
<!-- impl: wayfindinglib.analytics.envelope_assembly.assemble_performance_envelope -->
<!-- impl: wayfindinglib.tasks.planning_tasks.target_report_tasks.build_target_dataset_report -->
<!-- impl: wayfindinglib.tasks.planning_tasks.tonight_ranking_tasks.rank_tonight -->
<!-- impl: wayfindinglib.tasks.planning_tasks.result_judgment_tasks.judge_star_result -->
<!-- impl: wayfindinglib.tasks.planning_tasks.night_plan_tasks.store_session_change -->
<!-- impl: wayfindinglib.tasks.control_tasks.night_quality_task.build_night_quality -->
<!-- impl: wayfindinglib.tasks.control_tasks.night_history.build_night_list -->
<!-- impl: wayfindinglib.tasks.control_tasks.target_changes.find_target_changes -->
<!-- impl: wayfindinglib.models.session.observation_session.PlanChange -->
<!-- impl: wayfindinglib.models.session.observation_session.PlanRevisionConflictError -->
<!-- impl: wayfindinglib.models.planning.target_report.TargetDatasetReport -->
<!-- impl: wayfindinglib.models.planning.tonight_ranking.TonightRanking -->
<!-- impl: wayfindinglib.models.session.night_quality.NightQuality -->
<!-- impl: wayfindinglib.models.session.night_list.NightListEntry -->
<!-- impl: wayfindinglib.analytics.result_quality.allowed_capture_values.AllowedCaptureValues -->
<!-- impl: wayfindinglib.models.planning.sequence_summary.SequenceSummary -->
<!-- impl: wayfindinglib.analytics.result_quality.reply_budget.reply_budget -->
<!-- impl: wayfindinglib.models.equipment_and_site.site_profile.SiteProfile -->

Without `SiteProfile.typical_seeing_fwhm_arcsec`, the physical reference uses the diffraction limit alone (design estimate; WAY-D01). `DiskButler.put_session_from_execution` <!-- impl: wayfindinglib.drivers.butler.DiskButler.put_session_from_execution --> is the one write Execution uses. It merges the fields Planning owns inside one transaction, so a plan edit survives a checkpoint. A plan edit that changes nothing stores nothing, keeps the revision and sends no `plan:changed` event. The `sequencer:add`, `remove`, `reorder` and `modify` methods edit the queue through the plan service and send the same event.

**Table 13.** Main settings of the quality judgment. Each row's register identifier gives its status and a way to measure it.

| Setting | Value | Where | Register |
| --- | --- | --- | --- |
| Diffraction limit | 550 nm; 1.029 λ/D | `REFERENCE_WAVELENGTH_NM`, `AIRY_FWHM_FACTOR` | WAY-D20 |
| Equipment baseline | 10th percentile of nightly medians; 3 nights | `BASELINE_PERCENTILE`, `MINIMUM_NIGHTS_FOR_BASELINE` | WAY-D22 |
| Noise regimes | sky variance 9 × read-noise variance; source variance 1 × sky | `SKY_LIMITED_VARIANCE_RATIO`, `SOURCE_LIMITED_VARIANCE_RATIO` | WAY-D24 |
| Sky model | 5 nights, 20 frames; Moon term from 2 moonlit nights | `MINIMUM_NIGHTS_FOR_SKY_MODEL`, `MINIMUM_FRAMES_FOR_SKY_MODEL`, `MINIMUM_MOON_NIGHTS` | WAY-D29 |
| "Take N more" | noise to 0.707 of today's; new exposure length at least 5 frames | `TARGET_NOISE_RATIO`, `MINIMUM_FRAMES_NEW_EXPOSURE_LENGTH` | WAY-D30 |
| Floor and stop signal | 2 standard errors, one-sided; stop under a 10% cut from doubling | `FLOOR_SIGMAS`, `MINIMUM_GAIN_FRACTION` | WAY-D32 |
| Capture overheads | download 5 s, dither settle 20 s, autofocus 120 s | `DEFAULT_DOWNLOAD_SECONDS`, `DEFAULT_DITHER_SETTLE_SECONDS`, `DEFAULT_AUTOFOCUS_SECONDS` | WAY-D33 |
| Forecast agreement; dark master compared unless the camera name has zwo or asi | 2 standard errors | `AGREEMENT_TOLERANCES`, `COOLED_CAMERA_NAME_MARKERS` | WAY-D70 |
| Clock hour of the ranking | dither every 3 frames; 1 autofocus run; thin under 2 h | `DITHER_EVERY_FRAMES`, `AUTOFOCUS_RUNS_PER_HOUR`, `THIN_DATASET_SECONDS` | WAY-D71 |
| Frames lost warning | 5% of a setting's frames | `FRAME_LOSS_WARNING_FRACTION` | WAY-D60 |
| Report "take N more" | 10 frames at least; exposure rounded up to 10 s | `MINIMUM_FRAMES_FOR_FORECAST`, `EXPOSURE_ROUNDING_SECONDS` | WAY-D61 |
| Sky cost; altitude cost | 10% of captured time; cuts 30°, 40°, 50°, 15% | `SKY_COST_MINIMUM_SHARE`, `ALTITUDE_CUTS_DEG`, `ALTITUDE_MINIMUM_RELATIVE_LOSS` | WAY-D64, WAY-D65 |
| Sharpness trade-off | keep 70%; at least 5% narrower; 20 frames | `SHARPNESS_KEEP_FRACTION`, `SHARPNESS_MINIMUM_GAIN`, `SHARPNESS_MINIMUM_FRAMES` | WAY-D66 |
| Reply limit | 40,000 characters, 10% headroom | `MAXIMUM_REPLY_CHARACTERS`, `REPLY_HEADROOM_FRACTION` | WAY-D77 |

## 7. Verification (Architecture Section 7)

### 7.1 Structural checks

Static checks are import-linter contracts in `pyproject.toml`. Runtime checks use `test/import_probe.py`.

| Check | Static contract | Runtime test |
| --- | --- | --- |
| Layering | Foundation imports nothing above it; Control and Planning never import each other | None |
| Execution is optional | Only the composition root imports Execution | `test_control_and_planning_work_without_loading_execution` in `wayfindinglib/test/test_layer_independence.py` |
| Safety runs without execution | None | `test_safety_runs_without_loading_execution` (same file) |
| Planning is hardware-free | Planning is hardware-free, even indirectly | `test_planning_module_tree_imports_no_device_driver` in `wayfindinglib/api/test/test_planning.py` |
| Cross-library | `astrometricslib` never imports `wayfindinglib` | None |

<!-- impl: wayfindinglib.test.import_probe.wayfindinglib_modules_loaded_by -->

### 7.2 Promotion gates

No module applies the gates of the architecture's Table 8. `DivergenceEvidenceSummary.meets_phase_gate` tests a summary against caller thresholds. Every value in Table 12 is a conservative design estimate.

**Table 12.** Delegation phase promotion gates with their values.

| Entering phase | Gate |
| --- | --- |
| 1 | Mount commands through this system over at least 2 nights and 20 commands, incumbent disengaged. Out-of-envelope commands rejected on real hardware. Every device reports a `DeviceSummaryState`. No unintended motion. |
| 2 | A `GuiderCalibration` and `FocusModel` exist. All three correction functions run every real night with no commanded motion. |
| 3 | Over at least 5 nights: 500 paired comparisons each for alignment and guiding, 30 focus sweeps. Agreement at least 95%. Mean signed divergence within half the tolerance. No sign or axis inversions. Alignment converges on at least 95% of attempts. |
| 4 | Over at least 5 nights with corrections authoritative: guiding RMS within 20% of the Phase 2 baseline. No unrecovered guide-star losses. At least 10 meridian flips resumed. Cooling holds its target. |
| 5 | An independent hardware interlock is verified. Safety verdicts are correct for stale, absent and unsafe readings. At least 20 interlocked enclosure cycles with no violation. `safe_state.execute` verified from each trigger, including with the controlling process killed. Fault recovery exercised on an induced fault. |

## 8. Known gaps (Architecture Section 8)

- The imaging queue wires only a slew and a capture (Section 5).
- No code builds a default `SafeStateSteps`, `MeridianFlipSteps`, `GuiderCalibrationSteps` or `BacklashCalibrationSteps`.
- Nothing calls `write_heartbeat`, starts `run_watchdog_loop`, or calls `can_leave_park` or `check_motion_timeout` (Section 6.6).
- `classify_device_state` has no signal-gathering caller (Section 6.3). `run_loop` runs only with the simulator.
- The variable-star count is always 0, and `visibility_state_at` ignores `max_hour_angle_hours` (Section 4).

## Appendix A. Correspondence with KStars and Ekos

**Table 13.** Ekos module [1] to library call mapping.

| Ekos module | Library calls | Code |
| --- | --- | --- |
| Mount | `control.mount.slew`, `park`, `set_tracking` | `hardware_operations.py` |
| Align | `control.mount.compute_pointing_correction`, `slew(center=True)` | `pointing_correction.py`, `centering.py` |
| Focus | `control.imaging.compute_focus_correction` (no autofocus sweep call) | `focus_correction.py` |
| Guide | `control.guiding.compute_correction`, `status(include=["live"])` | `guiding_correction.py`, `live_guiding.py` |
| Scheduler | `planning.create_plan(kind="scheduled_session")` | `scheduling.py` |
| Capture | `planning.create_plan(kind="package")` | `observation_package.py` |
| Dome and weather interlock | `control.safety.execute_safe_state`, `assess` | `safe_state.py` |
| Session recording | `execution.advance_session`, `create_recorder`, `control.history.query` | `session_runner.py` |

## References

<!-- vale off -->

[1] J. Mutlaq et al., "KStars: Free, open source, cross-platform astronomy software," KDE Project. Accessed: Oct. 15, 2026. [Online]. Available: <https://edu.kde.org/kstars>

<!-- vale on -->
