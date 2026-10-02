# API

This folder holds the library's front door: the classes and functions scripts, backend services, and the MCP tools actually call. Each file wraps the pipelines and drivers underneath in a stateful, easy-to-use interface. Nothing outside `astrometricslib` should import from this folder directly — import from the top-level `astrometricslib` package instead, which re-exports the public names.

## What each file is for

- `targets.py` — `TargetCatalog`, the main interface for creating, reading, and updating targets in the catalog.
- `stars.py` — `StellarCatalog`, the main interface for looking up, updating, and listing individual stars.
- `stellar_operations.py` — tools for inspecting stars and calibrating the spectroscope, built on top of `stars.py`.
- `processing.py` — `ProcessingPipelines`, `CalibrationCatalog`, and `QualityDiagnostics`: the interfaces for running stacking/astrometry/photometry/spectroscopy on a target, reading calibration frame data, and inspecting a run's quality output.
- `moving_objects.py` — `MovingObjectRecovery`, the interface for running asteroid detection on a target.
- `visualization.py` — `Visualization`, the interface for plotting graphs and rendering images from a target's or star's stored data.
- `batch.py` — tools for running processing across many targets at once, rather than one at a time.

For exact behavior, read the code — the code is always the source of truth.
