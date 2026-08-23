# Wayfinding Library Implementation Overview

While the theoretical algorithms and hardware control flow are covered in the [Wayfinding Library Architecture](./Wayfinding_Library_Architecture.md) document, this map serves as a direct index to the Python source code where those hardware commands are physically implemented.

Due to the internal nature of these modules, they are deliberately hidden from the public API Reference. Developers wishing to review or modify the hardware execution logic should refer to the following directories within the `wayfindinglib/tasks/` package:

## Hardware Automation Tasks

### Hardware Control & Telemetry
*Located in:* `wayfindinglib/tasks/control_tasks/`
- **INDI driver communication (Mount, Camera, Focuser, Filter Wheel):** `hardware_operations.py` and `equipment_activation.py`
- **Real-time telemetry monitoring:** `device_state_tasks.py`
- **Hardware safety interlocks and weather monitoring:** `safety_monitor.py` and `safe_state.py`
- **Correction handling (Pointing, Guiding, Focus):** `pointing_correction.py`, `guiding_correction.py`, and `focus_correction.py`
- **Enclosure and cooling control:** `enclosure_control.py` and `cooling_control.py`

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
