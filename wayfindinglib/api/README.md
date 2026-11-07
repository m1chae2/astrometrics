# API

This folder holds the front door of `wayfindinglib`: the classes that scripts, the backend and the MCP tools (the adapters through which AI agents call the library) actually call. Each file wraps the tasks, models and drivers underneath in a stateful interface.

Terms are defined in the [glossary](../../documentation/Glossary.md). This file also explains each term when it first appears.

## Public layout

The library has a small public surface. Everything else is private.

| Import from | What it holds |
| :--- | :--- |
| `wayfindinglib` (the root, 7 names) | `Wayfinder`, the four sub-API classes `ObservatoryControl`, `ObservationPlanning`, `ObservationExecution` and `ObservationReview`, and the two records `ObservationSession` and `ObservationPackage`. |
| `wayfindinglib.exceptions` | `DelegationPolicyValidationError` and `PlanRevisionConflictError`. The shared categories (`NotFoundError`, `InvalidArgumentError` and the rest) come from `astrometricslib.exceptions`. |
| `wayfindinglib.reports` | The states and levels that results report: `RunState`, `PackageStatus`, `DepthGoalState` and `DepthLevel`. |
| `wayfindinglib._internal` | Everything else, in the groups `planning`, `execution`, `review` and `control`. |

The rules:

- **Only `wayfindinglib` may import `_internal`.** The backend and the MCP servers use the root and the two public modules only. Tests of the libraries are the one exception.
- **A result model is private, and its fields are not.** A call returns a Pydantic model (a Python class with typed, validated fields). A caller reads the fields by name and does not import the class. The API reference page lists the fields of each result without an import path.
- **The library uses only public `astrometricslib` names.** It reaches the science library through the `Astrometrics` handle that `Wayfinder` holds.
- **Calls take plain values.** A simple value is an argument. A group of options or a list of items is a dictionary, and the library checks it.
- **Old names have no alias.** Every rename is listed in [API renames](../../documentation/plans/API_RENAMES_134.md).

## The sub-APIs

`Wayfinder` builds four sub-APIs over one configuration, one data store and one `Astrometrics` handle, so all four see the same records. It has no methods of its own.

### `planning.py`

`ObservationPlanning` (`wayfinder.planning`) decides what to observe and when. It looks up coordinates, lists sources and catalogs, works out visibility and mosaic panels, gives advice for tonight and for calibration frames, and builds and edits observation packages (a package is a target with its planned exposures) and session queues. Every edit is its own call with its own revision, and `expected_revision` on each call makes the library refuse an edit made from an old copy.

### `execution.py`

`ObservationExecution` (`wayfinder.execution`) runs and recovers an observing session. It advances the queue, aborts a session, reconciles a session with what was captured, builds a divergence record (how the night differed from the plan) and creates the recorder that logs a run.

### `review.py`

`ObservationReview` (`wayfinder.review`) answers how the nights went. `query` reads past nights and analyses, `get_live_session_status` reads the night in progress, `frame_guiding` links a target's frames to the guiding record, `query_alignment_sessions` and `query_alignment_records` read pointing alignment, `get_performance_envelope` reads the limits that the equipment has shown, and `invalidate_night` forgets the kept night reviews. The library keeps each night review and the list of nights in memory until the frames, measurements, guiding records or equipment behind them change, so opening a night again is fast.

### `control/`

`ObservatoryControl` (`wayfinder.control`) commands and reads the devices. It has six children named by topic:

- `mount`: slewing, parking, tracking and polar alignment.
- `imaging`: the camera, the filter wheel and the focuser. `record_focus_model` saves a focus model that was measured elsewhere.
- `guiding`: the guide loop and the guiding spectrum fit. `record_calibration` saves a guider calibration that was measured elsewhere, such as PHD2's own.
- `safety`: the safety status and the delegation mode.
- `equipment`: device connection, the active telescope and camera, and the configuration.
- `remote`: the telescope computer's frame and log transfer.

Each of `mount`, `imaging`, `guiding`, `safety` and `equipment` has a `status` call with the same shape: it takes an optional `include` list of sections and returns a typed status. `mount.status` reads only the mount. `record_calibration` and `record_focus_model` take the numbers as keyword arguments and a required `source_note`, check them as the saved records do (a bad value raises `InvalidArgumentError` and saves nothing), and send nothing to a device. The backend serves them as `observatory:record_guider_calibration` and `observatory:record_focus_model`. `safety.enter_controller_mode` takes `shadow_first` to move the corrections to the shadowed state first, and `observatory:enter_controller_mode` passes it on. `close` stops the INDI session (INDI is the protocol that talks to the devices) when a program is done.

`Wayfinder` builds its own INDI session. Give `simulated=True` for the simulator, or leave it unset to use the simulator when `ASTROMETRICS_TESTING` is set. `separate_process=True` runs the session in a process of its own, which a program with a busy event loop needs. `open_port_forwards=True` lets the library open live SSH tunnels, such as the one to PHD2 on the observatory computer; it is off by default and only the backend turns it on. The driver properties on `control` are private.

For exact behavior, read the code.
