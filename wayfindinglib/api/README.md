# API

This folder holds the front door of `wayfindinglib`: the classes that scripts, the backend and the MCP tools (the adapters through which AI agents call the library) actually call. Each file wraps the tasks, models and drivers underneath in a stateful interface.

Terms are defined in the [glossary](../../documentation/Glossary.md). This file also explains each term when it first appears.

## Public layout

The library has a small public surface. Everything else is private.

| Import from | What it holds |
| :--- | :--- |
| `wayfindinglib` (the root, 8 names) | `Wayfinder`, the four sub-API classes `ObservatoryControl`, `ObservationPlanning`, `ObservationExecution` and `ObservationReview`, the two records `ObservationSession` and `ObservationPackage`, and `FrameEndDetector` (says when a camera frame has ended, from the camera status text). |
| `wayfindinglib.drivers` | The PHD2 client: `PHD2Client` (reads PHD2's guide steps, or opens a session) and `PHD2Session` (one connection that sends PHD2 commands such as connect, loop, guide and stop, and waits for events). Both refuse a host beyond this computer when the launch is offline. |
| `wayfindinglib.exceptions` | `DelegationPolicyValidationError` and `PlanRevisionConflictError`. The shared categories (`NotFoundError`, `InvalidArgumentError` and the rest) come from `astrometricslib.exceptions`. |
| `wayfindinglib.reports` | The states and levels that results report: `RunState`, `PackageStatus`, `DepthGoalState` and `DepthLevel`. |
| `wayfindinglib.logs` | Readers for the Ekos analyze log: `read_ekos_captures` (the frames a session saved, as `SavedCapture` records with UTC times) and `ekos_utc_offset_hours` (the offset of a zone name Ekos writes). They only read files. |
| `wayfindinglib._internal` | Everything else, in the groups `planning`, `execution`, `review` and `control`. |

The rules:

- **Only `wayfindinglib` may import `_internal`.** The backend, the MCP servers and the simulator use the root and the public modules only. Tests of the libraries are the one exception.
- **A result model is private, and its fields are not.** A call returns a Pydantic model (a Python class with typed, validated fields). A caller reads the fields by name and does not import the class. The API reference page lists the fields of each result without an import path.
- **The library uses only public `astrometricslib` names.** It reaches the science library through the `Astrometrics` handle that `Wayfinder` holds.
- **Calls take plain values.** A simple value is an argument. A group of options or a list of items is a dictionary, and the library checks it.
- **Old names have no alias.** A renamed call or field keeps no copy under its old name.

## The sub-APIs

`Wayfinder` builds four sub-APIs over one configuration, one data store and one `Astrometrics` handle, so all four see the same records. It has no methods of its own.

### `planning.py`

`ObservationPlanning` (`wayfinder.planning`) decides what to observe and when. It looks up coordinates, lists sources and catalogs, works out visibility and mosaic panels, gives advice for tonight and for calibration frames, and builds and edits observation packages (a package is a target with its planned exposures) and session queues. Every edit is its own call with its own revision, and `expected_revision` on each call makes the library refuse an edit made from an old copy.

### `execution.py`

`ObservationExecution` (`wayfinder.execution`) runs and recovers an observing session. It advances the queue, aborts a session, reconciles a session with what was captured, builds a divergence record (how the night differed from the plan) and creates the recorder that logs a run.

### `review.py`

`ObservationReview` (`wayfinder.review`) answers how the nights went. `query` reads past nights and analyses, `get_live_session_status` reads the night in progress, `frame_guiding` links a target's frames to the guiding record, `query_alignment_sessions` and `query_alignment_records` read pointing alignment, `get_performance_envelope` reads the limits that the equipment has shown. Four read-only measurements describe the equipment itself from the recorded nights: `measure_unguided_mount_error` rebuilds the mount's own error from the guide pulses and fits its periodic error, drift and jitter (arcseconds, seconds and radians; one entry per guiding run and a pooled summary); `measure_guiding_spectrum` is the guiding spectrum without saving it; `measure_focus_temperature_coefficient` fits focuser steps per degree Celsius with its uncertainty and the nights used; and `get_sky_rate_prediction` predicts the sky background rate (electrons and ADU per second per pixel) for a pointing and a time. All of them, and `get_performance_envelope`, take `exclude_nights` to leave nights out of the fit, which is how to check a model against a night it has not seen. Nothing they do is stored. The same call with the same arguments gives the same answer until the recorded data change. The library keeps each night review and the list of nights in memory until the frames, measurements, guiding records or equipment behind them change, so opening a night again is fast.

### `control/`

`ObservatoryControl` (`wayfinder.control`) commands and reads the devices. It has six children named by topic:

- `mount`: slewing, parking, tracking and polar alignment. `slew(center=True)` centers the mount by plate solving short frames. Each frame uses the `alignment_exposure_seconds` setting (5 s) for now. `get_centering_exposure` (a read that moves nothing) works out an exposure from the equipment, and centering uses it only once the `alignment_exposure_from_equipment` setting is turned on, which waits for its real-data check (issue #161): it gives the shortest exposure at which the Gaia stars bright enough to be detected number about 15, from the library's zero point and star width, the camera profile, the sky model and the downloaded star catalog. When an input is missing it returns the fixed `alignment_exposure_seconds` setting (5 s) and says which input was missing. The library asks the camera to send images only while it takes one of these frames, and turns that off right after, even on failure. It never receives an image while it only shadows Ekos, and frames from `capture_image` reach the library through the frame download, not live.
- `imaging`: the camera, the filter wheel and the focuser. `capture_image` takes a run of exposures. `stop_capture` ends the run this app started and aborts only that run's exposure; it returns `False` and sends nothing when the app has no run, so a run started by Ekos is never touched. A stopped run ends normally with `CaptureResult.stopped` set and keeps the frames already taken. `mount.slew` and `mount.park` stop the app's own run the same way before they move the mount. `query_focus_models` lists every saved focus model, and `record_focus_model` saves a focus model that was measured elsewhere.
- `guiding`: the guide loop and the guiding spectrum fit. `query_calibrations` lists every saved guider calibration, and `record_calibration` saves a guider calibration that was measured elsewhere, such as PHD2's own. `status(include=["live"])` reads the guiding another program runs. While PHD2 can be reached, only PHD2's own guide steps count (`drivers/phd2/guiding_driver.py`): the guide pulses the mount receives are thrown away, because PHD2's calibration pulses are not guiding. Only when PHD2 cannot be reached does the library estimate guiding from those pulses (`INDI_PULSE_ESTIMATE`), the path for Ekos guiding with its own guider.
- `safety`: the safety status and the delegation mode.
- `equipment`: device connection, the active telescope and camera, and the configuration.
- `remote`: the telescope computer's frame and log transfer.

Each of `mount`, `imaging`, `guiding`, `safety` and `equipment` has a `status` call with the same shape: it takes an optional `include` list of sections and returns a typed status. `mount.status` reads only the mount. `record_calibration` and `record_focus_model` take the numbers as keyword arguments and a required `source_note`, check them as the saved records do (a bad value raises `InvalidArgumentError` and saves nothing), and send nothing to a device. An id that is already saved is refused unless `replace=True` is passed. The id is at most 100 characters and the note at most 500. The backend serves them as `observatory:record_guider_calibration` and `observatory:record_focus_model`. `safety.enter_controller_mode` takes `shadow_first` to move the corrections to the shadowed state first, and `observatory:enter_controller_mode` passes it on. `library_path` names the folder of the wayfinding library, so a program that writes records can check where they go. `close` stops the INDI session (INDI is the protocol that talks to the devices) when a program is done.

`Wayfinder` builds its own INDI session. Give `simulated=True` for the simulator, or leave it unset to use the simulator when `ASTROMETRICS_TESTING` is set. `separate_process=True` runs the session in a process of its own, which a program with a busy event loop needs. `open_port_forwards=True` lets the library open live SSH tunnels, such as the one to PHD2 on the observatory computer; it is off by default and only the backend turns it on. The driver properties on `control` are private.

For exact behavior, read the code.
