# Wayfinding library implementation

*Version 2.5 · 2026-11-01 · Status: current*

## Abstract

This paper maps the design in `Wayfinding_Library_Architecture.md` to the Python source in `wayfindinglib/`. It names the layers, classes and modules, lists the settings with values and register identifiers, names the structural tests and says which parts of the design have no code yet. Evidence is in the validation register.

Terms are in the [glossary](../Glossary.md).

## 1. Scope

This paper indexes the code; theory stays in [Wayfinding_Library_Architecture.md](Wayfinding_Library_Architecture.md). Module paths are relative to `wayfindinglib/` unless a path starts with another folder.

The package root is the one supported entry point. It loads its names on first use, so importing it loads no hardware code. Its 8 names are `Wayfinder` (sub-APIs `control`, `planning`, `execution`, `review`), the four sub-API classes, `ObservationSession`, `ObservationPackage` and `FrameEndDetector`. `exceptions.py` and `reports.py` are public. The rest is private.

<!-- impl: wayfindinglib.Wayfinder -->
<!-- impl: wayfindinglib.api.control.control.ObservatoryControl -->
<!-- impl: wayfindinglib.api.planning.ObservationPlanning -->
<!-- impl: wayfindinglib.api.execution.ObservationExecution -->
<!-- impl: wayfindinglib.api.review.ObservationReview -->
<!-- impl: wayfindinglib.api.review.ObservationReview.query_alignment_sessions -->

## 2. System architecture

### 2.1 Layers and import contracts

**Table 1.** Layers of Figure 1 of the architecture paper and their folders.

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

`DeviceSummaryState` (`models/policy/device_state.py`) holds the five states (`OFFLINE`, `STANDBY`, `DISABLED`, `ENABLED`, `FAULT`).

<!-- impl: wayfindinglib.models.policy.device_state.DeviceSummaryState -->

### 2.3 Capability delegation (Architecture Section 2.3)

`models/policy/delegation.py` holds `ObservatoryCapability` (the six capabilities of Table 2), `DelegationState` (`DELEGATED`, `SHADOWED`, `AUTHORITATIVE`), `DelegationPolicy` and `CapabilityDelegation`. `data_access/delegation_policy_reader.py` enforces the rules.

`validate_delegation_policy` raises `DelegationPolicyValidationError` for a shadowed safety capability, early capture orchestration or missing calibration. `promote_capability` forces shadowing first.

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

The phase is the combination of states `DelegationPolicy` holds, not a type. `CommissioningRun` (`models/policy/commissioning.py`) records phase 0 to 5 evidence as `CommissioningObservation` records. `control.equipment._save_commissioning_run` stores it.

<!-- impl: wayfindinglib.models.policy.commissioning.CommissioningObservation -->
<!-- impl: wayfindinglib.api.control.equipment.EquipmentControl._save_commissioning_run -->

### 2.5 Domain models and operations (Architecture Section 2.5)

**Table 3.** Records of the architecture's Table 2 and the pydantic classes that hold them.

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

`ObservatoryControl` (Section 6.1), `ObservationPlanning` (Section 4) and `ObservationExecution` (Section 5) expose the operations of the architecture's Table 3.

<!-- impl: wayfindinglib.models.equipment_and_site.equipment.Camera -->
<!-- impl: wayfindinglib.models.session.divergence.DivergenceRecord -->
<!-- impl: wayfindinglib.models.session.ekos_session.EkosSessionContext -->
<!-- impl: wayfindinglib.models.policy.device_state.DeviceState -->

## 3. Foundation (Architecture Section 3)

- **Equipment catalog.** `data_access/equipment_catalog_reader.py` builds the `EquipmentCatalog`. `equipment_activation.py` validates an id before recording the active choice. `EquipmentConfiguration` uses `PLATE_SCALE_CONSTANT` (206.265, Architecture Equation (1)).
- **Site.** `data_access/site_profile_reader.py` reads `[Observatory.Location]`, falls back to `FALLBACK_SITE` (Denver: 39.7392, -104.9903, 1600 m) with a warning, and seeds the default `SiteProfile`. Control uses `hardware_operations.observer_location` (mount first), so the rule holds for planning only.
- **Storage.** `drivers/butler.py` holds `DiskButler`, the one storage path: `AbstractButler` from `astrometricslib`, one table per dataset type in `wayfinding.db`. `ControlRecordStore` keeps `alignment_attempts`, `guiding_samples` and `polar_alignments`. An alignment attempt from a frame names it in `source_frame`, so the same frame brought in or solved again updates its one row; `remove_repeated_alignment_attempts` reports, and with `apply=True` deletes, rows stored twice.

<!-- impl: wayfindinglib.data_access.equipment_catalog_reader.get_equipment_catalog -->
<!-- impl: wayfindinglib.tasks.control_tasks.equipment_activation.set_active_telescope -->
<!-- impl: wayfindinglib.data_access.site_profile_reader.get_or_seed_default_site_profile -->
<!-- impl: wayfindinglib.data_access.safety_policy_reader.get_safety_rule_set -->
<!-- impl: wayfindinglib.drivers.butler.DiskButler -->
<!-- impl: wayfindinglib.drivers.control_record_store.ControlRecordStore -->

## 4. Observation planning (Architecture Section 4)

Folder: `tasks/planning_tasks/`. Facade: `ObservationPlanning` (`api/planning.py`), which loads no hardware driver.

- **Night window and visibility.** `night_window.py` (`resolve_night_window`), `astronomy/solar_position.py`, `visibility_tasks.py` (`visibility_state_at`, `find_earliest_visible_window`, `ever_visible`), `visibility_report.py`, `visibility_operations.py`.
- **Placement.** `scheduling.py` (`PlacementRequest`, `place`, `schedule_session`, `_apply_advisory_boost`). Reasons: `InfeasibilityReasonCode`. The boost needs `quality_weighting_enabled`.
- **Mosaics and advice.** `mosaic_tasks.py` (`calculate_panels`), `calibration_advisory_tasks.py` (`get_calibration_advice`, `get_tonight_advice`). The quality judgments are in Section 6.10.
- **Plans.** `planning_operations.py` (`create_sequence`, `create_package`, `create_session`; `get_plan`, `add_to_queue`, `tick_checklist_item`).
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
| Default mosaic field of view | 23.5 by 15.6 mm, 400 mm | `DEFAULT_SENSOR_WIDTH_MM`, `DEFAULT_SENSOR_HEIGHT_MM`, `DEFAULT_FOCAL_LENGTH_MM` |
| Telescope altitude range | 0° to 90°, on by default | `Telescope.min_altitude_deg`, `max_altitude_deg`, `altitude_limits_enabled` |
| Hour-angle bound | 2 h, off by default; checked at slew time | `Telescope.max_hour_angle_hours`, `hour_angle_limits_enabled` |
| Meridian flip hour angle | 1° past transit (4 min) | `Telescope.flip_hour_angle_deg` (Architecture Equation (3)) |

## 5. Observation execution (Architecture Section 5)

Folder: `tasks/execution_tasks/`. Facade: `ObservationExecution` (`api/execution.py`), imported only by the composition root.

- **Queue advancement (Architecture Table 4).** `session_runner.py`: `advance_session`, `abort_session`, `dispose_action`, `ActionDisposition`, `SessionRunnerDependencies` (hardware steps are callables). `queue_runner.py`: `run_queue`. `SafetyAssessment.permits_observing` gates each advance.
- **Imaging queue.** `imaging_queue.py`: `imaging_dependencies` supplies only a slew and a capture.
- **Divergence and recovery.** `divergence_recording.py` (`record_divergence`), `meridian_flip.py` (`execute_meridian_flip`, `MeridianFlipSteps`), `fault_recovery.py` (`attempt_recovery`, `recover_fault`), `guide_star_loss_recovery.py` (`attempt_guide_star_recovery`).
- **Recording.** `session_recorder.py` (`ObservationSessionRecorder`), `post_session_reconciliation.py` (`attach_target_session_ids`, `reconcile_session`).

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

`ObservatoryControl` (`api/control/control.py`) holds the drivers and six children, each a `ControlChild` sharing one `ControlContext` (`api/control/context.py`). Each child lives in `api/control/<child>.py` and has `status(include=...)`.

**Table 6.** The six children of `ObservatoryControl`.

| Child | Main calls |
| --- | --- |
| `control.mount` | `slew`, `park`, `unpark`, `set_tracking` |
| `control.imaging` | `capture_image`, `set_filter`, `focus_move` |
| `control.guiding` | `run_loop`, `stop_loop`, `refit_spectrum` |
| `control.remote` | `list`, `sync_frames`, `sync_logs` |
| `control.safety` | `apply_promotion_decision`, `enter_monitoring_mode` |
| `control.equipment` | `connect`, `set_active_telescope` |

`Wayfinder.review` (`api/review.py`) holds the night reads.

The `kind` values of `review.query` are `capture`, `guiding`, `sky_coverage`, `recurring_issues`, `ekos_sessions`, `guiding_runs`, `pointing_model`, `alignment`. `hardware_operations.mount_position_to_icrs` converts the mount's JNow positions to ICRS.

<!-- impl: wayfindinglib.api.control.context.ControlContext -->
<!-- impl: wayfindinglib.api.control.context.ControlChild -->
<!-- impl: wayfindinglib.tasks.control_tasks.hardware_operations.mount_position_to_icrs -->
<!-- impl: wayfindinglib.tasks.control_tasks.night_history -->

### 6.2 Drivers

`drivers/interfaces/` holds `MountDriver`, `CameraDriver`, `FocuserDriver`, `FilterWheelDriver`, `EnclosureDriver`, `SwitchDriver`, `WeatherDriver` and `GuidingDriver` (subclasses of `ProtocolDriver`), with `registry.py` mapping protocol names to classes. INDI implements the first seven in `drivers/indi/` over `drivers/indi_interface.py`. `drivers/simulators/indi_simulator.py` simulates a session. `drivers/stellarmate_interface.py` implements `RemoteTransferDriver`.

`Telescope.guiding_protocol` picks the guiding driver (`build_guiding_driver_registry`): `phd2`, `internal` or `simulator`. Only the simulator runs the guide loop. `validate_altitude_limits` and `validate_hour_angle_limits` in `drivers/indi/mount_controller.py` run before each slew. Only the altitude range falls back to `[Observatory.Constraints]`.

When PHD2 runs on the observatory computer (`[Observatory.Guiding] phd2_location`, default `observatory` with the `stellarmate` transfer driver), its event server accepts only local connections, so the backend reaches it through an `SshPortForward` (`drivers/ssh_port_forward.py`) to `phd2_port` (default 4400). A `local` PHD2 uses `localhost:phd2_port`. Only the backend opens a forward (`open_port_forwards=True`); scripts and MCP servers use the mount's guide pulses. Under `setpriv --pdeathsig KILL`, when installed, ssh ends with the app. `StellarMateInterface.port_forward_command` rebuilds the command on each restart. A dead ssh restarts after 5 s (the `PHD2Client` cooldown); the forward counts as up only when its local port accepts a connection; ssh ends a silent link after 45 s (`ServerAliveInterval=15`, `ServerAliveCountMax=3`). These are design estimates, not yet measured.

A failing ``.local`` lookup does not drop a working link. `KnownHostAddress` (`drivers/host_resolver.py`) keeps the last resolved address; while the lookup fails, INDI connects to it and ssh and rsync get `-o HostName=<address> -o HostKeyAlias=<name>`, so `known_hosts` is still checked under the name. A changed host name clears it. A connected INDI client is replaced only when its connection is down or `MAX_CONSECUTIVE_PROBE_FAILURES` (3) handshakes at least 5 s apart fail; a failed lookup never counts. The limit is a design estimate, not yet measured.

<!-- impl: wayfindinglib.drivers.interfaces.registry.build_guiding_driver_registry -->
<!-- impl: wayfindinglib.drivers.ssh_port_forward.SshPortForward -->
<!-- impl: wayfindinglib.drivers.indi_interface.IndiInterface -->
<!-- impl: wayfindinglib.drivers.simulators.indi_simulator.SimulatorIndiInterface -->
<!-- impl: wayfindinglib.drivers.stellarmate_interface.StellarMateInterface -->
<!-- impl: wayfindinglib.drivers.phd2.guiding_driver -->
<!-- impl: wayfindinglib.drivers.simulators.guiding_simulator -->
<!-- impl: wayfindinglib.analytics.guide_pulses.rms_arcsec -->
<!-- impl: wayfindinglib.tasks.control_tasks.live_guiding.LiveGuidingMonitor -->
<!-- impl: wayfindinglib.drivers.indi.mount_controller.MountController.validate_altitude_limits -->

### 6.3 Device summary state

`tasks/control_tasks/device_state_tasks.py`: `classify_device_state`, `summarize_device` and `find_alert_detail`.

<!-- impl: wayfindinglib.tasks.control_tasks.device_state_tasks.summarize_device -->
<!-- impl: wayfindinglib.tasks.control_tasks.device_state_tasks.find_alert_detail -->

### 6.4 Corrections

All in `tasks/control_tasks/` unless noted.

- **Pointing (Equation (5)).** `pointing_correction.py`: `compute_pointing_correction` (`PointingCorrection`), using `analytics/pointing_model.py`.
- **Guiding (Equations (6) and (7)).** `guiding_correction.py`: `compute_guiding_correction` (`GuidingCorrection`), with `_signed_pulse_ms` and the feed-forward terms.
- **Focus (Equations (8) and (9)).** `focus_correction.py`: `sample_focus_curve`, `compute_focus_correction` returning a `FocusCorrection`.
- **Calibration.** `guider_calibration_tasks.py`, `focus_model_tasks.py`, `calibration_routines.py` (`run_guider_calibration`, `run_backlash_calibration`; `GuiderCalibrationSteps`).
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

<!-- impl: wayfindinglib.analytics.unguided_mount_error.measure_run -->
<!-- impl: wayfindinglib.analytics.unguided_mount_error.reconstruct_unguided_error -->
<!-- impl: wayfindinglib.tasks.control_tasks.mount_error_tasks.measure_unguided_mount_error -->
<!-- impl: wayfindinglib.tasks.control_tasks.mount_error_tasks.measure_guiding_spectrum -->
<!-- impl: wayfindinglib.tasks.control_tasks.focus_model_tasks.measure_focus_temperature_coefficient -->
<!-- impl: wayfindinglib.tasks.control_tasks.sky_rate_tasks.get_sky_rate_prediction -->
<!-- impl: wayfindinglib.drivers.phd2.guide_log_parser.parse_guide_log_file -->

<!-- impl: wayfindinglib.analytics.unguided_mount_error.MINIMUM_PERIOD_SECONDS -->
<!-- impl: wayfindinglib.analytics.unguided_mount_error.MAXIMUM_PERIOD_SECONDS -->
<!-- impl: wayfindinglib.analytics.unguided_mount_error.MINIMUM_FIT_SAMPLES -->
<!-- impl: wayfindinglib.analytics.unguided_mount_error.MINIMUM_AMPLITUDE_SIGNIFICANCE -->
<!-- impl: wayfindinglib.tasks.control_tasks.focus_model_tasks.MINIMUM_FOCUS_PAIRS -->
<!-- impl: wayfindinglib.tasks.control_tasks.focus_model_tasks.MINIMUM_TEMPERATURE_SPAN_C -->
<!-- impl: wayfindinglib.tasks.control_tasks.focus_model_tasks.MINIMUM_FILTER_SHARE_PAIRS -->

<!-- impl: wayfindinglib.tasks.control_tasks.guiding_log_ingestion.fetch_and_ingest_new_guide_logs -->
<!-- impl: wayfindinglib.analytics.guiding_spectrum.SEGMENT_BREAK_SECONDS -->
<!-- impl: wayfindinglib.analytics.guiding_spectrum.MINIMUM_CYCLES -->

### 6.8 Incumbent logs and the performance envelope

`ekos_log_ingestion.py` downloads guide and analyze logs (`control.remote.sync_logs`) and ties each session to its equipment (`attribute_session_equipment`). Parsers: `drivers/phd2/guide_log_parser.py`, `drivers/ekos/analyze_log_parser.py`. `frame_pointing.py` records each solved frame's pointing error, one record per frame file name.

`derive_performance_envelope` (`analytics/performance_envelope.py`) is the pure derivation (Architecture Equations (10) to (12)); `performance_envelope_tasks.py` gathers its inputs; the entry is `review.get_performance_envelope`. Constants: `SIGMA_TO_FWHM` (2.3548), `SIDEREAL_RATE_ARCSEC_PER_SECOND` (15.041), `MAD_TO_SIGMA` (1.4826). `analytics/tracking_risk.py` (`build_tracking_risk_map`) builds `tracking_risk`.

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
| Guide exposure test | `analytics/guide_exposure_ladder.py`, `guide_exposure_ladder_tasks.py` | `GuideExposureLadder`; `control.guiding._run_exposure_test`. Tested only against a simulated camera. |
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

**Table 10.** Settings of the performance envelope and the session-quality analyses. The Basis column gives a reason.

| Setting | Value | Where | Basis |
| --- | --- | --- | --- |
| Blur tolerance $\phi$ | 0.10 | `DEFAULT_BLUR_TOLERANCE_FRACTION` | Policy choice. |
| Guide cycles an exposure must span | 3 | `MINIMUM_GUIDE_CYCLES_PER_EXPOSURE` | WAY-D44. |
| Baseline spread multiplier $k$; poor sky part (at least the blur tolerance worse, z of at least 3) | 3 | `BASELINE_SPREAD_MULTIPLIER` | Outlier convention. |
| Fewest frames behind a star width; sessions behind a baseline | 20; 5 | `MINIMUM_IMAGE_QUALITY_SAMPLES`, `MINIMUM_BASELINE_SESSIONS` | One night of one target; a spread of fewer than five values is unstable. |
| Fewest samples for a session; for a run's error | 100; 20 | `MINIMUM_SAMPLES_PER_SESSION`, `MINIMUM_SAMPLES_PER_RUN` | About six minutes of guiding. |
| Guide-scale agreement | 2% | `GUIDE_SCALE_AGREEMENT_FRACTION` | Above log rounding, below a real optics change. |
| Excursion size | larger of 5 × the guiding-error limit and one guide pixel | `EXCURSION_SIGMA_MULTIPLE` | A 5σ event is very rare for Gaussian noise. |
| Percentile for fraction limits | 90th (nearest rank) | `BASELINE_FRACTION_PERCENTILE` | Most nights have none. |
| Shortest run for a Dec correction rate | 600 s | `MINIMUM_RUN_SECONDS_FOR_CORRECTION_RATE` | Longer than a worm period (8 to 10 min); not measured. |
| Guide-star brightness limit | earlier-night median × exp(-3 × robust log spread) | `guide_star_mass_low_limit` | Same rule as the SNR limit. |
| Saturation share for a guide star | 0.8 of ceiling | `SATURATION_FRACTION` | The science side's rule. |
| Fewest frames for a position noise | 5 | `MINIMUM_FRAMES` | A second difference needs three positions. |
| Pause that ends a guiding stretch; cycles a stretch needs | 60 s; 2 | `SEGMENT_BREAK_SECONDS`, `MINIMUM_CYCLES` | With fewer cycles a period looks like a trend. |
| Longest period attributed to the gears | 650 s | `_LONGEST_MECHANICAL_PERIOD_SECONDS` | Worm period; not measured. |
| Gap that means a lost star | 4 guide cycles | `LOST_STAR_CYCLES` | One missed frame is normal. |
| Clean share for a reliable exposure length; start-time step | 0.5; 0.25 of the exposure | `RELIABLE_CLEAN_FRACTION`, `_STRIDE_FRACTION` | Below 0.5, more are ruined than kept. |
| Fewest nights per sky part | 3 | `MINIMUM_NIGHTS_PER_BIN` | Smallest count whose middle value is not an extreme. |
| Fewest measurements per night and per sky part | star width and roundness: 10, 5; guiding error: 3, 1 | `METRIC_SPECS` | A night has few guiding runs. |
| Fewest frames for a night's star quality; exposures for its cancelled share | 10; 20 | `MINIMUM_FRAMES_PER_NIGHT`, `MINIMUM_ATTEMPTS_PER_NIGHT` | A median of fewer frames is too noisy. |
| Window to match an Ekos exposure to a frame | 30 s | `MATCH_WINDOW_SECONDS` | Pairs start seconds apart. |
| Smallest saturated blob that is a star | 4 pixels | `SATURATED_BLOB_MINIMUM_PIXELS` (science side) | A hot pixel is one pixel. |
| Share of frames that clips an exposure length | 0.5 | `SATURATED_FRAME_FRACTION` (science side) | One clipped frame may be a cosmic ray. |
| Dark-frame temperature tolerance | 3 °C | `DEFAULT_DARK_TEMPERATURE_TOLERANCE_C` (science side) | Darks cluster by cooler setpoint. |

**Table 11.** Settings of the tracking-risk map in `analytics/tracking_risk.py`.

| Setting | Value | Basis |
| --- | --- | --- |
| Priors: west of the meridian; near the pole; near the horizon | up to 0.10 by hour angle 75°; up to 0.08 above declination 75°; up to 0.40 at the horizon, 1 below it, from altitude 20° | Balance, bearing, air. |
| Jitter that is safe; that trails | below 0.75 pixel; above 1.25 pixels | Scatter of a target's repeated plate solves against pixel scale. |
| Spread of a target's pull; solves at full weight; largest measured share | 25°, Gaussian; 30; 85% | |

### 6.10 Quality judgment and advice (Quality Judgment paper)

The theory is in [Wayfinding quality judgment](Wayfinding_Quality_Judgment.md) (Architecture Section 6.11). Planning and Control share the vocabulary in `models/quality/`; the judging code is in `analytics/result_quality/` (see its README). Entry points: `review.get_advisory` (kinds `quality`, `result`), `planning.get_tonight_advice`, `planning.get_calibration_advice` and `review.query` (kinds `night_quality`, `nights`, `target_changes`). A rule's confidence is its register entry's status label (see [WAY-01](validation/wayfinding.md#way-01)).

Roles (user decision, 2026-11-01; `documentation/plans/VERDICT_REMOVAL_PLAN.md`): all gates and measurements stay in `astrometricslib`; every reading of good or bad is a suggestion in `wayfindinglib`. These rules compare a stored number with a copy of the library's limit, and a test pins each copy: `stack_rejected_fraction_high` ([STK-24](validation/stacking.md#stk-24)) and `stack_sharpness_poor` ([STK-25](validation/stacking.md#stk-25)) in `rules.py` and `judge_results.py`; `spectral_type_poor_match`, `spectral_class_ambiguous` and `spectral_subtype_ambiguous` ([SPE-16, SPE-18](validation/spectroscopy.md#spe-18)) in `star_result_rules.py`. `scripts/shadow_compare_verdicts.py` lists where the rules and the stored flags disagree.

**Table 12.** Where each part of the judgment lives. Paths are relative to `wayfindinglib/`. Register entries hold the evidence; WAY-D rows hold Table 13's settings.

| Part | Code | Register |
| --- | --- | --- |
| Statement, kinds, action classes, confidence | `TargetStatement`, `StatementKind`, `ActionClass`, `Confidence` (`models/quality/statements.py`) | [WAY-01](validation/wayfinding.md#way-01) |
| Rule tables and conformance | `QualityRule`; `RESULT_QUALITY_RULES`, `STAR_RESULT_RULES`, `SESSION_RULES`, joined as `ALL_RULES` (`all_rules.py`); `judge_results`, `needs_attention`; `test_quality_rule_conformance.py` | [WAY-05](validation/wayfinding.md#way-05); WAY-D05 to D11 |
| Per-night rules | `session_rules.py`; guiding, capture and sky pipelines of Section 6.9 | [WAY-45](validation/wayfinding.md#way-45), [WAY-46](validation/wayfinding.md#way-46), [WAY-47](validation/wayfinding.md#way-47); WAY-D45 to D48 |
| Envelope limits the rules read | `envelope_assembly.assemble_performance_envelope`, `performance_envelope.py` (Section 6.8) | [WAY-40](validation/wayfinding.md#way-40) to [WAY-44](validation/wayfinding.md#way-44); WAY-D40 to D44 |
| Efficiency, goal, references | `physical_reference`, `baseline_reference`, `noise_regime`, `assess_efficiency` (`efficiency.py`); `science_goal_for_setting` (`science_goal.py`) | [WAY-20](validation/wayfinding.md#way-20); WAY-D01, D20 to D28, D74 |
| Loss account and night review | `loss_account`, `capture_setting_summaries`, `night_summary`, `target_summary` (`aggregates.py`); `set_aside_reasons` (`library_inputs.py`); `build_night_quality` (`night_quality_task.py`) | [WAY-74](validation/wayfinding.md#way-74); WAY-D75, D76 |
| Noise growth and the stop signal | `forecast_stack_noise`, `frames_to_cut_noise`, `fit_fixed_pattern_noise`, `stop_signal`, `forecast_group_noise`, `plan_group_stack`, `choose_fixed_pattern_noise`, `measure_noise_model_error`, `TakeMoreResult` (`noise_growth.py`; the folder README lists the rest) | [WAY-21](validation/wayfinding.md#way-21), [WAY-76](validation/wayfinding.md#way-76); WAY-D30 to D32, D34, D89, D90 |
| Where the fixed-pattern noise sits | `locate_fixed_pattern`, `explain_with_calibration_masters`, `FixedPatternAccount` (`fixed_pattern_location.py`); rule `retake_flats` (`rules.py`) | [WAY-76](validation/wayfinding.md#way-76); WAY-D32, D91, D92 |
| A bad dark or bias set | rule `retake_bad_calibration_set` (`rules.py`) | WAY-D95 |
| History per setup | `fixed_pattern_setup_key`, `fixed_pattern_shares_by_setup`, `history_fixed_pattern_noise`, `NoiseHistory` (`forecast.py`) | WAY-D93 |
| Forecast check | `forecast_check`, `stack_groups_of_run`, `subset_pairs`, `recommended_additional_frames_status_from_checks`, `OddEvenDifferenceRecord` (`forecast.py`; rows `stacking.odd_even_difference_*`); `MeasuredNoiseCheck.from_odd_even_difference` (`aggregates.py`); `ForecastBasis`, `RecommendedAdditionalFramesStatus` (`target_report.py`) | [WAY-70](validation/wayfinding.md#way-70); WAY-D70, D88 |
| Target dataset report | `build_target_dataset_report` (`target_report_tasks.py`), `target_report_inputs.py`, `report_rules.py`, `starting_point.py`; `TargetDatasetReport` (`models/planning/target_report.py`) | [WAY-60](validation/wayfinding.md#way-60); WAY-D60 to D69 |
| One star's result | `judge_star_result` (`result_judgment_tasks.py`), `star_result_rules.py` | [WAY-73](validation/wayfinding.md#way-73); WAY-D73 |
| Sky model, overheads, tonight's ranking | `fit_sky_model` (`sky_model.py`), `estimate_capture_overheads` (`capture_overheads.py`), `rank_tonight`, `score_candidate`, `recommended_additional_frames_status_of_target` (`tonight_ranking_tasks.py`, `target_report_inputs.py`) | [WAY-71](validation/wayfinding.md#way-71), [WAY-72](validation/wayfinding.md#way-72); WAY-D29, D33, D71, D72 |
| Night plan | `ObservationSession` fields `checklist`, `framing`, `revision`; `PlanChange`, `PlanRevisionConflictError`; `night_plan_tasks.py`; `AllowedCaptureValues`; `SequenceSummary` | [WAY-50](validation/wayfinding.md#way-50), [WAY-51](validation/wayfinding.md#way-51), [WAY-52](validation/wayfinding.md#way-52), [WAY-53](validation/wayfinding.md#way-53), [WAY-54](validation/wayfinding.md#way-54); WAY-D50 to D54 |
| Night list and target changes | `build_night_list`, `build_night_history` (`night_history.py`); `find_target_changes` | [WAY-55](validation/wayfinding.md#way-55), [WAY-57](validation/wayfinding.md#way-57); WAY-D55 to D57 |
| Monitor mode | `ALLOWED_IN_MONITOR_MODE`, the `test_monitor_mode_*` tests, guards in `hardware_operations.py` | [WAY-56](validation/wayfinding.md#way-56) |
| Reply size | `mcp_servers/common/reply_budget.py`; `fit_status_to_budget` | WAY-D58, D77 |

<!-- impl: wayfindinglib.models.quality.statements.TargetStatement -->
<!-- impl: wayfindinglib.models.quality.rule.QualityRule -->
<!-- impl: wayfindinglib.analytics.result_quality.all_rules.ALL_RULES -->
<!-- impl: wayfindinglib.analytics.result_quality.judge_results.judge_results -->
<!-- impl: wayfindinglib.analytics.result_quality.efficiency.assess_efficiency -->
<!-- impl: wayfindinglib.analytics.result_quality.science_goal.science_goal_for_setting -->
<!-- impl: wayfindinglib.analytics.result_quality.aggregates.loss_account -->
<!-- impl: wayfindinglib.analytics.result_quality.noise_growth.stop_signal -->
<!-- impl: wayfindinglib.analytics.result_quality.noise_growth.POSSIBLE_FIXED_PATTERN_SIGMAS -->
<!-- impl: wayfindinglib.analytics.result_quality.noise_growth.MINIMUM_MODEL_ERROR_TARGETS -->
<!-- impl: wayfindinglib.analytics.result_quality.noise_growth.MINIMUM_HISTORY_TARGETS -->
<!-- impl: wayfindinglib.analytics.result_quality.fixed_pattern_location.SHAPE_SIGMAS -->
<!-- impl: wayfindinglib.analytics.result_quality.fixed_pattern_location.MINIMUM_SHAPE_APERTURES -->
<!-- impl: wayfindinglib.analytics.result_quality.fixed_pattern_location.COLLINEAR_DISTANCE_CORRELATION -->
<!-- impl: wayfindinglib.analytics.result_quality.forecast.MINIMUM_EARLIER_NIGHTS_FRAMES -->
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
<!-- impl: mcp_servers.common.reply_budget.reply_budget -->
<!-- impl: wayfindinglib.models.equipment_and_site.site_profile.SiteProfile -->

Without `SiteProfile.typical_seeing_fwhm_arcsec`, the physical reference is the diffraction limit (WAY-D01). `DiskButler.put_session_from_execution` <!-- impl: wayfindinglib.drivers.butler.DiskButler.put_session_from_execution --> is the one write Execution uses. It merges Planning's fields in one transaction, so a plan edit survives a checkpoint. A plan edit that changes nothing stores nothing, keeps the revision and sends no `plan:changed`. The `sequencer` methods `add`, `remove`, `reorder` and `modify` edit the queue through the plan service.

**Table 13.** Main settings of the quality judgment.

| Setting | Value | Where | Register |
| --- | --- | --- | --- |
| Diffraction limit | 550 nm; 1.029 λ/D | `REFERENCE_WAVELENGTH_NM`, `AIRY_FWHM_FACTOR` | WAY-D20 |
| Equipment baseline | 10th percentile of nightly medians; 3 nights | `BASELINE_PERCENTILE`, `MINIMUM_NIGHTS_FOR_BASELINE` | WAY-D22 |
| Noise regimes | sky variance 9 × read-noise variance; source variance 1 × sky | `SKY_LIMITED_VARIANCE_RATIO`, `SOURCE_LIMITED_VARIANCE_RATIO` | WAY-D24 |
| Sky model | 5 nights, 20 frames; Moon term from 2 moonlit nights | `MINIMUM_NIGHTS_FOR_SKY_MODEL`, `MINIMUM_FRAMES_FOR_SKY_MODEL`, `MINIMUM_MOON_NIGHTS` | WAY-D29 |
| "Take N more" | noise to 0.707 of today's; new exposure length at least 5 frames; a count only within 4 times the stack's exposure time; one doubling worth at least a 5% cut | `TARGET_NOISE_RATIO`, `MINIMUM_FRAMES_NEW_EXPOSURE_LENGTH`, `PRACTICAL_TIME_FACTOR`, `MINIMUM_DOUBLING_GAIN_FRACTION` | WAY-D30 |
| Fixed-pattern noise found at; possible from; stop signal | 2 standard errors, one-sided (fit only); 1 standard error; stop under a 10% cut from doubling | `FIXED_PATTERN_SIGMAS`, `POSSIBLE_FIXED_PATTERN_SIGMAS`, `MINIMUM_GAIN_FRACTION` | WAY-D32, WAY-D89 |
| Model or odd–even difference agreement (loss account, one-stack test) | 2 standard errors, one-sided; stand-in model error 11% as noise | `MODEL_AGREEMENT_SIGMAS`, `EXPECTED_NOISE_MODEL_RELATIVE_ERROR` | WAY-D34 |
| Fewest targets: measured model error (per setting); setup history | 5; 3 | `MINIMUM_MODEL_ERROR_TARGETS`, `MINIMUM_HISTORY_TARGETS` | WAY-D90, WAY-D93 |
| Shape tests: found at; fewest apertures; same measurement above | 2 standard errors; 20; 0.9 | `SHAPE_SIGMAS`, `MINIMUM_SHAPE_APERTURES`, `COLLINEAR_DISTANCE_CORRELATION` | WAY-D91 |
| Earlier-nights check: fewest frames | 8 | `MINIMUM_EARLIER_NIGHTS_FRAMES` (`forecast.py`; the restack script repeats it) | WAY-D88 |
| Capture overheads | download 5 s, dither settle 20 s, autofocus 120 s | `DEFAULT_DOWNLOAD_SECONDS`, `DEFAULT_DITHER_SETTLE_SECONDS`, `DEFAULT_AUTOFOCUS_SECONDS` | WAY-D33 |
| Forecast agreement; dark master compared unless the camera name has zwo or asi | 2 standard errors | `AGREEMENT_TOLERANCES`, `COOLED_CAMERA_NAME_MARKERS` | WAY-D70 |
| Provisional count in the ranking: gains tied to | 2 decimals (whole percent) | `GAIN_TIE_DIGITS` | WAY-D70 |
| Clock hour of the ranking | dither every 3 frames; 1 autofocus run; thin under 2 h | `DITHER_EVERY_FRAMES`, `AUTOFOCUS_RUNS_PER_HOUR`, `THIN_DATASET_SECONDS` | WAY-D71 |
| Stack rules: rejected share of pixel values; star width over predicted width | 15%; 1.2 (copies of the stacker's limits) | `REJECTED_PIXEL_FRACTION_LIMIT`, `STACK_SHARPNESS_RATIO_LIMIT` | [STK-24](validation/stacking.md#stk-24), [STK-25](validation/stacking.md#stk-25) |
| Spectrum rules: poor match RMS; ambiguity gap | 0.15; 0.02 (copies) | `POOR_MATCH_RMS_LIMIT`, `AMBIGUOUS_RMS_GAP_LIMIT` | [SPE-16, SPE-18](validation/spectroscopy.md#spe-18) |
| Frames lost warning | 5% of a setting's frames | `FRAME_LOSS_WARNING_FRACTION` | WAY-D60 |
| Report "take N more" | 10 frames at least; exposure rounded up to 10 s | `MINIMUM_FRAMES_FOR_FORECAST`, `EXPOSURE_ROUNDING_SECONDS` | WAY-D61 |
| Sky cost; altitude cost | 10% of at least 300 s of captured time; cuts 30°, 40°, 50°, 15% | `SKY_COST_MINIMUM_SHARE`, `SKY_COST_MINIMUM_CAPTURED_SECONDS`, `ALTITUDE_CUTS_DEG`, `ALTITUDE_MINIMUM_RELATIVE_LOSS` | WAY-D64, WAY-D65 |
| Sharpness trade-off | keep 70%; at least 5% narrower; 20 frames | `SHARPNESS_KEEP_FRACTION`, `SHARPNESS_MINIMUM_GAIN`, `SHARPNESS_MINIMUM_FRAMES` | WAY-D66 |
| Reply limit | 40,000 characters, 10% headroom | `MAXIMUM_REPLY_CHARACTERS`, `REPLY_HEADROOM_FRACTION` | WAY-D77 |

## 7. Verification (Architecture Section 7)

### 7.1 Structural checks

Static checks are import-linter contracts in `pyproject.toml`. Runtime checks use `test/import_probe.py`.

| Check | Static contract | Runtime test |
| --- | --- | --- |
| Layering | Foundation imports nothing above it; Control and Planning never import each other | None |
| Execution is optional | Only the composition root imports Execution | `test_control_and_planning_work_without_loading_execution` in `test_layer_independence.py` |
| Safety runs without execution | None | `test_safety_runs_without_loading_execution` (same file) |
| Planning is hardware-free | Planning is hardware-free, even indirectly | `test_planning_module_tree_imports_no_device_driver` in `api/test/test_planning.py` |
| Cross-library | `astrometricslib` never imports `wayfindinglib` | None |

<!-- impl: wayfindinglib.test.import_probe.wayfindinglib_modules_loaded_by -->

### 7.2 Promotion gates

No module applies the gates of the architecture's Table 8; `DivergenceEvidenceSummary.meets_phase_gate` tests a summary against caller thresholds. Every value in Table 14 is a design estimate.

**Table 14.** Delegation phase promotion gates with their values.

| Entering phase | Gate |
| --- | --- |
| 1 | At least 20 mount commands over 2 nights through this system, incumbent disengaged. Out-of-envelope commands rejected on real hardware. Every device reports a `DeviceSummaryState`. No unintended motion. |
| 2 | A `GuiderCalibration` and `FocusModel` exist. All three correction functions run on every real night, commanding nothing. |
| 3 | Over at least 5 nights: 500 paired comparisons each for alignment and guiding, 30 focus sweeps. Agreement at least 95%. Mean signed divergence within half the tolerance. No sign or axis inversions. Alignment converges on at least 95% of attempts. |
| 4 | Over at least 5 nights with corrections authoritative: guiding RMS within 20% of the Phase 2 baseline, no unrecovered guide-star loss, at least 10 meridian flips resumed, cooling at its target. |
| 5 | An independent hardware interlock is verified. Safety verdicts are correct for stale, absent and unsafe readings. At least 20 interlocked enclosure cycles with no violation. `safe_state.execute` verified from each trigger, also with the controlling process killed. Fault recovery tried on an induced fault. |

## 8. Known gaps (Architecture Section 8)

- No code builds a default `SafeStateSteps`, `MeridianFlipSteps`, `GuiderCalibrationSteps` or `BacklashCalibrationSteps`.
- Nothing calls `write_heartbeat`, starts `run_watchdog_loop`, or calls `can_leave_park` or `check_motion_timeout` (Section 6.6).
- `classify_device_state` has no signal-gathering caller (Section 6.3).
- The variable-star count is always 0, and `visibility_state_at` ignores `max_hour_angle_hours` (Section 4).

## Appendix A. Correspondence with KStars and Ekos

**Table 15.** Ekos module [1] to library call mapping.

| Ekos module | Library calls |
| --- | --- |
| Mount | `control.mount.slew`, `park`, `set_tracking` |
| Align | `control.mount._compute_pointing_correction`, `slew(center=True)` |
| Focus | `focus_correction.compute_focus_correction` (no autofocus sweep call) |
| Guide | `control.guiding._compute_correction`, `status(include=["live"])` |
| Scheduler | `planning.create_session(..., requests=)` |
| Capture | `planning.create_package` |
| Dome and weather interlock | `control.safety._execute_safe_state`, `_assess` |
| Session recording | `execution.advance_session`, `create_recorder`, `review.query` |

## References

<!-- vale off -->

[1] J. Mutlaq et al., "KStars: Free, open source, cross-platform astronomy software," KDE Project. Accessed: Oct. 15, 2026. [Online]. Available: <https://edu.kde.org/kstars>

<!-- vale on -->
