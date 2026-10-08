# Drivers

This folder holds the code that talks to something outside the pipeline logic itself: disk files, external processes, external databases, and external astronomy services. Pipelines and the API layer call into these drivers rather than opening files or databases themselves, so there is one place responsible for each outside dependency.

## What each file is for

- `image.py`, `fits_access.py` — `AstrometricsImage`, the main data container for a loaded astronomical image, and the lower-level functions that read pixel data and metadata out of a FITS file. `AstrometricsImage` fixes old-style header keywords (`RADECSYS`, a missing `MJD-OBS`) in memory only. It never rewrites a frame, so a frame on a backed-up network drive stays as it was downloaded. `fits_access.FITS_READ_ERRORS` names the errors that reading a damaged or missing FITS file can raise; code that reads a file catches that tuple instead of every exception.
- `filter_detection.py` — reads an image's settings to work out what kind of picture it is (which filter, light/dark/flat/bias).
- `interfaces/` — the abstract base classes (`*Driver`) the rest of the library uses instead of a particular program or service: `StackingDriver` (a stacking program, with the `StackSettings` it takes and the `StackRunResult` it returns), `PlateSolveDriver` (a plate solver) and `SimbadDriver` (the SIMBAD database).
- `siril_stacking_driver.py` — `SirilStackingDriver`, Siril as the stacking program. It wraps `siril_interface.py` and turns Siril's files into the plain values the interface names.
- `siril_interface.py`, `siril_output_parsing.py` — runs the external Siril program to stack images, and parses the text files Siril writes describing what it did.
- `astrometry_net_driver.py` — `AstrometryNetPlateSolveDriver`, Astrometry.net as the plate solver: figures out exactly what part of the sky an image shows.
- `astroquery_simbad_driver.py` — `AstroquerySimbadDriver`, the SIMBAD driver. It owns the process's one astroquery client for the external SIMBAD astronomical database. A failed query is raised as `ExternalServiceError`.
- `camera_profile_store.py` — finds and loads the stored profile (sensor characteristics, saturation threshold) for a given camera.
- `calibration_library.py` — the model describing the library of dark/bias/flat calibration frames. It can also list the flats as sets, one per telescope, camera, filter, gain and offset (`list_flat_groups`), so each set is checked on its own.
- `local_database.py`, `catalog_store.py` — the local SQLite databases holding the target/star catalog and the cached Gaia star catalog.
- `catalog_access.py` — reads and writes catalog data (targets, stars) to and from the local database.
- `provenance_store.py` — repository for the IVOA provenance graph (which run, at what version, produced a result — see `astrometricslib/models/provenance.py`), stored next to the job records (`astrometricslib/foundation/jobs/`) in `astrometrics_log.db`.

For exact behavior, read the code — the code is always the source of truth.
