# Wayfinding Library Implementation Overview

While the theoretical algorithms and hardware control flow are covered in the [Wayfinding Library Architecture](./Wayfinding_Library_Architecture.md) document, this map serves as a direct index to the Python source code where those hardware commands are physically implemented.

Due to the internal nature of these modules, they are deliberately hidden from the public API Reference. Developers wishing to review or modify the hardware execution logic should refer to the following directories within the `wayfindinglib/tasks/` package:

## Hardware Automation Tasks

### Hardware Control & Telemetry
*Located in:* `wayfindinglib/tasks/control_tasks/`
- **Hardware command orchestration (Mount, Camera, Focuser, Filter Wheel, Enclosure):** `hardware_operations.py` and `equipment_activation.py`, dispatching to the per-device-type driver interfaces in `wayfindinglib/drivers/protocols/` (`MountDriver`, `CameraDriver`, `FocuserDriver`, `FilterWheelDriver`, `EnclosureDriver`), whose first implementation is INDI (`wayfindinglib/drivers/indi/*_driver.py`, wrapping the shared session in `wayfindinglib/drivers/indi_interface.py`)
- **Real-time telemetry monitoring:** `device_state_tasks.py`
- **Hardware safety interlocks and weather monitoring:** `safety_monitor.py` and `safe_state.py`
- **Correction handling (Pointing, Guiding, Focus):** `pointing_correction.py`, `guiding_correction.py`, and `focus_correction.py`; both pointing and guiding accept an optional fitted mount model as feedforward input (session-scoped for pointing, persisted and cross-night for guiding)
- **Calibration/polar-alignment-assist orchestration:** `calibration_routines.py` (`run_guider_calibration`, `run_backlash_calibration`, `run_polar_alignment_assist`), built around the pure math in `guider_calibration_tasks.py` and `wayfindinglib/analytics/pointing_model.py`
- **Capability delegation and the monitoring/controller mode toggle:** `capability_promotion.py` (`apply_promotion_decision` per capability, `set_all_capabilities` for the bulk "monitoring mode"/"controller mode" convenience), reading/writing through `data_access/delegation_policy_reader.py`
- **Enclosure and cooling control:** `enclosure_control.py` (pure interlock checks) and `cooling_control.py`; the roof/dome shutter's own INDI commands live in `wayfindinglib/drivers/indi/enclosure_controller.py`
- **Remote file transfer and guiding/pointing log ingestion:** the pluggable `wayfindinglib/drivers/protocols/remote_transfer_driver.py` (first implemented by `wayfindinglib/drivers/stellarmate_interface.py`) retrieves files from a telescope host; `guiding_log_ingestion.py` and `pointing_log_ingestion.py` chain the download/parse/analyze/persist and analyze/expose steps that used to be hand-orchestrated separately in `backend/services/infrastructure/sync_service.py` and `backend/services/observatory/guiding_service.py`/`alignment_service.py`

### Sequence Execution
*Located in:* `wayfindinglib/tasks/execution_tasks/`
- **Image acquisition sequencing:** `session_runner.py` and `session_recorder.py`
- **Meridian flip orchestration:** `meridian_flip.py`
- **Fault recovery:** `fault_recovery.py` and `guide_star_loss_recovery.py`
- **Divergence recording:** `divergence_recording.py`

### Observation Planning
*Located in:* `wayfindinglib/tasks/planning_tasks/`
- **Target visibility and altitude calculation:** `visibility_tasks.py` and `night_window.py`
- **Mosaic panel generation:** `mosaic_tasks.py`
- **Scheduling and optimization:** `scheduling.py`
- **Archive-informed advising:** `calibration_advisory_tasks.py` and `quality_advisory_tasks.py`
