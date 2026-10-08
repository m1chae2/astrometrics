# Utilities

This folder holds small, generic helpers used across the library that do not belong to any one pipeline: configuration, parsing, domain-independent algorithms, and shared enums/exceptions.

## What each file is for

- `config_loader.py`, `config_schema.py` — load, save, and validate the application's TOML configuration file (`astrometrics.config.toml`), and the pydantic models describing its shape.
- `enums.py` — shared enumerations (filter types and similar) used across the domain models.
- `exceptions.py` — the library's own exception classes, and `DATA_ERRORS`, the built-in errors that measuring unusable data can raise. Code that measures data catches that tuple instead of every exception. `ONLINE_QUERY_ERRORS` names the errors an online catalog query (SIMBAD or Gaia) can raise.
- `coordinate_parsing.py` — parses astronomical coordinate strings (right ascension, declination) into decimal degrees.
- `camera_names.py` — compares camera names that may be written differently in different places (a header, a config section, a UI field) to decide whether they refer to the same camera.
- `observatory_setups.py` — the optics an observatory owns, and which camera is paired with which optic.
- `observing_night.py` — names the observing night a moment belongs to: the local date on which the night began, found by subtracting 12 hours from the moment. A night that crosses midnight keeps one name, so records written before and after midnight group together. The SQL queries of wayfindinglib's `ControlRecordStore` use the same rule.
- `iso_text.py` — helpers for parsing and formatting the ISO and gain text stored on a frame.
- `concurrency.py`, `parallel_batch.py` — generic worker-count reconciliation and a parallel batch-processing engine, used by any pipeline stage that fans work out across multiple processes.
- `rejection_thresholds.py`, `stack_filter_floor.py` — generic algorithms for adjusting a pixel-rejection threshold by frame count, and for keeping a sharpness filter from discarding too many frames.
- `spectroscopy_models.py` — pydantic models for spectroscopy camera and session configuration.
- `storage_mount.py` — checks that the drive holding the raw frames (a USB disk or a network share) is mounted before anything is written there. Without the check, a missing drive leaves an empty folder on the computer's own disk, and downloads would fill it. It reads the optional `frames_mount_point` setting and does nothing when that is not set.
- `warn_once.py` — logs a given warning only once per process run, instead of once per occurrence.

For exact behavior, read the code — the code is always the source of truth.
