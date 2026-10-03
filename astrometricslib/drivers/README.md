# Drivers

This folder holds the code that talks to something outside the pipeline logic itself: disk files, external processes, external databases, and external astronomy services. Pipelines and the API layer call into these drivers rather than opening files or databases themselves, so there is one place responsible for each outside dependency.

## What each file is for

- `image.py`, `fits_access.py` — `AstrometricsImage`, the main data container for a loaded astronomical image, and the lower-level functions that read pixel data and metadata out of a FITS file. `AstrometricsImage` fixes old-style header keywords (`RADECSYS`, a missing `MJD-OBS`) in memory only. It never rewrites a frame, so a frame on a backed-up network drive stays as it was downloaded.
- `filter_detection.py` — reads an image's settings to work out what kind of picture it is (which filter, light/dark/flat/bias).
- `stacking_engine.py` — the contract between the stacking pipeline and a stacking program: the settings, the result, and the methods an engine offers.
- `siril_stacking_engine.py` — Siril as the stacking engine. It wraps `siril_interface.py` and turns Siril's files into the plain values the contract names.
- `siril_interface.py`, `siril_output_parsing.py` — runs the external Siril program to stack images, and parses the text files Siril writes describing what it did.
- `plate_solve_interface.py` — figures out exactly what part of the sky an image shows.
- `simbad_interface.py` — the process's one client for querying the external SIMBAD astronomical database.
- `camera_profile_store.py` — finds and loads the stored profile (sensor characteristics, saturation threshold) for a given camera.
- `calibration_library.py` — the model describing the library of dark/bias/flat calibration frames. It can also list the flats as sets, one per telescope, camera, filter, gain and offset (`list_flat_groups`), so each set is checked on its own.
- `local_database.py`, `catalog_store.py` — the local SQLite databases holding the target/star catalog and the cached Gaia star catalog.
- `catalog_access.py` — reads and writes catalog data (targets, stars) to and from the local database.
- `job_logging.py`, `logger_interface.py` — record a long-running job (a pipeline run) and capture its log messages, and the repository that persists those job records. `logger_interface.py` also stores telescope telemetry in `astrometrics_log.db`:
  - `alignment_logs` holds plate-solve alignment attempts.
  - `guiding_logs` holds guiding samples. Each sample carries a `source` column that says where it came from (a guide log file, a live PHD2 stream, or an estimate reconstructed from mount pulses). The `source` lets analysis read only samples that were measured from a real guide star, because an estimate is a model's output and not a measurement. Rows written before the column existed read as `unverified`.
  - Both tables carry a `session_id`. A record written without one receives the name of the observing night its timestamp falls in (see `utilities/observing_night.py`).
  - `replace_guiding_samples` stores a batch and first removes any earlier samples from the same source inside the batch's time span. Reading the same log file twice therefore leaves one copy of each sample. Unlike the other `record_*` methods, it raises when the database cannot be written, so callers do not report samples that were never stored.
- `provenance_store.py` — repository for the IVOA provenance graph (which run, at what version, produced a result — see `astrometricslib/models/provenance.py`), stored alongside the job records in `astrometrics_log.db`.

For exact behavior, read the code — the code is always the source of truth.
