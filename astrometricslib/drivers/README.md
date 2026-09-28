# Drivers

This folder holds the code that talks to something outside the pipeline logic itself: disk files, external processes, external databases, and external astronomy services. Pipelines and the API layer call into these drivers rather than opening files or databases themselves, so there is one place responsible for each outside dependency.

## What each file is for

- `image.py`, `fits_access.py` — `AstrometricsImage`, the main data container for a loaded astronomical image, and the lower-level functions that read pixel data and metadata out of a FITS file.
- `filter_detection.py` — reads an image's settings to work out what kind of picture it is (which filter, light/dark/flat/bias).
- `siril_interface.py`, `siril_output_parsing.py` — runs the external Siril program to stack images, and parses the text files Siril writes describing what it did.
- `plate_solve_interface.py` — figures out exactly what part of the sky an image shows.
- `simbad_interface.py` — the process's one client for querying the external SIMBAD astronomical database.
- `camera_profile_store.py` — finds and loads the stored profile (sensor characteristics, saturation threshold) for a given camera.
- `calibration_library.py` — the model describing the library of dark/bias/flat calibration frames.
- `local_database.py`, `catalog_store.py` — the local SQLite databases holding the target/star catalog and the cached Gaia star catalog.
- `catalog_access.py` — reads and writes catalog data (targets, stars) to and from the local database.
- `job_logging.py`, `logger_interface.py` — record a long-running job (a pipeline run) and capture its log messages, and the repository that persists those job records.
- `provenance_store.py` — repository for the IVOA provenance graph (which run, at what version, produced a result — see `astrometricslib/models/provenance.py`), stored alongside the job records in `astrometrics_log.db`.

For exact behavior, read the code — the code is always the source of truth.
