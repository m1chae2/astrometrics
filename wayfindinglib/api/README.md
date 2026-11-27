# API

This folder holds the front door of `wayfindinglib`: the classes that scripts, the backend and the MCP tools (the adapters through which AI agents call the library) actually call. Each file wraps the tasks, models and drivers underneath in a stateful interface.

Terms are defined in the [glossary](../../documentation/Glossary.md). This file also explains each term when it first appears.

## Public layout

The library has a small public surface. Everything else is private.

| Import from | What it holds |
| :--- | :--- |
| `wayfindinglib` (the root) | `Wayfinder`, the four sub-API classes `ObservatoryControl`, `ObservationPlanning`, `ObservationExecution` and `ObservationReview`, the two records `ObservationSession` and `ObservationPackage`, and the models the public calls take as input: for planning `ExposureRequest`, `DitherConfig`, `Framing`, `ChecklistItem`, `QueueRequest`, `MosaicPanel` and `HorizonZone`, and for control `FocusModel` and `GuiderCalibration`. `FrameEndDetector` (says when a camera frame has ended, from the camera status text) is in `wayfindinglib.logs`. |
| `wayfindinglib.drivers` | The PHD2 client: `PHD2Client` (reads PHD2's guide steps, or opens a session) and `PHD2Session` (one connection that sends PHD2 commands such as connect, loop, guide and stop, and waits for events). Both refuse a host beyond this computer when the launch is offline. |
| `wayfindinglib.exceptions` | `DelegationPolicyValidationError` and `PlanRevisionConflictError`. The shared categories (`NotFoundError`, `InvalidArgumentError` and the rest) come from `astrometricslib.exceptions`. |
| `wayfindinglib.reports` | The states and levels that results report: `RunState`, `PackageStatus`, `DepthGoalState` and `DepthLevel`. |
| `wayfindinglib.logs` | Readers for the Ekos analyze log: `read_ekos_captures` (the frames a session saved, as `SavedCapture` records with UTC times) and `ekos_utc_offset_hours` (the offset of a zone name Ekos writes). They only read files. |
| `wayfindinglib._internal` | Everything else, in the groups `planning`, `execution`, `review` and `control`. |

The rules:

- **Only `wayfindinglib` may import `_internal`.** The backend, the MCP servers and the simulator use the root and the public modules only. Tests of the libraries are the one exception.
- **A result model is private, and its fields are not.** A call returns a Pydantic model (a Python class with typed, validated fields). A caller reads the fields by name and does not import the class. The API reference page lists the fields of each result without an import path.
- **The library uses only public `astrometricslib` names.** It reaches the science library through the `Astrometrics` handle that `Wayfinder` holds.
- **Calls take objects.** A call takes the library's objects (a `Target`, a session, a package) and typed input models (an `ExposureRequest`, a `Framing`), all importable from the root. The adapters, the backend services and the MCP tools, turn ids and JSON into these.
- **Old names have no alias.** A renamed call or field keeps no copy under its old name.

## The sub-APIs

`Wayfinder` builds four sub-APIs over one configuration, one data store and one `Astrometrics` handle, so all four see the same records. It has no methods of its own.

### `planning/`

`ObservationPlanning` (`wayfinder.planning`) decides what to observe and when, through four objects, one module each. Every method is a thin wrapper of one owner function under `tasks/planning_tasks/`, which its docstring names; `PlanningContext` there holds the settings, storage and sky engine they share.

- `sessions` (`sessions.py`): the night plans, one per observing night. `get(night)` and `query()` read them; the heavy lists (guiding and weather samples, divergence records) and the counted `progress` come only with `include=`. `create(night)` records one, empty or with `packages` placed automatically. `add` and `remove` change the queue, and `update` changes the order, an entry's exposures, the framing and the checklist (a tick is a new `ticked_at` on an item).
- `packages` (`packages.py`): the observation packages (a package is a target with its planned exposures). `get`, `get_for_target`, `query`, `create`, `update` (plain changes, `status="PLANNED"` to reopen, `dismissed=True` to dismiss a suggestion), `restore` and `delete`. `record_suggestions` refreshes the library's suggested packages and `query_suggestion_log` reads what it did. A target's depth goal (`get_depth_goal`, `set_depth_goal`) and `create_mosaic` live here too.
- `sky` (`sky.py`): `get_visibility` of targets and stars, `get_tonight_advice`, `get_constellation_lines` and `get_panels`, the panel centers of a mosaic grid.
- `catalogs` (`catalogs.py`): the online catalog drivers (`query`), the sources around a point (`query_sources`) and the deep-star catalog (`get_deep_status`, `predict_deep_catalog`, `record_deep_catalog`).

A name lookup goes through the core's `astrometrics.targets.resolve`, and calibration advice through `astrometrics.calibration.query`. A name lookup, a magnitude lookup and the online region search ask SIMBAD through `astrometricslib.drivers.get_simbad_driver`, the same driver the image pipelines use. A fresh stored answer is used with no network call, and when SIMBAD cannot be reached, or the outside-service gateway has paused it after repeated trouble or a 429 or 503 reply, the last stored answer is used with a warning that gives its age. The gateway also limits SIMBAD to 4 requests per second per server. The deep star catalog builder asks the ESA Gaia archive through `astrometricslib.drivers.get_gaia_archive`, under the same rules. A session or package carries its revision, and the library refuses an edit made from an old copy.

### `execution.py`

`ObservationExecution` (`wayfinder.execution`) runs and recovers an observing session. `run(session, stop=None)` advances the queue (one cycle, or until `stop` is set), `abort(session, reason)` closes a session, `reconcile(session)` matches a finished session with what was captured, and `create_recorder(phd2_host=None)` builds the recorder that logs a run. The imaging steps are built on the work behind `control`, not on the public objects.

### `review/`

`ObservationReview` (`wayfinder.review`) answers how the nights went. It holds five objects, one file each, and every method names its owner function in its docstring:

- `nights` (`ReviewNights`): `query` lists the recorded nights and what data each has; `get(night)` reads one night's review (where each target's time went, its warnings, a guiding summary); `get_target_changes`, `get_capture(night)`, `get_sky_coverage` and `get_recurring_issues` read the other night analyses; `query_ekos_sessions` and `get_ekos_session(ekos_id, include=...)` read the stored Ekos records; `get_live_status(window_min=None, force=False, include=None)` reads the night in progress, and `force=True` copies the newest logs first.
- `targets` (`ReviewTargets`): `get_report(target)` is the target dataset report, `get_result(star, target=None)` says how far one star's results can be trusted, `get_star_shapes(target, frames=None, night=None)` says in plain words whether the stars are round (the frames against the equipment's history, WAY-42; a stack against the library's other stacks), whether the stretch points one way (tracking, guiding, wind or flexing) or many ways (seeing or focus), which frames the stacker set aside as trailed, and how the corner widths compare with the center (reported only, not validated: AST-18). `get_frame_guiding(target, frames=None, include=None)` links each frame to the guide error during its exposure.
- `guiding` (`ReviewGuiding`): `get_night(night)` is one night's guiding analysis and `query_runs` lists the guiding runs. `measure_unguided_error` rebuilds the mount's own error from the guide pulses and fits its periodic error, drift and jitter (arcseconds, seconds and radians; one entry per guiding run and a pooled summary). `measure_spectrum` works out the guiding spectrum and stores nothing; `record_spectrum` runs the same analysis and keeps it as the mount's model, which the guider's status and loop read.
- `performance` (`ReviewPerformance`): `get_envelope(until=None, exclude_nights=None)` gives the limits the active equipment has shown, `predict_sky_rate(ra_deg, dec_deg, time=None)` predicts the sky background rate (electrons and ADU per second per pixel), and `measure_focus_temperature` fits focuser steps per degree Celsius with its uncertainty and the nights used.
- `alignment` (`ReviewAlignment`): `query(night=None, since=None, until=None, limit=None, include=None)` reads the plate-solve attempts, grouped into one session per target, with the latest polar alignment run (`include=["tracking"]` adds a row per library target imaged in that time); `query_nights` lists one summary per night; `get_pointing_model(night)` fits one night's pointing model; `get_polar_alignment` reads the polar alignment, live while a run goes on; `record_external` stores the syncs and polar alignment runs other programs made through the mount.

The measurements take `exclude_nights` to leave nights out of the fit, which is how to check a model against a night it has not seen. Only `record_spectrum` and `record_external` write. A list returns every match unless `limit` asks for fewer. The library keeps each night review and the list of nights in memory until the frames, measurements, guiding records or equipment behind them change, so opening a night again is fast. The wayfinding tuned values are worked out by `astrometricslib.parameters.tune(astrometrics, wayfinder)`, which runs the tuners of both libraries.

### `control/`

`ObservatoryControl` (`wayfinder.control`) commands and reads the devices. It has six children named by topic:

- `mount`: slewing, stopping, parking and tracking. A slew takes a `Target`, a `StellarObject` or a `SkyPosition`. `slew(center=True)` centers the mount by plate solving short frames. Each frame uses the `alignment_exposure_seconds` setting (5 s) for now. `get_centering_exposure` (a read that moves nothing) works out an exposure from the equipment, and centering uses it only once the `alignment_exposure_from_equipment` setting is turned on, which waits for its real-data check (issue #161): it gives the shortest exposure at which the Gaia stars bright enough to be detected number about 15, from the library's zero point and star width, the camera profile, the sky model and the downloaded star catalog. When an input is missing it returns the fixed `alignment_exposure_seconds` setting (5 s) and says which input was missing. The library asks the camera to send images only while it takes one of these frames, and turns that off right after, even on failure. It never receives an image while it only shadows Ekos, and frames from `capture` reach the library through the frame download, not live.
- `imaging`: the camera, the filter wheel and the focuser. `check_capture` checks the arguments and the delegation policy without touching a device, and `capture` runs the same check before it takes a run of exposures, so a program that runs the capture on its own thread can check first. `stop` ends the run this app started and aborts only that run's exposure; it sends nothing when the app has no run, so a run started by Ekos is never touched. A stopped run ends normally with `CaptureResult.stopped` set and keeps the frames already taken. `mount.slew` and `mount.park` stop the app's own run the same way before they move the mount. `query_focus_models` lists every saved focus model, and `create_focus_model` saves a `FocusModel` that was measured elsewhere.
- `guiding`: this app's guide loop (`start` runs it on a thread the library keeps, `stop` ends it) and one guide exposure (`capture`). `query_guider_calibrations` lists every saved guider calibration, and `create_guider_calibration` saves a `GuiderCalibration` that was measured elsewhere, such as PHD2's own. `get_status(include=["live"])` reads the guiding another program runs. While PHD2 can be reached, only PHD2's own guide steps count (`drivers/phd2/guiding_driver.py`): the guide pulses the mount receives are thrown away, because PHD2's calibration pulses are not guiding. Only when PHD2 cannot be reached does the library estimate guiding from those pulses (`INDI_PULSE_ESTIMATE`), the path for Ekos guiding with its own guider.
- `safety`: the safety status, the delegation mode (`set_mode("monitor")` or `set_mode("controller")`) and one-capability changes (`update(policy, {capability: state})`).
- `equipment`: device connection, the active telescope and camera, and the configuration.
- `remote`: the telescope computer's folders (`query_folders`), files (`query_files`, each a `RemoteFile` with its size), reachability (`check_connection`, which raises a status error when the computer is offline) and frame and log transfer (`sync_frames(target)` or `sync_frames(kind="dark")`, `sync_logs`). A sync reports its progress to the job it runs in.

Each of `mount`, `imaging`, `guiding`, `safety` and `equipment` has a `get_status` call with the same shape: it takes an optional `include` list of sections and returns a typed status. `mount.get_status` reads only the mount, with its position in degrees. Device actions (`slew`, `park`, `capture`, `stop` and the rest) return `None` and raise when they fail. `create_guider_calibration` and `create_focus_model` take the record, which needs a `source_note`, check its sizes (a bad value raises `InvalidArgumentError` and saves nothing), and send nothing to a device. An id that is already saved is refused unless `force=True` is passed. The id is at most 100 characters and the note at most 500. The backend serves them as `observatory:record_guider_calibration` and `observatory:record_focus_model`. `safety.set_mode("controller")` takes `shadow_first` to move the corrections to the shadowed state first, and `observatory:enter_controller_mode` passes it on. `library_path` names the folder of the wayfinding library, so a program that writes records can check where they go. `close` stops the INDI session (INDI is the protocol that talks to the devices) when a program is done.

`Wayfinder` builds its own INDI session. Give `simulated=True` for the simulator, or leave it unset to use the simulator when `ASTROMETRICS_TESTING` is set. `separate_process=True` runs the session in a process of its own, which a program with a busy event loop needs. `open_port_forwards=True` lets the library open live SSH tunnels, such as the one to PHD2 on the observatory computer; it is off by default and only the backend turns it on. The driver properties on `control` are private.

For exact behavior, read the code.
