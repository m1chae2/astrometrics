# Utilities

This folder holds small, generic helpers that several parts of the library use and that belong to no one pipeline: parsing, domain-independent algorithms, and the library's own error subclasses. The infrastructure both libraries share (configuration, enums, camera names, logging, storage, jobs) lives in `astrometricslib/foundation/` instead.

## What each file is for

- `exceptions.py` — the error subclasses only astrometricslib raises, such as `PlateSolveFailedError`. It also names `DATA_ERRORS`, the built-in errors that measuring unusable data can raise, and `ONLINE_QUERY_ERRORS`, the errors an online catalog query (SIMBAD or Gaia) can raise. Code that measures data catches those tuples instead of every exception.
- `coordinate_parsing.py` — parses astronomical coordinate strings (right ascension, declination) into decimal degrees.
- `observing_night.py` — names the observing night a moment belongs to: the local date on which the night began, found by subtracting 12 hours from the moment. A night that crosses midnight keeps one name, so records written before and after midnight group together. The SQL queries of wayfindinglib's `ControlRecordStore` use the same rule.
- `iso_text.py` — helpers for parsing and formatting the ISO and gain text stored on a frame.
- `concurrency.py`, `parallel_batch.py` — generic worker-count reconciliation and a parallel batch-processing engine, used by any pipeline stage that fans work out across several processes.
- `rejection_thresholds.py`, `stack_filter_floor.py` — generic algorithms for adjusting a pixel-rejection threshold by frame count (`rejection_bounds` adds a floor and a looser low limit for small stacks), and for keeping a sharpness filter from discarding too many frames.
- `spectroscopy_models.py` — pydantic models for spectroscopy camera and session configuration.

For exact behavior, read the code — the code is always the source of truth.
