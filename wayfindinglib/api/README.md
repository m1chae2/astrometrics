# API

This folder holds the front door of `wayfindinglib`: the classes that scripts, the backend and the MCP tools (the adapters through which AI agents call the library) actually call. Each file wraps the tasks, models and drivers underneath in a stateful interface.

Terms are defined in the [glossary](../../documentation/Glossary.md). This file also explains each term when it first appears.

## Public layout

The library has a small public surface. Everything else is private.

| Import from | What it holds |
| :--- | :--- |
| `wayfindinglib` (the root, 6 names) | `Wayfinder`, the three sub-API classes `ObservatoryControl`, `ObservationPlanning` and `ObservationExecution`, and the two records `ObservationSession` and `ObservationPackage`. |
| `wayfindinglib.exceptions` | `DelegationPolicyValidationError` and `PlanRevisionConflictError`. The shared categories (`NotFoundError`, `InvalidArgumentError` and the rest) come from `astrometricslib.exceptions`. |
| `wayfindinglib.reports` | The states and levels that results report: `RunState`, `PackageStatus`, `DepthGoalState` and `DepthLevel`. |
| `wayfindinglib._internal` | Everything else, in the groups `planning`, `execution`, `review` and `control`. |

The rules:

- **Only `wayfindinglib` may import `_internal`.** The backend and the MCP servers use the root and the two public modules only. Tests of the libraries are the one exception.
- **A result model is private, and its fields are not.** A call returns a Pydantic model (a Python class with typed, validated fields). A caller reads the fields by name and does not import the class. The API reference page lists the fields of each result without an import path.
- **The library uses only public `astrometricslib` names.** It reaches the science library through the `Astrometrics` handle that `Wayfinder` holds.
- **Calls take plain values.** A simple value is an argument. A group of options or a list of items is a dictionary, and the library checks it.
- **Nothing in a sub-API is an alias.** Every rename is listed in [API renames](../../documentation/plans/API_RENAMES_134.md).

## The sub-APIs

`Wayfinder` builds four sub-APIs over one configuration, one data store and one `Astrometrics` handle, so all four see the same records. It has no methods of its own.

### `planning.py`

`ObservationPlanning` (`wayfinder.planning`) decides what to observe and when. It looks up coordinates, lists sources and catalogs, works out visibility and mosaic panels, gives advice for tonight and for calibration frames, and builds and edits observation packages (a package is a target with its planned exposures) and session queues. Every edit is its own call with its own revision, and `expected_revision` on each call makes the library refuse an edit made from an old copy.

### `execution.py`

`ObservationExecution` (`wayfinder.execution`) runs and recovers an observing session. It advances the queue, aborts a session, reconciles a session with what was captured, builds a divergence record (how the night differed from the plan) and creates the recorder that logs a run.

### `review.py`

`ObservationReview` (`wayfinder.review`) answers how the nights went. `query` reads past nights and analyses, `get_live_session_status` reads the night in progress, `frame_guiding` links a target's frames to the guiding record, `query_alignment_sessions` and `query_alignment_records` read pointing alignment, and `get_performance_envelope` reads the limits that the equipment has shown.

### `control/`

`ObservatoryControl` (`wayfinder.control`) commands and reads the devices. It has six children named by topic:

- `mount`: slewing, parking, tracking and polar alignment.
- `imaging`: the camera, the filter wheel and the focuser.
- `guiding`: the guide loop and the guiding spectrum fit.
- `safety`: the safety status and the delegation mode.
- `equipment`: device connection, the active telescope and camera, and the configuration.
- `remote`: the telescope computer's frame and log transfer.

Each of `mount`, `imaging`, `guiding`, `safety` and `equipment` has a `status` call with the same shape: it takes an optional `include` list of sections and returns a typed status. `mount.status` reads only the mount. `close` stops the INDI session (INDI is the protocol that talks to the devices) when a program is done.

`Wayfinder` builds its own INDI session. Give `simulated=True` for the simulator, or leave it unset to use the simulator when `ASTROMETRICS_TESTING` is set. `separate_process=True` runs the session in a process of its own, which a program with a busy event loop needs. The driver properties on `control` are private.

For exact behavior, read the code.
