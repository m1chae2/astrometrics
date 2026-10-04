# API

This folder holds the library's front door: the classes and functions scripts, backend services, and the MCP tools actually call. Each file wraps the pipelines and drivers underneath in a stateful, easy-to-use interface. Nothing outside `astrometricslib` should import from this folder directly — import from the top-level `astrometricslib` package instead, which re-exports the public names.

## What each file is for

- `targets.py` — `TargetCatalog`, the main interface for creating, reading, and updating targets in the catalog. `camera_index` returns, for each target, the light-frame count and newest frame time per configured camera (see `pipelines/shared/target_camera_index.py`).
- `stars.py` — `StellarCatalog`, the main interface for looking up, updating, and listing individual stars.
- `stellar_operations.py` — tools for inspecting stars and calibrating the spectroscope, built on top of `stars.py`.
- `processing.py` — `ProcessingPipelines`, `CalibrationCatalog`, and `QualityDiagnostics`: the interfaces for running stacking/astrometry/photometry/spectroscopy on a target, reading calibration frame data, and inspecting a run's quality output. After new flats are downloaded, `CalibrationCatalog.refresh("flat")` rescans them and returns a report of what was found and whether each new set is good enough. `CalibrationCatalog.assess_flats(...)` runs the same check on the flats already in the library, one set per filter, gain and offset. `ProcessingPipelines` also manages the light frames the stacker sets aside for clouds or trailed stars: `preview_quarantine(target)` shows what it would move, `list_excluded_frames(target)` lists what it moved, and `restore_excluded_frames(target, apply=True)` moves frames back. A restack keeps the stack it replaces in a `_previous` folder (one version only, setting `keep_previous_stack_enabled`). `QualityDiagnostics.compare_stacks(before, after)` measures two stacks, `ProcessingPipelines.compare_with_previous_stack(target)` compares a target's stack with the kept one, `discard_previous_stack(target)` deletes the kept one once the new stack looks good, and `swap_with_previous_stack(target)` puts it back as current (and can be undone by calling it again).
- `moving_objects.py` — `MovingObjectRecovery`, the interface for running asteroid detection on a target.
- `visualization.py` — `Visualization`, the interface for plotting graphs and rendering images from a target's or star's stored data.
- `jobs.py` — `Jobs`, a read-only view of the job history (running and finished jobs, their log lines, stored results) and of the pipeline runs that produced a target's data. It opens the logs database read-only, so it cannot change it. The MCP tool `jobs_query` calls `Jobs.query`.
- `batch.py` — tools for running processing across many targets at once, rather than one at a time.

For exact behavior, read the code — the code is always the source of truth.
